"""ccnavi-review.sh がホストから組む写しと、印のアカウント（ADR-0093 の 8.9。段階 4）の受入テスト。

見るのは 3 つ。

1. 録ったホストの応答の見本
   （`chrome-extension/ccnavi-approval/test/fixtures/host/github/<場面>/`）から sh が組む写し
   （`fetch`。`fetched_at` を除く）が、見本の期待値（`expected.json`）と同じ。拡張の試験
   （CX-T129）も同じ見本から TS で組んで同じ期待値と比べるので、sh と TS が同じ写しを組む
2. 写しの結論（変更要求と未解決のスレッド。判定のコアの `review.effective`・`_unresolved`）が
   見本の `conclusion.json` と同じ
3. `confirm` はトークンの持ち主を引いて `--actor` で渡す。引けなければ渡さない（印は前と同じ）。
   呼び手が `--actor` を渡しても受けない

ホストの API が変わって見本を録り直したら、`CCNAVI_HOST_FIXTURE=1` を付けてこのテストを回し、
期待値を書き直す（手順は chrome-extension/ccnavi-approval/README.md の「ホストの応答の見本」）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from ccnavi import review, version
from tests import ROOT
from tests.sh import github_host

SHELL = shutil.which("sh") or shutil.which("bash")
NEEDED = all(shutil.which(tool) for tool in ("git", "jq"))

# 実行ファイルの代役。受けた引数を 1 行ずつ書き残し、何も書かずに 0 で終わる。
STUB = """#!/bin/sh
case " $* " in
*" --version "*) printf 'compat: {compat}\\n'; exit 0 ;;
esac
for a in "$@"; do printf '%s\\n' "$a"; done >>'{log}'
exit 0
"""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def conclusion(copy: dict) -> dict:
    """写しの結論。confirm が止める理由のうち、ホストの写しから決まるもの。"""
    result = review.Result.from_data(copy)
    changes = [
        r for r in review.effective(result.reviews) if r.state.upper() == "CHANGES_REQUESTED"
    ]
    return {
        "changes_requested": sorted(r.url for r in changes),
        "unresolved": sorted(t.id for t in review._unresolved(result.threads, set())),
    }


@unittest.skipIf(SHELL is None or not NEEDED, "sh・git・jq のどれかが無い")
class HostFixtureTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = os.path.join(self._tmp.name, "ws")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        os.makedirs(scripts)
        for name in ("ccnavi-review.sh", "ccnavi-common.sh"):
            shutil.copy(os.path.join(ROOT, ".ccnavi", "scripts", name), scripts)
        git = ["git", "-C", self.ws, "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run(["git", "init", "-q", "-b", "i0001", self.ws], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "seed"], check=True)
        subprocess.run(
            [*git, "remote", "add", "origin", "https://github.com/acme/widgets.git"], check=True
        )
        self.bin = os.path.join(self._tmp.name, "bin")
        github_host.install(self.bin, sys.executable)
        self.args = os.path.join(self._tmp.name, "args.txt")
        self.exe = write(
            os.path.join(self._tmp.name, "exe", "ccnavi"),
            STUB.format(compat=version.COMPAT, log=self.args),
        )
        os.chmod(self.exe, 0o755)

    def review(self, scene, *args, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("GITLAB_TOKEN", None)
        env.update(
            {
                "GITHUB_TOKEN": "t0k",
                "PATH": os.pathsep.join([self.bin, os.environ.get("PATH", "")]),
                "FAKE_GITHUB_SCENE": scene,
                "FAKE_GITHUB_STATE": os.path.join(self._tmp.name, "comments.json"),
                "CCNAVI_BIN_PATH": self.exe,
                **extra,
            }
        )
        return subprocess.run(
            [SHELL, os.path.join(self.ws, ".ccnavi", "scripts", "ccnavi-review.sh"), *args],
            cwd=self.ws,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def passed(self):
        with open(self.args, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_the_sh_copy_matches_each_recorded_scene(self):
        self.assertEqual(
            github_host.scene_names(), ["changes-requested", "hostile", "paged", "resolved"]
        )
        for scene in github_host.scene_names():
            with self.subTest(scene=scene):
                done = self.review(scene, "fetch")
                self.assertEqual(done.returncode, 0, done.stderr)
                copy = json.loads(done.stdout)
                self.assertRegex(copy.pop("fetched_at"), r"^\d{4}-\d\d-\d\dT")
                where = os.path.join(github_host.SCENES, scene)
                if os.environ.get("CCNAVI_HOST_FIXTURE"):
                    body = json.dumps(copy, ensure_ascii=False, indent=2) + "\n"
                    write(os.path.join(where, "expected.json"), body)
                    body = json.dumps(conclusion(copy), ensure_ascii=False, indent=2) + "\n"
                    write(os.path.join(where, "conclusion.json"), body)
                with open(os.path.join(where, "expected.json"), encoding="utf-8") as f:
                    self.assertEqual(copy, json.load(f), f"{scene} の expected.json が古い")
                with open(os.path.join(where, "conclusion.json"), encoding="utf-8") as f:
                    self.assertEqual(conclusion(copy), json.load(f))

    def test_the_scenes_say_what_they_are_for(self):
        def said(scene):
            with open(os.path.join(github_host.SCENES, scene, "conclusion.json")) as f:
                return json.load(f)

        self.assertEqual(said("resolved"), {"changes_requested": [], "unresolved": []})
        paged = said("paged")
        self.assertEqual(paged["changes_requested"], [])
        self.assertEqual(len(paged["unresolved"]), 3)
        self.assertEqual(len(said("changes-requested")["changes_requested"]), 1)
        self.assertEqual(len(said("hostile")["unresolved"]), 8)

    def test_confirm_passes_the_token_owner_as_the_actor(self):
        done = self.review("resolved", "confirm", "--phase", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        passed = self.passed()
        self.assertIn("confirm", passed)
        at = passed.index("--actor")
        self.assertEqual(passed[at + 1], "octo-reviewer")

    def test_without_an_owner_confirm_passes_no_actor(self):
        done = self.review("resolved", "confirm", "--phase", "1", FAKE_GITHUB_NO_USER="1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("confirm", self.passed())
        self.assertNotIn("--actor", self.passed())
        # 引けなかったことで止めず、ホストの失敗の文面も出さない
        self.assertNotIn("失敗した", done.stderr)

    def test_a_malformed_owner_is_not_passed(self):
        user = os.path.join(github_host.SCENES, "resolved", "user.json")
        scene = os.path.join(self._tmp.name, "scenes", "odd")
        shutil.copytree(os.path.dirname(user), scene)
        write(os.path.join(scene, "user.json"), json.dumps({"login": "a b;rm -rf /"}))
        done = self.review(scene, "confirm", "--phase", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("--actor", self.passed())

    def test_confirm_does_not_take_an_actor_from_the_caller(self):
        for given in (["--actor", "someone"], ["--actor=someone"]):
            done = self.review("resolved", "confirm", "--phase", "1", *given)
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
            self.assertIn("--actor を受けない", done.stderr)
        self.assertFalse(os.path.exists(self.args))


if __name__ == "__main__":
    unittest.main()
