"""判定のコアと差し口（ADR-0093 の 6 章、段階 2a）の受入テスト。

見るのは 6 つ。

1. 時計（Clock の差し口）: `fsio.clock` で固定した時刻を、承認の記録と状態の履歴が同じに書く
2. D22: 承認の記録の `source_path` はリポジトリからの相対、`source_tree` はブランチ名。
   前の形（絶対パス・ツリーの名前）の写しも同じに読み、判定の答えは変わらない
3. plan と Writer(FS): 書くもの（Changes）を並べるだけではディスクは変わらず、並べたものを
   書いた結果が Changes のとおりになる（改版・マーカーの消去・フローの移動・フィードバック計画）
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
import shutil
import subprocess
import unittest
from unittest import mock

from ccnavi import approval, core, fsio, history, lint, settings, version
from ccnavi import tree as tree_mod
from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import ROOT, git, read_json, write

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
            verdict = core.judge_approval(snapshot, only)
            changes = core.plan(snapshot, verdict)
        before = self.disk()
        applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 0, out + err)
        return changes, self.diff(before, self.disk())

    def reviewed_phase_one(self):
        """フェーズ 1（MR で見る設計）の子を閉じて合流し、レビューを依頼したところまで進める。"""
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
        return fixture

    # ---- Chrome の要求

    def chrome_request(self, op, family, heads=None, **extra):
        """手元のツリーから、拡張が組むのと同じ形の要求を作る（統合先は main）。

        ブランチの先頭は既定で `0` の並び。`heads` で名前ごとに本物の先頭を渡せる（レビュー済みは
        依頼時の先頭と比べるので、親のブランチの本物の先頭が要る）。
        """
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
            head = (heads or {}).get(name, "0" * 40)
            branches[name] = {"head": head, "files": dict(sorted(files.items()))}
        request = {
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
        if op != "confirm":
            # レビュー済み（段階 4）は手元の CLI の confirm と比べる。控えがあると手元は C1 の
            # 対象の家族として sh を通さない書き込みを断るので、控えは Chrome の側だけに組む。
            self.mirror_records(chrome, request)
        return request

    def mirror_records(self, chrome, request):
        """Chrome の入口が仮のツリーに組む取り込みの控え相当を、手元の控えの置き場にも書く。

        手元も同じ控えで判定する（取り込み済みの家族として読む。ADR-0093 の 3.3）ので、
        画面の本文と指紋（判定が読んだ中身。控えを含む）が Chrome と同じになる。
        """
        snap = request["snapshot"]
        place = chrome._placement(None)
        closure = chrome._closure(snap, place, request["family"])
        shutil.rmtree(os.path.join(self.state, "sync"), ignore_errors=True)
        for rel, text in chrome.records(snap, place, closure["families"]).items():
            write(os.path.join(self.state, *rel.split("/")), text)

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
        for key in ("batch", "text", "rejected", "problems"):
            self.assertEqual(a[key], b[key], key)
        # 指紋は判定が読んだ中身（read_set。ADR-0093 の段階 2c）で作るので、読んだ写しの
        # バイト列が変われば変わる（見せたあとに写しが書き換わった承認を通さない）。
        self.assertNotEqual(a["digest"], b["digest"])
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

    def test_a_windows_copy_is_read_the_same(self):
        """Windows の絶対パス（このリポジトリの done/ に在る形）の `source_path` も同じに読む。"""
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        before = json.loads(self.ccnavi("--explain", "--json").stdout)
        path = os.path.join(self.approved, "doing", "i0001.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        windows = r"C:\Users\someone\git\ccnavi\.claude\worktrees\i0001\wip\proposals\todo\i0001.md"
        write(
            path,
            text.replace("source_path: wip/proposals/todo/i0001.md", f"source_path: {windows}"),
        )
        copy, _ = approval.load_copy(path)
        self.assertEqual(copy.source_path, windows)
        after = json.loads(self.ccnavi("--explain", "--json").stdout)
        self.assertEqual(before["parents"], after["parents"])
        self.assertEqual(before["tickets"], after["tickets"])


class BranchOfTest(unittest.TestCase):
    """`tree.branch_of`: ブランチを指す HEAD だけを名前として読む。ほかは None。"""

    def setUp(self):
        import tempfile

        self.base = tempfile.mkdtemp(prefix="ccnavi-branch-")
        self.addCleanup(__import__("shutil").rmtree, self.base, ignore_errors=True)

    def repo(self, head, gitfile=None):
        tree_root = os.path.join(self.base, "t")
        if gitfile is None:
            write(os.path.join(tree_root, ".git", "HEAD"), head)
        else:
            write(os.path.join(tree_root, ".git"), gitfile)
            write(os.path.join(tree_root, "meta", "HEAD"), head)
        return tree_root

    def test_forms(self):
        from ccnavi import tree

        self.assertEqual(tree.branch_of(self.repo("ref: refs/heads/main\n")), "main")
        self.assertIsNone(tree.branch_of(self.repo("0123456789abcdef0123456789abcdef01234567\n")))
        self.assertIsNone(tree.branch_of(self.repo("")))
        self.assertIsNone(tree.branch_of(self.repo("ref: refs/heads/\n")))
        self.assertIsNone(tree.branch_of(self.repo("garbage")))
        self.assertIsNone(tree.branch_of(os.path.join(self.base, "none")))

    def test_a_relative_gitdir_is_read_from_the_tree(self):
        from ccnavi import tree

        root = self.repo("ref: refs/heads/i0001\n", gitfile="gitdir: meta\n")
        self.assertEqual(tree.branch_of(root), "i0001")


class SourceBranchTest(CoreHarness):
    """`source_tree` はブランチ名。分からなければツリーの名前（`per_branch` と同じ決め方）。"""

    def approve_one(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        copy, _ = approval.load_copy(os.path.join(self.approved, "doing", "i0001.md"))
        return copy.source_tree

    def test_a_detached_head_falls_back_to_the_tree_name(self):
        git(self.parent_tree, "checkout", "--quiet", "--detach")
        self.assertEqual(self.approve_one(), "i0001")

    def test_a_broken_head_falls_back_to_the_tree_name(self):
        gitfile = os.path.join(self.parent_tree, ".git")
        with open(gitfile, encoding="utf-8") as f:
            gitdir = f.read().split("gitdir:", 1)[1].strip()
        head = os.path.join(gitdir, "HEAD")
        with open(head, encoding="utf-8") as f:
            kept = f.read()
        write(head, "garbage\n")
        try:
            snapshot = self.snapshot()
            self.propose("i0001", parent_text("i0001", ["research"]))
            with history.session(history.VIA_CHROME, None):
                changes = core.plan(snapshot, core.judge_approval(snapshot))
            copy = next(
                row
                for row in changes.per_branch()["i0001"]
                if row["path"].endswith("doing/i0001.md")
            )
            self.assertIn(b"source_tree: i0001", copy["content"])
        finally:
            write(head, kept)

    def test_the_workspace_root_records_its_branch(self):
        write(
            os.path.join(self.root, "wip", "proposals", "todo", "i0001.md"),
            parent_text("i0001", ["research"]),
        )
        snapshot = self.snapshot()
        with history.session(history.VIA_CHROME, None):
            changes = core.plan(snapshot, core.judge_approval(snapshot))
        rows = [r for rows in changes.per_branch().values() for r in rows]
        copy = next(r for r in rows if r["path"].endswith("doing/i0001.md"))
        self.assertIn(b"source_tree: main", copy["content"])
        self.assertIn(b"source_path: wip/proposals/todo/i0001.md", copy["content"])


class PlanWriterTest(CoreHarness):
    """plan は書かない。Writer(FS) が書いた結果は plan の Changes のとおり。"""

    def plan_and_write(self, only=None):
        snapshot = self.snapshot()
        before = self.disk()
        with history.session(history.VIA_CHROME, None):
            verdict = core.judge_approval(snapshot, only)
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
        """提案が別のツリー（ワークスペースルート）に在れば、フローも親のツリーへ移す。"""
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
        """移せなかったフローは行で言い、元を消さない（前と同じ落ち方）。"""
        self.family(plan=["design"])
        todo = os.path.join(self.root, "wip", "proposals", "todo")
        write(
            os.path.join(todo, "i0001-01.md"),
            child_text("i0001-01", "i0001", 1, ["wip/design/*"]),
        )
        flow = os.path.join(self.root, ".ccnavi", "approved", "flows", "i0001-01.yml")
        write(flow, "version: 1\nsteps: []\n")
        snapshot = self.snapshot()
        verdict = core.judge_approval(snapshot)
        changes = core.plan(snapshot, verdict)
        # 並べた後で行き先にファイルを置く（書く前に別の誰かが置いた形）。
        write(os.path.join(self.approved, "flows", "i0001-01.yml"), "other\n")
        applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 0, out + err)
        self.assertIn("へ移せない", out)
        self.assertNotIn("から", out.split("へ移せない")[1].split("\n")[0])
        self.assertTrue(os.path.exists(flow))
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001-01.md")))


class WriterFailureTest(CoreHarness):
    """Writer(FS) の落ち方。並べる段では書き込みが落ちないので、書くときの枝を試す。"""

    def planned(self):
        snapshot = self.snapshot()
        with history.session(history.VIA_CHROME, None):
            return core.plan(snapshot, core.judge_approval(snapshot))

    def failing(self, name, when):
        """`fsio.<name>` を、`when(引数)` が真の呼び出しだけ落とす。"""
        real = getattr(fsio, name)

        def fake(*args):
            return "わざと落とした" if when(*args) else real(*args)

        return mock.patch.object(fsio, name, fake)

    def test_a_proposal_that_cannot_be_removed_undoes_the_copy_and_stops(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        changes = self.planned()
        with self.failing("unlink", lambda path: path.endswith(os.path.join("todo", "i0001.md"))):
            applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 1)
        self.assertEqual(applied.placed, [])
        self.assertIn("ccnavi: i0001: 提案を todo/ から動かせない (わざと落とした)", err)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))
        self.assertNotIn("承認した。", out)

    def test_a_revised_proposal_that_cannot_be_removed_is_said_and_goes_on(self):
        self.family(plan=["research", "design"])
        self.propose("i0001", parent_text("i0001", ["research", "design", "acceptance"]))
        self.commit_parent()
        changes = self.planned()
        with self.failing("unlink", lambda path: path.endswith(os.path.join("todo", "i0001.md"))):
            applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 0, out + err)
        self.assertIn("ccnavi: i0001: 改版の提案を todo/ から消せない (わざと落とした)", err)
        self.assertIn("i0001 の全体計画を改版した", out)

    def test_an_unwritable_history_is_a_warning(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        changes = self.planned()
        with self.failing("append", lambda path, data: path.endswith(".ndjson")):
            applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 0, out + err)
        self.assertIn("ccnavi: 警告: i0001 の履歴（approved）を", err)
        self.assertIn("（わざと落とした）。状態は動いた", err)

    def reopened(self):
        self.family(plan=["design"])
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        marks = os.path.join(self.approved, "phases", "i0001")
        write(os.path.join(marks, "1.requested"), '{"at": "x"}')
        write(os.path.join(marks, "1.reviewed"), '{"at": "x"}')
        self.propose("i0001-02", child_text("i0001-02", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        return marks

    def reopened_events(self):
        events, _ = history.read(self.approved, "i0001")
        return [e for e in events if e["kind"] == history.KIND_PHASE_REOPENED]

    def test_a_reviewed_mark_that_cannot_be_removed_stops(self):
        """決定 A: reviewed を消せなければ止める。ほかの種類には手を付けない。"""
        marks = self.reopened()
        changes = self.planned()
        with self.failing("unlink", lambda path: path.endswith("1.reviewed")):
            applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 1)
        self.assertEqual(applied.placed, ["i0001-02"])
        self.assertIn("レビュー済みのマーカー", err)
        self.assertIn("ここで止める", err)
        self.assertTrue(os.path.exists(os.path.join(marks, "1.requested")))
        self.assertTrue(os.path.exists(os.path.join(marks, "1.reviewed")))
        self.assertEqual(self.reopened_events(), [])
        self.assertNotIn("を消した", out)

    def test_another_mark_that_cannot_be_removed_is_said_and_left(self):
        """reviewed 以外は言って続ける。行と状態の履歴は実際に消せた種類だけ。"""
        marks = self.reopened()
        changes = self.planned()
        # 見え方（Chrome の 1 コミット）では両方消える。
        self.assertIn(
            "  i0001 のフェーズ 1 のマーカー（requested, reviewed）を消した。"
            "全部閉じたらレビューをもう一度頼むことになる",
            changes.lines,
        )
        with self.failing("unlink", lambda path: path.endswith("1.requested")):
            applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 0, out + err)
        self.assertIn("消せなかった（わざと落とした）。requested は残っていて効く", err)
        self.assertIn("マーカー（reviewed）を消した", out)
        self.assertTrue(os.path.exists(os.path.join(marks, "1.requested")))
        self.assertFalse(os.path.exists(os.path.join(marks, "1.reviewed")))
        self.assertEqual([e["cleared"] for e in self.reopened_events()], [["reviewed"]])

    def test_a_mark_that_is_a_link_is_removed_as_a_link(self):
        """控える段の消去はリンクを辿らない（行き先を消さない）。"""
        marks = self.reopened()
        outside = write(os.path.join(self.root, "outside.json"), "{}")
        os.remove(os.path.join(marks, "1.requested"))
        os.symlink(outside, os.path.join(marks, "1.requested"))
        applied, out, err = self.write_changes(self.planned())
        self.assertEqual(applied.code, 0, out + err)
        self.assertFalse(os.path.lexists(os.path.join(marks, "1.requested")))
        self.assertTrue(os.path.exists(outside))

    def test_a_move_whose_target_appeared_stops(self):
        """並べた後に行き先が置かれていたら、動かさずに止める（上書きしない）。"""
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
        from ccnavi import review

        result = review.Result.from_data({"host": "fixture", "mr": {"number": 7, "url": "u"}})
        checked = core.confirm(self.snapshot(), "i0001", 1, result, "")
        done = write(os.path.join(self.approved, "done", "i0001-01.md"), "other\n")
        applied, out, err = self.write_changes(checked.changes)
        self.assertEqual(applied.code, 1)
        self.assertIn("行き先に既に在る", err)
        with open(done, encoding="utf-8") as f:
            self.assertEqual(f.read(), "other\n")


def _normalized(changes):
    """経路（`via`）の欄を落とした Changes。手元の CLI（board・cli）と Chrome（chrome）で違う所。"""
    out = {}
    for name, rows in (changes or {}).items():
        kept = []
        for row in rows:
            row = dict(row)
            content = row.get("content")
            if content is not None and row["path"].endswith(".ndjson"):
                lines = [json.loads(line) for line in content.splitlines() if line]
                for line in lines:
                    line.pop("via", None)
                row["content"] = lines
            elif content is not None and row["path"].endswith(".reviewed"):
                mark = json.loads(content)
                mark.pop("via", None)
                row["content"] = mark
            kept.append(row)
        out[name] = kept
    return out


def _flat(lines):
    """見せる行を 1 行ずつに（CLI の JSON の `lines` と同じ切り方）。"""
    return [part for line in lines for part in line.split("\n") if part.strip()]


class CoreChromeTest(CoreHarness):
    """Chrome の入口が、手元の CLI が実際に書いたのと同じバイト列と出力を出す（6.2 の同じ答え）。

    比べるのは、Changes（経路の欄だけを落として）、見せる行、止まったか、問題点の文面、
    画面の本文と指紋。取り下げは手元の CLI が無い（段階 3 の Chrome だけの操作）ので、
    手元のコアを通して書いたものと比べる。
    """

    collected: dict = {}

    def setUp(self):
        # 見本は走らせるたびに同じ中身にする。
        # 時刻（承認の記録・状態の履歴・マーカー）は fsio の時計で、
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
        # 統合先の互換のマーカー（Chrome は版が違えば書く操作を受けない。ADR-0093 の 7.3）。
        write(
            os.path.join(self.root, *lint.SH_COMPAT_FILE.split(os.sep)),
            f"#!/bin/sh\nCCNAVI_COMPAT={version.COMPAT}\n",
        )

    @classmethod
    def tearDownClass(cls):
        if not os.environ.get("CCNAVI_CHROME_FIXTURE"):
            return
        missing = sorted(set(SCENARIO_NAMES) - set(cls.collected))
        if missing:
            raise AssertionError(f"見本を書き直せない。走らなかった場面: {', '.join(missing)}")
        body = json.dumps(
            dict(sorted(cls.collected.items())), ensure_ascii=False, indent=1, sort_keys=True
        )
        write(SCENARIOS, body + "\n")

    def keep_scenario(self, name, request, answer):
        """拡張の試験の見本と突き合わせる（書き直すときは控える）。場面ごとにその場で比べる。"""
        CoreChromeTest.collected[name] = {"request": request, "answer": answer}
        if os.environ.get("CCNAVI_CHROME_FIXTURE"):
            return
        with open(SCENARIOS, encoding="utf-8") as f:
            held = json.load(f)
        self.assertIn(
            name, held, f"{SCENARIOS} に {name} が無い。CCNAVI_CHROME_FIXTURE=1 で書き直す"
        )
        self.assertEqual(
            held[name],
            json.loads(json.dumps({"request": request, "answer": answer})),
            f"{SCENARIOS} の {name} が古い。"
            "CCNAVI_CHROME_FIXTURE=1 を付けて tests.ticket.test_core を回す",
        )

    def test_the_fixture_has_every_scenario(self):
        with open(SCENARIOS, encoding="utf-8") as f:
            self.assertEqual(sorted(json.load(f)), sorted(SCENARIO_NAMES))

    def same_as_cli(self, name, family, only=None):
        """同じ状態で、Chrome の plan と手元の CLI（preview → --yes）を比べる。"""
        request = self.chrome_request("plan", family, **({"only": only} if only else {}))
        answer = self.ask_chrome(request)
        preview = json.loads(self.ccnavi("--approve", "--preview", "--json", *(only or [])).stdout)
        self.assertEqual(answer["text"], preview["text"])
        self.assertEqual(answer["digest"], preview["digest"])
        self.assertEqual(answer["identifiers"], sorted(b["ticket"] for b in preview["batch"]))
        self.assertEqual(answer["rejected"], preview["rejected"])
        before = self.disk()
        result = self.ccnavi(
            "--approve",
            "--yes",
            ",".join(answer["identifiers"]),
            "--digest",
            preview["digest"],
            "--json",
            *(only or []),
        )
        body = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNone(answer["stopped"])
        self.assertEqual(_flat(answer["lines"]), body["lines"])
        self.assertEqual(
            _normalized(answer["changes"]), _normalized(self.diff(before, self.disk()))
        )
        self.keep_scenario(name, request, answer)
        return answer

    def test_approve_new(self):
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()
        answer = self.same_as_cli("approve-new", "i0001")
        self.assertEqual(answer["identifiers"], ["i0001", "i0001-01"])

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
        answer = self.same_as_cli("approve-reopen", "i0001")
        self.assertIn(
            {"op": "delete", "path": ".ccnavi/approved/phases/i0001/1.reviewed"},
            answer["changes"]["i0001"],
        )

    def test_revision(self):
        self.family(plan=["research", "design"])
        self.propose("i0001", parent_text("i0001", ["research", "design", "acceptance"]))
        self.commit_parent()
        answer = self.same_as_cli("revise", "i0001")
        self.assertIn("  i0001 の全体計画を改版した", answer["lines"])

    def test_feedback_plan(self):
        fixture = self.reviewed_phase_one()
        self.assertEqual(self.confirm(fixture, 1).returncode, 0)
        self.commit_parent("reviewed")
        self.propose("i0001", parent_text("i0001", ["design"], feedback=[]))
        self.commit_parent()
        # 合流した子のワークツリーを片付ける。手元の判定は全ツリーの写しを読む（控えの無い家族。
        # D11）ので、残すと手元だけが子のツリーの古い写しを読み、判定が読んだ中身（read_set）
        # で作る指紋が Chrome（統合先と P だけを読む）と食い違う。
        git(
            self.root,
            "worktree",
            "remove",
            "--force",
            tree_mod.worktree_path(self.root, "i0001-01"),
        )
        answer = self.same_as_cli("feedback-plan", "i0001")
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
        answer = self.same_as_cli("predecessors", "i0003")
        self.assertEqual(answer["identifiers"], ["i0003-01"])
        self.assertEqual([r["ticket"] for r in answer["rejected"]], ["i0003-02"])

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
        self.assertEqual(answer["lines"], checked.changes.lines)
        applied, out, err = self.write_changes(checked.changes)
        self.assertEqual(applied.code, 0, out + err)
        self.assertEqual(_flat(answer["lines"]), _flat(out.splitlines()))
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

    def host_compare(self):
        """compare API の代わり: 依頼時の先頭から親の今の先頭までに変わったパス。"""
        recorded = read_json(
            os.path.join(self.parent_tree, ".ccnavi", "approved", "phases", "i0001", "1.requested")
        )["head"]
        head = git(self.parent_tree, "rev-parse", "HEAD").strip()
        names = git(self.parent_tree, "diff", "--name-only", "--no-renames", f"{recorded}..HEAD")
        return {"base": recorded, "head": head, "files": names.split()}

    def confirm_request(self, result, compare):
        heads = {"i0001": compare["head"]}
        return self.chrome_request(
            "confirm", "i0001", heads=heads, phase=1, result=result, compare=compare
        )

    def test_confirm(self):
        """レビュー済み: レビュー待ちの子を done/ へ動かし（settle_review）、印を置く。"""
        fixture = self.reviewed_phase_one()
        self.commit_parent("requested")
        result = {
            "host": "fixture",
            "mr": {"number": 7, "url": "u/7"},
            "threads": [],
            "reviews": [],
        }
        compare = self.host_compare()
        # 依頼の後に親の先頭が動いていれば、Python が compare の一覧を求める
        # （比べる相手は Python が出す）
        need = self.ask_chrome(
            self.chrome_request(
                "confirm", "i0001", heads={"i0001": compare["head"]}, phase=1, result=result
            )
        )
        self.assertEqual(need["need_compare"], {"base": compare["base"], "head": compare["head"]})
        self.assertIsNone(need["changes"])
        # 変更要求があれば通さない。問題点の文面は手元の CLI の標準エラーと同じ。
        changes_requested = {**result, "reviews": [{"state": "CHANGES_REQUESTED", "url": "r/1"}]}
        refused = self.ask_chrome(self.confirm_request(changes_requested, compare))
        write(fixture, json.dumps(changes_requested))
        local = self.confirm(fixture, 1)
        self.assertEqual(local.returncode, 1)
        self.assertEqual(refused["problems"], local.stderr.splitlines())
        self.assertIsNone(refused["changes"])
        write(fixture, json.dumps(result))

        request = self.confirm_request(result, compare)
        answer = self.ask_chrome(request)
        self.assertEqual(answer["problems"], [])
        before = self.disk()
        local = self.confirm(fixture, 1)
        self.assertEqual(local.returncode, 0, local.stdout + local.stderr)
        self.assertIsNone(answer["stopped"])
        self.assertEqual(_flat(answer["lines"]), local.stdout.splitlines())
        self.assertEqual(
            _normalized(answer["changes"]), _normalized(self.diff(before, self.disk()))
        )
        paths = [(r["op"], r["path"]) for r in answer["changes"]["i0001"]]
        self.assertIn(("create", ".ccnavi/approved/done/i0001-01.md"), paths)
        self.assertIn(("delete", "wip/proposals/review/i0001-01.md"), paths)
        mark = next(r for r in answer["changes"]["i0001"] if r["path"].endswith("1.reviewed"))
        # Chrome の印は経路（chrome）を持つ。アカウントは要求に無いので書かない（8.9）。
        self.assertEqual(
            json.loads(mark["content"]), {"mr": 7, "accepted": [], "via": "chrome", "at": STAMP}
        )
        event = [
            json.loads(line)
            for row in answer["changes"]["i0001"]
            if row["path"].endswith(".ndjson")
            for line in row["content"].splitlines()
        ][-1]
        self.assertEqual(event["via"], history.VIA_CHROME)
        self.keep_scenario("confirm", request, answer)

    def test_an_unreadable_stamp_is_an_error_not_an_exception(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        request = {**self.chrome_request("plan", "i0001"), "stamp": "2026-09-29 12:00"}
        answer = json.loads(_chrome().handle(json.dumps(request), os.path.join(self.root, "m")))
        self.assertIn("時刻の形が違う", answer["error"])


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

    def test_an_unreadable_marks_place_refuses(self):
        """マーカーの置き場を読めない（ディレクトリでない）なら、無いとは言わずに止める。"""
        text = self.approved_parent()
        write(os.path.join(self.approved, "phases", "i0001"), "not a directory\n")
        problems = self.problems({"i0001": text.encode()})
        self.assertTrue(any("phases/i0001/ を読めない" in p for p in problems), problems)

    def test_nothing_is_written_when_refused(self):
        text = self.approved_parent()
        self.start_parent()
        checked = core.withdraw(self.snapshot(), ["i0001"], {"i0001": text.encode()})
        self.assertIsNone(checked.changes)


class RecordWritesTest(CoreHarness):
    """`--record-writes`: この実行で書いたパスの一覧が、git status の変化と一致する。"""

    def place(self, root=None):
        return os.path.join(root or self.root, "logs", "state", "c1")

    def record(self, *args, stdin="", target=None):
        target = target or os.path.join(self.place(), "self", "i0001.t.writes")
        state = os.path.join(self.root, "logs", "state")
        result = self.ccnavi("--state", state, "--record-writes", target, *args, stdin=stdin)
        listed = []
        if os.path.isfile(target):
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

    def test_a_failed_command_still_writes_its_list(self):
        """コマンドが落ちても一覧は書く（C1 が戻すのに使う）。終了コードはコマンドのもの。"""
        result, listed = self.record("ticket", "start", "nope")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.place(), "self", "i0001.t.writes")))
        self.assertEqual(listed, [])

    def test_an_unwritable_list_ends_with_one(self):
        target = os.path.join(self.place(), "self", "busy")
        os.makedirs(os.path.join(target, "inside"))
        result, _ = self.record("--approve", "--preview", "--json", target=target)
        self.assertEqual(result.returncode, 1)
        self.assertIn("書いたパスの一覧を", result.stderr)

    def refused(self, target, *extra):
        result = self.ccnavi(*extra, "--record-writes", target, "ticket", "start", "i0001")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("--record-writes の書き出し先は", result.stderr)
        return result

    def test_the_list_goes_only_under_the_default_state_place(self):
        outside = os.path.join(self.root, "wip", "list.txt")
        self.refused(outside)
        self.assertFalse(os.path.exists(outside))
        # `..` で出る綴り。
        climbing = os.path.join(self.place(), "..", "..", "..", "wip", "list.txt")
        self.refused(climbing)
        self.assertFalse(os.path.exists(outside))
        # `--state` と `CCNAVI_STATE` で置き場を動かしても、書き出し先は動かない。
        moved = os.path.join(self.root, "wip")
        self.refused(os.path.join(moved, "c1", "list.txt"), "--state", moved)
        with mock.patch.dict(os.environ, {"CCNAVI_STATE": moved}):
            self.refused(os.path.join(moved, "c1", "list.txt"))
        self.assertFalse(os.path.exists(os.path.join(moved, "c1")))

    def test_links_under_the_place_are_not_followed(self):
        outside = os.path.join(self.root, "wip", "elsewhere")
        os.makedirs(outside)
        os.makedirs(self.place())
        os.symlink(outside, os.path.join(self.place(), "self"))
        self.refused(os.path.join(self.place(), "self", "list.txt"))
        self.assertEqual(os.listdir(outside), [])
        # 行き先そのものがリンク。リンクの先は書き換えない。
        victim = write(os.path.join(self.root, "wip", "victim.txt"), "keep\n")
        link = os.path.join(self.place(), "link.writes")
        os.symlink(victim, link)
        self.refused(link)
        with open(victim, encoding="utf-8") as f:
            self.assertEqual(f.read(), "keep\n")

    def test_a_linked_workspace_root_lists_relative_paths(self):
        """ワークスペースルートがリンク越し（macOS の /var → /private/var）でも、相対で書く。"""
        linked = self.root + "-link"
        os.symlink(self.root, linked)
        self.addCleanup(os.remove, linked)
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        digest = json.loads(self.ccnavi("--approve", "--preview", "--json").stdout)["digest"]
        target = os.path.join(linked, "logs", "state", "c1", "self", "l.writes")
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment.pop("CLAUDE_PROJECT_DIR", None)
        from tests.inproc import run_ccnavi

        result = run_ccnavi(
            [
                "--root",
                linked,
                "--state",
                os.path.join(linked, "logs", "state"),
                "--log",
                "",
                "--guard-core-files",
                "disable",
                "--restore-if-deny",
                "disable",
                "--guard-ticket-approval",
                "disable",
                "--record-writes",
                target,
                "--approve",
                "--yes",
                "i0001",
                "--digest",
                digest,
                "--json",
            ],
            input="",
            cwd=ROOT,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open(target, encoding="utf-8") as f:
            listed = sorted(line for line in f.read().splitlines() if line)
        self.assertEqual(listed, self.changed(self.parent_tree))

    def test_the_layer_writes_bypass_nothing(self):
        """fsio を通らない書き込みが残っていない（置き場を書くモジュールに素の書き込みが無い）。"""
        import re

        pattern = re.compile(
            r"os\.remove\(|os\.rename\(|os\.replace\(|shutil\.copy2?\(|shutil\.move\(|"
            r"os\.open\(|open\([^)]*[\"'][wax]b?[\"']"
        )
        # 素の書き込みを許す所と理由。
        allowed = {
            # 実行後の監視が範囲の外の変更を脇へ退ける（`gitstate.restore`）。状態の操作では
            # なく、C1 の書いたパスの一覧に載せるものでもない。
            ("gitstate", "shutil.move(source, target)"),
        }
        names = ("approval", "history", "configsync", "ops", "flow", "risk", "core")
        names += ("review", "phase", "ticket", "gitstate")
        for name in names:
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
            hits = [h for h in hits if not any(n == name and w in h for n, w in allowed)]
            self.assertEqual(hits, [], name)


if __name__ == "__main__":
    unittest.main()
