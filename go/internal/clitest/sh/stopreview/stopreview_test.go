// Package stopreview は scripts/ccnavi-stop-review.sh（Stop の AI 判定の agent hook の登録・解除・点検）を外から確かめる。
package stopreview

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

const (
	marker   = "[ccnavi-stop-review]"
	envFlag  = "CCNAVI_STOP_REVIEW_AGENT"
	launcher = "sh .ccnavi/scripts/ccnavi-launcher.sh"
)

type object = map[string]any

// space は使い捨ての作業場所。settings は .claude/settings.json の絶対パス。
type space struct {
	t        *testing.T
	root     string
	settings string
}

func newSpace(t *testing.T) *space {
	t.Helper()
	if _, err := exec.LookPath("jq"); err != nil {
		t.Skip("jq が要る")
	}
	t.Parallel()
	root := base.TempDir(t)
	return &space{t: t, root: root, settings: filepath.Join(root, ".claude", "settings.json")}
}

// sh は `sh scripts/ccnavi-stop-review.sh <args…> --root <root>` を起こす。
func (s *space) sh(args ...string) run.Result {
	s.t.Helper()
	return run.Sh(s.t, filepath.Join(base.Root(), "scripts", "ccnavi-stop-review.sh"), append(append([]string{}, args...), "--root", s.root))
}

func (s *space) writeText(text string) {
	s.t.Helper()
	if err := os.MkdirAll(filepath.Dir(s.settings), 0o755); err != nil {
		s.t.Fatal(err)
	}
	if err := os.WriteFile(s.settings, []byte(text), 0o644); err != nil {
		s.t.Fatal(err)
	}
}

func (s *space) write(data object) {
	s.t.Helper()
	raw, err := json.Marshal(data)
	if err != nil {
		s.t.Fatal(err)
	}
	s.writeText(string(raw))
}

func (s *space) text() string {
	s.t.Helper()
	raw, err := os.ReadFile(s.settings)
	if err != nil {
		s.t.Fatal(err)
	}
	return string(raw)
}

func (s *space) read() object {
	s.t.Helper()
	var data object
	if err := json.Unmarshal([]byte(s.text()), &data); err != nil {
		s.t.Fatalf("設定が JSON でない: %v", err)
	}
	return data
}

func (s *space) exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

// ok は終了コード 0 で終わった結果を返す。
func (s *space) ok(args ...string) run.Result {
	s.t.Helper()
	r := s.sh(args...)
	if r.Code != 0 {
		s.t.Fatalf("%v: 終了コード %d\nstdout: %s\nstderr: %s", args, r.Code, r.Stdout, r.Stderr)
	}
	return r
}

func asList(v any) []any {
	l, _ := v.([]any)
	return l
}

func asObject(v any) object {
	o, _ := v.(object)
	return o
}

// agents は Stop の agent 型の hook（目印の有無を問わない）。
func agents(data object) []object {
	var out []object
	for _, group := range asList(asObject(data["hooks"])["Stop"]) {
		for _, h := range asList(asObject(group)["hooks"]) {
			if hook := asObject(h); hook["type"] == "agent" {
				out = append(out, hook)
			}
		}
	}
	return out
}

func only(t *testing.T, hooks []object) object {
	t.Helper()
	if len(hooks) != 1 {
		t.Fatalf("agent 型の hook は 1 件のはず: %v", hooks)
	}
	return hooks[0]
}

func TestDefaultIsNotRegistered(t *testing.T) {
	s := newSpace(t)
	r := s.ok("check")
	ticketkit.Has(t, r.Stdout, "登録されていません")
	if s.exists(s.settings) {
		t.Error("check が設定ファイルを作った")
	}
}

// 登録は他の定義（env・許可・ほかの Stop の hook）を保ち、既にある env の値と許可の順を触らない。
func TestRegisterKeepsOtherDefinitionsAndAddsOurs(t *testing.T) {
	s := newSpace(t)
	s.write(object{
		"env":         object{"KEEP": "1", envFlag: "0"},
		"permissions": object{"allow": []any{"Bash(ls)"}},
		"hooks":       object{"Stop": []any{object{"matcher": "", "hooks": []any{object{"type": "command", "command": "x"}}}}},
	})
	s.ok("register", "--model", "haiku")
	data := s.read()
	env := asObject(data["env"])
	if env["KEEP"] != "1" || env[envFlag] != "0" {
		t.Errorf("env を触った: %v", env)
	}
	allow := asList(asObject(data["permissions"])["allow"])
	want := []any{"Bash(ls)", "Bash(" + launcher + " stop-review material:*)", "Bash(" + launcher + " stop-review record:*)"}
	if !reflect.DeepEqual(allow, want) {
		t.Errorf("許可 = %v, want %v", allow, want)
	}
	var commands []any
	for _, g := range asList(asObject(data["hooks"])["Stop"]) {
		for _, h := range asList(asObject(g)["hooks"]) {
			if asObject(h)["type"] == "command" {
				commands = append(commands, h)
			}
		}
	}
	if !reflect.DeepEqual(commands, []any{object{"type": "command", "command": "x"}}) {
		t.Errorf("ほかの hook が変わった: %v", commands)
	}
	agent := only(t, agents(data))
	prompt, _ := agent["prompt"].(string)
	if !strings.HasPrefix(prompt, marker) {
		t.Errorf("prompt の先頭が目印でない: %q", prompt)
	}
	for _, w := range []string{"$ARGUMENTS", "stop-review material", "stop-review record"} {
		ticketkit.Has(t, prompt, w)
	}
	if agent["model"] != "haiku" {
		t.Errorf("model = %v", agent["model"])
	}
	if !s.exists(s.settings + ".bak") {
		t.Error("書き換え前の控え（.bak）が無い")
	}
}

func TestRegisterDefaultsToHaikuWithA60SecondTimeoutAndModelReplacesWithoutDuplicating(t *testing.T) {
	s := newSpace(t)
	s.ok("register")
	agent := only(t, agents(s.read()))
	if agent["model"] != "haiku" || agent["timeout"] != float64(60) {
		t.Errorf("既定 = %v, %v", agent["model"], agent["timeout"])
	}
	s.ok("register", "--model", "sonnet")
	if agent := only(t, agents(s.read())); agent["model"] != "sonnet" {
		t.Errorf("model を上書きできない: %v", agent["model"])
	}
}

func TestRegisterSetsTheMarkerEnvAndIsIdempotent(t *testing.T) {
	s := newSpace(t)
	s.ok("register")
	before := s.read()
	if asObject(before["env"])[envFlag] != "1" {
		t.Errorf("env が立っていない: %v", before["env"])
	}
	ticketkit.Has(t, s.ok("register").Stdout, "すでに登録")
	if !reflect.DeepEqual(s.read(), before) {
		t.Error("2 回目の register が設定を変えた")
	}
	s.ok("check")
}

// 解除は目印のある hook と env だけを外す。ユーザ自身の agent hook は、prompt に目印が途中に入っていても残す。
func TestUnregisterRemovesOnlyOurs(t *testing.T) {
	s := newSpace(t)
	mine := object{"type": "agent", "prompt": "x " + marker + " not at the start"}
	s.write(object{"hooks": object{"Stop": []any{
		object{"hooks": []any{object{"type": "agent", "prompt": "他人の判定"}}},
		object{"hooks": []any{mine}},
	}}})
	s.ok("register")
	if n := len(agents(s.read())); n != 3 {
		t.Fatalf("登録後の agent 型は 3 件のはず: %d", n)
	}
	s.ok("unregister")
	data := s.read()
	got := agents(data)
	if len(got) != 2 || got[0]["prompt"] != "他人の判定" || !reflect.DeepEqual(got[1], mine) {
		t.Errorf("ユーザの hook が変わった: %v", got)
	}
	if _, ok := asObject(data["env"])[envFlag]; ok {
		t.Error("env が残った")
	}
	ticketkit.Has(t, s.ok("unregister").Stdout, "外すものはありません")
}

func TestRegisterThenUnregisterRestoresTheUserSettings(t *testing.T) {
	s := newSpace(t)
	for _, c := range []struct {
		name     string
		original object
	}{
		{"ユーザの設定", object{
			"model": "x",
			"env":   object{"KEEP": "1"},
			"hooks": object{"Stop": []any{object{"hooks": []any{}}, object{"hooks": []any{object{"type": "command", "command": "c"}}}}},
		}},
		{"空の設定", object{}},
	} {
		name, original := c.name, c.original
		s.write(original)
		s.ok("register")
		s.ok("unregister")
		if got := s.read(); !reflect.DeepEqual(got, original) {
			t.Errorf("%s: 元に戻らない: %v", name, got)
		}
	}
	// 外した後に、空の Stop を残さない。
	s.write(object{})
	s.ok("register")
	s.ok("unregister")
	if _, ok := asObject(s.read()["hooks"])["Stop"]; ok {
		t.Error("空の Stop が残った")
	}
}

func TestCheckReportsMismatchesAndRegisterFixesThem(t *testing.T) {
	s := newSpace(t)
	s.ok("register")
	data := s.read()
	delete(asObject(data["env"]), envFlag)
	s.write(data)
	r := s.sh("check")
	if r.Code != 1 {
		t.Fatalf("env が無いとき check = %d, want 1", r.Code)
	}
	ticketkit.Has(t, r.Stdout, envFlag+"=1 がありません")

	asObject(data["env"])[envFlag] = "1"
	stop := asList(asObject(data["hooks"])["Stop"])
	asObject(data["hooks"])["Stop"] = append(stop, stop[0])
	s.write(data)
	r = s.sh("check")
	if r.Code != 1 {
		t.Fatalf("重複のとき check = %d, want 1", r.Code)
	}
	ticketkit.Has(t, r.Stdout, "重複")

	s.ok("register") // 重複を 1 件に直す
	only(t, agents(s.read()))
	s.ok("check")
}

// 想定と違う形の設定は、check・register・unregister のどれでも終了コード 2 で、書き換えない。
func TestMalformedSettingsExitWith2AndAreNotRewritten(t *testing.T) {
	for _, text := range []string{
		`[1, 2]`,
		`{"hooks": "x"}`,
		`{"hooks": {"Stop": {"hooks": []}}}`,
		`{"hooks": {"Stop": ["str"]}}`,
		`{"hooks": {"Stop": [{"hooks": "abc"}]}}`,
		`{"hooks": {"Stop": [{"hooks": ["x"]}]}}`,
		`{"env": "x"}`,
	} {
		for _, action := range []string{"check", "register", "unregister"} {
			t.Run(action+" "+text, func(t *testing.T) {
				s := newSpace(t)
				s.writeText(text)
				if r := s.sh(action); r.Code != 2 {
					t.Errorf("終了コード %d, want 2\n%s%s", r.Code, r.Stdout, r.Stderr)
				}
				if got := s.text(); got != text {
					t.Errorf("設定を書き換えた: %q", got)
				}
			})
		}
	}
}

func TestNothingToRemoveDoesNotRewriteTheFile(t *testing.T) {
	s := newSpace(t)
	text := `{"model":"x","permissions":{"allow":["Bash(ls)"]}}`
	s.writeText(text)
	ticketkit.Has(t, s.ok("unregister").Stdout, "外すものはありません")
	if got := s.text(); got != text {
		t.Errorf("設定を書き換えた: %q", got)
	}
	if s.exists(s.settings + ".bak") {
		t.Error("書き換えていないのに .bak ができた")
	}
}

func TestBadArgumentsExitWith2(t *testing.T) {
	s := newSpace(t)
	script := filepath.Join(base.Root(), "scripts", "ccnavi-stop-review.sh")
	for name, args := range map[string][]string{"引数なし": nil, "知らない操作": {"nope"}} {
		if r := run.Sh(t, script, args); r.Code != 2 {
			t.Errorf("%s: 終了コード %d, want 2", name, r.Code)
		}
	}
	if r := s.sh("check", "--bogus"); r.Code != 2 {
		t.Errorf("知らないオプション: 終了コード %d, want 2", r.Code)
	}
}
