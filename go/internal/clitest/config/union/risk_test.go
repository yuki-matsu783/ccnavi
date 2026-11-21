package union

import (
	"ccnavi/internal/harness/configws"
	"ccnavi/internal/harness/run"
	"encoding/json"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// 設定の受入テスト。risk は共通 + 1 レイヤーの和。
// risk は共通レイヤーに `big-diff`、lib のレイヤーに `schema` と `levels: {critical: 50}` がある。
// 親と子は lib の `work`（範囲の上限が無くレビュー不要）を使う。

// 共通レイヤーに足す項目。どれも `factors:` の続きなので、commonRiskText の後ろに繋げる。
const (
	commonScriptFactor        = "  - {id: common-counted, points: 5, script: .ccnavi/config/common/scripts/count.sh, message: 共通で数えた}\n"
	commonMissingScriptFactor = "  - {id: gone, points: 5, script: .ccnavi/config/common/scripts/gone.sh, message: 無い}\n"
	commonJudgeFactor         = "  - {id: outward, points: 10, judge: 外に出す変更か, message: 外向き}\n"
	// lib のレイヤーに足す定性の項目。judge.json の source がレイヤーごとに分かれることを見る。
	libJudgeFactor = "  - {id: untested, points: 10, judge: テストの無い変更か, message: テスト無し}\n"
)

const tenLines = "0\n1\n2\n3\n4\n5\n6\n7\n8\n9\n"

// riskProblems は risk の Problem。layer を渡すと、そのレイヤーのものだけ。
func (w *ws) riskProblems(severity, layer string) []configws.Problem {
	w.T.Helper()
	where := ""
	if layer != "" {
		where = configws.ProjectWhere(layer)
	}
	var out []configws.Problem
	for _, pr := range w.Problems(severity, where) {
		if strings.Contains(pr.Where, "risk") {
			out = append(out, pr)
		}
	}
	return out
}

// startParent は親を着手する（済んでいれば何もしない）。子の着手は親が着手済みであることを前提にする。
func (w *ws) startParent(name string) {
	w.T.Helper()
	r := w.Ccnavi("ticket", "start", name)
	if r.Code != 0 && !strings.Contains(r.Stderr, "着手済み") {
		w.T.Fatalf("親の着手: code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}

// oneChild は lib の親と子を承認し、子のワークツリーを lib から切って着手する。子のツリーと親のツリーを返す。
func (w *ws) oneChild() (tree, parentTree string) {
	w.T.Helper()
	scope := []string{"src/*", "schema/*", "wip/*"}
	w.Propose("i0001", configws.TicketText("i0001", configws.TicketOpts{Project: "lib", Plan: []string{"work"}, Allow: scope}), "lib")
	w.Propose("i0001-01-01", configws.TicketText("i0001-01-01", configws.TicketOpts{Project: "lib", Parent: "i0001", Phase: 1, Allow: scope}), "lib")
	w.mustApprove()
	parentTree = w.Worktree(w.Lib, "i0001")
	w.startParent("i0001")
	tree = w.Worktree(w.Lib, "i0001-01-01")
	w.MustOK(w.Ccnavi("ticket", "start", "i0001-01-01"), "子の着手")
	return tree, parentTree
}

func (w *ws) commit(tree, rel, text string) {
	w.T.Helper()
	configws.Write(w.T, filepath.Join(tree, filepath.FromSlash(rel)), text)
	run.Git(w.T, tree, "add", "-A")
	run.Git(w.T, tree, "commit", "--quiet", "-m", rel)
}

type riskHit struct {
	ID     string `json:"id"`
	Points int    `json:"points"`
	Detail string `json:"detail"`
	Source string `json:"source"`
}

type riskRecord struct {
	Points   int       `json:"points"`
	Hits     []riskHit `json:"hits"`
	Fallback string    `json:"fallback"`
}

func (r riskRecord) byID() map[string]riskHit {
	out := map[string]riskHit{}
	for _, h := range r.Hits {
		out[h.ID] = h
	}
	return out
}

func (w *ws) riskRecord() riskRecord {
	w.T.Helper()
	var rec riskRecord
	if err := json.Unmarshal([]byte(configws.Read(w.T, w.ApprovedPath("phases", "i0001", "i0001-01-01.risk.json"))), &rec); err != nil {
		w.T.Fatal(err)
	}
	return rec
}

// judgeSources は `<子>.judge.json` の項目名 → その項目のレイヤー（source）。
func (w *ws) judgeSources() map[string]string {
	w.T.Helper()
	var rec map[string]struct {
		Source string `json:"source"`
	}
	if err := json.Unmarshal([]byte(configws.Read(w.T, w.ApprovedPath("phases", "i0001", "i0001-01-01.judge.json"))), &rec); err != nil {
		w.T.Fatal(err)
	}
	out := map[string]string{}
	for k, v := range rec {
		out[k] = v.Source
	}
	return out
}

// finish は子を閉じる。
func (w *ws) finish(extra ...string) (code int, stdout, stderr string) {
	w.T.Helper()
	r := w.Ccnavi(append([]string{"ticket", "finish", "i0001-01-01"}, extra...)...)
	return r.Code, r.Stdout, r.Stderr
}

func (w *ws) mustFinish() string {
	w.T.Helper()
	code, out, errOut := w.finish()
	if code != 0 {
		w.T.Fatalf("finish: code %d\n%s%s", code, out, errOut)
	}
	return out + errOut
}

func TestFactorsConcatenateAndLevelsTakeTheMinimum(t *testing.T) {
	t.Parallel()
	// factors は連結、levels はキーごとに min。記録の hit に source。
	w := newWS(t, configws.KindRisk)
	tree, _ := w.oneChild()
	w.commit(tree, "schema/x.sql", tenLines)

	_, out, _ := w.finish()
	rec := w.riskRecord()
	if rec.Points != 55 {
		t.Errorf("points = %d, want 55", rec.Points)
	}
	by := rec.byID()
	if got := configws.SortedKeys(by); !slices.Equal(got, []string{"big-diff", "schema"}) {
		t.Errorf("hits = %v", got)
	}
	if by["big-diff"].Source != "common" || by["schema"].Source != "lib" {
		t.Errorf("source: %+v", by)
	}
	// critical は min(70, 50) = 50。55 点は CRITICAL。共通レイヤーだけなら HIGH。
	if !strings.Contains(out, "(CRITICAL)") {
		t.Errorf("CRITICAL でない: %s", out)
	}
}

func TestUnwrittenLevelKeysDoNotTakePart(t *testing.T) {
	t.Parallel()
	// 書かれていないキーは参加しない。lib が書かない medium / high は共通レイヤーの値。
	w := newWS(t, configws.KindRisk)
	tree, _ := w.oneChild()
	w.commit(tree, "src/a.py", tenLines)

	out := w.mustFinish()
	if got := w.riskRecord().Points; got != 25 {
		t.Errorf("points = %d, want 25", got)
	}
	// 25 点は medium 20 以上、high 40 未満。
	if !strings.Contains(out, "(MEDIUM)") {
		t.Errorf("MEDIUM でない: %s", out)
	}
}

func TestSameFactorIdAcrossLayersCountsBothUnderTheLayerName(t *testing.T) {
	t.Parallel()
	// 同 id で中身が違えば両方を数え、後ろのレイヤーは `<レイヤー>:<id>`。--lint は warn。
	// レイヤーを空にすると、プロジェクトが共通レイヤーと同じ名前の項目を 1 本書くだけで、そのレイヤーの項目と閾値が全部消えて
	// 点が下がる。両方を数えれば、衝突しても点は増える向きにしか変わらない。
	w := newWS(t, configws.KindRisk)
	configws.WriteRisk(t, w.Lib, "version: 1\nlevels: {critical: 50}\nfactors:\n"+
		"  - {id: big-diff, points: 5, files_over: 0, message: x}\n"+
		"  - {id: schema, points: 30, glob: \"schema/**\", message: スキーマに触った}\n")
	if got := w.riskProblems("error", ""); len(got) != 0 {
		t.Errorf("error が出る: %+v", got)
	}
	w.MustHaveDetail(w.riskProblems("warn", "lib"), "lib:big-diff")

	tree, _ := w.oneChild()
	w.commit(tree, "schema/x.sql", tenLines)
	out := w.mustFinish()
	rec := w.riskRecord()
	by := rec.byID()
	if got := configws.SortedKeys(by); !slices.Equal(got, []string{"big-diff", "lib:big-diff", "schema"}) {
		t.Errorf("hits = %v", got)
	}
	if by["big-diff"].Source != "common" || by["lib:big-diff"].Source != "lib" {
		t.Errorf("source: %+v", by)
	}
	// 25 + 5 + 30。lib の critical 50 も有効なまま。
	if rec.Points != 60 || rec.Fallback != "" || !strings.Contains(out, "(CRITICAL)") {
		t.Errorf("points = %d, fallback = %q, out = %s", rec.Points, rec.Fallback, out)
	}
}

func TestACollidingJudgeItemIsRecordedUnderTheLayerName(t *testing.T) {
	t.Parallel()
	// 同 id の定性項目は、後ろのレイヤーの側を `<レイヤー>:<id>` で record-risk する。
	w := newWS(t, configws.KindRisk)
	configws.Write(t, w.Risk, configws.CommonRiskText+commonJudgeFactor)
	configws.WriteRisk(t, w.Lib, configws.LibRiskText+"  - {id: outward, points: 15, judge: 公開の API を変えるか, message: 外向き（lib）}\n")

	tree, _ := w.oneChild()
	w.commit(tree, "src/a.py", "1\n")
	code, out, errOut := w.finish()
	if code == 0 || !strings.Contains(errOut, "lib:outward") {
		t.Fatalf("判定が済むまで閉じられず、lib:outward を言う: code %d\n%s%s", code, out, errOut)
	}
	for _, factor := range []string{"outward", "lib:outward"} {
		w.MustOK(w.Ccnavi("ticket", "record-risk", "i0001-01-01", factor, "yes", "--reason", "そう"), "record-risk "+factor)
	}
	w.mustFinish()
	got := w.judgeSources()
	if len(got) != 2 || got["outward"] != "common" || got["lib:outward"] != "lib" {
		t.Errorf("judge の source: %v", got)
	}
	if pts := w.riskRecord().Points; pts != 25 {
		t.Errorf("points = %d, want 25", pts)
	}
}

func TestRiskLintRefusesWhatTheLayersMustNotSay(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name     string
		setup    func(t testing.TB, w *ws)
		severity string
		layer    string
		detail   string // 空なら「その深刻度の問題が無い」を見る
		noError  bool   // true なら error が無いことも見る
	}{
		{"identical factor in a later layer is dropped with info", func(t testing.TB, w *ws) {
			configws.WriteRisk(t, w.Lib, strings.Replace(configws.CommonRiskText, "levels: {medium: 20, high: 40, critical: 70}\n", "", 1))
		}, "info", "lib", "big-diff", true},
		{"inverted levels after merge", func(t testing.TB, w *ws) {
			configws.WriteRisk(t, w.Lib, "version: 1\nlevels: {high: 10}\n")
		}, "error", "lib", "high", false},
		{"a layer cannot point at the common scripts", func(t testing.TB, w *ws) {
			configws.WriteRisk(t, w.Lib, "version: 1\nfactors:\n  - {id: x, points: 5, script: .ccnavi/config/common/scripts/x.sh, message: x}\n")
		}, "error", "lib", ".ccnavi/config/common/scripts/x.sh", false},
		{"the common layer cannot point at the project scripts", func(t testing.TB, w *ws) {
			configws.Write(t, w.Risk, "version: 1\nfactors:\n  - {id: y, points: 5, script: .ccnavi/scripts/y.sh, message: y}\n")
		}, "error", "", ".ccnavi/scripts/y.sh", false},
		{"a script missing from the project root", func(t testing.TB, w *ws) {
			configws.WriteRisk(t, w.Lib, "version: 1\nfactors:\n  - {id: gone, points: 5, script: .ccnavi/scripts/gone.sh, message: x}\n")
		}, "error", "lib", "gone.sh", false},
		{"a script missing from the workspace root", func(t testing.TB, w *ws) {
			configws.Write(t, w.Risk, configws.CommonRiskText+commonMissingScriptFactor)
		}, "error", "", "gone.sh", false},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w := newWS(t, configws.KindRisk)
			c.setup(t, w)
			if c.noError {
				if got := w.riskProblems("error", ""); len(got) != 0 {
					t.Errorf("error が出る: %+v", got)
				}
			}
			w.MustHaveDetail(w.riskProblems(c.severity, c.layer), c.detail)
		})
	}
}

func TestAScriptInTheProjectLayerRunsFromTheProjectRoot(t *testing.T) {
	t.Parallel()
	// レイヤーの `script:` はそのプロジェクトの git プロジェクトルートから解く。
	w := newWS(t, configws.KindRisk)
	configws.Write(t, p(w.Lib, ".ccnavi", "scripts", "count.sh"), "printf '{\"points\": 30, \"message\": \"%s\"}' \"$CCNAVI_TICKET\"\n")
	configws.WriteRisk(t, w.Lib, "version: 1\nfactors:\n  - {id: counted, points: 5, script: .ccnavi/scripts/count.sh, message: 数えた}\n")
	run.Git(t, w.Lib, "add", "-A")
	run.Git(t, w.Lib, "commit", "--quiet", "-m", "script")
	if got := w.riskProblems("error", ""); len(got) != 0 {
		t.Fatalf("error が出る: %+v", got)
	}

	w.oneChild()
	w.mustFinish()
	hit := w.riskRecord().byID()["counted"]
	if hit.Points != 30 || !strings.Contains(hit.Detail, "i0001-01-01") || hit.Source != "lib" {
		t.Errorf("counted = %+v", hit)
	}
}

func TestAScriptInTheCommonLayerRunsThroughTheMerge(t *testing.T) {
	t.Parallel()
	// 合成を通しても、共通レイヤーの `script:` はワークスペースルートから解いて走る。
	w := newWS(t, configws.KindRisk)
	configws.Write(t, p(w.Root, ".ccnavi", "config", "common", "scripts", "count.sh"), "printf '{\"points\": 7, \"message\": \"%s\"}' \"$CCNAVI_TICKET\"\n")
	configws.Write(t, w.Risk, configws.CommonRiskText+commonScriptFactor)
	if got := w.riskProblems("error", ""); len(got) != 0 {
		t.Fatalf("error が出る: %+v", got)
	}

	w.oneChild()
	w.mustFinish()
	hit := w.riskRecord().byID()["common-counted"]
	if hit.Points != 7 || !strings.Contains(hit.Detail, "i0001-01-01") || hit.Source != "common" {
		t.Errorf("common-counted = %+v", hit)
	}
}

func TestABrokenProjectRiskIsEmptyAndNamedInTheFallback(t *testing.T) {
	t.Parallel()
	// 不正な risk のレイヤーは空 + `fallback` にレイヤーの名前 + --lint error。
	w := newWS(t, configws.KindRisk)
	configws.Write(t, configws.LayerPath(w.Lib, "risk"), "version: 1\nfactors: [\n")
	if len(w.riskProblems("error", "lib")) == 0 {
		t.Fatalf("不正な risk を error で言わない: %+v", w.Problems("error", ""))
	}

	tree, _ := w.oneChild()
	w.commit(tree, "schema/x.sql", tenLines)
	out := w.mustFinish()
	rec := w.riskRecord()
	// 共通レイヤーの factors だけで点が付く。levels も共通レイヤーのまま。
	if len(rec.Hits) != 1 || rec.Hits[0].ID != "big-diff" || rec.Points != 25 || rec.Fallback != "lib" {
		t.Errorf("record = %+v", rec)
	}
	if !strings.Contains(out, "lib") {
		t.Errorf("閉じた出力がレイヤーを言わない: %s", out)
	}
}

func TestTheJudgeRecordNamesTheLayerOfEachItem(t *testing.T) {
	t.Parallel()
	// `<子>.judge.json` の各項目に、その項目のレイヤーの `source`。
	w := newWS(t, configws.KindRisk)
	configws.Write(t, w.Risk, configws.CommonRiskText+commonJudgeFactor)
	configws.WriteRisk(t, w.Lib, configws.LibRiskText+libJudgeFactor)

	tree, _ := w.oneChild()
	w.commit(tree, "src/a.py", "1\n")
	if code, out, _ := w.finish(); code == 0 {
		t.Fatalf("判定が済むまで閉じられない: %s", out)
	}
	for _, factor := range []string{"outward", "untested"} {
		w.MustOK(w.Ccnavi("ticket", "record-risk", "i0001-01-01", factor, "yes", "--reason", "そう"), "record-risk "+factor)
	}
	w.mustFinish()
	got := w.judgeSources()
	if len(got) != 2 || got["outward"] != "common" || got["untested"] != "lib" {
		t.Errorf("judge の source: %v", got)
	}
}

// skippedMark は親の `1.skipped`（定義を根拠に置くマーカー）の source。
func (w *ws) skippedMark(parentTree string) string {
	w.T.Helper()
	said := w.Post("Bash", parentTree, configws.Obj{"command": "ls"})
	if !strings.Contains(w.Reason(said), "省略") {
		w.T.Fatalf("省略を言わない: %s%s", said.Stdout, said.Stderr)
	}
	var mark struct {
		Source string `json:"source"`
	}
	if err := json.Unmarshal([]byte(configws.Read(w.T, w.ApprovedPath("phases", "i0001", "1.skipped"))), &mark); err != nil {
		w.T.Fatal(err)
	}
	return mark.Source
}

func TestTheMarkThatRestsOnATypeNamesItsLayer(t *testing.T) {
	t.Parallel()
	// 定義を根拠に置くマーカー（`review: false` の skipped）には、その定義のレイヤー。
	w := newWS(t, configws.KindRisk)
	tree, parentTree := w.oneChild()
	w.commit(tree, "src/a.py", "1\n")
	w.mustFinish()
	if got := w.skippedMark(parentTree); got != "lib" {
		t.Errorf("source = %q, want lib", got)
	}
}

func TestTheMarkNamesCommonWhenTheTypeComesFromCommon(t *testing.T) {
	t.Parallel()
	// 共通レイヤーの定義をコピーした親のマーカーは、source が common。`--explain --json` の和と同じ。lib の `work` は共通レイヤーの
	// `work` と全欄が同じなので、和では共通の側が残る。コピーは出どころを持たないが、共通レイヤーの定義と中身が同じなら
	// 共通の定義として記す。
	w := newWS(t, configws.KindRisk)
	configws.Write(t, w.Phases, configws.LibPhasesWork)
	tree, parentTree := w.oneChild()
	w.commit(tree, "src/a.py", "1\n")
	w.mustFinish()

	mark := w.skippedMark(parentTree)
	explained := sourcesOf(w.sums()["lib"])["work"]
	if explained != "common" || mark != explained {
		t.Errorf("mark = %q, 和の出どころ = %q（どちらも common のはず）", mark, explained)
	}
}

// レイヤーを探す先を動かすフラグも、診断の外では有効でない（issue #65）。`--projects` と `--project-home` は、共通レイヤーの
// 中身を差し替えるのと結果が同じ。外すとプロジェクトのレイヤーがまるごと消えるので、そのレイヤーが足していた配点もフェーズ定義も
// 無くなる。`.ccnavi/scripts/ccnavi-ticket.sh` は引数をそのまま渡すので、この形はエージェントが Bash で打てる。
//
// 土台の子は共通レイヤーの `big-diff`（25）と lib の `schema`（30）で 55 点、lib の critical は 50 なので CRITICAL。
// `review: true` のフェーズなのでレビュー待ちへ動く。lib のレイヤーが消えると 25 点の MEDIUM になり、レビューを飛ばして閉じられる。
func TestLayerPlaceFlagsAreDiagnosisOnly(t *testing.T) {
	t.Parallel()
	for _, flag := range []string{"--project-home", "--projects"} {
		t.Run(flag, func(t *testing.T) {
			t.Parallel()
			w := newWS(t, configws.KindRisk)
			tree, _ := w.oneChild()
			w.commit(tree, "schema/x.sql", tenLines)

			value := ".nothere"
			if flag == "--projects" {
				value = filepath.Join(w.Root, "x")
			}
			code, out, errOut := w.finish(flag, value)

			if !strings.Contains(errOut, flag+" は診断") {
				t.Errorf("無視したことを言っていない: %s", errOut)
			}
			if code != 0 {
				t.Fatalf("code %d\n%s%s", code, out, errOut)
			}
			rec := w.riskRecord()
			if rec.Points != 55 {
				t.Errorf("lib のレイヤーが落ちた: points = %d", rec.Points)
			}
			if _, ok := rec.byID()["schema"]; !ok {
				t.Error("schema の配点が無い")
			}
			if !strings.Contains(out, "(CRITICAL)") {
				t.Errorf("CRITICAL でない: %s", out)
			}
			if w.ProposalPath("i0001-01-01", "review") == "" {
				t.Error("レビュー待ちへ動いていない")
			}
		})
	}
}
