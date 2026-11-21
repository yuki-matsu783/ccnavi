package small

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// TestDoctorMasksCredentialsInURLs は、doctor の URL の資格情報の伏せ（doctor_mask）。
// トークンに `@` が入っても、ホストの手前の最後の `@` まで伏せる。scheme の無い `user:token@host:path` の形も見る。
func TestDoctorMasksCredentialsInURLs(t *testing.T) {
	t.Parallel()
	cases := []struct{ in, want string }{
		{"https://oauth2:SEC@RET@host.example/g/p.git", "https://<伏せた>@host.example/g/p.git"},
		{"oauth2:SEC@RET@host.example:g/p.git", "<伏せた>@host.example:g/p.git"},
		{"git@host.example:g/p.git", "git@host.example:g/p.git"},
	}
	shell := firstShell(t)
	doctor := filepath.Join(base.Root(), "scripts", "ccnavi-doctor.sh")
	data, err := os.ReadFile(doctor)
	if err != nil {
		t.Fatal(err)
	}
	// doctor は読み込むと診断を始めるので、関数の定義だけを取り出す。
	fn := regexp.MustCompile(`(?ms)^doctor_mask\(\) \{\n.*?^\}\n`).FindString(string(data))
	if fn == "" {
		t.Fatal("doctor_mask が見つからない")
	}
	for _, c := range cases {
		t.Run(c.in, func(t *testing.T) {
			t.Parallel()
			res := runProgram(t, shell, run.Opts{
				Env: map[string]string{"CCNAVI_PROBE": "git clone " + c.in},
			}, "-c", fn+`doctor_mask "$CCNAVI_PROBE"`, doctor)
			if res.Code != 0 {
				t.Fatalf("code = %d, stderr = %q", res.Code, res.Stderr)
			}
			if got := strings.TrimSuffix(res.Stdout, "\n"); got != "git clone "+c.want {
				t.Errorf("got %q, want %q", got, "git clone "+c.want)
			}
		})
	}
}
