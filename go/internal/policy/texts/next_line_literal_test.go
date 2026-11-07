package texts

import (
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// 出力の行頭の `次: ` を、文字列リテラルで直に組み立てるコードを増やさない（tests/core/test_next_line_literal.py の
// Go 版）。`次:` の文は文面ファイル（.ccnavi/messages/next-*.yml）に置いて Say で引く（.agents/docs/message-files.md）。
// 数えるのは、値が `次: ` で始まって続きがあるリテラルと、途中の改行の直後に `次: ` があるリテラル。
// NextLabel のような見出しだけの値（比較や組み立ての部品）は数えない。_test.go は見ない。
func TestNoLiteralNextLines(t *testing.T) {
	goDir := filepath.Join(repoRoot(t), "go")
	var found []string
	err := filepath.WalkDir(goDir, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return err
		}
		files := token.NewFileSet()
		file, err := parser.ParseFile(files, path, nil, 0)
		if err != nil {
			return err
		}
		ast.Inspect(file, func(n ast.Node) bool {
			lit, ok := n.(*ast.BasicLit)
			if !ok || lit.Kind != token.STRING {
				return true
			}
			value, err := strconv.Unquote(lit.Value)
			if err != nil {
				return true
			}
			if (strings.HasPrefix(value, NextLabel) && len(value) > len(NextLabel)) || strings.Contains(value, "\n"+NextLabel) {
				rel, _ := filepath.Rel(goDir, path)
				found = append(found, filepath.ToSlash(rel)+":"+strconv.Itoa(files.Position(lit.Pos()).Line))
			}
			return true
		})
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(found) > 0 {
		t.Errorf("`次: ` の文を Go に直書きしている。文を文面ファイル（.ccnavi/messages/next-cli.yml など）に足し、Say で引く: %v", found)
	}
}
