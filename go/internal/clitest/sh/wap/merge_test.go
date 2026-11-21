package wap

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
)

// 子の finish は、親のブランチへ合流してから push する（合流と状態は 1 つのマージコミット）。
//
//  1. 合流・完了の記録・子の merged_sha が 1 つのマージコミットになり、push される
//  2. 記録の無い別の PC でも、子 2 の着手で子 1 のコードが入り、finish の合流まで通る
//  3. 置き場の書き換え・範囲外のパス・衝突・未コミットは、何も変えずに止まる
//  4. push の失敗・コミットの失敗・シグナル中断は、合流ごと元に戻る。応答が失われた push は成功
//  5. 検査のあとに子が積んだコミットは載せない
//
// 合流の前の検査（置き場・範囲・親で無視されるファイル・戻せない形・子の未コミット）と衝突の文は Go の単体テスト
// （tickets/lifecycle/wapmerge・phase/scope）が見る。ここでは、止まったときに sh が何も変えないことを、範囲の外・置き場の変更・
// 戻せない形（改行を含むパス）・衝突で代表して見る。応答が失われた push と送る途中の TERM は、合流の無い形（ticket_test.go）で見る
// （sh の同じ段取り）。子と親の未コミットは Go（wapmerge・treegate）、リスクを数える起点は Go（git の ChildBase）が見る。

func (k *kit) startParent() { k.t.Helper(); okRun(k.t, k.ticket("start", parentID)) }

func (k *kit) startChild(ident string) string {
	k.t.Helper()
	okRun(k.t, k.ticket("start", ident))
	return filepath.Join(k.ws, ".claude", "worktrees", ident)
}

// approveSecondChild は 2 つ目の子を承認して送る。
func (k *kit) approveSecondChild() {
	k.t.Helper()
	k.approveChildren(childBID, "approval-request b", "approve b", func() {
		write(k.t, filepath.Join(k.tree, "wip", "proposals", "todo", childBID+".md"),
			samples.ChildTextReview(childBID, parentID, 1, []string{"wip/design/*"}, samples.Off))
	})
	k.git(k.tree, "push", "-q", "origin", parentID)
}

func (k *kit) work(tree, rel, text string) string {
	k.t.Helper()
	write(k.t, filepath.Join(tree, parts(rel)), text)
	k.git(tree, "add", "--", rel)
	k.git(tree, "commit", "-q", "-m", "work")
	return k.sha(tree, "HEAD")
}

func (k *kit) merging() bool {
	return k.try(k.tree, "rev-parse", "-q", "--verify", "MERGE_HEAD").Code == 0
}

func (k *kit) parentsOfHead() []string {
	return strings.Fields(k.git(k.tree, "rev-list", "--parents", "-n", "1", "HEAD"))
}

// assertUntouched は止まったあと、親のワークツリー・ブランチ・リモート・チケットが何も変わっていない。
func (k *kit) assertUntouched(head string) {
	k.t.Helper()
	eq(k.t, "親の HEAD", k.head(), head)
	eq(k.t, "リモート", k.remoteParent(), head)
	eq(k.t, "未コミット", k.dirty(), "")
	if k.merging() {
		k.t.Error("合流の途中で止まっている")
	}
	if !isFile(k.copyPath(childID, "doing")) {
		k.t.Error("doing/ から動いている")
	}
	if exists(k.lockDir()) {
		k.t.Error("ロックが残っている")
	}
}

// assertRolledBack は失敗とシグナルのあと、合流ごと戻っている。
func (k *kit) assertRolledBack(before string) {
	k.t.Helper()
	eq(k.t, "親の HEAD", k.head(), before)
	eq(k.t, "未コミット", k.dirty(), "")
	if k.merging() {
		k.t.Error("合流の途中で止まっている")
	}
	if exists(filepath.Join(k.tree, "wip", "design", "a.md")) {
		k.t.Error("合流したファイルが残っている")
	}
	if !isFile(k.copyPath(childID, "doing")) {
		k.t.Error("doing/ から動いている")
	}
	if exists(k.lockDir()) {
		k.t.Error("ロックが残っている")
	}
}

// termAfter は git の引数が pattern にヒットする呼び出しの直後に、finish へ TERM を送る。標準エラーを返す。
func (k *kit) termAfter(pattern string) string {
	k.t.Helper()
	mark := filepath.Join(k.base, "reached")
	path := k.shim("shim", "case \" $* \" in *\""+pattern+"\"*)\n  \"$REAL\" \"$@\"; rc=$?; : >'"+slash(mark)+"'; sleep 3; exit $rc ;;\nesac")
	return k.spawn("ccnavi-ticket.sh", map[string]string{env.Path: path}, "finish", childID).termWhen(k.t, mark)
}

func TestFinishMergesTheChildInOneMergeCommitAndPushes(t *testing.T) {
	k := newKit(t)
	k.startParent()
	tree := k.startChild(childID)
	childHead := k.work(tree, "wip/design/a.md", "a\n")
	before := k.head()
	okRun(t, k.ticket("finish", childID))
	head := k.head()
	eqList(t, "親", k.parentsOfHead()[1:], []string{before, childHead})
	eq(t, "リモート", k.remoteParent(), head)
	eq(t, "未コミット", k.dirty(), "")
	if k.merging() {
		t.Error("合流の途中で止まっている")
	}
	changed := strings.Join(strings.Fields(k.git(k.tree, "diff", "--name-only", "--no-renames", "HEAD^1", "HEAD")), "\n")
	has(t, changed, "wip/design/a.md")
	has(t, changed, approved+"/doing/"+childID+".md")
	has(t, changed, "wip/proposals/review/"+childID+".md")
	review := read(t, filepath.Join(k.tree, "wip", "proposals", "review", childID+".md"))
	has(t, review, `merged_sha: "`+childHead+`"`)
	// 子の先頭は親の先頭の祖先
	code(t, k.try(k.tree, "merge-base", "--is-ancestor", childHead, "HEAD"), 0)
}

func TestAChildAlreadyInTheParentIsNotMergedAgain(t *testing.T) {
	k := newKit(t)
	k.startParent()
	tree := k.startChild(childID)
	k.work(tree, "wip/design/a.md", "a\n")
	k.git(k.tree, "merge", "-q", "--no-edit", childID)
	k.git(k.tree, "push", "-q", "origin", parentID)
	okRun(t, k.ticket("finish", childID))
	eq(t, "親の数（合流するものが無いので、ふつうのコミット）", len(k.parentsOfHead()), 2)
	eq(t, "リモート", k.remoteParent(), k.head())
	// 親子ごとの取り込み状態は作らない
	if exists(filepath.Join(k.state, "sync")) {
		t.Error("取り込み状態を作った")
	}
	has(t, read(t, filepath.Join(k.tree, "wip", "proposals", "review", childID+".md")), `merged_sha: "`+k.sha(tree, "HEAD")+`"`)
}

// 記録の無い別の PC でも、子 2 の着手で子 1 のコードが入り、finish の合流まで通る。
func TestASecondChildSeesTheFirstAndReachesTheRequest(t *testing.T) {
	k := newKit(t)
	k.startParent()
	k.approveSecondChild()
	first := k.startChild(childID)
	firstHead := k.work(first, "wip/design/a.md", "a\n")
	okRun(t, k.ticket("finish", childID))
	second := k.startChild(childBID)
	if !isFile(filepath.Join(second, "wip", "design", "a.md")) {
		t.Error("子 2 に子 1 のコードが入っていない")
	}
	secondHead := k.work(second, "wip/design/b.md", "b\n")
	okRun(t, k.ticket("finish", childBID))
	eq(t, "リモート", k.remoteParent(), k.head())
	for _, sha := range []string{firstHead, secondHead} {
		code(t, k.try(k.tree, "merge-base", "--is-ancestor", sha, "HEAD"), 0)
	}
	body := write(t, filepath.Join(k.base, "body.md"), "見てほしい\n")
	prepared := k.exe(opts{}, "--cwd", k.tree, "--phase", "1", "--body-file", body, "review", "prepare")
	lacks(t, prepared.Stderr, "合流")
}

func TestAConflictIsAbortedAndChangesNothing(t *testing.T) {
	k := newKit(t)
	k.startParent()
	tree := k.startChild(childID)
	k.work(tree, "wip/design/a.md", "child\n")
	remote := k.remoteCommit("wip/design/a.md", "remote\n")
	r := k.ticket("finish", childID)
	code(t, r, 1)
	has(t, r.Stderr, "衝突する")
	has(t, r.Stderr, "wip/design/a.md")
	has(t, r.Stderr, "子のワークツリー（.claude/worktrees/"+childID+"）")
	// 取り込みは済んでいるが、合流は何も残していない
	k.assertUntouched(remote)
}

// 範囲の外のパスと置き場の変更は、合流せずに止まる。子の準備は 1 回にして、子のコミットを差し替えて finish を回す。
func TestAPathOutsideTheScopeOrATicketPlaceStopsWithoutMerging(t *testing.T) {
	k := newKit(t)
	k.startParent()
	tree := k.startChild(childID)
	start := k.sha(tree, "HEAD")
	before := k.head()
	for _, c := range []struct{ rel, kind string }{
		{"src/x.py", "範囲の外のパス"},
		{"wip/proposals/todo/evil.md", "置き場の変更"},
	} {
		k.git(tree, "reset", "-q", "--hard", start)
		k.work(tree, c.rel, "x\n")
		r := k.ticket("finish", childID)
		code(t, r, 1)
		has(t, r.Stderr, c.kind)
		has(t, r.Stderr, c.rel)
		k.assertUntouched(before)
	}
}

func TestAPathWithANewlineStopsWithoutMerging(t *testing.T) {
	k := newKit(t)
	k.startParent()
	tree := k.startChild(childID)
	k.work(tree, "wip/design/a\nb.md", "x\n")
	before := k.head()
	r := k.ticket("finish", childID)
	code(t, r, 1)
	has(t, r.Stderr, "合流を戻せない形の変更がある")
	has(t, r.Stderr, "改行を含むパス")
	k.assertUntouched(before)
}

// ---- push の失敗・コミットの失敗・シグナルは合流ごと戻す

func TestTwoRejectedPushesUndoTheMerge(t *testing.T) {
	k := newKit(t)
	k.startParent()
	tree := k.startChild(childID)
	k.work(tree, "wip/design/a.md", "a\n")
	before := k.head()
	k.rejectAllPushes()
	code(t, k.ticket("finish", childID), 1)
	k.assertRolledBack(before)
	eq(t, "リモート", k.remoteParent(), before)
}

func TestARejectedPushIsRetriedWithTheMergeDoneAgain(t *testing.T) {
	k := newKit(t)
	k.startParent()
	tree := k.startChild(childID)
	childHead := k.work(tree, "wip/design/a.md", "a\n")
	before := k.head()
	k.rejectPushes(1)
	r := k.ticket("finish", childID)
	code(t, r, 0)
	has(t, r.Stderr, "戻した")
	eqList(t, "親", k.parentsOfHead()[1:], []string{before, childHead})
	eq(t, "リモート", k.remoteParent(), k.head())
	eq(t, "未コミット", k.dirty(), "")
}

func TestAFailedCommitUndoesTheMerge(t *testing.T) {
	k := newKit(t)
	k.startParent()
	tree := k.startChild(childID)
	k.work(tree, "wip/design/a.md", "a\n")
	before := k.head()
	k.git(k.ws, "config", "commit.gpgsign", "true")
	k.git(k.ws, "config", "gpg.program", "false")
	r := k.ticket("finish", childID)
	code(t, r, 1)
	has(t, r.Stderr, "合流のコミットができなかった")
	k.assertRolledBack(before)
	eq(t, "リモート", k.remoteParent(), before)
}

// 範囲の検査の直後に子がコミットしても、検査していないコミットは合流しない。
func TestACommitAfterTheScopeCheckIsNotMerged(t *testing.T) {
	k := newKit(t)
	k.startParent()
	tree := k.startChild(childID)
	checked := k.work(tree, "wip/design/a.md", "a\n")
	mark := filepath.Join(k.base, "checked")
	// 子のワークツリーで範囲の検査（diff -z）が済んだ直後に、範囲外のファイルをコミットする。
	path := k.shim("shim", "case \" $* \" in *\" diff --name-only --no-renames --ignore-submodules=none -z \"*)\n"+
		"  if [ ! -e '"+slash(mark)+"' ] && [ \"$(pwd -P)\" = \"$(cd '"+slash(tree)+"' && pwd -P)\" ]; then\n"+
		"    \"$REAL\" \"$@\"; rc=$?; : >'"+slash(mark)+"'\n"+
		"    mkdir -p src && echo evil > src/evil.py && \"$REAL\" add src/evil.py >/dev/null &&"+
		" \"$REAL\" commit -q -m evil >/dev/null\n"+
		"    exit $rc\n  fi ;;\nesac")
	okRun(t, k.ticketWith(map[string]string{env.Path: path}, "finish", childID))
	if !exists(mark) {
		t.Error("検査の差分を読んでいない")
	}
	if k.sha(tree, "HEAD") == checked {
		t.Error("子にコミットが積まれていない")
	}
	if k.try(k.tree, "cat-file", "-e", "HEAD:src/evil.py").Code == 0 {
		t.Error("検査していないコミットが合流した")
	}
	eq(t, "合流した子の先頭", k.parentsOfHead()[2], checked)
}

// 検査のあとに子がコミットしても、合流した先頭を merged_sha に書き、それまでを数える。
func TestACommitAfterThePlanStillRecordsTheMergedHead(t *testing.T) {
	k := newKit(t)
	k.startParent()
	tree := k.startChild(childID)
	planned := k.work(tree, "wip/design/a.md", "a\n")
	// 書く前の検査（--check）では積まない。計画（write-and-push merge）のあと、書く呼び出しで積む。
	wrap := k.wrapExe("wrap", "case \" $* \" in *\" --check \"*) ;; *\" ticket finish \"*)\n"+
		"  cd '"+slash(tree)+"' && echo late > wip/design/late.md && '"+k.gitPath+"' add -A &&"+
		" '"+k.gitPath+"' commit -q -m late ;;\nesac\n", k.bin)
	okRun(t, k.ticketWith(map[string]string{env.EntryPath: wrap}, "finish", childID))
	if k.sha(tree, "HEAD") == planned {
		t.Error("子にコミットが積まれていない")
	}
	has(t, read(t, filepath.Join(k.tree, "wip", "proposals", "review", childID+".md")), `merged_sha: "`+planned+`"`)
	var risk struct {
		Summary string `json:"summary"`
	}
	if err := json.Unmarshal([]byte(read(t, filepath.Join(k.tree, parts(approved), "phases", parentID, childID+".risk.json"))), &risk); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(risk.Summary, "1 ファイル、+1 -0 行") {
		t.Errorf("summary = %q", risk.Summary)
	}
}

func TestATermRightAfterTheMergeCommitUndoesIt(t *testing.T) {
	if ospath.Windows {
		t.Skip("TERM を捕まえさせられない")
	}
	k := newKit(t)
	k.startParent()
	tree := k.startChild(childID)
	k.work(tree, "wip/design/a.md", "a\n")
	before := k.head()
	err := k.termAfter(" commit --quiet --no-verify -m ")
	has(t, err, "送る前に止められた")
	k.assertRolledBack(before)
	eq(t, "リモート", k.remoteParent(), before)
	okRun(t, k.ticket("finish", childID))
	eq(t, "リモート", k.remoteParent(), k.head())
}
