package wap

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
)

const (
	todo  = "wip/proposals/todo"
	flows = "wip/proposals/flows"
)

// put は提案を置く。todo/ の親・子の .md は、承認の画面の検査を通る形（承認済みの親と同じ範囲の改版）にして、text を本文の末尾に足す。
// text が --- で始まるときは、そのまま書く（形を自分で決める試験）。
func (k *kit) put(rel, text string) string {
	if text == "" {
		text = "x\n"
	}
	stem := strings.TrimSuffix(filepath.Base(rel), filepath.Ext(rel))
	if strings.HasPrefix(rel, todo+"/") && strings.HasSuffix(rel, ".md") && !strings.HasPrefix(text, "---") {
		switch stem {
		case parentID:
			text = k.parentText(samples.Plan("design"), nil) + "\n" + text
		case childID:
			text = samples.ChildTextReview(childID, parentID, 1, []string{"wip/design/*"}, samples.Off) + text
		}
	}
	write(k.t, filepath.Join(k.tree, parts(rel)), text)
	return rel
}

func (k *kit) parentText(plan []samples.Step, feedback []samples.Step) string {
	return samples.ParentText(parentID, plan, samples.ParentOpts{Feedback: feedback, Allow: []string{"src/*", "wip/*"}})
}

// result は approval-request の結果ファイル（Stop の AI 判定の操作実績）の 1 件。
type result map[string]string

// results は結果ファイルを、名前の順（書かれた順）に読む。
func (k *kit) results() []result {
	dir := filepath.Join(k.ws, "logs", "state", "stopops-result")
	entries, _ := os.ReadDir(dir)
	var found []result
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), ".kv") {
			continue
		}
		text := read(k.t, filepath.Join(dir, e.Name()))
		row := result{"text": text}
		for _, line := range strings.Split(text, "\n") {
			if key, value, ok := strings.Cut(line, "="); ok {
				row[key] = value
			}
		}
		found = append(found, row)
	}
	return found
}

func isDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, c := range s {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}

func (k *kit) routeOf(row result) [3]string { return [3]string{row["id"], row["route"], row["rc"]} }

// 承認待ちの提案と子のフローの下書きを親のブランチへ送る経路の通し。結果ファイルに経路（sent）・送った版・ファイルの版を書く。
func TestNewProposalsAreCommittedAndPushed(t *testing.T) {
	k := newKit(t)
	todoRel := k.put(todo+"/"+parentID+".md", "")
	childRel := k.put(todo+"/"+childID+".md", "")
	flow := k.put(flows+"/"+childID+".yml", "")
	before := k.head()
	r := k.ticket("approval-request", parentID)
	code(t, r, 0)
	head := k.head()
	if head == before {
		t.Error("コミットが増えていない")
	}
	eq(t, "リモート", k.remoteParent(), head)
	want := []string{todoRel, childRel, flow}
	sort.Strings(want)
	eqList(t, "コミット", k.committed("HEAD"), want)
	eqList(t, "件名", k.subjects(1), []string{"ccnavi: " + parentID + " の提案を送る"})
	eq(t, "未コミット", k.dirty(), "")
	has(t, r.Stdout, "提案: "+todoRel)
	eqList(t, "次の一手", linesWith(r.Stdout, "次: "), []string{"次: ユーザに承認を頼んで返事を待つ。承認が済むまで実装はしない"})
	lacks(t, r.Stderr, "子の提案がありません")
	if exists(k.lockDir()) {
		t.Error("ロックが残っている")
	}
	rows := k.results()
	if len(rows) != 1 {
		t.Fatalf("結果ファイル %d 件", len(rows))
	}
	eq(t, "結果", k.routeOf(rows[0]), [3]string{parentID, "sent", "0"})
	eq(t, "commit", rows[0]["commit"], head)
	has(t, rows[0]["text"], "file="+k.sha(k.tree, "HEAD:"+todoRel)+" "+todoRel)
}

// ccnavi-start.sh を通さずに手で切った親（未 push）。内部のエラーでなく、事実と次の手で止まる。
// start・finish は書く前の検査（承認済みの作業中か）が取り込みの前に断る。未 push の親の説明は approval-request だけが言う。
func TestAParentNeverPushedStopsBeforeAnythingIsWritten(t *testing.T) {
	k := newKit(t)
	tree := filepath.Join(k.ws, ".claude", "worktrees", "i0002")
	k.git(k.ws, "worktree", "add", "-q", tree, "-b", "i0002", "main")
	write(t, filepath.Join(tree, parts(todo+"/i0002.md")),
		samples.ParentText("i0002", samples.Plan("design"), samples.ParentOpts{Allow: []string{"src/*", "wip/*"}}))
	before := k.sha(tree, "HEAD")

	t.Run("approval-request は選択肢を言って止まる", func(t *testing.T) {
		r := k.sh("ccnavi-ticket.sh", opts{dir: tree}, "approval-request", "i0002")
		code(t, r, 1)
		err := r.Stderr
		has(t, err, "親のブランチ i0002 はリモートにありません")
		has(t, err, "何も反映していません")
		eq(t, "次の手の行", len(linesWith(err, "次の手: ")), 1)
		has(t, err, "その MR のブランチで作業するかを聞く")
		has(t, err, "ccnavi-start.sh' --issue <番号> で新しく切る")
		has(t, err, "git push -u origin i0002")
		has(t, err, "直接実装には切り替えない")
		lacks(t, err, "bad revision")
		lacks(t, err, "差分を読めない")
		eq(t, "リモート", k.remoteSHA("i0002"), "")
		if exists(filepath.Join(k.state, "locks", "self", "i0002")) {
			t.Error("ロックが残っている")
		}
	})
	for _, c := range []struct{ verb, want string }{
		{"start", "i0002 は承認済みの作業中ではない"},
		{"finish", "i0002 は作業中ではない"},
	} {
		t.Run(c.verb+" は取り込みの前の検査が断る", func(t *testing.T) {
			r := k.sh("ccnavi-ticket.sh", opts{dir: tree}, c.verb, "i0002")
			code(t, r, 1)
			has(t, r.Stderr, c.want)
			lacks(t, r.Stderr, "bad revision")
			lacks(t, r.Stderr, "次の手: ユーザに状況を伝えて")
		})
	}
	eq(t, "HEAD", k.sha(tree, "HEAD"), before)
}

// 承認で落ちる提案は、送る時点で断る。何も送らない。
func TestAProposalThatApprovalWouldRefuseIsRefusedBeforeItIsSent(t *testing.T) {
	cases := []struct {
		name     string
		plan     []string
		feedback []samples.Step
		want     []string
		next     bool
	}{
		{"計画の中を指すフィードバックの手順", []string{"design", "implement"}, []samples.Step{{Type: "implement-feedback", After: []int{1}}},
			[]string{"`feedback[0]` の `after` の 1 は読めない", "フィードバック計画の番号だけを指せる"}, true},
		{"全体計画のレビューが済む前のフィードバック計画", []string{"design", "implement"}, []samples.Step{{Type: "implement-feedback", After: []int{}}},
			[]string{"全体計画のフェーズが全部閉じてレビューが済んでから"}, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			k := newKit(t)
			k.put(todo+"/"+parentID+".md", k.parentText(samples.Plan(c.plan...), c.feedback))
			before := k.head()
			remoteBefore := k.remoteParent()
			r := k.ticket("approval-request", parentID)
			code(t, r, 1)
			for _, w := range c.want {
				has(t, r.Stderr, w)
			}
			if c.next {
				eqList(t, "次", linesWith(r.Stderr, "次: "), []string{"次: 直して `ccnavi-ticket.sh approval-request " + parentID + "` を打ち直す"})
			}
			lacks(t, r.Stdout, "提案:")
			eq(t, "HEAD", k.head(), before)
			eq(t, "リモート", k.remoteParent(), remoteBefore)
		})
	}
}

// 読めない（UTF-8 でない）提案は承認の画面でも外れる。ユーザに渡す前に止め、何も送らない。
func TestAProposalThatIsNotUTF8IsRefusedBeforeItIsSent(t *testing.T) {
	k := newKit(t)
	path := filepath.Join(k.tree, parts(todo), parentID+".md")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("---\nx: \xe9\n---\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	before := k.remoteParent()
	r := k.ticket("approval-request", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "何も送っていません")
	eq(t, "リモート", k.remoteParent(), before)
}

// --check は送る前の検査だけ。通っても、コミットも push も結果ファイルもない。
func TestCheckReportsOKAndSendsNothing(t *testing.T) {
	k := newKit(t)
	k.put(todo+"/"+parentID+".md", "")
	before := [2]string{k.head(), k.remoteParent()}
	r := k.ticket("approval-request", parentID, "--check")
	code(t, r, 0)
	has(t, r.Stdout, "確認: ok")
	lacks(t, r.Stdout, "提案:")
	eq(t, "HEAD とリモート", [2]string{k.head(), k.remoteParent()}, before)
	has(t, k.dirty(), "?? "+todo+"/"+parentID+".md")
	if exists(filepath.Join(k.state, "stopops-result")) {
		t.Error("結果ファイルを書いた")
	}
}

// 手でコミットした提案は、未 push のコミットだけを送る（新しいコミットを作らない）。
// そのあと未 push のコードがあれば、送る前の失敗として記録する。
func TestTheResultFileRecordsUnchangedAndARefusalBeforeSending(t *testing.T) {
	k := newKit(t)
	rel := k.put(todo+"/"+parentID+".md", "")
	k.git(k.tree, "add", "--", rel)
	k.git(k.tree, "commit", "-q", "-m", "approval-request by hand")
	before := k.head()
	if k.remoteParent() == before {
		t.Fatal("手でコミットしたのに送り済み")
	}
	code(t, k.ticket("approval-request", parentID), 0)
	eq(t, "HEAD", k.head(), before)
	eq(t, "リモート", k.remoteParent(), before)
	k.put("src/secret.py", "x = 1\n")
	k.git(k.tree, "add", "--", "src/secret.py")
	k.git(k.tree, "commit", "-q", "-m", "wip code")
	k.put(todo+"/"+parentID+".md", "v2\n")
	code(t, k.ticket("approval-request", parentID), 1)
	rows := k.results()
	// 親のブランチの状態で止めた回は、実行ファイルのゲート（treegate）が拒否した送る前の失敗。
	eq(t, "最後の経路", rows[len(rows)-1]["route"], "failed_before")
	if first := rows[0]["route"]; first != "sent" && first != "unchanged" {
		t.Errorf("最初の経路 = %q", first)
	}
}

// 変更も未 push のコミットも無い打ち直しは unchanged。直した提案は、そのパスだけを送る。
func TestAnUnchangedRetryRecordsUnchangedAndAnEditIsSent(t *testing.T) {
	k := newKit(t)
	rel := k.put(todo+"/"+parentID+".md", "v1\n")
	code(t, k.ticket("approval-request", parentID), 0)
	sent := k.head()
	code(t, k.ticket("approval-request", parentID), 0)
	eq(t, "HEAD", k.head(), sent)
	// 結果ファイルの名前は開始秒と pid で決まるため、同じ秒の 2 つは順番が決まらない。経路で見分ける。
	byRoute := map[string]result{}
	for _, row := range k.results() {
		byRoute[row["route"]] = row
	}
	if len(byRoute) != 2 || byRoute["sent"] == nil || byRoute["unchanged"] == nil {
		t.Fatalf("経路 = %v", byRoute)
	}
	u := byRoute["unchanged"]
	eq(t, "unchanged", [2]string{u["id"], u["rc"]}, [2]string{parentID, "0"})
	if !isDigits(u["started"]) || !isDigits(u["ended"]) {
		t.Errorf("時刻 %q %q", u["started"], u["ended"])
	}
	for _, key := range []string{"commit", "file"} { // 送ったものが無いので版は書かない
		if _, ok := u[key]; ok {
			t.Errorf("unchanged に %s がある", key)
		}
	}
	k.put(rel, "v2\n")
	code(t, k.ticket("approval-request", parentID), 0)
	eqList(t, "コミット", k.committed("HEAD"), []string{rel})
	eq(t, "リモート", k.remoteParent(), k.head())
}

// origin が無ければ送らず、コミットもしない（write_only）。
func TestTheResultFileRecordsWriteOnlyWithoutOrigin(t *testing.T) {
	k := newKit(t)
	k.git(k.ws, "remote", "remove", "origin")
	rel := k.put(todo+"/"+parentID+".md", "")
	head := k.head()
	remote := k.git(k.remote, "rev-parse", "refs/heads/"+parentID) // origin を外した後
	code(t, k.ticket("approval-request", parentID), 0)
	eq(t, "HEAD", k.head(), head) // 送らず、コミットもしない
	eq(t, "リモート", k.git(k.remote, "rev-parse", "refs/heads/"+parentID), remote)
	has(t, k.dirty(), "?? "+rel)
	rows := k.results()
	if len(rows) != 1 {
		t.Fatalf("結果ファイル %d 件", len(rows))
	}
	eq(t, "結果", k.routeOf(rows[0]), [3]string{parentID, "write_only", "0"})
	if !isDigits(rows[0]["started"]) || !isDigits(rows[0]["ended"]) {
		t.Errorf("時刻 %q %q", rows[0]["started"], rows[0]["ended"])
	}
	if _, ok := rows[0]["commit"]; ok {
		t.Error("commit がある")
	}
}

// 親のワークツリーが無い親子のチケットは、sh が何も書かずに止める（stopped）。
func TestTheResultFileRecordsStoppedWithoutAParentWorktree(t *testing.T) {
	k := newKit(t)
	r := k.ticket("approval-request", "i0099")
	code(t, r, 1)
	has(t, r.Stderr, "書かずに止めた")
	rows := k.results()
	if len(rows) != 1 {
		t.Fatalf("結果ファイル %d 件", len(rows))
	}
	eq(t, "結果", k.routeOf(rows[0]), [3]string{"i0099", "stopped", "1"})
	if _, ok := rows[0]["commit"]; ok {
		t.Error("commit がある")
	}
}

// 送る提案が無い・未送信のユーザの判断（review/ から done/ への移動など）がある。
// どちらも何も変えずに止め、stopped ではなく送る前の失敗として記録する。
func TestNoProposalAndAUserDecisionAreFailedBeforeNotStopped(t *testing.T) {
	k := newKit(t)
	before := k.remoteParent()
	empty := k.ticket("approval-request", parentID)
	code(t, empty, 1)
	has(t, empty.Stderr, "送る提案がありません")
	eq(t, "未コミット", k.dirty(), "")
	k.put(todo+"/"+parentID+".md", "")
	write(t, filepath.Join(k.tree, parts(approved), "done", "zzz.md"), "x\n")
	code(t, k.ticket("approval-request", parentID), 1)
	eq(t, "リモート", k.remoteParent(), before)
	rows := k.results()
	if len(rows) != 2 {
		t.Fatalf("結果ファイル %d 件", len(rows))
	}
	for _, row := range rows {
		eq(t, "経路と終了コード", [2]string{row["route"], row["rc"]}, [2]string{"failed_before", "1"})
	}
}

// push が済んだ後の「次:」の出力先が閉じていても、送った結果は sent・rc=0 のまま（結果は 1 件だけ）。
func TestAProposeWithItsOutputCutWritesExactlyOneResultForThePushedCommit(t *testing.T) {
	if ospath.Windows {
		t.Skip("SIGPIPE の終わり方は POSIX の sh を前提にする")
	}
	k := newKit(t)
	rel := k.put(todo+"/"+parentID+".md", "")
	script := shQuote(filepath.Join(k.scripts, "ccnavi-ticket.sh"))
	// 出力を先頭の 1 行で切る。
	r := run.Command(t, "sh", []string{"-c", "sh " + script + " approval-request " + parentID + " | head -n 1"},
		run.Opts{Dir: k.ws, Env: k.ticketEnv(nil)})
	code(t, r, 0)
	eq(t, "標準出力", r.Stdout, "提案: "+rel+"\n")
	head := k.head()
	eq(t, "リモート", k.remoteParent(), head) // 出力を切られても、送るところまでは済む
	rows := k.results()
	if len(rows) != 1 {
		t.Fatalf("結果ファイル %d 件", len(rows))
	}
	eq(t, "結果", k.routeOf(rows[0]), [3]string{parentID, "sent", "0"})
	eq(t, "commit", rows[0]["commit"], head)
	has(t, rows[0]["text"], "file="+k.sha(k.tree, "HEAD:"+rel)+" "+rel)
}

func shQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'" }

// 送れなかったときに戻すのは自分のコミットだけ。書いた提案（新しい提案・送り済みの提案の直し）は消えない。結果は送る前の失敗。
func TestARejectedPushKeepsWhatTheUserWrote(t *testing.T) {
	t.Run("新しい提案", func(t *testing.T) {
		k := newKit(t)
		newRel := k.put(todo+"/"+parentID+".md", "v1\n")
		flow := k.put(flows+"/"+childID+".yml", "flow\n")
		written := read(t, filepath.Join(k.tree, parts(newRel)))
		k.rejectAllPushes()
		before, remoteBefore := k.head(), k.remoteParent()
		r := k.ticket("approval-request", parentID)
		if r.Code == 0 {
			t.Error("push を断られたのに 0")
		}
		eq(t, "HEAD", k.head(), before)
		eq(t, "リモート", k.remoteParent(), remoteBefore)
		eq(t, "提案", read(t, filepath.Join(k.tree, parts(newRel))), written)
		eq(t, "フロー", read(t, filepath.Join(k.tree, parts(flow))), "flow\n")
		lacks(t, r.Stdout, "次: ")
		rows := k.results()
		if len(rows) != 1 {
			t.Fatalf("結果ファイル %d 件", len(rows))
		}
		eq(t, "経路", rows[0]["route"], "failed_before")
		if rows[0]["rc"] == "0" {
			t.Error("rc が 0")
		}
	})
	t.Run("送り済みの提案の直し", func(t *testing.T) {
		k := newKit(t)
		rel := k.put(todo+"/"+parentID+".md", "v1\n")
		code(t, k.ticket("approval-request", parentID), 0)
		k.put(rel, "v2 precious edit\n")
		edited := read(t, filepath.Join(k.tree, parts(rel)))
		k.rejectAllPushes()
		r := k.ticket("approval-request", parentID)
		if r.Code == 0 {
			t.Error("push を断られたのに 0")
		}
		eq(t, "直し", read(t, filepath.Join(k.tree, parts(rel))), edited)
		lacks(t, r.Stdout, "次: ")
	})
}

// 未 push のコードは、提案と一緒に送らない。
func TestUnpushedCodeIsNotSentAlongWithTheProposal(t *testing.T) {
	k := newKit(t)
	k.put("src/secret.py", "x = 1\n")
	k.git(k.tree, "add", "--", "src/secret.py")
	k.git(k.tree, "commit", "-q", "-m", "wip code")
	k.put(todo+"/"+parentID+".md", "")
	before := k.remoteParent()
	r := k.ticket("approval-request", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "src/secret.py")
	eq(t, "リモート", k.remoteParent(), before)
	has(t, k.dirty(), "?? "+todo+"/"+parentID+".md")
	if exists(k.lockDir()) {
		t.Error("ロックが残っている")
	}
}

// ワークスペースルートにコピーがあっても、親のワークツリーに無ければ送らない。
func TestItReadsOnlyTheParentWorktree(t *testing.T) {
	k := newKit(t)
	write(t, filepath.Join(k.ws, "wip", "proposals", "todo", parentID+".md"), "x\n")
	r := k.ticket("approval-request", parentID)
	code(t, r, 1)
	has(t, r.Stderr, "送る提案がありません")
}

// finish が通ったら、sh が実行ファイルに `worktree tidy <識別子>` を頼む（write-and-push なら送った後）。
func TestFinishAsksTheExeToTidyAfterSending(t *testing.T) {
	k := newKit(t)
	log := filepath.Join(k.base, "tidy.log")
	logger := k.wrapExe("logger", "case \" $* \" in\n*\" worktree tidy \"*) printf '%s\\n' \"$*\" >>'"+log+"' ;;\nesac\n", k.bin)
	okRun(t, k.ticketWith(map[string]string{env.EntryPath: logger}, "start", parentID))
	if exists(log) {
		t.Error("親の着手で tidy を頼んだ")
	}
	tree := k.childTree()
	okRun(t, k.ticketWith(map[string]string{env.EntryPath: logger}, "start", childID))
	write(t, filepath.Join(tree, "wip", "design", "a.md"), "a\n")
	k.git(tree, "add", "-A")
	k.git(tree, "commit", "-q", "-m", "work")
	okRun(t, k.ticketWith(map[string]string{env.EntryPath: logger}, "finish", childID))
	eq(t, "リモート", k.remoteParent(), k.head())
	called := read(t, log)
	// 識別子は名前の形で剥がさずにそのまま渡す（親へ割り出すのは実行ファイル）。この子はレビュー待ち（review/）へ動いたので、
	// 見返す場所としてワークツリーは残る（消すのは confirm が done/ へ動かした後）
	has(t, called, "worktree tidy "+childID)
	has(t, called, "--cwd")
	if !isDir(tree) {
		t.Error("レビュー待ちの子のワークツリーが消えた")
	}
}

// 取り消した子は作業中でないので、送った後にそのワークツリーを消す。
func TestCancelDropsTheCancelledChildsWorktree(t *testing.T) {
	k := newKit(t)
	okRun(t, k.ticket("start", parentID))
	tree := k.childTree()
	okRun(t, k.ticket("start", childID))
	r := okRun(t, k.ticket("cancel", childID, "--reason", "やめた"))
	eq(t, "リモート", k.remoteParent(), k.head())
	has(t, r.Stdout, "ワークツリー "+childID+" を削除しました")
	if exists(tree) {
		t.Error("子のワークツリーが残っている")
	}
	eq(t, "ブランチ", k.git(k.ws, "branch", "--list", childID), childID)
}

// 子の start で、この PC に子のワークツリーが無ければ、取り込み直後の親ブランチから作る（別の PC で承認された子でも同じ場所）。
func TestStartCreatesTheMissingChildWorktreeFromTheParentBranch(t *testing.T) {
	k := newKit(t)
	okRun(t, k.ticket("start", parentID))
	childPath := filepath.Join(k.ws, ".claude", "worktrees", childID)
	if exists(childPath) {
		t.Fatal("子のワークツリーが先にある")
	}
	parentHead := k.head()
	r := okRun(t, k.ticket("start", childID))
	if !isDir(childPath) {
		t.Fatal("子のワークツリーを作っていない")
	}
	eq(t, "子のブランチ", k.git(childPath, "rev-parse", "--abbrev-ref", "HEAD"), childID)
	// 子のワークツリーは、着手を書く前の（取り込み直後の）親ブランチの先頭から切れている
	eq(t, "子の元", k.git(childPath, "rev-parse", "HEAD"), parentHead)
	has(t, r.Stderr, "子のワークツリー")
	has(t, strings.Join(k.committed("HEAD"), "\n"), approved+"/doing/"+childID+".md")
	eq(t, "リモート", k.remoteParent(), k.head())
	// 親子ごとの取り込み状態は、どこにも作らない。手元に残った子のブランチの扱いは Go の単体テスト（ticketsh）が見る。
	if exists(filepath.Join(k.state, "sync")) {
		t.Error("取り込み状態を作った")
	}
}

// origin の無いリポジトリは write-and-push の対象にならない（今のまま、書くだけ）。
func TestAnOriginLessFamilyIsNotWriteAndPush(t *testing.T) {
	k := newKit(t)
	k.git(k.ws, "remote", "remove", "origin")
	head := k.head()
	okRun(t, k.ticket("start", parentID))
	eq(t, "HEAD", k.head(), head)
	has(t, k.dirty(), approved+"/doing/"+parentID+".md")
}
