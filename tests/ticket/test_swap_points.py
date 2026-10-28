"""判定のコアと差し替え点の受入テスト。

判定のコアは入出力を持たない関数にし、ファイルシステム・git・API は差し替え点に分ける。
Chrome（Pyodide）と手元が同じコアで判定するため。

見るのは 7 つ。

1. 時計（Clock の差し替え点）: `fsio.clock` で固定した時刻を、承認の記録と履歴が同じに書く
2. 承認の記録の `source_path` はリポジトリからの相対、`source_tree` はブランチ名。
   前の形（絶対パス・ツリーの名前）の承認済みチケットも同じに読み、判定の答えは変わらない
3. plan と Writer(FS): 書くもの（Changes）を並べるだけではディスクは変わらず、並べたものを
   書いた結果が Changes のとおりになる（改版・マーカーの消去・フローの移動・フィードバック計画）
4. Chrome の入口（`ccnavi_chrome.py`）が同じ入力から、手元が実際に書いたのと同じバイト列を出す
   （新規・マーカーの消去・改版・フィードバック計画・多段の先行と外れる提案・レビュー済み）。
   同じ要求と答えを拡張の試験の見本（`extensions/chrome/ccnavi-approval/test/fixtures/core-scenarios.json`）
   に置き、拡張の試験が Pyodide でも同じ答えになることを見る
5. fsio の記録層（`--record-writes`）: 各コマンドで書いたパスの一覧が `git status` の変化と一致する
6. Chrome の入口は置き場を既定に固定する。統合先の `.claude/settings.json` の `env` や手元の env に
   置き場の env を入れても既定の置き場を使い、`settings` の env 名の定数を参照しない

見本の形を変えたら `CCNAVI_CHROME_FIXTURE=1` を付けてこのテストを走らせ、見本を書き直す。
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from ccnavi.entry import lint, version
from ccnavi.infra import fsio, pathcase, settings
from ccnavi.infra import tree as tree_mod
from ccnavi.tickets import approval, approval_ops, core, core_base
from ccnavi.tickets.model import ticket_model, via
from tests import requires_symlink
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import ROOT, git, read_json, write
from tests.ticketsample import CHROME, child_text, load_chrome, parent_text

SCENARIOS = os.path.join(CHROME, "test", "fixtures", "core-scenarios.json")
STAMP = "2026-09-29T12:00:00+0900"
PLACES = (".ccnavi", "wip")


def _place_of(chrome):
    """入口（`handle` の `op: placement`）が答える置き場。`_placement` の引数の形に依らない。"""
    request = {"schema": chrome.SCHEMA, "op": "placement"}
    answer = json.loads(chrome.handle(json.dumps(request), os.devnull))
    if "error" in answer:
        raise AssertionError(f"置き場を読めない: {answer['error']}")
    return answer["placement"]


def _text(raw: bytes) -> str:
    """ファイルの中身を本文にする。Windows が書く改行（CRLF）は LF に揃える。"""
    return raw.decode().replace("\r\n", "\n")


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
                    rows.append({"op": "create", "path": path, "content": _text(new[path])})
                elif old[path] != new[path]:
                    rows.append({"op": "update", "path": path, "content": _text(new[path])})
            if rows:
                out[name] = rows
        return out

    def snapshot(self):
        return core.read_fs(self.conf(), self.root, STAMP, core.Actor("", via.VIA_CHROME))

    def write_changes(self, changes):
        out, err = io.StringIO(), io.StringIO()
        with via.session(via.VIA_CHROME):
            applied = core.write_fs(out, err, changes.planned)
        return applied, out.getvalue(), err.getvalue()

    def plan_local(self, only=None):
        """手元でコアを通して承認する。答えは (Changes, 書いた前後の差分)。"""
        snapshot = self.snapshot()
        with via.session(via.VIA_CHROME):
            verdict = core.judge_approval(snapshot, only)
            changes = core.plan(snapshot, verdict)
        before = self.disk()
        applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 0, out + err)
        return changes, self.diff(before, self.disk())

    def reviewed_phase_one(self):
        """フェーズ 1（MR で見る設計）の子を閉じて合流し、レビューを依頼したところまで進める。"""
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        return fixture

    # ---- Chrome の要求

    def chrome_request(self, op, family, heads=None, **extra):
        """手元のツリーから、拡張が組むのと同じ形の要求を作る（統合先は main）。

        ブランチの先頭は既定で `0` を並べたもの。
        `heads` で名前ごとに実際の先頭を渡せる（レビュー済みは
        依頼時の先頭と比べるので、親のブランチの実際の先頭が要る）。
        """
        chrome = load_chrome()
        place = _place_of(chrome)
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
        return request

    def ask_chrome(self, request):
        root = os.path.join(self.root, "memfs")
        answer = json.loads(load_chrome().handle(json.dumps(request), root))
        self.assertNotIn("error", answer, answer)
        return answer

    def keep_scenario(self, name, request, answer):
        """拡張の試験の見本に置く（要求と、手元の CPython の答え）。"""
        CoreChromeTest.collected[name] = {"request": request, "answer": answer}


class ClockTest(unittest.TestCase):
    def test_a_fixed_clock_gives_the_stamp(self):
        with fsio.clock("2026-09-29T09:30:05+0900"):
            self.assertEqual(fsio.stamp(), "2026-09-29T09:30:05+0900")
        self.assertNotEqual(fsio.stamp(), "2026-09-29T09:30:05+0900")

    def test_an_unreadable_stamp_is_refused(self):
        with self.assertRaises(ValueError), fsio.clock("2026-09-29 09:30"):
            pass


class SourcePathTest(CoreHarness):
    """承認は提案の中身を変えない。"""

    def test_the_copy_is_the_proposal(self):
        text = parent_text("i0001", ["research"])
        self.propose("i0001", text)
        self.commit_parent()
        proposal = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001.md")
        with open(proposal, "rb") as f:
            before = f.read()
        preview = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)
        self.assertIn("提案: wip/proposals/todo/i0001.md", preview["text"])
        self.assertNotIn(self.root, preview["text"])
        self.assertEqual(self.approve().returncode, 0)
        path = os.path.join(self.approved, "doing", "i0001.md")
        with open(path, "rb") as f:
            self.assertEqual(before, f.read())

    def test_a_crlf_proposal_moves_byte_for_byte(self):
        text = parent_text("i0001", ["research"]).replace("\n", "\r\n")
        path = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0001.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(text.encode("utf-8"))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        with open(os.path.join(self.approved, "doing", "i0001.md"), "rb") as f:
            self.assertEqual(text.encode("utf-8"), f.read())


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
        from ccnavi.infra import tree

        self.assertEqual(tree.branch_of(self.repo("ref: refs/heads/main\n")), "main")
        self.assertIsNone(tree.branch_of(self.repo("0123456789abcdef0123456789abcdef01234567\n")))
        self.assertIsNone(tree.branch_of(self.repo("")))
        self.assertIsNone(tree.branch_of(self.repo("ref: refs/heads/\n")))
        self.assertIsNone(tree.branch_of(self.repo("garbage")))
        self.assertIsNone(tree.branch_of(os.path.join(self.base, "none")))

    def test_a_relative_gitdir_is_read_from_the_tree(self):
        from ccnavi.infra import tree

        root = self.repo("ref: refs/heads/i0001\n", gitfile="gitdir: meta\n")
        self.assertEqual(tree.branch_of(root), "i0001")


class DetachedHeadTest(CoreHarness):
    """親のワークツリーの HEAD がブランチを指していなければ承認しない。"""

    def test_a_detached_head_is_not_approved(self):
        # 親のワークツリーの HEAD がブランチを指していなければ、ほかのツリーで代用せずに止まる
        git(self.parent_tree, "checkout", "--quiet", "--detach")
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        self.assertNotEqual(self.approve().returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))


class PlanWriterTest(CoreHarness):
    """plan は書かない。Writer(FS) が書いた結果は plan の Changes のとおり。"""

    def plan_and_write(self, only=None):
        snapshot = self.snapshot()
        before = self.disk()
        with via.session(via.VIA_CHROME):
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
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",), False)
        )
        self.commit_parent()
        changes, out = self.plan_and_write()
        self.assertIn("承認した。", out)
        rows = changes.per_branch()["i0001"]
        self.assertEqual(
            [(r["op"], r["path"]) for r in rows],
            [
                ("create", ".ccnavi/approved/doing/i0001-01-01.md"),
                ("create", ".ccnavi/approved/doing/i0001.md"),
                # 先行関係は書かない（承認済みチケットの計画から都度計算する）
                ("delete", "wip/proposals/todo/i0001-01-01.md"),
                ("delete", "wip/proposals/todo/i0001.md"),
            ],
        )
        # 承認済みチケットは提案のバイト列のまま（承認の記録を書き足さない）
        self.assertEqual(rows[1]["content"], parent_text("i0001", ["research", "design"]).encode())

    def test_revision_and_feedback_plan(self):
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
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
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        marks = os.path.join(self.approved, "phases", "i0001")
        write(os.path.join(marks, "1.requested"), '{"at": "x"}')
        write(os.path.join(marks, "1.reviewed"), '{"at": "x"}')
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        _, out = self.plan_and_write()
        self.assertIn("マーカー（requested, reviewed）を削除しました。", out)
        self.assertFalse(os.path.exists(os.path.join(marks, "1.reviewed")))


@contextlib.contextmanager
def durable_writes():
    """一時ファイル→fsync→os.replace の書き方（`fsio._replace_durably`）を通ったパスを集める。

    呼び出し側が素の書き方（`write_bytes`・`write_new`）に戻されると、ここに載らない。
    """
    seen: list[str] = []
    real = fsio._replace_durably

    def record(path, content):
        seen.append(pathcase.fold(os.path.abspath(path)))
        return real(path, content)

    with mock.patch.object(fsio, "_replace_durably", record):
        yield seen


def _norm(path: str) -> str:
    return pathcase.fold(os.path.abspath(path))


class DurableWiringTest(CoreHarness):
    """承認の書き込みが、途中を見せず fsync する書き方を通ること（呼び出し側の配線）。"""

    def planned(self):
        snapshot = self.snapshot()
        with via.session(via.VIA_CHROME):
            return core.plan(snapshot, core.judge_approval(snapshot))

    def test_approval_writes_the_ticket_durably_and_no_workflow(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        changes = self.planned()
        with durable_writes() as seen:
            applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 0, out + err)
        self.assertIn(_norm(os.path.join(self.approved, "doing", "i0001.md")), seen)
        held = os.path.join(self.approved, "phases", "i0001", "workflow.yml")
        self.assertNotIn(_norm(held), seen)
        self.assertFalse(os.path.exists(held))

    def approved_parent_copy(self):
        self.family(plan=["design"])
        found, _ = approval.scan(self.conf(), self.root)
        (parent,) = [t for t in found if t.ticket == "i0001"]
        return parent

    def test_start_and_finish_fields_are_rewritten_durably(self):
        """着手・終わりの欄の書き直し（`update_fields`）も、承認済みチケットを途中を見せずに書く。"""
        parent = self.approved_parent_copy()
        with durable_writes() as seen:
            self.assertEqual(approval_ops.update_fields(parent.path, {"started_at": "x"}), "")
        self.assertEqual(seen, [_norm(parent.path)])
        with open(parent.path, encoding="utf-8") as f:
            self.assertIn('started_at: "x"', f.read())

    def test_a_followup_child_is_written_durably(self):
        """続きの子（`followup`）は `doing/` に直に起こすので、同じ書き方で置く。"""
        parent = self.approved_parent_copy()
        with durable_writes() as seen:
            ident, failed = approval_ops.followup(
                self.conf(), self.root, parent, 1, [parent], ["指摘"]
            )
        self.assertEqual(failed, "")
        written = [
            p for p in seen if p.endswith(pathcase.fold(os.path.join("doing", ident + ".md")))
        ]
        self.assertEqual(len(written), 1, seen)
        self.assertTrue(os.path.exists(written[0]))

    def test_the_new_durable_op_does_not_overwrite_and_goes_durably(self):
        target = os.path.join(self.root, "flows-test", "a.yml")
        op = fsio.Op(fsio.OP_NEW_DURABLE, target, b"new")
        with durable_writes() as seen:
            self.assertEqual(core_base._write_op(op), "")
        self.assertEqual(seen, [_norm(target)])
        with durable_writes() as seen:
            self.assertNotEqual(core_base._write_op(fsio.Op(fsio.OP_NEW_DURABLE, target, b"x")), "")
        self.assertEqual(seen, [])
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"new")


class WriterFailureTest(CoreHarness):
    """Writer(FS) の失敗の仕方。並べる段では書き込みが失敗しないので、書くときの枝を試す。"""

    def planned(self):
        snapshot = self.snapshot()
        with via.session(via.VIA_CHROME):
            return core.plan(snapshot, core.judge_approval(snapshot))

    def failing(self, name, when):
        """`fsio.<name>` を、`when(引数)` が真の呼び出しだけ失敗させる。"""
        real = getattr(fsio, name)

        def fake(*args):
            return "わざと失敗させた" if when(*args) else real(*args)

        return mock.patch.object(fsio, name, fake)

    def test_a_proposal_that_cannot_be_removed_undoes_the_copy_and_stops(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        changes = self.planned()
        with self.failing("unlink", lambda path: path.endswith(os.path.join("todo", "i0001.md"))):
            applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 1)
        self.assertEqual(applied.placed, [])
        self.assertIn("ccnavi: i0001: 提案を todo/ から動かせない (わざと失敗させた)", err)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))
        self.assertNotIn("承認した。", out)

    def held(self):
        return os.path.join(self.approved, "phases", "i0001", "workflow.yml")

    def test_a_failed_move_leaves_no_copy_and_no_workflow_file(self):
        """承認は先行関係を書かない。動かす段で落ちても、承認済みチケットも先行関係のファイルも残らない。"""
        doing = os.path.join("doing", "i0001.md")
        todo = os.path.join("todo", "i0001.md")
        for name, when in (
            ("unlink", lambda path: path.endswith(todo)),
            ("write_bytes_atomic", lambda path, data: path.endswith(doing)),
        ):
            with self.subTest(name=name):
                self.propose("i0001", parent_text("i0001", ["research"]))
                self.commit_parent()
                changes = self.planned()
                with self.failing(name, when):
                    applied, out, err = self.write_changes(changes)
                self.assertEqual(applied.code, 1, out + err)
                self.assertFalse(os.path.exists(os.path.join(self.approved, doing)))
                self.assertFalse(os.path.exists(self.held()), err)

    def test_a_failed_revision_leaves_the_copy_as_it_was(self):
        """改版でチケットを書く段で落ちたら、承認済みチケットは前のまま。先行関係のファイルは書かない。"""
        self.family(plan=["research", "design"])
        copy = os.path.join(self.approved, "doing", "i0001.md")
        with open(copy, "rb") as f:
            before = f.read()
        self.propose("i0001", parent_text("i0001", ["research", "design", "acceptance"]))
        self.commit_parent()
        changes = self.planned()
        doing = os.path.join("doing", "i0001.md")
        with self.failing("write_text_durable", lambda path, *rest: path.endswith(doing)):
            applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 1, out + err)
        with open(copy, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertFalse(os.path.exists(self.held()))

    def test_a_revised_proposal_that_cannot_be_removed_is_said_and_goes_on(self):
        self.family(plan=["research", "design"])
        self.propose("i0001", parent_text("i0001", ["research", "design", "acceptance"]))
        self.commit_parent()
        changes = self.planned()
        with self.failing("unlink", lambda path: path.endswith(os.path.join("todo", "i0001.md"))):
            applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 0, out + err)
        self.assertIn("ccnavi: i0001: 改版の提案を todo/ から削除できない (わざと失敗させた)", err)
        self.assertIn("i0001 の全体計画を改版した", out)

    def reopened(self):
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        marks = os.path.join(self.approved, "phases", "i0001")
        write(os.path.join(marks, "1.requested"), '{"at": "x"}')
        write(os.path.join(marks, "1.reviewed"), '{"at": "x"}')
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        return marks

    def test_a_reviewed_mark_that_cannot_be_removed_stops(self):
        """reviewed を消せなければ止める。ほかの種類には手を付けない。"""
        marks = self.reopened()
        changes = self.planned()
        with self.failing("unlink", lambda path: path.endswith("1.reviewed")):
            applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 1)
        self.assertEqual(applied.placed, ["i0001-01-02"])
        self.assertIn("レビュー済みのマーカー", err)
        self.assertIn("ここで止める", err)
        self.assertTrue(os.path.exists(os.path.join(marks, "1.requested")))
        self.assertTrue(os.path.exists(os.path.join(marks, "1.reviewed")))
        self.assertNotIn("を削除した", out)

    def test_another_mark_that_cannot_be_removed_is_said_and_left(self):
        """reviewed 以外は言って続ける。行は実際に消せた種類だけ。"""
        marks = self.reopened()
        changes = self.planned()
        # 見え方（Chrome の 1 コミット）では両方消える。
        self.assertIn(
            "  i0001 のフェーズ 1 のマーカー（requested, reviewed）を削除しました。"
            "全部閉じたらレビューをもう一度頼むことになる",
            changes.lines,
        )
        with self.failing("unlink", lambda path: path.endswith("1.requested")):
            applied, out, err = self.write_changes(changes)
        self.assertEqual(applied.code, 0, out + err)
        self.assertIn(
            "削除できませんでした（わざと失敗させた）。requested は残っていて有効なまま", err
        )
        self.assertIn("マーカー（reviewed）を削除しました", out)
        self.assertTrue(os.path.exists(os.path.join(marks, "1.requested")))
        self.assertFalse(os.path.exists(os.path.join(marks, "1.reviewed")))

    @requires_symlink
    def test_a_mark_that_is_a_link_is_removed_as_a_link(self):
        """溜める段の消去はリンクを辿らない（行き先を消さない）。"""
        marks = self.reopened()
        outside = write(os.path.join(self.root, "outside.json"), "{}")
        os.remove(os.path.join(marks, "1.requested"))
        os.symlink(outside, os.path.join(marks, "1.requested"))
        applied, out, err = self.write_changes(self.planned())
        self.assertEqual(applied.code, 0, out + err)
        self.assertFalse(os.path.lexists(os.path.join(marks, "1.requested")))
        self.assertTrue(os.path.exists(outside))

    def test_a_move_whose_target_appeared_stops(self):
        """並べた後に行き先が置かれていたら、動かさずに止める。"""
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        from ccnavi.tickets.model import review_host

        result = review_host.Result.from_data({"host": "fixture", "mr": {"number": 7, "url": "u"}})
        checked = core.confirm(self.snapshot(), "i0001", 1, result, "")
        done = write(os.path.join(self.approved, "done", "i0001-01-01.md"), "other\n")
        applied, out, err = self.write_changes(checked.changes)
        self.assertEqual(applied.code, 1)
        self.assertIn("移動先に既にある", err)
        with open(done, encoding="utf-8") as f:
            self.assertEqual(f.read(), "other\n")


def _normalized(changes):
    """経路（`via`・`settled_by`）の欄を除いた Changes。

    手元の CLI（board・cli）と Chrome（chrome）で違う所。
    """
    out = {}
    for name, rows in (changes or {}).items():
        kept = []
        for row in rows:
            row = dict(row)
            content = row.get("content")
            if isinstance(content, str):
                content = row["content"] = content.replace("\r\n", "\n")
                # 子を動かした経路の欄（settled_by）も、CLI（confirm）と Chrome（chrome）で違う。
                content = row["content"] = re.sub(
                    r'^settled_by: "[a-z-]+"\n', "", content, flags=re.M
                )
            if content is not None and row["path"].endswith(".reviewed"):
                mark = json.loads(content)
                mark.pop("via", None)
                row["content"] = mark
            kept.append(row)
        out[name] = kept
    return out


def _machine_newline_is_lf() -> bool:
    """機械の既定の改行が LF か。実際にテキストで書いて確かめる（Windows は CRLF になる）。

    ccnavi は改行を指定しない書き込みを機械の改行で書き、承認のダイジェストと git のコミットは
    そのバイト列で決まる。見本（core-scenarios.json）は LF で作ってあるので、CRLF の機械では
    ダイジェストとコミットの値が合わない。
    """
    with tempfile.TemporaryDirectory(prefix="ccnavi-newline-probe-") as tmp:
        path = os.path.join(tmp, "probe")
        with open(path, "w", encoding="utf-8") as f:
            f.write("a\n")
        with open(path, "rb") as f:
            return f.read() == b"a\n"


MACHINE_NEWLINE_IS_LF = _machine_newline_is_lf()


def _flat(lines):
    """見せる行を 1 行ずつに（CLI の JSON の `lines` と同じ切り方）。"""
    return [part for line in lines for part in line.split("\n") if part.strip()]


class CoreChromeTest(CoreHarness):
    """Chrome の入口が、手元の CLI が実際に書いたのと同じバイト列と出力を出す。

    比べるのは、Changes（経路の欄だけを除いて）、見せる行、止まったか、問題点の文面、
    画面の本文とダイジェスト。
    """

    collected: dict = {}

    def setUp(self):
        # 見本は走らせるたびに同じ中身にする。時刻（承認の記録・履歴・マーカー）は fsio の時計で、
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
        # 統合先の互換のマーカー（Chrome は版が違えば書く操作を受けない）。
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
        """拡張の試験の見本と突き合わせる（書き直すときは記録しておく）。場面ごとにその場で比べる。"""
        CoreChromeTest.collected[name] = {"request": request, "answer": answer}
        if os.environ.get("CCNAVI_CHROME_FIXTURE"):
            return
        if not MACHINE_NEWLINE_IS_LF:
            self.skipTest("機械の改行が CRLF で、見本（LF で作ってある）とダイジェストが合わない")
        with open(SCENARIOS, encoding="utf-8") as f:
            held = json.load(f)
        self.assertIn(
            name, held, f"{SCENARIOS} に {name} が無い。CCNAVI_CHROME_FIXTURE=1 で書き直す"
        )
        self.assertEqual(
            held[name],
            json.loads(json.dumps({"request": request, "answer": answer})),
            f"{SCENARIOS} の {name} が古い。"
            "CCNAVI_CHROME_FIXTURE=1 を付けて tests.ticket.test_swap_points を回す",
        )

    def test_the_fixture_has_every_scenario(self):
        with open(SCENARIOS, encoding="utf-8") as f:
            self.assertEqual(sorted(json.load(f)), sorted(SCENARIO_NAMES))

    def same_as_cli(self, name, family, only=None):
        """同じ状態で、Chrome の plan と手元の CLI（preview → --yes）を比べる。"""
        request = self.chrome_request("plan", family, **({"only": only} if only else {}))
        answer = self.ask_chrome(request)
        preview = json.loads(self.ccnavi("--agree", "--preview", "--json", *(only or [])).stdout)
        self.assertEqual(answer["text"], preview["text"])
        self.assertEqual(answer["digest"], preview["digest"])
        self.assertEqual(answer["identifiers"], sorted(b["ticket"] for b in preview["batch"]))
        self.assertEqual(answer["rejected"], preview["rejected"])
        before = self.disk()
        result = self.ccnavi(
            "user",
            "agree",
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
        # 親の allow は計画の範囲と重なる形にする（重ならない親の警告は子の超過と混ざる）
        self.propose("i0001", parent_text("i0001", ["research", "design"], allow=("wip/*",)))
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",), False)
        )
        self.commit_parent()
        answer = self.same_as_cli("approve-new", "i0001")
        self.assertEqual(answer["identifiers"], ["i0001", "i0001-01-01"])

    def test_approve_reopens_a_reviewed_phase(self):
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        marks = os.path.join(self.approved, "phases", "i0001")
        write(os.path.join(marks, "1.requested"), '{"at": "x"}')
        write(os.path.join(marks, "1.reviewed"), '{"at": "x"}')
        self.propose("i0001-01-02", child_text("i0001-01-02", "i0001", 1, ["wip/design/*"]))
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
        # 合流した子のワークツリーを片付ける。手元の判定は全ツリーの承認済みチケットを読む
        # ので、残すと手元だけが子のツリーの古い承認済みチケットを読み、判定が読んだ中身
        # （read_set）で作るダイジェストが Chrome（統合先と P だけを読む）と食い違う。
        git(
            self.root,
            "worktree",
            "remove",
            "--force",
            tree_mod.worktree_path(self.root, "i0001-01-01"),
        )
        answer = self.same_as_cli("feedback-plan", "i0001")
        self.assertIn("  i0001 のフィードバック計画を改版した", answer["lines"])

    def test_predecessors_across_families(self):
        """多段の先行（閉包）。通る子と、先行が閉じていないので外れる子（文面まで同じ）。"""
        copies = {
            "i0001": [
                ("doing", parent_text("i0001", ["research"])),
                ("done", self.done("i0001-01-01", "i0001", [])),
            ],
            "i0002": [
                ("doing", parent_text("i0002", ["research"])),
                ("done", self.done("i0002-01-01", "i0002", ["i0001-01-01"])),
                ("doing", child_text("i0002-01-02", "i0002", 1, ["wip/research/*"], False)),
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
            os.path.join(todo, "i0003-01-01.md"),
            self.with_predecessors(
                child_text("i0003-01-01", "i0003", 1, ["wip/research/*"], False), ["i0002-01-01"]
            ),
        )
        write(
            os.path.join(todo, "i0003-01-02.md"),
            self.with_predecessors(
                child_text("i0003-01-02", "i0003", 1, ["wip/research/*"], False), ["i0002-01-02"]
            ),
        )
        for path in trees.values():
            git(path, "add", "-A")
            git(path, "commit", "--quiet", "-m", "copies")
        answer = self.same_as_cli("predecessors", "i0003")
        self.assertEqual(answer["identifiers"], ["i0003-01-01"])
        self.assertEqual([r["ticket"] for r in answer["rejected"]], ["i0003-01-02"])

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
        """レビュー済み: レビュー待ちの子を done/ へ動かし（settle_review）、マーカーを置く。"""
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
        # 分岐の材料は文面ではなくキー（文面の頭にも同じコードが載る）
        self.assertEqual(refused["code"], "CORE_CONFIRM_REVIEW_OPEN")
        self.assertTrue(refused["problems"][0].startswith("ccnavi: [CORE_CONFIRM_REVIEW_OPEN] "))
        write(fixture, json.dumps(result))

        request = self.confirm_request(result, compare)
        answer = self.ask_chrome(request)
        self.assertEqual(answer["problems"], [])
        self.assertIsNone(answer["code"])
        before = self.disk()
        local = self.confirm(fixture, 1)
        self.assertEqual(local.returncode, 0, local.stdout + local.stderr)
        self.assertIsNone(answer["stopped"])
        self.assertEqual(_flat(answer["lines"]), local.stdout.splitlines())
        self.assertEqual(
            _normalized(answer["changes"]), _normalized(self.diff(before, self.disk()))
        )
        paths = [(r["op"], r["path"]) for r in answer["changes"]["i0001"]]
        self.assertIn(("create", ".ccnavi/approved/done/i0001-01-01.md"), paths)
        self.assertIn(("delete", "wip/proposals/review/i0001-01-01.md"), paths)
        mark = next(r for r in answer["changes"]["i0001"] if r["path"].endswith("1.reviewed"))
        # Chrome のマーカーは経路（chrome）を持つ。アカウントは要求に無いので書かない。
        self.assertEqual(
            json.loads(mark["content"]), {"mr": 7, "accepted": [], "via": "chrome", "at": STAMP}
        )
        self.keep_scenario("confirm", request, answer)

    def test_confirm_a_hand_moved_child_asks_for_the_time_and_accepts_old_threads(self):
        """手で done/ に動かされた子: 移動の時刻を拡張に求め、移動前のスレッドを台帳に受ける。"""
        import shutil

        self.reviewed_phase_one()
        waiting = os.path.join(self.parent_tree, "wip", "proposals", "review", "i0001-01-01.md")
        done = os.path.join(self.parent_tree, ".ccnavi", "approved", "done", "i0001-01-01.md")
        os.makedirs(os.path.dirname(done), exist_ok=True)
        shutil.move(waiting, done)
        self.commit_parent("moved by hand")
        old = {
            "id": "t1",
            "url": "https://h/c/1",
            "body": "直して",
            "created_at": "2026-01-01T00:00:00Z",
        }
        old["last_id"] = "c1"
        result = {
            "host": "fixture",
            "mr": {"number": 7, "url": "u/7"},
            "threads": [old],
            "reviews": [],
        }
        compare = self.host_compare()
        need = self.ask_chrome(self.confirm_request(result, compare))
        self.assertEqual(need["need_manual"], {"paths": [".ccnavi/approved/done/i0001-01-01.md"]})
        self.assertIsNone(need["changes"])
        moved = self.ask_chrome({**self.confirm_request(result, compare), "manual_moved_at": 4e9})
        self.assertEqual(moved["problems"], [])
        paths = [r["path"] for r in moved["changes"]["i0001"]]
        self.assertIn(".ccnavi/approved/phases/i0001/accepted.json", paths)
        mark = next(r for r in moved["changes"]["i0001"] if r["path"].endswith("1.reviewed"))
        self.assertEqual(json.loads(mark["content"])["via"], "manual")
        # 移動の時刻を読めなければ確かめられないので、未解決の指摘は受け入れない
        unknown = self.ask_chrome({**self.confirm_request(result, compare), "manual_moved_at": 0})
        self.assertIsNone(unknown["changes"])
        self.assertTrue(unknown["problems"])

    def test_an_unreadable_stamp_is_an_error_not_an_exception(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        request = {**self.chrome_request("plan", "i0001"), "stamp": "2026-09-29 12:00"}
        answer = json.loads(load_chrome().handle(json.dumps(request), os.path.join(self.root, "m")))
        self.assertIn("時刻の形が違う", answer["error"])


SCENARIO_NAMES = (
    "approve-new",
    "approve-reopen",
    "revise",
    "feedback-plan",
    "predecessors",
    "confirm",
)


# 置き場を既定から外す env の名前（統合先の `.claude/settings.json` に書かれうるもの）。
# `settings` の定数ではなく綴りで持つ。定数が消えても（i0064）この試験は読み込める。
MOVED_ENV_NAMES = ("CCNAVI_TICKETS_PROPOSAL", "CCNAVI_TICKETS_APPROVED", "CCNAVI_PROJECT_HOME")

# env 名の定数（i0064 で消える）。ccnavi_chrome.py はこれを引かない。
ENV_CONSTANTS = ("TICKETS_ENV", "APPROVED_ENV", "PROJECT_HOME_ENV")

# 置き場の答えのうち、既定の置き場から組まれるはずの欄。
PLACEMENT_KEYS = (
    "tickets",
    "approved",
    "layer_dir",
    "integration_paths",
    "integration_files",
    "branch_paths",
    "workspace_paths",
    "workspace_files",
    "project_paths",
)


def _default_placement(chrome):
    """既定の置き場（`settings.DEFAULT_*`）から組んだ、置き場の答えの欄。"""
    tickets = settings.DEFAULT_TICKETS.replace(os.sep, "/").strip("/")
    approved = settings.DEFAULT_APPROVED.replace(os.sep, "/").strip("/")
    home = settings.DEFAULT_PROJECT_HOME.replace(os.sep, "/").strip("/")
    layer = f"{home}/{settings.LAYER_CONFIG_DIR}"
    done = f"{approved}/{ticket_model.DONE}"
    return {
        "tickets": tickets,
        "approved": approved,
        "layer_dir": layer,
        "integration_paths": sorted({done, chrome.COMMON_LAYER, layer}),
        "integration_files": [chrome.SETTINGS_FILE, chrome.COMPAT_FILE],
        "branch_paths": [approved, tickets],
        "workspace_paths": sorted({chrome.COMMON_LAYER, layer}),
        "workspace_files": [chrome.SETTINGS_FILE, chrome.COMPAT_FILE],
        "project_paths": sorted({done, layer}),
    }


def _settings_text(env):
    """統合先の `.claude/settings.json` の中身（置き場の env と、置き場に関わらない env）。"""
    return json.dumps({"env": {"CCNAVI_MODE": "enforce", **env}}, ensure_ascii=False)


class ChromePlacementTest(CoreHarness):
    """Chrome の入口は置き場を既定に固定し、置き場を動かす env を読まない。

    置き場は入口（`handle`）を通して確かめ、`_placement` の引数の形には縛られない。
    `CoreChromeTest` の見本には載せない（見本は拡張の側に置くもの）。
    """

    def ask_placement(self, chrome=None, **extra):
        chrome = chrome or load_chrome()
        request = {"schema": chrome.SCHEMA, "op": "placement", **extra}
        answer = json.loads(chrome.handle(json.dumps(request), os.path.join(self.root, "memfs")))
        self.assertNotIn("error", answer, answer)
        return chrome, answer["placement"]

    def assert_default(self, chrome, place):
        self.assertEqual(
            {k: place.get(k) for k in PLACEMENT_KEYS}, _default_placement(chrome), place
        )
        # 置き場の env は答えに載らない（欄ごと消してもよい）。
        self.assertEqual(place.get("env", {}), {}, place)

    def board_request(self):
        """手元のツリーから組んだ `board` の要求と、env を入れないときの答え。"""
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",), False)
        )
        self.commit_parent()
        request = self.chrome_request("board", "i0001")
        plain = json.loads(load_chrome().handle(json.dumps(request), os.path.join(self.root, "m1")))
        self.assertNotIn("error", plain, plain)
        self.assertEqual([e["ticket"] for e in plain["batch"]], ["i0001", "i0001-01-01"], plain)
        return request, plain

    def test_relative_env_in_the_integration_settings_is_not_read(self):
        # B1
        moved = {
            "CCNAVI_TICKETS_PROPOSAL": "moved/proposals",
            "CCNAVI_TICKETS_APPROVED": "moved/approved",
            "CCNAVI_PROJECT_HOME": "moved/home",
        }
        chrome, place = self.ask_placement(settings=_settings_text(moved))
        self.assert_default(chrome, place)

    def test_absolute_env_in_the_integration_settings_is_not_refused(self):
        # B2
        for form in ("/x", "C:/x", "~/x"):
            with self.subTest(form=form):
                moved = {name: f"{form}/{i}" for i, name in enumerate(MOVED_ENV_NAMES)}
                chrome, place = self.ask_placement(settings=_settings_text(moved))
                self.assert_default(chrome, place)

    def test_the_board_answer_does_not_change_with_the_env(self):
        # B3
        request, plain = self.board_request()
        moved = {
            "CCNAVI_TICKETS_PROPOSAL": "moved/proposals",
            "CCNAVI_TICKETS_APPROVED": "moved/approved",
            "CCNAVI_PROJECT_HOME": "moved/home",
        }
        with_env = {**request, "settings": _settings_text(moved)}
        answer = json.loads(
            load_chrome().handle(json.dumps(with_env), os.path.join(self.root, "m2"))
        )
        self.assertEqual(answer, plain)

    def test_the_module_does_not_refer_to_the_env_names(self):
        # B4 （1 段目）ソースが env 名の定数を引かない。土台の名前（`settings.`・別名）を問わず
        # 属性参照を拾い、`from ... import TICKETS_ENV` と、`getattr` に渡す同じ綴りの文字列も拾う。
        path = os.path.join(CHROME, "py", "ccnavi_chrome.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), path)
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ENV_CONSTANTS:
                found.append(f"{node.lineno}: 属性 {ast.unparse(node)}")
            elif isinstance(node, ast.ImportFrom):
                found += [
                    f"{node.lineno}: import {a.name}" for a in node.names if a.name in ENV_CONSTANTS
                ]
            elif isinstance(node, ast.Constant) and node.value in ENV_CONSTANTS:
                found.append(f"{node.lineno}: 文字列 {node.value!r}")
        self.assertEqual(
            sorted(found), [], "ccnavi_chrome.py が settings の env 名の定数を参照している"
        )

    def test_the_module_loads_without_the_env_name_constants(self):
        # B4 （2 段目）定数を外しても読み込める。外している間は読み込みだけを見る。
        # `settings.load` は i0064 まで定数を引くので、外したまま置き場を聞くと
        # 正しい実装でも失敗する。置き場は戻してから聞く。
        saved = {name: getattr(settings, name) for name in ENV_CONSTANTS if hasattr(settings, name)}
        for name, value in saved.items():
            self.addCleanup(setattr, settings, name, value)
        for name in saved:
            delattr(settings, name)
        try:
            chrome = load_chrome()
        except (AttributeError, ImportError) as exc:
            loaded = exc
        else:
            loaded = None
        finally:
            for name, value in saved.items():
                setattr(settings, name, value)
        if loaded is not None:
            self.fail(f"settings の env 名の定数が無いと ccnavi_chrome.py を読み込めない: {loaded}")
        _, place = self.ask_placement(chrome)
        self.assert_default(chrome, place)

    def test_the_local_process_env_is_not_read(self):
        # B5 手元のプロセスの env は `_environ` が外す（回帰の見張り）
        moved = {name: f"local/{i}" for i, name in enumerate(MOVED_ENV_NAMES)}
        with mock.patch.dict(os.environ, moved):
            chrome, place = self.ask_placement()
        self.assert_default(chrome, place)
        # 判定まで通る操作でも、手元の env は混ざらない。
        request, plain = self.board_request()
        with mock.patch.dict(os.environ, moved):
            answer = json.loads(
                load_chrome().handle(json.dumps(request), os.path.join(self.root, "m2"))
            )
        self.assertEqual(answer, plain)


class RecordWritesTest(CoreHarness):
    """`--record-writes`: この実行で書いたパスの一覧が、git status の変化と一致する。"""

    def place(self, root=None):
        return os.path.join(root or self.root, "logs", "state", "write-and-push")

    def record(self, *args, stdin="", target=None):
        target = target or os.path.join(self.place(), "self", "i0001.t.writes")
        result = self.ccnavi("--record-writes", target, *args, stdin=stdin)
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
        self.propose(
            "i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("wip/research/*",), False)
        )
        self.commit_parent()
        digest = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)["digest"]
        steps = [
            ("user", "agree", "--yes", "i0001,i0001-01-01", "--digest", digest, "--json"),
            ("ticket", "start", "i0001"),
        ]
        for args in steps:
            result, listed = self.record(*args)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(sorted(listed), self.changed(self.parent_tree), args)
            self.commit_parent(" ".join(args))
        tree = self.worktree("i0001-01-01", "i0001")
        result, listed = self.record("ticket", "start", "i0001-01-01")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(listed), self.changed(self.parent_tree))
        self.commit_parent("start 01")
        write(os.path.join(tree, "wip", "research", "summary.md"), "s\n")
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "work")
        result, listed = self.record("ticket", "finish", "i0001-01-01")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(listed), self.changed(self.parent_tree))
        self.assertTrue(listed)

    def test_a_failed_command_still_writes_its_list(self):
        """コマンドが失敗しても一覧は書く（write-and-push が戻すのに使う）。
        終了コードはコマンドのもの。"""
        result, listed = self.record("ticket", "start", "nope")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.place(), "self", "i0001.t.writes")))
        self.assertEqual(listed, [])

    def test_an_unwritable_list_ends_with_one(self):
        target = os.path.join(self.place(), "self", "busy")
        os.makedirs(os.path.join(target, "inside"))
        result, _ = self.record("--agree", "--preview", "--json", target=target)
        self.assertEqual(result.returncode, 1)
        self.assertIn("反映したパスの一覧を", result.stderr)

    def refused(self, target, *extra):
        result = self.ccnavi(*extra, "--record-writes", target, "ticket", "start", "i0001")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("--record-writes の書き出し先は", result.stderr)
        return result

    def test_the_list_goes_only_under_the_default_state_place(self):
        outside = os.path.join(self.root, "wip", "list.txt")
        self.refused(outside)
        self.assertFalse(os.path.exists(outside))
        # `..` で出るパス。
        climbing = os.path.join(self.place(), "..", "..", "..", "wip", "list.txt")
        self.refused(climbing)
        self.assertFalse(os.path.exists(outside))
        # `--state` で置き場を動かしても、書き出し先は動かない。
        moved = os.path.join(self.root, "wip")
        self.refused(os.path.join(moved, "write-and-push", "list.txt"), "--state", moved)
        self.assertFalse(os.path.exists(os.path.join(moved, "write-and-push")))

    @requires_symlink
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

    @requires_symlink
    def test_a_linked_workspace_root_lists_relative_paths(self):
        """ワークスペースルートがリンク越し（macOS の /var → /private/var）でも、相対で書く。"""
        linked = self.root + "-link"
        os.symlink(self.root, linked)
        self.addCleanup(os.remove, linked)
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        digest = json.loads(self.ccnavi("--agree", "--preview", "--json").stdout)["digest"]
        target = os.path.join(linked, "logs", "state", "write-and-push", "self", "l.writes")
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        from tests.inproc import run_ccnavi

        result = run_ccnavi(
            [
                "--root",
                linked,
                "--record-writes",
                target,
                "user",
                "agree",
                "--guard-core-files",
                "disable",
                "--restore-if-deny",
                "disable",
                "--guard-ticket-approval",
                "disable",
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
            # 実行後チェックが範囲の外の変更を脇へ退ける（`gitstate.restore`）。状態の操作では
            # なく、write-and-push の書いたパスの一覧に載せるものでもない。
            ("gitstate", "shutil.move(source, target)"),
        }
        names = ("tickets.approval", "tickets.agree", "tickets.model.via", "tickets.configsync")
        names += ("tickets.ops", "tickets.flow", "tickets.risk", "tickets.core", "tickets.review")
        names += ("tickets.phase", "tickets.model.ticket", "infra.gitstate")
        names += ("tickets.approval_marks", "tickets.approval_checks", "tickets.approval_times")
        names += ("tickets.model.review_host", "tickets.review_decide", "tickets.review_close")
        names += (
            "tickets.model.ticket_model",
            "tickets.model.ticket_ids",
            "tickets.model.ticket_places",
        )
        names += (
            "tickets.model.ticket_fold",
            "tickets.model.ticket_guard",
            "tickets.model.ticket_fields",
        )
        names += ("tickets.agree_candidates", "tickets.agree_digest", "tickets.agree_screen")
        names += ("tickets.model.phase_forms", "tickets.phase_scope")
        names += ("tickets.flow_text", "tickets.flow_shape", "tickets.flow_render")
        names += ("tickets.approval_ops", "tickets.ops_close", "tickets.ops_stop")
        names += (
            "tickets.core_base",
            "tickets.core_approve",
            "tickets.core_review",
        )
        for dotted in names:
            name = dotted.rpartition(".")[2]
            path = os.path.join(ROOT, "src", "ccnavi", *dotted.split(".")) + ".py"
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
