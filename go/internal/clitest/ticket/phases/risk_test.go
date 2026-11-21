package phases

import (
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/samples"
)

// 実績で測るリスクの受入テスト（tests/ticket/test_risk.py）。定義の検証と差分の数え方の細部は
// go/internal/tickets/config/risk が見る。
//
//  1. 差分から点を数える（行数・ファイル数・消したファイル）
//  2. スクリプトの項目（点を受け取る。測れなければ重いほうとして扱う）
//  3. 定性の項目（判定が揃うまで閉じられない。judge で記録。HEAD が動けば取り直し）
//  4. HIGH 以上なら宣言に関わらずレビューが要る（レビューで止まり、依頼文にリスクの行が載る）
//  5. --lint が不正な定義を言い、不正なら組み込みに戻る

const riskDef = `
version: 1
levels: {medium: 20, high: 40, critical: 70}
factors:
  - {id: big-diff, points: 25, lines_over: 5, message: 行数が多い}
  - {id: many-files, points: 15, files_over: 2, message: ファイルが多い}
  - {id: ci, points: 35, glob: ".github/**", max: 35, message: CI に触った}
  - {id: deletes, points: 20, deleted_over: 0, message: 消したファイルがある}
`

// riskPhases は範囲の上限が無く、レビュー不要の定義。宣言では「レビュー不要」な作業を実績で上書きする。
const riskPhases = "version: 1\nphases:\n  work:\n    kind: work\n    title: 作業\n    review: false\n    scope: inherit\n"

// riskWS は配点と定義を共通レイヤーの既定の置き場へ置いた作業場（`--risk`・`--phases` は診断でだけ有効で、
// `ticket` のサブコマンドには届かない）。
type riskWS struct {
	*ws
	risk string
}

func newRiskWS(t *testing.T) *riskWS {
	w := newWS(t)
	w.setPhases(riskPhases)
	return &riskWS{ws: w, risk: write(t, base.CommonPath(w.root, "risk"), riskDef)}
}

func (r *riskWS) oneChild(review samples.Flag) string {
	r.t.Helper()
	scope := []string{"src/*", "wip/*", ".github/*"}
	r.propose(parentName, r.parent(planOf("work"), samples.ParentOpts{Allow: scope}))
	r.propose("i0001-01-01", childReview("i0001-01-01", 1, review, scope...))
	r.commitParent()
	r.mustApprove()
	return r.runChild("i0001-01-01", file{"wip/research/summary.md", "s\n"})
}

func (r *riskWS) record() map[string]any {
	return readJSON(r.t, filepath.Join(r.approved, "phases", parentName, "i0001-01-01.risk.json"))
}

func (r *riskWS) commitBig(tree string) {
	write(r.t, filepath.Join(tree, "src", "a.py"), strings.Repeat("x\n", 20))
	r.git(tree, "add", "-A")
	r.git(tree, "commit", "--quiet", "-m", "big")
}

func TestASmallChangeIsLowAndThePhaseIsSkipped(t *testing.T) {
	t.Parallel()
	r := newRiskWS(t)
	r.oneChild(samples.Off)
	closed := r.mustClose("i0001-01-01")
	contains(t, "finish", closed.Stdout, "リスク: 0 (LOW)")
	if r.record()["points"] != float64(0) {
		t.Errorf("points = %v", r.record()["points"])
	}
	said := r.hook("PostToolUse", "Bash", r.parentTree, input{"command": "ls"})
	contains(t, "hook", r.reason(said), "レビューは不要")
}

func TestABigChangeEscalatesToReviewEvenWhenDeclaredUnneeded(t *testing.T) {
	t.Parallel()
	r := newRiskWS(t)
	tree := r.oneChild(samples.Off)
	// 行数と、ファイル数と、CI と、削除。全部ヒットする。
	write(t, filepath.Join(tree, "src", "a.py"), strings.Repeat("x\n", 20))
	write(t, filepath.Join(tree, "src", "b.py"), "b\n")
	write(t, filepath.Join(tree, ".github", "ci.yml"), "on: push\n")
	r.git(tree, "rm", "-q", filepath.Join("src", "keep.py"))
	r.git(tree, "add", "-A")
	r.git(tree, "commit", "--quiet", "-m", "big")
	closed := r.mustClose("i0001-01-01")
	contains(t, "finish", closed.Stdout, "(CRITICAL)", "宣言に関わらず")
	record := r.record()
	if record["points"] != float64(95) {
		t.Errorf("points = %v", record["points"])
	}
	var ids []string
	for _, h := range record["hits"].([]any) {
		ids = append(ids, h.(map[string]any)["id"].(string))
	}
	sort.Strings(ids)
	equalStrings(t, "hits", ids, []string{"big-diff", "ci", "deletes", "many-files"})
	// 定義は review: false、子も required: false。それでもレビューで止まる。
	said := r.hook("PostToolUse", "Bash", r.parentTree, input{"command": "ls"})
	contains(t, "hook", r.reason(said), "実績のリスクが高い")
	r.commitParent("close 01")
	r.merge("i0001-01-01")
	spawn := r.hook("PreToolUse", "Agent", r.parentTree, input{"description": "次"})
	contains(t, "hook", r.reason(spawn), "DENY_PHASE_REVIEW", "実績のリスクが高い")
	contains(t, "--explain", r.ccnavi("--explain").Stdout, "実績でレビュー必須")
	// 依頼文の先頭にリスクの行が載る。
	fixture := r.remote()
	if requested := r.request(fixture, 1); requested.Code != 0 {
		t.Fatalf("code %d\n%s", requested.Code, requested.Stderr)
	}
	contains(t, "依頼の本文", r.lastBody, "このレビューのリスク: 95 (CRITICAL)", "行数が多い")
}

func TestAScriptFactorAddsItsPointsAndFailsHeavy(t *testing.T) {
	t.Parallel()
	r := newRiskWS(t)
	write(t, filepath.Join(r.root, ".ccnavi", "config", "common", "scripts", "count.sh"),
		"printf '{\"points\": 30, \"message\": \"%s\"}' \"$CCNAVI_TICKET\"\n")
	write(t, r.risk, "version: 1\nfactors:\n"+
		"  - {id: counted, points: 5, script: .ccnavi/config/common/scripts/count.sh, message: 数えた}\n"+
		"  - {id: broken, points: 45, script: .ccnavi/config/common/scripts/none.sh, message: 無い}\n")
	r.oneChild(samples.On)
	closed := r.mustOK(r.ccnavi("ticket", "finish", "i0001-01-01"), "finish")
	record := r.record()
	if record["points"] != float64(75) {
		t.Errorf("points = %v", record["points"])
	}
	byID := map[string]map[string]any{}
	for _, h := range record["hits"].([]any) {
		m := h.(map[string]any)
		byID[m["id"].(string)] = m
	}
	contains(t, "counted", byID["counted"]["detail"].(string), "i0001-01-01")
	if byID["broken"]["points"] != float64(45) {
		t.Errorf("broken = %v", byID["broken"])
	}
	if u, _ := record["unmeasured"].([]any); len(u) == 0 {
		t.Error("unmeasured が空")
	}
	contains(t, "finish", closed.Stdout, "測れなかった")
}

func TestAJudgeFactorBlocksDoneUntilRecorded(t *testing.T) {
	t.Parallel()
	r := newRiskWS(t)
	write(t, r.risk, "version: 1\nlevels: {high: 30}\nfactors:\n"+
		"  - {id: untested, points: 30, judge: テストの無い振る舞いの変更を含むか, message: テスト無し}\n")
	tree := r.oneChild(samples.On)
	refused := r.ccnavi("ticket", "finish", "i0001-01-01")
	if refused.Code == 0 {
		t.Fatal("判定が無いのに閉じられた")
	}
	contains(t, "stderr", refused.Stderr, "untested", "judge")
	contains(t, "判定の依頼", read(t, filepath.Join(r.state, "risk-judge-i0001-01-01.md")), "テストの無い振る舞いの変更を含むか", "wip/research/summary.md")
	// 親が記録する。yes で加点。
	r.mustOK(r.ccnavi("ticket", "record-risk", "i0001-01-01", "untested", "yes", "--reason", "テストが無い"), "record-risk yes")
	if wrong := r.ccnavi("ticket", "record-risk", "i0001-01-01", "nope", "yes", "--reason", "x"); wrong.Code == 0 {
		t.Error("知らない項目の記録が通った")
	}
	// HEAD が動いたら判定は古い。
	write(t, filepath.Join(tree, "src", "later.py"), "1\n")
	r.git(tree, "add", "-A")
	r.git(tree, "commit", "--quiet", "-m", "later")
	stale := r.ccnavi("ticket", "finish", "i0001-01-01")
	if stale.Code == 0 || !strings.Contains(stale.Stderr, "untested") {
		t.Errorf("古い判定で閉じられた: code %d\n%s", stale.Code, stale.Stderr)
	}
	r.mustOK(r.ccnavi("ticket", "record-risk", "i0001-01-01", "untested", "no", "--reason", "テストを足した"), "record-risk no")
	r.mustOK(r.ccnavi("ticket", "finish", "i0001-01-01"), "finish")
	if r.record()["points"] != float64(0) {
		t.Errorf("points = %v", r.record()["points"])
	}
	// サブエージェントは judge を打てない。
	denied := r.hookWith(hookOpts{AgentID: "sub-1"}, "PreToolUse", "Bash", r.parentTree,
		input{"command": "sh .ccnavi/scripts/ccnavi-ticket.sh record-risk i0001-01-01 untested yes --reason x"})
	contains(t, "hook", r.reason(denied), "DENY_SUBAGENT_TICKET_OP")
}

func TestARiskFlagOnTicketFinishDoesNotChangeTheScore(t *testing.T) {
	t.Parallel()
	// `ticket finish <子> --risk <別の配点>` は採点を差し替えない（issue #65）。`ccnavi-ticket.sh` が引数をそのまま
	// 渡すので、この形はエージェントが Bash で打てる。通していた頃は、重い変更を軽い配点で閉じられた。
	// `--risk ""` も同じ。空は「配点を持たない」の意味で通っていた。
	cheap := "version: 1\nlevels: {medium: 20, high: 40, critical: 70}\nfactors: []\n"
	for _, c := range []struct {
		name  string
		empty bool
	}{{"別の配点", false}, {"空", true}} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			r := newRiskWS(t)
			value := ""
			if !c.empty {
				value = write(t, filepath.Join(r.root, "cheap.yml"), cheap)
			}
			tree := r.oneChild(samples.Off)
			r.commitBig(tree)
			closed := r.mustOK(r.ccnavi("ticket", "finish", "i0001-01-01", "--risk", value), "finish")
			contains(t, "stderr", closed.Stderr, "--risk は診断") // 無視したことを言う
			if r.record()["points"] != float64(25) {
				t.Errorf("渡した配点で採点された: %v", r.record()["points"])
			}
		})
	}
}

func TestASecondRootOnTicketFinishIsRefused(t *testing.T) {
	t.Parallel()
	// `ticket finish <子> --root <偽>` は断る（issue #65 の敵対的レビューで出た形）。sh は自分の `--root` を先に置き、
	// エージェントの引数を後ろに繋ぐ。後ろに 1 本足すと後勝ちで sh が渡した正しい値を上書きできた。`--root` からは共通レイヤーの
	// 3 本も projects も approved も導かれるので、`--risk` を使わずに同じ差し替えができる。偽のルートの中身を作り込まず、
	// 2 本目が在ること自体を断る。
	r := newRiskWS(t)
	tree := r.oneChild(samples.Off)
	r.commitBig(tree)
	refused := r.ccnavi("ticket", "finish", "i0001-01-01", "--root", filepath.Join(r.root, "x"))
	if refused.Code == 0 || !strings.Contains(refused.Stderr, "--root は 1 度しか渡せない") {
		t.Errorf("code %d\n%s", refused.Code, refused.Stderr)
	}
	// 止まったことを、結末の側でも見る。閉じても記録を残してもいない。
	if !r.isApproved("i0001-01-01") {
		t.Error("doing/ から動いた")
	}
	if exists(filepath.Join(r.approved, "phases", parentName, "i0001-01-01.risk.json")) {
		t.Error("採点の記録が残った")
	}
}

func TestLintReportsABrokenDefinitionAndBuiltinTakesOver(t *testing.T) {
	t.Parallel()
	r := newRiskWS(t)
	write(t, r.risk, "version: 1\nfactors: 3\n")
	linted := r.ccnavi("--lint")
	contains(t, "lint", linted.Stdout+linted.Stderr, "(risk)")
	r.oneChild(samples.Off)
	closed := r.mustOK(r.ccnavi("ticket", "finish", "i0001-01-01"), "finish")
	contains(t, "stderr", closed.Stderr, "組み込みの配点")
	contains(t, "stdout", closed.Stdout, "(builtin)")
}
