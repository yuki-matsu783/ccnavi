package core

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// closedFamily は Family のフェーズ 1 を閉じ、origin と、合流して push した親ブランチ（と結果ファイル）まで組んだ木。
func closedFamily(t *testing.T) (*ticketkit.Tree, ticketkit.Fixture) {
	t.Helper()
	return ticketkit.ClosedTree(t)
}

// requested は closedFamily のあとで依頼まで通した形。
func requested(t *testing.T) (*ticketkit.Tree, ticketkit.Fixture) {
	t.Helper()
	return ticketkit.RequestedTree(t)
}

// 依頼は子が親のブランチへ合流してから出せる。依頼のコメントの形（対象外の注記・固定の 1 文・目印のキー）と、
// 未解決のスレッドがある間は確認が通らない・解決すればゲートが開く。
func TestRequestNeedsMergedChildrenAndCheckOpensTheGate(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	// 子のツリーに成果を積んでから閉じる。合流していない子を見分けるため。
	for _, child := range []string{"i0001-01-01", "i0001-01-02"} {
		tree := tr.ChildTree(child)
		ticketkit.Write(t, filepath.Join(tree, "src", child[len(child)-1:], "work.py"), "x\n")
		tr.Git(tree, "add", "-A")
		tr.Git(tree, "commit", "--quiet", "-m", "work")
	}
	tr.ClosePhase()
	// 子を 1 本だけ合流した形で、前提の未充足を見る。
	fx := tr.Remote([]string{"i0001-01-01"})
	refused := ticketkit.Fails(t, tr.Request(fx))
	ticketkit.Has(t, refused.Stderr, "i0001-01-02")
	ticketkit.Has(t, refused.Stderr, "合流していない")
	tr.MergeAndPush("i0001-01-02")

	ticketkit.OK(t, tr.Request(fx))
	if !ticketkit.Exists(tr.RequestedMark()) {
		t.Error("依頼のマーカーが置かれない")
	}
	posted := fx.Comments()
	if len(posted) != 1 {
		t.Fatalf("コメントは 1 件のはず: %v", posted)
	}
	body := posted[0]["body"].(string)
	ticketkit.Has(t, body, "見てほしい点")
	const note = "wip/handoff/ 配下はユーザに渡すコピーで、レビューの対象外。integrate で消える。"
	if strings.Count(body, note) != 1 {
		t.Errorf("対象外の注記が 1 回でない:\n%s", body)
	}
	// 末尾は固定の 1 文（fix で直した指摘は、ユーザがホスト上で resolve する）。
	const resolve = "修正を確認したら、ユーザがホスト上で該当スレッドを resolve する。エージェントは fix を理由に resolve しない。"
	if !strings.HasSuffix(strings.TrimRight(body, "\n"), resolve) || strings.Count(body, resolve) != 1 {
		t.Errorf("末尾が固定の 1 文でない:\n%s", body)
	}
	if strings.Index(body, note) > strings.Index(body, resolve) {
		t.Error("注記が固定の 1 文より後ろにある")
	}
	// 依頼の目印のキーは、固定の 1 文を足す前の本文と親の先頭から作る（足す前に投稿した依頼を打ち直しても、目印が同じなので
	// 二重に投稿されない）。
	head := strings.TrimSpace(tr.Parent("rev-parse", "HEAD"))
	first, rest, _ := strings.Cut(body, "\n")
	before := strings.TrimRight(rest[:strings.Index(rest, resolve)], "\n") + "\n"
	sum := sha256.Sum256([]byte(before + "\n" + head))
	ticketkit.Has(t, first, "key="+hex.EncodeToString(sum[:])[:16]+" -->")
	// マージリクエストの下書きは作らない（MR が無ければ依頼は止まる）
	entries, _ := os.ReadDir(tr.State)
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "review-mr-") {
			t.Errorf("MR の下書きが作られた: %s", e.Name())
		}
	}
	ticketkit.Has(t, tr.Request(fx).Stderr, "依頼済み")

	// 未解決のスレッドがある間は開かない。
	fx.SetThreads(ticketkit.Thread{ID: "t1", URL: "u1", Path: "src/a/x.py", Line: 3, Body: "ここ"})
	ticketkit.Has(t, ticketkit.Fails(t, tr.Confirm(fx)).Stderr, "未解決")
	ticketkit.Has(t, tr.Spawn(tr.ParentTree, "次の子"), "DENY_PHASE_REVIEW")

	fx.SetThreads(ticketkit.Thread{ID: "t1", Resolved: true, URL: "u1", Path: "src/a/x.py", Line: 3, Body: "ここ"})
	ticketkit.OK(t, tr.Confirm(fx))
	ticketkit.Lacks(t, tr.Spawn(tr.ParentTree, "次の子"), "DENY_PHASE_REVIEW")
}

// レビュー待ちの子を done/ へ動かせなければ、レビュー済みのマーカーを置かない（done/ に同じ識別子が既に在る形。マーカーを先に
// 置くと、子は review/ に残ったまま「レビュー済み」になり、check も --reviewed も通らず取り出せなくなる）。動かしてから置くので、
// 直して打ち直せば通る。
func TestCheckDoesNotMarkReviewedWhenAChildCannotMove(t *testing.T) {
	t.Parallel()
	tr, fx := requested(t)
	stray := tr.ApprovedPath("done", "i0001-01-01")
	ticketkit.Write(t, stray, "stray\n")
	check := ticketkit.Fails(t, tr.Confirm(fx))
	ticketkit.Has(t, check.Stderr, "移動先に既にある")
	marker := tr.PhaseMark("1.reviewed")
	if ticketkit.Exists(marker) {
		t.Error("レビュー済みのマーカーが置かれた")
	}
	review := filepath.Join(tr.ParentTree, "wip", "proposals", "review", "i0001-01-01.md")
	if !ticketkit.Exists(review) {
		t.Error("review/ の子が消えた")
	}
	if got := ticketkit.Read(t, stray); got != "stray\n" {
		t.Errorf("done/ の既存のファイルが書き換わった: %q", got)
	}

	if err := os.Remove(stray); err != nil {
		t.Fatal(err)
	}
	ticketkit.OK(t, tr.Confirm(fx))
	if !ticketkit.Exists(marker) || ticketkit.Exists(review) || !ticketkit.Exists(stray) {
		t.Error("直して打ち直しても動かない")
	}
}

// ユーザは未解決の指摘を受け入れてレビューを済ませられるが、変更要求のレビューが残っていれば通せない。
func TestHumanAcceptsUnresolvedButNotChangesRequested(t *testing.T) {
	t.Parallel()
	tr, fx := requested(t)
	fx.SetThreads(ticketkit.Thread{ID: "t1", URL: "u1", Body: "ここ"})
	fx.SetReviews(ticketkit.Review{State: "CHANGES_REQUESTED", URL: "r1"})
	ticketkit.Has(t, ticketkit.Fails(t, tr.Reviewed(fx, "k\n")).Stderr, "変更要求")

	fx.SetReviews()
	accepted := ticketkit.OK(t, tr.Reviewed(fx, "k\n"))
	ticketkit.Has(t, accepted.Stdout, "u1")
	if !ticketkit.Exists(tr.PhaseMark("1.reviewed")) {
		t.Error("レビュー済みのマーカーが置かれない")
	}
}

// レビューが済むと review/ の子は done/ へ動く。指摘が残ればユーザが続きの子を起こせる。続きの子は
// .ccnavi/approved/doing/ に直に置かれ、承認はユーザが選んだことで済む。
func TestReviewMovesTheChildrenToDoneAndAcceptCanRaiseAFollowup(t *testing.T) {
	t.Parallel()
	tr, fx := closedFamily(t)
	review := filepath.Join(tr.ParentTree, "wip", "proposals", "review")
	for _, child := range []string{"i0001-01-01", "i0001-01-02"} {
		if !ticketkit.Exists(filepath.Join(review, child+".md")) {
			t.Errorf("%s が review/ に無い", child)
		}
	}
	ticketkit.OK(t, tr.Request(fx))
	fx.SetThreads(ticketkit.Thread{ID: "t1", URL: "u1", Path: "src/a/x.py", Body: "ここ"})
	// f: 続きの子を .ccnavi/approved/doing/ に直に起こす。見た子は done/ へ、マーカーは消える。
	chosen := ticketkit.OK(t, tr.Reviewed(fx, "f\n"))
	ticketkit.Has(t, chosen.Stdout, "i0001-01-03")
	text := ticketkit.Read(t, tr.ApprovedPath("doing", "i0001-01-03"))
	ticketkit.Has(t, text, "phase: 1")
	// 続きの子の目印はトップレベルの欄。承認の記録は書かない。
	ticketkit.Has(t, text, "followup_of:\n- i0001-01-01\n- i0001-01-02\n")
	ticketkit.Lacks(t, text, "ccnavi_approved:")
	for _, want := range []string{"u1", "src/a/*", "src/b/*"} {
		ticketkit.Has(t, text, want)
	}
	for _, child := range []string{"i0001-01-01", "i0001-01-02"} {
		if ticketkit.Exists(filepath.Join(review, child+".md")) || !ticketkit.Exists(tr.ApprovedPath("done", child)) {
			t.Errorf("%s が done/ へ動かない", child)
		}
	}
	for _, mark := range []string{"1.reviewed", "1.requested"} {
		if ticketkit.Exists(tr.PhaseMark(mark)) {
			t.Errorf("%s が残っている", mark)
		}
	}
	// フェーズは開き直り、止まっていたのが解ける。続きの子は普通に着手できる。
	ticketkit.Lacks(t, tr.Spawn(tr.ParentTree, "続き"), "DENY_PHASE_REVIEW")
	child := tr.Worktree("i0001-01-03", "i0001")
	ticketkit.OK(t, tr.Ccnavi("ticket", "start", "i0001-01-03"))
	// 続きの子の範囲は見た子の和。
	ticketkit.Lacks(t, tr.WriteReason(child, filepath.Join(child, "src", "b", "y.py")), "DENY_TICKET_SCOPE")
	ticketkit.Has(t, tr.WriteReason(child, filepath.Join(child, "docs", "y.md")), "DENY_TICKET_SCOPE")
}

// twoThreads はフェーズ 1 を依頼し、未解決の指摘を 2 件付けた結果を返す。
func twoThreads(t *testing.T) (*ticketkit.Tree, ticketkit.Fixture) {
	t.Helper()
	tr, fx := requested(t)
	fx.SetThreads(
		ticketkit.Thread{ID: "t1", URL: "u1", Path: "src/a/x.py", Body: "命名"},
		ticketkit.Thread{ID: "t2", URL: "u2", Path: "src/b/y.py", Body: "テスト"},
	)
	return tr, fx
}

func digestOf(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) string {
	t.Helper()
	return ticketkit.ParseJSON(t, tr.Decide(fx, "--preview", "--json").Stdout)["digest"].(string)
}

const (
	keepFix  = `{"u1": "keep", "u2": "fix"}`
	keepKeep = `{"u1": "keep", "u2": "keep"}`
)

// widenToTheLimit はレビュー済みの子の範囲を、上限（20 件）ちょうどまで足す。1 件の子と合わせると和が上限を超える。
func widenToTheLimit(t *testing.T, tr *ticketkit.Tree, child string) {
	t.Helper()
	path := filepath.Join(tr.ParentTree, "wip", "proposals", "review", child+".md")
	text := ticketkit.Read(t, path)
	ticketkit.Has(t, text, `glob: "src/b/*"`)
	var extra strings.Builder
	for n := 1; n < 20; n++ {
		extra.WriteString("\n  - match: Write|Edit\n    glob: \"src/c" + string(rune('0'+n/10)) + string(rune('0'+n%10)) + "/*\"")
	}
	ticketkit.Write(t, path, strings.Replace(text, `glob: "src/b/*"`, `glob: "src/b/*"`+extra.String(), 1))
}

// 指摘ごとの行き先。対応しない分は受け入れ、直す分は続きの子に載せ、フェーズは開き直る。
func TestDecideYesPlacesEachChoice(t *testing.T) {
	t.Parallel()
	tr, fx := twoThreads(t)
	done := ticketkit.OK(t, tr.Decide(fx, "--yes", keepFix, "--digest", digestOf(t, tr, fx), "--json"))
	answer := ticketkit.ParseJSON(t, done.Stdout)
	if answer["ok"] != true || answer["followup"] != "i0001-01-03" || answer["reviewed"] != false {
		t.Errorf("答え = %v", answer)
	}
	ticketkit.Has(t, answer["prompt"].(string), "i0001-01-03")
	// 受け入れの記録は持たない（ホスト側で返信を残して解決する）
	if ticketkit.Exists(tr.PhaseMark("accepted.json")) || ticketkit.Exists(tr.PhaseMark("1.reviewed")) {
		t.Error("受け入れの記録かレビュー済みのマーカーが置かれた")
	}
	text := ticketkit.Read(t, tr.ApprovedPath("doing", "i0001-01-03"))
	ticketkit.Has(t, text, "u2")
	ticketkit.Lacks(t, text, "u1")
	posted := ticketkit.Read(t, filepath.Join(tr.State, "review-decide-i0001-1.md"))
	if !strings.HasPrefix(posted, "<!-- ccnavi:decide -->") {
		t.Errorf("投稿の本文の頭: %q", posted)
	}
	ticketkit.Has(t, posted, "i0001-01-03")
}

// 見た子の範囲の和が上限を超えるとき、続きの子を書く前に止める（書いたあとに読めない子を残さない。受け入れ・マーカー・
// 続きの子のどれも置かない）。計画（--accept-plan）の段階でも同じ事前検査を適用し、通らなければ計画を書かない
// （計画を書けば sh がホストに返信と解決を書く。そのあとで「置けない」と分かると、解決済みのスレッドだけが残る）。
func TestDecideYesRefusesAFollowupWhoseScopeSumIsOverTheLimit(t *testing.T) {
	t.Parallel()
	for _, row := range []struct {
		name string
		plan bool
	}{{"置く呼び出し", false}, {"計画の呼び出し", true}} {
		t.Run(row.name, func(t *testing.T) {
			t.Parallel()
			tr, fx := twoThreads(t)
			digest := digestOf(t, tr, fx)
			// 子は 1 件と上限ちょうどの件数で、和だけが上限を超える形にする。
			widenToTheLimit(t, tr, "i0001-01-02")
			plan := filepath.Join(tr.State, "plan.json")
			args := []string{"--yes", keepFix, "--digest", digest, "--json"}
			if row.plan {
				args = append(args, "--accept-plan", plan)
			}
			refused := ticketkit.Fails(t, tr.Decide(fx, args...))
			ticketkit.Has(t, refused.Stderr, "続きの子チケットを起こせない")
			if !row.plan {
				ticketkit.Has(t, refused.Stderr, "複数の子に分ける")
			}
			if ticketkit.Exists(plan) || ticketkit.Exists(tr.ApprovedPath("doing", "i0001-01-03")) ||
				ticketkit.Exists(tr.PhaseMark("accepted.json")) || ticketkit.Exists(tr.PhaseMark("1.reviewed")) {
				t.Error("止める前に何かが置かれた")
			}
		})
	}
}

func TestDecideYesWithoutAFixMarksThePhaseReviewed(t *testing.T) {
	t.Parallel()
	tr, fx := twoThreads(t)
	// 前の回の下書き（投稿に失敗して残ったもの）は破棄する。残すと、この回に選んでいない issue を sh が投稿する
	stale := ticketkit.Write(t, filepath.Join(tr.State, "review-issue-i0001-1.md"), "前の回のタイトル\n\n前の回の本文\n")
	done := ticketkit.OK(t, tr.Decide(fx, "--yes", keepKeep, "--digest", digestOf(t, tr, fx), "--json"))
	if ticketkit.ParseJSON(t, done.Stdout)["reviewed"] != true {
		t.Error("reviewed でない")
	}
	if !ticketkit.Exists(tr.PhaseMark("1.reviewed")) || ticketkit.Exists(stale) {
		t.Error("マーカーが置かれないか、前の回の下書きが残った")
	}
	// レビュー済みのフェーズに confirm を重ねない。重ねるとマーカーと履歴が書き直される
	again := tr.Confirm(fx)
	if again.Code != 1 {
		t.Errorf("終了コード %d", again.Code)
	}
	ticketkit.Has(t, again.Stderr, "フェーズ 1 はレビュー済み")
	// マーカーを外して確かめ直す。受け入れた指摘は、ホストで解決済み（sh が返信と解決をした）なら数えない。
	// 記録は持たないので、開き直されれば未解決として数える
	if err := os.Remove(tr.PhaseMark("1.reviewed")); err != nil {
		t.Fatal(err)
	}
	ticketkit.Fails(t, tr.Confirm(fx))
	fx.SetThreads(
		ticketkit.Thread{ID: "t1", Resolved: true, URL: "u1", Path: "src/a/x.py", Body: "命名"},
		ticketkit.Thread{ID: "t2", Resolved: true, URL: "u2", Path: "src/b/y.py", Body: "テスト"},
	)
	ticketkit.OK(t, tr.Confirm(fx))
}

// 受け入れる指摘（対応しない）だけに返信の本文を付けた計画を書く。何も置かない。直す指摘は受け入れないので載せない。
// 実行ファイルはネットワークに出ないので、返信と解決は sh がこの計画でする。
func TestAcceptPlanWritesRepliesForTheAcceptedThreadsAndPlacesNothing(t *testing.T) {
	t.Parallel()
	tr, fx := requested(t)
	fx.SetThreads(
		ticketkit.Thread{ID: "t1", URL: "u1", Path: "src/a/x.py", Body: "命名"},
		ticketkit.Thread{ID: "t2", URL: "u2", Path: "src/b/y.py", Body: "テスト"},
		ticketkit.Thread{ID: "t3", URL: "u3", Path: "src/b/z.py", Body: "別件"},
	)
	digest := digestOf(t, tr, fx)
	choices := `{"u1": "keep", "u2": "fix", "u3": "keep"}`
	plan := filepath.Join(tr.State, "plan.json")
	ticketkit.OK(t, tr.Decide(fx, "--yes", choices, "--digest", digest, "--json", "--accept-plan", plan))
	threads := ticketkit.ReadJSON(t, plan)["threads"].([]any)
	var ids, keys []string
	for _, raw := range threads {
		thread := raw.(map[string]any)
		ids = append(ids, thread["id"].(string))
		keys = append(keys, thread["key"].(string))
		reply := thread["reply"].(string)
		if !strings.HasPrefix(reply, "<!-- ccnavi:accept -->") {
			t.Errorf("返信の頭: %q", reply)
		}
		ticketkit.Has(t, reply, "受け入れて進める")
		ticketkit.Has(t, reply, "フェーズ 1")
		ticketkit.Lacks(t, reply, "issue")
	}
	if strings.Join(ids, ",") != "t1,t3" || strings.Join(keys, ",") != "u1,u3" {
		t.Errorf("受け入れる指摘 = %v %v", ids, keys)
	}
	if ticketkit.Exists(tr.PhaseMark("1.reviewed")) || ticketkit.Exists(tr.ApprovedPath("doing", "i0001-01-03")) {
		t.Error("計画だけのはずが何かを置いた")
	}
	// 計画だけで置かない。同じ呼び出しを計画なしで打つと置く
	ticketkit.OK(t, tr.Decide(fx, "--yes", choices, "--digest", digest, "--json"))
}

// スレッドの id が無いと、ホストに返信も解決もできない。計画を書かずに止める。
func TestAcceptPlanStopsWhenAThreadCannotBeRepliedTo(t *testing.T) {
	t.Parallel()
	tr, fx := twoThreads(t)
	fx.SetThreads(
		ticketkit.Thread{ID: "", URL: "u1", Path: "src/a/x.py", Body: "命名"},
		ticketkit.Thread{ID: "t2", URL: "u2", Path: "src/b/y.py", Body: "テスト"},
	)
	plan := filepath.Join(tr.State, "plan.json")
	stopped := ticketkit.Fails(t, tr.Decide(fx, "--yes", keepKeep, "--digest", digestOf(t, tr, fx), "--json", "--accept-plan", plan))
	ticketkit.Has(t, stopped.Stderr, "スレッドの id が無いか読めず")
	if ticketkit.Exists(plan) {
		t.Error("計画が書かれた")
	}
}

// 置く呼び出しは、sh が返信と解決をして取り直した結果を、ホストの実状態で確かめる。受け入れるスレッドがホストで解決済みでなければ
// 置かない。見せたあとに増えた指摘は止めず、未対応のまま残す（reviewed は置かない）。
func TestAcceptDoneChecksTheHostStateAndTheShownThreads(t *testing.T) {
	t.Parallel()
	tr, fx := twoThreads(t)
	digest := digestOf(t, tr, fx)
	plan := filepath.Join(tr.State, "plan.json")
	ticketkit.OK(t, tr.Decide(fx, "--yes", keepKeep, "--digest", digest, "--json", "--accept-plan", plan))
	args := []string{"--yes", keepKeep, "--digest", digest, "--json", "--accept-done", plan}

	// sh がまだ解決していない（ホストは未解決のまま）
	early := ticketkit.Fails(t, tr.Decide(fx, args...))
	ticketkit.Has(t, early.Stderr, "ホストで解決済みになっていない")
	if ticketkit.Exists(tr.PhaseMark("1.reviewed")) {
		t.Error("置かれた")
	}

	// 解決したあとで、別の未解決が増えていた（見せていない指摘）
	resolved := []ticketkit.Thread{
		{ID: "t1", Resolved: true, URL: "u1", Path: "src/a/x.py", Body: "命名"},
		{ID: "t2", Resolved: true, URL: "u2", Path: "src/b/y.py", Body: "テスト"},
	}
	fx.SetThreads(append(resolved, ticketkit.Thread{ID: "t3", URL: "u3", Path: "src/c/z.py", Body: "新しい"})...)
	changed := ticketkit.OK(t, tr.Decide(fx, args...))
	answer := ticketkit.ParseJSON(t, changed.Stdout)
	if answer["reviewed"] != false {
		t.Errorf("reviewed = %v", answer["reviewed"])
	}
	if extra, _ := answer["pending_extra"].([]any); len(extra) != 1 || extra[0] != "u3" {
		t.Errorf("pending_extra = %v", answer["pending_extra"])
	}
	if ticketkit.Exists(tr.PhaseMark("1.reviewed")) {
		t.Error("置かれた")
	}

	// 解決済みで、ほかに増えていなければ置く。受け入れたキーがマーカーに残る
	fx.SetThreads(resolved...)
	done := ticketkit.OK(t, tr.Decide(fx, args...))
	if ticketkit.ParseJSON(t, done.Stdout)["reviewed"] != true {
		t.Error("reviewed でない")
	}
	accepted, _ := ticketkit.ReadJSON(t, tr.PhaseMark("1.reviewed"))["accepted"].([]any)
	if len(accepted) != 2 || accepted[0] != "u1" || accepted[1] != "u2" {
		t.Errorf("accepted = %v", accepted)
	}
}

// 見せたあとに指摘が変わっていたら、計画も置く呼び出しも止まる（ダイジェストが合わない）。ダイジェストが無い・選択が揃っていない・
// 知らない行き先・JSON でない選択も、何も置かずに断る。
func TestDecideRefusesWhatWasNotShown(t *testing.T) {
	t.Parallel()
	tr, fx := twoThreads(t)
	digest := digestOf(t, tr, fx)
	fx.Update(func(d map[string]any) {
		d["threads"].([]any)[1].(map[string]any)["body"] = "テスト（書き換えた）"
	})
	plan := filepath.Join(tr.State, "plan.json")
	for _, extra := range [][]string{{}, {"--accept-plan", plan}} {
		changed := ticketkit.Fails(t, tr.Decide(fx, append([]string{"--yes", keepKeep, "--digest", digest, "--json"}, extra...)...))
		if ticketkit.ParseJSON(t, changed.Stdout)["mismatch"] != true {
			t.Errorf("mismatch でない: %s", changed.Stdout)
		}
	}
	if ticketkit.Exists(plan) {
		t.Error("計画が書かれた")
	}
	digest = digestOf(t, tr, fx)
	for _, extra := range [][]string{
		{"--yes", keepKeep, "--json"},
		{"--yes", `{"u1": "keep"}`, "--digest", digest, "--json"},
		{"--yes", `{"u1": "keep", "u2": "later"}`, "--digest", digest, "--json"},
		{"--yes", "not json", "--digest", digest, "--json"},
	} {
		if r := tr.Decide(fx, extra...); r.Code == 0 {
			t.Errorf("断らない: %v", extra)
		}
	}
	if ticketkit.Exists(tr.PhaseMark("1.reviewed")) || ticketkit.Exists(tr.PhaseMark("accepted.json")) || ticketkit.Exists(tr.ApprovedPath("doing", "i0001-01-03")) {
		t.Error("何かが置かれた")
	}
}

// ターミナルでは 1 件ずつ選ぶ。知らない文字で何も置かずにやめ、issue に回せない局面では i も通らない。
func TestDecideInTheTerminalAsksOneByOne(t *testing.T) {
	t.Parallel()
	tr, fx := twoThreads(t)
	ticketkit.Fails(t, tr.Reviewed(fx, "k\nx\n"))
	if ticketkit.Exists(tr.PhaseMark("accepted.json")) {
		t.Error("置かれた")
	}
	ticketkit.Fails(t, tr.Reviewed(fx, "i\n"))
	done := ticketkit.OK(t, tr.Reviewed(fx, "f\nk\n"))
	ticketkit.Has(t, done.Stdout, "i0001-01-03")
	if ticketkit.Exists(tr.PhaseMark("accepted.json")) {
		t.Error("置かれた")
	}
}

// 依頼の後に HEAD が動いたら、確認は止まり、request で依頼を出し直せる（#36）。出し直しても、前の依頼への未解決の指摘は
// 数え続ける。レビュー済みになったあとは、HEAD が動いても出し直さない。
//
// 回帰: check が「request をやり直す」と案内する一方で、request が「依頼済み」で止まり、ユーザがマーカーを外すまで進めなかった。
func TestRequestAgainAfterHeadMoved(t *testing.T) {
	t.Parallel()
	tr, fx := requested(t)
	before := ticketkit.ReadJSON(t, tr.RequestedMark())["head"]
	// HEAD が依頼時のままなら、二重に投稿しない。
	ticketkit.Has(t, ticketkit.Fails(t, tr.Request(fx)).Stderr, "依頼済み")

	pushCode(t, tr, "later.py", "later")
	fx.SetThreads(ticketkit.Thread{ID: "t1", URL: "u1", Body: "直して"})
	moved := ticketkit.Fails(t, tr.Confirm(fx))
	ticketkit.Has(t, moved.Stderr, "HEAD が動いている")
	ticketkit.Has(t, moved.Stderr, "review-request --phase 1")

	redo := ticketkit.OK(t, tr.Request(fx))
	ticketkit.Has(t, redo.Stdout, "依頼し直した")
	if ticketkit.ReadJSON(t, tr.RequestedMark())["head"] == before {
		t.Error("依頼のマーカーの head が変わらない")
	}
	if n := len(fx.Comments()); n != 2 {
		t.Errorf("コメント %d 件（2 件のはず）", n)
	}
	// 前の依頼への指摘は、出し直しても数から消えない。
	ticketkit.Has(t, ticketkit.Fails(t, tr.Confirm(fx)).Stderr, "未解決")
	fx.SetThreads(ticketkit.Thread{ID: "t1", Resolved: true, URL: "u1", Body: "直して"})
	ticketkit.OK(t, tr.Confirm(fx))
	// レビュー済みになったあとは、HEAD が動いても出し直さない。
	pushCode(t, tr, "after.py", "after")
	ticketkit.Has(t, ticketkit.Fails(t, tr.Request(fx)).Stderr, "レビュー済み")
}

// pushCode は置き場の外（src/）にファイルを 1 つ足してコミットし、origin へ push する。
func pushCode(t *testing.T, tr *ticketkit.Tree, name, message string) {
	t.Helper()
	commitCode(t, tr, name, message, true)
}

func commitCode(t *testing.T, tr *ticketkit.Tree, name, message string, push bool) {
	t.Helper()
	ticketkit.Write(t, filepath.Join(tr.ParentTree, "src", name), "x\n")
	tr.Parent("add", "-A")
	tr.Parent("commit", "--quiet", "-m", message)
	if push {
		tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
	}
}

// close-early は依頼していないフェーズにもレビュー済みを置く。依頼の記録が無いことを先に見ていた版では、ユーザが早めに閉じた
// フェーズに request が通り、MR に依頼が投稿された。依頼せずにレビュー済みになったフェーズへは投稿しない。
func TestRequestRefusesAPhaseSettledWithoutRequest(t *testing.T) {
	t.Parallel()
	tr, fx := closedFamily(t)
	ticketkit.Write(t, tr.PhaseMark("1.reviewed"), `{"by": "close-early", "mr": 7, "accepted": []}`)
	ticketkit.Has(t, ticketkit.Fails(t, tr.Request(fx)).Stderr, "レビュー済み")
	if _, ok := fx.Data()["comments"]; ok {
		t.Error("依頼が投稿された")
	}
	if ticketkit.Exists(tr.RequestedMark()) {
		t.Error("依頼のマーカーが置かれた")
	}
}

// 合流済みかは merged_sha が親の HEAD の祖先かで確かめる。子のブランチは見ない。親に入っていない先頭を指す記録は断る。
func TestRequestChecksMergedShaAgainstTheParentNotTheChildBranch(t *testing.T) {
	t.Parallel()
	t.Run("子のブランチを消しても通る", func(t *testing.T) {
		t.Parallel()
		tr, fx := closedFamily(t)
		tr.Git(tr.Root, "worktree", "remove", "--force", filepath.Join(".claude", "worktrees", "i0001-01-01"))
		tr.Git(tr.Root, "branch", "-D", "i0001-01-01")
		ticketkit.OK(t, tr.Request(fx))
	})
	t.Run("親に入っていない先頭は断る", func(t *testing.T) {
		t.Parallel()
		tr, fx := closedFamily(t)
		// 親のブランチに入っていない先頭（子のブランチに積んだ、合流前のコミット）を指す記録。
		tree := tr.ChildTree("i0001-01-01")
		ticketkit.Write(t, filepath.Join(tree, "src", "late.py"), "x\n")
		tr.Git(tree, "add", "-A")
		tr.Git(tree, "commit", "--quiet", "-m", "late")
		late := strings.TrimSpace(tr.Git(tree, "rev-parse", "HEAD"))
		path := filepath.Join(tr.ParentTree, "wip", "proposals", "review", "i0001-01-01.md")
		if !ticketkit.Exists(path) {
			path = tr.ApprovedPath("done", "i0001-01-01")
		}
		ticketkit.Write(t, path, samples.SetFields(ticketkit.Read(t, path), samples.Field{Key: "merged_sha", Value: late}))
		refused := ticketkit.Fails(t, tr.Request(fx))
		ticketkit.Has(t, refused.Stderr, "i0001-01-01")
		ticketkit.Has(t, refused.Stderr, "親の HEAD に無い")
	})
}

// 指摘が残ったまま依頼をやり直しても、数から消えない。
//
// 回帰: 依頼より後のスレッドだけを数えていた版では、子をもう 1 本足して承認してもらい、依頼をやり直すだけで前回の指摘が
// 数から消え、check が通った（ユーザが解決も受け入れもしていないのに通る。実物の GitLab で出た）。
func TestUnresolvedThreadsSurviveANewRequest(t *testing.T) {
	t.Parallel()
	tr, fx := requested(t)
	// 依頼より前に付いた指摘。時刻で絞る版では数から外れていた。
	thread := ticketkit.Thread{ID: "t1", URL: "u1", Body: "直して", CreatedAt: "2020-01-01T00:00:00Z"}
	fx.SetThreads(thread)
	ticketkit.Has(t, ticketkit.Fails(t, tr.Confirm(fx)).Stderr, "未解決")

	// マーカーを消して依頼をやり直す（新しい子が承認された形）。指摘は残ったまま。
	if err := os.Remove(tr.RequestedMark()); err != nil {
		t.Fatal(err)
	}
	ticketkit.OK(t, tr.Request(fx))
	ticketkit.Has(t, ticketkit.Fails(t, tr.Confirm(fx)).Stderr, "未解決")

	// ユーザが受け入れれば通る。受け入れた分は、sh がホストで返信を残して解決済みにする。記録は持たないので、
	// 解決済みになったことで次から数えない（受け入れでレビュー済みになったので、マーカーを外して確かめ直す）。
	ticketkit.OK(t, tr.Reviewed(fx, "k\n"))
	if err := os.Remove(tr.PhaseMark("1.reviewed")); err != nil {
		t.Fatal(err)
	}
	ticketkit.Fails(t, tr.Confirm(fx))
	thread.Resolved = true
	fx.SetThreads(thread)
	ticketkit.OK(t, tr.Confirm(fx))
}

// ユーザが受け入れたスレッドは、ホストで解決済みの間だけ数えない。受け入れの記録（accepted.json）は持たない。受け入れた
// スレッドを誰かが開き直せば、未解決として数える（意図どおり）。
func TestTheAcceptanceIsTheResolvedStateOnTheHost(t *testing.T) {
	t.Parallel()
	tr, fx := requested(t)
	thread := ticketkit.Thread{ID: "t1", URL: "u1", Body: "承知で進める"}
	fx.SetThreads(thread)
	ticketkit.OK(t, tr.Reviewed(fx, "k\n"))
	if ticketkit.Exists(tr.PhaseMark("accepted.json")) {
		t.Error("受け入れの記録が置かれた")
	}
	// sh が返信を残して解決済みにした形。マーカーを外して確かめ直すと通る
	reviewed := tr.PhaseMark("1.reviewed")
	if err := os.Remove(reviewed); err != nil {
		t.Fatal(err)
	}
	thread.Resolved = true
	fx.SetThreads(thread)
	ticketkit.OK(t, tr.Confirm(fx))
	// 誰かが開き直せば、未解決として数える
	thread.Resolved = false
	fx.SetThreads(thread)
	if err := os.Remove(reviewed); err != nil {
		t.Fatal(err)
	}
	ticketkit.Fails(t, tr.Confirm(fx))
}

// prepare --check と decide --check は、本体と同じ前提で断り、何も書かない（次の一手を 1 つだけ案内する）。
func TestCheckRefusesLikeTheRealOperationAndWritesNothing(t *testing.T) {
	t.Parallel()
	t.Run("prepare --check はフェーズが終わっていなければ断る", func(t *testing.T) {
		t.Parallel()
		tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
		body := ticketkit.Write(t, filepath.Join(tr.Root, "body.md"), "見てほしい点\n")
		args := append(tr.PhaseArgs("1"), "--body-file", body)
		before := listDir(tr.State)
		checked := tr.Ccnavi(append([]string{"--check"}, append(args, "review", "prepare")...)...)
		real := tr.Ccnavi(append(args, "review", "prepare")...)
		if checked.Code != 1 || real.Code != 1 || checked.Stderr != real.Stderr {
			t.Errorf("--check と本体の答えが違う:\n%+v\n%+v", checked, real)
		}
		ticketkit.Has(t, checked.Stderr, "フェーズが終わっていない")
		if n := countLines(checked.Stderr, "次:"); n != 1 {
			t.Errorf("次: の行が %d 本", n)
		}
		if after := listDir(tr.State); strings.Join(before, ",") != strings.Join(after, ",") {
			t.Errorf("状態の置き場が変わった: %v → %v", before, after)
		}
	})
	t.Run("decide --check は依頼の記録が無ければ断る", func(t *testing.T) {
		t.Parallel()
		tr, fx := closedFamily(t)
		base := []string{"--cwd", tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved"}
		head := tr.Parent("rev-parse", "HEAD")
		checked := tr.Ccnavi(append(base, "--check")...)
		shown := tr.Ccnavi(append(base, "--preview", "--json", "--result", fx.Path)...)
		if checked.Code != 1 || shown.Code != 1 || checked.Stderr != shown.Stderr {
			t.Errorf("--check と --preview の答えが違う:\n%+v\n%+v", checked, shown)
		}
		ticketkit.Has(t, checked.Stderr, "依頼の記録がありません")
		if n := countLines(checked.Stderr, "次:"); n != 1 {
			t.Errorf("次: の行が %d 本", n)
		}
		if tr.Parent("rev-parse", "HEAD") != head || tr.Parent("status", "--porcelain") != "" {
			t.Error("親のツリーが変わった")
		}
	})
}

func listDir(dir string) []string {
	entries, _ := os.ReadDir(dir)
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	return names
}

func countLines(s, needle string) int {
	n := 0
	for _, line := range strings.Split(s, "\n") {
		if strings.Contains(line, needle) {
			n++
		}
	}
	return n
}

// 同じ作者のレビューは最後の 1 件だけが数える（変更要求のあとに承認・取り下げがあれば通る）。
func TestOnlyTheLatestReviewPerAuthorCounts(t *testing.T) {
	t.Parallel()
	tr, fx := requested(t)
	fx.SetReviews(
		ticketkit.Review{State: "CHANGES_REQUESTED", Author: "a", SubmittedAt: "2026-01-01T00:00:00Z"},
		ticketkit.Review{State: "APPROVED", Author: "a", SubmittedAt: "2026-01-02T00:00:00Z"},
		ticketkit.Review{State: "CHANGES_REQUESTED", Author: "b", SubmittedAt: "2026-01-01T00:00:00Z"},
		ticketkit.Review{State: "DISMISSED", Author: "b", SubmittedAt: "2026-01-03T00:00:00Z"},
	)
	ticketkit.OK(t, tr.Confirm(fx))
}

// 実行ファイルはリモートに出ない。結果ファイル（--result）が無ければ確認は動かず、投稿の url が無い結果ではマーカーを置かず、
// 依頼したのと違うマージリクエストの結果では開かない。
func TestExeNeverReachesTheRemoteAndNeedsAMatchingResult(t *testing.T) {
	t.Parallel()
	tr, fx := closedFamily(t)
	ticketkit.Has(t, ticketkit.Fails(t, tr.Ccnavi(append(tr.PhaseArgs("1"), "review", "confirm")...)).Stderr, "--result")
	// 投稿の url が無い結果ではマーカーを置かない。
	body := ticketkit.Write(t, filepath.Join(tr.Root, "body.md"), "x\n")
	prepared := ticketkit.OK(t, tr.Ccnavi(append(tr.PhaseArgs("1"), "--body-file", body, "review", "prepare")...))
	for _, line := range ticketkit.Lines(prepared.Stdout) {
		if !ticketkit.Exists(line) {
			t.Errorf("prepare が出したパスが無い: %s", line)
		}
	}
	unposted := ticketkit.Fails(t, tr.Ccnavi(append(tr.PhaseArgs("1"), "review", "requested", "--result", fx.Path)...))
	ticketkit.Has(t, unposted.Stderr, "url")
	ticketkit.OK(t, tr.Request(fx))
	other := ticketkit.Write(t, filepath.Join(tr.Root, "other.json"), `{"host": "fixture", "mr": {"number": 8, "url": "https://example/mr/8"}}`)
	check := ticketkit.Fails(t, tr.Ccnavi(append(tr.PhaseArgs("1"), "review", "confirm", "--result", other)...))
	ticketkit.Has(t, check.Stderr, "違う")
}
