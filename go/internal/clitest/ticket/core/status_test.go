package core

import (
	"io/fs"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
	"ccnavi/internal/infra/ospath"
)

// newPhaseTree はフェーズ定義の見本（samples.Phases）を持つ木。
func newPhaseTree(t *testing.T) *ticketkit.Tree {
	t.Helper()
	return ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
}

func researchParent(ident string, steps ...samples.Step) string {
	if len(steps) == 0 {
		steps = samples.Plan("research")
	}
	return samples.ParentText(ident, steps, samples.ParentOpts{})
}

// said は `ticket status <args>` が 0 で終わったときの標準出力。
func said(t *testing.T, tr *ticketkit.Tree, args ...string) string {
	t.Helper()
	return ticketkit.OK(t, tr.Ccnavi(append([]string{"ticket", "status"}, args...)...)).Stdout
}

// byHand は GitHub の画面で動かしたのと同じく、中身を変えずに doing/ へ置く。
func byHand(t *testing.T, tr *ticketkit.Tree, text string) {
	t.Helper()
	ticketkit.Write(t, tr.ApprovedPath("doing", "i0001"), text)
}

// writeAndPushTarget は本物の write-and-push の見分け（`ccnavi write-and-push family`）の答え。
func writeAndPushTarget(t *testing.T, tr *ticketkit.Tree, ident string) string {
	t.Helper()
	r := ticketkit.OK(t, tr.Ccnavi("write-and-push", "family", ident))
	for _, line := range ticketkit.Lines(r.Stdout) {
		if key, value, _ := strings.Cut(line, " "); key == "target" {
			return value
		}
	}
	return ""
}

// block は 1 つのチケットの行（`- <識別子>` から次の `- ` か親子の見出しまで）。
func block(t *testing.T, out, ident string) string {
	t.Helper()
	lines := strings.Split(out, "\n")
	for start, line := range lines {
		if strings.HasPrefix(line, "- "+ident+" ") {
			end := start + 1
			for end < len(lines) && strings.HasPrefix(lines[end], "    ") {
				end++
			}
			return strings.Join(lines[start:end], "\n")
		}
	}
	t.Fatalf("%s の行が無い:\n%s", ident, out)
	return ""
}

func addOrigin(tr *ticketkit.Tree, url string) { tr.Parent("remote", "add", "origin", url) }

func TestStatusListsAnUnfinishedOperationEvenWithoutOpenTickets(t *testing.T) {
	t.Parallel()
	tr := newPhaseTree(t)
	begun := ticketkit.OK(t, tr.Ccnavi("ops", "begin", "comment", "p1", "0", "7", "k", "comment:comment"))
	out := said(t, tr)
	ticketkit.Has(t, out, "引き継ぎ未完了")
	ticketkit.Has(t, out, strings.TrimSpace(begun.Stdout))
	// --lint も同じ未完了を言う
	byHand(t, tr, researchParent("i0001"))
	ticketkit.Has(t, tr.Ccnavi("--lint").Stdout, "引き継ぎ未完了")
}

func TestExplainSaysWhatDryRunStillStopsAndWhatHappensWhenItCannotDecide(t *testing.T) {
	t.Parallel()
	tr := newPhaseTree(t)
	out := tr.Ccnavi("--explain").Stdout
	ticketkit.Has(t, out, "dry-run のときに止めるもの")
	ticketkit.Has(t, out, "判定に達せなかったとき")
	ticketkit.Has(t, out, "Write・Edit・MultiEdit・NotebookEdit・Agent・Task: enable は止める、dry-run は止める")
}

// 手で置いて未コミットの承認は、時刻が「未コミット（手で置いた）」で、ユーザに運んでもらう案内を出す（エージェントが運ぶ
// git のコマンドは出さない）。write-and-push の対象でなければ「start へ進んでよい」、対象なら「ユーザがコミットして push するまで
// start は止まる」。状態は何も動かさない。
func TestATicketPlacedByHandIsUncommittedAndWaitsAccordingToWriteAndPush(t *testing.T) {
	t.Parallel()
	t.Run("write-and-push の対象でなければ start へ進んでよい", func(t *testing.T) {
		t.Parallel()
		tr := newPhaseTree(t)
		byHand(t, tr, researchParent("i0001"))
		out := said(t, tr, "i0001")
		ticketkit.Has(t, out, "リモートからの取得は行いません")
		ticketkit.Has(t, out, "最新の状態と異なる場合があります")
		mine := block(t, out, "i0001")
		for _, want := range []string{
			"作業中（doing/）", "承認: 未コミット（手で置いた）", "着手: 未着手", "置き場のファイル: 未コミット",
			"ccnavi-user-push-approved.sh i0001", "エージェントはコミットも push もしない",
			"start へ進んでよい", "ccnavi-ticket.sh start i0001", "メインエージェントのみ実行可能",
		} {
			ticketkit.Has(t, mine, want)
		}
		ticketkit.Lacks(t, out, "ccnavi-git.sh add")
		ticketkit.Lacks(t, mine, "commit")
		if ticketkit.Exists(filepath.Join(tr.Approved, "events")) {
			t.Error("状態を動かした")
		}
	})
	t.Run("write-and-push の対象ならユーザの push を待つ", func(t *testing.T) {
		t.Parallel()
		tr := newPhaseTree(t)
		// マージリクエストを持つフェーズ（design）を含める。持たない親子は write-and-push の対象外。
		byHand(t, tr, researchParent("i0001", samples.Plan("research", "design")...))
		// 親のワークツリーがあり、origin がある。
		addOrigin(tr, filepath.Join(tr.Root, "nowhere.git"))
		if got := writeAndPushTarget(t, tr, "i0001"); got != "yes" {
			t.Fatalf("write-and-push の対象でない: %q", got)
		}
		mine := block(t, said(t, tr, "i0001"), "i0001")
		ticketkit.Has(t, mine, "ユーザがコミットして push するまで start は実行できません")
		ticketkit.Lacks(t, mine, "start へ進んでよい")
		ticketkit.Lacks(t, mine, "ccnavi-ticket.sh start")
	})
}

// 手で動かした計画付きの親も計画の `after` で読む（一直線と読み替える注意は出さない）。計画が壊れていれば（終端が 1 つでない）、
// status が止める理由を言う。
func TestAHandMovedParentIsReadByItsPlan(t *testing.T) {
	t.Parallel()
	t.Run("計画の after で読む", func(t *testing.T) {
		t.Parallel()
		tr := newPhaseTree(t)
		byHand(t, tr, researchParent("i0001", samples.Plan("research", "design")...))
		mine := block(t, said(t, tr), "i0001")
		ticketkit.Lacks(t, mine, "一直線")
		ticketkit.Lacks(t, mine, "止まっている理由")
	})
	t.Run("壊れた計画は止める理由を言う", func(t *testing.T) {
		t.Parallel()
		tr := newPhaseTree(t)
		byHand(t, tr, researchParent("i0001", samples.Step{Type: "research"}, samples.Step{Type: "design", After: []int{}}))
		mine := block(t, said(t, tr), "i0001")
		ticketkit.Has(t, mine, "止まっている理由")
		ticketkit.Has(t, mine, "先行していない")
	})
}

func TestFieldsWrittenByStartAreNotAnApprovalToCarry(t *testing.T) {
	t.Parallel()
	tr := newPhaseTree(t)
	tr.PhaseFamily(samples.Plan("research"))
	tr.StartParent()
	mine := block(t, said(t, tr, "i0001"), "i0001")
	ticketkit.Has(t, mine, "未コミットの変更がある")
	ticketkit.Lacks(t, mine, "ccnavi-user-push-approved.sh")
}

// コミット済みで未 push・push 済みを、手元のリモート追跡の ref で言い分ける。
func TestCommittedTicketsAreToldApartByTheRemoteTrackingRef(t *testing.T) {
	t.Parallel()
	tr := newPhaseTree(t)
	tr.PhaseFamily(samples.Plan("research"))
	mine := block(t, said(t, tr, "i0001"), "i0001")
	ticketkit.Has(t, mine, "リモート追跡参照がない")
	// 承認は時刻を残さない
	ticketkit.Lacks(t, mine, "未コミット（手で置いた）")
	ticketkit.Lacks(t, mine, "承認: 不明")
	bare := filepath.Join(tr.Root, "origin.git")
	tr.Git(tr.Root, "init", "--quiet", "--bare", bare)
	addOrigin(tr, bare)
	tr.Parent("push", "--quiet", "-u", "origin", "i0001")
	ticketkit.Has(t, block(t, said(t, tr, "i0001"), "i0001"), "push 済み（origin/i0001）")
	path := tr.ApprovedPath("doing", "i0001")
	ticketkit.Write(t, path, ticketkit.Read(t, path)+"\n追記\n")
	tr.CommitParent("touch")
	ticketkit.Has(t, block(t, said(t, tr, "i0001"), "i0001"), "未push（origin/i0001 に未反映の変更があります）")
}

// child は親 i0001 の research の子（review なし）の提案。
func researchChild(name string) string {
	return samples.ChildTextReview(name, "i0001", 1, []string{"wip/research/*"}, samples.Off)
}

func TestAChildOfAnUnstartedParentIsStopped(t *testing.T) {
	t.Parallel()
	tr := newPhaseTree(t)
	tr.PhaseFamily(samples.Plan("research"))
	tr.Propose("i0001-01-01", researchChild("i0001-01-01"), "")
	tr.CommitParent("tickets")
	tr.Approve()
	out := said(t, tr, "i0001")
	child := block(t, out, "i0001-01-01")
	ticketkit.Has(t, child, "止まっている理由: 親チケット i0001 が未着手")
	ticketkit.Lacks(t, child, "start i0001-01-01")
	ticketkit.Has(t, block(t, out, "i0001"), "ccnavi-ticket.sh start i0001")
}

// 基準点が HEAD の祖先でないときは注意を出す。started_at の無い base_sha は判定が blocked で止めるので、止まっている理由は
// そちらで出る。再開で残った欄（作業中なのに completed_at）も注意で、finish を案内する。
func TestOddFieldsOnAnOpenTicketAreWarnings(t *testing.T) {
	t.Parallel()
	t.Run("履歴にない基準点", func(t *testing.T) {
		t.Parallel()
		tr := newPhaseTree(t)
		tr.PhaseFamily(samples.Plan("research"))
		other := tr.Worktree("elsewhere", "main")
		ticketkit.Write(t, filepath.Join(other, "x.txt"), "x\n")
		tr.Git(other, "add", "-A")
		tr.Git(other, "commit", "--quiet", "-m", "elsewhere")
		stray := strings.TrimSpace(tr.Git(other, "rev-parse", "HEAD"))
		tr.Replace(tr.ApprovedPath("doing", "i0001"), `base_sha: ""`, `base_sha: "`+stray+`"`)
		tr.CommitParent("planted")
		mine := block(t, said(t, tr, "i0001"), "i0001")
		ticketkit.Has(t, mine, "注意: 基準点")
		ticketkit.Has(t, mine, "HEAD の祖先でない")
		ticketkit.Has(t, mine, "止まっている理由: `started_at` が無いのに `base_sha` がある")
		ticketkit.Lacks(t, mine, "ccnavi-ticket.sh start i0001'")
	})
	t.Run("再開で残った欄", func(t *testing.T) {
		t.Parallel()
		tr := newPhaseTree(t)
		tr.PhaseFamily(samples.Plan("research"))
		tr.StartParent()
		tr.Replace(tr.ApprovedPath("doing", "i0001"), `completed_at: ""`, `completed_at: "2026-09-01T00:00:00+0900"`)
		tr.CommitParent("resumed")
		mine := block(t, said(t, tr, "i0001"), "i0001")
		ticketkit.Has(t, mine, "注意: 作業中なのに completed_at")
		ticketkit.Has(t, mine, "finish i0001")
	})
}

func TestAPendingProposalWaitsForTheUsersApproval(t *testing.T) {
	t.Parallel()
	tr := newPhaseTree(t)
	tr.Propose("i0001", researchParent("i0001"), "")
	mine := block(t, said(t, tr, "i0001"), "i0001")
	ticketkit.Has(t, mine, "承認待ち（todo/）")
	ticketkit.Has(t, mine, "ユーザの承認を待つ")
	ticketkit.Lacks(t, mine, "start")
}

func TestAClosedChildShowsHowItWasClosed(t *testing.T) {
	t.Parallel()
	t.Run("取り消し", func(t *testing.T) {
		t.Parallel()
		tr := newPhaseTree(t)
		tr.PhaseFamily(samples.Plan("research"))
		tr.Propose("i0001-01-01", researchChild("i0001-01-01"), "")
		tr.CommitParent("tickets")
		tr.Approve()
		ticketkit.OK(t, tr.Ccnavi("ticket", "cancel", "i0001-01-01", "--reason", "要らなくなった"))
		child := block(t, said(t, tr, "i0001"), "i0001-01-01")
		ticketkit.Has(t, child, "クローズ済み（done/）")
		ticketkit.Has(t, child, "取り消し")
		ticketkit.Has(t, child, "要らなくなった")
	})
	t.Run("finish で閉じた子は合流した先頭を出す", func(t *testing.T) {
		t.Parallel()
		tr := newPhaseTree(t)
		tr.PhaseFamily(samples.Plan("research"))
		tr.Propose("i0001-01-01", researchChild("i0001-01-01"), "")
		tr.CommitParent("tickets")
		tr.Approve()
		tr.RunChild("i0001-01-01", ticketkit.File{Path: "wip/research/summary.md", Text: "a\n"})
		ticketkit.OK(t, tr.Ccnavi("ticket", "finish", "i0001-01-01"))
		var done string
		_ = filepath.WalkDir(tr.Root, func(path string, d fs.DirEntry, err error) error {
			if err == nil && !d.IsDir() && d.Name() == "i0001-01-01.md" && filepath.Base(filepath.Dir(path)) == "done" {
				done = path
			}
			return nil
		})
		if done == "" {
			t.Fatal("done/ に閉じたチケットが無い")
		}
		ticketkit.Write(t, done, samples.SetFields(ticketkit.Read(t, done), samples.Field{Key: "merged_sha", Value: strings.Repeat("0123456789ab", 3)}))
		child := block(t, said(t, tr, "i0001"), "i0001-01-01")
		if !regexp.MustCompile(`閉じ方: 完了（[^）]*、合流 0123456789ab）`).MatchString(child) {
			t.Errorf("閉じ方に合流先が出ない:\n%s", child)
		}
	})
}

// 親を省けば開いている親子を全部、知らない親なら終了コード 1、何も無ければその旨。
func TestStatusWithoutAParentOrWithAnUnknownOne(t *testing.T) {
	t.Parallel()
	tr := newPhaseTree(t)
	ticketkit.Has(t, said(t, tr), "チケットはありません。")
	byHand(t, tr, researchParent("i0001"))
	out := said(t, tr)
	ticketkit.Has(t, out, "== 親子 i0001")
	if said(t, tr, "i0001-01-01") != said(t, tr, "i0001") {
		t.Error("子を渡しても親を渡しても同じ答えのはず")
	}
	r := tr.Ccnavi("ticket", "status", "i9999")
	if r.Code != 1 {
		t.Errorf("終了コード %d", r.Code)
	}
	ticketkit.Has(t, r.Stderr, "i9999")
}

// 立ち位置は止めずに write-and-push だけが止める本物の形。親のワークツリーのパスに改行がある。
func TestAFamilyWriteAndPushStopsNamesTheReasonAndDoesNotSayGo(t *testing.T) {
	t.Parallel()
	if ospath.Windows {
		t.Skip("改行を含むパスは Windows では作れない")
	}
	tr := ticketkit.New(t, ticketkit.Options{Phases: samples.Phases, Under: filepath.Join(base.TempDir(t), "status-\nline")})
	byHand(t, tr, researchParent("i0001", samples.Plan("research", "design")...))
	addOrigin(tr, filepath.Join(tr.Root, "nowhere.git"))
	if got := writeAndPushTarget(t, tr, "i0001"); got != "stop" {
		t.Fatalf("write-and-push の見分け = %q", got)
	}
	mine := block(t, said(t, tr, "i0001"), "i0001")
	ticketkit.Has(t, mine, "止まっている理由: write-and-push では状態の操作を実行できません: 親のワークツリーのパスに改行がある")
	ticketkit.Lacks(t, mine, "start へ進んでよい")
	ticketkit.Lacks(t, mine, "ccnavi-ticket.sh start")
}
