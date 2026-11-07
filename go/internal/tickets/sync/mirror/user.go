package mirror

import (
	"crypto/sha256"
	"encoding/binary"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
)

// `ccnavi user mirror` の中身。プロジェクトのユーザのチェックアウト（projects/<名前>/）のミラーと skills/ は
// 発言ごとの写し（Sync）の対象外なので、ユーザが見たいときに調べ（Prepare。何も書かない）、押したときに
// 揃える（Apply。ワークツリーに書くだけで、コミットはしない）。Apply は見せた差分の expect が今の差分と
// 一致するときだけ書く。

// JSONVersion は PlanJSON・AppliedJSON の version。
const JSONVersion = 1

// State はプロジェクト 1 つのミラーの状態。値は JSON の state の表記。
type State string

const (
	StateSynced  State = "synced"
	stateDiffers State = "differs"
	StateError   State = "error"
)

// stale は見せた差分と書く直前の差分がずれたときの文。拡張はこの文で読み直しを決める。
const stale = "内容が更新されました。最新の差分を読み直してから、もう一度実行してください"

const (
	nothingToSync = "揃えるミラーはありません。何も書いていません"
	noProject     = "プロジェクトがありません: "
)

// Project はプロジェクト 1 つのミラーの状態。Changes はミラー（.ccnavi/…）と skills/ の変更を合わせたもの。
type Project struct {
	Name    string
	Root    string
	State   State
	Changes []Change
	Error   string
	// Selected は名前で選ばれたか（名前を渡さなければ全部）。
	Selected bool
	// MirrorPath はミラーの置き場（絶対）。中身の差を人が見るときの案内に使う。
	MirrorPath string
}

// expect はこのプロジェクトだけを揃えるときの照合の値。差分が無ければ空。
func (p *Project) expect() string {
	if p.State != stateDiffers {
		return ""
	}
	return expectOf([]*Project{p})
}

// Plan は Prepare の結果。
type Plan struct {
	Projects []*Project
	// Error は全体を止める誤り（共通レイヤーが読めない、名前がプロジェクトに無いなど）。
	Error string
	// CommonEmpty は写すものが無い（正常。揃えても何も起きない）。
	CommonEmpty bool
	// CommonPath はワークスペースの共通レイヤーの置き場（絶対）。
	CommonPath string
}

// Chosen は揃える対象（選ばれた、差分のあるプロジェクト）。
func (p *Plan) Chosen() []*Project {
	var out []*Project
	for _, project := range p.Projects {
		if project.Selected && project.State == stateDiffers {
			out = append(out, project)
		}
	}
	return out
}

// Expect は対象をまとめて揃えるときの照合の値。対象が無ければ空。
func (p *Plan) Expect() string {
	chosen := p.Chosen()
	if len(chosen) == 0 {
		return ""
	}
	return expectOf(chosen)
}

// expectOf は名前順に、名前と変更（パス・種類・中身の要約）を長さ付きで連結した SHA-256。中身まで入れるのは、
// 同じ名前のファイルで共通レイヤーの中身だけが変わったときに、見せていない内容を書かないため。
func expectOf(projects []*Project) string {
	sum := digest.New(digest.SHA256)
	framed := func(value string) {
		_ = binary.Write(sum, binary.BigEndian, uint32(len(value)))
		sum.Write([]byte(value))
	}
	sorted := slices.Clone(projects)
	slices.SortFunc(sorted, func(a, b *Project) int { return strings.Compare(a.Name, b.Name) })
	for _, project := range sorted {
		framed(project.Name)
		changes := slices.Clone(project.Changes)
		slices.SortFunc(changes, func(a, b Change) int { return strings.Compare(a.Rel, b.Rel) })
		for _, c := range changes {
			framed(c.Rel)
			framed(string(c.Action()))
			content := c.content
			if c.remove {
				content = nil
			}
			raw := sha256.Sum256(content)
			sum.Write(raw[:])
		}
	}
	return digest.HexSum(sum)
}

// Prepare はミラーの状態を調べる。何も書かない。wanted が空なら全プロジェクト。名前は大文字小文字を区別しない。
// 共通レイヤーか工程スキルが読めなければ、どのプロジェクトでも同じ理由なので全体の誤りにする。
func Prepare(conf *config.Settings, root string, wanted []string) *Plan {
	plan := &Plan{CommonPath: conf.CommonLayerDir(root)}
	found := tree.Projects(conf.Projects)
	names := map[string]bool{}
	for _, t := range found {
		names[pathcase.Fold(t.Name)] = true
	}
	chosen := names
	if len(wanted) > 0 {
		var absent []string
		chosen = map[string]bool{}
		for _, name := range wanted {
			if !names[pathcase.Fold(name)] {
				absent = append(absent, name)
			}
			chosen[pathcase.Fold(name)] = true
		}
		if len(absent) > 0 {
			plan.Error = noProject + strings.Join(absent, ", ")
			return plan
		}
	}
	commonFiles, why := commonSources(conf, root)
	var skillFiles map[string][]byte
	if why == "" {
		skillFiles, why = skillSources(root)
	}
	if why != "" {
		plan.Error = why
		return plan
	}
	plan.CommonEmpty = len(commonFiles) == 0 && len(skillFiles) == 0
	for _, t := range found {
		project := &Project{Name: t.Name, Root: t.Root, State: StateSynced, Selected: chosen[pathcase.Fold(t.Name)],
			MirrorPath: conf.CommonLayerDir(t.Root)}
		plan.Projects = append(plan.Projects, project)
		changes, problem := commonPlan(conf, root, t.Root)
		if problem == "" {
			var skills []Change
			skills, problem = skillPlan(root, t.Root)
			changes = append(changes, skills...)
		}
		switch {
		case problem != "":
			project.State, project.Error = StateError, problem
		case len(changes) > 0:
			project.State, project.Changes = stateDiffers, changes
		}
	}
	return plan
}

// Synced は揃えたプロジェクト 1 つと、書いたファイル（ツリーからの相対）。
type Synced struct {
	Name  string
	Files []string
}

// Applied は Apply の結果。
type Applied struct {
	OK     bool
	Error  string
	Synced []Synced
}

// Apply は対象のミラーを揃える。expect が今の差分の値と一致するときだけ書く（空の expect も通さない。見せていない
// 内容を書くため）。プロジェクトごとに skills/ を先、ミラーを後に書き、skills/ が失敗したらミラーは書かない。
// 失敗したプロジェクトは Synced に入れず、名前を添えて Error にまとめる。書けた分は残り、次の Prepare が残りの
// 差分だけを出す。
func Apply(conf *config.Settings, plan *Plan, expect string) *Applied {
	if plan.Error != "" {
		return &Applied{Error: plan.Error}
	}
	chosen := plan.Chosen()
	if len(chosen) == 0 {
		return &Applied{Error: nothingToSync}
	}
	if expect == "" || expect != plan.Expect() {
		return &Applied{Error: stale}
	}
	done := &Applied{OK: true}
	var failures []string
	for _, project := range chosen {
		var skills, mirrored []Change
		for _, c := range project.Changes {
			if c.IsSkill() {
				skills = append(skills, c)
			} else {
				mirrored = append(mirrored, c)
			}
		}
		failed := ""
		if len(skills) > 0 {
			failed = applySkills(skills)
		}
		if failed == "" && len(mirrored) > 0 {
			failed = applyCommon(conf, project.Root, mirrored)
		}
		if failed != "" {
			failures = append(failures, project.Name+": "+failed)
			continue
		}
		files := make([]string, len(project.Changes))
		for i, c := range project.Changes {
			files[i] = c.Rel
		}
		done.Synced = append(done.Synced, Synced{Name: project.Name, Files: files})
	}
	if len(failures) > 0 {
		done.OK = false
		done.Error = strings.Join(failures, "; ")
	}
	return done
}

func projectJSON(p *Project) *pyjson.Object {
	changes := make([]any, len(p.Changes))
	for i, c := range p.Changes {
		changes[i] = pyjson.NewObject("rel", c.Rel, "action", string(c.Action()))
	}
	return pyjson.NewObject("name", p.Name, "state", string(p.State), "changes", changes, "error", p.Error,
		"expect", p.expect())
}

// PlanJSON は見るだけの結果の JSON。載せるのは選ばれたプロジェクトだけ。形は拡張との契約。
func PlanJSON(plan *Plan) *pyjson.Object {
	if plan.Error != "" {
		return pyjson.NewObject("version", JSONVersion, "ok", false, "error", plan.Error, "common_empty", false,
			"projects", []any{}, "expect", "")
	}
	projects := []any{}
	for _, p := range plan.Projects {
		if p.Selected {
			projects = append(projects, projectJSON(p))
		}
	}
	return pyjson.NewObject("version", JSONVersion, "ok", true, "error", "", "common_empty", plan.CommonEmpty,
		"projects", projects, "expect", plan.Expect())
}

// AppliedJSON は揃えた結果の JSON。形は拡張との契約。
func AppliedJSON(done *Applied) *pyjson.Object {
	synced := make([]any, len(done.Synced))
	for i, s := range done.Synced {
		synced[i] = pyjson.NewObject("name", s.Name, "files", slices.Clone(s.Files))
	}
	return pyjson.NewObject("version", JSONVersion, "ok", done.OK, "error", done.Error, "synced", synced)
}
