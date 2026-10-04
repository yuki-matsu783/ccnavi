"""ccnavi-branches.sh の受入テスト（ADR-0101）。

使い捨てのワークスペースを組み、sh を外から呼ぶ。実行ファイルはこのツリーのソースを
`python -m ccnavi` で起こす（tests/sh/test_branch_field_sh.py と同じ形）。ホストは PATH の
先頭に置いた `curl` の代役が答える（tests/sh/test_review_host_fixture.py と同じ作り方。
gh / glab は使えない代役にして、curl とトークンの経路に落とす）。代役は、要求の綴り
（`<METHOD> <URL>`）ごとに決めた答えを返し、決めていない要求は 404 にする。

見るのは次のとおり。

1. MR 指定: その MR の元ブランチ（GitHub・GitLab。GitLab のフォークから出た MR は印を付ける）
2. issue 指定: その issue を参照している開いた MR の元ブランチと、名前に番号を含む手元のブランチ
3. ホストに繋げない（トークンが無い・origin が無い・API が落ちた）ときは止めず、手元の候補だけを
   出して「ホストは見ていない」と理由を書く
4. projects/<名前>/ の中から打てば、そのリポジトリを見る
5. --json と、引数の誤り
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
NEEDED = all(shutil.which(tool) for tool in ("git", "jq"))
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")

EXE = """#!/bin/sh
PYTHONPATH='{root}' exec '{python}' -m ccnavi "$@"
"""

# curl の代役。`-X <M>` と URL を読み、FAKE_ROUTES の JSON（"<M> <URL>" → [状態, 本文]）から答える。
CURL = r"""
import json, os, sys
argv = sys.argv[1:]
method, url = "GET", ""
i = 0
while i < len(argv):
    a = argv[i]
    if a in ("-X", "-H", "--header", "--connect-timeout", "--max-time", "--data-binary"):
        if a == "-X":
            method = argv[i + 1]
        i += 2
        continue
    if a.startswith("http"):
        url = a
    i += 1
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(f"{method} {url}\n")
with open(os.environ["FAKE_ROUTES"], encoding="utf-8") as f:
    routes = json.load(f)
status, body = routes.get(f"{method} {url}", [404, {"message": "Not Found"}])
if status >= 400:
    sys.stderr.write(f"curl: (22) The requested URL returned error: {status}\n")
    sys.exit(22)
sys.stdout.write(json.dumps(body))
"""

GITHUB = "https://api.github.com/repos/acme/widgets"
GITLAB = "https://gitlab.com/api/v4/projects/acme%2Fwidgets"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def git(cwd, *args):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout


def init_repo(path, branches=()):
    git(os.path.dirname(path), "init", "-q", "-b", "main", path)
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
        git(path, "config", key, value)
    git(path, "commit", "-q", "--allow-empty", "-m", "seed")
    for name in branches:
        git(path, "branch", name)


def pr(number, ref, title="", body="", full_name="acme/widgets", merged=False):
    return {
        "number": number,
        "title": title,
        "body": body,
        "state": "closed" if merged else "open",
        "merged_at": "2026-10-01T00:00:00Z" if merged else None,
        "html_url": f"https://github.com/acme/widgets/pull/{number}",
        "head": {"ref": ref, "repo": {"full_name": full_name}},
    }


def mr(iid, branch, state="opened", pid=42):
    return {
        "iid": iid,
        "source_branch": branch,
        "source_project_id": pid,
        "state": state,
        "title": f"MR {iid}",
        "web_url": f"https://gitlab.com/acme/widgets/-/merge_requests/{iid}",
    }


@unittest.skipIf(SHELL is None or not NEEDED, "sh・git・jq のどれかが無い")
class BranchesShTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = self._tmp.name
        self.ws = os.path.join(base, "ws")
        os.makedirs(self.ws)
        init_repo(self.ws, ("feature-152-login", "fix/1520-other"))
        write(os.path.join(self.ws, ".gitignore"), "logs/\n.ccnavi/\nprojects/\n")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in ("ccnavi-branches.sh", "ccnavi-common.sh"):
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        self.exe = write(
            os.path.join(base, "exe", "ccnavi"), EXE.format(root=ROOT, python=sys.executable)
        )
        os.chmod(self.exe, os.stat(self.exe).st_mode | stat.S_IXUSR)
        self.bin = os.path.join(base, "bin")
        write(os.path.join(self.bin, "fake_curl.py"), CURL)
        for name, text in (
            ("curl", f"#!/bin/sh\nexec '{sys.executable}' '{self.bin}/fake_curl.py' \"$@\"\n"),
            # gh / glab があっても使わせない（疎通の試しで落ちて curl に切り替わる）
            ("gh", "#!/bin/sh\nexit 1\n"),
            ("glab", "#!/bin/sh\nexit 1\n"),
        ):
            path = write(os.path.join(self.bin, name), text)
            os.chmod(path, 0o755)
        self.routes = os.path.join(base, "routes.json")
        self.log = os.path.join(base, "requests.log")
        self.route({})

    def route(self, routes):
        write(self.routes, json.dumps(routes))

    def origin(self, url, cwd=None):
        git(cwd or self.ws, "remote", "add", "origin", url)

    def run_sh(self, *args, cwd=None, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        for name in ("GITHUB_TOKEN", "GITLAB_TOKEN", "CLAUDE_PROJECT_DIR"):
            env.pop(name, None)
        env.update(
            {
                "PATH": os.pathsep.join([self.bin, os.environ.get("PATH", "")]),
                "CCNAVI_BIN_PATH": self.exe,
                "FAKE_ROUTES": self.routes,
                "FAKE_LOG": self.log,
                **extra,
            }
        )
        return subprocess.run(
            [SHELL, os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-branches.sh"), *args],
            cwd=cwd or self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def ok(self, *args, **kw):
        done = self.run_sh(*args, **kw)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done.stdout

    def candidates(self, text):
        return {
            line.split()[1]: line
            for line in text.splitlines()
            if line.startswith("候補 ") and "在りか=" in line
        }

    # ---- 1. MR 指定

    def test_github_mr_lists_its_source_branch(self):
        self.origin("https://github.com/acme/widgets.git")
        self.route({f"GET {GITHUB}/pulls/5": [200, pr(5, "feature/5-login", merged=True)]})
        text = self.ok("--mr", "5", GITHUB_TOKEN="t0k")
        self.assertIn("ホスト: github.com acme/widgets を見た", text)
        found = self.candidates(text)
        self.assertEqual(list(found), ["feature/5-login"])
        self.assertIn("在りか=ホストだけ  由来=MR  MR=!5(merged)", found["feature/5-login"])

    def test_gitlab_mr_from_a_fork_is_marked(self):
        self.origin("https://gitlab.com/acme/widgets.git")
        self.route(
            {
                f"GET {GITLAB}": [200, {"id": 42}],
                f"GET {GITLAB}/merge_requests/9": [200, mr(9, "feature-152-login", pid=77)],
            }
        )
        data = json.loads(self.ok("--mr", "9", "--json", GITLAB_TOKEN="t0k"))
        self.assertTrue(data["host"]["checked"])
        (only,) = data["candidates"]
        self.assertEqual(only["branch"], "feature-152-login")
        self.assertTrue(only["local"])
        self.assertTrue(only["mrs"][0]["fork"])

    # ---- 2. issue 指定

    def test_github_issue_lists_referring_pull_requests_and_local_names(self):
        self.origin("https://github.com/acme/widgets.git")
        pulls = [
            pr(11, "topic/a", body="Closes #152"),
            pr(12, "topic/b", title="fix #1520"),
            pr(13, "fix-152-x"),
            pr(
                14,
                "topic/c",
                body="see https://github.com/acme/widgets/issues/152",
                full_name="o/w",
            ),
            pr(15, "topic/d", body="&#152; は文字参照"),
        ]
        self.route({f"GET {GITHUB}/pulls?state=open&per_page=100&page=1": [200, pulls]})
        text = self.ok("--issue", "152", GITHUB_TOKEN="t0k")
        found = self.candidates(text)
        self.assertEqual(sorted(found), ["feature-152-login", "fix-152-x", "topic/a", "topic/c"])
        self.assertIn("由来=MR  MR=!11(open)", found["topic/a"])
        self.assertIn("MR=!14(open,フォーク)", found["topic/c"])
        self.assertIn("在りか=手元  由来=名前に番号", found["feature-152-login"])

    def test_gitlab_issue_reads_related_merge_requests(self):
        self.origin("https://gitlab.com/acme/widgets.git")
        related = [mr(3, "feat-a"), mr(4, "feat-b", state="merged")]
        self.route(
            {
                f"GET {GITLAB}": [200, {"id": 42}],
                f"GET {GITLAB}/issues/152/related_merge_requests?per_page=100&page=1": [
                    200,
                    related,
                ],
            }
        )
        found = self.candidates(self.ok("--issue", "152", GITLAB_TOKEN="t0k"))
        self.assertEqual(sorted(found), ["feat-a", "feature-152-login"])

    # ---- 3. ホストに繋げない

    def test_without_a_token_only_local_candidates_and_says_so(self):
        self.origin("https://github.com/acme/widgets.git")
        text = self.ok("--issue", "152")
        self.assertIn("ホストは見ていない（gh が github.com で使えず", text)
        self.assertIn("GITHUB_TOKEN も無い", text)
        self.assertEqual(list(self.candidates(text)), ["feature-152-login"])

    def test_without_origin_says_so(self):
        text = self.ok("--issue", "152")
        self.assertIn("ホストは見ていない（origin が無い）", text)
        self.assertEqual(list(self.candidates(text)), ["feature-152-login"])

    def test_a_failed_api_call_says_which_one(self):
        self.origin("https://github.com/acme/widgets.git")
        text = self.ok("--mr", "6", GITHUB_TOKEN="t0k")
        self.assertIn(
            "ホストは見ていない（ホストの API が失敗した（GET repos/acme/widgets/pulls/6）", text
        )
        self.assertIn("候補なし", text)
        # トークンは出力に出さない
        self.assertNotIn("t0k", text)

    def test_credentials_in_origin_are_hidden(self):
        self.origin("https://oauth2:glpat-secret@gitlab.com/acme/widgets.git")
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("glpat-secret", done.stdout + done.stderr)

    # ---- 4. プロジェクト

    def test_inside_a_project_looks_at_its_repository(self):
        web = os.path.join(self.ws, "projects", "web")
        os.makedirs(os.path.dirname(web))
        init_repo(web, ("feature-152-web",))
        text = self.ok("--issue", "152", cwd=web)
        self.assertIn("プロジェクト web", text)
        self.assertEqual(list(self.candidates(text)), ["feature-152-web"])

    # ---- 5. 形と引数

    def test_bad_arguments_exit_2(self):
        for args in (
            (),
            ("--issue",),
            ("--issue", "0"),
            ("--mr", "x"),
            ("--issue", "1", "--mr", "2"),
            ("--pr", "1"),
        ):
            with self.subTest(args=args):
                self.assertEqual(self.run_sh(*args).returncode, 2)

    def test_help(self):
        done = self.run_sh("--help")
        self.assertEqual(done.returncode, 0)
        self.assertIn("--issue <番号>", done.stdout)

    def test_outside_git_is_refused(self):
        # ワークスペースの外の git でないディレクトリから、ワークスペースを指して打つ
        bare = tempfile.mkdtemp(dir=self._tmp.name)
        done = self.run_sh("--issue", "1", cwd=bare, CCNAVI_WORKSPACE=self.ws)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("git のリポジトリの中ではありません", done.stderr)


if __name__ == "__main__":
    unittest.main()
