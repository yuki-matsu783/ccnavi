package union

import (
	"ccnavi/internal/harness/configws"
	"encoding/json"
	"strings"
	"testing"
)

// 設定の受入テスト。phases は共通 + 1 レイヤーの和。
//
// 土台の共通レイヤーは phases を持たない（統合結果を見るテストが自分で置く）。自身のレイヤーに `docs`、lib のレイヤーに
// `design` / `build` / `release` がある。使うフェーズ定義は、親の承認済みチケットの `project:` で決まる。
// lib 向けの提案は `projects/lib/wip/proposals/` に置き、ワークスペース向けは `wip/proposals/` に置く。

// 別の id で title だけ自身のレイヤーの design（設計）と重なる。
const libPhasesTitleOverlap = `version: 1
phases:
  build:
    kind: work
    title: 設計
    review: false
    scope: ["src/*"]
`

// 自身のレイヤーに置く、lib と同じ design の id を別の中身で持つ定義。
const ownPhasesDesign = `version: 1
phases:
  design:
    kind: work
    title: 設計（自身）
    review: false
    scope: ["wip/*"]
`

type sumType struct {
	ID     string `json:"id"`
	Title  string `json:"title"`
	Source string `json:"source"`
}

// sums は `--explain --json` の和（名前 → 使えるフェーズ定義）。
func (w *ws) sums() map[string][]sumType {
	w.T.Helper()
	r := w.Ccnavi("--explain", "--json")
	var body struct {
		Sums []struct {
			Name   string `json:"name"`
			Phases struct {
				Types []sumType `json:"types"`
			} `json:"phases"`
		} `json:"sums"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &body); err != nil {
		w.T.Fatalf("--explain --json: %v\n%s", err, r.Stdout)
	}
	out := map[string][]sumType{}
	for _, s := range body.Sums {
		out[s.Name] = s.Phases.Types
	}
	return out
}

func sourcesOf(types []sumType) map[string]string {
	out := map[string]string{}
	for _, ty := range types {
		out[ty.ID] = ty.Source
	}
	return out
}

func idsOf(types []sumType) []string {
	var out []string
	for _, ty := range types {
		out = append(out, ty.ID)
	}
	return out
}

// phaseProblems は phases の Problem。layer を渡すと、そのレイヤーのものだけ。
func (w *ws) phaseProblems(severity, layer string) []configws.Problem {
	w.T.Helper()
	where := ""
	if layer != "" {
		where = configws.ProjectWhere(layer)
	}
	var out []configws.Problem
	for _, pr := range w.Problems(severity, where) {
		if strings.Contains(pr.Where, "phases") {
			out = append(out, pr)
		}
	}
	return out
}

func (w *ws) mustApprove() {
	w.T.Helper()
	r := w.Approve()
	if r.Code != 0 {
		w.T.Fatalf("承認が通らない: code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}

func (w *ws) mustRefuse(name string) {
	w.T.Helper()
	r := w.Approve()
	if r.Code == 0 {
		w.T.Fatalf("承認が通った: %s%s", r.Stdout, r.Stderr)
	}
	if configws.Exists(w.ApprovedCopy(name)) {
		w.T.Errorf("拒んだのに承認済みチケット %s が置かれた", name)
	}
}

func TestAPlanCanNameATypeFromTheProjectLayer(t *testing.T) {
	t.Parallel()
	w := newWS(t, configws.KindBase)
	w.Propose("i0001", configws.TicketText("i0001", configws.TicketOpts{Project: "lib", Plan: []string{"design", "release"}, Allow: []string{"src/*"}}), "lib")
	w.mustApprove()
	if !configws.Exists(w.ApprovedCopy("i0001")) {
		t.Error("承認済みチケットが置かれていない")
	}
	if !strings.Contains(w.Ccnavi("--explain").Stdout, "リリース") {
		t.Error("--explain に lib の定義（リリース）が出ない")
	}
}

func TestTheLayerFollowsTheProjectOfTheParent(t *testing.T) {
	t.Parallel()
	// 空の `project:` は自身のレイヤー。プロジェクトのレイヤーの定義は他から指せない。
	w := newWS(t, configws.KindBase)
	w.Propose("i0002", configws.TicketText("i0002", configws.TicketOpts{Plan: []string{"docs"}, Allow: []string{"docs/*"}}), "")
	w.mustApprove()

	w.Propose("i0003", configws.TicketText("i0003", configws.TicketOpts{Plan: []string{"build"}, Allow: []string{"src/*"}}), "")
	refused := w.Approve()
	if refused.Code == 0 || !strings.Contains(refused.Stderr, "build") {
		t.Errorf("lib の build を自身のレイヤーから指せてしまう: code %d\n%s", refused.Code, refused.Stderr)
	}
	if configws.Exists(w.ApprovedCopy("i0003")) {
		t.Error("拒んだのに承認済みチケットが置かれた")
	}

	if err := configws.RemoveFile(w.ProposalPath("i0003", "todo")); err != nil {
		t.Fatal(err)
	}
	w.Propose("i0004", configws.TicketText("i0004", configws.TicketOpts{Project: "lib", Plan: []string{"docs"}, Allow: []string{"docs/*"}}), "lib")
	w.mustRefuse("i0004")
}

func TestTheSameIdInTwoLayersIsNotAConflict(t *testing.T) {
	t.Parallel()
	// 自身のレイヤーとプロジェクトのレイヤーどうしは足さないので、その間の衝突は無い。足すのは共通レイヤーと親の `project:` が
	// 指すレイヤーだけ（その和の苦情は Go の phases の TestLayerTypes が見る）。同じ design を別の中身で持っても、title が
	// 重なっても、全欄が同じでも、error にも info にもならず、lib 向けの親は lib の design を使う。
	w := newWS(t, configws.KindBase)
	configws.WritePhases(t, w.Root, ownPhasesDesign)
	for _, name := range []string{"lib phases", "title overlap", "copied from common"} {
		text := map[string]string{"lib phases": configws.LibPhasesText, "title overlap": libPhasesTitleOverlap, "copied from common": configws.CommonPhasesText}[name]
		configws.WritePhases(t, w.Lib, text)
		if got := w.phaseProblems("error", ""); len(got) != 0 {
			t.Errorf("%s: error が出る: %+v", name, got)
		}
		if got := w.phaseProblems("info", ""); len(got) != 0 {
			t.Errorf("%s: info が出る: %+v", name, got)
		}
	}
	configws.WritePhases(t, w.Lib, configws.LibPhasesText)
	if got := w.phaseProblems("warn", ""); len(got) != 0 {
		t.Errorf("warn が出る: %+v", got)
	}
	w.Propose("i0001", configws.TicketText("i0001", configws.TicketOpts{Project: "lib", Plan: []string{"design"}, Allow: []string{"wip/design/*"}}), "lib")
	w.mustApprove()
	sums := w.sums()
	if len(sums["lib"]) == 0 || sums["lib"][0].Title != "設計" {
		t.Errorf("lib は lib の design を使う: %+v", sums["lib"])
	}
	if len(sums["self"]) == 0 || sums["self"][0].Title != "設計（自身）" {
		t.Errorf("self は自身の design を使う: %+v", sums["self"])
	}
}

func TestACommonPhasesFileIsNotAnErrorAndIsUsed(t *testing.T) {
	t.Parallel()
	// 共通レイヤーの phases.yml は置ける。--lint は error を出さず、`plan:` が共通レイヤーの定義を指せる。
	w := newWS(t, configws.KindBase)
	configws.Write(t, w.Phases, configws.CommonPhasesText)

	if got := w.phaseProblems("error", ""); len(got) != 0 {
		t.Errorf("error が出る: %+v", got)
	}
	w.Propose("i0001", configws.TicketText("i0001", configws.TicketOpts{Plan: []string{"design"}, Allow: []string{"wip/design/*"}}), "")
	w.mustApprove()
	if !configws.Exists(w.ApprovedCopy("i0001")) {
		t.Error("承認済みチケットが置かれていない")
	}
	if strings.Contains(w.Ccnavi("--explain").Stdout, "共通には置けない") {
		t.Error("--explain が共通には置けないと言う")
	}
	w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Root, "src", "a.py")})
	if _, has := w.LastRecord()["fallback"]; has {
		t.Error("fallback が付いた")
	}
}

func TestOnlyTheCommonLayerHasPhases(t *testing.T) {
	t.Parallel()
	// レイヤーに phases.yml が無くても、共通レイヤーの定義だけで `plan:` が読める。
	w := newWS(t, configws.KindBase)
	configws.Write(t, w.Phases, configws.CommonPhasesText)
	if err := configws.RemoveFile(configws.LayerPath(w.Root, "phases")); err != nil {
		t.Fatal(err)
	}

	if got := w.phaseProblems("error", ""); len(got) != 0 {
		t.Errorf("error が出る: %+v", got)
	}
	w.Propose("i0001", configws.TicketText("i0001", configws.TicketOpts{Plan: []string{"design"}, Allow: []string{"wip/design/*"}}), "")
	w.mustApprove()
	// app は `.ccnavi/` を持たないが、共通レイヤーの定義は使える。
	w.Propose("i0002", configws.TicketText("i0002", configws.TicketOpts{Project: "app", Plan: []string{"design"}, Allow: []string{"wip/design/*"}}), "app")
	w.mustApprove()
}

func TestCommonAndLayerTypesAddUp(t *testing.T) {
	t.Parallel()
	// 共通レイヤーの定義とレイヤーの定義を足す。1 つの計画で両方を使える。
	w := newWS(t, configws.KindBase)
	configws.Write(t, w.Phases, configws.CommonPhasesText)
	w.Propose("i0001", configws.TicketText("i0001", configws.TicketOpts{Plan: []string{"design", "docs"}, Allow: []string{"wip/design/*", "docs/*"}}), "")
	w.mustApprove()

	sums := w.sums()
	got := sourcesOf(sums["self"])
	if len(got) != 2 || got["design"] != "common" || got["docs"] != "self" {
		t.Errorf("self の定義と出どころ: %v", got)
	}
	// 自身のレイヤーの定義はプロジェクトには足さない。
	if ids := idsOf(sums["app"]); len(ids) != 1 || ids[0] != "design" {
		t.Errorf("app の定義: %v", ids)
	}
}

func TestFillPhasesTakesTypesFromBothLayers(t *testing.T) {
	t.Parallel()
	// `--plan-order <親> --fill-phases` は共通レイヤーとレイヤーの和からコピーする。
	w := newWS(t, configws.KindBase)
	configws.Write(t, w.Phases, configws.CommonPhasesText)
	text := configws.TicketText("i0001", configws.TicketOpts{Plan: []string{"design", "docs"}, Allow: []string{"wip/design/*", "docs/*"}})
	tree := w.Worktree(w.Root, "i0001")
	proposal := configws.Write(t, p(tree, "wip", "proposals", "todo", "i0001.md"), text)

	w.MustOK(w.Ccnavi("--plan-order", "i0001", "--fill-phases"), "--fill-phases")
	filled := configws.Read(t, proposal)
	for _, want := range []string{"設計", "文書"} {
		if !strings.Contains(filled, want) {
			t.Errorf("コピーに %q が無い", want)
		}
	}
	w.mustApprove()
}

func TestTheSameIdWithADifferentBodyAcrossCommonIsAnError(t *testing.T) {
	t.Parallel()
	// 共通レイヤーと同じ id で中身が違う定義は error。衝突した定義だけを破棄し、共通の定義を使う。そのレイヤーのほかの定義は生きている。
	w := newWS(t, configws.KindBase)
	configws.Write(t, w.Phases, configws.CommonPhasesText)
	configws.WritePhases(t, w.Root, ownPhasesDesign+"  docs:\n    kind: work\n    title: 文書\n    review: false\n    scope: [\"docs/*\"]\n")

	var errs []configws.Problem
	for _, pr := range w.phaseProblems("error", "") {
		if strings.HasPrefix(pr.Where, "(self)") {
			errs = append(errs, pr)
		}
	}
	found := false
	for _, pr := range errs {
		found = found || strings.Contains(pr.Where+pr.Detail, "design")
	}
	if !found {
		t.Errorf("design の衝突を error で言わない: %+v", errs)
	}
	// 破棄するのは衝突した design だけ。同じレイヤーの docs は使える。
	w.Propose("i0001", configws.TicketText("i0001", configws.TicketOpts{Plan: []string{"docs"}, Allow: []string{"docs/*"}}), "")
	w.mustApprove()
	if !configws.Exists(w.ApprovedCopy("i0001")) {
		t.Error("承認済みチケットが置かれていない")
	}
	// design は共通レイヤーの定義が使われる。
	byID := map[string]sumType{}
	for _, ty := range w.sums()["self"] {
		byID[ty.ID] = ty
	}
	if byID["design"].Source != "common" || byID["design"].Title != "設計" || byID["docs"].Source != "self" {
		t.Errorf("self の定義: %+v", byID)
	}
	// コピーが自身のレイヤーの design（中身違い）なら、承認で止まる。
	ownCopy := configws.TicketText("i0002", configws.TicketOpts{Plan: []string{"design"}, Allow: []string{"wip/*"}})
	configws.Write(t, p(w.Root, "wip", "proposals", "todo", "i0002.md"), configws.InsertPhases(ownCopy, ownPhasesDesign))
	w.mustRefuse("i0002")
}

func TestABrokenCommonLayerLeavesTheLayerTypesInUse(t *testing.T) {
	t.Parallel()
	// 共通レイヤーの phases.yml が読めなくても、レイヤーの定義だけで `plan:` が読める。
	// 共通の定義は空として扱うので、共通にしか無い定義を使う計画は承認で止まる。
	w := newWS(t, configws.KindBase)
	configws.Write(t, w.Phases, "version: 1\nphases: [\n")

	if !configws.AnyWhere(w.phaseProblems("error", ""), "(phases)") {
		t.Error("共通レイヤーの phases.yml の不正を error で言わない")
	}
	if got := w.phaseProblems("error", "lib"); len(got) != 0 {
		t.Errorf("lib の error が出る: %+v", got)
	}
	w.Propose("i0001", configws.TicketText("i0001", configws.TicketOpts{Plan: []string{"docs"}, Allow: []string{"docs/*"}}), "")
	w.mustApprove()
	if !configws.Exists(w.ApprovedCopy("i0001")) {
		t.Error("承認済みチケットが置かれていない")
	}
	if got := sourcesOf(w.sums()["self"]); len(got) != 1 || got["docs"] != "self" {
		t.Errorf("self の定義: %v", got)
	}

	w.Propose("i0002", configws.TicketText("i0002", configws.TicketOpts{Plan: []string{"design"}, Allow: []string{"wip/design/*"}}), "")
	w.mustRefuse("i0002")
}

func TestScopeStaysRelativeToTheWorktree(t *testing.T) {
	t.Parallel()
	// `scope` はワークツリーのルートからの相対のまま。定義の超過は承認を拒まず、承認画面の「編集対象としているが」に出る
	// （設計 approve-carry 3.1）。相対で読めていれば、`src/a/*` は定義 build の `src/*` に入り、`docs/*` だけが出る。
	w := newWS(t, configws.KindBase)
	w.Propose("i0001", configws.TicketText("i0001", configws.TicketOpts{Project: "lib", Plan: []string{"build"}, Allow: []string{"src/*", "docs/*"}}), "lib")
	w.Propose("i0001-01-01", configws.TicketText("i0001-01-01", configws.TicketOpts{Project: "lib", Parent: "i0001", Phase: 1, Allow: []string{"docs/*"}}), "lib")
	w.Propose("i0001-01-02", configws.TicketText("i0001-01-02", configws.TicketOpts{Project: "lib", Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}}), "lib")
	approved := w.Approve()
	w.MustOK(approved, "承認")
	for _, want := range []string{"編集対象としているが", "`docs/*` は定義"} {
		if !strings.Contains(approved.Stdout, want) {
			t.Errorf("承認の出力に %q が無い: %s", want, approved.Stdout)
		}
	}
	if strings.Contains(approved.Stdout, "`src/a/*` は定義") {
		t.Errorf("src/a/* は定義の範囲に入るはず: %s", approved.Stdout)
	}
	for _, name := range []string{"i0001-01-01", "i0001-01-02"} {
		if !configws.Exists(w.ApprovedCopy(name)) {
			t.Errorf("承認済みチケット %s が置かれていない", name)
		}
	}
}

func TestABrokenProjectPhasesIsAnErrorAndTheLayerIsEmpty(t *testing.T) {
	t.Parallel()
	// 不正なレイヤーの phases は空 + --lint error。`plan:` は読めない。
	w := newWS(t, configws.KindBase)
	configws.Write(t, configws.LayerPath(w.Lib, "phases"), "version: 1\nphases: [\n")

	if len(w.phaseProblems("error", "lib")) == 0 {
		t.Error("lib の phases の不正を error で言わない")
	}
	w.Propose("i0001", configws.TicketText("i0001", configws.TicketOpts{Project: "lib", Plan: []string{"design"}, Allow: []string{"wip/*"}}), "lib")
	if w.Approve().Code == 0 {
		t.Error("読めないレイヤーの定義を指す計画が通った")
	}
}
