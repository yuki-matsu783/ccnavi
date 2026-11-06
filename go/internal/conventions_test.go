package internal

// 書き方の決まりのうち、機械で見られるものを見る。決まりそのものは .agents/docs/go-rewrite.md の「書き方の標準」。

import (
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

// envName は ccnavi と Claude Code の環境変数の名前と、リモートのトークンの名前。infra/env の定数でだけ書く。
var envName = regexp.MustCompile(`^(?:(?:CCNAVI|CLAUDE)_[A-Z0-9_]+|GITHUB_TOKEN|GITLAB_TOKEN)$`)

// envReaders は環境変数を名前で読み書きする関数。名前を文字列で渡すと、PATH や HOME のような短い名前も
// infra/env を通らずに書けてしまうので、引数の直書きを見る（"PATH" は引数の名前の表記にも使うので、
// 文字列そのものは見ない）。
var envReaders = map[string]bool{"Getenv": true, "LookupEnv": true, "Setenv": true, "Unsetenv": true}

const envPackage = "infra/env"

func TestEnvNamesOnlyInEnvPackage(t *testing.T) {
	err := filepath.WalkDir(".", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		if filepath.ToSlash(filepath.Dir(path)) == envPackage {
			return nil
		}
		fset := token.NewFileSet()
		f, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			return err
		}
		ast.Inspect(f, func(n ast.Node) bool {
			if call, ok := n.(*ast.CallExpr); ok && len(call.Args) > 0 {
				if sel, ok := call.Fun.(*ast.SelectorExpr); ok && envReaders[sel.Sel.Name] {
					pkg, isIdent := sel.X.(*ast.Ident)
					lit, isLit := call.Args[0].(*ast.BasicLit)
					if isIdent && pkg.Name == "os" && isLit && lit.Kind == token.STRING {
						t.Errorf("%s: os.%s に環境変数の名前 %s を直書きしている。%s の定数を使う",
							fset.Position(lit.Pos()), sel.Sel.Name, lit.Value, envPackage)
					}
				}
			}
			lit, ok := n.(*ast.BasicLit)
			if !ok || lit.Kind != token.STRING {
				return true
			}
			if s, err := strconv.Unquote(lit.Value); err == nil && envName.MatchString(s) {
				t.Errorf("%s: 環境変数の名前 %q を直書きしている。%s の定数を使う", fset.Position(lit.Pos()), s, envPackage)
			}
			return true
		})
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}
