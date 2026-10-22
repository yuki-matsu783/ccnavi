"""チケットの作成から承認、承認済みチケットの push までを 1 本に通す受入テスト。

実際の sh（`.ccnavi/scripts/`）と組み立て済みの実行ファイル（`dist/ccnavi`）を、使い捨ての
ワークスペースと bare のリモートの上で順に呼ぶ。筋書きは次のとおりで、段ごとに前の段の
結果を前提にする。

1. ワークツリーを `ccnavi-git.sh worktree add` で切り、提案（`wip/proposals/todo/`）を書いて
   `ccnavi-git.sh` でコミットし、親のブランチをリモートへ push する（承認の前から親のブランチは
   リモートにある）。`ccnavi-sync.sh` で取り込むと、実行ファイルの
   `--agree --preview --json` が承認待ちに数える
2. 端末の無い `ccnavi-user-agree.sh` は承認の壁で止まり、何も置かず何も送らない
3. `ccnavi-user-agree.sh` で承認する。承認済みチケットが `doing/` に置かれ、
   `ccnavi-user-push-approved.sh` が置き場（と消えた提案）だけをコミットして push する。
   同じツリーの書きかけはコミットしない。別の機械（clone）から承認済みチケットが読める
4. コミットするものが無ければ `ccnavi-user-push-approved.sh` は 0 で
   「コミットして push する承認済みチケットは無い。」と言う
5. `ccnavi-ticket.sh start` が承認済みチケットに着手の欄を書き、自分でコミットして push する

単体のテスト（tests/ticket/test_ticket.py、tests/ticket/test_approve_json.py、
tests/sh/test_push_approved_sh.py）は、実行ファイルを in-process で呼ぶか、承認を stub の sh で
済ませていて、「実行ファイルが置いたものを sh がコミットして push する」つなぎ目は
通っていない。ここはそのつなぎ目だけを見る。個々の分岐はあちらが見るので、ここで増やさない。

組み立て済みの実行ファイルが無ければ skip する（tests/e2e/test_e2e_sh.py と同じ前準備を使う）。
試すのはソースではなくその実行ファイルなので、`src/ccnavi/` を直したら組み立て直してから回す。

    uv run --with pyinstaller python build.py
    uv run python -m unittest tests.e2e.test_e2e_push -v

承認は端末からしか通らない（`--agree` の壁）。テストは端末を持たないので、段 3 からは
`CCNAVI_GUARD_TICKET_APPROVAL=disable` を渡して y を標準入力から送る。単体のテストが
`--guard-ticket-approval disable` で切るのと同じ扱いで、壁そのものは段 2 で見る。

ここで確かめないもの。 モード B（`projects/<名前>/` の下のリポジトリ）と子チケット、
保護されたブランチ・push の失敗・detached は tests/sh/test_push_approved_sh.py が見る。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import config_path
from tests.e2e.test_e2e_sh import DIST, SH_DIR, SHELL, SKIP, WorkspaceTest, git, write
from tests.ticketsample import PHASES, parent_text

TICKET = "i0001"
APPROVED = ".ccnavi/approved/doing"
PROPOSAL = "wip/proposals/todo"
MESSAGE = "ccnavi: 承認済みチケットを更新"
NOTHING = "コミットして push する承認済みチケットはありません。"

# 親の提案。`ticket start` が計画（plan）を要るので、計画を持つ形にする（mr と phases の写しも付く）
PROPOSAL_TEXT = parent_text(TICKET, ["design"], allow=("src/*",))


def out(cwd, *args):
    """素の git の標準出力（行頭の空白は残す）。読み返すためだけに使う。操作は sh を通す。"""
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return done.stdout.rstrip() if done.returncode == 0 else ""


@unittest.skipIf(SKIP, SKIP)
class ApproveAndPushTest(unittest.TestCase):
    """段は 1 本のテストの中で順に進める。前の段が残したものを次の段が使うので、分けない。"""

    def setUp(self):
        print(f"\n  sh = {SH_DIR}\n  exe = {DIST}", flush=True)
        self.tmp = tempfile.mkdtemp(prefix="ccnavi-e2e-push-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = os.path.join(self.tmp, "ws")
        WorkspaceTest.build_workspace.__func__(WorkspaceTest, self.ws)
        # 承認は共通レイヤーのルールを読む。無いと承認は通るが「ルールを読めない」と言うので、
        # 実際のワークスペースと同じく置いておく。中身は空でよい。
        write(os.path.join(self.ws, ".ccnavi", "config", "common", "rules.yml"), "version: 1\n")
        # 計画（plan）のあるチケットは、計画が使うフェーズの定義も要る
        write(config_path(self.ws, "phases"), PHASES)
        git(self.ws, "add", ".ccnavi/config")
        git(self.ws, "commit", "-q", "-m", "rules")
        self.remote = os.path.join(self.tmp, "origin.git")
        git(self.tmp, "init", "-q", "--bare", self.remote)
        git(self.ws, "remote", "add", "origin", self.remote)
        # 取り込みは統合先をリモートの main から決める。リモートに main が無いと止まる
        git(self.ws, "push", "-q", "origin", "main")
        git(self.ws, "remote", "set-head", "origin", "main")
        self.tree = os.path.join(self.ws, ".claude", "worktrees", TICKET)

    # ---- 道具

    def run_sh(self, name, *args, cwd=None, stdin="", env=None):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        return subprocess.run(
            [SHELL, os.path.join(self.ws, ".ccnavi", "scripts", name), *args],
            cwd=cwd or self.ws,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )

    def ok(self, result):
        self.assertEqual(0, result.returncode, f"stdout={result.stdout!r} stderr={result.stderr!r}")
        return result

    def binary(self, *args):
        """組み立て済みの実行ファイルを直に呼ぶ。ボードの拡張が打つ形。"""
        name = "ccnavi.exe" if os.name == "nt" else "ccnavi"
        exe = os.path.join(self.ws, "dist", "ccnavi", name)
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return subprocess.run(
            [exe, "--root", self.ws, *args],
            cwd=self.ws,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )

    def approved_copy(self, tree=None):
        return os.path.join(tree or self.tree, *APPROVED.split("/"), TICKET + ".md")

    def remote_head(self):
        return out(self.tmp, "--git-dir", self.remote, "rev-parse", "--verify", "-q", TICKET)

    def committed(self):
        """HEAD のコミットが触ったパス。改名は消えた側と置かれた側の 2 本に分けて並べる。"""
        listed = out(self.tree, "show", "--name-only", "--no-renames", "--format=", "HEAD")
        return sorted(line for line in listed.splitlines() if line)

    def clone(self, name):
        """別の機械。リモートから親のブランチだけを取る。"""
        path = os.path.join(self.tmp, name)
        git(self.tmp, "clone", "-q", "--branch", TICKET, self.remote, path)
        return path

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    # ---- 筋書き

    def test_propose_approve_and_push(self):
        # 1. 作成。ワークツリーを切り、提案を書いてコミットする（エージェントの手順）。
        where = f".claude/worktrees/{TICKET}"
        self.ok(self.run_sh("ccnavi-git.sh", "fetch", "origin", "main"))
        self.ok(self.run_sh("ccnavi-git.sh", "worktree", "add", where, "-b", TICKET))
        write(os.path.join(self.tree, *PROPOSAL.split("/"), TICKET + ".md"), PROPOSAL_TEXT)
        self.ok(self.run_sh("ccnavi-git.sh", "add", f"{PROPOSAL}/{TICKET}.md", cwd=self.tree))
        self.ok(self.run_sh("ccnavi-git.sh", "commit", "-m", "propose", cwd=self.tree))
        proposed = out(self.tree, "rev-parse", "HEAD")
        # 親のブランチは承認の前にリモートへ送ってある（MR の元ブランチ）。承認済みチケットは、
        # リモートにある親のブランチに取り込んでから送る
        self.ok(self.run_sh("ccnavi-git.sh", "push", "-u", "origin", TICKET, cwd=self.tree))
        # 統合先を取り込んでおく（取り込みの後の検査も通ることを見る）
        self.ok(self.run_sh("ccnavi-sync.sh"))
        preview = self.binary("--agree", "--preview", "--json")
        self.ok(preview)
        self.assertEqual([TICKET], [b["ticket"] for b in json.loads(preview.stdout)["batch"]])

        # 同じツリーの書きかけ。承認の段でコミットされてはいけない。
        write(os.path.join(self.tree, "seed.txt"), "書きかけ\n")
        write(os.path.join(self.tree, "src", "draft.py"), "x\n")

        # 2. 端末が無いと承認の壁で止まる。承認済みチケットも、コミットも、push も出ない。
        walled = self.run_sh("ccnavi-user-agree.sh", stdin="y\n")
        self.assertEqual(1, walled.returncode, walled.stdout + walled.stderr)
        self.assertIn("ターミナル", walled.stderr)
        self.assertFalse(os.path.exists(self.approved_copy()))
        self.assertEqual(proposed, out(self.tree, "rev-parse", "HEAD"))
        self.assertEqual(proposed, self.remote_head())

        # 3. 承認。実行ファイルが doing/ に置き、ccnavi-user-push-approved.sh が
        # コミットして push する。
        approved = self.ok(
            self.run_sh(
                "ccnavi-user-agree.sh", stdin="y\n", env={"CCNAVI_GUARD_TICKET_APPROVAL": "disable"}
            )
        )
        self.assertIn("承認した", approved.stdout)
        self.assertNotIn("読めない", approved.stderr)
        # 承認は提案をそのまま動かす（欄を書き足さない）。
        self.assertEqual(PROPOSAL_TEXT, self.read(self.approved_copy()))
        self.assertEqual(
            MESSAGE,
            out(self.tree, "log", "-1", "--format=%s"),
            approved.stdout + approved.stderr,
        )
        # コミットしたのは置き場と、承認で todo/ から消えた提案だけ。
        self.assertEqual(
            sorted([f"{APPROVED}/{TICKET}.md", f"{PROPOSAL}/{TICKET}.md"]),
            self.committed(),
        )
        self.assertEqual(proposed, out(self.tree, "rev-parse", "HEAD~1"))
        self.assertEqual(out(self.tree, "rev-parse", "HEAD"), self.remote_head())
        self.assertEqual(" M seed.txt", out(self.tree, "status", "--porcelain", "--", "seed.txt"))
        self.assertEqual("?? src/", out(self.tree, "status", "--porcelain", "--", "src"))
        self.assertEqual("", out(self.tree, "diff", "--cached", "--name-only"))
        # 別の機械から、承認済みチケットが読め、提案は承認待ちに残っていない。
        there = self.clone("machine-b")
        self.assertEqual(PROPOSAL_TEXT, self.read(self.approved_copy(there)))
        self.assertFalse(os.path.exists(os.path.join(there, *PROPOSAL.split("/"), TICKET + ".md")))
        self.assertFalse(os.path.exists(os.path.join(there, "src", "draft.py")))
        self.assertEqual("seed\n", self.read(os.path.join(there, "seed.txt")))

        # 4. もうコミットするものは無い。コミットもリモートも動かない。
        pushed = self.remote_head()
        idle = self.ok(self.run_sh("ccnavi-user-push-approved.sh", cwd=self.tree))
        self.assertIn(NOTHING, idle.stdout)
        self.assertEqual(pushed, out(self.tree, "rev-parse", "HEAD"))
        self.assertEqual(pushed, self.remote_head())

        # 5. 着手の欄は実行ファイルが承認済みチケットに書く。
        # 親の `start` は自分で書いた置き場をコミットして push するので、あとに送るものは残らない。
        self.ok(self.run_sh("ccnavi-ticket.sh", "start", TICKET, cwd=self.tree))
        started = self.read(self.approved_copy())
        self.assertNotIn('started_at: ""', started)
        self.assertIn(pushed, started, "基準点が承認のコミットになっていない")
        self.assertEqual([f"{APPROVED}/{TICKET}.md"], self.committed())
        self.assertEqual(pushed, out(self.tree, "rev-parse", "HEAD~1"))
        self.assertEqual(out(self.tree, "rev-parse", "HEAD"), self.remote_head())
        later = self.clone("machine-b-later")
        self.assertEqual(started, self.read(self.approved_copy(later)))
        self.assertEqual(" M seed.txt", out(self.tree, "status", "--porcelain", "--", "seed.txt"))


if __name__ == "__main__":
    unittest.main()
