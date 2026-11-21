package phases

import (
	"encoding/json"
	"flag"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 計画の順序を振り直して書き戻す `ccnavi --plan-order <親>` の受入テスト。
// VS Code の計画編集タブが打つ経路。タブは図で引いた線（元の番号での直接の先行）を `--order` に付けて渡し、実行ファイルが
// 番号を振り直して答える。保存では `--write --expect <ハッシュ>` で提案の計画を書き換える。
// 番号の振り直しの決まり（拡張と共有する見本の表 tests/fixtures/plan-reorder.json）と、承認を渡す文は
// go/internal/tickets/proposal の planedit と agree が見る。

const planOrderFixture = "extensions/vscode/ccnavi-board/test/fixtures/plan-order.json"

var updateBoard = flag.Bool("update-board-fixtures", false, "VS Code 拡張の計画編集タブのフィクスチャを、実行ファイルの答えで書き直す")

// plan3 は 3 ステップの一直線。research → design → implement。
func plan3() []samples.Step {
	return []samples.Step{{Type: "research"}, after("design", 1), after("implement", 2)}
}

// reverse は design が research に先行するように線を逆に引いた形（元の番号での直接の先行）。
const reverse = `{"plan": {"1": [2], "2": [], "3": [1]}}`

func (w *ws) order(name string, extra ...string) (code int, stdout, stderr string) {
	r := w.ccnavi(append([]string{"--plan-order", name}, extra...)...)
	return r.Code, r.Stdout, r.Stderr
}

func (w *ws) orderBody(extra ...string) map[string]any {
	w.t.Helper()
	code, out, errText := w.order(parentName, append([]string{"--json"}, extra...)...)
	if code != 0 {
		w.t.Fatalf("--plan-order: code %d\n%s%s", code, out, errText)
	}
	return ticketkit.ParseJSON(w.t, out)
}

// writeOrder は振り直した順序を提案に書く。expect が空なら今の source_sha を渡す。
func (w *ws) writeOrder(order, expect string) (int, string, string) {
	w.t.Helper()
	if expect == "" {
		expect = w.orderBody()["source_sha"].(string)
	}
	return w.order(parentName, "--order", order, "--expect", expect, "--write", "--json")
}

func (w *ws) mustWriteOrder(order string) map[string]any {
	w.t.Helper()
	code, out, errText := w.writeOrder(order, "")
	if code != 0 {
		w.t.Fatalf("--write: code %d\n%s%s", code, out, errText)
	}
	return ticketkit.ParseJSON(w.t, out)
}

func (w *ws) previewBody() map[string]any {
	return ticketkit.ParseJSON(w.t, w.ccnavi("--agree", "--preview", "--json").Stdout)
}

func TestTheSourceShaCoversThePendingChildren(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	code, _, stderr := w.order("i0009", "--json")
	if code != 1 || !strings.Contains(stderr, "i0009") {
		t.Errorf("知らない親: code %d\n%s", code, stderr)
	}
	w.propose(parentName, w.parent(plan3(), samples.ParentOpts{Copy: samples.Phases}))
	before := w.orderBody()["source_sha"]
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	if w.orderBody()["source_sha"] == before {
		t.Error("子の提案が source_sha に入っていない")
	}
}

func TestTheWrittenProposalIsApprovedAndTheOldDigestIsNot(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.propose(parentName, w.parent(plan3(), samples.ParentOpts{}))
	w.commitParent()
	old := w.previewBody()["digest"].(string)
	w.mustWriteOrder(reverse)
	written := ticketkit.Read(t, w.todo(parentName))
	w.commitParent("reorder")
	stale := w.ccnavi("user", "agree", "--yes", parentName, "--digest", old, "--json")
	if stale.Code != 1 {
		t.Errorf("書き換え前のダイジェストが通った: code %d\n%s%s", stale.Code, stale.Stdout, stale.Stderr)
	}
	if w.isApproved(parentName) {
		t.Error("古いダイジェストで承認済みチケットが置かれた")
	}
	fresh := w.previewBody()["digest"].(string)
	w.mustOK(w.ccnavi("user", "agree", "--yes", parentName, "--digest", fresh, "--json"), "agree")
	if ticketkit.Read(t, w.doing(parentName)) != written {
		t.Error("承認が書き換えた提案の中身と違う")
	}
}

func TestAnAgentRewritingTheOrderAfterTheSaveChangesThePreviewSha(t *testing.T) {
	t.Parallel()
	// 拡張は保存で返った source_sha を覚え、承認のオーバーレイを開くときに preview の plans の source_sha と比べて、違えば
	// 「保存したあとに提案が変わった」と出す。その比べ方が成り立つ前提（書き換えなければ同じ・書き戻せば違う、図は書き戻した順序）を見る。
	w := newWS(t)
	text := w.parent(plan3(), samples.ParentOpts{})
	w.propose(parentName, text)
	w.commitParent()
	saved := w.mustWriteOrder(reverse)["source_sha"]
	w.commitParent("reorder")
	plans := w.previewBody()["plans"].([]any)
	if len(plans) != 1 || plans[0].(map[string]any)["source_sha"] != saved {
		t.Errorf("preview の source_sha が保存で返った値と違う: %v", plans)
	}
	// エージェントが提案を書いた順序へ黙って戻す。
	w.propose(parentName, text)
	w.commitParent("revert")
	plan := w.previewBody()["plans"].([]any)[0].(map[string]any)
	if plan["source_sha"] == saved {
		t.Error("書き戻しても source_sha が変わらない")
	}
	if !reflect.DeepEqual(plan["after"], map[string]any{"2": []any{1.0}, "3": []any{2.0}}) {
		t.Errorf("after = %v", plan["after"])
	}
}

func TestPreviewPlansComeFromTheSameFunction(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.propose(parentName, w.parent(plan3(), samples.ParentOpts{}))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent()
	sameJSON(t, "plans", w.previewBody()["plans"], w.orderBody()["plans"])
}

// ---- 改版では子が承認された番号を動かさない

var revisionPlan = []samples.Step{{Type: "research"}, after("design", 1), after("acceptance", 1), after("implement", 2, 3)}
var revisedPlan = []samples.Step{{Type: "research"}, after("design", 1), after("acceptance", 1), after("chores", 1), after("implement", 2, 3, 4)}

const revisionOrder = `{"plan": {"1": [], "2": [1, 4], "3": [1], "4": [1], "5": [2, 3, 4]}}`

// revisionWS は 1 番に子が承認された親に、4 ステップ目を足す改版を提案した所。
func newRevisionWS(t *testing.T) *ws {
	w := newWS(t)
	w.family(revisionPlan)
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent()
	w.mustApprove()
	w.propose(parentName, w.parent(revisedPlan, samples.ParentOpts{Copy: samples.Phases}))
	w.commitParent("revise")
	return w
}

func TestAFixedNumberStaysAndFreeOnesArePacked(t *testing.T) {
	t.Parallel()
	w := newRevisionWS(t)
	body := w.orderBody("--order", revisionOrder)
	if refused, _ := body["refused"].([]any); len(refused) != 0 {
		t.Errorf("refused = %v", refused)
	}
	plan := body["plans"].([]any)[0].(map[string]any)
	var from []float64
	var locked []bool
	for _, i := range plan["items"].([]any) {
		m := i.(map[string]any)
		from = append(from, m["from"].(float64))
		locked = append(locked, m["locked"].(bool))
	}
	if !reflect.DeepEqual(from, []float64{1, 3, 4, 2, 5}) || !reflect.DeepEqual(locked, []bool{true, false, false, false, false}) {
		t.Errorf("from = %v, locked = %v", from, locked)
	}
	sameJSON(t, "current", plan["current"], map[string]any{"2": []any{1}, "3": []any{1}, "4": []any{2, 3}})
	if rp, _ := body["revision_problems"].([]any); len(rp) != 0 {
		t.Errorf("revision_problems = %v", rp)
	}
	if body["writable"] != true {
		t.Error("writable が true でない")
	}
	w.mustWriteOrder(revisionOrder)
	var types []string
	for _, s := range stepsOf(frontOf(t, ticketkit.Read(t, w.todo(parentName))), "plan") {
		types = append(types, s.Type)
	}
	equalStrings(t, "plan の型", types, []string{"research", "acceptance", "chores", "design", "implement"})
	w.commitParent("reorder")
	if r := w.approve(); r.Code != 0 {
		t.Errorf("振り直した計画が承認されない: code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	// 詰められない形は見本の表（tests/fixtures/plan-reorder.json）を Go の planedit が見る。
}

func TestAnApprovedChildStaysAndAPendingOneMoves(t *testing.T) {
	t.Parallel()
	// 承認済みの子には触れない。動くのは承認前の子の提案だけ。
	w := newWS(t)
	w.family(revisionPlan)
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/research/*"))
	w.commitParent()
	w.mustApprove()
	held := ticketkit.Read(t, w.doing("i0001-01-01"))
	w.propose(parentName, w.parent(revisedPlan, samples.ParentOpts{Copy: samples.Phases}))
	w.propose("i0001-02-01", childText("i0001-02-01", 2, "wip/design/*"))
	w.commitParent("revise")
	body := w.orderBody("--order", revisionOrder)
	var moved []string
	for _, c := range body["children"].([]any) {
		m := c.(map[string]any)
		moved = append(moved, m["ticket"].(string)+"->"+m["new_ticket"].(string))
	}
	equalStrings(t, "children", moved, []string{"i0001-02-01->i0001-04-01"})
	w.mustWriteOrder(revisionOrder)
	if phase := frontOf(t, ticketkit.Read(t, w.todo("i0001-04-01"))).Text("phase"); phase != "4" {
		t.Errorf("phase = %v", phase)
	}
	if ticketkit.Exists(w.todo("i0001-02-01")) {
		t.Error("元の子の提案が残っている")
	}
	if ticketkit.Read(t, w.doing("i0001-01-01")) != held {
		t.Error("承認済みの子が変わった")
	}
}

// ---- 拡張のフィクスチャと同じ形

var shaPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

// portable は絶対パスを機械に依らない表記に置き換え、走らせるたびに変わる source_sha を伏せる。
func portable(t testing.TB, v any, root string) any {
	t.Helper()
	switch x := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, e := range x {
			if k == "source_sha" {
				if s, _ := e.(string); !shaPattern.MatchString(s) {
					t.Errorf("source_sha の形が違う: %v", e)
				}
				out[k] = "<sha>"
				continue
			}
			out[k] = portable(t, e, root)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = portable(t, e, root)
		}
		return out
	case string:
		return ticketkit.PortableText(x, root)
	}
	return v
}

func TestShapesMatchTheExtensionFixture(t *testing.T) {
	t.Parallel()
	// 計画編集タブが読む答えを並べる。開いたとき（open）、逆向きの線で番号と子が動くとき（reverse）、循環で断るとき（cycle）、
	// 終端の検査で書けないとき（loose）、改版で子が承認された番号があるとき（revision）、書いたとき（written）。
	// 形を変えたら -update-board-fixtures を付けてこのテストを走らせ、フィクスチャを書き直す。
	w := newWS(t)
	plan := []samples.Step{{Type: "research"}, after("design", 1), after("implement", 1), after("docs", 2, 3)}
	feedback := []samples.Step{{Type: "design-feedback"}, after("implement-feedback", 5)}
	w.propose(parentName, w.parent(plan, samples.ParentOpts{Feedback: feedback}))
	w.propose("i0001-02-01", childText("i0001-02-01", 2, "wip/design/*"))
	w.propose("i0001-03-01", childText("i0001-03-01", 3, "src/*"))
	bodies := map[string]map[string]any{
		"open":    w.orderBody(),
		"reverse": w.orderBody("--order", `{"plan": {"1": [], "2": [1, 3], "3": [1], "4": [2]}}`),
		"cycle":   w.orderBody("--order", `{"plan": {"1": [4], "2": [1], "3": [1], "4": [2, 3]}}`),
		"loose":   w.orderBody("--order", `{"plan": {"2": [1], "3": [1], "4": [2]}}`),
	}
	if c, _ := bodies["reverse"]["children"].([]any); len(c) == 0 {
		t.Error("reverse で子が動かない")
	}
	if refused := bodies["cycle"]["refused"].([]any); len(refused) != 1 || refused[0].(map[string]any)["kind"] != "cycle" {
		t.Errorf("cycle の refused = %v", bodies["cycle"]["refused"])
	}
	if bodies["loose"]["writable"] != false {
		t.Error("loose が書ける")
	}
	bodies["written"] = w.mustWriteOrder(`{"plan": {"1": [], "2": [1, 3], "3": [1], "4": [2]}}`)
	if bodies["written"]["written"] != true {
		t.Error("written が true でない")
	}
	got := map[string]any{}
	for name, body := range bodies {
		got[name] = portable(t, body, w.root)
	}
	// 改版の答え（1 番に子が承認されている＝固定した番号）。改版の道具を別に組んで読む。
	rw := newRevisionWS(t)
	got["revision"] = portable(t, rw.orderBody("--order", revisionOrder), rw.root)
	path := filepath.Join(base.Root(), filepath.FromSlash(planOrderFixture))
	if *updateBoard {
		body, err := marshalIndent(got)
		if err != nil {
			t.Fatal(err)
		}
		ticketkit.WriteFixture(t, path, body)
	}
	var fixture map[string]any
	if err := json.Unmarshal([]byte(ticketkit.Read(t, path)), &fixture); err != nil {
		t.Fatal(err)
	}
	for name := range got {
		sameJSON(t, name, fixture[name], got[name])
	}
	if len(fixture) != len(got) {
		t.Errorf("フィクスチャの場面が違う: %d 件 vs %d 件", len(fixture), len(got))
	}
}
