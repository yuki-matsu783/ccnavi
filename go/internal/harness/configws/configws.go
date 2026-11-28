package configws

import (
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
)

// 統合結果を見るテストの土台。

const Home = ".ccnavi"

var fileNames = map[string]string{"rules": "rules.yml", "phases": "phases.yml", "risk": "risks.yml", "sections": "ticket-sections.yml"}

// layerPath はその git プロジェクトルートの設定ファイル（rules / phases / risk / sections）のパス。
func LayerPath(root, kind string) string {
	return filepath.Join(root, Home, "config", "project", fileNames[kind])
}

func Write(t testing.TB, path, text string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func Read(t testing.TB, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	// Windows の git (autocrlf=true) は CRLF で戻すことがあるので、LF にそろえて比べる。
	return strings.ReplaceAll(string(data), "\r\n", "\n")
}

func Exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

// body は書く本文。文字列はそのまま、ほかは JSON にする。
func Body(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	data, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(data)
}

func WriteRules(t testing.TB, root string, v any) { Write(t, LayerPath(root, "rules"), Body(v)) }
func WritePhases(t testing.TB, root, text string) { Write(t, LayerPath(root, "phases"), text) }
func WriteRisk(t testing.TB, root, text string)   { Write(t, LayerPath(root, "risk"), text) }

// buildProject は git 初期化したプロジェクトを 1 つ置く。設定は空でない分だけ置く。
func buildProject(t testing.TB, root string, rules any, phases, risk string) string {
	t.Helper()
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	run.Git(t, root, "init", "--quiet", "-b", "main")
	for _, rel := range keepFiles {
		Write(t, filepath.Join(root, filepath.FromSlash(rel)), "keep\n")
	}
	if rules != nil {
		WriteRules(t, root, rules)
	}
	if phases != "" {
		WritePhases(t, root, phases)
	}
	if risk != "" {
		WriteRisk(t, root, risk)
	}
	run.Git(t, root, "add", "-A")
	run.Git(t, root, "commit", "--quiet", "-m", "init")
	return root
}

// ---- 雛形

// 雛形のワークスペースは 1 度だけ組み、以後はコピーを配る。組み直すと 1 件あたり git が 9 回起き、全件で数百回になる。
// コピーなら git は雛形の 9 回だけで済む。テストごとに別のディレクトリを配るので、テストどうしが状態を共有することはない。
type template struct {
	once sync.Once
	dir  string
}

var (
	templatesMu sync.Mutex
	templates   = map[string]*template{}
)

// templateDir は雛形の置き場。kind ごとにこのプロセスで 1 度だけ組む。guard と risk は base の雛形を写してから足す。
func templateDir(t testing.TB, kind string) string {
	t.Helper()
	templatesMu.Lock()
	tpl, ok := templates[kind]
	if !ok {
		tpl = &template{}
		templates[kind] = tpl
	}
	templatesMu.Unlock()
	tpl.once.Do(func() {
		holder, err := os.MkdirTemp(base.TempBase(), "ccnavi-configws-")
		if err != nil {
			t.Fatal(err)
		}
		base.AtExit(holder)
		dir := filepath.Join(holder, "ws")
		switch kind {
		case KindBase:
			baseTemplate(t, dir)
		case KindGuard, KindRisk:
			if err := copyTree(templateDir(t, KindBase), dir); err != nil {
				t.Fatal(err)
			}
			if kind == KindGuard {
				prepareGuard(t, dir)
			} else {
				prepareRisk(t, dir)
			}
		default:
			t.Fatalf("雛形の種類 %q を知らない", kind)
		}
		tpl.dir = dir
	})
	if tpl.dir == "" {
		t.Fatalf("雛形 %q が組めていない（最初のテストの失敗を見る）", kind)
	}
	return tpl.dir
}

func copyTree(from, to string) error {
	return filepath.WalkDir(from, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(from, path)
		if err != nil {
			return err
		}
		dest := filepath.Join(to, rel)
		if d.IsDir() {
			return os.MkdirAll(dest, 0o755)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		return os.WriteFile(dest, data, info.Mode().Perm()|0o200)
	})
}

// baseTemplate はワークスペースと 2 つのプロジェクト（lib・app）の雛形。
func baseTemplate(t testing.TB, ws string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(ws, ".claude"), 0o755); err != nil {
		t.Fatal(err)
	}
	run.Git(t, ws, "init", "--quiet", "-b", "main")
	Write(t, filepath.Join(ws, ".gitignore"), gitignoreText)
	for _, rel := range keepFiles {
		Write(t, filepath.Join(ws, filepath.FromSlash(rel)), "keep\n")
	}
	WriteRules(t, ws, OwnRules)
	WritePhases(t, ws, OwnPhasesText)
	Write(t, base.CommonPath(ws, "rules"), Body(CommonRules))
	Write(t, base.CommonPath(ws, "risk"), CommonRiskText)
	run.Git(t, ws, "add", "-A")
	run.Git(t, ws, "commit", "--quiet", "-m", "init")
	buildProject(t, filepath.Join(ws, "projects", "lib"), LibRules, LibPhasesText, LibRiskText)
	buildProject(t, filepath.Join(ws, "projects", "app"), nil, "", "")
}

// 雛形の種類。guard は設定ファイルの保護を見る土台（何でも通すルール + 共通設定の phases）、
// risk は risk を見る土台（lib のフェーズに `work` を足したもの）。
const (
	KindBase  = "base"
	KindGuard = "guard"
	KindRisk  = "risk"
)

// openRules は何でも通す。deny が 1 件も無いので、止まったものはルールの外で止まっている。
var OpenRules = Ruleset(nil, []Obj{Rule("anything", "Bash|Read|Write|Edit|NotebookEdit", "regex", ".", "")})

const LibPhasesWork = `version: 1
phases:
  work:
    kind: work
    title: 作業
    review: false
    scope: inherit
`

func prepareGuard(t testing.TB, ws string) {
	Write(t, base.CommonPath(ws, "rules"), Body(OpenRules))
	// 共通設定の phases.yml も守る対象。守ることを見るために置き、ワークツリー側の設定ができるようコミットしておく。
	Write(t, base.CommonPath(ws, "phases"), CommonPhasesText)
	run.Git(t, ws, "add", "-A")
	run.Git(t, ws, "commit", "--quiet", "-m", "common phases")
}

func prepareRisk(t testing.TB, ws string) {
	lib := filepath.Join(ws, "projects", "lib")
	WritePhases(t, lib, LibPhasesWork)
	run.Git(t, lib, "add", "-A")
	run.Git(t, lib, "commit", "--quiet", "-m", "phases")
}

// ---- ワークスペース

// ws はテスト 1 本のワークスペース。t はそのテストの t で、サブテストでは as で差し替える。
type WS struct {
	T                        testing.TB
	Root, Projects, Lib, App string
	// 共通設定の 3 本
	Rules, Phases, Risk string
	Log                 string
	// Env は子の環境に足す分（run.Opts.Env）。ワークスペースの写しごとに持つ。
	Env map[string]string
	// Session は hook の session_id。空なら s1。
	Session string
	// began は SessionStart を渡したセッション。As で写した WS と共有する。
	began *sync.Map
}

func NewWS(t testing.TB, kind string) *WS {
	t.Helper()
	source := templateDir(t, kind)
	root := filepath.Join(base.TempDir(t), "ws")
	if err := copyTree(source, root); err != nil {
		t.Fatal(err)
	}
	projects := filepath.Join(root, "projects")
	return &WS{
		T: t, Root: root, Projects: projects,
		Lib: filepath.Join(projects, "lib"), App: filepath.Join(projects, "app"),
		Rules: base.CommonPath(root, "rules"), Phases: base.CommonPath(root, "phases"), Risk: base.CommonPath(root, "risk"),
		Log:   filepath.Join(root, "logs", "decisions.jsonl"),
		began: &sync.Map{},
	}
}

// Prompt はユーザの発言を 1 件渡す（ミラーや工程スキルをコピーする）。モデルの文脈に足された文も返す。
// そのセッションの始まりを先に 1 度だけ渡し、実行後チェックの基準を取る。
func (w *WS) Prompt(session, guard string) (reason string) {
	w.T.Helper()
	saved := w.Session
	w.Session = session
	defer func() { w.Session = saved }()
	if _, began := w.began.LoadOrStore(session, true); !began {
		start := Body(Obj{"hook_event_name": "SessionStart", "session_id": session, "source": "startup", "cwd": w.Root})
		if done := w.Exec("disable", start, "--mode", "enable"); done.Code != 0 {
			w.T.Fatalf("SessionStart: %s%s", done.Stdout, done.Stderr)
		}
	}
	done := w.HookAs("UserPromptSubmit", guard, "", w.Root, nil)
	if done.Code != 0 {
		w.T.Fatalf("UserPromptSubmit: %s%s", done.Stdout, done.Stderr)
	}
	return w.Reason(done)
}

// StartTicket は親の `ticket start` を打ち、続けてユーザの発言を渡す。出力は start と発言の分をまとめたもの。
func (w *WS) StartTicket(name string) (code int, out string) {
	w.T.Helper()
	started := w.Ccnavi("ticket", "start", name)
	return started.Code, started.Stdout + started.Stderr + w.Prompt("s1", "disable")
}

// as はサブテストの t で動く写し。
func (w *WS) As(t testing.TB) *WS {
	c := *w
	c.T = t
	return &c
}

// project は `projects/<名前>/` に git 初期化したプロジェクトを足す。
func (w *WS) Project(name string, rules any, phases, risk string) string {
	w.T.Helper()
	return buildProject(w.T, filepath.Join(w.Projects, name), rules, phases, risk)
}

// worktree は `.claude/worktrees/<名前>` に、owner（ワークスペースかプロジェクト）から切る。
// あれば（`approval-request` が親の提案を書くために切ったもの）そのまま返す。
func (w *WS) Worktree(owner, name string) string {
	w.T.Helper()
	path := filepath.Join(w.Root, ".claude", "worktrees", name)
	if info, err := os.Stat(path); err != nil || !info.IsDir() {
		run.Git(w.T, owner, "worktree", "add", "--quiet", path, "-b", name)
	}
	return path
}

// homes は承認済みチケットを持ちうるツリー。ワークスペース、プロジェクト、ワークツリー。
func (w *WS) homes() []string {
	found := []string{w.Root, w.Lib, w.App}
	dir := filepath.Join(w.Root, ".claude", "worktrees")
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		found = append(found, filepath.Join(dir, e.Name()))
	}
	return found
}

var parentLine = regexp.MustCompile(`(?m)^parent: (\S+)$`)

// propose は提案を置く。親の提案は親のワークツリー（識別子と同じ名前）の `wip/proposals/`。
// 親のワークツリーが無ければ、先にそのプロジェクト（ワークスペース）から切る。正とするのは親のワークツリーだけなので、
// 提案も承認済みチケットもそこに置く。
func (w *WS) Propose(name, text, project string) string {
	w.T.Helper()
	owner := w.Root
	if project != "" {
		owner = filepath.Join(w.Projects, project)
	}
	treeName := name
	if m := parentLine.FindStringSubmatch(text); m != nil {
		treeName = m[1]
	}
	tree := w.Worktree(owner, treeName)
	// 計画を持つ親には、共通設定にその設定の phases.yml を足した統合結果から、計画が使う定義のコピー（`phases:`）を足す
	// （`ccnavi --plan-order <親> --fill-phases` と同じ）。どちらにも無ければ空のコピー。
	var texts []string
	for _, path := range []string{w.Phases, LayerPath(owner, "phases")} {
		if data, err := os.ReadFile(path); err == nil {
			texts = append(texts, string(data))
		}
	}
	text = samples.InsertPhases(text, samples.CombinedPhases(texts...))
	return Write(w.T, filepath.Join(tree, "wip", "proposals", "todo", name+".md"), text)
}

// proposalPath は提案（`wip/proposals/<置き場>/<名前>.md`）のパス。在る側のツリーを探す。無ければ空。
func (w *WS) ProposalPath(name string, states ...string) string {
	if len(states) == 0 {
		states = []string{"todo", "review"}
	}
	for _, where := range w.homes() {
		for _, place := range states {
			path := filepath.Join(where, "wip", "proposals", place, name+".md")
			if Exists(path) {
				return path
			}
		}
	}
	return ""
}

// approvedPath は承認済みチケットの置き場の下のパス。在る側を探す。
func (w *WS) ApprovedPath(parts ...string) string {
	for _, where := range w.homes() {
		path := filepath.Join(append([]string{where, Home, "approved"}, parts...)...)
		if Exists(path) {
			return path
		}
	}
	return filepath.Join(append([]string{w.Root, Home, "approved"}, parts...)...)
}

func (w *WS) ApprovedCopy(name string) string { return w.ApprovedPath("doing", name+".md") }

// ---- 起動

// Exec は実行ファイルを 1 回起動する。設定の置き場はフラグで渡さない。共通設定の 3 本（--rules / --phases / --risk）も
// 設定を探す先の 2 本（--projects / --project-home）も、診断（--lint / --test / --explain）でだけ有効で、
// hook の判定とチケットのサブコマンドでは無視する。差し替えたいテストは w.rules / w.phases / w.risk に書く。
func (w *WS) Exec(guard, input string, args ...string) run.Result {
	w.T.Helper()
	prefix := []string{"--root", w.Root, "--guard-core-files", guard, "--restore-if-deny", "disable", "--guard-ticket-approval", "disable"}
	return run.Ccnavi(w.T, samples.Place(prefix, args), run.Opts{Input: input, Dir: base.Root(), Env: w.Env})
}

func (w *WS) Ccnavi(args ...string) run.Result { return w.Exec("disable", "", args...) }

// hookAs は hook の payload を 1 件渡す。
func (w *WS) HookAs(event, guard, tool, cwd string, input Obj) run.Result {
	w.T.Helper()
	if input == nil {
		input = Obj{}
	}
	session := w.Session
	if session == "" {
		session = "s1"
	}
	payload := Body(Obj{"hook_event_name": event, "tool_name": tool, "cwd": cwd, "session_id": session, "tool_input": input})
	return w.Exec(guard, payload, "--mode", "enable")
}

// hook は設定ファイルの保護を切った PreToolUse。
func (w *WS) Hook(tool, cwd string, input Obj) run.Result {
	w.T.Helper()
	return w.HookAs("PreToolUse", "disable", tool, cwd, input)
}

// guardedHook は設定ファイルの保護を入れた PreToolUse。
func (w *WS) GuardedHook(tool, cwd string, input Obj) run.Result {
	w.T.Helper()
	return w.HookAs("PreToolUse", "enable", tool, cwd, input)
}

// post は実行後の hook（設定ファイルの保護は切る）。
func (w *WS) Post(tool, cwd string, input Obj) run.Result {
	w.T.Helper()
	return w.HookAs("PostToolUse", "disable", tool, cwd, input)
}

// approve は承認して、承認済みチケットを各ワークツリーにコミットする（本番は write-and-push が行う）。
func (w *WS) Approve() run.Result {
	w.T.Helper()
	done := w.Exec("disable", "y\n", "user", "agree")
	dir := filepath.Join(w.Root, ".claude", "worktrees")
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		path := filepath.Join(dir, e.Name())
		run.Git(w.T, path, "add", "-A")
		run.Git(w.T, path, "commit", "--quiet", "--allow-empty", "-m", "approve")
	}
	return done
}

func (w *WS) MustOK(r run.Result, what string) {
	w.T.Helper()
	if r.Code != 0 {
		w.T.Fatalf("%s: code %d\nstdout: %s\nstderr: %s", what, r.Code, r.Stdout, r.Stderr)
	}
}

// ---- 診断

type Problem struct {
	Severity string `json:"severity"`
	Where    string `json:"where"`
	Detail   string `json:"detail"`
}

func (w *WS) LintProblems(args ...string) []Problem {
	w.T.Helper()
	r := w.Ccnavi(append([]string{"--lint", "--json"}, args...)...)
	var report struct {
		Problems []Problem `json:"problems"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &report); err != nil {
		w.T.Fatalf("--lint --json が JSON を返さない: %v\n%s\n%s", err, r.Stdout, r.Stderr)
	}
	return report.Problems
}

// problems はその深刻度の Problem。where を渡すと、その出どころで先に絞る。
// detail の部分一致だけで見ると、`self` や `lib` のようなありふれた語が別の苦情の文面に紛れていても通ってしまう。
// どの設定の話かは where が持つ。
func (w *WS) Problems(severity, where string, args ...string) []Problem {
	w.T.Helper()
	var out []Problem
	for _, p := range w.LintProblems(args...) {
		if p.Severity == severity && (where == "" || strings.HasPrefix(p.Where, where)) {
			out = append(out, p)
		}
	}
	return out
}

// projectWhere は `--lint` の Problem の出どころのうち、そのプロジェクトのもの（`(projects/lib)`）。
func ProjectWhere(name string) string { return "(projects/" + name + ")" }

// anyDetail は problems のどれかの detail が sub を含むか。
func AnyDetail(ps []Problem, sub string) bool {
	return slices.ContainsFunc(ps, func(p Problem) bool { return strings.Contains(p.Detail, sub) })
}

func AnyWhere(ps []Problem, sub string) bool {
	return slices.ContainsFunc(ps, func(p Problem) bool { return strings.Contains(p.Where, sub) })
}

func (w *WS) MustHaveDetail(ps []Problem, sub string) {
	w.T.Helper()
	if !AnyDetail(ps, sub) {
		w.T.Errorf("detail に %q を含む問題が無い: %+v", sub, ps)
	}
}

// ---- 読み取り

type hookOutput struct {
	HookSpecificOutput struct {
		Decision string `json:"permissionDecision"`
		Reason   string `json:"permissionDecisionReason"`
		Context  string `json:"additionalContext"`
	} `json:"hookSpecificOutput"`
	SystemMessage string `json:"systemMessage"`
}

func (w *WS) parse(r run.Result) hookOutput {
	w.T.Helper()
	var out hookOutput
	if strings.TrimSpace(r.Stdout) == "" {
		return out
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		w.T.Fatalf("hook の出力が JSON でない: %q", r.Stdout)
	}
	return out
}

func (w *WS) Decision(r run.Result) string {
	w.T.Helper()
	return w.parse(r).HookSpecificOutput.Decision
}

func (w *WS) Reason(r run.Result) string {
	w.T.Helper()
	out := w.parse(r).HookSpecificOutput
	if out.Reason != "" {
		return out.Reason
	}
	return out.Context
}

// Record は決定ログの 1 件。
type Record map[string]any

func (r Record) Str(key string) string {
	s, _ := r[key].(string)
	return s
}

// names は記録の rules の記録名（`<相対パス>:<id>`）。
func (r Record) Names() []string {
	var out []string
	items, _ := r["rules"].([]any)
	for _, v := range items {
		out = append(out, v.(string))
	}
	return out
}

// ids は記録の rules を、書いた表記（設定の前置きつき）にしたもの。
func (r Record) Ids() []string {
	var out []string
	for _, n := range r.Names() {
		out = append(out, layerID(n))
	}
	return out
}

// hits は記録の rules のうち、設定のルール。組み込みの deny（`builtin:` で始まる id。共通設定と config の有無によらず
// 常にヒットする）は数えない。
func (r Record) Hits() []string {
	var out []string
	for _, n := range r.Names() {
		if !strings.HasPrefix(n, "builtin:") {
			out = append(out, layerID(n))
		}
	}
	return out
}

func (w *WS) LastRecord() Record {
	w.T.Helper()
	var lines []string
	for _, line := range strings.Split(Read(w.T, w.Log), "\n") {
		if strings.TrimSpace(line) != "" {
			lines = append(lines, line)
		}
	}
	if len(lines) == 0 {
		w.T.Fatal("決定ログが空")
	}
	var rec Record
	if err := json.Unmarshal([]byte(lines[len(lines)-1]), &rec); err != nil {
		w.T.Fatal(err)
	}
	return rec
}

func (w *WS) HitRules() []string {
	w.T.Helper()
	return w.LastRecord().Hits()
}

const (
	commonRuleFile = ".ccnavi/config/common/rules.yml"
	selfRuleFile   = ".ccnavi/config/project/rules.yml"
)

var projectRuleName = regexp.MustCompile(`^projects/([^/]+)/` + regexp.QuoteMeta(selfRuleFile) + `$`)

// recordOf は書いた表記（`x` / `self:x` / `lib:x`）を、決定ログの記録名（`<相対パス>:<id>`）にする。
func recordOf(id string) string {
	if strings.HasPrefix(id, "builtin:") {
		return id
	}
	layer, bare, found := strings.Cut(id, ":")
	switch {
	case !found:
		return commonRuleFile + ":" + id
	case layer == "self":
		return selfRuleFile + ":" + bare
	}
	return "projects/" + layer + "/" + selfRuleFile + ":" + bare
}

// layerID は記録名を、書いた表記（設定の前置きつき）に戻す。この 3 つの場所以外は名前のまま。
func layerID(name string) string {
	if strings.HasPrefix(name, "builtin:") {
		return name
	}
	i := strings.LastIndex(name, ":")
	if i < 0 {
		return name
	}
	path, bare := name[:i], name[i+1:]
	switch path {
	case commonRuleFile:
		return bare
	case selfRuleFile:
		return "self:" + bare
	}
	if m := projectRuleName.FindStringSubmatch(path); m != nil {
		return m[1] + ":" + bare
	}
	return name
}

func (w *WS) AssertDenied(r run.Result, ids ...string) {
	w.T.Helper()
	if d := w.Decision(r); d != "deny" {
		w.T.Errorf("decision = %q, want deny\n%s%s", d, r.Stdout, r.Stderr)
		return
	}
	reason := w.Reason(r)
	for _, id := range ids {
		if !strings.Contains(reason, recordOf(id)) {
			w.T.Errorf("理由に %q が無い: %s", recordOf(id), reason)
		}
	}
}

func (w *WS) AssertNotDenied(r run.Result) {
	w.T.Helper()
	if r.Code != 0 {
		w.T.Errorf("code = %d\n%s", r.Code, r.Stderr)
	}
	if d := w.Decision(r); d == "deny" {
		w.T.Errorf("deny された: %s", r.Stdout)
	}
}

// ---- ticket 本文

type TicketOpts struct {
	Project string
	Parent  string
	Phase   int
	Plan    []string
	Allow   []string
}

// ticketText は提案の本文。親は plan を、子は parent と phase を持つ。project は行き先の設定を決める。
func TicketText(name string, o TicketOpts) string {
	lines := []string{"---", "version: 1", "ticket: " + name}
	if o.Parent == "" {
		lines = append(lines, "mr: 1", "issue: 1")
	}
	if o.Project != "" {
		lines = append(lines, "project: "+o.Project)
	}
	if o.Parent != "" {
		lines = append(lines, "parent: "+o.Parent, "phase: "+Itoa(o.Phase))
	}
	if len(o.Plan) > 0 {
		// 計画のステップは、すぐ前のステップが先行する一直線にする（最初のステップは先行なし）。
		lines = append(lines, "plan:")
		for n, item := range o.Plan {
			if n > 0 {
				lines = append(lines, "  - {type: "+item+", after: ["+Itoa(n)+"]}")
			} else {
				lines = append(lines, "  - "+item)
			}
		}
	}
	lines = append(lines, "human_review:", "  required: false", "  reason: テスト", "title: 作業", "rationale: |", "  理由")
	if len(o.Allow) > 0 {
		lines = append(lines, "allow:")
		for _, g := range o.Allow {
			lines = append(lines, "  - match: Write|Edit", `    glob: "`+g+`"`)
		}
	}
	lines = append(lines, `started_at: ""`, `completed_at: ""`, `base_sha: ""`, "---", "", "本文")
	return strings.Join(lines, "\n") + "\n"
}

func Itoa(n int) string { return strconv.Itoa(n) }

// sortedKeys は map のキーを並べたもの。
func SortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func RemoveFile(path string) error { return os.Remove(path) }

func InsertPhases(text, phasesYML string) string {
	return samples.InsertPhases(text, phasesYML)
}

// ---- 設定ファイルの保護

// runHook は Bash 1 回。設定ファイルの保護を入れ、実行前でバックアップを取り、実行後で戻す。
func (w *WS) RunHook(event string) run.Result {
	w.T.Helper()
	return w.HookAs(event, "enable", "Bash", w.Root, Obj{"command": "ls"})
}

// said は失敗したときに何が起きたかをつける。標準エラーを見ないと理由が出ない。
func Said(r run.Result, note string) string {
	head := ""
	if note != "" {
		head = note + "\n"
	}
	return head + "stdout: " + r.Stdout + "\nstderr: " + r.Stderr
}

// breakAllAndRestore はバックアップを 1 回取らせ、全部を壊し、実行後に全部が戻ったかを確かめる。結果を返す。
func (w *WS) BreakAllAndRestore(paths []string) run.Result {
	w.T.Helper()
	before := map[string]string{}
	for _, path := range paths {
		before[path] = Read(w.T, path)
	}
	w.RunHook("PreToolUse")
	for _, path := range paths {
		text := "version: 1\ndeny: []\n"
		if strings.HasSuffix(path, "phases.yml") {
			text = "version: 1\nphases: {}\n"
		}
		Write(w.T, path, text)
	}
	result := w.RunHook("PostToolUse")
	for _, path := range paths {
		if got := Read(w.T, path); got != before[path] {
			w.T.Errorf("%s が戻らない\n%s", path, Said(result, path))
		}
	}
	if !strings.Contains(result.Stdout, "restored") {
		w.T.Errorf("restored を言わない\n%s", Said(result, ""))
	}
	return result
}

// Judged は 1 件の判定。決定（Verdict）とヒットしたルールの id。
type Judged struct {
	Verdict string
	IDs     []string
}

// Judge は (ツール, 対象) を何件も 1 回の起動で判定し、1 件ごとに決定とヒットしたルールの id を返す。
// `--test-expectations` に渡す。判定は hook の実行前と同じ関数を通り、居場所はワークスペースルート
// （hook に cwd をルートで渡したのと同じ）。guard は設定ファイルの保護（enable / disable）。
func (w *WS) Judge(guard string, calls [][2]string, flags ...string) []Judged {
	w.T.Helper()
	var asks []Obj
	for _, c := range calls {
		asks = append(asks, Obj{"tool": c[0], "subject": c[1]})
	}
	// 期待のタイプは判定に効かない。JSON は YAML としても読める。
	path := Write(w.T, filepath.Join(base.TempDir(w.T), "expectations.yml"), Body(Obj{"ask": asks}))
	r := w.Exec(guard, "", append(slices.Clone(flags), "--test-expectations", path, "--json")...)
	w.MustOK(r, "--test-expectations")
	var out struct {
		Expectations []struct {
			Verdict string `json:"verdict"`
			Rules   []struct {
				ID string `json:"id"`
			} `json:"rules"`
		} `json:"expectations"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil || len(out.Expectations) != len(calls) {
		w.T.Fatalf("判定が %d 件のはず: %v\n%s", len(calls), err, r.Stdout)
	}
	var res []Judged
	for _, e := range out.Expectations {
		j := Judged{Verdict: e.Verdict}
		for _, r := range e.Rules {
			j.IDs = append(j.IDs, r.ID)
		}
		res = append(res, j)
	}
	return res
}

// judgeMany はシェルのコマンドを何本も 1 回の起動で判定する。
func (w *WS) judgeMany(commands []string, flags ...string) []Judged {
	w.T.Helper()
	calls := make([][2]string, len(commands))
	for i, c := range commands {
		calls[i] = [2]string{"Bash", c}
	}
	return w.Judge("enable", calls, flags...)
}

const SettingFilesGuard = "builtin:guard-setting-files"

// assertAllGuarded はどれも設定ファイルの保護で止まる。何本も 1 回の起動で判定する。
func (w *WS) AssertAllGuarded(commands []string, flags ...string) {
	w.T.Helper()
	for i, j := range w.judgeMany(commands, flags...) {
		if j.Verdict != "deny" || !slices.Contains(j.IDs, SettingFilesGuard) {
			w.T.Errorf("止まらない: %q → %s %v", commands[i], j.Verdict, j.IDs)
		}
	}
}

// assertNoneDenied はどれも止まらない。
func (w *WS) AssertNoneDenied(commands []string, flags ...string) {
	w.T.Helper()
	for i, j := range w.judgeMany(commands, flags...) {
		if j.Verdict == "deny" {
			w.T.Errorf("止まった: %q → %v", commands[i], j.IDs)
		}
	}
}

// assertNoneGuarded はどれも設定ファイルの保護にヒットしない。
func (w *WS) AssertNoneGuarded(commands []string, flags ...string) {
	w.T.Helper()
	for i, j := range w.judgeMany(commands, flags...) {
		if slices.Contains(j.IDs, SettingFilesGuard) {
			w.T.Errorf("設定ファイルの保護にヒットした: %q → %v", commands[i], j.IDs)
		}
	}
}
