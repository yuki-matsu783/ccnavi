"""ccnavi-review.sh decide の受入テスト。

sh を外から呼び、GitLab の代役と実行ファイルの代役で 1 周させる。

見るのは sh の仕事だけ（設計 9.10）。

1. `--preview` は実行ファイルの答えをそのまま返し、何も投稿しない
2. `--choices … --digest …` は実行ファイルが書いた下書きから issue を作り、決めた内容をコメントに
   写して下書きを消す。答えに issue の URL を足す
3. 投稿に失敗したら下書きを残し、警告を答えに載せる（置いたことは戻さない）
4. フェーズ番号の綴りを揃える（`01` でも、実行ファイルが `1` で書いた下書きを拾う）
5. 引数の組み合わせの誤りは、実行ファイルを起こす前に断る

実行ファイルは代役（引数を記録し、決まった答えと下書きを書く sh）。判定そのものは
`tests/ticket` が見る。GitLab は同じプロセスの小さな HTTP サーバで、sh が呼ぶ道だけを返す。
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest

from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
NEEDS = [tool for tool in ("jq", "curl") if shutil.which(tool) is None]
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")
TOKEN = "t0k"

# 実行ファイルの代役。引数を 1 行ずつ記録し、`--preview` なら一覧、`--yes` なら下書きを書いて
# 答える。
STUB = """#!/bin/sh
printf '%s\\n' "$*" >>"$STUB_LOG"
root="$2"
case " $* " in
*" --preview "*) printf '{"version":1,"threads":[{"key":"u1"}],"digest":"d0"}\\n' ;;
*" --yes "*)
  printf 'issue の題\\n\\n本文\\n' >"$root/logs/state/review-issue-i0001-1.md"
  printf '<!-- ccnavi:decide -->\\n決めた\\n' >"$root/logs/state/review-decide-i0001-1.md"
  printf '{"version":1,"ok":true,"reviewed":true,"followup":""}\\n' ;;
esac
"""


WINDOWS = os.name == "nt"


def put_tool(found: str, link: str) -> None:
    """`found` の道具を `link` の名前で PATH に置く。

    POSIX ではシンボリックリンク。Windows ではリンクを張る権限が無いことが多く、コピーや
    ハードリンクにすると mingw64 の git / curl が隣の DLL を見つけられず起動しない。
    だから `exec` で本物へ渡す sh を置く。名前が PATH で引ける、という役目は同じ。
    """
    if not WINDOWS:
        os.symlink(found, link)
        return
    target = found.replace("\\", "/").replace("'", "'\\''")
    # 改行は LF。CRLF だと sh が shebang を読み違える
    with open(link, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"#!/bin/sh\nexec '{target}' \"$@\"\n")
    os.chmod(link, 0o755)


def quote_arg(arg: str) -> str:
    """Node（libuv）が子プロセスの引数を組むのと同じ綴りで 1 語を包む。

    Windows の Python は引用符を含む語を外側の引用符なしの `{\\"u1\\":...}` にするが、MSYS の
    sh はそれを 1 語として読めず、後ろの引数まで消える。ボードは Node から sh を起こすので、
    テストもその形で渡す。

    渡す引数（sh のパス、番号、`--choices`、JSON、`d0`）は往復できることを確かめてある。
    `'` や `*`、`\\\\` の連続と空白の組み合わせは、MSYS 側の癖で復元できない（Node 経由でも同じ）。
    """
    if arg and not any(c in arg for c in ' \t"'):
        return arg
    out = ['"']
    backslashes = 0
    for c in arg:
        if c == "\\":
            backslashes += 1
        elif c == '"':
            out.append("\\" * (backslashes * 2 + 1) + '"')
            backslashes = 0
        else:
            out.append("\\" * backslashes + c)
            backslashes = 0
    out.append("\\" * (backslashes * 2) + '"')
    return "".join(out)


class GitLab(http.server.BaseHTTPRequestHandler):
    """sh が呼ぶ道だけを返す代役。

    投稿は `posted` に積む。`fail_notes` なら投稿のコメントを 500 にする。
    """

    posted: list[tuple[str, dict]] = []
    fail_notes = False
    port = 0

    def log_message(self, *args):  # 出力を黙らせる
        pass

    def reply(self, code: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if "/merge_requests?" in self.path:
            url = f"http://127.0.0.1:{self.port}/demo/greeter/-/merge_requests/1"
            self.reply(200, [{"iid": 1, "web_url": url}])
        elif "/merge_requests" not in self.path and "per_page" not in self.path:
            self.reply(200, {"id": 1})
        else:
            self.reply(200, [])

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        data = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/notes"):
            if GitLab.fail_notes:
                self.reply(500, {"message": "500 boom"})
                return
            GitLab.posted.append(("note", data))
            self.reply(201, {"id": 9, "created_at": "2026-09-23T00:00:00Z"})
        elif self.path.endswith("/issues"):
            GitLab.posted.append(("issue", data))
            url = f"http://127.0.0.1:{self.port}/demo/greeter/-/issues/1"
            self.reply(201, {"iid": 1, "web_url": url})
        else:
            self.reply(405, {"message": "405"})


@unittest.skipIf(SHELL is None or GIT is None or NEEDS, f"sh / git / {NEEDS} が無い")
class ReviewDecideShTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="ccnavi-decide-sh-")
        self.addCleanup(shutil.rmtree, self.work, True)
        self.ws = os.path.join(self.work, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in ("ccnavi-review.sh", "ccnavi-common.sh"):
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        self.state = os.path.join(self.ws, "logs", "state")
        os.makedirs(self.state)
        GitLab.posted = []
        GitLab.fail_notes = False
        server = http.server.HTTPServer(("127.0.0.1", 0), GitLab)
        GitLab.port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.tree = os.path.join(self.ws, "tree")
        subprocess.run([GIT, "init", "-q", "-b", "i0001", self.tree], check=True)
        subprocess.run(
            [GIT, "-C", self.tree, "commit", "-q", "--allow-empty", "-m", "i"],
            check=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )
        origin = f"http://127.0.0.1:{GitLab.port}/demo/greeter.git"
        subprocess.run([GIT, "-C", self.tree, "remote", "add", "origin", origin], check=True)
        self.stub = os.path.join(self.work, "stub-ccnavi")
        with open(self.stub, "w", encoding="utf-8") as f:
            f.write(STUB)
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IXUSR)
        self.log = os.path.join(self.work, "stub.log")

    def decide(self, *args: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        # gh / glab を PATH から外し、curl とトークンの道を通す
        tools = os.path.join(self.work, "bin")
        os.makedirs(tools, exist_ok=True)
        for name in ("jq", "curl", "git", "sed", "cat", "rm", "dirname", "printf", "mkdir"):
            found = shutil.which(name)
            if found and not os.path.exists(os.path.join(tools, name)):
                put_tool(found, os.path.join(tools, name))
        env.update(
            CCNAVI_WORKSPACE=self.ws,
            CCNAVI_BIN_PATH=self.stub,
            GITLAB_TOKEN=TOKEN,
            STUB_LOG=self.log,
            PATH=tools + os.pathsep + os.path.dirname(SHELL),
        )
        script = os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-review.sh")
        command = [SHELL, script, "decide", *args]
        if WINDOWS:
            command = " ".join(quote_arg(part) for part in command)
        return subprocess.run(
            command,
            cwd=self.tree,
            env=env,
            capture_output=True,
            text=True,
            # sh は UTF-8 を出す。日本語 Windows の既定（cp932）で読むと警告の文が復号できない
            encoding="utf-8",
            # Windows は MSYS の起動が遅く、1 回に 12〜24 秒かかる
            timeout=180 if WINDOWS else 60,
        )

    def calls(self) -> list[str]:
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_preview_passes_the_answer_through_and_posts_nothing(self):
        shown = self.decide("1", "--preview")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["digest"], "d0")
        self.assertIn("--reviewed 1 --accept-unresolved --preview --json", self.calls()[-1])
        self.assertEqual(GitLab.posted, [])

    def test_choices_create_the_issue_and_post_the_decision(self):
        done = self.decide("1", "--choices", '{"u1":"issue"}', "--digest", "d0")
        self.assertEqual(done.returncode, 0, done.stderr)
        answer = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertTrue(answer["ok"])
        self.assertTrue(answer["issue_url"].endswith("/issues/1"))
        self.assertEqual(answer["warning"], "")
        self.assertIn('--yes {"u1":"issue"} --digest d0 --json', self.calls()[-1])
        kinds = [kind for kind, _ in GitLab.posted]
        self.assertEqual(kinds, ["issue", "note"])
        self.assertEqual(GitLab.posted[0][1]["title"], "issue の題")
        note = GitLab.posted[1][1]["body"]
        self.assertTrue(note.startswith("<!-- ccnavi:decide -->"))
        self.assertIn("issue に回した分: #1", note)
        self.assertEqual(os.listdir(self.state), [])

    def test_a_failed_comment_keeps_the_draft_and_warns(self):
        GitLab.fail_notes = True
        done = self.decide("1", "--choices", '{"u1":"issue"}', "--digest", "d0")
        self.assertEqual(done.returncode, 0, done.stderr)
        answer = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertIn("コメントを残せなかった", answer["warning"])
        self.assertIn("review-decide-i0001-1.md", os.listdir(self.state))

    def test_the_phase_number_is_normalized(self):
        done = self.decide("01", "--choices", '{"u1":"keep"}', "--digest", "d0")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("--reviewed 1 ", self.calls()[-1])
        self.assertEqual([kind for kind, _ in GitLab.posted], ["issue", "note"])

    def test_wrong_arguments_are_refused_before_anything_runs(self):
        for args in (
            ("x",),
            ("1", "--choices"),
            ("1", "--digest", "d0"),
            ("1", "--choices", "{}"),
            ("1", "--preview", "--choices", "{}", "--digest", "d0"),
            ("1", "--nope"),
        ):
            refused = self.decide(*args)
            self.assertEqual(refused.returncode, 2, (args, refused.stderr))
        self.assertEqual(self.calls(), [])
        self.assertEqual(GitLab.posted, [])


if __name__ == "__main__":
    unittest.main()
