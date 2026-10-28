"""残った指摘の対応方針の受入テスト（ccnavi-user-decide.sh と ccnavi-review.sh decide --preview）。

選んだものを置く形はユーザ専用の ccnavi-user-decide.sh、一覧を見るだけの形は
ccnavi-review.sh decide <N> --preview。ccnavi-review.sh の decide の置く形は、何も動かさずに
終了コード 2 で新しい sh を案内する。

sh を外から呼び、GitLab の代役と実行ファイルの代役で 1 周させる。

見るのは sh の仕事だけ。

1. `--preview` は実行ファイルの答えをそのまま返し、何も投稿しない
2. `--choices … --digest …` は実行ファイルが書いた下書きから issue を作り、決めた内容をコメントに
   書き出して下書きを消す。答えに issue の URL を足す
3. 投稿に失敗したら下書きを残し、警告を答えに載せる（置いたことは戻さない）
4. フェーズ番号の表記を揃える（`01` でも、実行ファイルが `1` で書いた下書きを拾う）
5. 引数の組み合わせの誤りは、実行ファイルを起こす前に断る。ccnavi-review.sh の decide の置く形と、
   ccnavi-user-decide.sh の --preview も断る
6. 抜けるときの片付け（EXIT の trap）で rm が失敗しても、元の終了コードで抜ける（dash と bash）

実行ファイルは代役（引数を記録し、決まった答えと下書きを書く sh）。判定そのものは
`tests/ticket` が見る。GitLab は同じプロセスの小さな HTTP サーバで、sh が呼ぶ経路だけを返す。

`CCNAVI_SH_DIR` で、コピーする sh の出どころを差し替えられる。既定はこのツリーの
`.ccnavi/scripts/`（テストしているソースそのもの）。
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

from ccnavi.entry import version
from ccnavi.tickets import review_host
from tests import ROOT, common_sh

SHELL = shutil.which("sh") or shutil.which("bash")
GIT = shutil.which("git")
NEEDS = [tool for tool in ("jq", "curl") if shutil.which(tool) is None]
SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
TOKEN = "t0k"

# 実行ファイルの代役。引数を 1 行ずつ記録し、`--preview` なら一覧、`--accept-plan` なら受け入れの
# 計画（スレッド d1 に返信する）、`--yes` なら下書きを書いて答える。
STUB = """#!/bin/sh
case " $* " in
*" --version "*) printf 'compat: {compat}\\n'; exit 0 ;;
*" write-and-push family "*) printf 'write-and-push 1\\nfamily i0001\\ntarget no\\n'; exit 0 ;;
esac
printf '%s\\n' "$*" >>"$STUB_LOG"
root="$2"
case " $* " in
*" --preview "*) printf '{"version":1,"threads":[{"key":"u1"}],"digest":"d0"}\\n' ;;
*" --accept-plan "*)
  plan=""; prev=""
  for a in "$@"; do [ "$prev" = --accept-plan ] && plan="$a"; prev="$a"; done
  if [ -n "$STUB_PLAN" ]; then cp "$STUB_PLAN" "$plan"; else
    thread='{"id":"d1","key":"u1","reply":"<!-- ccnavi:accept -->\\nok\\n"}'
    printf '{"version":1,"digest":"d0","threads":[%s]}\\n' "$thread" >"$plan"
  fi ;;
*" --yes "*)
  [ -z "$STUB_FAIL_YES" ] || { echo "置けない" >&2; exit 1; }
  printf 'issue のタイトル\\n\\n本文\\n' >"$root/logs/state/review-issue-i0001-1.md"
  printf '<!-- ccnavi:decide -->\\n決めた\\n' >"$root/logs/state/review-decide-i0001-1.md"
  printf '{"version":1,"ok":true,"reviewed":true,"followup":""}\\n' ;;
esac
"""


WINDOWS = os.name == "nt"


# 子プロセスの PATH に並べる標準コマンド。PATH は偽の道具の bin とシェル本体の置き場だけで組むので、
# sh が使うものはここに足す（macOS は awk などが /bin に無く、/usr/bin を足すと gh / glab も引ける）
STANDARD_TOOLS = (
    "jq",
    "curl",
    "git",
    "sed",
    "cat",
    "rm",
    "dirname",
    "printf",
    "mkdir",
    "tr",
    "mktemp",
    "head",
    "date",
    "grep",
    "wc",
    "uname",
    "cp",
    "mv",
    "awk",
    "tail",
    "sleep",
    "find",
    "rmdir",
    "basename",
    "env",
    "cut",
    "hostname",
)


def put_tool(found: str, link: str) -> None:
    """`found` の道具を `link` の名前で PATH に置く。

    POSIX ではシンボリックリンク。Windows ではリンクを張る権限が無いことが多く、コピーや
    ハードリンクにすると mingw64 の git / curl が隣の DLL を見つけられず起動しない。
    だから `exec` で実物へ渡す sh を置く。名前が PATH で引ける、という役目は同じ。
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
    """Node（libuv）が子プロセスの引数を組むのと同じ書き方で 1 語を包む。

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
    """sh が呼ぶ経路だけを返す代役。

    投稿は `posted` に積む。`fail_notes` なら投稿のコメントを 500 にする。
    """

    posted: list[tuple[str, dict]] = []
    fail_notes = False
    fail_reply = False
    fail_resolve = False
    # 失敗させる discussion の id（解決の PUT だけ）
    fail_resolve_ids: set = set()
    # discussion の id → 解決済みか。GET で返し、解決の PUT で真にする
    discussions: dict = {}
    fetches = 0
    port = 0

    def log_message(self, *args):  # 出力を出さないようにする
        pass

    def reply(self, code: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def thread_list(self):
        return [
            {
                "id": ident,
                "notes": [
                    {
                        "id": 100 + i,
                        "resolvable": True,
                        "resolved": resolved,
                        "body": "指摘",
                        "created_at": "2026-09-23T00:00:00Z",
                        "author": {"id": 5},
                        "position": {"new_path": "src/a.py", "new_line": 3},
                    }
                ],
            }
            for i, (ident, resolved) in enumerate(GitLab.discussions.items())
        ]

    def do_GET(self):
        if "/discussions" in self.path and "per_page" in self.path:
            if "page=1" in self.path:
                GitLab.fetches += 1
            self.reply(200, self.thread_list() if "page=1" in self.path else [])
        elif "/merge_requests?" in self.path:
            url = f"http://127.0.0.1:{self.port}/demo/greeter/-/merge_requests/1"
            self.reply(200, [{"iid": 1, "web_url": url, "source_project_id": 1}])
        elif "/merge_requests" not in self.path and "per_page" not in self.path:
            self.reply(200, {"id": 1})
        else:
            self.reply(200, [])

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        data = json.loads(self.rfile.read(length) or b"{}")
        if "/discussions/" in self.path:
            ident = self.path.rsplit("/", 1)[-1]
            if GitLab.fail_resolve or ident in GitLab.fail_resolve_ids:
                self.reply(500, {"message": "500 boom"})
                return
            GitLab.posted.append(("resolve", {"path": self.path, **data}))
            if ident in GitLab.discussions:
                GitLab.discussions[ident] = True
            self.reply(200, {"id": ident})
        else:
            self.reply(405, {"message": "405"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        data = json.loads(self.rfile.read(length) or b"{}")
        if "/discussions/" in self.path and self.path.endswith("/notes"):
            if GitLab.fail_reply:
                self.reply(500, {"message": "500 boom"})
                return
            GitLab.posted.append(("reply", {"path": self.path, **data}))
            self.reply(201, {"id": 11})
        elif self.path.endswith("/notes"):
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
        for name in (
            "ccnavi-review.sh",
            "ccnavi-user-decide.sh",
            "ccnavi-user-lib-review.sh",
            *common_sh(SH_DIR),
        ):
            shutil.copy(os.path.join(SH_DIR, name), scripts)
        self.state = os.path.join(self.ws, "logs", "state")
        os.makedirs(self.state)
        GitLab.posted = []
        GitLab.fail_notes = False
        GitLab.fail_reply = False
        GitLab.fail_resolve = False
        GitLab.fail_resolve_ids = set()
        GitLab.discussions = {"d1": False}
        GitLab.fetches = 0
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
            f.write(STUB.replace("{compat}", str(version.COMPAT)))
        os.chmod(self.stub, os.stat(self.stub).st_mode | stat.S_IXUSR)
        self.log = os.path.join(self.work, "stub.log")

    def decide(
        self,
        *args: str,
        shell: str | None = None,
        script: str = "ccnavi-user-decide.sh",
        **extra: str,
    ) -> subprocess.CompletedProcess:
        """ccnavi-user-decide.sh を打つ。script="ccnavi-review.sh" なら ccnavi-review.sh decide。"""
        shell = shell or SHELL
        assert shell is not None
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        # gh / glab を PATH から外し、curl とトークンの経路を通す
        tools = os.path.join(self.work, "bin")
        os.makedirs(tools, exist_ok=True)
        for name in STANDARD_TOOLS:
            found = shutil.which(name)
            if found and not os.path.exists(os.path.join(tools, name)):
                put_tool(found, os.path.join(tools, name))
        env.update(
            CCNAVI_WORKSPACE=self.ws,
            CCNAVI_ENTRY_PATH=self.stub,
            GITLAB_TOKEN=TOKEN,
            STUB_LOG=self.log,
            STUB_PLAN="",
            STUB_FAIL_YES="",
            PATH=tools + os.pathsep + os.path.dirname(shell),
        )
        env.update(extra)
        path = os.path.join(self.ws, ".ccnavi", "scripts", script)
        command = [shell, path, *(["decide"] if script == "ccnavi-review.sh" else []), *args]
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
        shown = self.decide("1", "--preview", script="ccnavi-review.sh")
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
        # 置く前に、受け入れる指摘のスレッドへ返信を残して解決する
        self.assertIn("--accept-plan", self.calls()[-2])
        self.assertIn('--yes {"u1":"issue"} --digest d0 --json', self.calls()[-1])
        # ユーザ専用の入口（`user reviewed`）で呼ぶ。`user` の外の `--reviewed --yes` は
        # 実行ファイルが断る
        self.assertIn(" user reviewed 1 --accept-unresolved --yes ", self.calls()[-1])
        self.assertIn(" user reviewed 1 --accept-unresolved --yes ", self.calls()[-2])
        # 置く呼び出しは計画を読み、返信と解決のあとで取り直した結果を渡される
        self.assertIn("--accept-done", self.calls()[-1])
        self.assertEqual(GitLab.fetches, 2)
        kinds = [kind for kind, _ in GitLab.posted]
        self.assertEqual(kinds, ["reply", "resolve", "issue", "note"])
        reply, resolve = GitLab.posted[0][1], GitLab.posted[1][1]
        self.assertTrue(reply["path"].endswith("/merge_requests/1/discussions/d1/notes"))
        self.assertTrue(reply["body"].startswith("<!-- ccnavi:accept -->"))
        self.assertTrue(resolve["path"].endswith("/merge_requests/1/discussions/d1"))
        self.assertIs(resolve["resolved"], True)
        self.assertEqual(GitLab.posted[2][1]["title"], "issue のタイトル")
        note = GitLab.posted[3][1]["body"]
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
        self.assertIn("user reviewed 1 ", self.calls()[-1])
        self.assertEqual([kind for kind, _ in GitLab.posted], ["reply", "resolve", "issue", "note"])

    def test_a_failed_reply_stops_before_anything_is_placed(self):
        GitLab.fail_reply = True
        stopped = self.decide("1", "--choices", '{"u1":"issue"}', "--digest", "d0")
        self.assertEqual(stopped.returncode, 1, stopped.stderr)
        self.assertIn("受け入れを完了できない", stopped.stderr)
        self.assertIn("返信を残せなかった", stopped.stderr)
        # 置く呼び出し（--yes）は打たない。issue もコメントも作らない
        self.assertNotIn(
            "--yes", " ".join(call for call in self.calls() if "--accept-plan" not in call)
        )
        self.assertEqual(GitLab.posted, [])
        self.assertEqual(os.listdir(self.state), [])

    def test_a_failed_resolve_stops_before_anything_is_placed(self):
        GitLab.fail_resolve = True
        stopped = self.decide("1", "--choices", '{"u1":"keep"}', "--digest", "d0")
        self.assertEqual(stopped.returncode, 1, stopped.stderr)
        self.assertIn("解決できなかった", stopped.stderr)
        self.assertEqual([kind for kind, _ in GitLab.posted], ["reply"])
        self.assertTrue(all("--accept-plan" in call for call in self.calls()))
        # 返信は書いたので、計画を残す
        self.assertEqual(os.listdir(self.state), ["review-accept-i0001-1.json"])

    def plan_file(self, *ids: str) -> str:
        """実物の書き手（実行ファイルが使う `review_host.write_accept_plan`）で書いた計画。

        sh と実行ファイルの契約を、両側が同じ書き手と同じ読み方で通ることで確かめる。
        """
        path = os.path.join(self.work, "plan-in.json")
        items = [
            (review_host.Thread(id=i, url=f"u/{i}"), review_host.accept_reply("テスト"))
            for i in ids
        ]
        self.assertEqual(review_host.write_accept_plan(path, items, "d0"), "")
        return path

    def test_all_replies_come_before_any_resolve_and_a_partial_failure_stops(self):
        """全部に返信してから解決する。途中で解決に失敗したら、置かずに止まり、計画を残す。"""
        GitLab.discussions = {"d1": False, "d2": False}
        GitLab.fail_resolve_ids = {"d2"}
        stopped = self.decide(
            "1",
            "--choices",
            '{"u1":"keep"}',
            "--digest",
            "d0",
            STUB_PLAN=self.plan_file("d1", "d2"),
            STUB_FAIL_YES="",
        )
        self.assertEqual(stopped.returncode, 1, stopped.stderr)
        self.assertEqual([k for k, _ in GitLab.posted], ["reply", "reply", "resolve"])
        self.assertIn("先に解決した分は残る", stopped.stderr)
        self.assertNotIn("--accept-done", " ".join(self.calls()))
        # ホストに書いたので、計画は残す（打ち直すと d1 は見せる指摘から外れる）
        self.assertIn("計画を残した", stopped.stderr)
        self.assertEqual(os.listdir(self.state), ["review-accept-i0001-1.json"])

    def test_nothing_is_written_when_the_plan_does_not_match_the_fetched_result(self):
        """計画の id が結果に無い、または API の経路に使えない字なら、ホストに書かない。"""
        for ident in ("abcdef", "d1/../x", "ZZ"):
            with self.subTest(ident=ident):
                GitLab.posted.clear()
                stopped = self.decide(
                    "1",
                    "--choices",
                    '{"u1":"keep"}',
                    "--digest",
                    "d0",
                    STUB_PLAN=self.plan_file(ident),
                )
                self.assertEqual(stopped.returncode, 1, stopped.stderr)
                self.assertEqual(GitLab.posted, [])
                self.assertIn("ホストには何も書いていない", stopped.stderr)

    def test_a_plan_written_by_the_real_writer_is_read_by_the_sh(self):
        done = self.decide(
            "1", "--choices", '{"u1":"keep"}', "--digest", "d0", STUB_PLAN=self.plan_file("d1")
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        reply = GitLab.posted[0][1]
        self.assertTrue(reply["body"].startswith(review_host.MARKER_ACCEPT))
        self.assertEqual(GitLab.discussions, {"d1": True})
        # 置けて終わったら計画は消える
        self.assertEqual(os.listdir(self.state), [])

    def test_a_failed_placement_after_the_host_was_written_keeps_the_plan(self):
        failed = self.decide("1", "--choices", '{"u1":"keep"}', "--digest", "d0", STUB_FAIL_YES="1")
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertEqual([k for k, _ in GitLab.posted], ["reply", "resolve"])
        self.assertIn("置けなかった", failed.stderr)
        self.assertEqual(os.listdir(self.state), ["review-accept-i0001-1.json"])

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

    def test_ccnavi_review_decide_takes_only_preview(self):
        """ccnavi-review.sh decide は --preview だけを受ける。置く形は何も動かさずに 2 で断る。"""
        for args in (
            ("1",),
            ("1", "--choices", '{"u1":"keep"}', "--digest", "d0"),
            ("1", "--preview", "--choices", "{}", "--digest", "d0"),
            ("1", "--yes", "{}"),
        ):
            with self.subTest(args=args):
                refused = self.decide(*args, script="ccnavi-review.sh")
                self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(GitLab.posted, [])
        self.assertEqual(GitLab.fetches, 0)
        self.assertEqual(os.listdir(self.state), [])

    def test_the_user_script_points_the_preview_back(self):
        """見るだけの --preview は ccnavi-review.sh に残る。ユーザ専用の sh は断って案内する。"""
        refused = self.decide("1", "--preview")
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertIn("ccnavi-review.sh decide <N> --preview", refused.stderr)
        self.assertEqual(self.calls(), [])

    @unittest.skipIf(WINDOWS, "rm の代役を PATH の頭に置く形は POSIX で見る")
    def test_a_failing_rm_in_the_exit_trap_keeps_the_exit_code(self):
        shells = [found for found in (shutil.which("dash"), shutil.which("bash")) if found]
        if not shells:
            self.skipTest("dash も bash も無い")
        tools = os.path.join(self.work, "bin")
        os.makedirs(tools, exist_ok=True)
        fake_rm = os.path.join(tools, "rm")
        with open(fake_rm, "w", encoding="utf-8", newline="\n") as f:
            f.write("#!/bin/sh\necho 'rm: cannot remove' >&2\nexit 1\n")
        os.chmod(fake_rm, 0o755)
        for shell in shells:
            with self.subTest(shell=os.path.basename(shell)):
                refused = self.decide("x", shell=shell, script="ccnavi-review.sh")
                self.assertEqual(2, refused.returncode, refused.stderr)
                self.assertEqual(
                    "ccnavi-review: decide には <N>（フェーズ番号）が必要です。\n", refused.stderr
                )
                shown = self.decide("1", "--preview", shell=shell, script="ccnavi-review.sh")
                self.assertEqual(0, shown.returncode, shown.stderr)
                self.assertEqual("d0", json.loads(shown.stdout)["digest"])


if __name__ == "__main__":
    unittest.main()
