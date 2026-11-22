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

// TestEnvNamesOnlyInEnvPackage は、本体のコードが環境変数の名前を直に書いていないことを確かめる。
func TestEnvNamesOnlyInEnvPackage(t *testing.T) {
	err := filepath.WalkDir(".", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		// テストは対象外（テストは環境を組むために名前を書いてよい）。
		if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		// 名前を定義する infra/env 自身は除く。
		if filepath.ToSlash(filepath.Dir(path)) == envPackage {
			return nil
		}
		fset := token.NewFileSet()
		f, err := parser.ParseFile(fset, path, nil, 0)
		if err != nil {
			return err
		}
		ast.Inspect(f, func(n ast.Node) bool {
			// os.Getenv("…") のような呼び出しの第 1 引数の直書きを見る。名前の形に合わない短い名前（PATH など）もここで拾う。
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
			// どの位置の文字列でも、ccnavi / Claude Code の環境変数の名前の形なら直書きとみなす。
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

// harnessRoots は CLI を通すテストの道具と、その使い手。ここの _test.go は base.Main の TestMain で始める。
var harnessRoots = []string{"harness", "clitest"}

// 並行のテストと一緒に使えない・機械で結果が変わる呼び出し。clitest の _test.go では使わない（決まりは clitest/doc.go）。
var forbiddenInClitest = map[string]string{
	"t.Setenv":    "子の環境は run.Opts.Env で渡す",
	"t.Chdir":     "作業ディレクトリは run.Opts.Dir で渡す",
	"os.Setenv":   "子の環境は run.Opts.Env で渡す",
	"os.Chdir":    "作業ディレクトリは run.Opts.Dir で渡す",
	"t.TempDir":   "base.TempDir を使う（名前が短く、macOS で実パスになる）",
	"os.Unsetenv": "子の環境は run.Opts.Unset で渡す",
}

// TestClitestFollowsTheHarnessRules は、clitest と harness の _test.go が土台の決まりを守ることを見る。
// パッケージごとに base.Main の TestMain があること（無いと git の締め出しも環境の掃除も効かない）と、
// clitest が t.Setenv・os.Chdir・t.TempDir などを使わないこと。
func TestClitestFollowsTheHarnessRules(t *testing.T) {
	hasMain := map[string]bool{}
	hasTests := map[string]bool{}
	for _, top := range harnessRoots {
		err := filepath.WalkDir(top, func(path string, d fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if d.IsDir() || !strings.HasSuffix(path, "_test.go") {
				return nil
			}
			dir := filepath.ToSlash(filepath.Dir(path))
			hasTests[dir] = true
			fset := token.NewFileSet()
			f, err := parser.ParseFile(fset, path, nil, 0)
			if err != nil {
				return err
			}
			for _, decl := range f.Decls {
				if fn, ok := decl.(*ast.FuncDecl); ok && fn.Name.Name == "TestMain" && callsBaseMain(fn) {
					hasMain[dir] = true
				}
			}
			if top != "clitest" {
				return nil
			}
			ast.Inspect(f, func(n ast.Node) bool {
				if call, ok := n.(*ast.CallExpr); ok {
					if sel, ok := call.Fun.(*ast.SelectorExpr); ok {
						if x, ok := sel.X.(*ast.Ident); ok {
							if why, bad := forbiddenInClitest[x.Name+"."+sel.Sel.Name]; bad {
								t.Errorf("%s: %s.%s は使わない。%s", fset.Position(call.Pos()), x.Name, sel.Sel.Name, why)
							}
						}
					}
				}
				return true
			})
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	for dir := range hasTests {
		if !hasMain[dir] {
			t.Errorf("%s: base.Main を呼ぶ TestMain が無い（clitest/doc.go）", dir)
		}
	}
}

// callsBaseMain は TestMain が base.Main(m)（base パッケージの中では Main(m)）を呼ぶか。
func callsBaseMain(fn *ast.FuncDecl) bool {
	found := false
	ast.Inspect(fn, func(n ast.Node) bool {
		if call, ok := n.(*ast.CallExpr); ok {
			switch f := call.Fun.(type) {
			case *ast.Ident:
				found = found || f.Name == "Main"
			case *ast.SelectorExpr:
				if x, ok := f.X.(*ast.Ident); ok {
					found = found || (x.Name == "base" && f.Sel.Name == "Main")
				}
			}
		}
		return true
	})
	return found
}
