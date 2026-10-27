"""文面ファイルと、それを引くコード・sh の食い違いを見る。

ccnavi のリポジトリのソースがあるときだけ確かめられる点検（配布先の `--lint` には呼ぶ側が無い）。

- 引かれている識別子は、共通レイヤーの文面ファイルにある
- 文面ファイルの識別子は、どこかから引かれている
- 呼ぶ側が渡す値の名前は、文面ファイルの `values` と一致する
- 共通レイヤーの文面ファイルは検査を通る
- 同梱の `rules.yml` の文面（message・additionalContext・additionalContextOnce）に ADR 番号が無い
"""

from __future__ import annotations

import ast
import glob
import os
import re
import shutil
import tempfile
import unittest

from ccnavi.policy import rules, texts
from tests import ROOT

SRC = os.path.join(ROOT, "src", "ccnavi")
SCRIPTS = os.path.join(ROOT, ".ccnavi", "scripts")

# sh の呼び出し。識別子の `$` を含むものは、頭の部分だけが決まっている。
_SH_CALL = re.compile(r'ccnavi_text "[^"]*" "?([A-Z][A-Z0-9_$a-z]*)"?((?: "[a-z_@]+=[^"]*")*)')
_SH_VALUE = re.compile(r'"([a-z_@]+)=')


def python_calls() -> list[tuple[str, str, set[str] | None, str]]:
    """（ファイル, 識別子, 値の名前（辞書が書かれていなければ None）, 場所）。"""
    found = []
    for path in glob.glob(os.path.join(SRC, "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "say"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "texts"
                and len(node.args) >= 2
            ):
                continue
            ident = node.args[1]
            if not (isinstance(ident, ast.Constant) and isinstance(ident.value, str)):
                continue
            names = set()
            values = node.args[2] if len(node.args) > 2 else None
            for kw in node.keywords:
                if kw.arg == "values":
                    values = kw.value
            if values is None:
                names = set()
            elif isinstance(values, ast.Dict) and all(
                isinstance(k, ast.Constant) for k in values.keys
            ):
                names = {k.value for k in values.keys}
            else:
                names = None
            found.append((path, ident.value, names, f"{os.path.relpath(path, ROOT)}:{node.lineno}"))
    return found


def sh_calls() -> list[tuple[str, str, set[str]]]:
    found = []
    for path in glob.glob(os.path.join(SCRIPTS, "*.sh")):
        with open(path, encoding="utf-8") as f:
            for number, line in enumerate(f, 1):
                for hit in _SH_CALL.finditer(line):
                    names = {n for n in _SH_VALUE.findall(hit.group(2)) if not n.startswith("@")}
                    found.append((hit.group(1), f"{os.path.basename(path)}:{number}", names))
    return found


def source_text() -> str:
    parts = []
    for path in [
        *glob.glob(os.path.join(SRC, "**", "*.py"), recursive=True),
        *glob.glob(os.path.join(SCRIPTS, "*.sh")),
    ]:
        with open(path, encoding="utf-8") as f:
            parts.append(f.read())
    return "\n".join(parts)


class CatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = texts.load(ROOT)

    def test_the_common_files_pass_the_check(self):
        self.assertEqual([], [str(p) for p in self.catalog.problems])
        self.assertGreater(len(self.catalog.entries), 20)

    def test_every_id_the_code_asks_for_exists_with_the_same_values(self):
        self.assertGreaterEqual(len(python_calls()), 8, "say の呼び出しを拾えていない")
        self.assertGreaterEqual(len(sh_calls()), 25, "ccnavi_text の呼び出しを拾えていない")
        for _, ident, names, where in python_calls():
            with self.subTest(where=where):
                entry = self.catalog.entries.get(ident)
                self.assertIsNotNone(entry, f"{where}: {ident} が文面ファイルに無い")
                if names is not None:
                    self.assertEqual(set(entry.values), names, f"{where}: {ident} の値の名前")
        for ident, where, names in sh_calls():
            with self.subTest(where=where):
                if "$" in ident:
                    head = ident.split("$", 1)[0]
                    self.assertTrue(
                        [i for i in self.catalog.entries if i.startswith(head)],
                        f"{where}: {ident}",
                    )
                    continue
                entry = self.catalog.entries.get(ident)
                self.assertIsNotNone(entry, f"{where}: {ident} が文面ファイルに無い")
                self.assertEqual(set(entry.values), names, f"{where}: {ident} の値の名前")

    def test_every_id_in_the_files_is_asked_for_somewhere(self):
        body = source_text()
        heads = [i.split("$", 1)[0] for i, _, _ in sh_calls() if "$" in i]
        for ident in self.catalog.entries:
            with self.subTest(ident=ident):
                used = ident in body or any(ident.startswith(h) for h in heads)
                self.assertTrue(used, f"{ident} をどこも引かない")


def builtin_rules(root: str, wrapper: bool) -> list[rules.Rule]:
    """判定で組み立てる組み込みのルール（文を持つもの）を、全部集める。

    ラッパーの有無（git の入口の sh）と、ワークスペースルートの有無で、文の場合が変わる。
    """
    from ccnavi.infra import settings
    from ccnavi.policy import builtin, selfguard_shell
    from ccnavi.tickets import phase_forms, ticket_guard

    if wrapper:
        script = os.path.join(root, ".ccnavi", "scripts", "ccnavi-git.sh")
        os.makedirs(os.path.dirname(script), exist_ok=True)
        with open(script, "w", encoding="utf-8"):
            pass
    conf = settings.Settings(project_home=settings.DEFAULT_PROJECT_HOME)
    found = [*builtin.load(root, conf)[0].deny, *builtin.load_base(root)[0].deny]
    rule_set = rules.RuleSet(version=rules.VERSION)
    # 実行ファイルの 1 本と共通レイヤーの 1 本は、パスが決まっているときだけ足す。
    common = tuple(p for p in selfguard_shell.common_layer_files(conf) if p) or (
        os.path.join(root, ".ccnavi", "config", "common", "rules.yml"),
    )
    selfguard_shell.add_rules(
        rule_set,
        os.path.join(root, "bin", "ccnavi"),
        conf.project_home,
        root,
        common,
        (conf.log, conf.state),
    )
    selfguard_shell.add_named_rules(rule_set, conf.project_home, root)
    selfguard_shell.add_named_rules(rule_set, conf.project_home, "")
    found += rule_set.deny
    found += ticket_guard.guard_rules(conf.tickets, root)
    found += ticket_guard.gate_rules(conf.approved, root)
    found.append(ticket_guard.raw_git_rule(root))
    found.append(phase_forms.ticket_approval_rule(conf.bin, root))
    return found


class BuiltinRuleTextsTest(unittest.TestCase):
    """組み込みのルールが持つ文の識別子・値・場合が、文面ファイルと合っている。

    組み込みのルールは文を組み立てで引かず、識別子を持つ。呼び出しが定数の `texts.say` では
    ないので、上の点検には映らない。ここで同じことを見る。
    """

    @classmethod
    def setUpClass(cls):
        cls.catalog = texts.load(ROOT)

    def test_every_builtin_rule_has_a_text_that_exists_with_the_same_values(self):
        seen = set()
        for wrapper in (False, True):
            with tempfile.TemporaryDirectory() as root:
                shutil.copytree(
                    os.path.join(ROOT, ".ccnavi", "messages"),
                    os.path.join(root, ".ccnavi", "messages"),
                )
                for rule in builtin_rules(root, wrapper):
                    with self.subTest(rule=rule.id, wrapper=wrapper):
                        self.assertTrue(rule.message_id, f"{rule.id}: 文の識別子が無い")
                        self.assertFalse(rule.message, f"{rule.id}: 文を直接持っている")
                        entry = self.catalog.entries.get(rule.message_id)
                        self.assertIsNotNone(entry, f"{rule.id}: {rule.message_id} が無い")
                        self.assertEqual(set(entry.values), set(rule.message_values))
                        for name, case in rule.message_variants.items():
                            self.assertIn(name, entry.fields)
                            self.assertIn(case, entry.fields[name], f"{rule.id}: 場合 {case}")
                        said = texts.spoken(rule)
                        self.assertNotIn("引けませんでした", said, f"{rule.id}: 文が引けない")
                        self.assertNotIn("{", said, f"{rule.id}: 差し込みが残っている")
                        self.assertEqual(len(texts.lines_of(said)) <= 1, True)
                        seen.add(rule.message_id)
        self.assertGreaterEqual(len(seen), 20)

    def test_every_rule_text_in_the_files_is_used_by_a_builtin_rule(self):
        """`RULE_` の文（`RULE_ASK` の系統を除く）は、組み込みのルールのどれかが持つ。"""
        used = set()
        for wrapper in (False, True):
            with tempfile.TemporaryDirectory() as root:
                used.update(r.message_id for r in builtin_rules(root, wrapper))
        # 実行後の報告（hook/post_findings.py）が作るルールの文は、組み立てる場面が違うので、
        # ソースに識別子が書かれていることで見る。
        body = source_text()
        for ident, entry in self.catalog.entries.items():
            if ident.startswith("RULE_") and not ident.startswith("RULE_ASK"):
                with self.subTest(ident=ident):
                    self.assertTrue(
                        ident in used or ident in body,
                        f"{ident} をどの組み込みのルールも持たない",
                    )


class HookTextsTest(unittest.TestCase):
    """識別子や場合を実行のときに選ぶ通知・報告の文が、どの組み合わせでも引ける。

    呼び出しの識別子が定数でない（結末や場合で選ぶ）ので、上の点検には映らない。
    """

    GONE = "引けませんでした"

    def test_undeclared_texts_exist_for_every_verdict_and_case(self):
        from ccnavi.hook import reasons

        for degraded in ("", "x"):
            for verdict in (rules.ASK, rules.DENY):
                for fell_back in (False, True):
                    with self.subTest(degraded=degraded, verdict=verdict, fell_back=fell_back):
                        said = reasons.undeclared(
                            "Bash",
                            "ls",
                            "/s/rules.yml",
                            degraded,
                            verdict=verdict,
                            fell_back=fell_back,
                            root=ROOT,
                        )
                        self.assertTrue(said)
                        self.assertNotIn(self.GONE, said)
                        self.assertNotIn("{", said)
        self.assertEqual(
            "", reasons.undeclared("Bash", "ls", "s", "", verdict="handover", root=ROOT)
        )

    def test_the_fallen_back_notice_names_the_file(self):
        from ccnavi.hook import reasons

        said = reasons.fallen_back("/p/rules.yml", ROOT)
        self.assertIn("/p/rules.yml", said)
        self.assertNotIn(self.GONE, said)

    def test_ticket_scope_texts_take_the_values_the_code_gives(self):
        expected = {
            "RULE_TICKET_SCOPE__BLOCKED": {"ticket", "worktree", "blocked"},
            "RULE_TICKET_SCOPE__TYPE": {
                "ticket",
                "worktree",
                "area",
                "type_title",
                "type_id",
                "scope",
            },
            "RULE_TICKET_SCOPE__PARENT": {"ticket", "worktree", "area", "parent", "parent_area"},
            "RULE_TICKET_SCOPE__PLAIN": {"ticket", "worktree", "area"},
        }
        catalog = texts.load(ROOT)
        for ident, names in expected.items():
            with self.subTest(ident=ident):
                self.assertEqual(set(catalog.entries[ident].values), names)

    def test_docs_and_report_texts_render_with_each_case(self):
        catalog = texts.load(ROOT)
        cases = {
            "NOTICE_DOCS_GUIDE": ("what", ("plain", "convention")),
            "NOTICE_DOCS_FRONTMATTER__NONE": ("what", ("plain", "convention")),
            "NOTICE_DOCS_FRONTMATTER__TYPE": ("what", ("plain", "convention")),
            "NOTICE_DOCS_FRONTMATTER__SKILL": ("what", ("plain", "convention")),
            "NOTICE_POST_NOT_RESTORED": ("what", ("shadowed", "scope", "ask")),
            "NOTICE_POST_DROPPED": ("what", ("git", "plain")),
            "NOTICE_POST_ALL_AT": ("what", ("git", "plain")),
            "NOTICE_POST_UNCOUNTED": ("what", ("git", "plain")),
            "DENY_SHELL_WORKSPACE_ROOT": ("what", ("origin", "plain")),
        }
        for ident, (field_name, wanted) in cases.items():
            entry = catalog.entries[ident]
            self.assertEqual(set(entry.fields[field_name]), set(wanted), ident)
            values = {name: "v" for name in entry.values}
            for case in wanted:
                with self.subTest(ident=ident, case=case):
                    said = texts.say(ROOT, ident, values, {field_name: case})
                    self.assertNotIn(self.GONE, said)
                    self.assertNotIn("{", said)


class ShNotesTest(unittest.TestCase):
    """sh の `案内:`（note-sh.yml）は、値を埋めると `案内: ` の行だけになり、`次:` の行を足さない。

    `次:` の行は、出力の終わりの 1 行に決まっている（script-output.md）。案内が `次:` を足すと、
    その数が崩れる。
    """

    def test_every_note_renders_as_note_lines_only(self):
        catalog = texts.load(ROOT)
        path = os.path.join(ROOT, ".ccnavi", "messages", "note-sh.yml")
        with open(path, encoding="utf-8") as f:
            ids = re.findall(r"^  (NOTE_[A-Z0-9_]+):$", f.read(), re.M)
        self.assertGreaterEqual(len(ids), 25)
        for ident in ids:
            with self.subTest(ident=ident):
                entry = catalog.entries[ident]
                said = texts.say(ROOT, ident, {name: "v" for name in entry.values})
                self.assertNotIn("引けませんでした", said)
                self.assertNotIn("{", said)
                self.assertEqual(texts.lines_of(said), [])
                for line in said.splitlines():
                    self.assertTrue(line.startswith(texts.NOTE_LABEL), line)

    def test_sh_calls_the_notes_it_moved(self):
        """移した `案内:` を sh が引く。引く識別子は文面ファイルにあり、値の名前が合う（上の点検）。"""
        called = {ident for ident, _, _ in sh_calls() if ident.startswith("NOTE_")}
        for ident in (
            "NOTE_GIT_INTEGRATE_CONFLICT",
            "NOTE_GIT_MERGE_ON_TARGET_ALLOWED",
            "NOTE_WORKTREE_RECORD_MISSING",
            "NOTE_START_TEMPLATES",
            "NOTE_STAGE_NONINTERACTIVE",
            "NOTE_SYNC_RETIRED_UNANSWERED",
            "NOTE_TICKET_NEW_ISSUE",
        ):
            self.assertIn(ident, called)


class SharedRulesWordingTest(unittest.TestCase):
    """同梱の rules.yml の文面。ユーザとエージェントに見える文に ADR 番号を書かない。

    配布先のプロジェクトの rules.yml は、その利用者の ADR を書いてよいので、検査しない。
    """

    def test_no_adr_number_in_shared_rule_wording(self):
        path = os.path.join(ROOT, ".ccnavi", "config", "common", "rules.yml")
        rule_set, problems = rules.load(path, ROOT)
        self.assertEqual([], [p for p in problems if p.severity == "error"])
        every = [*rule_set.deny, *rule_set.ask, *rule_set.allow]
        self.assertGreater(len(every), 10)
        adr = re.compile(r"ADR[-\s]?\d", re.IGNORECASE)
        for rule in every:
            for field in ("message", "additional_context", "additional_context_once"):
                with self.subTest(rule=rule.id, field=field):
                    self.assertIsNone(adr.search(getattr(rule, field) or ""))


if __name__ == "__main__":
    unittest.main()
