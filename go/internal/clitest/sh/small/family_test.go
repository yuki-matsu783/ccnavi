package small

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// TestFamilyStripsTwoStepsFromTheRight は、sh が識別子だけから親を割り出すこと（ccnavi_wap_family）。
// 子は `<親>-<2 桁のフェーズ番号>-<2 桁の連番>`。右から 2 階層を剥がす。実行ファイルを起こせないときは
// 割り出した親を自分として `stop` と答える（取り込み状態には触らない）。
func TestFamilyStripsTwoStepsFromTheRight(t *testing.T) {
	t.Parallel()
	shell := firstShell(t)
	common := commonSh()
	root := base.TempDir(t)
	const script = `. "$0"; ccnavi_wap_root=$1; ccnavi_wap_family "$2"; ` +
		`printf "%s %s\n" "$ccnavi_wap_family_id" "$ccnavi_wap_target"`
	cases := []struct{ ident, parent string }{
		{"web-i0012-05-01", "web-i0012"},
		{"i0001-01-01", "i0001"},
		{"i0001-00-12", "i0001"},
		{"abc-01-02-03", "abc-01"},
		{"i0001", "i0001"},
		{"web-i0012", "web-i0012"},
		// 古い形は子として扱わない（自身が親）
		{"i0012-01", "i0012-01"},
	}
	for _, c := range cases {
		t.Run(c.ident, func(t *testing.T) {
			t.Parallel()
			res := runProgram(t, shell, run.Opts{
				Env: map[string]string{"CCNAVI_BIN": filepath.Join(root, "no-such-ccnavi")},
			}, "-c", script, common, root, c.ident)
			if res.Code != 0 {
				t.Fatalf("code = %d, stderr = %q", res.Code, res.Stderr)
			}
			lines := strings.Split(strings.TrimSpace(res.Stdout), "\n")
			got := strings.SplitN(lines[len(lines)-1], " ", 2)
			if len(got) != 2 || got[0] != c.parent || got[1] != "stop" {
				t.Errorf("got %q, want [%s stop]", got, c.parent)
			}
		})
	}
}
