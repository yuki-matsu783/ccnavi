"""記録から秘密の形を伏せる（redact）の単体テストと、記録への配線の受入テスト。

見るのは 3 つ。

1. 形ごとに値が伏せられ、周りの綴り（名前・方式の語・コマンド）は残ること
2. 短い値は全部、長い値は頭 6 字と尻 4 字を残して伏せること
3. 伏せるのは記録の 1 行だけで、判定は伏せる前の文字列で下すこと
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from ccnavi import audit, redact
from tests.inproc import run_ccnavi

# 形だけを持つ見本。本物のトークンではない。
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


class RecordTest(unittest.TestCase):
    """記録の 1 行に伏せた形が入り、判定は伏せる前の文字列で下すこと。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-redact-")
        self.log = os.path.join(self.dir, "log.jsonl")

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
        # 上限の手前で値が始まり、上限をまたいで続く形。切ってから伏せると形が崩れて残る。
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


if __name__ == "__main__":
    unittest.main()
