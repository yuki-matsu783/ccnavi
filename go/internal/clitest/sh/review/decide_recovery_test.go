package review

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// decide の復旧と照合の穴を確かめる sh の受入テスト（ccnavi-user-decide.sh）。道具は decide_test.go と同じ。
//
//  1. AT43: ホストに書いたあとの照合が外れたとき、続きの子もマーカーも置かず（置く呼び出しが断る）、ホストの解決は残り、
//     操作記録に解決した分が残る
//  2. AT19: 置く段階で 2 回失敗し、あとから --resume で完了させる。返信は 1 回だけ
//  3. AT17: 適用済み（applied.local）の記録を --resume しても、自分の変化を食い違いとして断らない
//  4. AT25: 親のワークツリーを消したあと、ワークスペースのルートから --resume する
//  5. AT20: 操作のロック（sh の `locks/ops/<操作ID>`）が取られている間の --resume は投稿しない
//
// 実行ファイル側の AT43・AT13・AT24 は clitest/ticket（decide の試験）が見る。

// AT43（sh の側。実行ファイルの照合そのものは clitest/ticket が見る）
func TestDecideAMismatchAfterTheHostWriteKeepsTheResolvedPartAndPlacesNothing(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	failed := d.decide(dOpts{env: map[string]string{"STUB_MISMATCH": "1"}}, "1", "--choices", `{"u1":"issue"}`, "--digest", "d0")
	if failed.Code != 1 || answerOf(t, failed.Stdout)["mismatch"] != true || !strings.Contains(failed.Stderr, "何も置いていない") {
		t.Fatalf("code = %d\n%s%s", failed.Code, failed.Stdout, failed.Stderr)
	}
	// ホストで解決した分は残る。issue もコメントも作らない
	issues, notes := d.gl.stored()
	if fmt.Sprint(d.gl.kinds()) != "[reply resolve]" || !d.gl.resolved("d1") || len(issues) != 0 || len(notes) != 0 {
		t.Errorf("書き込み = %v, 解決 = %v", d.gl.kinds(), d.gl.resolved("d1"))
	}
	// 記録に解決した分が残り、適用（local）は立たない。計画も残す
	applied := d.record()["applied"].(map[string]any)
	if applied["accepted_on_host"] == nil || applied["local"] == true || !strings.Contains(failed.Stderr, "計画を残した") ||
		!exists(filepath.Join(d.state, "review-accept-i0001-1.json")) {
		t.Errorf("applied = %v, 残り = %v\n%s", applied, d.leftovers(), failed.Stderr)
	}
}

// AT19（置く段階で 2 回続けて失敗しても、返信は 1 回）
func TestDecideAFailedPlacementThenResumeRepliesOnceAndPlacesOnce(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	failed := d.decide(dOpts{env: map[string]string{"STUB_FAIL_YES": "1"}}, "1", "--choices", `{"u1":"issue"}`, "--digest", "d0")
	if failed.Code != 1 || fmt.Sprint(d.gl.kinds()) != "[reply resolve]" || d.record()["applied"].(map[string]any)["local"] == true {
		t.Fatalf("code = %d, 書き込み = %v\n%s", failed.Code, d.gl.kinds(), failed.Stderr)
	}
	if again := d.decide(dOpts{input: "y\n", env: map[string]string{"STUB_FAIL_YES": "1"}}, "--resume"); again.Code != 1 {
		t.Fatalf("2 回目の失敗: code = %d\n%s%s", again.Code, again.Stdout, again.Stderr)
	}
	done := d.decide(dOpts{input: "y\n"}, "--resume")
	if done.Code != 0 || d.gl.count("reply") != 1 || d.gl.count("resolve") != 1 { // 返信は 1 回だけ
		t.Fatalf("code = %d, 書き込み = %v\n%s%s", done.Code, d.gl.kinds(), done.Stdout, done.Stderr)
	}
	// 置く呼び出し（続きの子・マーカーを置くところ）は、失敗した 2 回と成功した 1 回
	var placing, planning int
	for _, c := range d.calls() {
		if strings.Contains(c, "--accept-done") {
			placing++
		}
		if strings.Contains(c, "--accept-plan") {
			planning++
		}
	}
	issues, notes := d.gl.stored()
	var plans []string // 投稿も 1 回ずつ。記録は消え、置けたので計画も消える
	for _, name := range d.leftovers() {
		if strings.HasPrefix(name, "review-accept") {
			plans = append(plans, name)
		}
	}
	if placing != 3 || planning != 1 || len(issues) != 1 || len(notes) != 1 || d.record() != nil || len(plans) != 0 {
		t.Errorf("置く = %d, 計画 = %d, issue = %d, note = %d, 計画のファイル = %v", placing, planning, len(issues), len(notes), plans)
	}
}

// AT17: 適用済みの記録を --resume しても、自分の変化（作った子・動かした子）を食い違いとして断らない。
func TestDecideResumeDoesNotRefuseItsOwnChangesWhenTheRecordSaysApplied(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	d.rejecting("note", true)
	d.choose(nil)
	rec := d.record()
	if rec["applied"].(map[string]any)["local"] != true {
		t.Fatalf("applied = %v", rec["applied"])
	}
	// 自分が作った子と動かした子を記録に足す。ホストは、直す指摘が未解決のまま、指摘も増えた
	applied := rec["applied"].(map[string]any)
	applied["followup"], applied["moved"] = "i0001-01-03", []any{"i0001-01-01", "i0001-01-02"}
	d.save(rec)
	d.gl.do(func() { d.gl.threads = []*thread{{id: "d1", resolved: true}, {id: "d2"}, {id: "d3"}} })
	d.rejecting("note", false)
	d.age(fmt.Sprint(rec["id"]), "note")
	before := len(d.calls())
	done := d.decide(dOpts{input: "y\n"}, "--resume")
	// 置く呼び出し・計画は打たない（適用は済んでいるので、投稿の段階だけを進める）
	var later []string
	for _, c := range d.calls()[before:] {
		if strings.Contains(c, "--accept") || strings.Contains(c, "--yes") {
			later = append(later, c)
		}
	}
	issues, notes := d.gl.stored()
	if done.Code != 0 || strings.Contains(done.Stderr, "違う") || strings.Contains(done.Stdout, "mismatch") || len(later) != 0 ||
		len(notes) != 1 || len(issues) != 1 || d.record() != nil {
		t.Errorf("code = %d, 打った = %v, issue = %d, note = %d\n%s%s", done.Code, later, len(issues), len(notes), done.Stdout, done.Stderr)
	}
}

// parentWorktree は `<ws>/.claude/worktrees/i0001`。親のブランチ（i0001）のワークツリーの代わりの git。
func (d *decideSite) parentWorktree() string {
	d.t.Helper()
	where := filepath.Join(d.ws, ".claude", "worktrees", "i0001")
	d.initRepo(where, "i0001", d.gl.url()+"/demo/greeter.git")
	return where
}

// stoppedAtTheNote はコメントの段階で止まった判断（時間が経った）を作る。
func (d *decideSite) stoppedAtTheNote() {
	d.t.Helper()
	d.rejecting("note", true)
	d.choose(nil)
	rec := d.record()
	d.rejecting("note", false)
	d.age(fmt.Sprint(rec["id"]), "note")
}

// AT25（親のワークツリーが在れば、ルートから打っても続きを送れる）。
// 親のワークツリーを消したあとは、実装が「親のワークツリーに必ず移り、無ければ断る」ので、ルートからは続けられない
// （設計は「対象は記録の repo から決める」。実装との差は残る）。
func TestDecideResumeFromTheWorkspaceRootMovesToTheParentWorktree(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	d.parentWorktree()
	d.stoppedAtTheNote()
	done := d.decide(dOpts{input: "y\n", cwd: d.ws}, "--resume")
	if _, notes := d.gl.stored(); done.Code != 0 || len(notes) != 1 || d.record() != nil {
		t.Errorf("code = %d, notes = %d\n%s%s", done.Code, len(notes), done.Stdout, done.Stderr)
	}
}

// AT20: 操作IDのロック中の --resume は、投稿せずに断る（同時実行の代わりの決定的な形）。
func TestDecideTwoResumesOfTheSameOperationDoNotPostTwice(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	d.rejecting("issue", true)
	d.choose(nil)
	rec := d.record()
	id := fmt.Sprint(rec["id"])
	d.rejecting("issue", false)
	d.age(id, "issue")
	// もう一方の --resume が実行中の状態（新しいロックファイル）を作る。持ち主は sh から生きて見えるプロセス
	lock := filepath.Join(d.state, "locks", "ops", id)
	host := strings.TrimSpace(run.Command(t, "hostname", nil).Stdout)
	now := time.Now().Unix()
	pid := base.LiveShPid(t)
	write(t, filepath.Join(lock, "owner"), fmt.Sprintf("%s %d %d %d-%d\n", host, pid, now, pid, now))
	// 最初の判断で issue 以外の段階（note）は送り済み。--resume でも増えないことを確かめる
	_, notesBefore := d.gl.stored()
	callsBefore := len(d.calls())
	// 待つ秒を縮める（既定の 30 秒を待たずに、取れないと分かる）
	result := d.decide(dOpts{input: "y\n", env: map[string]string{"CCNAVI_LOCK_WAIT": "1"}}, "--resume")
	issues, notes := d.gl.stored()
	if result.Code == 0 || len(issues) != 0 || len(notes) != len(notesBefore) || len(d.calls()) != callsBefore ||
		!strings.Contains(result.Stderr, "ロックを他の操作が持っている") {
		t.Errorf("code = %d, issue = %d, notes = %d→%d, 呼び出し = %v\n%s", result.Code, len(issues), len(notesBefore), len(notes), d.calls()[callsBefore:], result.Stderr)
	}
	if info, err := os.Stat(lock); err != nil || !info.IsDir() {
		t.Errorf("他のプロセスのロックを消した")
	}
}

// AT20: 新規の判断も --resume も、終わったら操作IDのロックを残さない。
func TestDecideTheOperationLockIsReleasedAfterANewDecisionAndAfterResume(t *testing.T) {
	t.Parallel()
	d := newDecideSite(t)
	locks := func() []string {
		entries, _ := os.ReadDir(filepath.Join(d.state, "locks", "ops"))
		var out []string
		for _, e := range entries {
			out = append(out, e.Name())
		}
		return out
	}
	d.choose(nil)
	if l := locks(); len(l) != 0 {
		t.Fatalf("新規の判断が操作IDのロックを残した: %v", l)
	}
	// 投稿が拒まれて止まった判断を --resume で通す
	d.rejecting("issue", true)
	d.choose(nil)
	rec := d.record()
	if l := locks(); len(l) != 0 {
		t.Fatalf("投稿に失敗した判断がロックを残した: %v", l)
	}
	d.rejecting("issue", false)
	d.age(fmt.Sprint(rec["id"]), "issue")
	done := d.decide(dOpts{input: "y\n"}, "--resume")
	if l := locks(); done.Code != 0 || len(l) != 0 {
		t.Errorf("code = %d, --resume がロックを残した: %v\n%s%s", done.Code, l, done.Stdout, done.Stderr)
	}
}
