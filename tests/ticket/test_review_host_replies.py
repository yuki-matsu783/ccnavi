"""ccnavi の依頼コメントへの返信を、指摘として数える（GitLab）。

GitLab では、ccnavi の依頼コメント（`<!-- ccnavi:request`）に返信するとスレッドになる。判定に使う
note は discussion の note から `system: true` を除いたもの。代表の note は本文が `<!-- ccnavi:` で
始まらない最初の note（無ければ先頭）で、本文・URL・位置・書き手はここから取る。`own` は note が全部
目印で始まるときだけ真。

見本は `extensions/chrome/ccnavi-approval/test/fixtures/host/gitlab/replies/`。
sh の jq と Chrome の TS が同じ見本から同じ JSON を組むことは、
`tests/sh/test_review_host_fixture.py` と Chrome の試験が見る。
ここでは、その JSON を実行ファイルが読んだときの結論を見る。

- (a) 依頼スレッドにユーザの返信 → 未解決 1 件、本文と URL は返信
- (b) 先頭は resolvable でなく、返信は resolvable → 対象になる
- (c) 全部 resolvable でない → 数えない（取得の段階で落ちる）
- (d) 依頼 note だけ → 数えない
- (e) 依頼したアカウントと同じアカウントの返信 → 数える
- (f) `own` が不正な型 → 従来の判定（目印と書き手）に戻る
- (g) 受け入れの返信を足した後も、代表の note・URL・版は変わらない
- (h) system note が先頭・途中・末尾に混ざっても、代表・own・last_* は system を除いた集合で決まる
- (i) system note だけの discussion → 取得の段階で落ちる
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi.tickets import review_host
from tests import ROOT

SCENES = os.path.join(
    ROOT, "extensions", "chrome", "ccnavi-approval", "test", "fixtures", "host", "gitlab"
)
POSTER = "201"


def load(scene: str) -> dict:
    with open(os.path.join(SCENES, scene, "expected.json"), encoding="utf-8") as f:
        return json.load(f)


def thread(**kw) -> review_host.Thread:
    return review_host.Thread(**kw)


class RepliesTest(unittest.TestCase):
    def setUp(self):
        self.result = review_host.Result.from_data(load("replies"))
        self.by_id = {t.id[:2]: t for t in self.result.threads}

    def open_ids(self, result=None, poster=POSTER):
        result = result or self.result
        return sorted(
            t.id[:2] for t in review_host._unresolved(result.threads, result.host, poster)
        )

    def test_a_reply_to_the_request_is_counted_with_the_reply_as_the_body_and_url(self):
        t = self.by_id["a0"]
        self.assertEqual(t.body, "この方針で良いか確認したい")
        self.assertTrue(t.url.endswith("#note_5302"))
        self.assertFalse(t.own)
        self.assertEqual(t.author, "202")
        self.assertIn("a0", self.open_ids())

    def test_a_reply_after_a_non_resolvable_first_note_makes_it_a_target(self):
        t = self.by_id["b0"]
        self.assertIn("b0", self.open_ids())
        # 先頭は目印でないので代表。最後の note は返信
        self.assertEqual(t.body, "全体の感想です")
        self.assertTrue(t.url.endswith("#note_5303"))
        self.assertEqual(t.last_comment_id, "5304")

    def test_discussions_with_no_resolvable_note_or_only_system_notes_are_not_taken(self):
        self.assertNotIn("c0", self.by_id)
        self.assertNotIn("i0", self.by_id)
        self.assertNotIn("c0", self.open_ids())
        self.assertNotIn("i0", self.open_ids())

    def test_the_request_note_alone_is_not_counted(self):
        t = self.by_id["d0"]
        self.assertIs(t.own, True)
        self.assertTrue(review_host._ccnavi_post(t, "gitlab", POSTER))
        self.assertNotIn("d0", self.open_ids())

    def test_a_reply_from_the_same_account_is_counted(self):
        t = self.by_id["e0"]
        self.assertEqual(t.author, POSTER)
        self.assertIs(t.own, False)
        self.assertFalse(review_host._ccnavi_post(t, "gitlab", POSTER))
        self.assertIn("e0", self.open_ids())

    def test_the_whole_conclusion(self):
        self.assertEqual(self.open_ids(), ["a0", "b0", "e0", "h1", "h2", "h3"])

    # ---- own の型

    def test_own_takes_only_json_booleans(self):
        for value, expected in ((True, True), (False, False)):
            got = review_host.Result.from_data({"threads": [{"id": "x", "own": value}]})
            self.assertIs(got.threads[0].own, expected)
        for value in ("true", "false", 1, 0, None, [], {}, "yes"):
            got = review_host.Result.from_data({"threads": [{"id": "x", "own": value}]})
            self.assertIsNone(got.threads[0].own, value)
        self.assertIsNone(review_host.Result.from_data({"threads": [{"id": "x"}]}).threads[0].own)

    def test_ccnavi_post_by_own(self):
        marked = "<!-- ccnavi:request i0001:1 -->\n依頼"
        cases = [
            # (host, poster, author, own, body) -> 期待
            ("gitlab", POSTER, POSTER, True, marked, True),
            ("gitlab", POSTER, POSTER, False, marked, False),
            # 取れていなければ従来の判定: 書き手が同じで、本文が目印で始まる
            ("gitlab", POSTER, POSTER, None, marked, True),
            ("gitlab", POSTER, POSTER, None, "ふつうの指摘", False),
            ("gitlab", POSTER, "202", None, marked, False),
            ("gitlab", POSTER, "202", True, marked, False),
            ("gitlab", "", POSTER, True, marked, False),
            ("github", POSTER, POSTER, True, marked, False),
        ]
        for host, poster, author, own, body, expected in cases:
            t = thread(id="x", author=author, own=own, body=body)
            self.assertIs(review_host._ccnavi_post(t, host, poster), expected, (host, own, author))

    def test_an_invalid_own_type_falls_back_to_the_marker_and_author(self):
        copy = load("replies")
        for item in copy["threads"]:
            if item["id"].startswith("d0") or item["id"].startswith("e0"):
                item["own"] = "true"
        result = review_host.Result.from_data(copy)
        # d: 目印で始まり書き手が同じ → 従来どおり ccnavi の投稿。e: 返信は目印で始まらない → 数える
        self.assertEqual(self.open_ids(result), ["a0", "b0", "e0", "h1", "h2", "h3"])
        mine = {t.id[:2]: review_host._ccnavi_post(t, "gitlab", POSTER) for t in result.threads}
        self.assertTrue(mine["d0"])
        self.assertFalse(mine["e0"])

    # ---- 受け入れの返信を足した後

    def test_accept_replies_do_not_change_the_key_or_the_version(self):
        accepted = review_host.Result.from_data(load("replies-accepted"))
        after = {t.id[:2]: t for t in accepted.threads}
        self.assertEqual(sorted(after), sorted(self.by_id))
        for name, before in self.by_id.items():
            now = after[name]
            self.assertEqual(review_host.thread_key(now), review_host.thread_key(before), name)
            self.assertEqual(
                review_host.thread_version(now), review_host.thread_version(before), name
            )
        # ホストで解決した後なので、数えない
        self.assertEqual(self.open_ids(accepted), [])

    # ---- system note と last_*

    def test_the_last_comment_is_the_last_note_without_system_notes(self):
        self.assertEqual(self.by_id["h3"].last_comment_id, "5317")
        self.assertEqual(self.by_id["h2"].last_comment_id, "5315")
        self.assertEqual(self.by_id["h1"].last_comment_id, "5311")

    def test_the_ledger_ignores_system_notes_when_it_compares_the_last_comment(self):
        """受け入れ台帳（`accepted.json`）は、system note が付いても再提起としない。

        system note（コミットの追加・行の変更・解決など）を最後のコメントに数えていた間は、
        受け入れたあとに system note が付くだけで再提起になった。
        system note を除いた最後のコメントの id が台帳の項目と同じなら、受け入れ済みのまま。
        台帳に system note の id を最後のコメントとして残している古い項目は、いちど合わなくなり、
        未解決として数え直される（隠さない側に倒れる）。
        """
        t = self.by_id["h3"]
        entry = {
            "key": review_host.thread_key(t),
            "version": review_host.thread_version(t),
            "last_comment_id": "5317",
        }
        self.assertTrue(review_host._accepted_by_ledger(t, [entry]))
        old = dict(entry, last_comment_id="5318")
        self.assertFalse(review_host._accepted_by_ledger(t, [old]))
        listed = review_host._unresolved(self.result.threads, "gitlab", POSTER, [old])
        self.assertIn("h3", [x.id[:2] for x in listed])

    # ---- decide の照合の範囲

    def test_a_changed_or_removed_representative_note_changes_the_key_or_the_version(self):
        """preview の後に代表の note が消える・変わると、キーか版が変わり、decide は止まる。"""
        shown = thread(id="d", url="m#note_10", body="最初の返信", last_comment_id="11")
        edited = thread(id="d", url="m#note_10", body="書き換えた", last_comment_id="11")
        removed = thread(id="d", url="m#note_11", body="次の返信", last_comment_id="11")
        self.assertEqual(review_host.thread_key(shown), review_host.thread_key(edited))
        self.assertNotEqual(review_host.thread_version(shown), review_host.thread_version(edited))
        self.assertNotEqual(review_host.thread_key(shown), review_host.thread_key(removed))

    def test_a_later_reply_is_not_detected_by_the_digest_inputs(self):
        """制約（今回は変えない）: キーと代表の本文の版だけで照合するので、preview の後に同じ
        discussion へ付いた別の返信は検知しない。last_* は台帳の再提起の判定にだけ使う。"""
        shown = thread(id="d", url="m#note_10", body="最初の返信", last_comment_id="11")
        later = thread(id="d", url="m#note_10", body="最初の返信", last_comment_id="12")
        self.assertEqual(review_host.thread_key(shown), review_host.thread_key(later))
        self.assertEqual(review_host.thread_version(shown), review_host.thread_version(later))
        entry = {
            "key": review_host.thread_key(shown),
            "version": review_host.thread_version(shown),
            "last_comment_id": "11",
        }
        self.assertTrue(review_host._accepted_by_ledger(shown, [entry]))
        self.assertFalse(review_host._accepted_by_ledger(later, [entry]))


if __name__ == "__main__":
    unittest.main()
