"""設定 3 本の和の受入テスト。rules の合成。

道具を外から動かす。一時ディレクトリにワークスペース 1 つとプロジェクト 2 つ
（lib と app）を組み、hook の payload を標準入力で渡して判定と記録を読む。

レイヤーは 3 種。

- 共通レイヤー: `.ccnavi/config/common/{rules,phases,risks}.yml`（置き場は固定）
- ワークスペース自身のレイヤー: `<ワークスペースルート>/.ccnavi/config/project/`
- プロジェクトのレイヤー: `projects/<名前>/.ccnavi/config/project/`

lib は 3 本とも持ち、app は `.ccnavi/` を持たない（無いレイヤー = 空）。

`.gitignore` は実物に合わせて 3 つだけ無視する（`projects/`、ワークツリー、記録と state の
`logs/`）。共通レイヤーの 3 本と自身のレイヤーは追跡するので、ワークスペースから切ったワークツリーに
ワークツリー側の設定ができ、ワークツリー側の設定が書けて戻らないという穴を
再現できる。

実装は入っている。ここが失敗したら、rules の合成が設計と食い違ったということ。
phases / risk の合成は test_config_union_phases_risk.py、selfguard は
test_config_union_guard.py、導入スクリプトが配る先は tests/sh/test_setup_layers.py。
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT, phasecopy, userform
from tests.inproc import run_ccnavi

# 共通レイヤー。どのツリーにも当てたい deny と、ワークスペースの allow。
COMMON_RULES = {
    "version": 1,
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
            "glob": "*/.ccnavi/approved/*",
            "message": "guard settings. ask the user.",
        },
    ],
    "allow": [
        {"id": "ws-src", "match": "Write|Edit", "glob": "*/src/*"},
        {"id": "ws-read", "match": "Read", "regex": "."},
    ],
}

# ワークスペース自身のレイヤー。ワークスペースのツリーにだけ当たる。
OWN_RULES = {
    "version": 1,
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

# lib のレイヤー。lib のツリーにだけ当たる。
LIB_RULES = {
    "version": 1,
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
  design:
    kind: work
    title: 設計
    review: mr
    scope: ["wip/design/*"]
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

# app のレイヤー。fixture では置かない（無いレイヤー = 空）。
# 2 つ目の非空のレイヤーを要るテストだけが置く。
APP_RULES = {
    "version": 1,
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

# lib 側の、app の `deploy` と同じ呼び出しに当たる deny。名前順に並ぶかを見る。
LIB_DEPLOY = {
    "id": "deploy-any",
    "match": "Bash",
    "glob": "*deploy*",
    "message": "deploy is run by a human.",
}

# 共通レイヤーの `ask`。プロジェクトのレイヤーの `allow` で緩められないことを見る。
ASK_VENDOR = {
    "id": "ask-vendor",
    "match": "Write|Edit",
    "glob": "*/vendor/*",
    "message": "vendor はユーザに確かめてから触る。",
}

# NotebookEdit を実際の `tool_name` として通すためのルール。欄は notebook_path。
NOTEBOOK_RULE = {
    "id": "notebook",
    "match": "NotebookEdit",
    "glob": "*/notebooks/*",
    "message": "ノートはユーザが回す。",
}

# `{root}` を含む定義。
# 共通レイヤーとレイヤーの両方に同じものを置いて、置換後の全欄一致を見る。
ROOT_RULE = {
    "id": "root-secret",
    "match": "Write|Edit",
    "glob": "{root}/*/secret.txt",
    "message": "secret.txt は置かない。",
}

# YAML として不正。閉じていないリスト。
BROKEN = "version: 1\ndeny: [\n"

# ccnavi ディレクトリの名前。固定。
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


FILE_NAMES = {"rules": "rules.yml", "phases": "phases.yml", "risk": "risks.yml"}


def layer_path(root, kind, home=HOME):
    """その git プロジェクトルートのレイヤーのファイル（rules / phases / risk）のパス。"""
    return os.path.join(root, home, "config", "project", FILE_NAMES[kind])


def write_layer(root, *, rules=None, phases=None, risk=None, home=HOME):
    """レイヤーの 3 本を置く。None の欄は置かない（無い = 空）。dict は JSON で書く。"""
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
    """提案の本文。親は plan を、子は parent と phase を持つ。
    project は行き先のレイヤーを決める。"""
    lines = ["---", "version: 1", f"ticket: {name}"]
    if not parent:
        lines.append("mr: 1")
    if project:
        lines.append(f"project: {project}")
    if parent:
        lines += [f"parent: {parent}", f"phase: {phase}"]
    if plan:
        # 計画の項は、すぐ前の項を待つ一直線にする（最初の項は何も待たない）。
        lines.append("plan:")
        for n, item in enumerate(plan, start=1):
            lines.append(f"  - {{type: {item}, after: [{n - 1}]}}" if n > 1 else f"  - {item}")
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

# ワークスペースの git が無視するもの。実物の .gitignore と同じ 3 つだけ。
# `/.ccnavi/` を丸ごと無視すると共通レイヤーの 3 本が追跡されず、ワークツリー側の設定ができない。
# それができないと、共通レイヤーのワークツリー側の設定が書けて戻らないという穴を
# 一度も再現できない。
GITIGNORE = "/projects/\n/.claude/worktrees/\n/logs/\n"

# 雛形のワークスペース。1 度だけ組んで、以後はコピーを配る。
#
# 組み直す形だと 1 件あたり git が 9 回（ワークスペースと 2 つのプロジェクトの
# init / add / commit）起き、この 3 ファイルの全件で 500 回を超えていた。コピーなら
# git は雛形の 9 回だけで済む。テストごとに別のディレクトリを配るのは変わらないので、
# テストどうしが状態を共有することもない（`setUpClass` にまとめる形との違いはここ）。
_TEMPLATE = ""


def build_project(root, *, rules=None, phases=None, risk=None, home=HOME):
    """git 初期化したプロジェクトを 1 つ置く。レイヤーは渡したぶんだけ。"""
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
    common = os.path.join(ws, ".ccnavi", "config", "common")
    write(os.path.join(common, "rules.yml"), json.dumps(COMMON_RULES))
    write(os.path.join(common, "risks.yml"), COMMON_RISK)
    git(ws, "add", "-A")
    git(ws, "commit", "--quiet", "-m", "init")

    projects = os.path.join(ws, "projects")
    build_project(os.path.join(projects, "lib"), rules=LIB_RULES, phases=LIB_PHASES, risk=LIB_RISK)
    build_project(os.path.join(projects, "app"))
    return ws


COMMON_RULE_FILE = ".ccnavi/config/common/rules.yml"
SELF_RULE_FILE = ".ccnavi/config/project/rules.yml"


def record_of(rule_id: str) -> str:
    """書いた表記（`x` / `self:x` / `lib:x`）を、決定ログの記録名（`<相対パス>:<id>`）にする。"""
    if rule_id.startswith("builtin:"):
        return rule_id
    layer, sep, bare = rule_id.partition(":")
    if not sep:
        return f"{COMMON_RULE_FILE}:{rule_id}"
    if layer == "self":
        return f"{SELF_RULE_FILE}:{bare}"
    return f"projects/{layer}/{SELF_RULE_FILE}:{bare}"


def layer_id(name: str) -> str:
    """記録名を、書いた表記（レイヤーの前置きつき）に戻す。この 3 つの場所以外は名前のまま。"""
    if name.startswith("builtin:"):
        return name
    path, _, bare = name.rpartition(":")
    if path == COMMON_RULE_FILE:
        return bare
    if path == SELF_RULE_FILE:
        return f"self:{bare}"
    m = re.fullmatch(r"projects/([^/]+)/" + re.escape(SELF_RULE_FILE), path)
    if m:
        return f"{m.group(1)}:{bare}"
    return name


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

        common = os.path.join(self.ws, ".ccnavi", "config", "common")
        self.rules = os.path.join(common, "rules.yml")
        self.phases = os.path.join(common, "phases.yml")
        self.risk = os.path.join(common, "risks.yml")
        # 承認済みチケットとマーカーは、そのチケットの親のツリーの `.ccnavi/approved/` に置かれる。
        # ここの土台は親のワークツリーを作らないので、提案があったツリーに置かれる。
        self.approved = os.path.join(self.ws, ".ccnavi", "approved")
        self.state = os.path.join(self.ws, "logs", "state")
        self.log = os.path.join(self.ws, "logs", "decisions.jsonl")

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
        """`.claude/worktrees/<名前>` に、owner（ワークスペースかプロジェクト）から切る。

        あれば（`propose` が親の提案を書くために切ったもの）そのまま返す。
        """
        path = os.path.join(self.ws, ".claude", "worktrees", name)
        if not os.path.isdir(path):
            git(owner, "worktree", "add", "--quiet", path, "-b", name)
        return path

    def homes(self):
        """承認済みチケットを持ちうるツリー。ワークスペース、プロジェクト、ワークツリー。"""
        found = [self.ws, self.lib, self.app]
        wt = os.path.join(self.ws, ".claude", "worktrees")
        if os.path.isdir(wt):
            found += [os.path.join(wt, n) for n in sorted(os.listdir(wt))]
        return found

    def propose(self, name, text, project=""):
        """提案を置く。親の提案は親のワークツリー（識別子と同じ名前）の `wip/proposals/`。

        親のワークツリーが無ければ、先にそのプロジェクト（ワークスペース）から切る。正とするのは
        親のワークツリーだけなので、提案も承認済みチケットもそこに置く。
        """
        base = os.path.join(self.projects, project) if project else self.ws
        match = re.search(r"^parent: (\S+)$", text, re.MULTILINE)
        owner = base
        home = self.worktree(owner, match.group(1) if match else name)
        # 計画を持つ親には、共通レイヤーとそのレイヤーの phases.yml の和から、計画が使う定義の
        # 写し（`phases:`）を足す（`ccnavi --plan-order <親> --fill-phases` と同じ）。
        # どちらにも無ければ空の写し。
        texts = []
        for path in (self.phases, layer_path(owner, "phases")):
            try:
                with open(path, encoding="utf-8") as f:
                    texts.append(f.read())
            except OSError:
                pass
        text = phasecopy.insert(text, phasecopy.combined(*texts))
        return write(os.path.join(home, "wip", "proposals", "todo", name + ".md"), text)

    def proposal_path(self, name, state=""):
        """提案（`wip/proposals/<置き場>/<名前>.md`）のパス。在る側のツリーを探す。無ければ空。"""
        for where in self.homes():
            for place in [state] if state else ["todo", "review"]:
                path = os.path.join(where, "wip", "proposals", place, name + ".md")
                if os.path.exists(path):
                    return path
        return ""

    def approved_dir_of(self, project=""):
        """このプロジェクトの承認済みチケットの置き場。"""
        base = os.path.join(self.projects, project) if project else self.ws
        return os.path.join(base, ".ccnavi", "approved")

    def approved_path(self, *parts, project=""):
        """承認済みチケットの置き場の下のパス。プロジェクトを渡さなければ、在る側を探す。"""
        for where in self.homes():
            path = os.path.join(where, ".ccnavi", "approved", *parts)
            if os.path.exists(path):
                return path
        return os.path.join(self.approved, *parts)

    def approved_copy(self, name, project=""):
        """承認済みチケットのパス。プロジェクトを渡さなければ、在る側を探す。"""
        for where in self.homes():
            path = os.path.join(where, ".ccnavi", "approved", "doing", name + ".md")
            if os.path.exists(path):
                return path
        return os.path.join(self.approved, "doing", name + ".md")

    # ---- 起動

    def ccnavi(self, *args, stdin="", env=None, guard="disable"):
        """実行ファイルを 1 回起動する。

        レイヤーの置き場はフラグで渡さない。共通レイヤーの 3 本
        （`--rules` / `--phases` / `--risk`）も、
        レイヤーを探す先の 2 本（`--projects` / `--project-home`）も、診断（`--lint` / `--test` /
        `--explain`）でだけ有効で、hook の判定とチケットのサブコマンドでは無視する。
        土台は `--root` の下の既定の置き場に置くので、渡す必要も無い。

        差し替えたいテストは `self.rules` / `self.phases` / `self.risk` に書く。
        「`projects/` を数えない」を言いたいテストは、`projects/` を動かして無くす
        （置き場は固定で、空文字で言う口は無い）。
        """
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        return run_ccnavi(
            userform.place(
                [
                    "--root",
                    self.ws,
                    "--guard-core-files",
                    guard,
                    "--restore-if-deny",
                    "disable",
                    "--guard-ticket-approval",
                    "disable",
                ],
                args,
            ),
            input=stdin,
            cwd=ROOT,
            env=environment,
        )

    def hook(self, tool, cwd, *, event="PreToolUse", session="s1", **kw):
        """hook の payload を 1 件渡す。kw のうち起動の引数は取り出し、残りは tool_input。"""
        options = {k: kw.pop(k) for k in ("env", "guard") if k in kw}
        payload = {
            "hook_event_name": event,
            "tool_name": tool,
            "cwd": cwd,
            "session_id": session,
            "tool_input": kw,
        }
        return self.ccnavi("--mode", "enable", stdin=json.dumps(payload), **options)

    def approve(self):
        """承認して、承認済みチケットを各ワークツリーにコミットする（本番は write-and-push
        が行う）。"""
        done = self.ccnavi("user", "agree", stdin="y\n")
        wt = os.path.join(self.ws, ".claude", "worktrees")
        if os.path.isdir(wt):
            for name in sorted(os.listdir(wt)):
                path = os.path.join(wt, name)
                git(path, "add", "-A")
                git(path, "commit", "--quiet", "--allow-empty", "-m", "approve")
        return done

    def lint_json(self, *args, **options):
        result = self.ccnavi("--lint", "--json", *args, **options)
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            self.fail(f"--lint --json が JSON を返さない: {exc}\n{result.stdout}\n{result.stderr}")

    def problems(self, severity, *args, where="", **options):
        """その深刻度の Problem。`where` を渡すと、その出どころで先に絞る。

        `detail` の部分一致だけで見ると、`self` や `lib` のようなありふれた語が
        別の苦情の文面に紛れていても通ってしまう。どのレイヤーの話かは `where` が持つ。
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

    def hit_rules(self, record=None):
        """記録の `rules` のうち、レイヤーのルール。組み込みの deny（`builtin:` で始まる id。
        共通レイヤーと config の有無によらず常に当たる）は数えない。"""
        record = record if record is not None else self.last_record()
        return [layer_id(name) for name in record["rules"] if not name.startswith("builtin:")]

    def assert_denied(self, result, *ids):
        self.assertEqual(self.decision(result), "deny", result.stdout + result.stderr)
        for rule_id in ids:
            self.assertIn(record_of(rule_id), self.reason(result))

    def assert_not_denied(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.decision(result), "deny", result.stdout)


class WriteUnionTest(ConfigUnionHarness):
    """Write / Edit は共通レイヤー + 行き先のレイヤーの和。"""

    def test_each_layer_deny_applies_to_its_own_files(self):
        """3 種のレイヤーの deny は、それぞれのレイヤーが対象とするファイルで当たる。

        id は共通レイヤーなら裸、プロジェクトのレイヤーなら `lib:`、
        自身のレイヤーなら `self:` が付く。
        記録の `source` はそのレイヤー、`project` は行き先のプロジェクトで、ワークスペースの
        ファイルなら持たない。cwd は行き先と別の側に置く。レイヤーを選ぶのは行き先で、cwd ではない。
        """
        for source, target, cwd, rule_id, message in (
            ("common", (self.lib, ".env"), self.ws, "credentials", "credentials are not edited"),
            ("lib", (self.lib, "schema", "x.sql"), self.ws, "lib:schema", "write a migration"),
            ("self", (self.ws, "generated", "x.py"), self.lib, "self:generated", "are rebuilt"),
        ):
            with self.subTest(source=source, rule=rule_id):
                denied = self.hook("Write", cwd, file_path=os.path.join(*target))
                self.assert_denied(denied, rule_id)
                self.assertIn(message, self.reason(denied))
                record = self.last_record()
                self.assertEqual(self.hit_rules(record), [rule_id])
                self.assertEqual(record.get("source"), source)
                if target[0] == self.lib:
                    self.assertEqual(record["project"], "lib")
                else:
                    self.assertNotIn("project", record)

    def test_a_layer_deny_does_not_reach_other_trees(self):
        """レイヤーの deny は対象の外には当たらない。

        プロジェクトのレイヤーは他のプロジェクトにもワークスペースにも、自身のレイヤーはプロジェクトにも足さない。
        """
        for rule_id, target in (
            ("lib:schema", (self.app, "schema", "x.sql")),
            ("lib:schema", (self.ws, "schema", "x.sql")),
            ("self:generated", (self.lib, "generated", "x.py")),
        ):
            with self.subTest(rule=rule_id, target=os.path.relpath(os.path.join(*target), self.ws)):
                self.assert_not_denied(self.hook("Write", self.ws, file_path=os.path.join(*target)))

    def test_a_worktree_gets_the_layers_of_the_tree_it_was_cut_from(self):
        """切ったワークツリーには、共通レイヤー + 切り元のレイヤー。
        記録の `tree` はワークツリーの名前。

        ワークスペースから切ったもの（w1）には自身のレイヤー、lib から切ったもの（i0007）
        には lib のレイヤー。
        """
        trees = {"w1": self.worktree(self.ws, "w1"), "i0007": self.worktree(self.lib, "i0007")}
        for tree, rel, rule_id in (
            ("w1", ("generated", "x.py"), "self:generated"),
            ("i0007", ("schema", "x.sql"), "lib:schema"),
            ("i0007", (".env",), "credentials"),
        ):
            with self.subTest(tree=tree, rule=rule_id):
                denied = self.hook("Write", self.ws, file_path=os.path.join(trees[tree], *rel))
                self.assert_denied(denied, rule_id)
                self.assertEqual(self.last_record()["tree"], tree)

    def test_project_allow_stays_inside_the_project(self):
        """レイヤーの allow は行き先の 1 レイヤーにしか足さない。"""
        allowed = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "src", "a.py"))
        self.assert_not_denied(allowed)
        record = self.last_record()
        self.assertEqual(record["decision"], "allow")
        # 順は 共通レイヤー → 自身のレイヤー → プロジェクトのレイヤー。
        # 先に当たるのは共通レイヤーの ws-src。
        # ここを「どちらでも良い」にすると、逆順に並べた実装でも通ってしまう。
        self.assertEqual(layer_id(record["rules"][0]), "ws-src", record)

        allowed = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "docs", "a.md"))
        self.assertEqual(self.last_record()["decision"], "allow")
        self.assertEqual([layer_id(n) for n in self.last_record()["rules"]], ["self:docs"])
        # lib の docs/ に self:docs は当たらない。どのルールも言及しない。
        self.hook("Write", self.ws, file_path=os.path.join(self.lib, "docs", "a.md"))
        self.assertNotIn("self:docs", self.last_record().get("rules", []))

    def test_notebook_edit_goes_through_the_same_union(self):
        """NotebookEdit も書き込み系。
        共通レイヤー + 行き先のレイヤーの和で、欄は notebook_path。"""
        deny = [*LIB_RULES["deny"], NOTEBOOK_RULE]
        write_layer(self.lib, rules=dict(LIB_RULES, deny=deny))

        denied = self.hook(
            "NotebookEdit",
            self.ws,
            notebook_path=os.path.join(self.lib, "notebooks", "a.ipynb"),
        )
        self.assert_denied(denied, "lib:notebook")
        record = self.last_record()
        self.assertEqual([layer_id(n) for n in record["rules"]], ["lib:notebook"])
        self.assertEqual(record.get("source"), "lib")

        # 共通レイヤーの guard-approved も NotebookEdit を持つ。プロジェクトのツリーでも当たる。
        common = self.hook(
            "NotebookEdit",
            self.ws,
            notebook_path=os.path.join(self.lib, ".ccnavi", "approved", "a.ipynb"),
        )
        self.assert_denied(common, "guard-approved")

    def test_a_project_allow_does_not_loosen_the_common_ask(self):
        """共通レイヤーの `ask` を、
        プロジェクトのレイヤーの `allow` では緩められない。"""
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
        self.assertEqual(layer_id(record["rules"][0]), "ask-vendor", record)
        self.assertEqual(record.get("source"), "common")
        # ワークスペースのツリーでも同じ。lib の allow はそこには足さない。
        here = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "vendor", "x.py"))
        self.assertEqual(self.decision(here), "ask", here.stdout + here.stderr)


class ToolLayerTest(ConfigUnionHarness):
    """パスを持つツールは行き先のレイヤー、持たないツールは全部のレイヤーの和。"""

    SECRETS = {
        "id": "secrets",
        "match": "Read|Grep|Glob",
        "glob": "*/secrets*",
        "message": "secrets は探さない。",
    }

    def test_grep_and_glob_use_the_layer_of_the_place_they_search(self):
        """Grep と Glob は Read と同じく行き先のレイヤー。探す場所を省けば cwd。"""
        write_layer(self.lib, rules=dict(LIB_RULES, deny=[*LIB_RULES["deny"], self.SECRETS]))
        secrets = os.path.join(self.lib, "secrets")

        self.assert_denied(self.hook("Read", self.ws, file_path=secrets), "lib:secrets")
        self.assert_denied(self.hook("Grep", self.ws, pattern="token", path=secrets), "lib:secrets")
        self.assert_denied(self.hook("Glob", self.ws, pattern="*", path=secrets), "lib:secrets")
        # 探す場所を省くと cwd。
        self.assert_denied(self.hook("Grep", secrets, pattern="token"), "lib:secrets")
        # 行き先のレイヤーだけなので、app の secrets には lib のルールは当たらない。
        self.assert_not_denied(
            self.hook("Grep", self.ws, pattern="token", path=os.path.join(self.app, "secrets"))
        )

    def test_tools_without_a_path_use_the_union_of_every_layer(self):
        """WebFetch・Skill・Agent は、Bash と同じく全部のレイヤーの和。cwd によらない。"""
        own = {
            "id": "fetch-internal",
            "match": "WebFetch",
            "glob": "*internal.example.com*",
            "message": "社内のページは取りに行かない。",
        }
        write_layer(self.ws, rules=dict(OWN_RULES, deny=[*OWN_RULES["deny"], own]))
        skill = {"id": "release", "match": "Skill", "glob": "release*", "message": "ユーザが回す。"}
        agent = {
            "id": "migrate",
            "match": "Agent",
            "glob": "*migrate*",
            "message": "ユーザが回す。",
        }
        write_layer(self.lib, rules=dict(LIB_RULES, deny=[*LIB_RULES["deny"], skill, agent]))

        for cwd in (self.ws, self.lib, self.app):
            with self.subTest(cwd=os.path.basename(cwd)):
                self.assert_denied(
                    self.hook("WebFetch", cwd, url="https://internal.example.com/a", prompt="read"),
                    "self:fetch-internal",
                )
                self.assert_denied(self.hook("Skill", cwd, skill="release"), "lib:release")
                self.assert_denied(
                    self.hook("Agent", cwd, description="migrate the db", prompt="go"),
                    "lib:migrate",
                )

    def test_glob_without_a_path_and_powershell_follow_the_same_split(self):
        """Glob は探す場所を省けば cwd のレイヤー。
        PowerShell は Bash と同じく全部のレイヤーの和。"""
        powershell = {
            "id": "ps-psql",
            "match": "PowerShell",
            "glob": "*psql*",
            "message": "ユーザが回す。",
        }
        deny = [*LIB_RULES["deny"], self.SECRETS, powershell]
        write_layer(self.lib, rules=dict(LIB_RULES, deny=deny))

        self.assert_denied(
            self.hook("Glob", os.path.join(self.lib, "secrets"), pattern="*"), "lib:secrets"
        )
        for cwd in (self.ws, self.lib, self.app):
            with self.subTest(cwd=os.path.basename(cwd)):
                self.assert_denied(self.hook("PowerShell", cwd, command="psql"), "lib:ps-psql")

    def test_a_layer_allow_now_reaches_grep_and_tools_without_a_path(self):
        """レイヤーをツールの種類で選ぶ形の代償。パスを持つツールは行き先のレイヤー、持たないツールは全部のレイヤーの和を足す。

        行き先のレイヤーの allow が Grep に当たり、レイヤーの allow がパスを持たないツールに当たる。
        """
        allow = [
            *LIB_RULES["allow"],
            {"id": "grep-src", "match": "Grep", "glob": "*/src*"},
            {"id": "fetch-docs", "match": "WebFetch", "glob": "*docs.example.com*"},
        ]
        write_layer(self.lib, rules=dict(LIB_RULES, allow=allow))

        self.hook("Grep", self.ws, pattern="x", path=os.path.join(self.lib, "src"))
        record = self.last_record()
        self.assertEqual(record["decision"], "allow", record)
        self.assertEqual([layer_id(n) for n in record["rules"]], ["lib:grep-src"], record)

        # lib のレイヤーの allow は、
        # app に居る WebFetch にも当たる（パスを持たないツールは全部の和）。
        self.hook("WebFetch", self.app, url="https://docs.example.com/a", prompt="read")
        record = self.last_record()
        self.assertEqual(record["decision"], "allow", record)
        self.assertEqual([layer_id(n) for n in record["rules"]], ["lib:fetch-docs"], record)


class RootPlaceholderUnionTest(ConfigUnionHarness):
    """`{root}` の置換先はどのレイヤーでもワークスペースルート。"""

    def test_root_in_a_project_layer_is_the_workspace_root(self):
        """プロジェクトのレイヤーの `{root}` も、
        そのプロジェクトではなくワークスペースルート。"""
        deny = [
            *LIB_RULES["deny"],
            {
                "id": "root-vendor",
                "match": "Write|Edit",
                "glob": "{root}/projects/lib/vendor/*",
                "message": "vendor はワークスペースルートから数えたパスで止める。",
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
        """重複の判定は置き換えた後の欄で比べる。`{root}` ごとコピーした定義は捨てる。"""
        write(self.rules, json.dumps(dict(COMMON_RULES, deny=[*COMMON_RULES["deny"], ROOT_RULE])))
        write_layer(self.lib, rules=dict(LIB_RULES, deny=[ROOT_RULE, *LIB_RULES["deny"]]))

        denied = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "secret.txt"))
        self.assert_denied(denied, "root-secret")
        self.assertEqual([layer_id(n) for n in self.last_record()["rules"]], ["root-secret"])
        self.assertEqual(self.last_record().get("source"), "common")

        infos = self.problems("info", where=self.project_where("lib"))
        self.assertTrue(any("root-secret" in p["detail"] for p in infos), infos)


class BashUnionTest(ConfigUnionHarness):
    """Bash は共通レイヤー + 自身のレイヤー + 全プロジェクトのレイヤー。"""

    def test_bash_is_the_union_of_every_layer_regardless_of_cwd(self):
        """どのレイヤーの deny も cwd によらず当たる。"""
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
        """記録の `source` は判定を下したルール（`rules` の先頭）のレイヤー。"""
        self.hook("Bash", self.app, command="dropdb prod")
        record = self.last_record()
        self.assertEqual(layer_id(record["rules"][0]), "self:dropdb")
        self.assertEqual(record.get("source"), "self")
        self.hook("Bash", self.app, command="psql")
        self.assertEqual(self.last_record().get("source"), "lib")

    def test_two_non_empty_project_layers_take_part_in_name_order(self):
        """非空のプロジェクトのレイヤーが 2 つでも両方が和に入り、順序は名前順。"""
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
        self.assertEqual(
            [layer_id(n) for n in record["rules"]], ["app:deploy", "lib:deploy-any"], record
        )
        self.assertEqual(record.get("source"), "app")


class DuplicateTest(ConfigUnionHarness):
    """同 `id` の扱い（重複は後ろを捨てる。同 id で中身が違うときは両方当たる）。"""

    def test_identical_definition_in_a_later_layer_is_dropped(self):
        """裸の id と全欄が一致する定義は後ろのレイヤーを捨てる。
        記録に 1 本、--lint は info。"""
        copied = dict(LIB_RULES)
        copied["deny"] = [COMMON_RULES["deny"][0], *LIB_RULES["deny"]]
        write_layer(self.lib, rules=copied)

        denied = self.hook("Write", self.ws, file_path=os.path.join(self.lib, ".env"))
        self.assert_denied(denied, "credentials")
        self.assertEqual(self.hit_rules(), ["credentials"])

        where = self.project_where("lib")
        infos = self.problems("info", where=where)
        self.assertTrue(any("credentials" in p["detail"] for p in infos), infos)
        warns = self.problems("warn", where=where)
        self.assertFalse(any("credentials" in p["detail"] for p in warns), warns)

    def test_same_id_with_different_content_keeps_both(self):
        """同 id で中身が違う rules は両方当たる。記録に両方が並び、--lint は warn。"""
        differs = dict(LIB_RULES)
        differs["deny"] = [
            dict(COMMON_RULES["deny"][0], glob="*/secrets/*"),
            *LIB_RULES["deny"],
        ]
        write_layer(self.lib, rules=differs)

        denied = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "secrets", ".env"))
        self.assert_denied(denied, "credentials", "lib:credentials")
        self.assertEqual(sorted(self.hit_rules()), ["credentials", "lib:credentials"])

        warns = self.problems("warn", where=self.project_where("lib"))
        self.assertTrue(any("credentials" in p["detail"] for p in warns), warns)

    def test_bash_union_drops_identical_duplicates_too(self):
        """Bash の和でも同じ。共通レイヤーの `terraform` をコピーした lib の定義は 1
        本にまとまる。"""
        copied = dict(LIB_RULES)
        copied["deny"] = [*LIB_RULES["deny"], COMMON_RULES["deny"][1]]
        write_layer(self.lib, rules=copied)

        self.assert_denied(self.hook("Bash", self.lib, command="terraform apply"), "terraform")
        self.assertEqual([layer_id(n) for n in self.last_record()["rules"]], ["terraform"])


class LayerFailureTest(ConfigUnionHarness):
    """レイヤーが無い・不正・共通レイヤー自身が不正、の 3 つ。"""

    def test_missing_layer_is_empty_without_fallback(self):
        """レイヤーのファイルが無い = 空。fallback は付かず、--lint も言わない。"""
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
        """不正なレイヤーは空 + 記録の `fallback` にレイヤーの名前。
        組み込みには戻らない。"""
        write(layer_path(self.lib, "rules"), BROKEN)

        passed = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "schema", "x.sql"))
        self.assert_not_denied(passed)
        record = self.last_record()
        self.assertEqual(record.get("fallback"), "lib", record)
        self.assertNotEqual(record.get("fallback"), "builtin:rules")
        self.assertNotIn("built-in defaults", self.reason(passed))

    def test_broken_layer_keeps_the_common_deny(self):
        """不正なレイヤーの上でも共通レイヤーの deny は当たったまま。"""
        write(layer_path(self.lib, "rules"), BROKEN)

        denied = self.hook("Write", self.ws, file_path=os.path.join(self.lib, ".env"))
        self.assert_denied(denied, "credentials")
        self.assertEqual(self.last_record().get("fallback"), "lib")

    def test_broken_layer_is_a_lint_error(self):
        """レイヤーのファイルが不正（空として扱っている）なら --lint の error。"""
        write(layer_path(self.lib, "rules"), BROKEN)

        errors = self.problems("error")
        self.assertTrue(any(p["where"].startswith("(projects/lib)") for p in errors), errors)

    def test_broken_layer_drops_out_of_the_bash_union(self):
        """Bash の和からも不正なレイヤーは外れ、記録がそれを言う。"""
        write(layer_path(self.lib, "rules"), BROKEN)

        self.assert_not_denied(self.hook("Bash", self.lib, command="psql"))
        self.assert_denied(self.hook("Bash", self.lib, command="dropdb prod"), "self:dropdb")
        self.assertIn("lib", self.last_record().get("detail", ""))

    def test_broken_common_layer_falls_back_to_builtin_as_before(self):
        """共通レイヤー自身が読めないときは今どおり組み込みの既定。
        レイヤーは足さない。"""
        write(self.rules, BROKEN)

        passed = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "schema", "x.sql"))
        self.assert_not_denied(passed)
        record = self.last_record()
        self.assertEqual(record.get("fallback"), "builtin:rules")
        self.assertIn("built-in defaults", self.reason(passed))
        self.assertNotIn("lib:schema", self.reason(passed))


class ProblemsSaidOnceTest(ConfigUnionHarness):
    """ルールの苦情は、1 回の起動で 1 度だけ出す。

    hook はツリーのレイヤーごとに共通レイヤーを読み直し、
    `--lint` はレイヤーのファイルを 2 つの経路で読む。
    読むたびに言うと、同じ苦情がレイヤーやツリーの数だけ並ぶ。
    """

    SILENT = {"id": "silent", "match": "Bash", "glob": "*nothing matches*"}

    def test_hooks_say_a_common_layer_problem_once(self):
        write(self.rules, json.dumps({"version": 1, "deny": [self.SILENT]}))
        for event in ("UserPromptSubmit", "Stop"):
            with self.subTest(event=event):
                done = self.hook("", self.ws, event=event)
                self.assertEqual(done.stderr.count("deny:silent"), 1, done.stderr)

    def test_lint_counts_a_layer_problem_once(self):
        write_layer(self.lib, rules={"version": 1, "deny": [self.SILENT]})

        said = [
            (p["where"], p["detail"])
            for p in self.lint_json()["problems"]
            if p["where"].startswith(self.project_where("lib")) and "silent" in p["where"]
        ]
        self.assertTrue(said, "レイヤーの苦情が出ていない")
        self.assertEqual(len(said), len(set(said)), said)


class PostMonitoringUnionTest(ConfigUnionHarness):
    """実行後チェックも「共通レイヤー + そのツリーのレイヤー」の和。

    行き先のレイヤー 1 本のままの実装では、共通レイヤーの deny の場所が保護領域に数えられない。
    プロジェクトのツリー（共通レイヤー）と、ワークスペースのワークツリー（自身のレイヤー）の両方で見る。
    """

    def start_turn(self, cwd):
        """ターンを起こし、そのツリーの記録を取らせる。初回の実行後は記録するだけ。"""
        started = self.hook("", self.ws, event="UserPromptSubmit")
        self.assertEqual(started.returncode, 0, started.stderr)
        first = self.hook("Bash", cwd, event="PostToolUse", command="python gen.py")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

    def test_a_project_tree_is_watched_with_the_common_layer_too(self):
        """プロジェクトのツリーでも、共通レイヤーの deny の場所が保護領域に入る。"""
        self.start_turn(self.lib)
        write(os.path.join(self.lib, ".env"), "SECRET=1\n")

        after = self.hook("Bash", self.lib, event="PostToolUse", command="python gen.py")
        self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
        self.assertIn("POST_VIOLATION", after.stderr)
        self.assertIn("credentials", after.stderr)
        self.assertIn(".env", after.stderr)
        self.assertEqual(self.hit_rules(), ["credentials"])

    def test_a_project_tree_is_watched_with_its_own_layer_as_well(self):
        """和なので、そのプロジェクトのレイヤーの deny も同じターンで並ぶ。"""
        self.start_turn(self.lib)
        write(os.path.join(self.lib, "schema", "keep.sql"), "-- dirty\n")
        write(os.path.join(self.lib, ".env"), "SECRET=1\n")

        after = self.hook("Bash", self.lib, event="PostToolUse", command="python gen.py")
        self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
        self.assertIn("projects/lib/.ccnavi/config/project/rules.yml:schema", after.stderr)
        self.assertIn("credentials", after.stderr)
        self.assertIn("tree: lib", after.stderr)

    def test_a_workspace_worktree_is_watched_with_the_own_layer(self):
        """ワークスペースから切ったワークツリーには、共通レイヤー + 自身のレイヤー。"""
        tree = self.worktree(self.ws, "w1")
        self.start_turn(tree)
        write(os.path.join(tree, "generated", "keep.py"), "dirty\n")

        after = self.hook("Bash", tree, event="PostToolUse", command="python gen.py")
        self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
        self.assertIn("POST_VIOLATION", after.stderr)
        self.assertIn(".ccnavi/config/project/rules.yml:generated", after.stderr)
        self.assertIn("generated/keep.py", after.stderr)
        self.assertEqual(self.last_record()["tree"], "w1")

    def test_a_workspace_worktree_is_watched_with_the_common_layer(self):
        """同じワークツリーで、共通レイヤーの deny の場所も見る。"""
        tree = self.worktree(self.ws, "w2")
        self.start_turn(tree)
        write(os.path.join(tree, ".env"), "SECRET=1\n")

        after = self.hook("Bash", tree, event="PostToolUse", command="python gen.py")
        self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
        self.assertIn("credentials", after.stderr)


class WiringTest(ConfigUnionHarness):
    """置き場と環境変数。"""

    def test_project_named_self_is_not_counted(self):
        """`projects/self/` は `self:id` と区別できないので数えず、--lint が error。"""
        deny = {
            "version": 1,
            "deny": [{"id": "dump", "match": "Bash", "glob": "*mysqldump*", "message": "no."}],
        }
        self.project("self", rules=deny)

        self.assert_not_denied(self.hook("Bash", self.ws, command="mysqldump db"))
        errors = self.problems("error", where=self.project_where("self"))
        self.assertTrue(any("self" in p["detail"] for p in errors), errors)

    def test_workspace_without_projects_or_own_layer_is_unchanged(self):
        """`projects/` が無く自身のレイヤーも無ければ、
        共通レイヤーだけで判定し記録する。"""
        # 自身のレイヤーだけを消す。
        # 共通レイヤーも同じ ccnavi ディレクトリの下（`.ccnavi/config/common/`）にある。
        shutil.rmtree(os.path.join(self.ws, HOME, "config", "project"))
        # `projects/` を作らないワークスペースにする（置き場は固定で、数えない口は無い）。消さずに
        # 外へ動かす。Windows は .git の中の読み取り専用のファイルを消せない。
        os.rename(self.projects, os.path.join(self.ws, "moved-away"))

        allowed = self.hook("Write", self.ws, file_path=os.path.join(self.ws, "src", "a.py"))
        self.assert_not_denied(allowed)
        record = self.last_record()
        self.assertEqual(record["decision"], "allow")
        self.assertEqual([layer_id(n) for n in record["rules"]], ["ws-src"])
        self.assertNotIn("fallback", record)
        self.assertNotIn("project", record)

        denied = self.hook("Write", self.ws, file_path=os.path.join(self.ws, ".env"))
        self.assert_denied(denied, "credentials")
        self.assertEqual(self.hit_rules(), ["credentials"])
        self.assert_not_denied(self.hook("Bash", self.ws, command="psql"))


class ExplainTest(ConfigUnionHarness):
    """`--explain` はレイヤーごとに全件。"""

    def test_explain_lists_rules_per_layer_and_the_phase_and_risk_tables(self):
        """rules を共通レイヤー・自身のレイヤー・各プロジェクトの順に、
        phases と risk の表を足す。"""
        result = self.ccnavi("--explain")
        out = result.stdout
        self.assertEqual(result.returncode, 0, result.stderr)
        for heading in (
            "■ rules 共通レイヤー",
            "■ rules ワークスペースのレイヤー",
            "■ rules lib",
            "■ phases",
            "■ risk",
        ):
            self.assertIn(heading, out)
        self.assertLess(
            out.index("■ rules 共通レイヤー"), out.index("■ rules ワークスペースのレイヤー")
        )
        self.assertLess(out.index("■ rules ワークスペースのレイヤー"), out.index("■ rules lib"))
        self.assertIn("self:generated", out)
        self.assertIn("lib:schema", out)
        # phases と risk は id と出どころのレイヤー。
        self.assertIn("build", out)
        self.assertIn("big-diff", out)
        self.assertIn("schema", out)

    def test_explain_shows_a_broken_layer_as_unreadable_and_empty(self):
        """読めないレイヤーはその位置に「読めない」と、空として扱っていることを出す。"""
        write(layer_path(self.lib, "rules"), BROKEN)
        out = self.ccnavi("--explain").stdout
        self.assertIn("■ rules lib", out)
        self.assertIn("読めない", out)

    def test_explain_json_carries_every_layer(self):
        """`--explain --json` にレイヤーごとの rules 全件と phases / risk
        の定義と出どころが出る。

        形は README「ボードの JSON」に足す。ここでは `layers` のリストに `name`（common / self /
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
        # この土台の共通レイヤーは phases.yml を持たないので、共通レイヤーの定義は空。
        self.assertEqual(layers["common"]["phases"], [])
        self.assertIn("build", ids(layers["lib"]["phases"]))
        self.assertIn("big-diff", ids(layers["common"]["risk"]["factors"]))
        self.assertIn("schema", ids(layers["lib"]["risk"]["factors"]))


if __name__ == "__main__":
    unittest.main()
