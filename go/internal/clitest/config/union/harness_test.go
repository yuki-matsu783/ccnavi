package union

import (
	"encoding/json"
	"io/fs"
	"os"
	"os/exec"
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

// 設定の和を見るテストの土台（tests/config/test_config_union.py の ConfigUnionHarness の写し）。

const home = ".ccnavi"

var fileNames = map[string]string{"rules": "rules.yml", "phases": "phases.yml", "risk": "risks.yml"}

// layerPath はその git プロジェクトルートのレイヤーのファイル（rules / phases / risk）のパス。
func layerPath(root, kind string) string {
	return filepath.Join(root, home, "config", "project", fileNames[kind])
}

func gitIn(dir string, args ...string) (string, error) {
	cmd := exec.Command("git", append([]string{"-c", "user.email=t@example.com", "-c", "user.name=t"}, args...)...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	return string(out), err
}

func git(t testing.TB, dir string, args ...string) string {
	t.Helper()
	out, err := gitIn(dir, args...)
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return out
}

func write(t testing.TB, path, text string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func read(t testing.TB, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	// Windows の git (autocrlf=true) は CRLF で戻すことがあるので、LF にそろえて比べる。
	return strings.ReplaceAll(string(data), "\r\n", "\n")
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

// body は書く本文。文字列はそのまま、ほかは JSON にする。
func body(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	data, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(data)
}

func writeRules(t testing.TB, root string, v any) { write(t, layerPath(root, "rules"), body(v)) }
func writePhases(t testing.TB, root, text string) { write(t, layerPath(root, "phases"), text) }
func writeRisk(t testing.TB, root, text string)   { write(t, layerPath(root, "risk"), text) }

// buildProject は git 初期化したプロジェクトを 1 つ置く。レイヤーは空でない分だけ置く。
func buildProject(t testing.TB, root string, rules any, phases, risk string) string {
	t.Helper()
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	git(t, root, "init", "--quiet", "-b", "main")
	for _, rel := range keepFiles {
		write(t, filepath.Join(root, filepath.FromSlash(rel)), "keep\n")
	}
	if rules != nil {
		writeRules(t, root, rules)
	}
	if phases != "" {
		writePhases(t, root, phases)
	}
	if risk != "" {
		writeRisk(t, root, risk)
	}
	git(t, root, "add", "-A")
	git(t, root, "commit", "--quiet", "-m", "init")
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
		holder, err := os.MkdirTemp(base.TempBase(), "ccnavi-union-")
		if err != nil {
			t.Fatal(err)
		}
		base.AtExit(holder)
		dir := filepath.Join(holder, "ws")
		switch kind {
		case kindBase:
			baseTemplate(t, dir)
		case kindGuard, kindRisk:
			if err := copyTree(templateDir(t, kindBase), dir); err != nil {
				t.Fatal(err)
			}
			if kind == kindGuard {
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
	git(t, ws, "init", "--quiet", "-b", "main")
	write(t, filepath.Join(ws, ".gitignore"), gitignoreText)
	for _, rel := range keepFiles {
		write(t, filepath.Join(ws, filepath.FromSlash(rel)), "keep\n")
	}
	writeRules(t, ws, ownRules)
	writePhases(t, ws, ownPhasesText)
	write(t, base.CommonPath(ws, "rules"), body(commonRules))
	write(t, base.CommonPath(ws, "risk"), commonRiskText)
	git(t, ws, "add", "-A")
	git(t, ws, "commit", "--quiet", "-m", "init")
	buildProject(t, filepath.Join(ws, "projects", "lib"), libRules, libPhasesText, libRiskText)
	buildProject(t, filepath.Join(ws, "projects", "app"), nil, "", "")
}

// 雛形の種類。guard は設定ファイルの保護を見る土台（何でも通すルール + 共通レイヤーの phases）、
// risk は risk を見る土台（lib のフェーズに `work` を足したもの）。
const (
	kindBase  = "base"
	kindGuard = "guard"
	kindRisk  = "risk"
)

// openRules は何でも通す。deny が 1 件も無いので、止まったものはルールの外で止まっている。
var openRules = ruleset(nil, []obj{rule("anything", "Bash|Read|Write|Edit|NotebookEdit", "regex", ".", "")})

const libPhasesWork = `version: 1
phases:
  work:
    kind: work
    title: 作業
    review: false
    scope: inherit
`

func prepareGuard(t testing.TB, ws string) {
	write(t, base.CommonPath(ws, "rules"), body(openRules))
	// 共通レイヤーの phases.yml も守る対象。守ることを見るために置き、ワークツリー側の設定ができるようコミットしておく。
	write(t, base.CommonPath(ws, "phases"), commonPhasesText)
	git(t, ws, "add", "-A")
	git(t, ws, "commit", "--quiet", "-m", "common phases")
}

func prepareRisk(t testing.TB, ws string) {
	lib := filepath.Join(ws, "projects", "lib")
	writePhases(t, lib, libPhasesWork)
	git(t, lib, "add", "-A")
	git(t, lib, "commit", "--quiet", "-m", "phases")
}

// ---- ワークスペース

// ws はテスト 1 本のワークスペース。t はそのテストの t で、サブテストでは as で差し替える。
type ws struct {
	t                        testing.TB
	root, projects, lib, app string
	// 共通レイヤーの 3 本
	rules, phases, risk string
	log                 string
}

func newWS(t testing.TB, kind string) *ws {
	t.Helper()
	source := templateDir(t, kind)
	root := filepath.Join(base.TempDir(t), "ws")
	if err := copyTree(source, root); err != nil {
		t.Fatal(err)
	}
	projects := filepath.Join(root, "projects")
	return &ws{
		t: t, root: root, projects: projects,
		lib: filepath.Join(projects, "lib"), app: filepath.Join(projects, "app"),
		rules: base.CommonPath(root, "rules"), phases: base.CommonPath(root, "phases"), risk: base.CommonPath(root, "risk"),
		log: filepath.Join(root, "logs", "decisions.jsonl"),
	}
}

// as はサブテストの t で動く写し。
func (w *ws) as(t testing.TB) *ws {
	c := *w
	c.t = t
	return &c
}

// project は `projects/<名前>/` に git 初期化したプロジェクトを足す。
func (w *ws) project(name string, rules any, phases, risk string) string {
	w.t.Helper()
	return buildProject(w.t, filepath.Join(w.projects, name), rules, phases, risk)
}

// worktree は `.claude/worktrees/<名前>` に、owner（ワークスペースかプロジェクト）から切る。
// あれば（`approval-request` が親の提案を書くために切ったもの）そのまま返す。
func (w *ws) worktree(owner, name string) string {
	w.t.Helper()
	path := filepath.Join(w.root, ".claude", "worktrees", name)
	if info, err := os.Stat(path); err != nil || !info.IsDir() {
		git(w.t, owner, "worktree", "add", "--quiet", path, "-b", name)
	}
	return path
}

// homes は承認済みチケットを持ちうるツリー。ワークスペース、プロジェクト、ワークツリー。
func (w *ws) homes() []string {
	found := []string{w.root, w.lib, w.app}
	dir := filepath.Join(w.root, ".claude", "worktrees")
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
func (w *ws) propose(name, text, project string) string {
	w.t.Helper()
	owner := w.root
	if project != "" {
		owner = filepath.Join(w.projects, project)
	}
	treeName := name
	if m := parentLine.FindStringSubmatch(text); m != nil {
		treeName = m[1]
	}
	tree := w.worktree(owner, treeName)
	// 計画を持つ親には、共通レイヤーとそのレイヤーの phases.yml の和から、計画が使う定義のコピー（`phases:`）を足す
	// （`ccnavi --plan-order <親> --fill-phases` と同じ）。どちらにも無ければ空のコピー。
	var texts []string
	for _, path := range []string{w.phases, layerPath(owner, "phases")} {
		if data, err := os.ReadFile(path); err == nil {
			texts = append(texts, string(data))
		}
	}
	text = samples.InsertPhases(text, samples.CombinedPhases(texts...))
	return write(w.t, filepath.Join(tree, "wip", "proposals", "todo", name+".md"), text)
}

// proposalPath は提案（`wip/proposals/<置き場>/<名前>.md`）のパス。在る側のツリーを探す。無ければ空。
func (w *ws) proposalPath(name string, states ...string) string {
	if len(states) == 0 {
		states = []string{"todo", "review"}
	}
	for _, where := range w.homes() {
		for _, place := range states {
			path := filepath.Join(where, "wip", "proposals", place, name+".md")
			if exists(path) {
				return path
			}
		}
	}
	return ""
}

// approvedPath は承認済みチケットの置き場の下のパス。在る側を探す。
func (w *ws) approvedPath(parts ...string) string {
	for _, where := range w.homes() {
		path := filepath.Join(append([]string{where, home, "approved"}, parts...)...)
		if exists(path) {
			return path
		}
	}
	return filepath.Join(append([]string{w.root, home, "approved"}, parts...)...)
}

func (w *ws) approvedCopy(name string) string { return w.approvedPath("doing", name+".md") }

// ---- 起動

// exec は実行ファイルを 1 回起動する。レイヤーの置き場はフラグで渡さない。共通レイヤーの 3 本（--rules / --phases / --risk）も
// レイヤーを探す先の 2 本（--projects / --project-home）も、診断（--lint / --test / --explain）でだけ有効で、
// hook の判定とチケットのサブコマンドでは無視する。差し替えたいテストは w.rules / w.phases / w.risk に書く。
func (w *ws) exec(guard, input string, args ...string) run.Result {
	w.t.Helper()
	prefix := []string{"--root", w.root, "--guard-core-files", guard, "--restore-if-deny", "disable", "--guard-ticket-approval", "disable"}
	return run.Ccnavi(w.t, samples.Place(prefix, args), run.Opts{Input: input, Dir: base.Root()})
}

func (w *ws) ccnavi(args ...string) run.Result { return w.exec("disable", "", args...) }

// hookAs は hook の payload を 1 件渡す。
func (w *ws) hookAs(event, guard, tool, cwd string, input obj) run.Result {
	w.t.Helper()
	if input == nil {
		input = obj{}
	}
	payload := body(obj{"hook_event_name": event, "tool_name": tool, "cwd": cwd, "session_id": "s1", "tool_input": input})
	return w.exec(guard, payload, "--mode", "enable")
}

// hook は設定ファイルの保護を切った PreToolUse。
func (w *ws) hook(tool, cwd string, input obj) run.Result {
	w.t.Helper()
	return w.hookAs("PreToolUse", "disable", tool, cwd, input)
}

// guardedHook は設定ファイルの保護を入れた PreToolUse。
func (w *ws) guardedHook(tool, cwd string, input obj) run.Result {
	w.t.Helper()
	return w.hookAs("PreToolUse", "enable", tool, cwd, input)
}

// post は実行後の hook（設定ファイルの保護は切る）。
func (w *ws) post(tool, cwd string, input obj) run.Result {
	w.t.Helper()
	return w.hookAs("PostToolUse", "disable", tool, cwd, input)
}

// approve は承認して、承認済みチケットを各ワークツリーにコミットする（本番は write-and-push が行う）。
func (w *ws) approve() run.Result {
	w.t.Helper()
	done := w.exec("disable", "y\n", "user", "agree")
	dir := filepath.Join(w.root, ".claude", "worktrees")
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		path := filepath.Join(dir, e.Name())
		git(w.t, path, "add", "-A")
		git(w.t, path, "commit", "--quiet", "--allow-empty", "-m", "approve")
	}
	return done
}

func (w *ws) mustOK(r run.Result, what string) {
	w.t.Helper()
	if r.Code != 0 {
		w.t.Fatalf("%s: code %d\nstdout: %s\nstderr: %s", what, r.Code, r.Stdout, r.Stderr)
	}
}

// ---- 診断

type problem struct {
	Severity string `json:"severity"`
	Where    string `json:"where"`
	Detail   string `json:"detail"`
}

func (w *ws) lintProblems(args ...string) []problem {
	w.t.Helper()
	r := w.ccnavi(append([]string{"--lint", "--json"}, args...)...)
	var report struct {
		Problems []problem `json:"problems"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &report); err != nil {
		w.t.Fatalf("--lint --json が JSON を返さない: %v\n%s\n%s", err, r.Stdout, r.Stderr)
	}
	return report.Problems
}

// problems はその深刻度の Problem。where を渡すと、その出どころで先に絞る。
// detail の部分一致だけで見ると、`self` や `lib` のようなありふれた語が別の苦情の文面に紛れていても通ってしまう。
// どのレイヤーの話かは where が持つ。
func (w *ws) problems(severity, where string) []problem {
	w.t.Helper()
	var out []problem
	for _, p := range w.lintProblems() {
		if p.Severity == severity && (where == "" || strings.HasPrefix(p.Where, where)) {
			out = append(out, p)
		}
	}
	return out
}

// projectWhere は `--lint` の Problem の出どころのうち、そのプロジェクトのもの（`(projects/lib)`）。
func projectWhere(name string) string { return "(projects/" + name + ")" }

// anyDetail は problems のどれかの detail が sub を含むか。
func anyDetail(ps []problem, sub string) bool {
	return slices.ContainsFunc(ps, func(p problem) bool { return strings.Contains(p.Detail, sub) })
}

func anyWhere(ps []problem, sub string) bool {
	return slices.ContainsFunc(ps, func(p problem) bool { return strings.Contains(p.Where, sub) })
}

func (w *ws) mustHaveDetail(ps []problem, sub string) {
	w.t.Helper()
	if !anyDetail(ps, sub) {
		w.t.Errorf("detail に %q を含む問題が無い: %+v", sub, ps)
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

func (w *ws) parse(r run.Result) hookOutput {
	w.t.Helper()
	var out hookOutput
	if strings.TrimSpace(r.Stdout) == "" {
		return out
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		w.t.Fatalf("hook の出力が JSON でない: %q", r.Stdout)
	}
	return out
}

func (w *ws) decision(r run.Result) string {
	w.t.Helper()
	return w.parse(r).HookSpecificOutput.Decision
}

func (w *ws) reason(r run.Result) string {
	w.t.Helper()
	out := w.parse(r).HookSpecificOutput
	if out.Reason != "" {
		return out.Reason
	}
	return out.Context
}

// record は決定ログの 1 件。
type record map[string]any

func (r record) str(key string) string {
	s, _ := r[key].(string)
	return s
}

// names は記録の rules の記録名（`<相対パス>:<id>`）。
func (r record) names() []string {
	var out []string
	items, _ := r["rules"].([]any)
	for _, v := range items {
		out = append(out, v.(string))
	}
	return out
}

// ids は記録の rules を、書いた表記（レイヤーの前置きつき）にしたもの。
func (r record) ids() []string {
	var out []string
	for _, n := range r.names() {
		out = append(out, layerID(n))
	}
	return out
}

// hits は記録の rules のうち、レイヤーのルール。組み込みの deny（`builtin:` で始まる id。共通レイヤーと config の有無によらず
// 常にヒットする）は数えない。
func (r record) hits() []string {
	var out []string
	for _, n := range r.names() {
		if !strings.HasPrefix(n, "builtin:") {
			out = append(out, layerID(n))
		}
	}
	return out
}

func (w *ws) lastRecord() record {
	w.t.Helper()
	var lines []string
	for _, line := range strings.Split(read(w.t, w.log), "\n") {
		if strings.TrimSpace(line) != "" {
			lines = append(lines, line)
		}
	}
	if len(lines) == 0 {
		w.t.Fatal("決定ログが空")
	}
	var rec record
	if err := json.Unmarshal([]byte(lines[len(lines)-1]), &rec); err != nil {
		w.t.Fatal(err)
	}
	return rec
}

func (w *ws) hitRules() []string {
	w.t.Helper()
	return w.lastRecord().hits()
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

// layerID は記録名を、書いた表記（レイヤーの前置きつき）に戻す。この 3 つの場所以外は名前のまま。
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

func (w *ws) assertDenied(r run.Result, ids ...string) {
	w.t.Helper()
	if d := w.decision(r); d != "deny" {
		w.t.Errorf("decision = %q, want deny\n%s%s", d, r.Stdout, r.Stderr)
		return
	}
	reason := w.reason(r)
	for _, id := range ids {
		if !strings.Contains(reason, recordOf(id)) {
			w.t.Errorf("理由に %q が無い: %s", recordOf(id), reason)
		}
	}
}

func (w *ws) assertNotDenied(r run.Result) {
	w.t.Helper()
	if r.Code != 0 {
		w.t.Errorf("code = %d\n%s", r.Code, r.Stderr)
	}
	if d := w.decision(r); d == "deny" {
		w.t.Errorf("deny された: %s", r.Stdout)
	}
}

// ---- ticket 本文

type ticketOpts struct {
	project string
	parent  string
	phase   int
	plan    []string
	allow   []string
	review  bool
}

// ticketText は提案の本文。親は plan を、子は parent と phase を持つ。project は行き先のレイヤーを決める。
func ticketText(name string, o ticketOpts) string {
	lines := []string{"---", "version: 1", "ticket: " + name}
	if o.parent == "" {
		lines = append(lines, "mr: 1", "issue: 1")
	}
	if o.project != "" {
		lines = append(lines, "project: "+o.project)
	}
	if o.parent != "" {
		lines = append(lines, "parent: "+o.parent, "phase: "+itoa(o.phase))
	}
	if len(o.plan) > 0 {
		// 計画のステップは、すぐ前のステップが先行する一直線にする（最初のステップは先行なし）。
		lines = append(lines, "plan:")
		for n, item := range o.plan {
			if n > 0 {
				lines = append(lines, "  - {type: "+item+", after: ["+itoa(n)+"]}")
			} else {
				lines = append(lines, "  - "+item)
			}
		}
	}
	required := "false"
	if o.review {
		required = "true"
	}
	lines = append(lines, "human_review:", "  required: "+required, "  reason: テスト", "title: 作業", "rationale: |", "  理由")
	if len(o.allow) > 0 {
		lines = append(lines, "allow:")
		for _, g := range o.allow {
			lines = append(lines, "  - match: Write|Edit", `    glob: "`+g+`"`)
		}
	}
	lines = append(lines, `started_at: ""`, `completed_at: ""`, `base_sha: ""`, "---", "", "本文")
	return strings.Join(lines, "\n") + "\n"
}

func itoa(n int) string { return strconv.Itoa(n) }

// sortedKeys は map のキーを並べたもの。
func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func removeFile(path string) error { return os.Remove(path) }

func insertPhases(text, phasesYML string) string {
	return samples.InsertPhases(text, phasesYML)
}

// ---- 設定ファイルの保護（tests/config/test_config_union_guard.py の GuardHarness の写し）

// runHook は Bash 1 回。設定ファイルの保護を入れ、実行前でバックアップを取り、実行後で戻す。
func (w *ws) runHook(event string) run.Result {
	w.t.Helper()
	return w.hookAs(event, "enable", "Bash", w.root, obj{"command": "ls"})
}

// said は失敗したときに何が起きたかをつける。標準エラーを見ないと理由が出ない。
func said(r run.Result, note string) string {
	head := ""
	if note != "" {
		head = note + "\n"
	}
	return head + "stdout: " + r.Stdout + "\nstderr: " + r.Stderr
}

// breakAllAndRestore はバックアップを 1 回取らせ、全部を壊し、実行後に全部が戻ったかを確かめる。結果を返す。
func (w *ws) breakAllAndRestore(paths []string) run.Result {
	w.t.Helper()
	before := map[string]string{}
	for _, path := range paths {
		before[path] = read(w.t, path)
	}
	w.runHook("PreToolUse")
	for _, path := range paths {
		text := "version: 1\ndeny: []\n"
		if strings.HasSuffix(path, "phases.yml") {
			text = "version: 1\nphases: {}\n"
		}
		write(w.t, path, text)
	}
	result := w.runHook("PostToolUse")
	for _, path := range paths {
		if got := read(w.t, path); got != before[path] {
			w.t.Errorf("%s が戻らない\n%s", path, said(result, path))
		}
	}
	if !strings.Contains(result.Stdout, "restored") {
		w.t.Errorf("restored を言わない\n%s", said(result, ""))
	}
	return result
}

// judgeMany はシェルのコマンドを何本も 1 回の起動で判定し、1 本ごとにヒットしたルールの id を返す。
// `--test-expectations` に渡す。判定は hook の実行前と同じ関数を通り、居場所はワークスペースルート
// （hook に cwd をルートで渡したのと同じ）。止めたかどうかも見るので、決定（verdict）と id の組を返す。
func (w *ws) judgeMany(commands []string, flags ...string) []judged {
	w.t.Helper()
	var asks []obj
	for _, c := range commands {
		asks = append(asks, obj{"tool": "Bash", "subject": c})
	}
	// 期待のタイプは判定に効かない。JSON は YAML としても読める。
	path := write(w.t, filepath.Join(base.TempDir(w.t), "expectations.yml"), body(obj{"ask": asks}))
	r := w.exec("enable", "", append(slices.Clone(flags), "--test-expectations", path, "--json")...)
	w.mustOK(r, "--test-expectations")
	var out struct {
		Expectations []struct {
			Verdict string `json:"verdict"`
			Rules   []struct {
				ID string `json:"id"`
			} `json:"rules"`
		} `json:"expectations"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		w.t.Fatalf("%v\n%s", err, r.Stdout)
	}
	if len(out.Expectations) != len(commands) {
		w.t.Fatalf("判定が %d 件、コマンドが %d 件", len(out.Expectations), len(commands))
	}
	var res []judged
	for _, e := range out.Expectations {
		j := judged{verdict: e.Verdict}
		for _, r := range e.Rules {
			j.ids = append(j.ids, r.ID)
		}
		res = append(res, j)
	}
	return res
}

type judged struct {
	verdict string
	ids     []string
}

const settingFilesGuard = "builtin:guard-setting-files"

// assertAllGuarded はどれも設定ファイルの保護で止まる。何本も 1 回の起動で判定する。
func (w *ws) assertAllGuarded(commands []string, flags ...string) {
	w.t.Helper()
	for i, j := range w.judgeMany(commands, flags...) {
		if j.verdict != "deny" || !slices.Contains(j.ids, settingFilesGuard) {
			w.t.Errorf("止まらない: %q → %s %v", commands[i], j.verdict, j.ids)
		}
	}
}

// assertNoneDenied はどれも止まらない。
func (w *ws) assertNoneDenied(commands []string, flags ...string) {
	w.t.Helper()
	for i, j := range w.judgeMany(commands, flags...) {
		if j.verdict == "deny" {
			w.t.Errorf("止まった: %q → %v", commands[i], j.ids)
		}
	}
}

// assertNoneGuarded はどれも設定ファイルの保護にヒットしない。
func (w *ws) assertNoneGuarded(commands []string, flags ...string) {
	w.t.Helper()
	for i, j := range w.judgeMany(commands, flags...) {
		if slices.Contains(j.ids, settingFilesGuard) {
			w.t.Errorf("設定ファイルの保護にヒットした: %q → %v", commands[i], j.ids)
		}
	}
}
