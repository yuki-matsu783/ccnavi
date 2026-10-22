"""複数のリポジトリの受入テスト。道具を外から呼んで応答だけを見る。

ワークスペース 1 つとプロジェクト 2 つ（app と lib）を一時ディレクトリに作る。
ワークスペースは Claude Code を起動した場所で、自分の git を持つ。プロジェクトは
`projects/` の直下に clone した別のリポジトリで、それぞれレイヤーの 3 本の置き場
（`.ccnavi/config/project/`）に rules.yml を持つ。

見るのは 5 つ。

1. パスを持つツールは共通レイヤー + 行き先のプロジェクトのレイヤーで判定される。ワークスペースへの
   書き込みは共通レイヤー + 自身のレイヤー
2. Bash は共通レイヤーと全部のレイヤーの和で判定され、cwd がどこでも同じ
3. 読めないレイヤーは空として扱われ、記録がレイヤーの名前を残す。組み込みの既定へは戻らない
4. プロジェクトから切ったワークツリーが認識され、元リポジトリとチケットの `project:` が
   食い違えば止まる
5. `projects/` を数えない設定では、この機能が入る前と同じに動く

レイヤーの和そのもの（重複の排除、同 id、`--explain`）は tests/config/test_config_union.py が見る。
ここが見るのは、置き場とツリーの結び付きが今までどおり合っていること。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT, common_path, userform
from tests.inproc import run_ccnavi
from tests.ticketsample import plain_parent, write_plain_phases

WS_RULES = {
    "version": 1,
    "deny": [
        {
            "id": "ws-rm",
            "match": "Bash",
            "glob": "*rm -rf *",
            "message": "recursive delete is not run by the agent.",
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

APP_RULES = {
    "version": 1,
    "deny": [
        {
            "id": "schema",
            "match": "Write|Edit",
            "glob": "*/schema/*",
            "message": "schema is not edited by hand. write a migration.",
        }
    ],
    "allow": [
        {"id": "source", "match": "Write|Edit", "glob": "*/src/*"},
        {"id": "npm", "match": "Bash", "regex": r"\bnpm test\b"},
    ],
}

LIB_RULES = {
    "version": 1,
    "deny": [
        {
            "id": "raw-psql",
            "match": "Bash",
            "glob": "*psql*",
            "message": "psql is not run by the agent.",
        }
    ],
    "allow": [{"id": "source", "match": "Write|Edit", "glob": "*/src/*"}],
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


# レイヤーの 3 本の置き場（既定の ccnavi ディレクトリ）。
LAYER = os.path.join(".ccnavi", "config", "project")


def layer_rules(root):
    """その git プロジェクトルートのレイヤーのルールファイル。"""
    return os.path.join(root, LAYER, "rules.yml")


def ticket_text(name, *, parent="", project="", allow=(), plan=0):
    lines = ["---", "version: 1", f"ticket: {name}"]
    if not parent:
        lines += ["mr: 1", "issue: 1"]
    if parent:
        lines += [f"parent: {parent}", "phase: 1"]
    if project:
        lines.append(f"project: {project}")
    lines += [
        "human_review:",
        "  required: false",
        "  reason: テスト",
        f"title: 子 {name}" if parent else "title: 作業",
        "rationale: |",
        "  理由",
    ]
    if allow:
        lines.append("allow:")
        for g in allow:
            lines += ["  - match: Write|Edit", f'    glob: "{g}"']
    lines += ['started_at: ""', 'completed_at: ""', 'base_sha: ""', "---", "", "本文"]
    return plain_parent("\n".join(lines) + "\n", plan)


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

        # 共通レイヤーは既定の置き場へ。`--rules` は診断でだけ有効。
        self.rules = write(common_path(self.ws, "rules"), json.dumps(WS_RULES))
        self.projects = os.path.join(self.ws, "projects")
        self.app = self.project("app", APP_RULES)
        self.lib = self.project("lib", LIB_RULES)
        # 承認済みチケットは、そのチケットの親のツリーの `.ccnavi/approved/` に置かれる。
        # ここの環境は親のワークツリーを作らないので、提案があったツリーに置かれる。
        self.approved = os.path.join(self.ws, ".ccnavi", "approved")
        self.state = os.path.join(self.ws, "logs", "state")
        self.log = os.path.join(self.ws, "logs", "decisions.jsonl")

    def approved_path(self, *parts):
        """承認済みチケットの置き場の下のパス。どのツリーに置かれたかを探す。"""
        homes = [self.ws, self.lib, self.app]
        wt = os.path.join(self.ws, ".claude", "worktrees")
        if os.path.isdir(wt):
            homes += [os.path.join(wt, n) for n in sorted(os.listdir(wt))]
        for where in homes:
            path = os.path.join(where, ".ccnavi", "approved", *parts)
            if os.path.exists(path):
                return path
        return os.path.join(self.approved, *parts)

    # ---- 道具

    def project(self, name, rules):
        root = os.path.join(self.projects, name)
        os.makedirs(root)
        git(root, "init", "--quiet", "-b", "main")
        write(layer_rules(root), json.dumps(rules))
        write(os.path.join(root, "src", "keep.py"), "print(1)\n")
        write(os.path.join(root, "schema", "keep.sql"), "-- keep\n")
        git(root, "add", "-A")
        git(root, "commit", "--quiet", "-m", "init")
        return root

    def worktree(self, owner, name):
        path = os.path.join(self.ws, ".claude", "worktrees", name)
        if not os.path.isdir(path):
            git(owner, "worktree", "add", "--quiet", path, "-b", name)
        return path

    def proposal_file(self, owner, name):
        """提案を置くパス。親の提案は親のワークツリー（識別子と同名。無ければ切る）の中。"""
        family = re.sub(r"-\d\d-\d\d$", "", name)
        home = self.worktree(owner, family)
        return os.path.join(home, "wip", "proposals", "todo", name + ".md")

    def ccnavi(self, *args, stdin="", env=None):
        """実行ファイルを 1 回起動する。

        `--projects` は渡さない。レイヤーを探す先を動かすフラグは診断でだけ有効な
        ので、置き場は `--root` の下の既定のまま。「`projects/` を
        数えない」は `projects/` を作らないワークスペースで言う（置き場は固定で、空文字の口は
        無い）。
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
                    "disable",
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

    def hook(self, tool, cwd, env=None, event="PreToolUse", **tool_input):
        payload = {
            "hook_event_name": event,
            "tool_name": tool,
            "cwd": cwd,
            "session_id": "s1",
            "tool_input": tool_input,
        }
        return self.ccnavi("--mode", "enable", stdin=json.dumps(payload), env=env)

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
        self.assertIn("projects/app/.ccnavi/config/project/rules.yml:schema", self.reason(denied))
        self.assertIn("write a migration", self.reason(denied))
        record = self.last_record()
        self.assertEqual(record["project"], "app")
        self.assertEqual(record["tree"], "app")
        self.assertEqual(record["rules"], ["projects/app/.ccnavi/config/project/rules.yml:schema"])

        # lib に schema の deny は無い。app の deny は lib には当たらない。
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
        self.assertEqual(record["rules"], [".ccnavi/config/common/rules.yml:ws-src"])
        self.assertNotIn("project", record)

        # ワークスペースの schema/ は誰も守っていない。app の deny は当たらない。
        passed = self.hook("Write", self.app, file_path=os.path.join(self.ws, "schema", "x.sql"))
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertNotIn("DENY", self.reason(passed))

    # ---- 2. Bash は全部の和

    def test_bash_is_judged_by_the_union_regardless_of_cwd(self):
        for cwd in (self.ws, self.app, self.lib):
            denied = self.hook("Bash", cwd, command="psql -c 'select 1'")
            self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
            self.assertIn(
                "projects/lib/.ccnavi/config/project/rules.yml:raw-psql", self.reason(denied)
            )
            denied = self.hook("Bash", cwd, command="rm -rf build")
            self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
            self.assertIn("ws-rm", self.reason(denied))
        # app だけが許した npm test は lib に居ても通る。和の代償で、意図した挙動。
        allowed = self.hook("Bash", self.lib, command="npm test")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        record = self.last_record()
        self.assertEqual(record["decision"], "allow")
        self.assertEqual(record["rules"], ["projects/app/.ccnavi/config/project/rules.yml:npm"])
        self.assertEqual(record["project"], "lib")

    # ---- 3. 読めないプロジェクトのルール

    def test_unreadable_project_rules_are_empty_and_drop_out_of_the_union(self):
        """不正なレイヤーは空として扱い、記録がレイヤーの名前を残す。

        組み込みの既定には戻らない。共通レイヤーが有るのに戻すと、共通レイヤーの deny が
        消える側になる。
        """
        write(layer_rules(self.app), "version: 1\ndeny: [\n")
        passed = self.hook("Write", self.ws, file_path=os.path.join(self.app, "schema", "x.sql"))
        self.assertEqual(passed.returncode, 0, passed.stderr)
        record = self.last_record()
        self.assertEqual(record.get("fallback"), "app", record)
        self.assertIn("app", record["detail"])
        self.assertNotIn("built-in defaults", self.reason(passed))

        # 共通レイヤーの deny は不正なレイヤーの上でも当たったまま。
        guarded = os.path.join(self.app, ".ccnavi", "approved", "x")
        denied = self.hook("Write", self.ws, file_path=guarded)
        self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
        self.assertIn("guard-approved", self.reason(denied))

        denied = self.hook("Bash", self.app, command="psql")
        self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
        self.assertIn("projects/lib/.ccnavi/config/project/rules.yml:raw-psql", self.reason(denied))
        record = self.last_record()
        self.assertEqual(record.get("fallback"), "app", record)
        self.assertIn("unreadable layer: app", record["detail"])

    # ---- 4. プロジェクトから切ったワークツリー

    def test_worktree_cut_from_a_project_is_judged_by_that_project(self):
        tree = self.worktree(self.app, "i0007")
        denied = self.hook("Write", self.ws, file_path=os.path.join(tree, "schema", "x.sql"))
        self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
        self.assertIn("projects/app/.ccnavi/config/project/rules.yml:schema", self.reason(denied))
        record = self.last_record()
        self.assertEqual(record["tree"], "i0007")
        self.assertEqual(record["project"], "app")

    def family_in_lib(self, parent):
        """lib の提案として、親 1 本とその子 1 本（フェーズ 1）を書いて承認する。"""
        write(
            self.proposal_file(self.lib, parent),
            ticket_text(parent, allow=("src/*",)),
        )
        child = f"{parent}-01-01"
        write(
            self.proposal_file(self.lib, child),
            ticket_text(child, parent=parent, allow=("src/a/*",)),
        )

    def test_worktree_cut_from_the_wrong_project_is_refused_by_the_ticket(self):
        # プロジェクトの提案は、親のワークツリー（そのプロジェクトから切る）の中に置く。
        # 置き場がプロジェクトを決めるので、frontmatter の project は書かなくてよい。
        self.family_in_lib("i0007")
        self.family_in_lib("i0008")
        approved = self.ccnavi("user", "agree", stdin="y\n")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

        # 子のワークツリーを別のプロジェクト（app）から切ってしまった
        wrong = self.worktree(self.app, "i0007-01-01")
        denied = self.hook("Write", self.ws, file_path=os.path.join(wrong, "src", "a", "a.py"))
        self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
        self.assertIn("DENY_TICKET_PROJECT_MISMATCH", self.reason(denied))
        self.assertIn("'lib'", self.reason(denied))
        self.assertIn("'app'", self.reason(denied))
        self.assertEqual(self.last_record()["code"], "DENY_TICKET_PROJECT_MISMATCH")
        # 範囲の判定ではないので、記録の名前は builtin:ticket-scope を使い回さない。
        self.assertEqual(self.last_record()["rules"], ["builtin:ticket-project"])

        right = self.worktree(self.lib, "i0008-01-01")
        allowed = self.hook("Write", self.ws, file_path=os.path.join(right, "src", "a", "a.py"))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertNotIn("DENY", self.reason(allowed))
        outside = self.hook("Write", self.ws, file_path=os.path.join(right, "docs", "a.md"))
        self.assertIn("DENY_TICKET_SCOPE", self.reason(outside))

    def test_wrong_project_is_refused_even_when_the_worktree_name_differs_in_case(self):
        """表記違いで切ったワークツリーでも（どの機械でも区別しない）取り違えを止める。

        範囲の判定（ticket_verdict）は表記の違いを吸収して引く。取り違えの検査だけ厳密に
        引くと、範囲の中への書き込みは別のプロジェクトのツリーでも通ってしまう。
        大文字小文字はどの機械でも区別しないので、Linux でもそのまま走る。
        """
        self.family_in_lib("i0007")
        approved = self.ccnavi("user", "agree", stdin="y\n")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

        wrong = self.worktree(self.app, "I0007-01-01")
        denied = self.hook("Write", self.ws, file_path=os.path.join(wrong, "src", "a", "a.py"))
        self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
        self.assertIn("DENY_TICKET_PROJECT_MISMATCH", self.reason(denied))
        self.assertEqual(self.last_record()["code"], "DENY_TICKET_PROJECT_MISMATCH")

    def test_project_skills_are_written_only_under_the_ticket_rules(self):
        """skills/ は保護の外のふつうの場所。チケットの範囲の中でだけ書ける。

        置き場を ccnavi ディレクトリの外にしたのは、組み込みの保護を緩めずに済ませるため。
        範囲に入れた子のワークツリーでは通り、範囲の外とチケットの無いワークツリーでは、
        同じツリーのほかのファイルと同じ判定になる。
        """
        write(
            self.proposal_file(self.lib, "i0009"),
            ticket_text("i0009", allow=("skills/*",)),
        )
        write(
            self.proposal_file(self.lib, "i0009-01-01"),
            ticket_text("i0009-01-01", parent="i0009", allow=("skills/deploy/*",)),
        )
        approved = self.ccnavi("user", "agree", stdin="y\n")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        # 親のワークツリーは、範囲の中でも置き場以外に書けない。
        home = self.worktree(self.lib, "i0009")
        refused = self.hook(
            "Write", self.ws, file_path=os.path.join(home, "skills", "deploy", "SKILL.md")
        )
        self.assertEqual(self.decision(refused), "deny", refused.stdout + refused.stderr)
        self.assertIn("DENY_TICKET_PARENT", self.reason(refused))
        self.assertEqual(self.last_record()["code"], "DENY_TICKET_PARENT")
        self.assertEqual(self.last_record()["rules"], ["builtin:ticket-parent"])
        tree = self.worktree(self.lib, "i0009-01-01")
        skill = os.path.join(tree, "skills", "deploy", "SKILL.md")
        allowed = self.hook("Write", self.ws, file_path=skill)
        self.assertNotEqual(self.decision(allowed), "deny", allowed.stdout + allowed.stderr)
        self.assertNotIn("DENY", self.reason(allowed))
        other = os.path.join(tree, "skills", "other", "SKILL.md")
        self.assertIn(
            "DENY_TICKET_SCOPE", self.reason(self.hook("Write", self.ws, file_path=other))
        )

        loose = self.worktree(self.lib, "loose")
        for rel in (("skills", "deploy", "SKILL.md"), ("docs", "notes.md")):
            done = self.hook("Write", self.ws, file_path=os.path.join(loose, *rel))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(self.decision(done), "ask", done.stdout)

    def test_the_parent_worktree_takes_only_the_places(self):
        """親のワークツリーに書けるのは置き場だけ。実装は子のワークツリーで書く。

        親の範囲の中でも、置き場の外は止める。止める理由は範囲の外ではなく、親が作業の場では
        ないこと（DENY_TICKET_PARENT）。範囲を広げても通らないので、案内は子を起こす手を言う。
        """
        self.family_in_lib("i0007")
        approved = self.ccnavi("user", "agree", stdin="y\n")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        home = self.worktree(self.lib, "i0007")
        for rel in (("src", "a", "a.py"), ("docs", "x.md"), ("wip", "design", "d.md")):
            refused = self.hook("Write", self.ws, file_path=os.path.join(home, *rel))
            self.assertEqual(self.decision(refused), "deny", refused.stdout + refused.stderr)
            self.assertIn("DENY_TICKET_PARENT", self.reason(refused))
            self.assertIn("child", self.reason(refused))
        for rel in (("wip", "handoff", "a.md"), ("wip", "proposals", "todo", "i0099.md")):
            placed = self.hook("Write", self.ws, file_path=os.path.join(home, *rel))
            self.assertNotIn("DENY_TICKET_PARENT", self.reason(placed))
        # 子のワークツリーは、いつもどおり子の範囲で判定する。
        child = self.worktree(self.lib, "i0007-01-01")
        inside = self.hook("Write", self.ws, file_path=os.path.join(child, "src", "a", "a.py"))
        self.assertNotIn("DENY", self.reason(inside))

    # ---- 4b. プロジェクトを決めるのは提案を置いた場所

    def test_the_place_decides_the_project_for_parent_and_child_alike(self):
        write(
            self.proposal_file(self.lib, "i0007"),
            ticket_text("i0007", allow=("src/*",)),
        )
        write(
            self.proposal_file(self.lib, "i0007-01-01"),
            ticket_text("i0007-01-01", parent="i0007", allow=("src/a/*",)),
        )
        # 承認の前から、親も子も同じプロジェクトとしてボードに出る
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        found = {t["ticket"]: t["project"] for t in board["tickets"]}
        self.assertEqual(found, {"i0007": "lib", "i0007-01-01": "lib"})
        self.assertEqual(board["pending_approval"], ["i0007", "i0007-01-01"])

        approved = self.ccnavi("user", "agree", stdin="y\n")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        # 承認の画面は、書き込みが向かうリポジトリをユーザに見せる
        self.assertIn("■ プロジェクト: lib", approved.stdout)
        # 承認は `project:` を書き足さない。承認済みチケットのプロジェクトも
        # 置き場（ツリー）から決まる
        for name in ("i0007", "i0007-01-01"):
            with open(self.approved_path("doing", name + ".md"), encoding="utf-8") as f:
                self.assertNotIn("project:", f.read())
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        found = {t["ticket"]: t["project"] for t in board["tickets"]}
        self.assertEqual(found, {"i0007": "lib", "i0007-01-01": "lib"})

    def test_a_declaration_that_disagrees_with_the_place_is_not_approved(self):
        write(
            os.path.join(self.ws, "wip", "proposals", "todo", "i0007.md"),
            ticket_text("i0007", project="lib", allow=("src/*",)),
        )
        result = self.ccnavi("user", "agree", stdin="y\n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("置き場（ワークスペース）と違う", result.stderr)
        self.assertIn("wip/proposals/ に置いて", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0007.md")))

    def test_a_child_placed_apart_from_its_parent_is_not_approved(self):
        write(
            self.proposal_file(self.lib, "i0007"),
            ticket_text("i0007", allow=("src/*",)),
        )
        write(
            os.path.join(self.app, "wip", "proposals", "todo", "i0007-01-01.md"),
            ticket_text("i0007-01-01", parent="i0007", allow=("src/a/*",)),
        )
        result = self.ccnavi("user", "agree", stdin="y\n")
        # 承認の対象の一部（子）が失敗したので、通ったぶん（親）を置いてから
        # 4 で終わる（何も置かない失敗の 1 とは分ける）。
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn("子は親と同じ置き場に置いて", result.stderr)
        self.assertTrue(os.path.exists(self.approved_path("doing", "i0007.md")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0007-01-01.md")))

    def test_a_proposal_inside_a_project_worktree_is_read_without_complaint(self):
        # 提案はそのツリーの wip/proposals/ に置く。プロジェクトのワークツリーの中も普通の置き場で、
        # 承認をプロジェクトの git で共有するために、そこに置く。
        # 置き場はワークツリーの元リポジトリで決まり、承認済みチケットは記録したパスから
        # 引くので閉じられる。
        tree = self.worktree(self.lib, "i0010")
        # 計画のある親だけが着手できる。定義はプロジェクトのレイヤー（config）に置く。
        write_plain_phases(self.lib)
        write(
            os.path.join(tree, "wip", "proposals", "todo", "i0010.md"),
            ticket_text("i0010", allow=("src/*",), plan=1),
        )
        approved = self.ccnavi("user", "agree", stdin="y\n")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertNotIn("の中に提案がある", approved.stderr)
        self.assertIn("■ プロジェクト: lib", approved.stdout)

        # 承認で todo/ から動き、着手と完了はそのツリーの置き場の中で動く。
        self.assertFalse(os.path.exists(os.path.join(tree, "wip", "proposals", "todo", "i0010.md")))
        started = self.ccnavi("ticket", "start", "i0010")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        # 着手の欄は、そのツリーの置き場の承認済みチケットに書かれる。
        doing = os.path.join(tree, ".ccnavi", "approved", "doing", "i0010.md")
        self.assertTrue(os.path.exists(doing), doing)
        with open(doing, encoding="utf-8") as f:
            self.assertNotIn('started_at: ""', f.read())

    def test_lint_names_a_ticket_place_that_is_not_scanned(self):
        write(
            os.path.join(self.ws, "wip", "gone", "proposals", "todo", "i0009.md"),
            ticket_text("i0009", allow=("src/*",)),
        )
        result = self.ccnavi("--lint")
        out = result.stdout + result.stderr
        self.assertIn("wip/gone/proposals", out)
        self.assertIn("走査されていない", out)
        # 置き場が走査されないので、提案はどこにも出ない
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        self.assertEqual(board["tickets"], [])

    def test_lint_names_a_workspace_side_place_even_for_a_known_project(self):
        # 名前が projects/ に在っても、ワークスペースの wip/<名前>/proposals/ は走査されない。
        # 提案はそのプロジェクトの側 projects/<名前>/wip/proposals/ に置く。
        # 何も言わないと提案が消えたように見えるので、正しい置き場をつけて名指しする
        write(
            os.path.join(self.ws, "wip", "lib", "proposals", "todo", "i0011.md"),
            ticket_text("i0011", allow=("src/*",)),
        )
        result = self.ccnavi("--lint")
        out = result.stdout + result.stderr
        self.assertIn("wip/lib/proposals", out)
        self.assertIn("走査されていない", out)
        self.assertIn("projects/lib/wip/proposals", out)
        board = json.loads(self.ccnavi("--explain", "--json").stdout)
        self.assertEqual(board["tickets"], [])

    def test_a_project_ticket_moves_inside_its_own_tree(self):
        write(
            self.proposal_file(self.lib, "i0007"),
            ticket_text("i0007", allow=("src/*",)),
        )
        self.assertEqual(self.ccnavi("user", "agree", stdin="y\n").returncode, 0)
        # 承認はプロジェクトの todo/ からプロジェクトの doing/ へ動かす。取り消すと done/ へ。
        # 置き場はプロジェクトの git が持つ。
        lib_approved = os.path.join(self.worktree(self.lib, "i0007"), ".ccnavi", "approved")
        self.assertFalse(os.path.exists(self.proposal_file(self.lib, "i0007")))
        self.assertTrue(os.path.exists(os.path.join(lib_approved, "doing", "i0007.md")))
        cancelled = self.ccnavi("ticket", "cancel", "i0007", "--reason", "やめる")
        self.assertEqual(cancelled.returncode, 0, cancelled.stdout + cancelled.stderr)
        self.assertTrue(os.path.exists(os.path.join(lib_approved, "done", "i0007.md")))

    def test_an_undecided_project_family_stops_the_hook_and_the_state(self):
        """プロジェクトの親子のチケットも、親のワークツリーが決まらなければ止まる。

        親のワークツリーはプロジェクトのリポジトリから切る（ワークスペースの同名の
        ワークツリーとは別）。
        """
        write(
            self.proposal_file(self.lib, "i0007"),
            ticket_text("i0007", allow=("src/*",)),
        )
        self.assertEqual(self.ccnavi("user", "agree", stdin="y\n").returncode, 0)
        tree = self.worktree(self.lib, "i0007")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "--allow-empty", "-m", "approve")
        # 親のワークツリーに書けるのは置き場だけ（実装は子のワークツリーで書く）。
        target = os.path.join(tree, "wip", "handoff", "a.py")
        before = self.hook("Write", self.ws, file_path=target)
        self.assertNotEqual(self.decision(before), "deny", before.stdout + before.stderr)
        # 親のワークツリーの HEAD がブランチ名と合わなくなれば、決まらない。
        git(tree, "checkout", "--quiet", "-b", "elsewhere")
        # 親のワークツリーは範囲の中でも置き場以外に書けない。決まらなくなっても止まる側は同じ。
        denied = self.hook("Write", self.ws, file_path=os.path.join(tree, "src", "a.py"))
        self.assertEqual(self.decision(denied), "deny", denied.stdout + denied.stderr)
        cancelled = self.ccnavi("ticket", "cancel", "i0007", "--reason", "やめる")
        self.assertNotEqual(cancelled.returncode, 0, cancelled.stdout + cancelled.stderr)
        self.assertIn("合わない", cancelled.stderr)

    # ---- 5. 実行後チェックはツリーごと

    def test_post_monitoring_reads_the_project_tree_the_call_touched(self):
        started = self.hook("", self.ws, event="UserPromptSubmit")
        self.assertEqual(started.returncode, 0, started.stderr)
        # 初回の実行後は記録を取るだけ。そのあとで app の schema/ をシェルが汚す。
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
        self.assertIn("projects/app/.ccnavi/config/project/rules.yml:schema", after.stderr)
        self.assertIn("tree: app", after.stderr)
        record = self.last_record()
        self.assertEqual(record["project"], "app")
        self.assertEqual(record["rules"], ["projects/app/.ccnavi/config/project/rules.yml:schema"])

        # ターンの終わりは全部のツリーを見て、ツリーの名前をつけてユーザに言う。
        stopped = self.hook("", self.ws, event="Stop")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertIn("app: schema/x.sql", self.system_message(stopped))

    # ---- 6. --lint がプロジェクトまわりの設定の誤りを言う

    def test_lint_names_project_config_problems(self):
        write(layer_rules(self.app), "version: 1\ndeny: [\n")
        os.makedirs(os.path.join(self.lib, ".claude"))
        # `projects/` を無視しない。追跡はしない: ここで `git add -A` すると、入れ子の
        # リポジトリが `projects/app` の名前で索引に載り、「追跡されている」の側の
        # 知らせ（置き場は固定）に切り替わって、「無視されていない」を確かめられなくなる。
        write(os.path.join(self.ws, ".gitignore"), "/.claude/\n")

        result = self.ccnavi("--lint")
        out = result.stdout + result.stderr
        self.assertIn("projects/ がワークスペースの git で無視されていない", out)
        self.assertIn("(projects/app)", out)
        # `.claude/` を持つだけのプロジェクトには苦情を出さない
        self.assertNotIn("(projects/lib)", out)

        # `--explain` はレイヤーごとに並べる。読めないレイヤーはその位置で言う。
        explained = self.ccnavi("--explain")
        self.assertIn("■ rules app", explained.stdout)
        self.assertIn("■ rules lib", explained.stdout)
        self.assertIn("読めない", explained.stdout)

    def test_lint_no_longer_asks_for_a_scratchpad_ignore_line(self):
        # 下書きの置き場は `wip/handoff/`（追跡する）に一本化した。ワークツリー直下の
        # `scratchpad/` を無視しているかの警告は出さない。
        result = self.ccnavi("--lint")
        text = result.stdout + result.stderr
        self.assertNotIn("(scratch", text)
        self.assertNotIn("無視されていない。実行前チェック", text)

    # ---- 7. projects/ を数えない設定では前と同じ

    def test_without_a_projects_dir_everything_is_judged_by_the_workspace_rules(self):
        # 「数えない」を言う口は、`projects/` を作らないこと（置き場は固定）。
        # 消さずに `projects/` の外へ動かす（Windows は .git の中の読み取り専用を消せない）。
        # 動かした先の app は入れ子の git のまま。`projects/` の外なら、その中でも
        # ワークスペースのルールで判定される。
        moved = os.path.join(self.ws, "moved-away")
        os.rename(self.projects, moved)
        app = os.path.join(moved, "app")
        passed = self.hook("Write", self.ws, file_path=os.path.join(app, "schema", "x.sql"))
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertNotIn("DENY", self.reason(passed))
        record = self.last_record()
        self.assertNotIn("project", record)

        passed = self.hook("Bash", app, command="psql")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertNotIn("DENY", self.reason(passed))

    def test_project_rules_file_swaps_one_projects_rules_for_diagnostics_only(self):
        # VS Code 拡張が、編集中の lib のルールを保存せずに試す形。lib の deny に
        # `*/docs/*` を足した一時ファイルを渡すと、lib への書き込みはそれで判定される。
        edited = dict(LIB_RULES)
        edited["deny"] = LIB_RULES["deny"] + [
            {
                "id": "docs",
                "match": "Write|Edit",
                "glob": "*/docs/*",
                "message": "docs are generated.",
            }
        ]
        tmp = write(os.path.join(self.ws, "tmp", "lib-rules.yml"), json.dumps(edited))
        target = os.path.join(self.lib, "docs", "a.md")

        tried = self.ccnavi(
            "--test", "Write", target, "--json", "--project-rules-file", f"lib={tmp}"
        )
        self.assertEqual(tried.returncode, 0, tried.stderr)
        body = json.loads(tried.stdout)
        self.assertEqual(body["verdict"], "deny", tried.stdout)
        hit = body["rules"][0]
        self.assertEqual(hit["id"], "tmp/lib-rules.yml:docs")
        # 当たったルールはプロジェクトの名前付きで引け、翻訳後の式まで見える。
        self.assertEqual(hit["source"], "file")
        self.assertEqual(hit["written"], "*/docs/*")

        # app は差し替えていないので、app への同じ書き込みは前のまま通る。
        other = self.ccnavi(
            "--test",
            "Write",
            os.path.join(self.app, "docs", "a.md"),
            "--json",
            "--project-rules-file",
            f"lib={tmp}",
        )
        self.assertNotEqual(json.loads(other.stdout)["verdict"], "deny", other.stdout)

        # --lint --json も差し替えた側を読む。不正な一時ファイルは lib の error として出る。
        broken = write(os.path.join(self.ws, "tmp", "broken.yml"), "version: 1\ndeny: [\n")
        linted = self.ccnavi("--lint", "--json", "--project-rules-file", f"lib={broken}")
        report = json.loads(linted.stdout)
        self.assertEqual(report["projects"], ["app", "lib"])
        wheres = [p["where"] for p in report["problems"] if p["severity"] == "error"]
        self.assertTrue(any(w.startswith("(projects/lib)") for w in wheres), linted.stdout)
        self.assertFalse(any(w.startswith("(projects/app)") for w in wheres), linted.stdout)

        # hook からの判定は差し替えを見ない。保存していないルールが判定に使われる経路を持たない。
        ignored = self.ccnavi(
            "--mode",
            "enable",
            "--project-rules-file",
            f"lib={tmp}",
            stdin=json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Write",
                    "cwd": self.ws,
                    "session_id": "s1",
                    "tool_input": {"file_path": target},
                }
            ),
        )
        self.assertEqual(ignored.returncode, 0, ignored.stderr)
        self.assertNotEqual(self.decision(ignored), "deny", ignored.stdout)
        self.assertIn("診断", ignored.stderr)

        # 形が違えば止まる。
        malformed = self.ccnavi("--lint", "--json", "--project-rules-file", "lib")
        self.assertEqual(malformed.returncode, 1)
        self.assertIn("<名前>=<パス>", malformed.stderr)


if __name__ == "__main__":
    unittest.main()
