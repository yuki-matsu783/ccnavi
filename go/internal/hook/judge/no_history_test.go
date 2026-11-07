package judge

import (
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// PreToolUse の判定（judge と failsafe）は git の履歴を読まない（tests/guard/test_gate_kinds.py から移した）。
// 手で done/ に動かした子の決着（phase.AssessManual）と、Stop の依頼の判定（tickets/lifecycle/stop）は履歴を読むので、
// 判定の期限に収まらない。どちらも参照しないことを、ソースの参照で固定する。
func TestPreToolUseDoesNotReachTheHistoryReaders(t *testing.T) {
	for _, dir := range []string{".", filepath.Join("..", "failsafe")} {
		files, err := filepath.Glob(filepath.Join(dir, "*.go"))
		if err != nil {
			t.Fatal(err)
		}
		for _, path := range files {
			if strings.HasSuffix(path, "_test.go") {
				continue
			}
			data, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(data), "AssessManual") {
				t.Errorf("%s が AssessManual を呼ぶ", path)
			}
			f, err := parser.ParseFile(token.NewFileSet(), path, data, parser.ImportsOnly)
			if err != nil {
				t.Fatal(err)
			}
			for _, spec := range f.Imports {
				name, _ := strconv.Unquote(spec.Path.Value)
				if name == "ccnavi/internal/tickets/lifecycle/stop" || name == "ccnavi/internal/tickets/core" {
					t.Errorf("%s が %s を読む", path, name)
				}
			}
		}
	}
}
