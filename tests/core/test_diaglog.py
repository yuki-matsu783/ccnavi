"""診断ログ（ccnavi/diaglog.py）と、その後始末（prune._prune_diag）。

見るのは 6 つ。

1. 行の形。時刻（現地時刻と時差、秒まで）・5 字のレベル・出どころ[pid]・本文・logfmt の値
2. logfmt の逃がし方。空白・タブ・`"`・`=`・改行を含む値だけを囲み、`\\` と `"` を逃がし、
   改行は `\\n` に畳む
3. レベルの絞り込み。CCNAVI_LOG_LEVEL（大文字小文字を問わない）、空と読めない値は INFO
4. 置き場は `<root>/logs/diag/<出どころ>.log`。無ければ作る。root が無ければ書かない
5. 書けないときは黙って捨てる。標準出力にも標準エラーにも何も出さない。書く前に
   mask_userinfo（3 つの言語で共通の伏せ字）と redact を通す
6. prune は保持日数を過ぎた診断ログを消し、上限を超えたものをローテートする。dry_run は本番と
   同じ結果を示す
7. リンク（logs・logs/diag・書き先）を辿らない。使えない字の出どころでは書かない。ファイルは 0600

sh と拡張が同じ行を出すことは tests/sh/test_diaglog_sh.py が見る。
"""

from __future__ import annotations

import contextlib
import datetime
import io
import os
import re
import shutil
import tempfile
import time
import unittest
from unittest import mock

from ccnavi import diaglog, prune

# 時刻・レベル・出どころ[pid] の頭。
HEAD = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}) "
    r"(DEBUG|INFO |WARN |ERROR) (\S+)\[(\d+)\] "
)


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-diaglog-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(diaglog.LEVEL_ENV, None)
        os.environ.pop("CLAUDE_PROJECT_DIR", None)

    def path(self, name: str = "probe") -> str:
        return os.path.join(self.root, "logs", "diag", f"{name}.log")

    def lines(self, name: str = "probe") -> list[str]:
        try:
            with open(self.path(name), encoding="utf-8") as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []


class LineShapeTest(_Base):
    def test_the_line_has_time_level_source_pid_message_and_fields(self):
        diaglog.get("probe", self.root).info("push を拒否した", reason="unapproved", ticket="T-12")
        (line,) = self.lines()
        head = HEAD.match(line)
        self.assertIsNotNone(head, line)
        assert head is not None
        self.assertEqual("INFO ", head.group(2))
        self.assertEqual("probe", head.group(3))
        self.assertEqual(str(os.getpid()), head.group(4))
        self.assertEqual("push を拒否した reason=unapproved ticket=T-12", line[head.end() :])

    def test_the_levels_are_five_characters_wide(self):
        self.assertEqual(
            "T DEBUG n[1] m",
            diaglog.format_line("T", diaglog.DEBUG, "n", 1, "m", {}),
        )
        self.assertEqual("T INFO  n[1] m", diaglog.format_line("T", diaglog.INFO, "n", 1, "m", {}))
        self.assertEqual("T WARN  n[1] m", diaglog.format_line("T", diaglog.WARN, "n", 1, "m", {}))
        self.assertEqual("T ERROR n[1] m", diaglog.format_line("T", diaglog.ERROR, "n", 1, "m", {}))

    def test_the_stamp_is_local_time_with_offset_in_seconds(self):
        zone = datetime.timezone(datetime.timedelta(hours=9))
        moment = datetime.datetime(2026, 9, 27, 10, 15, 3, 999000, tzinfo=zone)
        self.assertEqual("2026-09-27T10:15:03+09:00", diaglog.stamp(moment))
        self.assertRegex(diaglog.stamp(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")

    def test_values_become_text(self):
        line = diaglog.format_line(
            "T",
            diaglog.INFO,
            "n",
            1,
            "m",
            {k: diaglog.text(v) for k, v in {"a": True, "b": False, "c": None, "d": 3}.items()},
        )
        self.assertEqual("T INFO  n[1] m a=true b=false c= d=3", line)


class LogfmtTest(unittest.TestCase):
    def test_plain_values_are_bare(self):
        self.assertEqual("abc", diaglog.quote("abc"))
        self.assertEqual("a\\b", diaglog.quote("a\\b"))
        self.assertEqual("", diaglog.quote(""))

    def test_values_with_space_quote_or_equals_are_quoted_and_escaped(self):
        self.assertEqual('"a b"', diaglog.quote("a b"))
        self.assertEqual('"a\tb"', diaglog.quote("a\tb"))
        self.assertEqual('"k=v"', diaglog.quote("k=v"))
        self.assertEqual('"say \\"hi\\"\\\\x"', diaglog.quote('say "hi"\\x'))

    def test_newlines_fold_to_backslash_n(self):
        self.assertEqual('"l1\\nl2\\nl3\\nl4"', diaglog.quote("l1\nl2\r\nl3\rl4"))
        self.assertEqual(
            "T INFO  n[1] 1 行目\\n2 行目",
            diaglog.format_line("T", 20, "n", 1, "1 行目\n2 行目", {}),
        )


class LevelTest(_Base):
    def test_default_is_info(self):
        log = diaglog.get("probe", self.root)
        log.debug("見えない")
        log.info("見える")
        self.assertEqual(1, len(self.lines()))

    def test_level_is_read_case_insensitively(self):
        os.environ[diaglog.LEVEL_ENV] = "debug"
        diaglog.get("probe", self.root).debug("見える")
        self.assertEqual(1, len(self.lines()))

    def test_unknown_level_falls_back_to_info(self):
        os.environ[diaglog.LEVEL_ENV] = "verbose"
        log = diaglog.get("probe", self.root)
        log.debug("見えない")
        log.info("見える")
        self.assertEqual(1, len(self.lines()))

    def test_error_level_drops_warn(self):
        os.environ[diaglog.LEVEL_ENV] = "ERROR"
        log = diaglog.get("probe", self.root)
        log.warn("見えない")
        log.error("見える")
        self.assertEqual(["ERROR"], [HEAD.match(x).group(2) for x in self.lines()])  # type: ignore[union-attr]

    def test_a_dropped_level_builds_nothing(self):
        # 出さないレベルでは時刻も行も作らない。
        with (
            mock.patch.object(diaglog, "stamp") as stamp,
            mock.patch.object(diaglog, "format_line") as fmt,
        ):
            diaglog.get("probe", self.root).debug("見えない", a="b")
        stamp.assert_not_called()
        fmt.assert_not_called()


class PlaceTest(_Base):
    def test_the_directory_is_made(self):
        diaglog.get("probe", self.root).info("x")
        self.assertTrue(os.path.isfile(self.path()))

    def test_lines_are_appended(self):
        log = diaglog.get("probe", self.root)
        log.info("1")
        log.info("2")
        self.assertEqual(["1", "2"], [x[HEAD.match(x).end() :] for x in self.lines()])  # type: ignore[union-attr]

    def test_root_falls_back_to_claude_project_dir(self):
        os.environ["CLAUDE_PROJECT_DIR"] = self.root
        diaglog.get("probe").info("x")
        self.assertEqual(1, len(self.lines()))

    def test_no_root_writes_nothing(self):
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        diaglog.get("probe").info("x")
        self.assertFalse(os.path.exists(os.path.join(self.root, "logs")))


class FailOpenTest(_Base):
    def test_an_unwritable_place_is_dropped_silently(self):
        # logs がファイルなので logs/diag を作れない。
        with open(os.path.join(self.root, "logs"), "w", encoding="utf-8") as f:
            f.write("x")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            diaglog.get("probe", self.root).error("x", a="b")
        self.assertEqual(("", ""), (out.getvalue(), err.getvalue()))

    def test_nothing_goes_to_stdout_or_stderr(self):
        out, err = io.StringIO(), io.StringIO()
        os.environ[diaglog.LEVEL_ENV] = "DEBUG"
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            log = diaglog.get("probe", self.root)
            for write in (log.debug, log.info, log.warn, log.error):
                write("x", a="b c")
        self.assertEqual(("", ""), (out.getvalue(), err.getvalue()))
        self.assertEqual(4, len(self.lines()))

    def test_a_broken_redact_does_not_escape(self):
        with mock.patch.object(diaglog.redact, "redact", side_effect=RuntimeError("boom")):
            diaglog.get("probe", self.root).info("x")
        self.assertEqual([], self.lines())

    def test_message_and_values_are_redacted(self):
        token = "ghp_" + "A" * 36
        diaglog.get("probe", self.root).info(f"token {token}", url=f"https://u:{token}@h/x")
        (line,) = self.lines()
        self.assertNotIn(token, line)


class MaskTest(unittest.TestCase):
    def test_url_and_scp_credentials_become_stars(self):
        cases = {
            "https://user:tok@host/x y": "https://***@host/x y",
            "oauth2:tok@gitlab.example:org/r.git": "***@gitlab.example:org/r.git",
            "git@github.com:org/r.git": "git@github.com:org/r.git",
            "  a://b://c@d/e\tu:p@h:x\nz": "  a://b://***@d/e\t***@h:x\nz",
            "no at": "no at",
            "x@y": "x@y",
            "@a:b": "@a:b",
            "https://@h": "https://***@h",
            "ssh://git@h:22/p https://a:b@c@d/p": "ssh://***@h:22/p https://***@d/p",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(expected, diaglog.mask_userinfo(given))


class LinkTest(_Base):
    def link(self, target: str, path: str) -> None:
        try:
            os.symlink(target, path)
        except (OSError, NotImplementedError):
            self.skipTest("リンクを作れない")

    def victim(self) -> str:
        path = os.path.join(self.root, "logs", "decisions.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}\n")
        return path

    def assert_untouched(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            self.assertEqual("{}\n", f.read())

    def test_a_linked_log_file_is_not_followed(self):
        victim = self.victim()
        os.makedirs(os.path.join(self.root, "logs", "diag"))
        self.link("../decisions.jsonl", self.path())
        diaglog.get("probe", self.root).error("x")
        self.assert_untouched(victim)

    def test_a_linked_log_file_is_not_followed_without_o_nofollow(self):
        victim = self.victim()
        os.makedirs(os.path.join(self.root, "logs", "diag"))
        self.link("../decisions.jsonl", self.path())
        with mock.patch.object(diaglog.os, "O_NOFOLLOW", 0, create=True):
            diaglog.get("probe", self.root).error("x")
        self.assert_untouched(victim)

    def test_a_linked_place_is_not_followed(self):
        outside = tempfile.mkdtemp(prefix="ccnavi-diaglog-out-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "logs"))
        self.link(outside, os.path.join(self.root, "logs", "diag"))
        diaglog.get("probe", self.root).error("x")
        self.assertEqual([], os.listdir(outside))
        shutil.rmtree(os.path.join(self.root, "logs"))
        os.makedirs(os.path.join(outside, "diag"))
        self.link(outside, os.path.join(self.root, "logs"))
        diaglog.get("probe", self.root).error("x")
        self.assertEqual([], os.listdir(os.path.join(outside, "diag")))

    def test_a_name_with_other_characters_is_not_written(self):
        for name in ("../escape", "a.b", "a b", "a/b", ""):
            with self.subTest(name=name):
                diaglog.get(name, self.root).error("x")
                self.assertFalse(os.path.exists(os.path.join(self.root, "logs")))
                self.assertFalse(os.path.exists(os.path.join(self.root, "escape.log")))

    @unittest.skipIf(os.name == "nt", "権限のビットは POSIX だけ")
    def test_a_new_file_is_owner_only(self):
        saved = os.umask(0o022)
        try:
            diaglog.get("probe", self.root).info("x")
        finally:
            os.umask(saved)
        self.assertEqual(0o600, os.stat(self.path()).st_mode & 0o777)


class PruneDiagTest(_Base):
    def setUp(self):
        super().setUp()
        for name, _ in prune.LIMITS:
            os.environ.pop(name, None)
        self.diag = os.path.join(self.root, "logs", "diag")
        os.makedirs(self.diag)

    def put(self, name: str, age_days: float, size: int = 1) -> str:
        path = os.path.join(self.diag, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("a" * size)
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))
        return path

    def test_old_diag_logs_are_removed_and_new_ones_stay(self):
        old = self.put("ccnavi-git.log", 30)
        new = self.put("ccnavi.log", 1)
        other = self.put("notes.txt", 30)
        report = prune.run(self.root, "", "")
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))
        self.assertTrue(os.path.exists(other))
        self.assertEqual(["logs/diag/ccnavi-git.log"], report.logs)

    def test_dry_run_moves_nothing(self):
        old = self.put("ccnavi-git.log", 30)
        report = prune.run(self.root, "", "", dry_run=True)
        self.assertTrue(os.path.exists(old))
        self.assertEqual(["logs/diag/ccnavi-git.log"], report.logs)

    def test_keep_days_zero_keeps_everything(self):
        os.environ[prune.LOG_KEEP_DAYS_ENV] = "0"
        old = self.put("ccnavi-git.log", 30)
        prune.run(self.root, "", "")
        self.assertTrue(os.path.exists(old))

    def test_a_big_diag_log_is_rotated(self):
        os.environ[prune.LOG_ROTATE_MB_ENV] = "1"
        big = self.put("ccnavi.log", 0, size=1024 * 1024 + 10)
        report = prune.run(self.root, "", "")
        self.assertFalse(os.path.exists(big))
        self.assertEqual(1, len(report.rotated))
        (rotated,) = [n for n in os.listdir(self.diag) if n != "ccnavi.log"]
        self.assertRegex(rotated, r"^ccnavi\.\d{8}-\d{6}\.log$")

    def test_dry_run_shows_what_the_real_run_does(self):
        # 上限を超え、かつ保持日数も過ぎた 1 本。本番はローテートだけで消さないので、
        # dry_run も「ローテート」にだけ並べる。
        os.environ[prune.LOG_ROTATE_MB_ENV] = "1"
        self.put("ccnavi.log", 30, size=1024 * 1024 + 10)
        preview = prune.run(self.root, "", "", dry_run=True)
        self.assertEqual(1, len(preview.rotated))
        self.assertEqual([], preview.logs)
        real = prune.run(self.root, "", "")
        self.assertEqual((len(preview.rotated), preview.logs), (len(real.rotated), real.logs))

    def test_a_linked_logs_directory_is_not_followed(self):
        outside = tempfile.mkdtemp(prefix="ccnavi-diaglog-out-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        os.makedirs(os.path.join(outside, "diag"))
        victim = os.path.join(outside, "diag", "x.log")
        with open(victim, "w", encoding="utf-8") as f:
            f.write("x")
        os.utime(victim, (0, 0))
        shutil.rmtree(os.path.join(self.root, "logs"))
        try:
            os.symlink(outside, os.path.join(self.root, "logs"))
        except OSError:
            self.skipTest("リンクを作れない")
        report = prune.run(self.root, "", "")
        self.assertTrue(os.path.exists(victim))
        self.assertTrue(any("リンク" in p for p in report.problems))

    def test_a_rotated_diag_log_is_not_rotated_again(self):
        os.environ[prune.LOG_ROTATE_MB_ENV] = "1"
        self.put("ccnavi.20260901-000000.log", 0, size=1024 * 1024 + 10)
        report = prune.run(self.root, "", "")
        self.assertEqual([], report.rotated)

    def test_a_linked_place_is_not_followed(self):
        if not hasattr(os, "symlink"):
            self.skipTest("リンクを作れない")
        outside = tempfile.mkdtemp(prefix="ccnavi-diaglog-out-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        victim = os.path.join(outside, "x.log")
        with open(victim, "w", encoding="utf-8") as f:
            f.write("x")
        os.utime(victim, (0, 0))
        shutil.rmtree(self.diag)
        try:
            os.symlink(outside, self.diag)
        except OSError:
            self.skipTest("リンクを作れない")
        report = prune.run(self.root, "", "")
        self.assertTrue(os.path.exists(victim))
        self.assertTrue(any("リンク" in p for p in report.problems))


class RolloutTest(_Base):
    """ccnavi/ の失敗の経路と副命令が診断ログに残り、標準エラーの文面は変わらないこと。

    行の形は上のクラスが見る。ここは「書かれたか」と「書いてはいけない値が入っていないか」だけ。
    """

    def find(self, text: str, name: str = "ccnavi") -> list[str]:
        return [line for line in self.lines(name) if text in line]

    def test_cause_keeps_only_the_kind_and_errno(self):
        self.assertEqual(
            {"error": "PermissionError", "errno": 13},
            diaglog.cause(PermissionError(13, "Permission denied", "/secret/x")),
        )
        self.assertEqual({"error": "ValueError"}, diaglog.cause(ValueError("中身")))
        self.assertEqual({"errno": 28}, diaglog.cause("[Errno 28] No space left: '/x'"))
        self.assertEqual({}, diaglog.cause("読めない"))
        self.assertEqual({}, diaglog.cause(None))

    def test_a_subcommand_logs_what_it_took_and_how_it_ended(self):
        from tests.inproc import run_ccnavi

        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        done = run_ccnavi(
            ["--root", self.root, "--lint", "--json"], input="", cwd=self.root, env=env
        )
        took = self.find("副命令を受け付けた")
        ended = self.find("副命令を終えた")
        self.assertEqual(1, len(took), self.lines("ccnavi"))
        self.assertIn("cmd=lint", took[0])
        self.assertEqual(1, len(ended))
        self.assertIn(f"cmd=lint exit={done.returncode}", ended[0])

    def test_a_hook_call_adds_nothing_at_the_default_level(self):
        from tests.inproc import run_ccnavi

        env = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        # ルールが無いと組み込みの既定に戻り、その縮退は WARN で残る。ここは読める設定で見る。
        common = os.path.join(self.root, ".ccnavi", "common")
        os.makedirs(common)
        with open(os.path.join(common, "rules.yml"), "w", encoding="utf-8") as f:
            f.write("version: 1\n")
        payload = (
            '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls"},'
            '"session_id":"s1","cwd":"' + self.root.replace("\\", "/") + '"}'
        )
        done = run_ccnavi(["--root", self.root, "--state", "", "--log", ""], input=payload, env=env)
        self.assertEqual("", done.stderr)
        self.assertEqual([], self.lines("ccnavi"))

    def test_unreadable_rules_fall_back_and_say_so_once(self):
        from ccnavi import audit, ruleload, settings

        conf, _ = settings.load(self.root)
        conf.rules = os.path.join(self.root, "rules.yml")
        with open(conf.rules, "w", encoding="utf-8") as f:
            f.write("deny: [\n")
        err = io.StringIO()
        ruleload.load_rules(err, conf, audit.Record(), self.root)
        ruleload.load_rules(io.StringIO(), conf, audit.Record(), self.root)
        self.assertTrue(err.getvalue().startswith("ccnavi: ルールを読めない: "))
        warned = self.find("ルールを読めず組み込みの既定に戻した")
        self.assertEqual(1, len(warned), self.lines("ccnavi"))
        self.assertIn(" WARN  ", warned[0])
        self.assertIn("layer=common", warned[0])
        # 中身の断片（壊れた YAML）は写さない。
        self.assertNotIn("deny", warned[0])

    def test_post_state_that_cannot_be_written_is_warned_without_the_message(self):
        from ccnavi import fsio, post

        err = io.StringIO()
        failed = "[Errno 13] Permission denied: '/home/someone/secret.json'"
        with mock.patch.object(fsio, "write_json_atomic", return_value=failed):
            post._save_seen(err, self.root, "s1", {"a"}, root=self.root)
        self.assertEqual(f"ccnavi: 実行後の監視の控えを書けない: {failed}\n", err.getvalue())
        warned = self.find("実行後の監視の控えを書けない")
        self.assertEqual(1, len(warned))
        self.assertIn("errno=13", warned[0])
        self.assertNotIn("Permission denied", warned[0])

    def test_a_marker_that_cannot_be_placed_is_an_error(self):
        from ccnavi import approval, review

        err = io.StringIO()
        with mock.patch.object(approval, "write_mark", return_value="[Errno 30] ro"):
            placed = review._mark(err, self.root, self.root, "p0001", 2, "reviewed", {})
        self.assertFalse(placed)
        self.assertEqual("ccnavi: マーカーを置けない: [Errno 30] ro\n", err.getvalue())
        failed = self.find("フェーズのマーカーを置けない")
        self.assertEqual(1, len(failed))
        self.assertIn(" ERROR ", failed[0])
        self.assertIn("parent=p0001 phase=2 mark=reviewed errno=30", failed[0])

    def test_a_history_line_that_cannot_be_written_is_warned(self):
        from ccnavi import history

        blocker = os.path.join(self.root, "approved")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("ファイルなのでディレクトリを作れない")
        err = io.StringIO()
        with history.session(history.VIA_CLI, err):
            history.set_root(self.root)
            failed = history.note(blocker, "i0001", history.KIND_STARTED, "doing", "doing")
        self.assertTrue(failed)
        self.assertTrue(err.getvalue().startswith("ccnavi: 警告: i0001 の履歴（started）を"))
        warned = self.find("チケットの跡を書けず飛ばした")
        self.assertEqual(1, len(warned))
        self.assertIn("ticket=i0001 kind=started", warned[0])

    def test_an_unknown_mode_does_not_write_the_value(self):
        from ccnavi import modes, settings

        conf, _ = settings.load(self.root)
        err = io.StringIO()
        got = modes.resolve_mode(err, "tok-abc", conf, root=self.root)
        self.assertEqual(modes.ENABLE, got)
        self.assertIn("'tok-abc' is not a mode", err.getvalue())
        warned = self.find("動作モードを enable に戻した")
        self.assertEqual(1, len(warned))
        self.assertIn("source=--mode reason=unknown-value", warned[0])
        self.assertNotIn("tok-abc", warned[0])


if __name__ == "__main__":
    unittest.main()
