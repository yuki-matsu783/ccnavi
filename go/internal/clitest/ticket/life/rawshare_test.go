package life

import (
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"

	osexec "os/exec"
)

// 承認済みチケットの置き場を、1 回の起動で 1 度だけ読むこと。--explain（ボードの JSON と文の一覧）と --lint は、親ごとの局面とフェーズまで
// 同じ読み（approval.ReadRaw）を使い回す。親ごとに読み直すと、読む回数が親の数とツリーの数の積で増え、置き場が育ったワークスペースでは
// VS Code の拡張の期限を超える。実行ファイルの中に数える口は置かない。strace で実行ファイルが開いたファイルを記録し、全ツリーの
// .ccnavi/approved/{doing,done}/*.md がそれぞれ 1 度だけ開かれたことを見る。strace の無い機械（Linux 以外を含む）では飛ばす。

var openedRe = regexp.MustCompile(`openat\([^,]+, "([^"]+)"`)

func realOf(path string) string {
	if real, err := filepath.EvalSymlinks(path); err == nil {
		return real
	}
	return path
}

// approvedFiles は全ツリーの承認済みチケットの置き場（作業中・クローズした）のファイル（実パス）。
func approvedFiles(tr *ticketkit.Tree) []string {
	var found []string
	tops, _ := filepath.Glob(filepath.Join(tr.Root, ".claude", "worktrees", "*"))
	for _, top := range append([]string{tr.Root}, tops...) {
		for _, state := range []string{"doing", "done"} {
			matches, _ := filepath.Glob(filepath.Join(top, ".ccnavi", "approved", state, "*.md"))
			found = append(found, matches...)
		}
	}
	for i := range found {
		found[i] = realOf(found[i])
	}
	return found
}

// opened は実行ファイルを strace の下で起こし、開いたファイルの回数を返す。
func opened(tr *ticketkit.Tree, strace string, args ...string) map[string]int {
	tr.T.Helper()
	log := filepath.Join(tr.Root, "strace.log")
	prefix := []string{"--root", tr.Root, "--guard-core-files", "disable", "--restore-if-deny", "disable", "--guard-ticket-approval", "disable"}
	argv := append([]string{"-f", "-qq", "-e", "trace=openat", "-o", log, exe.Path(tr.T)}, samples.Place(prefix, args)...)
	done := run.Command(tr.T, strace, argv, run.Opts{})
	if done.Code != 0 && done.Code != 1 {
		tr.T.Fatalf("code %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
	counts := map[string]int{}
	for _, line := range strings.Split(ticketkit.Read(tr.T, log), "\n") {
		if m := openedRe.FindStringSubmatch(line); m != nil {
			counts[realOf(m[1])]++
		}
	}
	return counts
}

// ownTicket は <ツリー>/.ccnavi/approved/<状態>/<ツリーの名前>.md か。
func ownTicket(path string) bool {
	top := filepath.Dir(filepath.Dir(filepath.Dir(filepath.Dir(path))))
	return filepath.Base(path) == filepath.Base(top)+".md"
}

// 親とツリーが増えても、--explain と --lint が置き場のファイルを開くのは 1 度ずつ。
func TestExplainAndLintReadTheApprovedCopiesOnce(t *testing.T) {
	t.Parallel()
	strace, err := osexec.LookPath("strace")
	if runtime.GOOS != "linux" || err != nil {
		t.Skip("strace が無い（Linux だけで回す）")
	}
	tr := ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
	// フェーズ 1 を終わらせる（レビュー準備中になる手前）。
	tr.PhaseFamily(samples.Plan("chores", "design"))
	tr.Propose(childName, samples.ChildTextReview(childName, "i0001", 1, []string{"src/a*"}, samples.Off), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	tr.RunChild(childName, ticketkit.File{Path: "src/a1.py", Text: "x\n"})
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", childName))
	tr.CommitParent("close 01")
	tr.Merge(childName)
	tr.Hook("PostToolUse", "Bash", tr.ParentTree, map[string]any{"command": "ls"}) // レビュー準備中にする
	// 親をもう 1 つと、ワークツリーを 2 つ足す。開く回数は変わらない。
	other := tr.Worktree("i0002", "main")
	tr.Propose("i0002", samples.ParentText("i0002", samples.Plan("chores"), samples.ParentOpts{Copy: tr.Phases}), other)
	commitIn(tr, other, "approval-request i0002")
	ticketkit.OK(t, tr.Approve())
	tr.Worktree("x1", "i0001")
	tr.Worktree("x2", "i0001")
	files := approvedFiles(tr)
	if len(files) <= 4 {
		t.Fatalf("置き場のファイルが少ない: %v", files)
	}
	for _, args := range [][]string{{"--explain", "--json"}, {"--explain"}, {"--lint", "--json"}} {
		got := opened(tr, strace, args...)
		for _, p := range files {
			want := 1
			// --lint は、親のワークツリーかを見るために、ワークツリーと同じ名前のチケットをもう 1 度だけ開く（ワークツリーごとに 1 本。
			// 親の数と掛け合わない）。
			if args[0] == "--lint" && ownTicket(p) {
				want++
			}
			if got[p] != want {
				t.Errorf("%v: %s を %d 回開いた（%d 回のはず）", args, p, got[p], want)
			}
		}
	}
}
