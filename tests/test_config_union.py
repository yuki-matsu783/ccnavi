"""設定 3 本の和（設計 §25 改版、wip/design/config-union.md）の受入テスト。rules の面。

道具を外から動かす。一時ディレクトリにワークスペース 1 つとプロジェクト 2 つ
（lib と app）を組み、hook の payload を標準入力で渡して判定と記録を読む。

層は 3 種。

- 共通層: `.claude/ccnavi/{rules,phases,risk}.yml`（`--rules` / `--phases` / `--risk`）
- ワークスペース自身の層: `<ワークスペースルート>/.ccnavi/config/`
- プロジェクトの層: `projects/<名前>/.ccnavi/config/`

lib は 3 本とも持ち、app は `.ccnavi/` を持たない（無い層 = 空）。

`.gitignore` は実物に合わせて 4 つだけ無視する（`projects/`、作業ツリー、状態、承認済み
チケット）。共通層の 3 本と自身の層は追跡するので、ワークスペースから切った作業ツリーに
作業ツリー側の設定ができ、設計 §25.6 が名指しした穴（作業ツリー側の設定が書けて戻らない）を
再現できる。

実装はまだ無い。このテストは実装フェーズが緑にする。ここでは import 時に落ちない
ことと、振る舞いを 1 つずつ固定していることだけを守る。phases / risk の合成は
test_config_union_phases_risk.py、selfguard と導入スクリプトは test_config_union_guard.py。
"""

from __future__ import annotations

import atexit
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

# app の層。fixture では置かない（無い層 = 空）。2 つ目の非空の層を要るテストだけが置く。
APP_RULES = {
    "version": 3,
    "deny": [
        {
            "id": "kube",
            "match": "Bash",
            "glob": "*kubectl*",
            "message": "kubectl is not run by the agent.",
        },
        {
            "id": "deploy",
            "match": "Bash",
            "glob": "*deploy.sh*",
            "message": "deploy.sh is run by a human.",
        },
    ],
}

# lib 側の、app の `deploy` と同じ呼び出しに当たる deny。並びが名前順かを見る。
LIB_DEPLOY = {
    "id": "deploy-any",
    "match": "Bash",
    "glob": "*deploy*",
    "message": "deploy is run by a human.",
}

# 共通層の `ask`。プロジェクトの層の `allow` で緩められないことを見る（§25.4 代償）。
ASK_VENDOR = {
    "id": "ask-vendor",
    "match": "Write|Edit",
    "glob": "*/vendor/*",
    "message": "vendor は人に確かめてから触る。",
}

# NotebookEdit を実際の `tool_name` として通すためのルール。欄は notebook_path。
NOTEBOOK_RULE = {
    "id": "notebook",
    "match": "NotebookEdit",
    "glob": "*/notebooks/*",
    "message": "ノートは人が回す。",
}

# `{root}` を含む定義。共通層と層の両方に同じものを置いて、置換後の全欄一致を見る（§25.8）。
ROOT_RULE = {
    "id": "root-secret",
    "match": "Write|Edit",
    "glob": "{root}/*/secret.txt",
    "message": "secret.txt は置かない。",
}

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


# どの git プロジェクトルートにも置く、判定の的になるファイル。
KEEP = ("src/keep.py", "generated/keep.py", "schema/keep.sql", "docs/keep.md")

# ワークスペースの git が無視するもの。実物の .gitignore と同じ 4 つだけ。
# `/.claude/` を丸ごと無視すると共通層の 3 本が追跡されず、作業ツリー側の設定ができない。
# それができないと、設計 §25.6 が名指しした穴（共通層の作業ツリー側の設定が書けて
# 戻らない）を一度も踏めない。
GITIGNORE = "/projects/\n/.claude/worktrees/\n/.claude/ccnavi/state/\n/.claude/ccnavi/tickets/\n"

# 雛形のワークスペース。1 度だけ組んで、以後は写しを配る。
#
# 組み直す形だと 1 件あたり git が 9 回（ワークスペースと 2 つのプロジェクトの
# init / add / commit）起き、この 3 ファイルの全件で 500 回を超えていた。写しなら
# git は雛形の 9 回だけで済む。テストごとに別のディレクトリを配るのは変わらないので、
# テストどうしが状態を共有することもない（`setUpClass` に寄せる形との違いはここ）。
_TEMPLATE = ""


def build_project(root, *, rules=None, phases=None, risk=None, home=HOME):
    """git 初期化したプロジェクトを 1 つ置く。層は渡したぶんだけ。"""
    os.makedirs(root)
    git(root, "init", "--quiet", "-b", "main")
    for rel in KEEP:
        write(os.path.join(root, *rel.split("/")), "keep\n")
    write_layer(root, rules=rules, phases=phases, risk=risk, home=home)
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "-m", "init")
    return root


def build_template():
    """雛形を組んで、その場所を返す。片付けはプロセスの終わりに 1 度。"""
    ws = tempfile.mkdtemp(prefix="ccnavi-union-template-")
    atexit.register(shutil.rmtree, ws, ignore_errors=True)
    os.makedirs(os.path.join(ws, ".claude"))
    git(ws, "init", "--quiet", "-b", "main")
    write(os.path.join(ws, ".gitignore"), GITIGNORE)
    for rel in KEEP:
        write(os.path.join(ws, *rel.split("/")), "keep\n")
    write_layer(ws, rules=OWN_RULES, phases=OWN_PHASES)
    common = os.path.join(ws, ".claude", "ccnavi")
    write(os.path.join(common, "rules.yml"), json.dumps(COMMON_RULES))
    write(os.path.join(common, "phases.yml"), COMMON_PHASES)
    write(os.path.join(common, "risk.yml"), COMMON_RISK)
    git(ws, "add", "-A")
    git(ws, "commit", "--quiet", "-m", "init")

    projects = os.path.join(ws, "projects")
    build_project(os.path.join(projects, "lib"), rules=LIB_RULES, phases=LIB_PHASES, risk=LIB_RISK)
    build_project(os.path.join(projects, "app"))
    return ws


class ConfigUnionHarness(unittest.TestCase):
    """ワークスペースと 2 つのプロジェクトを配る道具。テストは持たない。"""

    def setUp(self):
        global _TEMPLATE
        if not _TEMPLATE:
            _TEMPLATE = build_template()
        base = tempfile.mkdtemp(prefix="ccnavi-union-")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.ws = os.path.join(base, "ws")
        shutil.copytree(_TEMPLATE, self.ws)

        common = os.path.join(self.ws, ".claude", "ccnavi")
        self.rules = os.path.join(common, "rules.yml")
        self.phases = os.path.join(common, "phases.yml")
        self.risk = os.path.join(common, "risk.yml")
        # 承認済みチケットと印は、そのチケットの親のツリーの `.ccnavi/tickets/` に置かれる
        # （設計 §24.5）。ここの土台は親の作業ツリーを作らないので、提案があったツリーに落ちる。
        self.approved = os.path.join(self.ws, ".ccnavi", "tickets")
        self.state = os.path.join(self.ws, "state")
        self.log = os.path.join(self.ws, "log.jsonl")

        self.projects = os.path.join(self.ws, "projects")
        self.lib = os.path.join(self.projects, "lib")
        self.app = os.path.join(self.projects, "app")

    # ---- 組み立て

    def project(self, name, *, rules=None, phases=None, risk=None, home=HOME):
        """`projects/<名前>/` に git 初期化したプロジェクトを足す。"""
        return build_project(
            os.path.join(self.projects, name), rules=rules, phases=phases, risk=risk, home=home
        )

    def worktree(self, owner, name):
        """`.claude/worktrees/<名前>` に、owner（ワークスペースかプロジェクト）から切る。"""
        path = os.path.join(self.ws, ".claude", "worktrees", name)
        git(owner, "worktree", "add", "--quiet", path, "-b", name)
        return path

    def propose(self, name, text, project=""):
        """提案を置く。プロジェクト向けはそのプロジェクトの `wip/tickets/`（設計 §25.5）。"""
        base = os.path.join(self.projects, project) if project else self.ws
        return write(os.path.join(base, "wip", "tickets", "todo", name + ".md"), text)

    def approved_dir_of(self, project=""):
        """このプロジェクトの承認済みチケットの置き場。"""
        base = os.path.join(self.projects, project) if project else self.ws
        return os.path.join(base, ".ccnavi", "tickets")

    def approved_path(self, *parts, project=""):
        """承認済みチケットの置き場の下のパス。プロジェクトを渡さなければ、在る側を探す。"""
        if project:
            return os.path.join(self.approved_dir_of(project), *parts)
        for where in (self.ws, self.lib, self.app):
            path = os.path.join(where, ".ccnavi", "tickets", *parts)
            if os.path.exists(path):
                return path
        return os.path.join(self.approved, *parts)

    def approved_copy(self, name, project=""):
        """承認済みチケットのパス。プロジェクトを渡さなければ、在る側を探す。"""
        if project:
            return os.path.join(self.approved_dir_of(project), name + ".md")
        for where in (self.ws, self.lib, self.app):
            path = os.path.join(where, ".ccnavi", "tickets", name + ".md")
            if os.path.exists(path):
                return path
        return os.path.join(self.approved, name + ".md")

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
                ".ccnavi/tickets",
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

    def problems(self, severity, *args, where="", **options):
        """その深刻度の Problem。`where` を渡すと、その出どころで先に絞る。

        `detail` の部分一致だけで見ると、`self` や `lib` のようなありふれた語が
        別の苦情の文面に紛れていても通ってしまう。どの層の話かは `where` が持つ。
        """
        return [
            p
            for p in self.lint_json(*args, **options)["problems"]
            if p["severity"] == severity and (not where or p["where"].startswith(where))
        ]

    def project_where(self, name):
        """`--lint` の Problem の出どころのうち、そのプロジェクトのもの（`(projects/lib)`）。"""
        return f"(projects/{name})"

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

    def system_message(self, result):
        if not result.stdout.strip():
            return ""
        return json.loads(result.stdout).get("systemMessage", "")

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
        # §25.4「順は 共通層 → 自身の層 → プロジェクトの層」。先に当たるのは共通層の ws-src。
        # ここを「どちらでも良い」にすると、逆順に並べた実装でも通ってしまう。
        self.assertEqual(record["rules"][0], "ws-src", record)

        allowed = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "docs", "a.md"))
        self.assertEqual(self.last_record()["decision"], "allow")
        self.assertEqual(self.last_record()["rules"], ["self:docs"])
        # lib の docs/ に self:docs は効かない。どのルールも言及しない。
        self.hook("Write", self.ws, file_path=os.path.join(self.lib, "docs", "a.md"))
        self.assertNotIn("self:docs", self.last_record().get("rules", []))

    def test_notebook_edit_goes_through_the_same_union(self):
        """§25.4: NotebookEdit も書き込み系。共通層 + 行き先の層の和で、欄は notebook_path。"""
        deny = [*LIB_RULES["deny"], NOTEBOOK_RULE]
        write_layer(self.lib, rules=dict(LIB_RULES, deny=deny))

        denied = self.hook(
            "NotebookEdit",
            self.ws,
            notebook_path=os.path.join(self.lib, "notebooks", "a.ipynb"),
        )
        self.assert_denied(denied, "lib:notebook")
        record = self.last_record()
        self.assertEqual(record["rules"], ["lib:notebook"])
        self.assertEqual(record.get("source"), "lib")

        # 共通層の guard-approved も NotebookEdit を持つ。プロジェクトのツリーでも効く。
        common = self.hook(
            "NotebookEdit",
            self.ws,
            notebook_path=os.path.join(self.lib, ".claude", "ccnavi", "a.ipynb"),
        )
        self.assert_denied(common, "guard-approved")

    def test_a_project_allow_does_not_loosen_the_common_ask(self):
        """§25.4 代償: 共通層の `ask` を、プロジェクトの層の `allow` では緩められない。"""
        write(self.rules, json.dumps(dict(COMMON_RULES, ask=[ASK_VENDOR])))
        allow = [
            *LIB_RULES["allow"],
            {"id": "open-vendor", "match": "Write|Edit", "glob": "*/vendor/*"},
        ]
        write_layer(self.lib, rules=dict(LIB_RULES, allow=allow))

        result = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "vendor", "x.py"))
        self.assertEqual(self.decision(result), "ask", result.stdout + result.stderr)
        self.assertIn("ask-vendor", self.reason(result))
        record = self.last_record()
        self.assertEqual(record["rules"][0], "ask-vendor", record)
        self.assertEqual(record.get("source"), "common")
        # ワークスペースのツリーでも同じ。lib の allow はそこには足さない。
        here = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "vendor", "x.py"))
        self.assertEqual(self.decision(here), "ask", here.stdout + here.stderr)


class RootPlaceholderUnionTest(ConfigUnionHarness):
    """`{root}` の置換先はどの層でもワークスペースルート（§25.8）。"""

    def test_root_in_a_project_layer_is_the_workspace_root(self):
        """§25.8: プロジェクトの層の `{root}` も、そのプロジェクトではなくワークスペースルート。"""
        deny = [
            *LIB_RULES["deny"],
            {
                "id": "root-vendor",
                "match": "Write|Edit",
                "glob": "{root}/projects/lib/vendor/*",
                "message": "vendor はワークスペースルートから数えた綴りで止める。",
            },
        ]
        write_layer(self.lib, rules=dict(LIB_RULES, deny=deny))

        denied = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "vendor", "x.py"))
        self.assert_denied(denied, "lib:root-vendor")
        # `{root}` が lib の git プロジェクトルートに置き換わるなら、こちらが当たるはず。
        self.assert_not_denied(
            self.hook(
                "Write",
                self.ws,
                file_path=os.path.join(self.lib, "projects", "lib", "vendor", "x.py"),
            )
        )

    def test_a_copied_root_rule_is_dropped_as_a_duplicate(self):
        """§25.8: 重複の判定は置き換えた後の欄で比べる。`{root}` ごと写した定義は捨てる。"""
        write(self.rules, json.dumps(dict(COMMON_RULES, deny=[*COMMON_RULES["deny"], ROOT_RULE])))
        write_layer(self.lib, rules=dict(LIB_RULES, deny=[ROOT_RULE, *LIB_RULES["deny"]]))

        denied = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "secret.txt"))
        self.assert_denied(denied, "root-secret")
        self.assertEqual(self.last_record()["rules"], ["root-secret"])
        self.assertEqual(self.last_record().get("source"), "common")

        infos = self.problems("info", where=self.project_where("lib"))
        self.assertTrue(any("root-secret" in p["detail"] for p in infos), infos)


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

    def test_two_non_empty_project_layers_take_part_in_name_order(self):
        """§25.4: 非空のプロジェクトの層が 2 つでも両方が和に入り、並びは名前順。"""
        write_layer(self.app, rules=APP_RULES)
        write_layer(self.lib, rules=dict(LIB_RULES, deny=[*LIB_RULES["deny"], LIB_DEPLOY]))

        for cwd in (self.ws, self.lib, self.app):
            with self.subTest(cwd=os.path.basename(cwd)):
                self.assert_denied(self.hook("Bash", cwd, command="kubectl get pods"), "app:kube")
                self.assert_denied(
                    self.hook("Bash", cwd, command="psql -c 'select 1'"), "lib:raw-psql"
                )

        # 1 本の呼び出しに app と lib の両方が当たる。先に載るのは名前順で前の app。
        both = self.hook("Bash", self.ws, command="sh deploy.sh --now")
        self.assert_denied(both, "app:deploy", "lib:deploy-any")
        record = self.last_record()
        self.assertEqual(record["rules"], ["app:deploy", "lib:deploy-any"], record)
        self.assertEqual(record.get("source"), "app")


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

        where = self.project_where("lib")
        infos = self.problems("info", where=where)
        self.assertTrue(any("credentials" in p["detail"] for p in infos), infos)
        warns = self.problems("warn", where=where)
        self.assertFalse(any("credentials" in p["detail"] for p in warns), warns)

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

        warns = self.problems("warn", where=self.project_where("lib"))
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


class PostMonitoringUnionTest(ConfigUnionHarness):
    """実行後の監視も「共通層 + そのツリーの層」の和（§25.7）。

    行き先の層 1 本のままの実装では、共通層の deny の場所が保護領域に数えられない。
    プロジェクトのツリー（共通層）と、ワークスペースの作業ツリー（自身の層）の両方で見る。
    """

    def start_turn(self, cwd):
        """ターンを起こし、そのツリーの控えを取らせる。初回の実行後は控えるだけ。"""
        started = self.hook("", self.ws, event="UserPromptSubmit")
        self.assertEqual(started.returncode, 0, started.stderr)
        first = self.hook("Bash", cwd, event="PostToolUse", command="python gen.py")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

    def test_a_project_tree_is_watched_with_the_common_layer_too(self):
        """§25.7: プロジェクトのツリーでも、共通層の deny の場所が保護領域に入る。"""
        self.start_turn(self.lib)
        write(os.path.join(self.lib, ".env"), "SECRET=1\n")

        after = self.hook("Bash", self.lib, event="PostToolUse", command="python gen.py")
        self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
        self.assertIn("POST_VIOLATION", after.stderr)
        self.assertIn("credentials", after.stderr)
        self.assertIn(".env", after.stderr)
        self.assertEqual(self.last_record()["rules"], ["credentials"])

    def test_a_project_tree_is_watched_with_its_own_layer_as_well(self):
        """§25.7: 和なので、そのプロジェクトの層の deny も同じターンで並ぶ。"""
        self.start_turn(self.lib)
        write(os.path.join(self.lib, "schema", "keep.sql"), "-- dirty\n")
        write(os.path.join(self.lib, ".env"), "SECRET=1\n")

        after = self.hook("Bash", self.lib, event="PostToolUse", command="python gen.py")
        self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
        self.assertIn("lib:schema", after.stderr)
        self.assertIn("credentials", after.stderr)
        self.assertIn("tree: lib", after.stderr)

    def test_a_workspace_worktree_is_watched_with_the_own_layer(self):
        """§25.7: ワークスペースから切った作業ツリーには、共通層 + 自身の層。"""
        tree = self.worktree(self.ws, "w1")
        self.start_turn(tree)
        write(os.path.join(tree, "generated", "keep.py"), "dirty\n")

        after = self.hook("Bash", tree, event="PostToolUse", command="python gen.py")
        self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
        self.assertIn("POST_VIOLATION", after.stderr)
        self.assertIn("self:generated", after.stderr)
        self.assertIn("generated/keep.py", after.stderr)
        self.assertEqual(self.last_record()["tree"], "w1")

    def test_a_workspace_worktree_is_watched_with_the_common_layer(self):
        """§25.7: 同じ作業ツリーで、共通層の deny の場所も見る。"""
        tree = self.worktree(self.ws, "w2")
        self.start_turn(tree)
        write(os.path.join(tree, ".env"), "SECRET=1\n")

        after = self.hook("Bash", tree, event="PostToolUse", command="python gen.py")
        self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
        self.assertIn("credentials", after.stderr)


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
        errors = self.problems("error", where=self.project_where("self"))
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
        warns = self.problems("warn", where=self.project_where("app"))
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
