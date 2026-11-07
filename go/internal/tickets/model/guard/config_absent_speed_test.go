package guard

import (
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
)

// 生の git の式は、長い入力ですぐ返る（tests/config/test_config_absent.py の BuiltinSpeedTest から移した）。
// 組み込みの Bash の deny は go/internal/policy/builtin の TestLongInputsReturnQuickly が見る。
func TestRawGitRegexReturnsQuickly(t *testing.T) {
	const limit = 500 * time.Millisecond
	regex := pyre.MustCompile(rawGitRegex, pyre.IgnoreCase)
	for _, text := range []string{
		strings.Repeat("rm ", 8000), strings.Repeat("git -c ", 8000), strings.Repeat("rm -r a ", 3200),
		strings.Repeat("git -C ", 8000), strings.Repeat("rm -f a ", 3200), strings.Repeat("git reset ", 2400),
		strings.Repeat("/", 24000), strings.Repeat("a/", 12000), strings.Repeat("git --x/git ", 2000),
		"mv " + strings.Repeat("a/", 12000), "echo > " + strings.Repeat("a/", 12000),
		"cp " + strings.Repeat("a/ ", 8000) + ".", `echo "` + strings.Repeat(">a", 12000),
		`echo "` + strings.Repeat(">a/", 8000), `echo "> ` + strings.Repeat("a/", 12000),
	} {
		subject := shell.Read(text).Text
		if subject == "" {
			subject = text
		}
		start := time.Now()
		regex.Search(subject)
		regex.Search(text)
		if took := time.Since(start); took > limit {
			t.Errorf("%q… に %v", text[:16], took)
		}
	}
}
