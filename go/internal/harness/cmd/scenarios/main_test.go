package main

import (
	"os"
	"testing"
)

// 入口は、go/ の中で打っても、パスをリポジトリの根から測る（os.Chdir は run が行う）。
func TestPathsAreMeasuredFromTheRepoRoot(t *testing.T) {
	devnull, err := os.OpenFile(os.DevNull, os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer devnull.Close()
	stdout := os.Stdout
	os.Stdout = devnull
	defer func() { os.Stdout = stdout }()
	if rc := run([]string{"preflight", "tests/scenarios/UC-01-direct-edit-denied.yml"}); rc != 3 {
		t.Fatalf("preflight = %d, want 3（確認文を出して止まる）", rc)
	}
}

func TestParseAllowsOptionsBeforeAndAfterPositionals(t *testing.T) {
	t.Parallel()
	a, err := parse([]string{"--confirmed", "x.yml", "--out", "o"}, []string{"confirmed"}, []string{"out"})
	if err != nil || len(a.pos) != 1 || a.pos[0] != "x.yml" || a.flags["confirmed"] == "" || a.flags["out"] != "o" {
		t.Fatalf("parse = %+v, %v", a, err)
	}
}
