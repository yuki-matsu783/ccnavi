"""親と子を動かす土台（PhaseHarness）。

テストはここに持たない。フェーズ・計画・承認・レビュー・範囲の上限・テンプレート・リスクのテストは
Go（go/internal/clitest/ticket/phases）へ移した。ほかの Python のテストが継いで使うので、
土台だけを残す（それらが Go へ移ったら、このファイルごと消す）。
"""

from __future__ import annotations

import json
import os
import unittest

from tests import (
    common_path,
    config_path,
    treecache,
    userform,
    write_and_push_flag,
)
from tests.inproc import run_ccnavi
from tests.ticket.test_ticket import ROOT, git, read_json, write

# child_text は、ほかのテストがここから読む（`from tests.ticket.test_phases import child_text`）。
from tests.ticketsample import PHASES, RULES, child_text, parent_text, record_merge  # noqa: F401


class PhaseHarness(unittest.TestCase):
    """親と子を動かす道具。テストは持たない（他のテストが継いで使う）。"""

    # 親の `phases:` にコピーする元の phases.yml の本文。phases.yml を差し替えるテストは上書きする。
    phases_text = PHASES
    # sh（write-and-push）を通らずに状態の操作を直に起こすテストは、write-and-push
    # の印を付ける（`tests/write_and_push_flag.py`）。
    # 印が無い呼び出しを断る検査は False にする。
    mark_write_and_push = True

    def setUp(self):
        self.root = treecache.root(self)
        self.build_tree()

    @treecache.cached
    def build_tree(self):
        """リポジトリと親のワークツリーを組み、クラスの前置き（`prepare`）を足す。

        2 度目からは写しを戻す（`tests/treecache.py`）。
        """
        os.makedirs(os.path.join(self.root, ".claude"))
        git(self.root, "init", "--quiet", "-b", "main")
        write(os.path.join(self.root, "src", "keep.py"), "print(1)\n")
        write(os.path.join(self.root, ".gitignore"), ".claude/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "init")
        # 共通レイヤーは `--root` の下の既定の置き場に置く。`--rules` / `--phases` は診断
        # （`--lint` / `--test` / `--explain`）でだけ有効なので、hook の判定と `--reviewed`
        # には渡せない。差し替えたいテストはこのファイルに書き直す。
        self.rules = write(common_path(self.root, "rules"), json.dumps(RULES))
        self.phases = write(config_path(self.root, "phases"), PHASES)
        self.state = os.path.join(self.root, "logs", "state")
        self.parent_tree = self.worktree("i0001", "main")
        # 承認済みチケットとマーカーは親のツリーに置かれ、親のブランチに乗る。
        self.approved = os.path.join(self.parent_tree, ".ccnavi", "approved")
        self.prepare()

    def prepare(self):
        """クラスのどのテストも同じところから始める前置き。継ぐクラスが書く（写しに入る）。"""

    # ---- 道具

    def worktree(self, name, base):
        path = os.path.join(self.root, ".claude", "worktrees", name)
        git(self.root, "worktree", "add", "--quiet", path, "-b", name, base)
        return path

    def ccnavi(self, *args, stdin="", env=None):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        # 学びの蓄積の既定は enable。学びを扱わないテストは切る（扱うテストは env で渡す）。
        environment.update(env or {})
        if self.mark_write_and_push:
            args = write_and_push_flag.as_write_and_push(
                self.root, self.state, self.parent_tree, args
            )
        return run_ccnavi(
            userform.place(
                [
                    "--root",
                    self.root,
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

    def hook(self, event, tool, cwd, mode="enable", agent_id="", permission_mode="", **tool_input):
        payload = {
            "hook_event_name": event,
            "tool_name": tool,
            "cwd": cwd,
            "session_id": "s1",
            "tool_input": tool_input,
        }
        if agent_id:
            payload["agent_id"] = agent_id
        # Claude Code 側の権限モード。どのルールも言及しない呼び出しの結末がこれで変わる
        # （judge.UndeclaredVerdict）ので、そこを見るテストだけが渡す。
        if permission_mode:
            payload["permission_mode"] = permission_mode
        return self.ccnavi("--mode", mode, stdin=json.dumps(payload))

    def reason(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecisionReason") or out.get("additionalContext") or ""

    def propose(self, name, text, tree=None):
        """提案を書く。`tree` が無ければ親 i0001 のワークツリー。別の親は同名のツリーに書く。"""
        return write(
            os.path.join(tree or self.parent_tree, "wip", "proposals", "todo", name + ".md"), text
        )

    def approve(self):
        """承認して、承認済みチケットを親のブランチに乗せる。

        承認済みチケットは親のツリーに置かれるので、コミットするまでワークツリーは汚れたまま。
        本番で `ccnavi-user-agree.sh` がやることを、テストでも同じ順でたどる。
        """
        result = self.ccnavi("user", "agree", stdin="y\n")
        if os.path.isdir(self.approved):
            git(self.parent_tree, "add", "-A")
            git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "approve")
        return result

    def commit_parent(self, message="tickets"):
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", message)

    def start_parent(self, name="i0001"):
        """親を着手する（済んでいれば何もしない）。

        子の着手は親が着手済みであることを前提にする。親を飛ばしたまま
        子を進められたころの手順をそのまま残すと、最初の子の着手で止まる。
        """
        started = self.ccnavi("ticket", "start", name)
        if started.returncode != 0:
            self.assertIn("着手済み", started.stderr, started.stdout + started.stderr)

    def run_child(self, name, files=()):
        """子のワークツリーを作って着手し、ファイルを置いてコミットし、閉じる。"""
        self.start_parent()
        tree = self.worktree(name, "i0001")
        started = self.ccnavi("ticket", "start", name)
        self.assertEqual(started.returncode, 0, started.stderr)
        for rel, text in files:
            write(os.path.join(tree, *rel.split("/")), text)
        git(tree, "add", "-A")
        if files:
            git(tree, "commit", "--quiet", "-m", name)
        return tree

    def close_child(self, name):
        return self.ccnavi("ticket", "finish", name)

    def merge(self, child):
        git(self.parent_tree, "merge", "--quiet", "--no-edit", child)
        record_merge(self.parent_tree, child)

    def remote(self):
        bare = os.path.join(self.root, "origin.git")
        git(self.root, "init", "--quiet", "--bare", bare)
        git(self.root, "remote", "add", "origin", bare)
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        fixture = os.path.join(self.root, "fixture.json")
        write(
            fixture,
            json.dumps({"host": "fixture", "mr": {"number": 7, "url": "u/7", "draft": True}}),
        )
        return fixture

    def request(self, fixture, phase):
        body = write(os.path.join(self.root, "body.md"), "見てほしい\n")
        prepared = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            str(phase),
            "--body-file",
            body,
            "review",
            "prepare",
        )
        if prepared.returncode != 0:
            return prepared
        body_path = prepared.stdout.splitlines()[0]
        with open(body_path, encoding="utf-8") as f:
            self.last_request_body = f.read()
        data = read_json(fixture)
        result = write(
            os.path.join(self.root, "posted.json"),
            json.dumps(
                {
                    "host": "fixture",
                    "mr": data["mr"],
                    "url": "u/7#1",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ),
        )
        return self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            str(phase),
            "review",
            "requested",
            "--result",
            result,
        )

    def confirm(self, fixture, phase):
        return self.ccnavi(
            "--cwd",
            self.parent_tree,
            "--phase",
            str(phase),
            "review",
            "confirm",
            "--result",
            fixture,
        )

    def board_phase(self, number, parent="i0001"):
        """ボードの JSON のフェーズ 1 つを (gate_closed, review_waiting, マーカーの種類) で。"""
        result = self.ccnavi("--explain", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        board = json.loads(result.stdout)
        owner = next(p for p in board["parents"] if p["ticket"] == parent)
        ph = next(p for p in owner["phases"] if p["number"] == number)
        return ph["gate_closed"], ph["review_waiting"], sorted(ph["marks"])

    @treecache.cached
    def family(self, plan=("research", "design"), feedback=None):
        """親を提案して承認する。plan の 1 番目の子もまとめて承認する。"""
        self.propose("i0001", parent_text("i0001", list(plan), feedback, copy=self.phases_text))
        self.commit_parent()
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result
