package phases

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
	"ccnavi/internal/infra/yaml"
)

// 親 i0001 のワークツリーを持つリポジトリを組み、承認・着手・レビュー依頼を実行ファイルで動かす土台
// 。

const parentName = "i0001"

// ws は 1 本のテストの作業場。
type ws struct {
	t          testing.TB
	root       string
	parentTree string
	approved   string
	state      string
	// rules・phases は共通レイヤーのルールと、自身のレイヤーのフェーズ定義のパス。
	rules, phases string
	// phasesText は親の `phases:` にコピーする元の phases.yml の本文。phases.yml を差し替えるテストは setPhases で揃える。
	phasesText string
	// noMark を立てると、sh（write-and-push）を通らない状態の操作に付ける印を付けない（印が無い呼び出しを断る検査）。
	noMark bool
	// env は ccnavi と git に足す環境（コミットの日時を固定する場面だけが使う）。
	env map[string]string
	// lastBody は直近の request が出したレビュー依頼の本文。
	lastBody string
}

// newWS はリポジトリと親のワークツリーを組む。共通レイヤーのルールは既定の置き場に置く
// （`--rules`・`--phases` は診断でだけ有効で、hook の判定と `--reviewed` には渡せない）。
func newWS(t *testing.T) *ws {
	t.Helper()
	root := base.TempDir(t)
	w := &ws{t: t, root: root, phasesText: samples.Phases}
	ticketkit.Write(t, filepath.Join(root, ".claude", "keep"), "")
	w.git(root, "init", "--quiet", "-b", "main")
	ticketkit.Write(t, filepath.Join(root, "src", "keep.py"), "print(1)\n")
	ticketkit.Write(t, filepath.Join(root, ".gitignore"), ".claude/\n")
	w.git(root, "add", "-A")
	w.git(root, "commit", "--quiet", "-m", "init")
	w.rules = ticketkit.Write(t, base.CommonPath(root, "rules"), samples.Rules)
	w.phases = ticketkit.Write(t, base.ConfigPath(root, "phases"), samples.Phases)
	w.state = filepath.Join(root, "logs", "state")
	w.parentTree = w.worktree(parentName, "main")
	w.approved = filepath.Join(w.parentTree, ".ccnavi", "approved")
	return w
}

// as は t を差し替えた作業場（サブテストの落ち方を、そのサブテストに結びつける）。
func (w *ws) as(t testing.TB) *ws {
	c := *w
	c.t = t
	return &c
}

// setPhases は自身のレイヤーの phases.yml を書き、親にコピーする元の本文も揃える。
func (w *ws) setPhases(text string) {
	w.t.Helper()
	ticketkit.Write(w.t, w.phases, text)
	w.phasesText = text
}

// ---- ファイルと git

func remove(t testing.TB, path string) {
	t.Helper()
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
}

func slash(s string) string { return strings.ReplaceAll(s, `\`, "/") }

func writeJSON(t testing.TB, path string, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return ticketkit.Write(t, path, string(b))
}

func (w *ws) git(dir string, args ...string) string {
	w.t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), base.ExecTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "git", append([]string{"-c", "user.email=t@example.com", "-c", "user.name=t"}, args...)...)
	cmd.Dir = dir
	cmd.Env = os.Environ()
	for k, v := range w.env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	out, err := cmd.CombinedOutput()
	if err != nil {
		w.t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return string(out)
}

// ---- 実行ファイル

// ccnavi は `ccnavi <引数>` を、判定のオプション（--guard-* の無効化）と状態の操作の印を付けて起こす。
func (w *ws) ccnavi(args ...string) run.Result { return w.ccnaviIn("", args...) }

// ccnaviIn は標準入力つき。
func (w *ws) ccnaviIn(input string, args ...string) run.Result {
	w.t.Helper()
	if !w.noMark {
		args = samples.AsWriteAndPush(w.root, w.state, w.parentTree, args)
	}
	prefix := []string{"--root", w.root, "--guard-core-files", "disable", "--restore-if-deny", "disable", "--guard-ticket-approval", "disable"}
	return run.Ccnavi(w.t, samples.Place(prefix, args), run.Opts{Input: input, Dir: base.Root(), Env: w.env})
}

// bare は道具のオプションも印も付けずに起こす（--root だけ付ける）。
func (w *ws) bare(env map[string]string, args ...string) run.Result {
	w.t.Helper()
	merged := map[string]string{}
	for k, v := range w.env {
		merged[k] = v
	}
	for k, v := range env {
		merged[k] = v
	}
	return run.Ccnavi(w.t, append([]string{"--root", w.root}, args...), run.Opts{Dir: base.Root(), Env: merged})
}

func (w *ws) mustOK(r run.Result, what string) run.Result {
	w.t.Helper()
	if r.Code != 0 {
		w.t.Fatalf("%s: code %d\n%s%s", what, r.Code, r.Stdout, r.Stderr)
	}
	return r
}

// hookOpts は hook の payload に足す欄。
type hookOpts struct {
	Mode    string // 空なら enable
	AgentID string
}

type input = map[string]any

// hook は hook の payload を 1 回渡す。
func (w *ws) hook(event, tool, cwd string, in input) run.Result {
	return w.hookWith(hookOpts{}, event, tool, cwd, in)
}

func (w *ws) hookWith(o hookOpts, event, tool, cwd string, in input) run.Result {
	w.t.Helper()
	payload := map[string]any{"hook_event_name": event, "tool_name": tool, "cwd": cwd, "session_id": "s1", "tool_input": in}
	if in == nil {
		payload["tool_input"] = input{}
	}
	if o.AgentID != "" {
		payload["agent_id"] = o.AgentID
	}
	b, err := json.Marshal(payload)
	if err != nil {
		w.t.Fatal(err)
	}
	mode := o.Mode
	if mode == "" {
		mode = "enable"
	}
	return w.ccnaviIn(string(b), "--mode", mode)
}

type hookAnswer struct {
	Out struct {
		Decision string `json:"permissionDecision"`
		Reason   string `json:"permissionDecisionReason"`
		Context  string `json:"additionalContext"`
	} `json:"hookSpecificOutput"`
}

func (w *ws) answer(r run.Result) hookAnswer {
	w.t.Helper()
	var a hookAnswer
	if strings.TrimSpace(r.Stdout) == "" {
		return a
	}
	if err := json.Unmarshal([]byte(r.Stdout), &a); err != nil {
		w.t.Fatalf("hook の出力が JSON でない: %q", r.Stdout)
	}
	return a
}

// reason は hook の答えの理由（無ければ追加の文脈）。
func (w *ws) reason(r run.Result) string {
	w.t.Helper()
	a := w.answer(r)
	if a.Out.Reason != "" {
		return a.Out.Reason
	}
	return a.Out.Context
}

func (w *ws) decision(r run.Result) string {
	w.t.Helper()
	return w.answer(r).Out.Decision
}

// ---- 木と親子

func (w *ws) worktree(name, from string) string {
	w.t.Helper()
	path := filepath.Join(w.root, ".claude", "worktrees", name)
	w.git(w.root, "worktree", "add", "--quiet", path, "-b", name, from)
	return path
}

// propose は提案を書く。tree が空なら親 i0001 のワークツリー。
func (w *ws) propose(name, text string, tree ...string) string {
	w.t.Helper()
	at := w.parentTree
	if len(tree) > 0 {
		at = tree[0]
	}
	return ticketkit.Write(w.t, filepath.Join(at, "wip", "proposals", "todo", name+".md"), text)
}

func (w *ws) commitParent(message ...string) {
	w.t.Helper()
	msg := "tickets"
	if len(message) > 0 {
		msg = message[0]
	}
	w.git(w.parentTree, "add", "-A")
	w.git(w.parentTree, "commit", "--quiet", "--allow-empty", "-m", msg)
}

// approve は承認して、承認済みチケットを親のブランチに乗せる（本番の ccnavi-user-agree.sh と同じ順）。
func (w *ws) approve() run.Result {
	w.t.Helper()
	r := w.ccnaviIn("y\n", "user", "agree")
	if ticketkit.Exists(w.approved) {
		w.git(w.parentTree, "add", "-A")
		w.git(w.parentTree, "commit", "--quiet", "--allow-empty", "-m", "approve")
	}
	return r
}

func (w *ws) mustApprove() run.Result {
	w.t.Helper()
	return w.mustOK(w.approve(), "user agree")
}

// family は親を提案して承認する（計画の定義のコピーは phasesText）。
func (w *ws) family(plan []samples.Step) { w.familyWith(plan, samples.ParentOpts{}) }

func (w *ws) familyWith(plan []samples.Step, o samples.ParentOpts) {
	w.t.Helper()
	if o.Copy == "" {
		o.Copy = w.phasesText
	}
	w.propose(parentName, samples.ParentText(parentName, plan, o))
	w.commitParent()
	w.mustApprove()
}

// parent は親の提案の本文（コピーの元は phasesText）。
func (w *ws) parent(plan []samples.Step, o samples.ParentOpts) string {
	if o.Copy == "" {
		o.Copy = w.phasesText
	}
	return samples.ParentText(parentName, plan, o)
}

// startParent は親を着手する（済んでいれば何もしない）。
func (w *ws) startParent() {
	w.t.Helper()
	r := w.ccnavi("ticket", "start", parentName)
	if r.Code != 0 && !strings.Contains(r.Stderr, "着手済み") {
		w.t.Fatalf("ticket start %s: code %d\n%s%s", parentName, r.Code, r.Stdout, r.Stderr)
	}
}

// file は子のワークツリーに置いてコミットするファイル。
type file struct{ rel, text string }

// runChild は子のワークツリーを作って着手し、ファイルを置いてコミットする。ワークツリーを返す。
func (w *ws) runChild(name string, files ...file) string {
	w.t.Helper()
	w.startParent()
	tree := w.worktree(name, parentName)
	w.mustOK(w.ccnavi("ticket", "start", name), "ticket start "+name)
	for _, f := range files {
		ticketkit.Write(w.t, filepath.Join(tree, filepath.FromSlash(f.rel)), f.text)
	}
	w.git(tree, "add", "-A")
	if len(files) > 0 {
		w.git(tree, "commit", "--quiet", "-m", name)
	}
	return tree
}

func (w *ws) closeChild(name string) run.Result { return w.ccnavi("ticket", "finish", name) }

func (w *ws) mustClose(name string) run.Result {
	w.t.Helper()
	return w.mustOK(w.closeChild(name), "ticket finish "+name)
}

// merge は子を親のブランチへ合流し、write-and-push の finish が書く merged_sha を足す。
func (w *ws) merge(child string) {
	w.t.Helper()
	w.git(w.parentTree, "merge", "--quiet", "--no-edit", child)
	samples.RecordMerge(w.t, w.parentTree, child)
}

// remote は bare の origin を足して親を push し、マージリクエストの取得結果の見本（Draft の MR 7）を返す。
func (w *ws) remote() string {
	w.t.Helper()
	bare := filepath.Join(w.root, "origin.git")
	w.git(w.root, "init", "--quiet", "--bare", bare)
	w.git(w.root, "remote", "add", "origin", bare)
	w.git(w.parentTree, "push", "--quiet", "origin", parentName)
	return writeJSON(w.t, filepath.Join(w.root, "fixture.json"), map[string]any{
		"host": "fixture", "mr": map[string]any{"number": 7, "url": "u/7", "draft": true},
	})
}

func (w *ws) push() { w.git(w.parentTree, "push", "--quiet", "origin", parentName) }

// request はレビューを依頼する（prepare → requested）。依頼の本文は lastBody に残る。
func (w *ws) request(fixture string, phase int) run.Result {
	w.t.Helper()
	body := ticketkit.Write(w.t, filepath.Join(w.root, "body.md"), "見てほしい\n")
	prepared := w.ccnavi("--cwd", w.parentTree, "--phase", itoa(phase), "--body-file", body, "review", "prepare")
	if prepared.Code != 0 {
		return prepared
	}
	w.lastBody = ticketkit.Read(w.t, strings.Split(prepared.Stdout, "\n")[0])
	data := ticketkit.ReadJSON(w.t, fixture)
	result := writeJSON(w.t, filepath.Join(w.root, "posted.json"), map[string]any{
		"host": "fixture", "mr": data["mr"], "url": "u/7#1", "created_at": "2026-01-01T00:00:00Z",
	})
	return w.ccnavi("--cwd", w.parentTree, "--phase", itoa(phase), "review", "requested", "--result", result)
}

func (w *ws) confirm(fixture string, phase int) run.Result {
	return w.ccnavi("--cwd", w.parentTree, "--phase", itoa(phase), "review", "confirm", "--result", fixture)
}

func (w *ws) ready(fixture string) run.Result {
	return w.ccnavi("--cwd", w.parentTree, "review", "ready", "--result", fixture)
}

// boardPhase はボードの JSON のフェーズ 1 つを、止まっているか・レビュー待ちか・マーカーの種類で返す。
func (w *ws) boardPhase(number int) (gateClosed, reviewWaiting bool, marks []string) {
	w.t.Helper()
	r := w.mustOK(w.ccnavi("--explain", "--json"), "--explain --json")
	var board struct {
		Parents []struct {
			Ticket string `json:"ticket"`
			Phases []struct {
				Number        int            `json:"number"`
				GateClosed    bool           `json:"gate_closed"`
				ReviewWaiting bool           `json:"review_waiting"`
				Marks         map[string]any `json:"marks"`
			} `json:"phases"`
		} `json:"parents"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &board); err != nil {
		w.t.Fatal(err)
	}
	for _, p := range board.Parents {
		if p.Ticket != parentName {
			continue
		}
		for _, ph := range p.Phases {
			if ph.Number == number {
				for k := range ph.Marks {
					marks = append(marks, k)
				}
				slices.Sort(marks)
				return ph.GateClosed, ph.ReviewWaiting, marks
			}
		}
	}
	w.t.Fatalf("フェーズ %d が無い: %s", number, r.Stdout)
	return
}

func itoa(n int) string { return strconv.Itoa(n) }

// doing は承認済みチケットの置き場（doing/）のパス。
func (w *ws) doing(name string) string { return filepath.Join(w.approved, "doing", name+".md") }

func (w *ws) isApproved(name string) bool { return ticketkit.Exists(w.doing(name)) }

// todo は親のワークツリーの提案の置き場のパス。
func (w *ws) todo(name string) string {
	return filepath.Join(w.parentTree, "wip", "proposals", "todo", name+".md")
}

// ---- 本文の見本

// childText は子の提案（human_review: required: true）。
func childText(name string, phase int, allow ...string) string {
	return samples.ChildText(name, parentName, phase, allow)
}

// childReview は human_review の書き方を選べる子の提案。
func childReview(name string, phase int, review samples.Flag, allow ...string) string {
	return samples.ChildTextReview(name, parentName, phase, allow, review)
}

// noPlanParent は計画を持たない親の提案（計画が無いと ticket start が止まる）。
func noPlanParent(name string, allow ...string) string {
	lines := []string{"---", "version: 1", "ticket: " + name, "mr: 1", "issue: 1",
		"human_review:", "  required: true", "  reason: テスト", "title: 作業", "rationale: |", "  理由"}
	if len(allow) > 0 {
		lines = append(lines, "allow:")
		for _, g := range allow {
			lines = append(lines, "  - match: Write|Edit", `    glob: "`+g+`"`)
		}
	}
	lines = append(lines, `started_at: ""`, `completed_at: ""`, `base_sha: ""`, "---", "", "本文")
	return strings.Join(lines, "\n") + "\n"
}

// planOf は定義の名前だけの計画。
func planOf(names ...string) []samples.Step { return samples.Plan(names...) }

// after は先行の番号を明記したステップ。
func after(name string, prev ...int) samples.Step {
	if prev == nil {
		prev = []int{}
	}
	return samples.Step{Type: name, After: prev}
}

var noFeedback = []samples.Step{}

// ---- 小さな道具

// setThreads は取得結果の見本の threads を差し替える。
func setThreads(t testing.TB, fixture string, threads ...map[string]any) {
	t.Helper()
	data := ticketkit.ReadJSON(t, fixture)
	data["threads"] = threads
	writeJSON(t, fixture, data)
}

func thread(id, body string, resolved bool) map[string]any {
	return map[string]any{"id": id, "resolved": resolved, "url": "u/7#" + id, "body": body}
}

// contains は text が全部の語を含むことを見る。
func contains(t testing.TB, what, text string, needles ...string) {
	t.Helper()
	for _, n := range needles {
		if !strings.Contains(text, n) {
			t.Errorf("%s に %q が無い:\n%s", what, n, text)
		}
	}
}

func lacks(t testing.TB, what, text string, needles ...string) {
	t.Helper()
	for _, n := range needles {
		if strings.Contains(text, n) {
			t.Errorf("%s に %q がある:\n%s", what, n, text)
		}
	}
}

func equalStrings(t testing.TB, what string, got, want []string) {
	t.Helper()
	if !slices.Equal(got, want) {
		t.Errorf("%s = %v, want %v", what, got, want)
	}
}

// ---- frontmatter の読み

// frontOf は本文の frontmatter を読む。
func frontOf(t testing.TB, text string) *yaml.Map {
	t.Helper()
	parts := strings.SplitN(text, "---\n", 3)
	if len(parts) < 3 {
		t.Fatalf("frontmatter が無い:\n%s", text)
	}
	data, err := yaml.Load(parts[1])
	if err != nil {
		t.Fatalf("frontmatter が読めない: %v", err)
	}
	m, ok := data.(*yaml.Map)
	if !ok {
		t.Fatalf("frontmatter が辞書でない:\n%s", parts[1])
	}
	return m
}

// step は計画のステップを書き方に依らない形にしたもの。
type step struct {
	Type     string
	Review   string // 書かなければ空
	Deferred bool
	After    []int
}

// stepsOf は frontmatter の plan / feedback のステップ（文字列 1 語でも {type: …} でも）。
func stepsOf(front *yaml.Map, key string) []step {
	items, _ := front.Get(key).([]any)
	var out []step
	for _, item := range items {
		s := step{After: []int{}}
		switch v := item.(type) {
		case string:
			s.Type = v
		case *yaml.Map:
			s.Type = v.Text("type")
			if r := v.Get("review"); r != nil {
				s.Review = fmt.Sprint(r)
			}
			s.Deferred, _ = v.Bool("deferred")
			if list, ok := v.List("after"); ok {
				for _, n := range list {
					k, _ := strconv.Atoi(fmt.Sprint(n))
					s.After = append(s.After, k)
				}
			}
		}
		out = append(out, s)
	}
	return out
}
