package flow

// decide の適用（実行ファイル側）の取りこぼしを、sh のスタブでなく本物の `ccnavi user reviewed --accept-unresolved` と
// 操作記録で見る。元: tests/ticket/test_decide_exe.py。
//
//  1. AT43: ホストに返信・解決を書いたあとの取り直しで、選んだ指摘の本文が変わっていたら、続きの子もマーカーも置かず、
//     解決した分を操作記録の applied に残して止まる（ボードは mismatch で見せ直す）
//  2. AT13: fix で作る続きの子に ccnavi_op が書かれる。同じ操作の打ち直しで 2 つ目の子を作らない
//  3. AT24: 引き継ぎ未完了の操作記録は、親の finish・ready・片付け（ops retire）で消えず、status が言い続ける
import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/oplog"
)

const twoChoices = `{"u1":"keep","u2":"fix"}`

// planned は受け入れの計画まで書く（--accept-plan）。返すのは (結果ファイル, ダイジェスト, 計画のパス, 操作ID)。
func planned(t *testing.T, tr *Tree, f ticketkit.Fixture) (digest, plan, op string) {
	t.Helper()
	f.SetThreads(
		ticketkit.Thread{ID: "t1", URL: "u1", Path: "src/a/x.py", Body: "命名"},
		ticketkit.Thread{ID: "t2", URL: "u2", Path: "src/b/y.py", Body: "テスト"},
	)
	digest = js(ticketkit.ParseJSON(t, tr.Decide(f, "--preview", "--json").Stdout), "digest")
	plan = filepath.Join(tr.State, "plan.json")
	ticketkit.OK(t, tr.Decide(f, "--yes", twoChoices, "--digest", digest, "--json", "--accept-plan", plan))
	return digest, plan, js(ticketkit.ReadJSON(t, plan), "op")
}

// hostResolved は sh が返信・解決したあとの、取り直した結果（ホストの実状態）を作る。
func hostResolved(f ticketkit.Fixture, resolved []string, edit map[string]string) {
	f.Update(func(d map[string]any) {
		for _, th := range d["threads"].([]any) {
			m := th.(map[string]any)
			id := m["id"].(string)
			if slices.Contains(resolved, id) {
				m["resolved"] = true
			}
			if body, ok := edit[id]; ok {
				m["body"] = body
			}
		}
	})
}

func opRecord(t *testing.T, tr *Tree, op string) map[string]any {
	return ticketkit.ReadJSON(t, filepath.Join(tr.State, "ops", op+".json"))
}

func doneArgs(digest, plan string) []string {
	return []string{"--yes", twoChoices, "--digest", digest, "--json", "--accept-done", plan}
}

func TestAChangedBodyAfterTheHostWritePlacesNothingAndKeepsTheResolvedPart(t *testing.T) {
	cases := []struct {
		name    string
		edit    map[string]string
		changed string
	}{
		{"the resolved thread", map[string]string{"t1": "命名（書き換えた）"}, "u1"},
		{"the fix thread", map[string]string{"t2": "テスト（書き換えた）"}, "u2"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr, f := RequestedTree(t)
			digest, plan, op := planned(t, tr, f)
			// sh が u1 を解決した。2 回目の取得で、本文が変わっている。
			hostResolved(f, []string{"t1"}, c.edit)
			done := ticketkit.Fails(t, tr.Decide(f, doneArgs(digest, plan)...))
			answer := ticketkit.ParseJSON(t, done.Stdout)
			if !jb(answer, "mismatch") || jb(answer, "ok") {
				t.Errorf("answer = %v", answer)
			}
			if got := strs(jl(answer, "changed")); !slices.Equal(got, []string{c.changed}) {
				t.Errorf("changed = %v", got)
			}
			ticketkit.Has(t, done.Stderr, "見せた指摘と今の指摘が違う。何も置いていない")
			phases := filepath.Join(tr.Approved, "phases", "i0001")
			// 続きの子もマーカーも置かない。子は動かさない。
			if ticketkit.Exists(tr.ApprovedPath("doing", "i0001-01-03")) || ticketkit.Exists(filepath.Join(phases, "1.reviewed")) || !ticketkit.Exists(filepath.Join(phases, "1.requested")) {
				t.Error("続きの子かマーカーが動いた")
			}
			for _, ch := range []string{"i0001-01-01", "i0001-01-02"} {
				if ticketkit.Exists(tr.ApprovedPath("done", ch)) {
					t.Errorf("%s が done/ へ動いた", ch)
				}
			}
			// ホストで解決した分は、操作記録の applied に残る。適用（local）は立たない。
			applied := jm(opRecord(t, tr, op), "applied")
			if got := strs(jl(applied, "accepted_on_host")); !slices.Equal(got, []string{"u1"}) {
				t.Errorf("accepted_on_host = %v", got)
			}
			if _, ok := applied["local"]; ok {
				t.Error("applied に local が立った")
			}
			// 設計との違い: 受け入れ台帳（accepted.json）には書かない。
			if ticketkit.Exists(filepath.Join(phases, "accepted.json")) {
				t.Error("accepted.json が書かれた")
			}
		})
	}
}

func TestTheFollowupChildCarriesTheOperationID(t *testing.T) {
	t.Parallel()
	tr, f := RequestedTree(t)
	digest, plan, op := planned(t, tr, f)
	hostResolved(f, []string{"t1"}, nil)
	done := ticketkit.OK(t, tr.Decide(f, doneArgs(digest, plan)...))
	if js(ticketkit.ParseJSON(t, done.Stdout), "followup") != "i0001-01-03" {
		t.Errorf("stdout = %s", done.Stdout)
	}
	ticketkit.Has(t, ticketkit.Read(t, tr.ApprovedPath("doing", "i0001-01-03")), "ccnavi_op: "+op)
	if js(opRecord(t, tr, op), "applied", "followup") != "i0001-01-03" {
		t.Errorf("record = %v", opRecord(t, tr, op))
	}
}

func followups(t *testing.T, tr *Tree) []string {
	entries, err := os.ReadDir(filepath.Join(tr.Approved, "doing"))
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "i0001-01-") {
			out = append(out, e.Name())
		}
	}
	slices.Sort(out)
	return out
}

// 同じ操作の打ち直し。印が戻ったのに子が残った形は 2 つ目の子を作らず、印が消えたままの打ち直しは断る。
func TestReplayingTheSameOperationMakesNoSecondChild(t *testing.T) {
	t.Run("the marker came back", func(t *testing.T) {
		t.Parallel()
		tr, f := RequestedTree(t)
		digest, plan, _ := planned(t, tr, f)
		hostResolved(f, []string{"t1"}, nil)
		marker := tr.RequestedMark()
		saved := ticketkit.Read(t, marker)
		args := doneArgs(digest, plan)
		ticketkit.OK(t, tr.Decide(f, args...))
		ticketkit.Write(t, marker, saved)
		again := ticketkit.OK(t, tr.Decide(f, args...))
		if got := followups(t, tr); !slices.Equal(got, []string{"i0001-01-03.md"}) {
			t.Errorf("同じ操作ID の子があるのに、別の子を作った: %v", got)
		}
		if js(ticketkit.ParseJSON(t, again.Stdout), "followup") != "i0001-01-03" {
			t.Errorf("stdout = %s", again.Stdout)
		}
	})
	t.Run("the marker is gone", func(t *testing.T) {
		t.Parallel()
		tr, f := RequestedTree(t)
		digest, plan, _ := planned(t, tr, f)
		hostResolved(f, []string{"t1"}, nil)
		args := doneArgs(digest, plan)
		ticketkit.OK(t, tr.Decide(f, args...))
		again := ticketkit.Fails(t, tr.Decide(f, args...))
		ticketkit.Has(t, again.Stderr, "依頼の記録がありません")
		if got := followups(t, tr); !slices.Equal(got, []string{"i0001-01-03.md"}) {
			t.Errorf("子が増えた: %v", got)
		}
	})
}

// ---- AT24

func (tr *Tree) stageSet(op, stage, state string) {
	tr.T.Helper()
	ticketkit.OK(tr.T, tr.Ccnavi("ops", "stage-set", op, stage, state))
}

func (tr *Tree) hasRecord(op string) bool {
	return ticketkit.Exists(filepath.Join(tr.State, "ops", op+".json"))
}

func (tr *Tree) status() string { return ticketkit.OK(tr.T, tr.Ccnavi("ticket", "status")).Stdout }

func (tr *Tree) pending() []string {
	var ids []string
	for _, l := range ticketkit.Lines(ticketkit.OK(tr.T, tr.Ccnavi("ops", "pending")).Stdout) {
		ids = append(ids, strings.Fields(l)[0])
	}
	return ids
}

func (tr *Tree) leaveAnUnfinishedDecide() string {
	tr.T.Helper()
	op := samples.BeginRecord(tr.T, tr.State, oplog.KindDecide, oplog.Options{
		Repo: pyjson.NewObject("host", "fixture", "mr_url", "u/7"), Parent: "i0001", Phase: 1, MR: 7,
		Stages: []oplog.StageSpec{{ID: "reply", Kind: oplog.StageReply}, {ID: "note", Kind: oplog.StageComment}},
	})
	tr.stageSet(op, "reply", "sent")
	tr.stageSet(op, "note", "unknown")
	return op
}

func TestTheUnfinishedRecordSurvivesTheFinishAndTheReadyAndTheCleanup(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.FamilyPlan(planOf("design"), nil)
	tr.ApproveChild("i0001-01-01", 1, "wip/design/*")
	f := tr.Fixture()
	tr.RunChild("i0001-01-01", ticketkit.File{Path: "wip/design/plan.md", Text: "d\n"})
	ticketkit.OK(t, tr.CloseChild("i0001-01-01"))
	tr.CommitParent("close 01")
	tr.Merge("i0001-01-01")
	tr.Origin()
	tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
	ticketkit.OK(t, tr.RequestFor(f, "1"))
	ticketkit.OK(t, tr.ConfirmFor(f, "1"))
	op := tr.leaveAnUnfinishedDecide()
	ticketkit.Has(t, tr.status(), "引き継ぎ未完了")
	// 親の finish は進む。status は言い続ける。
	tr.StartParent()
	tr.Propose("i0001", samples.ParentText("i0001", planOf("design"), samples.ParentOpts{Feedback: []samples.Step{}}), "")
	ticketkit.OK(t, tr.Agree("y"))
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", "i0001"))
	ticketkit.Has(t, tr.status(), "引き継ぎ未完了")
	tr.CommitParent("状態の移動")
	tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
	// ready も進む。sh が ready のあとに打つ片付け（ops retire）も、未完了の記録は消さない。
	ticketkit.OK(t, tr.Ccnavi("--cwd", tr.ParentTree, "review", "ready", "--result", f.Path))
	ticketkit.OK(t, tr.Ccnavi("ops", "retire", "i0001"))
	if !tr.hasRecord(op) || !slices.Equal(tr.pending(), []string{op}) {
		t.Errorf("記録が消えた: has=%v pending=%v", tr.hasRecord(op), tr.pending())
	}
	ticketkit.Has(t, tr.status(), "引き継ぎ未完了")
	ticketkit.Has(t, tr.status(), op[:8])
}

func TestTheCleanupRemovesOnlyTheFinishedRecordsOfTheParent(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	done := samples.BeginRecord(t, tr.State, oplog.Kind("comment"), oplog.Options{
		Repo: pyjson.NewObject(), Parent: "i0001", Stages: []oplog.StageSpec{{ID: "c", Kind: oplog.StageComment}},
	})
	tr.stageSet(done, "c", "sent")
	left := tr.leaveAnUnfinishedDecide()
	ticketkit.OK(t, tr.Ccnavi("ops", "retire", "i0001"))
	if tr.hasRecord(done) || !tr.hasRecord(left) {
		t.Errorf("done 残り=%v left 残り=%v", tr.hasRecord(done), tr.hasRecord(left))
	}
}
