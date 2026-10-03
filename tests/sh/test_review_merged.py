"""ccnavi-review.sh merged の約束（ADR-0093 の 3.6 の 5。段階 2b のレビューの決定 B2）。

ccnavi-sync.sh は答えが `none` のときだけ「マージされていない」と読み、
親のブランチの取り込み状態に gone を書く。
API が落ちた・答えを読めなかったときは `unknown`（終了コード 3）、道具やトークンが無ければ
`none` を出さずに止まる。ホストへの道具（curl）は PATH の先頭に置いた代役に差し替える。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
NEEDED = all(shutil.which(tool) for tool in ("git", "jq"))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


@unittest.skipIf(SHELL is None or not NEEDED, "sh・git・jq のどれかが無い")
class ReviewMergedTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in ("ccnavi-review.sh", "ccnavi-common.sh"):
            shutil.copy(os.path.join(ROOT, ".ccnavi", "scripts", name), scripts)
        subprocess.run(["git", "init", "-q", "-b", "i0001", self.ws], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                self.ws,
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.invalid",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "seed",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", self.ws, "remote", "add", "origin", "https://github.com/o/r.git"],
            check=True,
        )
        self.bin = os.path.join(self._tmp.name, "bin")
        os.makedirs(self.bin)

    def curl_says(self, body, code=0):
        path = write(
            os.path.join(self.bin, "curl"),
            f"#!/bin/sh\nprintf '%s' '{body}'\nexit {code}\n",
        )
        os.chmod(path, 0o755)

    def merged(self, token="t"):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("GITHUB_TOKEN", None)
        env.pop("GITLAB_TOKEN", None)
        if token:
            env["GITHUB_TOKEN"] = token
        env["PATH"] = os.pathsep.join([self.bin, os.environ.get("PATH", "")])
        return subprocess.run(
            [SHELL, os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-review.sh"), "merged"],
            cwd=self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_a_merged_request_is_named(self):
        self.curl_says('[{"number": 7, "merged_at": null}, {"number": 9, "merged_at": "x"}]')
        done = self.merged()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("merged 9", done.stdout.strip())

    def test_no_merged_request_is_none(self):
        self.curl_says("[]")
        done = self.merged()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("none", done.stdout.strip())

    def test_an_api_failure_is_unknown(self):
        self.curl_says("", 22)
        done = self.merged()
        self.assertEqual(3, done.returncode, done.stdout + done.stderr)
        self.assertEqual("unknown", done.stdout.strip())

    def test_an_unreadable_answer_is_unknown(self):
        self.curl_says("not json")
        done = self.merged()
        self.assertEqual(3, done.returncode, done.stdout + done.stderr)
        self.assertEqual("unknown", done.stdout.strip())

    def test_without_a_token_it_never_says_none(self):
        self.curl_says("[]")
        done = self.merged(token="")
        self.assertNotEqual(0, done.returncode)
        self.assertNotIn("none", done.stdout)


@unittest.skipIf(SHELL is None or not NEEDED, "sh・git・jq のどれかが無い")
class ReviewMergedGitLabTest(ReviewMergedTest):
    """GitLab の枝（ADR-0093 の 11.9.1 の 6・11.9.3 の 9）。

    フォークの MR を数えず、プロジェクトの id が引けなければ unknown。
    """

    def setUp(self):
        super().setUp()
        subprocess.run(
            ["git", "-C", self.ws, "remote", "set-url", "origin", "https://gitlab.com/o/r.git"],
            check=True,
        )

    def gitlab_says(self, mrs, project='{"id": 42}', project_code=0, pages=None):
        """URL ごとに答える curl の代役。`pages` を渡すと MR の一覧をページの番号で引く。"""
        mr_cases = (
            "".join(
                f"*merge_requests*page={n}) printf '%s' '{body}' ;;\n" for n, body in pages.items()
            )
            if pages
            else f"*merge_requests*) printf '%s' '{mrs}' ;;\n"
        )
        path = write(
            os.path.join(self.bin, "curl"),
            "#!/bin/sh\n"
            'for a in "$@"; do url="$a"; done\n'
            'case "$url" in\n'
            + mr_cases
            + f"*/projects/o%2Fr) printf '%s' '{project}'; exit {project_code} ;;\n"
            "*) exit 22 ;;\n"
            "esac\n",
        )
        os.chmod(path, 0o755)

    def merged(self, token="t"):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("GITHUB_TOKEN", None)
        env.pop("GITLAB_TOKEN", None)
        if token:
            env["GITLAB_TOKEN"] = token
        env["PATH"] = os.pathsep.join([self.bin, os.environ.get("PATH", "")])
        return subprocess.run(
            [SHELL, os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-review.sh"), "merged"],
            cwd=self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def curl_says(self, body, code=0):
        # 親の試験（GitHub の形）を GitLab の答えの形で回す
        if code:
            self.gitlab_says("", project_code=code)
        elif body.startswith("[") and "merged_at" in body:
            self.gitlab_says('[{"iid": 9, "source_project_id": 42}]')
        else:
            self.gitlab_says(body)

    def test_a_merged_request_is_named(self):
        self.gitlab_says('[{"iid": 9, "source_project_id": 42}]')
        done = self.merged()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("merged 9", done.stdout.strip())

    def test_a_fork_merge_request_is_not_counted(self):
        """フォーク（source_project_id 99）の同じ名前のブランチのマージ済みの MR は数えない。"""
        self.gitlab_says('[{"iid": 5, "source_project_id": 99}]')
        done = self.merged()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("none", done.stdout.strip())

    def test_the_real_request_on_a_later_page_is_found(self):
        """1 ページ目がフォークで埋まっても、2 ページ目の本物を見落とさない。"""
        forks = (
            "[" + ", ".join(f'{{"iid": {n}, "source_project_id": 99}}' for n in range(100)) + "]"
        )
        self.gitlab_says("", pages={1: forks, 2: '[{"iid": 300, "source_project_id": 42}]'})
        done = self.merged()
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("merged 300", done.stdout.strip())

    def test_an_unreadable_project_id_is_unknown(self):
        for project, code in (('{"id": "x"}', 0), ("{}", 0), ("", 22)):
            with self.subTest(project=project, code=code):
                self.gitlab_says(
                    '[{"iid": 9, "source_project_id": 42}]', project=project, project_code=code
                )
                done = self.merged()
                self.assertEqual(3, done.returncode, done.stdout + done.stderr)
                self.assertEqual("unknown", done.stdout.strip())


if __name__ == "__main__":
    unittest.main()
