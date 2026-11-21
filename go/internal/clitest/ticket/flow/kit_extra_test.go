package flow

// このパッケージのテストが共有する道具のうち、ticketkitに無いもの。
// tests/ticket/test_phases.py の PhaseHarness の続き（フェーズ定義つきの親子・Chrome の要求・JSON の読み）。

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// phaseOpts は PhaseHarness と同じ木（フェーズ定義 samples.Phases を設定の置き場に置く）。
var phaseOpts = ticketkit.Options{Phases: samples.Phases}

// newTree は写しを使わない木（親のワークツリー i0001 まで）。
func newTree(t *testing.T) *Tree { t.Helper(); return New(t, phaseOpts) }

// cachedTree は build の結果を鍵ごとに取っておく木。鍵は build の中身を言い、引数で変わるなら鍵に入れる。
func cachedTree(t *testing.T, key string, build func(tr *Tree)) *Tree {
	t.Helper()
	return Cached(t, key, phaseOpts, build)
}

// child・childNoReview は親 i0001 のフェーズ phase の子の提案。
func child(name string, phase int, allow ...string) string {
	return samples.ChildText(name, ticketkit.ParentName, phase, allow)
}

func childNoReview(name string, phase int, allow ...string) string {
	return samples.ChildTextReview(name, ticketkit.ParentName, phase, allow, samples.Off)
}

// parentOf は計画（定義の名前の並び）だけの親の提案。
func parentOf(name string, plan ...string) string {
	return samples.ParentText(name, samples.Plan(plan...), samples.ParentOpts{})
}

// parentWith は計画と任意の指定つきの親の提案。
func parentWith(name string, plan []samples.Step, o samples.ParentOpts) string {
	return samples.ParentText(name, plan, o)
}

// FamilyPlan は親 i0001 を計画つきで提案して承認する（family）。feedback が nil でなければフィードバック計画も書く。
func (tr *Tree) FamilyPlan(plan []samples.Step, feedback []samples.Step) {
	tr.T.Helper()
	tr.Propose(ticketkit.ParentName, samples.ParentText(ticketkit.ParentName, plan, samples.ParentOpts{Feedback: feedback, Copy: tr.Phases}), "")
	tr.CommitParent("tickets")
	ticketkit.OK(tr.T, tr.Approve())
}

// ApproveChild は子の提案を親のワークツリーに書き、親がコミットし、承認する。
func (tr *Tree) ApproveChild(name string, phase int, allow ...string) {
	tr.T.Helper()
	tr.Propose(name, child(name, phase, allow...), "")
	tr.CommitParent("tickets")
	ticketkit.OK(tr.T, tr.Approve())
}

// CloseChild は子を閉じる（ticket finish）。
func (tr *Tree) CloseChild(name string) run.Result { return tr.Ccnavi("ticket", "finish", name) }

// MergeChild は子を親へ合流し、合流の記録を書く（merge）。
func (tr *Tree) MergeChild(name string) { tr.Merge(name) }

// Origin は origin（空の bare）を作り、親のブランチを push して、結果ファイル（fixture.json）を書く（remote）。
func (tr *Tree) Origin() ticketkit.Fixture {
	tr.T.Helper()
	bare := filepath.Join(tr.Root, "origin.git")
	tr.Git(tr.Root, "init", "--quiet", "--bare", bare)
	tr.Git(tr.Root, "remote", "add", "origin", bare)
	tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
	f := tr.Fixture()
	ticketkit.Write(tr.T, f.Path, `{"host": "fixture", "mr": {"number": 7, "url": "u/7", "draft": true}}`)
	return f
}

// RequestFor はフェーズ phase のレビュー依頼（prepare → requested）を通す（request。結果の投稿先は 1 つ）。
func (tr *Tree) RequestFor(f ticketkit.Fixture, phase string) run.Result {
	tr.T.Helper()
	body := ticketkit.Write(tr.T, filepath.Join(tr.Root, "body.md"), "見てほしい\n")
	prepared := tr.Ccnavi("--cwd", tr.ParentTree, "--phase", phase, "--body-file", body, "review", "prepare")
	if prepared.Code != 0 {
		return prepared
	}
	mr, _ := f.Data()["mr"].(map[string]any)
	raw, err := json.Marshal(map[string]any{"host": "fixture", "mr": mr, "url": "u/7#1", "created_at": "2026-01-01T00:00:00Z"})
	if err != nil {
		tr.T.Fatal(err)
	}
	result := ticketkit.Write(tr.T, filepath.Join(tr.Root, "posted.json"), string(raw))
	return tr.Ccnavi("--cwd", tr.ParentTree, "--phase", phase, "review", "requested", "--result", result)
}

// ConfirmFor はフェーズ phase の review confirm。
func (tr *Tree) ConfirmFor(f ticketkit.Fixture, phase string) run.Result {
	tr.T.Helper()
	return tr.Ccnavi("--cwd", tr.ParentTree, "--phase", phase, "review", "confirm", "--result", f.Path)
}

// Phase1Requested はデザイン 1 ステップの全体計画のフェーズ 1 を閉じて合流し、レビューを依頼したところまで進める。
// 返すのは結果ファイル。
func (tr *Tree) Phase1Requested(plan ...string) ticketkit.Fixture {
	tr.T.Helper()
	if len(plan) == 0 {
		plan = []string{"design"}
	}
	tr.FamilyPlan(samples.Plan(plan...), nil)
	tr.ApproveChild("i0001-01-01", 1, "wip/design/*")
	tr.RunChild("i0001-01-01", ticketkit.File{Path: "wip/design/plan.md", Text: "d\n"})
	ticketkit.OK(tr.T, tr.CloseChild("i0001-01-01"))
	tr.CommitParent("close 01")
	tr.Merge("i0001-01-01")
	f := tr.Origin()
	ticketkit.OK(tr.T, tr.RequestFor(f, "1"))
	return f
}

// BoardPhase はボードの JSON の親のフェーズ 1 つを (gate_closed, review_waiting, マーカーの種類) で返す。
func (tr *Tree) BoardPhase(number int, parent string) (gateClosed, reviewWaiting bool, marks []string) {
	tr.T.Helper()
	board := ticketkit.ParseJSON(tr.T, ticketkit.OK(tr.T, tr.Ccnavi("--explain", "--json")).Stdout)
	for _, p := range jl(board, "parents") {
		if js(p, "ticket") != parent {
			continue
		}
		for _, ph := range jl(p, "phases") {
			if int(jf(ph, "number")) == number {
				for k := range jm(ph, "marks") {
					marks = append(marks, k)
				}
				slices.Sort(marks)
				return jb(ph, "gate_closed"), jb(ph, "review_waiting"), marks
			}
		}
	}
	tr.T.Fatalf("フェーズ %d が無い", number)
	return
}

// ---- JSON の読み（Python の dict の添字の代わり。キーは string、配列の添字は int）

func jv(v any, path ...any) any {
	for _, p := range path {
		switch k := p.(type) {
		case string:
			m, _ := v.(map[string]any)
			v = m[k]
		case int:
			l, _ := v.([]any)
			if k < 0 || k >= len(l) {
				return nil
			}
			v = l[k]
		}
	}
	return v
}

func js(v any, path ...any) string  { s, _ := jv(v, path...).(string); return s }
func jb(v any, path ...any) bool    { b, _ := jv(v, path...).(bool); return b }
func jf(v any, path ...any) float64 { f, _ := jv(v, path...).(float64); return f }
func jl(v any, path ...any) []any   { l, _ := jv(v, path...).([]any); return l }
func jm(v any, path ...any) map[string]any {
	m, _ := jv(v, path...).(map[string]any)
	return m
}

// strs は []any を []string にする。
func strs(l []any) []string {
	out := make([]string, len(l))
	for i, x := range l {
		out[i], _ = x.(string)
	}
	return out
}

// ---- 結果の見方

func decision(t testing.TB, r run.Result) string { return ticketkit.Decision(t, r) }

// contextOf はモデルへ渡した文（判定とは別の欄）。
func contextOf(t testing.TB, r run.Result) string {
	t.Helper()
	if strings.TrimSpace(r.Stdout) == "" {
		return ""
	}
	var out struct {
		Hook struct {
			Context string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		t.Fatalf("hook の出力が JSON でない: %q", r.Stdout)
	}
	return out.Hook.Context
}

func wantCode(t testing.TB, r run.Result, code int) {
	t.Helper()
	if r.Code != code {
		t.Fatalf("終了コード %d（%d のはず）\nstdout: %s\nstderr: %s", r.Code, code, r.Stdout, r.Stderr)
	}
}

func readFile(t testing.TB, path string) string { return ticketkit.Read(t, path) }

func removeFile(t testing.TB, path string) {
	t.Helper()
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
}

// planOf は定義の名前だけの計画。
func planOf(names ...string) []samples.Step { return samples.Plan(names...) }

func mustJSONText(v any) string {
	raw, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(raw)
}

// noCopy・noCopyFeedback は計画を持たない親（フェーズ定義のコピーも書かない）。後者は feedback: だけを持つ。
func noCopy() samples.ParentOpts { return samples.ParentOpts{NoCopy: true} }

func noCopyFeedback(names ...string) samples.ParentOpts {
	return samples.ParentOpts{NoCopy: true, Feedback: samples.Plan(names...)}
}
