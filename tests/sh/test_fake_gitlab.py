"""GitLab の代役（`tests/sh/fake_gitlab.py`）を関数で起こして止める。

見るのは次の 8 つ。

1. 起動して要求が通り、止めたあとはつながらない
2. keep-alive の接続を開いたままでも `stop()` が返る
3. 2 つ同時に立てても、状態ファイルは干渉しない
4. `skew` を変えると、返す時刻が変わる
5. 実物の `ccnavi-review.sh fetch` が、`GITLAB_TOKEN` と代役の origin から、
   curl 経路で模擬の MR とスレッドを読める（`glab` は失敗する代役を前に置く）
6. 401・404 を返したあとも、同じ keep-alive 接続の次の要求が通る（本文を読み捨てている）
7. 存在しない MR への要求は、どれも 404 になる（接続が切れない）
8. 手で使う入口 `python tests/manual/gitlab/fake_gitlab.py <state.json> <port>` が、
   別プロセスで起動して 1 往復できる
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
import time
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


class ServerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def start(self, name="state.json", **kw):
        kw.setdefault("initial_state", state())
        server = fake_gitlab.start(os.path.join(self.tmp, name), **kw)
        self.addCleanup(server.stop)
        return server


class FakeGitlabTest(ServerCase):
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
        # 接続は閉じない。サーバ側には、次の要求を待つスレッドが残っている。
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


TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANUAL = os.path.join(TESTS_DIR, "manual", "gitlab", "fake_gitlab.py")

# act の中で MR を引く要求のすべて（method, path の MR 以降, 本文）
MR_REQUESTS = [
    ("GET", "", None),
    ("PUT", "", {"title": "x"}),
    ("GET", "/discussions", None),
    ("POST", "/discussions", {"body": "b"}),
    ("PUT", "/discussions/d1?resolved=true", None),
    ("GET", "/reviewers", None),
    ("PUT", "/reviewers/5?state=reviewed", None),
    ("GET", "/notes", None),
    ("POST", "/notes", {"body": "b"}),
    ("PUT", "/merge", None),
]


class KeepAliveTest(ServerCase):
    """同じ接続で続けて叩く。1 件目の応答の取りこぼしが、2 件目の失敗として現れる。"""

    def keep_alive(self, server):
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        self.addCleanup(conn.close)
        return conn

    def send(self, conn, server, method, path, body=None, token=None):
        headers = {"PRIVATE-TOKEN": server.token if token is None else token}
        raw = json.dumps(body) if body is not None else None
        conn.request(method, path, body=raw, headers=headers)
        res = conn.getresponse()
        return res.status, json.loads(res.read() or b"null")

    def test_a_next_request_works_after_a_401_or_404_that_carried_a_body(self):
        server = self.start()
        cases = {
            "401": (f"/api/v4/projects/{ENC}/merge_requests", "wrong"),
            "別のプロジェクト": ("/api/v4/projects/x%2Fy/merge_requests", None),
            "知らない道": ("/api/v4/nothing", None),
        }
        expected = {"401": 401, "別のプロジェクト": 404, "知らない道": 404}
        for label, (path, token) in cases.items():
            with self.subTest(label):
                conn = self.keep_alive(server)
                code, _ = self.send(conn, server, "POST", path, {"title": "t" * 50}, token)
                self.assertEqual(code, expected[label])
                code, got = self.send(conn, server, "GET", f"/api/v4/projects/{ENC}")
                self.assertEqual((code, got.get("path_with_namespace")), (200, PROJECT))

    def test_a_missing_merge_request_is_a_404_on_every_route(self):
        server = self.start()
        for method, tail, body in MR_REQUESTS:
            with self.subTest(f"{method} {tail}"):
                conn = self.keep_alive(server)
                path = f"/api/v4/projects/{ENC}/merge_requests/99{tail}"
                code, got = self.send(conn, server, method, path, body)
                self.assertEqual(code, 404, got)
                # 接続は切れず、同じ接続で次の要求が通る
                code, _ = self.send(conn, server, "GET", f"/api/v4/projects/{ENC}")
                self.assertEqual(code, 200)
        with open(os.path.join(self.tmp, "state.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((saved["mrs"], saved["seq"]), ([], 100))

    def test_an_existing_merge_request_next_to_a_missing_one_still_answers(self):
        server = self.start(initial_state=seeded())
        base = f"/api/v4/projects/{ENC}/merge_requests"
        self.assertEqual(call(server, "GET", f"{base}/1")[0], 200)
        self.assertEqual(call(server, "GET", f"{base}/2")[0], 404)


class ManualEntryTest(unittest.TestCase):
    """`python tests/manual/gitlab/fake_gitlab.py <state.json> <port>` を別プロセスで起こす。

    `load_body`（本体を読む）と `main`（引数を読んで起こし、待つ）をここで通す。
    ポートは 0 を渡して OS に決めさせ、標準エラーの案内の行から読む。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = os.path.join(self._tmp.name, "state.json")
        with open(self.state, "w", encoding="utf-8") as f:
            json.dump(state(), f)

    def launch(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env["PYTHONIOENCODING"] = "utf-8"
        env["CCNAVI_LOG_LEVEL"] = "ERROR"
        proc = subprocess.Popen(
            [sys.executable, "-I", MANUAL, self.state, "0"],
            cwd=self._tmp.name,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.addCleanup(proc.stderr.close)
        self.addCleanup(self.stop, proc)
        return proc

    def stop(self, proc):
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def listening_port(self, proc) -> int:
        found: list[str] = []
        reader = threading.Thread(target=lambda: found.append(proc.stderr.readline()), daemon=True)
        reader.start()
        reader.join(15)
        line = found[0] if found else ""
        self.assertIn("fake-gitlab listening on 127.0.0.1:", line, f"起動の案内が出ない: {line!r}")
        return int(line.split("127.0.0.1:")[1].split()[0])

    def test_the_hand_entry_starts_serves_a_round_trip_and_stops(self):
        proc = self.launch()
        port = self.listening_port(proc)
        self.assertGreater(port, 0)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.addCleanup(conn.close)
        headers = {"PRIVATE-TOKEN": fake_gitlab.TOKEN}
        raw = json.dumps({"title": "by-hand"})
        conn.request("POST", f"/api/v4/projects/{ENC}/merge_requests", body=raw, headers=headers)
        res = conn.getresponse()
        self.assertEqual(res.status, 201)
        self.assertEqual(json.loads(res.read())["title"], "by-hand")
        conn.request("GET", f"/api/v4/projects/{ENC}/merge_requests", headers=headers)
        res = conn.getresponse()
        self.assertEqual([m["title"] for m in json.loads(res.read())], ["by-hand"])
        with open(self.state, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["mrs"]), 1)
        self.stop(proc)
        self.assertIsNotNone(proc.returncode)

    def test_the_hand_entry_keeps_listening_until_stopped(self):
        proc = self.launch()
        self.listening_port(proc)
        time.sleep(0.3)
        self.assertIsNone(proc.poll(), "入口が起動直後に終わった")


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
        # 前に置く glab は必ず失敗させる。成功すると glab の経路に入ってしまうため。
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
