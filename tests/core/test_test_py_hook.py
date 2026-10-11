"""ターンの終わりに Python のテストを回す hook（`.claude/hooks/test-py.sh`）の検査。

見るのは、どのツリーを回すかと、記録のファイルの扱い。

1. `lint-py.sh` が書き残したツリーを 1 行ずつ読む。空白の入ったパスも 1 つのツリーとして回す
2. 1 時間の掃除は、他のセッションの古い記録だけを消す。自分のセッションの記録は、1 時間を
   超えたターンでも読む
3. 失敗したら差し戻す（終了コード 2）

テストそのものは回さない。`uv` を代わりのものに差し替え、呼ばれた居場所を書き出させる。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest

from tests import ROOT

HOOK = os.path.join(ROOT, ".claude", "hooks", "test-py.sh")
SH = shutil.which("sh")

# 代わりの uv。呼ばれた居場所を 1 行足し、STUB_FAIL に挙げた居場所なら失敗する。
STUB = """#!/bin/sh
pwd >>"$STUB_LOG"
case "$STUB_FAIL" in
"$(pwd)") echo "FAIL: stub"; exit 1 ;;
esac
exit 0
"""


@unittest.skipIf(SH is None, "sh が無い")
class TestPyHookTest(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, True)
        self.base = os.path.realpath(base)
        self.ws = os.path.join(self.base, "ws")
        self.session = os.path.join(self.ws, "logs", "session")
        os.makedirs(os.path.join(self.ws, "tests"))
        os.makedirs(self.session)
        bin_dir = os.path.join(self.base, "bin")
        os.makedirs(bin_dir)
        uv = os.path.join(bin_dir, "uv")
        with open(uv, "w", encoding="utf-8", newline="\n") as f:
            f.write(STUB)
        os.chmod(uv, 0o755)
        self.bin_dir = bin_dir
        self.log = os.path.join(self.base, "stub.log")

    def tree(self, name):
        path = os.path.join(self.base, name)
        os.makedirs(os.path.join(path, "tests"))
        return path

    def mark(self, *trees, session="s1"):
        path = os.path.join(self.session, f"{session}.trees")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(f"{t}\n" for t in trees))
        return path

    def stop(self, session="s1", fail=""):
        payload = f'{{"session_id":"{session}","hook_event_name":"Stop","stop_hook_active":false}}'
        env = {
            **os.environ,
            "CLAUDE_PROJECT_DIR": self.ws,
            "PATH": self.bin_dir + os.pathsep + os.environ.get("PATH", ""),
            "STUB_LOG": self.log,
            "STUB_FAIL": fail,
        }
        result = subprocess.run(
            [SH, HOOK], input=payload, capture_output=True, text=True, cwd=self.ws, env=env
        )
        ran = []
        if os.path.exists(self.log):
            with open(self.log, encoding="utf-8") as f:
                ran = [os.path.realpath(line.strip()) for line in f if line.strip()]
        return result, ran

    def test_each_recorded_tree_runs_once(self):
        a, b = self.tree("a"), self.tree("b")
        self.mark(a, b, a)
        result, ran = self.stop()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(sorted([a, b]), sorted(ran))

    def test_a_tree_with_spaces_is_one_tree(self):
        # 空白で語に割れると、どの語も tests/ を持たず、何も回さずに通る。
        spaced = self.tree("my tree")
        self.mark(spaced)
        result, ran = self.stop()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([spaced], ran)

    def test_without_a_record_the_workspace_runs(self):
        result, ran = self.stop()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([os.path.realpath(self.ws)], ran)

    def test_an_old_record_of_this_session_is_still_read(self):
        # 1 つのターンは 1 時間を超えることがある。消してから読むと、直したツリーが回らない。
        mine = self.tree("mine")
        record = self.mark(mine)
        old = time.time() - 2 * 60 * 60
        os.utime(record, (old, old))
        other = self.mark(self.tree("other"), session="s2")
        os.utime(other, (old, old))
        result, ran = self.stop()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([mine], ran)
        self.assertFalse(os.path.exists(other), "終わったセッションの古い記録が残っている")

    def test_a_session_id_with_a_separator_stays_inside_the_record_place(self):
        failing = self.tree("f")
        self.mark(failing, session="unknown")
        result, _ = self.stop(session="../x", fail=failing)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.session, "unknown.retries")))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "logs", "x.retries")))

    def test_a_failing_tree_is_pushed_back(self):
        good, bad = self.tree("good"), self.tree("bad")
        self.mark(good, bad)
        result, _ = self.stop(fail=bad)
        self.assertEqual(2, result.returncode)
        self.assertIn(bad, result.stderr)
        self.assertIn("失敗したテストを直して", result.stderr)


if __name__ == "__main__":
    unittest.main()
