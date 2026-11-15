"""ccnavi-start.sh の受入テストの共通の部品（`test_start_sh.py`・`test_start_sh_adopt.py` が読む）。

使い捨てのワークスペースと、origin の代わりの bare リポジトリを組み、sh を外から呼ぶ。
origin の URL は GitHub / GitLab の形のままにする。PATH の先頭に置いた git のスタブが、
fetch / push のときだけ bare リポジトリへ向ける
（`git remote get-url` が書き換えた URL を返すと、ホストを見分けられないため）。
ホストは PATH の先頭の `curl` のスタブが答える（tests/sh/test_branches_sh.py と同じ作り）。
gh / glab は使えないスタブにして、curl とトークンの経路に切り替える。
スタブは POST の本文も記録する。

ホストの応答の読み方・振り分け・引数の誤り・識別子の決まり・止める文・事前確認の判定は、Go の表
（`go/internal/tickets/lifecycle/start/`・`preflight/`・`go/internal/tickets/model/ids/`）が確かめる。
sh のテストは、経路ごとの通しの確認（終了コード・出力・作られた物・ホストへの要求）と、sh の仕事
（ホストとの出入り・段取りの順・打ち直し・片付け・時間の枠）だけを見る。
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

from tests import ROOT, common_sh, exe, put_messages

SHELL = shutil.which("sh") or shutil.which("bash")
REAL_GIT = shutil.which("git")
NEEDED = all(shutil.which(tool) for tool in ("git", "jq"))
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")

EXE = """#!/bin/sh
exec '{exe}' "$@"
"""

# fetch / push のときだけ、origin の URL を bare リポジトリへ向ける git のスタブ。
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
# 出力に漏れていないことを確かめるダミーのトークン。一時ディレクトリ名に含まれにくい綴りにする
DUMMY_TOKEN = "tok-start-sh-test-7f3a91c2e5"
GITLAB = "https://gitlab.com/api/v4/projects/acme%2Fwidgets"
GRAPHQL = "POST https://api.github.com/graphql"
PULLS = f"GET {GITHUB}/pulls?state=open&per_page=100&page=1"
BRANCH = "feature-152-add-login"
CLOSED = "state=closed&head=acme%3A{branch}&per_page=100&page=1"
MR_QUERY = f"state=opened&source_branch={BRANCH}&per_page=100&page=1"
INIT_SUBJECT = "chore: #152 の作業を開始する"


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


def nexts(text):
    """行頭が `次: ` の行。"""
    return [line for line in text.splitlines() if line.startswith("次: ")]


@unittest.skipIf(SHELL is None or not NEEDED, "sh・git・jq のどれかが無い")
class StartShHarness(unittest.TestCase):
    """テストを持たない土台。origin_url で GitHub と GitLab を切り替える。"""

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
        put_messages(self.ws)
        self.exe = write(os.path.join(base, "exe", "ccnavi"), EXE.format(exe=exe.sh_path()))
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
        """ホストのスタブの既定の答え。probe と、書き込みの権限と、紐づく issue。"""
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

    def current_routes(self):
        with open(self.routes, encoding="utf-8") as f:
            return json.load(f)

    def run_sh(self, *args, token=True, cwd=None, **extra):
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        for name in list(env):
            if name in ("GITHUB_TOKEN", "GITLAB_TOKEN", "CLAUDE_PROJECT_DIR") or name.startswith(
                ("GIT_AUTHOR_", "GIT_COMMITTER_")
            ):
                env.pop(name)
        if token:
            env["GITLAB_TOKEN" if self.origin_url == GL_URL else "GITHUB_TOKEN"] = DUMMY_TOKEN
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

    def nothing_made(self, branch=BRANCH):
        """ブランチ・ワークツリー・push・MR の作成のどれもしていない。"""
        self.assertEqual(self.local_branches(), ["main"])
        self.assertEqual(self.remote_branches(), ["main"])
        self.assertFalse(os.path.isdir(self.wt(branch)))
        self.assertEqual(self.posts(), [])

    def fields(self, text):
        out = {}
        for line in text.splitlines():
            for key in ("ワークツリー", "ブランチ", "MR", "対象リポジトリ"):
                if line.startswith(key + ": "):
                    out[key] = line[len(key) + 2 :]
        return out

    def initial_branch(self, name=BRANCH):
        """作りかけ（初期コミットだけ）のブランチを手元と origin に置く。"""
        tip = git(self.ws, "commit-tree", "-p", "main", "-m", INIT_SUBJECT, "main^{tree}")
        git(self.ws, "branch", "-f", name, tip)
        git(self.ws, "push", "-q", self.bare, f"{name}:refs/heads/{name}")

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

    def no_identity(self, **extra):
        """作成者を推定させない構成（useConfigOnly が無いと、FQDN のある機械で推定されて通る）。"""
        git(self.ws, "config", "--unset", "user.name")
        git(self.ws, "config", "--unset", "user.email")
        empty = os.path.join(self._tmp.name, "home")
        os.makedirs(empty, exist_ok=True)
        env = {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "HOME": empty,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "user.useConfigOnly",
            "GIT_CONFIG_VALUE_0": "true",
        }
        return {**env, **extra}

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

    # ---- GitHub のスタブ

    def github_new(self, title="Add login", created=True, branch=BRANCH):
        """GitHub で新規作成が通るスタブ。"""
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

    def github_new_issue(self, found=None, created=True):
        """github_new に、open の issue の一覧と issue の作成を足す。"""
        routes = {
            f"GET {GITHUB}/issues?state=open&per_page=100&page=1": [200, found or []],
        }
        if created:
            routes[f"POST {GITHUB}/issues"] = [
                201,
                {"number": 152, "html_url": "https://github.com/acme/widgets/issues/152"},
            ]
        self.github_new()
        self.route({**self.current_routes(), **routes})

    def mr_branch(self, name="fix-152-other", number=5, **routes):
        """origin に name を置き、その MR（number）を答える。"""
        git(self.ws, "push", "-q", self.bare, f"main:refs/heads/{name}")
        self.route(
            {
                f"GET {GITHUB}/pulls/{number}": [200, pr(number, name)],
                PULLS: [200, [pr(number, name)]],
                **closed_pulls(name),
                **routes,
            }
        )

    def adopt(self, branch, mr=None, **routes):
        """origin に branch を置いて --mr 5 を打つ。mr は pr() の上書き。"""
        git(self.ws, "push", "-q", self.bare, f"main:refs/heads/{branch}")
        body = {**pr(5, branch), **(mr or {})}
        self.route({f"GET {GITHUB}/pulls/5": [200, body], **closed_pulls(branch), **routes})
        return self.run_sh("--mr", "5")

    # ---- GitLab のスタブ

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
