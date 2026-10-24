"""scripts/ccnavi-install.sh の受入テスト。外から動かし、出力・終了コード・書かれたものを読む。

配布元は偽物の組み立て（ccnavi-setup.sh は引数を記録する偽物。doctor と host 部品は本物）。
道具は隔離した bin だけを PATH にする。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import unittest

from tests import ROOT
from tests.sh.install_harness import (
    REAL_GIT,
    Env,
    count_next,
    have_tools,
    script,
    snapshot,
    write_exec,
)
from tests.sh.test_doctor_sh import THIS_MACHINE

INSTALL = script("ccnavi-install.sh")
HOST_SH = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common-host.sh")


@unittest.skipUnless(have_tools(), "sh・git・jq・ps が要る")
class InstallTest(Env):
    def setUp(self):
        super().setUp()
        self.link("jq")
        self.fake_git()
        self.ws = self.git_repo(
            os.path.join(self.tmpdir, "ws"), "https://u:tok@gl.example.com/g/p.git"
        )
        self.src = self.make_src(os.path.join(self.tmpdir, "src"))

    # ---- 偽の配布元

    def make_src(self, root: str) -> str:
        os.makedirs(os.path.join(root, "scripts"))
        for name in ("ccnavi-doctor.sh", "ccnavi-install.sh"):
            shutil.copy(script(name), os.path.join(root, "scripts", name))
        hostdir = os.path.join(root, ".ccnavi", "scripts")
        os.makedirs(hostdir)
        shutil.copy(HOST_SH, hostdir)
        os.makedirs(os.path.join(root, "dist", "ccnavi"))
        with open(os.path.join(root, "dist", "ccnavi.target"), "w", encoding="utf-8") as f:
            f.write(THIS_MACHINE + "\n")
        write_exec(os.path.join(root, "dist", "ccnavi", "ccnavi"), "#!/bin/sh\nexit 0\n")
        skills = os.path.join(root, ".claude", "skills", "foo")
        os.makedirs(skills)
        with open(os.path.join(skills, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("new foo\n")
        write_exec(os.path.join(root, "scripts", "ccnavi-setup.sh"), self.setup_text())
        return root

    def setup_text(self) -> str:
        launcher = (
            "#!/bin/sh\n"
            f'printf \'launcher %s\\n\' "$*" >> "{self.calls}"\n'
            f'printf "error 0 件、warn 2 件\\n"\n'
            f'exit "$(cat "{self.ctl_ref("launcher_rc")}" 2>/dev/null || echo 0)"\n'
        )
        return (
            f'printf \'setup %s\\n\' "$*" >> "{self.calls}"\n'
            "ws=$1\n"
            'case "$*" in\n'
            "*--check*)\n"
            f'\trc=$(cat "{self.ctl_ref("check_rc")}" 2>/dev/null || echo 0)\n'
            '\tprintf "(setup check)\\n"\n'
            '\texit "$rc"\n'
            "\t;;\n"
            "esac\n"
            f'rc=$(cat "{self.ctl_ref("setup_rc")}" 2>/dev/null || echo 0)\n'
            '[ "$rc" = 0 ] || { echo "setup failed" >&2; exit "$rc"; }\n'
            'mkdir -p "$ws/.ccnavi/scripts" "$ws/.ccnavi/bin/' + THIS_MACHINE + '" "$ws/.claude"\n'
            f"printf '%s' {shell_quote(launcher)} > \"$ws/.ccnavi/scripts/ccnavi-launcher.sh\"\n"
            f'printf "#!/bin/sh\\n" > "$ws/.ccnavi/bin/{THIS_MACHINE}/ccnavi"\n'
            f'chmod 644 "$ws/.ccnavi/bin/{THIS_MACHINE}/ccnavi"\n'
            '[ -f "$ws/.claude/settings.json" ] || '
            '\techo \'{"env":{"CCNAVI_MODE":"dry-run"}}\' > "$ws/.claude/settings.json"\n'
            "exit 0\n"
        )

    def install(self, *args: str, env: dict | None = None, ws: str | None = None):
        return self.run_sh([INSTALL, ws or self.ws, *args], env=env)

    def setup_calls(self) -> list[list[str]]:
        return [
            ln.split(" ")[1:] for ln in self.calls_text().splitlines() if ln.startswith("setup ")
        ]

    def write_settings(self, name: str, text: str) -> None:
        path = os.path.join(self.ws, ".claude", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def one_next(self, done) -> None:
        self.assertEqual(count_next(done.stdout), 1, done.stdout)

    # ---- 配布元の決め方

    def test_from_directly_and_one_level_down(self):
        done = self.install("--from", self.src, "--check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        parent = os.path.join(self.tmpdir, "parent")
        os.makedirs(parent)
        inner = self.make_src(os.path.join(parent, "pkg"))
        self.calls_reset()
        done = self.install("--from", parent, "--check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.setup_calls()[0][:3], [self.ws, "--deploy", inner])

    def calls_reset(self) -> None:
        if os.path.exists(self.calls):
            os.remove(self.calls)

    def test_two_candidates_one_level_down_is_3(self):
        parent = os.path.join(self.tmpdir, "parent")
        os.makedirs(parent)
        self.make_src(os.path.join(parent, "a"))
        self.make_src(os.path.join(parent, "b"))
        done = self.install("--from", parent, "--check")
        self.assertEqual(done.returncode, 3)
        self.one_next(done)
        self.assertEqual(self.setup_calls(), [])

    def test_no_candidate_is_3(self):
        empty = os.path.join(self.tmpdir, "empty")
        os.makedirs(empty)
        done = self.install("--from", empty, "--check")
        self.assertEqual(done.returncode, 3)
        self.one_next(done)

    def test_zip_file_is_2(self):
        zipped = os.path.join(self.tmpdir, "a.zip")
        with open(zipped, "wb") as f:
            f.write(b"PK")
        done = self.install("--from", zipped, "--check")
        self.assertEqual(done.returncode, 2)
        self.assertIn("zip", done.stderr)

    def test_default_source_is_one_up_from_the_script(self):
        done = self.run_sh(
            [os.path.join(self.src, "scripts", "ccnavi-install.sh"), self.ws, "--check"]
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.setup_calls()[0][2], os.path.realpath(self.src))

    def test_workspace_equal_to_source_is_3(self):
        done = self.install("--from", self.src, "--check", ws=self.src)
        self.assertEqual(done.returncode, 3)

    def test_workspace_that_is_a_link_to_or_inside_the_source_is_3(self):
        link = os.path.join(self.tmpdir, "link-to-src")
        os.symlink(self.src, link)
        before = snapshot(self.src)
        for ws in (link, os.path.join(self.src, "scripts")):
            done = self.install("--from", self.src, ws=ws)
            self.assertEqual(done.returncode, 3, ws + done.stdout)
            self.one_next(done)
        self.assertEqual(snapshot(self.src), before)

    def test_source_unpacked_inside_the_workspace_is_fine(self):
        inner = self.make_src(os.path.join(self.ws, "unpacked"))
        done = self.install("--from", inner, "--check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_argument_errors_are_2(self):
        self.assertEqual(self.install("--nope").returncode, 2)
        self.assertEqual(self.install("--mode", "disable", "--from", self.src).returncode, 2)
        self.assertEqual(self.install("--with-extensions", "--from", self.src).returncode, 2)
        self.assertEqual(self.install("--source", self.src, "--from", self.src).returncode, 2)
        self.assertEqual(self.run_sh([INSTALL]).returncode, 2)
        for bad in ("--from", "--mode", "--ticket-control", "--clear-quarantine", "--force"):
            args = (
                ["--build", bad]
                if bad in ("--clear-quarantine", "--force")
                else ["--build", bad, "x"]
            )
            self.assertEqual(self.install(*args).returncode, 2, bad)

    # ---- --check と配布元

    def test_check_does_not_change_the_source(self):
        before = snapshot(self.src)
        done = self.install("--from", self.src, "--check")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(snapshot(self.src), before)

    def test_real_run_does_not_change_the_source_either(self):
        before = snapshot(self.src)
        done = self.install("--from", self.src, "--force", "--clear-quarantine")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(snapshot(self.src), before)

    # ---- doctor との関係

    def test_ng_stops_before_setup(self):
        self.unlink("git")
        done = self.install("--from", self.src)
        self.assertEqual(done.returncode, 3)
        self.assertEqual(self.setup_calls(), [])
        self.one_next(done)
        self.assertIn("check: git ng not-installed", done.stdout)

    def test_todo_only_goes_on(self):
        done = self.install("--from", self.src)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("check: glab todo not-installed", done.stdout)
        self.assertEqual(len(self.setup_calls()), 1)
        self.one_next(done)
        self.assertIn("案内: glab が todo（not-installed）", done.stdout)

    def test_only_five_options_reach_setup(self):
        done = self.install(
            *("--from", self.src, "--mode", "enable", "--ticket-control", "disable"),
            *("--force", "--no-vscode", "--no-fetch", "--clear-quarantine", "--check"),
            env=self.env(),
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = self.setup_calls()[0]
        want = [self.ws, "--deploy", self.src, "--mode", "enable", "--ticket-control", "disable"]
        want += ["--force", "--no-vscode", "--no-fetch", "--check"]
        self.assertEqual(got, want)

    def test_plain_real_run_passes_only_ws_and_deploy(self):
        done = self.install("--from", self.src)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.setup_calls(), [[self.ws, "--deploy", self.src]])

    # ---- setup の終了コード

    def test_check_exit_codes(self):
        for setup_rc, want in (("0", 0), ("1", 1), ("2", 4)):
            self.ctl_set("check_rc", setup_rc)
            done = self.install("--from", self.src, "--check")
            self.assertEqual(done.returncode, want, setup_rc + done.stdout)
            self.one_next(done)

    def test_check_next_text_per_code(self):
        self.ctl_set("check_rc", "0")
        self.assertIn("すでに揃っている", self.install("--from", self.src, "--check").stdout)
        self.ctl_set("check_rc", "1")
        done = self.install("--from", self.src, "--check", "--force")
        self.assertIn("了承を得てから、--check を外して打つ", done.stdout)
        self.assertIn("references/update.md", done.stdout)
        self.ctl_set("check_rc", "2")
        self.assertIn(
            "setup の出力の理由を直してから", self.install("--from", self.src, "--check").stdout
        )

    def test_real_run_setup_failure_is_4(self):
        self.ctl_set("setup_rc", "2")
        done = self.install("--from", self.src)
        self.assertEqual(done.returncode, 4)
        self.assertIn("setup failed", done.stderr)
        self.one_next(done)
        self.assertEqual(
            [c for c in self.calls_text().splitlines() if c.startswith("launcher")], []
        )

    # ---- 入れてよい状態

    def test_enable_inside_claude_code_is_3(self):
        self.write_settings("settings.json", '{"env":{"CCNAVI_MODE":"enable"}}')
        done = self.install("--from", self.src, env=self.env(CLAUDECODE="1"))
        self.assertEqual(done.returncode, 3)
        self.one_next(done)
        self.assertIn("Claude Code の外のターミナルで同じコマンドを打ってもらう", done.stdout)
        self.assertEqual(self.setup_calls(), [])
        done = self.install("--from", self.src, "--check", env=self.env(CLAUDECODE="1"))
        self.assertEqual(done.returncode, 3)

    def test_anything_but_dry_run_counts_as_enable_inside_claude_code(self):
        # ccnavi は dry-run 以外の値を enable として扱う
        for value in ("enable ", "ENABLE", "enabled", "disable", "on"):
            self.write_settings("settings.json", mode_settings(value))
            done = self.install("--from", self.src, "--check", env=self.env(CLAUDECODE="1"))
            self.assertEqual(done.returncode, 3, value)
        for value in ("dry-run", " dry-run "):
            self.write_settings("settings.json", mode_settings(value))
            done = self.install("--from", self.src, "--check", env=self.env(CLAUDECODE="1"))
            self.assertEqual(done.returncode, 0, value + done.stdout)

    def test_no_stored_value_goes_on_but_the_environment_value_counts(self):
        env = self.env(CLAUDECODE="1")
        self.assertEqual(self.install("--from", self.src, "--check", env=env).returncode, 0)
        env = self.env(CLAUDECODE="1", CCNAVI_MODE="enable")
        self.assertEqual(self.install("--from", self.src, "--check", env=env).returncode, 3)
        env = self.env(CLAUDECODE="1", CCNAVI_MODE="dry-run")
        self.assertEqual(self.install("--from", self.src, "--check", env=env).returncode, 0)

    def test_enable_outside_claude_code_goes_on(self):
        self.write_settings("settings.json", '{"env":{"CCNAVI_MODE":"enable"}}')
        done = self.install("--from", self.src, "--check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_local_dry_run_overrides_enable(self):
        self.write_settings("settings.json", '{"env":{"CCNAVI_MODE":"enable"}}')
        self.write_settings("settings.local.json", '{"env":{"CCNAVI_MODE":"dry-run"}}')
        done = self.install("--from", self.src, "--check", env=self.env(CLAUDECODE="1"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_invalid_json_is_3(self):
        self.write_settings("settings.json", '{"env": ')
        done = self.install("--from", self.src, "--check")
        self.assertEqual(done.returncode, 3)
        self.one_next(done)
        self.assertEqual(self.setup_calls(), [])

    # ---- --build

    def make_build_src(self) -> str:
        with open(os.path.join(self.src, "build.py"), "w", encoding="utf-8") as f:
            f.write("")
        self.fake("uv", "exit 0")
        write_exec(
            os.path.join(self.src, "scripts", "ccnavi-build-install.sh"),
            f'#!/bin/sh\nprintf \'build-install %s\\n\' "$*" >> "{self.calls}"\n',
        )
        return self.src

    def test_build_check_runs_nothing(self):
        src = self.make_build_src()
        before = snapshot(src)
        done = self.run_sh(
            [os.path.join(src, "scripts", "ccnavi-install.sh"), self.ws, "--build", "--check"]
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.calls_text()
        for forbidden in ("pull", "uv run", "build-install", "setup "):
            self.assertNotIn(forbidden, calls)
        self.one_next(done)
        self.assertIn("git pull --ff-only", done.stdout)
        self.assertIn("了承を得てから、--check を外して打つ", done.stdout)
        self.assertEqual(snapshot(src), before)
        self.fake("node", "echo v22.0.0")
        self.fake("pnpm", "echo 10.0.0")
        done = self.run_sh(
            [os.path.join(src, "scripts", "ccnavi-install.sh"), self.ws]
            + ["--build", "--check", "--with-extensions"]
        )
        self.assertIn("VS Code への拡張機能のインストール", done.stdout)

    def test_build_runs_uv_and_does_not_call_setup(self):
        src = self.make_build_src()
        done = self.run_sh([os.path.join(src, "scripts", "ccnavi-install.sh"), self.ws, "--build"])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.calls_text()
        self.assertIn("uv run --with pyinstaller python build.py", calls)
        self.assertNotIn("setup ", calls)
        self.one_next(done)
        last = done.stdout.splitlines()[-1]
        self.assertIn(f"--from '{src}' --check", last)

    def test_build_with_extensions_calls_only_build_install(self):
        src = self.make_build_src()
        self.fake("node", "echo v22.0.0")
        self.fake("pnpm", "echo 10.0.0")
        done = self.run_sh(
            [os.path.join(src, "scripts", "ccnavi-install.sh"), self.ws]
            + ["--build", "--with-extensions"]
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        calls = self.calls_text()
        self.assertIn("build-install", calls)
        self.assertNotIn("uv run", calls)
        self.assertNotIn("pull", calls)

    def test_build_pulls_only_with_a_tracking_branch(self):
        src = self.make_build_src()
        self.git_repo(src, "https://gl.example.com/g/ccnavi.git")
        git = [REAL_GIT, "-C", src]
        cfg = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run([*git, *cfg, "commit", "--allow-empty", "-q", "-m", "x"], check=True)
        done = self.run_sh([os.path.join(src, "scripts", "ccnavi-install.sh"), self.ws, "--build"])
        self.assertNotIn("pull", self.calls_text())
        branch = subprocess.run(
            [*git, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run([*git, "update-ref", f"refs/remotes/origin/{branch}", "HEAD"], check=True)
        subprocess.run([*git, "config", f"branch.{branch}.remote", "origin"], check=True)
        subprocess.run(
            [*git, "config", f"branch.{branch}.merge", f"refs/heads/{branch}"], check=True
        )
        done = self.run_sh([os.path.join(src, "scripts", "ccnavi-install.sh"), self.ws, "--build"])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("pull --ff-only", self.calls_text())

    def test_build_failure_is_4(self):
        src = self.make_build_src()
        self.fake("uv", "exit 1")
        done = self.run_sh([os.path.join(src, "scripts", "ccnavi-install.sh"), self.ws, "--build"])
        self.assertEqual(done.returncode, 4)
        self.one_next(done)

    # ---- --force のバックアップ

    def prepare_installed(self) -> None:
        for rel, text in (
            (".ccnavi/CCNAVI.md", "old md\n"),
            (".ccnavi/scripts/ccnavi-x.sh", "old sh\n"),
            (".ccnavi/docs/a.md", "old doc\n"),
            (".ccnavi/config/common/rules.yml", "old rules\n"),
            (".claude/skills/foo/SKILL.md", "old foo\n"),
            (".claude/skills/mine/SKILL.md", "mine\n"),
            (".vscode/settings.json", "{}\n"),
            (".ccnavi/config/project/phases.yml", "mine\n"),
        ):
            path = os.path.join(self.ws, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        self.write_settings("settings.json", '{"env":{"CCNAVI_MODE":"dry-run"}}')

    def backups(self) -> list[str]:
        root = os.path.join(self.home, ".cache", "ccnavi", "backup")
        if not os.path.isdir(root):
            return []
        return sorted(os.path.join(root, n) for n in os.listdir(root))

    def test_force_backs_up_outside_the_workspace_with_mode_700(self):
        self.prepare_installed()
        done = self.install("--from", self.src, "--force")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        found = self.backups()
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0].startswith(self.ws))
        self.assertEqual(stat.S_IMODE(os.stat(found[0]).st_mode), 0o700)
        for rel, text in (
            (".ccnavi/CCNAVI.md", "old md\n"),
            (".ccnavi/scripts/ccnavi-x.sh", "old sh\n"),
            (".ccnavi/docs/a.md", "old doc\n"),
            (".ccnavi/config/common/rules.yml", "old rules\n"),
            (".claude/skills/foo/SKILL.md", "old foo\n"),
            (".vscode/settings.json", "{}\n"),
        ):
            with open(os.path.join(found[0], rel), encoding="utf-8") as f:
                self.assertEqual(f.read(), text, rel)
        self.assertFalse(os.path.exists(os.path.join(found[0], ".claude/skills/mine")))
        self.assertFalse(os.path.exists(os.path.join(found[0], ".ccnavi/config/project")))
        self.assertIn(found[0], done.stdout)
        self.assertIn("cp -R", done.stdout)
        self.assertEqual(self.setup_calls()[-1][-1], "--force")

    def test_no_backup_without_force_or_with_check(self):
        self.prepare_installed()
        self.install("--from", self.src)
        self.install("--from", self.src, "--force", "--check")
        self.assertEqual(self.backups(), [])

    def test_two_runs_in_the_same_second_do_not_collide(self):
        self.prepare_installed()
        self.fake("date", "echo 20260101-000000")
        first = self.install("--from", self.src, "--force")
        second = self.install("--from", self.src, "--force")
        self.assertEqual((first.returncode, second.returncode), (0, 0), second.stdout)
        self.assertEqual(len(self.backups()), 2)

    def test_unusable_backup_place_stops_with_4_before_setup(self):
        self.prepare_installed()
        cache = os.path.join(self.home, ".cache", "ccnavi")
        os.makedirs(os.path.dirname(cache))
        with open(cache, "w", encoding="utf-8") as f:
            f.write("a file where the directory should be\n")
        done = self.install("--from", self.src, "--force")
        self.assertEqual(done.returncode, 4)
        self.assertEqual(self.setup_calls(), [])
        self.one_next(done)

    def test_relative_cache_place_is_refused(self):
        self.prepare_installed()
        env = self.env(XDG_CACHE_HOME="cache")
        done = self.run_sh([INSTALL, self.ws, "--from", self.src, "--force"], env=env, cwd=self.ws)
        self.assertEqual(done.returncode, 4)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "cache")))
        self.assertEqual(self.setup_calls(), [])

    def test_backup_keeps_the_content_behind_symlinks(self):
        self.prepare_installed()
        shared = os.path.join(self.tmpdir, "shared-scripts")
        os.makedirs(shared)
        with open(os.path.join(shared, "keep.sh"), "w", encoding="utf-8") as f:
            f.write("shared\n")
        scripts = os.path.join(self.ws, ".ccnavi", "scripts")
        shutil.rmtree(scripts)
        os.symlink(shared, scripts)
        done = self.install("--from", self.src, "--force")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        saved = os.path.join(self.backups()[0], ".ccnavi", "scripts")
        self.assertFalse(os.path.islink(saved))
        with open(os.path.join(saved, "keep.sh"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "shared\n")

    # ---- 入れたあと

    def test_chmod_only_the_existing_executable(self):
        done = self.install("--from", self.src)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        exe = os.path.join(self.ws, ".ccnavi", "bin", THIS_MACHINE, "ccnavi")
        self.assertTrue(os.access(exe, os.X_OK))
        self.assertFalse(os.path.exists(exe + ".exe"))

    def test_lint_outcomes(self):
        for launcher_rc, want in (("127", 4), ("126", 4), ("2", 4), ("1", 5), ("0", 0)):
            self.ctl_set("launcher_rc", launcher_rc)
            done = self.install("--from", self.src)
            self.assertEqual(done.returncode, want, launcher_rc + done.stdout + done.stderr)
            self.one_next(done)
        self.assertIn(f"launcher --lint --root {self.ws} --log  --state ", self.calls_text())

    def test_lint_error_points_to_ccnavi_config(self):
        self.ctl_set("launcher_rc", "1")
        done = self.install("--from", self.src)
        self.assertIn("ccnavi-config", done.stdout)
        self.assertIn("lint の error を直す", done.stdout.splitlines()[-1])

    def test_dry_run_warn_note_and_restart_next(self):
        done = self.install("--from", self.src)
        self.assertIn("dry-run では lint の warn", done.stdout)
        self.assertIn("Claude Code を終了して", done.stdout.splitlines()[-1])
        self.assertIn(self.ws, done.stdout.splitlines()[-1])

    def test_extension_guidance_when_present(self):
        os.makedirs(os.path.join(self.src, "dist"), exist_ok=True)
        with open(os.path.join(self.src, "dist", "ccnavi-board-1.0.0.vsix"), "wb") as f:
            f.write(b"x")
        chrome = os.path.join(self.src, "extensions", "chrome", "ccnavi-approval", "dist")
        os.makedirs(chrome)
        with open(os.path.join(chrome, "manifest.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        done = self.install("--from", self.src)
        self.assertIn("code --install-extension", done.stdout)
        self.assertIn("パッケージ化されていない拡張機能を読み込む", done.stdout)
        self.assertIn("そろえて更新する決まり", done.stdout)
        self.one_next(done)

    # ---- macOS の隔離の印

    def fake_xattr(self, where: str) -> None:
        flag = os.path.join(self.ctl, "q")
        with open(flag, "w", encoding="utf-8") as f:
            f.write("1")
        body = (
            'case "$*" in\n'
            f'"-p com.apple.quarantine "*{where}*) [ -f "{flag}" ] && exit 0; exit 1;;\n'
            f'"-dr com.apple.quarantine "*) rm -f "{flag}"; exit 0;;\n'
            "esac\nexit 1"
        )
        self.fake("xattr", body)

    def test_quarantine_is_left_unless_asked(self):
        self.fake_xattr("/.ccnavi/bin/")
        done = self.install("--from", self.src)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("xattr -dr", self.calls_text())
        self.assertIn("--clear-quarantine", done.stdout)

    def test_quarantine_is_cleared_in_the_workspace_when_asked(self):
        self.fake_xattr("/.ccnavi/bin/")
        done = self.install("--from", self.src, "--clear-quarantine")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        bin_dir = os.path.join(self.ws, ".ccnavi", "bin", THIS_MACHINE)
        self.assertIn(f"xattr -dr com.apple.quarantine {bin_dir}", self.calls_text())
        self.assertNotIn(
            self.src, "\n".join(ln for ln in self.calls_text().splitlines() if "-dr" in ln)
        )

    def test_source_quarantine_is_only_reported_in_check(self):
        self.fake_xattr("/dist/")
        before = snapshot(self.src)
        done = self.install("--from", self.src, "--check")
        self.assertIn("本番では --clear-quarantine が要る", done.stdout)
        self.assertNotIn("-dr", self.calls_text())
        self.assertEqual(snapshot(self.src), before)

    # ---- 出さないもの

    def test_credentials_never_appear(self):
        done = self.install("--from", self.src, "--check")
        self.assertNotIn("tok", done.stdout + done.stderr)


def mode_settings(value: str) -> str:
    return json.dumps({"env": {"CCNAVI_MODE": value}})


def shell_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    unittest.main()
