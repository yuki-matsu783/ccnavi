"""ユーザだけが打つ sh（`ccnavi-user-*.sh`）の分け方の試験。

ユーザだけが打つ操作（承認・承認の push・取り込み状態の削除・残った指摘の対応方針・早めに閉じる・
残った子のブランチの削除）は、エージェントも使う sh から `ccnavi-user-*.sh` に分けてある。
hook はその名前で止めるので、名前と置き場の決まりが崩れると止めが外れる。見るのは次のとおり。

1. 書く処理（返信・解決・issue を作る・ユーザの判断を承認の push で送る）は
   `ccnavi-user-*.sh` の中だけにあり、エージェントも読む共通部（`ccnavi-common*.sh`）は持たない。
   共通部に置くと `sh -c '. …common….sh; 関数'` の形で、ユーザ専用の sh の名前を出さずに呼べる
2. 実行ファイルを起こす `ccnavi_wap_exe` の定義は共通部の 1 か所（と、レビューの共通部の包み）だけ
3. もとの sh に残した古い入口は、何も動かさずに終了コード 2 で新しい sh を案内する
4. どの `ccnavi-user-*.sh` も単体で起動でき（`-h`）、hook の名前の止めに当たる

古い入口と新しい sh の中身の動きは、それぞれの sh の試験
（test_review_decide・test_write_and_push_*・test_sync_sh・test_clean_sh・test_push_approved_sh）が見る。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.tickets import user_only
from tests import ROOT, common_sh

SHELL = shutil.which("sh") or shutil.which("bash")
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")

# ユーザの判断をホストやリモートに書く関数。ユーザ専用の sh の中だけに置く。
WRITERS = (
    "accept_reply",
    "accept_resolve",
    "accept_threads",
    "accept_execute",
    "accept_cleanup",
    "accept_before_place",
    "create_issue",
    "post_decision",
    "carry_human",
)
# 前の版にあったユーザ専用の sh の名前（今は ccnavi-user-*.sh）。
OLD_NAMES = ("ccnavi-agree.sh", "ccnavi-approve.sh", "ccnavi-push-approved.sh")
DEFINITION = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", re.MULTILINE)


def read(name: str) -> str:
    with open(os.path.join(SH_DIR, name), encoding="utf-8") as f:
        return f.read()


def code(text: str) -> str:
    """コメントの行を除いた本文。"""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def defined(name: str) -> set[str]:
    return set(DEFINITION.findall(code(read(name))))


def user_scripts() -> list[str]:
    return sorted(
        n for n in os.listdir(SH_DIR) if n.startswith("ccnavi-user-") and n.endswith(".sh")
    )


class PlacementTest(unittest.TestCase):
    def test_there_are_user_scripts(self):
        names = user_scripts()
        for want in (
            "ccnavi-user-agree.sh",
            "ccnavi-user-push-approved.sh",
            "ccnavi-user-decide.sh",
            "ccnavi-user-close-early.sh",
            "ccnavi-user-clean-branch.sh",
        ):
            self.assertIn(want, names)

    def test_common_files_do_not_hold_the_writers(self):
        for name in common_sh(SH_DIR):
            with self.subTest(name=name):
                self.assertFalse(defined(name) & set(WRITERS), name)

    def test_the_writers_live_in_user_scripts(self):
        found: dict[str, list[str]] = {}
        for name in os.listdir(SH_DIR):
            if name.endswith(".sh"):
                for fn in defined(name) & set(WRITERS):
                    found.setdefault(fn, []).append(name)
        for fn in WRITERS:
            with self.subTest(fn=fn):
                self.assertIn(fn, found)
                for where in found[fn]:
                    self.assertTrue(where.startswith("ccnavi-user-"), (fn, where))

    def test_common_files_do_not_start_a_user_script(self):
        """共通部は ccnavi-user-*.sh を起動しない（案内の文面に名前を出すだけ）。"""
        start = re.compile(r"^\s*(?:exec\s+)?sh\s+\S*ccnavi-user-", re.MULTILINE)
        for name in common_sh(SH_DIR):
            with self.subTest(name=name):
                self.assertIsNone(start.search(code(read(name))), name)

    def test_agent_scripts_do_not_start_a_user_script(self):
        """エージェントも使う sh（ccnavi-user- で始まらない sh）は、ユーザ専用の sh を起動しない。

        エージェントが打てる sh の中から、ユーザ専用の sh を名前を出さずに動かせないようにする。
        """
        start = re.compile(r"^\s*(?:exec\s+)?sh\s+\S*ccnavi-user-", re.MULTILINE)
        for name in os.listdir(SH_DIR):
            if name.endswith(".sh") and not name.startswith("ccnavi-user-"):
                with self.subTest(name=name):
                    self.assertIsNone(start.search(code(read(name))), name)

    def test_the_executable_runner_is_defined_once(self):
        """`ccnavi_wap_exe` の定義は ccnavi-common-write-and-push.sh（起こし方）と
        ccnavi-common-review.sh（レビューの sh の --cwd つきの包み）だけ。各 sh に写さない。"""
        where = sorted(
            name
            for name in os.listdir(SH_DIR)
            if name.endswith(".sh") and "ccnavi_wap_exe" in defined(name)
        )
        self.assertEqual(where, ["ccnavi-common-review.sh", "ccnavi-common-write-and-push.sh"])

    def test_every_user_script_is_caught_by_the_name(self):
        pattern = user_only.user_script_pattern()
        for name in user_scripts():
            with self.subTest(name=name):
                self.assertTrue(pattern.search(f"sh .ccnavi/scripts/{name} x"), name)

    def test_the_old_names_are_in_the_table(self):
        """古い名前は、hook が古い形として止める表（user_only.SCRIPTS）にある。"""
        listed = {f"{n}.sh" for row in user_only.SCRIPTS for n in row.names}
        for name in OLD_NAMES:
            self.assertIn(name, listed)

    def test_user_scripts_call_the_executable_through_the_user_entry(self):
        """ユーザ専用の操作は `user <操作>` の形で呼ぶ（`user` の外の形は実行ファイルが断る）。"""
        old = re.compile(r"(?<![\w-])--(?:agree|close-early|prune)\b|(?<![\w-])--reviewed\s+\"")
        for name in user_scripts():
            with self.subTest(name=name):
                self.assertIsNone(old.search(code(read(name))), name)


@unittest.skipIf(SHELL is None, "sh が無い")
class EntryTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="ccnavi-user-sh-")
        self.addCleanup(shutil.rmtree, self.work, True)
        self.ws = os.path.join(self.work, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in os.listdir(SH_DIR):
            if name.endswith(".sh"):
                shutil.copy(os.path.join(SH_DIR, name), scripts)
        # 実行ファイルの代役。呼ばれたら印を残す（古い入口は呼ばないこと）
        self.called = os.path.join(self.work, "called")
        self.bin = os.path.join(self.work, "bin", "ccnavi")
        os.makedirs(os.path.dirname(self.bin))
        with open(self.bin, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >>'{self.called}'\nexit 0\n")
        os.chmod(self.bin, 0o755)

    def run_sh(self, name, *args):
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.update(CCNAVI_WORKSPACE=self.ws, CCNAVI_ENTRY_PATH=self.bin)
        return subprocess.run(
            [SHELL, os.path.join(self.ws, ".ccnavi", "scripts", name), *args],
            cwd=self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

    def test_every_user_script_starts_and_shows_its_usage(self):
        for name in user_scripts():
            if name == "ccnavi-user-lib-review.sh":
                continue
            with self.subTest(name=name):
                shown = self.run_sh(name, "-h")
                self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
                self.assertIn(f"sh .ccnavi/scripts/{name}", shown.stdout)
        self.assertFalse(os.path.exists(self.called))

    def test_the_library_does_nothing_by_itself(self):
        """共有の部品は関数を決めるだけで、単体で打っても何もしない。"""
        done = self.run_sh("ccnavi-user-lib-review.sh")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertFalse(os.path.exists(self.called))

    def test_old_entries_refuse_and_point_to_the_new_scripts(self):
        for name, args, new in (
            ("ccnavi-review.sh", ("close-early", "--reason", "r"), "ccnavi-user-close-early.sh"),
            ("ccnavi-review.sh", ("decide", "1"), "ccnavi-user-decide.sh"),
            (
                "ccnavi-review.sh",
                ("decide", "1", "--choices", "{}", "--digest", "d"),
                "ccnavi-user-decide.sh",
            ),
            ("ccnavi-clean.sh", ("--branch", "i0001-01-01"), "ccnavi-user-clean-branch.sh"),
            (
                "ccnavi-clean.sh",
                ("--branch", "i0001-01-01", "--confirmed"),
                "ccnavi-user-clean-branch.sh",
            ),
        ):
            with self.subTest(name=name, args=args):
                refused = self.run_sh(name, *args)
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                self.assertIn(new, refused.stderr)
                self.assertIn("何もしていません", refused.stderr)
        self.assertFalse(os.path.exists(self.called), "古い入口は実行ファイルを起こさない")

    def test_review_usage_names_the_user_scripts(self):
        shown = self.run_sh("ccnavi-review.sh", "--help")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("ccnavi-user-decide.sh", shown.stdout)
        self.assertIn("ccnavi-user-close-early.sh", shown.stdout)
        self.assertNotIn("close-early  --reason", shown.stdout)


if __name__ == "__main__":
    unittest.main()
