package layers

import (
	"encoding/json"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/configws"
)

// 共通設定のミラーを調べ、ずれを同期する `ccnavi user mirror` の入口。
//
// 着手済みの親のワークツリーのミラーは発言のたびに揃うが、ユーザのチェックアウトは対象外なので、間が空くとミラーが古いまま残る。
// `user mirror` は、プロジェクトのユーザのチェックアウトのミラーを、見たいときに調べ（書かない）、押したときに揃える
// （ワークツリーに書き、コミットはしない）。
//
// 調べ方と揃え方の細部（ずれの判定・名前の絞り込み・expect の照合・リンクの除外・工程スキルの同期）は
// Go のテスト（go/internal/tickets/sync/mirror）が確かめる。ここでは入口を外から動かす。
//   - JSON の形、書かずに調べること
//   - 文の出力（実行する command の案内・上書きの警告）
//   - ボードの経路は expect が合うときだけ書く
//   - 組み合わせの誤りと、内部の旗を通さないこと

func mirrorOf(project, name string) string {
	return filepath.Join(project, ".ccnavi", "config", "common", filepath.FromSlash(name))
}

type mirrorWS struct{ *configws.WS }

func (w mirrorWS) userMirror(args ...string) (code int, stdout, stderr string) {
	r := w.Ccnavi(append([]string{"user", "mirror"}, args...)...)
	return r.Code, r.Stdout, r.Stderr
}

// view は `user mirror --json` の答え（書かずに調べる）。
func (w mirrorWS) view(names ...string) map[string]any {
	w.T.Helper()
	code, out, errText := w.userMirror(append(names, "--json")...)
	if code != 0 {
		w.T.Fatalf("code = %d\n%s%s", code, out, errText)
	}
	return decodeObject(w.T, out)
}

func decodeObject(t testing.TB, text string) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal([]byte(text), &out); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, text)
	}
	return out
}

// syncAll は全プロジェクトのミラーを、共通設定に揃える（ボードの経路）。
func (w mirrorWS) syncAll() {
	w.T.Helper()
	expect := w.view()["expect"].(string)
	code, out, errText := w.userMirror("--yes", "--expect", expect, "--via", "board", "--json")
	if code != 0 || decodeObject(w.T, out)["ok"] != true {
		w.T.Fatalf("code = %d\n%s%s", code, out, errText)
	}
}

func TestTheMirrorViewPrintsJSONWithTheAgreedShape(t *testing.T) {
	t.Parallel()
	w := mirrorWS{configws.NewWS(t, configws.KindBase)}
	answer := w.view()
	if answer["version"] != float64(1) || answer["ok"] != true || answer["error"] != "" || answer["common_empty"] != false {
		t.Errorf("答えの頭が違う: %v", answer)
	}
	var names []string
	var lib map[string]any
	for _, p := range answer["projects"].([]any) {
		project := p.(map[string]any)
		names = append(names, project["name"].(string))
		if project["name"] == "lib" {
			lib = project
		}
	}
	sort.Strings(names)
	if strings.Join(names, ",") != "app,lib" {
		t.Errorf("プロジェクト = %v", names)
	}
	if lib["state"] != "differs" || lib["expect"] == "" {
		t.Errorf("lib = %v", lib)
	}
	found := false
	for _, c := range lib["changes"].([]any) {
		if ch := c.(map[string]any); ch["rel"] == ".ccnavi/config/common/rules.yml" && ch["action"] == "create" {
			found = true
		}
	}
	if !found {
		t.Errorf("rules.yml の追加が changes に無い: %v", lib["changes"])
	}
	if answer["expect"] != w.view()["expect"] {
		t.Error("同じ状態で expect が変わる")
	}
	// 見るだけで、何も書かない。
	if configws.Exists(mirrorOf(w.Lib, "rules.yml")) {
		t.Error("調べるだけなのにミラーを書いた")
	}
}

func TestTheMirrorViewInTextNamesTheCommandToRun(t *testing.T) {
	t.Parallel()
	w := mirrorWS{configws.NewWS(t, configws.KindBase)}
	code, out, errText := w.userMirror()
	if code != 0 {
		t.Fatalf("code = %d\n%s%s", code, out, errText)
	}
	for _, want := range []string{"ずれ", "ccnavi-user-mirror.sh --yes --expect " + w.view()["expect"].(string)} {
		if !strings.Contains(out, want) {
			t.Errorf("出力に %q が無い:\n%s", want, out)
		}
	}
	// 名前は引用し、差分の見方を添える。
	w.Project("odd name", nil, "", "")
	code, odd, errText := w.userMirror("odd name")
	if code != 0 {
		t.Fatalf("code = %d\n%s%s", code, odd, errText)
	}
	for _, want := range []string{"ccnavi-user-mirror.sh 'odd name' --yes --expect", "diff -ru"} {
		if !strings.Contains(odd, want) {
			t.Errorf("出力に %q が無い:\n%s", want, odd)
		}
	}
}

func TestTheTextViewWarnsWhenAnUpdateOverwritesTheMirror(t *testing.T) {
	t.Parallel()
	w := mirrorWS{configws.NewWS(t, configws.KindBase)}
	w.syncAll()
	configws.Write(t, mirrorOf(w.Lib, "risks.yml"), "version: 1\nlevels: {}\nfactors: []\n")
	if _, out, _ := w.userMirror(); !strings.Contains(out, "上書き・削除します") {
		t.Errorf("上書きの警告が無い:\n%s", out)
	}
	if _, fresh, _ := w.userMirror("app"); strings.Contains(fresh, "上書き・削除します") {
		t.Errorf("上書きしない app に警告が出た:\n%s", fresh)
	}
}

func TestTheBoardRouteSyncsOnlyWithAMatchingExpect(t *testing.T) {
	t.Parallel()
	w := mirrorWS{configws.NewWS(t, configws.KindBase)}
	code, out, _ := w.userMirror("--yes", "--expect", strings.Repeat("0", 64), "--via", "board", "--json")
	refused := decodeObject(t, out)
	if code == 0 || refused["ok"] != false || !strings.Contains(refused["error"].(string), "内容が更新されました") {
		t.Errorf("古い expect を通した: code = %d\n%s", code, out)
	}
	if configws.Exists(mirrorOf(w.Lib, "rules.yml")) {
		t.Error("expect が合わないのに書いた")
	}
	expect := w.view("lib")["expect"].(string)
	code, out, errText := w.userMirror("lib", "--yes", "--expect", expect, "--via", "board", "--json")
	if code != 0 {
		t.Fatalf("code = %d\n%s%s", code, out, errText)
	}
	answer := decodeObject(t, out)
	synced := answer["synced"].([]any)[0].(map[string]any)
	if answer["ok"] != true || synced["name"] != "lib" {
		t.Errorf("答え = %v", answer)
	}
	if files, _ := json.Marshal(synced["files"]); !strings.Contains(string(files), ".ccnavi/config/common/rules.yml") {
		t.Errorf("files = %s", files)
	}
	if !configws.Exists(mirrorOf(w.Lib, "rules.yml")) {
		t.Error("expect が合うのに書かない")
	}
}

func TestMirrorCombinationsThatMakeNoSenseAreRefused(t *testing.T) {
	t.Parallel()
	w := mirrorWS{configws.NewWS(t, configws.KindBase)}
	for _, args := range [][]string{
		{"--expect", "x"},
		{"--yes"},
		{"--via", "board", "--json"},
		{"--yes", "--via", "board", "--json"},
		{"--via", "terminal", "--yes", "--expect", "x", "--json"},
	} {
		code, out, _ := w.userMirror(args...)
		if code == 0 {
			t.Errorf("%v を通した:\n%s", args, out)
		}
		if configws.Exists(mirrorOf(w.Lib, "rules.yml")) {
			t.Errorf("%v でミラーを書いた", args)
		}
	}
	// 内部の旗は、ユーザの入口を通さずには受け付けない。
	if w.Ccnavi("--mirror", "--json").Code == 0 {
		t.Error("内部の旗 --mirror を通した")
	}
}
