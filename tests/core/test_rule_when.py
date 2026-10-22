"""ルールの `when:`（組み合わせ条件）のテスト。

読み込み（書式・苦情・上限）、判定（all / any / not の組み合わせ、except との併用、
`{!root}` を組み立てられないとき、hook の判定と入れ子のコマンド）、lint、レイヤーのマージ、
提案が書き戻す形、`--explain` と期待する判定（`--test-expectations`）を見る。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import yaml

from ccnavi.entry import diagnose_shared, suggest
from ccnavi.policy import rule_lint, ruleload, rules
from tests import ROOT, common_path
from tests.inproc import run_ccnavi

# 「*.crt か、vault/ の下で .md でないもの」。組み込みの認証情報の土台（builtin:credentials）に
# 当たらない名前にしてある（土台は設定で切れず、当たると when の検査が土台の deny に紛れる）。
VAULT = {
    "id": "vault",
    "match": "Write|Edit",
    "when": {
        "any": [
            {"glob": "*.crt"},
            {"all": [{"glob": "*/vault/*"}, {"not": {"glob": "*.md"}}]},
        ]
    },
    "message": "鍵は書かない",
}


def build(raw: dict, section: str = rules.DENY, root: str = "/r") -> tuple:
    """ルール 1 件を読み込む。返すのは (ルール or None, 苦情のリスト)。"""
    rule_set, problems = rules.parse({"version": 1, section: [raw]}, root)
    found = rule_set.section(section)
    return (found[0] if found else None), problems


def errors(problems: list) -> list[str]:
    return [p.detail for p in problems if p.severity == rules.SEVERITY_ERROR]


def nested(depth: int) -> dict:
    """`not` を depth 段重ねた組み合わせ条件。個別条件を含めた段数は depth + 1。"""
    node: dict = {"glob": "*.crt"}
    for _ in range(depth):
        node = {"not": node}
    return node


class ParseTest(unittest.TestCase):
    def test_組み合わせ条件を読む(self):
        rule, problems = build(VAULT)
        self.assertEqual(problems, [])
        self.assertEqual(rule.glob, "")
        self.assertEqual(rule.regex, "")
        self.assertIsNone(rule.compiled)
        self.assertEqual(rule.when.written(), VAULT["when"])
        self.assertEqual(
            rule.written_body(),
            "when: any(glob '*.crt', all(glob '*/vault/*', not(glob '*.md')))",
        )
        leaves = [n.where for n in rule.when.walk() if n.is_leaf()]
        self.assertEqual(
            leaves, ["when.any[0].glob", "when.any[1].all[0].glob", "when.any[1].all[1].not.glob"]
        )

    def test_個別条件だけの組み合わせ条件(self):
        rule, problems = build(
            {"id": "x", "match": "Write", "when": {"regex": r"\.crt$"}}, section=rules.ALLOW
        )
        self.assertEqual(errors(problems), [])
        self.assertTrue(rule.matches("Write", "/a/K.CRT"))
        self.assertFalse(rule.matches("Write", "/a/k.crt.txt"))

    def test_root_を置き換える(self):
        rule, problems = build(
            {"id": "x", "match": "Write", "when": {"all": [{"glob": "{root}/*"}]}, "message": "m"},
            root="/work",
        )
        self.assertEqual(problems, [])
        self.assertTrue(rule.matches("Write", "/work/a"))
        self.assertTrue(rule.matches("Write", r"\work\a"))
        self.assertFalse(rule.matches("Write", "/other/a"))

    def test_not_root_を展開する(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.realpath(d)
            rule, problems = build(
                {
                    "id": "x",
                    "match": "Write",
                    "when": {"all": [{"regex": "^{!root}.*"}, {"not": {"glob": "*/tmp/*"}}]},
                    "message": "m",
                },
                root=root,
            )
            self.assertEqual(problems, [])
            self.assertTrue(rule.matches("Write", "/elsewhere/a"))
            self.assertFalse(rule.matches("Write", "/elsewhere/tmp/a"))
            self.assertFalse(rule.matches("Write", os.path.join(root, "a")))


class ErrorTest(unittest.TestCase):
    def check(self, when: object, *words: str, root: str = "/r", **extra) -> None:
        rule, problems = build(
            {"id": "x", "match": "Write", "when": when, "message": "m", **extra}, root=root
        )
        self.assertIsNone(rule, problems)
        found = errors(problems)
        self.assertEqual(len(found), 1, problems)
        for word in words:
            self.assertIn(word, found[0])
        self.assertTrue(all(p.rule == "deny:x" for p in problems))

    def test_本体はちょうど_1_つ(self):
        self.check({"glob": "*"}, "glob と when の両方", glob="*")
        self.check({"glob": "*"}, "regex と when の両方", regex="x")
        self.check({"glob": "*"}, "glob と regex と when", glob="*", regex="x")
        # 併記はキーの有無で見る。空や偽の値を添えても、どちらで判定するのか決まらない
        self.check({"glob": "*"}, "glob と when の両方", glob="")
        self.check({"glob": "*"}, "glob と when の両方", glob=0)
        self.check({"glob": "*"}, "regex と when の両方", regex=None)
        rule, problems = build({"id": "x", "match": "Write", "message": "m"})
        self.assertIsNone(rule)
        self.assertIn("glob も regex も when もありません。", errors(problems)[0])

    def test_ノードの形(self):
        self.check(None, "when", "マッピングではない")
        self.check("*.crt", "when", "マッピングではない")
        self.check(["*.crt"], "マッピングではない")
        self.check({}, "when", "キーがありません")
        self.check({"glob": "*", "regex": "x"}, "キーが 2 つ")
        self.check({"glob": "*", "why": "x"}, "未対応のキー", "why")
        self.check({"none": [{"glob": "*"}]}, "未対応のキー", "none")
        self.check({"all": [{"glob": "*"}, {"match": "Write"}]}, "when.all[1]", "未対応のキー")

    def test_型と空(self):
        self.check({"all": []}, "when の all が空")
        self.check({"any": []}, "when の any が空")
        self.check({"all": {"glob": "*"}}, "when の all", "リストではない")
        self.check({"any": "*.crt"}, "リストではない")
        self.check({"not": [{"glob": "*"}]}, "when.not", "マッピングではない")
        self.check({"not": None}, "when.not", "マッピングではない")
        self.check({"glob": ""}, "when の glob", "空か、文字列ではない")
        self.check({"regex": 3}, "when の regex", "空か、文字列ではない")
        self.check({"all": [{"glob": None}]}, "when.all[0] の glob")

    def test_上限(self):
        # 深さは個別条件を含めて 8 段まで
        rule, problems = build({"id": "x", "match": "Write", "when": nested(7), "message": "m"})
        self.assertEqual(errors(problems), [])
        self.assertIsNotNone(rule)
        self.check(nested(8), "8 段を超える")
        # ノードは 64 個まで（any の 1 個 + 個別条件 63 個 = 64）
        leaves = [{"glob": f"*.{i}"} for i in range(rules.MAX_WHEN_NODES - 1)]
        rule, problems = build(
            {"id": "x", "match": "Write", "when": {"any": leaves}, "message": "m"}
        )
        self.assertEqual(errors(problems), [])
        self.assertIsNotNone(rule)
        self.check({"any": [*leaves, {"glob": "*.x"}]}, "64 個を超える")

    def test_個別条件には本体と同じ制約(self):
        self.check({"regex": "a(?=b)"}, "when.regex の ", "先読み")
        self.check({"all": [{"regex": r"(a)\1"}]}, "when.all[0].regex の ", "後方参照")
        self.check({"not": {"regex": "a("}}, "when.not.regex の ", "正規表現として組み立てられない")
        self.check({"glob": "{root}/x"}, "{root}", root="")
        self.check({"glob": "{!root}/x"}, "when.glob の ", "`glob` には書けない")
        self.check({"regex": "x{!root}"}, "^")

    def test_except_の誤りもルールごと捨てる(self):
        self.check({"glob": "*"}, "except", "空", **{"except": []})


class EvaluateTest(unittest.TestCase):
    def test_any_all_not(self):
        rule, _ = build(VAULT)
        cases = {
            "/a/k.crt": True,
            "/a/vault/token": True,
            "/a/vault/README.MD": False,
            "/a/vault/k.crt": True,  # .md でなく、.crt でもある
            "/a/public/token": False,
            "/a/vault.md": False,
        }
        for subject, want in cases.items():
            with self.subTest(subject=subject):
                self.assertEqual(rule.matches("Write", subject), want)
        self.assertFalse(rule.matches("Read", "/a/k.crt"))  # match の見方は変わらない

    def test_not_の入れ子(self):
        rule, _ = build({"id": "x", "match": "Write", "when": nested(2), "message": "m"})
        self.assertTrue(rule.matches("Write", "/a/k.crt"))
        rule, _ = build({"id": "x", "match": "Write", "when": nested(3), "message": "m"})
        self.assertFalse(rule.matches("Write", "/a/k.crt"))
        self.assertTrue(rule.matches("Write", "/a/k.txt"))

    def test_all_は全部(self):
        rule, _ = build(
            {
                "id": "x",
                "match": "Bash",
                "when": {"all": [{"glob": "*git *"}, {"glob": "*--force*"}, {"glob": "*push*"}]},
                "message": "m",
            }
        )
        self.assertTrue(rule.matches("Bash", "git push --force"))
        self.assertFalse(rule.matches("Bash", "git push"))
        self.assertFalse(rule.matches("Bash", "git fetch --force"))

    def test_except_との併用(self):
        rule, problems = build({**VAULT, "except": [{"glob": "*/fixtures/*"}]})
        self.assertEqual(problems, [])
        self.assertTrue(rule.matches("Write", "/a/k.crt"))
        self.assertFalse(rule.matches("Write", "/a/fixtures/k.crt"))
        self.assertFalse(rule.matches("Write", "/a/fixtures/vault/token"))
        self.assertEqual(len(rule.excepts), 1)


class NotRootRefusedTest(unittest.TestCase):
    """個別条件の `{!root}` を組み立てられないとき。deny / ask は全部止め、allow は使わない。"""

    LONG_ROOT = "/" + "a" * rules.MAX_ROOT_LEN

    def raw(self, when: dict, **extra) -> dict:
        return {"id": "x", "match": "Write", "when": when, **extra}

    def test_deny_と_ask_は全部止める(self):
        for section in (rules.DENY, rules.ASK):
            for when in (
                {"all": [{"regex": "^{!root}.*"}, {"glob": "*.py"}]},
                # not の下の個別条件でも、外すだけの形にはしない
                {"all": [{"glob": "*.py"}, {"not": {"regex": "^{!root}.*"}}]},
            ):
                with self.subTest(section=section, when=when):
                    rule, problems = build(
                        self.raw(when, message="m", **{"except": [{"glob": "*.md"}]}),
                        section=section,
                        root=self.LONG_ROOT,
                    )
                    self.assertIsNotNone(rule)
                    self.assertTrue(any("when." in d and "長すぎ" in d for d in errors(problems)))
                    self.assertTrue(rule.matches("Write", "/anything/a.txt"))
                    # 全部止める形に except の穴を空けない
                    self.assertEqual(rule.excepts, [])
                    self.assertTrue(rule.matches("Write", "/anything/a.md"))
                    self.assertIn("止めています", rule.spoken_message())
                    # 書いた組み合わせ条件は残す（--explain と書き戻しのため）
                    self.assertEqual(rule.when.written(), when)

    def test_allow_は使わない(self):
        rule, problems = build(
            self.raw({"any": [{"glob": "*.py"}, {"regex": "^{!root}.*"}]}),
            section=rules.ALLOW,
            root=self.LONG_ROOT,
        )
        self.assertIsNone(rule)
        self.assertTrue(errors(problems))


class JudgeTest(unittest.TestCase):
    """hook の判定で when が効く。Bash はコマンド列の全体に当て、入れ子のコマンドにも当たる。"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.dir.name)
        self.addCleanup(self.dir.cleanup)
        path = common_path(self.root, "rules")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "deny": [
                        VAULT,
                        {
                            "id": "publish",
                            "match": "Bash",
                            "when": {
                                "all": [
                                    {"regex": r"(^|\x00)npm publish\b"},
                                    {"not": {"regex": r"--dry-run\b"}},
                                ]
                            },
                            "message": "公開しない",
                        },
                    ],
                    "allow": [{"id": "all", "match": "Read", "glob": "*"}],
                },
                f,
            )

    def judge(self, tool: str, tool_input: dict) -> dict:
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool,
                "cwd": self.root,
                "tool_input": tool_input,
            }
        )
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        done = run_ccnavi(
            [
                "--root",
                self.root,
                "--mode",
                "enable",
                "--guard-core-files",
                "disable",
            ],
            input=payload,
            cwd=ROOT,
            env=environment,
        )
        if not done.stdout.strip():
            return {}
        return json.loads(done.stdout)["hookSpecificOutput"]

    def says(self, command: str) -> tuple[str, bool]:
        out = self.judge("Bash", {"command": command})
        reason = out.get("permissionDecisionReason", "")
        return out.get("permissionDecision", ""), (
            "(rule: .ccnavi/config/common/rules.yml:publish)" in reason
        )

    def test_パス(self):
        decision = self.judge("Write", {"file_path": os.path.join(self.root, "k.crt")})
        self.assertEqual(decision.get("permissionDecision"), "deny")
        md = os.path.join(self.root, "vault", "a.md")
        self.assertNotEqual(
            self.judge("Write", {"file_path": md}).get("permissionDecision"), "deny"
        )

    def test_コマンドと入れ子のコマンド(self):
        self.assertEqual(self.says("npm publish"), ("deny", True))
        self.assertEqual(self.says("npm publish --dry-run"), ("ask", False))
        self.assertEqual(self.says("sudo npm publish"), ("deny", True))
        self.assertEqual(self.says("sudo npm publish --dry-run"), ("ask", False))
        # コマンド列の全体に当てる（lint が warn で言う形）
        self.assertEqual(self.says("npm publish; echo --dry-run"), ("ask", False))


class LintTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.dir.name)
        self.addCleanup(self.dir.cleanup)

    def lint(self, deny: list, allow: list | None = None) -> list:
        path = os.path.join(self.root, "rules.yml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                {
                    "version": 1,
                    "deny": deny,
                    "allow": allow or [{"id": "r", "match": "Read", "glob": "*"}],
                },
                f,
                allow_unicode=True,
            )
        return rule_lint.file_problems(path, self.root)

    def warns(self, problems: list) -> list[str]:
        return [f"{p.rule} {p.detail}" for p in problems if p.severity == rules.SEVERITY_WARN]

    def deny(self, when: dict, match: str = "Write") -> dict:
        return {"id": "x", "match": match, "when": when, "message": "m"}

    def test_正しい形は何も言わない(self):
        problems = self.lint([VAULT])
        self.assertEqual([p for p in problems if p.severity != rules.SEVERITY_INFO], [])

    def test_書式の誤りは_error(self):
        problems = self.lint([self.deny({"all": []})])
        found = [f"{p.rule} {p.detail}" for p in problems if p.severity == rules.SEVERITY_ERROR]
        self.assertTrue(any("deny:x" in d and "when の all が空" in d for d in found), found)

    def test_not_の下が何にでも当たる(self):
        found = self.warns(
            self.lint([self.deny({"all": [{"glob": "*.py"}, {"not": {"glob": "*"}}]})])
        )
        self.assertTrue(
            any("when の all は何にも当たらない。このルールは" in d for d in found), found
        )

    def test_any_の中に何にでも当たる個別条件(self):
        found = self.warns(self.lint([self.deny({"any": [{"glob": "*.py"}, {"regex": "^"}]})]))
        self.assertTrue(any("when の any は何にでも当たる" in d for d in found), found)

    def test_中のノードは条件を絞っていないと言う(self):
        when = {"all": [{"glob": "*.py"}, {"any": [{"glob": "*.md"}, {"glob": "*"}]}]}
        found = self.warns(self.lint([self.deny(when)]))
        self.assertTrue(
            any("when.all[1] の any は何にでも当たる。条件を" in d for d in found), found
        )
        when = {"any": [{"glob": "*.py"}, {"not": {"regex": ".*"}}]}
        found = self.warns(self.lint([self.deny(when)]))
        self.assertTrue(any("when.any[1] の not は何にも当たらない" in d for d in found), found)

    def test_同じ個別条件が肯定と否定で並ぶと打ち消し合うと言う(self):
        when = {"all": [{"glob": "*.py"}, {"not": {"glob": "*.py"}}]}
        found = self.warns(self.lint([self.deny(when)]))
        self.assertTrue(
            any("when の all に、同じ個別条件が肯定と否定で並んでいる" in d for d in found), found
        )
        self.assertTrue(any("この all は何にも当たらない" in d for d in found), found)
        # any なら何にでも当たる。同じ演算子の入れ子は開いて見る
        when = {"any": [{"glob": "*.md"}, {"any": [{"regex": "^a"}, {"not": {"regex": "^a"}}]}]}
        found = self.warns(self.lint([self.deny(when)]))
        self.assertTrue(any("when の any に、同じ個別条件が" in d for d in found), found)
        # 別々の個別条件なら言わない
        when = {"all": [{"glob": "*.py"}, {"not": {"glob": "*.md"}}]}
        found = self.warns(self.lint([self.deny(when)]))
        self.assertFalse(any("打ち消し合う" in d for d in found), found)

    def test_コマンドの_not(self):
        when = {"all": [{"glob": "*git push*"}, {"not": {"glob": "*--dry-run*"}}]}
        found = self.warns(self.lint([self.deny(when, "Bash|Write")]))
        self.assertTrue(any("Bash の when の not" in d for d in found), found)
        found = self.warns(self.lint([self.deny(when, "PowerShell")]))
        self.assertTrue(any("PowerShell の when の not" in d for d in found), found)
        # not が無ければ言わない
        found = self.warns(self.lint([self.deny({"any": [{"glob": "*git push*"}]}, "Bash")]))
        self.assertFalse(any("when の not" in d for d in found), found)
        # allow は外れても止める側に寄るだけなので言わない
        allow = {"id": "a", "match": "Bash", "when": when}
        found = self.warns(self.lint([VAULT], [allow]))
        self.assertFalse(any("when の not" in d for d in found), found)

    def test_stop_に当たらない組み合わせ条件(self):
        stop = {
            "id": "nudge",
            "match": "Stop",
            "when": {"not": {"glob": "(stop)"}},
            "every": 5,
            "additionalContext": "x",
        }
        found = self.warns(self.lint([VAULT], [stop]))
        self.assertTrue(any("(stop)" in d and "when" in d for d in found), found)
        ok = {**stop, "when": {"any": [{"glob": "*"}]}}
        found = self.warns(self.lint([VAULT], [ok]))
        self.assertFalse(any("(stop)" in d and "当たらないので" in d for d in found), found)

    def test_広い_allow_は組み合わせ条件の書き方から見積もる(self):
        # `not: {regex: '^rm'}` は "x" に当たるが、何にでも当たるわけではない
        narrow = {
            "id": "not-rm",
            "match": "Bash",
            "when": {"not": {"regex": "^rm"}},
            "additionalContext": "x",
        }
        broad = {
            "id": "all",
            "match": "Bash",
            "when": {"any": [{"glob": "*.py"}, {"glob": "*"}]},
            "additionalContext": "x",
        }
        found = self.warns(self.lint([VAULT], [narrow, broad]))
        self.assertFalse(any(d.startswith("not-rm ") and "広い allow" in d for d in found), found)
        self.assertTrue(
            any(d.startswith("all 広い allow") and "何にでもヒットする" in d for d in found), found
        )

    def test_何にでも当たる単体の_deny(self):
        def deny(rid: str, **body) -> dict:
            return {"id": rid, "match": "Bash", "message": "m", **body}

        found = self.warns(
            self.lint(
                [
                    deny("dot-star", regex=".*"),
                    deny("caret", regex="^"),
                    deny("stars", glob="**"),
                    deny("tool", glob="*"),
                    deny("narrow", glob="*git push*"),
                ]
            )
        )
        said = [d.split(" ")[0] for d in found if "本体の" in d and "何にでも当たる" in d]
        self.assertEqual(sorted(said), ["caret", "dot-star", "stars"], found)
        # ask と allow には言わない
        found = self.warns(self.lint([VAULT], [{"id": "any", "match": "Bash", "regex": ".*"}]))
        self.assertFalse(any("本体の" in d for d in found), found)

    def test_空文字以外のすべてに当たる本体も言う(self):
        # 判定は空の対象を見ない（Rule.matches）ので、`.` は何にでも当たるのと同じ
        found = self.warns(
            self.lint([{"id": "dot", "match": "Bash", "regex": ".", "message": "m"}])
        )
        self.assertTrue(
            any(d.startswith("dot ") and "本体の regex は何にでも当たる" in d for d in found),
            found,
        )
        found = self.warns(self.lint([{"id": "x", "match": "Bash", "regex": "x", "message": "m"}]))
        self.assertFalse(any("本体の" in d for d in found), found)

    def test_記号だけに当たる本体は何にでも当たると言わない(self):
        # 英字を含む文字列には当たらないので、何にでも当たるわけではない
        for written in ({"regex": "[^\\w./ -]"}, {"regex": "\\W"}, {"glob": "*[!a-z]*"}):
            rule = {"id": "sym", "match": "Bash", "message": "m", **written}
            found = self.warns(self.lint([rule]))
            self.assertFalse(
                any(d.startswith("sym ") and "本体の" in d for d in found), (written, found)
            )

    def test_except_を持つ_deny_には丸ごと止めると言わない(self):
        found = self.warns(
            self.lint(
                [
                    {
                        "id": "all-but",
                        "match": "Write",
                        "regex": ".*",
                        "except": [{"glob": "*/wip/*"}],
                        "message": "m",
                    }
                ]
            )
        )
        said = [d for d in found if d.startswith("all-but ") and "本体の" in d]
        self.assertTrue(said, found)
        self.assertFalse(any("丸ごと止める" in d for d in said), said)
        self.assertTrue(any("except" in d for d in said), said)

    def test_共通の_rules_yml_の_no_powershell_は言わない(self):
        problems = rule_lint.file_problems(common_path(ROOT, "rules"), ROOT)
        found = self.warns(problems)
        self.assertFalse(any("本体の" in d and "何にでも当たる" in d for d in found), found)

    def test_id_の無いルールは_error_で読まない(self):
        raw = {k: v for k, v in VAULT.items() if k != "id"}
        problems = self.lint([raw])
        errors = [p.detail for p in problems if p.severity == rules.SEVERITY_ERROR]
        self.assertTrue(any("id がありません" in d for d in errors), errors)


class MergeTest(unittest.TestCase):
    def rule_set(self, raw: dict) -> rules.RuleSet:
        rule_set, problems = rules.parse({"version": 1, "deny": [raw]}, "/r")
        self.assertEqual(errors(problems), [])
        return rule_set

    def test_when_はマージで落ちない(self):
        base = self.rule_set({"id": "a", "match": "Write", "glob": "*", "message": "m"})
        extra = self.rule_set({**VAULT, "id": "b"})
        ruleload.prefix_ids(extra, "lib")
        ruleload.merge_rules(base, extra, "lib")
        merged = base.deny[1]
        self.assertEqual(merged.id, "lib:b")
        self.assertEqual(merged.when.written(), VAULT["when"])
        self.assertTrue(merged.matches("Write", "/a/k.crt"))
        self.assertFalse(merged.matches("Write", "/a/vault/a.md"))

    def test_組み合わせ条件だけが違えば別の定義(self):
        base = self.rule_set(VAULT)
        other = {**VAULT, "when": {"any": [{"glob": "*.crt"}, {"glob": "*/vault/*"}]}}
        problems = ruleload.merge_rules(base, self.rule_set(other), "lib")
        self.assertEqual(len(base.deny), 2)
        self.assertEqual([p.severity for p in problems], [rules.SEVERITY_WARN])
        # 組み方だけが違う（all と any）2 本も別
        swapped = {**VAULT, "when": {"all": [{"glob": "*.crt"}, {"glob": "*/vault/*"}]}}
        problems = ruleload.merge_rules(base, self.rule_set(swapped), "lib")
        self.assertEqual(len(base.deny), 3)
        # 全欄が同じなら後ろを捨てる
        problems = ruleload.merge_rules(base, self.rule_set(VAULT), "lib")
        self.assertEqual(len(base.deny), 3)
        self.assertEqual([p.severity for p in problems], [rules.SEVERITY_INFO])

    def test_glob_と同じ個別条件だけの組み合わせ条件も別の定義(self):
        base = self.rule_set({"id": "a", "match": "Write", "glob": "*.crt", "message": "m"})
        tree = self.rule_set(
            {"id": "a", "match": "Write", "when": {"glob": "*.crt"}, "message": "m"}
        )
        ruleload.merge_rules(base, tree, "lib")
        self.assertEqual(len(base.deny), 2)


class WrittenTest(unittest.TestCase):
    def test_提案が書き戻す形に_when_が残る(self):
        raw = {**VAULT, "except": [{"glob": "*/fixtures/*"}]}
        rule, _ = build(raw)
        written = suggest._written_rule(rule)
        self.assertEqual(written["when"], VAULT["when"])
        self.assertEqual(written["except"], raw["except"])
        self.assertNotIn("glob", written)
        self.assertNotIn("regex", written)
        again, problems = build(written)
        self.assertEqual(errors(problems), [])
        self.assertEqual(again.key(), rule.key())

    def test_rule_form(self):
        rule, _ = build(VAULT)
        form = diagnose_shared.rule_form(rule)
        self.assertEqual(form["kind"], "when")
        self.assertEqual(form["when"], VAULT["when"])
        # kind が when を言うので、written に `when: ` は重ねない（画面の「when when:」を避ける）
        self.assertEqual(form["written"], rule.when.describe())
        self.assertTrue(form["written"].startswith("any("))

    def test_字下げの形(self):
        rule, _ = build(VAULT)
        self.assertEqual(
            rule.when.lines(),
            [
                "any",
                "  glob '*.crt'",
                "  all",
                "    glob '*/vault/*'",
                "    not",
                "      glob '*.md'",
            ],
        )


class CommandLineTest(unittest.TestCase):
    """`--explain` が組み合わせ条件を出し、`--test-expectations` が when のルールを確かめる。"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.dir.name)
        self.addCleanup(self.dir.cleanup)
        path = common_path(self.root, "rules")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "deny": [VAULT],
                    "allow": [{"id": "all", "match": "Read", "glob": "*"}],
                },
                f,
            )

    def run_cli(self, *args: str) -> str:
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        done = run_ccnavi(
            ["--root", self.root, *args],
            cwd=ROOT,
            env=environment,
        )
        return done.stdout

    def test_explain(self):
        out = self.run_cli("--explain")
        self.assertIn("when: any(glob '*.crt', all(glob '*/vault/*', not(glob '*.md')))", out)
        self.assertIn("        any\n          glob '*.crt'\n", out)

    def test_expectations(self):
        expectations = os.path.join(self.root, "expectations.yml")
        with open(expectations, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "deny": [
                        {"tool": "Write", "subject": "/repo/k.crt", "why": "鍵"},
                        {"tool": "Write", "subject": "/repo/vault/token", "why": "秘密"},
                    ],
                    "ask": [
                        {"tool": "Write", "subject": "/repo/vault/README.md", "why": "説明"},
                    ],
                },
                f,
            )
        body = json.loads(self.run_cli("--test-expectations", expectations, "--json"))
        self.assertEqual(body["mismatches"], 0, body)
        hit = body["expectations"][0]["rules"][0]
        self.assertEqual(hit["id"], ".ccnavi/config/common/rules.yml:vault")
        self.assertEqual(hit["kind"], "when")


if __name__ == "__main__":
    unittest.main()
