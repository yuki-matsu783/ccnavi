"""共通レイヤーと config が無いとき・不正なときの受入テスト。

ファイルが無いのは「設定が無い」正常で、ルール・リスクの配点・フェーズ定義のすべてで空として扱う。
記録の `fallback` にも `--lint` にも出さない。不正なときだけ `fallback` を残す。
組み込みの deny は、共通レイヤーと config の有無・状態によらず常にヒットする。

fixture は tests/config/test_config_union.py の ConfigUnionHarness を継ぐ。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import unittest

from ccnavi.infra import settings, shellread, yamlread
from ccnavi.policy import builtin, texts
from ccnavi.tickets import risk
from ccnavi.tickets.model import ticket_guard
from tests import ROOT
from tests.config.test_config_union import (
    BROKEN,
    COMMON_RISK,
    COMMON_RULES,
    LIB_RISK,
    LIB_RULES,
    ConfigUnionHarness,
    layer_path,
    write,
)

# 組み込みの既定は `Read` だけを通す（allow）。deny は取り返しの付かない操作。
RM_RF = "rm -rf /tmp/x"
READ = os.path.join("README.md")


class AbsentRulesTest(ConfigUnionHarness):
    def remove_common_rules(self):
        os.remove(self.rules)

    def remove_own_layer(self):
        for kind in ("rules", "phases", "risk"):
            path = layer_path(self.ws, kind)
            if os.path.exists(path):
                os.remove(path)

    def test_a_missing_common_layer_is_empty_and_is_not_a_fallback(self):
        """共通レイヤーが無くても、config のレイヤーを足して動く。`fallback` は残さない。"""
        self.remove_common_rules()

        denied = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "generated", "x.py"))

        self.assert_denied(denied, "self:generated")
        self.assertNotIn("fallback", self.last_record())
        self.assertNotIn("built-in defaults", self.reason(denied))

    def test_builtin_deny_stays_on_without_the_common_layer(self):
        """共通レイヤーが無くても、組み込みの deny（取り返しの付かない操作）はヒットする。"""
        self.remove_common_rules()

        for tool, field, value in (
            ("Bash", "command", RM_RF),
            ("Bash", "command", "git push origin main"),
            ("Bash", "command", "git reset --hard HEAD~1"),
            ("Write", "file_path", os.path.join(self.ws, ".env")),
        ):
            with self.subTest(value=value):
                denied = self.hook(tool, self.ws, **{field: value})
                self.assert_denied(denied)
                self.assertTrue(
                    any(name.startswith("builtin:") for name in self.last_record()["rules"]),
                    self.last_record(),
                )
                self.assertNotIn("fallback", self.last_record())

    def test_the_builtin_credentials_deny_has_a_leading_boundary(self):
        """土台の認証情報の止めは、実際の `.env`・`.ssh/` を止め、フィールド参照は巻き込まない。"""
        self.remove_common_rules()
        for tool, field, value in (
            ("Bash", "command", "cat .env"),
            ("Bash", "command", "cat .envrc"),
            ("Bash", "command", "cat x/.env.local"),
            ("Bash", "command", "cat ~/.ssh/id_rsa"),
            ("Read", "file_path", os.path.join(self.ws, ".env")),
            ("Write", "file_path", os.path.join(self.ws, "x", ".npmrc")),
        ):
            with self.subTest(value=value):
                self.assert_denied(
                    self.hook(tool, self.ws, **{field: value}), "builtin:credentials"
                )
        for command in ("jq -r '.hooks[0],.env.X' a.json", "echo process.env.HOME"):
            with self.subTest(command=command):
                self.hook("Bash", self.ws, command=command)
                self.assertNotIn("builtin:credentials", self.last_record().get("rules", []))

    def test_builtin_deny_stays_on_under_the_common_layer(self):
        """共通レイヤーが在っても、組み込みの deny は土台としてヒットする。"""
        denied = self.hook("Bash", self.ws, command="git reset --hard HEAD~1")

        self.assert_denied(denied, "builtin:git-reset-hard")

    def test_without_both_layers_the_builtin_defaults_decide(self):
        """共通レイヤーも config も無ければ、組み込みの既定だけで判定する。`fallback` は残さない。

        既定は `Read` を通す（allow）。deny と合わせて、組み込みの既定の全部。"""
        self.remove_common_rules()
        self.remove_own_layer()

        read = self.hook("Read", self.ws, file_path=os.path.join(self.ws, READ))
        self.assertEqual(self.last_record()["decision"], "allow", read.stdout)
        self.assertEqual(self.last_record()["rules"], ["builtin:read-anything"])
        self.assertNotIn("fallback", self.last_record())
        self.assert_denied(self.hook("Bash", self.ws, command=RM_RF), "builtin:recursive-delete")

    def test_the_read_allow_is_not_added_when_a_layer_exists(self):
        """`Read` を通す allow は組み込みの既定だけのとき限り。config が 1 本でも在れば足さない。"""
        self.remove_common_rules()
        write(layer_path(self.ws, "rules"), json.dumps({"version": 1, "deny": []}))

        self.hook("Read", self.ws, file_path=os.path.join(self.ws, READ))

        self.assertNotIn("builtin:read-anything", self.last_record().get("rules", []))

    def test_a_broken_common_layer_falls_back_to_the_builtin_defaults_only(self):
        """共通レイヤーのルールが不正なら、組み込みの既定だけで判定する。`fallback` を残す。"""
        write(self.rules, BROKEN)

        generated = self.hook(
            "Write", self.ws, file_path=os.path.join(self.ws, "generated", "x.py")
        )

        self.assertNotEqual(self.decision(generated), "deny", generated.stdout)
        self.assertEqual(self.last_record()["fallback"], "builtin:rules")
        self.hook("Read", self.ws, file_path=os.path.join(self.ws, READ))
        self.assertEqual(self.last_record()["decision"], "allow")
        self.assert_denied(self.hook("Bash", self.ws, command=RM_RF))

    def test_a_broken_layer_is_empty_and_is_named_in_the_fallback(self):
        """config のレイヤーが不正なら空。共通レイヤーは効き、`fallback` に名前を残す。"""
        write(layer_path(self.lib, "rules"), BROKEN)

        denied = self.hook("Write", self.lib, file_path=os.path.join(self.lib, ".env"))

        self.assert_denied(denied, "credentials")
        self.assertEqual(self.last_record()["fallback"], "lib")

    def test_lint_says_nothing_about_missing_files(self):
        """`--lint` は、共通レイヤーと config の 3 本が無いことを不備として言わない。"""
        self.remove_common_rules()
        os.remove(self.risk)
        self.remove_own_layer()
        for kind in ("rules", "phases", "risk"):
            os.remove(layer_path(self.lib, kind))

        errors = self.problems("error")
        warns = [
            p for p in self.problems("warn") if p["where"] in ("(rules)", "(phases)", "(risk)")
        ]

        self.assertEqual(errors, [])
        self.assertEqual(warns, [])

    def test_lint_still_says_a_broken_common_layer(self):
        """不正な共通レイヤーは error。"""
        write(self.rules, BROKEN)

        errors = self.problems("error", where="(rules)")

        self.assertTrue(errors)

    def test_explain_shows_a_missing_layer_as_not_placed(self):
        """`--explain` は、置いていないレイヤーを「置いていない（無い = 空）」と言う。"""
        self.remove_common_rules()

        out = self.ccnavi("--explain").stdout

        self.assertIn("このレイヤーの設定ファイルはありません", out)
        self.assertNotIn("読めない: ", out.split("■ rules ワークスペースのレイヤー")[0])


GIT = "git"
# 組み込みの deny が表記違いでも止める形と、止めない形。
BUILTIN_DENIED = (
    (f"{GIT} -C . push", "builtin:git-push"),
    (f"{GIT} --no-pager push", "builtin:git-push"),
    (f"{GIT} --git-dir=.git push", "builtin:git-push"),
    (f"{GIT} --git-dir .git push origin main", "builtin:git-push"),
    (f"{GIT} -c x=y reset --hard", "builtin:git-reset-hard"),
    (f"{GIT} reset -q --hard HEAD~1", "builtin:git-reset-hard"),
    ("rm -fr x", "builtin:recursive-delete"),
    ("rm -r -f x", "builtin:recursive-delete"),
    ("rm -f -r x", "builtin:recursive-delete"),
    ("rm --recursive --force x", "builtin:recursive-delete"),
    ("rm -Rfv x", "builtin:recursive-delete"),
    ("rm -r x -f", "builtin:recursive-delete"),
    # 後ろに別の rm を引数として挟んでも、最初の rm の選択肢として読む。
    ("rm -r rm -f", "builtin:recursive-delete"),
    ("ls; rm a rm -rf b", "builtin:recursive-delete"),
    # 値を取るオプションの値まで読んだ後のサブコマンド。
    (f"{GIT} -c push.default=current push", "builtin:git-push"),
    (f"{GIT} -C push push", "builtin:git-push"),
    (f"/usr/bin/{GIT} push", "builtin:git-push"),
    (f"{GIT}.exe push", "builtin:git-push"),
    (f"{GIT} push;", "builtin:git-push"),
    # サブコマンドを直接起こす形（パス付き・.exe 付き）。
    (f"/usr/lib/{GIT}-core/{GIT}-push origin HEAD", "builtin:git-push"),
    (f"{GIT}-push.exe origin", "builtin:git-push"),
    (f"/usr/lib/{GIT}-core/{GIT}-reset --hard", "builtin:git-reset-hard"),
    (f"{GIT}-reset.exe -q --hard HEAD", "builtin:git-reset-hard"),
    (f"{GIT} -C x reset --hard", "builtin:git-reset-hard"),
    (f"{GIT} status; {GIT} reset --hard", "builtin:git-reset-hard"),
    # 値が g・gi・git… で始まっても、値として最後まで読む。
    (f"{GIT} -C gitx push", "builtin:git-push"),
    (f"{GIT} -c remote.origin.url=github.com push", "builtin:git-push"),
    (f"{GIT} -C gitlab reset --hard", "builtin:git-reset-hard"),
    (f"{GIT} -C gi push", "builtin:git-push"),
    # `--` の後ろはオペランド。その前に揃っていればヒットする。
    ("rm -rf -- x", "builtin:recursive-delete"),
    ("rm -r -f -- x", "builtin:recursive-delete"),
    (f"{GIT} reset --hard -- x", "builtin:git-reset-hard"),
    # 空の値（読みでは空白が 2 字続く）の後ろのサブコマンド。
    (f"{GIT} -C '' push", "builtin:git-push"),
    (f'{GIT} -C "" reset --hard', "builtin:git-reset-hard"),
    (f"{GIT} --work-tree '' push", "builtin:git-push"),
    (f"{GIT} -C '' -c '' push", "builtin:git-push"),
    # `\` の無い `$'…'` は単一引用と同じに読む。
    (f"{GIT} $'push'", "builtin:git-push"),
    ("rm $'-rf' x", "builtin:recursive-delete"),
)
BUILTIN_PASSED = (
    "legit push",
    "rm -r x",
    "rm -f x",
    "rm -f file-r",
    f"{GIT} commit -m push",
    f"{GIT} reset --soft HEAD~1",
    # `-c` の値の中の push・reset はサブコマンドではない。
    f"{GIT} -c push.default=current status",
    f"{GIT} -C push status",
    f"{GIT} -c reset.x=1 status --hard",
    f"{GIT} pushx",
    f"{GIT}-pushx origin",
    # `--` の後ろはオペランドで、選択肢には読まない。
    "rm -f -- -r",
    "rm -r -- -f x",
    f"{GIT} reset -- --hard",
    f"{GIT} -c x=push status",
)

# 照合の後戻りが長引く形だった入力。どれもヒットしない。
SLOW_BEFORE = (
    "rm " * 8000,
    "git -c " * 8000,
    "rm -r a " * 3200,
    "git -C " * 8000,
    "rm -f a " * 3200,
    "git reset " * 2400,
    "/" * 24000,
    "a/" * 12000,
    "git --x/git " * 2000,
    # `.git` の 1 本の、前置きを読み直す形。
    "mv " + "a/" * 12000,
    "echo > " + "a/" * 12000,
    "cp " + "a/ " * 8000 + ".",
    # 読み切れなかった生の文字列。`>` ごとに尾を読み直さない（上限に頼らない）。
    'echo "' + ">a" * 12000,
    'echo "' + ">a/" * 8000,
    'echo "> ' + "a/" * 12000,
)
# 共通ルールの照合が長引く形だった入力（パスの前置きを読み直す形）。
COMMON_SLOW_BEFORE = (
    "/" * 30000,
    "echo " + "/" * 30000 + "; git push",
    "\x00a" * 15000,
    "\x00/" * 15000,
)
COMMON_LIMIT = 0.1
# 照合 1 本にかけてよい時間。手元では 0.01 秒ほどで、負荷で揺れても届かない余裕を持たせてある。
SLOW_LIMIT = 0.5
TICKET_CONTROL = "CCNAVI_TICKET_CONTROL"


class BuiltinSpeedTest(unittest.TestCase):
    """組み込みの deny の照合は、入力の長さにほぼ比例する時間で終わる。

    判定の期限はルールの合間でしか確かめないので、1 本の照合が後戻りで長引くと、hook の timeout に
    達して呼び出しが止められずに通る。
    """

    def test_common_regexes_return_quickly(self):
        """共通ルールの regex（組み合わせ条件と except の中も）も、長い入力ですぐ返る。"""
        with open(
            os.path.join(ROOT, ".ccnavi", "config", "common", "rules.yml"), encoding="utf-8"
        ) as f:
            data = yamlread.safe_load(f.read())
        found = []
        by_rule: dict[str, list[str]] = {}

        def walk(node, rule_id=""):
            if isinstance(node, dict):
                rule_id = node.get("id", rule_id) if isinstance(node.get("id"), str) else rule_id
                for key, value in node.items():
                    if key == "regex" and isinstance(value, str):
                        expression = value.replace("{root}", "/ws").replace("{!root}", "")
                        found.append(expression)
                        by_rule.setdefault(rule_id, []).append(expression)
                    else:
                        walk(value, rule_id)
            elif isinstance(node, list):
                for item in node:
                    walk(item, rule_id)

        walk(data)
        # 残しているルール（ccnavi 自身の保護と認証情報は組み込みにあり、ここには無い）の
        # regex が、検査の対象に入っていることを確かめる。入っていなければ速さを見たことにならない。
        for rule_id in ("workspace-root", "session-scratchpad", "find-writes", "heredoc"):
            self.assertIn(rule_id, by_rule)
        for text in (*COMMON_SLOW_BEFORE, *SLOW_BEFORE):
            for expression in found:
                regex = re.compile(expression, re.IGNORECASE)
                with self.subTest(text=text[:16], regex=expression[:24]):
                    start = time.monotonic()
                    regex.search(text)
                    self.assertLess(time.monotonic() - start, COMMON_LIMIT)

    def test_long_inputs_return_quickly(self):
        built, problems = builtin.load_base()
        self.assertEqual(problems, [])
        regexes = [rule.compiled for rule in built.deny if rule.match == "Bash"]
        regexes.append(re.compile(ticket_guard.RAW_GIT_REGEX, re.IGNORECASE))
        for text in SLOW_BEFORE:
            subject = shellread.read(text).text or text
            for regex in regexes:
                with self.subTest(text=text[:16], regex=regex.pattern[:24]):
                    start = time.monotonic()
                    regex.search(subject)
                    regex.search(text)
                    self.assertLess(time.monotonic() - start, SLOW_LIMIT)


RAW_GIT_BUILTIN = "builtin:guard-raw-git"


class BuiltinFormsTest(ConfigUnionHarness):
    """組み込みの deny（土台）は、glob の文字列一致で外れていた表記違いも止める。

    git のグローバルオプションを挟んだ形、rm の再帰と強制の組み合わせ・順序・長い形。
    片方だけの rm や、`git` を含む別の語は止めない。共通レイヤーが無いときと、壊れて組み込みの
    既定に戻ったときの両方で見る（hook と同じ経路）。
    """

    def check(self):
        for command, rule in BUILTIN_DENIED:
            with self.subTest(command=command):
                self.assert_denied(self.hook("Bash", self.ws, command=command), rule)
        for command in BUILTIN_PASSED:
            with self.subTest(command=command):
                result = self.hook("Bash", self.ws, command=command)
                # 生の git は組み込みで常に止まる。ほかの土台の deny にヒットしていないことを見る。
                if self.decision(result) == "deny":
                    self.assert_denied(result, RAW_GIT_BUILTIN)

    def test_without_the_common_layer(self):
        os.remove(self.rules)
        self.check()

    def test_with_a_broken_common_layer(self):
        write(self.rules, BROKEN)
        self.check()

    def test_next_step_names_the_git_wrapper(self):
        """次の一手に挙げる git の操作は、ラッパーがあればラッパーの実パスで書く。

        生の git は組み込みで常に止まるので、止まる形（`git`）は案内しない。ラッパーが無いときは
        git の操作をユーザに頼む文にする。
        """
        os.remove(self.rules)
        cases = (
            (f"{GIT} reset --hard", "builtin:git-reset-hard"),
            ("rm -rf x", "builtin:recursive-delete"),
        )
        for command, rule in cases:
            with self.subTest(command=command, wrapper=False):
                denied = self.hook("Bash", self.ws, command=command)
                self.assert_denied(denied, rule)
                self.assertNotIn("ccnavi-git.sh", self.reason(denied))
                self.assertNotIn("'git ", self.reason(denied))
        write(os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-git.sh"), "")
        wrapper = settings.script_command(self.ws, "ccnavi-git.sh")
        for command, rule in cases:
            with self.subTest(command=command, wrapper=True):
                denied = self.hook("Bash", self.ws, command=command)
                self.assert_denied(denied, rule)
                self.assertIn(wrapper, self.reason(denied))
                self.assertNotIn("'git ", self.reason(denied))


class BuiltinRawGitTest(ConfigUnionHarness):
    """生の git は組み込みで常に止める。ラッパーがあればラッパーへ誘導し、無ければユーザに頼む。

    共通レイヤーの有無、壊れて組み込みの既定に戻ったとき、チケット制御の可否、ラッパーの有無に依らない。
    """

    def setUp(self):
        super().setUp()
        self.script = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-git.sh")
        write(self.script, "exit 0\n")

    def assert_raw_git_denied(self, **options):
        result = self.hook("Bash", self.ws, command=f"{GIT} status", **options)
        self.assert_denied(result, RAW_GIT_BUILTIN)
        self.assertIn(".ccnavi/scripts/ccnavi-git.sh", self.reason(result))
        self.assertIn("<サブコマンド>", self.reason(result))
        # 誘導先のラッパーは止めない。
        wrapper = self.hook("Bash", self.ws, command=f"sh {self.script} status", **options)
        self.assertNotIn(RAW_GIT_BUILTIN, self.last_record().get("rules", []), wrapper.stdout)
        legit = self.hook("Bash", self.ws, command="legit push", **options)
        self.assertNotEqual(self.decision(legit), "deny", legit.stdout)

    def test_without_the_common_layer(self):
        os.remove(self.rules)
        self.assert_raw_git_denied()

    def test_with_a_broken_common_layer(self):
        """ルールファイルが読めない間も止める。直す道はラッパーの add / restore --ours が残す。"""
        write(self.rules, BROKEN)
        self.assert_raw_git_denied()
        self.assertEqual(self.last_record()["fallback"], "builtin:rules")

    def test_only_git_at_the_command_position(self):
        """`git` がコマンドの位置にある形だけを止める。引数の語の `git` は止めない。"""
        os.remove(self.rules)
        for command in (
            f"ls && {GIT} log",
            f"sudo {GIT} status",
            f"env A=1 {GIT} status",
            f"command {GIT} status",
            f"ls | xargs {GIT} status",
            f"nohup {GIT} status",
            f"time {GIT} status",
            f"/usr/bin/{GIT} status",
            f"{GIT}.exe status",
            f"echo $({GIT} status)",
            f"if true; then {GIT} status; fi",
            f"C:\\Git\\{GIT}.exe status",
            f'"C:\\Git\\{GIT}.exe" status',
        ):
            with self.subTest(command=command):
                self.assert_denied(self.hook("Bash", self.ws, command=command), RAW_GIT_BUILTIN)
        for command in (
            f"grep {GIT} README.md",
            f"echo hello {GIT} world",
            f"echo to do {GIT} stuff",
        ):
            with self.subTest(command=command):
                self.hook("Bash", self.ws, command=command)
                self.assertNotIn(RAW_GIT_BUILTIN, self.last_record().get("rules", []))

    def test_denied_when_ticket_control_is_disabled(self):
        os.remove(self.rules)
        result = self.hook(
            "Bash", self.ws, command=f"{GIT} status", env={TICKET_CONTROL: "disable"}
        )
        self.assert_denied(result, RAW_GIT_BUILTIN)

    def test_denied_without_the_wrapper_and_names_no_script(self):
        """ラッパーが無いときも止める。誘導先が無いので、スクリプトを名指しせず、ユーザに頼む文にする。"""
        os.remove(self.rules)
        os.remove(self.script)
        result = self.hook("Bash", self.ws, command=f"{GIT} status")
        self.assert_denied(result, RAW_GIT_BUILTIN)
        self.assertNotIn("ccnavi-git.sh", self.reason(result))
        self.assertIn("ユーザ", self.reason(result))


GIT_DIR_BUILTIN = builtin.GIT_DIR_RULE_ID
GIT_DIR_SHELL_BUILTIN = builtin.GIT_DIR_SHELL_RULE_ID
# `.git` の中へシェルから書き込む形と、読むだけの形。
GIT_DIR_SHELL_DENIED = (
    "echo x > .git/config",
    "echo x >> /abs/repo/.git/hooks/pre-commit",
    "cp /tmp/x .git/config",
    "cp /tmp/x .git",
    "cp -t .git/hooks /tmp/pre-commit",
    "mv /tmp/x .git/HEAD",
    "mv .git /tmp/old",
    "rm -f .git/index.lock",
    "tee .git/config < /tmp/x",
    "sed -i 's/a/b/' .git/config",
    "cp -r /tmp/x/.git .",
    "echo x > .GIT/config",
    "echo x > .git./config",
    "dd if=/tmp/x of=.git/HEAD",
    "tee .git/config < /tmp/x",
    "rm .git",
    "sed -i s/a/b/ /abs/.git/config",
    "sed -i s/a/b/ x .git/config",
    "sed -e s/a/b/ -i .git/config",
    "find .git -name '*.lock' -delete",
    "find .git -type f -exec rm {} +",
    "cd .git && find . -delete",
    "cd .git && echo x > config",
    "printf '.git/config\\n' | xargs rm",
)
GIT_DIR_SHELL_PASSED = (
    "cat .git/config",
    "cp .git/config /tmp/x",
    "sed -n 1p .git/config",
    "echo x > .gitignore",
    "echo x > .github/w.yml",
    "sed -i 's/old.git/new.git/' README.md",
    'echo "a > .git/x"',
    "echo x > repo.git/config",
    "cp ../foo/*.py .",
    "dd if=.git/index of=/tmp/x",
    "tee /tmp/o < .git/config",
    "patch -p1 < .git/x",
    "sed -i 's/foo/.git/' f",
    "sed -i 's/\\.git\\//x/' f",
    "find . -path ./.git -prune -o -type f -delete",
    "printf a | xargs rm",
    "ls .gitignore | xargs rm",
)


class BuiltinGitDirTest(ConfigUnionHarness):
    """`.git` の中への書き込みは、共通レイヤーと config の有無・状態によらず土台で止める。

    名指しのツール（Write / Edit / NotebookEdit）は `.git/` の下と `.git` そのもの、
    シェルは守る場所と同じ書き込みの形で見る。読むだけと、`.gitignore`・`.github/` は
    止めない。git のラッパーの呼び出しは、`.git` がコマンド文字列に出ないのでヒットしない。
    """

    def check(self):
        for tool in ("Write", "Edit", "NotebookEdit"):
            for path in (
                os.path.join(self.ws, ".git", "config"),
                os.path.join(self.ws, ".git", "hooks", "pre-commit"),
                os.path.join(self.ws, "wt", ".git"),
            ):
                with self.subTest(tool=tool, path=path):
                    field = "notebook_path" if tool == "NotebookEdit" else "file_path"
                    result = self.hook(tool, self.ws, **{field: path})
                    self.assert_denied(result, GIT_DIR_BUILTIN)
        for name in (".gitignore", os.path.join(".github", "w.yml"), "repo.git"):
            with self.subTest(path=name):
                self.hook("Write", self.ws, file_path=os.path.join(self.ws, name))
                self.assertNotIn(GIT_DIR_BUILTIN, self.last_record().get("rules", []))
        for command in GIT_DIR_SHELL_DENIED:
            with self.subTest(command=command):
                self.assert_denied(
                    self.hook("Bash", self.ws, command=command), GIT_DIR_SHELL_BUILTIN
                )
        wrapper = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-git.sh")
        for command in (
            *GIT_DIR_SHELL_PASSED,
            f"sh {wrapper} integrate x",
            f"sh {wrapper} -C {self.ws} status",
        ):
            with self.subTest(command=command):
                self.hook("Bash", self.ws, command=command)
                self.assertNotIn(GIT_DIR_SHELL_BUILTIN, self.last_record().get("rules", []))
        self.hook("Read", self.ws, file_path=os.path.join(self.ws, ".git", "config"))
        self.assertNotIn(GIT_DIR_BUILTIN, self.last_record().get("rules", []))

    def test_with_the_common_layer(self):
        self.check()

    def test_without_the_common_layer(self):
        os.remove(self.rules)
        self.check()

    def test_with_a_broken_common_layer(self):
        write(self.rules, BROKEN)
        self.check()

    def test_the_message_names_the_git_wrapper(self):
        os.remove(self.rules)
        path = os.path.join(self.ws, ".git", "config")
        denied = self.hook("Write", self.ws, file_path=path)
        self.assertNotIn("git <サブコマンド>", self.reason(denied))
        write(os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-git.sh"), "")
        wrapper = settings.script_command(self.ws, "ccnavi-git.sh")
        for tool, field, value in (
            ("Write", "file_path", path),
            ("Bash", "command", "echo x > .git/config"),
        ):
            with self.subTest(tool=tool):
                denied = self.hook(tool, self.ws, **{field: value})
                self.assertIn(wrapper, self.reason(denied))
                self.assertNotIn("'git ", self.reason(denied))


class BuiltinGitWordingTest(unittest.TestCase):
    """組み込みの文面は、止まる形（生の git）を案内しない。

    ラッパーがあるワークスペースではラッパーの実パスで、無ければ `git` で書く
    （ラッパーが無ければ、生の git を組み込みで止めることもない）。
    """

    IDS = (
        "builtin:recursive-delete",
        "builtin:git-reset-hard",
        "builtin:git-push",
        builtin.CONFIG_VIA_BASH_RULE_ID,
    )

    def messages(self, root):
        # 文は拒否に当たった回に文面ファイルから引く。同梱の文面ファイルを写して引く。
        shutil.copytree(
            os.path.join(ROOT, ".ccnavi", "messages"),
            os.path.join(root, ".ccnavi", "messages"),
        )
        found = {r.id: texts.spoken(r) for r in builtin.load_base(root)[0].deny}
        conf = settings.Settings(project_home=settings.DEFAULT_PROJECT_HOME)
        for rule in builtin.load(root, conf)[0].deny:
            found.setdefault(rule.id, texts.spoken(rule))
        return {i: found[i] for i in self.IDS}

    def test_with_the_wrapper(self):
        root = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-builtin:words-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        script = os.path.join(root, ".ccnavi", "scripts", "ccnavi-git.sh")
        os.makedirs(os.path.dirname(script))
        with open(script, "w", encoding="utf-8"):
            pass
        git = settings.script_command(root, "ccnavi-git.sh")
        for rule_id, text in self.messages(root).items():
            with self.subTest(rule=rule_id):
                self.assertIn(git, text)
                self.assertNotRegex(text, r"'git ")

    def test_push_with_the_wrapper_separates_the_parent_the_child_and_the_user(self):
        root = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-builtin:push-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        script = os.path.join(root, ".ccnavi", "scripts", "ccnavi-git.sh")
        os.makedirs(os.path.dirname(script))
        with open(script, "w", encoding="utf-8"):
            pass
        text = self.messages(root)["builtin:git-push"]
        self.assertIn("親のワークツリーのブランチ", text)
        self.assertIn("子のワークツリーのブランチは送らず", text)
        self.assertNotIn("push をユーザに頼む", text)

    def test_without_the_wrapper(self):
        root = tempfile.mkdtemp(prefix="ccnavi-builtin:words-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        said = self.messages(root)
        self.assertIn("ユーザに削除を頼む", said["builtin:recursive-delete"])
        self.assertIn("ユーザに確認する", said["builtin:git-reset-hard"])
        self.assertIn("push をユーザに頼む", said["builtin:git-push"])
        self.assertIn("ユーザに頼んでください", said[builtin.CONFIG_VIA_BASH_RULE_ID])
        # 生の git は止まるので、`git` の形は案内しない。ユーザに頼む文になる。


class AbsentRiskTest(ConfigUnionHarness):
    def definition(self, project="lib"):
        conf, _ = settings.load(self.ws)
        return risk.layer_definition(conf, self.ws, project)

    def test_without_both_files_the_builtin_items_are_used(self):
        """共通レイヤーにも config にも risks.yml が無ければ、組み込みの 4 項目。"""
        os.remove(self.risk)
        os.remove(layer_path(self.lib, "risk"))

        definition, problems = self.definition()

        self.assertEqual(
            [f.id for f in definition.factors], ["big-diff", "many-files", "ci", "deletes"]
        )
        self.assertEqual(definition.fallback, "")
        self.assertEqual(problems, [])

    def test_a_missing_common_layer_counts_only_the_config_items(self):
        """共通レイヤーの risks.yml だけが無ければ、共通レイヤーは空。config の項目だけを数える。"""
        os.remove(self.risk)

        definition, problems = self.definition()

        self.assertEqual([f.id for f in definition.factors], ["schema"])
        self.assertEqual(definition.levels, {"critical": 50})
        self.assertEqual(definition.fallback, "")
        self.assertEqual(problems, [])

    def test_a_missing_config_counts_only_the_common_items(self):
        """config の risks.yml だけが無ければ、共通レイヤーの項目だけを数える。"""
        os.remove(layer_path(self.lib, "risk"))

        definition, _ = self.definition()

        self.assertEqual([f.id for f in definition.factors], ["big-diff"])
        self.assertEqual(definition.fallback, "")

    def test_a_broken_config_is_empty_and_named(self):
        """不正な config のレイヤーは空。共通レイヤーの項目だけを数え、`fallback` に名前を残す。"""
        write(layer_path(self.lib, "risk"), "version: 1\nfactors: [\n")

        definition, _ = self.definition()

        self.assertEqual([f.id for f in definition.factors], ["big-diff"])
        self.assertEqual(definition.dropped, ["lib"])
        self.assertTrue(definition.fallback)

    def test_a_broken_common_layer_falls_back_to_the_builtin_items(self):
        """不正な共通レイヤーは組み込みの配点に戻り、レイヤーは足さない。"""
        write(self.risk, "version: 1\nfactors: [\n")

        definition, _ = self.definition()

        self.assertEqual(
            [f.id for f in definition.factors], ["big-diff", "many-files", "ci", "deletes"]
        )
        self.assertTrue(definition.fallback)


class MirrorIsNotReadInTheWorkspaceTest(ConfigUnionHarness):
    """ワークスペースの中では、プロジェクトのミラー（`.ccnavi/config/common/`）を共通レイヤーとして読まない。"""

    MIRROR_RULES = {
        "version": 1,
        "deny": [
            {
                "id": "mirror-only",
                "match": "Write|Edit",
                "glob": "*/mirror-target/*",
                "message": "from the mirror.",
            }
        ],
    }

    def place_mirror(self):
        mirror = os.path.join(self.lib, ".ccnavi", "config", "common")
        write(os.path.join(mirror, "rules.yml"), json.dumps(self.MIRROR_RULES))
        write(
            os.path.join(mirror, "risks.yml"),
            COMMON_RISK + "  - {id: m, points: 1, lines_over: 0}\n",
        )
        write(os.path.join(mirror, "phases.yml"), "version: 1\nphases: [\n")

    def test_the_mirror_does_not_decide_in_the_workspace(self):
        """ミラーのルールは、プロジェクトへの書き込みの判定に入らない（共通レイヤーは 1 本）。"""
        self.place_mirror()

        result = self.hook(
            "Write", self.lib, file_path=os.path.join(self.lib, "mirror-target", "x.py")
        )

        self.assertNotEqual(self.decision(result), "deny", result.stdout)
        self.assertNotIn("mirror-only", result.stdout)
        shown = json.loads(self.ccnavi("--explain", "--json").stdout)
        ids = [r["id"] for layer in shown["layers"] for r in layer["rules"]["deny"]]
        self.assertNotIn("mirror-only", ids)

    def test_the_mirror_risk_and_phases_are_not_read_either(self):
        """配点とフェーズ定義も同じ。不正なミラーの phases.yml があっても何も言わない。"""
        self.place_mirror()
        conf, _ = settings.load(self.ws)

        definition, _ = risk.layer_definition(conf, self.ws, "lib")

        self.assertEqual([f.id for f in definition.factors], ["big-diff", "schema"])
        paths = [p["detail"] for p in self.problems("error")]
        self.assertFalse(
            [d for d in paths if "mirror" in d or "lib/.ccnavi/config/common" in d], paths
        )


class StandaloneCloneTest(ConfigUnionHarness):
    """プロジェクトを単体で clone したとき。

    そのプロジェクトがルートになる。`.ccnavi/config/common/`（ミラー）が共通レイヤー、
    `.ccnavi/config/project/` が
    自身のレイヤーとして読まれ、ワークスペースの中と同じ「共通 + 1 レイヤー」の和で判定される。
    """

    def setUp(self):
        super().setUp()
        # lib を単体の clone に見立てる。ミラーは共通レイヤーのコピー。
        self.mirror = os.path.join(self.lib, ".ccnavi", "config", "common")
        write(os.path.join(self.mirror, "rules.yml"), json.dumps(COMMON_RULES))
        write(os.path.join(self.mirror, "risks.yml"), COMMON_RISK)
        self.ws = self.lib
        self.state = os.path.join(self.lib, "logs", "state")
        self.log = os.path.join(self.lib, "logs", "decisions.jsonl")

    def test_the_mirror_is_the_common_layer_and_the_config_is_the_own_layer(self):
        """ミラーのルールは裸の id、config のルールは `self:` の id でヒットする。"""
        env = self.hook("Write", self.lib, file_path=os.path.join(self.lib, ".env"))
        self.assert_denied(env, "credentials")
        self.assertEqual(self.hit_rules(), ["credentials"])
        self.assertEqual(self.last_record()["source"], "common")

        schema = self.hook("Write", self.lib, file_path=os.path.join(self.lib, "schema", "x.sql"))
        self.assert_denied(schema, "self:schema")
        self.assertEqual(self.hit_rules(), ["self:schema"])
        self.assertEqual(self.last_record()["source"], "self")
        self.assertEqual(LIB_RULES["deny"][0]["id"], "schema")

    def test_the_sum_is_the_common_layer_plus_one_layer(self):
        """Bash も同じ和（共通 + 自身のレイヤー）。プロジェクトのレイヤーは無い。"""
        denied = self.hook("Bash", self.lib, command="terraform apply")
        self.assert_denied(denied, "terraform")
        denied = self.hook("Bash", self.lib, command="psql -c x")
        self.assert_denied(denied, "self:raw-psql")

    def test_the_risk_is_the_common_layer_plus_the_own_layer(self):
        """配点も共通 + 自身のレイヤーの和。境目の点は小さいほう。"""
        conf, _ = settings.load(self.lib)

        definition, problems = risk.layer_definition(conf, self.lib, "")

        self.assertEqual([f.id for f in definition.factors], ["big-diff", "schema"])
        self.assertEqual(risk.effective_levels(definition.levels)["critical"], 50)
        self.assertEqual([p for p in problems if p.severity != "info"], [])
        self.assertIn("critical", LIB_RISK)

    def test_the_mirror_script_resolves_from_the_clone_root(self):
        """ミラーの `script:` は `.ccnavi/config/common/scripts/` を指し、その clone
        のルートから解く。"""
        write(
            os.path.join(self.mirror, "risks.yml"),
            COMMON_RISK
            + "  - {id: counted, points: 5, script: .ccnavi/config/common/scripts/count.sh, message: m}\n",  # noqa: E501
        )
        write(os.path.join(self.mirror, "scripts", "count.sh"), "printf 1\n")
        conf, _ = settings.load(self.lib)

        definition, _ = risk.layer_definition(conf, self.lib, "")
        counted = definition.factor("counted")

        self.assertEqual(counted.home, self.lib)
        self.assertEqual(risk.script_problems(definition), [])

    def test_the_phases_come_from_the_config_only(self):
        """フェーズ定義は config の 1 本（`.ccnavi/config/project/phases.yml`）。"""
        sums = {s["name"]: s for s in json.loads(self.ccnavi("--explain", "--json").stdout)["sums"]}

        self.assertEqual(list(sums), ["self"])
        self.assertEqual(
            [t["id"] for t in sums["self"]["phases"]["types"]], ["design", "build", "release"]
        )
        self.assertEqual(
            sums["self"]["phases"]["path"],
            os.path.join(self.lib, ".ccnavi", "config", "project", "phases.yml"),
        )

    def test_lint_reads_the_mirror_as_the_common_layer(self):
        """`--lint` もミラーを共通レイヤーとして読む。ミラーが不正なら error で名指しする。"""
        write(os.path.join(self.mirror, "rules.yml"), BROKEN)

        errors = self.problems("error", where="(rules)")

        self.assertTrue(errors)


class ExplainSumsTest(ConfigUnionHarness):
    """`--explain --json` の `sums[]`。拡張が統合結果を自分で組まずに済むための合成結果。"""

    def sums(self):
        body = json.loads(self.ccnavi("--explain", "--json").stdout)
        return {s["name"]: s for s in body["sums"]}

    def test_every_sum_is_the_common_layer_plus_one_layer(self):
        sums = self.sums()

        self.assertEqual(sorted(sums), ["app", "lib", "self"])
        self.assertEqual(sums["lib"]["layers"], ["common", "lib"])
        for name, expected in (
            (
                "self",
                ["credentials", "terraform", "guard-approved", "self:generated", "self:dropdb"],
            ),
            ("lib", ["credentials", "terraform", "guard-approved", "lib:schema", "lib:raw-psql"]),
            ("app", ["credentials", "terraform", "guard-approved"]),
        ):
            with self.subTest(sum=name):
                self.assertEqual([r["id"] for r in sums[name]["rules"]["deny"]], expected)

    def test_a_sum_keeps_what_the_layers_overview_drops(self):
        """`layers[]` は重ねの途中経過なので、先のレイヤーと全欄が同じ定義は後ろから外れる。
        `sums[]` は「共通 + そのレイヤー」だけで重ねるので、外れない。"""
        same = {"version": 1, "deny": [COMMON_RULES["deny"][0]]}
        write(layer_path(self.ws, "rules"), json.dumps(same))
        write(layer_path(self.lib, "rules"), json.dumps(same))
        body = json.loads(self.ccnavi("--explain", "--json").stdout)
        sums = {s["name"]: s for s in body["sums"]}

        # どちらの和も共通レイヤーの定義だけ（全欄一致の重複は後ろを破棄する）。
        self.assertEqual(
            [r["id"] for r in sums["lib"]["rules"]["deny"]],
            [r["id"] for r in COMMON_RULES["deny"]],
        )
        # lib のレイヤー自身の宣言は、他のレイヤーの定義に引かれない。
        self.assertEqual(
            [r["id"] for r in sums["lib"]["rules"]["deny"]][:1],
            [r["id"] for r in sums["self"]["rules"]["deny"]][:1],
        )

    def test_the_risk_sum_carries_the_effective_levels_and_factors(self):
        sums = self.sums()

        self.assertEqual(sums["lib"]["risk"]["levels"], {"medium": 20, "high": 40, "critical": 50})
        self.assertEqual([f["id"] for f in sums["lib"]["risk"]["factors"]], ["big-diff", "schema"])
        self.assertEqual([f["source"] for f in sums["lib"]["risk"]["factors"]], ["common", "lib"])
        self.assertEqual([f["id"] for f in sums["app"]["risk"]["factors"]], ["big-diff"])

    def test_the_phases_are_the_one_file_that_the_layer_names(self):
        sums = self.sums()

        self.assertEqual([t["id"] for t in sums["self"]["phases"]["types"]], ["docs"])
        self.assertEqual(
            [t["id"] for t in sums["lib"]["phases"]["types"]], ["design", "build", "release"]
        )
        self.assertEqual(sums["app"]["phases"]["types"], [])
        self.assertEqual(sums["app"]["phases"]["unreadable"], "")
        # 定義は順序を持たない。先行関係は計画のステップの `after` から読むので、
        # 先行関係の欄は渡さない。
        self.assertNotIn("order", sums["lib"]["phases"])

    def test_a_missing_or_broken_layer_is_said_in_its_own_sum(self):
        write(layer_path(self.lib, "rules"), BROKEN)

        sums = self.sums()

        self.assertTrue(sums["lib"]["rules"]["unreadable"])
        self.assertEqual(
            [r["id"] for r in sums["lib"]["rules"]["deny"]],
            [r["id"] for r in COMMON_RULES["deny"]],
        )
        self.assertTrue(sums["app"]["rules"]["missing"])
        self.assertFalse(sums["self"]["rules"]["missing"])


if __name__ == "__main__":
    unittest.main()
