package layers

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/configws"
)

// `--project-phases-file <名前>=<パス>` と `--project-risk-file <名前>=<パス>` の受入テスト
// 。
//
// VS Code 拡張のフェーズ管理画面とリスク管理画面が、編集中の設定（自身かプロジェクト）の定義を保存せずに、
// 共通設定と合わせて検証するための差し替え。config 単体の形式だけでなく、合成したあとの
// 表示名の重なり・同じ id で中身が違う定義・閾値の逆転・同 id の衝突・`script:` の存在確認（解決先はその設定の git プロジェクトルート）を見る。
// 土台は configws の雛形（ワークスペースの設定に `docs`「文書」、lib の設定に `design`「設計」/ `build` / `release`。
// 共通設定に `big-diff` と `levels: {medium: 20, high: 40, critical: 70}`、lib の設定に `schema` と `levels: {critical: 50}`。
// ワークスペースの設定と app は risks.yml を持たない）。

// lib の設定の build の表示名を、同じ 1 本の中の design と同じ「設計」にしたもの。
var libTitleOverlap = strings.Replace(configws.LibPhasesText, "title: ビルド", "title: 設計", 1)

const (
	// 同じ 1 本の定義と id も表示名も重ならない定義。`phases:` の続きに繋げる。
	notesType = "  notes:\n    kind: work\n    title: メモ\n    review: false\n    scope: inherit\n"
	// 定義の無い設定。実行ファイルは読めないとして error にする（拡張はこの形を書き出さない）。
	emptyLayer = "version: 1\nphases: {}\n"

	// config 単体では読めるが、共通の medium 20 と合わせると high 10 < medium 20 で逆転する。
	invertingRisk = "version: 1\nlevels: {high: 10}\n"
	// 共通の `big-diff` と同じ id で中身が違う。
	clashingRisk = "version: 1\nfactors:\n  - {id: big-diff, points: 99, lines_over: 1, message: 別の行数}\n"

	scriptFactor = "version: 1\nfactors:\n  - {id: counted, points: 5, script: .ccnavi/scripts/count.sh, message: 数えた}\n"
)

type fileWS struct{ *configws.WS }

func newFileWS(t *testing.T) fileWS { return fileWS{configws.NewWS(t, configws.KindBase)} }

// layerProblems は、その深刻度・出どころ・種類（`(phases)` / `(risk)`）の Problem。
func (w fileWS) layerProblems(kind, severity, where string, args ...string) []configws.Problem {
	w.T.Helper()
	var out []configws.Problem
	for _, p := range w.Problems(severity, where, args...) {
		if strings.Contains(p.Where, "("+kind+")") {
			out = append(out, p)
		}
	}
	return out
}

// edited は診断に渡す差し替えファイルを置く。置き場は ws/tmp（設定の git プロジェクトルートの外）。
func (w fileWS) edited(name, text string) string {
	return configws.Write(w.T, filepath.Join(w.Root, "tmp", name), text)
}

func detailHas(ps []configws.Problem, sub string) bool { return configws.AnyDetail(ps, sub) }

func TestProjectPhasesFileSwapsOneLayersPhaseTypesAndLintsTheOneFile(t *testing.T) {
	t.Parallel()
	w := newFileWS(t)
	// 差し替えなしなら lib の設定は読める。
	if errs := w.layerProblems("phases", "error", "(projects/lib)"); len(errs) != 0 {
		t.Fatalf("差し替えなしで error: %v", errs)
	}
	edited := w.edited("lib-phases.yml", libTitleOverlap)
	if errs := w.layerProblems("phases", "error", "(projects/lib)", "--project-phases-file", "lib="+edited); len(errs) == 0 {
		t.Error("表示名の重なりが lib の設定の error として出ない")
	}
	// 差し替えは診断の中だけ。本来のファイルは書き換えない。
	if got := configws.Read(t, configws.LayerPath(w.Lib, "phases")); got != configws.LibPhasesText {
		t.Errorf("本来のファイルが変わった: %q", got)
	}
}

func TestProjectPhasesFileSelfNamesTheWorkspacesOwnLayer(t *testing.T) {
	t.Parallel()
	w := newFileWS(t)
	own := configws.Read(t, configws.LayerPath(w.Root, "phases"))
	args := func(path string) []string { return []string{"--project-phases-file", "self=" + path} }

	clash := w.edited("self-phases.yml", own+"  clash:\n    kind: work\n    title: 文書\n    review: true\n    scope: inherit\n")
	if len(w.layerProblems("phases", "error", "(self)", args(clash)...)) == 0 {
		t.Error("ワークスペースの設定の差し替えが検証に届かない")
	}
	// 重ならない定義を足しただけなら通る。
	added := w.edited("self-added.yml", own+notesType)
	if errs := w.layerProblems("phases", "error", "(self)", args(added)...); len(errs) != 0 {
		t.Errorf("重ならない定義で error: %v", errs)
	}
	// 定義の無い設定は error。拡張が設定のファイルを最初の保存まで作らないのはこのため。
	empty := w.edited("empty.yml", emptyLayer)
	if len(w.layerProblems("phases", "error", "(self)", args(empty)...)) == 0 {
		t.Error("定義の無い設定が error にならない")
	}
}

// 差し替えた 1 本は共通設定の定義と足して確かめる。共通の design と中身が違う design は error。
func TestProjectPhasesFileIsCheckedTogetherWithTheCommonLayer(t *testing.T) {
	t.Parallel()
	w := newFileWS(t)
	configws.Write(t, w.Phases, configws.CommonPhasesText)
	// lib の design は共通の design と全欄が同じなので、そのままなら error にならない。
	if errs := w.layerProblems("phases", "error", "(projects/lib)"); len(errs) != 0 {
		t.Fatalf("そのままで error: %v", errs)
	}
	edited := w.edited("lib-phases.yml", strings.Replace(configws.LibPhasesText, `scope: ["wip/design/*"]`, `scope: ["docs/*"]`, 1))
	errs := w.layerProblems("phases", "error", "(projects/lib)", "--project-phases-file", "lib="+edited)
	found := false
	for _, p := range errs {
		found = found || strings.Contains(p.Where+p.Detail, "design")
	}
	if !found {
		t.Errorf("共通の design と違う design が error にならない: %v", errs)
	}
}

func TestProjectRiskFileALayerThatInvertsLevelsOnlyAfterTheMergeIsAnError(t *testing.T) {
	t.Parallel()
	w := newFileWS(t)
	tmp := w.edited("lib-risks.yml", invertingRisk)
	if errs := w.layerProblems("risk", "error", "(projects/lib)"); len(errs) != 0 {
		t.Fatalf("差し替えなしで error: %v", errs)
	}
	errs := w.layerProblems("risk", "error", "(projects/lib)", "--project-risk-file", "lib="+tmp)
	if !detailHas(errs, "high") {
		t.Errorf("合成後の逆転が error にならない: %v", errs)
	}
	// 差し替えは診断の中だけ。本来のファイルは書き換えない。
	if strings.Contains(configws.Read(t, configws.LayerPath(w.Lib, "risk")), "high") {
		t.Error("本来のファイルが変わった")
	}
	// 共通の差し替え（--risk）に同じ中身を渡す形では、合成後の逆転は見えない。共通設定単体の `levels` が逆転していないため。
	// 設定の側として渡すと見える。
	if old := w.layerProblems("risk", "error", "(projects/lib)", "--risk", tmp); len(old) != 0 {
		t.Errorf("--risk で逆転が見えた: %v", old)
	}
}

func TestProjectRiskFileSelfAndAProjectWithoutARiskFileAreNamedToo(t *testing.T) {
	t.Parallel()
	w := newFileWS(t)
	tmp := w.edited("risks.yml", invertingRisk)
	if own := w.layerProblems("risk", "error", "(self)", "--project-risk-file", "self="+tmp); !detailHas(own, "high") {
		t.Errorf("ワークスペースの設定の逆転が出ない: %v", own)
	}
	// ファイルの無い app でも、渡せばその設定の配点として合成される。lib は巻き込まない。
	args := []string{"--project-risk-file", "app=" + tmp}
	if app := w.layerProblems("risk", "error", "(projects/app)", args...); !detailHas(app, "high") {
		t.Errorf("app の逆転が出ない: %v", app)
	}
	if lib := w.layerProblems("risk", "error", "(projects/lib)", args...); len(lib) != 0 {
		t.Errorf("lib を巻き込んだ: %v", lib)
	}
}

func TestProjectRiskFileAClashingIDIsAWarnNamingTheLayer(t *testing.T) {
	t.Parallel()
	w := newFileWS(t)
	args := []string{"--project-risk-file", "lib=" + w.edited("lib-risks.yml", clashingRisk)}
	warns := w.layerProblems("risk", "warn", "(projects/lib)", args...)
	if !detailHas(warns, "big-diff") || !detailHas(warns, "lib:big-diff") {
		t.Errorf("同 id の衝突の warn が設定の名前を言わない: %v", warns)
	}
	// error ではない（両方を数える）。
	if errs := w.layerProblems("risk", "error", "(projects/lib)", args...); len(errs) != 0 {
		t.Errorf("error になった: %v", errs)
	}
}

func TestProjectRiskFileScriptIsLookedUpUnderTheLayersOwnProjectRoot(t *testing.T) {
	t.Parallel()
	w := newFileWS(t)
	args := []string{"--project-risk-file", "lib=" + w.edited("lib-risks.yml", scriptFactor)}
	lintLib := func() []configws.Problem { return w.layerProblems("risk", "error", "(projects/lib)", args...) }

	// 置いてなければ失敗する。差し替えファイルの置き場（ws/tmp）には引かない。
	if !detailHas(lintLib(), "count.sh") {
		t.Error("script が無いのに通る")
	}
	// ワークスペースルートに置いても lib の基準には効かない（設定ごとに解決先が違う）。
	configws.Write(t, filepath.Join(w.Root, ".ccnavi", "scripts", "count.sh"), "true\n")
	if !detailHas(lintLib(), "count.sh") {
		t.Error("ワークスペースルートの script が lib の基準に効いた")
	}
	// lib の置き場にあれば通る。
	configws.Write(t, filepath.Join(w.Lib, ".ccnavi", "scripts", "count.sh"), "true\n")
	if errs := lintLib(); len(errs) != 0 {
		t.Errorf("lib の置き場にあるのに error: %v", errs)
	}
	// ワークスペースの設定はワークスペースルートが基準。上で置いたものが引かれる。
	own := []string{"--project-risk-file", "self=" + w.edited("self-risks.yml", scriptFactor)}
	if errs := w.layerProblems("risk", "error", "(self)", own...); len(errs) != 0 {
		t.Errorf("ワークスペースの設定で error: %v", errs)
	}
	if err := configws.RemoveFile(filepath.Join(w.Root, ".ccnavi", "scripts", "count.sh")); err != nil {
		t.Fatal(err)
	}
	if gone := w.layerProblems("risk", "error", "(self)", own...); !detailHas(gone, "count.sh") {
		t.Errorf("script を消したのに通る: %v", gone)
	}
}

func TestTheRiskSwapDoesNotMoveWhatIsGuarded(t *testing.T) {
	t.Parallel()
	w := newFileWS(t)
	tmp := w.edited("lib-risks.yml", configws.CommonRiskText)
	real := configws.LayerPath(w.Lib, "risk")
	for name, flags := range map[string][]string{"no swap": nil, "swapped": {"--project-risk-file", "lib=" + tmp}} {
		t.Run(name, func(t *testing.T) {
			done := w.As(t).Exec("enable", "", append(flags, "--test", "--json", "Write", real)...)
			var body struct {
				Verdict string `json:"verdict"`
				Rules   []struct {
					ID string `json:"id"`
				} `json:"rules"`
			}
			if err := json.Unmarshal([]byte(done.Stdout), &body); err != nil {
				t.Fatalf("%v\n%s", err, done.Stdout)
			}
			guarded := false
			for _, r := range body.Rules {
				guarded = guarded || strings.Contains(r.ID, "guard")
			}
			if body.Verdict != "deny" || !guarded {
				t.Errorf("verdict = %q, rules = %v", body.Verdict, body.Rules)
			}
		})
	}
}

// 差し替えのフラグは診断の外では無視して言い、`<名前>=<パス>` でない形は 1 で止まる。
func TestProjectFileFlagsAreIgnoredOutsideDiagnosticsAndNeedNameAndPath(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct{ flag, edited, text string }{
		{"--project-phases-file", "lib-phases.yml", libTitleOverlap},
		{"--project-risk-file", "lib-risks.yml", invertingRisk},
	} {
		t.Run(tc.flag, func(t *testing.T) {
			t.Parallel()
			w := newFileWS(t)
			tmp := w.edited(tc.edited, tc.text)
			payload := configws.Body(configws.Obj{
				"hook_event_name": "PreToolUse", "tool_name": "Read",
				"tool_input": configws.Obj{"file_path": filepath.Join(w.Root, "README.md")}, "cwd": w.Root,
			})
			ignored := w.Exec("disable", payload, "--mode", "enable", tc.flag, "lib="+tmp)
			if !strings.Contains(ignored.Stderr, tc.flag+" は診断") {
				t.Errorf("無視したことを言わない: %q", ignored.Stderr)
			}
			malformed := w.Ccnavi("--lint", "--json", tc.flag, "lib")
			if malformed.Code != 1 || !strings.Contains(malformed.Stderr, "<名前>=<パス>") {
				t.Errorf("code = %d, stderr = %q", malformed.Code, malformed.Stderr)
			}
		})
	}
}
