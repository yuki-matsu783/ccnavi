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

from ccnavi.entry import version
from ccnavi.tickets import review
from tests import ROOT
from tests.sh import github_host, gitlab_host

SHELL = shutil.which("sh") or shutil.which("bash")
NEEDED = all(shutil.which(tool) for tool in ("git", "jq"))

# 実行ファイルの代役。受けた引数を 1 行ずつ書き残し、何も書かずに 0 で終わる。
STUB = """#!/bin/sh
case " $* " in
*" --version --json "*)
  printf '{{"compat": {compat}, "flags": ["--accept-unresolved", "--actor", "--via"]}}\\n'
  exit 0 ;;
*" --version "*) printf 'compat: {compat}\\n'; exit 0 ;;
esac
for a in "$@"; do printf '%s\\n' "$a"; done >>'{log}'
exit 0
"""


# ELI5 の HTML の既定の置き場。ワークツリーの wip/ の下にコミットする（ADR-0095）
ELI5 = "wip/eli5/phase-1.html"


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
            "*merge_requests\\?*)\n"
            '  printf \'[{"iid": 7, "web_url": "u", "source_project_id": 1}]\' ;;\n'
            "*/discussions*|*/reviewers*) printf '[]' ;;\n"
            "projects/*) printf '{\"id\": 1}' ;;\n"
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

    def old_exe(self, flags):
        """古い実行ファイルの代役。知らないフラグ（--actor・--via）を渡されると引数の誤りで落ちる。

        `flags` が None なら `--version --json` を知らない（段階 4 より前）。
        387d4a6 の実行ファイルは
        `--actor` を知っていて `--via` を知らない（`["--actor"]`）。
        """
        known = " ".join(flags or [])
        answer = (
            "exit 2"
            if flags is None
            else 'printf \'{"compat": %s, "flags": [%s]}\\n\' '
            + str(version.COMPAT)
            + " '"
            + ", ".join(f'"{f}"' for f in flags or [])
            + "'; exit 0"
        )
        path = write(
            os.path.join(self._tmp.name, "old", "ccnavi"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            f'*" --version --json "*) {answer} ;;\n'
            f"*\" --version \"*) printf 'compat: {version.COMPAT}\\n'; exit 0 ;;\n"
            "esac\n"
            'for a in "$@"; do case "$a" in --actor*|--via*)\n'
            f'  case " {known} " in *" ${{a%%=*}} "*) ;;\n'
            '  *) echo "unrecognized $a" >&2; exit 2 ;; esac ;;\n'
            "esac; done\n"
            f"for a in \"$@\"; do printf '%s\\n' \"$a\"; done >>'{self.args}'\n"
            "exit 0\n",
        )
        os.chmod(path, 0o755)
        return path

    def test_an_old_exe_gets_only_the_flags_it_knows(self):
        """決定 C: `--version --json` の flags に無いフラグは渡さない（古いものが落ちる）。"""
        for flags, confirm_actor in ((None, False), (["--actor"], True)):
            with self.subTest(flags=flags):
                exe = self.old_exe(flags)
                for args in (
                    ["confirm", "--phase", "1"],
                    ["decide", "1", "--choices", "{}", "--digest", "d"],
                    ["decide", "1"],
                ):
                    if os.path.exists(self.args):
                        os.remove(self.args)
                    done = self.review("resolved", *args, CCNAVI_BIN_PATH=exe)
                    self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                    passed = self.passed()
                    self.assertFalse(any(a.startswith("--via") for a in passed), args)
                    want = confirm_actor and args[0] == "confirm"
                    self.assertEqual(any(a.startswith("--actor") for a in passed), want, args)

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

    def origin_of(self, scene):
        """場面の名前空間とプロジェクトに origin を合わせる（入れ子のグループの場面のため）。"""
        with open(os.path.join(gitlab_host.SCENES, scene, "scene.json"), encoding="utf-8") as f:
            meta = json.load(f)
        url = f"https://gitlab.com/{meta['namespace']}/{meta['project']}.git"
        subprocess.run(["git", "-C", self.ws, "remote", "set-url", "origin", url], check=True)

    def test_the_sh_copy_matches_each_recorded_scene(self):
        self.assertEqual(
            gitlab_host.scene_names(),
            [
                "hostile",
                "impostor",
                "nested",
                "odd-types",
                "paged",
                "requested-changes",
                "resolved",
            ],
        )
        for scene in gitlab_host.scene_names():
            with self.subTest(scene=scene):
                self.origin_of(scene)
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
        self.assertEqual(said("nested"), said("resolved"))
        self.assertEqual(len(said("odd-types")["unresolved"]), 2)

    def test_a_fork_merge_request_is_not_picked(self):
        """フォークの同じ名前のブランチの MR（source_project_id が違う）は拾わない。"""
        done = self.review("impostor", "fetch")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["mr"]["number"], 7)

    def test_an_unreadable_project_id_stops_with_a_reason(self):
        """プロジェクトの id を読めなければ、MR が無いと取り違えず（作り直さず）、
        理由を言って止める（11.9.3 の 6）。"""
        done = self.review("impostor", "fetch", FAKE_GITLAB_NO_PROJECT="1")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("プロジェクト", done.stderr)
        self.assertIn("id を読めない", done.stderr)
        self.assertEqual(done.stdout, "")

    def request_stub(self, out):
        """実行ファイルの代役。`review prepare` で本文と下書きを書き、`--result` の写しを控える。

        受け取った引数は 1 行 1 つで `out/args.txt` に溜める。
        """
        os.makedirs(out, exist_ok=True)
        stub = write(
            os.path.join(self._tmp.name, "exe2", "ccnavi"),
            "#!/bin/sh\n"
            'case " $* " in *" --version "*) printf \'compat: %s\\n\' '
            + str(version.COMPAT)
            + "; exit 0 ;; esac\n"
            f"for a in \"$@\"; do printf '%s\\n' \"$a\"; done >>'{out}/args.txt'\n"
            'case " $* " in *" prepare "*)\n'
            f"  printf '<!-- ccnavi:request i0001:1 key=k -->\\n依頼\\n' >'{out}/body.md'\n"
            f"  printf 'Draft: t\\n\\nb\\n' >'{out}/draft.md'\n"
            f"  printf '%s\\n%s\\n' '{out}/body.md' '{out}/draft.md'; exit 0 ;;\n"
            "esac\n"
            'prev=""\n'
            'for a in "$@"; do [ "$prev" = --result ] && cp "$a" '
            f"'{out}/result.json'; prev=\"$a\"; done\n"
            "exit 0\n",
        )
        os.chmod(stub, 0o755)
        return stub

    def eli5(self, rel=ELI5, text="<!doctype html><p>やさしい説明</p>\n", commit=True):
        """ELI5 の HTML をワークツリーに書く。commit なら追跡してコミットする（ADR-0095）。"""
        path = write(os.path.join(self.ws, *rel.split("/")), text)
        if commit:
            git = ["git", "-C", self.ws, "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
            subprocess.run([*git, "add", "--", rel], check=True)
            subprocess.run([*git, "commit", "-q", "-m", f"add {rel}"], check=True)
        return path

    def test_request_records_the_account_that_posted(self):
        """依頼の記録に投稿したアカウント（ノートの author）が入り、打ち直しても 1 度だけ。"""
        out = os.path.join(self._tmp.name, "out")
        stub = self.request_stub(out)
        html = self.eli5()
        state = os.path.join(self._tmp.name, "notes.json")
        extra = {"CCNAVI_BIN_PATH": stub, "FAKE_GITLAB_STATE": state}
        for _ in range(2):
            done = self.review(
                "resolved", "request", "--phase", "1", "--body-file", "x", "--eli5", html, **extra
            )
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
                result = json.load(f)
            # 投稿者は名前でなく id で残す（11.9.1 の 15）
            self.assertEqual((result["host"], result["author"]), ("gitlab", "201"))
            self.assertEqual(result["mr"]["number"], 7)
        with open(state, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 1)

    def test_request_stops_without_an_eli5_html(self):
        """--eli5 の誤りは 2、置き場とコミットの欠けは全部を挙げて 1 で止める（ADR-0097）。

        実行ファイルもホストも触らない。旧方式（追跡しない wip/tmp/ の HTML）も、wip/eli5/ の外の
        wip/ も、名前に `'`・`$`・空白・日本語を含むものも、リンクや実行の印付きも止まる。
        """
        out = os.path.join(self._tmp.name, "out")
        stub = self.request_stub(out)
        state = os.path.join(self._tmp.name, "notes.json")
        extra = {"CCNAVI_BIN_PATH": stub, "FAKE_GITLAB_STATE": state}
        blank = self.eli5("wip/eli5/blank.html", " \n\t\n")
        notes = self.eli5("wip/eli5/eli5.md", "# やさしい説明\n")
        docs = self.eli5("docs/eli5.html")
        blank_docs = self.eli5("docs/blank.html", "\n")
        design = self.eli5("wip/design/x.html")
        old_way = self.eli5("wip/tmp/eli5.html", commit=False)
        dirty = self.eli5("wip/eli5/phase-2.html")
        write(dirty, "<p>書き換えた</p>\n")
        outside = write(os.path.join(self._tmp.name, "elsewhere", "eli5.html"), "<p>x</p>\n")
        quote = self.eli5("wip/eli5/a'$(id)'.html")
        dollar = self.eli5("wip/eli5/a$HOME.html")
        space = self.eli5("wip/eli5/a b.html")
        japanese = self.eli5("wip/eli5/説明.html")
        self.eli5("wip/eli5/target.html")
        link = os.path.join(self.ws, "wip", "eli5", "link.html")
        os.symlink("target.html", link)
        executable = self.eli5("wip/eli5/run.html", commit=False)
        os.chmod(executable, 0o755)
        git = ["git", "-C", self.ws, "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run(
            [
                *git,
                "-c",
                "core.fileMode=true",
                "add",
                "--",
                "wip/eli5/link.html",
                "wip/eli5/run.html",
            ],
            check=True,
        )
        subprocess.run([*git, "commit", "-q", "-m", "link and exec"], check=True)
        untracked = self.eli5("wip/eli5/untracked.html", commit=False)
        bad_name = ["名前に使えない字がある"]
        cases = {
            "無い": ([], 2, []),
            "値が無い": (["--eli5"], 2, []),
            "拡張子が違う": (["--eli5", notes], 2, []),
            "ファイルが無い": (["--eli5", "wip/eli5/missing.html"], 1, ["ファイルが無い"]),
            "空": (["--eli5", blank], 1, ["中身が空"]),
            "wip/ の外": (["--eli5", docs], 1, ["wip/eli5/ の下に無い"]),
            "空で wip/ の外": (["--eli5", blank_docs], 1, ["中身が空", "wip/eli5/ の下に無い"]),
            "wip/eli5/ の外の wip/": (["--eli5", design], 1, ["wip/eli5/ の下に無い"]),
            "旧方式（wip/tmp/）": (["--eli5", old_way], 1, ["wip/eli5/ の下に無い"]),
            "未追跡": (["--eli5", untracked], 1, ["HEAD に無い"]),
            "未コミット": (["--eli5", dirty], 1, ["HEAD から変わっている"]),
            "ワークツリーの外": (["--eli5", outside], 1, ["このワークツリーの外"]),
            "名前に '": (["--eli5", quote], 1, bad_name),
            "名前に $": (["--eli5", dollar], 1, bad_name),
            "名前に空白": (["--eli5", space], 1, bad_name),
            "名前に日本語": (["--eli5", japanese], 1, bad_name),
            "シンボリックリンク": (["--eli5", link], 1, ["普通のファイルでない（モード 120000"]),
            "実行の印付き": (["--eli5", executable], 1, ["普通のファイルでない（モード 100755"]),
        }
        for name, (flag, code, said) in cases.items():
            with self.subTest(name):
                done = self.review(
                    "resolved", "request", "--phase", "1", "--body-file", "x", *flag, **extra
                )
                self.assertEqual(done.returncode, code, done.stdout + done.stderr)
                self.assertIn("--eli5", done.stderr)
                self.assertIn("HTML", done.stderr)
                self.assertIn("wip/eli5/phase-<N>.html", done.stderr)
                listed = [line for line in done.stderr.splitlines() if line.startswith("  - ")]
                self.assertEqual(len(listed), len(said), done.stderr)
                for words in said:
                    self.assertIn(words, done.stderr)
                self.assertFalse(os.path.exists(os.path.join(out, "body.md")))
                self.assertFalse(os.path.exists(state))

    def test_request_points_to_crit_push_and_keeps_eli5_from_the_exe(self):
        """投稿が済んだら、人が打つ crit review <相対> と crit push <番号> を出す（ADR-0095）。

        相対は打った場所から解く。--eli5 は実行ファイルには渡さない。crit・glab が PATH に
        無くても止めない。
        """
        out = os.path.join(self._tmp.name, "out")
        stub = self.request_stub(out)
        self.eli5()
        state = os.path.join(self._tmp.name, "notes.json")
        done = self.review(
            "resolved",
            "request",
            "--phase",
            "1",
            "--body-file",
            "x",
            f"--eli5={ELI5}",
            CCNAVI_BIN_PATH=stub,
            FAKE_GITLAB_STATE=state,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
            number = json.load(f)["mr"]["number"]
        said = [line for line in done.stdout.splitlines() if line.startswith("ELI5 を見る: ")]
        self.assertEqual(len(said), 1, done.stdout)
        # ツリーの絶対パスはいつも '…' で包む（中の ' は '\'' に。ADR-0097）
        self.assertIn("cd '", said[0])
        top = said[0].split("cd ", 1)[1].split(" ", 1)[0].strip("'")
        self.assertTrue(os.path.isabs(top), top)
        self.assertTrue(os.path.samefile(top, self.ws), top)
        self.assertIn(f"crit review {ELI5} ", said[0])
        self.assertIn(f"crit push {number} ", said[0])
        if shutil.which("crit") is None:
            self.assertIn("PATH に crit は無い", done.stdout)
        with open(os.path.join(out, "args.txt"), encoding="utf-8") as f:
            passed = f.read()
        self.assertNotIn("eli5", passed)
        # 依頼の本文には在りかと送り方を 1 行載せ、HTML の中身は載せない
        with open(state, encoding="utf-8") as f:
            body = json.load(f)[0]["body"]
        self.assertTrue(body.startswith("<!-- ccnavi:request i0001:1 key=k -->"), body)
        self.assertIn(f"差分の `{ELI5}`", body)
        self.assertIn(f"`crit review {ELI5}`", body)
        self.assertIn(f"`crit push {number}`", body)
        self.assertNotIn("やさしい説明", body)

    def test_the_tree_path_is_quoted_for_the_shell(self):
        """ツリーの絶対パスに `'`・`$`・空白があっても、案内の cd は 1 語のまま（ADR-0097）。"""
        moved = os.path.join(self._tmp.name, "w s'$(touch pwned)")
        shutil.move(self.ws, moved)
        self.ws = moved
        out = os.path.join(self._tmp.name, "out")
        stub = self.request_stub(out)
        self.eli5()
        state = os.path.join(self._tmp.name, "notes.json")
        done = self.review(
            "resolved",
            "request",
            "--phase",
            "1",
            "--body-file",
            "x",
            f"--eli5={ELI5}",
            CCNAVI_BIN_PATH=stub,
            FAKE_GITLAB_STATE=state,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        said = [line for line in done.stdout.splitlines() if line.startswith("ELI5 を見る: ")]
        self.assertEqual(len(said), 1, done.stdout)
        word = said[0].split("cd ", 1)[1].split(" してから ", 1)[0]
        # 打った人のシェルがその 1 語を読んだ結果が、元の絶対パスと同じで、何も実行されない
        echoed = subprocess.run(
            [SHELL, "-c", f"printf '%s' {word}"],
            cwd=self._tmp.name,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertTrue(os.path.samefile(echoed.stdout, moved), echoed.stdout)
        self.assertFalse(os.path.exists(os.path.join(self._tmp.name, "pwned")))

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
