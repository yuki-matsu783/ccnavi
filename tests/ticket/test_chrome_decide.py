"""Chrome の decide（残った指摘の行き先を決める）の Python の入口の受入テスト。

見るのは 8 つ。

1. 見せる指摘とダイジェストが、手元の `--preview --json` と同じ（版 2 の形）
2. keep・issue・fix が混ざった選択、全部 keep、全部 fix、指摘が 0 件の書くものとホストの計画
3. 直す指摘（fix）には、返信も解決も付かない（型と計画の両方）
4. 変更要求のレビューは通さない。見せた指摘と今の指摘が違えば、何も積まない
5. 見せたあとに増えた指摘は、食い違いに数えず未対応として残す。受け入れ済み（carry）の扱い
6. issue の操作ID の印が、同じ指摘なら同じ、指摘が変われば違う
7. 続きの子の識別子が、親のブランチ上の既存のチケットと重ならない
8. 手元の `decide` と同じ書くものになる（レビュー済みのマーカー・続きの子）
"""

from __future__ import annotations

import json
import os
import re
import sys

from ccnavi.entry import lint, version
from ccnavi.infra import fsio
from ccnavi.tickets import core_review, review_decide, review_host
from tests.ticket.test_swap_points import CoreHarness
from tests.ticket.test_ticket import git, read_json, write
from tests.ticketsample import child_text, load_chrome

STAMP = "2026-09-29T12:00:00+0900"
ACTOR = {"account": "alice"}


def thread(ident, body, url=None, resolved=False, line=3):
    return {
        "id": ident,
        "resolved": resolved,
        "url": url or f"u-{ident}",
        "path": "src/a/x.py",
        "line": line,
        "body": body,
    }


class ChromeDecideHarness(CoreHarness):
    def setUp(self):
        super().setUp()
        write(
            os.path.join(self.root, *lint.SH_COMPAT_FILE.split(os.sep)),
            f"#!/bin/sh\nCCNAVI_COMPAT={version.COMPAT}\n",
        )
        self.fixture = self.reviewed_phase_one()
        self.commit_parent("requested")
        recorded = read_json(os.path.join(self.approved, "phases", "i0001", "1.requested"))["head"]
        head = git(self.parent_tree, "rev-parse", "HEAD").strip()
        names = git(self.parent_tree, "diff", "--name-only", "--no-renames", f"{recorded}..HEAD")
        self.compare = {"base": recorded, "head": head, "files": names.split()}

    def result(self, threads, reviews=()):
        return {
            "host": "fixture",
            "mr": {"number": 7, "url": "https://example/mr/7"},
            "threads": list(threads),
            "reviews": list(reviews),
        }

    def ask(self, mode, result, **extra):
        request = self.chrome_request(
            "decide",
            "i0001",
            heads={"i0001": self.compare["head"]},
            phase=1,
            result=result,
            compare=self.compare,
            mode=mode,
            actor=ACTOR,
            **extra,
        )
        return self.ask_chrome(request)

    def preview(self, threads, **extra):
        answer = self.ask("preview", self.result(threads), **extra)
        self.assertEqual(answer["problems"], [], answer)
        return answer["preview"]

    def apply(self, threads, choices, shown=None, result=None, **extra):
        """見せた指摘（`shown`。既定は今の指摘）のダイジェストで、選択を置く答え。"""
        digest = self.preview(shown if shown is not None else threads)["digest"]
        return self.ask(
            "apply",
            result if result is not None else self.result(threads),
            choices=choices,
            shown_digest=digest,
            **extra,
        )

    @staticmethod
    def rows(answer):
        return {(r["op"], r["path"]): r for r in (answer["changes"] or {}).get("i0001", [])}

    @staticmethod
    def mark(answer):
        row = next(r for r in answer["changes"]["i0001"] if r["path"].endswith("1.reviewed"))
        return json.loads(row["content"])


class PreviewTest(ChromeDecideHarness):
    def test_the_preview_is_the_same_as_the_terminal_one(self):
        threads = [thread("t1", "命名"), thread("t2", "テスト")]
        shown = self.preview(threads)
        data = read_json(self.fixture)
        data["threads"] = [
            {"id": t["id"], "resolved": False, "url": t["url"], "path": t["path"], "line": 3}
            | {"body": t["body"]}
            for t in threads
        ]
        data["mr"] = {"number": 7, "url": "https://example/mr/7"}
        write(self.fixture, json.dumps(data))
        local = self.ccnavi(
            "--cwd",
            self.parent_tree,
            "user",
            "reviewed",
            "1",
            "--accept-unresolved",
            "--result",
            self.fixture,
            "--preview",
            "--json",
        )
        self.assertEqual(local.returncode, 0, local.stderr)
        self.assertEqual(shown, json.loads(local.stdout))
        self.assertRegex(shown["digest"], r"^v2\.[A-Za-z0-9_-]+$")
        self.assertEqual(shown["version"], review_decide.DECIDE_VERSION)
        self.assertEqual([t["key"] for t in shown["threads"]], ["u-t1", "u-t2"])
        self.assertEqual(
            sorted(shown["threads"][0]),
            ["body", "key", "last_comment", "line", "path", "url", "version"],
        )

    def test_the_preview_writes_nothing_and_has_no_host_plan(self):
        answer = self.ask("preview", self.result([thread("t1", "命名")]))
        self.assertIsNone(answer["changes"])
        self.assertNotIn("host_plan", answer)
        self.assertIsNone(answer["code"])

    def test_a_changes_requested_review_is_refused(self):
        answer = self.ask(
            "preview",
            self.result([thread("t1", "命名")], [{"state": "CHANGES_REQUESTED", "url": "r/1"}]),
        )
        self.assertEqual(answer["code"], "CORE_CONFIRM_REVIEW_OPEN")
        self.assertIsNone(answer["preview"])

    def test_a_moved_head_asks_for_the_compare(self):
        request = self.chrome_request(
            "decide",
            "i0001",
            heads={"i0001": self.compare["head"]},
            phase=1,
            result=self.result([]),
            mode="preview",
        )
        need = self.ask_chrome(request)
        self.assertEqual(
            need["need_compare"], {"base": self.compare["base"], "head": self.compare["head"]}
        )
        self.assertIsNone(need["changes"])

    def test_a_bad_request_is_an_error(self):
        for extra in ({"mode": "other"}, {"mode": "apply"}, {"mode": "preview", "carry": "x"}):
            with self.subTest(extra=extra):
                request = self.chrome_request(
                    "decide", "i0001", phase=1, result=self.result([]), **extra
                )
                memfs = os.path.join(self.root, "memfs")
                answer = json.loads(load_chrome().handle(json.dumps(request), memfs))
                self.assertIn("error", answer)


class ApplyTest(ChromeDecideHarness):
    THREADS = [thread("t1", "命名"), thread("t2", "テスト"), thread("t3", "性能")]

    def test_a_mix_of_keep_issue_and_fix(self):
        answer = self.apply(self.THREADS, {"u-t1": "keep", "u-t2": "issue", "u-t3": "fix"})
        self.assertEqual(answer["problems"], [], answer)
        self.assertIsNone(answer["code"])
        plan = answer["host_plan"]
        # issue は issue を選んだ指摘 1 件につき 1 つ。本文の頭に操作ID の印が入る
        (issue,) = plan["issues"]
        self.assertEqual(issue["key"], "u-t2")
        self.assertTrue(issue["body"].startswith(f"<!-- ccnavi:issue op={issue['op']} -->\n"))
        self.assertIn("テスト", issue["body"])
        self.assertIn("src/a/x.py:3", issue["title"])
        # 返信と解決は keep と issue だけ。fix の指摘（t3）は入らない
        self.assertEqual([r["key"] for r in plan["replies"]], ["u-t1", "u-t2"])
        self.assertEqual([r["thread_id"] for r in plan["replies"]], ["t1", "t2"])
        self.assertEqual(
            plan["resolves"],
            [{"key": "u-t1", "thread_id": "t1"}, {"key": "u-t2", "thread_id": "t2"}],
        )
        self.assertNotIn(review_host.MARKER_ISSUE.split("{")[0], plan["replies"][0]["body"])
        self.assertIn(review_host.MARKER_ACCEPT, plan["replies"][0]["body"])
        self.assertNotIn(core_review.ISSUE_URL_SLOT, plan["replies"][0]["body"])
        self.assertIn(core_review.ISSUE_URL_SLOT, plan["replies"][1]["body"])
        self.assertIn(core_review.ISSUE_LINKS_SLOT, plan["comment"])
        self.assertIn("u-t3", plan["comment"])
        # 続きの子を起こし、マーカーは置かない。レビュー待ちの子は done/ へ
        self.assertFalse(answer["reviewed"])
        self.assertEqual(answer["followup"], "i0001-01-02")
        rows = self.rows(answer)
        self.assertIn(("create", ".ccnavi/approved/doing/i0001-01-02.md"), rows)
        self.assertNotIn(("create", ".ccnavi/approved/phases/i0001/1.reviewed"), rows)
        self.assertIn(("delete", ".ccnavi/approved/phases/i0001/1.requested"), rows)
        self.assertIn(("create", ".ccnavi/approved/done/i0001-01-01.md"), rows)
        child = rows[("create", ".ccnavi/approved/doing/i0001-01-02.md")]["content"]
        self.assertIn("性能", child)
        self.assertNotIn("命名", child)
        self.assertEqual(answer["pending_extra"], [])

    def test_all_keep_places_the_reviewed_mark(self):
        answer = self.apply(self.THREADS, dict.fromkeys(["u-t1", "u-t2", "u-t3"], "keep"))
        self.assertEqual(answer["problems"], [], answer)
        self.assertTrue(answer["reviewed"])
        self.assertEqual(answer["followup"], "")
        self.assertEqual(self.mark(answer)["accepted"], ["u-t1", "u-t2", "u-t3"])
        self.assertEqual(self.mark(answer)["via"], "chrome")
        plan = answer["host_plan"]
        self.assertEqual(plan["issues"], [])
        self.assertEqual(len(plan["replies"]), 3)
        self.assertEqual(len(plan["resolves"]), 3)
        self.assertNotIn(core_review.ISSUE_LINKS_SLOT, plan["comment"])
        self.assertIn(review_host.MARKER_DECIDE, plan["comment"])

    def test_all_fix_replies_and_resolves_nothing(self):
        answer = self.apply(self.THREADS, dict.fromkeys(["u-t1", "u-t2", "u-t3"], "fix"))
        self.assertEqual(answer["problems"], [], answer)
        plan = answer["host_plan"]
        self.assertEqual(plan["issues"], [])
        self.assertEqual(plan["replies"], [])
        self.assertEqual(plan["resolves"], [])
        self.assertIsNotNone(plan["comment"])
        self.assertEqual(answer["followup"], "i0001-01-02")
        self.assertFalse(answer["reviewed"])
        child = self.rows(answer)[("create", ".ccnavi/approved/doing/i0001-01-02.md")]["content"]
        for body in ("命名", "テスト", "性能"):
            self.assertIn(body, child)

    def test_no_threads_places_the_reviewed_mark(self):
        answer = self.apply([], {})
        self.assertEqual(answer["problems"], [], answer)
        self.assertTrue(answer["reviewed"])
        self.assertEqual(self.mark(answer)["accepted"], [])
        plan = answer["host_plan"]
        self.assertEqual((plan["issues"], plan["replies"], plan["resolves"]), ([], [], []))
        self.assertIn("未解決（Unresolved）の指摘なし", plan["comment"])

    def test_a_fix_can_not_be_given_a_reply_or_a_resolve(self):
        """型で固める: 受け入れる指摘の型は、直す指摘から作れない。"""
        for choice in (review_decide.CHOICE_KEEP, review_decide.CHOICE_ISSUE):
            core_review.Accepted("u", "t", choice)
        with self.assertRaises(ValueError):
            core_review.Accepted("u", "t", review_decide.CHOICE_FIX)

    def test_a_fix_never_appears_in_the_plan_whatever_the_mix(self):
        keys = ["u-t1", "u-t2", "u-t3"]
        for fixed in keys:
            others = [k for k in keys if k != fixed]
            for pair in (("keep", "keep"), ("keep", "issue"), ("issue", "issue")):
                choices = {fixed: "fix", others[0]: pair[0], others[1]: pair[1]}
                with self.subTest(choices=choices):
                    answer = self.apply(self.THREADS, choices)
                    self.assertEqual(answer["problems"], [], answer)
                    plan = answer["host_plan"]
                    for part in ("issues", "replies", "resolves"):
                        self.assertNotIn(fixed, [row["key"] for row in plan[part]])

    def test_a_thread_without_an_id_can_not_be_accepted(self):
        threads = [thread("", "命名", url="u-1")]
        answer = self.apply(threads, {"u-1": "keep"})
        self.assertEqual(answer["code"], "CORE_DECIDE_THREAD_ID")
        self.assertIsNone(answer["changes"])
        self.assertIsNone(answer["host_plan"])
        # 直すだけなら id は要らない
        answer = self.apply(threads, {"u-1": "fix"})
        self.assertEqual(answer["problems"], [], answer)

    def test_a_changes_requested_review_is_refused(self):
        threads = [thread("t1", "命名")]
        digest = self.preview(threads)["digest"]
        result = self.result(threads, [{"state": "CHANGES_REQUESTED", "url": "r/1"}])
        answer = self.ask("apply", result, choices={"u-t1": "keep"}, shown_digest=digest)
        self.assertEqual(answer["code"], "CORE_CONFIRM_REVIEW_OPEN")
        self.assertIsNone(answer["changes"])
        self.assertIsNone(answer["host_plan"])

    def test_choices_must_cover_the_shown_threads(self):
        for choices in ({"u-t1": "keep"}, {"u-t1": "keep", "u-t2": "keep", "u-t3": "later"}):
            with self.subTest(choices=choices):
                answer = self.apply(self.THREADS, choices)
                self.assertEqual(answer["code"], "CORE_DECIDE_CHOICES")
                self.assertIsNone(answer["changes"])
                self.assertIsNone(answer["host_plan"])

    def test_a_missing_or_old_digest_places_nothing(self):
        for digest in ("", "0" * 64, "v1.abc"):
            with self.subTest(digest=digest):
                answer = self.ask(
                    "apply",
                    self.result(self.THREADS),
                    choices=dict.fromkeys(["u-t1", "u-t2", "u-t3"], "keep"),
                    shown_digest=digest,
                )
                self.assertEqual(answer["code"], "CORE_DECIDE_NO_DIGEST")
                self.assertIsNone(answer["changes"])


class MismatchTest(ChromeDecideHarness):
    def test_an_edited_thread_places_nothing(self):
        shown = [thread("t1", "命名"), thread("t2", "テスト")]
        now = [thread("t1", "命名（書き換えた）"), thread("t2", "テスト")]
        answer = self.apply(now, {"u-t1": "keep", "u-t2": "keep"}, shown=shown)
        self.assertEqual(answer["code"], "CORE_DECIDE_MISMATCH")
        self.assertEqual(answer["mismatch"]["changed"], ["u-t1"])
        self.assertTrue(answer["mismatch"]["digest"]["expected"].startswith("v2."))
        self.assertIsNone(answer["changes"])
        self.assertIsNone(answer["host_plan"])

    def test_a_thread_resolved_by_someone_places_nothing(self):
        shown = [thread("t1", "命名"), thread("t2", "テスト")]
        now = [thread("t1", "命名"), thread("t2", "テスト", resolved=True)]
        answer = self.apply(now, {"u-t1": "keep", "u-t2": "keep"}, shown=shown)
        self.assertEqual(answer["code"], "CORE_DECIDE_MISMATCH")
        self.assertEqual(answer["mismatch"]["changed"], ["u-t2"])

    def test_a_different_target_places_nothing(self):
        shown = [thread("t1", "命名")]
        digest = self.preview(shown)["digest"]
        result = self.result(shown)
        result["mr"] = {"number": 7, "url": "https://example/mr/7-other"}
        answer = self.ask("apply", result, choices={"u-t1": "keep"}, shown_digest=digest)
        self.assertEqual(answer["mismatch"]["changed"], ["target"])

    def test_a_thread_added_after_showing_is_left_pending(self):
        shown = [thread("t1", "命名")]
        now = [thread("t1", "命名"), thread("t9", "あとから")]
        answer = self.apply(now, {"u-t1": "keep"}, shown=shown)
        self.assertEqual(answer["problems"], [], answer)
        self.assertEqual(answer["pending_extra"], ["u-t9"])
        self.assertFalse(answer["reviewed"])
        rows = self.rows(answer)
        # 直す指摘が無く追加分があるので、マーカーも置かず、子も動かさない
        self.assertFalse([p for _, p in rows if p.endswith("1.reviewed")])
        self.assertFalse([p for _, p in rows if "/done/" in p])
        self.assertEqual([r["key"] for r in answer["host_plan"]["replies"]], ["u-t1"])
        # 直す指摘があれば、追加分があっても子を動かして続きの子を起こす
        fixed = self.apply(now, {"u-t1": "fix"}, shown=shown)
        self.assertEqual(fixed["followup"], "i0001-01-02")
        self.assertEqual(fixed["pending_extra"], ["u-t9"])
        self.assertNotIn("u-t9", json.dumps(fixed["host_plan"]["replies"]))


class CarryTest(ChromeDecideHarness):
    SHOWN = [thread("t1", "命名"), thread("t2", "テスト"), thread("t3", "性能")]
    CHOICES = {"u-t1": "keep", "u-t2": "issue", "u-t3": "fix"}

    def resolved(self, *ids, keep_missing=()):
        out = []
        for t in self.SHOWN:
            if t["id"] in keep_missing:
                continue
            out.append(thread(t["id"], t["body"], resolved=t["id"] in ids))
        return out

    def test_the_host_write_is_not_a_mismatch_with_carry_and_is_not_written_again(self):
        now = self.resolved("t1", "t2")
        answer = self.apply(now, self.CHOICES, shown=self.SHOWN, carry=["u-t1", "u-t2"])
        self.assertEqual(answer["problems"], [], answer)
        plan = answer["host_plan"]
        self.assertEqual((plan["issues"], plan["replies"], plan["resolves"]), ([], [], []))
        self.assertEqual(answer["followup"], "i0001-01-02")
        self.assertIn("u-t1", plan["comment"])
        # 今回作る issue が無いので、作った issue の一覧の枠は MR に出さない
        self.assertNotIn(core_review.ISSUE_LINKS_SLOT, plan["comment"])
        # 受け入れ済みの 2 件は、見せた指摘の一部として数える
        self.assertEqual(answer["pending_extra"], [])

    def test_the_same_result_without_carry_is_a_mismatch(self):
        answer = self.apply(self.resolved("t1", "t2"), self.CHOICES, shown=self.SHOWN)
        self.assertEqual(answer["code"], "CORE_DECIDE_MISMATCH")
        self.assertEqual(answer["mismatch"]["changed"], ["u-t1", "u-t2"])

    def test_all_accepted_and_carried_places_the_mark_with_the_keys(self):
        shown = self.SHOWN[:2]
        now = [thread("t1", "命名", resolved=True), thread("t2", "テスト", resolved=True)]
        answer = self.apply(
            now, {"u-t1": "keep", "u-t2": "issue"}, shown=shown, carry=["u-t1", "u-t2"]
        )
        self.assertEqual(answer["problems"], [], answer)
        self.assertTrue(answer["reviewed"])
        self.assertEqual(self.mark(answer)["accepted"], ["u-t1", "u-t2"])

    def test_a_carried_thread_that_vanished_is_refused(self):
        shown = self.SHOWN[:2]
        now = [thread("t2", "テスト", resolved=True)]
        answer = self.apply(
            now, {"u-t1": "keep", "u-t2": "keep"}, shown=shown, carry=["u-t1", "u-t2"]
        )
        self.assertEqual(answer["code"], "CORE_DECIDE_CHOICES")
        self.assertIsNone(answer["changes"])
        self.assertIsNone(answer["host_plan"])

    def test_a_carried_thread_must_be_resolved_on_the_host(self):
        shown = self.SHOWN[:1]
        answer = self.apply(shown, {"u-t1": "keep"}, shown=shown, carry=["u-t1"])
        self.assertEqual(answer["code"], "CORE_DECIDE_CHOICES")
        self.assertIsNone(answer["changes"])

    def test_a_fix_can_not_be_carried(self):
        now = [thread("t1", "命名", resolved=True)]
        answer = self.apply(now, {"u-t1": "fix"}, shown=[thread("t1", "命名")], carry=["u-t1"])
        self.assertEqual(answer["code"], "CORE_DECIDE_CHOICES")

    def test_an_edited_carried_thread_is_still_a_mismatch(self):
        now = [thread("t1", "命名（書き換えた）", resolved=True)]
        answer = self.apply(now, {"u-t1": "keep"}, shown=[thread("t1", "命名")], carry=["u-t1"])
        self.assertEqual(answer["code"], "CORE_DECIDE_MISMATCH")
        self.assertEqual(answer["mismatch"]["changed"], ["u-t1"])


class IssueMarkTest(ChromeDecideHarness):
    def issue_of(self, threads, key):
        answer = self.apply(threads, {t["url"]: "issue" for t in threads})
        self.assertEqual(answer["problems"], [], answer)
        return next(i for i in answer["host_plan"]["issues"] if i["key"] == key)

    def test_the_op_is_the_same_for_the_same_thread(self):
        threads = [thread("t1", "命名"), thread("t2", "テスト")]
        first = self.issue_of(threads, "u-t2")
        again = self.issue_of(threads, "u-t2")
        self.assertEqual(first, again)
        self.assertRegex(first["op"], r"^[0-9a-f]{32}$")
        self.assertIn(f"<!-- ccnavi:issue op={first['op']} -->", first["body"])
        # 指摘ごとに違う
        other = self.issue_of(threads, "u-t1")
        self.assertNotEqual(first["op"], other["op"])

    def test_the_op_changes_with_the_thread_version(self):
        before = self.issue_of([thread("t1", "命名")], "u-t1")
        after = self.issue_of([thread("t1", "命名を直して")], "u-t1")
        self.assertNotEqual(before["op"], after["op"])

    def test_a_long_body_goes_to_follow_up_comments(self):
        long = "あ" * (review_decide.ISSUE_BODY_LIMIT + 10)
        issue = self.issue_of([thread("t1", long)], "u-t1")
        self.assertLessEqual(len(issue["body"]), review_decide.ISSUE_BODY_LIMIT)
        self.assertTrue(issue["comments"])
        self.assertTrue(issue["body"].startswith("<!-- ccnavi:issue op="))

    def test_every_piece_leaves_room_for_the_extension_to_add_its_marker(self):
        # 拡張は 1 回に 60,000 字まで書く。続きのコメントには印の行（約 80 字）を足す
        extension_cap = 60000
        marker_room = 200
        for size in (
            review_decide.ISSUE_BODY_LIMIT - 5,
            review_decide.ISSUE_BODY_LIMIT + 5,
            150000,
        ):
            issue = self.issue_of([thread("t1", "あ" * size)], "u-t1")
            for piece in [issue["body"], *issue["comments"]]:
                self.assertLessEqual(len(piece) + marker_room, extension_cap, size)


class FollowupIdTest(ChromeDecideHarness):
    def test_the_next_id_skips_the_existing_children_of_the_phase(self):
        existing = child_text("i0001-01-03", "i0001", 1, ["wip/design/*"], False).replace(
            'completed_at: ""', 'completed_at: "2026-01-01T00:00:00+0000"'
        )
        write(os.path.join(self.approved, "done", "i0001-01-03.md"), existing)
        answer = self.apply([thread("t1", "命名")], {"u-t1": "fix"})
        self.assertEqual(answer["problems"], [], answer)
        self.assertEqual(answer["followup"], "i0001-01-04")
        paths = [p for _, p in self.rows(answer)]
        self.assertIn(".ccnavi/approved/doing/i0001-01-04.md", paths)
        self.assertNotIn(".ccnavi/approved/doing/i0001-01-03.md", paths)

    def test_the_same_choice_gives_the_same_op_in_the_child(self):
        threads = [thread("t1", "命名")]
        ops = set()
        for _ in range(2):
            answer = self.apply(threads, {"u-t1": "fix"})
            child = self.rows(answer)[("create", f".ccnavi/approved/doing/{answer['followup']}.md")]
            ops.add(re.search(r"ccnavi_op: \"?([0-9a-f]{32})", child["content"]).group(1))
        self.assertEqual(len(ops), 1)


class SameAsTerminalTest(ChromeDecideHarness):
    """手元の `decide` が書いたものと、同じ書くものになる。"""

    def put(self, threads):
        data = read_json(self.fixture)
        data["threads"] = [
            {"id": t["id"], "resolved": False, "url": t["url"], "path": t["path"], "line": 3}
            | {"body": t["body"]}
            for t in threads
        ]
        data["mr"] = {"number": 7, "url": "https://example/mr/7"}
        write(self.fixture, json.dumps(data))

    def local(self, threads, choices, shown=None):
        self.put(shown if shown is not None else threads)
        base = [
            "--cwd",
            self.parent_tree,
            "user",
            "reviewed",
            "1",
            "--accept-unresolved",
            "--result",
            self.fixture,
        ]
        digest = json.loads(self.ccnavi(*base, "--preview", "--json").stdout)["digest"]
        self.put(threads)
        before = self.disk()
        done = self.ccnavi(
            *base, "--yes", json.dumps(choices), "--digest", digest, "--json", "--actor", "alice"
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return self.diff(before, self.disk()).get("i0001", [])

    @staticmethod
    def normalized(rows):
        out = {}
        for row in rows:
            content = row.get("content")
            if isinstance(content, str):
                content = content.replace("\r\n", "\n")
                content = re.sub(r"^settled_by: \"[a-z-]+\"\n", "", content, flags=re.M)
                content = re.sub(r"^ccnavi_op: .*\n", "", content, flags=re.M)
                content = re.sub(r" / 操作: `[0-9a-f]+`", "", content)
                if row["path"].endswith(".reviewed"):
                    mark = json.loads(content)
                    for drop in ("via", "at"):
                        mark.pop(drop, None)
                    content = mark
            out[(row["op"], row["path"])] = content
        return out

    def test_keep_and_issue(self):
        threads = [thread("t1", "命名"), thread("t2", "テスト")]
        choices = {"u-t1": "keep", "u-t2": "issue"}
        chrome = self.apply(threads, choices)
        local = self.local(threads, choices)
        self.assertEqual(
            self.normalized((chrome["changes"] or {}).get("i0001", [])),
            self.normalized(local),
        )

    def test_fix(self):
        threads = [thread("t1", "命名"), thread("t2", "テスト")]
        choices = {"u-t1": "keep", "u-t2": "fix"}
        chrome = self.apply(threads, choices)
        local = self.local(threads, choices)
        self.assertEqual(
            self.normalized((chrome["changes"] or {}).get("i0001", [])),
            self.normalized(local),
        )

    def same(self, threads, choices, shown=None):
        chrome = self.apply(threads, choices, shown=shown)
        self.assertEqual(chrome["problems"], [], chrome)
        local = self.local(threads, choices, shown)
        self.assertEqual(
            self.normalized((chrome["changes"] or {}).get("i0001", [])),
            self.normalized(local),
        )
        return chrome

    def test_all_keep(self):
        threads = [thread("t1", "命名"), thread("t2", "テスト")]
        self.same(threads, {"u-t1": "keep", "u-t2": "keep"})

    def test_all_fix(self):
        threads = [thread("t1", "命名"), thread("t2", "テスト")]
        self.same(threads, {"u-t1": "fix", "u-t2": "fix"})

    def test_no_threads(self):
        self.same([], {})

    def test_a_thread_added_after_showing(self):
        shown = [thread("t1", "命名")]
        now = [*shown, thread("t9", "あとから")]
        answer = self.same(now, {"u-t1": "keep"}, shown)
        self.assertEqual(answer["pending_extra"], ["u-t9"])
        # 追加分だけが残るなら、書くものは無い
        answer = self.same([thread("t9", "あとから")], {}, [])
        self.assertEqual(answer["changes"], {})


class BoundaryTest(ChromeDecideHarness):
    def test_nothing_decided_and_a_thread_added_says_so_in_the_comment(self):
        answer = self.apply([thread("t9", "あとから")], {}, shown=[])
        self.assertEqual(answer["problems"], [], answer)
        comment = answer["host_plan"]["comment"]
        self.assertIn("今回の判断なし。追加分 1 件は判断待ち", comment)
        self.assertNotIn("レビュー済みにした", comment)
        self.assertFalse(answer["reviewed"])
        self.assertEqual(answer["changes"], {})

    def test_more_than_the_limit_of_issues_places_nothing(self):
        many = [thread(f"t{i}", f"指摘{i}") for i in range(core_review.MAX_ISSUES + 1)]
        answer = self.apply(many, {t["url"]: "issue" for t in many})
        self.assertEqual(answer["code"], "CORE_DECIDE_CHOICES")
        self.assertIn(str(core_review.MAX_ISSUES + 1), answer["problems"][0])
        self.assertIsNone(answer["changes"])
        ok = self.apply(many[:-1], {t["url"]: "issue" for t in many[:-1]})
        self.assertEqual(ok["problems"], [], ok)
        self.assertEqual(len(ok["host_plan"]["issues"]), core_review.MAX_ISSUES)

    def test_the_parent_worktree_that_can_not_be_decided_stops_without_writing(self):
        from unittest import mock

        from ccnavi.tickets import approval

        calls = []
        real = core_review.approval.home_dir

        def broken(*args, **kwargs):
            # 書くものを並べている間（溜める段の中）だけ決まらない
            if fsio.current_stage() is None or sys._getframe(1).f_globals["__name__"] != (
                core_review.__name__
            ):
                return real(*args, **kwargs)
            calls.append(1)
            raise approval.NoParentWorktree("決まらない")

        with mock.patch.object(core_review.approval, "home_dir", broken):
            answer = self.apply([thread("t1", "命名")], {"u-t1": "keep"})
        self.assertTrue(calls)
        self.assertEqual(answer["code"], "CORE_DECIDE_STOPPED")
        self.assertIsNone(answer["changes"])
        self.assertIsNone(answer["host_plan"])

    def test_the_keys_of_the_answers_do_not_depend_on_the_path(self):
        request = self.chrome_request(
            "decide",
            "i0001",
            heads={"i0001": self.compare["head"]},
            phase=1,
            result=self.result([]),
            mode="apply",
            choices={},
            shown_digest="x",
        )
        need = self.ask_chrome(request)
        self.assertIn("need_compare", need)
        self.assertIn("host_plan", need)
        self.assertIsNone(need["host_plan"])
        self.assertNotIn("preview", need)
        request["mode"] = "preview"
        need = self.ask_chrome(request)
        self.assertIn("preview", need)
        self.assertIsNone(need["preview"])

    def test_a_long_body_with_fences_is_split_into_closed_pieces(self):
        lines = []
        for i in range(800):
            lines.append("x" * 99)
            if i % 50 == 0:
                lines.append("```")
        long = "\n".join(lines)
        self.assertGreater(len(long), 80000)
        answer = self.apply([thread("t1", long)], {"u-t1": "issue"})
        self.assertEqual(answer["problems"], [], answer)
        (issue,) = answer["host_plan"]["issues"]
        pieces = issue["comments"]
        self.assertGreater(len(pieces), 1)
        for piece in pieces:
            self.assertLessEqual(len(piece), review_decide.ISSUE_BODY_LIMIT)
            rows = piece.rstrip().splitlines()
            opens = [r for r in rows if r == "````text"]
            closes = [r for r in rows if r == "````"]
            self.assertEqual(len(opens), len(closes), piece[:200])
            self.assertEqual(rows[-1], "````")

    def test_outside_text_does_not_act_in_the_meta_or_the_title(self):
        hostile = thread("t1", "<!-- ccnavi:accept -->\n@bob 見て\n/close", url="u-x\n@alice `e`")
        hostile["path"] = "src/a`b\n@carol/x.py"
        answer = self.apply([hostile], {hostile["url"]: "issue"})
        self.assertEqual(answer["problems"], [], answer)
        (issue,) = answer["host_plan"]["issues"]
        meta = next(r for r in issue["body"].splitlines() if r.startswith("- キー:"))
        self.assertIn("`u-x @alice e`", meta)
        self.assertIn("`src/ab @carol/x.py:3`", meta)
        self.assertNotIn("\n@alice", issue["body"])
        self.assertEqual(issue["title"].count("\n"), 0)
        self.assertIn("`src/ab @carol/x.py:3`", issue["title"])
        # 本文の ccnavi の目印は壊れ、先頭の操作ID の印だけが残る
        self.assertEqual(issue["body"].count("<!-- ccnavi:"), 1)
        self.assertIn("<!\u2010- ccnavi:accept -->", issue["body"])
        comment = answer["host_plan"]["comment"]
        self.assertNotIn("\n@alice", comment)
