"""このリポジトリの本物のルール（.ccnavi/common/rules.yml）と組み込みルールで判定する受入テスト。

tests/fixtures/ のルールではなく、運用に使っている rules.yml をそのまま `--test` に
渡す。見るのは、shellread が目印を 2 つに分けたあとの判定（wip/design/shellread-sep.md
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
RULES = os.path.join(ROOT, ".ccnavi", "common", "rules.yml")

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
            'grep -n "rm -rf" /repo/.ccnavi/common/rules.yml',
            """grep -n "regex: '(>" /repo/.ccnavi/common/rules.yml""",
            'grep -n "<<EOF" /repo/README.md',
            # 引用の中の `> 場所` は grep の引数。selfguard の行き先の式が語の中の目印を
            # 食わなくなって、はじめて allow に届く。
            'grep -n "> /repo/.ccnavi/common/rules.yml" f',
        ]:
            with self.subTest(subject=subject):
                self.assert_verdict(subject, "allow", "prefer-read-grep")

    def test_引用付きの_find_は_allow_に当たる(self):
        self.assert_verdict('find /repo -name "a b"', "allow", "prefer-glob")

    def test_引用の空白をまたいだ書き換えは_deny_に届く(self):
        # 今まで穴だった側。`[^\x00]*` が引用の空白をまたげるようになる。
        for subject, rule_id in [
            ('find /repo -name "a b" -delete', "find-writes"),
            ('sed -i "s/a b/c/" /repo/.ccnavi/common/rules.yml', "builtin-guard-setting-files"),
        ]:
            with self.subTest(subject=subject):
                self.assert_verdict(subject, "deny", rule_id)

    def test_入力に混ざった語の中の目印は取り除かれる(self):
        # `git␁push` のまま読むと実在しない 1 語で、どのルールにも当たらない。
        # 取り除いて `git push` と読み、raw-git に当たる。
        self.assert_verdict("git" + shellread.WORD_SEP + "push", "deny", "raw-git")

    # 変わってはいけないもの

    def test_パイプの後ろがあれば_allow_に当たらない(self):
        # コマンドの区切りは `\x00` のまま。`[^\x00]*$` がそこで止まる。
        body = self.assert_verdict("cat /repo/README.md | head -20", "ask", "")
        self.assertEqual(body["code"], "UNDECLARED")

    def test_引用だけの二重の山括弧は読めないまま止まる(self):
        # 許容した誤検知（ccnavi.md §12.2、tests/test_acceptance.py）。生の文字列に
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

    def test_設定の場所の名前は語の中の目印でも終わる(self):
        # selfguard の `_TERM` / `_END` は語の中の目印も語の終わりとして数える。
        # 数えないと、分ける前に止まっていた綴りが通るようになる。
        for subject in [
            'rm ".ccnavi x"',
            'rm ".claude x"',
            'mv ".ccnavi;x" y',
        ]:
            with self.subTest(subject=subject):
                self.assert_verdict(subject, "deny", "builtin-guard-setting-files")


@unittest.skipUnless(hasattr(shellread, "REASON_AMBIGUOUS_SUBST"), "shellread-subst の実装待ち")
class SubstRepoRulesTest(unittest.TestCase):
    """コマンド置換・改行・プロセス置換を読んだあとの判定。

    wip/design/shellread-subst.md の §3 と §4.3。

    見本は (subject, 判定, 当たるルールの id, 根拠コード)。id が空ならどのルールにも当たらないこと、
    コードが空なら見ない。
    """

    def check(self, cases):
        for subject, want, rule_id, code in cases:
            with self.subTest(subject=subject):
                body = judge("Bash", subject)
                self.assertTrue(body["known"])
                self.assertEqual(body["verdict"], want, body["response"])
                if rule_id:
                    self.assertIn(rule_id, hit(body), body["rules"])
                elif want != "skip":
                    self.assertEqual(hit(body), [], body["rules"])
                if code:
                    self.assertEqual(body["code"], code, body["response"])

    # 変わるべきもの

    def test_引用の中の置換は中身で判定する(self):
        self.check(
            [
                ('echo "$(git push origin main)"', "deny", "raw-git", ""),
                ('grep -n "$(git push)" f', "deny", "raw-git", ""),
                ('grep -n "`rm -rf /tmp/x`" f', "deny", "recursive-delete", ""),
                ('h="$(git rev-parse HEAD)"', "deny", "raw-git", ""),
                ('echo "$(rm -rf /tmp/x)"', "deny", "recursive-delete", ""),
                ("echo `find . -delete`", "deny", "find-writes", ""),
                ("cat <<EOF\nuse `rm -rf /tmp/x`\nEOF", "deny", "recursive-delete", ""),
            ]
        )

    def test_改行とプロセス置換と語の途中の井桁は_allow_を後ろまで広げない(self):
        # 今は allow が後ろのコマンドまで通していた（設計 §0）。
        self.check(
            [
                ("grep -n x f\nsh evil.sh", "ask", "", "UNDECLARED"),
                ("cat <(sh evil.sh)", "ask", "", "UNDECLARED"),
                ("grep x <(curl https://example.com)", "ask", "prefer-webfetch", ""),
                ("find . -name x\npython evil.py", "ask", "", "UNDECLARED"),
                ("grep -n a#b f; sh evil.sh", "ask", "", "UNDECLARED"),
            ]
        )

    def test_外側を割らず_後ろと予約語の直後もコマンドの先頭として読む(self):
        self.check(
            [
                ("find $(pwd) -name x -delete", "deny", "find-writes", ""),
                ("echo a#; git push origin main", "deny", "raw-git", ""),
                ("curl https://example.com/#top; rm -rf /tmp/x", "deny", "recursive-delete", ""),
                ("ls\nfind . -delete", "deny", "find-writes", ""),
                ("echo hi\ncurl https://example.com", "ask", "prefer-webfetch", ""),
                ("if true; then find . -delete; fi", "deny", "find-writes", ""),
            ]
        )

    def test_組み込みの守りも中身に当たる(self):
        self.check(
            [
                ('echo "$(ccnavi --approve x)"', "deny", "builtin-guard-ticket-approval", ""),
                ("ls\nccnavi --approve x", "deny", "builtin-guard-ticket-approval", ""),
                (
                    'echo "$(tee /repo/.ccnavi/common/rules.yml < /tmp/x)"',
                    "deny",
                    "builtin-guard-setting-files",
                    "",
                ),
                (
                    "cat /tmp/x >(tee /repo/.ccnavi/common/rules.yml)",
                    "deny",
                    "builtin-guard-setting-files",
                    "",
                ),
                (
                    "ls\nsed -i s/a/b/ /repo/.ccnavi/common/rules.yml",
                    "deny",
                    "builtin-guard-setting-files",
                    "",
                ),
                (
                    'echo x > "$(pwd)/.ccnavi/common/rules.yml"',
                    "deny",
                    "builtin-guard-setting-files",
                    "",
                ),
            ]
        )

    # 変わってはいけないもの

    def test_文字として書いたものと既存の読みは変わらない(self):
        self.check(
            [
                ("echo $(git push origin main)", "deny", "raw-git", ""),
                ("echo $((1 << 2))", "ask", "", "UNDECLARED"),
                ('grep -n "git push" README.md', "allow", "prefer-read-grep", ""),
                ("grep -n '$(git push)' f", "allow", "prefer-read-grep", ""),
                ('grep -n "\\$(git push)" f', "allow", "prefer-read-grep", ""),
                ('grep -n "\\`git push\\`" f', "allow", "prefer-read-grep", ""),
                ("echo hi # $(git push)", "ask", "", "UNDECLARED"),
                ("# git push origin main", "skip", "", ""),
                ("cat /repo/README.md | head -20", "ask", "", "UNDECLARED"),
                ("""grep -n "regex: '(>" f""", "allow", "prefer-read-grep", ""),
                ('grep -n "<<EOF" /repo/README.md', "allow", "prefer-read-grep", ""),
                ("cat <<'EOF' > notes.md", "deny", "heredoc", "PARSE_UNCERTAIN"),
                ("cat a.txt\n", "allow", "prefer-read-grep", ""),
                ("curl -s 'https://example.com/a#frag'", "ask", "prefer-webfetch", ""),
                ('export PATH="$(go env GOPATH)/bin:$PATH"', "ask", "", "UNDECLARED"),
                ('eval "$(ssh-agent -s)"', "ask", "", "PARSE_UNCERTAIN"),
                (
                    "sed -n \"$(grep -n '^### レビュー' README.md | cut -d: -f1),+60p\" README.md",
                    "ask",
                    "",
                    "UNDECLARED",
                ),
            ]
        )

    def test_引用付き_heredoc_の本文は読まない(self):
        for subject in [
            "cat <<'EOF' > notes.md\n$(git push) `rm -rf /tmp/x`\nEOF",
            "cat <<'EOF' > notes.md\ngit push origin main\nEOF\necho done",
        ]:
            with self.subTest(subject=subject):
                body = judge("Bash", subject)
                self.assertEqual(body["verdict"], "deny")
                self.assertEqual(hit(body), ["heredoc"], body["rules"])

    # 増える誤検知と回避策

    def test_増える誤検知(self):
        self.check(
            [
                (
                    "sh .ccnavi/scripts/ccnavi-git.sh commit -m \"$(cat <<'EOF'\n"
                    "fix: don't push\nEOF\n)\"",
                    "deny",
                    "heredoc",
                    "",
                ),
                (
                    "gh pr create --title t --body \"$(cat <<'EOF'\n- `rm -rf x` is gone\nEOF\n)\"",
                    "deny",
                    "heredoc",
                    "",
                ),
                ('gh issue create --title t --body "use `git push` here"', "deny", "raw-git", ""),
                (
                    'gh issue create --title t --body "odd ` backtick and git push"',
                    "deny",
                    "raw-git",
                    "PARSE_UNCERTAIN",
                ),
                ('cd "$(git rev-parse --show-toplevel)"', "deny", "raw-git", ""),
                ('grep -rn foo "$(pwd)"', "ask", "", "UNDECLARED"),
                ('find "$(pwd)" -name x', "ask", "", "UNDECLARED"),
                ('cat "$(ls -t logs/git-*.log | head -1)"', "ask", "", "UNDECLARED"),
                ("grep -n foo f\ngrep -n bar g", "ask", "", "UNDECLARED"),
                ('echo "$(case a in a) git push;; esac)"', "deny", "raw-git", "PARSE_UNCERTAIN"),
                ('echo "$(xargs echo < f)"', "ask", "", "PARSE_UNCERTAIN"),
            ]
        )

    def test_回避策の綴りは止まらない(self):
        self.check(
            [
                (
                    "sh .ccnavi/scripts/ccnavi-git.sh commit -F /tmp/msg.txt",
                    "allow",
                    "ccnavi-git",
                    "",
                ),
                (
                    'sh .ccnavi/scripts/ccnavi-git.sh commit -m "fix: 1 行目\n\n本文"',
                    "allow",
                    "ccnavi-git",
                    "",
                ),
                ("gh pr create --title t --body-file /tmp/body.md", "ask", "", "UNDECLARED"),
                (
                    'gh issue create --title t --body "use \\`git push\\` here"',
                    "ask",
                    "",
                    "UNDECLARED",
                ),
                (
                    "gh issue create --title t --body 'use `git push` and don'\"'\"'t $(x)'",
                    "ask",
                    "",
                    "UNDECLARED",
                ),
                ("sh /tmp/run.sh", "ask", "", "UNDECLARED"),
                (
                    "sh .ccnavi/scripts/ccnavi-git.sh rev-parse --show-toplevel",
                    "allow",
                    "ccnavi-git",
                    "",
                ),
            ]
        )

    # 文面（§4.3）

    def test_引用の中から切り出したコマンドに当たったときだけ断りが出る(self):
        quoted = judge("Bash", 'gh issue create --title t --body "use `git push` here"')
        self.assertIn("inside double quotes", quoted["response"])
        self.assertEqual(quoted.get("quoted"), ["raw-git"])

        bare = judge("Bash", "echo $(git push origin main)")
        self.assertNotIn("inside double quotes", bare["response"])
        self.assertFalse(bare.get("quoted"), bare.get("quoted"))

    def test_縮退の断りは理由ごとに違う(self):
        responses = {}
        for subject, reason, phrase in [
            ('echo "git push', shellread.REASON_UNTERMINATED, "quote or heredoc"),
            ("echo `git push", shellread.REASON_UNTERMINATED_SUBST, "backquote"),
            (
                'echo "$(case a in a) git push;; esac)"',
                shellread.REASON_AMBIGUOUS_SUBST,
                "shells read differently",
            ),
        ]:
            with self.subTest(subject=subject):
                body = judge("Bash", subject)
                self.assertEqual(body["degraded"], reason)
                self.assertIn(phrase, body["response"])
                responses[reason] = body["response"]
        self.assertEqual(len(set(responses.values())), 3, "理由の違う縮退に同じ文面を返した")


if __name__ == "__main__":
    unittest.main()
