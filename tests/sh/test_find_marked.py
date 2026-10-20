"""`find_marked`（ccnavi-common-review.sh）。目印で自分の投稿を探す、送信の照合の読み取り。

ホストの一覧は代役（`ccnavi_host_pages` を差し替える）で返し、jq の絞り込みを GitHub と GitLab の
両方で確かめる。

- 目印の一致だけを数える（別の段階・別の操作ID・目印の無い投稿は数えない）
- アカウントが分かれば、その人の投稿だけを数える
- GitHub の issue の一覧には PR も入るので除く
- 一覧を上限で打ち切ったら `no`、読めなければ `error`
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT, common_sh

SHELL = shutil.which("sh") or shutil.which("bash")
JQ = shutil.which("jq")
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")
OP = "a" * 32
ME = {"login": "me"}

HARNESS = """
. "$(dirname "$0")/ccnavi-common.sh"
review_label=t
ccnavi_load ccnavi-common-review.sh
JQ="$JQ_BIN"
kind="$KIND"
path="o/r"
encoded_path() { printf 'o%%2Fr'; }
ccnavi_host_encode() { printf '%s' "$1"; }
ccnavi_host_pages() {
	printf '%s' "$(cat "$LIST")"
	return "${LIST_RC:-0}"
}
find_marked "$@"
"""


@unittest.skipIf(SHELL is None or JQ is None, "sh / jq が無い")
class FindMarkedTest(unittest.TestCase):
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

    def find(self, kind, items, args, rc=0):
        listing = os.path.join(self.dir, "list.json")
        with open(listing, "w", encoding="utf-8") as f:
            json.dump(items, f)
        done = subprocess.run(
            [SHELL, self.harness, *args],
            env={
                **os.environ,
                "JQ_BIN": JQ,
                "KIND": kind,
                "LIST": listing,
                "LIST_RC": str(rc),
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    @staticmethod
    def mark(stage="issue", op=OP):
        return f"本文\n<!-- ccnavi:op={op} stage={stage} -->\n"

    def test_github_issues_match_the_marker_and_the_creator_and_skip_pull_requests(self):
        items = [
            {"html_url": "https://h/o/r/issues/5", "body": self.mark(), "user": {"login": "me"}},
            {
                "html_url": "https://h/o/r/issues/6",
                "body": self.mark("note"),
                "user": {"login": "me"},
            },
            {
                "html_url": "https://h/o/r/issues/7",
                "body": self.mark(op="b" * 32),
                "user": {"login": "me"},
            },
            {"html_url": "https://h/o/r/issues/8", "body": self.mark(), "user": {"login": "other"}},
            {
                "html_url": "https://h/o/r/pull/9",
                "body": self.mark(),
                "user": {"login": "me"},
                "pull_request": {},
            },
            {"html_url": "https://h/o/r/issues/10", "body": "目印なし", "user": {"login": "me"}},
        ]
        got = self.find("github", items, ["issue", OP, "issue", "7", "me", "https://h/o/r/pull/7"])
        self.assertEqual(got, "yes https://h/o/r/issues/5")

    def test_github_comments(self):
        items = [
            {"html_url": "https://h/c/1", "body": self.mark("note"), "user": {"login": "me"}},
            {"html_url": "https://h/c/2", "body": self.mark("note"), "user": {"login": "other"}},
        ]
        got = self.find("github", items, ["note", OP, "note", "7", "me", ""])
        self.assertEqual(got, "yes https://h/c/1")
        # アカウントが分からなければ、目印だけで数える
        both = self.find("github", items, ["note", OP, "note", "7", "", ""])
        self.assertEqual(both, "yes https://h/c/1,https://h/c/2")

    def test_gitlab_issue_uses_the_description_and_notes_use_the_body(self):
        issues = [
            {"web_url": "https://g/i/1", "description": self.mark(), "author": {"username": "me"}},
            {"web_url": "https://g/i/2", "description": self.mark(), "author": {"username": "x"}},
        ]
        self.assertEqual(
            self.find("gitlab", issues, ["issue", OP, "issue", "7", "me", "https://g/mr/7"]),
            "yes https://g/i/1",
        )
        notes = [
            {"id": 31, "body": self.mark("note"), "description": "", "author": {"username": "me"}}
        ]
        self.assertEqual(
            self.find("gitlab", notes, ["note", OP, "note", "7", "me", "https://g/mr/7"]),
            "yes https://g/mr/7#note_31",
        )
        self.assertEqual(
            self.find("gitlab", notes, ["issue-note", OP, "note", "3", "me", "https://g/i/3"]),
            "yes https://g/i/3#note_31",
        )

    def test_nothing_found_on_a_complete_read_is_yes_with_no_urls(self):
        self.assertEqual(self.find("gitlab", [], ["note", OP, "note", "7", "me", "u"]), "yes")

    def test_a_truncated_list_says_no_and_still_returns_what_it_found(self):
        items = [{"html_url": "https://h/c/1", "body": self.mark("note"), "user": {"login": "me"}}]
        self.assertEqual(
            self.find("github", items, ["note", OP, "note", "7", "me", ""], rc=2),
            "no https://h/c/1",
        )

    def test_an_unreadable_list_says_error(self):
        self.assertEqual(
            self.find("github", [], ["note", OP, "note", "7", "me", ""], rc=1), "error"
        )


if __name__ == "__main__":
    unittest.main()
