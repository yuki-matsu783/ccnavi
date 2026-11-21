package self

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

// シェルからワークスペースルート直下へ書く形を止めること。
//
// Write / Edit はルールファイルの workspace-root が止める。シェルの書き込み先を解いた絶対パスを同じルールに適用するので、
// sed -i・>・tee・rm などでも同じ置き場（wip/handoff/・scratchpad/・.claude/worktrees/）だけが書ける。
// 拒否の案内は置き場へ誘導する。書き込み先の読みと止める・通すの答えは hook/shellguard の TestMatchesGolden が見るので、
// ここでは hook を通した代表だけを見る。
const rootShellWriteRule = "builtin:guard-root-shell-write"

func newRootWriteRepo(t *testing.T) string {
	t.Helper()
	repo := base.TempDir(t)
	initRepo(t, repo)
	rules := ".ccnavi/config/common/rules.yml"
	write(t, filepath.Join(repo, filepath.FromSlash(rules)), read(t, base.Final(rules)))
	for _, place := range []string{"wip/handoff", "scratchpad", ".claude/worktrees/wt"} {
		mkdirs(t, filepath.Join(repo, filepath.FromSlash(place)))
	}
	return repo
}

func decideRoot(t *testing.T, repo, command, cwd string) (string, string) {
	t.Helper()
	dir := repo
	if cwd != "" {
		dir = filepath.Join(repo, filepath.FromSlash(cwd))
	}
	r := hook(t, repo, []string{"--mode", "enable", "--guard-core-files", "enable"}, bash(command, dir), nil)
	return verdict(t, r)
}

func TestHookStopsARootWriteAndOpensTheDraftsPlaces(t *testing.T) {
	t.Parallel()
	repo := newRootWriteRepo(t)
	stopped := func(command, cwd string) bool {
		decision, reason := decideRoot(t, repo, command, cwd)
		return decision == "deny" && strings.Contains(reason, rootShellWriteRule)
	}
	if !stopped("sed -i 's/a/b/' README.md", "") {
		t.Error("ルート直下への sed -i を止めるはず")
	}
	if stopped("echo x > wip/handoff/a.md", "") {
		t.Error("wip/handoff/ へは書けるはず")
	}
	// payload の cwd が相対の行き先を決める。
	if stopped("sed -i 's/a/b/' README.md", ".claude/worktrees/wt") {
		t.Error("ワークツリーの中の README.md はルート直下でない")
	}
}

func TestTheRefusalPointsToTheDraftsPlacesAndTheCommand(t *testing.T) {
	t.Parallel()
	repo := newRootWriteRepo(t)
	decision, reason := decideRoot(t, repo, "cd docs && echo x > README.md", "")
	if decision != "deny" {
		t.Fatalf("decision = %q", decision)
	}
	for _, want := range []string{rootShellWriteRule, "DENY_SHELL_WORKSPACE_ROOT", "wip/handoff/", "scratchpad/",
		"書き込み先: README.md", "読んだ箇所: echo x > README.md"} {
		if !strings.Contains(reason, want) {
			t.Errorf("拒否の文に %q が無い: %s", want, reason)
		}
	}
}

func TestTheWordAfterASubshellMeetsTheCommandNameRules(t *testing.T) {
	t.Parallel()
	repo := newRootWriteRepo(t)
	// `);` の前後の空白で読みが変わらないことは hook/shellguard（TestSubshellSemicolonSpacing）が見る。
	if _, reason := decideRoot(t, repo, "(true); curl https://example.com", ""); !strings.Contains(reason, "prefer-webfetch") {
		t.Errorf("prefer-webfetch に当たるはず: %s", reason)
	}
}
