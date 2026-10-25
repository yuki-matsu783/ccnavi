"""ロックの強制取得の排他を固定する試験（`ccnavi-common-lock.sh` の `ccnavi_lock_take`）。

古いロックを取り合っても、2 つのプロセスが同時に臨界区間に入らないことを見る。

- 5 つのプロセスが同時に 1 つの古いロックを取りに行き、臨界区間（`mkdir` で作る印）が重ならない
- 時系列: A が古いと判断して強制取得の入口で止まる間に、B が回収して生きたロックを持つ。
  再開した A が B のロックを奪わない。強制取得の入口で owner の行を読み直す比較が、これを守る

守る範囲: 古いと判断してから強制取得に入るまでの間に持ち主が替わる場合。
`mv` の直前の比較と `mv` の間に持ち主が替わる場合（`mv` 後の再確認が守る）と、`.steal` ゲート
（外しても排他は保たれ、戻し損ねて人の対応になる確率が上がる）は、この試験では見ない。

時系列の試験の遅延は、スクリプトのコピーに差し込む。本番のスクリプトには入れない。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

from tests import SH_SCRIPTS

SHELL = shutil.which("sh") or shutil.which("bash")
LOCK_SH = os.path.join(SH_SCRIPTS, "ccnavi-common-lock.sh")
PARENT = "i0001"
CONTENDERS = 5
ROUNDS = 30

# <ロックの部品> <モード> <ワークスペース> <同期用のフォルダ>
DRIVER = """\
. "$1"
mode=$2
ws=$3
dir=$4
log_warn() { :; }
log_info() { :; }
log_debug() { :; }
ccnavi_state() { printf '%s\\n' "$1/state"; }
case "$mode" in
race)
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
"""

# `ccnavi_lock_steal` の入口で止まる（A が古いと判断したあと、強制取得に入る前に遅れる）。
PAUSE = """\
	if [ -n "${CCNAVI_TEST_PAUSE_DIR:-}" ]; then
		: >"$CCNAVI_TEST_PAUSE_DIR/a.waiting"
		while [ ! -e "$CCNAVI_TEST_PAUSE_DIR/a.go" ]; do sleep 0.05; done
	fi
"""


def wait_for(path, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.02)
    return False


@unittest.skipIf(SHELL is None, "sh が無い")
class LockExclusionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        self.dir = os.path.join(self._tmp.name, "sync")
        os.makedirs(self.dir)
        self.driver = os.path.join(self._tmp.name, "driver.sh")
        with open(self.driver, "w", encoding="utf-8", newline="\n") as f:
            f.write(DRIVER)
        self.host = socket.gethostname()

    def lock_dir(self):
        return os.path.join(self.ws, "state", "locks", "self", PARENT)

    def dead_pid(self):
        done = subprocess.run([SHELL, "-c", "echo $$"], capture_output=True, text=True)
        return done.stdout.strip()

    def stale_lock(self):
        """持ち主の pid が死んでいる、同じホストのロックを置く。"""
        lock = self.lock_dir()
        os.makedirs(lock)
        started = int(time.time())
        with open(os.path.join(lock, "owner"), "w", encoding="utf-8", newline="\n") as f:
            pid = self.dead_pid()
            f.write(f"{self.host} {pid} {started} {pid}-{started}\n")
        return lock

    def start(self, mode, lock_sh=LOCK_SH, **env):
        full = dict(os.environ, PARENT=PARENT, **env)
        return subprocess.Popen(
            [SHELL, self.driver, lock_sh, mode, self.ws, self.dir],
            env=full,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def test_contenders_for_a_stale_lock_never_overlap(self):
        for round_no in range(ROUNDS):
            with self.subTest(round=round_no):
                shutil.rmtree(self.ws, ignore_errors=True)
                shutil.rmtree(self.dir, ignore_errors=True)
                os.makedirs(self.dir)
                self.stale_lock()
                procs = [self.start("race") for _ in range(CONTENDERS)]
                time.sleep(0.2)
                with open(os.path.join(self.dir, "go"), "w"):
                    pass
                for proc in procs:
                    proc.wait(timeout=60)
                names = os.listdir(self.dir)
                overlaps = [n for n in names if n.startswith("overlap.")]
                entered = [n for n in names if n.startswith("entered.")]
                self.assertEqual([], overlaps, "臨界区間が重なった")
                self.assertGreaterEqual(len(entered), 1, "古いロックを誰も取れなかった")
                self.assertFalse(os.path.exists(os.path.join(self.dir, "crit")))

    def test_a_late_thief_does_not_rob_the_lock_a_collector_took_in_between(self):
        patched = os.path.join(self._tmp.name, "ccnavi-common-lock.sh")
        with open(LOCK_SH, encoding="utf-8", newline="") as f:
            text = f.read()
        head = "ccnavi_lock_steal() {\n"
        self.assertEqual(1, text.count(head), "差し込む場所が見つからない")
        with open(patched, "w", encoding="utf-8", newline="\n") as f:
            f.write(text.replace(head, head + PAUSE))
        lock = self.stale_lock()
        # A は古いと判断して、強制取得の入口で止まる。
        a = self.start("a", lock_sh=patched, CCNAVI_TEST_PAUSE_DIR=self.dir)
        self.assertTrue(wait_for(os.path.join(self.dir, "a.waiting")), "A が強制取得に入らなかった")
        # その間に B が古いロックを回収して、生きたロックを持つ。
        b = self.start("b")
        self.addCleanup(b.kill)
        self.assertTrue(wait_for(os.path.join(self.dir, "b.held")), "B がロックを取れなかった")
        with open(os.path.join(self.dir, "b.mark"), encoding="utf-8") as f:
            mark = f.read().strip()
        # A が再開する。
        with open(os.path.join(self.dir, "a.go"), "w"):
            pass
        a.wait(timeout=60)
        entered = os.path.exists(os.path.join(self.dir, "a.entered"))
        self.assertFalse(entered, "A が B のロックを奪った")
        with open(os.path.join(self.dir, "a.rc"), encoding="utf-8") as f:
            self.assertEqual("1", f.read().strip())
        with open(os.path.join(lock, "owner"), encoding="utf-8") as f:
            self.assertIn(f" {mark}", f.read(), "B のロックの持ち主が替わった")
        with open(os.path.join(self.dir, "b.release"), "w"):
            pass
        self.assertEqual(0, b.wait(timeout=60))
        self.assertFalse(os.path.exists(lock))
        self.assertFalse(os.path.exists(lock + ".steal"))


if __name__ == "__main__":
    unittest.main()
