"""手動の GitLab の道具（tests/manual/gitlab/）の振る舞いを、Docker 無しで守る（issue #572）。

本物の GitLab は立てず、`docker` の呼び出しと HTTP を差し替える。見るのは次の 5 つ。

1. `docker_gitlab.py up` は、イメージが無いとき `--pull` が無ければ取得せず止まる
2. トークンは有効なら作り直さない。`--rotate` のときだけ作る。ファイルは 0600
3. `status` はトークンの値を端末に出さない
4. `probe_gitlab.py` と `gitlab_api.py` は import しても副作用が無い
   （環境変数・ファイル・一時ディレクトリに触れない）
5. `probe_gitlab.py --help` はトークンが無くても終わる
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

GITLAB_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), os.pardir, "manual", "gitlab")
)
sys.path.insert(0, GITLAB_DIR)

import docker_gitlab  # noqa: E402
import gitlab_api  # noqa: E402


class FakeClient:
    def __init__(self, valid: bool = True, status: int | None = None) -> None:
        self.valid = valid
        self.status = status

    def user(self, token: str):
        if self.status is not None:
            return self.status, {}
        return (200 if self.valid else 401), {}

    def version(self, token: str):
        return 200, {"version": "19.4.1"}


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class ImportSideEffectTest(unittest.TestCase):
    def run_py(self, code: str, env: dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )

    def test_probe_import_reads_no_token_and_makes_no_run_dir(self):
        code = (
            f"import sys; sys.path.insert(0, {GITLAB_DIR!r}); import probe_gitlab as p; "
            "print(repr((p.ROOT_TOKEN, p.REVIEWER_TOKEN, p.OUT, p.LOG)))"
        )
        done = self.run_py(code, env={"PATH": os.environ.get("PATH", "")})
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("('', '', '', None)", done.stdout.strip())

    def test_gitlab_api_import_is_pure(self):
        code = (
            f"import sys; sys.path.insert(0, {GITLAB_DIR!r}); import gitlab_api; "
            "print(gitlab_api.Client('http://x').api)"
        )
        done = self.run_py(code, env={"PATH": os.environ.get("PATH", "")})
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("http://x/api/v4", done.stdout.strip())

    def test_probe_help_needs_no_token_and_no_exe(self):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": tempfile.gettempdir()}
        done = subprocess.run(
            [sys.executable, os.path.join(GITLAB_DIR, "probe_gitlab.py"), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("GITLAB_TOKEN", done.stdout)

    def test_probe_without_token_stops_in_main_with_a_hint(self):
        env = {"PATH": os.environ.get("PATH", "")}
        done = subprocess.run(
            [sys.executable, os.path.join(GITLAB_DIR, "probe_gitlab.py")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.assertNotEqual(0, done.returncode)
        self.assertIn("make_gitlab_tokens.rb", done.stderr)


class ProbePureHelpersTest(unittest.TestCase):
    """probe の純粋な部分（Docker 不要）。実機で直した期待が戻らないように固定する（#614）。"""

    def setUp(self) -> None:
        import probe_gitlab  # noqa: PLC0415

        self.probe = probe_gitlab

    def approved_parent(self) -> str:
        """probe が承認する親と同じ形（plan 1 つ・フィードバック計画なし）の全文。"""
        from tests.ticketsample import parent_text  # noqa: PLC0415

        return parent_text("i0001", ["work"])

    def parse(self, text: str):
        from ccnavi.tickets import ticket as ticket_mod  # noqa: PLC0415

        found, problems = ticket_mod.parse(text)
        self.assertIsNotNone(found, problems)
        return found

    def test_feedback_revision_adds_only_an_empty_feedback_plan(self):
        text = self.approved_parent()
        revised = self.probe.feedback_revision(text)
        self.assertEqual(text.replace("human_review:", "feedback: []\nhuman_review:", 1), revised)

    def test_feedback_revision_blanks_the_fields_written_after_approval(self):
        text = (
            self.approved_parent()
            .replace('started_at: ""', 'started_at: "2026-01-01T00:00:00"')
            .replace('base_sha: ""', 'base_sha: "abc123"')
        )
        revised = self.probe.feedback_revision(text)
        self.assertIn('started_at: ""', revised)
        self.assertIn('base_sha: ""', revised)
        self.assertNotIn("2026-01-01", revised)
        self.assertNotIn("abc123", revised)

    def test_feedback_revision_is_detected_as_a_revision(self):
        from ccnavi.tickets import approval  # noqa: PLC0415

        current = self.parse(self.approved_parent())
        revised = self.parse(self.probe.feedback_revision(self.approved_parent()))
        self.assertTrue(approval.plan_differs(revised, current))
        self.assertEqual([], revised.feedback)
        self.assertIsNone(current.feedback)

    def test_feedback_revision_refuses_a_text_that_already_has_feedback(self):
        once = self.probe.feedback_revision(self.approved_parent())
        with self.assertRaises(ValueError):
            self.probe.feedback_revision(once)

    def test_cancelled_children_live_in_done_not_in_a_cancelled_directory(self):
        from ccnavi.tickets import ticket_model  # noqa: PLC0415

        path = self.probe.approved_copy_path("/t", "done", "i0002-01-01")
        self.assertEqual(os.path.join("/t", ".ccnavi", "approved", "done", "i0002-01-01.md"), path)
        self.assertNotIn(ticket_model.CANCELLED, path)
        self.assertNotIn(ticket_model.CANCELLED, ticket_model.STATES)

    def test_cancelled_mark_needs_a_filled_cancelled_at(self):
        self.assertTrue(self.probe.is_cancelled_copy('x: 1\ncancelled_at: "2026-01-01T00:00:00"\n'))
        self.assertFalse(self.probe.is_cancelled_copy('x: 1\ncancelled_at: ""\n'))
        self.assertFalse(self.probe.is_cancelled_copy("x: 1\n"))

    def test_project_name_takes_a_prefix_to_tell_runs_apart(self):
        self.assertEqual(
            "ccnavi-probe-0101-120000", self.probe.project_name("ccnavi-probe", "0101-120000")
        )
        self.assertEqual("p614-0101-120000", self.probe.project_name("p614", "0101-120000"))

    def test_summary_counts_skip_apart_from_pass(self):
        results = [("a", "PASS", ""), ("b", "SKIP", ""), ("c", "FAIL", ""), ("d", "PASS", "")]
        self.assertEqual("PASS 2 / SKIP 1 / FAIL 1", self.probe.summary_line(results))


class ClientMethodTest(unittest.TestCase):
    def test_get_branch_calls_the_branch_path(self):
        client = gitlab_api.Client("http://x")
        with mock.patch.object(client, "call", return_value=(200, {})) as call:
            client.get_branch("tok", 7, "i0001")
        call.assert_called_once_with(
            "GET", "/projects/7/repository/branches/i0001", "tok", tag="branch-i0001"
        )


class DockerGitlabTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # 状態のファイルは一時ディレクトリへ。up・down の表示はテストの出力に出さない
        # docker の情報・ボリューム・診断ログは実物に触れさせない
        # （既定は十分なメモリで、ボリュームは無し）
        for patcher in (
            mock.patch.dict(os.environ, {"XDG_CACHE_HOME": self._tmp.name}),
            mock.patch("sys.stdout", new_callable=io.StringIO),
            mock.patch("sys.stderr", new_callable=io.StringIO),
            mock.patch.object(docker_gitlab, "docker_memory_bytes", return_value=10 * 1024**3),
            mock.patch.object(docker_gitlab, "volume_exists", return_value=False),
            mock.patch.object(docker_gitlab, "log"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def args(self, **kw) -> argparse.Namespace:
        base = {"pull": False, "rotate": False, "purge": False}
        base.update(kw)
        return argparse.Namespace(**base)

    def test_up_without_image_stops_and_does_not_pull(self):
        with (
            mock.patch.object(docker_gitlab, "docker_ok", return_value=True),
            mock.patch.object(docker_gitlab, "image_present", return_value=False),
            mock.patch.object(docker_gitlab.subprocess, "run") as pull,
            mock.patch.object(docker_gitlab, "compose") as compose,
        ):
            rc = docker_gitlab.cmd_up(self.args())
        self.assertEqual(2, rc)
        pull.assert_not_called()
        compose.assert_not_called()

    def test_up_pull_fetches_only_when_asked(self):
        pull_result = mock.Mock(returncode=0)
        with (
            mock.patch.object(docker_gitlab, "docker_ok", return_value=True),
            mock.patch.object(docker_gitlab, "image_present", return_value=False),
            mock.patch.object(docker_gitlab, "container_state", return_value=("", "")),
            mock.patch.object(docker_gitlab.subprocess, "run", return_value=pull_result) as pull,
            mock.patch.object(docker_gitlab, "compose", return_value=completed()),
            mock.patch.object(docker_gitlab, "wait_ready", return_value=(None, None)),
        ):
            rc = docker_gitlab.cmd_up(self.args(pull=True))
        pull.assert_called_once_with(["docker", "pull", docker_gitlab.GITLAB_IMAGE])
        self.assertEqual(1, rc)

    def test_state_file_is_0600(self):
        path = docker_gitlab.password_path()
        docker_gitlab.write_env_file(path, {"GITLAB_ROOT_PASSWORD": "x"})
        self.assertEqual(0o600, stat.S_IMODE(os.stat(path).st_mode))

    def test_existing_valid_tokens_are_not_rotated(self):
        docker_gitlab.write_env_file(
            docker_gitlab.tokens_path(),
            {"GITLAB_TOKEN": "glpat-old", "CCNAVI_PROBE_REVIEWER_TOKEN": "glpat-rev-old"},
        )
        with mock.patch.object(docker_gitlab, "run") as run:
            tokens = docker_gitlab.ensure_tokens(FakeClient(valid=True), rotate=False)
        run.assert_not_called()
        self.assertEqual("glpat-old", tokens["GITLAB_TOKEN"])

    def test_transient_api_failure_does_not_revoke_tokens(self):
        docker_gitlab.write_env_file(
            docker_gitlab.tokens_path(),
            {"GITLAB_TOKEN": "glpat-old", "CCNAVI_PROBE_REVIEWER_TOKEN": "glpat-rev-old"},
        )
        for status in (0, 502, 503):
            with self.subTest(status=status), mock.patch.object(docker_gitlab, "run") as run:
                tokens = docker_gitlab.ensure_tokens(FakeClient(status=status), rotate=False)
            run.assert_not_called()
            self.assertEqual("glpat-old", tokens["GITLAB_TOKEN"])

    def test_invalid_tokens_are_recreated_without_rotate_flag(self):
        docker_gitlab.write_env_file(
            docker_gitlab.tokens_path(),
            {"GITLAB_TOKEN": "glpat-old", "CCNAVI_PROBE_REVIEWER_TOKEN": "glpat-rev-old"},
        )
        script = (
            "GITLAB_TOKEN=glpat-new\nCCNAVI_PROBE_REVIEWER_TOKEN=glpat-rev-new\n"
            "CCNAVI_PROBE_REVIEWER_ID=3\nCCNAVI_PROBE_ROOT_ID=1\n"
        )
        with mock.patch.object(docker_gitlab, "run", return_value=completed(script)) as run:
            tokens = docker_gitlab.ensure_tokens(FakeClient(valid=False), rotate=False)
        self.assertIn("runner", run.call_args.args[0])
        self.assertEqual("glpat-new", tokens["GITLAB_TOKEN"])

    def test_rotate_writes_new_tokens_0600(self):
        docker_gitlab.write_env_file(
            docker_gitlab.tokens_path(),
            {"GITLAB_TOKEN": "glpat-old", "CCNAVI_PROBE_REVIEWER_TOKEN": "glpat-rev-old"},
        )
        script = "GITLAB_TOKEN=glpat-new\nCCNAVI_PROBE_REVIEWER_TOKEN=glpat-rev-new\n"
        with mock.patch.object(docker_gitlab, "run", return_value=completed(script)):
            tokens = docker_gitlab.ensure_tokens(FakeClient(valid=True), rotate=True)
        self.assertEqual("glpat-new", tokens["GITLAB_TOKEN"])
        mode = stat.S_IMODE(os.stat(docker_gitlab.tokens_path()).st_mode)
        self.assertEqual(0o600, mode)
        self.assertEqual(
            "glpat-new", docker_gitlab.read_env_file(docker_gitlab.tokens_path())["GITLAB_TOKEN"]
        )

    def test_status_never_prints_token_values(self):
        docker_gitlab.write_env_file(
            docker_gitlab.tokens_path(),
            {"GITLAB_TOKEN": "glpat-secret-1", "CCNAVI_PROBE_REVIEWER_TOKEN": "glpat-secret-2"},
        )
        out = io.StringIO()
        with (
            mock.patch.object(docker_gitlab, "docker_ok", return_value=True),
            mock.patch.object(
                docker_gitlab, "container_state", return_value=("running", "healthy")
            ),
            mock.patch.object(docker_gitlab.gitlab_api, "Client", return_value=FakeClient()),
            contextlib.redirect_stdout(out),
        ):
            rc = docker_gitlab.cmd_status(self.args())
        self.assertEqual(0, rc)
        self.assertNotIn("glpat-secret", out.getvalue())
        self.assertIn("有効", out.getvalue())

    def test_up_again_keeps_the_root_password(self):
        docker_gitlab.write_env_file(
            docker_gitlab.password_path(), {"GITLAB_ROOT_PASSWORD": "keep-me"}
        )
        with (
            mock.patch.object(docker_gitlab, "docker_ok", return_value=True),
            mock.patch.object(docker_gitlab, "image_present", return_value=True),
            mock.patch.object(docker_gitlab, "container_state", return_value=("exited", "")),
            mock.patch.object(docker_gitlab, "compose", return_value=completed()) as compose,
            mock.patch.object(docker_gitlab, "wait_ready", return_value=(None, None)),
        ):
            docker_gitlab.cmd_up(self.args())
        self.assertEqual("keep-me", compose.call_args.args[1])
        self.assertEqual(
            "keep-me",
            docker_gitlab.read_env_file(docker_gitlab.password_path())["GITLAB_ROOT_PASSWORD"],
        )

    def test_up_after_down_keeps_the_password_when_the_volume_exists(self):
        """down のあと（コンテナが無い・ボリュームは残る）の up は、パスワードを作り直さない。"""
        docker_gitlab.write_env_file(
            docker_gitlab.password_path(), {"GITLAB_ROOT_PASSWORD": "first"}
        )
        before = read_text(docker_gitlab.password_path())
        with (
            mock.patch.object(docker_gitlab, "docker_ok", return_value=True),
            mock.patch.object(docker_gitlab, "image_present", return_value=True),
            mock.patch.object(docker_gitlab, "container_state", return_value=("", "")),
            mock.patch.object(docker_gitlab, "volume_exists", return_value=True),
            mock.patch.object(docker_gitlab, "compose", return_value=completed()) as compose,
            mock.patch.object(docker_gitlab, "wait_ready", return_value=(None, None)),
        ):
            docker_gitlab.cmd_up(self.args())
        self.assertEqual("first", compose.call_args.args[1])
        self.assertEqual(before, read_text(docker_gitlab.password_path()))

    def test_one_port_setting_drives_the_tool_and_the_probe(self):
        code = (
            f"import sys; sys.path.insert(0, {GITLAB_DIR!r}); "
            "import docker_gitlab as d, probe_gitlab as p; print(d.PORT, p.GITLAB)"
        )
        done = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={
                **{k: v for k, v in os.environ.items() if k != "CCNAVI_PROBE_GITLAB"},
                "CCNAVI_GITLAB_PORT": "9123",
            },
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual("9123 http://127.0.0.1:9123", done.stdout.strip())

    def test_compose_takes_the_port_and_memory_from_the_tool(self):
        with open(os.path.join(GITLAB_DIR, "compose.yml"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("${GITLAB_PORT}", text)
        self.assertIn("${GITLAB_MEM_LIMIT:?", text)
        self.assertNotIn("8929", text)

    def test_up_without_a_volume_makes_a_new_password_and_stores_it(self):
        with (
            mock.patch.object(docker_gitlab, "docker_ok", return_value=True),
            mock.patch.object(docker_gitlab, "image_present", return_value=True),
            mock.patch.object(docker_gitlab, "container_state", return_value=("", "")),
            mock.patch.object(docker_gitlab, "compose", return_value=completed()) as compose,
            mock.patch.object(docker_gitlab, "wait_ready", return_value=(None, None)),
        ):
            docker_gitlab.cmd_up(self.args())
        stored = docker_gitlab.read_env_file(docker_gitlab.password_path())["GITLAB_ROOT_PASSWORD"]
        self.assertEqual(stored, compose.call_args.args[1])
        self.assertEqual(48, len(stored))

    def test_low_docker_memory_prints_a_next_line_warning(self):
        with (
            mock.patch.object(docker_gitlab, "docker_ok", return_value=True),
            mock.patch.object(docker_gitlab, "docker_memory_bytes", return_value=4 * 1024**3),
            mock.patch.object(docker_gitlab, "image_present", return_value=False),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            rc = docker_gitlab.cmd_up(self.args())
        self.assertIn("次: Docker に割り当てたメモリが 4.0GB", out.getvalue())
        self.assertEqual(2, rc)  # 警告のあとも、イメージが無ければ止まる

    def test_enough_docker_memory_prints_no_warning(self):
        with (
            mock.patch.object(docker_gitlab, "docker_ok", return_value=True),
            mock.patch.object(docker_gitlab, "image_present", return_value=False),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            docker_gitlab.cmd_up(self.args())
        self.assertNotIn("メモリ", out.getvalue())

    def test_down_purge_removes_state_files(self):
        docker_gitlab.write_env_file(docker_gitlab.password_path(), {"GITLAB_ROOT_PASSWORD": "x"})
        docker_gitlab.write_env_file(docker_gitlab.tokens_path(), {"GITLAB_TOKEN": "y"})
        with (
            mock.patch.object(docker_gitlab, "docker_ok", return_value=True),
            mock.patch.object(docker_gitlab, "compose", return_value=completed()) as compose,
        ):
            rc = docker_gitlab.cmd_down(self.args(purge=True))
        self.assertEqual(0, rc)
        self.assertIn("-v", compose.call_args.args[0])
        self.assertFalse(os.path.exists(docker_gitlab.password_path()))
        self.assertFalse(os.path.exists(docker_gitlab.tokens_path()))


if __name__ == "__main__":
    unittest.main()
