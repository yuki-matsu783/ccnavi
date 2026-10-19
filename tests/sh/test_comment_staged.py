"""`comment_staged`（ccnavi-common-review.sh）。エージェントの単発の投稿を操作記録に載せて送る。

`ccnavi`・`comment`・`find_marked`・`account` は代役に差し替え、分岐だけを確かめる。

- 記録を作れなければ、前と同じ `comment` に戻る
- 送り済みなら送らない
- 送るときは本文の末尾に目印を足し、pending → sent を記録する
- 前に試した形跡があれば、先に照合して、確定できなければ送らず 1 で止まる
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT, common_sh

SHELL = shutil.which("sh") or shutil.which("bash")
JQ = shutil.which("jq")
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")

HARNESS = """
. "$(dirname "$0")/ccnavi-common.sh"
review_label=t
ccnavi_load ccnavi-common-review.sh
JQ="$JQ_BIN"
state="$STATE"
branch=feature
ccnavi() {
	[ "$1" = ops ] || return 1
	shift
	printf '%s\\n' "$*" >>"$STATE/ops.log"
	case "$1" in
	begin) [ -z "${NO_RECORD:-}" ] || return 1; printf '%s\\n' "$OPID" ;;
	show)
		printf '{"stages":[{"id":"comment","state":"%s","url":"u0","tries":%s}]}' \\
			"$ST" "$TRIES"
		;;
	reconcile) printf '%s\\n' "$VERDICT" ;;
	*) : ;;
	esac
}
comment() {
	cat "$3" >"$STATE/posted.md"
	[ -z "${POST_FAIL:-}" ] || return 1
	printf '{"url":"https://h/c/1","created_at":"","author":"me"}\\n'
}
create_issue() {
	cat "$1" >"$STATE/issue.md"
	printf '{"number":5,"url":"https://h/o/r/issues/5"}\\n'
}
find_marked() { printf 'yes %s\\n' "${FOUND:-}"; }
account() { printf 'me'; }
"${FUNC:-comment_staged}" "$@"
"""


@unittest.skipIf(SHELL is None or JQ is None, "sh / jq が無い")
class CommentStagedTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = directory.name
        scripts = os.path.join(self.dir, "scripts")
        os.makedirs(scripts)
        for name in common_sh(SH_DIR):
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        shutil.copy(os.path.join(SH_DIR, "ccnavi-common-review.sh"), scripts)
        self.harness = os.path.join(scripts, "harness.sh")
        with open(self.harness, "w", encoding="utf-8", newline="\n") as f:
            f.write(HARNESS)
        self.body = os.path.join(self.dir, "body.md")
        with open(self.body, "w", encoding="utf-8", newline="\n") as f:
            f.write("本文\n")

    def run_staged(self, **env):
        base = {
            **os.environ,
            "JQ_BIN": JQ,
            "STATE": self.dir,
            "OPID": "a" * 32,
            "ST": "pending",
            "TRIES": "0",
            "VERDICT": "send ",
        }
        base.update(env)
        return subprocess.run(
            [SHELL, self.harness, "comment", "k", "7", "https://h/mr/7", self.body],
            env=base,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    def ops_log(self):
        path = os.path.join(self.dir, "ops.log")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as f:
            return f.read()

    def posted(self):
        path = os.path.join(self.dir, "posted.md")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_sends_with_a_marker_and_records_the_stages(self):
        done = self.run_staged()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("https://h/c/1", done.stdout)
        self.assertEqual(self.posted(), f"本文\n\n<!-- ccnavi:op={'a' * 32} stage=comment -->\n")
        log = self.ops_log()
        self.assertIn("stage-set " + "a" * 32 + " comment pending", log)
        self.assertIn("comment sent https://h/c/1 me", log)

    def test_close_early_issue_is_created_with_a_marker(self):
        done = subprocess.run(
            [SHELL, self.harness, "close-early", "fam", "issue", "issue", "7", "u", self.body],
            env={
                **os.environ,
                "JQ_BIN": JQ,
                "STATE": self.dir,
                "OPID": "a" * 32,
                "ST": "pending",
                "TRIES": "0",
                "FUNC": "staged_send",
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("issues/5", done.stdout)
        with open(os.path.join(self.dir, "issue.md"), encoding="utf-8") as f:
            self.assertIn("stage=issue -->", f.read())
        self.assertIn("issue sent https://h/o/r/issues/5", self.ops_log())

    def test_without_a_record_it_posts_like_before(self):
        done = self.run_staged(NO_RECORD="1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.posted(), "本文\n")

    def test_already_sent_is_not_posted_again(self):
        done = self.run_staged(ST="sent")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsNone(self.posted())
        self.assertIn("u0", done.stdout)

    def test_an_earlier_try_is_reconciled_first(self):
        found = self.run_staged(TRIES="1", VERDICT="sent", FOUND="https://h/c/9")
        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertIsNone(self.posted())
        self.assertIn("https://h/c/9", found.stdout)

    def test_an_unconfirmed_send_stops_without_posting(self):
        done = self.run_staged(ST="unknown", VERDICT="unknown 作成直後")
        self.assertEqual(done.returncode, 1)
        self.assertIsNone(self.posted())

    def test_a_failed_post_is_recorded_as_unknown(self):
        done = self.run_staged(POST_FAIL="1")
        self.assertEqual(done.returncode, 1)
        self.assertIn("comment unknown", self.ops_log())


if __name__ == "__main__":
    unittest.main()
