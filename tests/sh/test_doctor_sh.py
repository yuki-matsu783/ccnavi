"""scripts/ccnavi-doctor.sh の受入テスト。外から動かし、出力と終了コードだけを読む。

道具は隔離した bin だけを PATH にし、glab・curl・git の通信は記録つきの偽物にする。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import signal
import subprocess
import time
import unittest

from tests import ROOT
from tests.sh.install_harness import Env, count_next, have_tools, script, write_exec

DOCTOR = script("ccnavi-doctor.sh")
HOST_SH = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common-host.sh")
GITLAB = "https://gitlab.example.com/g/p.git"


def _host_target() -> str:
    spec = importlib.util.spec_from_file_location("ccnavi_build", os.path.join(ROOT, "build.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_target()


THIS_MACHINE = _host_target()


def checks(out: str) -> dict[str, tuple[str, str, str]]:
    found: dict[str, tuple[str, str, str]] = {}
    for line in out.splitlines():
        if not line.startswith("check: "):
            continue
        parts = line[len("check: ") :].split(" ", 3)
        parts += [""] * (4 - len(parts))
        found[parts[0]] = (parts[1], parts[2], parts[3])
    return found


@unittest.skipUnless(have_tools(), "sh・git・jq・ps が要る")
class DoctorTest(Env):
    def setUp(self):
        super().setUp()
        self.link("jq")
        self.fake_git()
        self.ws = self.git_repo(os.path.join(self.tmpdir, "ws"), GITLAB)
        self.src = self.make_from()

    def make_from(self, target: str | None = None, exe: str | None = "ccnavi") -> str:
        src = os.path.join(self.tmpdir, "src")
        scripts = os.path.join(src, ".ccnavi", "scripts")
        os.makedirs(scripts, exist_ok=True)
        shutil.copy(HOST_SH, scripts)
        os.makedirs(os.path.join(src, "dist", "ccnavi"), exist_ok=True)
        with open(os.path.join(src, "dist", "ccnavi.target"), "w", encoding="utf-8") as f:
            f.write((target or THIS_MACHINE) + "\n")
        if exe:
            write_exec(os.path.join(src, "dist", "ccnavi", exe), "#!/bin/sh\nexit 0\n")
        return src

    def doctor(self, *args: str, env: dict | None = None):
        argv = [DOCTOR, "--workspace", self.ws, "--from", self.src, *args]
        return self.run_sh(argv, env=env)

    def write_settings(self, name: str, text: str) -> None:
        path = os.path.join(self.ws, ".claude", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    # ---- 形

    def test_lines_have_the_documented_shape_and_one_next(self):
        done = self.doctor()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for line in done.stdout.splitlines():
            if line.startswith("check: "):
                level = line.split(" ")[2]
                self.assertIn(level, ("ok", "warn", "todo", "ng"), line)
        self.assertEqual(count_next(done.stdout), 1)
        last = done.stdout.splitlines()[-1]
        self.assertTrue(last.startswith("次: sh '"), last)
        self.assertIn(
            "ccnavi-install.sh' '" + self.ws + "' --from '" + self.src + "' --check", last
        )

    def test_no_next_prints_no_guidance(self):
        done = self.doctor("--no-next")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(count_next(done.stdout), 0)
        self.assertNotIn("案内:", done.stdout)

    def test_todo_guidance_comes_before_next(self):
        done = self.doctor()
        lines = done.stdout.splitlines()
        guide = [i for i, ln in enumerate(lines) if ln.startswith("案内: glab が todo")]
        self.assertEqual(len(guide), 1, done.stdout)
        self.assertLess(guide[0], len(lines) - 1)

    def test_bad_arguments_exit_2(self):
        self.assertEqual(self.run_sh([DOCTOR, "--nope"]).returncode, 2)
        self.assertEqual(self.run_sh([DOCTOR, "--mode", "x"]).returncode, 2)
        self.assertEqual(self.run_sh([DOCTOR, "--workspace", "/no/such/dir"]).returncode, 2)

    def test_zip_file_as_from_is_refused(self):
        zipped = os.path.join(self.tmpdir, "a.zip")
        with open(zipped, "wb") as f:
            f.write(b"PK")
        done = self.run_sh([DOCTOR, "--workspace", self.ws, "--from", zipped])
        self.assertEqual(done.returncode, 2)
        self.assertIn("zip", done.stderr)

    # ---- 道具

    def test_machine_and_shell(self):
        got = checks(self.doctor().stdout)
        self.assertEqual(got["machine"][:2], ("ok", THIS_MACHINE))
        self.assertEqual(got["shell"][0], "ok")
        self.assertIn(got["shell"][1], ("posix", "wsl", "gitbash"))

    def test_git_missing_is_ng_and_exit_1(self):
        self.unlink("git")
        done = self.doctor()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(checks(done.stdout)["git"][:2], ("ng", "not-installed"))
        self.assertIn(
            "次: ng の項目をユーザに直してもらって返事を待ち、doctor を打ち直す", done.stdout
        )
        self.assertEqual(count_next(done.stdout), 1)

    def test_git_too_old_is_todo(self):
        self.fake("git", "case \"$*\" in --version) echo 'git version 2.30.2';; esac")
        got = checks(self.doctor().stdout)
        self.assertEqual(got["git"][:2], ("todo", "too-old"))

    def test_jq_missing_is_ng_and_candidate_is_not_on_path(self):
        self.unlink("jq")
        got = checks(self.doctor().stdout)
        self.assertEqual(got["jq"][:2], ("ng", "not-installed"))
        cand = os.path.join(self.tmpdir, "cand dir")
        os.makedirs(cand)
        write_exec(os.path.join(cand, "jq.exe"), "#!/bin/sh\nexit 0\n")
        env = self.env(CCNAVI_DOCTOR_JQ_CANDIDATES="/nope/jq\n" + os.path.join(cand, "j*.exe"))
        got = checks(self.doctor(env=env).stdout)
        self.assertEqual(got["jq"][:2], ("ng", "not-on-path"))
        self.assertTrue(got["jq"][2].endswith("jq.exe"), got["jq"])

    def test_curl_missing_is_todo_without_glab_and_warn_with_glab(self):
        self.assertEqual(checks(self.doctor().stdout)["curl"][:2], ("todo", "not-installed"))
        self.fake_glab()
        self.assertEqual(checks(self.doctor().stdout)["curl"][:2], ("warn", "not-installed"))

    def test_workspace_not_git(self):
        plain = os.path.join(self.tmpdir, "plain")
        os.makedirs(plain)
        done = self.run_sh([DOCTOR, "--workspace", plain, "--from", self.src])
        got = checks(done.stdout)
        self.assertEqual(got["workspace"][:2], ("warn", "not-git"))
        self.assertEqual(got["origin"][:2], ("warn", "none"))

    def test_pkg_manager_lists_all_available(self):
        self.fake("winget", "exit 0")
        self.fake("scoop", "exit 0")
        got = checks(self.doctor().stdout)
        self.assertEqual(got["pkg-manager"][:2], ("ok", "winget,scoop"))
        self.unlink("winget")
        self.unlink("scoop")
        self.assertEqual(checks(self.doctor().stdout)["pkg-manager"][:2], ("warn", "none"))

    # ---- glab

    def test_glab_states(self):
        got = checks(self.doctor().stdout)
        self.assertEqual(got["glab"][:2], ("todo", "not-installed"))
        self.fake_glab(credential=False)
        got = checks(self.doctor().stdout)
        self.assertEqual(got["glab"][:2], ("todo", "too-old"))
        self.assertEqual(got["glab"][2], "1.52.0")
        self.fake_glab(credential=True)
        got = checks(self.doctor().stdout)
        self.assertEqual(got["glab"], ("ok", "installed", "1.52.0"))

    def test_glab_found_in_candidate_place(self):
        cand = os.path.join(self.tmpdir, "gl dir")
        os.makedirs(cand)
        glab = os.path.join(cand, "glab")
        write_exec(glab, "#!/bin/sh\nexit 0\n")
        env = self.env(CCNAVI_DOCTOR_GLAB_CANDIDATES="\n" + os.path.join(cand, "g*b") + "\n")
        got = checks(self.doctor(env=env).stdout)
        self.assertEqual(got["glab"], ("todo", "not-on-path", glab))

    def test_glab_candidates_default_when_unset(self):
        env = self.env()
        del env["CCNAVI_DOCTOR_GLAB_CANDIDATES"]
        done = self.doctor(env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(checks(done.stdout)["glab"][1], ("not-installed", "not-on-path"))

    def test_nothing_communicates_without_online(self):
        self.fake_glab()
        self.fake_curl()
        self.doctor(env=self.env(GITLAB_TOKEN="x"))
        text = self.calls_text()
        self.assertNotIn("ls-remote", text)
        self.assertNotIn("glab api", text)
        self.assertEqual(
            [ln for ln in text.splitlines() if ln.startswith("curl ")], ["curl --version"]
        )
        self.assertIn("glab auth --help", text)

    # ---- origin

    def test_origin_hides_credentials_and_keeps_the_port(self):
        subprocess.run(
            ["git", "-C", self.ws, "remote", "set-url", "origin"]
            + ["https://user:tok@gitlab.example.com:8929/g/p.git"],
            check=True,
            env={"PATH": os.environ["PATH"]},
        )
        done = self.doctor()
        got = checks(done.stdout)
        self.assertEqual(got["origin"], ("ok", "https", "gitlab.example.com:8929"))
        self.assertNotIn("tok", done.stdout + done.stderr)

    def test_origin_kinds(self):
        def origin(url: str):
            subprocess.run(
                ["git", "-C", self.ws, "remote", "set-url", "origin", url],
                check=True,
                env={"PATH": os.environ["PATH"]},
            )
            return checks(self.doctor().stdout)["origin"]

        self.assertEqual(origin("http://localhost:8929/g/p.git"), ("ok", "http", "localhost:8929"))
        self.assertEqual(
            origin("git@gitlab.example.com:g/p.git"), ("warn", "ssh", "gitlab.example.com")
        )
        self.assertEqual(origin("https://github.com/o/r.git")[:2], ("warn", "github"))
        self.assertEqual(origin("git@github.com:o/r.git")[:2], ("warn", "github"))
        self.assertEqual(origin("/srv/repo.git")[:2], ("warn", "unknown"))

    # ---- --online

    def online(self, **extra: str):
        return self.doctor("--online", env=self.env(**extra))

    def test_online_remote_ok_means_readable_only(self):
        got = checks(self.online().stdout)
        self.assertEqual(got["remote"][:2], ("ok", "readable"))
        self.assertIn("push", got["remote"][2])
        self.assertIn("ls-remote --exit-code origin HEAD", self.calls_text())

    def test_online_remote_failure_is_masked(self):
        self.ctl_set("ls", "fail")
        done = self.online()
        got = checks(done.stdout)
        self.assertEqual(got["remote"][:2], ("todo", "unreachable"))
        self.assertNotIn("tok", done.stdout)
        self.assertIn("<伏せた>", got["remote"][2])

    def test_online_empty_remote_is_readable(self):
        self.ctl_set("ls", "empty")
        self.assertEqual(checks(self.online().stdout)["remote"][:2], ("ok", "readable"))

    def test_curl_401_is_unauthorized_and_never_ok(self):
        self.fake_curl("401")
        done = self.online(GITLAB_TOKEN="x")
        got = checks(done.stdout)
        self.assertEqual(got["host-api"][:2], ("todo", "unauthorized"))
        self.assertEqual(got["glab"][:2], ("todo", "not-installed"))
        self.assertNotIn("optional:", done.stdout)

    def test_curl_failure_kinds(self):
        for mode, code in (
            ("404", "not-found"),
            ("timeout", "timeout"),
            ("unreach", "unreachable"),
        ):
            self.fake_curl(mode)
            got = checks(self.online(GITLAB_TOKEN="x").stdout)
            self.assertEqual(got["host-api"][:2], ("todo", code), mode)

    def test_no_token_no_curl_no_jq_codes(self):
        self.fake_curl("ok")
        self.assertEqual(checks(self.online().stdout)["host-api"][:2], ("todo", "token-missing"))
        self.unlink("curl")
        self.assertEqual(
            checks(self.online(GITLAB_TOKEN="x").stdout)["host-api"][:2], ("todo", "no-curl")
        )
        self.unlink("jq")
        self.assertEqual(
            checks(self.online(GITLAB_TOKEN="x").stdout)["host-api"][:2], ("todo", "no-jq")
        )

    def test_without_glab_but_reachable_glab_is_optional(self):
        self.fake_curl("ok")
        done = self.online(GITLAB_TOKEN="x")
        got = checks(done.stdout)
        self.assertEqual(got["host-api"][:2], ("ok", "curl"))
        self.assertEqual(got["remote"][:2], ("ok", "readable"))
        self.assertEqual(got["glab"][:2], ("warn", "not-installed"))
        self.assertTrue(got["glab"][2].startswith("optional:"), got["glab"])
        self.assertNotIn("案内: glab", done.stdout)
        self.assertEqual(done.returncode, 0)

    def test_old_glab_is_optional_too_but_keeps_the_code(self):
        self.fake_glab(credential=False, api_ok=False)
        self.fake_curl("ok")
        got = checks(self.online(GITLAB_TOKEN="x").stdout)
        self.assertEqual(got["glab"][:2], ("warn", "too-old"))
        self.assertTrue(got["glab"][2].startswith("optional:"))

    def test_glab_api_is_used_when_it_works(self):
        self.fake_glab(api_ok=True)
        got = checks(self.online().stdout)
        self.assertEqual(got["host-api"][:2], ("ok", "glab"))
        self.assertEqual(got["glab"][:2], ("ok", "installed"))

    def test_host_api_is_skipped_for_github(self):
        subprocess.run(
            ["git", "-C", self.ws, "remote", "set-url", "origin", "https://github.com/o/r.git"],
            check=True,
            env={"PATH": os.environ["PATH"]},
        )
        got = checks(self.online().stdout)
        self.assertNotIn("host-api", got)

    def test_remote_is_skipped_without_origin(self):
        subprocess.run(
            ["git", "-C", self.ws, "remote", "remove", "origin"],
            check=True,
            env={"PATH": os.environ["PATH"]},
        )
        got = checks(self.online().stdout)
        self.assertEqual(got["remote"][:2], ("warn", "skipped"))

    # ---- existing

    def existing(self, settings: str | None = None, local: str | None = None, **kw) -> tuple:
        if settings is not None:
            self.write_settings("settings.json", settings)
        if local is not None:
            self.write_settings("settings.local.json", local)
        return checks(self.doctor(**kw).stdout)["existing"]

    def test_existing_values(self):
        self.assertEqual(self.existing()[:2], ("ok", "none"))
        self.assertEqual(self.existing('{"env":{"CCNAVI_MODE":"dry-run"}}')[:2], ("ok", "dry-run"))
        self.assertEqual(self.existing('{"env":{"CCNAVI_MODE":"enable"}}')[:2], ("warn", "enable"))
        self.assertEqual(self.existing('{"env":{"CCNAVI_MODE":"Enable"}}')[:2], ("warn", "enable"))
        got = self.existing('{"env":{"CCNAVI_MODE":"nope"}}')
        self.assertEqual(got, ("warn", "other", "nope"))

    def test_existing_reads_json_not_text(self):
        one_line = '{"a":1,"env":{"x":"y","CCNAVI_MODE":"enable"},"b":[1,2]}'
        self.assertEqual(self.existing(one_line)[:2], ("warn", "enable"))
        escaped = '{"env":{"CCNAVI_MODE":"\\u0065nable"}}'
        self.assertEqual(self.existing(escaped)[:2], ("warn", "enable"))
        commented = '{"note":"CCNAVI_MODE enable","env":{"CCNAVI_MODE":"dry-run"}}'
        self.assertEqual(self.existing(commented)[:2], ("ok", "dry-run"))

    def test_existing_local_overrides(self):
        enable = '{"env":{"CCNAVI_MODE":"enable"}}'
        dry = '{"env":{"CCNAVI_MODE":"dry-run"}}'
        self.assertEqual(self.existing(enable, dry)[:2], ("ok", "dry-run"))
        self.assertEqual(self.existing(dry, enable)[:2], ("warn", "enable"))

    def test_existing_invalid_and_unchecked(self):
        got = self.existing('{"env": ', None)
        self.assertEqual(got, ("warn", "invalid", ".claude/settings.json"))
        self.write_settings("settings.json", '{"env":{"CCNAVI_MODE":"enable"}}')
        self.unlink("jq")
        got = checks(self.doctor().stdout)["existing"]
        self.assertEqual(got[:2], ("warn", "unchecked"))

    # ---- 配布元

    def test_package_states(self):
        self.assertEqual(checks(self.doctor().stdout)["package"], ("ok", "ok", THIS_MACHINE))
        os.remove(os.path.join(self.src, "dist", "ccnavi", "ccnavi"))
        got = checks(self.doctor().stdout)["package"]
        self.assertEqual(got[:2], ("ng", "no-executable"))
        write_exec(os.path.join(self.src, "dist", "ccnavi", "ccnavi.exe"), "#!/bin/sh\n")
        self.assertEqual(checks(self.doctor().stdout)["package"][:2], ("ok", "ok"))
        os.remove(os.path.join(self.src, "dist", "ccnavi.target"))
        done = self.doctor()
        self.assertEqual(checks(done.stdout)["package"][:2], ("ng", "missing"))
        self.assertEqual(done.returncode, 1)

    def test_package_wrong_target(self):
        other = "windows-x86_64" if not THIS_MACHINE.startswith("windows") else "linux-x86_64"
        self.make_from(target=other)
        got = checks(self.doctor().stdout)["package"]
        self.assertEqual(got[:2], ("ng", "wrong-target"))
        self.assertEqual(got[2], other)

    def test_package_quarantine_is_read_not_cleared(self):
        self.fake("xattr", 'case "$*" in "-p com.apple.quarantine"*) exit 0;; esac; exit 1')
        got = checks(self.doctor().stdout)["package"]
        self.assertEqual(got[:2], ("warn", "quarantined"))
        self.assertNotIn(
            "-d", " ".join(ln for ln in self.calls_text().splitlines() if "xattr" in ln)
        )

    def test_build_mode_items(self):
        build = os.path.join(self.src, "build.py")
        done = self.doctor("--mode", "build")
        got = checks(done.stdout)
        self.assertEqual(got["uv"][:2], ("ng", "not-installed"))
        self.assertEqual(got["source"][:2], ("ng", "missing"))
        self.assertNotIn("package", got)
        self.assertEqual(got["node"][:2], ("warn", "not-installed"))
        with open(build, "w", encoding="utf-8") as f:
            f.write("")
        self.fake("uv", "echo uv 0.5.0")
        done = self.doctor("--mode", "build", "--with-extensions")
        got = checks(done.stdout)
        self.assertEqual(got["uv"][0], "ok")
        self.assertEqual(got["source"][0], "ok")
        self.assertEqual(got["node"][:2], ("ng", "not-installed"))
        self.assertEqual(done.returncode, 1)
        done = self.doctor("--mode", "build")
        self.assertEqual(done.returncode, 0, done.stdout)
        last = done.stdout.splitlines()[-1]
        self.assertIn("--build --source '" + self.src + "' --check", last)

    def test_with_extensions_needs_build_mode(self):
        self.assertEqual(self.doctor("--with-extensions").returncode, 2)

    # ---- 時間の上限と後始末

    def slow_env(self, ls_sleep: str, limit: str = "2.718") -> dict:
        self.ctl_set("ls", "sleep")
        self.ctl_set("ls_sleep", ls_sleep)
        return self.env(CCNAVI_DOCTOR_GIT_LIMIT=limit)

    def test_timeout_ends_and_leaves_no_children(self):
        env = self.slow_env("7311")
        start = time.time()
        done = self.doctor("--online", env=env)
        took = time.time() - start
        got = checks(done.stdout)
        self.assertEqual(got["remote"][:2], ("todo", "timeout"), done.stdout)
        self.assertLess(took, 15)
        self.assertEqual(self.wait_gone("sleep 7311"), [])
        self.assertEqual(self.wait_gone("sleep 2.718"), [])

    def test_normal_finish_leaves_no_watchdog_sleep(self):
        env = self.env(CCNAVI_DOCTOR_GIT_LIMIT="2.719")
        self.doctor("--online", env=env)
        self.assertEqual(self.wait_gone("sleep 2.719"), [])

    def test_hanging_glab_is_cut(self):
        self.fake("glab", "sleep 7312 & wait")
        env = self.env(CCNAVI_DOCTOR_GIT_LIMIT="2.717")
        start = time.time()
        done = self.doctor(env=env)
        self.assertLess(time.time() - start, 15)
        # 応答しない glab を「古い」とは言わない
        self.assertEqual(checks(done.stdout)["glab"][:2], ("todo", "timeout"))
        self.assertEqual(self.wait_gone("sleep 7312"), [])
        self.assertEqual(self.wait_gone("sleep 2.717"), [])

    def test_child_that_ignores_term_is_killed(self):
        self.fake("glab", "trap '' TERM\nsleep 7315 &\nwait")
        env = self.env(CCNAVI_DOCTOR_GIT_LIMIT="2.716")
        start = time.time()
        done = self.run_sh(
            [DOCTOR, "--workspace", self.ws, "--from", self.src], env=env, timeout=40
        )
        self.assertLess(time.time() - start, 20)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.wait_gone("sleep 7315"), [])

    def test_failing_glab_is_unavailable_not_too_old(self):
        self.fake("glab", "exit 3")
        self.assertEqual(checks(self.doctor().stdout)["glab"][:2], ("todo", "unavailable"))

    def test_failure_text_hides_token_shapes_and_the_env_token(self):
        self.ctl_set("ls", "leak")
        done = self.online(GITLAB_TOKEN="s3cr3tvalue99")
        for secret in ("abc12345xyz", "ghp_abcdefghijkl", "glrt-abcdefghij12", "s3cr3tvalue99"):
            self.assertNotIn(secret, done.stdout + done.stderr)
        self.assertEqual(checks(done.stdout)["remote"][:2], ("todo", "unreachable"))

    def test_limits_must_be_numbers(self):
        bad = self.doctor(env=self.env(CCNAVI_DOCTOR_GIT_LIMIT="abc"))
        self.assertEqual(bad.returncode, 2)
        bad = self.doctor(env=self.env(CCNAVI_DOCTOR_CURL_LIMIT="2.5"))
        self.assertEqual(bad.returncode, 2)

    def test_inside_dot_git_is_not_a_work_tree(self):
        done = self.run_sh(
            [DOCTOR, "--workspace", os.path.join(self.ws, ".git")] + ["--from", self.src]
        )
        self.assertEqual(checks(done.stdout)["workspace"][:2], ("warn", "not-git"))

    def test_dash_named_workspace_is_found(self):
        odd = os.path.join(self.tmpdir, "-odd")
        os.makedirs(odd)
        done = self.run_sh([DOCTOR, "--workspace", "-odd", "--from", self.src], cwd=self.tmpdir)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def signal_run(self, sig: int, needle: str) -> subprocess.Popen:
        env = self.slow_env(needle, limit="30.123")
        proc = subprocess.Popen(
            [os.path.join(self.bin, "sh"), DOCTOR, "--workspace", self.ws, "--from", self.src]
            + ["--online"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
        )
        end = time.time() + 20
        while time.time() < end and not self.running("sleep " + needle):
            time.sleep(0.1)
        self.assertTrue(self.running("sleep " + needle), "偽の git が起動しなかった")
        proc.send_signal(sig)
        try:
            proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            self.fail("信号のあとも終わらなかった")
        return proc

    def test_int_cleans_up(self):
        proc = self.signal_run(signal.SIGINT, "7313")
        self.assertEqual(proc.returncode, 130)
        self.assertEqual(self.wait_gone("sleep 7313"), [])
        self.assertEqual(self.wait_gone("sleep 30.123"), [])

    def test_term_cleans_up(self):
        proc = self.signal_run(signal.SIGTERM, "7314")
        self.assertEqual(proc.returncode, 143)
        self.assertEqual(self.wait_gone("sleep 7314"), [])
        self.assertEqual(self.wait_gone("sleep 30.123"), [])

    def test_temp_dir_is_removed(self):
        tmp = os.path.join(self.tmpdir, "tmp")
        os.makedirs(tmp)
        self.doctor("--online", env=self.env(TMPDIR=tmp))
        self.assertEqual(os.listdir(tmp), [])

    def test_source_is_untouched(self):
        from tests.sh.install_harness import snapshot

        before = snapshot(self.src)
        self.doctor("--online", env=self.env(GITLAB_TOKEN="x"))
        self.assertEqual(snapshot(self.src), before)


@unittest.skipUnless(have_tools(), "sh・git・jq・ps が要る")
class HostPartLoadTest(Env):
    """host 部品を単体で `.` 読み込みする使い方（doctor の使い方）が成り立つことの検査。

    host 部品がほかの部品の関数に頼るようになったら、ここが落ちる。
    """

    def test_host_part_loads_alone_and_has_the_functions(self):
        self.link("jq")
        names = "ccnavi_host_parse ccnavi_host_connect ccnavi_host_api ccnavi_host_encoded_path"
        probe = (
            f'. "{HOST_SH}"\n'
            f"for f in {names} ccnavi_git_auth; do\n"
            '  command -v "$f" >/dev/null || { echo "missing $f"; exit 1; }\n'
            "done\n"
            'ccnavi_host_parse "https://u:tok@GitLab.example.com:8929/g/p.git" || exit 2\n'
            'printf "%s|%s|%s|%s\\n" "$ccnavi_h_scheme" "$ccnavi_h_host" "$ccnavi_h_kind" '
            '"$ccnavi_h_path"\n'
        )
        done = self.run_sh(["-c", probe])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.strip(), "https|GitLab.example.com:8929|gitlab|g/p")
        self.assertNotIn("tok", done.stdout)


if __name__ == "__main__":
    unittest.main()
