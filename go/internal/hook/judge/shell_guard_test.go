package judge

import (
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// 設定ファイルの保護（builtin:guard-setting-files）のシェルの形の表。testdata/shell_guard.json の 1 行が 1 件で、
// 判定とヒットしたルールの名前（書いた id）を固定する。ルールは何でも通す 1 本だけにして、止まったものはルールの外の
// 組み込みで止まったと読めるようにする。見るのは、コピーする側の行き先だけを見ること（読むだけの cp は通し、行き先に
// なる形は書き方を変えても止める）、ディレクトリごと消す・退かす形、守るものが入ったディレクトリを行き先にした形。
// subject の {WS} はワークスペースのルートに置き換える。

type shellGuardRow struct {
	Case    string   `json:"case"`
	Command string   `json:"command"`
	Verdict string   `json:"verdict"`
	Rules   []string `json:"rules"`
}

// openRulesWorkspace は何でも通すルールと、プロジェクト（projects/lib）を持つワークスペース。
func openRulesWorkspace(t *testing.T) string {
	t.Helper()
	root := testkit.Workspace(t)
	testkit.Put(t, filepath.Join(root, config.DefaultRules),
		"version: 1\nallow:\n  - id: anything\n    match: Bash|Read|Write|Edit|NotebookEdit\n    regex: '.'\n")
	for _, name := range []string{"phases", "risks"} {
		testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", name+".yml"), "version: 1\n")
	}
	lib := filepath.Join(root, "projects", "lib")
	testkit.Git(t, root, "init", "-q", lib)
	for _, name := range []string{"rules", "phases", "risks"} {
		testkit.Put(t, filepath.Join(lib, ".ccnavi", "config", "project", name+".yml"), "version: 1\n")
	}
	testkit.CopyMessages(t, root)
	return root
}

func TestShellGuardTable(t *testing.T) {
	rows := golden.Load[[]shellGuardRow](t, "shell_guard")
	root := openRulesWorkspace(t)
	for _, r := range rows {
		command := strings.ReplaceAll(r.Command, "{WS}", filepath.ToSlash(root))
		a := runGo(t, root, judgeCase{Payload: call(config.ToolBash, root, map[string]any{"command": command}), Env: with("CCNAVI_GUARD_CORE_FILES", "enable")})
		rules, _ := a.Record["rules"].([]any)
		got := writtenIDs(rules)
		decision, _ := a.Record["decision"].(string)
		if decision != r.Verdict || !slices.Equal(got, r.Rules) {
			t.Errorf("%s: %q: %s %v、想定 %s %v", r.Case, r.Command, decision, got, r.Verdict, r.Rules)
		}
	}
}
