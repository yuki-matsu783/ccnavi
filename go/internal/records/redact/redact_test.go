package redact

import (
	"strings"
	"testing"
	"time"
)

// 形だけを持つ見本。実際のトークンではない。
var (
	ghp   = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
	glpat = "glpat-" + "abcdefghij0123456789"
	akia  = "AKIA" + "ABCDEFGHIJKLMNOP"
	long  = "s3cr3t-value-that-is-long-enough"
)

func TestMask(t *testing.T) {
	cases := []struct{ in, want string }{
		{"hunter2", Stars},
		{strings.Repeat("x", short-1), Stars},
		{"abcdef0123456789wxyz", "abcdef***wxyz"},
		{strings.Repeat("a", short), "aaaaaa***aaaa"}, // 境界は長い側
		{strings.Repeat("あ", short), "ああああああ***ああああ"}, // 字数は符号位置で数える
	}
	for _, c := range cases {
		if got := Mask(c.in); got != c.want {
			t.Errorf("Mask(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestUntouched(t *testing.T) {
	for _, text := range []string{
		"", "git push origin main", "mkdir -p src/a && cp -p a b", "ssh -p 22 host", "sort -u names.txt",
		"python -m tokenizer --secretName foo",
		"GITHUB_TOKEN=$GITHUB_TOKEN gh pr list", `TOKEN="${TOKEN}" sh x.sh`, // 変数を指すだけの値
		"mysql -p db",                                                              // -p の後ろに空白があれば次の語はパスワードではない
		"mysql -e x; mkdir -pv a",                                                  // mysql の外の -p
		"PWD=/home/me make",                                                        // 素の PWD はいまの場所
		"git checkout task-1234567890abcdefghijkl", "ls disk-0123456789abcdefghij", // 語の途中の sk-
		"sshpass -f pw.txt ssh -p 2222 host", "docker run -p 8080:80 img", "ls -a; mkdir -p x",
		"redis-cli ping; ls -a src",
	} {
		if got := Redact(text); got != text {
			t.Errorf("Redact(%q) = %q, want unchanged", text, got)
		}
	}
}

func TestHidden(t *testing.T) {
	cases := []struct {
		text, secret string
		kept         []string
	}{
		{"curl -H 'Authorization: Bearer " + long + "' https://api.example.com", long, []string{"Authorization: Bearer", "https://api.example.com", Mask(long)}},
		{`curl -H "Authorization: tok12345" x`, "tok12345", []string{"Authorization:"}},
		{"echo Bearer " + long, long, []string{"Bearer"}},
		{"GITHUB_TOKEN=abc123 gh pr list", "abc123", []string{"GITHUB_TOKEN="}},
		{"curl 'https://x/api?token=abc123&page=2'", "abc123", []string{"&page=2"}},
		{"mysql --password=hunter2 db", "hunter2", []string{"--password="}},
		{"export DB_PASSWORD='p a s s'", "p a s s", []string{"DB_PASSWORD="}},
		{`{"api_key": "k-123456"}`, "k-123456", []string{`"api_key"`}},
		{"aws_secret_access_key = wJalrXUtnFEMI", "wJalrXUtnFEMI", []string{"aws_secret_access_key"}},
		{"client_secret: zzz999", "zzz999", []string{"client_secret:"}},
		{"x-api-key: kkk111", "kkk111", []string{"x-api-key:"}},
		{"SECRET_KEY=abc123 python app.py", "abc123", []string{"SECRET_KEY="}},
		{"export aws_secret_key=abc123", "abc123", []string{"aws_secret_key="}},
		{"MYSQL_PWD=hunter2 mysql db", "hunter2", []string{"MYSQL_PWD="}},
		{"echo //registry.npmjs.org/:_authToken=npmsecret1 >> x", "npmsecret1", []string{"_authToken="}},
		{"SESSION_COOKIE=abc123 run", "abc123", []string{"SESSION_COOKIE="}},
		{"docker login --password hunter2 -u me", "hunter2", []string{"--password"}},
		{"tool --token 'abc def'", "abc def", []string{"--token"}},
		{"git clone https://oauth2:" + glpat + "@gitlab.example.com/g/r.git", glpat, []string{"https://oauth2:", "@gitlab.example.com/g/r.git"}},
		// パスワードに @ が入っても、ホストの手前の最後の @ までを伏せる（ccnavi_mask_url と同じ）。
		{"psql postgres://user:P@ssw0rd!@db.example.com/app", "ssw0rd", []string{"postgres://user:", "@db.example.com/app"}},
		{"mysql -u root -phunter2 db", "hunter2", []string{"mysql -u root -p", " db"}},
		{"cd x && mysqldump -p'hunter 2' db", "hunter 2", []string{"mysqldump"}},
		{"curl -u me:hunter2 https://x", "hunter2", []string{"curl -u me:"}},
		{"curl -H 'Cookie: session=abc123; id=zz99' https://x", "abc123", []string{"Cookie:", "https://x", Mask("session=abc123; id=zz99")}},
		{"curl -H 'Cookie: a=1; token=abc123' x", "abc123", []string{"Cookie:"}},
		{"curl -b 'sid=abc123' https://x", "abc123", []string{"curl -b"}},
		{"curl --cookie sid=abc123 https://x", "abc123", []string{"--cookie"}},
		{"aws configure set aws_secret_access_key wJalrXUtnFEMI --profile p", "wJalrXUtnFEMI", []string{"aws_secret_access_key", "--profile p"}},
		{"sshpass -p hunter2 ssh -p 22 me@host", "hunter2", []string{"ssh -p 22"}},
		{"sshpass -phunter2 scp a b", "hunter2", []string{"sshpass"}},
		{"docker login -u me -p hunter2 reg.io", "hunter2", []string{"reg.io"}},
		{"redis-cli -h h -a hunter2 ping", "hunter2", []string{"redis-cli -h h -a", "ping"}},
		{"cat > k <<EOF\n-----BEGIN RSA PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END RSA PRIVATE KEY-----\nEOF",
			"MIIEvQIBADANBgkqhkiG9w0BAQEFAASC", []string{"BEGIN RSA PRIVATE KEY", "END RSA PRIVATE KEY", "EOF"}},
		// Python の IGNORECASE は i に ı・İ も当てる。
		{"PRİVATE_KEY=abc123", "abc123", []string{"PRİVATE_KEY="}},
	}
	for _, c := range cases {
		out := Redact(c.text)
		if strings.Contains(out, c.secret) {
			t.Errorf("Redact(%q) = %q still has %q", c.text, out, c.secret)
		}
		for _, word := range c.kept {
			if !strings.Contains(out, word) {
				t.Errorf("Redact(%q) = %q lost %q", c.text, out, word)
			}
		}
	}
}

func TestTokenShapes(t *testing.T) {
	for _, token := range []string{
		ghp, "github_pat_" + strings.Repeat("a", 30), glpat, akia, "xoxb-" + "1234567890-abc",
		"sk-ant-api03-" + strings.Repeat("A1b2C3d4E5", 3), "sk-proj-" + strings.Repeat("A1b2C3d4E5", 3),
		"sk-" + strings.Repeat("A1b2C3d4E5", 4), "sk_live_" + strings.Repeat("A1b2C3d4E5", 2),
		"rk_live_" + strings.Repeat("A1b2C3d4E5", 2), "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6",
		"npm_" + strings.Repeat("A1b2C3d4E5", 3) + "abcdef",
		"hf_" + strings.Repeat("A1b2C3d4E5", 3) + "abcd", "dop_v1_" + strings.Repeat("0123456789abcdef", 4),
		"ya29." + "a0AfH6SMB" + strings.Repeat("A1b2C3d4E5", 2), "pypi-" + "AgEIcHlwaS5vcmc" + strings.Repeat("A1b2C3d4E5", 4),
		"eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + ".dozjgNryP4J3jVmNHl0w5N",
	} {
		out := Redact("echo " + token + " > /dev/null")
		if strings.Contains(out, token) || !strings.Contains(out, token[:6]) || !strings.HasPrefix(out, "echo ") {
			t.Errorf("token %q: got %q", token[:8], out)
		}
	}
}

func TestExactOutputs(t *testing.T) {
	cases := []struct{ in, want string }{
		// 同じ値に 2 つの形が当たっても 1 度だけ伏せる（残した頭と尻まで消さない）。
		{"GITHUB_TOKEN=" + ghp + " gh pr list", "GITHUB_TOKEN=" + Mask(ghp) + " gh pr list"},
		// unwrapped はコマンドを NUL でつなぐ。値はそこで終わる。
		{"GITHUB_TOKEN=abc123\x00gh pr list", "GITHUB_TOKEN=***\x00gh pr list"},
		{"mysql -phunter2\x00ls -a", "mysql -p***\x00ls -a"},
		// 空白はまたがない。後ろの語の @ まで伸ばさない。
		{"git clone https://u:pw@host/r.git && ssh me@other", "git clone https://u:***@host/r.git && ssh me@other"},
		// 結びの無い秘密キーは終わりまで。Python の $ と同じく最後の改行は残す。
		{"-----BEGIN PRIVATE KEY-----abcdefghijklmnopqrstuvwxyz\n", "-----BEGIN PRIVATE KEY-----abcdef***wxyz\n"},
	}
	for _, c := range cases {
		if got := Redact(c.in); got != c.want {
			t.Errorf("Redact(%q) = %q, want %q", c.in, got, c.want)
		}
	}
	out := Redact("A_TOKEN=aaa111 B_PASSWORD=bbb222 run " + akia)
	for _, secret := range []string{"aaa111", "bbb222", akia} {
		if strings.Contains(out, secret) {
			t.Errorf("%q still has %q", out, secret)
		}
	}
}

// 名前の長さに上限を置かない。65 字以上の名前の値も伏せる（回帰）。
func TestLongNames(t *testing.T) {
	const secret = "abcdefghijklmnopqrst"
	for _, size := range []int{64, 65, 200, 10_000} {
		for _, head := range []string{"x", "-", "_."} {
			name := strings.Repeat(head, size)[:size] + "token="
			if got, want := Redact(name+secret), name+Mask(secret); got != want {
				t.Errorf("size %d head %q: got …%q", size, head, got[len(got)-30:])
			}
		}
	}
	// 長くても名前がキーワードで終わらなければ読まない。
	if text := strings.Repeat("x", 100) + "tokenizer=abc123"; Redact(text) != text {
		t.Errorf("tokenizer: %q", Redact(text))
	}
	got := Redact("az storage blob list --connection-string " +
		"'DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=abc123def456==;EndpointSuffix=core.windows.net'")
	if strings.Contains(got, "abc123def456") || !strings.Contains(got, "AccountKey=***;EndpointSuffix=core.windows.net") {
		t.Errorf("AccountKey: %q", got)
	}
}

// 長い入力でも手間が長さにほぼ比例すること。記録は実行前チェックの期限の中で書き、期限を過ぎると hook の拒否が破棄される。
// 名前の形を名前の頭から照らすと、`-` が続くだけの 1.5 万字で期限を過ぎた（回帰）。
func TestSpeed(t *testing.T) {
	const limit = 500 * time.Millisecond
	check := func(t *testing.T, text string) {
		start := time.Now()
		Redact(text)
		if d := time.Since(start); d > limit {
			t.Errorf("%q…: %v", text[:min(20, len(text))], d)
		}
	}
	for _, ch := range []string{"-", "_", ".", "a", "A", "a-", "x_"} {
		check(t, strings.Repeat(ch, 100_000/len(ch)))
	}
	pieces := []string{"://a:", "token=", "-p", "\x00", "eyJ", "sk-", "Cookie: ", "@", " -a", "xsk-", "xauthorization:", strings.Repeat("a", 65) + "token="}
	for _, piece := range pieces {
		t.Run("", func(t *testing.T) {
			t.Parallel()
			text := strings.Repeat(piece, 100_000/len(piece))
			for _, command := range []string{"", "mysql ", "curl -u a:", "redis-cli ", "sshpass ", "docker login "} {
				check(t, command+text)
			}
		})
	}
}
