"""控えを置く側が、途中を見せない書き方を通っていることの受入テスト。

`fsio` 側の単体テストは `write_text_atomic` そのものしか見ない。それだけだと、
呼び出し側が素の書き方に戻されてもスイートは緑のまま通る（実際に戻して
確かめた）。ここで見るのは配線で、控えを置く 4 か所が `write_json_atomic` を
通ること。

もう 1 つ見るのは、控えを読めなかったときに書き戻さないこと。読めないのは控えが
在るときにしか起きないので、そこで「まだ何も無い」として書くと、覚えていたぶんを
消すことになる。
"""

from __future__ import annotations

import errno
import io
import os
import tempfile
import unittest
from unittest import mock

from ccnavi import approval, ctxfile, fsio, hookio, post, rules


def _payload(session: str = "s1", agent: str = "") -> hookio.Input:
    return hookio.Input(event="PostToolUse", tool_name="Write", session_id=session, agent_id=agent)


def _once_rule() -> rules.Rule:
    return rules.Rule(
        id="note",
        match="Write",
        glob="*",
        decision=rules.ALLOW,
        additional_context_once="1 度だけの文。",
    )


class WiringTest(unittest.TestCase):
    """控えを置く 4 か所が、途中を見せない書き方を通ること。"""

    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="ccnavi-wiring-")
        self.err = io.StringIO()

    def _assert_atomic(self, call):
        """呼び出しが write_json_atomic を通ること。"""
        with mock.patch.object(fsio, "write_json_atomic", wraps=fsio.write_json_atomic) as atomic:
            call()
        self.assertTrue(atomic.called, "write_json_atomic を通っていない")

    def test_ctxfile_once_state(self):
        self._assert_atomic(
            lambda: ctxfile.for_rules(self.err, self.state, _payload(), [_once_rule()], [])
        )

    def test_post_seen_state(self):
        self._assert_atomic(lambda: post._save_seen(self.err, self.state, "s1", {"a"}))

    def test_post_turn_state(self):
        self._assert_atomic(
            lambda: post._save_turn(self.err, self.state, "s1", {"a"}, {"/tmp/tree": "abc123"})
        )

    def test_approval_known_state(self):
        path = os.path.join(self.state, "approved-s1.json")
        self._assert_atomic(lambda: approval._write_known(self.err, path, {"i0001": "1"}))


class UnreadableStateTest(unittest.TestCase):
    """読めなかった控えを、空で上書きしないこと。

    読みの打ち直しが尽きるのは、重なりが続いたときだけ。そこで書き戻すと、
    覚えていたぶんが消える。読めなかった回は、文は渡す側へ倒しつつ、
    控えには触らない。
    """

    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="ccnavi-unreadable-")
        self.err = io.StringIO()
        self.path = ctxfile._once_path(self.state, "s1", "")

    def test_keeps_the_state_when_it_cannot_be_read(self):
        # 覚えている控えを用意する。
        ctxfile.for_rules(self.err, self.state, _payload(), [_once_rule()], [])
        before = fsio.read_json(self.path)[0]
        self.assertEqual(before, {"given": {"note": 1}})

        # 読めない状態にして、もう一度通す。
        with mock.patch.object(
            fsio, "read_json", return_value=(None, PermissionError(errno.EACCES, "busy"))
        ):
            text = ctxfile.for_rules(self.err, self.state, _payload(), [_once_rule()], [])

        # 文は渡す（覚えられないなら言う側へ倒す）。
        self.assertIn("1 度だけの文。", text)
        # 控えは消えていない。
        self.assertEqual(fsio.read_json(self.path)[0], before)
        self.assertIn("控えを読めない", self.err.getvalue())

    def test_missing_state_is_written_as_usual(self):
        """無いのは普通の状態。ここは今までどおり書く。"""
        self.assertFalse(os.path.exists(self.path))
        ctxfile.for_rules(self.err, self.state, _payload(), [_once_rule()], [])
        self.assertEqual(fsio.read_json(self.path)[0], {"given": {"note": 1}})


if __name__ == "__main__":
    unittest.main()
