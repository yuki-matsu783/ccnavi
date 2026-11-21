package phases

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/infra/ospath"
)

// フィードバック計画・Draft を外す（ready）・早めに閉じる（close-early）（tests/ticket/test_phases.py の中盤）。

// reviewedFamily は設計 1 つの親を、フェーズ 1 のレビューが済み、フィードバック計画だけが残る所まで進める。
// レビューの取得結果の見本を返す。
func (w *ws) reviewedFamily() string {
	w.t.Helper()
	w.family(planOf("design"))
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/design/*"))
	w.commitParent()
	w.mustApprove()
	w.runChild("i0001-01-01", file{"wip/design/plan.md", "d\n"})
	w.mustClose("i0001-01-01")
	w.commitParent("close 01")
	w.merge("i0001-01-01")
	fixture := w.remote()
	w.mustOK(w.request(fixture, 1), "request")
	return fixture
}

func TestTheFeedbackPlanComesAfterTheLastReviewAndOnlyOnce(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("design"))
	// 早すぎる改版は拒む。
	w.propose(parentName, w.parent(planOf("design"), samples.ParentOpts{Feedback: noFeedback}))
	early := w.approve()
	if early.Code == 0 || !strings.Contains(early.Stderr, "全部閉じてレビューが済んでから") {
		t.Errorf("早すぎる改版が止まらない: code %d\n%s", early.Code, early.Stderr)
	}
	w.propose(parentName, w.parent(planOf("design"), samples.ParentOpts{}))

	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/design/*"))
	w.commitParent()
	w.mustApprove()
	w.runChild("i0001-01-01", file{"wip/design/plan.md", "d\n"})
	w.mustClose("i0001-01-01")
	w.commitParent("close 01")
	w.merge("i0001-01-01")
	fixture := w.remote()
	w.mustOK(w.request(fixture, 1), "request")
	// ボードの JSON は「依頼済みで止まったまま」を review_waiting で言う。レビューが済んで止まらなくなれば false に戻り、
	// 依頼のマーカーは残る。
	w.wantBoard(1, true, true, "requested")
	w.mustOK(w.confirm(fixture, 1), "confirm")
	w.wantBoard(1, false, false, "requested", "reviewed")
	// 親はまだ閉じられない。
	w.startParent()
	refused := w.closeChild(parentName)
	if refused.Code == 0 || !strings.Contains(refused.Stderr, "フィードバック計画がまだ") {
		t.Errorf("フィードバック計画が無いのに親を閉じられる: code %d\n%s", refused.Code, refused.Stderr)
	}
	// 空のフィードバック計画を改版で出す（承認済みチケットは doing/ に在ってエージェントは書けないので、提案を todo/ に書く）。
	w.propose(parentName, w.parent(planOf("design"), samples.ParentOpts{Feedback: noFeedback}))
	approved := w.approve()
	if approved.Code != 0 {
		t.Fatalf("code %d\n%s%s", approved.Code, approved.Stdout, approved.Stderr)
	}
	contains(t, "承認画面", approved.Stdout, "対応なし")
	text := read(t, w.doing(parentName))
	contains(t, "承認済みチケット", text, "feedback: []")
	lacks(t, "承認済みチケット", text, "feedback_at") // 改版の時刻はチケットに書かない
	if exists(w.todo(parentName)) {
		t.Error("改版の提案が承認で todo/ から消えない")
	}
	// 2 度目は拒む。
	w.propose(parentName, w.parent(planOf("design"), samples.ParentOpts{Feedback: planOf("implement-feedback")}))
	again := w.approve()
	if again.Code == 0 || !strings.Contains(again.Stderr, "1 回だけ") {
		t.Errorf("2 度目のフィードバック計画が止まらない: code %d\n%s", again.Code, again.Stderr)
	}
	remove(t, w.todo(parentName))
	// 閉じられる。
	w.mustClose(parentName)
}

func TestAFeedbackWorkPhaseRunsAndDecideSendsTheRestToAnIssue(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	fixture := w.reviewedFamily()
	// 差し戻し。未解決の指摘が付いて confirm は通らない。
	setThreads(t, fixture, thread("t0", "直して", false))
	if w.confirm(fixture, 1).Code == 0 {
		t.Fatal("未解決の指摘があるのに confirm が通った")
	}
	spawn := w.hook("PreToolUse", "Agent", w.parentTree, input{"description": "次"})
	contains(t, "hook", w.reason(spawn), "DENY_PHASE_REVIEW")
	// フィードバック計画の承認ではレビューの状態を変えない。レビューが済む前は拒む。
	w.startParent()
	w.propose(parentName, w.parent(planOf("design"), samples.ParentOpts{Feedback: planOf("implement-feedback")}))
	early := w.approve()
	if early.Code == 0 || !strings.Contains(early.Stderr, "レビューが済んでから") {
		t.Errorf("レビュー前のフィードバック計画が止まらない: code %d\n%s", early.Code, early.Stderr)
	}
	// 最後のレビューでも、残った指摘を issue に回せる（フィードバック計画の有無に関わらない）。回す先を選んだ decide がレビュー済みにする。
	last := w.mustOK(w.ccnavi("--cwd", w.parentTree, "user", "reviewed", "1", "--accept-unresolved", "--preview", "--json", "--result", fixture), "reviewed preview")
	lastPreview := jsonOf(t, last.Stdout)
	if _, ok := lastPreview["can_issue"]; ok {
		t.Error("preview に can_issue が残っている")
	}
	choices, _ := json.Marshal(map[string]string{"u/7#t0": "issue"})
	settled := w.mustOK(w.ccnavi("--cwd", w.parentTree, "user", "reviewed", "1", "--accept-unresolved", "--yes", string(choices),
		"--digest", lastPreview["digest"].(string), "--json", "--result", fixture), "reviewed --yes")
	if jsonOf(t, settled.Stdout)["reviewed"] != true {
		t.Errorf("reviewed が true でない: %s", settled.Stdout)
	}
	reviewedMark := filepath.Join(w.approved, "phases", parentName, "1.reviewed")
	before := read(t, reviewedMark)
	planned := w.mustOK(w.approve(), "approve feedback plan")
	lacks(t, "承認画面", planned.Stdout, "済ませた扱い")
	if read(t, reviewedMark) != before {
		t.Error("フィードバック計画の承認がレビュー済みのマーカーを書き直した")
	}
	spawn = w.hook("PreToolUse", "Agent", w.parentTree, input{"description": "次"})
	lacks(t, "hook", w.reason(spawn), "DENY_PHASE_REVIEW")
	explained := w.ccnavi("--explain")
	contains(t, "--explain", explained.Stdout, "フェーズ 2（実装フィードバック対応）", "局面: フィードバック対応中")
	// 2 番目の子（範囲は親の範囲そのまま）。
	w.propose("i0001-02-02", childText("i0001-02-02", 2, "src/*"))
	w.commitParent("approval-request 02")
	w.mustApprove()
	w.runChild("i0001-02-02", file{"src/fix.py", "x\n"})
	w.mustClose("i0001-02-02")
	w.commitParent("close 02")
	w.merge("i0001-02-02")
	w.push()
	w.hook("PostToolUse", "Bash", w.parentTree, input{"command": "ls"})
	w.mustOK(w.request(fixture, 2), "request 2")
	// 前の指摘が残ったままなので、フィードバック作業フェーズの confirm も通らない。
	if w.confirm(fixture, 2).Code == 0 {
		t.Fatal("前の指摘が残っているのに confirm が通った")
	}
	data := readJSON(t, fixture)
	data["threads"] = append(data["threads"].([]any), thread("t1", "まだ", false))
	writeJSON(t, fixture, data)
	// フィードバック計画が承認済みなので、ユーザは残りを issue に回せる。
	shown := w.mustOK(w.ccnavi("--cwd", w.parentTree, "user", "reviewed", "2", "--accept-unresolved", "--preview", "--json", "--result", fixture), "preview 2")
	preview := jsonOf(t, shown.Stdout)
	if _, ok := preview["can_issue"]; ok {
		t.Error("preview に can_issue が残っている")
	}
	choices, _ = json.Marshal(map[string]string{"u/7#t0": "keep", "u/7#t1": "issue"})
	if w.confirm(fixture, 2).Code == 0 {
		t.Fatal("未解決の指摘があるのに confirm が通った")
	}
	done := w.mustOK(w.ccnavi("--cwd", w.parentTree, "user", "reviewed", "2", "--accept-unresolved", "--yes", string(choices),
		"--digest", preview["digest"].(string), "--json", "--result", fixture), "reviewed 2 --yes")
	answer := jsonOf(t, done.Stdout)
	if answer["reviewed"] != true {
		t.Errorf("reviewed が true でない: %s", done.Stdout)
	}
	text := read(t, answer["issue_draft"].(string))
	if !strings.HasPrefix(text, "レビューで残った指摘（i0001 のフェーズ 2）\n\n") {
		t.Errorf("issue の下書きの頭が違う: %q", text)
	}
	contains(t, "issue の下書き", text, "u/7#t1")
	lacks(t, "issue の下書き", text, "u/7#t0")
	// レビュー済みのフェーズに confirm を重ねない。重ねるとマーカーと履歴が書き直される
	again := w.confirm(fixture, 2)
	if again.Code != 1 || !strings.Contains(again.Stderr, "フェーズ 2 はレビュー済み") {
		t.Errorf("レビュー済みに confirm を重ねられる: code %d\n%s", again.Code, again.Stderr)
	}
}

func TestCheckStopsReviewConfirmAndReadyWithoutWriting(t *testing.T) {
	t.Parallel()
	// `--check review confirm` は依頼の記録が無ければ 1 と `次:` を返す。`--check review ready` は条件を満たさなければ 1 を返す。
	w := newWS(t)
	w.family(planOf("design"))
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/design/*"))
	w.commitParent()
	w.mustApprove()
	fixture := w.remote()
	checked := w.ccnavi("--check", "--cwd", w.parentTree, "--phase", "1", "review", "confirm", "--result", fixture)
	if checked.Code != 1 || !strings.Contains(checked.Stderr, "次:") {
		t.Errorf("--check review confirm: code %d\n%s", checked.Code, checked.Stderr)
	}
	checked = w.ccnavi("--check", "--cwd", w.parentTree, "review", "ready", "--result", fixture)
	if checked.Code != 1 {
		t.Errorf("--check review ready: code %d\n%s", checked.Code, checked.Stderr)
	}
}

// closedFamily は親を閉じて push し、ready を打てる形にする。取得結果の見本を返す。
func (w *ws) closedFamily() string {
	fixture, _ := w.closedFamilyWith(nil)
	return fixture
}

// closedFamilyWith は beforeClose があれば、親を閉じる直前に呼ぶ。親の finish の結果も返す。
func (w *ws) closedFamilyWith(beforeClose func()) (string, run.Result) {
	w.t.Helper()
	w.family(planOf("design"))
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/design/*"))
	w.commitParent()
	w.mustApprove()
	fixture := w.remote()
	w.runChild("i0001-01-01", file{"wip/design/plan.md", "d\n"})
	w.mustClose("i0001-01-01")
	w.commitParent("close 01")
	w.merge("i0001-01-01")
	w.push()
	w.mustOK(w.request(fixture, 1), "request")
	w.mustOK(w.confirm(fixture, 1), "confirm")
	w.startParent()
	w.propose(parentName, w.parent(planOf("design"), samples.ParentOpts{Feedback: noFeedback}))
	w.mustApprove()
	if beforeClose != nil {
		beforeClose()
	}
	closed := w.mustClose(parentName)
	w.commitParent("状態の移動")
	w.push()
	return fixture, closed
}

// readyRecorded は write-and-push と同じ形（書いたパスを集める）で ready を打つ。
func (w *ws) readyRecorded(fixture string) ([]string, run.Result) {
	listed := filepath.Join(w.root, "logs", "state", "write-and-push", "ready.writes")
	done := w.ccnavi("--record-writes", listed, "--record-tree", w.parentTree, "--cwd", w.parentTree, "review", "ready", "--result", fixture)
	var written []string
	if exists(listed) {
		written = splitLines(read(w.t, listed))
	}
	return written, done
}

func splitLines(s string) []string {
	var out []string
	for _, l := range strings.Split(s, "\n") {
		if l != "" {
			out = append(out, l)
		}
	}
	return out
}

func TestReadyNeedsTheParentToBeClosable(t *testing.T) {
	t.Parallel()
	// Draft を外せるのは、親を閉じられる状態と同じ。閉じたあとでも打てる。
	w := newWS(t)
	w.family(planOf("design"))
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/design/*"))
	w.commitParent()
	w.mustApprove()
	fixture := w.remote()
	// 子が開いている間は外せない。
	refused := w.ready(fixture)
	if refused.Code == 0 {
		t.Fatal("子が開いているのに Draft を外せた")
	}
	contains(t, "stderr", refused.Stderr, "Draft を解除できません", "close-early")
	w.runChild("i0001-01-01", file{"wip/design/plan.md", "d\n"})
	w.mustClose("i0001-01-01")
	w.commitParent("close 01")
	w.merge("i0001-01-01")
	w.push()
	w.mustOK(w.request(fixture, 1), "request")
	w.mustOK(w.confirm(fixture, 1), "confirm")
	// フィードバック計画が無い間も外せない。
	refused = w.ready(fixture)
	contains(t, "stderr", refused.Stderr, "フィードバック計画")
	w.startParent()
	w.propose(parentName, w.parent(planOf("design"), samples.ParentOpts{Feedback: noFeedback}))
	w.mustApprove()
	// 閉じられる状態になった。wip/ が追跡されていても、それは ready が消すので止めない。
	refused = w.ready(fixture)
	if refused.Code == 0 {
		t.Error("閉じる前に Draft を外せた")
	}
	lacks(t, "stderr", refused.Stderr, "`wip/` に追跡されているファイル")
	tracked := strings.Fields(w.git(w.parentTree, "ls-files", "--", "wip"))
	if len(tracked) == 0 {
		t.Fatal("wip/ に追跡されたファイルが無い")
	}
	// 親を閉じる。案内は「push して ready」。wip/ を消す手順（rm -r wip）は案内しない。
	closed := w.mustOK(w.ccnavi("ticket", "finish", parentName), "finish parent")
	lacks(t, "finish", closed.Stdout, "rm -r wip")
	contains(t, "finish", closed.Stdout, "ready", "`wip/` の追跡済みのファイルを削除して", "ready --parent i0001", "合意した範囲に入る", "確認を取り直さずに進める", "squash")
	// 閉じた記録（どのフェーズをどこで見たか）を置く。
	record := readJSON(t, filepath.Join(w.approved, "phases", parentName, "closed.json"))
	if reviews, _ := record["reviews"].(map[string]any); len(reviews) != 1 || reviews["1"] != true {
		t.Errorf("closed.json の reviews = %v", record["reviews"])
	}
	w.commitParent("状態の移動")
	// push していなければまだ外せない。
	contains(t, "stderr", w.ready(fixture).Stderr, "push されていない")
	w.push()
	// wip/ に未追跡のファイルがあれば、消さずに名指しして止める（履歴から戻せないため）。
	memo := write(t, filepath.Join(w.parentTree, "wip", "memo.txt"), "メモ\n")
	refused = w.ready(fixture)
	if refused.Code == 0 {
		t.Error("未追跡のファイルがあるのに ready が通った")
	}
	contains(t, "stderr", refused.Stderr, "未追跡のファイルが 1 件", "wip/memo.txt")
	if !exists(memo) {
		t.Error("止めたのに未追跡のファイルを消した")
	}
	remove(t, memo)
	// push 済み。ready が通り、マーカーと note の下書きができる。wip/ の追跡済みのファイルは消える。
	head := strings.TrimSpace(w.git(w.parentTree, "rev-parse", "HEAD"))
	// write-and-push と同じく書いたパスを集めて打つ。wip/ の削除は write-and-push がコミットしてよいもの
	listed := filepath.Join(w.root, "logs", "state", "write-and-push", "ready.writes")
	passed := w.ccnavi("--record-writes", listed, "--record-tree", w.parentTree, "--cwd", w.parentTree, "review", "ready", "--result", fixture)
	if passed.Code != 0 {
		t.Fatalf("code %d\n%s%s", passed.Code, passed.Stdout, passed.Stderr)
	}
	written := splitLines(read(t, listed))
	for _, rel := range tracked {
		if !slices.Contains(written, rel) {
			t.Errorf("書いたパスの一覧に %s が無い", rel)
		}
	}
	lines := splitLines(passed.Stdout)
	note := read(t, lines[0])
	contains(t, "note の下書き", note, "ccnavi:ready", "squash")
	if !slices.Contains(lines, "wip-from "+head) {
		t.Errorf("wip-from の行が無い: %v", lines)
	}
	var wipLines []string
	for _, l := range lines {
		if rest, ok := strings.CutPrefix(l, "wip "); ok {
			wipLines = append(wipLines, rest)
		}
	}
	sort.Strings(wipLines)
	sorted := slices.Clone(tracked)
	sort.Strings(sorted)
	equalStrings(t, "wip の行", wipLines, sorted)
	for _, rel := range tracked {
		if exists(filepath.Join(w.parentTree, filepath.FromSlash(rel))) {
			t.Errorf("%s が消えていない", rel)
		}
	}
	if exists(filepath.Join(w.parentTree, "wip")) {
		t.Error("wip/ が残っている")
	}
	// マーカーは閉じたチケットと一緒に手元の退避（logs/archive/）へ移る
	mark := readJSON(t, filepath.Join(w.root, "logs", "archive", "self", "phases", parentName, "ready.json"))
	if mark["mr"] != float64(7) {
		t.Errorf("ready.json の mr = %v", mark["mr"])
	}
	if exists(filepath.Join(w.approved, "done", "i0001.md")) {
		t.Error("done/ に親が残っている")
	}
	// 同じ親にもう 1 度打っても通る（sh が外し損ねたときの打ち直し。MR がまだ Draft のとき）。
	if again := w.ready(fixture); again.Code != 0 {
		t.Errorf("打ち直しが通らない: code %d\n%s", again.Code, again.Stderr)
	}
}

func TestWriteAndPushLetsReadyRemoveWipInAnyCaseButNothingElse(t *testing.T) {
	t.Parallel()
	// write-and-push の置き場の外の検査は、ready が消す wip/ の判定（大文字小文字と \ を問わない）と同じ判定で、消したものだけを通す。
	if ospath.Windows {
		t.Skip("大文字違いの並存と名前の \\ は Windows では作れない")
	}
	if !base.CanCaseSensitive() {
		t.Skip("大文字小文字を区別しないファイルシステムでは大文字違いを並べられない")
	}
	w := newWS(t)
	fixture := w.closedFamily()
	odd := []string{"WIP/design/a.html", `wip\design\b.html`}
	for _, rel := range odd {
		write(t, filepath.Join(w.parentTree, filepath.FromSlash(rel)), "<p>x</p>\n")
		w.git(w.parentTree, "add", "--", rel)
	}
	w.git(w.parentTree, "commit", "--quiet", "-m", "odd wip")
	w.push()
	written, done := w.readyRecorded(fixture)
	if done.Code != 0 {
		t.Fatalf("ready: code %d\n%s", done.Code, done.Stderr)
	}
	for _, rel := range append(odd, "wip/design/plan.md") {
		if !slices.Contains(written, rel) {
			t.Errorf("書いたパスの一覧に %s が無い", rel)
		}
		if _, err := os.Lstat(filepath.Join(w.parentTree, filepath.FromSlash(rel))); err == nil {
			t.Errorf("%s が消えていない", rel)
		}
	}
}

func (w *ws) closeEarly(fixture, reason, answer string) (code int, stdout, stderr string) {
	r := w.ccnaviIn(answer+"\n", "--cwd", w.parentTree, "user", "close-early", "--reason", reason, "--result", fixture)
	return r.Code, r.Stdout, r.Stderr
}

func TestCloseEarlyClosesEarlyAndFilesTheRest(t *testing.T) {
	t.Parallel()
	// ユーザが「キリの良いところ」と早めに閉じる。残りは取り消し・省略・受け入れになり、issue に書き出される。
	w := newWS(t)
	w.family(planOf("research", "design", "acceptance", "implement"))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent()
	w.mustApprove()
	w.runChild("i0001-01-01", file{"wip/research/summary.md", "s\n"})
	// 作業中の子がいる間は早めに閉じられない。
	fixture := w.remote()
	code, _, stderr := w.closeEarly(fixture, "ここまでで十分", "y")
	if code == 0 || !strings.Contains(stderr, "作業中の子") {
		t.Errorf("作業中の子がいるのに早めに閉じられる: code %d\n%s", code, stderr)
	}
	w.mustClose("i0001-01-01")
	w.commitParent("close 01")
	w.merge("i0001-01-01")
	w.hook("PostToolUse", "Bash", w.parentTree, input{"command": "ls"})
	// 2 番目の子を提案して承認するが、着手はしない（todo/ に残る）。
	w.propose("i0001-02-02", childText("i0001-02-02", 2, "wip/design/*"))
	w.commitParent("approval-request 02")
	w.mustApprove()
	w.startParent()
	setThreads(t, fixture, thread("t0", "気になる", false))
	// n なら何も変わらない。
	code, stdout, _ := w.closeEarly(fixture, "ここまでで十分", "n")
	if code == 0 {
		t.Error("n と答えたのに早めに閉じた")
	}
	contains(t, "承認画面", stdout, "i0001-02-02", "実装とテスト", "フィードバック計画", "u/7#t0")
	if !w.isApproved("i0001-02-02") {
		t.Error("n なのに子が動いた")
	}
	// y で早めに閉じる。
	code, stdout, stderr = w.closeEarly(fixture, "今期はここまで", "y")
	if code != 0 {
		t.Fatalf("code %d\n%s%s", code, stdout, stderr)
	}
	contains(t, "stdout", stdout, "早めに閉じた")
	cancelled := filepath.Join(w.approved, "done", "i0001-02-02.md")
	if !exists(cancelled) {
		t.Fatal("取り消した子が done/ に無い")
	}
	contains(t, "取り消した子", read(t, cancelled), "cancelled_at:")
	if w.isApproved("i0001-02-02") {
		t.Error("取り消した子が doing/ に残っている")
	}
	mark := readJSON(t, filepath.Join(w.approved, "phases", parentName, "close-early.json"))
	if mark["reason"] != "今期はここまで" {
		t.Errorf("reason = %v", mark["reason"])
	}
	if got, _ := mark["cancelled"].([]any); len(got) != 1 || got[0] != "i0001-02-02" {
		t.Errorf("cancelled = %v", mark["cancelled"])
	}
	// 手を付けていないフェーズ(2・3・4)には省略のマーカーを置かない。
	if got, _ := mark["skipped"].([]any); len(got) != 0 {
		t.Errorf("skipped = %v", mark["skipped"])
	}
	w.noSkipMarks(2, 3, 4)
	if got, _ := mark["accepted"].([]any); len(got) != 1 || got[0] != "u/7#t0" {
		t.Errorf("accepted = %v", mark["accepted"])
	}
	issue := read(t, filepath.Join(w.state, "review-close-early-issue-i0001.md"))
	if !strings.HasPrefix(issue, "親 の残り\n\n") {
		t.Errorf("issue の下書きの頭が違う: %q", issue)
	}
	contains(t, "issue の下書き", issue, "i0001-02-02", "実装とテスト", "u/7#t0", "今期はここまで")
	// 打ち直しても、取り消し済みの子だけになったフェーズ(2)に省略のマーカーを置かない。
	if code, stdout, stderr = w.closeEarly(fixture, "今期はここまで", "y"); code != 0 {
		t.Fatalf("打ち直し: code %d\n%s%s", code, stdout, stderr)
	}
	w.noSkipMarks(2, 3, 4)
	// 早めに閉じたので、フィードバック計画が無くても親を閉じられる。
	contains(t, "--explain", w.ccnavi("--explain").Stdout, "ユーザが早めにクローズした")
	w.mustOK(w.ccnavi("ticket", "finish", parentName), "finish parent")
	// Draft はまだ外れていない。片付けて push してから ready で外す。
	if exists(filepath.Join(w.approved, "phases", parentName, "ready.json")) {
		t.Error("ready 前に ready.json がある")
	}
	w.commitParent("状態の移動")
	w.push()
	passed := w.ready(fixture)
	if passed.Code != 0 {
		t.Fatalf("ready: code %d\n%s", passed.Code, passed.Stderr)
	}
	if !slices.Contains(splitLines(passed.Stdout), "wip wip/research/summary.md") {
		t.Errorf("ready の出力に wip の行が無い: %s", passed.Stdout)
	}
}

func (w *ws) noSkipMarks(numbers ...int) {
	w.t.Helper()
	for _, n := range numbers {
		if exists(filepath.Join(w.approved, "phases", parentName, itoa(n)+".skipped")) {
			w.t.Errorf("フェーズ %d に省略のマーカーがある", n)
		}
	}
}

func TestCloseEarlyAcceptsThreadsInThreeStages(t *testing.T) {
	t.Parallel()
	// 受け入れの返信と解決は sh がする。置く前に済ませるため、3 回に分けて打つ。
	//  1 回目（--accept-plan）は y/N を聞いて計画だけを書く。何も置かない。
	//  2 回目（--accept-check）は、取り直した結果の未解決が計画と同じかだけを確かめる。
	//  3 回目（--accept-done）は、返信と解決のあとで取り直した結果を、ホストの実状態で確かめて聞き直さずに置く。
	w := newWS(t)
	w.family(planOf("design"))
	w.commitParent()
	fixture := w.remote()
	setThreads(t, fixture, thread("t0", "気になる", false))
	plan := filepath.Join(w.state, "accept-plan.json")
	baseArgs := []string{"--cwd", w.parentTree, "user", "close-early", "--reason", "ここまで"}
	run := func(input string, extra ...string) (int, string, string) {
		r := w.ccnaviIn(input, append(slices.Clone(baseArgs), extra...)...)
		return r.Code, r.Stdout, r.Stderr
	}
	marker := filepath.Join(w.approved, "phases", parentName, "close-early.json")

	// 計画が無いまま 2 回目・3 回目を打っても、置かない
	for _, flag := range []string{"--accept-check", "--accept-done"} {
		code, _, stderr := run("", "--result", fixture, flag, plan)
		if code == 0 || !strings.Contains(stderr, "受け入れの計画") {
			t.Errorf("%s: 計画が無いのに通る: code %d\n%s", flag, code, stderr)
		}
	}
	// n なら計画も書かない
	if code, _, _ := run("n\n", "--result", fixture, "--accept-plan", plan); code == 0 || exists(plan) {
		t.Errorf("n なのに計画を書いた: code %d", code)
	}
	code, stdout, stderr := run("y\n", "--result", fixture, "--accept-plan", plan)
	if code != 0 {
		t.Fatalf("accept-plan: code %d\n%s%s", code, stdout, stderr)
	}
	body := readJSON(t, plan)
	threads, _ := body["threads"].([]any)
	if len(threads) != 1 || threads[0].(map[string]any)["id"] != "t0" {
		t.Fatalf("計画の threads = %v", body["threads"])
	}
	reply := threads[0].(map[string]any)["reply"].(string)
	contains(t, "返信", reply, "早めに閉じた", "issue の下書き")
	if exists(marker) {
		t.Error("計画だけの段でマーカーを置いた")
	}
	// y/N の間に指摘が増えていたら、ホストに触る前に止まる
	grown := readJSON(t, fixture)
	grown["threads"] = append(grown["threads"].([]any), thread("t1", "追加", false))
	grownFixture := writeJSON(t, filepath.Join(w.state, "grown.json"), grown)
	if code, _, stderr = run("", "--result", grownFixture, "--accept-check", plan); code == 0 || !strings.Contains(stderr, "未解決の指摘が変わった") {
		t.Errorf("増えた指摘で止まらない: code %d\n%s", code, stderr)
	}
	if code, _, stderr = run("", "--result", fixture, "--accept-check", plan); code != 0 {
		t.Errorf("accept-check: code %d\n%s", code, stderr)
	}
	if exists(marker) {
		t.Error("確かめだけの段でマーカーを置いた")
	}
	// sh が解決する前（ホストは未解決のまま）に置こうとしても置かない
	if code, _, stderr = run("", "--result", fixture, "--accept-done", plan); code == 0 || !strings.Contains(stderr, "ホストで解決済みになっていない") {
		t.Errorf("未解決のまま置ける: code %d\n%s", code, stderr)
	}
	if exists(marker) {
		t.Error("未解決のままマーカーを置いた")
	}
	// 解決したあとに、見せていない未解決が増えていたら置かない
	data := readJSON(t, fixture)
	data["threads"] = []any{thread("t0", "気になる", true)}
	extra := jsonOf(t, mustMarshal(t, data))
	extra["threads"] = append(extra["threads"].([]any), thread("t1", "追加", false))
	extraFixture := writeJSON(t, filepath.Join(w.state, "extra.json"), extra)
	if code, _, stderr = run("", "--result", extraFixture, "--accept-done", plan); code == 0 || !strings.Contains(stderr, "計画のほかに未解決の指摘が残っている") {
		t.Errorf("見せていない未解決を置ける: code %d\n%s", code, stderr)
	}
	if exists(marker) {
		t.Error("見せていない未解決があるのにマーカーを置いた")
	}
	// 解決済みで、ほかに増えていなければ、聞き直さずに置く（stdin は空）
	writeJSON(t, fixture, data)
	if code, stdout, stderr = run("", "--result", fixture, "--accept-done", plan); code != 0 {
		t.Fatalf("accept-done: code %d\n%s%s", code, stdout, stderr)
	}
	mark := readJSON(t, marker)
	if got, _ := mark["accepted"].([]any); len(got) != 1 || got[0] != "u/7#t0" {
		t.Errorf("accepted = %v", mark["accepted"])
	}
	if exists(filepath.Join(w.approved, "phases", parentName, "accepted.json")) {
		t.Error("accepted.json が残っている")
	}
}

func mustMarshal(t testing.TB, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestCloseEarlyReplyDoesNotPromiseAnIssueWithNoIssue(t *testing.T) {
	t.Parallel()
	// `--no-issue` で残りを issue に書き出さないときは、返信に issue に回すと書かない。
	w := newWS(t)
	w.family(planOf("design"))
	w.commitParent()
	fixture := w.remote()
	setThreads(t, fixture, thread("t0", "気になる", false))
	plan := filepath.Join(w.state, "accept-plan.json")
	planned := w.ccnaviIn("y\n", "--cwd", w.parentTree, "user", "close-early", "--reason", "ここまで", "--result", fixture, "--accept-plan", plan, "--no-issue")
	if planned.Code != 0 {
		t.Fatalf("code %d\n%s%s", planned.Code, planned.Stdout, planned.Stderr)
	}
	reply := readJSON(t, plan)["threads"].([]any)[0].(map[string]any)["reply"].(string)
	lacks(t, "返信", reply, "issue")
}

func TestCloseEarlyMarksATouchedPhaseSoTheHookStaysQuiet(t *testing.T) {
	t.Parallel()
	// 未着手の子のほかにレビュー待ちの子が居るフェーズは、取り消すと終わる。省略のマーカーが無いと、次の hook の告知が
	// pending を書いて「レビューを頼め」と言い出すので、置き続ける。
	w := newWS(t)
	w.family(planOf("design", "implement"))
	w.propose("i0001-01-01", childText("i0001-01-01", 1, "wip/design/*"))
	w.commitParent()
	w.mustApprove()
	w.runChild("i0001-01-01", file{"wip/design/plan.md", "d\n"})
	w.mustClose("i0001-01-01")
	w.commitParent("close 01")
	w.merge("i0001-01-01")
	// フェーズ 1 に 2 つ目の子を足して承認するが、着手はしない。
	w.propose("i0001-01-02", childText("i0001-01-02", 1, "wip/design/*"))
	w.commitParent("approval-request 02")
	w.mustApprove()
	w.startParent()
	fixture := w.remote()
	if code, stdout, stderr := w.closeEarly(fixture, "ここまでで十分", "y"); code != 0 {
		t.Fatalf("code %d\n%s%s", code, stdout, stderr)
	}
	dir := filepath.Join(w.approved, "phases", parentName)
	// 手を付けたフェーズ 1 には置く。手を付けていないフェーズ 2 には置かない。
	if !exists(filepath.Join(dir, "1.skipped")) {
		t.Error("手を付けたフェーズ 1 に省略のマーカーが無い")
	}
	w.noSkipMarks(2)
	w.hook("PostToolUse", "Bash", w.parentTree, input{"command": "ls"})
	if exists(filepath.Join(dir, "1.pending")) {
		t.Error("次の hook が pending を書いた")
	}
}

func TestCloseEarlyIsAHumanPath(t *testing.T) {
	t.Parallel()
	// close-early はターミナルを求める。サブエージェントと直接の exe 呼び出しは止まる。
	w := newWS(t)
	w.family(planOf("design"))
	fixture := w.remote()
	r := w.ccnaviIn("y\n", "--cwd", w.parentTree, "--entry", "sh", "user", "close-early", "--reason", "r", "--result", fixture, "--guard-ticket-approval", "enable")
	if r.Code == 0 || !strings.Contains(r.Stderr, "ターミナル") {
		t.Errorf("直接の呼び出しが止まらない: code %d\n%s", r.Code, r.Stderr)
	}
	for _, command := range []string{"sh .ccnavi/scripts/ccnavi-review.sh decide 1", "sh .ccnavi/scripts/ccnavi-review.sh ready"} {
		denied := w.hookWith(hookOpts{AgentID: "sub-1"}, "PreToolUse", "Bash", w.parentTree, input{"command": command})
		if !strings.Contains(w.reason(denied), "DENY_SUBAGENT_TICKET_OP") {
			t.Errorf("%s: サブエージェントが止まらない: %s", command, denied.Stdout)
		}
	}
}
