package main

import (
	"bytes"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/harness/contractshape"
	"ccnavi/internal/harness/fakeclaude"
)

// 本物に繋がない。claude・GitHub・ネットワークは呼ばず、引数・環境・記録の組み立て・漏れの検査だけを見る。

func TestParseArgsNeedsTheBudget(t *testing.T) {
	t.Parallel()
	root := filepath.Join("r", "root")
	var stderr bytes.Buffer
	if _, err := parseArgs(nil, root, &stderr); err == nil || !strings.Contains(err.Error(), "--max-budget-usd") {
		t.Fatalf("予算が無いのに通った: %v", err)
	}
	opt, err := parseArgs([]string{"--max-budget-usd", "2", "--dry-run"}, root, &stderr)
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(root, "scratchpad", "claude-record")
	if opt.budget != 2 || !opt.dryRun || opt.rawDir != want {
		t.Errorf("options = %+v, rawDir の既定は %s", opt, want)
	}
}

// 子の環境から、走らせた人の CCNAVI_* と CLAUDE_PROJECT_DIR を外し、ほかは残す。
func TestChildEnvDropsCcnaviAndProjectDir(t *testing.T) {
	t.Parallel()
	in := []string{"CLAUDE_PROJECT_DIR=/x", "CCNAVI_ENTRY_PATH=/y", "CCNAVI_MODE=enable", "HOME=/h", "PATH=p"}
	if got := childEnv(in); !reflect.DeepEqual(got, []string{"HOME=/h", "PATH=p"}) {
		t.Errorf("childEnv = %q", got)
	}
}

func TestVersionIsTheFirstDottedNumber(t *testing.T) {
	t.Parallel()
	cases := map[string]string{
		"2.1.0 (Claude Code)\n": "2.1.0",
		"Claude Code 10.0.12":   "10.0.12",
		"no version here":       "unknown",
		"":                      "unknown",
	}
	for in, want := range cases {
		if got := parseVersion(in); got != want {
			t.Errorf("parseVersion(%q) = %q, want %q", in, got, want)
		}
	}
}

// 費用は result の行だけを足す。読めない行と、result でない行は数えない。
func TestCostIsTheSumOfResultLines(t *testing.T) {
	t.Parallel()
	streams := []string{
		"{\"type\":\"system\",\"total_cost_usd\":9}\n{\"type\":\"result\",\"total_cost_usd\":0.25}\n",
		"{\"type\":\"result\",\"total_cost_usd\":0.5}\nnot json\n{\"type\":\"result\"}\n",
	}
	if got := costOf(streams); got != 0.75 {
		t.Errorf("costOf = %v, want 0.75", got)
	}
}

// 記録の形（hooks・stream）は全部の禁止を見る。形はキーの名と型だけを持つので、禁止する文字列はキーに置く。本文は REC の目印だけを許す。
func TestLeaksScansShapesAndText(t *testing.T) {
	t.Parallel()
	clean := fakeclaude.Doc{
		Hooks:  map[string]*contractshape.Node{"PreToolUse:Read": contractshape.Normalize(map[string]any{"cwd": "<dir>"})},
		Stream: map[string]*contractshape.Node{},
	}
	machine := fakeclaude.Doc{
		Hooks: map[string]*contractshape.Node{"PreToolUse:Read": contractshape.Normalize(map[string]any{"/home/user/x": "str"})},
	}
	marker := fakeclaude.Doc{
		Hooks: map[string]*contractshape.Node{"PreToolUse:Read": contractshape.Normalize(map[string]any{"REC-DENY": "str"})},
	}
	cases := []struct {
		name string
		doc  fakeclaude.Doc
		text string
		want []string
	}{
		{"clean", clean, "{}", nil},
		{"machine path in shape", machine, "{}", []string{"機械のパス"}},
		{"REC marker in text only is allowed", clean, "REC-DENY in the observations", nil},
		{"REC marker in shape is refused", marker, "{}", []string{"REC の目印"}},
		{"mail address in text", clean, "a@example.com", []string{"メールの形"}},
		{"model name in text", clean, "claude-haiku-5", []string{"モデルの名前"}},
		{"call id in text", clean, "toolu_01", []string{"呼び出しの ID"}},
	}
	for _, c := range cases {
		if got := leaks(c.doc, c.text); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: leaks = %v, want %v", c.name, got, c.want)
		}
	}
}

// recorded.json の先頭は source と claude_version（Python の dict の順と同じ）。字下げは 1。末尾は改行。
func TestEncodeKeepsTheDocumentLayout(t *testing.T) {
	t.Parallel()
	text, err := encode(document{Source: "claude", ClaudeVersion: "2.1.0", Doc: fakeclaude.Doc{
		Hooks:        map[string]*contractshape.Node{},
		Stream:       map[string]*contractshape.Node{},
		Observations: map[string]any{},
	}})
	if err != nil {
		t.Fatal(err)
	}
	head := "{\n \"source\": \"claude\",\n \"claude_version\": \"2.1.0\",\n"
	if !strings.HasPrefix(text, head) || !strings.HasSuffix(text, "}\n") {
		t.Errorf("layout = %q", text)
	}
}

// 場面の木は、hook の包み（teststub の claude-hook を起こす sh）と settings.json と台本の手の材料を持つ。
func TestSceneTreeHasTheHookAndSettings(t *testing.T) {
	t.Parallel()
	base := t.TempDir()
	tree, err := fakeclaude.BuildTree(base, "/opt/teststub")
	if err != nil {
		t.Fatal(err)
	}
	wrapper, err := os.ReadFile(filepath.Join(tree, filepath.FromSlash(fakeclaude.HookPath)))
	if err != nil || !strings.Contains(string(wrapper), "claude-hook") {
		t.Errorf("hook の包み = %q (%v)", wrapper, err)
	}
	settings, err := os.ReadFile(filepath.Join(tree, ".claude", "settings.json"))
	if err != nil || !strings.Contains(string(settings), fakeclaude.HookPathEnv) {
		t.Errorf("settings.json = %q (%v)", settings, err)
	}
	if _, err := os.Stat(filepath.Join(tree, ".git")); err != nil {
		t.Errorf("git の木になっていない: %v", err)
	}
}
