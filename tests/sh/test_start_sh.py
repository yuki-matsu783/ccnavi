"""ccnavi-start.sh の受入テスト。

使い捨てのワークスペースと、origin の代わりの bare リポジトリを組み、sh を外から呼ぶ。
origin の URL は GitHub / GitLab の形のままにする。PATH の先頭に置いた git の代役が、
fetch / push のときだけ bare リポジトリへ向ける
（`git remote get-url` が書き換えた URL を返すと、ホストを見分けられないため）。
ホストは PATH の先頭の `curl` の代役が答える（tests/sh/test_branches_sh.py と同じ作り）。
gh / glab は使えない代役にして、curl とトークンの経路に切り替える。代役は POST の本文も記録する。

見るのは次のとおり。

1. 候補 0 件（新規作成）・1 件（続ける）・複数（一覧を出して終了コード 3）
2. ホストを見られないとき（GitHub は MCP の案内と終了コード 4、手元の候補が 1 件なら続ける）
3. フォークの MR、`/` を含むブランチ、--dry-run
4. 途中で失敗して打ち直したときに、重複を作らず続きから進むこと
5. GitLab の経路
6. 既存の MR を採用するときの確認（open・フォーク・マージ先・保護されたブランチ・
   識別子・識別子の重なり・マージ済みの MR）
7. ワークツリーはブランチを直接チェックアウトする。
   毎回 fetch して早送りする
8. MR の番号は親チケットの `mr:` が持つ
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import quote

from tests import ROOT, SRC, common_sh

SHELL = shutil.which("sh") or shutil.which("bash")
REAL_GIT = shutil.which("git")
NEEDED = all(shutil.which(tool) for tool in ("git", "jq"))
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")

EXE = """#!/bin/sh
PYTHONPATH='{src}' exec '{python}' -m ccnavi "$@"
"""

# fetch / push のときだけ、origin の URL を bare リポジトリへ向ける git の代役。
GIT = """#!/bin/sh
for a in "$@"; do
  case "$a" in
  fetch | push | pull | ls-remote)
    exec '{git}' -c "url.{bare}.insteadOf=$FAKE_ORIGIN_URL" "$@" ;;
  esac
done
exec '{git}' "$@"
"""

CURL = r"""
import json, os, sys
argv = sys.argv[1:]
method, url, data = "GET", "", False
i = 0
while i < len(argv):
    a = argv[i]
    if a in ("-X", "-H", "--header", "--connect-timeout", "--max-time", "--data-binary"):
        if a == "-X":
            method = argv[i + 1]
        if a == "--data-binary":
            data = True
        i += 2
        continue
    if a.startswith("http"):
        url = a
    i += 1
body = sys.stdin.read() if data else ""
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(f"{method} {url}\n")
    if body:
        log.write("BODY " + " ".join(body.split("\n")) + "\n")
with open(os.environ["FAKE_ROUTES"], encoding="utf-8") as f:
    routes = json.load(f)
key = f"{method} {url}"
if key not in routes:
    # MR の番号だけが違う closes_issues は、番号の所に * を書いた route で受ける
    import re
    key = re.sub(r"/merge_requests/\d+/closes_issues", "/merge_requests/*/closes_issues", key)
if os.environ.get("FAKE_SLEEP"):
    import time
    time.sleep(float(os.environ["FAKE_SLEEP"]))
status, payload = routes.get(key, [404, {"message": "Not Found"}])
if status >= 400:
    sys.stderr.write(f"curl: (22) The requested URL returned error: {status}\n")
    sys.exit(22)
sys.stdout.write(json.dumps(payload))
"""

GH_URL = "https://github.com/acme/widgets.git"
GL_URL = "https://gitlab.com/acme/widgets.git"
GITHUB = "https://api.github.com/repos/acme/widgets"
GL_API = "https://gitlab.com/api/v4"
GITLAB = "https://gitlab.com/api/v4/projects/acme%2Fwidgets"
GRAPHQL = "POST https://api.github.com/graphql"
PULLS = f"GET {GITHUB}/pulls?state=open&per_page=100&page=1"
BRANCH = "feature-152-add-login"
CLOSED = "state=closed&head=acme%3A{branch}&per_page=100&page=1"
MR_QUERY = f"state=opened&source_branch={BRANCH}&per_page=100&page=1"


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def git(cwd, *args):
    done = subprocess.run(
        [REAL_GIT, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8"
    )
    if done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout.strip()


def pr(number, ref, full_name="acme/widgets", body=""):
    return {
        "number": number,
        "title": f"PR {number}",
        "body": body,
        "state": "open",
        "merged_at": None,
        "html_url": f"https://github.com/acme/widgets/pull/{number}",
        "head": {"ref": ref, "repo": {"full_name": full_name}},
        "base": {"ref": "main"},
    }


def closing(issues, more=False):
    """GitHub の GraphQL（closingIssuesReferences）の答え。issues は (番号, リポジトリ) の並び。"""
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "closingIssuesReferences": {
                        "nodes": [
                            {"number": n, "repository": {"nameWithOwner": repo}}
                            for n, repo in issues
                        ],
                        "pageInfo": {"hasNextPage": more, "endCursor": "c" if more else None},
                    }
                }
            }
        }
    }


def closed_pulls(branch, found=()):
    """同じブランチ名の閉じた PR の一覧（マージ済みの確認）。found はマージ済みの PR。"""
    return {
        f"GET {GITHUB}/pulls?" + CLOSED.format(branch=quote(branch, safe="")): [200, list(found)]
    }


@unittest.skipIf(SHELL is None or not NEEDED, "sh・git・jq のどれかが無い")
class StartShTest(unittest.TestCase):
    origin_url = GH_URL

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = self._tmp.name
        self.bare = os.path.join(base, "origin.git")
        git(base, "init", "-q", "--bare", "-b", "main", self.bare)
        self.ws = os.path.join(base, "ws")
        os.makedirs(self.ws)
        git(base, "init", "-q", "-b", "main", self.ws)
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
            git(self.ws, "config", key, value)
        write(os.path.join(self.ws, ".gitignore"), "logs/\n.ccnavi/\n.claude/\n")
        git(self.ws, "add", ".gitignore")
        git(self.ws, "commit", "-q", "-m", "seed")
        git(self.ws, "push", "-q", self.bare, "main")
        git(self.ws, "remote", "add", "origin", self.origin_url)
        git(self.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(self.ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in (
            "ccnavi-start.sh",
            "ccnavi-branches.sh",
            "ccnavi-git.sh",
            "ccnavi-sync.sh",
            *common_sh(SH_DIR),
        ):
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        self.exe = write(
            os.path.join(base, "exe", "ccnavi"), EXE.format(src=SRC, python=sys.executable)
        )
        os.chmod(self.exe, os.stat(self.exe).st_mode | stat.S_IXUSR)
        self.bin = os.path.join(base, "bin")
        write(os.path.join(self.bin, "fake_curl.py"), CURL)
        for name, text in (
            ("curl", f"#!/bin/sh\nexec '{sys.executable}' '{self.bin}/fake_curl.py' \"$@\"\n"),
            ("gh", "#!/bin/sh\nexit 1\n"),
            ("glab", "#!/bin/sh\nexit 1\n"),
            ("git", GIT.format(git=REAL_GIT, bare=self.bare)),
        ):
            os.chmod(write(os.path.join(self.bin, name), text), 0o755)
        self.routes = os.path.join(base, "routes.json")
        self.log = os.path.join(base, "requests.log")
        self.route({})

    def default_routes(self):
        """ホストの代役の既定の答え。probe と、書き込みの権限と、紐づく issue。"""
        if self.origin_url == GL_URL:
            return {
                f"GET {GITLAB}": [
                    200,
                    {"id": 77, "permissions": {"project_access": {"access_level": 40}}},
                ],
                f"GET {GL_API}/user": [200, {"id": 5}],
                f"GET {GITLAB}/members/all/5": [200, {"access_level": 40}],
                f"GET {GITLAB}/merge_requests/*/closes_issues?per_page=100&page=1": [
                    200,
                    [{"iid": 152, "references": {"full": "acme/widgets#152"}}],
                ],
            }
        return {
            f"GET {GITHUB}": [200, {"id": 1, "permissions": {"push": True}}],
            GRAPHQL: [200, closing([(152, "acme/widgets")])],
        }

    def route(self, routes):
        write(self.routes, json.dumps({**self.default_routes(), **routes}))

    def run_sh(self, *args, token=True, cwd=None, **extra):
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        for name in ("GITHUB_TOKEN", "GITLAB_TOKEN", "CLAUDE_PROJECT_DIR"):
            env.pop(name, None)
        if token:
            env["GITLAB_TOKEN" if self.origin_url == GL_URL else "GITHUB_TOKEN"] = "t0k"
        env.update(
            {
                "PATH": os.pathsep.join([self.bin, os.environ.get("PATH", "")]),
                "CCNAVI_ENTRY_PATH": self.exe,
                "FAKE_ROUTES": self.routes,
                "FAKE_LOG": self.log,
                "FAKE_ORIGIN_URL": self.origin_url,
                **extra,
            }
        )
        return subprocess.run(
            [SHELL, os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-start.sh"), *args],
            cwd=cwd or self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def requests(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            return f.read().splitlines()

    def posts(self):
        lines = self.requests()
        return [
            json.loads(lines[i + 1][5:])
            for i, line in enumerate(lines)
            if line.startswith("POST ")
            and not line.endswith("/graphql")
            and i + 1 < len(lines)
            and lines[i + 1].startswith("BODY ")
        ]

    def wt(self, name):
        return os.path.join(self.ws, ".claude", "worktrees", name)

    def local_branches(self):
        return git(self.ws, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()

    def remote_branches(self):
        return git(self.bare, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()

    def fields(self, text):
        out = {}
        for line in text.splitlines():
            for key in ("ワークツリー", "ブランチ", "MR", "対象リポジトリ"):
                if line.startswith(key + ": "):
                    out[key] = line[len(key) + 2 :]
        return out

    # GitHub で新規作成が通る代役
    def github_new(self, title="Add login", created=True, branch=BRANCH):
        routes = {
            PULLS: [200, []],
            f"GET {GITHUB}/issues/152": [200, {"title": title}],
            f"GET {GITHUB}/pulls?state=open&head=acme%3A{quote(branch, safe='')}": [200, []],
            **closed_pulls(branch),
        }
        if created:
            routes[f"POST {GITHUB}/pulls"] = [
                201,
                {"number": 7, "html_url": "https://github.com/acme/widgets/pull/7"},
            ]
        self.route(routes)

    # ---- 1. 候補 0 件・1 件・複数

    def test_new_issue_creates_branch_worktree_commit_push_and_draft_mr(self):
        self.github_new()
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = self.fields(done.stdout)
        self.assertEqual(got["ブランチ"], BRANCH)
        self.assertEqual(got["MR"], "https://github.com/acme/widgets/pull/7")
        self.assertTrue(got["ワークツリー"].endswith(f".claude/worktrees/{BRANCH}"), got)
        self.assertTrue(os.path.isdir(self.wt(BRANCH)))
        self.assertEqual(git(self.wt(BRANCH), "rev-parse", "--abbrev-ref", "HEAD"), BRANCH)
        self.assertEqual(
            git(self.wt(BRANCH), "log", "-1", "--format=%s"), "chore: #152 の作業を開始する"
        )
        self.assertIn(BRANCH, self.remote_branches())
        (body,) = self.posts()
        self.assertEqual(body["head"], BRANCH)
        self.assertEqual(body["base"], "main")
        self.assertEqual(body["title"], "Add login")
        self.assertEqual(body["body"], "Closes #152")
        self.assertTrue(body["draft"])
        # 提案に MR の番号を書く（必須。チケットの `mr:` で、この MR の番号がチケットに結び付く）
        self.assertIn("必須: 提案の frontmatter に issue: 152 と mr: 7 を書く", done.stdout)
        # 親チケットも提案も無いので、提案を作る次の一手を、埋められる値を埋めて出す
        nexts = [line for line in done.stdout.splitlines() if line.startswith("次: ")]
        self.assertEqual(len(nexts), 1, done.stdout)
        self.assertIn(f"ccnavi-launcher.sh ticket new {BRANCH} --title 'Add login' ", nexts[0])
        self.assertIn(
            "--plan quick-work --scope '<触るファイルの glob>' --issue 152 --mr 7", nexts[0]
        )
        self.assertIn("直接実装に切り替えず、ユーザに報告して止まる", done.stdout)

    def test_second_run_continues_without_duplicates(self):
        self.github_new()
        self.assertEqual(self.run_sh("--issue", "152").returncode, 0)
        self.route({PULLS: [200, [pr(7, BRANCH, body="Closes #152")]], **closed_pulls(BRANCH)})
        before = self.posts()
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        # 既存の MR で続ける枝にも同じ案内
        self.assertIn("必須: 提案の frontmatter に issue: 152 と mr: 7 を書く", done.stdout)
        self.assertIn("次: ワークツリーの中で sh ", done.stdout)
        self.assertIn(f"ticket new {BRANCH} --title ", done.stdout)
        self.assertEqual(self.fields(done.stdout)["ブランチ"], BRANCH)
        self.assertEqual(self.fields(done.stdout)["MR"], "https://github.com/acme/widgets/pull/7")
        self.assertEqual(self.posts(), before)
        self.assertEqual(self.local_branches().count(BRANCH), 1)

    def test_single_local_candidate_gets_a_worktree(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/fix-152-other")
        git(self.ws, "branch", "fix-152-other", "origin/main")
        self.route(
            {
                PULLS: [200, [pr(3, "fix-152-other", body="Closes #152")]],
                **closed_pulls("fix-152-other"),
            }
        )
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(self.wt("fix-152-other")))
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.fields(done.stdout)["ブランチ"], "fix-152-other")

    def test_existing_worktree_is_only_reported(self):
        git(
            self.ws,
            "worktree",
            "add",
            "-q",
            self.wt("fix-152-other"),
            "-b",
            "fix-152-other",
            "origin/main",
        )
        git(self.wt("fix-152-other"), "commit", "-q", "--allow-empty", "-m", "work")
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/fix-152-other")
        self.route(
            {
                PULLS: [200, [pr(3, "fix-152-other", body="Closes #152")]],
                **closed_pulls("fix-152-other"),
            }
        )
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(
            self.fields(done.stdout)["ワークツリー"].endswith("worktrees/fix-152-other")
        )

    def test_several_candidates_create_nothing_and_exit_3(self):
        git(self.ws, "branch", "fix-152-a", "origin/main")
        git(self.ws, "branch", "fix-152-b", "origin/main")
        self.route({PULLS: [200, []]})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("fix-152-a", done.stdout)
        self.assertIn("fix-152-b", done.stdout)
        self.assertFalse(os.path.exists(self.wt("fix-152-a")))
        self.assertEqual(self.posts(), [])

    # ---- 2. ホストを見られない

    def test_github_unreachable_at_search_prints_mcp_guidance_and_exits_4(self):
        done = self.run_sh("--issue", "152", token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        for word in (
            "mcp__github__search_pull_requests",
            "repo:acme/widgets",
            "--mcp-checked",
            "何も作成せずに処理を中断しました",
        ):
            self.assertIn(word, done.stdout)
        self.assertEqual(self.local_branches(), ["main"])
        self.assertFalse(os.path.isdir(os.path.join(self.ws, ".claude", "worktrees")))

    def test_unchecked_host_with_one_local_candidate_stops_with_exit_4(self):
        # ホストを見られないときは、手元の候補が 1 件でも続けない
        # （既存の MR も閉じる issue も確かめられない）
        git(self.ws, "branch", "fix-152-other", "origin/main")
        done = self.run_sh("--issue", "152", token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("既存の MR を確かめられなかった", done.stdout)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))

    def test_adopting_an_existing_mr_guidance_asks_for_all_three_arguments(self):
        # --mr で既存の MR を採用する道は、--mcp-checked を足すだけでは進めない
        done = self.run_sh("--mr", "5", token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn(
            "--mr 5 --issue <閉じる issue> --branch <head.ref> --mcp-checked", done.stdout
        )
        self.assertNotIn("同じコマンドに --mcp-checked を付けて再実行する", done.stdout)
        again = self.run_sh("--mr", "5", "--mcp-checked", token=False)
        self.assertEqual(again.returncode, 4, again.stdout + again.stderr)
        self.assertIn(
            "--issue <issue の番号> --mr 5 --branch <元ブランチ> --mcp-checked", again.stdout
        )

    def test_new_creation_guidance_only_adds_mcp_checked(self):
        # 候補が 0 件で --issue だけの新規作成は、--mcp-checked を足した打ち直しで進む
        done = self.run_sh("--issue", "152", token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        _, args = self.guided_args(done.stdout, "再実行する: ")
        self.assertEqual(args, ["--issue", "152", "--mcp-checked"])

    def test_title_unreadable_prints_issue_read_guidance(self):
        # API が失敗した（500）。ホストには届いていないので MCP の案内と終了コード 4
        self.route({PULLS: [200, []], f"GET {GITHUB}/issues/152": [500, {"message": "boom"}]})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("mcp__github__issue_read", done.stdout)
        self.assertIn("--slug <語> --title", done.stdout)
        self.assertEqual(self.local_branches(), ["main"])

    def test_title_unreadable_stops_even_with_slug_and_title_until_mcp_checked(self):
        # API が失敗して実在を確かめられない間は、--slug・--title があっても無確認で進まない
        self.route({PULLS: [200, []], f"GET {GITHUB}/issues/152": [500, {"message": "boom"}]})
        done = self.run_sh("--issue", "152", "--slug", "login", "--title", "Add login")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("実在", done.stdout)
        self.assertIn("--mcp-checked", done.stdout)
        self.assertEqual(self.local_branches(), ["main"])
        self.assertEqual(self.posts(), [])

    def test_title_unreadable_but_slug_given_goes_on_with_a_provisional_title(self):
        self.route(
            {
                PULLS: [200, []],
                f"GET {GITHUB}/issues/152": [500, {"message": "boom"}],
                f"GET {GITHUB}/pulls?state=open&head=acme%3Afeature-152-login": [200, []],
                **closed_pulls("feature-152-login"),
                f"POST {GITHUB}/pulls": [
                    201,
                    {"number": 8, "html_url": "https://github.com/acme/widgets/pull/8"},
                ],
            }
        )
        # MCP で実在を確かめた後の再実行（--mcp-checked）なら、仮の題で進む
        done = self.run_sh("--issue", "152", "--slug", "login", "--mcp-checked")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.posts()[0]["title"], "#152 login")

    def test_a_title_of_symbols_only_falls_back_to_issue(self):
        """題から何も残らなければ slug は issue（Chrome の「始める」と同じ作り方）。"""
        self.github_new(title="!!!", branch="feature-152-issue")
        done = self.run_sh("--issue", "152", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("feature-152-issue", done.stdout)

    def test_the_branch_from_a_title_is_the_one_chrome_makes(self):
        """題からのブランチ名は Chrome の「始める」と同じ関数（`issue_identifier`）で作る。

        末尾が `-<2 桁>` になる題は、その部分を除く（除かないと、作ったあとに採用の確認で止まり、
        手で消すまで戻れない）。日本語の題は日本語のまま残す。
        """
        for title, branch in (
            ("Update 10 20", "feature-152-update"),
            ("Fix bug 12", "feature-152-fix-bug"),
            ("ログイン画面", "feature-152-ログイン画面"),
        ):
            with self.subTest(title=title):
                self.github_new(title=title, branch=branch)
                done = self.run_sh("--issue", "152", "--dry-run")
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn(f"ブランチ: {branch}（", done.stdout)
                self.assertEqual(self.local_branches(), ["main"])

    def test_an_unusable_identifier_stops_before_anything_is_made(self):
        """--slug で作る識別子が親に使えなければ（58 文字超・末尾が -2 桁）、何も作らずに止める。"""
        for slug, word in (("a" * 50, "長すぎる"), ("login-12", "末尾")):
            with self.subTest(slug=slug):
                branch = f"feature-152-{slug}"
                self.github_new(branch=branch)
                done = self.run_sh("--issue", "152", "--slug", slug)
                self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
                self.assertIn(word, done.stderr)
                self.assertEqual(self.local_branches(), ["main"])
                self.assertEqual(self.remote_branches(), ["main"])
                self.assertFalse(os.path.isdir(self.wt(branch)))
                self.assertEqual(self.posts(), [])

    def test_host_encode_percent_encodes_japanese_branch_names(self):
        # 日本語を生でクエリに入れるとホストが HTML のエラーを返す。エンコードして渡す
        script = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common-host.sh")
        done = subprocess.run(
            [
                SHELL,
                "-c",
                '. "$1"; ccnavi_h_jq=$(command -v jq); ccnavi_host_encode "$2"',
                "sh",
                script,
                "acme:feature-418-状態/jsonl",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "acme%3Afeature-418-%E7%8A%B6%E6%85%8B%2Fjsonl")

    def test_a_merged_mr_of_the_same_name_stops_before_anything_is_made(self):
        merged = {**pr(2, BRANCH), "merged_at": "2026-01-01T00:00:00Z", "state": "closed"}
        self.github_new()
        with open(self.routes, encoding="utf-8") as f:
            routes = json.load(f)
        self.route({**routes, **closed_pulls(BRANCH, [merged])})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("マージ済み MR（!2）", done.stderr)
        self.assertEqual(self.local_branches(), ["main"])
        self.assertEqual(self.remote_branches(), ["main"])

    def test_github_unreachable_at_create_prints_mcp_guidance_then_rerun_continues(self):
        # MR の作成だけ届かない（POST の代役が無い）。ブランチ・ワークツリー・push は済む
        self.github_new(created=False)
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        for word in (
            "mcp__github__create_pull_request",
            "mcp__github__list_pull_requests",
            "owner: acme",
            "repo: widgets",
            f"head: {BRANCH}",
            "base: main",
            "title: Add login",
            "body: Closes #152",
            "draft: true",
            "push 済み",
            "--mr <作った番号> --branch",
        ):
            self.assertIn(word, done.stdout)
        self.assertIn(BRANCH, self.remote_branches())
        self.assertTrue(os.path.isdir(self.wt(BRANCH)))
        # 打ち直し: MR が無くホストが答えるなら、続きの MR 作成から進む
        self.github_new()
        again = self.run_sh("--issue", "152")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.fields(again.stdout)["MR"], "https://github.com/acme/widgets/pull/7")
        self.assertEqual(git(self.ws, "rev-list", "--count", f"origin/main..{BRANCH}"), "1")
        # POST は失敗した 1 回と、打ち直しの 1 回だけ（重複して作っていない）
        self.assertEqual(len(self.posts()), 2)

    def guided_args(self, out, marker):
        """案内の行（marker の後ろ）にあるコマンドを、sh と同じ字句の切り方で引数に戻す。"""
        prefix = "sh .ccnavi/scripts/ccnavi-start.sh "
        for line in out.splitlines():
            if marker in line:
                cmd = line.split(marker, 1)[1].lstrip()
                cmd = cmd.split("（", 1)[0].strip()
                self.assertTrue(cmd.startswith(prefix), cmd)
                return cmd, shlex.split(cmd[len(prefix) :])
        self.fail(f"{marker} の行が無い: {out}")

    def test_rerun_after_mcp_made_the_mr_follows_the_guided_commands(self):
        # チケットが無く、API に届かない状態から: 新規作成 → create 段で 4 → MCP で MR を作った想定
        # → 案内のコマンド（--issue M --mr N --branch <ブランチ> --mcp-checked）を打ち直して 0
        create = self.run_sh("--issue", "152", "--slug", "login", "--mcp-checked", token=False)
        self.assertEqual(create.returncode, 4, create.stdout + create.stderr)
        self.assertIn("mcp__github__create_pull_request", create.stdout)
        self.assertEqual(self.posts(), [])
        cmd, args = self.guided_args(create.stdout, "打ち直す: ")
        self.assertEqual(
            args,
            ["--issue", "152", "--mr", "<作った番号>", "--branch", "feature-152-login"]
            + ["--mcp-checked"],
        )
        # エージェントが MCP で PR（7 番）を作った。ホストは見られないまま、案内のコマンドを打つ
        args[args.index("--mr") + 1] = "7"
        done = self.run_sh(*args, token=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(
            self.fields(done.stdout)["ワークツリー"].endswith("worktrees/feature-152-login")
        )
        self.assertIn("必須: 提案の frontmatter に issue: 152 と mr: 7 を書く", done.stdout)
        self.assertIn("ホストは見ていない（MCP などで確認済みとして扱った）", done.stdout)
        self.assertIn("--issue 152 --mr 7", done.stdout)
        self.assertEqual(self.posts(), [])

    def test_guided_command_keeps_quoting(self):
        title = 'It\'s $HOME  "x" `a`'
        first = self.run_sh("--issue", "152", "--title", title, token=False)
        self.assertEqual(first.returncode, 4, first.stdout + first.stderr)
        cmd, args = self.guided_args(first.stdout, "再実行する: ")
        self.assertEqual(args, ["--issue", "152", "--title", title, "--mcp-checked"])
        self.assertIn("'It'\\''s $HOME  \"x\" `a`'", cmd)

    def test_issue_not_found_exits_2_with_reason(self):
        # 404 はホストには届いている。MCP へ回さず、番号を確かめてもらう
        self.route({PULLS: [200, []]})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("issue #152 が見つからない", done.stderr)
        self.assertNotIn("mcp__github__", done.stdout)
        self.assertEqual(self.local_branches(), ["main"])
        # --slug を付けても、存在しない issue のブランチは作らない
        again = self.run_sh("--issue", "152", "--slug", "login")
        self.assertEqual(again.returncode, 2, again.stdout + again.stderr)
        self.assertEqual(self.local_branches(), ["main"])

    def test_a_repo_that_differs_from_cwd_stops_before_asking_the_host(self):
        proj = os.path.join(self.ws, "projects", "tour")
        os.makedirs(proj)
        git(proj, "init", "-q", "-b", "main")
        git(proj, "remote", "add", "origin", "https://github.com/Acme/Tour.git")
        done = self.run_sh("--issue", "2", "--repo", "acme/tour")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("/projects/tour' && sh '", done.stderr)
        self.assertIn("/.ccnavi/scripts/ccnavi-start.sh' '--issue' '2'", done.stderr)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.local_branches(), ["main"])
        # 同じリポジトリ（大文字小文字違い）なら止めない
        same = self.run_sh("--issue", "152", "--repo", "ACME/Widgets", "--dry-run")
        self.assertNotIn("が違います", same.stderr)
        os.remove(self.log)
        # projects/ の中から、ワークスペース自身のリポジトリを頼むと、ルートへの移動を案内する
        up = self.run_sh("--issue", "152", "--repo", "acme/widgets", cwd=proj)
        self.assertEqual(up.returncode, 1, up.stdout + up.stderr)
        self.assertIn("ワークスペースルートに移動", up.stderr)
        os.remove(self.log) if os.path.exists(self.log) else None
        # projects/ に無いリポジトリは、移動先を案内せず止める
        gone = self.run_sh("--issue", "2", "--repo", "acme/none")
        self.assertEqual(gone.returncode, 1, gone.stdout + gone.stderr)
        self.assertIn("projects/ に見つかりません", gone.stderr)
        self.assertEqual(self.requests(), [])

    def test_the_target_repository_is_reported(self):
        self.github_new()
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            self.fields(done.stdout)["対象リポジトリ"], "acme/widgets（ワークスペース自身）"
        )

    def test_issue_not_found_is_not_bypassed_by_slug_and_title(self):
        # --title を付けても、ホストが読めるなら issue の存在を確かめる。作らず、次の手を案内する
        self.route({PULLS: [200, []]})
        done = self.run_sh("--issue", "999", "--slug", "memo", "--title", "メモアプリ")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("issue #999 が見つからない", done.stderr)
        self.assertNotIn("next-id", done.stderr)
        self.assertIn("ユーザに作ってもらい", done.stderr)
        self.assertEqual(self.local_branches(), ["main"])
        self.assertEqual(self.posts(), [])

    def test_issue_forbidden_exits_2(self):
        self.route({PULLS: [200, []], f"GET {GITHUB}/issues/152": [403, {"message": "no"}]})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("issue #152", done.stderr)
        self.assertIn("権限", done.stderr)

    def test_number_of_a_pull_request_exits_2(self):
        self.route(
            {
                PULLS: [200, []],
                f"GET {GITHUB}/issues/152": [200, {"title": "PR", "pull_request": {"url": "x"}}],
            }
        )
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("#152 は issue ではなく PR", done.stderr)
        self.assertEqual(self.local_branches(), ["main"])

    # ---- 4. 古いブランチ

    def test_stale_branch_without_mr_or_initial_commit_asks_instead_of_resuming(self):
        # 取り込み済みで消し忘れた、統合先と同じ先頭のブランチ。作りかけと見て MR を作ってはいけない
        git(self.ws, "branch", "feature-152-old", "origin/main")
        self.route({PULLS: [200, []]})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("feature-152-old", done.stdout)
        self.assertEqual(self.posts(), [])
        self.assertFalse(os.path.isdir(self.wt("feature-152-old")))
        self.assertEqual(git(self.ws, "rev-list", "--count", "origin/main..feature-152-old"), "0")
        self.assertEqual(self.remote_branches(), ["main"])

    def test_mcp_checked_goes_on_without_the_host_and_stops_at_create(self):
        done = self.run_sh("--issue", "152", "--slug", "login", "--mcp-checked", token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("mcp__github__create_pull_request", done.stdout)
        self.assertIn("head: feature-152-login", done.stdout)
        self.assertIn("feature-152-login", self.remote_branches())

    # ---- 3. フォーク・/ を含むブランチ・dry-run

    def test_fork_mr_is_refused(self):
        self.route({f"GET {GITHUB}/pulls/5": [200, pr(5, "topic/x", full_name="someone/widgets")]})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("フォーク", done.stderr)

    def test_mr_with_slash_branch_checks_out_directly_without_identifier_branch(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic/x")
        self.route({f"GET {GITHUB}/pulls/5": [200, pr(5, "topic/x")], **closed_pulls("topic/x")})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = self.fields(done.stdout)
        self.assertEqual(got["ブランチ"], "topic/x")
        self.assertTrue(got["ワークツリー"].endswith("worktrees/topic-x"))
        self.assertEqual(git(self.wt("topic-x"), "rev-parse", "--abbrev-ref", "HEAD"), "topic/x")
        # 識別子のブランチ（topic-x）は作らない。switch の回り道もしない
        self.assertEqual(sorted(self.local_branches()), ["main", "topic/x"])
        self.assertNotIn("switch", done.stdout)
        self.assertIn("識別子: topic-x", done.stdout)

    # ---- 6. 既存の MR を採用するときの確認

    def adopt(self, branch, mr=None, **routes):
        """origin に branch を置いて --mr 5 を打つ。mr は pr() の上書き。"""
        git(self.ws, "push", "-q", self.bare, f"main:refs/heads/{branch}")
        body = {**pr(5, branch), **(mr or {})}
        self.route({f"GET {GITHUB}/pulls/5": [200, body], **closed_pulls(branch), **routes})
        return self.run_sh("--mr", "5")

    def test_adopt_stops_when_mr_is_not_open(self):
        for state, merged_at in (("closed", None), ("closed", "2026-01-01T00:00:00Z")):
            with self.subTest(merged_at=merged_at):
                done = self.adopt("topic/x", {"state": state, "merged_at": merged_at})
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertIn("open の MR だけ", done.stderr)
                self.assertFalse(os.path.isdir(self.wt("topic-x")))

    def test_adopt_stops_when_target_is_not_integration(self):
        done = self.adopt("topic/x", {"base": {"ref": "develop"}})
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("マージ先は develop", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("topic-x")))

    def test_adopt_stops_when_branch_is_protected(self):
        for branch in ("release/1.0", "develop"):
            with self.subTest(branch=branch):
                done = self.adopt(branch)
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertIn("保護されたブランチ", done.stderr)
                self.assertFalse(os.path.isdir(self.wt(branch.replace("/", "-"))))

    def test_adopt_stops_when_identifier_is_unusable(self):
        long_branch = "feature/" + "a" * 60
        for branch, word in (
            (long_branch, "長すぎる"),
            ("feature/" + "a" * 51, "長すぎる"),  # 59 文字。子の識別子が 64 文字に収まらない
            ("feature/123-login-05", "末尾"),
        ):
            with self.subTest(branch=branch):
                done = self.adopt(branch)
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertIn("使えない", done.stderr)
                self.assertIn(word, done.stderr)

    def test_adopt_accepts_identifier_of_58_characters(self):
        branch = "feature/" + "a" * 50
        done = self.adopt(branch)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_adopt_stops_when_another_branch_has_the_same_identifier(self):
        # origin の全ブランチで見る（MR の無い別ブランチ、手元に無いブランチも）
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic-x")
        done = self.adopt("topic/x")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("topic-x", done.stderr)
        self.assertIn("識別子 topic-x", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("topic-x")))

    def test_adopt_stops_when_another_branch_differs_only_in_case(self):
        """英字の大文字小文字だけ違う識別子も同じものとして止める（macOS・Windows で同じ場所）。"""
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/Topic-X")
        done = self.adopt("topic/x")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("Topic-X", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("topic-x")))

    def test_adopt_accepts_a_japanese_identifier_of_58_characters(self):
        """58 文字の日本語の識別子は通す（dash の `${#x}` はバイト数なので sh では数えない）。"""
        branch = "feature/" + "あ" * 50
        done = self.adopt(branch)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_candidate_without_mr_is_checked_too(self):
        """MR の無い候補（名前に番号を含むブランチ）も、親に使えない識別子なら出さずに止める。"""
        tip = git(self.ws, "commit-tree", "-p", "main", "-m", "work", "main^{tree}")
        git(self.ws, "branch", "fix-152-other-05", tip)
        self.route({PULLS: [200, []]})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("末尾", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other-05")))

    def test_without_the_executable_the_identifier_is_not_guessed(self):
        """実行ファイルが答えなければ、sh で代わりに数えず「確かめられない」と止める。"""
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic/x")
        self.route({f"GET {GITHUB}/pulls/5": [200, pr(5, "topic/x")], **closed_pulls("topic/x")})
        # `worktree id` にだけ答えない実行ファイル
        silent = write(
            os.path.join(self._tmp.name, "silent", "ccnavi"),
            '#!/bin/sh\ncase " $* " in *" worktree id "*) exit 0 ;; esac\n'
            f"exec '{self.exe}' \"$@\"\n",
        )
        os.chmod(silent, 0o755)
        done = self.run_sh("--mr", "5", CCNAVI_ENTRY_PATH=silent)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("確かめられない", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("topic-x")))

    def test_adopt_stops_when_a_merged_mr_has_the_same_branch_name(self):
        merged = {**pr(2, "topic/x"), "merged_at": "2026-01-01T00:00:00Z", "state": "closed"}
        done = self.adopt("topic/x", **closed_pulls("topic/x", [merged]))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("マージ済み MR（!2）", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("topic-x")))

    def test_adopt_merged_check_unreachable_prints_guidance_and_mcp_checked_goes_on(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic/x")
        self.route({f"GET {GITHUB}/pulls/5": [200, pr(5, "topic/x")]})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("マージ済みの MR があるかを確かめられなかった", done.stdout)
        self.assertIn("state=closed", done.stdout)
        self.assertFalse(os.path.isdir(self.wt("topic-x")))
        again = self.run_sh("--mr", "5", "--mcp-checked")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertTrue(os.path.isdir(self.wt("topic-x")))

    # ---- 7. 毎回 fetch して早送りする

    def test_existing_local_branch_is_fast_forwarded_every_time(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic/x")
        git(self.ws, "fetch", "-q", self.bare, "topic/x:refs/heads/topic/x")
        # origin だけ進める
        other = os.path.join(self._tmp.name, "other")
        git(self._tmp.name, "clone", "-q", self.bare, other)
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
            git(other, "config", key, value)
        git(other, "checkout", "-q", "topic/x")
        git(other, "commit", "-q", "--allow-empty", "-m", "remote work")
        git(other, "push", "-q", "origin", "topic/x")
        tip = git(other, "rev-parse", "HEAD")
        self.assertNotEqual(git(self.ws, "rev-parse", "topic/x"), tip)
        self.route({f"GET {GITHUB}/pulls/5": [200, pr(5, "topic/x")], **closed_pulls("topic/x")})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(git(self.wt("topic-x"), "rev-parse", "HEAD"), tip)
        # 2 回目: ワークツリーがあっても取り直して早送りする
        git(other, "commit", "-q", "--allow-empty", "-m", "remote work 2")
        git(other, "push", "-q", "origin", "topic/x")
        tip = git(other, "rev-parse", "HEAD")
        again = self.run_sh("--mr", "5")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(git(self.wt("topic-x"), "rev-parse", "HEAD"), tip)
        self.assertIn("早送り", again.stdout)

    def test_fast_forward_does_not_overwrite_an_ignored_file(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic/x")
        git(self.ws, "fetch", "-q", self.bare, "topic/x:refs/heads/topic/x")
        self.route({f"GET {GITHUB}/pulls/5": [200, pr(5, "topic/x")], **closed_pulls("topic/x")})
        first = self.run_sh("--mr", "5")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        wt = self.wt("topic-x")
        before = git(wt, "rev-parse", "HEAD")
        other = os.path.join(self._tmp.name, "other")
        git(self._tmp.name, "clone", "-q", self.bare, other)
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
            git(other, "config", key, value)
        git(other, "checkout", "-q", "topic/x")
        write(os.path.join(other, "ign.txt"), "remote\n")
        git(other, "add", "ign.txt")
        git(other, "commit", "-q", "-m", "remote ign")
        git(other, "push", "-q", "origin", "topic/x")
        common = git(wt, "rev-parse", "--git-common-dir")
        common = common if os.path.isabs(common) else os.path.join(wt, common)
        write(os.path.join(common, "info", "exclude"), "ign.txt\n")
        write(os.path.join(wt, "ign.txt"), "手元の ignored\n")
        again = self.run_sh("--mr", "5")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(before, git(wt, "rev-parse", "HEAD"))
        with open(os.path.join(wt, "ign.txt"), encoding="utf-8") as f:
            self.assertEqual("手元の ignored\n", f.read())

    def test_unreachable_remote_stops_when_the_mr_is_adopted(self):
        # 候補には必ず MR がある（MR の無い候補は成功させない）。
        # MR を採用するときは origin の全ブランチで
        # 識別子の重なりを確かめるので、git のリモートに届かないと止まる
        tip = git(self.ws, "commit-tree", "-p", "main", "-m", "work", "main^{tree}")
        git(self.ws, "branch", "fix-152-other", tip)
        self.route(
            {
                PULLS: [200, [pr(3, "fix-152-other", body="Closes #152")]],
                **closed_pulls("fix-152-other"),
            }
        )
        shutil.rmtree(self.bare)
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("origin のブランチ一覧を取れなかった", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))

    def test_dry_run_creates_nothing(self):
        self.github_new()
        done = self.run_sh("--issue", "152", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(BRANCH, done.stdout)
        self.assertIn("Add login", done.stdout)
        self.assertEqual(self.local_branches(), ["main"])
        self.assertEqual(self.remote_branches(), ["main"])
        self.assertEqual(self.posts(), [])
        self.assertFalse(os.path.isdir(self.wt(BRANCH)))
        # 次の一手は、--dry-run を外した同じコマンド
        nexts = [line for line in done.stdout.splitlines() if line.startswith("次: ")]
        self.assertEqual(len(nexts), 1, done.stdout)
        self.assertIn("--dry-run を外して実行する", nexts[0])
        self.assertTrue(nexts[0].endswith("ccnavi-start.sh '--issue' '152'"), nexts[0])

    def test_prefix_and_slug(self):
        self.route(
            {
                PULLS: [200, []],
                f"GET {GITHUB}/issues/152": [200, {"title": "Add login"}],
                f"GET {GITHUB}/pulls?state=open&head=acme%3Afix-152-login": [200, []],
                **closed_pulls("fix-152-login"),
            }
        )
        done = self.run_sh("--issue", "152", "--prefix", "fix", "--slug", "login", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("fix-152-login", done.stdout)

    # ---- 5. GitLab

    def test_bad_arguments_exit_2(self):
        for args in (
            (),
            ("--issue",),
            ("--issue", "0"),
            ("--issue", "1", "--mr", "x"),
            ("--issue", "a/b#c"),
            ("--issue", "1", "--slug", "Bad Slug"),
            ("--branch", "x", "--issue", "1"),
            ("--branch", "x", "--mr", "1"),
            ("--check", "--issue", "1"),
            ("--check", "--dry-run"),
            ("--mr", "1", "--slug", "x"),
            ("--nope",),
        ):
            with self.subTest(args=args):
                self.assertEqual(self.run_sh(*args).returncode, 2)

    # ---- 7. 事前確認（--check）と権限

    def check(self, *routes, token=True, **extra):
        if routes:
            self.route(routes[0])
        return self.run_sh("--check", token=token, **extra)

    def labels(self, out):
        got = {}
        for line in out.splitlines():
            if line.startswith("確認: "):
                name, _, rest = line[len("確認: ") :].partition(": ")
                got[name] = rest.split(" ")[0].split("（")[0]
        return got

    def test_check_all_ok_creates_nothing(self):
        done = self.check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith("対象リポジトリ: acme/widgets"), done.stdout)
        self.assertEqual(
            self.labels(done.stdout),
            {k: "OK" for k in ("tools", "origin", "integration", "api", "access", "git")},
        )
        self.assertEqual(self.local_branches(), ["main"])
        self.assertEqual(self.remote_branches(), ["main"])
        self.assertFalse(os.path.isdir(os.path.join(self.ws, ".claude", "worktrees")))
        self.assertNotIn("t0k", done.stdout + done.stderr)

    def test_check_probe_401_and_404_are_ng(self):
        for status, fix in ((401, "auth login"), (404, "origin のパス")):
            with self.subTest(status=status):
                done = self.check({f"GET {GITHUB}": [status, {"message": "x"}]})
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertEqual(self.labels(done.stdout)["api"], "NG")
                self.assertEqual(self.labels(done.stdout)["access"], "省略")
                self.assertIn(fix, done.stdout)
                # git のリモートは API の結果によらず確かめる
                self.assertEqual(self.labels(done.stdout)["git"], "OK")
                self.assertNotIn("t0k", done.stdout + done.stderr)

    def test_check_probe_500_is_unverified(self):
        done = self.check({f"GET {GITHUB}": [500, {"message": "boom"}]})
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertEqual(self.labels(done.stdout)["api"], "未確認")
        self.assertIn("MCP", done.stdout)

    def test_check_without_a_token_is_unverified_and_still_checks_git(self):
        done = self.check(token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        got = self.labels(done.stdout)
        self.assertEqual((got["api"], got["access"], got["git"]), ("未確認", "省略", "OK"))
        self.assertIn("GITHUB_TOKEN", done.stdout)

    def test_check_github_push_false_is_ng(self):
        done = self.check({f"GET {GITHUB}": [200, {"permissions": {"push": False}}]})
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.labels(done.stdout)["access"], "NG")
        self.assertIn("書き込みの権限", done.stdout)

    def test_check_local_path_origin_is_ng(self):
        git(self.ws, "remote", "set-url", "origin", self.bare)
        done = self.check()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.labels(done.stdout)["origin"], "NG")
        self.assertEqual(self.labels(done.stdout)["api"], "省略")

    def test_check_must_stand_alone(self):
        for extra in (("--issue", "1"), ("--dry-run",), ("--mr", "1")):
            with self.subTest(extra=extra):
                self.assertEqual(self.run_sh("--check", *extra).returncode, 2)

    def test_check_times_out_on_a_hanging_curl(self):
        done = self.check(token=True, FAKE_SLEEP="30", CCNAVI_PREFLIGHT_TIMEOUT="2")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertEqual(self.labels(done.stdout)["api"], "未確認")

    def test_check_times_out_on_a_hanging_gh(self):
        write(os.path.join(self.bin, "gh"), "#!/bin/sh\nsleep 30\n")
        os.chmod(os.path.join(self.bin, "gh"), 0o755)
        done = self.check(token=False, CCNAVI_PREFLIGHT_TIMEOUT="2")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertEqual(self.labels(done.stdout)["api"], "未確認")

    def test_insufficient_role_stops_before_anything_is_made(self):
        self.github_new()
        self.route(
            {
                PULLS: [200, []],
                f"GET {GITHUB}/issues/152": [200, {"title": "Add login"}],
                f"GET {GITHUB}": [200, {"permissions": {"push": False}}],
                **closed_pulls(BRANCH),
            }
        )
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("書き込みの権限が足りない", done.stderr)
        self.assertEqual(self.local_branches(), ["main"])
        self.assertEqual(self.remote_branches(), ["main"])
        self.assertEqual(self.posts(), [])
        self.assertFalse(os.path.isdir(self.wt(BRANCH)))

    def test_insufficient_role_stops_a_resumed_start_before_the_worktree(self):
        # 作りかけ（初期コミットだけ・MR が無い）の再開も、ワークツリーを切る前に止める
        git(self.ws, "branch", BRANCH, "origin/main")
        git(self.ws, "commit-tree", "-p", "main", "-m", "x", "main^{tree}")
        tip = git(
            self.ws,
            "commit-tree",
            "-p",
            "main",
            "-m",
            "chore: #152 の作業を開始する",
            "main^{tree}",
        )
        git(self.ws, "branch", "-f", BRANCH, tip)
        git(self.ws, "push", "-q", self.bare, f"{BRANCH}:refs/heads/{BRANCH}")
        self.route(
            {
                PULLS: [200, []],
                f"GET {GITHUB}": [200, {"permissions": {"push": False}}],
                **closed_pulls(BRANCH),
            }
        )
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("書き込みの権限が足りない", done.stderr)
        self.assertFalse(os.path.isdir(self.wt(BRANCH)))
        self.assertEqual(self.posts(), [])

    # ---- 8. issue と MR のセット

    def mr_branch(self, name="fix-152-other", number=5, **routes):
        git(self.ws, "push", "-q", self.bare, f"main:refs/heads/{name}")
        self.route(
            {
                f"GET {GITHUB}/pulls/{number}": [200, pr(number, name)],
                PULLS: [200, [pr(number, name)]],
                **closed_pulls(name),
                **routes,
            }
        )

    def test_issue_adopting_an_mr_that_does_not_close_it_stops(self):
        self.mr_branch(**{GRAPHQL: [200, closing([])]})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("閉じるリンクを持たない", done.stderr)
        self.assertIn("Closes #152", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))

    def test_mr_with_no_linked_issue_stops(self):
        self.mr_branch(**{GRAPHQL: [200, closing([])]})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("Closes #<番号>", done.stderr)

    def test_mr_with_one_linked_issue_reports_issue_and_mr(self):
        self.mr_branch(**{GRAPHQL: [200, closing([(12, "acme/widgets")])]})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("必須: 提案の frontmatter に issue: 12 と mr: 5 を書く", done.stdout)
        self.assertIn("--issue 12 --mr 5", done.stdout)

    def test_mr_with_a_different_issue_stops(self):
        self.mr_branch(**{GRAPHQL: [200, closing([(12, "acme/widgets")])]})
        done = self.run_sh("--mr", "5", "--issue", "13")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("閉じるリンクを持たない", done.stderr)

    def test_mr_with_two_linked_issues_needs_a_choice(self):
        self.mr_branch(**{GRAPHQL: [200, closing([(12, "acme/widgets"), (13, "acme/widgets")])]})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("acme/widgets#12", done.stdout)
        self.assertIn("acme/widgets#13", done.stdout)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))

    def test_issue_in_the_list_passes_and_order_of_arguments_does_not_matter(self):
        self.mr_branch(**{GRAPHQL: [200, closing([(12, "acme/widgets"), (13, "acme/widgets")])]})
        for args in (("--mr", "5", "--issue", "13"), ("--issue", "13", "--mr", "5")):
            with self.subTest(args=args):
                done = self.run_sh(*args)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("issue: 13 と mr: 5", done.stdout)
        # 一覧に無いものは 1
        self.assertEqual(self.run_sh("--mr", "5", "--issue", "14").returncode, 1)

    def test_issue_forms_are_the_same_issue_ignoring_case(self):
        self.mr_branch(**{GRAPHQL: [200, closing([(12, "acme/widgets")])]})
        for value in ("12", "acme/Widgets#12", "ACME/WIDGETS#12"):
            with self.subTest(value=value):
                done = self.run_sh("--mr", "5", "--issue", value)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("issue: 12 と mr: 5", done.stdout)

    def test_issue_in_another_repository_is_shown_with_its_repository(self):
        self.mr_branch(**{GRAPHQL: [200, closing([(3, "other/repo")])]})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("issue: other/repo#3 と mr: 5", done.stdout)
        self.assertIn("--issue other/repo#3 --mr 5", done.stdout)

    def test_linked_issues_needing_a_sixth_page_stop_with_exit_4(self):
        self.mr_branch(**{GRAPHQL: [200, closing([(12, "acme/widgets")], more=True)]})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("mcp__github__pull_request_read", done.stdout)
        self.assertIn("--mr 5 --issue <issue の番号> --branch", done.stdout)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))

    def test_graphql_errors_and_null_pull_request_are_not_zero_linked_issues(self):
        for payload in (
            {"errors": [{"message": "boom"}], "data": None},
            {"data": {"repository": {"pullRequest": None}}},
        ):
            with self.subTest(payload=payload):
                self.mr_branch(**{GRAPHQL: [200, payload]})
                done = self.run_sh("--mr", "5")
                self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
                self.assertIn("mcp__github__issue_read", done.stdout)
                # 理由が空（「?」）にならない
                self.assertIn("紐づく issue を読めなかった", done.stdout)

    def test_the_guided_rerun_after_an_unreadable_closing_link_succeeds(self):
        # 閉じる issue だけ読めない（GraphQL が errors）。案内の 3 引数で打ち直せば確定する
        self.mr_branch(
            **{
                GRAPHQL: [200, {"errors": [{"message": "boom"}], "data": None}],
                f"GET {GITHUB}/issues/152": [200, {"title": "Add login"}],
            }
        )
        first = self.run_sh("--mr", "5")
        self.assertEqual(first.returncode, 4, first.stdout + first.stderr)
        again = self.run_sh(
            "--mr", "5", "--issue", "152", "--branch", "fix-152-other", "--mcp-checked"
        )  # fmt: skip
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("必須: 提案の frontmatter に issue: 152 と mr: 5 を書く", again.stdout)
        self.assertTrue(os.path.isdir(self.wt("fix-152-other")))

    def test_the_guided_rerun_does_not_pass_an_issue_that_does_not_exist(self):
        self.mr_branch(**{GRAPHQL: [200, {"errors": [{"message": "boom"}], "data": None}]})
        done = self.run_sh(
            "--mr", "5", "--issue", "999", "--branch", "fix-152-other", "--mcp-checked"
        )  # fmt: skip
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("issue #999 が見つからない", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))

    def test_branch_with_no_candidate_on_a_reachable_host_stops(self):
        self.github_new()
        done = self.run_sh(
            "--issue", "152", "--branch", "other-152-x", "--mcp-checked"
        )  # fmt: skip
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("--branch", done.stderr)
        self.assertEqual(self.local_branches(), ["main"])
        self.assertEqual(self.posts(), [])

    def test_a_resumed_start_checks_the_issue_before_the_worktree(self):
        tip = git(
            self.ws,
            "commit-tree",
            "-p",
            "main",
            "-m",
            "chore: #152 の作業を開始する",
            "main^{tree}",
        )
        git(self.ws, "branch", BRANCH, tip)
        git(self.ws, "push", "-q", self.bare, f"{BRANCH}:refs/heads/{BRANCH}")
        # issue 152 は 404（既定の routes に無い）。作りかけでも、実在しない issue の MR は作らない
        self.route({PULLS: [200, []], **closed_pulls(BRANCH)})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("issue #152 が見つからない", done.stderr)
        self.assertFalse(os.path.isdir(self.wt(BRANCH)))
        self.assertEqual(self.posts(), [])

    def test_a_candidate_without_an_mr_is_not_a_success(self):
        tip = git(self.ws, "commit-tree", "-p", "main", "-m", "work", "main^{tree}")
        git(self.ws, "branch", "fix-152-other", tip)
        git(self.ws, "push", "-q", self.bare, "fix-152-other:refs/heads/fix-152-other")
        self.route({PULLS: [200, []]})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("fix-152-other", done.stdout)
        self.assertIn("Closes #152", done.stdout)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))

    def test_mcp_checked_without_branch_stops_with_exit_4(self):
        done = self.run_sh("--issue", "152", "--mr", "7", "--mcp-checked", token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("--branch", done.stdout)

    def test_mcp_checked_with_a_missing_branch_stops(self):
        done = self.run_sh(
            "--issue", "152", "--mr", "7", "--branch", "nope-152-x", "--mcp-checked", token=False
        )
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("nope-152-x", done.stderr)

    def test_branch_that_differs_from_the_mr_source_branch_stops(self):
        self.mr_branch()
        done = self.run_sh(
            "--mr", "5", "--issue", "152", "--branch", "other-152-x", "--mcp-checked"
        )
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("--branch", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))

    def test_help(self):
        done = self.run_sh("--help")
        self.assertEqual(done.returncode, 0)
        self.assertIn("終了コード", done.stdout)


class StartShGitLabTest(StartShTest):
    origin_url = GL_URL

    def gitlab_routes(self, created=True):
        routes = {
            f"GET {GITLAB}": [200, {"id": 42}],
            f"GET {GITLAB}/issues/152/related_merge_requests?per_page=100&page=1": [200, []],
            f"GET {GITLAB}/issues/152": [200, {"title": "Add login"}],
            f"GET {GITLAB}/merge_requests?{MR_QUERY}": [200, []],
            f"GET {GITLAB}/merge_requests?state=merged&source_branch={BRANCH}"
            "&per_page=100&page=1": [200, []],
        }
        if created:
            routes[f"POST {GITLAB}/merge_requests"] = [
                201,
                {"iid": 9, "web_url": "https://gitlab.com/acme/widgets/-/merge_requests/9"},
            ]
        self.route(routes)

    def test_gitlab_new_issue_creates_draft_mr(self):
        self.gitlab_routes()
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            self.fields(done.stdout)["MR"], "https://gitlab.com/acme/widgets/-/merge_requests/9"
        )
        (body,) = self.posts()
        self.assertEqual(body["source_branch"], BRANCH)
        self.assertEqual(body["target_branch"], "main")
        self.assertEqual(body["title"], "Draft: Add login")
        self.assertEqual(body["description"], "Closes #152")
        self.assertIn(BRANCH, self.remote_branches())

    def test_gitlab_unreachable_at_create_has_manual_steps_and_no_mcp(self):
        self.gitlab_routes(created=False)
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("GitLab APIが応答しないか要求を拒み", done.stdout)
        self.assertIn("手で行う手順", done.stdout)
        self.assertIn("Closes #152", done.stdout)
        self.assertNotIn("mcp__github__", done.stdout)
        self.gitlab_routes()
        again = self.run_sh("--issue", "152")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(len(self.posts()), 2)

    def install_glab(self):
        """認証済みのglabの代役。本文付きでContent-Typeが無いと、GitLabと同じくHTTP 415を返す。
        それ以外はcurlの代役（ルート表）へ回す"""
        text = f"""#!/bin/sh
method=GET; ct=""; input=""; path=""
while [ $# -gt 0 ]; do
  case "$1" in
  api) ;;
  --hostname) shift ;;
  --method) method="$2"; shift ;;
  --header) case "$2" in 'Content-Type: application/json') ct=1 ;; esac; shift ;;
  --input) input=1; shift ;;
  *) path="$1" ;;
  esac
  shift
done
if [ -n "$input" ] && [ -z "$ct" ]; then
  cat >/dev/null
  echo '{{"error":"The provided content-type '"''"' is not supported."}}'
  echo 'glab: HTTP 415' >&2
  exit 1
fi
if [ -n "$input" ]; then
  exec '{self.bin}/curl' -X "$method" --data-binary @- "https://gitlab.com/api/v4/$path"
fi
exec '{self.bin}/curl' -X "$method" "https://gitlab.com/api/v4/$path"
"""
        os.chmod(write(os.path.join(self.bin, "glab"), text), 0o755)

    def test_gitlab_glab_sends_content_type_for_create(self):
        self.install_glab()
        self.gitlab_routes()
        done = self.run_sh("--issue", "152", token=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            self.fields(done.stdout)["MR"], "https://gitlab.com/acme/widgets/-/merge_requests/9"
        )
        (body,) = self.posts()
        self.assertEqual(body["title"], "Draft: Add login")

    def test_gitlab_failure_shows_http_status_and_body(self):
        self.gitlab_routes(created=False)
        with open(self.routes, encoding="utf-8") as f:
            routes = json.load(f)
        routes[f"POST {GITLAB}/merge_requests"] = [415, {"error": "unsupported content-type"}]
        self.route(routes)
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("HTTP 415", done.stdout)
        self.assertNotIn("glab を", done.stdout)

    def test_gitlab_unreachable_at_search_has_no_mcp(self):
        done = self.run_sh("--issue", "152", token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("GitLab APIが応答しないか要求を拒み", done.stdout)
        self.assertNotIn("mcp__github__", done.stdout)

    def test_gitlab_issue_not_found_exits_2(self):
        self.gitlab_routes()
        with open(self.routes, encoding="utf-8") as f:
            routes = json.load(f)
        del routes[f"GET {GITLAB}/issues/152"]
        self.route(routes)
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("issue #152 が見つからない", done.stderr)

    def test_gitlab_check_access_levels(self):
        project = f"GET {GITLAB}"
        members = f"GET {GITLAB}/members/all/5"
        cases = (
            # (routes, 終了コード, access の表示)
            ({members: [200, {"access_level": 20}]}, 1, "NG"),
            ({members: [200, {"access_level": 30}]}, 0, "OK"),
            (
                {
                    members: [404, {"message": "x"}],
                    project: [
                        200,
                        {"id": 42, "permissions": {"group_access": {"access_level": 30}}},
                    ],
                },
                0,
                "OK",
            ),
            (
                {f"GET {GL_API}/user": [404, {"message": "x"}], project: [200, {"id": 42}]},
                0,
                "注意",
            ),
        )
        for routes, code, shown in cases:
            with self.subTest(shown=shown, code=code):
                done = self.check(routes)
                self.assertEqual(done.returncode, code, done.stdout + done.stderr)
                self.assertEqual(self.labels(done.stdout)["access"], shown)
        self.assertIn("権限を判定できない", done.stdout)

    def test_gitlab_check_without_a_token_suggests_glab_login(self):
        done = self.check(token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        got = self.labels(done.stdout)
        self.assertEqual((got["api"], got["access"], got["git"]), ("未確認", "省略", "OK"))
        self.assertIn("glab auth login --hostname gitlab.com", done.stdout)

    def test_gitlab_check_times_out_on_a_hanging_glab(self):
        write(os.path.join(self.bin, "glab"), "#!/bin/sh\nsleep 30\n")
        os.chmod(os.path.join(self.bin, "glab"), 0o755)
        done = self.check(token=False, CCNAVI_PREFLIGHT_TIMEOUT="2")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertEqual(self.labels(done.stdout)["api"], "未確認")

    def gitlab_mr(self, **over):
        return {
            "iid": 9,
            "source_branch": "topic/x",
            "source_project_id": 42,
            "target_branch": "main",
            "state": "opened",
            "title": "MR 9",
            "web_url": "https://gitlab.com/acme/widgets/-/merge_requests/9",
            **over,
        }

    def test_gitlab_adopt_checks_target_and_merged_mr(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic/x")
        merged_url = (
            f"GET {GITLAB}/merge_requests?state=merged&source_branch=topic%2Fx&per_page=100&page=1"
        )
        base = {f"GET {GITLAB}": [200, {"id": 42}], merged_url: [200, []]}
        self.route(
            {**base, f"GET {GITLAB}/merge_requests/9": [200, self.gitlab_mr(target_branch="dev")]}
        )
        done = self.run_sh("--mr", "9")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("マージ先は dev", done.stderr)
        self.route(
            {
                **base,
                merged_url: [200, [self.gitlab_mr(iid=3, state="merged")]],
                f"GET {GITLAB}/merge_requests/9": [200, self.gitlab_mr()],
            }
        )
        done = self.run_sh("--mr", "9")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("マージ済み MR（!3）", done.stderr)
        self.route({**base, f"GET {GITLAB}/merge_requests/9": [200, self.gitlab_mr()]})
        done = self.run_sh("--mr", "9")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(git(self.wt("topic-x"), "rev-parse", "--abbrev-ref", "HEAD"), "topic/x")

    def test_gitlab_fork_mr_is_refused(self):
        mr = {
            "iid": 9,
            "source_branch": "topic/x",
            "source_project_id": 77,
            "state": "opened",
            "title": "MR 9",
            "web_url": "https://gitlab.com/acme/widgets/-/merge_requests/9",
        }
        self.route(
            {f"GET {GITLAB}": [200, {"id": 42}], f"GET {GITLAB}/merge_requests/9": [200, mr]}
        )
        done = self.run_sh("--mr", "9")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("フォーク", done.stderr)


# GitLab の派生クラスが GitHub 用のテストを引き継がないように、継承したテストを外す
for _name in [n for n in dir(StartShTest) if n.startswith("test_")]:
    if _name not in StartShGitLabTest.__dict__ and not _name.startswith("test_gitlab"):
        setattr(StartShGitLabTest, _name, None)


if __name__ == "__main__":
    unittest.main()
