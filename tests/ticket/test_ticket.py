"""並行するチケットの道具（ほかの tests/ticket が借りる）。

テストは Go（`go/internal/clitest/ticket/core`）へ移した。ここに残るのは、まだ Python のテストが
借りる git・write・read_json・ticket_text と TicketTest の道具だけで、テストは持たない。
実際の git リポジトリとワークツリーを一時ディレクトリに作り、親 1 本と子 2 本をフェーズ 1 つで通す。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT, common_path, treecache, userform, write_and_push_flag
from tests.inproc import run_ccnavi
from tests.ticketsample import (
    RULES,
    plain_parent,
    record_merge,
    write_plain_phases,
)


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


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def ticket_text(
    name,
    *,
    parent="",
    phase=None,
    allow=(),
    ask=(),
    review=True,
    predecessors=(),
    title="作業",
    issue=None,
    plan=1,
):
    lines = ["---", "version: 1", f"ticket: {name}"]
    if not parent:
        lines.append("mr: 1")
        if issue is None:
            issue = 1
    if issue is not None:
        lines.append(f"issue: {issue}")
    if parent:
        lines += [f"parent: {parent}", f"phase: {phase}"]
    if predecessors:
        lines.append("predecessors: [" + ", ".join(predecessors) + "]")
    lines += [
        "human_review:",
        f"  required: {'true' if review else 'false'}",
        "  reason: テスト",
        f"title: {title}",
        "rationale: |",
        "  理由",
    ]
    for section, globs in (("allow", allow), ("ask", ask)):
        if globs:
            lines.append(f"{section}:")
            for g in globs:
                lines += ["  - match: Write|Edit", f'    glob: "{g}"']
    lines += ['started_at: ""', 'completed_at: ""', 'base_sha: ""', "---", "", "本文"]
    return plain_parent("\n".join(lines) + "\n", plan)


class TicketTest(unittest.TestCase):
    def setUp(self):
        self.root = treecache.root(self)
        self.build_tree()

    @treecache.cached
    def build_tree(self):
        """リポジトリと親のワークツリーを組む。2 度目からは写しを戻す（`tests/treecache.py`）。"""
        os.makedirs(os.path.join(self.root, ".claude"))
        git(self.root, "init", "--quiet", "-b", "main")
        write(os.path.join(self.root, "src", "keep.py"), "print(1)\n")
        write(os.path.join(self.root, ".gitignore"), ".claude/\nwip/tmp/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "init")

        # 共通レイヤーは既定の置き場に置く。`--rules` は診断でだけ有効なので渡せない。
        self.rules = write(common_path(self.root, "rules"), json.dumps(RULES))
        write_plain_phases(self.root)
        self.state = os.path.join(self.root, "logs", "state")
        self.parent_tree = self.worktree("i0001", "main")
        # 承認済みチケットとマーカーは親のツリーに置かれ、親のブランチに乗る。
        self.approved = os.path.join(self.parent_tree, ".ccnavi", "approved")

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
        # 学びの蓄積の既定は enable。このテストは学びを扱わないので切る（扱うテストは env で渡す）。
        environment["CCNAVI_LEARNING"] = "disable"
        environment.update(env or {})
        args = self.as_write_and_push(args)
        return run_ccnavi(
            userform.place(
                [
                    "--root",
                    self.root,
                    "--guard-core-files",
                    "disable",
                    "--restore-if-deny",
                    "disable",
                    # ユーザの判断の経路のターミナル要求は切る。テストはターミナルを持たない。
                    # 経路そのものの検査は、個別に enable を渡す。
                    "--guard-ticket-approval",
                    "disable",
                ],
                args,
            ),
            input=stdin,
            cwd=ROOT,
            env=environment,
        )

    def as_write_and_push(self, args):
        """sh を通さない呼び出しに、write-and-push
        の印を付ける（`tests/write_and_push_flag.py`）。"""
        return write_and_push_flag.as_write_and_push(self.root, self.state, self.parent_tree, args)

    def hook(
        self,
        event,
        tool,
        cwd,
        mode="enable",
        agent_id="",
        guard_ticket_approval="",
        session="s1",
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
        extra = ["--guard-ticket-approval", guard_ticket_approval] if guard_ticket_approval else []
        return self.ccnavi("--mode", mode, *extra, stdin=json.dumps(payload))

    def reason(self, result):
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout).get("hookSpecificOutput", {})
        return out.get("permissionDecisionReason") or out.get("additionalContext") or ""

    def propose(self, name, tree=None, **kw):
        """提案を書く。`tree` を渡さなければ親 i0001 のワークツリーに書く。

        別の親の提案は、識別子と同じ名前のワークツリー（`tree`）に書く。別名のツリーの提案は
        承認が止まる。
        """
        return write(
            os.path.join(tree or self.parent_tree, "wip", "proposals", "todo", name + ".md"),
            ticket_text(name, **kw),
        )

    def approve(self, answer="y"):
        """承認して、承認済みチケットを親のブランチに乗せる。

        承認済みチケットは親のツリーに置かれ、コミットして初めて子のワークツリーへ渡る。
        本番で `ccnavi-user-agree.sh` がやることを、テストでも同じ順でたどる。
        """
        result = self.ccnavi("user", "agree", stdin=answer + "\n")
        if os.path.isdir(self.approved):
            git(self.parent_tree, "add", "-A")
            git(self.parent_tree, "commit", "--quiet", "--allow-empty", "-m", "approve")
        return result

    def start_parent(self, name="i0001"):
        """親を着手する（済んでいれば何もしない）。

        子の着手は親が着手済みであることを前提にする。親を飛ばしたまま
        子を進められたころの手順をそのまま残すと、最初の子の着手で止まる。
        """
        started = self.ccnavi("ticket", "start", name)
        if started.returncode != 0:
            self.assertIn("着手済み", started.stderr, started.stdout + started.stderr)

    @treecache.cached
    def family(
        self, review=(True, False), plan=1, allow=("src/*", "wip/*"), child_allow=("src/a/*",)
    ):
        """親 1 本と子 2 本をフェーズ 1 で提案し、承認して、子のワークツリーを作って着手する。"""
        self.propose("i0001", allow=allow, plan=plan)
        self.propose("i0001-01-01", parent="i0001", phase=1, allow=child_allow, review=review[0])
        self.propose("i0001-01-02", parent="i0001", phase=1, allow=("src/b/*",), review=review[1])
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "tickets")
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.start_parent()
        for child in ("i0001-01-01", "i0001-01-02"):
            self.worktree(child, "i0001")
            started = self.ccnavi("ticket", "start", child)
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        # 状態の移動は親がコミットする。参考にした運用と同じ。
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "start")

    def hand_move(self, name, text=""):
        """ユーザが GitHub の画面でやることと同じ。提案を承認済みの置き場へ動かすだけ。

        `ccnavi_approved` は足さない。ターミナルもボードも無いユーザには足す手段が無い。
        """
        source = os.path.join(self.parent_tree, "wip", "proposals", "todo", name + ".md")
        with open(source, encoding="utf-8") as f:
            moved = text or f.read()
        os.remove(source)
        write(os.path.join(self.approved, "doing", name + ".md"), moved)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "move " + name)

    def family_without_starting(self):
        """親 1 本と子 1 本を承認して、子のワークツリーだけ作る（どちらも未着手）。"""
        self.propose("i0001", allow=("src/*", "wip/*"))
        self.propose("i0001-01-01", parent="i0001", phase=1, allow=("src/a/*",))
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "tickets")
        result = self.approve()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.worktree("i0001-01-01", "i0001")

    @treecache.cached
    def close_phase(self):
        for child in ("i0001-01-01", "i0001-01-02"):
            done = self.ccnavi("ticket", "finish", child)
            self.assertEqual(done.returncode, 0, done.stderr)
        git(self.parent_tree, "add", "-A")
        git(self.parent_tree, "commit", "--quiet", "-m", "done")

    @treecache.cached
    def remote(self, merge=("i0001-01-01", "i0001-01-02")):
        """origin と、合流して push した親ブランチ。リモートから取得した結果（JSON）の置き場を返す。

        sh がリモートから取ってくる形そのもの。テストはネットワークに出ないので、
        sh の代わりにこのファイルを --result で渡す。
        """
        bare = os.path.join(self.root, "origin.git")
        git(self.root, "init", "--quiet", "--bare", bare)
        git(self.root, "remote", "add", "origin", bare)
        for child in merge:
            git(self.parent_tree, "merge", "--quiet", "--no-edit", child)
            record_merge(self.parent_tree, child)
        git(self.parent_tree, "push", "--quiet", "origin", "i0001")
        fixture = os.path.join(self.root, "fixture.json")
        write(
            fixture,
            json.dumps({"host": "fixture", "mr": {"number": 7, "url": "https://example/mr/7"}}),
        )
        return fixture

    def request(self, fixture, phase="1"):
        """sh の request と同じ 3 段階。prepare → 投稿（承認済みチケットに書く）→ requested。"""
        body = write(os.path.join(self.root, "body.md"), "見てほしい点\n")
        prepared = self.ccnavi(
            "--cwd", self.parent_tree, "--phase", phase, "--body-file", body, "review", "prepare"
        )
        if prepared.returncode != 0:
            return prepared
        # 出力は依頼の本文のパス 1 行だけ。
        (body_path,) = prepared.stdout.splitlines()
        with open(body_path, encoding="utf-8") as f:
            text = f.read()
        data = read_json(fixture)
        posted = data.setdefault("comments", [])
        url = f"{data['mr']['url']}#note-{len(posted) + 1}"
        posted.append({"body": text, "url": url, "created_at": "2026-01-01T00:00:00Z"})
        write(fixture, json.dumps(data))
        result = write(
            os.path.join(self.root, "posted.json"),
            json.dumps(
                {
                    "host": "fixture",
                    "mr": data["mr"],
                    "url": url,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ),
        )
        return self.ccnavi(
            "--cwd", self.parent_tree, "--phase", phase, "review", "requested", "--result", result
        )

    def two_threads(self):
        """フェーズ 1 を依頼し、未解決の指摘を 2 件付けた結果を返す。"""
        self.family()
        self.close_phase()
        fixture = self.remote()
        self.assertEqual(self.request(fixture).returncode, 0)
        data = read_json(fixture)
        data["threads"] = [
            {"id": "t1", "resolved": False, "url": "u1", "path": "src/a/x.py", "body": "命名"},
            {"id": "t2", "resolved": False, "url": "u2", "path": "src/b/y.py", "body": "テスト"},
        ]
        write(fixture, json.dumps(data))
        return fixture

    def decide(self, fixture, *extra, stdin=""):
        return self.ccnavi(
            "--cwd",
            self.parent_tree,
            "user",
            "reviewed",
            "1",
            "--accept-unresolved",
            "--result",
            fixture,
            *extra,
            stdin=stdin,
        )

    def commit(self, message):
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", message)

    def snapshot(self):
        """self.root の今をコピーし、コピーの置き場を返す。"""
        dest = os.path.join(tempfile.mkdtemp(prefix="ccnavi-ticket-snap-"), "root")
        self.addCleanup(shutil.rmtree, os.path.dirname(dest), ignore_errors=True)
        shutil.copytree(self.root, dest, symlinks=True)
        return dest

    def reviewed(self, fixture):
        """ユーザがターミナルで打つ経路（--reviewed）。未解決の指摘は受け入れる。"""
        return self.ccnavi(
            "--cwd",
            self.parent_tree,
            "user",
            "reviewed",
            "1",
            "--accept-unresolved",
            "--result",
            fixture,
            stdin="k\n",
        )

    # 動かし方。どれも「依頼の前」か「依頼の後」のコピーから始まる。

    def confirm(self, fixture, phase="1"):
        return self.ccnavi(
            "--cwd", self.parent_tree, "--phase", phase, "review", "confirm", "--result", fixture
        )


if __name__ == "__main__":
    unittest.main()
