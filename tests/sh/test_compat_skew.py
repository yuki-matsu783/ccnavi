"""保護済み sh と実行ファイルの互換の版（ccnavi-common.sh の ccnavi_compat_skew）。

sh は起動のときに実行ファイルの `--version` を読み、自分の `CCNAVI_COMPAT` と比べる。
食い違えば直し方（ccnavi のリポジトリなら組み立て直し、配布先なら配り直し）を標準エラーに
言い、止めずに先へ進む。`--version` を知らない古い実行ファイルは古いとして言う。

ワークスペースを使い捨てで組み、実行ファイルの代わりに `--version` にだけ答える sh を置く。
版の値そのもの（実行ファイル・sh・拡張の 3 か所が揃っていること）は最後のテストが見る。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from ccnavi.entry import version
from tests import ROOT, SRC
from tests.inproc import run_ccnavi

SHELL = shutil.which("sh") or shutil.which("bash")
SCRIPTS = os.path.join(ROOT, ".ccnavi", "scripts")

# `--version` には {compat} を返し、それ以外は受け取った引数を 1 行で出す。
STUB = """#!/bin/sh
if [ "$1" = --version ]; then
	printf 'ccnavi 9.9.9\\ncommit: abc\\ncompat: {compat}\\n'
	exit 0
fi
printf 'ran %s\\n' "$*"
"""
# `--version` を知らない古い実行ファイル。argparse と同じく使い方を出して 1 で終わる。
OLD_STUB = """#!/bin/sh
if [ "$1" = --version ]; then
	printf 'ccnavi: error: unrecognized arguments: --version\\n' >&2
	exit 1
fi
printf 'ran %s\\n' "$*"
"""


def write(path, text, mode=0o755):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.chmod(path, mode)


def sh_compat() -> int:
    with open(os.path.join(SCRIPTS, "ccnavi-common.sh"), encoding="utf-8") as f:
        found = re.search(r"^CCNAVI_COMPAT=([0-9]+)$", f.read(), re.MULTILINE)
    assert found is not None, "ccnavi-common.sh に CCNAVI_COMPAT が無い"
    return int(found.group(1))


def extension_compat() -> int:
    path = os.path.join(ROOT, "extensions", "vscode", "ccnavi-board", "src", "core", "version.ts")
    with open(path, encoding="utf-8") as f:
        found = re.search(r"^export const EXTENSION_COMPAT = ([0-9]+);$", f.read(), re.MULTILINE)
    assert found is not None, (
        "extensions/vscode/ccnavi-board/src/core/version.ts に EXTENSION_COMPAT が無い"
    )
    return int(found.group(1))


@unittest.skipIf(not SHELL, "sh も bash も見つからない")
class CompatSkewTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-compat-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        shutil.copytree(SCRIPTS, os.path.join(self.ws, ".ccnavi", "scripts"))

    def put_bin(self, text):
        write(os.path.join(self.ws, "dist", "ccnavi", "ccnavi"), text)

    def run_ticket(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        return subprocess.run(
            [SHELL, os.path.join(".ccnavi", "scripts", "ccnavi-ticket.sh"), "status", "x"],
            cwd=self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_v1_matching_compat_says_nothing(self):
        """V1"""
        self.put_bin(STUB.format(compat=sh_compat()))
        result = self.run_ticket()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("ticket status x", result.stdout)

    def test_v2_a_different_compat_names_both_and_the_redistribution(self):
        """V2 配布先（build.py もソースも無い）では配り直しを言い、止めずに進む。"""
        other = sh_compat() + 1
        self.put_bin(STUB.format(compat=other))
        result = self.run_ticket()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ticket status x", result.stdout)
        self.assertIn(f"互換 {other}", result.stderr)
        self.assertIn(f"sh は互換 {sh_compat()}", result.stderr)
        self.assertIn("scripts/ccnavi-setup.sh", result.stderr)
        self.assertNotIn("組み立て直して", result.stderr)
        # sh の再配布で、ユーザが打つ sh は ccnavi-user-*.sh に入れ替わることも言う
        self.assertIn("ccnavi-user-*.sh", result.stderr)

    def test_v3_in_the_ccnavi_repository_the_fix_is_a_rebuild(self):
        """V3 build.py とソースがあれば組み立て直しを言う。"""
        write(os.path.join(self.ws, "build.py"), "", mode=0o644)
        write(os.path.join(self.ws, "src", "ccnavi", "__main__.py"), "", mode=0o644)
        self.put_bin(STUB.format(compat=sh_compat() + 1))
        result = self.run_ticket()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("build.py を実行して組み立て直して", result.stderr)
        self.assertNotIn("ccnavi-setup.sh", result.stderr)

    def test_v4_an_executable_that_does_not_know_version_is_called_old(self):
        """V4 `--version` を知らない実行ファイルは古いとして言う。"""
        self.put_bin(OLD_STUB)
        result = self.run_ticket()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ticket status x", result.stdout)
        self.assertIn("古い版", result.stderr)
        self.assertNotIn("unrecognized", result.stderr)

    def test_v5_the_real_executable_agrees_with_the_sh(self):
        """V5 ソースの `--version` が言う互換の版を、同じ読み方（sed）で sh が読める。"""
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        fake = os.path.join(self.ws, "ccnavi-src")
        python = sys.executable.replace("\\", "/")
        write(
            fake, f'#!/bin/sh\ncd "{ROOT}" && PYTHONPATH="{SRC}" exec "{python}" -m ccnavi "$@"\n'
        )
        script = (
            '. "$0"\n'
            'if said=$(ccnavi_compat_skew "$1" "$2"); then echo same; else echo "$said"; fi\n'
        )
        # `$0` に入口のパスを渡す。保護済み sh と同じく、部品は `$0` のディレクトリから読む。
        common = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-common.sh")
        result = subprocess.run(
            [SHELL, "-c", script, common, self.ws, fake],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.stdout.strip(), "same", result.stderr)


class CompatAgreesTest(unittest.TestCase):
    def test_v6_the_executable_the_sh_and_the_extension_declare_the_same_compat(self):
        """V6 互換の版は 3 か所に書く。上げるときは揃えて上げる。

        上げ方は src/ccnavi/entry/version.py の説明のとおり。
        """
        self.assertEqual(sh_compat(), version.COMPAT)
        self.assertEqual(extension_compat(), version.COMPAT)

    def test_v7_the_eli5_request_change_raised_the_compat_to_2(self):
        """V7 ELI5 の依頼の形（`request` の `--eli5` と、wip/eli5/ のコミット済みの HTML）で
        sh と実行ファイルの契約が変わったので 2 以上。

        古い sh（互換 1）と組み合わせると、食い違いとして知らせる。
        """
        self.assertGreaterEqual(version.COMPAT, 2)

    def test_v8_renaming_the_approve_flag_to_agree_raised_the_compat_to_3(self):
        """V8 `--approve` を `--agree` に改名したので 3 以上。

        改名の前の sh（互換 2）は `--approve` を渡して失敗するので、食い違いとして知らせる。
        """
        self.assertGreaterEqual(version.COMPAT, 3)

    def test_v9_approval_leaving_the_ticket_as_is_raised_the_compat_to_6(self):
        """V9 承認がチケットの中身を変えなくなり、待ち方の置き場（`phases/<親>/workflow.yml`）と
        取り下げの条件が変わった。sh は `ticket status` を呼ぶ。なので 6 以上。

        この変更は互換の版を 4 に上げて作ったが、統合先では別の変更が先に 5 へ上げていたので、
        取り込みのときにどちらとも重ならない 6 にした。古い実行ファイル（古いコアを積んだ
        Chrome 拡張を含む）は待ち方を一直線と読み、`status` を知らないので、食い違いとして知らせる。
        """
        self.assertGreaterEqual(version.COMPAT, 6)

    def test_v10_the_board_reading_attention_from_the_executable_raised_the_compat_to_7(self):
        """V10 ボードの JSON のチケットが `issues` と `attention` を持ち、拡張はそれを読むだけに
        なった。そのため版は 7 以上。

        欄を足しただけだが、古い実行ファイルの答えでは拡張の「要対応のみ」が空になり、不備も
        出ない。知らないうちに空になるより食い違いとして知らせる。
        """
        self.assertGreaterEqual(version.COMPAT, 7)

    def test_v11_ready_removing_wip_raised_the_compat_to_8(self):
        """V11 `review ready` が `wip/` の追跡済みのファイルを消すようになった。write-and-push
        の外では sh が
        その削除の未コミットを確かめてから Draft を外す。なので 8 以上。

        確かめを持たない古い sh（互換 7）と新しい実行ファイルを組み合わせると、`wip/` の削除が
        未コミットのまま Draft が外れうるので、食い違いとして知らせる。sh は `worktree drop|tidy`
        （古い実行ファイルの知らないサブコマンド）も呼ぶ。
        """
        self.assertGreaterEqual(version.COMPAT, 8)

    def test_v12_dropping_the_workflow_file_raised_the_compat_to_9(self):
        """V12 待ち方のファイル（`phases/<親>/workflow.yml`）を無くし、待ち方を承認済みチケットの
        計画の項の `after` から都度計算するようになった。承認はファイルを書かず、取り下げは
        チケットのバイト一致だけで決め、ファイルを消さない。なので 9 以上。

        古い実行ファイル（古いコアを積んだ Chrome 拡張を含む）は `after` を知らずに計画を一直線と
        読み、取り下げでファイルを探すので、食い違いとして知らせる。待ち方の決まりそのものの
        見張りは、計画と待ち方の見本の表（tests/fixtures/plan-waits.json）のテストが持つ。
        """
        self.assertGreaterEqual(version.COMPAT, 9)

    def test_v13_the_parent_copy_of_phase_types_ships_with_compat_9(self):
        """V13 親チケットが計画の使うフェーズ定義の写し（`phases:`）を持ち、承認のあとの判定は
        `phases.yml` ではなくその写しを読むようになった。写しを書く
        `--plan-order <親> --fill-phases` も足した。待ち方のファイルを無くした変更（V12）と
        同じ出荷にするので、版は 9 のまま。

        古い実行ファイル（古いコアを積んだ Chrome 拡張を含む）は写しを読まずに `phases.yml` で
        判定するので、9 より前とは食い違いとして知らせる。フラグが在るかは `flags` で分かる。
        """
        self.assertGreaterEqual(version.COMPAT, 9)
        flags = run_ccnavi(["--version"], cwd=ROOT).stdout
        self.assertIn("--plan-order", flags)
        self.assertIn("--fill-phases", flags)

    def test_v14_accepting_by_resolving_the_thread_raised_the_compat_to_12(self):
        """V14 受け入れた未解決スレッドに、sh が返信を残して解決するようになった。

        sh は実行ファイルに受け入れの計画を書かせる
        オプション（`--accept-plan`・`--accept-done`）を渡す。
        古い実行ファイルは知らずに落ちるか、受け入れを記録に足すだけでホストを解決済みにしないので、
        食い違いとして知らせる。なので 12 以上。
        """
        self.assertGreaterEqual(version.COMPAT, 12)
        flags = run_ccnavi(["--version"], cwd=ROOT).stdout
        self.assertIn("--accept-plan", flags)
        self.assertIn("--accept-done", flags)

    def test_v15_dropping_the_sync_records_raised_the_compat_to_13(self):
        """V15 親子ごとの取り込み状態（`sync/<リポジトリ>/families/<P>`）と統合先のコピー
        （`integration/`）を廃止した。新しい sh は、実行ファイルの新しい副命令
        （`tree parent` の追加の行 `approved`・`requested`・`archived`・`mr`、`sync retired`、
        `sync migrate`）を呼ぶ。Chrome と手元の判定の規則も変わった。なので 13 以上。

        古い sh（互換 12）は取り込み状態の `present` を前提に write-and-push と承認の push
        を止める。
        組み合わせると、新しい実行ファイルは記録を書かないので、古い sh が止まる。
        食い違いとして知らせる。sh の版と実行ファイルの版は揃える。
        """
        self.assertGreaterEqual(version.COMPAT, 13)
        self.assertEqual(sh_compat(), version.COMPAT)

    def test_v16_integrate_tree_bound_raised_the_compat_to_15(self):
        """V16 `integrate` がチケットに結び付くかの判定を、sh が自分で置き場を読まずに実行ファイルの
        `tree bound` に聞くようになった。古い実行ファイルはサブコマンドを知らず頭の行を返さない
        ので、新しい sh は断る向きに倒す。食い違いとして知らせる。なので 15 以上。
        """
        self.assertGreaterEqual(version.COMPAT, 15)
        self.assertEqual(sh_compat(), version.COMPAT)

    def test_v17_the_user_entry_raised_the_compat_to_16(self):
        """V17 ユーザだけが打つ操作を、実行ファイルの `user <操作>` と ccnavi-user-*.sh に分けた。

        新しい実行ファイルは、`user` の外のユーザ専用のオプション（`--agree`・`--reviewed --yes`
        など）を断る。古い sh（ccnavi-agree.sh など）は断られるので、食い違いとして知らせ、
        sh の配り直しを言う。なので 16 以上。
        """
        self.assertGreaterEqual(version.COMPAT, 17)
        self.assertEqual(sh_compat(), version.COMPAT)


@unittest.skipIf(not SHELL, "sh も bash も見つからない")
class TicketStatusPassTest(unittest.TestCase):
    """`ccnavi-ticket.sh status` は親を省いても実行ファイルへ渡す（write-and-push を通らない）。"""

    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-status-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        shutil.copytree(SCRIPTS, os.path.join(self.ws, ".ccnavi", "scripts"))
        write(os.path.join(self.ws, "dist", "ccnavi", "ccnavi"), STUB.format(compat=sh_compat()))

    def run_ticket(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        return subprocess.run(
            [SHELL, os.path.join(".ccnavi", "scripts", "ccnavi-ticket.sh"), *args],
            cwd=self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_status_without_a_parent_reaches_the_executable(self):
        result = self.run_ticket("status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ticket status", result.stdout)

    def test_status_with_a_parent_reaches_the_executable(self):
        result = self.run_ticket("status", "i0001")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ticket status i0001", result.stdout)

    def test_other_verbs_still_need_an_id(self):
        result = self.run_ticket("start")
        self.assertEqual(result.returncode, 2, result.stdout)


if __name__ == "__main__":
    unittest.main()
