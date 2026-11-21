package git

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// ロックの強制取得の排他を固定する試験（ccnavi-common-lock.sh の ccnavi_lock_take）。古いロックを取り合っても、
// 2 つのプロセスが同時に臨界区間に入らないことを見る。
//   - 5 つのプロセスが同時に 1 つの古いロックを取りに行き、臨界区間（mkdir で作る印）が重ならない
//   - 時系列: A が古いと判断して強制取得の入口で止まる間に、B が回収して生きたロックを持つ。再開した A が B のロックを奪わない。
//     強制取得の入口で owner の行を読み直す比較が、これを守る
//
// 守る範囲: 古いと判断してから強制取得に入るまでの間に持ち主が替わる場合。mv の直前の比較と mv の間に持ち主が替わる場合
// （mv 後の再確認が守る）と、.steal ゲート（外しても排他は保たれ、戻し損ねて人の対応になる確率が上がる）は、この試験では見ない。
// 時系列の試験の遅延は、スクリプトのコピーに差し込む。本番のスクリプトには入れない。

const (
	lockParent     = "i0001"
	lockContenders = 5
	lockRounds     = 30
)

// 引数は <ロックの部品> <モード> <ワークスペース> <同期用のフォルダ>。
const lockDriver = `. "$1"
mode=$2
ws=$3
dir=$4
log_warn() { :; }
log_info() { :; }
log_debug() { :; }
ccnavi_state() { printf '%s\n' "$1/state"; }
case "$mode" in
race)
	: >"$dir/ready.$$"
	while [ ! -e "$dir/go" ]; do :; done
	ccnavi_lock_take "$ws" self "$PARENT" 0
	rc=$?
	[ "$rc" -eq 0 ] || exit 0
	if mkdir "$dir/crit" 2>/dev/null; then
		i=0
		while [ "$i" -lt 3000 ]; do i=$((i + 1)); done
		rmdir "$dir/crit"
		: >"$dir/entered.$$"
	else
		: >"$dir/overlap.$$"
	fi
	ccnavi_lock_drop
	;;
a)
	ccnavi_lock_take "$ws" self "$PARENT" 0
	rc=$?
	echo "$rc" >"$dir/a.rc"
	[ "$rc" -ne 0 ] || : >"$dir/a.entered"
	;;
b)
	ccnavi_lock_take "$ws" self "$PARENT" 0 || exit 1
	echo "$ccnavi_lock_mark" >"$dir/b.mark"
	: >"$dir/b.held"
	while [ ! -e "$dir/b.release" ]; do sleep 0.05; done
	ccnavi_lock_drop
	;;
esac
`

// ccnavi_lock_steal の入口で止まる（A が古いと判断したあと、強制取得に入る前に遅れる）。
const lockPause = `	if [ -n "${CCNAVI_TEST_PAUSE_DIR:-}" ]; then
		: >"$CCNAVI_TEST_PAUSE_DIR/a.waiting"
		while [ ! -e "$CCNAVI_TEST_PAUSE_DIR/a.go" ]; do sleep 0.05; done
	fi
`

func waitFor(path string, d time.Duration) bool {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if shkit.Exists(path) {
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return shkit.Exists(path)
}

type lockWS struct {
	t      *testing.T
	tmp    string
	ws     string
	dir    string
	driver string
	lockSh string
}

func newLockWS(t *testing.T) *lockWS {
	t.Helper()
	tmp := base.TempDir(t)
	l := &lockWS{t: t, tmp: tmp, ws: filepath.Join(tmp, "ws"), dir: filepath.Join(tmp, "sync"), lockSh: filepath.Join(base.ScriptsDir(), "ccnavi-common-lock.sh")}
	if err := os.MkdirAll(l.dir, 0o755); err != nil {
		t.Fatal(err)
	}
	l.driver = shkit.Write(t, filepath.Join(tmp, "driver.sh"), lockDriver)
	return l
}

func (l *lockWS) lockDir() string { return filepath.Join(l.ws, "state", "locks", "self", lockParent) }

// staleLock は、持ち主の pid が死んでいる、同じホストのロックを置く。
func (l *lockWS) staleLock() string {
	l.t.Helper()
	out, err := exec.Command("sh", "-c", "echo $$").Output()
	if err != nil {
		l.t.Fatal(err)
	}
	pid := strings.TrimSpace(string(out))
	host, err := os.Hostname()
	if err != nil {
		l.t.Fatal(err)
	}
	started := time.Now().Unix()
	shkit.Write(l.t, filepath.Join(l.lockDir(), "owner"), fmt.Sprintf("%s %s %d %s-%d\n", host, pid, started, pid, started))
	return l.lockDir()
}

func (l *lockWS) start(mode, lockSh string, extra map[string]string) *exec.Cmd {
	l.t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	l.t.Cleanup(cancel)
	env := map[string]string{"PARENT": lockParent}
	for k, v := range extra {
		env[k] = v
	}
	cmd := exec.CommandContext(ctx, "sh", l.driver, lockSh, mode, l.ws, l.dir)
	cmd.Env = run.Environment(run.Opts{Env: env})
	if err := cmd.Start(); err != nil {
		l.t.Fatal(err)
	}
	return cmd
}

func TestContendersForAStaleLockNeverOverlap(t *testing.T) {
	t.Parallel()
	shkit.Need(t, "sh")
	l := newLockWS(t)
	for round := 0; round < lockRounds; round++ {
		_ = os.RemoveAll(l.ws)
		_ = os.RemoveAll(l.dir)
		if err := os.MkdirAll(l.dir, 0o755); err != nil {
			t.Fatal(err)
		}
		l.staleLock()
		var procs []*exec.Cmd
		for i := 0; i < lockContenders; i++ {
			procs = append(procs, l.start("race", l.lockSh, nil))
		}
		// 全員が部品を読み終えて合図を待つまで待つ（決まった時間は待たない）
		deadline := time.Now().Add(60 * time.Second)
		for len(withPrefix(shkit.Names(t, l.dir), "ready.")) != lockContenders && time.Now().Before(deadline) {
			time.Sleep(5 * time.Millisecond)
		}
		if got := len(withPrefix(shkit.Names(t, l.dir), "ready.")); got != lockContenders {
			t.Fatalf("round %d: 取りに行くプロセスが揃わなかった（%d）", round, got)
		}
		shkit.Write(t, filepath.Join(l.dir, "go"), "")
		for _, p := range procs {
			_ = p.Wait()
		}
		all := shkit.Names(t, l.dir)
		if overlaps := withPrefix(all, "overlap."); len(overlaps) > 0 {
			t.Fatalf("round %d: 臨界区間が重なった: %v", round, overlaps)
		}
		if len(withPrefix(all, "entered.")) < 1 {
			t.Fatalf("round %d: 古いロックを誰も取れなかった", round)
		}
		if shkit.Exists(filepath.Join(l.dir, "crit")) {
			t.Fatalf("round %d: 臨界区間の印が残った", round)
		}
	}
}

func TestALateThiefDoesNotRobTheLockACollectorTookInBetween(t *testing.T) {
	t.Parallel()
	shkit.Need(t, "sh")
	l := newLockWS(t)
	text := shkit.Read(t, l.lockSh)
	head := "ccnavi_lock_steal() {\n"
	if strings.Count(text, head) != 1 {
		t.Fatal("差し込む場所が見つからない")
	}
	patched := shkit.Write(t, filepath.Join(l.tmp, "ccnavi-common-lock.sh"), strings.Replace(text, head, head+lockPause, 1))
	lock := l.staleLock()
	// A は古いと判断して、強制取得の入口で止まる。
	a := l.start("a", patched, map[string]string{"CCNAVI_TEST_PAUSE_DIR": l.dir})
	if !waitFor(filepath.Join(l.dir, "a.waiting"), 60*time.Second) {
		t.Fatal("A が強制取得に入らなかった")
	}
	// その間に B が古いロックを回収して、生きたロックを持つ。
	b := l.start("b", l.lockSh, nil)
	t.Cleanup(func() { _ = b.Process.Kill() })
	if !waitFor(filepath.Join(l.dir, "b.held"), 60*time.Second) {
		t.Fatal("B がロックを取れなかった")
	}
	mark := strings.TrimSpace(shkit.Read(t, filepath.Join(l.dir, "b.mark")))
	// A が再開する。
	shkit.Write(t, filepath.Join(l.dir, "a.go"), "")
	_ = a.Wait()
	if shkit.Exists(filepath.Join(l.dir, "a.entered")) {
		t.Error("A が B のロックを奪った")
	}
	if rc := strings.TrimSpace(shkit.Read(t, filepath.Join(l.dir, "a.rc"))); rc != "1" {
		t.Errorf("A の終了コード = %q, want 1", rc)
	}
	if owner := shkit.Read(t, filepath.Join(lock, "owner")); !strings.Contains(owner, " "+mark) {
		t.Errorf("B のロックの持ち主が替わった: %q（B の印 %q）", owner, mark)
	}
	shkit.Write(t, filepath.Join(l.dir, "b.release"), "")
	if err := b.Wait(); err != nil {
		t.Errorf("B が正常に終わらなかった: %v", err)
	}
	if shkit.Exists(lock) || shkit.Exists(lock+".steal") {
		t.Error("ロックか .steal が残った")
	}
}
