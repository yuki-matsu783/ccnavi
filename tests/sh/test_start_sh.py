"""ccnavi-start.sh の受入テスト。

使い捨てのワークスペースと、origin の代わりの bare リポジトリを組み、sh を外から呼ぶ。
origin の URL は GitHub / GitLab の形のままにする。PATH の先頭に置いた git の代役が、
fetch / push のときだけ bare リポジトリへ向ける
（`git remote get-url` が書き換えた URL を返すと、ホストを見分けられないため）。
ホストは PATH の先頭の `curl` の代役が答える（tests/sh/test_branches_sh.py と同じ作り）。
gh / glab は使えない代役にして、curl とトークンの経路に落とす。代役は POST の本文も記録する。

見るのは次のとおり。

1. 候補 0 件（新規作成）・1 件（続ける）・複数（一覧を出して終了コード 3）
2. ホストを見られないとき（GitHub は MCP の案内と終了コード 4、手元の候補が 1 件なら続ける）
3. フォークの MR、`/` を含むブランチ、--dry-run
4. 途中で落ちて打ち直したときに、重複を作らず続きから進むこと
5. GitLab の経路
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
status, payload = routes.get(f"{method} {url}", [404, {"message": "Not Found"}])
if status >= 400:
    sys.stderr.write(f"curl: (22) The requested URL returned error: {status}\n")
    sys.exit(22)
sys.stdout.write(json.dumps(payload))
"""

GH_URL = "https://github.com/acme/widgets.git"
GL_URL = "https://gitlab.com/acme/widgets.git"
GITHUB = "https://api.github.com/repos/acme/widgets"
GITLAB = "https://gitlab.com/api/v4/projects/acme%2Fwidgets"
PULLS = f"GET {GITHUB}/pulls?state=open&per_page=100&page=1"
BRANCH = "feature-152-add-login"
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
        for name in ("ccnavi-start.sh", "ccnavi-branches.sh", "ccnavi-git.sh", *common_sh(SH_DIR)):
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

    def route(self, routes):
        write(self.routes, json.dumps(routes))

    def run_sh(self, *args, token=True, cwd=None, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        for name in ("GITHUB_TOKEN", "GITLAB_TOKEN", "CLAUDE_PROJECT_DIR"):
            env.pop(name, None)
        if token:
            env["GITLAB_TOKEN" if self.origin_url == GL_URL else "GITHUB_TOKEN"] = "t0k"
        env.update(
            {
                "PATH": os.pathsep.join([self.bin, os.environ.get("PATH", "")]),
                "CCNAVI_BIN_PATH": self.exe,
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
            if line.startswith("POST ") and i + 1 < len(lines) and lines[i + 1].startswith("BODY ")
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
            for key in ("ワークツリー", "ブランチ", "MR"):
                if line.startswith(key + ": "):
                    out[key] = line[len(key) + 2 :]
        return out

    # GitHub で新規作成が通る代役
    def github_new(self, title="Add login", created=True):
        routes = {
            PULLS: [200, []],
            f"GET {GITHUB}/issues/152": [200, {"title": title}],
            f"GET {GITHUB}/pulls?state=open&head=acme:{BRANCH}": [200, []],
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

    def test_second_run_continues_without_duplicates(self):
        self.github_new()
        self.assertEqual(self.run_sh("--issue", "152").returncode, 0)
        self.route({PULLS: [200, [pr(7, BRANCH, body="Closes #152")]]})
        before = self.posts()
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.fields(done.stdout)["ブランチ"], BRANCH)
        self.assertEqual(self.fields(done.stdout)["MR"], "https://github.com/acme/widgets/pull/7")
        self.assertEqual(self.posts(), before)
        self.assertEqual(self.local_branches().count(BRANCH), 1)

    def test_single_local_candidate_gets_a_worktree(self):
        git(self.ws, "branch", "fix-152-other", "origin/main")
        self.route({PULLS: [200, [pr(3, "fix-152-other", body="Closes #152")]]})
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
        self.route({PULLS: [200, []]})
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
            "何も作らずに止めた",
        ):
            self.assertIn(word, done.stdout)
        self.assertEqual(self.local_branches(), ["main"])
        self.assertFalse(os.path.isdir(os.path.join(self.ws, ".claude", "worktrees")))

    def test_unchecked_host_with_one_local_candidate_continues_and_says_so(self):
        git(self.ws, "branch", "fix-152-other", "origin/main")
        done = self.run_sh("--issue", "152", token=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ホストは見ていない", done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(self.wt("fix-152-other")))

    def test_title_unreadable_prints_issue_read_guidance(self):
        # API が落ちた（500）。ホストには届いていないので MCP の案内と終了コード 4
        self.route({PULLS: [200, []], f"GET {GITHUB}/issues/152": [500, {"message": "boom"}]})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("mcp__github__issue_read", done.stdout)
        self.assertIn("--slug <語> --title", done.stdout)
        self.assertEqual(self.local_branches(), ["main"])

    def test_title_unreadable_but_slug_given_goes_on_with_a_provisional_title(self):
        self.route(
            {
                PULLS: [200, []],
                f"GET {GITHUB}/issues/152": [500, {"message": "boom"}],
                f"GET {GITHUB}/pulls?state=open&head=acme:feature-152-login": [200, []],
                f"POST {GITHUB}/pulls": [
                    201,
                    {"number": 8, "html_url": "https://github.com/acme/widgets/pull/8"},
                ],
            }
        )
        done = self.run_sh("--issue", "152", "--slug", "login")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.posts()[0]["title"], "#152 login")

    def test_non_ascii_title_asks_for_slug(self):
        self.github_new(title="ログイン画面")
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("--slug", done.stderr)
        self.assertEqual(self.local_branches(), ["main"])

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
            "同じコマンドを打ち直す",
        ):
            self.assertIn(word, done.stdout)
        self.assertIn(BRANCH, self.remote_branches())
        self.assertTrue(os.path.isdir(self.wt(BRANCH)))
        # 打ち直し: MR が無くホストが答えるなら、続きの MR 作成から進む（初期コミットは重ねない）
        self.github_new()
        again = self.run_sh("--issue", "152")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.fields(again.stdout)["MR"], "https://github.com/acme/widgets/pull/7")
        self.assertEqual(git(self.ws, "rev-list", "--count", f"origin/main..{BRANCH}"), "1")
        # POST は落ちた 1 回と、打ち直しの 1 回だけ（重複して作っていない）
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
        # 案内どおりの流れ。search 段 → --mcp-checked で打ち直す → create 段
        # → MCP で PR を作った想定 → 案内のコマンドを打ち直す
        search = self.run_sh("--issue", "152", "--slug", "login", token=False)
        self.assertEqual(search.returncode, 4, search.stdout + search.stderr)
        _, args = self.guided_args(search.stdout, "打ち直す: ")
        self.assertIn("--mcp-checked", args)
        create = self.run_sh(*args, token=False)
        self.assertEqual(create.returncode, 4, create.stdout + create.stderr)
        self.assertIn("mcp__github__create_pull_request", create.stdout)
        self.assertEqual(self.posts(), [])
        cmd, args = self.guided_args(create.stdout, "打ち直す: ")
        self.assertNotIn("--mcp-checked", cmd)
        self.assertEqual(args[:4], ["--issue", "152", "--slug", "login"])
        # エージェントが MCP で PR を作った。ホストは見られないまま、案内のコマンドを打つ
        done = self.run_sh(*args, token=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(
            self.fields(done.stdout)["ワークツリー"].endswith("worktrees/feature-152-login")
        )
        self.assertEqual(self.posts(), [])

    def test_guided_command_keeps_quoting(self):
        title = 'It\'s $HOME  "x" `a`'
        first = self.run_sh(
            "--issue", "152", "--slug", "login", "--title", title, "--mcp-checked", token=False
        )
        self.assertEqual(first.returncode, 4, first.stdout + first.stderr)
        cmd, args = self.guided_args(first.stdout, "打ち直す: ")
        self.assertEqual(args, ["--issue", "152", "--slug", "login", "--title", title])
        self.assertIn("'It'\\''s $HOME  \"x\" `a`'", cmd)
        again = self.run_sh(*args, token=False)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)

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

    def test_mr_with_slash_branch_uses_identifier_and_switches(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic/x")
        self.route({f"GET {GITHUB}/pulls/5": [200, pr(5, "topic/x")]})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = self.fields(done.stdout)
        self.assertEqual(got["ブランチ"], "topic/x")
        self.assertTrue(got["ワークツリー"].endswith("worktrees/topic-x"))
        self.assertEqual(git(self.wt("topic-x"), "rev-parse", "--abbrev-ref", "HEAD"), "topic/x")
        self.assertIn("switch", done.stdout)

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

    def test_prefix_and_slug(self):
        self.route(
            {
                PULLS: [200, []],
                f"GET {GITHUB}/issues/152": [200, {"title": "Add login"}],
                f"GET {GITHUB}/pulls?state=open&head=acme:fix-152-login": [200, []],
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
            ("--issue", "1", "--mr", "2"),
            ("--issue", "1", "--slug", "Bad Slug"),
            ("--mr", "1", "--slug", "x"),
            ("--nope",),
        ):
            with self.subTest(args=args):
                self.assertEqual(self.run_sh(*args).returncode, 2)

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
        self.assertIn("GitLabのAPIに届かず", done.stdout)
        self.assertIn("手で行う手順", done.stdout)
        self.assertIn("Closes #152", done.stdout)
        self.assertNotIn("mcp__github__", done.stdout)
        self.gitlab_routes()
        again = self.run_sh("--issue", "152")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(len(self.posts()), 2)

    def test_gitlab_unreachable_at_search_has_no_mcp(self):
        done = self.run_sh("--issue", "152", token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("GitLabのAPIに届かず", done.stdout)
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
