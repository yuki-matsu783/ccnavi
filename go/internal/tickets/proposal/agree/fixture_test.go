package agree

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/model/pyoracle"
)

// テストの道具。git のワークスペースと親のワークツリー、提案の見本を作る（tickets/phase の fixture_test.go と同じ形）。

const commitDate = "2026-01-05T00:00:00+0000"

func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_CONFIG_GLOBAL="+os.DevNull, "GIT_CONFIG_NOSYSTEM=1",
		"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com",
		"GIT_AUTHOR_DATE="+commitDate, "GIT_COMMITTER_DATE="+commitDate)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return string(out)
}

func workspace(t *testing.T, worktrees ...string) string {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	runGit(t, root, "init", "-q", "-b", "main")
	runGit(t, root, "commit", "-q", "--allow-empty", "-m", "init")
	for _, name := range worktrees {
		runGit(t, root, "worktree", "add", "-q", "-b", name, filepath.Join(".claude", "worktrees", name), "main")
	}
	from := filepath.Join(pyoracle.RepoRoot(t), ".ccnavi", "messages")
	entries, err := os.ReadDir(from)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		data, err := os.ReadFile(filepath.Join(from, e.Name()))
		if err != nil {
			t.Fatal(err)
		}
		put(t, filepath.Join(root, ".ccnavi", "messages", e.Name()), string(data))
	}
	put(t, filepath.Join(root, ".ccnavi", "config", "common", "phases.yml"), "version: 1\n"+phasesCopy+extraType)
	return root
}

func put(t *testing.T, path, text string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func settingsOf(t *testing.T, root string) *config.Settings {
	t.Helper()
	conf, _ := config.Load(root)
	return conf
}

func worktree(root, name string) string { return filepath.Join(root, ".claude", "worktrees", name) }

func todoDir(root, parent string) string {
	return filepath.Join(worktree(root, parent), "wip", "proposals", "todo")
}

func approvedDir(root, parent string) string {
	return filepath.Join(worktree(root, parent), ".ccnavi", "approved")
}

const phasesCopy = `phases:
  research:
    kind: work
    title: 調査
    review: false
    scope:
    - wip/research/*
  design:
    kind: work
    title: 設計
    review: true
    when: 設計の案内
    scope:
    - wip/design/*
  implement:
    kind: work
    title: 実装
    review: true
    when: 実装の案内
    scope:
    - src/*
    - wip/design/*
`

// extraType は phases.yml にだけある定義（画面の「この親では使わない定義」）。
const extraType = `  release:
    kind: work
    title: リリース
    review: true
    scope: inherit
`

// parentText は親の見本。plan は計画の行で、空なら計画も定義のコピーも書かない。extra は frontmatter に足す行。
func parentText(id string, plan []string, extra ...string) string {
	head := "---\nversion: 1\nticket: " + id + "\nissue: 1\nmr: 1\n"
	if len(plan) > 0 {
		head += phasesCopy + "plan:\n" + strings.Join(plan, "\n") + "\n"
	}
	for _, e := range extra {
		head += e + "\n"
	}
	return head + "human_review:\n  required: true\n  reason: t\ntitle: 親 " + id + "\nrationale: |\n  理由の 1 行目\n  2 行目\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n  - match: Write|Edit\n    glob: \"wip/*\"\n" +
		"---\n\n## 目的\n\nx\n\n## 範囲\n\nx\n\n## 対象外\n\nx\n\n## 完了の確かめ方\n\nx\n\n## 計画の理由\n\nx\n"
}

// childText は子の見本。extra は frontmatter に足す行。
func childText(id, parent, phase string, globs []string, extra ...string) string {
	text := "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n" +
		"human_review:\n  required: false\n  reason: t\ntitle: 子 " + id + "\nrationale: r\nallow:\n"
	for _, g := range globs {
		text += "  - match: Write|Edit\n    glob: \"" + g + "\"\n"
	}
	for _, e := range extra {
		text += e + "\n"
	}
	return text + "---\n\n本文\n"
}
