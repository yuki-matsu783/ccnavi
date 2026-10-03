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
from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
SCRIPTS = os.path.join(ROOT, ".ccnavi", "scripts")

# `--version` には {compat} を名乗り、それ以外は受け取った引数を 1 行で出す。
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
    path = os.path.join(ROOT, "vscode-extension", "ccnavi-board", "src", "core", "version.ts")
    with open(path, encoding="utf-8") as f:
        found = re.search(r"^export const EXTENSION_COMPAT = ([0-9]+);$", f.read(), re.MULTILINE)
    assert found is not None, "src/core/version.ts に EXTENSION_COMPAT が無い"
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
            [SHELL, os.path.join(".ccnavi", "scripts", "ccnavi-ticket.sh"), "start", "x"],
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
        self.assertIn("ticket start x", result.stdout)

    def test_v2_a_different_compat_names_both_and_the_redistribution(self):
        """V2 配布先（build.py もソースも無い）では配り直しを言い、止めずに進む。"""
        other = sh_compat() + 1
        self.put_bin(STUB.format(compat=other))
        result = self.run_ticket()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ticket start x", result.stdout)
        self.assertIn(f"互換 {other}", result.stderr)
        self.assertIn(f"sh は互換 {sh_compat()}", result.stderr)
        self.assertIn("scripts/ccnavi-setup.sh", result.stderr)
        self.assertNotIn("組み立て直して", result.stderr)

    def test_v3_in_the_ccnavi_repository_the_fix_is_a_rebuild(self):
        """V3 build.py とソースがあれば組み立て直しを言う。"""
        write(os.path.join(self.ws, "build.py"), "", mode=0o644)
        write(os.path.join(self.ws, "ccnavi", "__main__.py"), "", mode=0o644)
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
        self.assertIn("ticket start x", result.stdout)
        self.assertIn("古い版", result.stderr)
        self.assertNotIn("unrecognized", result.stderr)

    def test_v5_the_real_executable_agrees_with_the_sh(self):
        """V5 ソースの `--version` が言う互換の版を、同じ読み方（sed）で sh が読める。"""
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        fake = os.path.join(self.ws, "ccnavi-src")
        python = sys.executable.replace("\\", "/")
        write(fake, f'#!/bin/sh\ncd "{ROOT}" && exec "{python}" -m ccnavi "$@"\n')
        script = (
            '. "$1/.ccnavi/scripts/ccnavi-common.sh"\n'
            'if said=$(ccnavi_compat_skew "$1" "$2"); then echo same; else echo "$said"; fi\n'
        )
        result = subprocess.run(
            [SHELL, "-c", script, "sh", self.ws, fake],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.stdout.strip(), "same", result.stderr)


class CompatAgreesTest(unittest.TestCase):
    def test_v6_the_executable_the_sh_and_the_extension_declare_the_same_compat(self):
        """V6 互換の版は 3 か所に書く。上げるときは揃えて上げる。

        上げ方は ccnavi/entry/version.py の説明のとおり。
        """
        self.assertEqual(sh_compat(), version.COMPAT)
        self.assertEqual(extension_compat(), version.COMPAT)

    def test_v7_the_eli5_request_change_raised_the_compat_to_2(self):
        """V7 ELI5 の依頼の形（ADR-0094〜0097）で sh と実行ファイルの契約が変わったので 2 以上。

        古い sh（互換 1）と組み合わせると、食い違いとして知らせる。
        """
        self.assertGreaterEqual(version.COMPAT, 2)

    def test_v8_renaming_the_approve_flag_to_agree_raised_the_compat_to_3(self):
        """V8 `--approve` を `--agree` に改名した（ADR-0099）ので 3 以上。

        改名の前の sh（互換 2）は `--approve` を渡して落ちるので、食い違いとして知らせる。
        """
        self.assertGreaterEqual(version.COMPAT, 3)


if __name__ == "__main__":
    unittest.main()
