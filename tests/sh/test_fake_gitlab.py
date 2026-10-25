"""GitLab の代役（`tests/sh/fake_gitlab.py`）を関数で起こして止める。

見るのは次の 5 つ。

1. 起動して要求が通り、止めたあとはつながらない
2. keep-alive の接続を開いたままでも `stop()` が返る
3. 2 つ同時に立てても、状態ファイルは干渉しない
4. `skew` を変えると、返す時刻が変わる
5. 実物の `ccnavi-review.sh fetch` が、`GITLAB_TOKEN` と代役の origin から、
   curl 経路で模擬の MR とスレッドを読める（`glab` は失敗する代役を前に置く）
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta

from ccnavi.entry import version
from tests import common_sh
from tests.sh import fake_gitlab
from tests.sh.test_review_host_fixture import REVIEW_SH, SH_DIR, STUB, write

SHELL = shutil.which("sh") or shutil.which("bash")
NEEDED = all(shutil.which(tool) for tool in ("git", "jq", "curl"))

PROJECT = "ns/proj"
ENC = "ns%2Fproj"


def state(**extra) -> dict:
    return {"project": PROJECT, "seq": 100, "issues": [], "mrs": [], **extra}


def call(server, method, path, body=None, token=None):
    """1 回だけ叩いて (状態, JSON) を返す。接続は閉じる。"""
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        headers = {"PRIVATE-TOKEN": server.token if token is None else token}
        raw = json.dumps(body) if body is not None else None
        conn.request(method, path, body=raw, headers=headers)
        res = conn.getresponse()
        return res.status, json.loads(res.read() or b"null")
    finally:
        conn.close()


class FakeGitlabTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def start(self, name="state.json", **kw):
        kw.setdefault("initial_state", state())
        server = fake_gitlab.start(os.path.join(self.tmp, name), **kw)
        self.addCleanup(server.stop)
        return server

    def test_it_serves_and_stops(self):
        server = self.start()
        self.assertGreater(server.port, 0)
        self.assertEqual(server.url, f"http://127.0.0.1:{server.port}")
        self.assertEqual(server.token, fake_gitlab.TOKEN)
        code, got = call(server, "GET", "/api/v4/version")
        self.assertEqual(code, 200)
        self.assertIn("version", got)
        code, got = call(server, "GET", f"/api/v4/projects/{ENC}")
        self.assertEqual((code, got["path_with_namespace"]), (200, PROJECT))
        self.assertEqual(call(server, "GET", f"/api/v4/projects/{ENC}", token="x")[0], 401)
        server.stop()
        with self.assertRaises(OSError):
            http.client.HTTPConnection("127.0.0.1", server.port, timeout=2).request("GET", "/")
        server.stop()  # 2 回目は何もしない

    def test_the_access_log_keeps_the_method_and_path(self):
        server = self.start()
        call(server, "GET", "/api/v4/version")
        call(server, "GET", f"/api/v4/projects/{ENC}")
        self.assertEqual(server.requests, ["GET /api/v4/version", f"GET /api/v4/projects/{ENC}"])

    def test_the_initial_state_is_written_before_it_starts(self):
        path = os.path.join(self.tmp, "written.json")
        server = self.start("written.json", initial_state=state(seq=7))
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["seq"], 7)
        self.assertEqual(server.port, server.server_address[1])

    def test_without_a_state_file_it_refuses_to_start(self):
        with self.assertRaises(FileNotFoundError):
            fake_gitlab.start(os.path.join(self.tmp, "none.json"))

    def test_stop_returns_while_a_keep_alive_connection_is_open(self):
        server = self.start()
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", "/api/v4/version", headers={"Connection": "keep-alive"})
        conn.getresponse().read()
        # 接続は閉じない。2 本目の要求を待つスレッドがサーバに残っている
        done = threading.Event()
        threading.Thread(target=lambda: (server.stop(), done.set()), daemon=True).start()
        self.assertTrue(done.wait(5), "stop() が keep-alive の接続を待って返らない")
        # 開いていた接続も切れている
        with self.assertRaises((OSError, http.client.HTTPException)):
            conn.request("GET", "/api/v4/version")
            conn.getresponse().read()

    def test_the_server_does_not_wait_for_handler_threads(self):
        # stop が返る理由を固定する。block_on_close が True だと、接続のスレッドの終わりを待つ
        server = self.start()
        self.assertFalse(server.block_on_close)
        self.assertTrue(server.daemon_threads)

    def test_two_servers_do_not_interfere(self):
        a = self.start("a.json", initial_state=state(project="a/one"))
        b = self.start("b.json", initial_state=state(project="b/two"))
        self.assertNotEqual(a.port, b.port)
        code, made = call(a, "POST", "/api/v4/projects/a%2Fone/merge_requests", {"title": "only-a"})
        self.assertEqual(code, 201)
        self.assertEqual(made["title"], "only-a")
        # b には届かず、プロジェクトの名前も別
        self.assertEqual(call(b, "GET", "/api/v4/projects/b%2Ftwo/merge_requests")[1], [])
        self.assertEqual(call(b, "GET", "/api/v4/projects/a%2Fone")[0], 404)
        with open(os.path.join(self.tmp, "a.json"), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["mrs"]), 1)
        with open(os.path.join(self.tmp, "b.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["mrs"], [])
        self.assertIsNot(a.lock, b.lock)
        # 片方を止めても、もう片方は動く
        a.stop()
        self.assertEqual(call(b, "GET", "/api/v4/version")[0], 200)

    def created_offset(self, server) -> float:
        _, made = call(server, "POST", f"/api/v4/projects/{ENC}/merge_requests", {"title": "t"})
        stamp = datetime.fromisoformat(made["created_at"].replace("Z", "+00:00"))
        return (stamp - datetime.now(UTC)).total_seconds()

    def test_skew_moves_the_clock(self):
        default = self.created_offset(self.start("d.json"))
        self.assertAlmostEqual(default, 180, delta=30)
        ahead = self.created_offset(self.start("p.json", skew=timedelta(hours=1)))
        self.assertAlmostEqual(ahead, 3600, delta=30)
        behind = self.created_offset(self.start("n.json", skew=-600))
        self.assertAlmostEqual(behind, -600, delta=30)
        zero = self.created_offset(self.start("z.json", skew=0))
        self.assertAlmostEqual(zero, 0, delta=30)


def seeded() -> dict:
    note = {
        "id": 101,
        "body": "ここを直してください",
        "resolvable": True,
        "resolved": False,
        "created_at": "2026-10-25T00:00:00Z",
        "author": {"id": 2, "username": "reviewer"},
        "position": {"new_path": "a.txt", "new_line": 3},
    }
    mr = {
        "iid": 1,
        "state": "opened",
        "source_branch": "i0001",
        "target_branch": "main",
        "title": "i0001",
        "web_url": f"http://127.0.0.1/{PROJECT}/-/merge_requests/1",
        "discussions": [{"id": "d101", "individual_note": False, "notes": [note]}],
        "reviewers": [],
        "notes": [],
        "created_at": "2026-10-25T00:00:00Z",
    }
    return state(mrs=[mr])


@unittest.skipIf(SHELL is None or not NEEDED, "sh・git・jq・curl のどれかが無い")
class ReviewShReadsTheFakeTest(unittest.TestCase):
    """実コードの ccnavi-review.sh が、glab を通らず curl で代役の MR とスレッドを読む。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = self._tmp.name
        self.server = fake_gitlab.start(os.path.join(tmp, "state.json"), initial_state=seeded())
        self.addCleanup(self.server.stop)
        self.ws = os.path.join(tmp, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in (*REVIEW_SH, *common_sh(SH_DIR)):
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        git = ["git", "-C", self.ws, "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run(["git", "init", "-q", "-b", "i0001", self.ws], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "seed"], check=True)
        origin = f"http://127.0.0.1:{self.server.port}/{PROJECT}.git"
        subprocess.run([*git, "remote", "add", "origin", origin], check=True)
        # 前に置く glab は失敗する。通れば glab の経路に入ってしまうので、通れないようにしておく
        glab = write(os.path.join(tmp, "bin", "glab"), "#!/bin/sh\nexit 1\n")
        os.chmod(glab, 0o755)
        self.bin = os.path.dirname(glab)
        self.args = os.path.join(tmp, "args.txt")
        self.exe = write(
            os.path.join(tmp, "exe", "ccnavi"),
            STUB.format(compat=version.COMPAT, log=self.args),
        )
        os.chmod(self.exe, 0o755)

    def fetch(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("GITHUB_TOKEN", None)
        env.update(
            {
                "GITLAB_TOKEN": self.server.token,
                "PATH": os.pathsep.join([self.bin, os.environ.get("PATH", "")]),
                "CCNAVI_ENTRY_PATH": self.exe,
                "CCNAVI_LEARNING": "disable",
            }
        )
        script = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-review.sh")
        return subprocess.run(
            [SHELL, script, "fetch"],
            cwd=self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_fetch_reads_the_mr_and_the_thread_through_curl(self):
        done = self.fetch()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = json.loads(done.stdout)
        self.assertEqual(got["host"], "gitlab")
        self.assertEqual(got["mr"]["number"], 1)
        self.assertEqual(len(got["threads"]), 1)
        thread = got["threads"][0]
        self.assertFalse(thread["resolved"])
        self.assertIn("ここを直してください", thread["body"])
        # curl の経路に入ったことは、代役が受けた要求で分かる
        log = self.server.requests
        self.assertIn(f"GET /api/v4/projects/{PROJECT.replace('/', '%2F')}", log)
        self.assertTrue(any("/merge_requests/1/discussions" in line for line in log), log)


if __name__ == "__main__":
    sys.exit(unittest.main())
