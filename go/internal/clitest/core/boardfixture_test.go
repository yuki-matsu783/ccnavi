package core

import (
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/infra/pyjson"
)

// VS Code 拡張（ccnavi-board）のフィクスチャと、実行ファイルの JSON の形（キーの集合）が同じことを見る道具。
// 形を変えたら `go test ./internal/clitest/core -run <テスト> -update-board-fixtures` でフィクスチャを書き直す。
// tests/ticket の Python は環境変数 `CCNAVI_BOARD_FIXTURE=1` を使う。ticket 側を Go に移したら、このフラグに寄せる。

var updateBoardFixtures = flag.Bool("update-board-fixtures", false, "VS Code 拡張のフィクスチャを、実行ファイルの出力で書き直す")

func boardFixturesDir() string {
	return filepath.Join(base.Root(), "extensions", "vscode", "ccnavi-board", "test", "fixtures")
}

// checkBoardFixture は stdout（実行ファイルの JSON）が、フィクスチャ name と同じキーを持つことを見る。
// paths は、キーを比べる入れ子の位置（`expectations.0.rules.0` のように配列の添字を数字で書く）。空なら先頭の階層だけ。
// -update-board-fixtures なら先に、走らせた場所（root）を /ws に読み替えて書き直す。
func checkBoardFixture(t *testing.T, name, stdout, root string, paths ...string) {
	t.Helper()
	path := filepath.Join(boardFixturesDir(), name)
	if *updateBoardFixtures {
		decoded, err := pyjson.Loads(stdout)
		if err != nil {
			t.Fatal(err)
		}
		text, err := pyjson.Dumps(decoded, pyjson.Options{RawUnicode: true, Indent: 1})
		if err != nil {
			t.Fatal(err)
		}
		for _, form := range []string{strings.ReplaceAll(root, `\`, "/"), strings.ReplaceAll(strings.ReplaceAll(root, `\`, "/"), "/", `\\`)} {
			text = strings.ReplaceAll(text, form, "/ws")
		}
		if err := os.WriteFile(path, []byte(text+"\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture, body any
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(stdout), &body); err != nil {
		t.Fatal(err)
	}
	paths = append([]string{""}, paths...)
	for _, p := range paths {
		want, got := at(fixture, p), at(body, p)
		if !slices.Equal(keysOf(want), keysOf(got)) {
			t.Errorf("%s の %q のキーが違う: フィクスチャ %v, 出力 %v", name, p, keysOf(want), keysOf(got))
		}
	}
}

func keysOf(v any) []string {
	m, _ := v.(map[string]any)
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}

func at(v any, path string) any {
	if path == "" {
		return v
	}
	for _, part := range strings.Split(path, ".") {
		switch x := v.(type) {
		case map[string]any:
			v = x[part]
		case []any:
			i := 0
			for _, c := range part {
				i = i*10 + int(c-'0')
			}
			if i >= len(x) {
				return nil
			}
			v = x[i]
		default:
			return nil
		}
	}
	return v
}
