package review

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
)

// 残った指摘の対応方針の受入テスト（ccnavi-user-decide.sh と ccnavi-review.sh decide --preview）。
//
// 選んだものを置く形はユーザ専用の ccnavi-user-decide.sh、一覧を見るだけの形は ccnavi-review.sh decide <N> --preview。
// ccnavi-review.sh の decide の置く形は、何も動かさずに終了コード 2 で新しい sh を案内する。
//
// 見るのは sh の仕事だけ。
//
//  1. `--preview` は実行ファイルの答えをそのまま返し、何も投稿しない
//  2. `--choices … --digest …` は、受け入れの計画の操作 ID で issue とコメントを投稿し、答えに issue の URL を足す
//  3. 投稿に失敗したら操作記録を残し、警告を答えに載せる（置いたことは戻さない。decide_ops_test.go が見る）。計画に操作 ID が無ければ、
//     何も送らずに止める
//  4. フェーズ番号の表記を揃える（`01` でも、実行ファイルが `1` で書いた下書きを拾う。通しの代表で見る）
//  5. 引数の組み合わせの誤りは、実行ファイルを起こす前に断る。ccnavi-review.sh の decide の置く形と、ccnavi-user-decide.sh の
//     --preview も断る
//  6. 抜けるときの片付け（EXIT の trap）で rm が失敗しても、元の終了コードで抜ける（dash と bash）
//
// 実行ファイルは代わりの sh（decideStub）。判定そのものは clitest/ticket が見る。

func TestDecidePreviewPassesTheAnswerThroughAndPostsNothing(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	shown := d.decide(dOpts{script: reviewSh}, "1", "--preview")
	calls := d.calls()
	if shown.Code != 0 || decode(t, shown.Stdout)["digest"] != "d0" || len(calls) == 0 ||
		!strings.Contains(calls[len(calls)-1], "--reviewed 1 --accept-unresolved --preview --json") || len(d.gl.kinds()) != 0 {
		t.Errorf("code = %d, 呼び出し = %v, 書き込み = %v\n%s%s", shown.Code, calls, d.gl.kinds(), shown.Stdout, shown.Stderr)
	}
}

// 置く形の通しの代表。フェーズ番号は `01` で渡し、`1` に揃えて呼ぶ。
func TestDecideChoicesCreateTheIssueAndPostTheDecision(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	done := d.decide(dOpts{}, "01", "--choices", `{"u1":"issue"}`, "--digest", "d0")
	if done.Code != 0 {
		t.Fatalf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
	answer := answerOf(t, done.Stdout)
	if answer["ok"] != true || !strings.HasSuffix(fmt.Sprint(answer["issue_url"]), "/issues/1") || answer["warning"] != "" {
		t.Errorf("答え = %v", answer)
	}
	calls := d.calls()
	if len(calls) < 2 {
		t.Fatalf("呼び出し = %v", calls)
	}
	plan, place := calls[len(calls)-2], calls[len(calls)-1]
	// 置く前に、受け入れる指摘のスレッドへ返信を残して解決する。ユーザ専用の入口（`user reviewed`）で呼ぶ
	// （`user` の外の `--reviewed --yes` は実行ファイルが断る）。置く呼び出しは計画を読み、返信と解決のあとで取り直した結果を渡される
	if !strings.Contains(plan, "--accept-plan") || !strings.Contains(place, `--yes {"u1":"issue"} --digest d0 --json`) ||
		!strings.Contains(plan, " user reviewed 1 --accept-unresolved --yes ") || !strings.Contains(place, " user reviewed 1 --accept-unresolved --yes ") ||
		!strings.Contains(place, "--accept-done") || d.gl.fetched() != 2 {
		t.Errorf("計画 = %q, 置く = %q, 取り直し = %d", plan, place, d.gl.fetched())
	}
	if kinds := d.gl.kinds(); fmt.Sprint(kinds) != "[reply resolve issue note]" {
		t.Fatalf("書き込み = %v", kinds)
	}
	reply, resolve := d.gl.postedOf("reply")[0], d.gl.postedOf("resolve")[0]
	if !strings.HasSuffix(fmt.Sprint(reply["path"]), "/merge_requests/1/discussions/d1/notes") || !strings.HasPrefix(fmt.Sprint(reply["body"]), acceptMarker) ||
		!strings.HasSuffix(fmt.Sprint(resolve["path"]), "/merge_requests/1/discussions/d1") || resolve["resolved"] != true {
		t.Errorf("返信 = %v, 解決 = %v", reply, resolve)
	}
	issue, note := d.gl.postedOf("issue")[0], fmt.Sprint(d.gl.postedOf("note")[0]["body"])
	desc := fmt.Sprint(issue["description"])
	if !strings.HasPrefix(fmt.Sprint(issue["title"]), "レビューで残った指摘") || !strings.HasPrefix(note, "<!-- ccnavi:decide -->") || !strings.Contains(note, "issue に回した分: #1") {
		t.Errorf("issue = %v, note = %q", issue, note)
	}
	// 投稿の末尾に操作記録の段階の目印を足す
	if !regexp.MustCompile(`<!-- ccnavi:op=[0-9a-f]{32} stage=issue -->\s*$`).MatchString(desc) || !regexp.MustCompile(`<!-- ccnavi:op=[0-9a-f]{32} stage=note -->\s*$`).MatchString(note) {
		t.Errorf("段階の目印が無い: issue = %q, note = %q", desc, note)
	}
	// 本文は保存した判断から作り直し、全文をフェンスで囲んでいる
	if !strings.Contains(desc, "````text") || !strings.Contains(desc, "指摘の本文") {
		t.Errorf("issue の本文 = %q", desc)
	}
	if left := d.leftovers(); len(left) != 0 {
		t.Errorf("state に残った = %v", left)
	}
	if ids := samples.RecordIDs(d.state); len(ids) != 0 { // すべて送れたら記録を消す
		t.Errorf("操作記録が残った = %v", ids)
	}
}

func TestDecideAPlanWithoutAnOpIDStopsAndSendsNothing(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	empty := ""
	stopped := d.decide(dOpts{env: map[string]string{"STUB_PLAN": d.planFile(&empty, "d1")}}, "1", "--choices", `{"u1":"keep"}`, "--digest", "d0")
	if stopped.Code != 1 || !strings.Contains(stopped.Stderr, "操作 ID が無く") || strings.Contains(strings.Join(d.calls(), " "), "--accept-done") {
		t.Errorf("code = %d\n%s%s", stopped.Code, stopped.Stdout, stopped.Stderr)
	}
}

func TestDecideAFailedReplyStopsBeforeAnythingIsPlaced(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	d.gl.do(func() { d.gl.failReply = true })
	stopped := d.decide(dOpts{}, "1", "--choices", `{"u1":"issue"}`, "--digest", "d0")
	// 置く呼び出し（--yes）は打たない。issue もコメントも作らない
	var placing []string
	for _, c := range d.calls() {
		if !strings.Contains(c, "--accept-plan") {
			placing = append(placing, c)
		}
	}
	if stopped.Code != 1 || !strings.Contains(stopped.Stderr, "受け入れを完了できない") || !strings.Contains(stopped.Stderr, "返信を残せなかった") ||
		strings.Contains(strings.Join(placing, " "), "--yes") || len(d.gl.kinds()) != 0 || len(d.leftovers()) != 0 {
		t.Errorf("code = %d, 置く呼び出し = %v, 書き込み = %v, 残り = %v\n%s", stopped.Code, placing, d.gl.kinds(), d.leftovers(), stopped.Stderr)
	}
}

// 全部に返信してから解決する。途中で解決に失敗したら、置かずに止まり、計画を残す。
// 計画のファイルは、実行ファイルが `--accept-plan` に書く形（planFile）で渡す。
func TestDecideAllRepliesComeBeforeAnyResolveAndAPartialFailureStops(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	d.gl.do(func() { d.gl.threads = []*thread{{id: "d1"}, {id: "d2"}}; d.gl.failResolveIDs["d2"] = true })
	stopped := d.decide(dOpts{env: map[string]string{"STUB_PLAN": d.planFile(nil, "d1", "d2")}}, "1", "--choices", `{"u1":"keep"}`, "--digest", "d0")
	allPlans := true
	for _, c := range d.calls() {
		allPlans = allPlans && strings.Contains(c, "--accept-plan")
	}
	// ホストに書いたので、計画は残す（打ち直すと d1 は見せる指摘から外れる）
	if stopped.Code != 1 || fmt.Sprint(d.gl.kinds()) != "[reply reply resolve]" || !strings.Contains(stopped.Stderr, "解決できなかった") ||
		!strings.Contains(stopped.Stderr, "先に解決した分は残る") || !allPlans || !strings.Contains(stopped.Stderr, "計画を残した") ||
		fmt.Sprint(d.leftovers()) != "[review-accept-i0001-1.json]" {
		t.Errorf("code = %d, 書き込み = %v, 残り = %v\n%s", stopped.Code, d.gl.kinds(), d.leftovers(), stopped.Stderr)
	}
}

// 計画の id を実行ファイル（accept threads）が断れば、ホストに書かない。断る id の種類（結果に無い・経路に使えない字・
// GitLab で 16 進でない）は Go の host.TestAcceptCheck が見る。ここでは経路に使えない字の 1 つで、sh が止まることだけを見る。
func TestDecideNothingIsWrittenWhenThePlanDoesNotMatchTheFetchedResult(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	stopped := d.decide(dOpts{env: map[string]string{"STUB_PLAN": d.planFile(nil, "d1/../x")}}, "1", "--choices", `{"u1":"keep"}`, "--digest", "d0")
	if stopped.Code != 1 || len(d.gl.kinds()) != 0 || !strings.Contains(stopped.Stderr, "ホストには何も書いていない") {
		t.Errorf("code = %d, 書き込み = %v\n%s", stopped.Code, d.gl.kinds(), stopped.Stderr)
	}
}

func TestDecideAFailedPlacementAfterTheHostWasWrittenKeepsThePlan(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	failed := d.decide(dOpts{env: map[string]string{"STUB_FAIL_YES": "1"}}, "1", "--choices", `{"u1":"keep"}`, "--digest", "d0")
	if failed.Code != 1 || fmt.Sprint(d.gl.kinds()) != "[reply resolve]" || !strings.Contains(failed.Stderr, "置けなかった") ||
		fmt.Sprint(d.leftovers()) != "[review-accept-i0001-1.json]" {
		t.Errorf("code = %d, 書き込み = %v, 残り = %v\n%s", failed.Code, d.gl.kinds(), d.leftovers(), failed.Stderr)
	}
}

// 前提を満たさなければ、ホストへの問い合わせ・取り込み・選ぶ操作の前に断る。
func TestDecideTheGateRefusesBeforeTheHostTheLockAndTheChoosing(t *testing.T) {
	t.Parallel()
	for name, args := range map[string][]string{"選ぶ前": {"1"}, "置く形": {"1", "--choices", `{"u1":"keep"}`, "--digest", "d0"}} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			d := newDecideSite(t)
			stopped := d.decide(dOpts{env: map[string]string{"STUB_FAIL_CHECK": "1"}}, args...)
			all := d.allCalls()
			if stopped.Code != 1 || len(d.calls()) != 0 || len(all) != 1 || !strings.Contains(all[0], " user reviewed 1 --check --accept-unresolved") ||
				len(d.gl.kinds()) != 0 || d.gl.fetched() != 0 || len(d.leftovers()) != 0 {
				t.Errorf("code = %d, 呼び出し = %v, 書き込み = %v, 取得 = %d\n%s", stopped.Code, all, d.gl.kinds(), d.gl.fetched(), stopped.Stderr)
			}
		})
	}
}

func TestDecideWrongArgumentsAreRefusedBeforeAnythingRuns(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	for _, args := range [][]string{
		{"x"}, {"1", "--choices"}, {"1", "--digest", "d0"}, {"1", "--choices", "{}"},
		{"1", "--preview", "--choices", "{}", "--digest", "d0"}, {"1", "--nope"},
	} {
		if refused := d.decide(dOpts{}, args...); refused.Code != 2 {
			t.Errorf("%v: code = %d\n%s", args, refused.Code, refused.Stderr)
		}
	}
	if len(d.calls()) != 0 || len(d.gl.kinds()) != 0 {
		t.Errorf("呼び出し = %v, 書き込み = %v", d.calls(), d.gl.kinds())
	}
}

// ccnavi-review.sh decide は --preview だけを受ける。置く形は何も動かさずに 2 で断る。
func TestReviewShDecideTakesOnlyPreview(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	for _, args := range [][]string{
		{"1"}, {"1", "--choices", `{"u1":"keep"}`, "--digest", "d0"}, {"1", "--preview", "--choices", "{}", "--digest", "d0"}, {"1", "--yes", "{}"},
	} {
		if refused := d.decide(dOpts{script: reviewSh}, args...); refused.Code != 2 {
			t.Errorf("%v: code = %d\n%s", args, refused.Code, refused.Stderr)
		}
	}
	if len(d.calls()) != 0 || len(d.gl.kinds()) != 0 || d.gl.fetched() != 0 || len(d.leftovers()) != 0 {
		t.Errorf("呼び出し = %v, 書き込み = %v, 取得 = %d, 残り = %v", d.calls(), d.gl.kinds(), d.gl.fetched(), d.leftovers())
	}
}

// 見るだけの --preview は ccnavi-review.sh に残る。ユーザ専用の sh は断って案内する。
func TestTheUserScriptPointsThePreviewBack(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	refused := d.decide(dOpts{}, "1", "--preview")
	if refused.Code != 2 || !strings.Contains(refused.Stderr, "ccnavi-review.sh decide <N> --preview") || len(d.calls()) != 0 {
		t.Errorf("code = %d, 呼び出し = %v\n%s", refused.Code, d.calls(), refused.Stderr)
	}
}

// 抜けるときの片付け（EXIT の trap）で rm が失敗しても、元の終了コードで抜ける（dash と bash）。rm のスタブを PATH の頭に置く形は POSIX で見る。
func TestAFailingRmInTheExitTrapKeepsTheExitCode(t *testing.T) {
	t.Parallel()
	if os.PathSeparator == '\\' {
		t.Skip("rm のスタブを PATH の頭に置く形は POSIX で見る")
	}
	var shells []string
	for _, name := range []string{"dash", "bash"} {
		if found, err := exec.LookPath(name); err == nil {
			shells = append(shells, found)
		}
	}
	if len(shells) == 0 {
		t.Skip("dash も bash も無い")
	}
	for _, shell := range shells {
		t.Run(filepath.Base(shell), func(t *testing.T) {
			t.Parallel()
			d := newDecideSite(t)
			d.putTools() // 先に標準コマンドを置き、rm だけを失敗するスタブに替える
			remove(t, filepath.Join(d.tools, "rm"))
			writeExec(t, filepath.Join(d.tools, "rm"), "#!/bin/sh\necho 'rm: cannot remove' >&2\nexit 1\n")
			refused := d.decide(dOpts{shell: shell, script: reviewSh}, "x")
			if refused.Code != 2 || refused.Stderr != "ccnavi-review: decide には <N>（フェーズ番号）が必要です。\n" {
				t.Errorf("code = %d, stderr = %q", refused.Code, refused.Stderr)
			}
			shown := d.decide(dOpts{shell: shell, script: reviewSh}, "1", "--preview")
			if shown.Code != 0 || decode(t, shown.Stdout)["digest"] != "d0" {
				t.Errorf("code = %d\n%s%s", shown.Code, shown.Stdout, shown.Stderr)
			}
		})
	}
}
