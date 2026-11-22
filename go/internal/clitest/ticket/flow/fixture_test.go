package flow

// VS Code 拡張（ccnavi-board）のフィクスチャと、実行ファイルの JSON が同じ形・同じ値であることを見る道具。
// 形を変えたら `go test ./internal/clitest/ticket/flow -run <テスト> -update-board-fixtures` でフィクスチャを書き直す
// （clitest/core の boardfixture_test.go と同じフラグ名）。
import (
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/ticketkit"
	"ccnavi/internal/infra/pyjson"
)

var updateBoardFixtures = flag.Bool("update-board-fixtures", false, "VS Code 拡張のフィクスチャを、実行ファイルの出力で書き直す")

func fixturePath(name string) string {
	return filepath.Join(base.Root(), "extensions", "vscode", "ccnavi-board", "test", "fixtures", name)
}

func readFixture(t testing.TB, name string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(fixturePath(name))
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("%s: %v", name, err)
	}
	return m
}

// portable は絶対パスを <root> に置き換えた JSON の値。フィクスチャに書く分だけ。
func portable(v any, root string) any {
	switch x := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, e := range x {
			out[k] = portable(e, root)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = portable(e, root)
		}
		return out
	case string:
		return ticketkit.PortableText(x, root)
	}
	return v
}

var digestRe = regexp.MustCompile(`^[0-9a-f]{64}$`)

// maskDigests は本文のダイジェスト（SHA-256 の 16 進）を固定の文字列に置き換える。本文は一時ディレクトリのパスを含むので、
// 走らせるたびに値が変わる。形だけを確かめる。
func maskDigests(t testing.TB, v any) any {
	t.Helper()
	switch x := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, e := range x {
			if s, ok := e.(string); ok && (k == "digest" || k == "expected" || k == "current") && digestRe.MatchString(s) {
				out[k] = "<digest>"
				continue
			}
			out[k] = maskDigests(t, e)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = maskDigests(t, e)
		}
		return out
	}
	return v
}

// portableJSON は実行ファイルの JSON（stdout）を、フィクスチャに書く形の JSON テキストにする。
// 値に対して、走らせた場所は <root>、時刻（generated_at）は <time>、ダイジェスト（digest・expected・current）は <digest> に置き換える。
// テキストではなく値を置き換えるので、JSON のエスケープ（\n・\"・\\）は壊れない。
func portableJSON(stdout, root string) (string, error) {
	decoded, err := pyjson.Loads(stdout)
	if err != nil {
		return "", err
	}
	text, err := pyjson.Dumps(portableValue(decoded, root), pyjson.Options{RawUnicode: true, Indent: 1})
	if err != nil {
		return "", err
	}
	return text + "\n", nil
}

func portableValue(v any, root string) any {
	switch x := v.(type) {
	case *pyjson.Object:
		for _, k := range x.Keys() {
			e := x.Value(k)
			if s, ok := e.(string); ok {
				if k == "generated_at" {
					x.Set(k, "<time>")
					continue
				}
				if (k == "digest" || k == "expected" || k == "current") && digestRe.MatchString(s) {
					x.Set(k, "<digest>")
					continue
				}
			}
			x.Set(k, portableValue(e, root))
		}
		return x
	case []any:
		for i, e := range x {
			x[i] = portableValue(e, root)
		}
		return x
	case string:
		return ticketkit.PortableText(x, root)
	}
	return v
}

// rewriteFixture は -update-board-fixtures のとき、stdout（実行ファイルの JSON。キーの順を保つ）を name へ書く。
func rewriteFixture(t testing.TB, name, stdout, root string) {
	t.Helper()
	text, err := portableJSON(stdout, root)
	if err != nil {
		t.Fatal(err)
	}
	ticketkit.WriteFixture(t, fixturePath(name), text)
}

// checkApproveFixture は承認の JSON（body）が拡張のフィクスチャ name と、キーだけでなく値まで同じことを見る
// （キーの集合だけを比べると、識別子や本文が入れ替わっても気づけない）。時刻の欄（generated_at）は外す。
func checkApproveFixture(t testing.TB, name, stdout, root string) {
	t.Helper()
	if *updateBoardFixtures {
		rewriteFixture(t, name, stdout, root)
	}
	var body map[string]any
	if err := json.Unmarshal([]byte(stdout), &body); err != nil {
		t.Fatal(err)
	}
	got := maskDigests(t, portable(body, root)).(map[string]any)
	want := readFixture(t, name)
	if !slices.Equal(sortedKeys(want), sortedKeys(got)) {
		t.Fatalf("%s のキーが違う: フィクスチャ %v, 出力 %v", name, sortedKeys(want), sortedKeys(got))
	}
	for _, k := range sortedKeys(got) {
		if k == "generated_at" {
			continue
		}
		if !reflect.DeepEqual(want[k], got[k]) {
			t.Errorf("%s の %q が違う:\nフィクスチャ: %v\n出力: %v", name, k, want[k], got[k])
		}
	}
}

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}

// 見本の書き直しは、本文のエスケープ（改行・引用符・バックスラッシュ）を壊さず、パス・時刻・ダイジェストだけを置き換える。
func TestPortableJSONKeepsEscapes(t *testing.T) {
	t.Parallel()
	digest := strings.Repeat("ab", 32)
	in := `{"generated_at": "2026-01-01T00:00:00", "body": "a\nb \"q\" c\\d /r/x", "list": ["/r/y\\z", "` + digest + `"], "digest": "` + digest + `"}`
	got, err := portableJSON(in, "/r")
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal([]byte(got), &m); err != nil {
		t.Fatalf("JSON として読めない: %v\n%s", err, got)
	}
	if m["generated_at"] != "<time>" {
		t.Errorf("generated_at = %v", m["generated_at"])
	}
	if want := "a\nb \"q\" c/d <root>/x"; m["body"] != want {
		t.Errorf("body = %q, want %q", m["body"], want)
	}
	if want := []any{"<root>/y/z", digest}; !reflect.DeepEqual(m["list"], want) {
		t.Errorf("list = %v, want %v", m["list"], want)
	}
	if m["digest"] != "<digest>" {
		t.Errorf("digest = %v", m["digest"])
	}
}
