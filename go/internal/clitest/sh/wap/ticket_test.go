package wap

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/shkit"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
)

// started はチケットのコピーに着手の時刻が書かれたか（書かれていれば write-and-push が書いた）。
func (k *kit) started(ident string) bool {
	return strings.Contains(shkit.Read(k.t, k.copyPath(ident, "doing")), "started_at: 20")
}

// 順序: hook のマーカーを先にコミット → 取り込み → 書く → 書いたパスだけ commit --only → push。

func TestStartCommitsOnlyWhatItWroteAndPushes(t *testing.T) {
	k := newKit(t)
	before := k.head()
	r := k.ticket("start", parentID)
	code(t, r, 0)
	has(t, r.Stdout, "に着手した")
	head := k.head()
	if head == before {
		t.Error("コミットが増えていない")
	}
	eq(t, "リモート", k.remoteParent(), head)
	eqList(t, "コミット", k.committed("HEAD"), []string{approved + "/doing/" + parentID + ".md"})
	eqList(t, "件名", k.subjects(1), []string{"ccnavi: " + parentID + " に着手"})
	eq(t, "未コミット", k.dirty(), "")
	// 親子ごとの取り込み状態も、統合先のコピーも書かない。
	if shkit.Exists(filepath.Join(k.state, "sync")) {
		t.Error("取り込み状態を作った")
	}
	if shkit.Exists(k.lockDir()) {
		t.Error("ロックが残っている")
	}
}

func TestHookMarksAreCommittedBeforeTheTakeIn(t *testing.T) {
	k := newKit(t)
	pending := k.mark(1, "pending", `{"review": true, "at": "2026-09-29T00:00:00+0900"}`)
	code(t, k.ticket("start", parentID), 0)
	eqList(t, "件名", k.subjects(2), []string{"ccnavi: " + parentID + " に着手", "ccnavi: " + parentID + " の hook のマーカーをコミットする"})
	eqList(t, "マーカーのコミット", k.committed("HEAD~1"), []string{pending})
	eq(t, "リモート", k.remoteParent(), k.head())
	eq(t, "未コミット", k.dirty(), "")
}

// ユーザの判断（c）が未送信なら、何も書かずに止める。
func TestAHumanDecisionNotYetSentStopsWithoutWriting(t *testing.T) {
	k := newKit(t)
	k.mark(1, "reviewed", `{"by": "decide", "at": "t"}`)
	copyBefore := shkit.Read(t, k.copyPath(parentID, "doing"))
	head := k.head()
	r := k.ticket("start", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "ユーザの判断が未送信")
	has(t, r.Stderr, "ccnavi-user-push-approved.sh "+parentID)
	eq(t, "コピー", shkit.Read(t, k.copyPath(parentID, "doing")), copyBefore)
	eq(t, "HEAD", k.head(), head)
	if shkit.Exists(k.lockDir()) {
		t.Error("ロックが残っている")
	}
}

// ロック: 他の操作が持っていれば何も書かずに止まる。
func TestAHeldLockStopsWithoutWriting(t *testing.T) {
	k := newKit(t)
	if err := os.MkdirAll(k.lockDir(), 0o755); err != nil {
		t.Fatal(err)
	}
	host := strings.TrimSpace(run.Command(t, "hostname", nil).Stdout)
	now := time.Now().Unix()
	pid := base.LiveShPid(t)
	shkit.Write(t, filepath.Join(k.lockDir(), "owner"),
		host+" "+strconv.Itoa(pid)+" "+strconv.FormatInt(now, 10)+" "+strconv.Itoa(pid)+"-"+strconv.FormatInt(now, 10)+"\n")
	r := k.ticket("start", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "ロックを他の操作が持っている")
	if k.started(parentID) {
		t.Error("着手を書いた")
	}
	if !shkit.IsDir(k.lockDir()) {
		t.Error("他の操作のロックを消した")
	}
}

// 競合: リモートが進んでいれば取り込んでから書く。

func TestARemoteAheadIsTakenInBeforeWriting(t *testing.T) {
	k := newKit(t)
	rel := approved + "/doing/" + childBID + ".md"
	remote := k.remoteCommit(rel, samples.ChildText(childBID, parentID, 1, []string{"wip/design/*"}))
	code(t, k.ticket("start", parentID), 0)
	if !shkit.IsFile(filepath.Join(k.tree, parts(rel))) {
		t.Error("取り込めていない")
	}
	eq(t, "取り込んだ先頭", k.sha(k.tree, "HEAD~1"), remote)
	eq(t, "リモート", k.remoteParent(), k.head())
}

// 取り込みの後の検査が error を言えば（ccnavi-sync.sh が 1 で終わる）、何も書かずに止める。
func TestATakeInThatFindsAnErrorWritesNothing(t *testing.T) {
	k := newKit(t)
	k.remoteCommit(approved+"/doing/"+childBID+".md", "---\nticket: "+childBID+"\n---\n")
	r := k.ticket("start", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "取り込みの後の検査で止める理由がある")
	has(t, r.Stderr, "取り込めなかった")
	if shkit.Exists(filepath.Join(k.state, "sync")) {
		t.Error("取り込み状態を作った")
	}
	if k.started(parentID) {
		t.Error("着手を書いた")
	}
	if shkit.Exists(k.lockDir()) {
		t.Error("ロックが残っている")
	}
}

func TestADivergedParentIsMergedBeforeWriting(t *testing.T) {
	k := newKit(t)
	k.mark(1, "pending", `{"review": true, "at": "t"}`)
	k.remoteCommit("src/remote.py", "y = 2\n")
	code(t, k.ticket("start", parentID), 0)
	if !shkit.IsFile(filepath.Join(k.tree, "src", "remote.py")) {
		t.Error("取り込めていない")
	}
	eq(t, "リモート", k.remoteParent(), k.head())
	// 取り込みの merge（親が 2 つ）
	eq(t, "親の数", len(strings.Fields(k.git(k.tree, "rev-list", "--parents", "-n", "1", "HEAD~1"))), 3)
}

func TestAConflictingTakeInWritesNothing(t *testing.T) {
	k := newKit(t)
	pending := approved + "/phases/" + parentID + "/1.pending"
	k.mark(1, "pending", `{"review": true, "at": "l"}`)
	k.git(k.tree, "add", "-A")
	k.git(k.tree, "commit", "-q", "-m", "hook")
	k.remoteCommit(pending, `{"review": true, "at": "r"}`)
	head := k.head()
	r := k.ticket("start", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "衝突")
	eq(t, "HEAD", k.head(), head)
	if k.started(parentID) {
		t.Error("着手を書いた")
	}
	if shkit.Exists(k.mergeHead()) {
		t.Error("取り込みの途中で止まっている")
	}
}

// 3 と 4 の間に hook が書いて取り込みが重なったら、やり直さずに止める。再実行は通る。
func TestAHookWritingBetweenSortingAndTakingInStopsAndARerunPasses(t *testing.T) {
	k := newKit(t)
	rel := approved + "/phases/" + parentID + "/2.pending"
	body := `{"review": true, "at": "t"}`
	k.remoteCommit(rel, body)
	once := filepath.Join(k.base, "once")
	target := filepath.Join(k.tree, parts(rel))
	path := k.rawShim("hookshim", "#!/bin/sh\ncase \" $* \" in\n*\" fetch \"*\"refs/heads/"+parentID+":\"*)\n"+
		"  if [ ! -e '"+shkit.Slash(once)+"' ]; then : >'"+shkit.Slash(once)+"'; mkdir -p '"+shkit.Slash(filepath.Dir(target))+"';"+
		" printf '%s' '"+body+"' >'"+shkit.Slash(target)+"'; fi ;;\nesac\nexec '"+k.gitPath+"' \"$@\"\n")
	before := k.head()
	env := map[string]string{"PATH": path}
	r := k.ticketWith(env, "start", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "書きかけ")
	has(t, r.Stderr, "再実行してください")
	eq(t, "HEAD", k.head(), before)
	if k.started(parentID) {
		t.Error("着手を書いた")
	}
	okRun(t, k.ticketWith(env, "start", parentID))
	eq(t, "リモート", k.remoteParent(), k.head())
	eq(t, "未コミット", k.dirty(), "")
}

// 途中の操作（merge など）があれば始めない。
func TestAnOperationInProgressStopsBeforeAnything(t *testing.T) {
	k := newKit(t)
	shkit.Write(t, k.mergeHead(), k.head()+"\n")
	r := k.ticket("start", parentID)
	if r.Code == 0 {
		t.Error("途中の操作があるのに 0")
	}
	has(t, r.Stderr, "途中の操作")
	if k.started(parentID) {
		t.Error("着手を書いた")
	}
}

// 戻し: push が通らなければ、自分のコミットを比較つきで戻し、書いたパスの中身も戻して 1 回だけやり直す。

func TestARejectedPushIsUndoneAndRetriedOnce(t *testing.T) {
	k := newKit(t)
	k.rejectPushes(1)
	before := k.head()
	r := k.ticket("start", parentID)
	code(t, r, 0)
	has(t, r.Stderr, "戻した")
	eq(t, "1 つ前", k.sha(k.tree, "HEAD~1"), before)
	eq(t, "リモート", k.remoteParent(), k.head())
	eq(t, "未コミット", k.dirty(), "")
}

func TestTwoRejectedPushesLeaveNothingWritten(t *testing.T) {
	k := newKit(t)
	k.rejectPushes(-1)
	before := k.head()
	copyBefore := shkit.Read(t, k.copyPath(parentID, "doing"))
	r := k.ticket("start", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "ccnavi-sync.sh")
	eq(t, "HEAD", k.head(), before)
	eq(t, "コピー", shkit.Read(t, k.copyPath(parentID, "doing")), copyBefore)
	eq(t, "未コミット", k.dirty(), "")
}

// 送る前に TERM が来たら、自分のコミットを戻して抜ける。
func TestATermBeforeThePushEndsUndoesTheCommit(t *testing.T) {
	if ospath.Windows {
		t.Skip("TERM を捕まえさせられない")
	}
	k := newKit(t)
	mark := filepath.Join(k.base, "pushing")
	hooks := strings.TrimSpace(k.try(k.tree, "rev-parse", "--git-path", "hooks").Stdout)
	if !filepath.IsAbs(hooks) {
		hooks = filepath.Join(k.tree, hooks)
	}
	shkit.WriteExec(t, filepath.Join(hooks, "pre-push"), "#!/bin/sh\n: >'"+shkit.Slash(mark)+"'\nsleep 5\nexit 1\n")
	before := k.head()
	p := k.spawn("ccnavi-ticket.sh", nil, "start", parentID)
	err := p.termWhen(t, mark)
	has(t, err, "送る前に止められた")
	eq(t, "HEAD", k.head(), before)
	eq(t, "未コミット", k.dirty(), "")
	if shkit.Exists(k.lockDir()) {
		t.Error("ロックが残っている")
	}
}

// 届いていた push（応答だけが失われた）は ls-remote で確かめて成功にする。

func TestAPushWhoseReplyWasLostCountsAsSent(t *testing.T) {
	k := newKit(t)
	path := k.rawShim("shim", fill(lostReply, "GIT", k.gitPath))
	r := k.ticketWith(map[string]string{"PATH": path}, "start", parentID)
	code(t, r, 0)
	has(t, r.Stderr, "届いていた")
	eq(t, "リモート", k.remoteParent(), k.head())
}

// push の応答も ls-remote も失敗したが届いていた。戻して取り込み直すと自分のコミットが戻るので、書き直さずに成功で終える。
// push のゲートの ls-remote（push の前）は数えない。push のあとの 1 回目だけ失敗させる。
func TestADeliveredPushWhoseCheckAlsoFailedIsNotWrittenTwice(t *testing.T) {
	k := newKit(t)
	count := shkit.Write(t, filepath.Join(k.base, "ls-count"), "0\n")
	pushed := filepath.Join(k.base, "pushed")
	path := k.rawShim("shim2", "#!/bin/sh\nfor a in \"$@\"; do\n  if [ \"$a\" = push ]; then\n    : > '"+shkit.Slash(pushed)+"'\n"+
		"    '"+k.gitPath+"' \"$@\" >/dev/null 2>&1; echo 'fatal: hung up' >&2; exit 128\n  fi\n"+
		"  if [ \"$a\" = refs/heads/"+parentID+" ] && [ -e '"+shkit.Slash(pushed)+"' ]; then\n"+
		"    n=$(cat '"+shkit.Slash(count)+"'); echo $((n + 1)) > '"+shkit.Slash(count)+"'\n"+
		"    if [ \"$n\" = 0 ]; then echo 'fatal: unable to access' >&2; exit 128; fi\n  fi\ndone\n"+
		"exec '"+k.gitPath+"' \"$@\"\n")
	r := k.ticketWith(map[string]string{"PATH": path}, "start", parentID)
	code(t, r, 0)
	has(t, r.Stderr, "書き直さずに終える")
	subjects := k.subjects(2)
	eq(t, "着手のコミット", subjects[0], "ccnavi: "+parentID+" に着手")
	eq(t, "その前", subjects[1], "approve")
	eq(t, "リモート", k.remoteParent(), k.head())
	eq(t, "未コミット", k.dirty(), "")
}

// コミット: 失敗したときの索引と、タイムアウト監視の時間。

// 署名に失敗してコミットできなければ、add した新しいファイルも索引から外す。
func TestAFailedCommitLeavesNothingStaged(t *testing.T) {
	k := newKit(t)
	k.git(k.ws, "config", "commit.gpgsign", "true")
	k.git(k.ws, "config", "gpg.program", "false")
	code(t, k.ticket("start", parentID), 1)
	k.git(k.ws, "config", "commit.gpgsign", "false")
	k.childTree()
	before := k.head()
	k.git(k.ws, "config", "commit.gpgsign", "true")
	r := k.ticket("start", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "コミットできなかった")
	eq(t, "HEAD", k.head(), before)
	eq(t, "索引", k.git(k.tree, "diff", "--cached", "--name-only"), "")
	eq(t, "未コミット", k.dirty(), "")
}

func TestAHangingSignerIsCutOff(t *testing.T) {
	k := newKit(t)
	signer := shkit.WriteExec(t, filepath.Join(k.base, "slow-gpg"), "#!/bin/sh\nsleep 30\n")
	k.git(k.ws, "config", "commit.gpgsign", "true")
	k.git(k.ws, "config", "gpg.program", shkit.Slash(signer))
	r := k.ticketWith(map[string]string{env.WriteAndPushCommitTimeout: "2"}, "start", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "制限時間")
	eq(t, "未コミット", k.dirty(), "")
}

// 断ったあと、親のブランチ・リモート・ロック・hook のマーカーが動いていない。
func (k *kit) assertNothingChanged(before, pending string) {
	k.t.Helper()
	eq(k.t, "HEAD", k.head(), before)
	eq(k.t, "リモート", k.remoteParent(), before)
	has(k.t, k.dirty(), pending)
	if shkit.Exists(k.lockDir()) {
		k.t.Error("ロックが残っている")
	}
}

// 親に着手していない子の start は断られる。hook のマーカーを (b) でコミットしない。
func TestStartRefusedUpFrontLeavesNoChildWorktreeOrCommit(t *testing.T) {
	k := newKit(t)
	pending := k.mark(1, "pending", `{"review": true, "at": "2026-09-29T00:00:00+0900"}`)
	before := k.head()
	r := k.ticket("start", childID)
	code(t, r, 1)
	has(t, r.Stderr, "未着手")
	next := linesWith(r.Stderr, "次: ")
	if len(next) != 1 {
		t.Fatalf("次の一手 %d 行\n%s", len(next), r.Stderr)
	}
	has(t, next[0], "ccnavi-ticket.sh start "+childID)
	if shkit.Exists(filepath.Join(k.ws, ".claude", "worktrees", childID)) {
		t.Error("子のワークツリーを作った")
	}
	eq(t, "子のブランチ", k.git(k.ws, "branch", "--list", childID), "")
	k.assertNothingChanged(before, pending)
}

func TestFinishOfAnUnstartedChildIsRefusedBeforeTheMerge(t *testing.T) {
	k := newKit(t)
	okRun(t, k.ticket("start", parentID))
	tree := k.childTree()
	pending := k.mark(1, "pending", `{"review": true, "at": "2026-09-29T00:00:00+0900"}`)
	before := k.head()
	r := k.ticket("finish", childID)
	code(t, r, 1)
	has(t, r.Stderr, "未着手")
	eq(t, "次の一手の行", len(linesWith(r.Stderr, "次: ")), 1)
	if shkit.Exists(filepath.Join(k.tree, ".git", "MERGE_HEAD")) {
		t.Error("合流を始めた")
	}
	eq(t, "子の HEAD", k.sha(tree, "HEAD"), before)
	k.assertNothingChanged(before, pending)
}

// --check は書く前の検査だけ。通っても断っても、何も書かず、子のワークツリーも作らない。
func TestCheckFlagRunsOnlyTheGate(t *testing.T) {
	k := newKit(t)
	before := k.head()
	ok := k.ticket("start", parentID, "--check")
	code(t, ok, 0)
	has(t, ok.Stdout, "何も変えていません")
	eq(t, "HEAD", k.head(), before)
	if k.started(parentID) {
		t.Error("着手を書いた")
	}
	done := k.copyPath(childID, "done")
	shkit.Write(t, done, shkit.Read(t, k.copyPath(childID, "doing")))
	refused := k.ticket("start", childID, "--check")
	code(t, refused, 1)
	has(t, refused.Stderr, "複数の場所")
	if shkit.Exists(filepath.Join(k.ws, ".claude", "worktrees", childID)) {
		t.Error("子のワークツリーを作った")
	}
	eq(t, "HEAD", k.head(), before)
}

// ワークスペースがリンク越しでも、運べる。
func TestALinkedWorkspaceStillCarries(t *testing.T) {
	if !base.CanSymlink() {
		t.Skip("シンボリックリンクが作れない")
	}
	k := newKit(t)
	linked := k.ws + "-link"
	if err := os.Symlink(k.ws, linked); err != nil {
		t.Skip(err)
	}
	t.Cleanup(func() { _ = os.Remove(linked) })
	r := run.Sh(t, filepath.Join(linked, ".ccnavi", "scripts", "ccnavi-ticket.sh"), []string{"start", parentID},
		run.Opts{Dir: linked, Env: k.ticketEnv(nil)})
	code(t, r, 0)
	eq(t, "リモート", k.remoteParent(), k.head())
	eq(t, "未コミット", k.dirty(), "")
}

// 止める親子のチケット

// リモートから消えた親（マージされていない）は、取り込み（ccnavi-sync.sh）が終了コード 1 で止める。何も書かない。
func TestAParentGoneFromTheRemoteStopsAtTheTakeIn(t *testing.T) {
	k := newKit(t)
	shkit.Write(t, filepath.Join(k.scripts, "ccnavi-review.sh"), "#!/bin/sh\n[ \"$1\" = merged ] && echo none\nexit 0\n")
	k.git(k.ws, "--git-dir", k.remote, "update-ref", "-d", "refs/heads/"+parentID)
	r := k.ticket("start", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "取り込めなかった")
	has(t, r.Stderr, "戻し方 1")
	if k.started(parentID) {
		t.Error("着手を書いた")
	}
	eq(t, "リモート", k.remoteParent(), "")
}

// 取り込みが通っても（スタブが 0 で終わる）、リモートに無い親のブランチは作り直さない。write-and-push の push の前のゲートが止める。
func TestAParentGoneFromTheRemoteIsRefusedByThePushGate(t *testing.T) {
	k := newKit(t)
	shkit.Write(t, filepath.Join(k.scripts, "ccnavi-sync.sh"), "#!/bin/sh\nexit 0\n")
	k.git(k.ws, "--git-dir", k.remote, "update-ref", "-d", "refs/heads/"+parentID)
	r := k.ticket("start", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "親のブランチ "+parentID+" がリモートに無い")
	has(t, r.Stderr, "何も反映していません")
	if k.started(parentID) {
		t.Error("着手を書いた")
	}
	eq(t, "リモート", k.remoteParent(), "")
}

// 取り込み（ccnavi-sync.sh）が、マージ済みの親のワークツリーを片付けて 0 で終わっても、存在しないツリーには書かずに止まる。
func TestATakeInThatTidiedTheParentAwayWritesNothing(t *testing.T) {
	k := newKit(t)
	shkit.Write(t, filepath.Join(k.scripts, "ccnavi-sync.sh"),
		"#!/bin/sh\ngit -C \""+shkit.Slash(k.ws)+"\" worktree remove --force \""+shkit.Slash(k.tree)+"\"\nexit 0\n")
	before := k.remoteParent()
	r := k.ticket("start", parentID)
	if r.Code == 0 {
		t.Error("片付けられた親に書いたのに 0")
	}
	has(t, r.Stderr, "閉じた（マージ済み）か、ワークツリーが無い")
	has(t, r.Stderr, "何も反映していません")
	if shkit.Exists(k.tree) {
		t.Error("親のワークツリーが残っている")
	}
	eq(t, "リモート", k.remoteParent(), before)
}

// record-risk は write-and-push ではなく、finish に乗る。記録（judge.json）が未コミットでも、finish はコミットして push する。
func TestRecordRiskIsNotWriteAndPushAndRidesOnTheFinish(t *testing.T) {
	k := newKit(t)
	okRun(t, k.ticket("start", parentID))
	tree := k.childTree()
	okRun(t, k.ticket("start", childID))
	shkit.Write(t, filepath.Join(tree, "wip", "design", "a.md"), "a\n")
	k.git(tree, "add", "-A")
	k.git(tree, "commit", "-q", "-m", "work")
	head := k.head()
	judge := filepath.Join(k.tree, parts(approved), "phases", parentID, childID+".judge.json")
	data, _ := json.Marshal(map[string]any{"x": map[string]any{"hit": false, "reason": "r", "head": "h", "at": "t"}})
	shkit.Write(t, judge, string(data))
	okRun(t, k.ticket("finish", childID))
	if k.head() == head {
		t.Error("コミットが増えていない")
	}
	rel, _ := filepath.Rel(k.tree, judge)
	has(t, strings.Join(k.committed("HEAD"), "\n"), filepath.ToSlash(rel))
	eq(t, "リモート", k.remoteParent(), k.head())
	eq(t, "未コミット", k.dirty(), "")
}
