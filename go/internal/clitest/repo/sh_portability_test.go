package repo

import (
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/pathcase"
)

// 配る sh が macOS の bash 3.2 と BSD の sed・grep でも読めることの検査。書かれた文面を読むだけで、
// bash 3.2 の無い機械でも同じ報告になる。
//
// macOS の sh は bash 3.2 で、2 つの書き方が失敗する（どちらもロケールでは避けられないので、書き方で避ける）。
//  1. 変数のすぐ後ろに全角文字を続ける（`$bin（`）。UTF-8 のロケールでは全角の先頭バイトまで名前に読み、
//     `set -u` の下で unbound variable になる。`${bin}（` と括る
//  2. `$( )` の中に `case` を書く。C のロケールでは `)` を読み違え、ファイルを読む段階で構文エラーになる。
//     `case` 文で変数に入れる
//
// BSD の sed と grep には GNU だけの書き方が 2 つ通らない。
//  3. sed の大文字小文字を区別しない指定（`s/…/…/I`）。字ごとに `[Dd]` と書く
//  4. grep の基本の正規表現の中の `\|`。`grep -E 'a|b'` と書く
//
// 単語の境界（\b）は ASCII の定義で見る（RE2）。この検査の対象の語（sed・grep・case）は ASCII だけなので、
// 全角の字に続く場合の違いは実際のファイルでは起きない。

// 組み立ての出力、依存の置き場、他のワークツリーは見ない。ccnavi が書いた sh ではない。
var skipDirs = map[string]bool{
	".git": true, "node_modules": true, ".venv": true, "dist": true, "build": true,
	"worktrees": true, "projects": true,
}

var (
	nameThenWide   = regexp.MustCompile(`\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]`)
	caseInSubshell = regexp.MustCompile(`\$\(\s*case\b`)
	// 複数行にまたがる `$( )`。行末の `$(` で開き、`)` だけの行で閉じる形を見る。
	opensSubshell = regexp.MustCompile(`\$\(\s*$`)
	closesLine    = regexp.MustCompile(`^\s*\)`)
	caseLine      = regexp.MustCompile(`^\s*case\b`)
	// `s/<探す>/<置く>/` の後ろの指定に I を含む sed（`s/a/b/I`・`s/a/b/gI`）。区切りは / だけを見る。
	sedIgnoreCase = regexp.MustCompile(`\bsed\b[^|;]*\bs/(?:[^/\\]|\\.)*/(?:[^/\\]|\\.)*/[a-zA-Z0-9]*I`)
	grepWord      = regexp.MustCompile(`\bgrep\b`)
	// grep の引数に -E（…E を含む束）が引用符より前にあるか。あれば拡張の正規表現なので `|` は正しい。
	grepFlagE = regexp.MustCompile(`^[^'"]*\s-[a-zA-Z]*E`)
	// 引用符の中に `\|` があるか（BRE の alternation）。
	grepBREAlternation = regexp.MustCompile(`^[^'"|]*['"][^'"]*\\\|`)
)

// codeLine は 1 行（番号つき）。コメントだけの行は含めない（コメントは bash が読まない）。
type codeLine struct {
	number int
	text   string
}

// shellScripts はリポジトリの下の .sh を、リポジトリの根からの相対パス（/ 区切り）で返す。
func shellScripts(t *testing.T, root string) []string {
	t.Helper()
	var found []string
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if path != root && skipDirs[pathcase.Fold(d.Name())] {
				return filepath.SkipDir
			}
			return nil
		}
		if strings.HasSuffix(pathcase.Fold(d.Name()), ".sh") {
			rel, err := filepath.Rel(root, path)
			if err != nil {
				return err
			}
			found = append(found, filepath.ToSlash(rel))
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return found
}

// codeLines は .sh の行をコメントを除いて読む。改行は CRLF も LF に揃える（Windows の取り出しでも同じ答えにする）。
func codeLines(t *testing.T, path string) []codeLine {
	t.Helper()
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var out []codeLine
	for i, line := range strings.Split(strings.ReplaceAll(string(body), "\r\n", "\n"), "\n") {
		if !strings.HasPrefix(strings.TrimLeft(line, " \t"), "#") {
			out = append(out, codeLine{number: i + 1, text: line})
		}
	}
	return out
}

// grepBRE は行の中に、grep の引数として BRE の `\|` を書いた箇所があるかを見る。
// 負の先読みを使えないので、grep の起こしごとに、-E の有無を見てから引用符の中を見る。
func grepBRE(line string) bool {
	for _, at := range grepWord.FindAllStringIndex(line, -1) {
		rest := line[at[1]:]
		if grepFlagE.MatchString(rest) {
			continue
		}
		if grepBREAlternation.MatchString(rest) {
			return true
		}
	}
	return false
}

// found は各 .sh のコード行を pattern に掛け、ヒットを「相対パス:行: 行の中身」の並びで返す。
func found(t *testing.T, root string, match func(string) bool) []string {
	t.Helper()
	var hits []string
	for _, rel := range shellScripts(t, root) {
		for _, l := range codeLines(t, filepath.Join(root, filepath.FromSlash(rel))) {
			if match(l.text) {
				hits = append(hits, rel+":"+strconv.Itoa(l.number)+": "+strings.TrimSpace(l.text))
			}
		}
	}
	return hits
}

func TestShFilesAreFound(t *testing.T) {
	t.Parallel()
	// 探し方が誤っていて 1 本も見ないまま通る、を防ぐ。
	names := map[string]bool{}
	for _, rel := range shellScripts(t, base.Root()) {
		names[filepath.Base(rel)] = true
	}
	for _, want := range []string{"ccnavi-setup.sh", "ccnavi-git.sh", "ccnavi-user-push-approved.sh"} {
		if !names[want] {
			t.Errorf("%s が見つからない", want)
		}
	}
}

func TestVariableIsBracedBeforeAWideCharacter(t *testing.T) {
	t.Parallel()
	if hits := found(t, base.Root(), nameThenWide.MatchString); len(hits) > 0 {
		t.Errorf("${name} と括ってください: %v", hits)
	}
}

func TestCaseIsNotWrittenInsideCommandSubstitution(t *testing.T) {
	t.Parallel()
	if hits := found(t, base.Root(), caseInSubshell.MatchString); len(hits) > 0 {
		t.Errorf("case 文で変数に入れてください: %v", hits)
	}
}

// `x=$(` から `)` までの間に case を書かない。1 行の検査では見えない形。
// ループを丸ごと `$( )` で包んで報せを集める書き方で起きる。case の要るものは `$( )` の外で関数にし、中からは呼ぶだけにする。
func TestCaseIsNotWrittenInsideAMultilineCommandSubstitution(t *testing.T) {
	t.Parallel()
	var hits []string
	for _, rel := range shellScripts(t, base.Root()) {
		inside := false
		for _, l := range codeLines(t, filepath.Join(base.Root(), filepath.FromSlash(rel))) {
			if !inside {
				inside = opensSubshell.MatchString(l.text)
				continue
			}
			if closesLine.MatchString(l.text) && strings.TrimSpace(strings.TrimRight(strings.TrimSpace(l.text), ")")) == "" {
				inside = false
			} else if caseLine.MatchString(l.text) {
				hits = append(hits, rel+":"+strconv.Itoa(l.number)+": "+strings.TrimSpace(l.text))
			}
		}
	}
	if len(hits) > 0 {
		t.Errorf("case は $( ) の外の関数に移してください: %v", hits)
	}
}

func TestSedDoesNotIgnoreCaseWithTheGNUFlag(t *testing.T) {
	t.Parallel()
	if hits := found(t, base.Root(), sedIgnoreCase.MatchString); len(hits) > 0 {
		t.Errorf("大文字小文字は [Dd] のように字ごとに書いてください: %v", hits)
	}
}

func TestGrepDoesNotUseTheGNUAlternation(t *testing.T) {
	t.Parallel()
	if hits := found(t, base.Root(), grepBRE); len(hits) > 0 {
		t.Errorf("grep -E 'a|b' と書いてください: %v", hits)
	}
}

// 検査の正規表現が、直したい形を見つけ、直した形を見逃すこと。
func TestTheGNUFormsAreCaught(t *testing.T) {
	t.Parallel()
	sedCases := []struct {
		line string
		hit  bool
	}{
		{"x=$(printf '%s' \"$t\" | sed -E 's/^(Draft|WIP)://I')", true},
		{"printf x | sed 's/a/b/gI'", true},
		{"sed -E 's/^([Dd]raft)://'", false},
		{"sed -n 's/^Item //p'", false},
		{"sed 's|a|b|'", false},
	}
	for _, c := range sedCases {
		if got := sedIgnoreCase.MatchString(c.line); got != c.hit {
			t.Errorf("sed の検査 %q = %v, want %v", c.line, got, c.hit)
		}
	}
	grepCases := []struct {
		line string
		hit  bool
	}{
		{"elif grep -qi 'gpg\\|signing' \"$1\"; then", true},
		{"grep 'a\\|b' f", true},
		{"elif grep -Eqi 'gpg|signing' \"$1\"; then", false},
		{"grep -E 'a\\|b' f", false},
		{"grep -q 'index\\.lock' f", false},
	}
	for _, c := range grepCases {
		if got := grepBRE(c.line); got != c.hit {
			t.Errorf("grep の検査 %q = %v, want %v", c.line, got, c.hit)
		}
	}
}

// Windows で core.autocrlf=true だと、取り出すときに CRLF になり、シバンが `#!/bin/sh\r` になって直に起動する sh が動かない。
// .gitattributes で LF に固定する。書かれた文面ではなく、git が決める属性を聞く。
func TestShFilesAreCheckedOutWithLF(t *testing.T) {
	t.Parallel()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	root := base.Root()
	paths := shellScripts(t, root)
	if len(paths) == 0 {
		t.Fatal("sh が 1 本も見つからない")
	}
	cmd := exec.Command("git", append([]string{"-C", root, "check-attr", "eol", "--"}, paths...)...)
	cmd.Env = run.Environment(run.Opts{})
	out, err := cmd.Output()
	if err != nil {
		t.Skip("git のワークツリーではない")
	}
	var wrong []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if !strings.HasSuffix(line, ": eol: lf") {
			wrong = append(wrong, line)
		}
	}
	if len(wrong) > 0 {
		t.Errorf(".gitattributes で *.sh を eol=lf にしてください: %v", wrong)
	}
}
