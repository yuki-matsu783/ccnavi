"""ccnavi-review.sh mr-state の約束。

ボードの「マージ状況を確認」が、MR の今の状態を機械で読む。出力は 2 行で、
1 行目は版の `mr-state 1`、2 行目は次のどれか（区切りは空白 1 つ）。

- `open <番号> <yes|no> <URL>`（3 つめは Draft か）
- `merged <番号> <URL>`
- `closed <番号> <URL>`
- `unknown <種類> <理由>`（種類は no-tool / auth / network / api / not-found）

終了コードは、状態が分かれば 0、unknown は 1、引数の誤りは 2（標準出力は空）。
読むだけで、何も書き残さない。
理由には資格情報を入れない。ホストへの道具（curl）は PATH の先頭に置いた代役に差し替える
（gh / glab は代役で必ず失敗させ、実際のホストに触れない）。

`CCNAVI_SH_DIR` で、写す sh の出どころを差し替えられる。既定はこのツリーの `.ccnavi/scripts/`。
差し替えた場所に同じ名前の sh があれば、`.ccnavi/scripts/` から写したあとにその上へ重ねる
（写す前の版は一部の sh しか持たない）。使った sh のパスは最初に標準エラーへ 1 行出す。
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

SHELL = shutil.which("sh") or shutil.which("bash")
NEEDED = all(shutil.which(tool) for tool in ("git", "jq"))
SH_DIR = os.path.join(ROOT, ".ccnavi", "scripts")
_OVERLAY = os.environ.get("CCNAVI_SH_DIR", "")
OVERLAY_DIR = os.path.join(ROOT, _OVERLAY) if _OVERLAY else ""
TOKEN = "s3cr3t-token"


def _review_sh() -> str:
    """試す ccnavi-review.sh のパス。差し替えた場所に無ければ `.ccnavi/scripts/` のもの。"""
    over = os.path.join(OVERLAY_DIR, "ccnavi-review.sh") if OVERLAY_DIR else ""
    return over if over and os.path.isfile(over) else os.path.join(SH_DIR, "ccnavi-review.sh")


def _has_mr_state() -> bool:
    with open(_review_sh(), encoding="utf-8") as f:
        return "mr_state()" in f.read()


# 取り込み前（`.ccnavi/scripts/` の本体にまだ mr-state が無い）は、写しを `CCNAVI_SH_DIR` で
# 指したときだけ回る。
# 取り込み後は何も指さなくても回る
SKIP_UNTIL_IMPORTED = (
    "ccnavi-review.sh に mr-state がまだ無い（取り込み前。CCNAVI_SH_DIR で写しを指すと回る）"
)

# 使った sh を 1 度だけ言う（写しで試した結果と、取り込み後の結果を取り違えないため）
_told = False


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


# 代役の curl。受けた URL を記録する。`curl.code` が 0 でなければ `curl.err` を標準エラーに出して、
# その終了コードで終わる。
# 0 なら `curl.body` を出す（成功のときは標準エラーに何も出さない。ccnavi_host_api は両方を拾う）。
CURL = """#!/bin/sh
d=$(dirname "$0")
for a in "$@"; do url="$a"; done
printf '%s\\n' "$url" >>"$d/curl.url"
code=0
[ ! -f "$d/curl.code" ] || code=$(cat "$d/curl.code")
if [ "$code" != 0 ]; then
  [ ! -f "$d/curl.err" ] || cat "$d/curl.err" >&2
  exit "$code"
fi
cat "$d/curl.body"
"""

FAIL = "#!/bin/sh\nexit 1\n"


def path_without(root, *names):
    """PATH のディレクトリにある道具のうち、`names` 以外を並べた置き場を作って返す。"""
    out = os.path.join(root, "tools")
    os.makedirs(out, exist_ok=True)
    seen = set()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if name in names or name in seen:
                continue
            full = os.path.join(directory, name)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                seen.add(name)
                os.symlink(full, os.path.join(out, name))
    return out


@unittest.skipIf(SHELL is None or not NEEDED, "sh・git・jq のどれかが無い")
@unittest.skipUnless(_has_mr_state(), SKIP_UNTIL_IMPORTED)
class MrStateTest(unittest.TestCase):
    """GitHub の形。GitLab は下の継承が、origin と答えの形だけを入れ替えて同じ約束を見る。"""

    origin = "https://github.com/o/r.git"
    token_name = "GITHUB_TOKEN"
    request = "/repos/o/r/pulls/9"
    fallback = "https://github.com/o/r/pull/9"
    url = "https://github.com/o/r/pull/9"

    def answer(self, state="open", merged=False, draft=False, number=9, url=None):
        """GitHub の `pulls/<番号>` の答え。"""
        shown = self.url if url is None else url
        body = {
            "number": number,
            "state": state,
            "merged": merged,
            "merged_at": "2026-01-01T00:00:00Z" if merged else None,
            "draft": draft,
        }
        if shown:
            body["html_url"] = shown
        return json.dumps(body)

    def merged_answer(self):
        return self.answer(state="closed", merged=True)

    def closed_answer(self):
        return self.answer(state="closed")

    def setUp(self):
        global _told
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in ("ccnavi-review.sh", *common_sh()):
            shutil.copy(os.path.join(SH_DIR, name), scripts)
            over = os.path.join(OVERLAY_DIR, name) if OVERLAY_DIR else ""
            if over and os.path.isfile(over):
                shutil.copy(over, scripts)
        if not _told:
            _told = True
            print(f"[test_review_mr_state] ccnavi-review.sh = {_review_sh()}", file=sys.stderr)
        subprocess.run(["git", "init", "-q", "-b", "i0001", self.ws], check=True)
        identity = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run(
            ["git", "-C", self.ws, *identity, "commit", "-q", "--allow-empty", "-m", "seed"],
            check=True,
        )
        subprocess.run(["git", "-C", self.ws, "remote", "add", "origin", self.origin], check=True)
        self.bin = os.path.join(self._tmp.name, "bin")
        os.makedirs(self.bin)
        for name in ("gh", "glab"):
            os.chmod(write(os.path.join(self.bin, name), FAIL), 0o755)
        os.chmod(write(os.path.join(self.bin, "curl"), CURL), 0o755)

    # ---- 道具

    def says(self, body, code=0, err=""):
        write(os.path.join(self.bin, "curl.body"), body)
        write(os.path.join(self.bin, "curl.code"), str(code))
        write(os.path.join(self.bin, "curl.err"), err)

    def run_sh(self, *args, token=TOKEN, path=None, cwd=None):
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.pop("GITHUB_TOKEN", None)
        env.pop("GITLAB_TOKEN", None)
        if token:
            env[self.token_name] = token
        env["PATH"] = (
            path if path is not None else os.pathsep.join([self.bin, os.environ.get("PATH", "")])
        )
        return subprocess.run(
            [
                SHELL,
                os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-review.sh"),
                "mr-state",
                *args,
            ],
            cwd=cwd or self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def lines(self, done):
        return done.stdout.split("\n")

    def assert_known(self, done, line):
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertEqual(["mr-state 1", line, ""], self.lines(done), done.stderr)

    def assert_unknown(self, done, kind, reason=None):
        self.assertEqual(1, done.returncode, done.stdout + done.stderr)
        lines = self.lines(done)
        self.assertEqual(3, len(lines), done.stdout)
        self.assertEqual("mr-state 1", lines[0])
        self.assertEqual("", lines[2])
        self.assertTrue(lines[1].startswith(f"unknown {kind} "), lines[1])
        why = lines[1][len(f"unknown {kind} ") :]
        self.assertNotEqual("", why)
        self.assertEqual(why, why.strip())
        self.assertFalse(any(ord(c) < 32 or ord(c) == 127 for c in why), repr(why))
        self.assertNotIn(TOKEN, done.stdout)
        if reason is not None:
            self.assertIn(reason, why)

    # ---- 4 つの状態

    def test_an_open_request_says_whether_it_is_a_draft(self):
        self.says(self.answer())
        self.assert_known(self.run_sh("9"), f"open 9 no {self.url}")
        self.says(self.answer(draft=True))
        self.assert_known(self.run_sh("9"), f"open 9 yes {self.url}")

    def test_a_merged_request(self):
        self.says(self.merged_answer())
        self.assert_known(self.run_sh("9"), f"merged 9 {self.url}")

    def test_a_request_closed_without_merging(self):
        self.says(self.closed_answer())
        self.assert_known(self.run_sh("9"), f"closed 9 {self.url}")

    def test_it_asks_only_that_request_of_the_host(self):
        self.says(self.answer())
        self.run_sh("9")
        with open(os.path.join(self.bin, "curl.url"), encoding="utf-8") as f:
            asked = f.read().split()
        self.assertEqual(1, len(asked), asked)
        self.assertTrue(asked[0].endswith(self.request), asked)

    def test_a_missing_or_unusable_url_is_built_from_the_host_and_path(self):
        for url in ("", "https://example.com/a b", "https://example.com/a\tb"):
            with self.subTest(url=url):
                self.says(self.answer(url=url) if url else self.answer(url=""))
                self.assert_known(self.run_sh("9"), f"open 9 no {self.fallback}")

    def test_the_answer_for_another_number_is_unknown(self):
        self.says(self.answer(number=10))
        self.assert_unknown(self.run_sh("9"), "api", "別の MR")

    def test_it_writes_nothing_behind(self):
        self.says(self.answer())
        self.run_sh("9")
        state = os.path.join(self.ws, "logs", "state")
        self.assertEqual([], os.listdir(state) if os.path.isdir(state) else [])
        tracked = subprocess.run(
            ["git", "-C", self.ws, "status", "--porcelain", "--", ".", ":!logs", ":!.ccnavi"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual("", tracked.stdout)

    # ---- unknown の種類

    def test_without_a_token_it_is_auth(self):
        self.says(self.answer())
        done = self.run_sh("9", token="")
        self.assert_unknown(done, "auth", self.token_name)

    def test_a_refused_token_is_auth(self):
        for code, err in (
            (22, "curl: (22) The requested URL returned error: 401"),
            (22, "curl: (22) The requested URL returned error: 403"),
        ):
            with self.subTest(err=err):
                self.says("", code, err)
                self.assert_unknown(self.run_sh("9"), "auth")

    def test_a_missing_request_is_not_found(self):
        self.says("", 22, "curl: (22) The requested URL returned error: 404")
        self.assert_unknown(self.run_sh("9"), "not-found", "#9")

    def test_an_unreachable_host_is_network(self):
        for code, err in (
            (6, "curl: (6) Could not resolve host: api.github.com"),
            (7, "curl: (7) Failed to connect to api.github.com port 443"),
            (28, "curl: (28) Operation timed out after 30001 milliseconds"),
        ):
            with self.subTest(err=err):
                self.says("", code, err)
                self.assert_unknown(self.run_sh("9"), "network")

    def test_a_host_failure_is_api_with_its_status(self):
        self.says("", 22, "curl: (22) The requested URL returned error: 500")
        self.assert_unknown(self.run_sh("9"), "api", "HTTP 500")

    def test_an_unreadable_answer_is_api(self):
        for body in ("not json", "[]", '"x"', "{}"):
            with self.subTest(body=body):
                self.says(body)
                self.assert_unknown(self.run_sh("9"), "api")

    def test_an_unexpected_state_is_api(self):
        self.says(self.answer(state="weird"))
        self.assert_unknown(self.run_sh("9"), "api", "weird")

    def test_the_reason_never_carries_the_host_body_or_the_token(self):
        self.says(
            "", 22, f"curl: (22) The requested URL returned error: 500\n{TOKEN} secret\n\x07bell"
        )
        done = self.run_sh("9")
        self.assert_unknown(done, "api")
        self.assertNotIn("secret", done.stdout)
        self.assertNotIn(TOKEN, done.stderr)

    def test_without_any_way_to_reach_the_host_it_is_no_tool(self):
        tools = path_without(self._tmp.name, "curl", "gh", "glab")
        self.assert_unknown(self.run_sh("9", path=tools), "no-tool")

    def test_without_jq_it_is_no_tool(self):
        tools = path_without(self._tmp.name, "jq")
        self.assert_unknown(
            self.run_sh("9", path=os.pathsep.join([self.bin, tools])), "no-tool", "jq"
        )

    def test_without_an_origin_it_is_not_found(self):
        subprocess.run(["git", "-C", self.ws, "remote", "remove", "origin"], check=True)
        self.assert_unknown(self.run_sh("9"), "not-found", "origin")

    def test_an_unreadable_origin_is_not_found(self):
        subprocess.run(
            ["git", "-C", self.ws, "remote", "set-url", "origin", "/some/local/dir"], check=True
        )
        self.assert_unknown(self.run_sh("9"), "not-found", "origin")

    # ---- 引数の誤り

    def test_a_bad_argument_is_refused_with_nothing_on_stdout(self):
        self.says(self.answer())
        for args in (
            (),
            ("abc",),
            ("0",),
            ("007",),
            ("9; ls",),
            ("-1",),
            ("9", "10"),
            ("1234567890",),
            ("",),
        ):
            with self.subTest(args=args):
                done = self.run_sh(*args)
                self.assertEqual(2, done.returncode, done.stdout + done.stderr)
                self.assertEqual("", done.stdout)
                self.assertNotEqual("", done.stderr.strip())

    def test_merged_keeps_its_own_output(self):
        """merged 副命令（ccnavi-sync.sh が読む）の出力と終了コードは変えない。"""
        self.says("not json")
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env[self.token_name] = TOKEN
        env["PATH"] = os.pathsep.join([self.bin, os.environ.get("PATH", "")])
        done = subprocess.run(
            [SHELL, os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-review.sh"), "merged", "9"],
            cwd=self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.assertEqual(3, done.returncode, done.stdout + done.stderr)
        self.assertEqual("unknown", done.stdout.strip())


@unittest.skipIf(SHELL is None or not NEEDED, "sh・git・jq のどれかが無い")
@unittest.skipUnless(_has_mr_state(), SKIP_UNTIL_IMPORTED)
class MrStateGitLabTest(MrStateTest):
    """GitLab の形。答えの欄（iid・state・web_url・draft）とパスだけが違う。"""

    origin = "https://gitlab.com/o/r.git"
    token_name = "GITLAB_TOKEN"
    request = "/projects/o%2Fr/merge_requests/9"
    fallback = "https://gitlab.com/o/r/-/merge_requests/9"
    url = "https://gitlab.com/o/r/-/merge_requests/9"

    def answer(self, state="open", merged=False, draft=False, number=9, url=None):
        shown = self.url if url is None else url
        gitlab = "merged" if merged else ("opened" if state == "open" else state)
        body = {"iid": number, "state": gitlab, "draft": draft}
        if shown:
            body["web_url"] = shown
        return json.dumps(body)

    def test_a_request_in_the_middle_of_merging_is_unknown(self):
        self.says(self.answer(state="locked"))
        self.assert_unknown(self.run_sh("9"), "api", "locked")

    def test_the_old_draft_flag_is_read(self):
        self.says(
            json.dumps({"iid": 9, "state": "opened", "work_in_progress": True, "web_url": self.url})
        )
        self.assert_known(self.run_sh("9"), f"open 9 yes {self.url}")

    def test_a_self_hosted_host_with_a_port_keeps_its_scheme_and_port(self):
        local = "http://localhost:8929/g/p.git"
        subprocess.run(["git", "-C", self.ws, "remote", "set-url", "origin", local], check=True)
        self.says('{"iid": 9, "state": "opened", "draft": false}')
        self.assert_known(
            self.run_sh("9"), "open 9 no http://localhost:8929/g/p/-/merge_requests/9"
        )


if __name__ == "__main__":
    unittest.main()
