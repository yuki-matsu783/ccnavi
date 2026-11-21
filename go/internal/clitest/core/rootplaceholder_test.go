package core

import (
	"path/filepath"
	"strings"
	"testing"
	"unicode"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// ルールの `{root}` がワークスペースルートに置き換わることの受入テスト。道具を外から呼んで応答だけを見る。
// 「ワークスペースルートの下で、かつ .claude/worktrees/ の外」を止めるルールが、main の側では止め、ワークツリーの中では止めず、
// ワークスペースの外には何も言わないこと。

// workspaceRootExpr はこのリポジトリの rules.yml と同じ形。先読みを使わずに 1 つ目の部分で場合分けする。
const workspaceRootExpr = `^{root}[\\/](?:[^.\\/][^\\/]*|\.[^c\\/][^\\/]*|\.c[^l\\/][^\\/]*|\.claude[\\/][^w\\/][^\\/]*)`

// このリポジトリの rules.yml の workspace-root が、3 つの置き場（wip/handoff/・scratchpad/・.claude/worktrees/）だけを通すこと。
// ルールだけを置いたワークスペースで `--test Write` を当て、workspace-root がヒットしたかを見る。
// ルールは 1 本の regex でも、`except:` で置き場を除く形でも書けるので、式を取り出して適用すると書き方に縛られる。
// 大文字小文字は判定と同じく区別しない。
func TestWorkspaceRootRealRuleLetsOnlyThePlacesThrough(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	raw := ruleByID(t, loadYAML(t, base.Final(".ccnavi/config/common/rules.yml")), "workspace-root")
	putCommonRules(t, root, map[string]any{"deny": []any{raw}})
	hits := func(tail string) bool {
		done := run.Ccnavi(t, []string{"--root", root, "--mode", "enable", "--guard-core-files", "disable", "--test", "Write", root + tail}, run.Opts{Dir: base.Root()})
		if done.Code != 0 {
			t.Fatalf("code = %d: %s", done.Code, done.Stderr)
		}
		return strings.Contains(done.Stdout, ":workspace-root ")
	}
	for _, tail := range []string{"/wip/handoff/a.md", "/wip/handoff/x/y.sh", "/wip/Handoff/a.md", "/scratchpad/a.md", "/.claude/worktrees/x/a.md"} {
		if hits(tail) {
			t.Errorf("%s が止まった", tail)
		}
	}
	for _, tail := range []string{
		"/scratchpadx/a.md", "/wip/scratchpad/a.md", "/wip/proposals/todo/a.md", "/wip/a.md", "/wip", "/w", "/wipx/a.md",
		"/wip/handoffx/a.md", "/README.md", "/.claude/settings.json", "/.cfoo", "/.ccnavi/config/common/rules.yml",
	} {
		if !hits(tail) {
			t.Errorf("%s が通った", tail)
		}
	}
}

type rootPlaceholderEnv struct {
	t    *testing.T
	root string
}

func newRootPlaceholderEnv(t *testing.T) *rootPlaceholderEnv {
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{
		"deny": []rule{
			{"id": "workspace-root", "match": "Write|Edit", "regex": workspaceRootExpr, "message": "main では編集しない"},
			{
				"id": "wip", "match": "Write", "glob": "{root}/wip/*",
				"message": "glob でもワークスペースルートを指せる。'sh {root}/.ccnavi/scripts/ccnavi-git.sh rm -r wip' で消す",
			},
		},
		"allow": []rule{{"id": "worktrees", "match": "Write|Edit", "glob": "*/.claude/worktrees/*"}},
	})
	return &rootPlaceholderEnv{t, root}
}

func (e *rootPlaceholderEnv) judge(tool, path string) map[string]any {
	return judgeTool(e.t, e.root, tool, path, "--guard-core-files", "disable")
}

func TestRootMainIsDeniedAndWorktreesAreAllowed(t *testing.T) {
	t.Parallel()
	e := newRootPlaceholderEnv(t)
	join := func(parts ...string) string { return filepath.Join(append([]string{e.root}, parts...)...) }
	for _, path := range []string{
		join("README.md"), join("src", "ccnavi", "entry", "cli.py"), join(".gitignore"),
		join(".ccnavi", "config", "common", "rules.yml"), join(".claude", "settings.json"), join(".claude", "skills", "x", "SKILL.md"),
	} {
		out := e.judge("Write", path)
		if str(out, "permissionDecision") != "deny" || !strings.Contains(str(out, "permissionDecisionReason"), "workspace-root") {
			t.Errorf("%s: %v", path, out)
		}
	}
	for _, path := range []string{
		join(".claude", "worktrees", "x", "README.md"),
		join(".claude", "worktrees", "x", "src", "ccnavi", "entry", "cli.py"),
		join(".claude", "worktrees", "x", ".ccnavi", "config", "common", "rules.yml"),
	} {
		out := e.judge("Edit", path)
		if str(out, "permissionDecision") == "deny" || strings.Contains(str(out, "permissionDecisionReason"), "workspace-root") {
			t.Errorf("%s: %v", path, out)
		}
	}
}

func TestRootOutsideTheRootIsNotMentioned(t *testing.T) {
	t.Parallel()
	e := newRootPlaceholderEnv(t)
	out := e.judge("Write", filepath.Join(emptyRoot(t), "README.md"))
	if str(out, "permissionDecision") == "deny" || !strings.Contains(str(out, "permissionDecisionReason"), "UNDECLARED") {
		t.Errorf("out = %v", out)
	}
}

func TestRootGlobFormAndSpellingOfTheRoot(t *testing.T) {
	t.Parallel()
	e := newRootPlaceholderEnv(t)
	out := e.judge("Write", filepath.Join(e.root, "wip", "a.md"))
	mustContain(t, "wip の理由", str(out, "permissionDecisionReason"), "wip")
	// 表記を変えても行き着く先でヒットする。`..` と、大文字小文字。
	if d := str(e.judge("Write", filepath.Join(e.root, "docs", "..", "README.md")), "permissionDecision"); d != "deny" {
		t.Errorf("`..` の迂回 = %s", d)
	}
	// ワークスペースルートのパスも、どの機械でも区別せずに適用する。区別する機械では別の場所を指すパスだが、
	// それでも止める側を採る（`{root}` を機械で変えない）。
	swapped := strings.Map(func(r rune) rune {
		if unicode.IsUpper(r) {
			return unicode.ToLower(r)
		}
		return unicode.ToUpper(r)
	}, e.root)
	if d := str(e.judge("Write", filepath.Join(swapped, "README.md")), "permissionDecision"); d != "deny" {
		t.Errorf("大文字小文字違い = %s", d)
	}
}

// 止めたときにモデルへ渡す文面の `{root}` も、ワークスペースルートの実パスになる。
// 置き換わらないと、案内どおりに `sh {root}/...` を打っても `{root}` というディレクトリは無く、どこからも届かない。
func TestRootMessageNamesTheRoot(t *testing.T) {
	t.Parallel()
	e := newRootPlaceholderEnv(t)
	reason := str(e.judge("Write", filepath.Join(e.root, "wip", "a.md")), "permissionDecisionReason")
	mustContain(t, "理由", reason, "'sh "+filepath.ToSlash(e.root)+"/.ccnavi/scripts/ccnavi-git.sh rm -r wip'")
	mustNotContain(t, "理由", reason, "{root}")
}
