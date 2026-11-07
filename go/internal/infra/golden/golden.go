// Package golden は、テストが testdata/<名前>.json に持つ見本の答えを読む・比べる（テストからだけ読む）。
//
// 見本は 2 通り。Load が読むのは、手で持つ見本で、Go の答えを正として守る。
// テストが Go の答えと項目ごとに比べるので、振る舞いを意図して変えたときは、落ちたテストが示す Go の答えに
// 合わせて JSON を手で直す。Check は Go の答えをまるごと見本と比べ、go test の -update で見本を書き直す
// （文面ファイルの文を含むなど、見本が変わりやすい答えに使う）。
package golden

import (
	"bytes"
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"testing"
)

var update = flag.Bool("update", false, "testdata の見本を Go の答えで書き直す（golden.Check の見本）")

// Load は testdata/<name>.json を T に読む。無い・読めないときは落とす（飛ばさない）。
func Load[T any](t testing.TB, name string) T {
	t.Helper()
	var want T
	path := filepath.Join("testdata", name+".json")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, &want); err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	return want
}

// Check は got を JSON にして testdata/<name>.json と比べる。-update なら書き直す。列と表は 1 件を 1 行に書く（git の
// 差分が件ごとに出る）。機械ごとに変わる値は呼び手が置き換えてから渡す。一時置き場の名前とコミットの 40 桁は masked が置き換える。
func Check(t testing.TB, name string, got any) {
	t.Helper()
	data := masked(encode(t, got))
	path := filepath.Join("testdata", name+".json")
	if *update {
		if err := os.MkdirAll("testdata", 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, data, 0o644); err != nil {
			t.Fatal(err)
		}
		return
	}
	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("%v（go test -run '%s' -update で作る）", err, t.Name())
	}
	want = bytes.ReplaceAll(want, []byte("\r\n"), []byte("\n"))
	if bytes.Equal(want, data) {
		return
	}
	// 食い違った最初の行だけを見せる（見本は大きいことがある）。
	gotLines, wantLines := bytes.Split(data, []byte("\n")), bytes.Split(want, []byte("\n"))
	for i := range max(len(gotLines), len(wantLines)) {
		var g, w []byte
		if i < len(gotLines) {
			g = gotLines[i]
		}
		if i < len(wantLines) {
			w = wantLines[i]
		}
		if !bytes.Equal(g, w) {
			t.Errorf("%s の %d 行目が Go の答えと違う（意図した変更なら go test -run '%s' -update で書き直す）\nGo:   %s\n見本: %s",
				path, i+1, t.Name(), g, w)
			return
		}
	}
}

// encode は got を JSON にする。最上段が列か表なら 1 件を 1 行に、それ以外は字下げして書く。
func encode(t testing.TB, got any) []byte {
	t.Helper()
	compact := func(v any) []byte {
		var buf bytes.Buffer
		enc := json.NewEncoder(&buf)
		enc.SetEscapeHTML(false)
		if err := enc.Encode(v); err != nil {
			t.Fatal(err)
		}
		return bytes.TrimSuffix(buf.Bytes(), []byte("\n"))
	}
	// 数は字のまま読み直す（float64 に通すと大きな整数が丸まる）。
	dec := json.NewDecoder(bytes.NewReader(compact(got)))
	dec.UseNumber()
	var top any
	if err := dec.Decode(&top); err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	switch v := top.(type) {
	case []any:
		out.WriteString("[\n")
		for i, item := range v {
			out.Write(compact(item))
			if i < len(v)-1 {
				out.WriteByte(',')
			}
			out.WriteByte('\n')
		}
		out.WriteString("]\n")
	case map[string]any:
		keys := make([]string, 0, len(v))
		for k := range v {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		out.WriteString("{\n")
		for i, k := range keys {
			out.Write(compact(k))
			out.WriteString(": ")
			out.Write(compact(v[k]))
			if i < len(keys)-1 {
				out.WriteByte(',')
			}
			out.WriteByte('\n')
		}
		out.WriteString("}\n")
	default:
		out.Write(compact(top))
		out.WriteByte('\n')
	}
	return out.Bytes()
}

var (
	// tempDir は t.TempDir が作る置き場の頭（一時置き場の直下の 1 段）。名前の数字は回すたびに変わり、桁数も変わる。
	// macOS の一時置き場はリンクなので、リンクを解いた綴りも見る。
	tempDir = regexp.MustCompile(`(?i)(?:` + tempPrefix(os.TempDir()) + `|` + tempPrefix(realpath(os.TempDir())) + `)[^\\/\s"'）]*`)
	// cut は文が長い引数を切った形（頭の数十字と「...」）。頭がルートで始まると、置き場の名前の桁数で切る位置がずれる。
	cut = regexp.MustCompile(`(<tmp>|<root>|\{ROOT\})[^\s"'）]*?\.\.\.`)
	// sha はコミットの 40 桁。テストが写すリポジトリのファイル（CCNAVI.md・文面ファイルなど）が変わるだけで値が変わり、
	// 見本が判定と関係なく落ちるので印に置き換える。
	sha = regexp.MustCompile(`\b[0-9a-f]{40}\b`)
)

// masked は一時置き場の名前を <tmp> に、コミットの 40 桁を <sha> に、ルートで始まって途中で切った引数を切った印だけにする。
func masked(data []byte) []byte {
	data = tempDir.ReplaceAll(data, []byte("<tmp>"))
	data = sha.ReplaceAll(data, []byte("<sha>"))
	return cut.ReplaceAll(data, []byte("$1..."))
}

func tempPrefix(dir string) string {
	return regexp.QuoteMeta(jsonText(filepath.Clean(dir) + string(filepath.Separator)))
}

func realpath(dir string) string {
	if real, err := filepath.EvalSymlinks(dir); err == nil {
		return real
	}
	return dir
}

// jsonText は s を JSON の文字列の中身にした形（Windows の \ は \\ になる）。
func jsonText(s string) string {
	data, _ := json.Marshal(s)
	return string(data[1 : len(data)-1])
}
