package actors

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"

	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
)

// RunTimeout は human_run の 1 回の上限。
const RunTimeout = 300 * time.Second

// ErrNoTerminal は擬似端末を作れない（`script` が無い・Windows）。
var ErrNoTerminal = errors.New("擬似端末を作れない")

// ErrTimeout は端末の操作が時間内に終わらなかった。
var ErrTimeout = errors.New("時間切れ")

// Ticket はボード（`ccnavi --explain --json`）のチケット 1 件。
type Ticket struct {
	ID      string
	Project string
	// Open は承認済みで閉じていない。
	Open bool
}

// Board はボードのうち human_run が読む所。Pending は承認待ちの識別子。
type Board struct {
	Tickets []Ticket
	Pending []string
}

// pending は承認待ちの提案（識別子, プロジェクト）。複数のツリーに同じ識別子が出るので畳む。
func (b Board) pending() []core.Proposal {
	waiting := map[string]bool{}
	for _, id := range b.Pending {
		waiting[id] = true
	}
	seen := map[core.Proposal]bool{}
	out := []core.Proposal{}
	for _, t := range b.Tickets {
		p := core.Proposal{t.ID, t.Project}
		if waiting[t.ID] && !seen[p] {
			seen[p] = true
			out = append(out, p)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i][0] != out[j][0] {
			return out[i][0] < out[j][0]
		}
		return out[i][1] < out[j][1]
	})
	return out
}

// Proposals は承認待ちの提案（識別子, プロジェクト）。複数のツリーに同じ識別子が出るので畳む。
func (b Board) Proposals() []core.Proposal { return b.pending() }

// approved は承認済みで閉じていないチケットの識別子（全ツリー）。
func (b Board) approved() map[string]bool {
	out := map[string]bool{}
	for _, t := range b.Tickets {
		if t.Open {
			out[t.ID] = true
		}
	}
	return out
}

// RunSpec は human_run の step（script は実行器が解決した sh のパス）。
type RunSpec struct {
	Script  string
	Answers []string
}

// humanEnv は人間役の既定の環境。親の環境から承認の保護（CCNAVI_GUARD_TICKET_APPROVAL）を外し、ワークスペースを root にする。
// 保護が残ると、端末から打つ承認が通らない。
func humanEnv(root string) []string {
	out := []string{}
	for _, kv := range os.Environ() {
		name, _, _ := strings.Cut(kv, "=")
		if name != env.GuardTicketApproval && name != env.Workspace {
			out = append(out, kv)
		}
	}
	return append(out, env.Workspace+"="+root)
}

// Terminal は human_run を打つ。欄を差し替えられる（nil は既定）。
type Terminal struct {
	// Board は ccnavi のボードを読む（`ccnavi --explain --json --root <root>`）。必須。
	Board func(root string) (Board, error)
	// Exec は argv を端末に付けて起動し、（終了コード, 出力）を返す。nil なら RunOnTerminal。
	Exec func(argv []string, dir string, env []string, answers []string, timeout time.Duration) (int, string, error)
	// Env は人間役の環境（模擬のホストでは、push 先を模擬に向けるため claude と同じ環境）。nil なら humanEnv。
	Env func(root string) []string
	// Timeout は 1 回の上限。0 なら RunTimeout。
	Timeout time.Duration
}

func halt(st *core.State, save func() error, out func(string), num int, reason string) int {
	st.Halt = &core.Halt{Step: num, Kind: "human_run", Reasons: []string{reason}}
	_ = save()
	out(fmt.Sprintf("step %d: 人間役の操作を止めた（%s）。後続の step には進まない", num, reason))
	return 11
}

// 結果の意味。
const (
	// Pause は、端末を作れないので、人間の step と同じく一時停止させる。
	Pause = -1
	// Done は、進んだ。
	Done = 0
	// Halted は、止めた（終了コード 11）。
	Halted = 11
)

// Run は human_run の sh を人間役として端末で打ち、止まらずに次の step へ進む。止めたら Halted、進んだら Done、
// 端末を作れないときは Pause。idx は step の 0 始まりの番号。
//
// 処理フロー:
//  1. 前の回で打ち始めたまま止まった記録があれば、打ち直さずに止める
//  2. 承認待ちの提案を集め、基準（回し始めか、前の human_run のあと）に無い識別子を対象にする。
//     0 件か、test-repo 以外の提案が混ざっていれば止める（黙って外すと親だけ落ちて承認が失敗する）
//  3. 打ち始めたことを state に書いてから、端末で打つ
//  4. 終了コードでなく、対象が新しく承認済みになったか、打つ前から承認済みで承認待ちから消えたか（親の改版）で成否を見る
//     （push の失敗や保護ブランチでも 0 で終わるため。承認待ちのまま残った改版は数えない）
func (tm Terminal) Run(st *core.State, idx int, spec RunSpec, root, outDir string, save func() error, out func(string)) int {
	num := idx + 1
	for _, r := range st.HumanRuns {
		if r.Step == num && r.Status == "started" {
			return halt(st, save, out, num, "前の回で打ち始めたまま止まった。承認と push の状態を確かめてから run でやり直す")
		}
	}
	if st.ProposalsBefore == nil {
		// 基準が無いと、承認待ちが全部「増えた」ことになる。打たずに止める
		return halt(st, save, out, num, "承認待ちの基準が state に無い。run でやり直す")
	}
	before := map[string]bool{}
	for _, p := range st.ProposalsBefore {
		before[p[0]] = true
	}
	board, err := tm.Board(root)
	if err != nil {
		return halt(st, save, out, num, err.Error())
	}
	var added []core.Proposal
	for _, p := range board.pending() {
		if !before[p[0]] {
			added = append(added, p)
		}
	}
	if len(added) == 0 {
		return halt(st, save, out, num, "承認する提案が増えていない")
	}
	var other []string
	idSet := map[string]bool{}
	for _, p := range added {
		idSet[p[0]] = true
		if !pathcase.SameName(p[1], core.DisposableProject) {
			proj := p[1]
			if proj == "" {
				proj = "ワークスペースルート"
			}
			other = append(other, fmt.Sprintf("%s（%s）", p[0], proj))
		}
	}
	if len(other) > 0 {
		return halt(st, save, out, num, core.DisposableProject+" 以外の提案が増えている: "+strings.Join(other, ", "))
	}
	ids := make([]string, 0, len(idSet))
	for id := range idSet {
		ids = append(ids, id)
	}
	sort.Strings(ids)

	st.HumanRuns = append(st.HumanRuns, core.HumanRun{Step: num, Script: spec.Script, Tickets: ids, Status: "started"})
	rec := &st.HumanRuns[len(st.HumanRuns)-1]
	_ = save()
	out(fmt.Sprintf("\n== 人間役の操作（step %d、端末）==\n  sh %s %s", num, spec.Script, strings.Join(ids, " ")))
	logPath := filepath.Join(outDir, fmt.Sprintf("human-run-%d.log", num))
	approvedBefore := board.approved()

	timeout := tm.Timeout
	if timeout == 0 {
		timeout = RunTimeout
	}
	run := tm.Exec
	if run == nil {
		run = RunOnTerminal
	}
	env := humanEnv(root)
	if tm.Env != nil {
		env = tm.Env(root)
	}
	rc, text, err := run(append([]string{"sh", spec.Script}, ids...), root, env, spec.Answers, timeout)
	switch {
	case errors.Is(err, ErrNoTerminal):
		st.HumanRuns = st.HumanRuns[:len(st.HumanRuns)-1]
		_ = save()
		return Pause
	case errors.Is(err, ErrTimeout):
		rec.Status = "halted"
		return halt(st, save, out, num, fmt.Sprintf("%d 秒で終わらなかった", int(timeout.Seconds())))
	case err != nil: // sh や dir が無いなど、起動できない
		rec.Status = "halted"
		return halt(st, save, out, num, fmt.Sprintf("起動できない: %v", err))
	}
	_ = os.WriteFile(logPath, []byte(text), 0o644)
	lines := strings.Split(strings.TrimSpace(text), "\n")
	if len(lines) > 5 {
		lines = lines[len(lines)-5:]
	}
	for _, l := range lines {
		out("  | " + l)
	}
	rec.Exit, rec.Log = &rc, logPath
	after, err := tm.Board(root)
	if err != nil {
		rec.Status = "halted"
		return halt(st, save, out, num, err.Error())
	}
	nowPending := after.pending()
	pendingIDs := map[string]bool{}
	for _, p := range nowPending {
		pendingIDs[p[0]] = true
	}
	// 新しく承認済みになったか、打つ前から承認済みで承認待ちから消えた（親の改版）なら成功
	approved := map[string]bool{}
	for id := range after.approved() {
		if !approvedBefore[id] {
			approved[id] = true
		}
	}
	for id := range approvedBefore {
		if !pendingIDs[id] {
			approved[id] = true
		}
	}
	var missing []string
	for _, id := range ids {
		if !approved[id] {
			missing = append(missing, id)
		}
	}
	if len(missing) > 0 {
		rec.Status = "halted"
		return halt(st, save, out, num, fmt.Sprintf("新しく承認済みにならなかった: %s（終了コード %d、ログ: %s）", strings.Join(missing, ", "), rc, logPath))
	}
	rec.Status = "done"
	st.ProposalsBefore = nowPending
	st.NextStep = num
	_ = save()
	return Done
}

// RunOnTerminal は argv を擬似端末に付けて起動し、（終了コード, 出力）を返す。出力の CR は落とす。
// 擬似端末は `script` で包む（Linux は `script -qec <コマンド> /dev/null`、macOS は `script -q /dev/null <argv…>`）。
// 答えは標準入力から送る（端末は行単位の入力で、sh が読むまで溜まる）。時間切れは ErrTimeout で、script だけを殺す。
// 子（sh）と孫は、擬似端末の master が閉じて SIGHUP を受けて終わる。env が nil なら親の環境。
func RunOnTerminal(argv []string, dir string, env []string, answers []string, timeout time.Duration) (int, string, error) {
	args, err := scriptArgs(argv, exec.LookPath)
	if err != nil {
		return 0, "", err
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, args[0], args[1:]...)
	cmd.Dir, cmd.Env = dir, env
	cmd.Stdin = strings.NewReader(strings.Join(answers, "\n") + func() string {
		if len(answers) > 0 {
			return "\n"
		}
		return ""
	}())
	var buf lockedBuffer
	cmd.Stdout, cmd.Stderr = &buf, &buf
	cmd.WaitDelay = 2 * time.Second
	groupKill(cmd)
	err = cmd.Run()
	text := strings.ReplaceAll(buf.String(), "\r", "")
	if ctx.Err() != nil {
		return 0, text, ErrTimeout
	}
	var exit *exec.ExitError
	switch {
	case errors.As(err, &exit):
		return exit.ExitCode(), text, nil
	case err != nil:
		return 0, text, err
	}
	return 0, text, nil
}

// scriptArgs は `script` で包んだ起動の組み立て。
func scriptArgs(argv []string, lookPath func(string) (string, error)) ([]string, error) {
	if ospath.Windows {
		return nil, ErrNoTerminal
	}
	script, err := lookPath("script")
	if err != nil {
		return nil, ErrNoTerminal
	}
	if runtime.GOOS == "darwin" {
		return append([]string{script, "-q", "/dev/null"}, argv...), nil
	}
	quoted := make([]string, len(argv))
	for i, a := range argv {
		quoted[i] = "'" + strings.ReplaceAll(a, "'", `'\''`) + "'"
	}
	return []string{script, "-qec", strings.Join(quoted, " "), "/dev/null"}, nil
}

// ParseBoard はボードの JSON（`ccnavi --explain --json`）から、チケットの一覧と承認待ちの識別子を読む。
func ParseBoard(data []byte) (Board, error) {
	var raw struct {
		Pending []string `json:"pending_approval"`
		Tickets []struct {
			Ticket  string `json:"ticket"`
			Project string `json:"project"`
			Copy    struct {
				Status string `json:"status"`
			} `json:"copy"`
		} `json:"tickets"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return Board{}, fmt.Errorf("ボードの JSON を読めない: %w", err)
	}
	b := Board{Pending: raw.Pending}
	for _, t := range raw.Tickets {
		b.Tickets = append(b.Tickets, Ticket{ID: t.Ticket, Project: t.Project, Open: t.Copy.Status == "open"})
	}
	return b, nil
}

// lockedBuffer は標準出力と標準エラーを 1 本に集める（exec が別々の goroutine で書く）。
type lockedBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *lockedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *lockedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}
