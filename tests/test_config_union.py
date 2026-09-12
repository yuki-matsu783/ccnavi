"""設定 3 本の和（設計 §25 改版、wip/design/config-union.md）の受入テスト。rules の面。

道具を外から動かす。一時ディレクトリにワークスペース 1 つとプロジェクト 2 つ
（lib と app）を組み、hook の payload を標準入力で渡して判定と記録を読む。

層は 3 種。

- 共通層: `.claude/ccnavi/{rules,phases,risk}.yml`（`--rules` / `--phases` / `--risk`）
- ワークスペース自身の層: `<ワークスペースルート>/.ccnavi/config/`
- プロジェクトの層: `projects/<名前>/.ccnavi/config/`

lib は 3 本とも持ち、app は `.ccnavi/` を持たない（無い層 = 空）。

実装はまだ無い。このテストは実装フェーズが緑にする。ここでは import 時に落ちない
ことと、振る舞いを 1 つずつ固定していることだけを守る。phases / risk の合成は
test_config_union_phases_risk.py、selfguard と導入スクリプトは test_config_union_guard.py。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests.inproc import run_ccnavi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 共通層。どのツリーにも効いてほしい deny と、ワークスペースの allow。
COMMON_RULES = {
    "version": 3,
    "deny": [
        {
            "id": "credentials",
            "match": "Write|Edit",
            "glob": "*/.env*",
            "message": "credentials are not edited by the agent.",
        },
        {
            "id": "terraform",
            "match": "Bash",
            "glob": "*terraform apply*",
            "message": "terraform apply is not run by the agent.",
        },
        {
            "id": "guard-approved",
            "match": "Write|Edit|NotebookEdit",
            "glob": "*/.claude/ccnavi/*",
            "message": "guard settings. ask the user.",
        },
    ],
    "allow": [
        {"id": "ws-src", "match": "Write|Edit", "glob": "*/src/*"},
        {"id": "ws-read", "match": "Read", "regex": "."},
    ],
}

# ワークスペース自身の層。ワークスペースのツリーにだけ効く。
OWN_RULES = {
    "version": 3,
    "deny": [
        {
            "id": "generated",
            "match": "Write|Edit",
            "glob": "*/generated/*",
            "message": "generated files are rebuilt, not edited.",
        },
        {
            "id": "dropdb",
            "match": "Bash",
            "glob": "*dropdb*",
            "message": "dropdb is not run by the agent.",
        },
    ],
    "allow": [{"id": "docs", "match": "Write|Edit", "glob": "*/docs/*"}],
}

# lib の層。lib のツリーにだけ効く。
LIB_RULES = {
    "version": 3,
    "deny": [
        {
            "id": "schema",
            "match": "Write|Edit",
            "glob": "*/schema/*",
            "message": "schema is not edited by hand. write a migration.",
        },
        {
            "id": "raw-psql",
            "match": "Bash",
            "glob": "*psql*",
            "message": "psql is not run by the agent.",
        },
    ],
    "allow": [{"id": "source", "match": "Write|Edit", "glob": "*/src/*"}],
}

COMMON_PHASES = """\
version: 1
phases:
  design:
    kind: work
    title: 設計
    review: mr
    scope: ["wip/design/*"]
"""

OWN_PHASES = """\
version: 1
phases:
  docs:
    kind: work
    title: 文書
    review: none
    scope: ["docs/*"]
"""

LIB_PHASES = """\
version: 1
phases:
  build:
    kind: work
    title: ビルド
    review: none
    scope: ["src/*"]
  release:
    kind: work
    title: リリース
    review: mr
    scope: ["src/*"]
    requires: [design]
"""

COMMON_RISK = """\
version: 1
levels: {medium: 20, high: 40, critical: 70}
factors:
  - {id: big-diff, points: 25, lines_over: 5, message: 行数が多い}
"""

LIB_RISK = """\
version: 1
levels: {critical: 50}
factors:
  - {id: schema, points: 30, glob: "schema/**", message: スキーマに触った}
"""

# YAML として壊れている。閉じていない並び。
BROKEN = "version: 3\ndeny: [\n"

# 層の傘の既定の名前（設計 §25.2、`CCNAVI_PROJECT_HOME` の既定）。
HOME = ".ccnavi"


def git(cwd, *args):
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def layer_path(root, kind, home=HOME):
    """その git プロジェクトルートの層のファイル（rules / phases / risk）の綴り。"""
    return os.path.join(root, home, "config", f"{kind}.yml")


def write_layer(root, *, rules=None, phases=None, risk=None, home=HOME):
    """層の 3 本を置く。None の欄は置かない（無い = 空）。dict は JSON で書く。"""
    for kind, body in (("rules", rules), ("phases", phases), ("risk", risk)):
        if body is None:
            continue
        text = json.dumps(body) if isinstance(body, dict) else body
        write(layer_path(root, kind, home), text)


def ticket_text(
    name,
    *,
    project="",
    parent="",
    phase=None,
    plan=(),
    allow=(),
    review=False,
    title="作業",
):
    """提案の本文。親は plan を、子は parent と phase を持つ。project は行き先の層を決める。"""
    lines = ["---", "version: 1", f"ticket: {name}"]
    if project:
        lines.append(f"project: {project}")
    if parent:
        lines += [f"parent: {parent}", f"phase: {phase}"]
    if plan:
        lines.append("plan:")
        for item in plan:
            lines.append(f"  - {item}")
    lines += [
        "human_review:",
        f"  required: {'true' if review else 'false'}",
        "  reason: テスト",
        f"title: {title}",
        "rationale: |",
        "  理由",
    ]
    if allow:
        lines.append("allow:")
        for g in allow:
            lines += ["  - match: Write|Edit", f'    glob: "{g}"']
    lines += ['started_at: ""', 'completed_at: ""', 'base_sha: ""', "---", "", "本文"]
    return "\n".join(lines) + "\n"


class ConfigUnionHarness(unittest.TestCase):
    """ワークスペースと 2 つのプロジェクトを組む道具。テストは持たない。"""

    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-union-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        self.build_workspace()

    # ---- 組み立て

    def build_workspace(self):
        """git 初期化したワークスペースに、共通層と自身の層と projects/ を置く。

        `.claude/` と `projects/` はワークスペースの git で無視する。自身の層 `.ccnavi/` は
        追跡する（作業ツリーに写しが入る形）。
        """
        os.makedirs(os.path.join(self.ws, ".claude"))
        git(self.ws, "init", "--quiet", "-b", "main")
        write(os.path.join(self.ws, ".gitignore"), "/projects/\n/.claude/\n")
        for rel in ("src/keep.py", "generated/keep.py", "schema/keep.sql", "docs/keep.md"):
            write(os.path.join(self.ws, *rel.split("/")), "keep\n")
        write_layer(self.ws, rules=OWN_RULES, phases=OWN_PHASES)
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "--quiet", "-m", "init")

        common = os.path.join(self.ws, ".claude", "ccnavi")
        self.rules = write(os.path.join(common, "rules.yml"), json.dumps(COMMON_RULES))
        self.phases = write(os.path.join(common, "phases.yml"), COMMON_PHASES)
        self.risk = write(os.path.join(common, "risk.yml"), COMMON_RISK)
        self.approved = os.path.join(common, "tickets")
        self.state = os.path.join(self.ws, "state")
        self.log = os.path.join(self.ws, "log.jsonl")

        self.projects = os.path.join(self.ws, "projects")
        self.lib = self.project("lib", rules=LIB_RULES, phases=LIB_PHASES, risk=LIB_RISK)
        self.app = self.project("app")

    def project(self, name, *, rules=None, phases=None, risk=None, home=HOME):
        """`projects/<名前>/` に git 初期化したプロジェクトを置く。層は渡したぶんだけ。"""
        root = os.path.join(self.projects, name)
        os.makedirs(root)
        git(root, "init", "--quiet", "-b", "main")
        for rel in ("src/keep.py", "generated/keep.py", "schema/keep.sql", "docs/keep.md"):
            write(os.path.join(root, *rel.split("/")), "keep\n")
        write_layer(root, rules=rules, phases=phases, risk=risk, home=home)
        git(root, "add", "-A")
        git(root, "commit", "--quiet", "-m", "init")
        return root

    def worktree(self, owner, name):
        """`.claude/worktrees/<名前>` に、owner（ワークスペースかプロジェクト）から切る。"""
        path = os.path.join(self.ws, ".claude", "worktrees", name)
        git(owner, "worktree", "add", "--quiet", path, "-b", name)
        return path

    def propose(self, name, text, project=""):
        """提案を置く。プロジェクト向けは `wip/<名前>/tickets/`（設計 §25.5）。"""
        parts = ["wip", *([project] if project else []), "tickets", "todo", name + ".md"]
        return write(os.path.join(self.ws, *parts), text)

    # ---- 起動

    def ccnavi(self, *args, stdin="", projects=None, rules=None, env=None, guard="disable"):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        return run_ccnavi(
            [
                "--root",
                self.ws,
                "--rules",
                rules or self.rules,
                "--phases",
                self.phases,
                "--risk",
                self.risk,
                "--projects",
                self.projects if projects is None else projects,
                "--approved",
                self.approved,
                "--state",
                self.state,
                "--log",
                self.log,
                "--guard-core-files",
                guard,
                "--restore-if-deny",
                "disable",
                "--guard-ticket-approval",
                "disable",
                *args,
            ],
            input=stdin,
            cwd=ROOT,
            env=environment,
        )

    def hook(self, tool, cwd, *, event="PreToolUse", session="s1", **kw):
        """hook の payload を 1 件渡す。kw のうち起動の引数は取り出し、残りは tool_input。"""
        options = {k: kw.pop(k) for k in ("projects", "rules", "env", "guard") if k in kw}
        payload = {
            "hook_event_name": event,
            "tool_name": tool,
            "cwd": cwd,
            "session_id": session,
            "tool_input": kw,
        }
        return self.ccnavi("--mode", "enable", stdin=json.dumps(payload), **options)

    def approve(self):
        return self.ccnavi("--approve", stdin="y\n")

    def lint_json(self, *args, **options):
        result = self.ccnavi("--lint", "--json", *args, **options)
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            self.fail(f"--lint --json が JSON を返さない: {exc}\n{result.stdout}\n{result.stderr}")

    def problems(self, severity, *args, **options):
        return [
            p for p in self.lint_json(*args, **options)["problems"] if p["severity"] == severity
        ]

    # ---- 読み取り

    def decision(self, result):
        if not result.stdout.strip():
            return ""
        return json.loads(result.stdout).get("hookSpecificOutput", {}).get("permissionDecision", "")

    def reason(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecisionReason") or out.get("additionalContext") or ""

    def last_record(self):
        with open(self.log, encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
        return json.loads(lines[-1])

    def assert_denied(self, result, *ids):
        self.assertEqual(self.decision(result), "deny", result.stdout + result.stderr)
        for rule_id in ids:
            self.assertIn(rule_id, self.reason(result))

    def assert_not_denied(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.decision(result), "deny", result.stdout)


class WriteUnionTest(ConfigUnionHarness):
    """Write / Edit は共通層 + 行き先の層の和（§25.4、REQ-MLT-03 の変更）。"""

    def test_common_deny_applies_to_project_files(self):
        """§25.4: 共通層の deny がプロジェクトのファイルに効く。id は裸、source は common。"""
        denied = self.hook("Write", self.ws, file_path=os.path.join(self.lib, ".env"))
        self.assert_denied(denied, "credentials")
        record = self.last_record()
        self.assertEqual(record["project"], "lib")
        self.assertEqual(record["rules"], ["credentials"])
        self.assertEqual(record.get("source"), "common")

    def test_project_deny_applies_only_to_that_project(self):
        """§25.4: プロジェクトの層の deny はそのプロジェクトにだけ効く。id は `lib:schema`。"""
        denied = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "schema", "x.sql"))
        self.assert_denied(denied, "lib:schema", "write a migration")
        self.assertEqual(self.last_record()["rules"], ["lib:schema"])
        self.assertEqual(self.last_record().get("source"), "lib")

        self.assert_not_denied(
            self.hook("Write", self.ws, file_path=os.path.join(self.app, "schema", "x.sql"))
        )
        self.assert_not_denied(
            self.hook("Write", self.ws, file_path=os.path.join(self.ws, "schema", "x.sql"))
        )

    def test_own_layer_applies_to_workspace_trees(self):
        """§25.4: 自身の層はワークスペースルートと、そこから切った作業ツリーに効く。"""
        denied = self.hook("Write", self.lib, file_path=os.path.join(self.ws, "generated", "x.py"))
        self.assert_denied(denied, "self:generated")
        record = self.last_record()
        self.assertEqual(record["rules"], ["self:generated"])
        self.assertEqual(record.get("source"), "self")
        self.assertNotIn("project", record)

        tree = self.worktree(self.ws, "w1")
        denied = self.hook("Write", self.ws, file_path=os.path.join(tree, "generated", "x.py"))
        self.assert_denied(denied, "self:generated")
        self.assertEqual(self.last_record()["tree"], "w1")

    def test_own_layer_does_not_reach_projects(self):
        """§25.4: 自身の層はプロジェクトのツリーには足さない。"""
        self.assert_not_denied(
            self.hook("Write", self.ws, file_path=os.path.join(self.lib, "generated", "x.py"))
        )

    def test_project_layer_applies_to_a_worktree_cut_from_it(self):
        """§25.4: プロジェクトから切った作業ツリーには、共通層 + そのプロジェクトの層。"""
        tree = self.worktree(self.lib, "i0007")
        denied = self.hook("Write", self.ws, file_path=os.path.join(tree, "schema", "x.sql"))
        self.assert_denied(denied, "lib:schema")
        self.assertEqual(self.last_record()["tree"], "i0007")
        denied = self.hook("Write", self.ws, file_path=os.path.join(tree, ".env"))
        self.assert_denied(denied, "credentials")

    def test_project_allow_stays_inside_the_project(self):
        """§25.4 代償: 層の allow は行き先の 1 層にしか足さない。"""
        allowed = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "src", "a.py"))
        self.assert_not_denied(allowed)
        record = self.last_record()
        self.assertEqual(record["decision"], "allow")
        # 共通層の ws-src が先に当たる（共通層 → 層の順）。どちらでも allow。
        self.assertIn(record["rules"][0], ("ws-src", "lib:source"))

        allowed = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "docs", "a.md"))
        self.assertEqual(self.last_record()["decision"], "allow")
        self.assertEqual(self.last_record()["rules"], ["self:docs"])
        # lib の docs/ に self:docs は効かない。どのルールも言及しない。
        self.hook("Write", self.ws, file_path=os.path.join(self.lib, "docs", "a.md"))
        self.assertNotIn("self:docs", self.last_record().get("rules", []))


class BashUnionTest(ConfigUnionHarness):
    """Bash は共通層 + 自身の層 + 全プロジェクトの層（§25.4、REQ-MLT-05 の変更）。"""

    def test_bash_is_the_union_of_every_layer_regardless_of_cwd(self):
        """§25.4: どの層の deny も cwd によらず当たる。"""
        for cwd in (self.ws, self.lib, self.app):
            with self.subTest(cwd=os.path.basename(cwd)):
                self.assert_denied(
                    self.hook("Bash", cwd, command="terraform apply -auto-approve"), "terraform"
                )
                self.assert_denied(self.hook("Bash", cwd, command="dropdb prod"), "self:dropdb")
                self.assert_denied(
                    self.hook("Bash", cwd, command="psql -c 'select 1'"), "lib:raw-psql"
                )

    def test_bash_record_names_the_layer_of_the_deciding_rule(self):
        """§25.9: 記録の `source` は判定を下したルール（`rules` の先頭）の層。"""
        self.hook("Bash", self.app, command="dropdb prod")
        record = self.last_record()
        self.assertEqual(record["rules"][0], "self:dropdb")
        self.assertEqual(record.get("source"), "self")
        self.hook("Bash", self.app, command="psql")
        self.assertEqual(self.last_record().get("source"), "lib")


class DuplicateTest(ConfigUnionHarness):
    """同 `id` の扱い（§25.4「重複は後ろを捨てる」「同 id で中身が違うとき」）。"""

    def test_identical_definition_in_a_later_layer_is_dropped(self):
        """§25.4: 裸の id と全欄が一致する定義は後ろの層を捨てる。記録に 1 本、--lint は info。"""
        copied = dict(LIB_RULES)
        copied["deny"] = [COMMON_RULES["deny"][0], *LIB_RULES["deny"]]
        write_layer(self.lib, rules=copied)

        denied = self.hook("Write", self.ws, file_path=os.path.join(self.lib, ".env"))
        self.assert_denied(denied, "credentials")
        self.assertEqual(self.last_record()["rules"], ["credentials"])

        infos = self.problems("info")
        self.assertTrue(any("credentials" in p["detail"] for p in infos), infos)
        self.assertFalse(any("credentials" in p["detail"] for p in self.problems("warn")))

    def test_same_id_with_different_content_keeps_both(self):
        """§25.4: 同 id で中身が違う rules は両方効く。記録に両方が並び、--lint は warn。"""
        differs = dict(LIB_RULES)
        differs["deny"] = [
            dict(COMMON_RULES["deny"][0], glob="*/secrets/*"),
            *LIB_RULES["deny"],
        ]
        write_layer(self.lib, rules=differs)

        denied = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "secrets", ".env"))
        self.assert_denied(denied, "credentials", "lib:credentials")
        self.assertEqual(sorted(self.last_record()["rules"]), ["credentials", "lib:credentials"])

        warns = self.problems("warn")
        self.assertTrue(any("credentials" in p["detail"] for p in warns), warns)

    def test_bash_union_drops_identical_duplicates_too(self):
        """§25.4: Bash の和でも同じ。共通層の `terraform` を写した lib の定義は 1 本にまとまる。"""
        copied = dict(LIB_RULES)
        copied["deny"] = [*LIB_RULES["deny"], COMMON_RULES["deny"][1]]
        write_layer(self.lib, rules=copied)

        self.assert_denied(self.hook("Bash", self.lib, command="terraform apply"), "terraform")
        self.assertEqual(self.last_record()["rules"], ["terraform"])


class LayerFailureTest(ConfigUnionHarness):
    """層が無い・壊れている・共通層自身が壊れている、の 3 つ（§25.2）。"""

    def test_missing_layer_is_empty_without_fallback(self):
        """§25.2: 層のファイルが無い = 空。fallback は付かず、--lint も言わない。"""
        passed = self.hook("Write", self.ws, file_path=os.path.join(self.app, "schema", "x.sql"))
        self.assert_not_denied(passed)
        record = self.last_record()
        self.assertNotIn("fallback", record)
        self.assertNotIn("built-in defaults", self.reason(passed))

        report = self.lint_json()
        self.assertFalse(
            [p for p in report["problems"] if p["where"].startswith("(projects/app)")], report
        )

    def test_broken_layer_is_empty_and_named_in_the_record(self):
        """§25.2 / REQ-MLT-06: 壊れた層は空 + 記録の `fallback` に層の名前。組み込みへ落ちない。"""
        write(layer_path(self.lib, "rules"), BROKEN)

        passed = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "schema", "x.sql"))
        self.assert_not_denied(passed)
        record = self.last_record()
        self.assertEqual(record.get("fallback"), "lib", record)
        self.assertNotEqual(record.get("fallback"), "builtin-rules")
        self.assertNotIn("built-in defaults", self.reason(passed))

    def test_broken_layer_keeps_the_common_deny(self):
        """§25.2: 壊れた層の上でも共通層の deny は効いたまま。"""
        write(layer_path(self.lib, "rules"), BROKEN)

        denied = self.hook("Write", self.ws, file_path=os.path.join(self.lib, ".env"))
        self.assert_denied(denied, "credentials")
        self.assertEqual(self.last_record().get("fallback"), "lib")

    def test_broken_layer_is_a_lint_error(self):
        """§25.9: 層のファイルが壊れている（空として扱っている）は --lint の error。"""
        write(layer_path(self.lib, "rules"), BROKEN)

        errors = self.problems("error")
        self.assertTrue(any(p["where"].startswith("(projects/lib)") for p in errors), errors)

    def test_broken_layer_drops_out_of_the_bash_union(self):
        """§25.4: Bash の和からも壊れた層は外れ、記録がそれを言う。"""
        write(layer_path(self.lib, "rules"), BROKEN)

        self.assert_not_denied(self.hook("Bash", self.lib, command="psql"))
        self.assert_denied(self.hook("Bash", self.lib, command="dropdb prod"), "self:dropdb")
        self.assertIn("lib", self.last_record().get("detail", ""))

    def test_broken_common_layer_falls_back_to_builtin_as_before(self):
        """§25.2 / REQ-PRE-06: 共通層自身が読めないときは今どおり組み込みの既定。層は足さない。"""
        broken = write(os.path.join(self.ws, "broken.yml"), BROKEN)

        passed = self.hook(
            "Write", self.ws, rules=broken, file_path=os.path.join(self.lib, "schema", "x.sql")
        )
        self.assert_not_denied(passed)
        record = self.last_record()
        self.assertEqual(record.get("fallback"), "builtin-rules")
        self.assertIn("built-in defaults", self.reason(passed))
        self.assertNotIn("lib:schema", self.reason(passed))


class WiringTest(ConfigUnionHarness):
    """置き場と環境変数（§25.2、§25.9、§25.12）。"""

    def test_project_named_self_is_not_counted(self):
        """§25.4: `projects/self/` は `self:id` と区別できないので数えず、--lint が error。"""
        deny = {
            "version": 3,
            "deny": [{"id": "dump", "match": "Bash", "glob": "*mysqldump*", "message": "no."}],
        }
        self.project("self", rules=deny)

        self.assert_not_denied(self.hook("Bash", self.ws, command="mysqldump db"))
        errors = self.problems("error")
        self.assertTrue(any("self" in p["detail"] for p in errors), errors)

    def test_project_home_env_moves_the_umbrella(self):
        """§25.2: `CCNAVI_PROJECT_HOME` で傘の名前が動く。`config/` と 3 本の名前は固定。"""
        moved = {
            "version": 3,
            "deny": [
                {"id": "vendor", "match": "Write|Edit", "glob": "*/vendor/*", "message": "no."}
            ],
        }
        write_layer(self.lib, rules=moved, home=".navi")
        env = {"CCNAVI_PROJECT_HOME": ".navi"}

        denied = self.hook(
            "Write", self.ws, env=env, file_path=os.path.join(self.lib, "vendor", "x.py")
        )
        self.assert_denied(denied, "lib:vendor")
        # 既定の `.ccnavi/config/` はもう読まない。
        self.assert_not_denied(
            self.hook(
                "Write", self.ws, env=env, file_path=os.path.join(self.lib, "schema", "x.sql")
            )
        )

    def test_retired_project_rules_env_is_warned(self):
        """§25.2: `CCNAVI_PROJECT_RULES` は廃止。設定されていれば --lint が warn で名指しする。"""
        warns = self.problems("warn", env={"CCNAVI_PROJECT_RULES": "config/rules.yml"})
        self.assertTrue(any("CCNAVI_PROJECT_RULES" in p["detail"] for p in warns), warns)

    def test_old_config_rules_is_not_read_and_warned(self):
        """§25.12: 旧の `config/rules.yml` は読まない。--lint が warn で言う。"""
        old = {
            "version": 3,
            "deny": [
                {"id": "vendor", "match": "Write|Edit", "glob": "*/vendor/*", "message": "no."}
            ],
        }
        write(os.path.join(self.app, "config", "rules.yml"), json.dumps(old))

        self.assert_not_denied(
            self.hook("Write", self.ws, file_path=os.path.join(self.app, "vendor", "x.py"))
        )
        warns = self.problems("warn")
        self.assertTrue(any("config/rules.yml" in p["detail"] for p in warns), warns)

    def test_workspace_without_projects_or_own_layer_is_unchanged(self):
        """§25.12 / REQ-MLT-15: `projects/` を数えず自身の層も無ければ、判定と記録は今のまま。"""
        shutil.rmtree(os.path.join(self.ws, HOME))

        allowed = self.hook(
            "Write", self.ws, projects="", file_path=os.path.join(self.ws, "src", "a.py")
        )
        self.assert_not_denied(allowed)
        record = self.last_record()
        self.assertEqual(record["decision"], "allow")
        self.assertEqual(record["rules"], ["ws-src"])
        self.assertNotIn("fallback", record)
        self.assertNotIn("project", record)

        denied = self.hook("Write", self.ws, projects="", file_path=os.path.join(self.ws, ".env"))
        self.assert_denied(denied, "credentials")
        self.assertEqual(self.last_record()["rules"], ["credentials"])
        self.assert_not_denied(self.hook("Bash", self.ws, projects="", command="psql"))


class ExplainTest(ConfigUnionHarness):
    """`--explain` は層ごとに全件（§25.9、REQ-MLT-17 の変更）。"""

    def test_explain_lists_rules_per_layer_and_the_phase_and_risk_tables(self):
        """§25.9: rules を共通層・自身の層・各プロジェクトの順に、phases と risk の表を足す。"""
        result = self.ccnavi("--explain")
        out = result.stdout
        self.assertEqual(result.returncode, 0, result.stderr)
        for heading in ("■ rules 共通層", "■ rules 自身の層", "■ rules lib", "■ phases", "■ risk"):
            self.assertIn(heading, out)
        self.assertLess(out.index("■ rules 共通層"), out.index("■ rules 自身の層"))
        self.assertLess(out.index("■ rules 自身の層"), out.index("■ rules lib"))
        self.assertIn("self:generated", out)
        self.assertIn("lib:schema", out)
        # phases と risk は id と出どころの層。
        self.assertIn("build", out)
        self.assertIn("big-diff", out)
        self.assertIn("schema", out)

    def test_explain_shows_a_broken_layer_as_unreadable_and_empty(self):
        """§25.9: 読めない層はその位置に「読めない」と、空として扱っていることを出す。"""
        write(layer_path(self.lib, "rules"), BROKEN)
        out = self.ccnavi("--explain").stdout
        self.assertIn("■ rules lib", out)
        self.assertIn("読めない", out)

    def test_explain_json_carries_every_layer(self):
        """§25.9: `--explain --json` に層ごとの rules 全件と phases / risk の定義と出どころが出る。

        形は README「ボードの JSON」に足す。ここでは `layers` の並びに `name`（common / self /
        プロジェクト名）と `rules` / `phases` / `risk` が在ることまでを固定する。
        """
        result = self.ccnavi("--explain", "--json")
        body = json.loads(result.stdout)
        layers = {layer["name"]: layer for layer in body["layers"]}
        self.assertEqual(sorted(layers), ["app", "common", "lib", "self"])

        def ids(section):
            return [rule["id"] for rule in section]

        self.assertIn("credentials", ids(layers["common"]["rules"]["deny"]))
        self.assertIn("self:generated", ids(layers["self"]["rules"]["deny"]))
        self.assertIn("lib:schema", ids(layers["lib"]["rules"]["deny"]))
        self.assertEqual(layers["app"]["rules"]["deny"], [])
        self.assertIn("design", ids(layers["common"]["phases"]))
        self.assertIn("build", ids(layers["lib"]["phases"]))
        self.assertIn("big-diff", ids(layers["common"]["risk"]["factors"]))
        self.assertIn("schema", ids(layers["lib"]["risk"]["factors"]))


if __name__ == "__main__":
    unittest.main()
