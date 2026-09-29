"""判定のコアと差し口（ADR-0093 の 6 章、段階 2a）の受入テスト。

見るのは 6 つ。

1. 時計（Clock の差し口）: `fsio.clock` で固定した時刻を、承認の記録と跡が同じに書く
2. D22: 承認の記録の `source_path` はリポジトリからの相対、`source_tree` はブランチ名。
   前の形（絶対パス・ツリーの名前）の写しも同じに読み、判定の答えは変わらない
3. plan と Writer(FS): 書くもの（Changes）を並べるだけではディスクは変わらず、並べたものを
   書いた結果が Changes のとおりになる（改版・マーカーの消去・フローの運び・フィードバック計画）
4. Chrome の入口（`ccnavi_chrome.py`）が同じ入力から、手元が実際に書いたのと同じバイト列を出す
   （新規・マーカーの消去・改版・フィードバック計画・多段の先行と落ちる提案・取り下げ・レビュー済み）。
   同じ要求と答えを拡張の試験の見本（`chrome-extension/ccnavi-approval/test/fixtures/core-scenarios.json`）
   に置き、拡張の試験が Pyodide でも同じ答えになることを見る
5. 承認の取り下げ（8.8）の条件
6. fsio の記録層（`--record-writes`）: 各コマンドで書いたパスの一覧が `git status` の変化と一致する

見本の形を変えたら `CCNAVI_CHROME_FIXTURE=1` を付けてこのテストを走らせ、見本を書き直す。
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import unittest

from ccnavi import approval, core, fsio, history, settings
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import ROOT, git, write

CHROME = os.path.join(ROOT, "chrome-extension", "ccnavi-approval")
SCENARIOS = os.path.join(CHROME, "test", "fixtures", "core-scenarios.json")
STAMP = "2026-09-29T12:00:00+0900"
PLACES = (".ccnavi", "wip")


def _chrome():
    spec = importlib.util.spec_from_file_location(
        "ccnavi_chrome", os.path.join(CHROME, "py", "ccnavi_chrome.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _files(tree_root, prefixes=PLACES):
    """ツリーの置き場のファイル（相対パス → バイト列）。"""
    found = {}
    for prefix in prefixes:
        base = os.path.join(tree_root, prefix)
        for current, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in names:
                full = os.path.join(current, name)
                rel = os.path.relpath(full, tree_root).replace(os.sep, "/")
                with open(full, "rb") as f:
                    found[rel] = f.read()
    return found


class CoreHarness(PhaseHarness):
    """親のワークツリー（i0001）を持つリポジトリで、コアを直に呼ぶ道具。"""

    def conf(self):
        conf, _ = settings.load(self.root)
        conf.state = self.state
        return conf

    def trees(self):
        """ブランチの名前 → ツリーのルート。ワークスペースルートは main。"""
        found = {"main": self.root}
        base = os.path.join(self.root, ".claude", "worktrees")
        for name in sorted(os.listdir(base)):
            found[name] = os.path.join(base, name)
        return found

    def disk(self):
        return {name: _files(path) for name, path in self.trees().items()}

    def diff(self, before, after):
        """2 つの `disk` の違いを、Changes の `per_branch` と同じ形（中身は本文）で。"""
        out = {}
        for name in sorted(set(before) | set(after)):
            old, new = before.get(name, {}), after.get(name, {})
            rows = []
            for path in sorted(set(old) | set(new)):
                if path in old and path not in new:
                    rows.append({"op": "delete", "path": path})
                elif path not in old:
                    rows.append({"op": "create", "path": path, "content": new[path].decode()})
                elif old[path] != new[path]:
                    rows.append({"op": "update", "path": path, "content": new[path].decode()})
            if rows:
                out[name] = rows
        return out

    def snapshot(self):
        return core.read_fs(self.conf(), self.root, STAMP, core.Actor("", history.VIA_CHROME))

    def write_changes(self, changes):
        out, err = io.StringIO(), io.StringIO()
        with history.session(history.VIA_CHROME, err):
            applied = core.write_fs(out, err, changes.planned)
        return applied, out.getvalue(), err.getvalue()

    def plan_local(self, only=None):
        """手元でコアを通して承認する。答えは (Changes, 書いた前後の差分)。"""
        snapshot = self.snapshot()
        with history.session(history.VIA_CHROME, None):
            verdict = core.judge(snapshot, only)
            changes = core.plan(snapshot, verdict)
        before = self.disk()
        applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 0, out + err)
        return changes, self.diff(before, self.disk())

    # ---- Chrome の要求

    def chrome_request(self, op, family, **extra):
        """手元のツリーから、拡張が組むのと同じ形の要求を作る（統合先は main）。"""
        chrome = _chrome()
        place = chrome._placement(None)
        branches = {}
        for name, path in self.trees().items():
            if name == "main":
                wanted = tuple(p + "/" for p in place["integration_paths"])
                files = {
                    rel: body.decode()
                    for rel, body in _files(path, (".ccnavi", ".claude")).items()
                    if rel.startswith(wanted) or rel in place["integration_files"]
                }
            else:
                wanted = tuple(p + "/" for p in place["branch_paths"])
                files = {
                    rel: body.decode()
                    for rel, body in _files(path).items()
                    if rel.startswith(wanted)
                }
            branches[name] = {"head": "0" * 40, "files": dict(sorted(files.items()))}
        return {
            "schema": chrome.SCHEMA,
            "op": op,
            "family": family,
            "stamp": STAMP,
            "snapshot": {
                "integration": {"name": "main", "source": "setting", "head": "0" * 40},
                "branches": branches,
                "absent": [],
            },
            **extra,
        }

    def ask_chrome(self, request):
        root = os.path.join(self.root, "memfs")
        answer = json.loads(_chrome().handle(json.dumps(request), root))
        self.assertNotIn("error", answer, answer)
        return answer

    def keep_scenario(self, name, request, answer):
        """拡張の試験の見本に置く（要求と、手元の CPython の答え）。"""
        CoreChromeTest.collected[name] = {"request": request, "answer": answer}


class ClockTest(unittest.TestCase):
    def test_a_fixed_clock_gives_one_instant_to_both_stamps(self):
        with fsio.clock("2026-09-29T09:30:05+0900"):
            self.assertEqual(fsio.stamp(), "2026-09-29T09:30:05+0900")
            self.assertEqual(fsio.utc_stamp(), "2026-09-29T00:30:05Z")
            self.assertEqual(history.stamp(), "2026-09-29T00:30:05Z")
        self.assertNotEqual(fsio.stamp(), "2026-09-29T09:30:05+0900")

    def test_an_unreadable_stamp_is_refused(self):
        with self.assertRaises(ValueError), fsio.clock("2026-09-29 09:30"):
            pass


class SourcePathTest(CoreHarness):
    """D22: 承認の記録の出所は、リポジトリからの相対パスとブランチ名。"""

    def test_the_copy_records_a_relative_path_and_the_branch(self):
        text = parent_text("i0001", ["research"])
        self.propose("i0001", text)
        self.commit_parent()
        preview = json.loads(self.ccnavi("--approve", "--preview", "--json").stdout)
        self.assertIn("提案: wip/proposals/todo/i0001.md", preview["text"])
        self.assertNotIn(self.root, preview["text"])
        self.assertEqual(self.approve().returncode, 0)
        copy, _ = approval.load_copy(os.path.join(self.approved, "doing", "i0001.md"))
        self.assertEqual(copy.source_path, "wip/proposals/todo/i0001.md")
        self.assertEqual(copy.source_tree, "i0001")

    def test_a_copy_written_in_the_old_form_reads_the_same(self):
        """前の形（絶対パス・ツリーの名前）の写しと今の形の写しで、判定の答えが同じ。"""
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()
        new_form = self.ccnavi("--approve", "--preview", "--json")
        verify_new = self.ccnavi("--approve", "--preview", "--verify")
        board_new = json.loads(self.ccnavi("--explain", "--json").stdout)
        path = os.path.join(self.approved, "doing", "i0001.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        old = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001.md")
        # 前の形: 絶対パス。`source_tree` はツリーの名前で、
        # 親のワークツリーではブランチ名と同じ綴り。
        legacy = text.replace(
            "source_path: wip/proposals/todo/i0001.md", f"source_path: {json.dumps(old)}"
        )
        self.assertNotEqual(legacy, text)
        write(path, legacy)
        copy, _ = approval.load_copy(path)
        self.assertEqual(copy.source_path, old)
        old_form = self.ccnavi("--approve", "--preview", "--json")
        verify_old = self.ccnavi("--approve", "--preview", "--verify")
        board_old = json.loads(self.ccnavi("--explain", "--json").stdout)
        a, b = json.loads(new_form.stdout), json.loads(old_form.stdout)
        for key in ("batch", "text", "digest", "rejected", "problems"):
            self.assertEqual(a[key], b[key], key)
        self.assertEqual(verify_new.returncode, verify_old.returncode)
        self.assertEqual(verify_new.stdout, verify_old.stdout)
        # 板の違いは、出所をそのまま見せる欄（copy.source_tree）だけ。ここは同じ値。
        self.assertEqual(board_new["parents"], board_old["parents"])
        # 実行前の判定も同じ（子の範囲で書ける・範囲の外は止まる）。
        self.assertEqual(self.approve().returncode, 0)
        tree = self.worktree("i0001-01", "i0001")
        self.start_parent()
        self.assertEqual(self.ccnavi("ticket", "start", "i0001-01").returncode, 0)
        inside = self.hook("PreToolUse", "Write", tree, file_path=f"{tree}/wip/research/a.md")
        outside = self.hook("PreToolUse", "Write", tree, file_path=f"{tree}/src/a.py")
        self.assertNotIn('"deny"', inside.stdout)
        self.assertIn('"deny"', outside.stdout)


class PlanWriterTest(CoreHarness):
    """plan は書かない。Writer(FS) が書いた結果は plan の Changes のとおり。"""

    def plan_and_write(self, only=None):
        snapshot = self.snapshot()
        before = self.disk()
        with history.session(history.VIA_CHROME, None):
            verdict = core.judge(snapshot, only)
            changes = core.plan(snapshot, verdict)
        self.assertEqual(self.disk(), before, "plan がディスクを書いた")
        planned = {
            name: [
                {k: (v.decode() if isinstance(v, bytes) else v) for k, v in row.items()}
                for row in rows
            ]
            for name, rows in changes.per_branch().items()
        }
        applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 0, out + err)
        self.assertEqual(planned, self.diff(before, self.disk()))
        return changes, out

    def test_new_parent_and_child(self):
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()
        changes, out = self.plan_and_write()
        self.assertIn("承認した。", out)
        rows = changes.per_branch()["i0001"]
        self.assertEqual(
            [(r["op"], r["path"]) for r in rows],
            [
                ("create", ".ccnavi/approved/doing/i0001-01.md"),
                ("create", ".ccnavi/approved/doing/i0001.md"),
                ("create", ".ccnavi/approved/events/i0001-01.ndjson"),
                ("create", ".ccnavi/approved/events/i0001.ndjson"),
                ("delete", "wip/proposals/todo/i0001-01.md"),
                ("delete", "wip/proposals/todo/i0001.md"),
            ],
        )
        event = json.loads(rows[3]["content"].decode())
        self.assertEqual(event["at"], "2026-09-29T03:00:00Z")
        self.assertEqual(event["tree"], "i0001")
        self.assertIn(b"approved_at: 2026-09-29T12:00:00+0900", rows[1]["content"])

    def test_revision_and_feedback_plan(self):
        self.family(plan=["design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.commit_parent("reviewed")
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        self.commit_parent()
        _, out = self.plan_and_write()
        self.assertIn("i0001 のフィードバック計画を改版した", out)

    def test_adding_a_child_clears_the_marks(self):
        self.family(plan=["design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        marks = os.path.join(self.approved, "phases", "i0001")
        write(os.path.join(marks, "1.requested"), '{"at": "x"}')
        write(os.path.join(marks, "1.reviewed"), '{"at": "x"}')
        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        _, out = self.plan_and_write()
        self.assertIn("マーカー（requested, reviewed）を消した", out)
        self.assertFalse(os.path.exists(os.path.join(marks, "1.reviewed")))

    def test_a_flow_is_carried_to_the_parent_tree(self):
        """提案が別のツリー（ワークスペースルート）に在れば、フローも親のツリーへ運ぶ。"""
        self.family(plan=["design"])
        todo = os.path.join(self.root, "wip", "proposals", "todo")
        write(
            os.path.join(todo, "i0001-01.md"),
            child_text("i0001-01", "i0001", 1, ["wip/design/*"]),
        )
        flow = os.path.join(self.root, ".ccnavi", "approved", "flows", "i0001-01.yml")
        write(flow, "version: 1\nsteps: []\n")
        _, out = self.plan_and_write()
        self.assertIn("i0001-01 のフローを", out)
        self.assertFalse(os.path.exists(flow))
        self.assertTrue(os.path.exists(os.path.join(self.approved, "flows", "i0001-01.yml")))

    def test_a_flow_that_cannot_be_written_says_so_and_skips_the_rest(self):
        """運べなかったフローは行で言い、元を消さない（前と同じ落ち方）。"""
        self.family(plan=["design"])
        todo = os.path.join(self.root, "wip", "proposals", "todo")
        write(
            os.path.join(todo, "i0001-01.md"),
            child_text("i0001-01", "i0001", 1, ["wip/design/*"]),
        )
        flow = os.path.join(self.root, ".ccnavi", "approved", "flows", "i0001-01.yml")
        write(flow, "version: 1\nsteps: []\n")
        snapshot = self.snapshot()
        verdict = core.judge(snapshot)
        changes = core.plan(snapshot, verdict)
        # 並べた後に行き先を塞ぐ（書く前に別の誰かが置いた形）。
        write(os.path.join(self.approved, "flows", "i0001-01.yml"), "other\n")
        applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 0, out + err)
        self.assertIn("へ運べない", out)
        self.assertNotIn("から", out.split("へ運べない")[1].split("\n")[0])
        self.assertTrue(os.path.exists(flow))
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-01.md")))


class CoreChromeTest(CoreHarness):
    """Chrome の入口が、手元が実際に書いたのと同じバイト列を出す（6.2 の同じ答え）。"""

    collected: dict = {}

    def setUp(self):
        # 見本は走らせるたびに同じ中身にする。時刻（承認の記録・跡・マーカー）は fsio の時計で、
        # コミットの sha（依頼のマーカーの `head`）は git の日時で固定する。
        for key in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
            if key in os.environ:
                self.addCleanup(os.environ.__setitem__, key, os.environ[key])
            else:
                self.addCleanup(os.environ.pop, key, None)
            os.environ[key] = "2026-09-29T12:00:00+0900"
        clock = fsio.clock(STAMP)
        clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        super().setUp()

    @classmethod
    def tearDownClass(cls):
        if len(cls.collected) != len(SCENARIO_NAMES):
            return
        body = json.dumps(
            dict(sorted(cls.collected.items())), ensure_ascii=False, indent=1, sort_keys=True
        )
        if os.environ.get("CCNAVI_CHROME_FIXTURE"):
            write(SCENARIOS, body + "\n")
            return
        with open(SCENARIOS, encoding="utf-8") as f:
            held = f.read()
        if held != body + "\n":
            raise AssertionError(
                f"{SCENARIOS} が古い。"
                "CCNAVI_CHROME_FIXTURE=1 を付けて tests.ticket.test_core を回す"
            )

    def same_as_local(self, name, family, only=None):
        request = self.chrome_request("plan", family, **({"only": only} if only else {}))
        answer = self.ask_chrome(request)
        _, written = self.plan_local(only)
        self.assertEqual(answer["changes"], written)
        self.keep_scenario(name, request, answer)
        return answer

    def test_approve_new(self):
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()
        preview = json.loads(self.ccnavi("--approve", "--preview", "--json").stdout)
        answer = self.same_as_local("approve-new", "i0001")
        self.assertEqual(answer["identifiers"], ["i0001", "i0001-01"])
        # 手元の `--approve --preview` と同じ本文と指紋（画面の本文が機械に依らない。D22）。
        self.assertEqual(answer["text"], preview["text"])
        self.assertEqual(answer["digest"], preview["digest"])

    def test_approve_reopens_a_reviewed_phase(self):
        self.family(plan=["design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        marks = os.path.join(self.approved, "phases", "i0001")
        write(os.path.join(marks, "1.requested"), '{"at": "x"}')
        write(os.path.join(marks, "1.reviewed"), '{"at": "x"}')
        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        answer = self.same_as_local("approve-reopen", "i0001")
        self.assertIn(
            {"op": "delete", "path": ".ccnavi/approved/phases/i0001/1.reviewed"},
            answer["changes"]["i0001"],
        )

    def test_revision(self):
        self.family(plan=["research", "design"])
        self.propose("i0001", parent_text("i0001", ["research", "design", "acceptance"]))
        self.commit_parent()
        answer = self.same_as_local("revise", "i0001")
        self.assertIn("  i0001 の全体計画を改版した", answer["lines"])

    def test_feedback_plan(self):
        self.family(plan=["design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.commit_parent("reviewed")
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        self.commit_parent()
        answer = self.same_as_local("feedback-plan", "i0001")
        self.assertIn("  i0001 のフィードバック計画を改版した", answer["lines"])

    def test_predecessors_across_families(self):
        """多段の先行（閉包）。通る子と、先行が閉じていないので落ちる子（文面まで同じ）。"""
        copies = {
            "i0001": [
                ("doing", parent_text("i0001", ["research"])),
                ("done", self.done("i0001-01", "i0001", [])),
            ],
            "i0002": [
                ("doing", parent_text("i0002", ["research"])),
                ("done", self.done("i0002-01", "i0002", ["i0001-01"])),
                ("doing", child_text("i0002-02", "i0002", 1, ["wip/research/*"], False)),
            ],
            "i0003": [("doing", parent_text("i0003", ["research"]))],
        }
        trees = {"i0001": self.parent_tree}
        for family in ("i0002", "i0003"):
            trees[family] = self.worktree(family, "main")
        for family, rows in copies.items():
            for state, text in rows:
                ident = text.split("ticket: ", 1)[1].split("\n", 1)[0]
                write(
                    os.path.join(trees[family], ".ccnavi", "approved", state, ident + ".md"), text
                )
        todo = os.path.join(trees["i0003"], "wip", "proposals", "todo")
        write(
            os.path.join(todo, "i0003-01.md"),
            self.with_predecessors(
                child_text("i0003-01", "i0003", 1, ["wip/research/*"], False), ["i0002-01"]
            ),
        )
        write(
            os.path.join(todo, "i0003-02.md"),
            self.with_predecessors(
                child_text("i0003-02", "i0003", 1, ["wip/research/*"], False), ["i0002-02"]
            ),
        )
        for path in trees.values():
            git(path, "add", "-A")
            git(path, "commit", "--quiet", "-m", "copies")
        request = self.chrome_request("plan", "i0003")
        answer = self.ask_chrome(request)
        self.assertEqual(answer["identifiers"], ["i0003-01"])
        self.assertEqual([r["ticket"] for r in answer["rejected"]], ["i0003-02"])
        verify = self.ccnavi("--approve", "--preview", "--json")
        local = json.loads(verify.stdout)
        self.assertEqual(answer["rejected"], local["rejected"])
        _, written = self.plan_local()
        self.assertEqual(answer["changes"], written)
        self.keep_scenario("predecessors", request, answer)

    def done(self, name, parent, predecessors):
        text = child_text(name, parent, 1, ["wip/research/*"], False)
        text = text.replace('completed_at: ""', 'completed_at: "2026-01-01T00:00:00+0000"')
        return self.with_predecessors(text, predecessors)

    @staticmethod
    def with_predecessors(text, predecessors):
        if not predecessors:
            return text
        return text.replace(
            "human_review:", f"predecessors: {json.dumps(predecessors)}\nhuman_review:", 1
        )

    def test_withdraw(self):
        text = parent_text("i0001", ["research"])
        self.propose("i0001", text)
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        request = self.chrome_request(
            "withdraw", "i0001", ids=["i0001"], prior={"i0001": text}, reason="押し間違い"
        )
        answer = self.ask_chrome(request)
        self.assertEqual(answer["problems"], [])
        before = self.disk()
        checked = core.withdraw(self.snapshot(), ["i0001"], {"i0001": text.encode()}, "押し間違い")
        self.assertEqual(checked.problems, [])
        applied, out, err = self.write_changes(checked.changes)
        self.assertEqual(applied.code, 0, out + err)
        self.assertEqual(answer["changes"], self.diff(before, self.disk()))
        with open(
            os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001.md"), encoding="utf-8"
        ) as f:
            self.assertEqual(f.read(), text)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))
        events, _ = history.read(self.approved, "i0001")
        self.assertEqual(events[-1]["kind"], history.KIND_WITHDRAWN)
        self.assertEqual(events[-1]["via"], history.VIA_CHROME)
        self.assertEqual(events[-1]["reason"], "押し間違い")
        self.keep_scenario("withdraw", request, answer)

    def test_confirm(self):
        """レビュー済み: レビュー待ちの子を done/ へ動かし（settle_review）、印を置く。"""
        self.family(plan=["design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        self.commit_parent("requested")
        result = {
            "host": "fixture",
            "mr": {"number": 7, "url": "u/7"},
            "threads": [],
            "reviews": [],
        }
        request = self.chrome_request("confirm", "i0001", phase=1, result=result, changed="")
        answer = self.ask_chrome(request)
        self.assertEqual(answer["problems"], [])
        from ccnavi import review

        before = self.disk()
        checked = core.confirm(self.snapshot(), "i0001", 1, review.Result.from_data(result), "")
        applied, out, err = self.write_changes(checked.changes)
        self.assertEqual(applied.code, 0, out + err)
        self.assertEqual(answer["changes"], self.diff(before, self.disk()))
        paths = [(r["op"], r["path"]) for r in answer["changes"]["i0001"]]
        self.assertIn(("create", ".ccnavi/approved/done/i0001-01.md"), paths)
        self.assertIn(("delete", "wip/proposals/review/i0001-01.md"), paths)
        mark = next(r for r in answer["changes"]["i0001"] if r["path"].endswith("1.reviewed"))
        self.assertEqual(json.loads(mark["content"]), {"mr": 7, "accepted": [], "at": STAMP})
        self.keep_scenario("confirm", request, answer)
        # 変更要求があれば通さない（文面も手元と同じ）。
        refused = self.chrome_request(
            "confirm",
            "i0001",
            phase=1,
            result={**result, "reviews": [{"state": "CHANGES_REQUESTED", "url": "r/1"}]},
            changed="",
        )
        self.assertIn("変更要求のレビューが立っている", self.ask_chrome(refused)["problems"][0])


SCENARIO_NAMES = (
    "approve-new",
    "approve-reopen",
    "revise",
    "feedback-plan",
    "predecessors",
    "withdraw",
    "confirm",
)


class WithdrawTest(CoreHarness):
    """取り下げの条件（8.8）。どれか 1 つでも当たれば何も並べない。"""

    def approved_parent(self):
        text = parent_text("i0001", ["research"])
        self.propose("i0001", text)
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        return text

    def problems(self, prior):
        return core.withdraw(self.snapshot(), ["i0001"], prior).problems

    def test_a_started_parent_is_not_withdrawn(self):
        text = self.approved_parent()
        self.start_parent()
        problems = self.problems({"i0001": text.encode()})
        self.assertTrue(any("着手済み" in p and "cancel" in p for p in problems), problems)

    def test_a_parent_with_a_child_is_not_withdrawn(self):
        text = self.approved_parent()
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False))
        problems = self.problems({"i0001": text.encode()})
        self.assertTrue(any("子の提案" in p for p in problems), problems)
        self.assertEqual(self.approve().returncode, 0)
        problems = self.problems({"i0001": text.encode()})
        self.assertTrue(any("子の承認済みチケット" in p for p in problems), problems)

    def test_marks_a_revision_a_blocked_todo_or_no_commit_refuse(self):
        text = self.approved_parent()
        self.assertTrue(any("承認コミット" in p for p in self.problems({})))
        write(os.path.join(self.approved, "phases", "i0001", "1.pending"), "{}")
        self.assertTrue(any("マーカー" in p for p in self.problems({"i0001": text.encode()})))
        os.remove(os.path.join(self.approved, "phases", "i0001", "1.pending"))
        self.propose("i0001", text)
        self.assertTrue(any("戻す先" in p for p in self.problems({"i0001": text.encode()})))
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.assertEqual(self.approve().returncode, 0)
        self.assertTrue(any("改版" in p for p in self.problems({"i0001": text.encode()})))

    def test_nothing_is_written_when_refused(self):
        text = self.approved_parent()
        self.start_parent()
        checked = core.withdraw(self.snapshot(), ["i0001"], {"i0001": text.encode()})
        self.assertIsNone(checked.changes)


class RecordWritesTest(CoreHarness):
    """`--record-writes`: この実行で書いたパスの一覧が、git status の変化と一致する。"""

    def record(self, *args, stdin=""):
        target = os.path.join(self.state, "c1", "self", "i0001.t.writes")
        result = self.ccnavi("--record-writes", target, *args, stdin=stdin)
        with open(target, encoding="utf-8") as f:
            listed = [line for line in f.read().splitlines() if line]
        return result, listed

    def changed(self, tree_root):
        out = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--no-renames"],
            cwd=tree_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        prefix = os.path.relpath(tree_root, self.root).replace(os.sep, "/")
        return sorted(f"{prefix}/{line[3:]}".removeprefix("./") for line in out.splitlines())

    def test_each_command_lists_what_it_wrote(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()
        digest = json.loads(self.ccnavi("--approve", "--preview", "--json").stdout)["digest"]
        steps = [
            ("--approve", "--yes", "i0001,i0001-01", "--digest", digest, "--json"),
            ("ticket", "start", "i0001"),
        ]
        for args in steps:
            result, listed = self.record(*args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(sorted(listed), self.changed(self.parent_tree), args)
            self.commit_parent(" ".join(args))
        tree = self.worktree("i0001-01", "i0001")
        result, listed = self.record("ticket", "start", "i0001-01")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(listed), self.changed(self.parent_tree))
        self.commit_parent("start 01")
        write(os.path.join(tree, "wip", "research", "summary.md"), "s\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "work")
        result, listed = self.record("ticket", "finish", "i0001-01")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(listed), self.changed(self.parent_tree))
        self.assertTrue(listed)

    def test_the_list_goes_only_under_the_state_place(self):
        outside = os.path.join(self.root, "wip", "list.txt")
        result = self.ccnavi("--record-writes", outside, "ticket", "start", "i0001")
        self.assertEqual(result.returncode, 1)
        self.assertIn("控えの置き場の下", result.stderr)
        self.assertFalse(os.path.exists(outside))

    def test_the_layer_writes_bypass_nothing(self):
        """fsio を通らない書き込みが残っていない（置き場を書くモジュールに素の書き込みが無い）。"""
        import re

        pattern = re.compile(
            r"os\.remove\(|os\.rename\(|os\.replace\(|shutil\.copy2?\(|shutil\.move\(|"
            r"os\.open\(|open\([^)]*[\"'][wax]b?[\"']"
        )
        for name in ("approval", "history", "configsync", "ops", "flow", "risk", "core"):
            path = os.path.join(ROOT, "ccnavi", name + ".py")
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            hits = [
                f"{name}.py:{n}: {line.strip()}"
                for n, line in enumerate(lines, 1)
                if pattern.search(line)
            ]
            # flow.read_bytes は読むだけ（`os.open` の flags は O_RDONLY）。
            reads = ["flow.py:" + str(n) for n, line in enumerate(lines, 1) if "O_RDONLY" in line]
            if name == "flow":
                self.assertEqual(len(reads), 1)
                hits = [h for h in hits if "os.open(path, flags)" not in h]
            self.assertEqual(hits, [], name)


if __name__ == "__main__":
    unittest.main()
