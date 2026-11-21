package hostfixture

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/hoststub"
)

// Host は見本の持ち主のホスト。
type Host string

const (
	GitLab Host = "gitlab"
	GitHub Host = "github"
)

// Getenv は環境変数を引く関数。teststub は os.Getenv を渡し、テストは表を渡す。
type Getenv func(string) string

// FixtureRootVar は見本のあるリポジトリの根を子に伝える環境変数。teststub は指紋の置き場に 1 本だけ組まれて共有されるので、
// 自分の置き場から根を引かず、起こすたびに渡された根から引く。
const FixtureRootVar = "FAKE_FIXTURE_ROOT"

func scenesDir(root string, h Host) string {
	return filepath.Join(root, "extensions", "chrome", "ccnavi-approval", "test", "fixtures", "host", string(h))
}

// SceneNames はレビューの場面（見本のディレクトリ名）。
func SceneNames(t testing.TB, h Host) []string {
	t.Helper()
	entries, err := os.ReadDir(scenesDir(base.Root(), h))
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range entries {
		if e.IsDir() {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	return names
}

// SceneDir は 1 つの場面の見本のディレクトリ。
func SceneDir(h Host, scene string) string { return filepath.Join(scenesDir(base.Root(), h), scene) }

// Env は見本を効かせる環境変数の差分（見本の根と場面）。状態・ログ・投稿者などの FAKE_* は呼び手が足す。
func Env(h Host, scene string) map[string]string {
	return map[string]string{
		FixtureRootVar: base.Root(),
		"FAKE_" + strings.ToUpper(string(h)) + "_SCENE": scene,
	}
}

// requireFixtureEnv は見本の根と場面が渡されているかを見る。空なら変数名を stderr に言って true を返す（呼び手は 1 で終わる）。
// 空のままだと相対パスで見本が読めず、404・403 に見えて原因が分からなくなる。
func requireFixtureEnv(h Host, stderr io.Writer, getenv Getenv) bool {
	sceneVar := "FAKE_" + strings.ToUpper(string(h)) + "_SCENE"
	for _, name := range []string{FixtureRootVar, sceneVar} {
		if getenv(name) == "" {
			fmt.Fprintf(stderr, "%s が無い（hostfixture.Env を通す）\n", name)
			return true
		}
	}
	return false
}

// Install は PATH の先頭に置くスタブを bin に書く。curl は見本を返し、使えない glab（GitHub は gh）は失敗する
// （認証の無い glab・gh は疎通の試しで失敗して curl に切り替わる）。GitHub の gh は `gh api` を見本で答える版を Install ではなく
// InstallGh で置く。
func Install(t testing.TB, bin string, h Host) {
	t.Helper()
	stub := exe.TeststubPath(t)
	hoststub.WriteShim(t, filepath.Join(bin, "curl"), stub, string(h)+"-host", "curl")
	switch h {
	case GitLab:
		hoststub.WriteFail(t, filepath.Join(bin, "glab"))
	case GitHub:
		hoststub.WriteFail(t, filepath.Join(bin, "gh"))
	}
}

// InstallGh は GitHub の `gh api` を見本で答える gh を bin に置く（Install の失敗する gh の代わり）。
func InstallGh(t testing.TB, bin string) {
	t.Helper()
	hoststub.WriteShim(t, filepath.Join(bin, "gh"), exe.TeststubPath(t), "github-host", "gh")
}

// fixtures は 1 つの場面の見本を読む入口。
type fixtures struct {
	dir string
}

// load は見本のファイル。無ければ ok が false。中身は書かれたまま（キーの順を保つ）。
func (f fixtures) load(name string) (json.RawMessage, bool) {
	data, err := os.ReadFile(filepath.Join(f.dir, name))
	if err != nil {
		return nil, false
	}
	return json.RawMessage(data), true
}

// decode は見本のファイルを木にして読む。無ければ nil。
func (f fixtures) decode(name string) any {
	raw, ok := f.load(name)
	if !ok {
		return nil
	}
	return decodeJSON(raw)
}

func decodeJSON(raw []byte) any {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil
	}
	return v
}

type object = map[string]any

func asObject(v any) object { m, _ := v.(object); return m }
func asList(v any) []any    { l, _ := v.([]any); return l }
func str(v any) string {
	switch s := v.(type) {
	case string:
		return s
	case json.Number:
		return s.String()
	}
	return ""
}

// splitURL は API の根からのパス（%2F のまま）とクエリ（同じ名前は最後の値）に分ける。
func splitURL(rel string) (string, map[string]string) {
	path, rawQuery, _ := strings.Cut(rel, "?")
	query := map[string]string{}
	values, _ := url.ParseQuery(rawQuery)
	for k, v := range values {
		query[k] = v[len(v)-1]
	}
	return path, query
}

func marshal(v any) []byte {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	return bytes.TrimRight(buf.Bytes(), "\n")
}

func readState(path string) []any {
	if path == "" {
		return nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	return asList(decodeJSON(raw))
}

func writeState(path string, list []any) error {
	return os.WriteFile(path, marshal(list), 0o644)
}

func appendLine(path string, text string) {
	if path == "" {
		path = os.DevNull
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = io.WriteString(f, text)
}
