package phase

import (
	"fmt"
	"slices"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

// ゲートの表。計画のステップ・定義・引き受けた延期・子の宣言と状態・実績のリスク・マーカーの組み合わせを全部
// 作り、終わったか・レビューが要るか・止めているか・呼び名・止めたときの文が Python と同じことを見る。

type gateItem struct {
	Review   *bool `json:"review"`
	Deferred bool  `json:"deferred"`
}

type gateCase struct {
	Item     *gateItem      `json:"item"`
	Type     *bool          `json:"type"`
	Covered  []bool         `json:"covered"`
	Children [][2]any       `json:"children"`
	Risk     map[string]any `json:"risk"`
	Marks    []string       `json:"marks"`
}

func gateCases() []gateCase {
	yes, no := true, false
	items := []*gateItem{nil, {}, {Review: &yes}, {Review: &no}, {Deferred: true}}
	types := []*bool{nil, &yes, &no}
	covered := [][]bool{{}, {true}, {false}}
	children := [][][2]any{
		{},
		{{"done", nil}},
		{{"done", false}},
		{{"review", true}, {"done", false}},
		{{"doing", true}, {"done", nil}},
		{{"cancelled", true}},
		{{"cancelled", nil}, {"review", false}},
	}
	risks := []map[string]any{nil, {"points": 58, "level": "HIGH", "hits": []any{map[string]any{"detail": "行数が多い"}}},
		{"points": 3, "level": "LOW"}}
	markSets := [][]string{{}, {"requested"}, {"reviewed"}}
	var out []gateCase
	for _, i := range items {
		for _, ty := range types {
			for _, cv := range covered {
				for _, ch := range children {
					for _, r := range risks {
						for _, m := range markSets {
							out = append(out, gateCase{Item: i, Type: ty, Covered: cv, Children: ch, Risk: r, Marks: m})
						}
					}
				}
			}
		}
	}
	return out
}

func needOf(v any) ticket.Need {
	switch v {
	case true:
		return ticket.Needed
	case false:
		return ticket.Unneeded
	}
	return ticket.Unsaid
}

func (k gateCase) phase() *Phase {
	p := newPhase("i0001", 2)
	if k.Item != nil {
		p.Item = &ticket.PlanItem{Type: "design", Deferred: k.Item.Deferred}
		if k.Item.Review != nil {
			p.Item.Review = ticket.NeedOf(*k.Item.Review)
		}
	}
	if k.Type != nil {
		p.Type = &phases.PhaseType{ID: "design", Title: "設計", Review: *k.Type}
	}
	for i, c := range k.Children {
		t := &ticket.Ticket{ID: fmt.Sprintf("i0001-02-%02d", i+1), Parent: "i0001", Phase: 2, ReviewRequired: needOf(c[1])}
		p.Tickets = append(p.Tickets, t)
		p.States[t.ID] = place.State(c[0].(string))
		if k.Risk != nil {
			record := pyjson.NewObject("points", k.Risk["points"].(int)+i, "level", k.Risk["level"])
			if hits, ok := k.Risk["hits"]; ok {
				record.Set("hits", []any{pyjson.NewObject("detail", hits.([]any)[0].(map[string]any)["detail"])})
			}
			p.Risks[t.ID] = record
		}
	}
	for _, m := range k.Marks {
		p.Marks[marks.Kind(m)] = pyjson.NewObject()
	}
	p.CoveredReviews = k.Covered
	return p
}

func flag(b bool) string {
	if b {
		return "T"
	}
	return "F"
}

func TestGateMatchesPython(t *testing.T) {
	root := testkit.RepoRoot(t)
	cases := gateCases()
	want := golden.Load[struct {
		Rows  []string `json:"rows"`
		Holds []string `json:"holds"`
	}](t, "gate")
	if len(want.Rows) != len(cases) {
		t.Fatalf("行の数 %d、場合の数 %d", len(want.Rows), len(cases))
	}
	holds := map[string]bool{}
	for i, k := range cases {
		p := k.phase()
		declared := "-"
		if d := p.DeclaredReview(); d.Said() {
			declared = flag(d == ticket.Needed)
		}
		got := flag(p.Ended()) + declared + flag(p.ReviewRequired()) + flag(p.GateClosed()) + flag(p.ReviewWaiting()) +
			flag(p.RiskEscalates()) + flag(p.ReviewedOrSkipped()) + "|" + p.ReviewLabel() + "|" + p.RiskLine() + "|" + p.Label()
		if got != want.Rows[i] {
			t.Errorf("%+v: Go %v、Python %v", k, got, want.Rows[i])
		}
		if p.GateClosed() {
			holds[HoldReason(p, "Agent", root)] = true
			holds[HoldReason(p, "Bash", root)] = true
		}
	}
	var got []string
	for h := range holds {
		got = append(got, h)
	}
	slices.Sort(got)
	if !slices.Equal(got, want.Holds) {
		t.Errorf("止めたときの文が違う\nGo: %q\nPython: %q", got, want.Holds)
	}
}

// 止める条件の代表。表の答えを Python に任せる前に、約束そのものを名指しで残す。
func TestGateClosedPromises(t *testing.T) {
	yes, no := true, false
	tests := []struct {
		name string
		k    gateCase
		want bool
	}{
		{"レビュー要の定義で子が閉じたら止める", gateCase{Type: &yes, Children: [][2]any{{"done", nil}}}, true},
		{"レビュー済みなら止めない", gateCase{Type: &yes, Children: [][2]any{{"done", nil}}, Marks: []string{"reviewed"}}, false},
		{"依頼しただけでは止めたまま", gateCase{Type: &yes, Children: [][2]any{{"review", nil}}, Marks: []string{"requested"}}, true},
		{"作業中の子が残れば終わらない", gateCase{Type: &yes, Children: [][2]any{{"doing", nil}, {"done", nil}}}, false},
		{"取り消しだけのフェーズは終わらない", gateCase{Type: &yes, Children: [][2]any{{"cancelled", nil}}}, false},
		{"定義が要らないなら止めない", gateCase{Type: &no, Children: [][2]any{{"done", nil}}}, false},
		{"定義が要らなくても子が求めれば止める", gateCase{Type: &no, Children: [][2]any{{"done", true}}}, true},
		{"定義が要らなくても実績のリスクが高ければ止める",
			gateCase{Type: &no, Children: [][2]any{{"done", false}}, Risk: map[string]any{"points": 90, "level": "CRITICAL"}}, true},
		{"ステップの review: false が定義より優先する", gateCase{Item: &gateItem{Review: &no}, Type: &yes, Children: [][2]any{{"done", nil}}}, false},
		{"延期したフェーズは自分では止めない", gateCase{Item: &gateItem{Deferred: true}, Type: &yes, Children: [][2]any{{"done", true}}}, false},
		{"引き受けた延期が要れば止める", gateCase{Type: &no, Covered: []bool{true}, Children: [][2]any{{"done", nil}}}, true},
		{"根拠が無ければ子が要らないと明示しない限り止める", gateCase{Children: [][2]any{{"done", nil}}}, true},
		{"根拠が無く子が要らないと明示すれば止めない", gateCase{Children: [][2]any{{"done", false}}}, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := tt.k.phase().GateClosed(); got != tt.want {
				t.Errorf("GateClosed = %v", got)
			}
		})
	}
}

// 引き受けた側に渡す宣言は、覆っている分の定義が読めなければ要る（読めなくなったことでレビューを消さない）。
func TestCoveredReviewDefaultsToNeeded(t *testing.T) {
	no := false
	if !coveredReview(nil) {
		t.Error("番号のフェーズが無いのに要らないと読んだ")
	}
	if !coveredReview(newPhase("p", 1)) {
		t.Error("宣言が無いのに要らないと読んだ")
	}
	if coveredReview(gateCase{Type: &no}.phase()) {
		t.Error("要らないと宣言したのに要ると読んだ")
	}
}
