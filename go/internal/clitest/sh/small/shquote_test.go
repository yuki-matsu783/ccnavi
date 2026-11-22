package small

import (
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
)

// TestShQuoteMatchesTheTable は、sh の ccnavi_sh_quote が単引用符の写しを表（sh-quote-cases.json）どおりに作ること。
// 実行ファイルの shell.SingleQuote は同じ表で見る。
func TestShQuoteMatchesTheTable(t *testing.T) {
	t.Parallel()
	var tab struct {
		Cases []struct {
			In  string `json:"in"`
			Out string `json:"out"`
		} `json:"cases"`
	}
	loadFixture(t, "sh-quote-cases.json", &tab)
	if len(tab.Cases) == 0 {
		t.Fatal("表が空")
	}
	shells := shellNames()
	if len(shells) == 0 {
		t.Skip("sh が無い")
	}
	common := commonSh()
	for _, shell := range shells {
		for _, c := range tab.Cases {
			t.Run(shell+"/"+c.In, func(t *testing.T) {
				t.Parallel()
				res := runProgram(t, shell, run.Opts{
					Env: map[string]string{"CCNAVI_PROBE": c.In},
				}, "-c", `. "$0"; ccnavi_sh_quote "$CCNAVI_PROBE"`, common)
				if res.Code != 0 {
					t.Fatalf("code = %d, stderr = %q", res.Code, res.Stderr)
				}
				if got := strings.TrimSuffix(res.Stdout, "\n"); got != c.Out {
					t.Errorf("got %q, want %q", got, c.Out)
				}
			})
		}
	}
}
