package core

import (
	"fmt"
	"os"
	"regexp"
	"sort"
	"strings"

	"ccnavi/internal/harness/scenario/host"
	"ccnavi/internal/infra/yaml"
)

// 回す相手（実行器の --agent）。claude は本物の claude -p で回す。script は step の script を台本のスタブ
// （teststub claude）で回す。シナリオのファイルには書かない（1 本を両方で回す）。
const (
	AgentClaude = "claude"
	AgentScript = "script"
)

// 人間役の API 操作の種類（human_api の op）。
const (
	OpThread  = "thread"
	OpResolve = "resolve"
	OpApprove = "approve"
)

// HumanOps は human_api に書ける op。
var HumanOps = []string{OpThread, OpResolve, OpApprove}

// HumanRunScripts は human_run で実行器が人間役として打てる sh（ワークスペースルートからの相対）と、確認文に出す説明。
// argv は実行器が組み、yml には書かせない（本物のチケットの識別子や --root を書けないように）。
// 行を足すのは、必要なシナリオができたときだけ。
var HumanRunScripts = map[string]struct{ Path, What string }{
	"agree": {".ccnavi/scripts/ccnavi-user-agree.sh", "この回で増えた test-repo の提案を承認し、push する"},
}

var sha40 = regexp.MustCompile(`^[0-9a-fA-F]{40}$`)

// setup.git で、対象の外へ出られる git の指定。
var gitEscapes = []string{"-C", "--git-dir", "--work-tree"}

// git の先頭（サブコマンドより前）に置くと、設定や実行ファイルを差し替えられる指定。
var gitGlobalEscapes = []string{"-c", "--config-env", "--exec-path"}

// File は setup.files の 1 件。Append は、対象にある管理下のファイルへの追記（作って消す形ではない）。
type File struct {
	Rel    string
	Body   string
	Append bool
}

// IssueSpec は setup.issue（回すたびに issue を 1 つ作る）。
type IssueSpec struct {
	Repo  string
	Title string
	Body  string
	Close bool
}

// Setup は setup の欄。
type Setup struct {
	Files []File
	Issue *IssueSpec
	// Git は対象で打つ git（argv の列）。
	Git [][]string
	// ResetBase は setup.reset.base（小文字の 40 桁。空なら reset なし）。
	ResetBase string
	// Host は setup.host（書かなければ real）。
	Host      host.Kind
	HostState map[string]any
	// Other は files 以外に書いてある欄の名前（スタブで回せるかの判定に使う）。
	Other []string
}

// Op は human_api の操作 1 件。
type Op struct {
	Op     string
	Body   string
	As     string
	Thread string
}

// Text は操作の表示（種類と名前。本文は出さない）。
func (o Op) Text() string {
	name := o.As
	if name == "" {
		name = o.Thread
	}
	if name == "" {
		return o.Op
	}
	return fmt.Sprintf("%s（%s）", o.Op, name)
}

// RunSpec は human_run の step（script は HumanRunScripts のキー）。
type RunSpec struct {
	Script  string
	Answers []string
}

// Text は確認文に出す、実行器が打つ sh の説明。
func (r RunSpec) Text() string {
	s := HumanRunScripts[r.Script]
	name := s.Path
	if i := strings.LastIndex(name, "/"); i >= 0 {
		name = name[i+1:]
	}
	return fmt.Sprintf("`%s`（%s）", name, s.What)
}

// Cond は expect.decisions・decisions_none・expect_after.decisions の 1 条件。
// 選ぶ欄（Event・Tool・SubjectContains）と、判定を決める欄（Decision・DecisionNot・Code・RulesInclude・Hinted）。
type Cond struct {
	Event           string   `json:"event,omitempty"`
	Tool            string   `json:"tool,omitempty"`
	SubjectContains string   `json:"subject_contains,omitempty"`
	Decision        string   `json:"decision,omitempty"`
	DecisionNot     string   `json:"decision_not,omitempty"`
	Code            string   `json:"code,omitempty"`
	RulesInclude    []string `json:"rules_include,omitempty"`
	Hinted          *bool    `json:"hinted,omitempty"`
	// MinCount は一致が何件要るか（0 は 1 件）。
	MinCount int `json:"min_count,omitempty"`
}

// StateCheck は expect.state の 1 件（コマンドを打って出力を見る）。
type StateCheck struct {
	Run               string  `json:"run"`
	Cwd               string  `json:"cwd,omitempty"`
	ExpectContains    *string `json:"expect_contains,omitempty"`
	ExpectNotContains *string `json:"expect_not_contains,omitempty"`
}

// Expect は期待。
type Expect struct {
	Decisions           []Cond
	DecisionsNone       []Cond
	FilesUnchanged      []string
	State               []StateCheck
	TranscriptForbidden []string
	Judge               []string
}

// Step は手順の 1 つ。
type Step struct {
	Prompt         string
	Human          bool
	Instruction    string
	Check          []string
	Resume         bool
	Script         []any
	AfterStopBlock []any
	// ExpectAfter は step の終わりの期待（decisions と state だけ）。
	ExpectAfter *Expect
	HumanAPI    []Op
	HumanRun    *RunSpec
}

// Scenario は読んだシナリオ。Raw は digest のための、既定値を足した読んだままの値。
type Scenario struct {
	ID             string
	Title          string
	Layer          string
	Reqs           []string
	Status         string
	Model          string
	BudgetUSD      float64
	PermissionMode string
	TestRepo       bool
	Unconfirmed    []string
	Setup          Setup
	Steps          []Step
	Expect         Expect
	Agent          string
	// PromptSteps は人間の操作でない step の数。本物で回すとき、step ごとの費用の上限は BudgetUSD をこれで割る。
	PromptSteps int
	Raw         any
}

// IsScripted はスタブ（--agent script）で回すシナリオか。
func (s *Scenario) IsScripted() bool { return s.Agent == AgentScript }

// HostKind は setup.host の値。欄が無ければ real。
func (s *Scenario) HostKind() host.Kind {
	if s.Setup.Host == "" {
		return host.Default
	}
	return s.Setup.Host
}

// UsesTarget は使い捨てのリポジトリを触る setup（git・reset・追記・スタブを使う相手）があるか。
func (s *Scenario) UsesTarget() bool {
	appends := false
	for _, f := range s.Setup.Files {
		appends = appends || f.Append
	}
	return len(s.Setup.Git) > 0 || s.Setup.ResetBase != "" || appends || host.UsesStubGit(s.HostKind())
}

// UnscriptedStep は script を持たない、人間の操作でない最初の step の番号（1 始まり）。無ければ 0。
func (s *Scenario) UnscriptedStep() int {
	for i, st := range s.Steps {
		if !st.Human && len(st.Script) == 0 {
			return i + 1
		}
	}
	return 0
}

// StubUnready は使い捨てのワークスペースでスタブで回せないなら、その理由。回せるなら空。
// 回せるのは、人間の操作が無く、step がすべて script を持ち、setup が作るファイルだけのもの。
func (s *Scenario) StubUnready() string {
	for _, st := range s.Steps {
		if st.Human {
			return "人間の操作の step がある"
		}
	}
	if n := s.UnscriptedStep(); n > 0 {
		return fmt.Sprintf("step %d に script が無い", n)
	}
	if len(s.Setup.Other) > 0 {
		return "setup が作るファイル以外を使う: " + strings.Join(s.Setup.Other, ", ")
	}
	for _, f := range s.Setup.Files {
		if f.Append {
			return "setup.files に追記がある"
		}
	}
	return ""
}

// CheckGitArgv は setup.git の 1 コマンドを検べる。
func CheckGitArgv(argv []string) error {
	if len(argv) == 0 {
		return fmt.Errorf("argv は空でない文字列の配列にする（シェルを介さない）")
	}
	for _, a := range argv {
		if a == "" {
			return fmt.Errorf("argv は空でない文字列の配列にする（シェルを介さない）")
		}
	}
	if argv[0] != "git" {
		return fmt.Errorf("先頭は git に限る: %s", argv[0])
	}
	for _, a := range argv[1:] {
		for _, p := range gitEscapes { // -C<パス>、--git-dir=<パス> も含む
			if strings.HasPrefix(a, p) {
				return fmt.Errorf("%s は対象の外へ出られるので書けない（-C・--git-dir・--work-tree）", a)
			}
		}
	}
	for _, a := range argv[1:] {
		if !strings.HasPrefix(a, "-") {
			break // サブコマンド。これより後ろの -c は、サブコマンドの引数（switch -c など）
		}
		for _, p := range gitGlobalEscapes {
			if strings.HasPrefix(a, p) {
				return fmt.Errorf("%s は git の設定や実行ファイルを差し替えられるので書けない", a)
			}
		}
	}
	return nil
}

// LoadFile はシナリオのファイルを読む。agent は AgentClaude か AgentScript。
func LoadFile(path, agent string) (*Scenario, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return Load(path, string(data), agent)
}

// LoadFromState は回の状態から、その回と同じ相手でシナリオを読み直す。
func LoadFromState(st *State) (*Scenario, error) {
	agent := st.Agent
	if agent == "" {
		agent = AgentClaude
	}
	return LoadFile(st.Scenario, agent)
}

// Load は text（path のファイルの中身）を読んで検べる。誤りは path を頭に付けて返す。
func Load(path, text, agent string) (*Scenario, error) {
	v, err := yaml.Load(text)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	top, ok := v.(*yaml.Map)
	if !ok {
		return nil, fmt.Errorf("%s: id がありません", path)
	}
	for _, key := range []string{"id", "steps"} {
		if !top.Has(key) {
			return nil, fmt.Errorf("%s: %s がありません", path, key)
		}
	}
	l := loader{path: path}
	s := &Scenario{Agent: agent, Model: "haiku", BudgetUSD: 1.0, PermissionMode: "auto"}
	s.ID = top.Text("id")
	s.Title = top.Text("title")
	s.Layer = top.Text("layer")
	s.Status = top.Text("status")
	if top.Has("model") {
		s.Model = top.Text("model")
	}
	if top.Has("permission_mode") {
		s.PermissionMode = top.Text("permission_mode")
	}
	if top.Has("budget_usd") {
		f, ok := number(top.Get("budget_usd"))
		if !ok {
			return nil, l.errf("budget_usd は数です")
		}
		s.BudgetUSD = f
	}
	s.TestRepo, _ = top.Bool("test_repo")
	s.Reqs, _ = yaml.Strings(top.Get("reqs"))
	s.Unconfirmed, _ = yaml.Strings(top.Get("unconfirmed"))

	steps, ok := top.List("steps")
	if !ok {
		return nil, l.errf("steps は list です")
	}
	for i, raw := range steps {
		st, err := l.step(i+1, raw)
		if err != nil {
			return nil, err
		}
		s.Steps = append(s.Steps, st)
		if !st.Human {
			s.PromptSteps++
		}
	}
	if agent != AgentClaude && agent != AgentScript {
		return nil, fmt.Errorf("--agent は %s か %s です", AgentClaude, AgentScript)
	}
	if s.IsScripted() {
		if n := s.UnscriptedStep(); n > 0 {
			return nil, l.errf("step %d --agent script で回すには script が要ります", n)
		}
	}
	if top.Has("expect") {
		e, ok := top.Get("expect").(*yaml.Map)
		if !ok {
			return nil, l.errf("expect は dict です")
		}
		if s.Expect, err = l.expect(e); err != nil {
			return nil, err
		}
	}
	setup, err := l.setup(top.Get("setup"))
	if err != nil {
		return nil, err
	}
	s.Setup = setup
	if err := l.humanAPI(s, top); err != nil {
		return nil, err
	}
	if err := l.humanRun(s, top, steps); err != nil {
		return nil, err
	}
	raw, ok := plain(top).(map[string]any)
	if !ok {
		raw = map[string]any{}
	}
	raw["model"], raw["budget_usd"], raw["permission_mode"] = s.Model, s.BudgetUSD, s.PermissionMode
	if _, has := raw["expect"]; !has {
		raw["expect"] = map[string]any{}
	}
	raw["agent"] = agent
	if s.Setup.ResetBase != "" {
		if su, ok := raw["setup"].(map[string]any); ok {
			su["reset"] = map[string]any{"base": s.Setup.ResetBase}
		}
	}
	s.Raw = raw
	return s, nil
}

type loader struct{ path string }

func (l loader) errf(format string, a ...any) error {
	return fmt.Errorf("%s: %s", l.path, fmt.Sprintf(format, a...))
}

func number(v any) (float64, bool) {
	switch n := v.(type) {
	case int:
		return float64(n), true
	case float64:
		return n, true
	}
	return 0, false
}

// plain は読んだ YAML を map[string]any・[]any に直す（digest の材料）。
func plain(v any) any {
	switch x := v.(type) {
	case *yaml.Map:
		out := map[string]any{}
		for k, val := range x.All() {
			out[fmt.Sprint(k)] = plain(val)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = plain(e)
		}
		return out
	case []byte:
		return string(x)
	}
	return v
}

func (l loader) step(n int, raw any) (Step, error) {
	m, ok := raw.(*yaml.Map)
	if !ok {
		return Step{}, l.errf("step %d は dict です", n)
	}
	st := Step{}
	st.Prompt = m.Text("prompt")
	st.Human, _ = m.Get("human").(bool)
	st.Instruction = m.Text("instruction")
	st.Resume, _ = m.Get("resume").(bool)
	switch c := m.Get("check").(type) {
	case string:
		st.Check = []string{c}
	case []any:
		for _, e := range c {
			st.Check = append(st.Check, fmt.Sprint(e))
		}
	}
	for _, key := range []string{"script", "after_stop_block"} {
		if !m.Has(key) {
			continue
		}
		list, ok := m.Get(key).([]any)
		if !ok {
			return st, l.errf("step %d の %s は手の list です", n, key)
		}
		plainList := plain(list).([]any) // 台本は JSON にして渡す
		if key == "script" {
			st.Script = plainList
		} else {
			st.AfterStopBlock = plainList
		}
	}
	if m.Has("expect_after") {
		if st.Human {
			return st, l.errf("step %d 人間の操作の step には expect_after を付けられません", n)
		}
		after, ok := m.Get("expect_after").(*yaml.Map)
		if !ok {
			return st, l.errf("step %d expect_after は decisions と state を持つ dict です", n)
		}
		var extra []string
		for k := range after.All() {
			if s := fmt.Sprint(k); s != "decisions" && s != "state" {
				extra = append(extra, s)
			}
		}
		if len(extra) > 0 {
			sort.Strings(extra)
			return st, l.errf("step %d expect_after に書けるのは decisions と state だけです: %s", n, strings.Join(extra, ", "))
		}
		e, err := l.expect(after)
		if err != nil {
			return st, err
		}
		st.ExpectAfter = &e
	}
	return st, nil
}

func (l loader) expect(m *yaml.Map) (Expect, error) {
	var e Expect
	var err error
	conds := func(key string) []Cond {
		list, _ := m.Get(key).([]any)
		var out []Cond
		for _, item := range list {
			if err == nil {
				var c Cond
				c, err = l.cond(item)
				out = append(out, c)
			}
		}
		return out
	}
	e.Decisions, e.DecisionsNone = conds("decisions"), conds("decisions_none")
	e.FilesUnchanged, _ = yaml.Strings(m.Get("files_unchanged"))
	e.TranscriptForbidden, _ = yaml.Strings(m.Get("transcript_forbidden"))
	e.Judge, _ = yaml.Strings(m.Get("judge"))
	if list, ok := m.Get("state").([]any); ok {
		for _, item := range list {
			im, ok := item.(*yaml.Map)
			if !ok {
				return e, l.errf("expect.state の要素は dict です")
			}
			c := StateCheck{Run: im.Text("run"), Cwd: im.Text("cwd")}
			if s, ok := im.String("expect_contains"); ok {
				c.ExpectContains = &s
			}
			if s, ok := im.String("expect_not_contains"); ok {
				c.ExpectNotContains = &s
			}
			e.State = append(e.State, c)
		}
	}
	return e, err
}

func (l loader) cond(item any) (Cond, error) {
	m, ok := item.(*yaml.Map)
	if !ok {
		return Cond{}, l.errf("期待の条件は dict です")
	}
	c := Cond{Event: m.Text("event"), Tool: m.Text("tool"), SubjectContains: m.Text("subject_contains"),
		Decision: m.Text("decision"), DecisionNot: m.Text("decision_not"), Code: m.Text("code")}
	c.RulesInclude, _ = yaml.Strings(m.Get("rules_include"))
	if b, ok := m.Get("hinted").(bool); ok {
		c.Hinted = &b
	}
	if n, ok := m.Get("min_count").(int); ok {
		c.MinCount = n
	}
	return c, nil
}

var setupKeys = []string{"files", "issue", "git", "reset", "host", "host_state"}

func (l loader) setup(v any) (Setup, error) {
	var s Setup
	if v == nil {
		return s, nil
	}
	m, ok := v.(*yaml.Map)
	if !ok {
		return s, l.errf("setup は dict です")
	}
	var extra []string
	for k := range m.All() {
		name := fmt.Sprint(k)
		found := false
		for _, want := range setupKeys {
			found = found || want == name
		}
		if !found {
			extra = append(extra, name)
		}
		if name != "files" {
			s.Other = append(s.Other, name)
		}
	}
	sort.Strings(s.Other)
	if len(extra) > 0 {
		sort.Strings(extra)
		return s, l.errf("setup に書けるのは %s です: %s", strings.Join(sortedCopy(setupKeys), ", "), strings.Join(extra, ", "))
	}
	appends := false
	if m.Has("files") && m.Get("files") != nil {
		files, ok := m.Get("files").(*yaml.Map)
		if !ok {
			return s, l.errf("setup.files は パス: 中身 の dict です")
		}
		for k, val := range files.All() {
			rel := fmt.Sprint(k)
			switch x := val.(type) {
			case *yaml.Map:
				var bad []string
				for kk := range x.All() {
					if n := fmt.Sprint(kk); n != "content" && n != "append" {
						bad = append(bad, n)
					}
				}
				if len(bad) > 0 {
					sort.Strings(bad)
					return s, l.errf("setup.files[%s] に書けるのは content と append だけです: %s", rel, strings.Join(bad, ", "))
				}
				body, ok := x.Get("content").(string)
				if !ok {
					return s, l.errf("setup.files[%s] の content は文字列で必須です", rel)
				}
				f := File{Rel: rel, Body: body}
				if x.Has("append") {
					b, ok := x.Get("append").(bool)
					if !ok {
						return s, l.errf("setup.files[%s] の append は true / false です", rel)
					}
					f.Append = b
				}
				appends = appends || f.Append
				s.Files = append(s.Files, f)
			case string:
				s.Files = append(s.Files, File{Rel: rel, Body: x})
			default:
				return s, l.errf("setup.files[%s] は文字列か {content, append} です", rel)
			}
		}
	}
	if m.Has("git") && m.Get("git") != nil {
		list, ok := m.Get("git").([]any)
		if !ok || len(list) == 0 {
			return s, l.errf("setup.git は argv の配列の配列です")
		}
		for _, item := range list {
			argv, ok := yaml.Strings(item)
			var err error
			if !ok {
				err = fmt.Errorf("argv は空でない文字列の配列にする（シェルを介さない）")
			} else {
				err = CheckGitArgv(argv)
			}
			if err != nil {
				return s, l.errf("setup.git %v: %v", item, err)
			}
			s.Git = append(s.Git, argv)
		}
	}
	if m.Has("reset") && m.Get("reset") != nil {
		reset, ok := m.Get("reset").(*yaml.Map)
		keys, _ := reset.StringKeys()
		if !ok || len(keys) != 1 || keys[0] != "base" {
			return s, l.errf("setup.reset は base だけを持つ dict です")
		}
		base, ok := reset.Get("base").(string)
		if !ok || !sha40.MatchString(base) {
			return s, l.errf("setup.reset.base は 40 桁の 16 進のコミット SHA にする（短縮 SHA・ブランチ名・タグ名は受けない）")
		}
		s.ResetBase = strings.ToLower(base)
	}
	if (len(s.Git) > 0 || appends) && s.ResetBase == "" {
		return s, l.errf("setup.git と setup.files の追記は、戻す setup.reset が無いと使えない")
	}
	if m.Has("issue") && m.Get("issue") != nil {
		im, _ := m.Get("issue").(*yaml.Map)
		s.Issue = &IssueSpec{Repo: im.Text("repo"), Title: im.Text("title"), Body: im.Text("body")}
		s.Issue.Close, _ = im.Get("close").(bool)
	}
	hostMap, ok := plain(m).(map[string]any)
	if !ok {
		hostMap = map[string]any{}
	}
	if err := host.Validate(l.path, hostMap); err != nil {
		return s, err
	}
	s.Host = host.Default
	if h, ok := hostMap["host"].(string); ok {
		s.Host = host.Kind(h)
	}
	if st, ok := hostMap["host_state"].(map[string]any); ok {
		s.HostState = st
	}
	return s, nil
}

func sortedCopy(in []string) []string {
	out := append([]string(nil), in...)
	sort.Strings(out)
	return out
}

func (l loader) humanAPI(s *Scenario, top *yaml.Map) error {
	steps, _ := top.Get("steps").([]any)
	named := map[string]bool{}
	for i, raw := range steps {
		m, ok := raw.(*yaml.Map)
		if !ok || !m.Has("human_api") || m.Get("human_api") == nil {
			continue
		}
		where := fmt.Sprintf("step %d human_api", i+1)
		if !s.Steps[i].Human {
			return l.errf("%s は human: true の step にだけ書けます", where)
		}
		if s.HostKind() != host.DockerGitLab {
			return l.errf("%s は setup.host: docker-gitlab のときだけ使えます（今は %s）。"+
				"setup.host を docker-gitlab にするか、human_api を消して instruction で手で操作する", where, s.HostKind())
		}
		ops, ok := m.Get("human_api").([]any)
		if !ok || len(ops) == 0 {
			return l.errf("%s は 1 件以上の操作の list です", where)
		}
		for j, item := range ops {
			om, _ := item.(*yaml.Map)
			kind, _ := om.Get("op").(string)
			valid := false
			for _, k := range HumanOps {
				valid = valid || k == kind
			}
			if om == nil || !valid {
				return l.errf("%s の %d 件目: op は %s のどれかです", where, j+1, strings.Join(HumanOps, " / "))
			}
			op := Op{Op: kind}
			switch kind {
			case OpThread:
				body, ok := om.Get("body").(string)
				if !ok || strings.TrimSpace(body) == "" {
					return l.errf("%s の %d 件目: thread には body（本文）が要ります", where, j+1)
				}
				op.Body = body
				if om.Has("as") && om.Get("as") != nil {
					as, ok := om.Get("as").(string)
					if !ok || strings.TrimSpace(as) == "" {
						return l.errf("%s の %d 件目: as は空でない文字列です", where, j+1)
					}
					op.As = as
					named[as] = true
				}
			case OpResolve:
				name, _ := om.Get("thread").(string)
				if !named[name] {
					return l.errf("%s の %d 件目: resolve の thread %q は、前の thread の as で名前を付けたものだけ指せます", where, j+1, name)
				}
				op.Thread = name
			}
			s.Steps[i].HumanAPI = append(s.Steps[i].HumanAPI, op)
		}
	}
	return nil
}

func (l loader) humanRun(s *Scenario, top *yaml.Map, steps []any) error {
	for i, raw := range steps {
		m, ok := raw.(*yaml.Map)
		if !ok || !m.Has("human_run") || m.Get("human_run") == nil {
			continue
		}
		where := fmt.Sprintf("step %d human_run", i+1)
		if !s.Steps[i].Human {
			return l.errf("%s は human: true の step にだけ書けます", where)
		}
		if m.Has("human_api") && m.Get("human_api") != nil {
			return l.errf("%s は human_api と同じ step には書けません", where)
		}
		if !s.TestRepo {
			return l.errf("%s は test_repo: true のシナリオにだけ書けます", where)
		}
		var names []string
		for k := range HumanRunScripts {
			names = append(names, k)
		}
		sort.Strings(names)
		spec, ok := m.Get("human_run").(*yaml.Map)
		script, _ := spec.Get("script").(string)
		if _, known := HumanRunScripts[script]; !ok || !known {
			return l.errf("%s: script は %s のどれかです", where, strings.Join(names, " / "))
		}
		var extra []string
		for k := range spec.All() {
			if n := fmt.Sprint(k); n != "script" && n != "answers" {
				extra = append(extra, n)
			}
		}
		if len(extra) > 0 {
			sort.Strings(extra)
			return l.errf("%s に書けるのは script と answers だけです: %s", where, strings.Join(extra, ", "))
		}
		run := &RunSpec{Script: script}
		if spec.Has("answers") {
			answers, ok := yaml.Strings(spec.Get("answers"))
			if !ok {
				return l.errf("%s: answers は文字列の list です", where)
			}
			run.Answers = answers
		}
		if strings.TrimSpace(s.Steps[i].Instruction) == "" {
			return l.errf("%s には、端末を作れないときに手で打つ instruction が要ります", where)
		}
		s.Steps[i].HumanRun = run
	}
	return nil
}
