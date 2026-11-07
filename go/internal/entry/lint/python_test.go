package lint

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// Python の lint.report と lint.family_check を同じワークスペースに当て、出力（本文と --json）・終了コード・苦情が
// 1 字も違わないことを見る。ワークスペースは苦情が出る形をまとめて作る。

type reportRun struct {
	Mode    string `json:"mode"`
	Restore string `json:"restore"`
	Core    string `json:"core"`
	JSON    bool   `json:"json"`
	Flow    string `json:"flow"`
}

type reportAnswer struct {
	Compat int `json:"compat"`
	Runs   []struct {
		Code int    `json:"code"`
		Out  string `json:"out"`
	} `json:"runs"`
	Family [][][]string `json:"family"`
}

// quietEnv は利用者の環境の CCNAVI_* と CLAUDE_* を外す（答えを機械に依らせない）。
func quietEnv(t *testing.T) {
	t.Helper()
	for _, kv := range os.Environ() {
		name, _, _ := strings.Cut(kv, "=")
		if strings.HasPrefix(name, env.Prefix) || strings.HasPrefix(name, "CLAUDE_") {
			t.Setenv(name, "")
			os.Unsetenv(name)
		}
	}
}

// compare は Python と Go の答えを比べる。
func compare(t *testing.T, name, root string, runs []reportRun, family [][2]string) {
	t.Helper()
	if family == nil {
		family = [][2]string{}
	}
	want := golden.Load[reportAnswer](t, name)
	for i, r := range runs {
		conf, notes := config.Load(root)
		var out bytes.Buffer
		code := Report(&out, Options{Root: root, Conf: conf, Notes: notes, Mode: r.Mode, RestoreIfDeny: r.Restore,
			GuardCoreFiles: r.Core, JSON: r.JSON, Flow: r.Flow, Compat: want.Compat})
		got := testkit.Rel(root, out.String())
		if code != want.Runs[i].Code || got != want.Runs[i].Out {
			t.Errorf("run %d (%+v): Go の答えが Python と違う\nGo (%d):\n%s\nPython (%d):\n%s", i, r, code, got, want.Runs[i].Code, want.Runs[i].Out)
		}
	}
	for i, f := range family {
		conf, _ := config.Load(root)
		var got [][]string
		for _, p := range FamilyCheck(conf, root, f[0], f[1]) {
			got = append(got, []string{string(p.Severity), p.Rule, testkit.Rel(root, p.Detail)})
		}
		if !sameRows(got, want.Family[i]) {
			t.Errorf("family %v: Go の答えが Python と違う\nGo:\n%v\nPython:\n%v", f, got, want.Family[i])
		}
	}
}

func sameRows(a, b [][]string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if strings.Join(a[i], "\x00") != strings.Join(b[i], "\x00") {
			return false
		}
	}
	return true
}

var (
	textRun = reportRun{}
	jsonRun = reportRun{JSON: true}
)

func put(t *testing.T, root, rel, text string) string {
	t.Helper()
	return testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), text)
}

// 何も無いワークスペース。言うことがほとんど無いことと、出力の頭と数え。
func TestReportEmptyWorkspace(t *testing.T) {
	quietEnv(t)
	root := testkit.Workspace(t)
	compare(t, "empty", root, []reportRun{textRun, jsonRun, {Mode: "dry-run"}, {Mode: "bogus", JSON: true}}, nil)
}

// 切り替えの環境変数の書き誤り・切ってあること・モードとの掛け合わせ。
func TestReportSwitches(t *testing.T) {
	quietEnv(t)
	root := testkit.Workspace(t)
	for name, value := range map[string]string{
		env.Mode: "dry-run", env.GuardCoreFiles: "disable", env.RestoreIfDeny: "dry-run", env.GuardUnwatched: "disable",
		env.GuardTicketApproval: " Dry-Run ", env.TicketControl: "bogus", env.Learning: "maybe", env.StopReview: "enabled",
		env.StopReviewAgent: "1", env.BranchPrefixes: "feature,Main,9x",
	} {
		t.Setenv(name, value)
	}
	compare(t, "switches", root, []reportRun{textRun, jsonRun, {Restore: "nope", Core: "enable"}}, nil)
	t.Setenv(env.GuardTicketApproval, "disable")
	t.Setenv(env.GuardCoreFiles, "disable")
	t.Setenv(env.TicketControl, "disable")
	compare(t, "switches-off", root, []reportRun{textRun}, nil)
}

// Claude Code の設定ファイル・sh の互換・作業の決まりの md。
func TestReportProjectFiles(t *testing.T) {
	quietEnv(t)
	root := testkit.Workspace(t)
	put(t, root, ".claude/settings.json", `{"env": {"CCNAVI_MODE": "DISABLE", "CCNAVI_ENTRY_PATH": "bin/ccnavi",
 "CCNAVI_STOP_REVIEW_AGENT": "1"},
 "hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "${CCNAVI_ENTRY_PATH}"}]}],
  "Stop": [{"hooks": [{"type": "agent", "prompt": "[ccnavi-stop-review] 判定する。とても長い prompt の残りの部分はここで切れる"},
   {"type": "agent", "prompt": "[ccnavi-stop-review] 2 本目"}, {"type": "agent", "prompt": "user own"}]}]}}`)
	put(t, root, ".claude/settings.local.json", `{"env": {"CCNAVI_MODE": "enable", "CCNAVI_LOG_LEVEL": "debug", "CCNAVI_LEARNING": "x"}}`)
	put(t, root, "bin/ccnavi", "#!/bin/sh\n")
	put(t, root, ".ccnavi/scripts/ccnavi-common.sh", "CCNAVI_COMPAT=3\n")
	put(t, root, ".ccnavi/CCNAVI.md", "# 決まり\n<!-- ccnavi:if no-such -->\nx\n<!-- ccnavi:endif -->\n{{unknown}}\n")
	compare(t, "project-files", root, []reportRun{textRun, jsonRun}, nil)

	put(t, root, ".claude/settings.json", `{"env": {"CCNAVI_MODE": "sideways"}`)
	put(t, root, ".claude/settings.local.json", "\ufeff{}")
	put(t, root, ".ccnavi/scripts/ccnavi-common.sh", "# 版なし\n")
	if err := os.Remove(filepath.Join(root, ".ccnavi", "CCNAVI.md")); err != nil {
		t.Fatal(err)
	}
	compare(t, "project-files-broken", root, []reportRun{textRun}, nil)
}

// チケットの置き場・承認済みチケット・提案・ブランチ名の検査。
func TestReportTickets(t *testing.T) {
	quietEnv(t)
	root := testkit.ProposalWorkspace(t, "feature-1-alpha", "stray")
	plan := []string{"  - research", "  - {type: design, after: [1]}"}
	alpha := testkit.Worktree(root, "feature-1-alpha")
	put(t, alpha, ".ccnavi/approved/doing/feature-1-alpha.md", testkit.ProposalParent("feature-1-alpha", plan))
	put(t, alpha, ".ccnavi/approved/doing/feature-1-alpha-01-01.md",
		testkit.ProposalChild("feature-1-alpha-01-01", "feature-1-alpha", "1", []string{"wip/research/*"}))
	put(t, root, ".ccnavi/approved/doing/feature-9-gone.md", testkit.ProposalParent("feature-9-gone", plan))
	todo := filepath.Join(root, "wip", "proposals", "todo")
	put(t, todo, "feature-1-alpha.md", testkit.ProposalParent("feature-1-alpha", plan))
	put(t, todo, "Feature-1-Alpha.md", testkit.ProposalParent("Feature-1-Alpha", plan))
	put(t, todo, "release.md", testkit.ProposalParent("release", nil))
	put(t, todo, "fix-1-other.md", testkit.ProposalParent("fix-1-other", plan))
	put(t, todo, "chore-2-x-03.md", testkit.ProposalParent("chore-2-x-03", plan))
	put(t, todo, "feature-1-alpha-02-01.md",
		testkit.ProposalChild("feature-1-alpha-02-01", "feature-1-alpha", "2", []string{"wip/design/*"}))
	// 壊れた YAML は入れない（写しを取った PyYAML が libyaml の文を出したので、純 Python の読み手を写した infra/yaml の文と合わない）。
	put(t, todo, "broken.md", "---\nticket: 1\n---\n")
	stray := testkit.Worktree(root, "stray")
	put(t, stray, "wip/proposals/todo/feature-3-moved.md", testkit.ProposalParent("feature-3-moved", plan))
	put(t, root, ".claude/settings.json", `{"hooks": {"PostToolUse": [{"hooks": [{"command": "ccnavi"}]}]}}`)
	put(t, root, "logs/state/ops/0123456789abcdef0123456789abcdef.json", "{broken")
	compare(t, "tickets", root, []reportRun{textRun, jsonRun}, [][2]string{{"feature-1-alpha", ""}, {"feature-1-alpha", "self"}, {"nope", "app"}, {"feature-9-gone", ""}})
}

// プロジェクトの置き場・走査されない提案の置き場・予約名・プロジェクトの中のワークツリー。
func TestReportProjects(t *testing.T) {
	quietEnv(t)
	root := testkit.Workspace(t)
	for _, name := range []string{"Self", "app"} {
		dir := filepath.Join(root, "projects", name)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		testkit.Git(t, dir, "init", "-q", "-b", "main")
	}
	if err := os.MkdirAll(filepath.Join(root, "projects", "app", ".claude", "worktrees", "w1"), 0o755); err != nil {
		t.Fatal(err)
	}
	for _, rel := range []string{"wip/app/proposals/todo", "wip/ghost/proposals"} {
		if err := os.MkdirAll(filepath.Join(root, filepath.FromSlash(rel)), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	compare(t, "projects", root, []reportRun{textRun}, nil)

	put(t, root, "projects/README.md", "x\n")
	testkit.Git(t, root, "add", "projects/README.md")
	compare(t, "projects-tracked", root, []reportRun{textRun}, nil)
}

// 直接作業の記録・marker・一時ワークツリーと、ワークツリーにしかないレイヤーのファイル・レイヤーのずれ。
func TestReportDirectAndLayers(t *testing.T) {
	quietEnv(t)
	root := testkit.Workspace(t, "w1", "w2")
	common := filepath.Join(root, ".git")
	put(t, common, "ccnavi-direct/w2", "version 3\nbase_branch main\n")
	put(t, common, "ccnavi-direct/gone", "version 4\nbase_branch main\nsnap_sha x\ncommon_dir "+common+"\nmain_tree "+root+"\n")
	put(t, common, "ccnavi-integrate/o1/marker", "state staged\n")
	put(t, common, "ccnavi-integrate/o2/marker", "version 4\n")
	if err := os.MkdirAll(filepath.Join(root, "logs", "direct-tmp", "self", "t1"), 0o755); err != nil {
		t.Fatal(err)
	}
	testkit.Git(t, root, "update-ref", "refs/ccnavi/direct/t1/c", "HEAD")
	put(t, testkit.Worktree(root, "w1"), ".ccnavi/config/project/rules.yml", "version: 1\nrules: []\n")
	put(t, root, ".ccnavi/config/project/risks.yml", "version: 1\n")
	testkit.Remote(t, root)
	testkit.Git(t, root, "push", "-q", "origin", "main")
	testkit.Git(t, root, "remote", "set-head", "origin", "main")
	compare(t, "direct-layers", root, []reportRun{textRun}, nil)
}

// 子のフローの確かめ（--flow）。読めないもの・無いもの・読めたものの JSON。
func TestReportFlow(t *testing.T) {
	quietEnv(t)
	root := testkit.Workspace(t)
	good := put(t, root, "flows/good.yml", "nodes:\n- id: a\n  type: start\n  data: {label: 始め}\n- id: b\n  type: end\nedges:\n- {source: a, target: b}\nmode: 0755\n")
	bad := put(t, root, "flows/bad.yml", "- 1\n")
	compare(t, "flow", root, []reportRun{{Flow: good, JSON: true}, {Flow: good}, {Flow: bad, JSON: true},
		{Flow: filepath.Join(root, "flows", "missing.yml")}}, nil)
}
