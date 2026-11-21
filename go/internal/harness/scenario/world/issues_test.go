package world

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/scenario/core"
	modelhost "ccnavi/internal/tickets/model/host"
)

func TestIssueCommandsPerTracker(t *testing.T) {
	t.Parallel()
	issue := core.IssueSpec{Title: "タイトル", Body: "本文", Repo: "projects/test-repo"}
	cases := []struct {
		kind modelhost.Kind
		spec core.IssueSpec
		want string
		code string
	}{
		{modelhost.GitLab, issue, "glab issue create --title タイトル --description 本文 --yes", "glab issue close 12"},
		{modelhost.GitHub, issue, "gh issue create --title タイトル --body 本文", "gh issue close 12"},
		{modelhost.GitLab, core.IssueSpec{Title: "t"}, "glab issue create --title t --description  --yes", "glab issue close 12"},
	}
	for _, tc := range cases {
		got, err := IssueCreateArgs(tc.kind, tc.spec)
		if err != nil || strings.Join(got, " ") != tc.want {
			t.Errorf("create %s = %v %v", tc.kind, got, err)
		}
		got, err = IssueCloseArgs(tc.kind, 12)
		if err != nil || strings.Join(got, " ") != tc.code {
			t.Errorf("close %s = %v %v", tc.kind, got, err)
		}
	}
	if _, err := IssueCreateArgs("", issue); err == nil {
		t.Error("ホストが分からなければ作れない")
	}
	if _, err := IssueCloseArgs("", 1); err == nil {
		t.Error("ホストが分からなければ閉じられない")
	}
}

func TestIssueNumberIsTakenFromTheURLLine(t *testing.T) {
	t.Parallel()
	for out, want := range map[string]int{
		"https://github.com/o/r/issues/7\n":                                                 7,
		"https://gitlab.example.com/g/p/-/issues/12\n":                                      12,
		"Creating issue in g/p\n\nhttps://gitlab.example.com/g/p/-/issues/34\nopen later\n": 34,
		"https://gl.example.com/g/p/-/issues/12\nsee https://docs.example.com/9\n":          12,
	} {
		if got, err := IssueNumber(out); err != nil || got != want {
			t.Errorf("%q = %d %v", out, got, err)
		}
	}
	for _, bad := range []string{"created 5\n", "作成できませんでした"} {
		if _, err := IssueNumber(bad); err == nil {
			t.Errorf("%q は番号が取れない", bad)
		}
	}
}

func TestFillIssueReplacesThePlaceholderOnlyWhenThereIsANumber(t *testing.T) {
	t.Parallel()
	if got := FillIssue("issue #{issue} で `--issue {issue}` を打つ", 34); got != "issue #34 で `--issue 34` を打つ" {
		t.Errorf("= %q", got)
	}
	if got := FillIssue("issue #{issue}", 0); got != "issue #{issue}" {
		t.Errorf("= %q", got)
	}
}

func TestModeCheckStopsUnlessEveryDeclarationIsEnable(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name     string
		settings string
		environ  string
		want     bool
	}{
		{"enable", `{"env": {"CCNAVI_MODE": "enable"}}`, "", true},
		{"dry-run", `{"env": {"CCNAVI_MODE": "dry-run"}}`, "", false},
		{"環境変数の dry-run は設定の enable より強い", `{"env": {"CCNAVI_MODE": "enable"}}`, "dry-run", false},
		{"値が無ければ enable", `{"env": {}}`, "", true},
		{"設定を読めない", `{`, "", false},
	}
	for _, tc := range cases {
		root := base.TempDir(t)
		write(t, filepath.Join(root, ".claude", "settings.json"), tc.settings)
		got, text := ModeCheck(root, func(string) string { return tc.environ })
		if got != tc.want || text == "" {
			t.Errorf("%s = %v（%s）", tc.name, got, text)
		}
	}
}

func TestPreflightFactsReportAMissingDirectoryInsteadOfFailing(t *testing.T) {
	t.Parallel()
	if got := PreflightFacts(base.TempDir(t)); !strings.Contains(got, "置き場が無い") {
		t.Errorf("= %s", got)
	}
}

func TestSandboxHasTheFilesAHookNeeds(t *testing.T) {
	t.Parallel()
	fake := filepath.Join(base.TempDir(t), "ccnavi")
	write(t, fake, "#!/bin/sh\n")
	dir := base.TempDir(t)
	ws, err := BuildSandbox(base.Root(), dir, fake)
	if err != nil {
		t.Fatal(err)
	}
	for _, rel := range []string{".claude/settings.json", ".ccnavi/scripts/ccnavi-git.sh", "projects/test-repo/README.md"} {
		if !exists(filepath.Join(ws, rel)) {
			t.Errorf("%s が無い", rel)
		}
	}
	if !strings.Contains(read(t, filepath.Join(ws, ".claude", "settings.json")), `"CCNAVI_MODE": "enable"`) {
		t.Error("CCNAVI_MODE を enable にしていない")
	}
	if !strings.Contains(read(t, filepath.Join(ws, "projects", "test-repo", "README.md")), "演習") || RepoSnapshot(ws) == "" {
		t.Error("test-repo の README が違う")
	}
	if _, err := os.Stat(filepath.Join(ws, ".git")); err != nil {
		t.Error("git のリポジトリになっていない")
	}
}
