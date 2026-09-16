"""配る sh が macOS の bash 3.2 でも読めることの検査。

macOS の `sh` は bash 3.2 で、Linux や Git Bash の bash では通る書き方のうち 2 つで落ちる。
どちらもロケールで逃げられない（片方は UTF-8 で、もう片方は C で落ちる）ので、書き方で避ける。

1. 変数のすぐ後ろに全角文字を続ける（`$bin（`）。UTF-8 のロケールでは全角の先頭バイトまで
   名前に読み、`set -u` の下で「unbound variable」で落ちる。`${bin}（` と括る
2. `$( )` の中に `case` を書く。C のロケールでは `)` を読み違え、ファイルを読む段で構文エラーに
   なる。`case` 文で変数に入れる

走らせて確かめるのではなく、綴りを読む。bash 3.2 の無い機械でも同じ報告になるように。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest

from tests import ROOT

# 組み立ての出力、依存の置き場、他の作業ツリーは見ない。ccnavi が書いた sh ではない。
SKIP_DIRS = {".git", "node_modules", ".venv", "dist", "build", "worktrees", "projects"}

NAME_THEN_WIDE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])")
CASE_IN_SUBSHELL = re.compile(r"\$\(\s*case\b")


def shell_scripts():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".sh"):
                yield os.path.join(base, name)


def code_lines(path):
    """行番号と、コメントだけの行を除いた行。コメントは bash が読まない。"""
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if not line.lstrip().startswith("#"):
                yield number, line


class ShPortabilityTest(unittest.TestCase):
    def found(self, pattern):
        hits = []
        for path in shell_scripts():
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            for number, line in code_lines(path):
                if pattern.search(line):
                    hits.append(f"{rel}:{number}: {line.strip()}")
        return hits

    def test_sh_files_are_found(self):
        # 探し方が壊れて 1 本も見ないまま通る、を防ぐ。
        names = {os.path.basename(p) for p in shell_scripts()}
        self.assertIn("ccnavi-setup.sh", names)
        self.assertIn("ccnavi-git.sh", names)
        # 承認済みチケットを運ぶ sh（設計 approve-carry §1）。配るので、同じ検査を通す。
        self.assertIn("ccnavi-push-approved.sh", names)

    def test_variable_is_braced_before_a_wide_character(self):
        self.assertEqual([], self.found(NAME_THEN_WIDE), "${name} と括ってください")

    def test_case_is_not_written_inside_command_substitution(self):
        self.assertEqual([], self.found(CASE_IN_SUBSHELL), "case 文で変数に入れてください")

    def test_sh_files_are_checked_out_with_lf(self):
        # Windows で core.autocrlf=true だと、取り出すときに CRLF になり、シバンが
        # `#!/bin/sh\r` になって直に起動する sh（振り分けの sh）が動かない。.gitattributes で
        # LF に固定する。綴りを読む検査ではなく、git が決める属性を聞く。
        git = shutil.which("git")
        if git is None:
            self.skipTest("git が無い")
        paths = [os.path.relpath(p, ROOT).replace(os.sep, "/") for p in shell_scripts()]
        result = subprocess.run(
            [git, "-C", ROOT, "check-attr", "eol", "--", *paths],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.skipTest("git の作業ツリーではない")
        wrong = [line for line in result.stdout.splitlines() if not line.endswith(": eol: lf")]
        self.assertEqual([], wrong, ".gitattributes で *.sh を eol=lf にしてください")


if __name__ == "__main__":
    unittest.main()
