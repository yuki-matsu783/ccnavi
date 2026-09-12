"""複数のリポジトリ（REQ-MLT）の受入テスト。道具を外から叩いて応答だけを見る。

ワークスペース 1 つとプロジェクト 2 つ（app と lib）を一時ディレクトリに作る。
ワークスペースは Claude Code を起動した場所で、自分の git を持つ。プロジェクトは
`projects/` の直下に clone した別のリポジトリで、それぞれ `config/rules.yml` を持つ。

見るのは 5 つ。

1. パスを持つツールは行き先のプロジェクトのルールで判定される。ワークスペースへの
   書き込みはワークスペースのルール
2. Bash はワークスペースと全プロジェクトのルールの和で判定され、cwd がどこでも同じ
3. 読めないプロジェクトのルールは、書き込みなら組み込みの既定、Bash なら和から外れる
4. プロジェクトから切った作業ツリーが認識され、切り元とチケットの `project:` が
   食い違えば止まる
5. `projects/` を数えない設定では、この機能が入る前と同じに動く
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

WS_RULES = {
    "version": 3,
    "deny": [
        {
            "id": "ws-rm",
            "match": "Bash",
            "glob": "*rm -rf *",
            "message": "recursive delete is not run by the agent.",
        },
        {
            "id": "guard-approved",
            "match": "Write|Edit|MultiEdit|NotebookEdit",
            "glob": "*/.claude/ccnavi/*",
            "message": "guard settings. ask the user.",
        },
    ],
    "allow": [
        {"id": "ws-src", "match": "Write|Edit|MultiEdit", "glob": "*/src/*"},
        {"id": "ws-read", "match": "Read", "regex": "."},
    ],
}

APP_RULES = {
    "version": 3,
    "deny": [
        {
            "id": "schema",
            "match": "Write|Edit|MultiEdit",
            "glob": "*/schema/*",
            "message": "schema is not edited by hand. write a migration.",
        }
    ],
    "allow": [
        {"id": "source", "match": "Write|Edit|MultiEdit", "glob": "*/src/*"},
        {"id": "npm", "match": "Bash", "regex": r"\bnpm test\b"},
    ],
}

LIB_RULES = {
    "version": 3,
    "deny": [
        {
            "id": "raw-psql",
            "match": "Bash",
            "glob": "*psql*",
            "message": "psql is not run by the agent.",
        }
    ],
    "allow": [{"id": "source", "match": "Write|Edit|MultiEdit", "glob": "*/src/*"}],
}


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


def ticket_text(name, *, project="", allow=()):
    lines = ["---", "version: 1", f"ticket: {name}"]
    if project:
        lines.append(f"project: {project}")
    lines += [
        "human_review:",
        "  required: false",
        "  reason: テスト",
        "title: 作業",
        "rationale: |",
        "  理由",
    ]
    if allow:
        lines.append("allow:")
        for g in allow:
            lines += ["  - match: Write|Edit|MultiEdit", f'    glob: "{g}"']
    lines += ['started_at: ""', 'completed_at: ""', 'base_sha: ""', "---", "", "本文"]
    return "\n".join(lines) + "\n"


class ProjectsTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-projects-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        git(self.ws, "init", "--quiet", "-b", "main")
        write(os.path.join(self.ws, ".gitignore"), "/projects/\n/.claude/\n")
        write(os.path.join(self.ws, "src", "keep.py"), "print(1)\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "--quiet", "-m", "init")

        self.rules = write(os.path.join(self.ws, "rules.yml"), json.dumps(WS_RULES))
        self.projects = os.path.join(self.ws, "projects")
        self.app = self.project("app", APP_RULES)
        self.lib = self.project("lib", LIB_RULES)
        self.approved = os.path.join(self.ws, ".claude", "ccnavi", "tickets")
        self.state = os.path.join(self.ws, "state")
        self.log = os.path.join(self.ws, "log.jsonl")

    # ---- 道具

    def project(self, name, rules):
        root = os.path.join(self.projects, name)
        os.makedirs(root)
        git(root, "init", "--quiet", "-b", "main")
        write(os.path.join(root, "config", "rules.yml"), json.dumps(rules))
        write(os.path.join(root, "src", "keep.py"), "print(1)\n")
        write(os.path.join(root, "schema", "keep.sql"), "-- keep\n")
        git(root, "add", "-A")
        git(root, "commit", "--quiet", "-m", "init")
        return root

    def worktree(self, owner, name):
        path = os.path.join(self.ws, ".claude", "worktrees", name)
        git(owner, "worktree", "add", "--quiet", path, "-b", name)
        return path

    def ccnavi(self, *args, stdin="", projects=None):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        return run_ccnavi(
            [
                "--root",
                self.ws,
                "--rules",
                self.rules,
                "--projects",
                self.projects if projects is None else projects,
                "--approved",
                self.approved,
                "--state",
                self.state,
                "--log",
                self.log,
                "--guard-core-files",
                "disable",
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

    def hook(self, tool, cwd, projects=None, event="PreToolUse", **tool_input):
        payload = {
            "hook_event_name": event,
            "tool_name": tool,
            "cwd": cwd,
            "session_id": "s1",
            "tool_input": tool_input,
        }
        return self.ccnavi("--mode", "enable", stdin=json.dumps(payload), projects=projects)

    def system_message(self, result):
        if not result.stdout.strip():
            return ""
        return json.loads(result.stdout).get("systemMessage", "")

    def reason(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecisionReason") or out.get("additionalContext") or ""

    def decision(self, result):
        """応答の判定。拒否は終了コードではなく応答の欄で返る。"""
        if not result.stdout.strip():
            return ""
        return json.loads(result.stdout).get("hookSpecificOutput", {}).get("permissionDecision", "")

    def last_record(self):
        with open(self.log, encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
        return json.loads(lines[-1])

    # ---- 1. パスを持つツールは行き先のプロジェクトのルール

    def test_writes_are_judged_by_the_rules_of_the_project_they_land_in(self):
        denied = self.hook("Write", self.ws, file_path=os.path.join(self.app, "schema", "x.sql"))
        self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
        self.assertIn("app:schema", self.reason(denied))
        self.assertIn("write a migration", self.reason(denied))
        record = self.last_record()
        self.assertEqual(record["project"], "app")
        self.assertEqual(record["tree"], "app")
        self.assertEqual(record["rules"], ["app:schema"])

        # lib に schema の deny は無い。app の deny は lib には届かない。
        passed = self.hook("Write", self.ws, file_path=os.path.join(self.lib, "schema", "x.sql"))
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertNotIn("DENY", self.reason(passed))
        self.assertEqual(self.last_record()["project"], "lib")

        allowed = self.hook("Write", self.app, file_path=os.path.join(self.lib, "src", "a.py"))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(self.last_record()["decision"], "allow")

    def test_writes_into_the_workspace_use_the_workspace_rules(self):
        allowed = self.hook("Write", self.app, file_path=os.path.join(self.ws, "src", "a.py"))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        record = self.last_record()
        self.assertEqual(record["decision"], "allow")
        self.assertEqual(record["rules"], ["ws-src"])
        self.assertNotIn("project", record)

        # ワークスペースの schema/ は誰も守っていない。app の deny は漏れない。
        passed = self.hook("Write", self.app, file_path=os.path.join(self.ws, "schema", "x.sql"))
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertNotIn("DENY", self.reason(passed))

    # ---- 2. Bash は全部の和

    def test_bash_is_judged_by_the_union_regardless_of_cwd(self):
        for cwd in (self.ws, self.app, self.lib):
            denied = self.hook("Bash", cwd, command="psql -c 'select 1'")
            self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
            self.assertIn("lib:raw-psql", self.reason(denied))
            denied = self.hook("Bash", cwd, command="rm -rf build")
            self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
            self.assertIn("ws-rm", self.reason(denied))
        # app だけが許した npm test は lib に居ても通る。和の代償で、意図した挙動。
        allowed = self.hook("Bash", self.lib, command="npm test")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        record = self.last_record()
        self.assertEqual(record["decision"], "allow")
        self.assertEqual(record["rules"], ["app:npm"])
        self.assertEqual(record["project"], "lib")

    # ---- 3. 読めないプロジェクトのルール

    def test_unreadable_project_rules_fall_back_for_writes_and_drop_out_of_the_union(self):
        write(os.path.join(self.app, "config", "rules.yml"), "version: 3\ndeny: [\n")
        passed = self.hook("Write", self.ws, file_path=os.path.join(self.app, "schema", "x.sql"))
        self.assertEqual(passed.returncode, 0, passed.stderr)
        record = self.last_record()
        self.assertTrue(record.get("fallback"), record)
        self.assertIn("app", record["detail"])
        self.assertIn("built-in defaults", self.reason(passed))

        denied = self.hook("Bash", self.app, command="psql")
        self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
        self.assertIn("lib:raw-psql", self.reason(denied))
        record = self.last_record()
        self.assertNotIn("fallback", record)
        self.assertIn("unreadable project rules: app", record["detail"])

    # ---- 4. プロジェクトから切った作業ツリー

    def test_worktree_cut_from_a_project_is_judged_by_that_project(self):
        tree = self.worktree(self.app, "i0007")
        denied = self.hook("Write", self.ws, file_path=os.path.join(tree, "schema", "x.sql"))
        self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
        self.assertIn("app:schema", self.reason(denied))
        record = self.last_record()
        self.assertEqual(record["tree"], "i0007")
        self.assertEqual(record["project"], "app")

    def test_worktree_cut_from_the_wrong_project_is_refused_by_the_ticket(self):
        write(
            os.path.join(self.ws, "wip", "tickets", "todo", "i0007.md"),
            ticket_text("i0007", project="lib", allow=("src/*",)),
        )
        write(
            os.path.join(self.ws, "wip", "tickets", "todo", "i0008.md"),
            ticket_text("i0008", project="lib", allow=("src/*",)),
        )
        approved = self.ccnavi("--approve", stdin="y\n")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

        wrong = self.worktree(self.app, "i0007")
        denied = self.hook("Write", self.ws, file_path=os.path.join(wrong, "src", "a.py"))
        self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
        self.assertIn("DENY_TICKET_PROJECT_MISMATCH", self.reason(denied))
        self.assertIn("'lib'", self.reason(denied))
        self.assertIn("'app'", self.reason(denied))
        self.assertEqual(self.last_record()["code"], "DENY_TICKET_PROJECT_MISMATCH")

        right = self.worktree(self.lib, "i0008")
        allowed = self.hook("Write", self.ws, file_path=os.path.join(right, "src", "a.py"))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertNotIn("DENY", self.reason(allowed))
        outside = self.hook("Write", self.ws, file_path=os.path.join(right, "docs", "a.md"))
        self.assertIn("DENY_TICKET_SCOPE", self.reason(outside))

    # ---- 5. 実行後の監視はツリーごと

    def test_post_monitoring_reads_the_project_tree_the_call_touched(self):
        started = self.hook("", self.ws, event="UserPromptSubmit")
        self.assertEqual(started.returncode, 0, started.stderr)
        # 初回の実行後は控えを取るだけ。そのあとで app の schema/ をシェルが汚す。
        first = self.hook("Bash", self.app, event="PostToolUse", command="python gen.py")
        self.assertEqual(first.returncode, 0, first.stderr)
        write(os.path.join(self.app, "schema", "x.sql"), "dirty\n")

        # ワークスペースに居る Bash の後では app のツリーを見ない。
        elsewhere = self.hook("Bash", self.ws, event="PostToolUse", command="python gen.py")
        self.assertEqual(elsewhere.returncode, 0, elsewhere.stderr)
        self.assertNotIn("POST_VIOLATION", self.reason(elsewhere))

        # app に居る Bash の後では、app のルールで app のツリーを見る。
        after = self.hook("Bash", self.app, event="PostToolUse", command="python gen.py")
        self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
        self.assertIn("POST_VIOLATION", after.stderr)
        self.assertIn("app:schema", after.stderr)
        self.assertIn("tree: app", after.stderr)
        record = self.last_record()
        self.assertEqual(record["project"], "app")
        self.assertEqual(record["rules"], ["app:schema"])

        # ターンの終わりは全部のツリーを見て、ツリーの名前を添えて人に言う。
        stopped = self.hook("", self.ws, event="Stop")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertIn("app: schema/x.sql", self.system_message(stopped))

    # ---- 6. --lint がプロジェクトの配線を言う

    def test_lint_names_project_wiring_problems(self):
        write(os.path.join(self.app, "config", "rules.yml"), "version: 3\ndeny: [\n")
        os.makedirs(os.path.join(self.lib, ".claude"))
        write(os.path.join(self.ws, ".gitignore"), "/.claude/\n")
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "--quiet", "-m", "stop ignoring projects")

        result = self.ccnavi("--lint")
        out = result.stdout + result.stderr
        self.assertIn("projects/ がワークスペースの git で無視されていない", out)
        self.assertIn("(projects/app)", out)
        self.assertIn("(projects/lib)", out)
        self.assertIn(".claude/ を持つ", out)

        explained = self.ccnavi("--explain")
        self.assertIn("■ プロジェクト（2 件", explained.stdout)
        self.assertIn("読めない", explained.stdout)

    # ---- 7. projects/ を数えない設定では前と同じ

    def test_without_a_projects_dir_everything_is_judged_by_the_workspace_rules(self):
        passed = self.hook(
            "Write", self.ws, projects="", file_path=os.path.join(self.app, "schema", "x.sql")
        )
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertNotIn("DENY", self.reason(passed))
        record = self.last_record()
        self.assertNotIn("project", record)

        passed = self.hook("Bash", self.app, projects="", command="psql")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertNotIn("DENY", self.reason(passed))


if __name__ == "__main__":
    unittest.main()
