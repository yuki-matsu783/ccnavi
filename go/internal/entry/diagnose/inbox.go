package diagnose

import (
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/sync/handoff"
)

// inboxRecord はユーザ待ちの分類（チケット管理画面の inbox）。形は docs/11_requirements/json-shapes.md「ボードの JSON」。
// チケットは上から最初に当てはまる 1 種類だけに入る: approval（承認待ち）→ review（レビュー待ちのフェーズ）→
// notice（attention のうち上の 2 つに入らないもの）→ flow_draft（attention ではなく、フローの下書きが「提案あり」。閉じた子は flow.DraftDiffers が偽にする）。
// handoff はチケットではなくツリーごとの取り込み待ちの数。拡張は数を並べるだけにする（分類を 2 か所に持たない）。
func inboxRecord(rows []*ticketRow, parents []*parentRow, pending []string, handoffs []any) *pyjson.Object {
	review := []any{}
	// レビュー待ちのフェーズを持つ親と、レビュー待ちの（親, フェーズ）。
	reviewing := map[string]bool{}
	waitingPhase := map[string]map[int]bool{}
	for _, p := range parents {
		for _, ph := range p.phases {
			if !ph.reviewWaiting {
				continue
			}
			review = append(review, pyjson.NewObject("parent", p.id, "phase", ph.number))
			reviewing[p.id] = true
			if waitingPhase[p.id] == nil {
				waitingPhase[p.id] = map[int]bool{}
			}
			waitingPhase[p.id][ph.number] = true
		}
	}
	approval := map[string]bool{}
	for _, id := range pending {
		approval[id] = true
	}
	notice, flowDraft := []string{}, []string{}
	for _, r := range rows {
		inReview := reviewing[r.id]
		if r.parent != "" {
			inReview = waitingPhase[r.parent][r.phase]
		}
		switch {
		case approval[r.id], r.attention && inReview:
			// approval と review に入れた（review はフェーズで数える）
		case r.attention:
			notice = append(notice, r.id)
		case r.flowDraft:
			flowDraft = append(flowDraft, r.id)
		}
	}
	return pyjson.NewObject(
		"approval", nonNil(pending),
		"review", review,
		"notice", notice,
		"handoff", handoffs,
		"flow_draft", flowDraft,
	)
}

// handoffWaiting はツリーごとの取り込み待ちの数（`user handoff --json` と同じ判定。数が 0 のツリーは載せない）。
// 拡張がそのツリーを --root にして打つのと同じく、ツリーごとに設定を読み直す。
func handoffWaiting(trees []tree.Tree) []any {
	out := []any{}
	for _, t := range trees {
		conf, _ := config.Load(t.Root)
		if n := handoff.Prepare(conf, t.Root, nil).Waiting(); n > 0 {
			out = append(out, pyjson.NewObject("tree", t.Root, "count", n))
		}
	}
	return out
}
