"""設定 3 本の和を見るテストの土台（ConfigUnionHarness と見本の設定）。

テストの本体は Go に移した（go/internal/clitest/config/union）。この土台は、まだ Python のままの
tests/config/ の残りのテストが継ぐので残してある。それらを移したら、この土台ごと消す。

一時ディレクトリにワークスペース 1 つとプロジェクト 2 つ（lib と app）を組み、hook の payload を
標準入力で渡して判定と記録を読む。lib は 3 本とも持ち、app は `.ccnavi/` を持たない
（無いレイヤー = 空）。
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

# 共通レイヤー。どのツリーにも適用したい deny と、ワークスペースの allow。
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

# ワークスペース自身のレイヤー。ワークスペースのツリーにだけヒットする。
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

# lib のレイヤー。lib のツリーにだけヒットする。
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
    review: true
    scope: ["wip/design/*"]
"""

OWN_PHASES = """\
version: 1
phases:
  docs:
    kind: work
    title: 文書
    review: false
    scope: ["docs/*"]
"""

LIB_PHASES = """\
version: 1
phases:
  design:
    kind: work
    title: 設計
    review: true
    scope: ["wip/design/*"]
  build:
    kind: work
    title: ビルド
    review: false
    scope: ["src/*"]
  release:
    kind: work
    title: リリース
    review: true
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
        lines += ["mr: 1", "issue: 1"]
    if project:
        lines.append(f"project: {project}")
    if parent:
        lines += [f"parent: {parent}", f"phase: {phase}"]
    if plan:
        # 計画のステップは、すぐ前のステップが先行する一直線にする（最初のステップは先行なし）。
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

    def template(self):
        """写す雛形の置き場。継いだ道具は、雛形に手を足した形を返してよい（1 回だけ組んで写す）。"""
        global _TEMPLATE
        if not _TEMPLATE:
            _TEMPLATE = build_template()
        return _TEMPLATE

    def setUp(self):
        source = self.template()
        base = tempfile.mkdtemp(prefix="ccnavi-union-")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.ws = os.path.join(base, "ws")
        shutil.copytree(source, self.ws)

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

        あれば（`approval-request` が親の提案を書くために切ったもの）そのまま返す。
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
        # コピー（`phases:`）を足す（`ccnavi --plan-order <親> --fill-phases` と同じ）。
        # どちらにも無ければ空のコピー。
        texts = []
        for path in (self.phases, layer_path(owner, "phases")):
            try:
                with open(path, encoding="utf-8") as f:
                    texts.append(f.read())
            except OSError:
                pass
        text = phasecopy.insert(text, phasecopy.combined(*texts))
        return write(os.path.join(home, "wip", "proposals", "todo", name + ".md"), text)

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
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
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
        共通レイヤーと config の有無によらず常にヒットする）は数えない。"""
        record = record if record is not None else self.last_record()
        return [layer_id(name) for name in record["rules"] if not name.startswith("builtin:")]

    def assert_denied(self, result, *ids):
        self.assertEqual(self.decision(result), "deny", result.stdout + result.stderr)
        for rule_id in ids:
            self.assertIn(record_of(rule_id), self.reason(result))

    def assert_not_denied(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.decision(result), "deny", result.stdout)
