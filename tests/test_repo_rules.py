"""このリポジトリの本物のルール（.claude/ccnavi/rules.yml）と組み込みルールで判定する受入テスト。

tests/fixtures/ のルールではなく、運用に使っている rules.yml をそのまま `--test` に
渡す。見るのは、shellread が印を 2 つに分けたあとの判定（wip/design/shellread-sep.md
§4「見本 → 判定」）。ルールの `[^\\x00]*` が「同じコマンドの中」だけを指すようになり、
引用付きの grep / find が allow に当たる一方、引用の空白をまたいだ書き換えが deny に
届くこと。変わってはいけないもの（§3）も同じ表で固定する。

道具は外から動かす。`--test <tool> <subject> --json` の `verdict` と `rules[].id` を読む
（形は README「試験の JSON」）。見本の `/repo` は `--root` に渡したルートに読み替える。
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi import shellread
from tests.inproc import run_ccnavi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(ROOT, ".claude", "ccnavi", "rules.yml")

# 見本のパスに書く合言葉。--test-samples と同じ読み替えを、ここでは自分で行う。
PLACEHOLDER = "/repo"


def judge(tool: str, subject: str) -> dict:
    """1 件を本物のルールで判定して、試験の JSON を返す。

    写しと控えは外し、記録も残さない。組み込みの selfguard（設定ファイルの保護）は
    既定のまま効かせる。§4 の表はそれを含めた判定なので。
    """
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    environment.pop("CLAUDE_PROJECT_DIR", None)
    done = run_ccnavi(
        [
            "--root",
            ROOT,
            "--rules",
            RULES,
            "--approved",
            "",
            "--state",
            "",
            "--log",
            "",
            "--test",
            tool,
            subject.replace(PLACEHOLDER, ROOT.replace("\\", "/")),
            "--json",
        ],
        input="",
        cwd=ROOT,
        env=environment,
    )
    if done.returncode != 0:
        raise AssertionError(f"--test が失敗した: {done.stderr}")
    return json.loads(done.stdout)


def hit(body: dict) -> list[str]:
    return [rule["id"] for rule in body["rules"]]


@unittest.skipUnless(hasattr(shellread, "WORD_SEP"), "shellread-sep の実装待ち")
class RepoRulesTest(unittest.TestCase):
    """§4「見本 → 判定」の表。"""

    def assert_verdict(self, subject, want, rule_id):
        body = judge("Bash", subject)
        self.assertTrue(body["known"])
        self.assertEqual(body["verdict"], want, body["response"])
        if rule_id:
            self.assertIn(rule_id, hit(body), body["rules"])
        else:
            self.assertEqual(hit(body), [], body["rules"])
        return body

    # 変わるべきもの

    def test_引用付きの_grep_は_allow_に当たる(self):
        # 課題そのもの。`[^\x00]*$` が引用の空白で止まって外れていた。
        for subject in [
            'grep -n "git push" README.md',
            'grep -n "rm -rf" /repo/.claude/ccnavi/rules.yml',
            """grep -n "regex: '(>" /repo/.claude/ccnavi/rules.yml""",
            'grep -n "<<EOF" /repo/README.md',
            # 引用の中の `> 場所` は grep の引数。selfguard の行き先の式が語の中の印を
            # 食わなくなって、はじめて allow に届く。
            'grep -n "> /repo/.claude/ccnavi/rules.yml" f',
        ]:
            with self.subTest(subject=subject):
                self.assert_verdict(subject, "allow", "prefer-read-grep")

    def test_引用付きの_find_は_allow_に当たる(self):
        self.assert_verdict('find /repo -name "a b"', "allow", "prefer-glob")

    def test_引用の空白をまたいだ書き換えは_deny_に届く(self):
        # 今まで穴だった側。`[^\x00]*` が引用の空白をまたげるようになる。
        for subject, rule_id in [
            ('find /repo -name "a b" -delete', "find-writes"),
            ('sed -i "s/a b/c/" /repo/.claude/ccnavi/rules.yml', "builtin-guard-setting-files"),
        ]:
            with self.subTest(subject=subject):
                self.assert_verdict(subject, "deny", rule_id)

    def test_入力に混ざった語の中の印は取り除かれる(self):
        # `git␁push` のまま読むと実在しない 1 語で、どのルールにも当たらない。
        # 取り除いて `git push` と読み、raw-git に当たる。
        self.assert_verdict("git" + shellread.WORD_SEP + "push", "deny", "raw-git")

    # 変わってはいけないもの

    def test_パイプの後ろがあれば_allow_に当たらない(self):
        # コマンドの区切りは `\x00` のまま。`[^\x00]*$` がそこで止まる。
        body = self.assert_verdict("cat /repo/README.md | head -20", "ask", "")
        self.assertEqual(body["code"], "UNDECLARED")

    def test_引用だけの二重の山括弧は読めないまま止まる(self):
        # 許容した誤検知（ccnavi.md §12.3、tests/test_acceptance.py）。生の文字列に
        # heredoc が当たり、読めなかったことを名乗る。
        body = judge("Bash", 'grep -n "<<" README.md')
        self.assertEqual(body["verdict"], "deny")
        self.assertEqual(body["code"], "PARSE_UNCERTAIN")
        self.assertEqual(body["degraded"], shellread.REASON_UNTERMINATED)
        self.assertIn("raw text", body["response"])

    def test_コマンド置換の中の_git_は止まる(self):
        self.assert_verdict("echo $(git push origin main)", "deny", "raw-git")

    def test_パイプで繋いだ_curl_は_ask_のまま(self):
        self.assert_verdict('echo "a b" | curl -d @- x', "ask", "prefer-webfetch")

    def test_引用の中の_preview_は承認の免除にならない(self):
        # phase.py の `_NOT_PREVIEW` は同じ語の中まで見ない。見ると、引数の値に
        # `--preview` を書くだけで `--approve` の枝が免除される。
        for subject in [
            'uv run python -m ccnavi --approve i0001 "a --preview"',
            'ccnavi --approve "i0001 --preview"',
        ]:
            with self.subTest(subject=subject):
                self.assert_verdict(subject, "deny", "builtin-guard-ticket-approval")

    def test_設定の場所の名前は語の中の印でも終わる(self):
        # selfguard の `_TERM` / `_END` は語の中の印も語の終わりとして数える。
        # 数えないと、分ける前に止まっていた綴りが通るようになる。
        for subject in [
            'rm ".ccnavi x"',
            'rm ".claude x"',
            'mv ".ccnavi;x" y',
        ]:
            with self.subTest(subject=subject):
                self.assert_verdict(subject, "deny", "builtin-guard-setting-files")


if __name__ == "__main__":
    unittest.main()
