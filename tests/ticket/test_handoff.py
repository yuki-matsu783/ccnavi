"""保護対象の下書きの取り込み（`ccnavi user handoff`。src/ccnavi/tickets/handoff.py）。

見るのは次のとおり。

1. 取り込み先の表（許可リスト）: 表にヒットする下書きだけが対象になり、
ヒットしないものは対象外として
   件数だけを数える。パスは全要素を NFC と fold にそろえてから適用する。表と docs が食い違わない
2. 断る形: リンク（途中も）・ふつうのファイルでない・ハードリンク・`.git` で始まる要素・
   同じ取り込み先に落ちる 2 本・バイナリ・新規作成を許さない種類・引数の絶対パスと `..`
3. `--expect`: 定義どおりの値。下書き・取り込み先・`.orig` のどれが変わっても値が変わり、
   違えば何も書かない
4. `conflict`: `.orig` と今の取り込み先が違えば、選んだ対象のどれも書かない
5. 差分の見え方: CRLF・BOM・末尾の改行・ゼロ幅の文字を見える形にする。見えない違いは断る
6. 書き込み: 一時ファイル → fsync → 差し替え。書く直前の確かめ直し。途中で失敗したら元に戻す。
   下書きと `.orig` は、取り込んだものと同じときだけ消す
7. 経路: 書くにはターミナルか `--via board`（`--expect` と `--json` が要る）。
`defense` はボードから断る
8. 中身の検査: レイヤーの設定は `--lint` と同じ関数で、同じ回の対象を取り込んだあとの状態で見る。
   テンプレートの検査、`defense` の `.sh` と `settings*.json`
9. `ready` の止め: 取り込んでいない下書きが残っていれば止める
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import textwrap
import unicodedata
import unittest
from unittest import mock

from ccnavi.infra import fsio, settings
from ccnavi.tickets import handoff, handoff_names, review
from tests import ROOT, can_case_sensitive, common_path, config_path, requires_symlink
from tests.inproc import run_ccnavi
from tests.ticket.test_ticket import git, write
from tests.ticketsample import PHASES, RULES

DOCS = ".ccnavi/docs/guide.md"
SCRIPT = ".ccnavi/scripts/ccnavi-x.sh"
SETTINGS = ".claude/settings.json"

TEMPLATE = textwrap.dedent(
    """\
    name: feature
    description: 機能を設計から実装まで進める
    when: 新しい機能を足すとき
    plan:
      - design
      - {type: implement, after: [1]}
    """
)


def put(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def slurp(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class HandoffCase(unittest.TestCase):
    """ワークスペースルートの置き場に下書きを置いて、取り込みを動かす道具。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-handoff-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        write(common_path(self.root, "rules"), json.dumps(RULES))
        write(common_path(self.root, "phases"), PHASES)
        write(self.target(DOCS), "# 手引き\n\n古い文\n")
        write(self.target(SCRIPT), "#!/bin/sh\necho old\n")
        write(self.target(SETTINGS), '{"hooks": {}}\n')

    # ---- 置き場

    def target(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))

    def draft_path(self, rel: str) -> str:
        return os.path.join(self.root, "wip", "handoff", *handoff_names.to_draft(rel).split("/"))

    def stage(self, rel: str, data: bytes | None = None) -> str:
        """`ccnavi-stage.sh` と同じく、元を下書きと `.orig` にコピーし、下書きを `data` にする。"""
        current = slurp(self.target(rel)) if os.path.exists(self.target(rel)) else None
        path = self.draft_path(rel)
        if current is not None:
            put(path + handoff.ORIG_SUFFIX, current)
        put(path, current if data is None else data)
        return path

    # ---- 起動

    def run_handoff(self, *args, guard="disable", env=None):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment[settings.GUARD_TICKET_APPROVAL_ENV] = guard
        environment.update(env or {})
        # ボードの経路（`--via board`）は拡張の子プロセスが呼ぶ形なので、印も board にする。
        entry = "board" if "board" in args else "sh"
        return run_ccnavi(
            ["--root", self.root, "--entry", entry, "user", "handoff", *args],
            env=environment,
            cwd=ROOT,
        )

    def view(self, *paths) -> dict:
        result = self.run_handoff(*paths, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def entry(self, answer: dict, rel: str) -> dict:
        found = [e for e in answer["entries"] if e["path"] == rel]
        self.assertEqual(len(found), 1, answer)
        return found[0]

    def take(self, *paths, expect=None, extra=()):
        if expect is None:
            expect = self.view(*paths)["expect"]
        return self.run_handoff(*paths, "--yes", "--expect", expect, *extra, "--json")

    def plan(self, *paths):
        conf, _ = settings.load(self.root)
        return handoff.prepare(conf, self.root, list(paths) or None)


class AllowListTest(HandoffCase):
    def test_each_row_of_the_table_is_a_kind(self):
        for rel, kind in (
            (".ccnavi/config/common/templates/a.yml", handoff.KIND_TEMPLATE),
            (".ccnavi/config/project/templates/a.yml", handoff.KIND_TEMPLATE),
            ("projects/lib/.ccnavi/config/project/templates/a.yml", handoff.KIND_TEMPLATE),
            (".ccnavi/config/common/rules.yml", handoff.KIND_LAYER_CONFIG),
            (".ccnavi/config/project/phases.yml", handoff.KIND_LAYER_CONFIG),
            ("projects/lib/.ccnavi/config/project/risks.yml", handoff.KIND_LAYER_CONFIG),
            (".ccnavi/docs/a/b.md", handoff.KIND_DOCS),
            (".ccnavi/CCNAVI.md", handoff.KIND_DOCS),
            # スキルの frontmatter は権限や hook を持てるので、防御としてターミナルでだけ取り込む。
            (".claude/skills/x/SKILL.md", handoff.KIND_DEFENSE),
            (".ccnavi/scripts/ccnavi-git.sh", handoff.KIND_DEFENSE),
            (".claude/hooks/lint-py.sh", handoff.KIND_DEFENSE),
            (".claude/settings.json", handoff.KIND_DEFENSE),
            (".claude/settings.local.json", handoff.KIND_DEFENSE),
        ):
            with self.subTest(rel=rel):
                found = handoff.kind_of(handoff.key_of(rel))
                self.assertIsNotNone(found)
                self.assertEqual(found.id, kind)

    def test_paths_off_the_table_are_not_kinds(self):
        for rel in (
            "learnings/i0001.md",
            "inbox/x.json",
            "repro.sh",
            ".ccnavi/bin/ccnavi",
            ".ccnavi/approved/doing/i0001.md",
            ".ccnavi/config/common/rule-expectations.yml",
            ".ccnavi/config/project/templates/sub/a.yml",
            "src/x.py",
            ".git/config",
            ".gitmodules",
        ):
            with self.subTest(rel=rel):
                self.assertIsNone(handoff.kind_of(handoff.key_of(rel)))

    def test_case_and_nfd_are_folded_before_matching(self):
        self.assertEqual(handoff.kind_of(handoff.key_of(".CCNAVI/Docs/Guide.MD")).id, "docs")
        nfd = unicodedata.normalize("NFD", ".ccnavi/docs/が.md")
        self.assertEqual(handoff.key_of(nfd), handoff.key_of(".ccnavi/docs/が.md"))

    def test_only_the_board_refuses_defense(self):
        self.assertEqual(
            {k.id: k.board for k in handoff.KINDS},
            {"template": True, "layer-config": True, "docs": True, "defense": False},
        )
        self.assertEqual(
            {k.id: k.new for k in handoff.KINDS},
            {"template": True, "layer-config": False, "docs": False, "defense": False},
        )

    def test_drafts_off_the_table_are_counted_and_not_errors(self):
        write(self.draft_path("learnings/i0001.md"), "学び\n")
        write(self.draft_path("repro.sh"), "echo\n")
        self.stage(DOCS, b"# \xe6\x89\x8b\xe5\xbc\x95\xe3\x81\x8d\n\n\xe6\x96\xb0\n")
        answer = self.view()
        self.assertEqual(answer["ignored"], 2)
        self.assertEqual(
            answer["ignored_paths"],
            [
                {"path": "repro.sh", "why": "not-allowed"},
                {"path": "learnings/i0001.md", "why": "not-allowed"},
            ],
        )
        self.assertEqual(answer["ignored"], len(answer["ignored_paths"]))
        self.assertEqual([e["path"] for e in answer["entries"]], [DOCS])
        self.assertTrue(answer["ok"])

    @requires_symlink
    def test_a_linked_directory_is_ignored_with_its_reason(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        os.makedirs(os.path.dirname(self.draft_path("x")), exist_ok=True)
        os.symlink(outside, self.draft_path("linked"))
        answer = self.view()
        self.assertEqual(answer["ignored"], 1)
        self.assertEqual(answer["ignored_paths"], [{"path": "linked", "why": "linked-dir"}])

    def test_the_orig_file_is_not_a_draft(self):
        self.stage(SCRIPT, b"#!/bin/sh\necho new\n")
        answer = self.view()
        self.assertEqual([e["path"] for e in answer["entries"]], [SCRIPT])
        self.assertEqual(answer["ignored"], 0)

    def test_the_table_and_the_design_document_agree(self):
        # 表を直したら、設計の文書の表も直す。文書に無い行を表に足さない。
        with open(
            os.path.join(ROOT, "docs", "21_design", "tickets", "handoff.md"), encoding="utf-8"
        ) as f:
            text = f.read()
        for kind in handoff.KINDS:
            for shown in kind.shown:
                with self.subTest(shown=shown):
                    self.assertIn(f"`{shown}`", text)


class RefuseTest(HandoffCase):
    @requires_symlink
    def test_a_linked_draft_is_rejected(self):
        outside = write(os.path.join(self.root, "outside.md"), "x\n")
        os.makedirs(os.path.dirname(self.draft_path(DOCS)), exist_ok=True)
        os.symlink(outside, self.draft_path(DOCS))
        row = self.entry(self.view(), DOCS)
        self.assertEqual(row["state"], "rejected")
        self.assertIn("リンク", " ".join(row["errors"]))

    @requires_symlink
    def test_a_linked_directory_on_the_way_is_not_read(self):
        outside = tempfile.mkdtemp(prefix="ccnavi-outside-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        write(os.path.join(outside, "docs", "guide.md"), "evil\n")
        os.makedirs(os.path.join(self.root, "wip", "handoff"))
        os.symlink(outside, os.path.join(self.root, "wip", "handoff", ".ccnavi"))
        answer = self.view()
        self.assertEqual(answer["entries"], [])
        result = self.take(DOCS, expect="x")
        self.assertEqual(result.returncode, 1)
        self.assertIn("無いか", json.loads(result.stdout)["error"])
        self.assertEqual(slurp(self.target(DOCS)), "# 手引き\n\n古い文\n".encode())

    @requires_symlink
    def test_a_linked_target_is_rejected(self):
        real = write(os.path.join(self.root, "real.md"), "real\n")
        os.remove(self.target(DOCS))
        os.symlink(real, self.target(DOCS))
        write(self.draft_path(DOCS), "new\n")
        row = self.entry(self.view(), DOCS)
        self.assertEqual(row["state"], "rejected")

    @requires_symlink
    def test_a_linked_handoff_place_is_an_error(self):
        outside = tempfile.mkdtemp(prefix="ccnavi-outside-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "wip"))
        os.symlink(outside, os.path.join(self.root, "wip", "handoff"))
        result = self.run_handoff("--json")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_a_hard_linked_draft_is_rejected(self):
        other = write(os.path.join(self.root, "other.md"), "x\n")
        os.makedirs(os.path.dirname(self.draft_path(DOCS)), exist_ok=True)
        try:
            os.link(other, self.draft_path(DOCS))
        except OSError:
            self.skipTest("ハードリンクを作れない")
        row = self.entry(self.view(), DOCS)
        self.assertEqual(row["state"], "rejected")
        self.assertIn("ハードリンク", " ".join(row["errors"]))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "名前付きパイプを作れない")
    def test_a_draft_that_is_not_a_regular_file_is_rejected(self):
        os.makedirs(os.path.dirname(self.draft_path(DOCS)), exist_ok=True)
        os.mkfifo(self.draft_path(DOCS))
        row = self.entry(self.view(), DOCS)
        self.assertEqual(row["state"], "rejected")

    def test_a_part_starting_with_dot_git_is_rejected(self):
        rel = ".ccnavi/docs/.github/x.md"
        write(self.target(rel), "a\n")
        write(self.draft_path(rel), "b\n")
        row = self.entry(self.view(), rel)
        self.assertEqual(row["state"], "rejected")
        self.assertIn(".git", " ".join(row["errors"]))

    @unittest.skipUnless(can_case_sensitive(), "大文字小文字だけ違う名前を並べられない")
    def test_two_drafts_for_the_same_target_are_both_rejected(self):
        write(self.draft_path(DOCS), "a\n")
        write(self.draft_path(".ccnavi/docs/GUIDE.md"), "b\n")
        answer = self.view()
        self.assertEqual(len(answer["entries"]), 2)
        for row in answer["entries"]:
            self.assertEqual(row["state"], "rejected")
            self.assertIn("複数", " ".join(row["errors"]))

    def test_a_binary_draft_is_rejected(self):
        put(self.draft_path(DOCS), b"a\x00b\n")
        row = self.entry(self.view(), DOCS)
        self.assertEqual(row["state"], "rejected")
        self.assertIn("バイナリ", " ".join(row["errors"]))

    def test_a_draft_that_is_not_utf8_is_rejected(self):
        put(self.draft_path(DOCS), b"\xff\xfe\n")
        row = self.entry(self.view(), DOCS)
        self.assertIn("UTF-8", " ".join(row["errors"]))

    def test_kinds_that_cannot_be_created_need_the_target(self):
        rel = ".ccnavi/docs/new.md"
        write(self.draft_path(rel), "new\n")
        row = self.entry(self.view(), rel)
        self.assertEqual(row["state"], "rejected")
        self.assertIn("新しく作れない", " ".join(row["errors"]))

    def test_absolute_and_parent_paths_are_refused(self):
        self.stage(DOCS, b"new\n")
        for arg in (self.target(DOCS), "../x.md", ".ccnavi/../../x.md", ""):
            with self.subTest(arg=arg):
                result = self.run_handoff(arg, "--json")
                self.assertEqual(result.returncode, 1)
                self.assertFalse(json.loads(result.stdout)["ok"])

    def test_a_path_with_the_handoff_prefix_and_other_case_points_to_the_same_draft(self):
        self.stage(DOCS, b"new\n")
        for arg in (DOCS, f"wip/handoff/{DOCS}", ".CCNAVI/docs/Guide.md"):
            with self.subTest(arg=arg):
                answer = self.view(arg)
                self.assertTrue(self.entry(answer, DOCS)["selected"])


class ExpectTest(HandoffCase):
    def test_the_hash_follows_the_definition(self):
        self.stage(DOCS, b"new\n")
        self.stage(SCRIPT, b"#!/bin/sh\necho new\n")
        answer = self.view()

        def framed(text: str) -> bytes:
            data = text.encode()
            return str(len(data)).encode() + b":" + data

        def sha(data):
            return hashlib.sha256(data).hexdigest() if data is not None else "-"

        records = []
        for rel, kind in sorted(((DOCS, "docs"), (SCRIPT, "defense")), key=lambda r: r[0]):
            record = b"".join(
                framed(v)
                for v in (
                    rel.casefold(),
                    kind,
                    sha(slurp(self.draft_path(rel))),
                    sha(slurp(self.target(rel))),
                    sha(slurp(self.draft_path(rel) + ".orig")),
                )
            )
            records.append(record)
            self.assertEqual(
                self.entry(answer, rel)["entry_sha"], hashlib.sha256(record).hexdigest()
            )
        self.assertEqual(answer["expect"], hashlib.sha256(b"".join(records)).hexdigest())

    def test_the_hash_changes_with_any_of_the_three(self):
        self.stage(DOCS, b"new\n")
        first = self.view()["expect"]
        put(self.draft_path(DOCS) + ".orig", slurp(self.target(DOCS)) + b"x")
        put(self.target(DOCS), slurp(self.target(DOCS)) + b"x")
        second = self.view()["expect"]
        self.assertNotEqual(first, second)
        put(self.draft_path(DOCS), b"newer\n")
        self.assertNotEqual(second, self.view()["expect"])

    def test_a_missing_target_and_orig_are_a_dash(self):
        rel = ".ccnavi/config/project/templates/feature.yml"
        write(self.draft_path(rel), TEMPLATE)
        entry = self.plan().entries[0]
        self.assertIn(b"1:-1:-", entry.record())

    def test_a_wrong_expect_writes_nothing(self):
        self.stage(DOCS, b"new\n")
        result = self.take(expect="0" * 64)
        self.assertEqual(result.returncode, 1)
        answer = json.loads(result.stdout)
        self.assertEqual(answer["version"], 1)
        self.assertFalse(answer["ok"])
        self.assertIn("--expect", answer["error"])
        self.assertEqual(slurp(self.target(DOCS)), "# 手引き\n\n古い文\n".encode())
        self.assertTrue(os.path.exists(self.draft_path(DOCS)))

    def test_yes_needs_expect(self):
        self.stage(DOCS, b"new\n")
        result = self.run_handoff("--yes", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--expect", json.loads(result.stdout)["error"])

    def test_the_expect_covers_only_the_chosen(self):
        self.stage(DOCS, b"new\n")
        self.stage(SCRIPT, b"#!/bin/sh\necho new\n")
        both = self.view()["expect"]
        one = self.view(DOCS)
        self.assertNotEqual(both, one["expect"])
        self.assertEqual([e["selected"] for e in one["entries"]], [True, False])
        result = self.take(DOCS, expect=one["expect"])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(slurp(self.target(DOCS)), b"new\n")
        self.assertEqual(slurp(self.target(SCRIPT)), b"#!/bin/sh\necho old\n")


class ConflictTest(HandoffCase):
    def test_a_target_changed_after_staging_is_a_conflict_and_nothing_is_written(self):
        self.stage(DOCS, b"new\n")
        self.stage(SCRIPT, b"#!/bin/sh\necho new\n")
        write(self.target(DOCS), "# 手引き\n\nほかの人の変更\n")
        answer = self.view()
        self.assertEqual(self.entry(answer, DOCS)["state"], "conflict")
        self.assertEqual(self.entry(answer, SCRIPT)["state"], "changed")
        self.assertFalse(answer["ok"])
        result = self.take(expect=answer["expect"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("conflict", json.loads(result.stdout)["error"])
        self.assertEqual(slurp(self.target(SCRIPT)), b"#!/bin/sh\necho old\n")

    def test_without_an_orig_there_is_no_conflict_check(self):
        write(self.draft_path(DOCS), "new\n")
        self.assertEqual(self.entry(self.view(), DOCS)["state"], "changed")

    def test_the_other_drafts_can_be_chosen_instead(self):
        self.stage(DOCS, b"new\n")
        self.stage(SCRIPT, b"#!/bin/sh\necho new\n")
        write(self.target(DOCS), "changed\n")
        self.assertTrue(self.view(SCRIPT)["ok"])
        self.assertEqual(self.take(SCRIPT).returncode, 0)

    def test_an_unchanged_draft_is_not_chosen_by_default(self):
        self.stage(DOCS)
        answer = self.view()
        row = self.entry(answer, DOCS)
        self.assertEqual(row["state"], "unchanged")
        self.assertFalse(row["selected"])
        self.assertEqual(row["diff"], "")


class DiffTest(HandoffCase):
    def diff(self, old: bytes, new: bytes) -> dict:
        put(self.target(DOCS), old)
        put(self.draft_path(DOCS), new)
        return self.entry(self.view(), DOCS)

    def test_crlf_is_shown(self):
        row = self.diff(b"a\nb\n", b"a\r\nb\r\n")
        self.assertEqual(row["state"], "changed")
        self.assertIn("+a\\u000d", row["diff"])

    def test_a_bom_is_shown(self):
        row = self.diff(b"a\n", b"\xef\xbb\xbfa\n")
        self.assertIn("\\ufeff", row["diff"])

    def test_the_last_newline_is_shown(self):
        row = self.diff(b"a\n", b"a")
        self.assertIn(handoff.NO_EOL, row["diff"])

    def test_zero_width_and_control_characters_are_shown(self):
        row = self.diff(b"ab\n", "a​b\x1b\n".encode())
        self.assertIn("\\u200b", row["diff"])
        self.assertIn("\\u001b", row["diff"])

    def test_nfd_is_shown(self):
        row = self.diff("が\n".encode(), unicodedata.normalize("NFD", "が\n").encode())
        self.assertIn("\\u3099", row["diff"])

    def test_bytes_that_differ_without_a_visible_difference_are_refused(self):
        row = self.diff(b"a\\u000d\n", b"a\r\n")
        self.assertEqual(row["state"], "rejected")
        self.assertIn("見えない", " ".join(row["errors"]))

    def test_a_new_file_is_shown_whole(self):
        rel = ".ccnavi/config/project/templates/feature.yml"
        write(self.draft_path(rel), TEMPLATE)
        row = self.entry(self.view(), rel)
        self.assertEqual(row["state"], "new")
        self.assertIn("--- /dev/null", row["diff"])
        self.assertIn("+name: feature", row["diff"])

    def test_the_text_answer_shows_the_diff_and_the_command(self):
        self.stage(DOCS, "# 手引き\n\n新しい文\n".encode())
        result = self.run_handoff()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-古い文", result.stdout)
        self.assertIn("+新しい文", result.stdout)
        expect = self.view()["expect"]
        self.assertIn(f"ccnavi-user-handoff.sh --yes --expect {expect}", result.stdout)
        self.assertIn("何も反映していません", result.stdout)
        self.assertNotIn("次:", result.stdout)


class WriteTest(HandoffCase):
    def test_yes_writes_and_cleans_the_draft_and_the_orig(self):
        draft = self.stage(DOCS, b"new\n")
        result = self.take()
        self.assertEqual(result.returncode, 0, result.stdout)
        answer = json.loads(result.stdout)
        self.assertEqual(
            answer,
            {
                "version": 1,
                "ok": True,
                "applied": [{"path": DOCS, "kind": "docs"}],
                "removed_drafts": [f"wip/handoff/{DOCS}", f"wip/handoff/{DOCS}.orig"],
                "kept_drafts": [],
            },
        )
        self.assertEqual(slurp(self.target(DOCS)), b"new\n")
        self.assertFalse(os.path.lexists(draft))
        self.assertFalse(os.path.lexists(draft + ".orig"))

    def test_a_new_template_is_written(self):
        rel = ".ccnavi/config/project/templates/feature.yml"
        write(self.draft_path(rel), TEMPLATE)
        result = self.take()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(slurp(self.target(rel)), TEMPLATE.encode())
        listed = run_ccnavi(["--root", self.root, "--templates", "--json"], cwd=ROOT)
        names = [t["name"] for t in json.loads(listed.stdout)["templates"]]
        self.assertEqual(names, ["feature"])

    def test_the_write_goes_through_a_synced_temp_file(self):
        self.stage(DOCS, b"new\n")
        synced = []
        real = fsio._sync_file
        with mock.patch.object(
            fsio, "_sync_file", side_effect=lambda fd: (synced.append(fd), real(fd))
        ):
            self.assertEqual(self.take().returncode, 0)
        self.assertTrue(synced)
        leftovers = [
            n for n in os.listdir(os.path.dirname(self.target(DOCS))) if n.endswith(".part")
        ]
        self.assertEqual(leftovers, [])

    def test_a_draft_rewritten_after_the_write_is_kept(self):
        draft = self.stage(DOCS, b"new\n")
        real = handoff._write_one

        def write_then_edit(root, entry):
            said = real(root, entry)
            put(draft, b"agent kept editing\n")
            return said

        with mock.patch.object(handoff, "_write_one", side_effect=write_then_edit):
            result = self.take()
        answer = json.loads(result.stdout)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["removed_drafts"], [])
        self.assertEqual(answer["kept_drafts"][0]["path"], f"wip/handoff/{DOCS}")
        self.assertEqual(slurp(draft), b"agent kept editing\n")

    def test_a_failure_in_the_middle_puts_back_what_was_written(self):
        self.stage(DOCS, b"new\n")
        self.stage(SCRIPT, b"#!/bin/sh\necho new\n")
        real = fsio.write_bytes_atomic

        def fail_on_script(path, content):
            if path.endswith("ccnavi-x.sh") and content == b"#!/bin/sh\necho new\n":
                return "disk full"
            return real(path, content)

        with mock.patch.object(fsio, "write_bytes_atomic", side_effect=fail_on_script):
            result = self.take()
        self.assertEqual(result.returncode, 1)
        answer = json.loads(result.stdout)
        self.assertIn("元に戻しました", answer["error"])
        self.assertEqual(slurp(self.target(DOCS)), "# 手引き\n\n古い文\n".encode())
        self.assertEqual(slurp(self.target(SCRIPT)), b"#!/bin/sh\necho old\n")
        self.assertTrue(os.path.exists(self.draft_path(DOCS)))

    def test_what_could_not_be_put_back_is_named(self):
        self.stage(DOCS, b"new\n")
        self.stage(SCRIPT, b"#!/bin/sh\necho new\n")
        real = fsio.write_bytes_atomic

        def fail(path, content):
            if path.endswith("ccnavi-x.sh") or content != b"new\n":
                return "disk full"
            return real(path, content)

        with mock.patch.object(fsio, "write_bytes_atomic", side_effect=fail):
            result = self.take()
        error = json.loads(result.stdout)["error"]
        self.assertIn("元に戻せなかった", error)
        self.assertIn(DOCS, error)

    def test_the_target_is_checked_again_just_before_writing(self):
        self.stage(DOCS, b"new\n")
        conf, _ = settings.load(self.root)
        plan = handoff.prepare(conf, self.root)
        put(self.target(DOCS), b"someone else\n")
        done = handoff.apply(plan, plan.expect)
        self.assertFalse(done.ok)
        self.assertIn("変わった", done.error)
        self.assertEqual(slurp(self.target(DOCS)), b"someone else\n")

    @requires_symlink
    def test_a_link_put_on_the_way_before_writing_is_refused(self):
        rel = ".ccnavi/docs/sub/page.md"
        write(self.target(rel), "old\n")
        write(self.draft_path(rel), "new\n")
        conf, _ = settings.load(self.root)
        plan = handoff.prepare(conf, self.root)
        outside = tempfile.mkdtemp(prefix="ccnavi-outside-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        write(os.path.join(outside, "page.md"), "old\n")
        shutil.rmtree(os.path.dirname(self.target(rel)))
        os.symlink(outside, os.path.dirname(self.target(rel)))
        done = handoff.apply(plan, plan.expect)
        self.assertFalse(done.ok)
        self.assertEqual(slurp(os.path.join(outside, "page.md")), b"old\n")

    def test_the_target_keeps_its_spelling(self):
        write(self.draft_path(".ccnavi/docs/GUIDE.md"), "new\n")
        if not can_case_sensitive():
            self.skipTest("大文字小文字を区別しないファイルシステムでは同じ名前になる")
        self.assertEqual(self.take().returncode, 0)
        self.assertEqual(slurp(self.target(DOCS)), b"new\n")
        self.assertFalse(os.path.exists(self.target(".ccnavi/docs/GUIDE.md")))


class RouteTest(HandoffCase):
    def test_writing_needs_a_terminal(self):
        self.stage(DOCS, b"new\n")
        expect = self.view()["expect"]
        result = self.run_handoff("--yes", "--expect", expect, guard="enable")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ターミナル", result.stderr)
        self.assertEqual(slurp(self.target(DOCS)), "# 手引き\n\n古い文\n".encode())

    def test_viewing_does_not_need_a_terminal(self):
        self.stage(DOCS, b"new\n")
        self.assertEqual(self.run_handoff("--json", guard="enable").returncode, 0)

    def test_the_board_route_skips_the_terminal(self):
        self.stage(DOCS, b"new\n")
        expect = self.view()["expect"]
        result = self.run_handoff(
            "--yes", "--expect", expect, "--via", "board", "--json", guard="enable"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(slurp(self.target(DOCS)), b"new\n")

    def test_the_board_route_refuses_defense(self):
        self.stage(SCRIPT, b"#!/bin/sh\necho new\n")
        expect = self.view()["expect"]
        result = self.run_handoff(
            "--yes", "--expect", expect, "--via", "board", "--json", guard="enable"
        )
        self.assertEqual(result.returncode, 1)
        answer = json.loads(result.stdout)
        self.assertFalse(answer["ok"])
        self.assertIn("defense", answer["error"])
        self.assertIn("ターミナル", answer["error"])
        self.assertEqual(slurp(self.target(SCRIPT)), b"#!/bin/sh\necho old\n")

    def test_the_board_route_needs_expect_and_json(self):
        self.stage(DOCS, b"new\n")
        for args in (
            ("--yes", "--via", "board", "--json"),
            ("--yes", "--expect", "x", "--via", "board"),
            ("--expect", "x", "--via", "board", "--json"),
            ("--yes", "--expect", "x", "--via", "terminal", "--json"),
        ):
            with self.subTest(args=args):
                result = self.run_handoff(*args, guard="enable")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(slurp(self.target(DOCS)), "# 手引き\n\n古い文\n".encode())

    def test_other_options_are_refused(self):
        for args in (("--lint",), ("--docs",), ("--yes", "i1"), ("--templates",)):
            with self.subTest(args=args):
                result = self.run_handoff(*args)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_the_view_json_has_the_agreed_shape(self):
        self.stage(DOCS, b"new\n")
        answer = self.view()
        self.assertEqual(sorted(answer), ["entries", "expect", "ignored", "ignored_paths", "ok", "tree", "version"])
        self.assertEqual(answer["version"], 1)
        self.assertEqual(answer["tree"], self.root)
        self.assertEqual(
            sorted(answer["entries"][0]),
            sorted(
                [
                    "path",
                    "draft",
                    "kind",
                    "state",
                    "board",
                    "selected",
                    "errors",
                    "warnings",
                    "diff",
                    "entry_sha",
                ]
            ),
        )

    def test_the_view_exits_zero_even_with_errors(self):
        put(self.draft_path(DOCS), b"a\x00\n")
        result = self.run_handoff("--json")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_outside_the_user_entry_it_is_refused(self):
        result = run_ccnavi(["--root", self.root, "--handoff"], cwd=ROOT)
        self.assertEqual(result.returncode, 2)
        self.assertIn("ccnavi-user-handoff.sh", result.stderr)


class BrokenRulesRepairTest(HandoffCase):
    """共通の rules.yml が読めない間の修復。

    エージェントは直接直せず（`builtin:guard-ccnavi-config` が Write / Edit を止める）、
    `wip/handoff/` に下書きを置いて、ユーザがターミナルの `ccnavi user handoff` で取り込む。
    今の中身が読めない rules.yml の取り込みは、確かめられないのでターミナルに限る。"""

    RULES_REL = ".ccnavi/config/common/rules.yml"

    def setUp(self):
        super().setUp()
        self.broken = b"deny:\n  - id: [unclosed\n<<<<<<< HEAD\n"
        write(common_path(self.root, "rules"), self.broken.decode())
        self.fixed = json.dumps(RULES).encode()
        self.stage(self.RULES_REL, self.fixed)

    def test_the_draft_is_listed_and_is_terminal_only(self):
        row = self.entry(self.view(), self.RULES_REL)
        self.assertEqual(row["kind"], "layer-config")
        self.assertFalse(row["board"], row)

    def test_the_board_refuses_it(self):
        expect = self.view()["expect"]
        result = self.run_handoff(
            "--yes", "--expect", expect, "--via", "board", "--json", guard="enable"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ターミナル", json.loads(result.stdout)["error"])
        self.assertEqual(slurp(self.target(self.RULES_REL)), self.broken)

    def test_the_terminal_takes_it(self):
        result = self.take()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(slurp(self.target(self.RULES_REL)), self.fixed)
        self.assertFalse(os.path.exists(self.draft_path(self.RULES_REL)))


class BoardDefenseRulesTest(HandoffCase):
    """`rules.yml` の取り込みのうち、防御の記述（`guard-*` と今ある deny）を動かすものは
    ターミナルに限る。"""

    RULES_REL = ".ccnavi/config/common/rules.yml"

    def setUp(self):
        super().setUp()
        self.rules = {
            **RULES,
            "deny": [*RULES["deny"], {**RULES["deny"][0], "id": "keep-out", "glob": "*/x/*"}],
        }
        write(common_path(self.root, "rules"), json.dumps(self.rules))

    def board(self, rules: dict) -> tuple[dict, object]:
        self.stage(self.RULES_REL, json.dumps(rules).encode())
        row = self.entry(self.view(), self.RULES_REL)
        expect = self.view()["expect"]
        result = self.run_handoff(
            "--yes", "--expect", expect, "--via", "board", "--json", guard="enable"
        )
        return row, result

    def assert_terminal_only(self, rules: dict) -> None:
        before = slurp(self.target(self.RULES_REL))
        row, result = self.board(rules)
        self.assertFalse(row["board"], row)
        self.assertEqual(row["kind"], "layer-config")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        answer = json.loads(result.stdout)
        self.assertIn("防御の記述", answer["error"])
        self.assertIn("ターミナル", answer["error"])
        self.assertEqual(slurp(self.target(self.RULES_REL)), before)
        # ターミナルの前のユーザなら取り込める。
        self.assertEqual(self.take().returncode, 0)

    def test_changing_the_except_of_a_guard_rule_is_terminal_only(self):
        rules = json.loads(json.dumps(self.rules))
        rules["deny"][0]["except"] = [{"regex": ".*"}]
        self.assert_terminal_only(rules)

    def test_removing_a_deny_is_terminal_only(self):
        rules = json.loads(json.dumps(self.rules))
        rules["deny"] = rules["deny"][:1]
        self.assert_terminal_only(rules)

    def test_changing_a_deny_is_terminal_only(self):
        rules = json.loads(json.dumps(self.rules))
        rules["deny"][1]["glob"] = "*/never/*"
        self.assert_terminal_only(rules)

    def test_adding_a_guard_id_is_terminal_only(self):
        rules = json.loads(json.dumps(self.rules))
        rules["allow"] = [{"id": "guard-open", "match": "Write", "glob": "*"}]
        self.assert_terminal_only(rules)

    def test_moving_a_guard_rule_to_allow_is_terminal_only(self):
        rules = json.loads(json.dumps(self.rules))
        moved = rules["deny"].pop(0)
        moved.pop("message")
        rules["allow"] = [moved]
        self.assert_terminal_only(rules)

    def test_adding_a_deny_or_an_ask_is_taken_from_the_board(self):
        rules = json.loads(json.dumps(self.rules))
        rules["deny"].append({**RULES["deny"][0], "id": "more", "glob": "*/y/*"})
        rules["ask"] = [{"id": "check", "match": "Bash", "regex": "make"}]
        row, result = self.board(rules)
        self.assertTrue(row["board"], row)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(slurp(self.target(self.RULES_REL)))["ask"], rules["ask"])

    def test_the_text_view_says_it_is_terminal_only(self):
        rules = json.loads(json.dumps(self.rules))
        rules["deny"] = rules["deny"][:1]
        self.stage(self.RULES_REL, json.dumps(rules).encode())
        result = self.run_handoff()
        self.assertIn("ターミナルのみ", result.stdout)
        self.assertIn("keep-out", result.stdout)

    def test_a_skill_is_defense_and_refused_from_the_board(self):
        rel = ".claude/skills/x/SKILL.md"
        write(self.target(rel), "---\nname: x\n---\n")
        self.stage(rel, b"---\nname: x\nallowed-tools: Bash\n---\n")
        row = self.entry(self.view(), rel)
        self.assertEqual(row["kind"], "defense")
        self.assertFalse(row["board"])


class LayerConfigTest(HandoffCase):
    RULES_REL = ".ccnavi/config/common/rules.yml"
    PHASES_REL = ".ccnavi/config/common/phases.yml"

    def test_a_broken_rules_file_is_an_error_like_lint(self):
        self.stage(self.RULES_REL, b"version: 1\ndeny: [\n")
        row = self.entry(self.view(), self.RULES_REL)
        self.assertEqual(row["kind"], "layer-config")
        self.assertEqual(row["state"], "rejected")
        self.assertIn("ルールを読めない", " ".join(row["errors"]))

    def test_an_empty_deny_is_the_same_error_as_lint(self):
        self.stage(self.RULES_REL, b"version: 1\ndeny: []\nallow: []\n")
        row = self.entry(self.view(), self.RULES_REL)
        self.assertIn("`deny` が空", " ".join(row["errors"]))
        # 同じ中身を `--lint` に掛けると、同じ文の error になる。
        lint = run_ccnavi(
            ["--root", self.root, "--lint", "--rules", self.draft_path(self.RULES_REL), "--json"],
            cwd=ROOT,
        )
        details = [p["detail"] for p in json.loads(lint.stdout)["problems"]]
        self.assertTrue(any("`deny` が空" in d for d in details))

    def test_a_good_rules_file_is_taken(self):
        changed = dict(RULES)
        changed["deny"] = [*RULES["deny"], {**RULES["deny"][0], "id": "second"}]
        self.stage(self.RULES_REL, json.dumps(changed).encode())
        self.assertTrue(self.view()["ok"])
        self.assertEqual(self.take().returncode, 0)

    def test_a_layer_file_that_does_not_exist_cannot_be_created(self):
        rel = ".ccnavi/config/project/rules.yml"
        write(self.draft_path(rel), json.dumps(RULES))
        row = self.entry(self.view(), rel)
        self.assertEqual(row["state"], "rejected")

    def test_a_broken_layer_phases_file_is_an_error(self):
        rel = ".ccnavi/config/project/phases.yml"
        write(config_path(self.root, "phases"), "version: 1\nphases: {}\n")
        self.stage(rel, b"version: 1\nphases:\n  design:\n    kind: nope\n")
        row = self.entry(self.view(), rel)
        self.assertEqual(row["state"], "rejected", row)

    def test_a_template_taken_with_the_phases_is_checked_against_the_new_phases(self):
        rel = ".ccnavi/config/project/templates/feature.yml"
        text = TEMPLATE.replace("{type: implement, after: [1]}", "{type: brand-new, after: [1]}")
        write(self.draft_path(rel), text)
        alone = self.entry(self.view(rel), rel)
        self.assertIn("brand-new", " ".join(alone["warnings"]))
        new_phases = PHASES + (
            "  brand-new:\n"
            "    kind: work\n"
            "    title: 新しい型\n"
            "    review: mr\n"
            '    scope: ["src/*"]\n'
        )
        self.stage(self.PHASES_REL, new_phases.encode())
        together = self.view(rel, self.PHASES_REL)
        self.assertNotIn("brand-new", " ".join(self.entry(together, rel)["warnings"]))
        self.assertTrue(together["ok"], together)


class TemplateTest(HandoffCase):
    REL = ".ccnavi/config/project/templates/feature.yml"
    COMMON = ".ccnavi/config/common/templates/feature.yml"

    def test_a_name_that_differs_from_the_file_name_is_an_error(self):
        write(self.draft_path(self.REL), TEMPLATE.replace("name: feature", "name: other"))
        row = self.entry(self.view(), self.REL)
        self.assertEqual(row["state"], "rejected")

    def test_a_todo_mark_is_a_warning(self):
        write(self.draft_path(self.REL), TEMPLATE.replace("機能を", "TODO 機能を"))
        row = self.entry(self.view(), self.REL)
        self.assertEqual(row["state"], "new")
        self.assertIn("TODO", " ".join(row["warnings"]))

    def test_a_hidden_character_in_a_comment_is_an_error(self):
        write(self.draft_path(self.REL), TEMPLATE + "# ‮\n")
        row = self.entry(self.view(), self.REL)
        self.assertEqual(row["state"], "rejected")

    def test_the_same_name_with_other_content_in_common_is_an_error(self):
        write(self.target(self.COMMON), TEMPLATE.replace("新しい機能", "別の"))
        write(self.draft_path(self.REL), TEMPLATE)
        row = self.entry(self.view(), self.REL)
        self.assertIn("共通に同名で中身の違う", " ".join(row["errors"]))

    def test_the_collision_is_judged_after_the_import(self):
        # 共通の同名も同じ回に同じ中身へ直すなら、衝突しない。
        write(self.target(self.COMMON), TEMPLATE.replace("新しい機能", "別の"))
        write(self.draft_path(self.COMMON), TEMPLATE)
        write(self.draft_path(self.REL), TEMPLATE)
        answer = self.view()
        self.assertTrue(answer["ok"], answer)


class DefenseTest(HandoffCase):
    def test_crlf_in_sh_is_an_error(self):
        self.stage(SCRIPT, b"#!/bin/sh\r\necho new\r\n")
        row = self.entry(self.view(), SCRIPT)
        self.assertEqual(row["state"], "rejected")
        self.assertIn("CRLF", " ".join(row["errors"]))

    @unittest.skipUnless(shutil.which("sh"), "sh が無い")
    def test_a_syntax_error_in_sh_is_an_error(self):
        self.stage(SCRIPT, b"#!/bin/sh\nif then\n")
        row = self.entry(self.view(), SCRIPT)
        self.assertIn("sh -n", " ".join(row["errors"]))

    def test_settings_must_be_json(self):
        self.stage(SETTINGS, b"{not json\n")
        row = self.entry(self.view(), SETTINGS)
        self.assertIn("JSON", " ".join(row["errors"]))

    @unittest.skipUnless(shutil.which("sh"), "sh が無い")
    def test_good_defense_files_are_taken_from_the_terminal(self):
        self.stage(SCRIPT, b"#!/bin/sh\necho new\n")
        self.stage(SETTINGS, b'{"hooks": {"x": 1}}\n')
        self.assertTrue(self.view()["ok"])
        self.assertEqual(self.take().returncode, 0)
        self.assertEqual(slurp(self.target(SCRIPT)), b"#!/bin/sh\necho new\n")


class ReadyTest(HandoffCase):
    def test_pending_names_drafts_that_differ_from_the_target(self):
        self.stage(DOCS, b"new\n")
        self.stage(SCRIPT)
        write(self.draft_path("learnings/x.md"), "x\n")
        self.assertEqual(handoff.pending(self.root), [f"wip/handoff/{DOCS}"])

    def test_ready_stops_on_a_tracked_draft_that_was_not_taken(self):
        git(self.root, "init", "--quiet", "-b", "main")
        self.stage(DOCS, b"new\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "x")
        conf, _ = settings.load(self.root)
        problems = review._merge_problems(self.root, conf, self.root)
        said = [p for p in problems if "取り込んでいない保護対象の下書き" in p]
        self.assertEqual(len(said), 1, problems)
        self.assertIn("ccnavi-user-handoff.sh", said[0])
        self.assertIn(f"wip/handoff/{DOCS}", said[0])

    def test_ready_does_not_name_untracked_drafts_twice(self):
        git(self.root, "init", "--quiet", "-b", "main")
        self.stage(DOCS, b"new\n")
        conf, _ = settings.load(self.root)
        problems = review._merge_problems(self.root, conf, self.root)
        self.assertTrue(any("未追跡" in p for p in problems), problems)
        self.assertFalse(any("取り込んでいない保護対象の下書き" in p for p in problems), problems)

    def test_ready_does_not_stop_on_taken_or_unrelated_drafts(self):
        git(self.root, "init", "--quiet", "-b", "main")
        self.stage(DOCS)
        write(self.draft_path("learnings/x.md"), "x\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "--quiet", "-m", "x")
        conf, _ = settings.load(self.root)
        problems = review._merge_problems(self.root, conf, self.root)
        self.assertFalse(any("取り込んでいない保護対象の下書き" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
