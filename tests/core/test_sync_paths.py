"""`ccnavi sync paths` と `ccnavi sync migrate` の約束。

`ccnavi-sync.sh` は jq を使わず JSON も読まない。置き場のパスを、
実行ファイルが 1 行 1 項目（`<鍵> <値>`）で返す。
統合先の名前は返さない（sh が各リポジトリの `origin/HEAD` で決める）。
置き場は固定で、env では動かない（設計 wip/design/i0064-fixed-places.md）。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests.inproc import run_ccnavi


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


class SyncPathsTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-sync-paths-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))

    def paths(self, env=None):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        result = run_ccnavi(["--root", self.ws, "sync", "paths"], env=environment)
        self.assertEqual(0, result.returncode, result.stderr)
        return dict((line.split(" ", 1) + [""])[:2] for line in result.stdout.splitlines())

    def test_defaults(self):
        self.assertEqual(
            {
                "approved": ".ccnavi/approved",
                "proposals": "wip/proposals",
                "home": ".ccnavi",
            },
            self.paths(),
        )

    def test_places_do_not_follow_the_environment(self):
        # 置き場を動かしていた 6 つの env を、既定と違う綴り（相対・絶対・末尾 `/` 付き）で
        # 全部入れても、返すのは既定の置き場。項目ごとではなく辞書全体で見る。
        elsewhere = os.path.join(tempfile.gettempdir(), "ccnavi-elsewhere")
        found = self.paths(
            {
                "CCNAVI_TICKETS_APPROVED": "tickets/approved/",
                "CCNAVI_TICKETS_PROPOSAL": os.path.join(elsewhere, "proposals"),
                "CCNAVI_PROJECT_HOME": ".nav",
                "CCNAVI_PROJECTS": "repos/",
                "CCNAVI_LOG": os.path.join(elsewhere, "log.jsonl"),
                "CCNAVI_STATE": "state",
            }
        )
        self.assertEqual(
            {
                "approved": ".ccnavi/approved",
                "proposals": "wip/proposals",
                "home": ".ccnavi",
            },
            found,
        )

    def test_works_with_ticket_control_disabled(self):
        found = self.paths({"CCNAVI_TICKET_CONTROL": "disable"})
        self.assertEqual(".ccnavi/approved", found["approved"])

    def test_place_flags_are_ignored_outside_diagnosis(self):
        # sh は受け取った引数を `"$@"` で渡すので、
        # サブコマンドに置き場のフラグを足されても効かない。
        # 無視したことは標準エラーに出す。
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        elsewhere = os.path.join(self.ws, "elsewhere")
        result = run_ccnavi(
            [
                "--root",
                self.ws,
                "--approved",
                "x/approved",
                "--tickets",
                "x/proposals",
                "--state",
                elsewhere,
                "--log",
                "",
                "sync",
                "paths",
            ],
            env=environment,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        found = dict((line.split(" ", 1) + [""])[:2] for line in result.stdout.splitlines())
        self.assertEqual(".ccnavi/approved", found["approved"])
        self.assertEqual("wip/proposals", found["proposals"])
        for flag in ("--approved", "--tickets", "--state", "--log"):
            self.assertIn(f"ccnavi: {flag} は診断", result.stderr)


class SyncMigrateTest(unittest.TestCase):
    """`ccnavi sync migrate`。古い取り込み状態から `retire/<親>.json` への 1 回限りの移行。"""

    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-sync-migrate-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        os.makedirs(os.path.join(self.ws, ".claude"))
        self.state = os.path.join(self.ws, "logs", "state")

    def migrate(self):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        result = run_ccnavi(["--root", self.ws, "sync", "migrate"], env=environment)
        self.assertEqual(0, result.returncode, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual("migrate 1", lines[0])
        return lines[1:]

    def legacy(self, repo, name, mr="12", branch=None, state="gone"):
        lines = ["remote origin", f"branch {name if branch is None else branch}", "sha abc"]
        lines += ["fetched_at 1", f"state {state}", "reason 理由"]
        if mr is not None:
            lines.append(f"mr {mr}")
        path = os.path.join(self.state, "sync", repo, "families", name)
        write(path, "\n".join(lines) + "\n")
        return path

    def record(self, name):
        return os.path.join(self.state, "retire", f"{name}.json")

    def read(self, name):
        with open(self.record(name), encoding="utf-8") as f:
            return json.load(f)

    def test_nothing_is_just_the_head(self):
        self.assertEqual([], self.migrate())

    def test_a_record_moves_to_the_retire_note_and_the_old_one_goes(self):
        old = self.legacy("self", "i0001")
        self.assertEqual(["moved i0001 self"], self.migrate())
        self.assertEqual(
            {"parent": "i0001", "project": "", "branch": "i0001", "children": [], "mr": 12},
            self.read("i0001"),
        )
        self.assertFalse(os.path.exists(old))
        # 空になった sync/ は残さない。2 回目は何も起きない。
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync")))
        self.assertEqual([], self.migrate())

    def test_the_state_of_the_old_record_is_dropped(self):
        for state in ("present", "gone", "blocked", "closed"):
            with self.subTest(state=state):
                self.legacy("self", "i0001", state=state)
                self.assertEqual(["moved i0001 self"], self.migrate())
                self.assertNotIn("state", self.read("i0001"))
                os.remove(self.record("i0001"))

    def test_a_project_record_carries_the_project_name(self):
        self.legacy("app", "a0001", mr="3", branch="feature-3-x")
        self.assertEqual(["moved a0001 app"], self.migrate())
        held = self.read("a0001")
        self.assertEqual(("app", "feature-3-x", 3), (held["project"], held["branch"], held["mr"]))

    def test_a_record_with_a_live_parent_worktree_is_dropped(self):
        os.makedirs(os.path.join(self.ws, ".claude", "worktrees", "i0001"))
        old = self.legacy("self", "i0001")
        (line,) = self.migrate()
        self.assertTrue(line.startswith("dropped i0001 self "), line)
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(self.record("i0001")))

    def test_a_parent_worktree_that_differs_only_in_case_is_a_live_one(self):
        # 大小違いのワークツリーは同じものとして扱う。実在する親を片付け待ちとして移さない。
        os.makedirs(os.path.join(self.ws, ".claude", "worktrees", "I0001"))
        old = self.legacy("self", "i0001")
        (line,) = self.migrate()
        self.assertTrue(line.startswith("dropped i0001 self "), line)
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(os.path.join(self.state, "retire")))

    def test_a_note_that_differs_only_in_case_is_the_same_note(self):
        write(
            self.record("I0001"),
            json.dumps({"parent": "I0001", "project": "", "branch": "", "children": []}),
        )
        self.legacy("self", "i0001", mr="12", branch="i0001")
        self.assertEqual(["moved i0001 self"], self.migrate())
        self.assertEqual(["I0001.json"], os.listdir(os.path.join(self.state, "retire")))
        with open(self.record("I0001"), encoding="utf-8") as f:
            held = json.load(f)
        self.assertEqual(("i0001", 12), (held["branch"], held["mr"]))

    def test_a_record_with_neither_mr_nor_branch_is_dropped(self):
        old = self.legacy("self", "i0002", mr=None, branch="")
        (line,) = self.migrate()
        self.assertTrue(line.startswith("dropped i0002 self "), line)
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(self.record("i0002")))

    def test_a_branch_alone_is_enough_to_move(self):
        self.legacy("self", "i0003", mr=None)
        self.assertEqual(["moved i0003 self"], self.migrate())
        held = self.read("i0003")
        self.assertEqual("i0003", held["branch"])
        self.assertNotIn("mr", held)

    def test_an_existing_note_is_respected(self):
        write(
            self.record("i0001"),
            json.dumps(
                {
                    "parent": "i0001",
                    "project": "",
                    "branch": "mine",
                    "children": [{"ticket": "c"}],
                    "mr": 99,
                }
            ),
        )
        old = self.legacy("self", "i0001", mr="12", branch="theirs")
        self.assertEqual(["kept i0001 self"], self.migrate())
        held = self.read("i0001")
        self.assertEqual(("mine", 99), (held["branch"], held["mr"]))
        self.assertEqual([{"ticket": "c"}], held["children"])
        self.assertFalse(os.path.exists(old))

    def test_an_existing_note_gets_only_what_it_lacks(self):
        write(
            self.record("i0001"),
            json.dumps(
                {"parent": "i0001", "project": "", "branch": "", "children": [{"ticket": "c"}]}
            ),
        )
        self.legacy("self", "i0001", mr="12", branch="i0001")
        self.assertEqual(["moved i0001 self"], self.migrate())
        held = self.read("i0001")
        self.assertEqual(("i0001", 12), (held["branch"], held["mr"]))
        self.assertEqual([{"ticket": "c"}], held["children"])

    def test_a_broken_existing_note_is_not_overwritten_and_the_old_record_stays(self):
        write(self.record("i0001"), "{broken")
        old = self.legacy("self", "i0001")
        (line,) = self.migrate()
        self.assertTrue(line.startswith("skipped i0001 self "), line)
        with open(self.record("i0001"), encoding="utf-8") as f:
            self.assertEqual("{broken", f.read())
        self.assertTrue(os.path.exists(old))

    def test_a_linked_record_is_skipped_and_kept(self):
        target = os.path.join(self.ws, "elsewhere")
        write(target, "mr 5\nbranch x\n")
        link = os.path.join(self.state, "sync", "self", "families", "i0001")
        os.makedirs(os.path.dirname(link))
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("シンボリックリンクを作れない")
        (line,) = self.migrate()
        self.assertTrue(line.startswith("skipped i0001 self "), line)
        self.assertTrue(os.path.islink(link))
        self.assertFalse(os.path.exists(self.record("i0001")))

    def test_the_integration_copy_goes(self):
        write(os.path.join(self.state, "sync", "self", "integration", "head"), "branch main\n")
        write(os.path.join(self.state, "sync", "self", "integration", "done", "x.md"), "x\n")
        self.assertEqual(["removed integration self"], self.migrate())
        self.assertFalse(os.path.exists(os.path.join(self.state, "sync", "self", "integration")))
        self.assertEqual([], self.migrate())

    def test_a_bad_name_is_skipped(self):
        old = self.legacy("self", "bad name")
        (line,) = self.migrate()
        self.assertTrue(line.startswith("skipped "), line)
        self.assertTrue(os.path.exists(old))


class SyncRetiredCommandTest(unittest.TestCase):
    """`ccnavi sync retired` は答えの形だけをここで見る（中身は tests/ticket/test_retire.py）。"""

    def test_the_head_comes_first_without_any_note(self):
        ws = tempfile.mkdtemp(prefix="ccnavi-sync-retired-")
        self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
        os.makedirs(os.path.join(ws, ".claude"))
        result = run_ccnavi(["--root", ws, "sync", "retired"])
        self.assertEqual((0, "retired 1\n"), (result.returncode, result.stdout))


if __name__ == "__main__":
    unittest.main()
