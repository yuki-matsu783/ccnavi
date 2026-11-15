package shell

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// sh の ccnavi_sh_quote と同じ表（tests/fixtures/sh-quote-cases.json）で、SingleQuote の答えを確かめる。
// sh の答えは tests/sh/test_sh_quote_sh.py が同じ表で見る。
func TestSingleQuoteMatchesSh(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "tests", "fixtures", "sh-quote-cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var table struct {
		Cases []struct{ In, Out string } `json:"cases"`
	}
	if err := json.Unmarshal(data, &table); err != nil {
		t.Fatal(err)
	}
	if len(table.Cases) == 0 {
		t.Fatal("表が空")
	}
	for _, c := range table.Cases {
		if got := SingleQuote(c.In); got != c.Out {
			t.Errorf("SingleQuote(%q) = %q, want %q", c.In, got, c.Out)
		}
	}
}
