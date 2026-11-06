package drafts

import (
	"fmt"
	"sort"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/archive"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/templates"
	"ccnavi/internal/tickets/sync/handoff"
)

// Target は下書きの取り込み先の置き場（--to）。
type Target string

const (
	ToCommon  Target = "common"
	ToProject Target = "project"
)

// Targets は --to に渡せる値（この順で案内する）。
var Targets = []Target{ToCommon, ToProject}

// todoPrefix は下書きのタイトルの前に付ける印（templates.TodoMark を含む）。
const todoPrefix = templates.TodoMark + " 一般化する: "

const (
	msgBadID       = "%s は親の識別子として読めない"
	msgStillDoing  = "%s はまだ作業中（doing/）。完了した（done/ に在る）親だけから作る"
	msgArchived    = "%s は ready で閉じて退避済み（%s/）で、退避した親は読まない。ready の前に（done/ に在るうちに）下書きを作る"
	msgNotFound    = "%s の完了した計画（done/）が見つからない"
	msgManyTrees   = "%s が複数のツリーにある（%s）。決められない"
	msgCancelled   = "%s は取り消した親。完了した親だけから下書きを作る"
	msgNoPlan      = "%s は計画（plan:）を持たない"
	msgAfterDrop   = "%s[%d] の after %d は%sの中のステップを指さないので落とした"
	msgFlowUnread  = "%d 番目の子 %s のフローは読めないのでコピーしない（%s）"
	msgFlowTaken   = "%d 番目は %s のフローをコピーした（%s にもフローがある）"
	msgFeedbackOff = "フィードバック計画の子のフローはコピーしない（全体計画の子のフローだけ）"
	msgWhen        = "「%s」と同じ形の作業をするとき"
	msgLayerToOnly = "--layer は --to project のときだけ使う"
	msgProjectTree = "%s はプロジェクト（%s）のツリーの親。そのツリーの共通の置き場は共通レイヤーのコピーなので、共通の下書きは作らない"
	msgBadTo       = "--to は %s"
	msgNoLayer     = "レイヤー %s はありません（%s）"
	msgLayerAway   = "レイヤー %s の置き場が親のツリー（%s）の外にある。このツリーの下書きからは取り込めない"
	msgBadName     = "下書きの名前 %s は指定できない（%s）。--name で付ける"
	msgTargetThere = "取り込み先に同名のテンプレート %s が既に在る。上書きになる下書きは作らない" +
		"（別の --name を付けるか、直すなら ccnavi-stage.sh で既存をコピーして編集する）"
	msgPlaceLinked = "下書きの置き場（%s/）の途中にリンクがあるので反映しません"
	msgDraftThere  = "下書き %s が既に在る。上書きしない（別の --name を付けるか、先に削除します）"
	msgNotSaved    = "下書きを保存できませんでした（%s）"
)

const (
	planKey     = "plan"
	feedbackKey = "feedback"
)

func sprintf(format string, a ...any) string { return fmt.Sprintf(format, a...) }

func line(text string) string {
	return strings.Join(strings.FieldsFunc(text, ospath.IsSpace), " ")
}

func splitKey(key string) []string { return strings.Split(key, "/") }

// findParent は完了した親（done/）とその子たち。見つからなければ理由。
func findParent(conf *config.Settings, root, parent string) (*ticket.Ticket, []*ticket.Ticket, string) {
	if !ids.IsValidID(parent) {
		return nil, nil, sprintf(msgBadID, pyfmt.Quote(parent))
	}
	raw := approval.ReadRaw(conf, root)
	closed, _ := approval.Scan(conf, root, true, raw)
	var hits []*ticket.Ticket
	for _, t := range closed {
		if t.ID == parent && !t.IsChild() {
			hits = append(hits, t)
		}
	}
	if len(hits) == 0 {
		opened, _ := approval.Scan(conf, root, false, raw)
		for _, t := range opened {
			if t.ID == parent {
				return nil, nil, sprintf(msgStillDoing, parent)
			}
		}
		if len(archive.FindAny(root, parent)) > 0 {
			return nil, nil, sprintf(msgArchived, parent, place.ArchiveDir)
		}
		return nil, nil, sprintf(msgNotFound, parent)
	}
	roots := map[string]bool{}
	for _, t := range hits {
		roots[t.TreeRoot] = true
	}
	if len(roots) > 1 {
		labels := make([]string, len(hits))
		for i, t := range hits {
			labels[i] = treeLabel(t.Tree)
		}
		sort.Strings(labels)
		return nil, nil, sprintf(msgManyTrees, parent, strings.Join(labels, ", "))
	}
	found := hits[0]
	if found.State == place.Cancelled {
		return nil, nil, sprintf(msgCancelled, parent)
	}
	if !found.HasPlan() {
		return nil, nil, sprintf(msgNoPlan, parent)
	}
	var children []*ticket.Ticket
	for _, t := range closed {
		if _, ok := t.ChildPhase(); ok && t.Parent == parent {
			children = append(children, t)
		}
	}
	return found, children, ""
}

// relativeItems は親の計画のステップを、その計画の中の添字の after に直す。offset はその計画の最初の番号 - 1。
func relativeItems(items []ticket.PlanItem, offset int, key string, notes *[]string) []templates.Item {
	out := make([]templates.Item, 0, len(items))
	for n, item := range items {
		i := n + 1
		seen := map[int]bool{}
		var after []int
		for _, value := range item.After {
			if local := value - offset; 1 <= local && local < i {
				if !seen[local] {
					seen[local] = true
					after = append(after, local)
				}
			} else {
				*notes = append(*notes, sprintf(msgAfterDrop, key, i, value, key))
			}
		}
		sort.Ints(after)
		out = append(out, templates.Item{Type: item.Type, Review: item.Review, Deferred: item.Deferred, After: after})
	}
	return out
}

// flows は全体計画の n 番目のステップ → その子のフローの本文。フローを持つ最初の子の 1 本をコピーする。
func flows(conf *config.Settings, parent *ticket.Ticket, children []*ticket.Ticket, notes *[]string) map[int]*yaml.Map {
	out := map[int]*yaml.Map{}
	for n := 1; n <= len(parent.Plan); n++ {
		var mine []*ticket.Ticket
		for _, c := range children {
			if phase, _ := c.ChildPhase(); phase == n && c.State == place.Done {
				mine = append(mine, c)
			}
		}
		sort.SliceStable(mine, func(i, j int) bool { return mine[i].ID < mine[j].ID })
		taken := ""
		for _, child := range mine {
			path := flow.FlowFile(conf, parent.TreeRoot, child.ID)
			if !ospath.DiskLexists(path) {
				continue
			}
			raw, why := flow.ReadBytes(path, parent.TreeRoot)
			var data *yaml.Map
			if raw != nil {
				data, why = flow.Parse(raw)
			}
			if data == nil {
				*notes = append(*notes, sprintf(msgFlowUnread, n, child.ID, why))
				continue
			}
			if taken != "" {
				*notes = append(*notes, sprintf(msgFlowTaken, n, taken, child.ID))
				continue
			}
			out[n] = data
			taken = child.ID
		}
	}
	return out
}

// build は完了した親の計画とフローを、下書きの中身（まだ一般化していない）にコピーする。何も書かない。
func build(conf *config.Settings, found *ticket.Ticket, children []*ticket.Ticket, name string) (*templates.Template, []string) {
	var notes []string
	planned := len(found.Plan)
	plan := relativeItems(found.Plan, 0, planKey, &notes)
	feedback := relativeItems(found.Feedback, planned, feedbackKey, &notes)
	title := line(found.Title)
	if title == "" {
		title = found.ID
	}
	t := &templates.Template{
		Name:        name,
		Description: todoPrefix + title,
		When:        todoPrefix + sprintf(msgWhen, title),
		Plan:        plan,
		Feedback:    feedback,
		Flows:       flows(conf, found, children, &notes),
	}
	if len(found.Feedback) > 0 {
		for _, c := range children {
			if phase, _ := c.ChildPhase(); phase > planned && ospath.DiskLexists(flow.FlowFile(conf, found.TreeRoot, c.ID)) {
				notes = append(notes, msgFeedbackOff)
				break
			}
		}
	}
	return t, notes
}

// targetOf は下書きの取り込み先（ツリーからの相対）と、そのレイヤー。決まらなければ理由。
func targetOf(conf *config.Settings, root string, found *ticket.Ticket, name string, to Target, layer string) (string, string, string) {
	home := conf.ProjectHome
	if home == "" {
		home = config.DefaultProjectHome
	}
	home = strings.Trim(ospath.Slashed(home), "/")
	file := config.TemplatesDir + "/" + name + templates.Suffix
	switch to {
	case ToCommon:
		if layer != "" {
			return "", "", msgLayerToOnly
		}
		if found.Project != "" {
			return "", "", sprintf(msgProjectTree, found.ID, found.Project)
		}
		return home + "/" + config.CommonDir + "/" + file, config.LayerCommon, ""
	case ToProject:
	default:
		shown := make([]string, len(Targets))
		for i, t := range Targets {
			shown[i] = string(t)
		}
		return "", "", sprintf(msgBadTo, strings.Join(shown, " か "))
	}
	if layer == "" || layer == config.LayerSelf {
		own := config.LayerSelf
		if contains(known(conf, root), found.Project) {
			own = found.Project
		}
		return home + "/" + config.LayerConfigDir + "/" + file, own, ""
	}
	var listed []string
	var view *ruleload.Layer
	for _, l := range ruleload.Layers(conf, root) {
		if !contains(listed, l.Name) {
			listed = append(listed, l.Name)
		}
		if l.Name == layer {
			v := l
			view = &v
		}
	}
	if view == nil {
		return "", "", sprintf(msgNoLayer, pyfmt.Quote(layer), strings.Join(listed, " / "))
	}
	rel := templates.Relative(found.TreeRoot, view.Home)
	if ospath.IsAbs(rel) || strings.HasPrefix(rel, "..") {
		return "", "", sprintf(msgLayerAway, layer, treeLabel(found.Tree))
	}
	return rel + "/" + home + "/" + config.LayerConfigDir + "/" + file, layer, ""
}

// Request は下書きを作る頼み。Name は空なら親の識別子、To は空なら ToProject、Layer は --to project の行き先のレイヤー。
type Request struct {
	Parent string
	Name   string
	To     Target
	Layer  string
}

// Made は作った下書き。Rel は下書き（ツリーからの相対）、Target と Layer は取り込み先とそのレイヤー、
// Tree は下書きを置いたツリーの名前、TreeRoot はそのルート（user handoff を打つツリー）。Notes は知らせ
// （コピーできなかったフロー、落とした先行など）、Problems は書いたものを読み直した検査。
type Made struct {
	Name, Rel, Target, Layer, SHA, Tree, TreeRoot string
	Template                                      *templates.Template
	Notes                                         []string
	Problems                                      []rules.Problem
}

// Make は完了した親から下書きを作って、親のツリーの wip/handoff/ に書く。上書きはしない。止まったら (nil, 理由)。
func Make(conf *config.Settings, root string, req Request) (*Made, string) {
	found, children, why := findParent(conf, root, req.Parent)
	if found == nil {
		return nil, why
	}
	name := req.Name
	if name == "" {
		name = req.Parent
	}
	name = ospath.Strip(name)
	if problem := templates.NewNameProblem(name); problem != "" {
		return nil, sprintf(msgBadName, pyfmt.Quote(line(name)), problem)
	}
	to := req.To
	if to == "" {
		to = ToProject
	}
	targetRel, layer, why := targetOf(conf, root, found, name, to, req.Layer)
	if why != "" {
		return nil, why
	}
	base := found.TreeRoot
	target, why := handoff.Spelled(base, targetRel)
	if why != "" {
		return nil, why
	}
	if ospath.DiskLexists(target) {
		return nil, sprintf(msgTargetThere, targetRel)
	}
	rel := handoff.DraftRel(targetRel)
	spot, why := handoff.Spelled(base, rel)
	if why != "" {
		return nil, why
	}
	if pathmatch.HasLink(base, ospath.Dirname(spot)) {
		return nil, sprintf(msgPlaceLinked, ospath.Dirname(rel))
	}
	if ospath.DiskLexists(spot) {
		return nil, sprintf(msgDraftThere, rel)
	}
	t, notes := build(conf, found, children, name)
	if err := fsio.WriteFile(spot, []byte(templates.Dump(t)), fsio.NewDurable); err != "" {
		return nil, sprintf(msgNotSaved, line(err))
	}
	entry := templates.ReadEntry(templates.LayerDraft, base, spot, true)
	problems := append([]rules.Problem(nil), entry.Notes...)
	if entry.Template != nil {
		c := flow.LoadCatalog(root)
		problems = append(problems, templates.Check(entry.Template, templates.TypesFor(conf, root, layer), &c)...)
		t = entry.Template
	}
	return &Made{
		Name: name, Rel: rel, Target: targetRel, Layer: layer, SHA: entry.SHA,
		Tree: found.Tree, TreeRoot: base, Template: t, Notes: notes, Problems: problems,
	}, ""
}
