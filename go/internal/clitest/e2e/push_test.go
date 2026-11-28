package e2e

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
)

// チケットの作成から承認、承認済みチケットの push までを 1 本に通す受入。
//
// 実際の sh（`.ccnavi/scripts/`）と組み立て済みの実行ファイル（dist/ccnavi）を、使い捨てのワークスペースと bare のリモートの上で
// 順に呼ぶ。手順ごとに前の手順の結果を前提にするので、分けない。単体のテストは実行ファイルを in-process で呼ぶか承認を stub で
// 済ませていて、「実行ファイルが置いたものを sh がコミットして push する」つなぎ目は通らない。ここはそのつなぎ目だけを見る。
//
// 承認はターミナルからしか通らない（`--agree` の壁）。テストはターミナルを持たないので、手順 3 からは
// CCNAVI_GUARD_TICKET_APPROVAL=disable を渡して y を標準入力から送る。壁そのものは手順 2 で見る。

const (
	pushTicket   = "i0001"
	pushApproved = ".ccnavi/approved/doing"
	pushProposal = "wip/proposals/todo"
	pushMessage  = "ccnavi: 承認済みチケットを更新"
	pushNothing  = "コミットして push する承認済みチケットはありません。"
)

// pushFixture は承認から push までの筋書きが共有する木。
type pushFixture struct {
	t       *testing.T
	tmp, ws string
	remote  string
	tree    string
	stub    hoststub.Stub
}

func newPushFixture(t *testing.T) *pushFixture {
	t.Helper()
	s := sourcesFor(t)
	tmp := base.TempDir(t)
	ws := filepath.Join(tmp, "ws")
	buildWorkspace(t, s, ws)
	// 承認は共通設定のルールを読む。無いと承認は通るが「ルールを読めない」と言うので、実際のワークスペースと同じく置く。
	writeFile(t, base.CommonPath(ws, "rules"), "version: 1\n")
	// 計画（plan）のあるチケットは、計画が使うフェーズの定義も要る。
	writeFile(t, base.ConfigPath(ws, "phases"), samples.Phases)
	gitRun(t, ws, "add", ".ccnavi/config")
	gitRun(t, ws, "commit", "-q", "-m", "rules")
	remote := filepath.Join(tmp, "origin.git")
	gitRun(t, tmp, "init", "-q", "--bare", remote)
	gitRun(t, ws, "remote", "add", "origin", remote)
	// 取り込みは統合先をリモートの main から決める。リモートに main が無いと止まる。
	gitRun(t, ws, "push", "-q", "origin", "main")
	gitRun(t, ws, "remote", "set-head", "origin", "main")
	return &pushFixture{
		t:      t,
		tmp:    tmp,
		ws:     ws,
		remote: remote,
		tree:   filepath.Join(ws, ".claude", "worktrees", pushTicket),
		// `ccnavi-ticket.sh start` の事前確認は origin がホストのものであることを求める。ホストのスタブは
		// `git remote get-url origin` にだけホストの形の URL を答え、fetch・push は手元の bare へ通す。
		// トークンを渡さないので、ホストの API は「確かめられなかった」で続く。
		stub: hoststub.Build(t, tmp, true),
	}
}

// sh は ワークスペースの .ccnavi/scripts/<name> を sh で起こす。
func (p *pushFixture) sh(o run.Opts, name string, args ...string) run.Result {
	p.t.Helper()
	if o.Dir == "" {
		o.Dir = p.ws
	}
	return run.Sh(p.t, filepath.Join(p.ws, ".ccnavi", "scripts", name), args, o)
}

// binary は組み立て済みの実行ファイルを直に呼ぶ。ボードの拡張が打つ形。
func (p *pushFixture) binary(args ...string) run.Result {
	p.t.Helper()
	name := "ccnavi"
	if ospath.Windows {
		name = "ccnavi.exe"
	}
	exe := filepath.Join(p.ws, "dist", "ccnavi", name)
	return execute(p.t, p.ws, run.Opts{}, exe, append([]string{"--root", p.ws}, args...)...)
}

func (p *pushFixture) approvedCopy(tree string) string {
	return filepath.Join(tree, filepath.FromSlash(pushApproved), pushTicket+".md")
}

func (p *pushFixture) remoteHead() string {
	return gitOut(p.t, p.tmp, "--git-dir", p.remote, "rev-parse", "--verify", "-q", pushTicket)
}

// committed は HEAD のコミットが触ったパス（改名は消えた側と置かれた側の 2 本に分けて並べる）。
func (p *pushFixture) committed() []string {
	listed := gitOut(p.t, p.tree, "show", "--name-only", "--no-renames", "--format=", "HEAD")
	var out []string
	for _, line := range strings.Split(listed, "\n") {
		if line != "" {
			out = append(out, line)
		}
	}
	sort.Strings(out)
	return out
}

// clone は別の機械。リモートから親のブランチだけを取る。
func (p *pushFixture) clone(name string) string {
	path := filepath.Join(p.tmp, name)
	gitRun(p.t, p.tmp, "clone", "-q", "--branch", pushTicket, p.remote, path)
	return path
}

func readText(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func mustOK(t *testing.T, r run.Result) run.Result {
	t.Helper()
	if r.Code != 0 {
		t.Fatalf("code = %d: stdout=%q stderr=%q", r.Code, r.Stdout, r.Stderr)
	}
	return r
}

// TestProposeApproveAndPush は、提案の作成から承認・承認済みチケットの push・着手までを、ひと続きの手順で見る。
func TestProposeApproveAndPush(t *testing.T) {
	t.Parallel()
	p := newPushFixture(t)
	proposalText := samples.ParentText(pushTicket, samples.Plan("design"), samples.ParentOpts{Allow: []string{"src/*"}})
	proposalFile := filepath.Join(pushProposal, pushTicket+".md")

	// 1. 作成。ワークツリーを切り、提案を書いてコミットする（エージェントの手順）。
	where := ".claude/worktrees/" + pushTicket
	mustOK(t, p.sh(run.Opts{}, "ccnavi-git.sh", "fetch", "origin", "main"))
	mustOK(t, p.sh(run.Opts{}, "ccnavi-git.sh", "worktree", "add", where, "-b", pushTicket))
	writeFile(t, filepath.Join(p.tree, proposalFile), proposalText)
	mustOK(t, p.sh(run.Opts{Dir: p.tree}, "ccnavi-git.sh", "add", filepath.ToSlash(proposalFile)))
	mustOK(t, p.sh(run.Opts{Dir: p.tree}, "ccnavi-git.sh", "commit", "-m", "approval-request"))
	proposed := gitOut(t, p.tree, "rev-parse", "HEAD")
	// 親のブランチは承認の前にリモートへ送ってある（MR の元ブランチ）。承認済みチケットは、リモートにある親のブランチに取り込んでから送る。
	mustOK(t, p.sh(run.Opts{Dir: p.tree}, "ccnavi-git.sh", "push", "-u", "origin", pushTicket))
	// 統合先を取り込んでおく（取り込みの後の検査も通ることを見る）。
	mustOK(t, p.sh(run.Opts{}, "ccnavi-sync.sh"))
	preview := mustOK(t, p.binary("--agree", "--preview", "--json"))
	var got struct {
		Batch []struct {
			Ticket string `json:"ticket"`
		} `json:"batch"`
	}
	if err := json.Unmarshal([]byte(preview.Stdout), &got); err != nil {
		t.Fatalf("preview の JSON が読めない: %v: %q", err, preview.Stdout)
	}
	if len(got.Batch) != 1 || got.Batch[0].Ticket != pushTicket {
		t.Fatalf("承認待ちに %v が数えられた。%s だけのはず", got.Batch, pushTicket)
	}

	// 同じツリーの書きかけ。承認の時点でコミットされてはいけない。
	writeFile(t, filepath.Join(p.tree, "seed.txt"), "書きかけ\n")
	writeFile(t, filepath.Join(p.tree, "src", "draft.py"), "x\n")

	// 2. ターミナルが無いと承認の壁で止まる。承認済みチケットも、コミットも、push も出ない。
	walled := p.sh(run.Opts{Input: "y\n"}, "ccnavi-user-agree.sh")
	if walled.Code != 1 {
		t.Fatalf("壁で止まらない: code = %d: %q", walled.Code, walled.Stdout+walled.Stderr)
	}
	if !strings.Contains(walled.Stderr, "ターミナル") {
		t.Fatalf("ターミナルが無いことを言っていない: %q", walled.Stderr)
	}
	if _, err := os.Stat(p.approvedCopy(p.tree)); err == nil {
		t.Fatal("壁で止まったのに承認済みチケットが置かれた")
	}
	if got := gitOut(t, p.tree, "rev-parse", "HEAD"); got != proposed {
		t.Fatalf("壁で止まったのにコミットされた: %s", got)
	}
	if got := p.remoteHead(); got != proposed {
		t.Fatalf("壁で止まったのに push された: %s", got)
	}

	// 3. 承認。実行ファイルが doing/ に置き、ccnavi-user-push-approved.sh がコミットして push する。
	approved := mustOK(t, p.sh(run.Opts{Input: "y\n", Env: map[string]string{env.GuardTicketApproval: "disable"}}, "ccnavi-user-agree.sh"))
	if !strings.Contains(approved.Stdout, "承認した") {
		t.Fatalf("承認したと言っていない: %q", approved.Stdout)
	}
	if strings.Contains(approved.Stderr, "読めない") {
		t.Fatalf("ルールを読めないと言っている: %q", approved.Stderr)
	}
	// 承認は提案をそのまま動かす（欄を書き足さない）。
	if got := readText(t, p.approvedCopy(p.tree)); got != proposalText {
		t.Fatal("承認が提案をそのまま動かしていない")
	}
	if got := gitOut(t, p.tree, "log", "-1", "--format=%s"); got != pushMessage {
		t.Fatalf("コミットの題 = %q: %s%s", got, approved.Stdout, approved.Stderr)
	}
	// コミットしたのは置き場と、承認で todo/ から消えた提案だけ。
	wantCommitted := []string{pushApproved + "/" + pushTicket + ".md", pushProposal + "/" + pushTicket + ".md"}
	sort.Strings(wantCommitted)
	if got := p.committed(); !equalStrings(got, wantCommitted) {
		t.Fatalf("コミットしたもの = %v、want %v", got, wantCommitted)
	}
	if got := gitOut(t, p.tree, "rev-parse", "HEAD~1"); got != proposed {
		t.Fatalf("親が承認の前の提案でない: %s", got)
	}
	if head := gitOut(t, p.tree, "rev-parse", "HEAD"); head != p.remoteHead() {
		t.Fatalf("push されていない: HEAD %s、リモート %s", head, p.remoteHead())
	}
	if got := gitOut(t, p.tree, "status", "--porcelain", "--", "seed.txt"); got != " M seed.txt" {
		t.Fatalf("書きかけの状態 = %q", got)
	}
	if got := gitOut(t, p.tree, "status", "--porcelain", "--", "src"); got != "?? src/" {
		t.Fatalf("書きかけの src の状態 = %q", got)
	}
	if got := gitOut(t, p.tree, "diff", "--cached", "--name-only"); got != "" {
		t.Fatalf("索引に残りがある: %q", got)
	}
	// 別の機械から、承認済みチケットが読め、提案は承認待ちに残っていない。
	there := p.clone("machine-b")
	if got := readText(t, p.approvedCopy(there)); got != proposalText {
		t.Fatal("別の機械から承認済みチケットが読めない")
	}
	if _, err := os.Stat(filepath.Join(there, proposalFile)); err == nil {
		t.Fatal("提案が承認待ちに残っている")
	}
	if _, err := os.Stat(filepath.Join(there, "src", "draft.py")); err == nil {
		t.Fatal("書きかけが別の機械に届いた")
	}
	if got := readText(t, filepath.Join(there, "seed.txt")); got != "seed\n" {
		t.Fatalf("別の機械の seed.txt = %q", got)
	}

	// 4. もうコミットするものは無い。コミットもリモートも動かない。
	pushed := p.remoteHead()
	idle := mustOK(t, p.sh(run.Opts{Dir: p.tree}, "ccnavi-user-push-approved.sh"))
	if !strings.Contains(idle.Stdout, pushNothing) {
		t.Fatalf("何も無いと言っていない: %q", idle.Stdout)
	}
	if got := gitOut(t, p.tree, "rev-parse", "HEAD"); got != pushed {
		t.Fatal("何も無いのにコミットが増えた")
	}
	if got := p.remoteHead(); got != pushed {
		t.Fatal("何も無いのに push が動いた")
	}

	// 5. 着手の欄は実行ファイルが承認済みチケットに書く。親の `start` は自分で書いた置き場をコミットして push するので、あとに送るものは残らない。
	mustOK(t, p.sh(run.Opts{Dir: p.tree, Env: p.stub.Env(hoststub.EnvOpts{})}, "ccnavi-ticket.sh", "start", pushTicket))
	started := readText(t, p.approvedCopy(p.tree))
	if strings.Contains(started, `started_at: ""`) {
		t.Fatal("着手の欄が書かれていない")
	}
	if !strings.Contains(started, pushed) {
		t.Fatal("基準点が承認のコミットになっていない")
	}
	if got := p.committed(); !equalStrings(got, []string{pushApproved + "/" + pushTicket + ".md"}) {
		t.Fatalf("着手のコミットの中身 = %v", got)
	}
	if got := gitOut(t, p.tree, "rev-parse", "HEAD~1"); got != pushed {
		t.Fatalf("着手の親が承認のコミットでない: %s", got)
	}
	if head := gitOut(t, p.tree, "rev-parse", "HEAD"); head != p.remoteHead() {
		t.Fatalf("着手が push されていない: HEAD %s、リモート %s", head, p.remoteHead())
	}
	later := p.clone("machine-b-later")
	if got := readText(t, p.approvedCopy(later)); got != started {
		t.Fatal("別の機械から着手の欄が読めない")
	}
	if got := gitOut(t, p.tree, "status", "--porcelain", "--", "seed.txt"); got != " M seed.txt" {
		t.Fatalf("着手の後の書きかけ = %q", got)
	}
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
