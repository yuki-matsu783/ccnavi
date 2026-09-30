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
from tests.sh import github_host, gitlab_host

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


def conclusion(copy: dict, poster: str = "") -> dict:
    """写しの結論。confirm が止める理由のうち、ホストの写しから決まるもの。

    GitLab は依頼を投稿したアカウント（`poster`）の ccnavi の依頼のスレッドを数えない
    （11.8.1 の決定 C）。
    """
    result = review.Result.from_data(copy)
    changes = [
        r for r in review.effective(result.reviews) if r.state.upper() == "CHANGES_REQUESTED"
    ]
    unresolved = review._unresolved(result.threads, set(), result.host, poster)
    return {
        "changes_requested": sorted(r.url for r in changes),
        "unresolved": sorted(t.id for t in unresolved),
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
            github_host.scene_names(),
            [
                "changes-requested",
                "cr-commented",
                "full-page",
                "hostile",
                "paged",
                "pending",
                "resolved",
            ],
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
        self.assertEqual(said("full-page"), {"changes_requested": [], "unresolved": []})
        paged = said("paged")
        self.assertEqual(paged["changes_requested"], [])
        # GitHub では目印で始まるスレッドも人のものとして数える（11.8.1 の決定 C）
        self.assertEqual(len(paged["unresolved"]), 4)
        self.assertIn("PRRT_kwDOAbCdEs5P2003", paged["unresolved"])
        for scene in ("changes-requested", "cr-commented", "pending"):
            self.assertEqual(len(said(scene)["changes_requested"]), 1, scene)
        self.assertEqual(len(said("hostile")["unresolved"]), 8)

    def test_confirm_passes_the_token_owner_as_the_actor(self):
        done = self.review("resolved", "confirm", "--phase", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        passed = self.passed()
        self.assertIn("confirm", passed)
        self.assertIn("--actor=octo-reviewer", passed)

    def test_without_an_owner_confirm_passes_no_actor(self):
        done = self.review("resolved", "confirm", "--phase", "1", FAKE_GITHUB_NO_USER="1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("confirm", self.passed())
        self.assertFalse(any(a.startswith("--actor") for a in self.passed()))
        # 引けなかったことで止めず、ホストの失敗の文面も出さない
        self.assertNotIn("失敗した", done.stderr)

    def scene_with_login(self, login):
        scene = os.path.join(tempfile.mkdtemp(dir=self._tmp.name), "odd")
        shutil.copytree(os.path.join(github_host.SCENES, "resolved"), scene)
        write(os.path.join(scene, "user.json"), json.dumps({"login": login}))
        return scene

    def test_a_malformed_owner_is_not_passed(self):
        for login in ("a b;rm -rf /", "-rf", "--actor", "ａｂｃ", "a" * 101):
            if os.path.exists(self.args):
                os.remove(self.args)
            done = self.review(self.scene_with_login(login), "confirm", "--phase", "1")
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertFalse(any(a.startswith("--actor") for a in self.passed()), login)

    def test_a_carriage_return_is_dropped_from_the_owner(self):
        done = self.review(self.scene_with_login("octo-reviewer\r"), "confirm", "--phase", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("--actor=octo-reviewer", self.passed())

    def gh_path(self):
        """gh が認証済みの形（疎通が通る）。curl は置かない（gh の経路だけを通す）。"""
        fakes = os.path.join(self._tmp.name, "ghbin")
        os.makedirs(fakes, exist_ok=True)
        write(
            os.path.join(fakes, "gh"),
            f"#!/bin/sh\nexec '{sys.executable}' '{github_host.__file__}' gh \"$@\"\n",
        )
        os.chmod(os.path.join(fakes, "gh"), 0o755)
        return os.pathsep.join([fakes, os.environ.get("PATH", "")])

    def test_through_gh_the_owner_is_passed_and_its_stderr_is_not_mixed_in(self):
        done = self.review("resolved", "confirm", "--phase", "1", PATH=self.gh_path())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("--actor=octo-reviewer", self.passed())

    def test_a_slow_gh_is_cut_off_and_no_actor_is_passed(self):
        done = self.review(
            "resolved",
            "confirm",
            "--phase",
            "1",
            PATH=self.gh_path(),
            FAKE_GH_SLOW_USER="5",
            CCNAVI_REVIEW_ACCOUNT_TIMEOUT="1",
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("confirm", self.passed())
        self.assertFalse(any(a.startswith("--actor") for a in self.passed()))

    def test_through_glab_the_username_is_passed(self):
        glab = os.path.join(self._tmp.name, "glabbin", "glab")
        write(
            glab,
            "#!/bin/sh\n"
            "echo 'glab: 雑音' >&2\n"
            'for a in "$@"; do last="$a"; done\n'
            'case "$last" in\n'
            'user) printf \'{"username": "lab-reviewer"}\' ;;\n'
            '*merge_requests\\?*) printf \'[{"iid": 7, "web_url": "u"}]\' ;;\n'
            "*/discussions*|*/reviewers*) printf '[]' ;;\n"
            "*) printf '{}' ;;\n"
            "esac\n",
        )
        os.chmod(glab, 0o755)
        subprocess.run(
            ["git", "-C", self.ws, "remote", "set-url", "origin", "https://gitlab.example/g/p.git"],
            check=True,
        )
        path = os.pathsep.join([os.path.dirname(glab), os.environ.get("PATH", "")])
        done = self.review("resolved", "confirm", "--phase", "1", PATH=path)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("--actor=lab-reviewer", self.passed())

    def test_the_stand_in_refuses_a_query_that_drops_a_field(self):
        q = (
            "query { repository { pullRequest { reviewThreads { pageInfo { hasNextPage endCursor }"
            " nodes { id isResolved comments { nodes { url path line body createdAt } } } } } } }"
        )

        def ask(query):
            body = json.dumps({"query": query, "variables": {"n": 42, "c": None}})
            return github_host.answer("resolved", "POST", "/graphql", body)[1]

        self.assertIn("data", ask(q))
        self.assertIn("createdAt", ask(q.replace(" createdAt", ""))["errors"][0]["message"])

    def test_decide_passes_the_token_owner_and_the_way(self):
        """段階 5: decide の印にも actor と via（ボードは board、端末は terminal）"""
        for args, via in (
            (["decide", "1", "--choices", "{}", "--digest", "d"], "board"),
            (["decide", "1"], "terminal"),
        ):
            with self.subTest(via=via):
                if os.path.exists(self.args):
                    os.remove(self.args)
                done = self.review("resolved", *args)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                passed = self.passed()
                self.assertIn("--accept-unresolved", passed)
                self.assertIn("--actor=octo-reviewer", passed)
                self.assertIn(f"--via={via}", passed)

    def test_decide_without_an_owner_or_for_preview_passes_no_actor(self):
        for args, extra in (
            (["decide", "1", "--choices", "{}", "--digest", "d"], {"FAKE_GITHUB_NO_USER": "1"}),
            (["decide", "1", "--preview"], {}),
        ):
            with self.subTest(args=args):
                if os.path.exists(self.args):
                    os.remove(self.args)
                done = self.review("resolved", *args, **extra)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("--accept-unresolved", self.passed())
                self.assertFalse(any(a.startswith(("--actor", "--via")) for a in self.passed()))

    def test_decide_does_not_take_an_actor_from_the_caller(self):
        done = self.review("resolved", "decide", "1", "--actor=someone")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertFalse(os.path.exists(self.args))

    def test_confirm_does_not_take_an_actor_from_the_caller(self):
        for given in (["--actor", "someone"], ["--actor=someone"]):
            done = self.review("resolved", "confirm", "--phase", "1", *given)
            self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
            self.assertIn("--actor を受けない", done.stderr)
        self.assertFalse(os.path.exists(self.args))


@unittest.skipIf(SHELL is None or not NEEDED, "sh・git・jq のどれかが無い")
class GitLabHostFixtureTest(unittest.TestCase):
    """段階 5: GitLab の見本から sh が組む写しと結論。拡張も同じ期待値と比べる。"""

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
            [*git, "remote", "add", "origin", "https://gitlab.com/acme/widgets.git"], check=True
        )
        self.bin = os.path.join(self._tmp.name, "bin")
        gitlab_host.install(self.bin, sys.executable)
        self.args = os.path.join(self._tmp.name, "args.txt")
        self.exe = write(
            os.path.join(self._tmp.name, "exe", "ccnavi"),
            STUB.format(compat=version.COMPAT, log=self.args),
        )
        os.chmod(self.exe, 0o755)

    def review(self, scene, *args, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        env.pop("GITHUB_TOKEN", None)
        env.update(
            {
                "GITLAB_TOKEN": "glpat-t0k",
                "PATH": os.pathsep.join([self.bin, os.environ.get("PATH", "")]),
                "FAKE_GITLAB_SCENE": scene,
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
            gitlab_host.scene_names(),
            ["hostile", "impostor", "paged", "requested-changes", "resolved"],
        )
        for scene in gitlab_host.scene_names():
            with self.subTest(scene=scene):
                done = self.review(scene, "fetch")
                self.assertEqual(done.returncode, 0, done.stderr)
                copy = json.loads(done.stdout)
                self.assertRegex(copy.pop("fetched_at"), r"^\d{4}-\d\d-\d\dT")
                where = os.path.join(gitlab_host.SCENES, scene)
                with open(os.path.join(where, "scene.json"), encoding="utf-8") as f:
                    poster = json.load(f)["poster"]
                if os.environ.get("CCNAVI_HOST_FIXTURE"):
                    body = json.dumps(copy, ensure_ascii=False, indent=2) + "\n"
                    write(os.path.join(where, "expected.json"), body)
                    body = json.dumps(conclusion(copy, poster), ensure_ascii=False, indent=2)
                    write(os.path.join(where, "conclusion.json"), body + "\n")
                with open(os.path.join(where, "expected.json"), encoding="utf-8") as f:
                    self.assertEqual(copy, json.load(f), f"{scene} の expected.json が古い")
                with open(os.path.join(where, "conclusion.json"), encoding="utf-8") as f:
                    self.assertEqual(conclusion(copy, poster), json.load(f))

    def test_the_scenes_say_what_they_are_for(self):
        def said(scene):
            with open(os.path.join(gitlab_host.SCENES, scene, "conclusion.json")) as f:
                return json.load(f)

        # 依頼したアカウントの ccnavi の依頼のスレッドは数えない（11.8.1 の決定 C）
        self.assertEqual(said("resolved"), {"changes_requested": [], "unresolved": []})
        self.assertEqual(len(said("requested-changes")["changes_requested"]), 1)
        self.assertEqual(said("requested-changes")["unresolved"], [])
        # 目印をまねた別の人のスレッドと、後のノートが未解決のスレッドは数える
        self.assertEqual(len(said("impostor")["unresolved"]), 2)
        self.assertEqual(len(said("paged")["unresolved"]), 11)
        self.assertEqual(len(said("hostile")["unresolved"]), 8)

    def test_confirm_and_decide_pass_the_token_owner(self):
        done = self.review("resolved", "confirm", "--phase", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("--actor=lab-reviewer", self.passed())
        os.remove(self.args)
        done = self.review("resolved", "decide", "1", "--choices", "{}", "--digest", "d")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("--actor=lab-reviewer", self.passed())
        self.assertIn("--via=board", self.passed())


if __name__ == "__main__":
    unittest.main()
