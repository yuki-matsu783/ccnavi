"""判定のコアを Chrome の入口と手元の CLI が同じに使うことの受入テスト。

見るのは 5 つ。

1. 承認は提案の中身を変えない（承認済みチケットは提案のバイト列のまま。CRLF も）
2. 親のワークツリーの HEAD がブランチを指していなければ承認しない
3. Chrome の入口（`go/cmd/ccnavi-chrome`。拡張の WASM と同じ要求を受ける）が同じ入力から、
   手元の CLI が実際に書いたのと同じバイト列を出す（新規・マーカーの消去・改版・フィードバック計画・
   多階層の先行と外れる提案・レビュー済み）。時刻は、Chrome は要求の時刻、CLI は手元の時計で書く
   ので、時刻の欄だけを除いて比べる。
   同じ要求と答えを拡張の試験の見本（`extensions/chrome/ccnavi-approval/test/fixtures/core-scenarios.json`）
   に置き、拡張の試験が WASM でも同じ答えになることを見る
4. Chrome の入口は置き場を既定に固定する。統合先の `.claude/settings.json` の `env` や手元の env に
   置き場の env を入れても既定の置き場を使う
5. 記録層（`--record-writes`）: 各コマンドで書いたパスの一覧が `git status` の変化と一致する

見本の形を変えたら `CCNAVI_CHROME_FIXTURE=1` を付けてこのテストを走らせ、見本を書き直す。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from tests import exe, requires_symlink
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
        """拡張の試験の見本に置く（要求と、Chrome の入口の答え）。"""
        CoreChromeTest.collected[name] = {"request": request, "answer": answer}


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


class DetachedHeadTest(CoreHarness):
    """親のワークツリーの HEAD がブランチを指していなければ承認しない。"""

    def test_a_detached_head_is_not_approved(self):
        # 親のワークツリーの HEAD がブランチを指していなければ、ほかのツリーで代用せずに止まる
        git(self.parent_tree, "checkout", "--quiet", "--detach")
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()
        self.assertNotEqual(self.approve().returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))


# 書いた時刻（`2026-09-29T12:00:00+0900` の形）。
_STAMP_PATTERN = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:[+-]\d{4}|Z)")


def _normalized(changes):
    """経路（`via`・`settled_by`）の欄を除き、書いた時刻を伏せた Changes。

    手元の CLI（board・cli）と Chrome（chrome）で違う所。時刻は、Chrome は要求の `stamp`、CLI は
    手元の時計で書く。
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
            if isinstance(content, str):
                content = row["content"] = _STAMP_PATTERN.sub("<stamp>", content)
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
        # コミットの sha は git の日時で固定する。CLI が書く時刻は手元の時計なので、見本の要求は
        # 書き直すたびに変わる（Chrome が書く時刻は要求の `stamp`）。
        for key in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
            if key in os.environ:
                self.addCleanup(os.environ.__setitem__, key, os.environ[key])
            else:
                self.addCleanup(os.environ.pop, key, None)
            os.environ[key] = "2026-09-29T12:00:00+0900"
        super().setUp()
        # 統合先の互換のマーカー（Chrome は版が違えば書く操作を受けない）。
        write(
            os.path.join(self.root, ".ccnavi", "scripts", "ccnavi-common.sh"),
            f"#!/bin/sh\nCCNAVI_COMPAT={exe.compat()}\n",
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
        """拡張の試験の見本に置く場面。書き直すときだけ記録する。

        見本の答えが今の入口と合うことは `test_the_fixture_answers_are_what_the_entry_says` が見る。
        """
        CoreChromeTest.collected[name] = {"request": request, "answer": answer}
        if not os.environ.get("CCNAVI_CHROME_FIXTURE"):
            return
        if not MACHINE_NEWLINE_IS_LF:
            self.skipTest("機械の改行が CRLF で、見本（LF で作る）を書き直せない")

    def test_the_fixture_answers_are_what_the_entry_says(self):
        """見本の要求を Chrome の入口に渡すと、見本の答えが返る。

        拡張の試験は WASM で同じことを見る。

        手元の CLI は時刻を手元の時計で書くので、見本の要求は書き直すたびに時刻とコミットが変わる。
        見本が今の入口と合っていることは、要求を渡し直して確かめる。
        """
        with open(SCENARIOS, encoding="utf-8") as f:
            held = json.load(f)
        for name, scene in sorted(held.items()):
            with self.subTest(name=name):
                answer = json.loads(
                    load_chrome().handle(
                        json.dumps(scene["request"]), os.path.join(self.root, "replay", name)
                    )
                )
                self.assertEqual(
                    scene["answer"],
                    answer,
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
            os.path.join(self.root, ".claude", "worktrees", "i0001-01-01"),
        )
        answer = self.same_as_cli("feedback-plan", "i0001")
        self.assertIn("  i0001 のフィードバック計画を改版した", answer["lines"])

    def test_predecessors_across_families(self):
        """多階層の先行（閉包）。通る子と、先行が閉じていないので外れる子（文面まで同じ）。"""
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

    def refused_by_changes_requested(self, fixture, result, compare):
        """変更要求のある結果を、Chrome の入口と手元の CLI の両方に渡す。

        答えは（Chrome の答え, CLI の結果）。
        """
        changes_requested = {**result, "reviews": [{"state": "CHANGES_REQUESTED", "url": "r/1"}]}
        refused = self.ask_chrome(self.confirm_request(changes_requested, compare))
        write(fixture, json.dumps(changes_requested))
        return refused, self.confirm(fixture, 1)

    def test_a_refusal_has_the_same_text_as_the_cli(self):
        fixture = self.reviewed_phase_one()
        self.commit_parent("requested")
        result = {
            "host": "fixture",
            "mr": {"number": 7, "url": "u/7"},
            "threads": [],
            "reviews": [],
        }
        refused, local = self.refused_by_changes_requested(fixture, result, self.host_compare())
        self.assertEqual(refused["problems"], local.stderr.splitlines())
        self.assertTrue(refused["problems"][0].startswith("ccnavi: [CORE_CONFIRM_REVIEW_OPEN] "))

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
        # 依頼の後に親の先頭が動いていれば、入口が compare の一覧を求める
        # （比べる相手は入口が出す）
        need = self.ask_chrome(
            self.chrome_request(
                "confirm", "i0001", heads={"i0001": compare["head"]}, phase=1, result=result
            )
        )
        self.assertEqual(need["need_compare"], {"base": compare["base"], "head": compare["head"]})
        self.assertIsNone(need["changes"])
        # 変更要求があれば通さない。名指しする相手は手元の CLI の標準エラーと同じ。
        refused, local = self.refused_by_changes_requested(fixture, result, compare)
        self.assertEqual(local.returncode, 1)
        self.assertEqual(refused["problems"][1:], local.stderr.splitlines()[1:])
        self.assertIsNone(refused["changes"])
        # 分岐の材料は文面ではなくキー（文面にも同じコードが載る）
        self.assertEqual(refused["code"], "CORE_CONFIRM_REVIEW_OPEN")
        self.assertIn("[CORE_CONFIRM_REVIEW_OPEN] ", refused["problems"][0])
        write(fixture, json.dumps(result))

        request = self.confirm_request(result, compare)
        answer = self.ask_chrome(request)
        self.assertEqual(answer["problems"], [])
        self.assertIsNone(answer["code"])
        before = self.disk()
        local = self.confirm(fixture, 1)
        self.assertEqual(local.returncode, 0, local.stdout + local.stderr)
        self.assertIsNone(answer["stopped"])
        # 手元の CLI だけが、エージェント向けの `次:` の行を足す。
        # Chrome を押すのはユーザなので返さない。
        cli_lines = [line for line in local.stdout.splitlines() if not line.startswith("次: ")]
        self.assertEqual(_flat(answer["lines"]), cli_lines)
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
MOVED_ENV_NAMES = ("CCNAVI_TICKETS_PROPOSAL", "CCNAVI_TICKETS_APPROVED", "CCNAVI_PROJECT_HOME")

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
    """既定の置き場から組んだ、置き場の答えの欄。"""
    tickets = "wip/proposals"
    approved = ".ccnavi/approved"
    layer = ".ccnavi/config/project"
    done = f"{approved}/done"
    # 文面ファイルは拒否の文を手元の CLI と同じにするため、統合先とワークスペースから読む。
    messages = ".ccnavi/messages"
    return {
        "tickets": tickets,
        "approved": approved,
        "layer_dir": layer,
        "integration_paths": sorted({done, chrome.COMMON_LAYER, layer, messages}),
        "integration_files": [chrome.SETTINGS_FILE, chrome.COMPAT_FILE],
        "branch_paths": [approved, tickets],
        "workspace_paths": sorted({chrome.COMMON_LAYER, layer, messages}),
        "workspace_files": [chrome.SETTINGS_FILE, chrome.COMPAT_FILE],
        "project_paths": sorted({done, layer}),
    }


def _settings_text(env):
    """統合先の `.claude/settings.json` の中身（置き場の env と、置き場に関わらない env）。"""
    return json.dumps({"env": {"CCNAVI_MODE": "enforce", **env}}, ensure_ascii=False)


class ChromePlacementTest(CoreHarness):
    """Chrome の入口は置き場を既定に固定し、置き場を動かす env を読まない。

    置き場は入口（`handle`）を通して確かめる。
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

    def test_the_local_process_env_is_not_read(self):
        # B5 手元のプロセスの env は読まない（回帰の見張り）
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


if __name__ == "__main__":
    unittest.main()
