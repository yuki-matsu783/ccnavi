"""このリポジトリの実際のルール（.ccnavi/config/common/rules.yml）と組み込みルールで判定する受入テスト。

tests/fixtures/ のルールではなく、運用に使っている rules.yml をそのまま `--test` に
渡す。見るのは、shellread が目印を 2 つに分けたあとの判定（wip/design/shellread-sep.md
4「見本 → 判定」）。ルールの `[^\\x00]*` が「同じコマンドの中」だけを指すようになり、
引用付きの grep / find が allow にヒットする一方、引用の空白をまたいだ書き換えが deny に
ヒットすること。変わってはいけないもの（3 章）も同じ表で固定する。

後半は承認の経路と、実行役のコマンド（`env`・`sudo`・`sh -c`・`xargs` など）が
中で実行するコマンドを止める側のルールに適用すること（wip/design/launcher-scripts.md
3.4・3.5 節、12 節の A1〜A4・W1〜W3・W6・W7）。

判定とヒットしたルールの名前だけを見る表は Go（go/internal/hook/judge の `TestRepoRulesTable`、testdata/repo_rules.json）に移した。
ここに残すのは、文面・縮退の理由・unwrapped・quoted・環境の切り替えを見るもの。

道具は外から動かす。`--test <tool> <subject> --json` の `verdict` と `rules[].id` を読む
（形は docs/11_requirements/json-shapes.md「試験の JSON」）。
期待する判定の `/repo` は `--root` に渡したルートに読み替える。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi

RULES = os.path.join(ROOT, ".ccnavi", "config", "common", "rules.yml")

# 期待する判定のパスに書く合言葉。--test-expectations と同じ読み替えを、ここでは自分で行う。
PLACEHOLDER = "/repo"

# 配布先の hook が起動する振り分けの sh（launcher-scripts 1 節）。
LAUNCHER = ".ccnavi/scripts/ccnavi-launcher.sh"
APPROVAL = "builtin:guard-ticket-approval"
SETTING_FILES = "builtin:guard-setting-files"
# 書き直しを求める形で止めたときの、記録のルール名。読み分けずに止め、書き直し方を案内する。
NAME = "builtin:command-name-expansion"
BACKQUOTE = "builtin:backquote"
TICKET_STATE = "builtin:ticket-state-shell"
# 読み切れなかった理由（試験の JSON の degraded）。
REASON_UNTERMINATED = "unterminated-quote"
REASON_UNTERMINATED_SUBST = "unterminated-substitution"
REASON_TAKEN_AS_CODE = "command-taken-as-code"


def judge(tool: str, subject: str, bin_path: str = "") -> dict:
    """1 件を実際のルールで判定して、試験の JSON を返す。

    承認済みチケットと state は外し、記録も残さない。組み込みの selfguard（設定ファイルの保護）は
    既定のまま有効にする。4 の表はそれを含めた判定なので。

    `bin_path` を渡すと `CCNAVI_ENTRY_PATH` に置く。承認のルールは実行ファイルのパスから
    適用する形を作るので、振り分けの sh を指したときの判定はこれで見る。
    """
    environment = _environment(bin_path)
    done = run_ccnavi(
        [
            "--root",
            ROOT,
            "--rules",
            RULES,
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


def judge_many(tool: str, subjects: list[str], bin_path: str = "") -> list[dict]:
    """何件もを 1 回の起動で判定して、1 件ごとの結果を並べて返す。

    `--test-expectations` に渡す。判定は `--test` と同じ関数（TryOne）を通るので、`known`・
    `verdict`・`code`・`rules` は `judge` と同じ。文面（`response`）・`degraded`・`unwrapped`・
    `quoted` は持たないので、それを見るテストは `judge` で 1 件ずつ見る。
    """
    environment = _environment(bin_path)
    root = ROOT.replace("\\", "/")
    # 期待のタイプは判定に効かない。JSON は YAML としても読める。
    items = [{"tool": tool, "subject": s.replace(PLACEHOLDER, root)} for s in subjects]
    with tempfile.TemporaryDirectory(prefix="ccnavi-repo-rules-") as scratch:
        path = os.path.join(scratch, "expectations.yml")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ask": items}, f)
        done = run_ccnavi(
            ["--root", ROOT, "--rules", RULES, "--test-expectations", path, "--json"],
            input="",
            cwd=ROOT,
            env=environment,
        )
    if done.returncode != 0:
        raise AssertionError(f"--test-expectations が失敗した: {done.stderr}")
    bodies = json.loads(done.stdout)["expectations"]
    if len(bodies) != len(subjects):
        raise AssertionError(f"{len(subjects)} 件を渡して {len(bodies)} 件が返った")
    for body in bodies:
        body["response"] = ""
    return bodies


def _environment(bin_path: str) -> dict:
    environment = {
        **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
        "CCNAVI_LEARNING": "disable",
    }
    environment.pop("CLAUDE_PROJECT_DIR", None)
    if bin_path:
        environment["CCNAVI_ENTRY_PATH"] = bin_path
    return environment


def written_id(name: str) -> str:
    """記録名（`<相対パス>:<書いた id>`）から書いた id を取り出す。組み込みの名前はそのまま。"""
    if name.startswith("builtin:"):
        return name
    return name.rsplit(":", 1)[-1]


def hit(body: dict) -> list[str]:
    return [written_id(rule["id"]) for rule in body["rules"]]


class RepoRulesTest(unittest.TestCase):
    """4「見本 → 判定」の表。"""

    def assert_verdict(self, subject, want, rule_id):
        return self.check_body(judge("Bash", subject), want, rule_id)

    def assert_verdicts(self, subjects, want, rule_id):
        """何件もを 1 回の起動で判定し、どれも want で rule_id にヒットすることを見る。"""
        for subject, body in zip(subjects, judge_many("Bash", subjects), strict=True):
            with self.subTest(subject=subject):
                self.check_body(body, want, rule_id)

    def check_body(self, body, want, rule_id):
        self.assertTrue(body["known"])
        self.assertEqual(body["verdict"], want, body["response"])
        if rule_id:
            self.assertIn(rule_id, hit(body), body["rules"])
        else:
            self.assertEqual(hit(body), [], body["rules"])
        return body

    def test_確認の理由の固定文は非対話のときだけ出る(self):
        from unittest import mock

        script = "sh /repo/.ccnavi/scripts/ccnavi-worktree.sh"
        cases = {
            f"{script} x --base feature/x": "direct-worktree-confirm",
            "curl -s https://example.com/a": "prefer-webfetch",
        }
        for subject, rule_id in cases.items():
            for attended, shown in (("0", True), ("1", False), (None, False)):
                with self.subTest(subject=subject, attended=attended):
                    patch = {} if attended is None else {"CLAUDE_CODE_SESSION_ATTENDED": attended}
                    with mock.patch.dict(os.environ, patch):
                        if attended is None:
                            os.environ.pop("CLAUDE_CODE_SESSION_ATTENDED", None)
                        body = self.assert_verdict(subject, "ask", rule_id)
                    said = json.dumps(body["response"], ensure_ascii=False)
                    self.assertEqual(shown, "確認に答えられません" in said, said)
                    if rule_id == "prefer-webfetch":
                        self.assertEqual(shown, "まとめてから実行する" in said, said)

    def test_リダイレクトの上限で止めた拒否には確認の固定文を足さない(self):
        from unittest import mock

        # ask のルールにもヒットするが、返すのは deny。打ち直しても通らないので、了承を頼ませない。
        # 閉じない引用で縮退させ、生の文字列の `>` を上限（50）より多く並べる。
        subject = 'curl -s https://example.com/a "' + ">a" * 51
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ATTENDED": "0"}):
            body = judge("Bash", subject)
        self.assertEqual("deny", body["verdict"], body["response"])
        self.assertIn("prefer-webfetch", hit(body))
        self.assertNotIn("確認に答えられません", json.dumps(body["response"], ensure_ascii=False))

    # 変わるべきもの

    def test_worktree_add_は通して_fetch_の案内を添える(self):
        # 止めない。遅れの判定で止めるのは ccnavi-git.sh の中（hook はネットワークに出ない）。
        body = self.assert_verdict(
            "sh /repo/.ccnavi/scripts/ccnavi-git.sh worktree add .claude/worktrees/y -b y main",
            "allow",
            "ccnavi-git-worktree-add",
        )
        # ルートが渡らない読み方では `{script:}` は置き換わらない。
        # 置き換えは tests/core/test_script_placeholder.py
        self.assertIn("ccnavi-worktree.sh", body["response"])
        self.assertNotIn("--no-track", body["response"], "手の worktree add に誘わない")
        self.assertNotIn("ff-only", body["response"])
        # ブランチが既にあるときの worktree add は、続けて --adopt が要ります
        # （言い切りで済ませない）。
        self.assertIn("--adopt --base", body["response"])
        self.assertIn("integrate できません", body["response"])
        # ほかのサブコマンドには添えない。
        self.assertNotIn(
            "ccnavi-git-worktree-add",
            hit(
                judge("Bash", "sh /repo/.ccnavi/scripts/ccnavi-git.sh worktree list"),
            ),
        )

    def test_integrate_は通して_確定の前の報告の指示を添える(self):
        script = "sh /repo/.ccnavi/scripts/ccnavi-git.sh"
        for subject in (
            f"{script} integrate foo",
            f"cd /repo/.claude/worktrees/foo && {script} integrate foo",
            f"cd /repo && {script} integrate foo-1",
        ):
            with self.subTest(subject=subject):
                body = self.assert_verdict(subject, "allow", "integrate-report-first")
                self.assertIn("integrate-done の前", body["response"])
                self.assertIn("DENY_NEEDS_USER_TURN", body["response"])
        # 確定・取りやめ・名前なしには添えない。
        others = [
            f"{script} integrate-done foo -m x",
            f"{script} integrate --abort foo",
            f"{script} integrate",
            f"{script} status",
        ]
        for subject, body in zip(others, judge_many("Bash", others), strict=True):
            with self.subTest(subject=subject):
                self.assertNotIn("integrate-report-first", hit(body))

    # 変わってはいけないもの

    def test_引用だけの二重の山括弧は読めないまま止まる(self):
        # 許容した誤検知（tests/guard/test_acceptance.py）。生の文字列に
        # heredoc がヒットし、読めなかったことを示す。
        body = judge("Bash", 'grep -n "<<" README.md')
        self.assertEqual(body["verdict"], "deny")
        self.assertEqual(body["code"], "PARSE_UNCERTAIN")
        self.assertEqual(body["degraded"], REASON_UNTERMINATED)
        self.assertIn("生の文字列", body["response"])


class LauncherJudgeTest(unittest.TestCase):
    """`CCNAVI_ENTRY_PATH` が振り分けの sh を指すときの判定。"""

    def judge(self, subject: str) -> dict:
        body = judge("Bash", subject, bin_path=LAUNCHER)
        self.assertTrue(body["known"])
        return body

    def judge_all(self, subjects) -> list[dict]:
        bodies = judge_many("Bash", list(subjects), bin_path=LAUNCHER)
        for body in bodies:
            self.assertTrue(body["known"])
        return bodies

    def assert_all_denied_by(self, subjects, rule_id: str, code: str = "") -> None:
        """何件もを 1 回の起動で判定し、どれも rule_id で止まることを見る。"""
        subjects = list(subjects)
        for subject, body in zip(subjects, self.judge_all(subjects), strict=True):
            with self.subTest(subject=subject):
                self.check_denied_by(subject, body, rule_id, code)

    def assert_denied_by(self, subject: str, rule_id: str, code: str = "") -> dict:
        return self.check_denied_by(subject, self.judge(subject), rule_id, code)

    def check_denied_by(self, subject: str, body: dict, rule_id: str, code: str = "") -> dict:
        self.assertEqual(
            body["verdict"], "deny", f"{subject!r}: {body['code']} {hit(body)} {body['response']}"
        )
        self.assertIn(rule_id, hit(body), f"{subject!r}: {body['rules']}")
        if code:
            self.assertEqual(body["code"], code, f"{subject!r}: {body['response']}")
        return body


class TicketApprovalPathTest(LauncherJudgeTest):
    """承認の経路（launcher-scripts 3.4 節、12 節 A1〜A4）。"""


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

# 組み込みの保護にヒットする、中で実行されるコマンドと、ヒットするべきルール。
GUARDED = [
    ("rm -f .ccnavi/config/common/rules.yml", SETTING_FILES),
    ("mv .claude/settings.json /tmp/settings.json", SETTING_FILES),
    ("tee .ccnavi/config/common/rules.yml", SETTING_FILES),
    ("sed -i s/a/b/ .claude/settings.json", SETTING_FILES),
    ("cp /tmp/rules.yml .ccnavi/config/common/rules.yml", SETTING_FILES),
    ("truncate -s 0 .claude/settings.json", SETTING_FILES),
    ("mv .ccnavi/approved/doing/a.md wip/proposals/review/a.md", TICKET_STATE),
    ("ccnavi --agree --yes x", APPROVAL),
    ("sh .ccnavi/scripts/ccnavi-user-agree.sh", APPROVAL),
    # 識別子を並べた形（#31）。引数が付いても承認の経路として止める。
    ("sh .ccnavi/scripts/ccnavi-user-agree.sh i0002-01-03 i0002-01-04", APPROVAL),
]


class RunnerTest(LauncherJudgeTest):
    """実行役のコマンドが中で実行するコマンドを、止める側のルールに適用する（3.5 節）。"""

    def test_中で実行されるコマンドにヒットしたことが_unwrapped_と文面に出る(self):
        # W7。元の形のどこが問題なのかを、読み手が辿れるように。
        for subject, runner, layer in [
            (
                "env rm -f .ccnavi/config/common/rules.yml",
                "env",
                "rm -f .ccnavi/config/common/rules.yml",
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
                    f"{quote}{re.escape(layer)}{quote} にヒットしました"
                )
                self.assertRegex(body["response"], line)

    def test_元の形でヒットしたときは_unwrapped_も文面も出ない(self):
        # W7 の反対側。
        body = self.judge("rm -f .ccnavi/config/common/rules.yml")
        self.assertEqual(body["verdict"], "deny", body["response"])
        self.assertIn("unwrapped", body, "JSON に unwrapped の欄が無い")
        self.assertEqual(body["unwrapped"], "")
        self.assertNotIn("が実行する", body["response"])


class MovedJudgeTest(LauncherJudgeTest):
    """`cd` で移った先から見たパスに、止める側のルールを適用する（issue #61）。"""

    def test_文面は移った先から見たパスを示す(self):
        body = self.judge("cd .claude && echo x > settings.json")

        self.assertIn("`cd` で移った先から見ると", body["response"])
        self.assertIn(".claude/settings.json", body["response"])

    def test_行き先を読めない_cd_は読みを変えない(self):
        # 縮退させない。縮退すると生の文字列で見るので、コマンドの頭に固定して書かれた
        # 保護（`(^|\x00)(mv|rm|tee|…)`）がヒットしなくなり、書かれた表記で今は
        # 止まっている形が止まらなくなる（敵対的レビュー）。
        for subject in [
            'cd "$(pwd)" && rm -f /repo/.ccnavi/config/common/rules.yml',
            "cd - && cp /tmp/x .ccnavi/config/common/rules.yml",
            "pushd .claude && mv .ccnavi/config/common/rules.yml /tmp/x",
        ]:
            with self.subTest(subject=subject):
                body = self.assert_denied_by(subject, SETTING_FILES)
                self.assertEqual(body["degraded"], "", body["response"])

    def test_読めない行き先のあとは継ぎ足さないだけ(self):
        # 読み自体は変わらない。読み切れなかった扱い（PARSE_UNCERTAIN）にはしない。
        body = self.judge("cd - && cat README.md")

        self.assertEqual(body["degraded"], "", body["response"])
        self.assertEqual(body["code"], "UNDECLARED", body["response"])


class SubstRepoRulesTest(unittest.TestCase):
    """コマンド置換・改行・プロセス置換を読んだあとの判定。

    wip/design/shellread-subst.md の 3 と 4.3。

    期待は (subject, 判定, ヒットするルールの id, 根拠コード)。
    id が空ならどのルールにもヒットしないこと、
    コードが空なら見ない。
    """

    def check(self, cases):
        bodies = judge_many("Bash", [c[0] for c in cases])
        for (subject, want, rule_id, code), body in zip(cases, bodies, strict=True):
            with self.subTest(subject=subject):
                self.assertTrue(body["known"])
                self.assertEqual(body["verdict"], want, body["response"])
                if rule_id:
                    self.assertIn(rule_id, hit(body), body["rules"])
                elif want != "skip":
                    self.assertEqual(hit(body), [], body["rules"])
                if code:
                    self.assertEqual(body["code"], code, body["response"])

    # 変わるべきもの

    # 変わってはいけないもの

    # 増える誤検知と回避策

    # 文面（4.3）

    def test_引用の中から切り出したコマンドにヒットしたときだけ断りが出る(self):
        quoted = judge("Bash", 'gh issue create --title t --body "use $(git push) here"')
        self.assertIn("二重引用符の中", quoted["response"])
        # 引用の中の git push は、組み込みの push と生の git の両方にヒットする。
        self.assertIn("builtin:guard-raw-git", quoted.get("quoted"))
        # ラッパーのあるワークスペースでは、文字として渡す道もラッパーで案内する。
        self.assertIn("ccnavi-git.sh commit -F <file>", quoted["response"])
        self.assertNotIn("(git commit -F", quoted["response"])

        bare = judge("Bash", "echo $(git push origin main)")
        self.assertNotIn("二重引用符の中", bare["response"])
        self.assertFalse(bare.get("quoted"), bare.get("quoted"))

    def test_縮退の断りは理由ごとに違う(self):
        responses = {}
        for subject, reason, phrase in [
            (
                'echo "git push',
                REASON_UNTERMINATED,
                "引用符かヒアドキュメントが閉じていない",
            ),
            (
                "echo $(git push",
                REASON_UNTERMINATED_SUBST,
                "$( ) が閉じていない",
            ),
            (
                "bash -c x; git push origin main",
                REASON_TAKEN_AS_CODE,
                "コードとして実行するものに渡している",
            ),
        ]:
            with self.subTest(subject=subject):
                body = judge("Bash", subject)
                self.assertEqual(body["degraded"], reason)
                self.assertIn(phrase, body["response"])
                responses[reason] = body["response"]
        self.assertEqual(len(set(responses.values())), 3, "理由の違う縮退に同じ文面を返した")

    def test_バッククォートは一律に止める(self):
        # 二重引用の中でも、区切りを引用しないヒアドキュメントの中でも実行される。
        code = "DENY_BACKQUOTE"
        self.check(
            [
                ("echo `find . -delete`", "deny", BACKQUOTE, code),
                ('grep -n "`rm -rf /tmp/x`" f', "deny", BACKQUOTE, code),
                ("cat <<EOF\nuse `rm -rf /tmp/x`\nEOF", "deny", BACKQUOTE, code),
                ('gh issue create --title t --body "use `git push` here"', "deny", BACKQUOTE, code),
                ('gh issue create --title t --body "odd ` backtick"', "deny", BACKQUOTE, code),
                ('echo "$(echo `id`)"', "deny", BACKQUOTE, code),
                # 文字として渡す表記は止まらない。
                ('grep -n "\\`git push\\`" f', "allow", "prefer-read-grep", ""),
                ("grep -n '`git push`' f", "allow", "prefer-read-grep", ""),
            ]
        )
        body = judge("Bash", 'gh issue create --title t --body "use `x` here"')
        self.assertIn("$( )", body["response"])
        self.assertIn("--body-file", body["response"])

    def test_実行するときに決まるコマンド名は一律に止める(self):
        # どれもどのルールにもヒットせず、auto では権限モードに渡っていた。
        code = "DENY_COMMAND_NAME_EXPANSION"
        self.check(
            [
                ("c=git; $c push origin main", "deny", NAME, code),
                ("$(echo git) push origin main", "deny", NAME, code),
                ("/usr/bin/gi? push origin main", "deny", NAME, code),
                ("git${IFS}push origin main", "deny", NAME, code),
                ("/bin/r? -rf /tmp/x", "deny", NAME, code),
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
                # 外側が縮退する `sh -c` と `eval` の文字列の中は、止めずに確認にする。
                ('sh -c "$c status"', "ask", "", "PARSE_UNCERTAIN"),
                ('eval "$(pyenv init -)"', "ask", "", "PARSE_UNCERTAIN"),
                ('"$(git rev-parse --show-toplevel)/x.sh"', "deny", NAME, code),
            ]
        )
        body = judge("Bash", "c=git; $c status")
        self.assertIn("`$c`", body["response"])
        # 書き直しの例に、止まる形（生の git）を使わない。
        self.assertIn("`c=ls; $c -la` は `ls -la`", body["response"])
        self.assertNotIn("`git status`", body["response"])

    def test_ブレース展開を止めた文面は書き直し方を言う(self):
        body = judge("Bash", "grep -rn x --exclude-dir={node_modules,.git} .")
        self.assertIn("`{node_modules,.git}`", body["response"])
        self.assertIn("--exclude-dir=a --exclude-dir=b", body["response"])
        # 読めなかったのではない。読めなかった断りを付けない。
        self.assertNotIn("PARSE_UNCERTAIN", body["response"])
        self.assertNotIn("生の文字列", body["response"])


if __name__ == "__main__":
    unittest.main()
