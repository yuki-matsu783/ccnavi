"""学びの蓄積の CLI と記録の書き方を、src の誤りで壊れたら落ちる形で確かめる。"""

from __future__ import annotations

import dataclasses
import io
import json
import os
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest import mock

from ccnavi.infra import settings
from ccnavi.infra import tree as tree_mod
from ccnavi.records import redact
from ccnavi.tickets.learning import notes as learning
from tests import requires_symlink
from tests.inproc import run_ccnavi


class LearningBase(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = tempfile.mkdtemp(prefix="ccnavi-learn-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        subprocess.run(
            ["git", "init", "--quiet", "-b", "main"], cwd=self.ws, check=True, capture_output=True
        )
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        self.env.pop("CLAUDE_PROJECT_DIR", None)
        self.env["CCNAVI_LEARNING"] = "enable"

    def learn(self, *words: str, cwd: str | None = None):
        return run_ccnavi(
            ["--root", self.ws, "ticket", "learn", *words],
            input="",
            env=self.env,
            cwd=cwd or self.ws,
        )

    def direct_worktree(self, name: str = "direct-1-x") -> str:
        """チケットに結び付かない直接作業のワークツリーを切る。"""
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=a@b.c",
                "-c",
                "user.name=n",
                "commit",
                "--allow-empty",
                "-q",
                "-m",
                "init",
            ],
            cwd=self.ws,
            check=True,
            capture_output=True,
        )
        wt = os.path.join(self.ws, ".claude", "worktrees", name)
        subprocess.run(
            ["git", "worktree", "add", wt, "-b", name],
            cwd=self.ws,
            check=True,
            capture_output=True,
        )
        return wt

    def read(self, path: str) -> str:
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()

    def enter_child(self, child: str, wt: str) -> None:
        """`child` のワークツリーの中から打ったことにする（`wt` がそのワークツリー）。"""
        patches = [
            mock.patch.object(
                learning.phase,
                "worktree_at",
                return_value=tree_mod.Tree(name=child, root=wt, kind=tree_mod.KIND_WORKTREE),
            ),
            mock.patch.object(
                learning,
                "_bound_ticket",
                return_value=types.SimpleNamespace(
                    parent="feat-1", is_child=True, is_consolidate=False
                ),
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class LearningMoreTest(LearningBase):
    def test_inbox_names_do_not_collide(self) -> None:
        wt = self.direct_worktree()
        for marker in ("気づきA", "気づきB", "気づきC"):
            result = self.learn("--note", marker, cwd=wt)
            self.assertEqual(result.returncode, 0, result.stderr)
        inbox = os.path.join(wt, "docs", "wiki", "inbox")
        names = sorted(os.listdir(inbox))
        self.assertEqual(len(names), 3, names)
        found = []
        for name in names:
            text = self.read(os.path.join(inbox, name))
            hits = [m for m in ("気づきA", "気づきB", "気づきC") if m in text]
            self.assertEqual(len(hits), 1, (name, hits))
            found.append(hits[0])
        self.assertEqual(sorted(found), ["気づきA", "気づきB", "気づきC"])

    def test_direct_worktree_inbox_goes_to_docs_wiki(self) -> None:
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=a@b.c",
                "-c",
                "user.name=n",
                "commit",
                "--allow-empty",
                "-q",
                "-m",
                "init",
            ],
            cwd=self.ws,
            check=True,
            capture_output=True,
        )
        wt = os.path.join(self.ws, ".claude", "worktrees", "direct-1-x")
        subprocess.run(
            ["git", "worktree", "add", wt, "-b", "direct-1-x"],
            cwd=self.ws,
            check=True,
            capture_output=True,
        )
        result = self.learn("--note", "直接作業の気づき", cwd=wt)
        self.assertEqual(result.returncode, 0, result.stderr)
        inbox = os.path.join(wt, "docs", "wiki", "inbox")
        names = os.listdir(inbox)
        self.assertEqual(len(names), 1, names)
        self.assertTrue(names[0].startswith("direct-1-x-"), names)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "wip", "handoff", "inbox")))

    def test_bound_child_appends_and_recounts(self) -> None:
        child = "feat-1-01-01"
        wt = os.path.join(self.ws, ".claude", "worktrees", child)
        os.makedirs(wt)
        self.enter_child(child, wt)
        conf = dataclasses.replace(settings.load(self.ws)[0], learning="enable")
        rec = os.path.join(wt, "wip", "handoff", "learnings", f"{child}.md")

        rc = learning.run(
            io.StringIO(), io.StringIO(), self.ws, conf, self.ws, "", "最初の気づき", "skill", ""
        )
        self.assertEqual(rc, 0)
        self.assertIn("learnings: 1", self.read(rec))

        rc = learning.run(
            io.StringIO(), io.StringIO(), self.ws, conf, self.ws, "", "2 件目の気づき", "skill", ""
        )
        self.assertEqual(rc, 0)
        text = self.read(rec)
        self.assertIn("learnings: 2", text)
        heads = [line for line in text.split("\n") if line.startswith("## ")]
        self.assertEqual(len(heads), 2, heads)

    def test_learn_without_note_keeps_existing_record(self) -> None:
        child = "feat-1-01-01"
        wt = os.path.join(self.ws, ".claude", "worktrees", child)
        os.makedirs(os.path.join(wt, "wip", "handoff", "learnings"))
        self.enter_child(child, wt)
        conf = dataclasses.replace(settings.load(self.ws)[0], learning="enable")

        text = learning.append_entry(
            learning.scaffold_text(child, "feat-1"),
            learning.entry_text("既存の気づき", "docs", "", "2026-01-01T00:00:00+0900"),
            child,
            "feat-1",
        )
        rec = os.path.join(wt, "wip", "handoff", "learnings", f"{child}.md")
        with open(rec, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        before = self.read(rec)

        rc = learning.run(io.StringIO(), io.StringIO(), self.ws, conf, self.ws, "", "", "", "")
        self.assertEqual(rc, 0)
        self.assertEqual(self.read(rec), before)

        digest_path = os.path.join(wt, "wip", "handoff", "learnings", f"{child}.digest.json")
        with open(digest_path, encoding="utf-8") as f:
            digest = json.load(f)
        for key in ("recorded", "counts", "repeated"):
            self.assertIn(key, digest)

    def test_signal_and_target_rejected_via_cli(self) -> None:
        wt = self.direct_worktree()
        result = self.learn("--note", "x", "--signal", "zzz", cwd=wt)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--signal", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(wt, "docs")))

        for blank in ("", "   "):
            result = self.learn("--note", blank, cwd=wt)
            self.assertNotEqual(result.returncode, 0, repr(blank))
        self.assertFalse(os.path.exists(os.path.join(wt, "docs")))

    def test_secret_is_redacted_in_note(self) -> None:
        secret = "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyz"
        self.assertNotEqual(redact.redact(secret), secret)
        wt = self.direct_worktree()
        result = self.learn("--note", f"接続に使った {secret} を忘れない", cwd=wt)
        self.assertEqual(result.returncode, 0, result.stderr)
        inbox = os.path.join(wt, "docs", "wiki", "inbox")
        (name,) = os.listdir(inbox)
        text = self.read(os.path.join(inbox, name))
        self.assertNotIn(secret, text)

    def test_cr_cannot_forge_a_section(self) -> None:
        entry = learning.entry_text("本当の気づき\r\r## 偽の気づき\r\r- 種類: rule", "", "", "t")
        heads = [line for line in entry.splitlines() if line.startswith("## ")]
        self.assertEqual(len(heads), 1, heads)
        text = learning.append_entry(learning.scaffold_text("a", ""), entry)
        self.assertIn("learnings: 1", text)
        heads = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(len(heads), 1, heads)

    @requires_symlink
    def test_symlink_destination_is_refused(self) -> None:
        outside = tempfile.mkdtemp(prefix="ccnavi-learn-outside-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, "wip"))
        os.symlink(outside, os.path.join(self.ws, "wip", "handoff"))
        result = self.learn("--note", "x")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(os.listdir(outside), [])

    def test_digest_scoping_and_env_values(self) -> None:
        def deny(tree_name: str) -> dict:
            return {
                "decision": "deny",
                "enforced": True,
                "rules": ["r"],
                "tool": "Bash",
                "subject": "DB_PASSWORD=hunter2 psql -c x",
                "tree": tree_name,
            }

        records = [deny("t1"), deny("t1"), deny("t2"), deny("t2"), deny("t2")]

        scoped = learning.build_digest(records, True, tree_name="t1")
        self.assertEqual(scoped["counts"]["deny"], 2)
        self.assertEqual(len(scoped["repeated"]), 1, scoped["repeated"])
        item = scoped["repeated"][0]
        self.assertEqual(item["word"], "psql")
        self.assertEqual(item["count"], 2)
        self.assertNotIn("hunter2", json.dumps(scoped))

        whole = learning.build_digest(records, True, tree_name=None)
        self.assertEqual(whole["counts"]["deny"], 5)

        self.assertEqual(learning.command_word("FOO=bar env /usr/bin/git push"), "git")

    def test_workspace_and_project_roots_refuse_and_point_to_a_worktree(self) -> None:
        result = self.learn("--note", "n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ワークツリーを切", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "wip")))
        result = self.learn()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "wip")))
        proj = os.path.join(self.ws, "projects", "foo")
        os.makedirs(proj)
        subprocess.run(
            ["git", "init", "--quiet", "-b", "main"], cwd=proj, check=True, capture_output=True
        )
        result = self.learn("--note", "n", cwd=proj)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ワークツリーを切", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(proj, "wip")))
        self.assertFalse(os.path.exists(os.path.join(self.ws, "wip")))

    def test_line_separators_cannot_forge_a_section(self) -> None:
        entry = learning.entry_text("本当 ## 偽 - 種類: rule\x85## 偽2", "", "", "t")
        heads = [line for line in entry.splitlines() if line.startswith("## ")]
        self.assertEqual(len(heads), 1, heads)

    def test_directory_is_not_a_record(self) -> None:
        child = "feat-1-01-01"
        wt = os.path.join(self.ws, ".claude", "worktrees", child)
        os.makedirs(wt)
        subprocess.run(
            ["git", "init", "--quiet", "-b", "main"], cwd=wt, check=True, capture_output=True
        )
        conf, _ = settings.load(self.ws)
        conf = dataclasses.replace(conf, learning="enable")
        found = types.SimpleNamespace(is_child=True, is_consolidate=False, ticket=child, phase=None)

        rec_dir = os.path.join(wt, learning.record_rel(child))
        os.makedirs(rec_dir)
        with open(os.path.join(rec_dir, "a"), "w", encoding="utf-8") as f:
            f.write("x\n")
        subprocess.run(
            ["git", "add", "-f", learning.record_rel(child)],
            cwd=wt,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-c", "user.email=a@b.c", "-c", "user.name=n", "commit", "-q", "-m", "x"],
            cwd=wt,
            check=True,
            capture_output=True,
        )
        self.assertTrue(learning.record_missing(io.StringIO(), self.ws, conf, found))

    def test_command_word_table(self) -> None:
        cases = {
            'DB_PASSWORD="my secret pw" psql': "psql",
            "TOKEN='abc def' curl x": "curl",
            r"PASS=abc\ def cmd": "cmd",
            "FOO=1 BAR=$(cat /home/alice/.secret) cmd": "",
            '"/home/alice/My Docs/run.sh" a': "run.sh",
            "env -i HOME=/x git push": "git",
            "FOO=bar env /usr/bin/git push": "git",
            "/usr/bin/git push": "git",
        }
        for subject, expected in cases.items():
            with self.subTest(subject=subject):
                self.assertEqual(learning.command_word(subject), expected)

    def test_non_utf8_record_is_reported(self) -> None:
        child = "feat-1-01-01"
        wt = os.path.join(self.ws, ".claude", "worktrees", child)
        os.makedirs(wt)
        self.enter_child(child, wt)
        conf = dataclasses.replace(settings.load(self.ws)[0], learning="enable")
        rec = os.path.join(wt, "wip", "handoff", "learnings", f"{child}.md")
        os.makedirs(os.path.dirname(rec))
        with open(rec, "wb") as f:
            f.write("あ".encode("shift_jis"))

        rc = learning.run(io.StringIO(), io.StringIO(), self.ws, conf, self.ws, "", "n", "", "")
        self.assertEqual(rc, 1)

    def test_empty_record_is_rescaffolded(self) -> None:
        child = "feat-1-01-01"
        wt = os.path.join(self.ws, ".claude", "worktrees", child)
        os.makedirs(wt)
        self.enter_child(child, wt)
        conf = dataclasses.replace(settings.load(self.ws)[0], learning="enable")
        rec = os.path.join(wt, "wip", "handoff", "learnings", f"{child}.md")
        os.makedirs(os.path.dirname(rec))
        with open(rec, "w", encoding="utf-8", newline="") as f:
            f.write("  \n")

        rc = learning.run(io.StringIO(), io.StringIO(), self.ws, conf, self.ws, "", "", "", "")
        self.assertEqual(rc, 0)
        self.assertIn("learnings: none", self.read(rec))


if __name__ == "__main__":
    unittest.main()
