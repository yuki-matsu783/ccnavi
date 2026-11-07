"""承認済みチケットの置き場を、1 回の起動で 1 度だけ読むことの受入テスト。

`--explain`（ボードの JSON と文の一覧）と `--lint` は、親ごとの局面とフェーズまで同じ読み
（`approval.ReadRaw`）を使い回す。親ごとに読み直すと、読む回数が親の数とツリーの数の積で増え、
置き場が育ったワークスペースでは VS Code の拡張の期限を超える。

実行ファイルの中に数える口は置かない。strace で実行ファイルが開いたファイルを記録し、全ツリーの
`.ccnavi/approved/{doing,done}/*.md` がそれぞれ 1 度だけ開かれたことを見る。strace の無い機械
（Linux 以外を含む）では飛ばす。
"""

from __future__ import annotations

import collections
import glob
import os
import re
import shutil
import subprocess
import sys
import unittest

from tests import exe, userform
from tests.inproc import GIT_ENV
from tests.ticket.test_phases import PhaseHarness, git
from tests.ticketsample import child_text, parent_text

_STRACE = shutil.which("strace") if sys.platform.startswith("linux") else None
_OPENED = re.compile(r'openat\([^,]+, "([^"]+)"')


@unittest.skipUnless(_STRACE, "strace が無い（Linux だけで回す）")
class RawShareTest(PhaseHarness):
    def ended_phase(self):
        """フェーズ 1 を終わらせる（レビュー準備中になる手前）。"""
        self.family(plan=["chores", "design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["src/a*"], review=False))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("src/a1.py", "x\n")])
        closed = self.close_child("i0001-01-01")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")

    def approved_files(self) -> list[str]:
        """全ツリーの承認済みチケットの置き場（作業中・閉じた）のファイル。"""
        found = []
        for top in [self.root, *glob.glob(os.path.join(self.root, ".claude", "worktrees", "*"))]:
            for state in ("doing", "done"):
                found += glob.glob(os.path.join(top, ".ccnavi", "approved", state, "*.md"))
        return sorted(os.path.realpath(p) for p in found)

    def opened(self, *args) -> collections.Counter:
        """実行ファイルを strace の下で起こし、開いたファイルの回数を返す。"""
        log = os.path.join(self.root, "strace.log")
        argv = userform.place(
            [
                "--root",
                self.root,
                "--guard-core-files",
                "disable",
                "--restore-if-deny",
                "disable",
                "--guard-ticket-approval",
                "disable",
            ],
            list(args),
        )
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
            **GIT_ENV,
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        done = subprocess.run(
            [_STRACE, "-f", "-qq", "-e", "trace=openat", "-o", log, exe.path(), *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.assertIn(done.returncode, (0, 1), done.stdout + done.stderr)
        with open(log, encoding="utf-8", errors="replace") as f:
            paths = [m.group(1) for m in map(_OPENED.search, f) if m]
        return collections.Counter(os.path.realpath(p) for p in paths)

    def test_explain_and_lint_read_the_approved_copies_once(self):
        """親とツリーが増えても、`--explain` と `--lint` が置き場のファイルを開くのは 1 度ずつ。"""
        self.ended_phase()
        self.hook("PostToolUse", "Bash", self.parent_tree, command="ls")  # レビュー準備中にする
        # 親をもう 1 つと、ワークツリーを 2 つ足す。開く回数は変わらない。
        other = self.worktree("i0002", "main")
        self.propose("i0002", parent_text("i0002", ["chores"]), tree=other)
        git(other, "add", "-A")
        git(other, "commit", "--quiet", "-m", "approval-request i0002")
        self.assertEqual(self.approve().returncode, 0)
        self.worktree("x1", "i0001")
        self.worktree("x2", "i0001")
        files = self.approved_files()
        self.assertGreater(len(files), 4)
        for args in (("--explain", "--json"), ("--explain",), ("--lint", "--json")):
            with self.subTest(args=args):
                opened = self.opened(*args)
                want = {p: 1 for p in files}
                if args[0] == "--lint":
                    # --lint は、親のワークツリーかを見るために、ワークツリーと同じ名前の
                    # チケットをもう 1 度だけ開く（ワークツリーごとに 1 本。親の数と掛け合わない）。
                    for p in files:
                        if _own_ticket(p):
                            want[p] += 1
                self.maxDiff = None
                self.assertEqual({p: opened[p] for p in files}, want)


def _own_ticket(path: str) -> bool:
    """<ツリー>/.ccnavi/approved/<状態>/<ツリーの名前>.md か。"""
    state_dir = os.path.dirname(path)
    top = os.path.dirname(os.path.dirname(os.path.dirname(state_dir)))
    return os.path.basename(path) == os.path.basename(top) + ".md"


if __name__ == "__main__":
    unittest.main()
