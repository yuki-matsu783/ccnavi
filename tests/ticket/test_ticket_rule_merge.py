"""ルールとチケットの判定を合わせ、厳しい側を採ることの受入テスト。

道具を外から叩いて、応答と記録だけを見る。

見るのは 5 つ。

1. 実行前の判定。ルール（deny / ask / allow / 何も言わない）とチケット（deny / ask / allow /
   範囲の外 / チケットが無い）の全組み合わせで、判定・理由コード・どちらの文面か・記録の欄
2. 実行後の監視。シェルが書いたあとに報告するか、どのコードか
3. チケットの置き場を範囲の外から外すこと。実行前・実行後・サブエージェント終了時で同じ答え
4. チケットが効かない場面と、ルールより先に見る点検
5. 診断の出力

ルールは作業ツリーを `*/.claude/worktrees/*` で丸ごと指す 1 本を、表の列ごとに置き換える。

チケットの範囲は子 `i0001-01` のもの。

    allow: src/*
    ask:   src/ask/*
    deny:  src/deny/*

範囲の外には `docs/` を使う。チケットの無い行は、main から切った別の作業ツリー `free` に書く。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi

NOTE = "ルールの additionalContext。判定を決めた側に関わらず載る。"

WORKTREES = "*/.claude/worktrees/*"

# チケットの判定の列。子の作業ツリーのルートからの相対パス。
TICKET_DENY = "src/deny/x.py"
TICKET_ASK = "src/ask/x.py"
TICKET_ALLOW = "src/ok/x.py"
TICKET_OUTSIDE = "docs/x.md"

# 承認画面と `--explain` が添える文面。
APPROVAL_NOTE = (
    "ルールの allow で開けてある場所も、この範囲の外では止まる。"
    "ルールの deny はこの範囲の中でも止まる"
)
EXPLAIN_NOTE = (
    "作業ツリーに結び付いたチケットの範囲は、ルールの allow / ask より強い。範囲の外は止まる"
)

# ルールが何も言わない列に置くルール。Write には当たらない。
SILENT = {
    "version": 1,
    "deny": [{"id": "push", "match": "Bash", "glob": "*git push*", "message": "push は人が行う"}],
}


def rules_with(section: str) -> dict:
    """作業ツリーを丸ごと指すルールを、指定のタイプに 1 本だけ置く。"""
    body = {"version": 1, "deny": list(SILENT["deny"])}
    entry = {
        "id": f"rule-{section}",
        "match": "Write|Edit|NotebookEdit",
        "glob": WORKTREES,
        "message": f"RULE-{section.upper()}-MESSAGE",
        "additionalContext": NOTE,
    }
    body.setdefault(section, []).append(entry)
    return body


def rules_for(section: str) -> dict:
    """表の列の名前からルールを組む。`silent` は Write に何も言わないルール。"""
    return SILENT if section == "silent" else rules_with(section)


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


def ticket_text(name, *, parent="", phase=None, allow=(), ask=(), deny=()):
    """提案の文面。tests/ticket/test_ticket.py の雛形に `deny` を足したもの。"""
    lines = ["---", "version: 1", f"ticket: {name}"]
    if parent:
        lines += [f"parent: {parent}", f"phase: {phase}"]
    lines += [
        "human_review:",
        "  required: true",
        "  reason: テスト",
        "title: 作業",
        "rationale: |",
        "  理由",
    ]
    for section, globs in (("allow", allow), ("ask", ask), ("deny", deny)):
        if globs:
            lines.append(f"{section}:")
            for g in globs:
                lines += ["  - match: Write|Edit", f'    glob: "{g}"']
    lines += ['started_at: ""', 'completed_at: ""', 'base_sha: ""', "---", "", "本文"]
    return "\n".join(lines) + "\n"


def section_of(text: str, head: str) -> str:
    """出力のうち、見出し `head` から次の見出しの手前まで。見出しが無ければ空。"""
    if head not in text:
        return ""
    rest = text.split(head, 1)[1]
    for mark in ("\n■", "\n== "):
        rest = rest.split(mark, 1)[0]
    return rest


class Workspace(unittest.TestCase):
    """本物の git リポジトリに、承認済みの親 1 本と子 1 本と、チケットの無い作業ツリーを置く。"""

    # 提案と承認済みチケットの置き場。ツリーのルートからの相対で、道具にもそのまま渡す。
    # 子クラスで綴りを変え、置き場を決め打ちしていないことを確かめる。
    TICKETS = "wip/tickets"
    APPROVED = ".ccnavi/tickets"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-merge-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".claude"))
        git(self.root, "init", "--quiet", "-b", "main")
        write(os.path.join(self.root, "src", "keep.py"), "print(1)\n")
        write(os.path.join(self.root, ".gitignore"), ".claude/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "init")

        self.rules = write(os.path.join(self.root, "rules.yml"), json.dumps(SILENT))
        self.state = os.path.join(self.root, "state")
        self.log = os.path.join(self.root, "log.jsonl")
        self.parent_tree = self.worktree("i0001", "main")
        self.child = os.path.join(self.root, ".claude", "worktrees", "i0001-01")

    # ---- 道具

    def use_rules(self, body):
        write(self.rules, json.dumps(body))

    def worktree(self, name, base):
        path = os.path.join(self.root, ".claude", "worktrees", name)
        git(self.root, "worktree", "add", "--quiet", path, "-b", name, base)
        return path

    def ccnavi(self, *args, stdin="", env=None, core="disable"):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        return run_ccnavi(
            [
                "--root",
                self.root,
                "--rules",
                self.rules,
                "--tickets",
                self.TICKETS,
                "--approved",
                self.APPROVED,
                "--state",
                self.state,
                "--log",
                self.log,
                "--guard-core-files",
                core,
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

    def hook(
        self,
        event,
        tool,
        cwd,
        *,
        mode="enable",
        agent_id="",
        session="s1",
        env=None,
        core="disable",
        **tool_input,
    ):
        payload = {
            "hook_event_name": event,
            "tool_name": tool,
            "cwd": cwd,
            "session_id": session,
            "tool_input": tool_input,
        }
        if agent_id:
            payload["agent_id"] = agent_id
        return self.ccnavi("--mode", mode, stdin=json.dumps(payload), env=env, core=core)

    def write_hook(self, tree, rel, **kw):
        """Write の実行前の判定。"""
        return self.hook(
            "PreToolUse", "Write", tree, file_path=os.path.join(tree, *rel.split("/")), **kw
        )

    def after_shell(self, tree, rel, session):
        """控えを取ってからシェルが rel を書いたことにし、その後の PostToolUse を返す。

        控えはセッションごと。行ごとにセッションを変えれば、前の行が書いたファイルは
        「前から在った変更」として控えに入り、その行が書いた 1 件だけが報告の対象になる。
        """
        baseline = self.hook("PostToolUse", "Bash", tree, session=session, command="ls")
        self.assertIn(baseline.returncode, (0, 2), baseline.stderr)
        write(os.path.join(tree, *rel.split("/")), "generated\n")
        return self.hook("PostToolUse", "Bash", tree, session=session, command="python gen.py")

    @staticmethod
    def out(result):
        if not result.stdout.strip():
            return {}
        return json.loads(result.stdout).get("hookSpecificOutput", {})

    def decision(self, result):
        """応答の判定。欄が無ければ通した（additionalContext だけの応答を含む）。"""
        return self.out(result).get("permissionDecision", "allow")

    def reason(self, result):
        out = self.out(result)
        return out.get("permissionDecisionReason") or out.get("additionalContext") or ""

    def last_record(self):
        with open(self.log, encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
        return json.loads(lines[-1])

    def propose(self, name, **kw):
        return write(
            os.path.join(self.parent_tree, *self.TICKETS.split("/"), "todo", name + ".md"),
            ticket_text(name, **kw),
        )

    def approve(self):
        result = self.ccnavi("--approve", stdin="y\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "approve")
        return result

    def family(self, parent=None, child=None):
        """親と子を提案して承認し、子の作業ツリーを親のブランチから切って着手する。"""
        self.propose("i0001", **(parent or {"allow": ("src/*",)}))
        self.propose(
            "i0001-01",
            parent="i0001",
            phase=1,
            **(child or {"allow": ("src/*",), "ask": ("src/ask/*",), "deny": ("src/deny/*",)}),
        )
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "tickets")
        self.approve()
        self.worktree("i0001-01", "i0001")
        started = self.ccnavi("ticket", "start", "i0001-01")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "start")
        self.free = self.worktree("free", "main")


class PreToolUseTable(Workspace):
    """実行前の判定。"""

    # (チケットの列の名前, 書き込み先の作業ツリーを選ぶ鍵, 相対パス)
    COLUMNS = (
        ("deny", "child", TICKET_DENY),
        ("ask", "child", TICKET_ASK),
        ("allow", "child", TICKET_ALLOW),
        ("outside", "child", TICKET_OUTSIDE),
        ("no-ticket", "free", TICKET_ALLOW),
    )

    # ルールの列ごとに、チケットの列 → (判定, code, 文面の出所, rules の先頭)。
    # 文面の出所: "rule" はルールの message、"ticket" はチケットの文面、"" は文面なし。
    EXPECTED = {
        "deny": {
            "deny": ("deny", "DENY_PATH", "rule", "rule-deny"),
            "ask": ("deny", "DENY_PATH", "rule", "rule-deny"),
            "allow": ("deny", "DENY_PATH", "rule", "rule-deny"),
            "outside": ("deny", "DENY_PATH", "rule", "rule-deny"),
            "no-ticket": ("deny", "DENY_PATH", "rule", "rule-deny"),
        },
        "ask": {
            "deny": ("deny", "DENY_TICKET_SCOPE", "ticket", "(ticket-scope)"),
            "outside": ("deny", "DENY_TICKET_SCOPE", "ticket", "(ticket-scope)"),
            "ask": ("ask", "RULE_ASK", "rule", "rule-ask"),
            "allow": ("ask", "RULE_ASK", "rule", "rule-ask"),
            "no-ticket": ("ask", "RULE_ASK", "rule", "rule-ask"),
        },
        "allow": {
            "deny": ("deny", "DENY_TICKET_SCOPE", "ticket", "(ticket-scope)"),
            "outside": ("deny", "DENY_TICKET_SCOPE", "ticket", "(ticket-scope)"),
            "ask": ("ask", "TICKET_ASK", "ticket", "(ticket-scope)"),
            "allow": ("allow", "", "", "rule-allow"),
            "no-ticket": ("allow", "", "", "rule-allow"),
        },
        "silent": {
            "deny": ("deny", "DENY_TICKET_SCOPE", "ticket", "(ticket-scope)"),
            "outside": ("deny", "DENY_TICKET_SCOPE", "ticket", "(ticket-scope)"),
            "ask": ("ask", "TICKET_ASK", "ticket", "(ticket-scope)"),
            # チケットの範囲の中で、ルールも何も言わない。ルールの id もチケットの印も残らない。
            "allow": ("allow", "", "", None),
            # 権限モードに委ねる行。payload に permission_mode を入れないので、委ねた先の
            # 答えは見ない（判定を ask に倒すか渡すかは権限モード次第）。見るのはコードだけ。
            "no-ticket": (None, "UNDECLARED", "", None),
        },
    }

    def setUp(self):
        super().setUp()
        self.family()

    def check_column(self, rule_section):
        self.use_rules(rules_for(rule_section))
        for column, where, rel in self.COLUMNS:
            decision, code, text_from, first_rule = self.EXPECTED[rule_section][column]
            with self.subTest(rule=rule_section, ticket=column):
                tree = self.child if where == "child" else self.free
                result = self.write_hook(tree, rel)
                self.assertEqual(result.returncode, 0, result.stderr)
                reason = self.reason(result)
                record = self.last_record()
                if decision is not None:
                    self.assertEqual(self.decision(result), decision, reason)
                self.assertEqual(record.get("code", ""), code, reason)

                rule_message = f"RULE-{rule_section.upper()}-MESSAGE"
                if text_from == "rule":
                    self.assertIn(rule_message, reason)
                    self.assertNotIn("DENY_TICKET_SCOPE", reason)
                    self.assertNotIn("TICKET_ASK", reason)
                elif text_from == "ticket":
                    self.assertIn(code, reason)
                    self.assertNotIn(
                        rule_message, self.out(result).get("permissionDecisionReason", "")
                    )

                rules = record.get("rules") or []
                if first_rule is None:
                    self.assertNotIn("(ticket-scope)", rules, rules)
                else:
                    self.assertTrue(rules, record)
                    self.assertEqual(rules[0], first_rule, rules)

                if text_from == "ticket":
                    # 判定を下したのが層を持たないチケットなら、層の欄は空。
                    self.assertEqual(record.get("source", ""), "", record)
                    # 足す行は場面ごとに独立していて、重なれば（ルールを狭め、かつチケットの
                    # deny に当たった）両方載る。
                    if rule_section in ("ask", "allow"):
                        # チケットがルールを狭めた回は、ルールの id も記録に残し、文面が名指しする。
                        self.assertIn(f"rule-{rule_section}", rules, rules)
                        self.assertIn(
                            f"rule: rule-{rule_section} ({rule_section}) lets this through, "
                            "but the ticket for this worktree narrows it",
                            reason,
                        )
                    else:
                        self.assertNotIn("rule: ", reason)
                    if column == "deny":
                        self.assertIn("ticket entry: deny src/deny/*", reason)
                    else:
                        self.assertNotIn("ticket entry:", reason)

                if rule_section != "silent":
                    # 当たったルールの additionalContext は、判定を決めた側に関わらず載る。
                    self.assertIn(NOTE, result.stdout)

    def test_rule_deny_is_always_strongest(self):
        self.check_column("deny")

    def test_rule_ask_is_narrowed_by_ticket_deny_and_outside(self):
        self.check_column("ask")

    def test_rule_allow_is_narrowed_by_ticket_deny_outside_and_ask(self):
        self.check_column("allow")

    def test_silent_rules_leave_the_decision_to_the_ticket(self):
        self.check_column("silent")


class PostToolUseTable(Workspace):
    """実行後の監視。作業ツリーでシェルが書いたあと。"""

    # (ルールのタイプ, チケットの列, 相対パスの接頭, 報告のコード。None は報告しない)
    CASES = (
        ("deny", "deny", "src/deny", "POST_VIOLATION"),
        ("deny", "allow", "src/ok", "POST_VIOLATION"),
        ("deny", "outside", "docs", "POST_VIOLATION"),
        ("ask", "deny", "src/deny", "POST_VIOLATION"),
        ("ask", "allow", "src/ok", "POST_VIOLATION"),
        ("ask", "outside", "docs", "POST_VIOLATION"),
        ("allow", "deny", "src/deny", "POST_TICKET_SCOPE"),
        ("allow", "outside", "docs", "POST_TICKET_SCOPE"),
        ("allow", "ask", "src/ask", None),
        ("allow", "allow", "src/ok", None),
        ("silent", "deny", "src/deny", "POST_TICKET_SCOPE"),
        ("silent", "outside", "docs", "POST_TICKET_SCOPE"),
        ("silent", "ask", "src/ask", None),
        ("silent", "allow", "src/ok", None),
    )

    # チケットの無い作業ツリー。ルールだけで決まる。
    NO_TICKET = (
        ("deny", "POST_VIOLATION"),
        ("ask", "POST_VIOLATION"),
        ("allow", None),
        ("silent", None),
    )

    def setUp(self):
        super().setUp()
        self.family()

    def check(self, result, rel, code):
        if code is None:
            self.assertNotIn("POST_", result.stderr, result.stderr)
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn(code, result.stderr)
            self.assertIn(rel, result.stderr)

    def test_post_reports_follow_the_merged_verdict(self):
        for n, (rule_section, column, prefix, code) in enumerate(self.CASES):
            with self.subTest(rule=rule_section, ticket=column):
                self.use_rules(rules_for(rule_section))
                rel = f"{prefix}/case{n}.txt"
                self.check(self.after_shell(self.child, rel, session=f"post-{n}"), rel, code)

    def test_post_without_a_ticket_follows_the_rules_alone(self):
        for n, (rule_section, code) in enumerate(self.NO_TICKET):
            with self.subTest(rule=rule_section, ticket="no-ticket"):
                self.use_rules(rules_for(rule_section))
                rel = f"docs/free{n}.txt"
                self.check(self.after_shell(self.free, rel, session=f"free-{n}"), rel, code)


class TicketPlaces(Workspace):
    """チケットの置き場は範囲の外でも咎めない。ルールは作業ツリーを allow で開ける。"""

    def setUp(self):
        super().setUp()
        self.family(child={"allow": ("src/*",)})
        self.use_rules(rules_with("allow"))

    def test_pre_tool_use_exempts_the_ticket_places_only(self):
        # (相対パス, 判定, code)
        cases = (
            (f"{self.TICKETS}/todo/i0001-01.md", "allow", ""),
            (f"{self.TICKETS}/todo/i0009.md", "allow", ""),
            # 置き場の綴りの前置に続けただけの場所は置き場ではない。前置は `/` の境で切る。
            (f"{self.TICKETS}X/a.md", "deny", "DENY_TICKET_SCOPE"),
            ("docs/a.md", "deny", "DENY_TICKET_SCOPE"),
        )
        for rel, decision, code in cases:
            with self.subTest(path=rel):
                result = self.write_hook(self.child, rel)
                self.assertEqual(self.decision(result), decision, self.reason(result))
                self.assertEqual(self.last_record().get("code", ""), code, self.reason(result))

    def test_state_directories_stay_with_the_builtin_guard(self):
        result = self.write_hook(self.child, f"{self.TICKETS}/doing/i0009.md")
        self.assertEqual(self.decision(result), "deny")
        self.assertIn("builtin-ticket-state", self.reason(result))
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(result))

    def test_approved_tickets_stay_with_the_builtin_guard(self):
        result = self.write_hook(self.child, f"{self.APPROVED}/i0009.md", core="enable")
        self.assertEqual(self.decision(result), "deny", self.reason(result))
        self.assertIn("builtin-guard-project-home", self.reason(result))
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(result))

    def test_post_tool_use_exempts_the_ticket_places_only(self):
        # (相対パス, 報告のコード。None は報告しない)
        cases = (
            (f"{self.TICKETS}/todo/i0001-01.md", None),
            (f"{self.TICKETS}/todo/i0009.md", None),
            (f"{self.TICKETS}X/b.md", "POST_TICKET_SCOPE"),
            ("docs/b.md", "POST_TICKET_SCOPE"),
        )
        for n, (rel, code) in enumerate(cases):
            with self.subTest(path=rel):
                result = self.after_shell(self.child, rel, session=f"p{n}")
                if code is None:
                    self.assertNotIn("POST_TICKET_SCOPE", result.stderr, result.stderr)
                else:
                    self.assertIn(code, result.stderr)
                    self.assertIn(rel, result.stderr)

    def test_subagent_stop_leaves_the_proposals_alone(self):
        # 自分の提案も、他のチケットの提案も。
        for name in ("i0001-01", "i0009"):
            write(os.path.join(self.child, *f"{self.TICKETS}/todo/{name}.md".split("/")), "x\n")
        result = self.hook("SubagentStop", "", self.child, agent_id="sub-1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("POST_TICKET_SCOPE", result.stderr)

    def test_subagent_stop_bounces_a_lookalike_of_the_ticket_places(self):
        rel = f"{self.TICKETS}X/a.md"
        write(os.path.join(self.child, *rel.split("/")), "x\n")
        result = self.hook("SubagentStop", "", self.child, agent_id="sub-2")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("POST_TICKET_SCOPE", result.stderr)
        self.assertIn(rel, result.stderr)


class TicketPlacesElsewhere(TicketPlaces):
    """置き場の綴りを変えても、その綴りで外れる。既定の綴りを決め打ちしていないこと。"""

    TICKETS = "work/proposals"
    APPROVED = ".ccnavi/copies"


class Boundaries(Workspace):
    """チケットが効かない場面と、ルールより先に見る点検。

    ルールは作業ツリーを allow で開ける。
    """

    def test_parent_and_child_strictest_wins_under_rule_allow(self):
        """ルールが allow の場所でも、親子の判定は厳しい側を採る。"""
        self.family(
            parent={"allow": ("src/a/*",), "ask": ("src/b/*",)},
            child={"allow": ("src/a/*", "src/b/*")},
        )
        self.use_rules(rules_with("allow"))
        asked = self.write_hook(self.child, "src/b/x.py")
        self.assertEqual(self.decision(asked), "ask", self.reason(asked))
        self.assertIn("TICKET_ASK", self.reason(asked))
        allowed = self.write_hook(self.child, "src/a/x.py")
        self.assertEqual(self.decision(allowed), "allow", self.reason(allowed))

    def test_unapproved_proposal_does_nothing(self):
        self.propose("i0001", allow=("src/*",))
        self.use_rules(rules_with("allow"))
        result = self.write_hook(self.parent_tree, "docs/x.md")
        self.assertEqual(self.decision(result), "allow", self.reason(result))
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(result))

    def test_ticket_control_disabled_leaves_rules_alone(self):
        self.family()
        self.use_rules(rules_with("allow"))
        result = self.write_hook(
            self.child, TICKET_OUTSIDE, env={"CCNAVI_TICKET_CONTROL": "disable"}
        )
        self.assertEqual(self.decision(result), "allow", self.reason(result))
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(result))

    def test_main_tree_has_no_ticket(self):
        self.family()
        self.use_rules(
            {
                "version": 1,
                "allow": [{"id": "anything", "match": "Write|Edit", "glob": "*", "message": "x"}],
            }
        )
        result = self.write_hook(self.root, "docs/x.md")
        self.assertEqual(self.decision(result), "allow", self.reason(result))
        self.assertNotIn("DENY_TICKET_SCOPE", self.reason(result))

    def test_checks_before_the_rules_still_come_first(self):
        self.family()
        self.use_rules(
            {
                "version": 1,
                "allow": [{"id": "shell", "match": "Bash", "regex": ".", "message": "x"}],
            }
        )
        result = self.hook(
            "PreToolUse",
            "Bash",
            self.parent_tree,
            agent_id="sub-1",
            command="sh .ccnavi/scripts/ccnavi-ticket.sh done i0001-01",
        )
        self.assertIn("DENY_SUBAGENT_TICKET_OP", self.reason(result))

    def test_dry_run_says_what_enable_would_do(self):
        """ルールが allow の場所でチケットが止める判定も、dry-run では止めずに言うだけ。"""
        self.family()
        self.use_rules(rules_with("allow"))
        result = self.write_hook(self.child, TICKET_OUTSIDE, mode="dry-run")
        out = self.out(result)
        self.assertNotIn("permissionDecision", out)
        self.assertIn("would have stopped", out.get("additionalContext", ""))
        self.assertIn("DENY_TICKET_SCOPE", out.get("additionalContext", ""))

    def test_builtin_fallback_still_applies_the_ticket(self):
        self.family()
        write(self.rules, "version: 1\ndeny: [\n  - id: x\n")
        result = self.write_hook(self.child, TICKET_OUTSIDE)
        self.assertEqual(self.decision(result), "deny", self.reason(result))
        self.assertIn("DENY_TICKET_SCOPE", self.reason(result))

    def test_child_approval_screen_gets_no_new_note(self):
        """子の画面の「親からどれだけ絞ったか」には注記を添えない。注記は親の画面だけ。"""
        self.propose("i0001", allow=("src/*",))
        self.propose("i0001-01", parent="i0001", phase=1, allow=("src/a/*",))
        result = self.ccnavi("--approve", "--preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        child = section_of(result.stdout, "== i0001-01")
        self.assertTrue(child, result.stdout)
        self.assertNotIn(APPROVAL_NOTE, child)


class Diagnostics(Workspace):
    """診断の出力。"""

    def test_test_command_names_both_the_rule_and_the_ticket(self):
        self.family()
        self.use_rules(rules_with("allow"))
        target = os.path.join(self.child, "docs", "x.md")
        result = self.ccnavi("--test", "Write", target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verdict: deny (DENY_TICKET_SCOPE)", result.stdout)
        self.assertIn("(ticket-scope)", result.stdout)
        self.assertIn("rule-allow", result.stdout)

    def test_approval_screen_says_rule_allow_stops_outside_the_area(self):
        self.propose("i0001", allow=("src/*",))
        result = self.ccnavi("--approve", "--preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        # 見出し「このチケットで書き込みが許される領域」の節の中に出る。
        parent = section_of(result.stdout, "■ このチケットで書き込みが許される領域")
        self.assertIn(APPROVAL_NOTE, parent, result.stdout)

    def test_explain_says_the_ticket_is_stronger_than_rule_allow(self):
        self.family()
        result = self.ccnavi("--explain")
        self.assertEqual(result.returncode, 0, result.stderr)
        # 「チケットの作業範囲」の節の頭。チケット制御の行と並ぶ最初の 2 行のうちに出る。
        section = section_of(result.stdout, "■ チケットの作業範囲（承認済みチケット）")
        head = [line.strip() for line in section.strip().splitlines()][:2]
        self.assertIn(EXPLAIN_NOTE, head, result.stdout)


if __name__ == "__main__":
    unittest.main()
