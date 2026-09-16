"""このリポジトリの本物のルール（.ccnavi/common/rules.yml）と組み込みルールで判定する受入テスト。

tests/fixtures/ のルールではなく、運用に使っている rules.yml をそのまま `--test` に
渡す。見るのは、shellread が目印を 2 つに分けたあとの判定（wip/design/shellread-sep.md
§4「見本 → 判定」）。ルールの `[^\\x00]*` が「同じコマンドの中」だけを指すようになり、
引用付きの grep / find が allow に当たる一方、引用の空白をまたいだ書き換えが deny に
届くこと。変わってはいけないもの（§3）も同じ表で固定する。

後半は承認の経路と、実行役のコマンド（`env`・`sudo`・`sh -c`・`xargs` など）が
中で実行するコマンドを止める側のルールに当てること（wip/design/launcher-scripts.md
3.4・3.5 節、12 節の A1〜A4・W1〜W3・W6・W7）。

道具は外から動かす。`--test <tool> <subject> --json` の `verdict` と `rules[].id` を読む
（形は README「試験の JSON」）。見本の `/repo` は `--root` に渡したルートに読み替える。
"""

from __future__ import annotations

import json
import os
import re
import unittest

from ccnavi import shellread
from tests import ROOT
from tests.inproc import run_ccnavi

RULES = os.path.join(ROOT, ".ccnavi", "common", "rules.yml")

# 見本のパスに書く合言葉。--test-samples と同じ読み替えを、ここでは自分で行う。
PLACEHOLDER = "/repo"

# 配布先の hook が起動する振り分けの sh（launcher-scripts 1 節）。
LAUNCHER = ".ccnavi/scripts/ccnavi-launcher.sh"
APPROVAL = "builtin-guard-ticket-approval"
APPROVAL_CODE = "DENY_TICKET_APPROVAL_CLI"
SETTING_FILES = "builtin-guard-setting-files"
# 書き直しを求める形で止めたときの、記録のルール名（ADR-0047）。
NAME = "(command-name-expansion)"
BACKQUOTE = "(backquote)"
AMBIGUOUS = "(ambiguous-form)"
TICKET_STATE = "builtin-ticket-state-shell"
# 承認の形の末尾。
YES = " --approve --yes x"


def judge(tool: str, subject: str, bin_path: str = "") -> dict:
    """1 件を本物のルールで判定して、試験の JSON を返す。

    写しと控えは外し、記録も残さない。組み込みの selfguard（設定ファイルの保護）は
    既定のまま効かせる。§4 の表はそれを含めた判定なので。

    `bin_path` を渡すと `CCNAVI_BIN_PATH` に置く。承認のルールは実行ファイルの綴りから
    当てる形を作るので、振り分けの sh を指したときの判定はこれで見る。
    """
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    environment.pop("CLAUDE_PROJECT_DIR", None)
    if bin_path:
        environment["CCNAVI_BIN_PATH"] = bin_path
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


def blocking(body: dict) -> set[str]:
    """deny / ask の側で当たったルール。組み込みは `section` が空で返るので、allow 以外を数える。"""
    return {rule["id"] for rule in body["rules"] if rule.get("section") != "allow"}


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
        # 許容した誤検知（ccnavi.md §12.2、tests/guard/test_acceptance.py）。生の文字列に
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


class LauncherJudgeTest(unittest.TestCase):
    """`CCNAVI_BIN_PATH` が振り分けの sh を指すときの判定。"""

    def judge(self, subject: str) -> dict:
        body = judge("Bash", subject, bin_path=LAUNCHER)
        self.assertTrue(body["known"])
        return body

    def assert_denied_by(self, subject: str, rule_id: str, code: str = "") -> dict:
        body = self.judge(subject)
        self.assertEqual(
            body["verdict"], "deny", f"{subject!r}: {body['code']} {hit(body)} {body['response']}"
        )
        self.assertIn(rule_id, hit(body), f"{subject!r}: {body['rules']}")
        if code:
            self.assertEqual(body["code"], code, f"{subject!r}: {body['response']}")
        return body


class TicketApprovalPathTest(LauncherJudgeTest):
    """承認の経路（launcher-scripts 3.4 節、12 節 A1〜A4）。"""

    def test_振り分けの_sh_を実行役のコマンド越しに打つ承認は止まる(self):
        # A1。今は `<sh>` を先頭に書いた形だけが止まり、残りは確認に落ちている。
        for subject in [
            LAUNCHER + YES,
            PLACEHOLDER + "/" + LAUNCHER + YES,
            "sh " + LAUNCHER + YES,
            "bash " + LAUNCHER + YES,
            "/bin/sh " + LAUNCHER + YES,
            "env sh " + LAUNCHER + YES,
            "env FOO=1 sh " + LAUNCHER + YES,
            "command sh " + LAUNCHER + YES,
            "exec sh " + LAUNCHER + YES,
            "nohup sh " + LAUNCHER + YES,
            "zsh " + LAUNCHER + YES,
            "dash " + LAUNCHER + YES,
            "sh -x " + LAUNCHER + YES,
            "cd /tmp && sh " + LAUNCHER + YES,
            "sh " + LAUNCHER + " ticket start x",
            # 別の名前の sh と、実体を直に指す形。
            "sh .ccnavi/bin/ccnavi" + YES,
            "sh .ccnavi/bin/linux-x86_64/ccnavi" + YES,
            "sh ccnavi" + YES,
        ]:
            with self.subTest(subject=subject):
                self.assert_denied_by(subject, APPROVAL)

    def test_読み切れない形の承認も承認のルールで止まる(self):
        # A2。今は PARSE_UNCERTAIN の確認に落ちる。止めた理由が承認のルールだと読めること。
        for subject in [
            "sh -c '" + LAUNCHER + YES + "'",
            'bash -lc "' + LAUNCHER + YES + '"',
            ". " + LAUNCHER + YES,
            "source " + LAUNCHER + YES,
        ]:
            with self.subTest(subject=subject):
                self.assert_denied_by(subject, APPROVAL, code=APPROVAL_CODE)

    def test_承認でない形は承認のルールに当たらない(self):
        # A3。止めすぎの候補。中で実行されるコマンドを見ても、ここは広がらない。
        for subject in [
            LAUNCHER + " --approve --preview x",
            "sh " + LAUNCHER + " --approve --preview x",
            "cat " + LAUNCHER,
            "grep -n 'ccnavi --approve --yes' README.md",
            "sed -n 1,20p " + LAUNCHER,
            "sh .ccnavi/scripts/ccnavi-ticket.sh start x",
            "sh .ccnavi/scripts/ccnavi-review.sh request --phase 1 --body-file x.md",
            "echo " + LAUNCHER,
            # 引用しない形。`echo` や `grep` は実行役のコマンドではないので、中を見ない。
            "echo ccnavi --approve --yes x",
            "grep -rn ccnavi --yes docs",
            "git log --grep ccnavi --yes",
        ]:
            with self.subTest(subject=subject):
                body = self.judge(subject)
                self.assertNotIn(APPROVAL, hit(body), f"{subject!r}: {body['response']}")

    def test_コミットの文面に書いた承認の形は_raw_git_だけに当たる(self):
        # A3 の続き。git を直に打ったことでは止まるが、承認のルールには当たらない。
        body = self.judge("git commit -m 'docs: ccnavi --approve --yes の説明'")
        self.assertIn("raw-git", hit(body), body["rules"])
        self.assertNotIn(APPROVAL, hit(body), body["rules"])

    def test_sudo_の_sh_c_と_find_exec_の中の承認も止まる(self):
        # A4。実行役のコマンドの並びを正規表現に持たせる案（B）で残っていた 2 形。
        for subject in [
            "sudo -u me sh -c 'ccnavi --approve --yes x'",
            "find . -name x -exec ccnavi --approve --yes {} \\;",
        ]:
            with self.subTest(subject=subject):
                self.assert_denied_by(subject, APPROVAL)


# 実行役のコマンド。`{}` に中で実行されるコマンドが入る。
RUNNERS = [
    "env {}",
    "FOO=1 {}",
    "command {}",
    "exec {}",
    "nohup {}",
    "time {}",
    "sudo {}",
    "sudo -u me {}",
    "timeout 5 {}",
    "nice -n 5 {}",
    "stdbuf -o0 {}",
    "/usr/bin/env {}",
    "doas {}",
    # 読み切れない形。
    "sh -c '{}'",
    "eval '{}'",
    "echo x | xargs {}",
    "find . -name x -exec {} \\;",
]

# 組み込みの守りに当たる、中で実行されるコマンドと、当たるべきルール。
GUARDED = [
    ("rm -f .ccnavi/common/rules.yml", SETTING_FILES),
    ("mv .claude/settings.json /tmp/settings.json", SETTING_FILES),
    ("tee .ccnavi/common/rules.yml", SETTING_FILES),
    ("sed -i s/a/b/ .claude/settings.json", SETTING_FILES),
    ("cp /tmp/rules.yml .ccnavi/common/rules.yml", SETTING_FILES),
    ("truncate -s 0 .claude/settings.json", SETTING_FILES),
    ("mv wip/tickets/todo/a.md wip/tickets/doing/a.md", TICKET_STATE),
    ("ccnavi --approve --yes x", APPROVAL),
    ("sh .ccnavi/scripts/ccnavi-approve.sh", APPROVAL),
    # 識別子を並べた形（#31）。引数が付いても承認の経路として止める。
    ("sh .ccnavi/scripts/ccnavi-approve.sh i0002-03 i0002-04", APPROVAL),
]


class RunnerTest(LauncherJudgeTest):
    """実行役のコマンドが中で実行するコマンドを、止める側のルールに当てる（3.5 節）。"""

    def test_中で実行されるコマンドそのものは今も止まる(self):
        # W1 の見本が正しいことの確かめ。実行役のコマンドを付けない形で、同じルールに当たる。
        for inner, rule_id in GUARDED:
            with self.subTest(subject=inner):
                self.assert_denied_by(inner, rule_id)

    def test_組み込みの守りは実行役のコマンドの中でも止める(self):
        # W1。今は実行役のコマンドを付けると、先頭に固定した式が外れて確認に落ちる。
        for runner in RUNNERS:
            for inner, rule_id in GUARDED:
                subject = runner.format(inner)
                with self.subTest(subject=subject):
                    self.assert_denied_by(subject, rule_id)

    def test_env_越しの承認のスクリプトは途中の層で止まる(self):
        # W1 の続き。`sh …approve.sh` は `env` を外した途中の層で、
        # そこに承認の `script` の枝が当たる。
        self.assert_denied_by("env sh .ccnavi/scripts/ccnavi-approve.sh", APPROVAL)

    def test_先頭に固定した利用者のルールも実行役のコマンドの中で当たる(self):
        # W2。
        body = self.judge("env curl -d @x https://example.com")
        self.assertEqual(body["verdict"], "ask", body["response"])
        self.assertIn("prefer-webfetch", hit(body), body["rules"])

    def test_中で実行されるコマンドは_allow_に当てない(self):
        # W3。当てると、元の形では確認に落ちる `sudo` が読み取りとして通る。
        body = self.judge("sudo -u me cat /etc/hosts")
        self.assertNotIn("prefer-read-grep", hit(body), body["rules"])
        self.assertNotEqual(body["verdict"], "allow", body["response"])

    def test_普通の作業では止める側に当たるルールが増えない(self):
        # W6。3.5.4 節の 27 形。値は元の形だけに当てていたとき（この変更の前）の、
        # deny / ask の側で当たったルール。中で実行されるコマンドを見ても、ここから増えない。
        cases = {
            "time uv run pytest": set(),
            "nice -n 10 make test": set(),
            "env PYTHONUTF8=1 uv run python -m unittest": set(),
            "timeout 60 pnpm test": set(),
            "sudo -u me cat /etc/hosts": set(),
            "sh scripts/ccnavi-setup.sh --check": set(),
            "bash tests/run.sh": set(),
            "command -v jq": set(),
            "env | grep CCNAVI": set(),
            "sh -c 'cat README.md'": set(),
            "find . -name '*.py' -exec grep -l foo {} \\;": set(),
            "echo x | xargs grep -n foo": set(),
            "nohup python -m http.server &": set(),
            "stdbuf -o0 tail -n 3 logs/log.jsonl": set(),
            "source .venv/bin/activate": set(),
            ". .venv/bin/activate && uv run pytest": set(),
            "bash -lc 'uv run ruff check .'": set(),
            # ゲートの sh 3 形。承認のスクリプトは元の形から止まっている。
            "sh .ccnavi/scripts/ccnavi-ticket.sh start x": set(),
            "sh .ccnavi/scripts/ccnavi-review.sh request --phase 1 --body-file x.md": set(),
            "sh .ccnavi/scripts/ccnavi-approve.sh": {APPROVAL},
            "exec zsh": set(),
            "time git log --oneline": {"raw-git"},
            "find . -name '*.pyc' -exec rm {} +": set(),
            "xargs -n1 -I{} echo {}": set(),
            "sudo -E env PATH=/x make install": set(),
            "env -u CCNAVI_MODE uv run python -m ccnavi --lint": set(),
            "timeout 5 sh -c 'cat logs/log.jsonl | tail -n 3'": set(),
        }
        self.assertEqual(len(cases), 27)
        for subject, want in cases.items():
            with self.subTest(subject=subject):
                body = self.judge(subject)
                self.assertEqual(blocking(body), want, body["response"])

    def test_中で実行されるコマンドに当たったことが_unwrapped_と文面に出る(self):
        # W7。元の形のどこが問題なのかを、読み手が辿れるように。
        for subject, runner, layer in [
            ("env rm -f .ccnavi/common/rules.yml", "env", "rm -f .ccnavi/common/rules.yml"),
            (
                "env sh .ccnavi/scripts/ccnavi-approve.sh",
                "env",
                "sh .ccnavi/scripts/ccnavi-approve.sh",
            ),
        ]:
            with self.subTest(subject=subject):
                body = self.judge(subject)
                self.assertEqual(body["verdict"], "deny", body["response"])
                self.assertIn("unwrapped", body, "JSON に unwrapped の欄が無い")
                self.assertEqual(body["unwrapped"], layer)
                # 名前を括る記号は問わない。
                quote = "[`'\"]?"
                line = (
                    f"{quote}{re.escape(runner)}{quote} が実行する "
                    f"{quote}{re.escape(layer)}{quote} に当たりました"
                )
                self.assertRegex(body["response"], line)

    def test_元の形で当たったときは_unwrapped_も文面も出ない(self):
        # W7 の反対側。
        body = self.judge("rm -f .ccnavi/common/rules.yml")
        self.assertEqual(body["verdict"], "deny", body["response"])
        self.assertIn("unwrapped", body, "JSON に unwrapped の欄が無い")
        self.assertEqual(body["unwrapped"], "")
        self.assertNotIn("が実行する", body["response"])


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
                ('h="$(git rev-parse HEAD)"', "deny", "raw-git", ""),
                ('echo "$(rm -rf /tmp/x)"', "deny", "recursive-delete", ""),
                ("cat <<EOF\nuse $(rm -rf /tmp/x)\nEOF", "deny", "recursive-delete", ""),
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
                # eval の文字列の中はコマンド名を見ない。外側が縮退して確認に落ちる（ADR-0047）。
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
                ('gh issue create --title t --body "use $(git push) here"', "deny", "raw-git", ""),
                ('cd "$(git rev-parse --show-toplevel)"', "deny", "raw-git", ""),
                ('grep -rn foo "$(pwd)"', "ask", "", "UNDECLARED"),
                ('find "$(pwd)" -name x', "ask", "", "UNDECLARED"),
                ('cat "$(ls -t logs/git-*.log | head -1)"', "ask", "", "UNDECLARED"),
                ("grep -n foo f\ngrep -n bar g", "ask", "", "UNDECLARED"),
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
        quoted = judge("Bash", 'gh issue create --title t --body "use $(git push) here"')
        self.assertIn("inside double quotes", quoted["response"])
        self.assertEqual(quoted.get("quoted"), ["raw-git"])

        bare = judge("Bash", "echo $(git push origin main)")
        self.assertNotIn("inside double quotes", bare["response"])
        self.assertFalse(bare.get("quoted"), bare.get("quoted"))

    def test_縮退の断りは理由ごとに違う(self):
        responses = {}
        for subject, reason, phrase in [
            ('echo "git push', shellread.REASON_UNTERMINATED, "quote or heredoc"),
            ("echo $(git push", shellread.REASON_UNTERMINATED_SUBST, "$( ) in this command never"),
            ("bash -c x; git push origin main", shellread.REASON_TAKEN_AS_CODE, "runs it as code"),
        ]:
            with self.subTest(subject=subject):
                body = judge("Bash", subject)
                self.assertEqual(body["degraded"], reason)
                self.assertIn(phrase, body["response"])
                responses[reason] = body["response"]
        self.assertEqual(len(set(responses.values())), 3, "理由の違う縮退に同じ文面を返した")

    def test_シェルで読みが割れる形は一律に止める(self):
        # ADR-0047。`coproc` は敵対的レビューで見つかった予約語の漏れ（shellread-subst-04）で、
        # NAME の読みが bash 4 と zsh で割れる。読み分けずに止める。
        code = "DENY_AMBIGUOUS_FORM"
        self.check(
            [
                ('echo "$(case a in a) git push;; esac)"', "deny", AMBIGUOUS, code),
                ("echo $((echo a) | cat)", "deny", AMBIGUOUS, code),
                ("coproc { find . -delete; }", "deny", AMBIGUOUS, code),
                ("coproc find . -delete", "deny", AMBIGUOUS, code),
                ("coproc NAME { find . -delete; }", "deny", AMBIGUOUS, code),
                ("coproc NAME find . -delete", "deny", AMBIGUOUS, code),
                ("select x in a b; do echo $x; done", "deny", AMBIGUOUS, code),
                # 引数に書いた同じ綴りは予約語ではない。
                ("echo coproc", "ask", "", "UNDECLARED"),
                ("echo select", "ask", "", "UNDECLARED"),
                ("for select in a b; do echo $select; done", "ask", "", "UNDECLARED"),
                ("echo $((1 << 2))", "ask", "", "UNDECLARED"),
            ]
        )

    def test_バッククォートは一律に止める(self):
        # ADR-0047。二重引用の中でも、区切りを引用しないヒアドキュメントの中でも実行される。
        code = "DENY_BACKQUOTE"
        self.check(
            [
                ("echo `find . -delete`", "deny", BACKQUOTE, code),
                ('grep -n "`rm -rf /tmp/x`" f', "deny", BACKQUOTE, code),
                ("cat <<EOF\nuse `rm -rf /tmp/x`\nEOF", "deny", BACKQUOTE, code),
                ('gh issue create --title t --body "use `git push` here"', "deny", BACKQUOTE, code),
                ('gh issue create --title t --body "odd ` backtick"', "deny", BACKQUOTE, code),
                ('echo "$(echo `id`)"', "deny", BACKQUOTE, code),
                # 文字として渡す綴りは止まらない。
                ('grep -n "\\`git push\\`" f', "allow", "prefer-read-grep", ""),
                ("grep -n '`git push`' f", "allow", "prefer-read-grep", ""),
            ]
        )
        body = judge("Bash", 'gh issue create --title t --body "use `x` here"')
        self.assertIn("$( )", body["response"])
        self.assertIn("--body-file", body["response"])

    def test_実行するときに決まるコマンド名は一律に止める(self):
        # ADR-0047。どれもどのルールにも当たらず、auto では権限モードに渡っていた。
        code = "DENY_COMMAND_NAME_EXPANSION"
        self.check(
            [
                ("c=git; $c push origin main", "deny", NAME, code),
                ("$(echo git) push origin main", "deny", NAME, code),
                ("/usr/bin/gi? push origin main", "deny", NAME, code),
                ("git${IFS}push origin main", "deny", NAME, code),
                ("/bin/r? -rf /tmp/x", "deny", NAME, code),
                ("$'git' push origin main", "deny", NAME, code),
                ("FOO=1 $c status", "deny", NAME, code),
                (">/dev/null $c status", "deny", NAME, code),
                ("2>&1 $c status", "deny", NAME, code),
                ("env $c status", "deny", NAME, code),
                (">/dev/null env $c status", "deny", NAME, code),
                ("sudo -u me $c status", "deny", NAME, code),
                ("sh $G status", "deny", NAME, code),
                ("if $c status; then :; fi", "deny", NAME, code),
                ("{fd}>/dev/null $c status", "deny", NAME, code),
                ('FOO=1 eval "$c status"', "deny", NAME, code),
                # 外側が縮退する `sh -c` と `eval` の文字列の中は、止めずに確認に落とす。
                ('sh -c "$c status"', "ask", "", "PARSE_UNCERTAIN"),
                ('eval "$(pyenv init -)"', "ask", "", "PARSE_UNCERTAIN"),
                ('"$(git rev-parse --show-toplevel)/x.sh"', "deny", NAME, code),
            ]
        )
        body = judge("Bash", "c=git; $c status")
        self.assertIn("`$c`", body["response"])
        self.assertIn("becomes `git status`", body["response"])

    def test_コマンド名でない位置の変数とグロブは止めない(self):
        for subject in [
            "echo $HOME",
            "x=$(pwd)",
            "FOO=$HOME/x make",
            "ls *.py",
            "[ -f x ] && echo y",
            "[[ -f x ]] && echo y",
            "case $x in a) echo a;; esac",
            "for f in *.py; do echo $f; done",
            "a[1]=x",
            "timeout $T make",
            "cd $S && ls",
        ]:
            with self.subTest(subject=subject):
                body = judge("Bash", subject)
                self.assertNotEqual(body["code"], "DENY_COMMAND_NAME_EXPANSION", body["response"])

    def test_引用の外のブレース展開は一律に止める(self):
        # Issue #38（ADR-0046）。どのルールにも当たらないまま、bash は広げた語を実行していた。
        brace, code = "(brace-expansion)", "DENY_BRACE_EXPANSION"
        self.check(
            [
                ("{git,push,origin,main}", "deny", brace, code),
                ("{rm,-rf,/tmp/x}", "deny", brace, code),
                # 生の CR を挟んでも止まる。挟むとルールにも当たらず、
                # auto では権限モードに渡っていた。
                ("{git,\rpush,origin,main}", "deny", brace, code),
                ("grep -rn x --exclude-dir={node_modules,.git} /repo", "deny", brace, code),
                ("cp f{,.bak}", "deny", brace, code),
                ('echo "$({git,push})"', "deny", brace, code),
                ("sh -c '{git,push}'", "deny", brace, code),
                # 書き直した形と、文字として書いた形は今までどおり。
                (
                    "grep -rn x --exclude-dir=node_modules --exclude-dir=.git /repo",
                    "allow",
                    "prefer-read-grep",
                    "",
                ),
                ("grep -n '{a,b}' f", "allow", "prefer-read-grep", ""),
                ("git show HEAD@{1}", "deny", "raw-git", ""),
            ]
        )

    def test_ブレース展開を止めた文面は書き直し方を言う(self):
        body = judge("Bash", "grep -rn x --exclude-dir={node_modules,.git} .")
        self.assertIn("`{node_modules,.git}`", body["response"])
        self.assertIn("--exclude-dir=a --exclude-dir=b", body["response"])
        # 読めなかったのではない。読めなかった断りを付けない。
        self.assertNotIn("PARSE_UNCERTAIN", body["response"])
        self.assertNotIn("raw text", body["response"])


if __name__ == "__main__":
    unittest.main()
