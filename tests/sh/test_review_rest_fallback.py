"""GraphQL が 403 の環境（Claude Code のクラウド）で、GitHub の 4 か所が REST に替わること。

スレッドの取得（`threads`）・Draft 外し（`undraft`）・指摘への返信と解決（`accept_reply`・
`accept_resolve`）は、GraphQL を先に試し、使えない 403 のときだけ REST（`/ccr/` の道）に替える。
GraphQL が通る環境の動きは変えない。認証の失敗（401）と別の理由の 403 は替えない。

確かめるのは次の点。

- threads: GraphQL が通れば REST（`ccr/`・`pulls/<N>/comments`）を呼ばない。403 なら REST の応答から
  従来と同じ形の JSON を組む。返信が重なったスレッドでは、last_* が最後のコメントを指す
- confirm: その JSON を実行ファイルに渡す。未解決のスレッドが 1 件あれば止まり、解決済みなら通る
- undraft: 403 で `ccr/ready_for_review` を呼び、`draft` を読んで返す
- decide の返信と解決: 数字の id は REST、node id は GraphQL
- 401・別の理由の 403 は REST に落とさず失敗する。REST も失敗したら、拒否される手順を案内に書かない

ホストは `github_host.py` の代役。`FAKE_GITHUB_GRAPHQL` で GraphQL の断り方を選ぶ。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests import ROOT, common_sh
from tests.sh import github_host
from tests.sh.test_review_host_fixture import conclusion

SHELL = shutil.which("sh") or shutil.which("bash")
NEEDED = all(shutil.which(tool) for tool in ("git", "jq"))
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
SCRIPTS = ("ccnavi-review.sh", "ccnavi-user-decide.sh", "ccnavi-user-lib-review.sh")

# 実行ファイルの代役。`--result` で渡された JSON を、そのまま残す。
EXE = """#!/bin/sh
case " $* " in
*" --version --json "*)
  printf '{{"compat": {compat}, "flags": ["--accept-unresolved", "--actor", "--via"]}}\\n'
  exit 0 ;;
*" --version "*) printf 'compat: {compat}\\n'; exit 0 ;;
*" write-and-push family "*) printf 'write-and-push 1\\nfamily i0001\\ntarget no\\n'; exit 0 ;;
esac
prev=""
for a in "$@"; do
  case "$prev" in
  --result) cp "$a" '{copy}' ;;
  --accept-plan) cp "$STUB_PLAN" "$a" ;;
  --choose-out) printf '{{"choices": {{}}, "digest": "d"}}\\n' >"$a" ;;
  esac
  prev="$a"
done
exit 0
"""

# REST の代わりの道の見本。スレッドは 2 つ。
#   101: 未解決。101（最初の指摘）→ 102（返信）→ 103（指摘した人の再返信）の 3 つ。
#   201: 解決済み。
THREADS = [
    {
        "resolved": False,
        "outdated": False,
        "path": "src/app.py",
        "line": 8,
        "comment_ids": [101, 102, 103],
    },
    {"resolved": True, "outdated": True, "path": "README.md", "line": None, "comment_ids": [201]},
]
URL = "https://github.com/acme/widgets/pull/42"


def comment(cid, login, at, body, **more):
    return {
        "id": cid,
        "body": body,
        "html_url": f"{URL}#discussion_r{cid}",
        "created_at": at,
        "user": {"login": login},
        "path": "src/app.py",
        "line": 8,
        **more,
    }


COMMENTS = [
    comment(101, "reviewer", "2026-10-01T00:00:00Z", "直してください"),
    comment(102, "author", "2026-10-01T01:00:00Z", "直しました", in_reply_to_id=101),
    comment(103, "reviewer", "2026-10-01T02:00:00Z", "もう 1 点あります", in_reply_to_id=101),
    comment(201, "reviewer", "2026-10-01T03:00:00Z", "済み", path="README.md", line=None),
    comment(301, "someone", "2026-10-01T04:00:00Z", "このスレッドとは別の PR のコメント"),
]
EXPECTED = [
    {
        "id": "101",
        "resolved": False,
        "url": f"{URL}#discussion_r101",
        "path": "src/app.py",
        "line": 8,
        "body": "直してください",
        "created_at": "2026-10-01T00:00:00Z",
        "last_id": "103",
        "last_at": "2026-10-01T02:00:00Z",
        "last_author": "reviewer",
    },
    {
        "id": "201",
        "resolved": True,
        "url": f"{URL}#discussion_r201",
        "path": "README.md",
        "line": 0,
        "body": "済み",
        "created_at": "2026-10-01T03:00:00Z",
        "last_id": "201",
        "last_at": "2026-10-01T03:00:00Z",
        "last_author": "reviewer",
    },
]


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


@unittest.skipIf(SHELL is None or not NEEDED, "sh・git・jq のどれかが無い")
class RestFallbackTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = self._tmp.name
        self.ws = os.path.join(tmp, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in (*SCRIPTS, *common_sh(SH_DIR)):
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        git = ["git", "-C", self.ws, "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run(["git", "init", "-q", "-b", "i0001", self.ws], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "seed"], check=True)
        remote = "https://github.com/acme/widgets.git"
        subprocess.run([*git, "remote", "add", "origin", remote], check=True)
        self.bin = os.path.join(tmp, "bin")
        github_host.install(self.bin, sys.executable)
        # gh の経路（本文を返す）。curl は同じ PATH に置かない
        self.ghbin = os.path.join(tmp, "ghbin")
        write(
            os.path.join(self.ghbin, "gh"),
            f"#!/bin/sh\nexec '{sys.executable}' '{github_host.__file__}' gh \"$@\"\n",
        )
        os.chmod(os.path.join(self.ghbin, "gh"), 0o755)
        self.copy = os.path.join(tmp, "result-copy.json")
        self.log = os.path.join(tmp, "calls.log")
        self.muts = os.path.join(tmp, "mutations.jsonl")
        self.rest = write(
            os.path.join(tmp, "rest.json"),
            json.dumps(
                {"review_threads": THREADS, "comments": COMMENTS, "body": "", "draft": True}
            ),
        )
        from ccnavi.entry import version

        self.exe = write(
            os.path.join(tmp, "exe", "ccnavi"), EXE.format(compat=version.COMPAT, copy=self.copy)
        )
        os.chmod(self.exe, 0o755)

    def env(self, graphql="", transport="curl", **extra):
        path = self.bin if transport == "curl" else self.ghbin
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.pop("GITLAB_TOKEN", None)
        env.update(
            {
                "GITHUB_TOKEN": "t0k",
                "PATH": os.pathsep.join([path, os.environ.get("PATH", "")]),
                "FAKE_GITHUB_SCENE": "resolved",
                "FAKE_GITHUB_STATE": os.path.join(self._tmp.name, "comments.json"),
                "FAKE_GITHUB_REST": self.rest,
                "FAKE_GITHUB_LOG": self.log,
                "FAKE_GITHUB_MUTATIONS": self.muts,
                "FAKE_GITHUB_DRAFT_STATE": os.path.join(self._tmp.name, "draft.json"),
                "CCNAVI_ENTRY_PATH": self.exe,
                **extra,
            }
        )
        if graphql:
            env["FAKE_GITHUB_GRAPHQL"] = graphql
        return env

    def run_sh(self, args, **kw):
        return subprocess.run(
            [SHELL, *args],
            cwd=self.ws,
            env=self.env(**kw),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def review(self, *args, **kw):
        return self.run_sh(
            [os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-review.sh"), *args], **kw
        )

    def driver(self, body, **kw):
        """ccnavi-review.sh と同じ支度をして、関数を直接呼ぶ（undraft・accept_*）。"""
        script = "\n".join(
            [
                "set -eu",
                ". .ccnavi/scripts/ccnavi-common.sh",
                "review_label=ccnavi-test",
                "ccnavi_load ccnavi-common-review.sh",
                "ccnavi_load ccnavi-user-lib-review.sh",
                "sub=test",
                "review_place",
                "review_exe || review_no_bin",
                "review_host",
                'mkdir -p "$state"',
                "eval \"$(sed -n '/^strip_draft() {/,/^}/p;/^undraft() {/,/^}/p'"
                ' .ccnavi/scripts/ccnavi-review.sh)"',
                body,
            ]
        )
        # dirname $0 で共通部を探すので、配った sh と同じ場所に置いて打つ
        driver = write(os.path.join(self.ws, ".ccnavi", "scripts", "driver-test.sh"), script + "\n")
        return self.run_sh([driver], **kw)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            return f.read().splitlines()

    def mutations(self):
        if not os.path.exists(self.muts):
            return []
        with open(self.muts, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    # ---- threads

    def test_when_graphql_works_rest_is_not_called(self):
        done = self.review("fetch")
        self.assertEqual(done.returncode, 0, done.stderr)
        calls = self.calls()
        self.assertIn("POST /graphql", calls)
        self.assertEqual(
            [c for c in calls if "/ccr/" in c or "/comments" in c and "issues" not in c], []
        )

    def test_when_graphql_is_blocked_rest_builds_the_same_shape(self):
        for transport in ("curl", "gh"):
            with self.subTest(transport=transport):
                done = self.review("fetch", graphql="blocked", transport=transport)
                self.assertEqual(done.returncode, 0, done.stderr)
                got = json.loads(done.stdout)
                self.assertEqual(got["threads"], EXPECTED)
                self.assertEqual(got["mr"]["number"], 42)
                # GraphQL を先に試し、そのあとで REST
                calls = self.calls()
                self.assertLess(
                    calls.index("POST /graphql"),
                    calls.index("GET /repos/acme/widgets/pulls/42/ccr/review_threads"),
                )
                self.assertIn(
                    "GET /repos/acme/widgets/pulls/42/comments?per_page=100&page=1", calls
                )
                # 失敗の文面（GraphQL の 403）は出さない
                self.assertNotIn("が失敗した", done.stderr)

    def test_a_reply_on_a_thread_sets_the_last_fields_to_the_last_comment(self):
        done = self.review("fetch", graphql="blocked")
        thread = json.loads(done.stdout)["threads"][0]
        self.assertEqual(
            (thread["last_id"], thread["last_at"], thread["last_author"]),
            ("103", "2026-10-01T02:00:00Z", "reviewer"),
        )
        # 最初のコメントは変わらない（url・本文・作成の時刻）
        self.assertEqual(thread["created_at"], "2026-10-01T00:00:00Z")

    # ---- confirm

    def result_copy(self):
        with open(self.copy, encoding="utf-8") as f:
            return json.load(f)

    def test_confirm_hands_the_threads_and_one_unresolved_thread_stops_it(self):
        done = self.review("confirm", "--phase", "1", graphql="blocked")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = self.result_copy()
        self.assertEqual(conclusion(got)["unresolved"], ["101"])

    def test_confirm_with_every_thread_resolved_has_nothing_unresolved(self):
        resolved = [{**t, "resolved": True} for t in THREADS]
        write(
            self.rest,
            json.dumps({"review_threads": resolved, "comments": COMMENTS, "draft": True}),
        )
        done = self.review("confirm", "--phase", "1", graphql="blocked")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(conclusion(self.result_copy())["unresolved"], [])

    # ---- 失敗の分け方

    def test_401_does_not_fall_back_to_rest(self):
        for transport in ("curl", "gh"):
            with self.subTest(transport=transport):
                if os.path.exists(self.log):
                    os.remove(self.log)
                done = self.review("fetch", graphql="unauthorized", transport=transport)
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("が失敗した", done.stderr)
                self.assertEqual([c for c in self.calls() if "/ccr/" in c], [])

    def test_another_403_through_gh_does_not_fall_back_to_rest(self):
        done = self.review("fetch", graphql="forbidden", transport="gh")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("Resource not accessible", done.stderr)
        self.assertEqual([c for c in self.calls() if "/ccr/" in c], [])

    def test_when_rest_fails_too_the_hint_does_not_ask_for_a_json(self):
        """GraphQL は 403、/ccr/ は 404。案内に、拒否される手順（JSON を作らせる）を書かない。"""
        for transport in ("curl", "gh"):
            with self.subTest(transport=transport):
                done = self.review(
                    "fetch", graphql="blocked", transport=transport, FAKE_GITHUB_NO_CCR="1"
                )
                self.assertNotEqual(done.returncode, 0)
                self.assertIn("REST の代わりの道", done.stderr)
                self.assertIn("が失敗した", done.stderr)
                for word in ("--result", "JSON", "MCP"):
                    self.assertNotIn(word, done.stderr)

    # ---- undraft

    def test_undraft_when_blocked_calls_ready_for_review_and_returns_false(self):
        for transport in ("curl", "gh"):
            with self.subTest(transport=transport):
                done = self.driver("undraft 42", graphql="blocked", transport=transport)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(done.stdout.strip(), "false")
                rest = [m["rest"] for m in self.mutations()]
                self.assertIn("POST pulls/42/ccr/ready_for_review", rest)
                # 題の Draft: 除去の PATCH は今のまま
                self.assertIn("PATCH /repos/acme/widgets/pulls/42", self.calls())

    def test_undraft_when_graphql_works_does_not_call_rest(self):
        done = self.driver("undraft 42")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(), "false")
        self.assertEqual([c for c in self.calls() if "/ccr/" in c], [])
        self.assertTrue(
            any("markPullRequestReadyForReview" in m.get("query", "") for m in self.mutations())
        )

    def test_undraft_when_rest_fails_too_says_so_without_asking_for_a_json(self):
        for transport in ("curl", "gh"):
            with self.subTest(transport=transport):
                done = self.driver(
                    "undraft 42", graphql="blocked", transport=transport, FAKE_GITHUB_NO_CCR="1"
                )
                self.assertNotEqual(done.returncode, 0)
                self.assertEqual(done.stdout.strip(), "")
                self.assertIn("REST の代わりの道", done.stderr)
                self.assertIn("Draft の解除", done.stderr)
                for word in ("--result", "JSON", "MCP"):
                    self.assertNotIn(word, done.stderr)

    def test_undraft_401_fails_without_rest(self):
        done = self.driver("undraft 42", graphql="unauthorized")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual([c for c in self.calls() if "/ccr/" in c], [])

    # ---- decide の返信と解決

    def accept(self, ids, **kw):
        plan = write(
            os.path.join(self._tmp.name, "plan.json"),
            json.dumps(
                {
                    "version": 1,
                    "digest": "d",
                    "threads": [
                        {"id": i, "key": f"k{i}", "reply": "<!-- ccnavi:accept -->\n受け入れる"}
                        for i in ids
                    ],
                }
            ),
        )
        return subprocess.run(
            [
                SHELL,
                os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-user-decide.sh"),
                "1",
                "--choices",
                "{}",
                "--digest",
                "d",
            ],
            cwd=self.ws,
            env=self.env(STUB_PLAN=plan, **kw),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_decide_uses_rest_replies_and_resolve_for_comment_ids(self):
        done = self.accept(["101"], graphql="blocked")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        sent = [(m["rest"], m["body"]) for m in self.mutations()]
        self.assertEqual(
            [s[0] for s in sent],
            ["POST pulls/42/comments/101/replies", "POST pulls/42/ccr/comments/101/resolve"],
        )
        self.assertTrue(sent[0][1]["body"].startswith("<!-- ccnavi:accept -->"))
        # GraphQL の mutation は打たない
        self.assertEqual([m for m in self.mutations() if "query" in m], [])

    def test_decide_keeps_using_graphql_for_node_ids(self):
        """GraphQL が通る環境で取った node id は、GraphQL の mutation で書く（REST は呼ばない）。"""
        done = self.accept(["PRRT_kwDOAbCdEs5AAAAB"])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        sent = self.mutations()
        self.assertEqual(len(sent), 2)
        self.assertTrue(all("query" in m for m in sent))
        self.assertEqual([c for c in self.calls() if "/ccr/" in c or "/replies" in c], [])

    def test_decide_with_a_node_id_stays_on_graphql_even_when_blocked(self):
        """403 のときも、node id を REST の経路に使わない（id の形が道を決める。混ぜない）。"""
        done = self.accept(["PRRT_kwDOAbCdEs5AAAAB"], graphql="blocked")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual([m for m in self.mutations() if "rest" in m], [])


if __name__ == "__main__":
    unittest.main()
