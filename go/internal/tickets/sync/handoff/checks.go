package handoff

import (
	"bytes"
	"errors"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/infra/yamljson"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/config/check"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/proposal/templates"
)

// shSaidLimit は sh -n の苦情を理由に入れる字数。
const shSaidLimit = 400

// defenseIDPrefix はレイヤーの rules.yml に利用者が書く防御のルールの id の印（ccnavi 自身の保護は組み込みの
// builtin:guard-*）。
const defenseIDPrefix = "guard-"

// defenseShown は防御の記述を動かす理由に並べる件数。
const defenseShown = 5

const (
	settingsFile      = "settings.json"
	settingsLocalFile = "settings.local.json"
	shExt             = ".sh"
)

const (
	msgRewritten    = "下書きを読んでいるあいだに書き換わりました。もう一度見てください"
	msgCommonRival  = "%s に同名で中身の違う %s がある。共通へ取り込むと %s の側が使われなくなる。先に %s の側を直す"
	msgLayerRival   = "共通に同名で中身の違う %s がある。取り込んでも %s の側は使われない。別の名前にする"
	msgRulesUnread  = "ルールファイルとして読めず、防御の記述が変わるかを確かめられない"
	msgNoID         = "id の無いルール"
	msgMovedOf      = "%s の %s"
	msgDenyMoved    = "deny の %s（消す・変える）"
	msgMoves        = "防御の記述を動かす（%s%s）"
	msgMovesMore    = "ほか"
	msgCRLF         = "CR の改行（CRLF）がある。sh は CRLF の行を読めず、hook とラッパーが全部止まる。LF に直す"
	msgNoJSON       = "JSON として読めない（%s）"
	msgNoSh         = "sh が見つからないので、文法を確かめられない（確かめられないものは取り込まない）"
	msgShFailed     = "sh -n を実行できない（%s）"
	msgShSyntax     = "sh -n が文法の誤りを言った（%s）"
	msgShTimeout    = "Command '%s' timed out after %d seconds"
	hiddenProbeHead = 12
)

// checkContents は種類ごとの中身の検査。同じ回に選んだ対象は、取り込んだあとの状態で検査する。
func checkContents(conf *config.Settings, root string, plan *Plan) {
	var readable, chosen []*Entry
	for _, e := range plan.Entries {
		if e.raw != nil && len(e.Errors) == 0 {
			readable = append(readable, e)
			if e.Selected {
				chosen = append(chosen, e)
			}
		}
	}
	var catalog *flow.Catalog
	for _, e := range readable {
		together := chosen
		if !e.Selected {
			together = append(append([]*Entry(nil), chosen...), e)
		}
		switch {
		case e.Kind.ID == Template:
			if catalog == nil {
				c := flow.LoadCatalog(root)
				catalog = &c
			}
			checkTemplate(conf, root, e, together, catalog)
		case e.Kind.ID == layerConfig && e.configKind != "":
			said(e, check.FileProblems(overlay(conf, together), root, e.configKind, e.layer))
			if e.configKind == config.KindRules {
				e.Defense = defenseChange(e.current, e.raw)
			}
		case e.Kind.ID == messages:
			checkMessages(e)
		case e.Kind.ID == defense:
			checkDefense(e)
		}
	}
}

// overlay は取り込んだあとの状態の設定。レイヤーの設定の置き場を下書きへ差し替える。
func overlay(conf *config.Settings, entries []*Entry) *config.Settings {
	out := *conf
	out.ProjectRulesFiles = copied(conf.ProjectRulesFiles)
	out.ProjectPhasesFiles = copied(conf.ProjectPhasesFiles)
	out.ProjectRiskFiles = copied(conf.ProjectRiskFiles)
	for _, e := range entries {
		if e.Kind.ID != layerConfig || e.raw == nil || e.configKind == "" {
			continue
		}
		if e.layer == config.LayerCommon {
			switch e.configKind {
			case config.KindRules:
				out.Rules = e.draft
			case config.KindPhases:
				out.Phases = e.draft
			case config.KindRisk:
				out.Risk = e.draft
			}
		} else if e.layer != "" {
			swaps := map[config.LayerKind]map[string]string{
				config.KindRules:  out.ProjectRulesFiles,
				config.KindPhases: out.ProjectPhasesFiles,
				config.KindRisk:   out.ProjectRiskFiles,
			}[e.configKind]
			swaps[e.layer] = e.draft
		}
	}
	return &out
}

func copied(m map[string]string) map[string]string {
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func said(e *Entry, problems []rules.Problem) {
	for _, p := range problems {
		text := p.Detail
		if p.Rule != "" {
			text = p.Rule + ": " + p.Detail
		}
		switch p.Severity {
		case rules.Error:
			e.Errors = append(e.Errors, text)
		case rules.Warn:
			e.Warnings = append(e.Warnings, text)
		}
	}
}

// checkMessages は文面ファイルの下書き。取り込んだあとに --lint で落ちる文（欄・差し込みの名前・`次:` の数などを
// 外して拒否や促しの型を弱める文）は取り込ませない。
func checkMessages(e *Entry) {
	if !utf8.Valid(e.raw) {
		e.Errors = append(e.Errors, msgNotUTF8)
		return
	}
	path := e.draft
	if path == "" {
		path = e.Rel
	}
	_, problems := texts.CheckText(string(e.raw), path)
	said(e, problems)
}

var decisions = []string{"deny", "ask", "allow"}

type ruleKey struct{ decision, id string }

// ruleRows はルールファイルの各ルールを、(判定, id) → 中身（キーを並べた JSON）の組にする。id の無いルールは
// 中身そのものをキーにする。読めなければ nil と偽。
func ruleRows(raw []byte) (map[ruleKey][]string, bool) {
	if raw == nil {
		return map[ruleKey][]string{}, true
	}
	// ファイルが無い（nil）か空なら、ルールは 0 件として読める。形が違うもの（UTF-8 でない・YAML でない・最上位がマッピングでない）は読めない。
	text := string(yaml.TrimBOM(raw))
	if !utf8.Valid([]byte(text)) {
		return nil, false
	}
	data, err := yaml.Load(text)
	if err != nil {
		return nil, false
	}
	if data == nil {
		return map[ruleKey][]string{}, true
	}
	m, ok := data.(*yaml.Map)
	if !ok {
		return nil, false
	}
	rows := map[ruleKey][]string{}
	// 判定（deny・ask・allow）ごとに、ルール 1 件を (判定, id) の行にする。id が無いルールは、中身（キーを並べた JSON）を id の代わりにする。
	// 同じキーが複数あっても数えられるよう、中身は並びで持つ。
	for _, decision := range decisions {
		value := m.Get(decision)
		if !pyfmt.Truthy(value) {
			continue
		}
		items, ok := value.([]any)
		if !ok {
			return nil, false
		}
		for _, item := range items {
			body, err := pyjson.Dumps(yamljson.ToJSON(item), pyjson.Options{RawUnicode: true, SortKeys: true})
			if err != nil {
				return nil, false
			}
			key := ruleKey{decision, "#" + body}
			if rule, ok := item.(*yaml.Map); ok {
				if id := rule.Get("id"); pyfmt.Truthy(id) {
					key.id = pyfmt.Str(id)
				}
			}
			rows[key] = append(rows[key], body)
		}
	}
	for k := range rows {
		sort.Strings(rows[k])
	}
	return rows, true
}

// defenseChange は rules.yml の下書きが防御の記述を動かすなら、ターミナルに限る理由。動かさなければ空。
// 防御の記述とみなすのは、guard- で始まる id のルール（足す・消す・判定を移す・except を含む中身を変える）と、
// 今ある deny（消す・中身を変える）。deny を足すのは止める側への変更なので数えない。どちらかが読めなければ、
// 確かめられないのでターミナルに限る。
func defenseChange(current, raw []byte) string {
	before, ok1 := ruleRows(current)
	after, ok2 := ruleRows(raw)
	if !ok1 || !ok2 {
		return msgRulesUnread
	}
	// 1. 前後どちらかにある (判定, id) を集め、判定 → id の順に並べて比べる。
	keys := map[ruleKey]bool{}
	for k := range before {
		keys[k] = true
	}
	for k := range after {
		keys[k] = true
	}
	ordered := make([]ruleKey, 0, len(keys))
	for k := range keys {
		ordered = append(ordered, k)
	}
	sort.Slice(ordered, func(i, j int) bool {
		if ordered[i].decision != ordered[j].decision {
			return ordered[i].decision < ordered[j].decision
		}
		return ordered[i].id < ordered[j].id
	})
	// 2. 動いた行のうち防御の記述にあたるものを集める。guard- で始まる id は大文字小文字を畳んで見る。deny は、今あるものが消える・変わるときだけ数える。
	var moved []string
	for _, k := range ordered {
		b, inBefore := before[k]
		a, inAfter := after[k]
		if inBefore == inAfter && equalStrings(b, a) {
			continue
		}
		shown := k.id
		if strings.HasPrefix(k.id, "#") {
			shown = msgNoID
		}
		switch {
		case strings.HasPrefix(pyfmt.Casefold(k.id), defenseIDPrefix):
			moved = append(moved, sprintf(msgMovedOf, k.decision, shown))
		case k.decision == "deny" && inBefore:
			moved = append(moved, sprintf(msgDenyMoved, shown))
		}
	}
	if len(moved) == 0 {
		return ""
	}
	more := ""
	if len(moved) > defenseShown {
		moved, more = moved[:defenseShown], msgMovesMore
	}
	return sprintf(msgMoves, strings.Join(moved, "、"), more)
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func checkTemplate(conf *config.Settings, root string, e *Entry, together []*Entry, catalog *flow.Catalog) {
	layer := e.layer
	if layer == "" {
		layer = config.LayerSelf
	}
	read := templates.ReadEntry(layer, root, e.draft, true)
	if !bytes.Equal(read.Raw, e.raw) {
		e.Errors = append(e.Errors, msgRewritten)
		return
	}
	said(e, read.Notes)
	t := read.Template
	if t == nil {
		return
	}
	// 取り込んだあとの設定（phases.yml の下書きを差し替えたもの）で型を検査する。同じ回に phases.yml の下書きがあれば、足した型も使える。
	types := templates.TypesFor(overlay(conf, together), root, e.layer)
	said(e, templates.Check(t, types, catalog))
	// コメントなど、読んだ中身に出ない場所の文字も見る（CRLF の改行は差分が見せる）。
	text := strings.ReplaceAll(decodeReplace(e.raw, true), "\r\n", "\n")
	if hidden := templates.HiddenProblem(text); hidden != "" {
		probe := pyfmt.Head(hidden, hiddenProbeHead)
		named := false
		for _, have := range e.Errors {
			if strings.Contains(have, probe) {
				named = true
				break
			}
		}
		if !named {
			e.Errors = append(e.Errors, hidden)
		}
	}
	templateCollisions(conf, root, e, t, together)
}

type rivalKey struct{ layer, name string }

type rivalBody struct{ rel, text string }

// templateCollisions は、取り込むと共通とレイヤーに同名で中身の違うものが並ぶ形になるなら断る（統合結果は
// その形のレイヤーの側を使わない error にする）。比べる相手は、同じ回に取り込む下書きを優先し、無ければ今の置き場のもの。
func templateCollisions(conf *config.Settings, root string, e *Entry, t *templates.Template, together []*Entry) {
	// 同名のテンプレートが共通とレイヤーに並んで中身が違うと、統合結果がレイヤーの側を使わない error になる。
	// 名前は大文字小文字を畳んで比べる。比べる相手は、同じ回に取り込む下書き → 今の置き場の順に優先する。
	mine := templates.Dump(t)
	name := FoldPart(t.Name)
	others := map[rivalKey]rivalBody{}
	var order []rivalKey
	put := func(k rivalKey, v rivalBody) {
		if _, ok := others[k]; !ok {
			order = append(order, k)
		}
		others[k] = v
	}
	scanned := templates.Load(conf, root)
	for _, view := range scanned.All() {
		for _, other := range view.Entries {
			if other.Template != nil {
				put(rivalKey{view.Name, FoldPart(other.Name)}, rivalBody{other.Rel, templates.Dump(other.Template)})
			}
		}
	}
	for _, other := range together {
		if other == e || other.Kind.ID != Template || other.raw == nil {
			continue
		}
		if parsed, _ := templates.Parse(other.raw, ""); parsed != nil {
			put(rivalKey{other.layer, FoldPart(parsed.Name)}, rivalBody{other.Rel, templates.Dump(parsed)})
		}
	}
	if e.layer == config.LayerCommon {
		for _, k := range order {
			v := others[k]
			if k.layer != config.LayerCommon && k.name == name && v.text != mine {
				e.Errors = append(e.Errors, sprintf(msgCommonRival, k.layer, v.rel, k.layer, k.layer))
			}
		}
		return
	}
	if rival, ok := others[rivalKey{config.LayerCommon, name}]; ok && rival.text != mine {
		e.Errors = append(e.Errors, sprintf(msgLayerRival, rival.rel, e.layer))
	}
}

func checkDefense(e *Entry) {
	parts := strings.Split(e.key, "/")
	base := parts[len(parts)-1]
	switch {
	case strings.HasSuffix(base, shExt):
		if bytes.IndexByte(e.raw, '\r') >= 0 {
			e.Errors = append(e.Errors, msgCRLF)
			return
		}
		if why := shSyntax(e.draft); why != "" {
			e.Errors = append(e.Errors, why)
		}
	case base == settingsFile || base == settingsLocalFile:
		if _, err := pyjson.Loads(string(e.raw)); err != nil {
			e.Errors = append(e.Errors, sprintf(msgNoJSON, line(err.Error())))
		}
	}
}

// shSyntax は sh -n で読めるか。読めなければ理由。sh が無い機械では確かめられないので断る。
func shSyntax(path string) string {
	ran, err := shell.Script([]string{"-n", path}, "", nil, shell.SyntaxTimeout)
	switch {
	case errors.Is(err, shell.ErrNoShell):
		return msgNoSh
	case err != nil:
		return sprintf(msgShFailed, line(fsio.ErrorText(err)))
	case ran.TimedOut:
		return sprintf(msgShFailed, line(sprintf(msgShTimeout,
			pyfmt.Repr([]any{ran.Shell, "-n", path}), int(shell.SyntaxTimeout/time.Second))))
	}
	if ran.Code != 0 {
		out := string(ran.Stderr)
		if out == "" {
			out = string(ran.Stdout)
		}
		return sprintf(msgShSyntax, pyfmt.Head(line(out), shSaidLimit))
	}
	return ""
}

// decodeReplace は UTF-8 として読み、不正なバイトは置換文字にする（sig なら先頭の BOM を外す）。改行は読み替えない。
// fsio.Decode は改行をそろえるので、CR（ASCII なので並びの途中に来ない）で切って復号し、つなぎ直す。
func decodeReplace(raw []byte, sig bool) string {
	if sig {
		raw = yaml.TrimBOM(raw)
	}
	text, _ := fsio.DecodeBytes(raw, fsio.Replace)
	return text
}

// DraftText は下書きの本文を、そのまま見せる形の文字列にする（BOM を外し、不正なバイトは置換文字にする）。
func DraftText(raw []byte) string { return decodeReplace(raw, true) }
