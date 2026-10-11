"""記録から秘密の形を伏せる（redact）の単体テストと、記録への配線の受入テスト。

見るのは 3 つ。

1. 形ごとに値が伏せられ、周りの文字列（名前・方式の語・コマンド）は残ること
2. 短い値は全部、長い値は頭 6 字と尻 4 字を残して伏せること
3. 伏せるのは記録の 1 行だけで、判定は伏せる前の文字列で下すこと
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from unittest import mock

from ccnavi.records import audit, redact
from tests.inproc import run_ccnavi

# 形だけを持つ見本。実際のトークンではない。
GHP = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
GLPAT = "glpat-" + "abcdefghij0123456789"
AKIA = "AKIA" + "ABCDEFGHIJKLMNOP"
LONG = "s3cr3t-value-that-is-long-enough"


class MaskTest(unittest.TestCase):
    def test_short_value_is_fully_masked(self):
        self.assertEqual(redact.mask("hunter2"), "***")
        self.assertEqual(redact.mask("x" * (redact.SHORT - 1)), "***")

    def test_long_value_keeps_head_and_tail(self):
        value = "abcdef" + "0123456789" + "wxyz"
        self.assertEqual(len(value), 20)
        self.assertEqual(redact.mask(value), "abcdef***wxyz")

    def test_boundary_is_long(self):
        value = "a" * redact.SHORT
        self.assertEqual(redact.mask(value), "aaaaaa***aaaa")


class RedactTest(unittest.TestCase):
    def assertHidden(self, text: str, secret: str, kept: tuple[str, ...] = ()):
        out = redact.redact(text)
        self.assertNotIn(secret, out, out)
        for word in kept:
            self.assertIn(word, out, out)
        return out

    def test_nothing_to_hide_is_untouched(self):
        for text in (
            "git push origin main",
            "mkdir -p src/a && cp -p a b",
            "ssh -p 22 host",
            "sort -u names.txt",
            "python -m tokenizer --secretName foo",
            "",
        ):
            self.assertEqual(redact.redact(text), text)

    def test_authorization_header(self):
        out = self.assertHidden(
            f"curl -H 'Authorization: Bearer {LONG}' https://api.example.com",
            LONG,
            ("Authorization: Bearer", "https://api.example.com"),
        )
        self.assertIn(redact.mask(LONG), out)

    def test_authorization_header_without_scheme_and_in_double_quotes(self):
        self.assertHidden('curl -H "Authorization: tok12345" x', "tok12345", ("Authorization:",))

    def test_bare_bearer(self):
        self.assertHidden(f"echo Bearer {LONG}", LONG, ("Bearer",))

    def test_assignments(self):
        for text, secret, name in (
            ("GITHUB_TOKEN=abc123 gh pr list", "abc123", "GITHUB_TOKEN="),
            ("curl 'https://x/api?token=abc123&page=2'", "abc123", "&page=2"),
            ("mysql --password=hunter2 db", "hunter2", "--password="),
            ("export DB_PASSWORD='p a s s'", "p a s s", "DB_PASSWORD="),
            ('{"api_key": "k-123456"}', "k-123456", '"api_key"'),
            ("aws_secret_access_key = wJalrXUtnFEMI", "wJalrXUtnFEMI", "aws_secret_access_key"),
            ("client_secret: zzz999", "zzz999", "client_secret:"),
            ("x-api-key: kkk111", "kkk111", "x-api-key:"),
        ):
            with self.subTest(text=text):
                self.assertHidden(text, secret, (name,))

    def test_variable_reference_is_not_a_secret(self):
        for text in ("GITHUB_TOKEN=$GITHUB_TOKEN gh pr list", 'TOKEN="${TOKEN}" sh x.sh'):
            self.assertEqual(redact.redact(text), text)

    def test_flag_with_space(self):
        self.assertHidden("docker login --password hunter2 -u me", "hunter2", ("--password",))
        self.assertHidden("tool --token 'abc def'", "abc def", ("--token",))

    def test_token_shapes(self):
        for token in (GHP, "github_pat_" + "a" * 30, GLPAT, AKIA, "xoxb-" + "1234567890-abc"):
            with self.subTest(token=token[:6]):
                out = self.assertHidden(f"echo {token} > /dev/null", token, ("echo",))
                self.assertIn(token[:6], out)

    def test_url_userinfo(self):
        self.assertHidden(
            f"git clone https://oauth2:{GLPAT}@gitlab.example.com/g/r.git",
            GLPAT,
            ("https://oauth2:", "@gitlab.example.com/g/r.git"),
        )

    def test_mysql_short_p(self):
        self.assertHidden("mysql -u root -phunter2 db", "hunter2", ("mysql -u root -p", " db"))
        self.assertHidden("cd x && mysqldump -p'hunter 2' db", "hunter 2", ("mysqldump",))
        # `-p` の後ろに空白があれば、次の語はパスワードではない（プロンプトで聞かれる）。
        self.assertEqual(redact.redact("mysql -p db"), "mysql -p db")
        # mysql の外の `-p` は読まない。
        self.assertEqual(redact.redact("mysql -e x; mkdir -pv a"), "mysql -e x; mkdir -pv a")

    def test_curl_user(self):
        self.assertHidden("curl -u me:hunter2 https://x", "hunter2", ("curl -u me:",))

    def test_private_key_body(self):
        body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC"
        text = (
            "cat > k <<EOF\n-----BEGIN RSA PRIVATE KEY-----\n"
            f"{body}\n-----END RSA PRIVATE KEY-----\nEOF"
        )
        self.assertHidden(text, body, ("BEGIN RSA PRIVATE KEY", "END RSA PRIVATE KEY", "EOF"))

    def test_overlapping_forms_are_masked_once(self):
        out = redact.redact(f"GITHUB_TOKEN={GHP} gh pr list")
        self.assertEqual(out, f"GITHUB_TOKEN={redact.mask(GHP)} gh pr list")

    def test_several_secrets_in_one_command(self):
        out = redact.redact(f"A_TOKEN=aaa111 B_PASSWORD=bbb222 run {AKIA}")
        for secret in ("aaa111", "bbb222", AKIA):
            self.assertNotIn(secret, out)
        self.assertIn(" run ", out)

    def test_url_userinfo_password_with_at_sign(self):
        # パスワードに `@` が入っても、ホストの手前の最後の `@` までを伏せる
        # （ccnavi_mask_url と同じ）。
        out = self.assertHidden(
            "psql postgres://user:P@ssw0rd!@db.example.com/app",
            "ssw0rd",
            ("postgres://user:", "@db.example.com/app"),
        )
        self.assertNotIn("P@", out)
        # 空白はまたがない。後ろの語の `@` まで伸ばさない。
        out = redact.redact("git clone https://u:pw@host/r.git && ssh me@other")
        self.assertIn("ssh me@other", out)
        self.assertNotIn(":pw@", out)

    def test_more_names(self):
        for text, secret, name in (
            ("SECRET_KEY=abc123 python app.py", "abc123", "SECRET_KEY="),
            ("export aws_secret_key=abc123", "abc123", "aws_secret_key="),
            ("MYSQL_PWD=hunter2 mysql db", "hunter2", "MYSQL_PWD="),
            ("echo //registry.npmjs.org/:_authToken=npmsecret1 >> x", "npmsecret1", "_authToken="),
            ("SESSION_COOKIE=abc123 run", "abc123", "SESSION_COOKIE="),
        ):
            with self.subTest(text=text):
                self.assertHidden(text, secret, (name,))
        # 素の PWD（いまの場所）は秘密ではない。
        self.assertEqual(redact.redact("PWD=/home/me make"), "PWD=/home/me make")

    def test_cookie_header(self):
        # `;` で並ぶ値は 1 つの値として伏せる（長ければ頭と尻が残る）。
        out = self.assertHidden(
            "curl -H 'Cookie: session=abc123; id=zz99' https://x",
            "abc123",
            ("Cookie:", "https://x"),
        )
        self.assertIn(redact.mask("session=abc123; id=zz99"), out)
        self.assertHidden("curl -H 'Cookie: a=1; token=abc123' x", "abc123", ("Cookie:",))
        self.assertHidden("curl -b 'sid=abc123' https://x", "abc123", ("curl -b",))
        self.assertHidden("curl --cookie sid=abc123 https://x", "abc123", ("--cookie",))

    def test_aws_configure_set(self):
        self.assertHidden(
            "aws configure set aws_secret_access_key wJalrXUtnFEMI --profile p",
            "wJalrXUtnFEMI",
            ("aws_secret_access_key", "--profile p"),
        )

    def test_more_token_shapes(self):
        for token in (
            "sk-ant-api03-" + "A1b2C3d4E5" * 3,
            "sk-proj-" + "A1b2C3d4E5" * 3,
            "sk-" + "A1b2C3d4E5" * 4,
            "sk_live_" + "A1b2C3d4E5" * 2,
            "rk_live_" + "A1b2C3d4E5" * 2,
            "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
            "npm_" + "A1b2C3d4E5" * 3 + "abcdef",
            "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".dozjgNryP4J3jVmNHl0w5N",
        ):
            with self.subTest(token=token[:8]):
                out = self.assertHidden(f"echo {token} > /dev/null", token, ("echo",))
                self.assertIn(token[:6], out)
        # 語の途中の `sk-` は読まない。
        for text in ("git checkout task-1234567890abcdefghijkl", "ls disk-0123456789abcdefghij"):
            self.assertEqual(redact.redact(text), text)

    def test_command_scoped_password_flags(self):
        self.assertHidden("sshpass -p hunter2 ssh -p 22 me@host", "hunter2", ("ssh -p 22",))
        self.assertHidden("sshpass -phunter2 scp a b", "hunter2", ("sshpass",))
        self.assertHidden("docker login -u me -p hunter2 reg.io", "hunter2", ("reg.io",))
        self.assertHidden(
            "redis-cli -h h -a hunter2 ping", "hunter2", ("redis-cli -h h -a", "ping")
        )
        # コマンドの外の -p / -a は読まない。
        for text in (
            "sshpass -f pw.txt ssh -p 2222 host",
            "docker run -p 8080:80 img",
            "ls -a; mkdir -p x",
            "redis-cli ping; ls -a src",
        ):
            with self.subTest(text=text):
                self.assertEqual(redact.redact(text), text)

    def test_nul_is_a_boundary(self):
        # unwrapped はコマンドを \x00 でつなぐ。値はそこで終わる。
        out = redact.redact("GITHUB_TOKEN=abc123\x00gh pr list")
        self.assertEqual(out, "GITHUB_TOKEN=***\x00gh pr list")
        out = redact.redact("mysql -phunter2\x00ls -a")
        self.assertEqual(out, "mysql -p***\x00ls -a")


class SpeedTest(unittest.TestCase):
    """長い入力でも伏せる手間が長さにほぼ比例すること。

    記録は実行前チェックの期限の中で書く。名前の形（`_ASSIGN`）の頭が長さを限らずに
    文字を読み進めると、`-` や `_` が続くだけの 1.5 万字で 10 秒を超え、期限を過ぎた hook の拒否が
    捨てられる（止められずに通る）。
    """

    LIMIT_SECONDS = 0.5

    def test_long_runs_of_name_characters(self):
        for ch in ("-", "_", ".", "a", "A", "a-", "x_"):
            text = ch * (100_000 // len(ch))
            with self.subTest(ch=ch):
                start = time.perf_counter()
                redact.redact(text)
                self.assertLess(time.perf_counter() - start, self.LIMIT_SECONDS)

    def test_long_runs_of_other_shapes(self):
        for piece in ("://a:", "token=", "-p", "\x00", "eyJ", "sk-", "Cookie: ", "@", " -a"):
            text = piece * (100_000 // len(piece))
            for command in ("", "mysql ", "curl -u a:", "redis-cli ", "sshpass ", "docker login "):
                with self.subTest(piece=piece, command=command):
                    start = time.perf_counter()
                    redact.redact(command + text)
                    self.assertLess(time.perf_counter() - start, self.LIMIT_SECONDS)

    def test_record_cuts_before_redacting(self):
        # 記録は上限の数倍で切ってから伏せる。100 万字でも期限の中で書ける。
        directory = tempfile.mkdtemp(prefix="ccnavi-redact-")
        log = os.path.join(directory, "decisions.jsonl")
        subject = "-" * 1_000_000
        start = time.perf_counter()
        audit.Log(log).write(audit.Record(decision=audit.ALLOW, subject=subject))
        self.assertLess(time.perf_counter() - start, self.LIMIT_SECONDS)
        with open(log, encoding="utf-8") as f:
            line = json.loads(f.read())
        self.assertTrue(line["subject"].endswith(f"…(+{len(subject) - audit.SUBJECT_LIMIT})"))


class RecordTest(unittest.TestCase):
    """記録の 1 行に伏せた形が入り、判定は伏せる前の文字列で下すこと。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-redact-")
        self.log = os.path.join(self.dir, "decisions.jsonl")

    def _lines(self) -> list[dict]:
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_record_fields_are_redacted_but_record_object_is_not(self):
        record = audit.Record(
            decision=audit.ALLOW,
            subject=f"curl -H 'Authorization: Bearer {LONG}' x",
            unwrapped=f"GITHUB_TOKEN={GHP} gh api",
            detail="password=hunter2",
        )
        audit.Log(self.log).write(record)
        (line,) = self._lines()
        for secret in (LONG, GHP, "hunter2"):
            self.assertNotIn(secret, json.dumps(line))
        self.assertIn("Authorization: Bearer", line["subject"])
        self.assertIn(LONG, record.subject)

    def test_redact_runs_before_the_length_limit(self):
        # 上限の手前で値が始まり、上限をまたいで続く形。切ってから伏せると形が途中で切れて
        # 伏せられずに残る。
        pad = "x" * (audit.SUBJECT_LIMIT - 20)
        record = audit.Record(decision=audit.ALLOW, subject=f"{pad} --token {LONG}{LONG}")
        audit.Log(self.log).write(record)
        (line,) = self._lines()
        self.assertNotIn(LONG[:10], line["subject"][len(pad) + 9 :])

    def test_judgement_sees_the_unredacted_command(self):
        # ルールが秘密の値そのものに当たるなら、伏せた記録ではなく伏せる前の文字列で当たる。
        rules = os.path.join(self.dir, ".ccnavi", "common", "rules.yml")
        os.makedirs(os.path.dirname(rules))
        with open(rules, "w", encoding="utf-8") as f:
            f.write(
                "version: 1\n"
                "deny:\n"
                "  - id: no-this-token\n"
                "    match: Bash\n"
                f"    glob: '*{LONG}*'\n"
                "    message: 止める\n"
            )
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_name": "Bash",
            "tool_input": {"command": f"curl -H 'Authorization: Bearer {LONG}' x"},
        }
        proc = run_ccnavi(
            ["--log", self.log, "--state", "", "--ticket-control", "disable"],
            input=json.dumps(payload),
            env={"CLAUDE_PROJECT_DIR": self.dir, "CCNAVI_MODE": "enable"},
            cwd=self.dir,
        )
        self.assertIn("no-this-token", proc.stdout + proc.stderr)
        (line,) = self._lines()
        self.assertEqual(line["decision"], "deny")
        self.assertEqual(line["rules"], ["no-this-token"])
        self.assertNotIn(LONG, line["subject"])

    def test_redact_failure_writes_no_plain_text(self):
        # 伏せる処理が失敗しても記録は書け、その欄には平文の代わりに決まった文が入る。
        record = audit.Record(
            decision=audit.ALLOW,
            subject=f"curl -H 'Authorization: Bearer {LONG}' x",
            unwrapped=f"GITHUB_TOKEN={GHP} gh api",
            detail="password=hunter2",
        )
        with mock.patch.object(redact, "redact", side_effect=RuntimeError("boom")):
            audit.Log(self.log).write(record)
        (line,) = self._lines()
        for secret in (LONG, GHP, "hunter2"):
            self.assertNotIn(secret, json.dumps(line))
        self.assertEqual(line["subject"], audit.REDACT_FAILED)
        self.assertEqual(line["unwrapped"], audit.REDACT_FAILED)
        self.assertEqual(line["detail"], audit.REDACT_FAILED)

    def test_redact_failure_keeps_the_deny(self):
        # 伏せる処理が失敗しても hook は 0 で終わり、出した deny が残る。
        rules = os.path.join(self.dir, ".ccnavi", "common", "rules.yml")
        os.makedirs(os.path.dirname(rules))
        with open(rules, "w", encoding="utf-8") as f:
            f.write(
                "version: 1\n"
                "deny:\n"
                "  - id: no-curl\n"
                "    match: Bash\n"
                "    glob: 'curl *'\n"
                "    message: 止める\n"
            )
        payload = {
            "hook_event_name": "PreToolUse",
            "session_id": "s1",
            "tool_name": "Bash",
            "tool_input": {"command": f"curl -H 'Authorization: Bearer {LONG}' x"},
        }
        with mock.patch.object(redact, "redact", side_effect=RuntimeError("boom")):
            proc = run_ccnavi(
                ["--log", self.log, "--state", "", "--ticket-control", "disable"],
                input=json.dumps(payload),
                env={"CLAUDE_PROJECT_DIR": self.dir, "CCNAVI_MODE": "enable"},
                cwd=self.dir,
            )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(
            json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        (line,) = self._lines()
        self.assertEqual(line["decision"], "deny")
        self.assertEqual(line["subject"], audit.REDACT_FAILED)


if __name__ == "__main__":
    unittest.main()
