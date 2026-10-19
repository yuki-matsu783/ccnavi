"""指摘の版・ダイジェスト・全文の転記（decide の版 2）を確かめる。

- 版は鍵と本文（改行コードと Unicode の正規化だけを除いたもの）で決まる
- 見せたあとに指摘が追加されただけなら食い違いに数えず、本文や位置が変わったら数える
- 転記は最初のコメントの全文を、本文の中のバッククォートより長いフェンスで囲む
- issue の本文が上限を超えたら、目次と続きのコメントに分ける
"""

from __future__ import annotations

import unicodedata
import unittest
from types import SimpleNamespace

from ccnavi.tickets import review, review_decide, review_host


def thread(key="u1", body="x", **kw):
    return review_host.Thread(
        id=kw.pop("id", f"id-{key}"),
        url=key,
        path=kw.pop("path", "a.py"),
        line=kw.pop("line", 3),
        body=body,
        **kw,
    )


def decision(threads, host="github", can_issue=True):
    return review_decide.Decision(
        parent=SimpleNamespace(ticket="i0001"),
        ph=SimpleNamespace(number=1),
        result=SimpleNamespace(mr=SimpleNamespace(number=7, url="https://h/pr/7"), host=host),
        unresolved=list(threads),
        can_issue=can_issue,
    )


class ThreadVersionTest(unittest.TestCase):
    def test_the_version_is_32_hex_and_follows_key_and_body(self):
        v = review.thread_version(thread("u1", "本文"))
        self.assertEqual(len(v), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in v))
        self.assertNotEqual(v, review.thread_version(thread("u2", "本文")))
        self.assertNotEqual(v, review.thread_version(thread("u1", "本文2")))

    def test_line_endings_and_unicode_form_do_not_change_it(self):
        base = review.thread_version(thread("u1", "a\nb\nガ"))
        self.assertEqual(base, review.thread_version(thread("u1", "a\r\nb\rガ")))
        decomposed = unicodedata.normalize("NFD", "a\nb\nガ")
        self.assertNotEqual(decomposed, "a\nb\nガ")
        self.assertEqual(base, review.thread_version(thread("u1", decomposed)))

    def test_path_and_line_alone_do_not_change_it(self):
        # 版は鍵と最初のコメントの本文だけで決まる（位置はホストが動かすことがある）。
        self.assertEqual(
            review.thread_version(thread("u1", "x", line=3)),
            review.thread_version(thread("u1", "x", line=9)),
        )


class DigestTest(unittest.TestCase):
    def test_round_trip_and_order_independence(self):
        a, b = thread("u1", "x"), thread("u2", "y")
        d1, d2 = decision([a, b]), decision([b, a])
        self.assertEqual(review_decide.decision_digest(d1), review_decide.decision_digest(d2))
        parsed = review_decide.parse_digest(review_decide.decision_digest(d1))
        self.assertIsNotNone(parsed)
        self.assertEqual(set(parsed[1]), {"u1", "u2"})

    def test_a_version_1_digest_and_garbage_are_not_read(self):
        for text in ("a" * 64, "", "v2.", "v2.!!!", "v2." + "QQ"):
            with self.subTest(text=text):
                self.assertIsNone(review_decide.parse_digest(text))

    def test_additions_are_not_a_change_but_edits_and_removals_are(self):
        a, b = thread("u1", "x"), thread("u2", "y")
        base, versions = review_decide.parse_digest(review_decide.decision_digest(decision([a])))
        # 追加だけ
        self.assertEqual(review_decide.changed_since_shown(decision([a, b]), base, versions), [])
        # 本文が変わった
        self.assertEqual(
            review_decide.changed_since_shown(decision([thread("u1", "changed")]), base, versions),
            ["u1"],
        )
        # 無くなった（解決済みになった）
        self.assertEqual(review_decide.changed_since_shown(decision([b]), base, versions), ["u1"])

    def test_a_different_target_is_a_change(self):
        a = thread("u1", "x")
        base, versions = review_decide.parse_digest(review_decide.decision_digest(decision([a])))
        other = decision([a])
        other.result.mr.number = 8
        self.assertIn("target", review_decide.changed_since_shown(other, base, versions))
        self.assertIn(
            "target",
            review_decide.changed_since_shown(decision([a], can_issue=False), base, versions),
        )

    def test_the_board_json_carries_versions_and_the_last_comment(self):
        t = thread("u1", "x", last_comment_id="c9", last_comment_at="T", last_comment_author="me")
        data = review_decide._decision_json(decision([t]))
        self.assertEqual(data["version"], 2)
        row = data["threads"][0]
        self.assertEqual(row["version"], review.thread_version(t))
        self.assertEqual(row["last_comment"], {"id": "c9", "at": "T", "author": "me"})
        self.assertTrue(data["digest"].startswith("v2."))


class TranscribeTest(unittest.TestCase):
    def test_the_whole_first_comment_is_copied_inside_a_fence(self):
        body = "1行目\n\n```py\nprint(1)\n```\n@someone\n/close\n"
        block = review_decide.thread_block(decision([]), thread("u1", body), 1, "o" * 32)
        self.assertIn("### 指摘 1", block)
        self.assertIn("キー: u1", block)
        self.assertIn("位置: a.py:3", block)
        self.assertIn(f"版: {review.thread_version(thread('u1', body))}", block)
        self.assertIn("取得元: github", block)
        self.assertIn("操作: " + "o" * 32, block)
        # 本文の中の ``` より長いフェンスで囲み、本文はそのまま入る。
        self.assertIn("````text\n", block)
        self.assertIn(body.rstrip("\n"), block)
        self.assertTrue(block.rstrip().endswith("````"))

    def test_the_fence_is_at_least_three_and_longer_than_any_run(self):
        self.assertEqual(review_decide.fence("plain"), "```")
        self.assertEqual(review_decide.fence("a ``` b"), "````")
        self.assertEqual(review_decide.fence("`````` x"), "```````")

    def test_a_thread_without_a_position_omits_it(self):
        block = review_decide.thread_block(decision([]), thread("u1", "x", path="", line=0), 1)
        self.assertNotIn("位置:", block)
        self.assertNotIn("操作:", block)

    def test_a_short_issue_is_one_body_with_the_heading(self):
        d = decision([thread("u1", "x")])
        main, parts = review_decide.issue_texts(d, d.unresolved)
        self.assertEqual(parts, [])
        self.assertTrue(main.startswith("レビューで残った指摘（i0001 のフェーズ 1）"))
        self.assertIn(review_decide.COPY_HEADING, main)
        self.assertIn("### 指摘 1", main)

    def test_a_long_issue_gets_a_toc_and_continuation_comments(self):
        big = "あ" * 20000
        threads = [thread(f"u{i}", big) for i in range(1, 6)]
        d = decision(threads)
        main, parts = review_decide.issue_texts(d, threads)
        self.assertLessEqual(len(main), review_decide.ISSUE_BODY_LIMIT)
        self.assertIn("続きのコメント", main)
        for i in range(1, 6):
            self.assertIn(f"u{i}", main)
        self.assertGreaterEqual(len(parts), 2)
        for part in parts:
            self.assertLessEqual(len(part), review_decide.ISSUE_BODY_LIMIT)
        # 全文が欠けずに続きのコメントに載る。
        self.assertEqual(sum(p.count("あ") for p in parts), 5 * 20000)

    def test_one_huge_thread_is_split_with_the_fence_closed_each_time(self):
        huge = "\n".join(f"line {i} " + "x" * 80 for i in range(1200))
        t = thread("u1", huge)
        d = decision([t])
        _, parts = review_decide.issue_texts(d, [t])
        self.assertGreaterEqual(len(parts), 2)
        for part in parts:
            self.assertLessEqual(len(part), review_decide.ISSUE_BODY_LIMIT)
            self.assertEqual(part.count("```text") % 1, 0)
            self.assertEqual(part.count("```") % 2, 0, "フェンスが閉じていない片がある")
        joined = "\n".join(parts)
        self.assertIn("line 0 ", joined)
        self.assertIn("line 1199 ", joined)


class PostTextTest(unittest.TestCase):
    """保存した判断から、投稿の本文を作り直す（記録は本文を持たない）。"""

    def record(self, **extra):
        base = {
            "id": "a" * 32,
            "kind": "decide",
            "parent": "i0001",
            "phase": 2,
            "mr": 7,
            "repo": {"host": "gitlab", "mr_url": "https://h/mr/7"},
            "shown": [
                {"key": "u1", "id": "d1", "url": "u1", "path": "a.py", "line": 3, "body": "指摘 1"},
                {"key": "u2", "id": "d2", "url": "u2", "path": "", "line": 0, "body": "指摘 2"},
                {"key": "u3", "id": "d3", "url": "u3", "path": "", "line": 0, "body": "指摘 3"},
            ],
            "choices": {"u1": "issue", "u2": "keep", "u3": "fix"},
            "applied": {"local": True, "followup": "i0001-02-03"},
            "stages": [
                {"id": "issue", "state": "pending"},
                {"id": "note", "state": "pending"},
            ],
        }
        base.update(extra)
        return base

    def test_the_issue_text_has_the_issue_threads_only_and_the_operation_id(self):
        text = review_decide.post_text(self.record(), "issue")
        self.assertIn("### 指摘 1", text)
        self.assertIn("指摘 1", text)
        self.assertNotIn("指摘 2", text)
        self.assertNotIn("指摘 3", text)
        self.assertIn("操作: " + "a" * 32, text)
        self.assertIn("取得元: gitlab", text)

    def test_the_note_lists_each_destination_and_the_followup(self):
        text = review_decide.post_text(self.record(), "note")
        self.assertTrue(text.startswith(review_host.MARKER_DECIDE))
        self.assertIn("issue に回す:\n- u1", text)
        self.assertIn("対応しない（受け入れて進む）:\n- u2", text)
        self.assertIn("続きの子チケット `i0001-02-03`", text)

    def test_the_note_names_the_issue_once_it_was_created(self):
        stages = [
            {"id": "issue", "state": "sent", "url": "https://h/demo/-/issues/12"},
            {"id": "note", "state": "pending"},
        ]
        text = review_decide.post_text(self.record(stages=stages), "note")
        self.assertIn("issue に回した分: #12 https://h/demo/-/issues/12", text)

    def test_unknown_stage_and_missing_issue_threads_give_nothing(self):
        self.assertIsNone(review_decide.post_text(self.record(), "nope"))
        self.assertIsNone(review_decide.post_text(self.record(), "issue-part-1"))
        self.assertIsNone(review_decide.post_text(self.record(choices={"u1": "keep"}), "issue"))

    def test_the_resume_plan_replies_to_the_saved_accepted_threads_only(self):
        import json
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "plan.json")
            self.assertEqual(review_decide.resume_plan(self.record(), path), 0)
            plan = review_host.read_accept_plan(path)
        self.assertEqual(plan.ids, ["d1", "d2"])
        self.assertEqual(plan.op, "a" * 32)
        self.assertEqual(plan.digest, "")
        self.assertTrue(json.dumps(plan.keys))


if __name__ == "__main__":
    unittest.main()
