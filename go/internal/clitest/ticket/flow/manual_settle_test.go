package flow

// 手で動かした子の判定と、confirm による版つきの確定、受け入れ台帳。
// ユーザが子を review/ から done/ へ手で動かす（settled_by を持たない）と、親が confirm を打ったときだけ、
// 未解決の指摘を解決せずに台帳へ受け入れてレビュー済みにする。元: tests/ticket/test_manual_settle.py。
//
// 判定の細部（状態ごとの答え・台帳の読み方）は go の phase・host・marks のテストが見る。ここは status と confirm の出力で見る。
import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
)

const (
	// 移動の前のこの秒数の間に付いた指摘は、移動の後に付いたものとして扱う（時計のずれの余裕）。
	marginSeconds = 600
	oldStamp      = "2026-01-01T00:00:00Z"
	futureStamp   = "2999-01-01T00:00:00Z"
)

func partialLine(moved, waiting string) string {
	return "子 " + moved + " はレビュー完了、フェーズは未完了（まだ review/・doing/ に残る子: " + waiting + "）"
}

// thread は指摘 1 件（extra で欄を上書きする）。
func thread(extra map[string]any) map[string]any {
	t := map[string]any{"id": "t1", "resolved": false, "url": "https://h/c/1", "body": "ここを直して", "created_at": oldStamp, "last_id": "c1"}
	for k, v := range extra {
		t[k] = v
	}
	return t
}

func (tr *Tree) markPath(number int, kind string) string {
	return filepath.Join(tr.Approved, "phases", ParentName, strconv.Itoa(number)+"."+kind)
}

// mark はフェーズのマーカー。無ければ nil。
func (tr *Tree) markOf(number int, kind string) map[string]any {
	tr.T.Helper()
	if !Exists(tr.markPath(number, kind)) {
		return nil
	}
	return ReadJSON(tr.T, tr.markPath(number, kind))
}

func (tr *Tree) noReviewedMark(number int) {
	tr.T.Helper()
	if m := tr.markOf(number, "reviewed"); m != nil {
		tr.T.Errorf("フェーズ %d がレビュー済みになった: %v", number, m)
	}
}

// ledger は受け入れ台帳（phases/<親>/accepted.json）の項目。無ければ空。
func (tr *Tree) ledger() []any {
	tr.T.Helper()
	path := filepath.Join(tr.Approved, "phases", ParentName, "accepted.json")
	if !Exists(path) {
		return nil
	}
	return jl(ReadJSON(tr.T, path), "entries")
}

func (tr *Tree) status1() string {
	tr.T.Helper()
	return OK(tr.T, tr.Ccnavi("ticket", "status", ParentName)).Stdout
}

func (tr *Tree) share() {
	tr.T.Helper()
	tr.CommitParent("move by hand")
	tr.Parent("push", "--quiet", "origin", ParentName)
}

// childPath は子 child の置き場所（where は review・doing・done）。
func (tr *Tree) childPath(child, where string) string {
	if where == "review" {
		return filepath.Join(tr.ParentTree, "wip", "proposals", "review", child+".md")
	}
	return tr.ApprovedPath(where, child)
}

// hand は子を review/ から done/ へ手で動かす（settled_by は書かない）。
func (tr *Tree) hand(child string) {
	tr.T.Helper()
	done := tr.ApprovedPath("done", child)
	if err := os.MkdirAll(filepath.Dir(done), 0o755); err != nil {
		tr.T.Fatal(err)
	}
	if err := os.Rename(tr.childPath(child, "review"), done); err != nil {
		tr.T.Fatal(err)
	}
	Lacks(tr.T, Read(tr.T, done), "settled_by")
}

// work は子を 1 つ、承認から親への合流まで進める。
func (tr *Tree) work(name string, number int, glob, path string) {
	tr.T.Helper()
	tr.ApproveChild(name, number, glob)
	tr.RunChild(name, File{path, "d\n"})
	OK(tr.T, tr.CloseChild(name))
	tr.CommitParent("close " + name)
	tr.Merge(name)
}

// pushRequest は依頼を通して、親がコミットして push する。
func (tr *Tree) pushRequest(f Fixture, phase string) {
	tr.T.Helper()
	OK(tr.T, tr.RequestFor(f, phase))
	tr.CommitParent("request")
	tr.Parent("push", "--quiet", "origin", ParentName)
}

func (f Fixture) with(threads ...map[string]any) Fixture {
	f.Update(func(d map[string]any) {
		list := make([]any, len(threads))
		for i, th := range threads {
			list[i] = th
		}
		d["threads"] = list
	})
	return f
}

// resultFile は取得結果の見本（result.json）を書く。
func (tr *Tree) resultFile(threads []map[string]any, reviews ...map[string]any) Fixture {
	tr.T.Helper()
	if threads == nil {
		threads = []map[string]any{}
	}
	raw, err := json.Marshal(map[string]any{"host": "fixture", "mr": map[string]any{"number": 7, "url": "u/7", "draft": true}, "threads": threads, "reviews": reviews})
	if err != nil {
		tr.T.Fatal(err)
	}
	return Fixture{T: tr.T, Path: Write(tr.T, filepath.Join(tr.Root, "result.json"), string(raw))}
}

func (tr *Tree) confirmWith(f Fixture, phase int) run.Result {
	return tr.ConfirmFor(f, strconv.Itoa(phase))
}

func TestAMoveIsSettledByConfirmOnlyWhenSharedAndWithoutLaterThreads(t *testing.T) {
	t.Parallel()
	tr, _ := requestedTree(t)
	tr.hand("i0001-01-01")
	// 動かしただけ（未コミット）は確定しない。
	Has(t, tr.status1(), "移動が未共有")
	Fails(t, tr.confirmWith(tr.resultFile([]map[string]any{thread(nil)}), 1))
	tr.share()
	Has(t, tr.status1(), "レビュー完了確認待ち")
	// 移動の後に付いた指摘・移動の後の返信・時刻を読めない指摘は受け入れない。
	for _, c := range []struct {
		label  string
		thread map[string]any
		word   string
	}{
		{"移動の後の指摘", thread(map[string]any{"created_at": futureStamp}), "移動の後に付いた指摘"},
		{"移動の後の返信", thread(map[string]any{"last_at": futureStamp, "last_id": "c9"}), "移動の後に付いた指摘"},
		{"時刻を読めない指摘", thread(map[string]any{"created_at": ""}), "時刻を読めない指摘"},
	} {
		refused := Fails(t, tr.confirmWith(tr.resultFile([]map[string]any{c.thread}), 1))
		Has(t, refused.Stderr, c.word)
		tr.noReviewedMark(1)
	}
	// 変更要求があれば保留する。
	review := map[string]any{"state": "CHANGES_REQUESTED", "url": "u", "submitted_at": oldStamp, "author": "r"}
	Has(t, Fails(t, tr.confirmWith(tr.resultFile(nil, review), 1)).Stderr, "変更要求")
	// 受け入れられる指摘なら、版つきの台帳に残してレビュー済みにする。
	OK(t, tr.confirmWith(tr.resultFile([]map[string]any{thread(nil)}), 1))
	mark := tr.markOf(1, "reviewed")
	if js(mark, "via") != "manual" || !slices.Equal(strs(jl(mark, "moved")), []string{"i0001-01-01"}) {
		t.Errorf("mark = %v", mark)
	}
	e := tr.ledger()[0]
	if js(e, "key") != "https://h/c/1" || js(e, "accepted_by") != "manual" || js(e, "last_comment_id") != "c1" || js(e, "version") == "" {
		t.Errorf("ledger entry = %v", e)
	}
}

func TestStopNudgesToConfirmOnceWhenTheMoveIsShared(t *testing.T) {
	t.Parallel()
	tr, _ := requestedTree(t)
	tr.hand("i0001-01-01")
	tr.share()
	first := tr.Hook("Stop", "", tr.ParentTree, nil)
	Has(t, first.Stdout, "NUDGE_TICKET_REQUEST")
	Has(t, first.Stdout, "confirm --phase 1")
	Lacks(t, tr.Hook("Stop", "", tr.ParentTree, nil).Stdout, "NUDGE_TICKET_REQUEST")
}

func TestTheNextPromptSaysToConfirmOnceAndStopDoesNotRepeatIt(t *testing.T) {
	t.Parallel()
	tr, _ := requestedTree(t)
	tr.hand("i0001-01-01")
	tr.share()
	raw, _ := json.Marshal(map[string]any{"hook_event_name": "UserPromptSubmit", "cwd": tr.ParentTree, "session_id": "s1", "prompt": "続けて"})
	first := tr.Call(Call{Stdin: string(raw)}, "--mode", "enable")
	Has(t, first.Stdout, "confirm --phase 1")
	Lacks(t, tr.Call(Call{Stdin: string(raw)}, "--mode", "enable").Stdout, "confirm --phase 1")
	Lacks(t, tr.Hook("Stop", "", tr.ParentTree, nil).Stdout, "confirm --phase 1")
}

// ---- 手で動かした子の取り違え（AT7・AT9・AT18・AT33〜AT38）。「手動とみなさない」「確定しない」を固定する。

// twoChildren はフェーズ 1 に子を 2 つ持たせて依頼まで進めた木。
func twoChildren(t *testing.T) (*Tree, Fixture) {
	t.Helper()
	tr := cachedTree(t, "manual-two-children", func(tr *Tree) {
		tr.FamilyPlan(planOf("design"), nil)
		tr.work("i0001-01-01", 1, "wip/design/*", "wip/design/a.md")
		tr.work("i0001-01-02", 1, "wip/design/*", "wip/design/b.md")
		tr.pushRequest(tr.Origin(), "1")
	})
	return tr, tr.Fixture()
}

func (tr *Tree) assertReady(children ...string) {
	tr.T.Helper()
	said := tr.status1()
	Has(tr.T, said, "レビュー完了確認待ち: 子 ")
	for _, c := range children {
		Has(tr.T, said, c)
	}
}

func TestMovingOnlyOneOfTwoChildrenIsPartialAndNotSettled(t *testing.T) {
	t.Parallel()
	tr, f := twoChildren(t)
	tr.hand("i0001-01-01")
	tr.share()
	said := tr.status1()
	Has(t, said, partialLine("i0001-01-01", "i0001-01-02"))
	Lacks(t, said, "レビュー完了確認待ち")
	refused := Fails(t, tr.confirmWith(f.with(thread(nil)), 1))
	Has(t, refused.Stdout, "フェーズは未完了")
	Has(t, refused.Stderr, "未解決のスレッド")
	tr.noReviewedMark(1)
	if len(tr.ledger()) != 0 || !Exists(tr.childPath("i0001-01-02", "review")) {
		t.Error("台帳が書かれたか、残りの子が動いた")
	}
}

// 未解決が無ければ通常の confirm（手動の確定ではない）で、残りの子を settled_by: confirm で動かしてレビュー済みにする。
func TestAPartialMoveWithNoThreadsGoesThroughTheOrdinaryConfirm(t *testing.T) {
	t.Parallel()
	tr, f := twoChildren(t)
	tr.hand("i0001-01-01")
	tr.share()
	OK(t, tr.confirmWith(f.with(), 1))
	mark := tr.markOf(1, "reviewed")
	if _, ok := mark["via"]; ok {
		t.Errorf("手動の確定になった: %v", mark)
	}
	if _, ok := mark["moved"]; ok {
		t.Errorf("moved がある: %v", mark)
	}
	Has(t, Read(t, tr.childPath("i0001-01-02", "done")), `settled_by: "confirm"`)
}

func TestBothChildrenMovedByHandAreReady(t *testing.T) {
	t.Parallel()
	tr, _ := twoChildren(t)
	tr.hand("i0001-01-01")
	tr.hand("i0001-01-02")
	tr.share()
	tr.assertReady("i0001-01-01", "i0001-01-02")
}

// deferredFamily はフェーズ 1（設計、延期）とフェーズ 2（受入テスト、引き受ける）。依頼はフェーズ 2。
func deferredFamily(t *testing.T) (*Tree, Fixture) {
	t.Helper()
	tr := cachedTree(t, "manual-deferred", func(tr *Tree) {
		tr.FamilyPlan([]samples.Step{samples.DeferStep("design"), {Type: "acceptance"}}, nil)
		tr.work("i0001-01-01", 1, "wip/design/*", "wip/design/a.md")
		tr.work("i0001-02-01", 2, "tests/*", "tests/a.txt")
		tr.pushRequest(tr.Origin(), "2")
	})
	return tr, tr.Fixture()
}

func TestDeferredPhases(t *testing.T) {
	t.Run("moving only the accepting phase's child is partial while the deferred one waits", func(t *testing.T) {
		t.Parallel()
		tr, f := deferredFamily(t)
		// 延期したフェーズの子も review/ に置かれ、引き受ける側のレビューで一緒に見る。
		if !Exists(tr.childPath("i0001-01-01", "review")) {
			t.Error("延期した子が review/ に無い")
		}
		tr.hand("i0001-02-01")
		tr.share()
		Has(t, tr.status1(), partialLine("i0001-02-01", "i0001-01-01"))
		Fails(t, tr.confirmWith(f.with(thread(nil)), 2))
		tr.noReviewedMark(2)
	})
	t.Run("moving only the deferred phase's child is partial too", func(t *testing.T) {
		t.Parallel()
		tr, _ := deferredFamily(t)
		tr.hand("i0001-01-01")
		tr.share()
		Has(t, tr.status1(), partialLine("i0001-01-01", "i0001-02-01"))
	})
	t.Run("moving both is ready", func(t *testing.T) {
		t.Parallel()
		tr, f := deferredFamily(t)
		tr.hand("i0001-01-01")
		tr.hand("i0001-02-01")
		tr.share()
		tr.assertReady("i0001-01-01", "i0001-02-01")
		OK(t, tr.confirmWith(f.with(thread(nil)), 2))
		mark := tr.markOf(2, "reviewed")
		moved := strs(jl(mark, "moved"))
		slices.Sort(moved)
		if js(mark, "via") != "manual" || !slices.Equal(moved, []string{"i0001-01-01", "i0001-02-01"}) {
			t.Errorf("mark = %v", mark)
		}
	})
}

// AT9: finished_to が無い子は経路が分からず、手動とみなさない。
func TestAChildWithoutFinishedToIsRouteUnknownAndNotManual(t *testing.T) {
	t.Parallel()
	tr, f := requestedTree(t)
	path := tr.childPath("i0001-01-01", "review")
	var kept []string
	for _, l := range strings.SplitAfter(Read(t, path), "\n") {
		if !strings.HasPrefix(l, "finished_to:") {
			kept = append(kept, l)
		}
	}
	Write(t, path, strings.Join(kept, ""))
	tr.hand("i0001-01-01")
	tr.share()
	Has(t, tr.status1(), "経路が分からない")
	refused := Fails(t, tr.confirmWith(f.with(thread(nil)), 1))
	Has(t, refused.Stdout, "経路が分からない")
	tr.noReviewedMark(1)
	if len(tr.ledger()) != 0 {
		t.Error("台帳が書かれた")
	}
}

func TestAMoveWithoutARequestWaitsForTheRequest(t *testing.T) {
	t.Parallel()
	tr := cachedTree(t, "manual-no-request", func(tr *Tree) {
		tr.FamilyPlan(planOf("design"), nil)
		tr.work("i0001-01-01", 1, "wip/design/*", "wip/design/a.md")
		tr.Origin()
	})
	f := tr.Fixture()
	tr.hand("i0001-01-01")
	tr.share()
	said := tr.status1()
	Has(t, said, "依頼の記録が無い")
	Has(t, said, "review-request")
	Has(t, Fails(t, tr.confirmWith(f.with(thread(nil)), 1)).Stderr, "依頼の記録がありません")
	tr.noReviewedMark(1)
}

// AT34: confirm・close-early で settled_by を書いてマーカーの前に止まった子は手動ではない。
func TestAChildSettledByConfirmOrCloseEarlyAndStoppedBeforeTheMarkIsNotManual(t *testing.T) {
	for _, by := range []string{"confirm", "close-early"} {
		t.Run(by, func(t *testing.T) {
			t.Parallel()
			tr, f := requestedTree(t)
			tr.hand("i0001-01-01")
			done := tr.childPath("i0001-01-01", "done")
			Write(t, done, strings.Replace(Read(t, done), "\n---\n", "\nsettled_by: \""+by+"\"\n---\n", 1))
			tr.share()
			tr.noReviewedMark(1)
			Lacks(t, tr.status1(), "レビュー完了確認待ち")
			Has(t, Fails(t, tr.confirmWith(f.with(thread(nil)), 1)).Stderr, "未解決のスレッド")
			tr.noReviewedMark(1)
			// 未解決が無ければ、通常の confirm でマーカーを置いて終わる（手動の確定ではない）。
			OK(t, tr.confirmWith(f.with(), 1))
			if _, ok := tr.markOf(1, "reviewed")["via"]; ok {
				t.Error("手動の確定になった")
			}
		})
	}
}

// AT33: shownThreads を見せて適用する。適用前に keep は解決済みになり、t1 が増える。
func (tr *Tree) decideWithExtra(f Fixture, choices map[string]string, shown []map[string]any) map[string]any {
	tr.T.Helper()
	f.with(shown...)
	base := []string{"--cwd", tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved"}
	r := func(extra ...string) run.Result { return tr.Ccnavi(append(append([]string{}, base...), extra...)...) }
	digest := js(ParseJSON(tr.T, OK(tr.T, r("--preview", "--json", "--result", f.Path)).Stdout), "digest")
	picked, _ := json.Marshal(choices)
	plan := filepath.Join(tr.State, "accept-plan.json")
	OK(tr.T, r("--yes", string(picked), "--digest", digest, "--json", "--result", f.Path, "--accept-plan", plan))
	var after []map[string]any
	for _, th := range shown {
		cp := map[string]any{}
		for k, v := range th {
			cp[k] = v
		}
		cp["resolved"] = choices[th["url"].(string)] == "keep"
		after = append(after, cp)
	}
	after = append(after, map[string]any{"id": "t1", "resolved": false, "url": "u/7#t1", "body": "追加", "last_id": "c1"})
	f.with(after...)
	done := OK(tr.T, r("--yes", string(picked), "--digest", digest, "--json", "--result", f.Path, "--accept-done", plan))
	return ParseJSON(tr.T, done.Stdout)
}

func TestAfterADecideThatKeptOneThreadConfirmCountsTheExtraOne(t *testing.T) {
	t.Parallel()
	tr, f := requestedTree(t)
	t0 := map[string]any{"id": "t0", "resolved": false, "url": "u/7#t0", "body": "直して", "last_id": "c0"}
	answer := tr.decideWithExtra(f, map[string]string{"u/7#t0": "keep"}, []map[string]any{t0})
	if jb(answer, "reviewed") || !slices.Equal(strs(jl(answer, "pending_extra")), []string{"u/7#t1"}) {
		t.Errorf("answer = %v", answer)
	}
	if !Exists(tr.childPath("i0001-01-01", "review")) {
		t.Error("子が review/ から動いた")
	}
	tr.noReviewedMark(1)
	tr.share()
	Lacks(t, tr.status1(), "手で done/")
	refused := Fails(t, tr.ConfirmFor(f, "1"))
	Has(t, refused.Stderr, "未解決のスレッドが 1 件")
	Has(t, refused.Stderr, "u/7#t1")
	Lacks(t, refused.Stderr, "u/7#t0")
	Lacks(t, refused.Stdout, "レビュー完了確認待ち")
	tr.noReviewedMark(1)
}

// fix を選ぶと子は settled_by: decide で done/ へ動く。マーカーは置かれず、confirm は手動移動に入らずに止まる。
func TestAfterADecideThatMovedTheChildConfirmIsNotManual(t *testing.T) {
	t.Parallel()
	tr, f := requestedTree(t)
	t0 := map[string]any{"id": "t0", "resolved": false, "url": "u/7#t0", "body": "直して", "last_id": "c0"}
	t2 := map[string]any{"id": "t2", "resolved": false, "url": "u/7#t2", "body": "直す", "last_id": "c2"}
	answer := tr.decideWithExtra(f, map[string]string{"u/7#t0": "keep", "u/7#t2": "fix"}, []map[string]any{t0, t2})
	if js(answer, "followup") == "" {
		t.Errorf("answer = %v", answer)
	}
	Has(t, Read(t, tr.childPath("i0001-01-01", "done")), `settled_by: "decide"`)
	tr.noReviewedMark(1)
	tr.share()
	Lacks(t, tr.status1(), "手で done/")
	refused := Fails(t, tr.ConfirmFor(f, "1"))
	Lacks(t, refused.Stdout, "レビュー完了確認待ち")
	// fix でフェーズが開き直り、依頼のマーカーも消える。続きの子を終えて依頼し直すまで通らない。
	Has(t, refused.Stderr, "依頼の記録がありません")
	tr.noReviewedMark(1)
}

// AT36: 移動の前の余裕の中の指摘は移動の後のものとして扱い、それより古い指摘は受け入れる。
func TestAThreadNearTheMoveIsJudgedByTheMargin(t *testing.T) {
	for _, c := range []struct {
		name     string
		offset   int
		accepted bool
	}{{"inside the margin", -marginSeconds / 2, false}, {"older than the margin", -marginSeconds - 60, true}} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr, f := requestedTree(t)
			tr.hand("i0001-01-01")
			tr.share()
			rel := filepath.Join(".ccnavi", "approved", "done", "i0001-01-01.md")
			epoch, err := strconv.ParseInt(strings.TrimSpace(tr.Parent("log", "--diff-filter=A", "--format=%ct", "-1", "--", rel)), 10, 64)
			if err != nil {
				t.Fatal(err)
			}
			at := time.Unix(epoch+int64(c.offset), 0).UTC().Format("2006-01-02T15:04:05Z")
			r := tr.confirmWith(f.with(thread(map[string]any{"created_at": at})), 1)
			if c.accepted {
				OK(t, r)
				return
			}
			Has(t, Fails(t, r).Stderr, "移動の後に付いた指摘")
			tr.noReviewedMark(1)
			if len(tr.ledger()) != 0 {
				t.Error("台帳が書かれた")
			}
		})
	}
}

// AT38: 指摘が無くても、未共有の移動は通常の confirm に回さず止める。コミットしても push するまでは未共有。
func TestAnUnsharedMoveIsNotConfirmedEvenWithNoThreads(t *testing.T) {
	t.Parallel()
	tr, f := requestedTree(t)
	tr.hand("i0001-01-01")
	tr.CommitParent("move by hand")
	Has(t, tr.status1(), "移動が未共有")
	Has(t, Fails(t, tr.confirmWith(f.with(), 1)).Stderr, "[CORE_CONFIRM_UNSHARED]")
	tr.noReviewedMark(1)
	// 指摘があっても同じ。push すれば確定してよい形になる。
	Has(t, Fails(t, tr.confirmWith(f.with(thread(nil)), 1)).Stderr, "移動が未共有")
	if len(tr.ledger()) != 0 {
		t.Error("台帳が書かれた")
	}
	tr.Parent("push", "--quiet", "origin", ParentName)
	tr.assertReady("i0001-01-01")
}

// twoPhases はフェーズ 1 を手動移動で確定し、フェーズ 2 の依頼まで進めた木と取得結果の見本。
func twoPhases(t *testing.T) (*Tree, Fixture) {
	t.Helper()
	tr := cachedTree(t, "manual-two-phases", func(tr *Tree) {
		tr.FamilyPlan(planOf("design", "acceptance"), nil)
		tr.work("i0001-01-01", 1, "wip/design/*", "wip/design/a.md")
		f := tr.Origin()
		tr.pushRequest(f, "1")
		tr.hand("i0001-01-01")
		tr.share()
		f.with(thread(nil))
		OK(tr.T, tr.ConfirmFor(f, "1"))
		tr.CommitParent("reviewed 1")
		tr.work("i0001-02-01", 2, "tests/*", "tests/a.txt")
		tr.Parent("push", "--quiet", "origin", ParentName)
		OK(tr.T, tr.RequestFor(f, "2"))
	})
	return tr, tr.Fixture()
}

func TestAThreadAcceptedInPhase1IsNotCountedByPhase2EvenAfterReopening(t *testing.T) {
	t.Parallel()
	tr, f := twoPhases(t)
	entries := tr.ledger()
	if len(entries) != 1 || js(entries[0], "key") != "https://h/c/1" {
		t.Fatalf("ledger = %v", entries)
	}
	// フェーズ 1 を開き直す（マーカーが消える）。台帳は残る。
	folder := filepath.Join(tr.Approved, "phases", ParentName)
	var kinds []string
	files, _ := os.ReadDir(folder)
	for _, e := range files {
		if strings.HasPrefix(e.Name(), "1.") {
			removeFile(t, filepath.Join(folder, e.Name()))
			kinds = append(kinds, strings.SplitN(e.Name(), ".", 2)[1])
		}
	}
	if !slices.Contains(kinds, "reviewed") {
		t.Errorf("消したマーカー = %v", kinds)
	}
	tr.noReviewedMark(1)
	if len(tr.ledger()) != 1 {
		t.Errorf("ledger = %v", tr.ledger())
	}
	OK(t, tr.ConfirmFor(f, "2"))
}

func TestAThreadAcceptedInPhase1IsCountedAgainAfterANewCommentOrAnEdit(t *testing.T) {
	t.Parallel()
	tr, f := twoPhases(t)
	for _, c := range []struct {
		name    string
		threads []map[string]any
	}{
		{"a new comment", []map[string]any{thread(map[string]any{"last_id": "c2"})}},
		{"an edit", []map[string]any{thread(map[string]any{"body": "やはりここも"})}},
	} {
		Has(t, Fails(t, tr.ConfirmFor(f.with(c.threads...), "2")).Stderr, "未解決のスレッドが 1 件")
	}
	extra := Fails(t, tr.ConfirmFor(f.with(thread(nil), thread(map[string]any{"id": "t2", "url": "https://h/c/2"})), "2"))
	Has(t, extra.Stderr, "https://h/c/2")
	Lacks(t, extra.Stderr, "https://h/c/1")
}

// AT18: 受け入れた指摘を鍵だけで持つ古い形のマーカーは、指摘を隠さない（台帳が無ければ数える）。
func TestAnAcceptedKeysOnlyMarkDoesNotHideAThread(t *testing.T) {
	t.Parallel()
	tr := cachedTree(t, "manual-keys-only", func(tr *Tree) {
		tr.FamilyPlan(planOf("design", "acceptance"), nil)
		tr.work("i0001-01-01", 1, "wip/design/*", "wip/design/a.md")
		raw, _ := json.Marshal(map[string]any{"at": time.Now().UTC().Format("2006-01-02T15:04:05+0000"), "mr": 7, "accepted": []string{"https://h/c/1"}})
		Write(tr.T, tr.markPath(1, "reviewed"), string(raw))
		f := tr.Origin()
		tr.work("i0001-02-01", 2, "tests/*", "tests/a.txt")
		tr.Parent("push", "--quiet", "origin", ParentName)
		OK(tr.T, tr.RequestFor(f, "2"))
	})
	if len(tr.ledger()) != 0 {
		t.Errorf("ledger = %v", tr.ledger())
	}
	refused := Fails(t, tr.ConfirmFor(tr.Fixture().with(thread(nil)), "2"))
	Has(t, refused.Stderr, "未解決のスレッドが 1 件")
	Has(t, refused.Stderr, "https://h/c/1")
	tr.noReviewedMark(2)
}
