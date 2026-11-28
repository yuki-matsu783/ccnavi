package post

// ccnavi 自身の保護（hook 本体・設定・sh・共通設定）と、承認済みチケットの置き場の保護が組み込みにあることの受入テスト。
// 実行前（PreToolUse）と実行後（PostToolUse など）の両方を、実際の git リポジトリ（ワークスペース・ワークツリー・
// プロジェクト・プロジェクトのワークツリー）で見る。共通のルールは最小の組にして、検知を支えるのが組み込みだけであることを、
// 記録に残る `rules` の集合で確かめる。止める・通すの表（パスごとの判定）は Go の hook/judge が持つので、ここに残すのは
// 文面と、実ファイルを使う形（ハードリンク・コピーの例外・復元）。

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

const (
	hooksRule    = "builtin:guard-hooks"
	settingsRule = "builtin:guard-settings"
	scriptsRule  = "builtin:guard-scripts"
	configRule   = "builtin:guard-ccnavi-config"
	linksRule    = "builtin:guard-handoff-links"
	approvedRule = "builtin:ticket-approved"
)

// minimalRules はこの検査のどの入力にもヒットしない deny を 1 本だけ持つ共通ルール（残す側のルールも入れない）。
const minimalRules = `{"version": 1, "deny": [{"id": "blocked-fetch", "match": "WebFetch", "glob": "*://blocked.example/*", "message": "取りに行かない。"}]}`

// targets は保護される場所と、それを守る組み込みのルール。順序は固定（ツリーを入れ替えて回す）。
var targets = []struct{ rel, rule string }{
	{".claude/hooks/guard.sh", hooksRule},
	{".claude/settings.json", settingsRule},
	{".claude/settings.local.json", settingsRule},
	{".ccnavi/scripts/ccnavi-x.sh", scriptsRule},
	{".ccnavi/config/common/rules.yml", configRule},
	{".ccnavi/config/common/phases.yml", configRule},
	{".ccnavi/config/common/risks.yml", configRule},
}

var trees = []string{"ws", "worktree", "project", "project-worktree"}

func allRules() []string {
	set := map[string]bool{}
	for _, tg := range targets {
		set[tg.rule] = true
	}
	var out []string
	for r := range set {
		out = append(out, r)
	}
	sort.Strings(out)
	return out
}

func initial(rel string) string {
	switch {
	case strings.HasSuffix(rel, "rules.yml"):
		return minimalRules + "\n"
	case strings.HasSuffix(rel, ".json"):
		return "{}\n"
	}
	return "# committed\n"
}

func changed(rel string) string {
	if strings.HasSuffix(rel, ".json") {
		return "{\"changed\": true}\n"
	}
	return initial(rel) + "# changed by a build\n"
}

// ---- 実行前 ----

// workspace は共通のルールを最小の組にした作業場所。
type workspace struct{ root string }

func newWorkspace(t *testing.T, sub ...string) *workspace {
	t.Helper()
	root := base.TempDir(t)
	if len(sub) > 0 {
		root = filepath.Join(root, sub[0])
	}
	write(t, base.CommonPath(root, "rules"), minimalRules)
	return &workspace{root}
}

func (w *workspace) at(rel string) string { return filepath.Join(w.root, filepath.FromSlash(rel)) }

// pre は PreToolUse を 1 回渡し、(判定, 理由, ヒットしたルールの id の並び) を返す。
func (w *workspace) pre(t *testing.T, tool string, input map[string]any, tickets string) (decision, reason string, ids []string) {
	t.Helper()
	c := call{Event: "PreToolUse", Tool: tool, Input: input, Cwd: w.root, Mode: "enable", Guard: "enable"}
	if tickets != "" {
		c.Env = map[string]string{env.TicketControl: tickets}
	}
	got := fire(t, w.root, c)
	if strings.TrimSpace(got.Stdout) != "" {
		out, _ := hookOutput(t, got)["hookSpecificOutput"].(map[string]any)
		decision, reason = str(out, "permissionDecision"), str(out, "permissionDecisionReason")
	}
	if rows := records(t, w.root); len(rows) > 0 {
		ids = strs(rows[len(rows)-1], "rules")
	}
	return decision, reason, ids
}

func (w *workspace) writeTo(t *testing.T, rel, tickets string) (string, string, []string) {
	t.Helper()
	return w.pre(t, "Write", map[string]any{"file_path": w.at(rel), "content": "x\n"}, tickets)
}

func TestAHardLinkedCopyUnderHandoffIsNotWritableAndTheNamedRulesAreNotAddedTwice(t *testing.T) {
	t.Parallel()
	t.Run("a hard-linked copy", func(t *testing.T) {
		t.Parallel()
		if !canHardLink(t) {
			t.Skip("ハードリンクを作れない機械")
		}
		w := newWorkspace(t)
		write(t, w.at(".claude/hooks/guard.sh"), "#!/bin/sh\n")
		if err := os.MkdirAll(w.at("wip/handoff/.claude/hooks"), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.Link(w.at(".claude/hooks/guard.sh"), w.at("wip/handoff/.claude/hooks/g.sh")); err != nil {
			t.Fatal(err)
		}
		decision, _, ids := w.writeTo(t, "wip/handoff/.claude/hooks/g.sh", "")
		if decision != "deny" || strings.Join(ids, "|") != hooksRule+"|"+linksRule {
			t.Errorf("decision=%q ids=%v", decision, ids)
		}
	})
	t.Run("the named rules are added once", func(t *testing.T) {
		t.Parallel()
		// 同じ id が 2 本入ると、同じ文面が併記される。
		w := newWorkspace(t)
		_, reason, ids := w.writeTo(t, ".claude/hooks/guard.sh", "")
		if strings.Join(ids, "|") != hooksRule || strings.Count(reason, "hook 本体です") != 1 {
			t.Errorf("ids=%v reason=%s", ids, reason)
		}
	})
}

// 共通のルールファイルが読めない間も、4 本は外れない。直し方は下書きに置いてユーザに取り込みを頼む形になる。
func TestWhileTheCommonRulesAreBrokenTheyCannotBeRepairedInPlace(t *testing.T) {
	t.Parallel()
	const draft = "wip/handoff/.ccnavi/config/common/rules.yml"
	newBroken := func(t *testing.T) *workspace {
		w := newWorkspace(t)
		write(t, base.CommonPath(w.root, "rules"), "deny:\n  - id: [unclosed\n<<<<<<< HEAD\n")
		return w
	}
	t.Run("write edit and notebook edit", func(t *testing.T) {
		t.Parallel()
		w := newBroken(t)
		rules := w.at(".ccnavi/config/common/rules.yml")
		for tool, input := range map[string]map[string]any{
			"Write":        {"file_path": rules, "content": "a"},
			"Edit":         {"file_path": rules, "old_string": "a", "new_string": "b"},
			"NotebookEdit": {"notebook_path": rules},
		} {
			decision, reason, ids := w.pre(t, tool, input, "")
			if decision != "deny" || len(ids) == 0 || ids[0] != configRule {
				t.Errorf("%s: decision=%q ids=%v\n%s", tool, decision, ids, reason)
			}
			mustHave(t, reason, draft, "ccnavi-user-handoff.sh") // 下書きに置いてユーザに取り込みを頼む、と言う
		}
	})
	t.Run("the shell", func(t *testing.T) {
		t.Parallel()
		w := newBroken(t)
		decision, reason, ids := w.pre(t, "Bash", map[string]any{"command": "echo x > .ccnavi/config/common/rules.yml"}, "")
		if decision != "deny" || !strings.Contains(strings.Join(ids, "|"), "builtin:guard-config-via-bash") {
			t.Errorf("decision=%q ids=%v", decision, ids)
		}
		mustHave(t, reason, draft, "ccnavi-user-handoff.sh")
		mustNotHave(t, reason, "Write か Edit のツールで書いて") // 下書きとユーザの取り込みを案内する
	})
}

func TestTheApprovedPlaceIsDeniedBeforeTheCallWhateverTheSelfGuard(t *testing.T) {
	t.Parallel()
	w := newWorkspace(t)
	_, reason, _ := w.writeTo(t, ".ccnavi/approved/doing/i0001.md", "enable")
	ticketSh := base.ScriptCommand(w.root, "ccnavi-ticket.sh")
	mustHave(t, reason, ticketSh+" status", ticketSh+" finish <識別子>", ticketSh+" approval-request <識別子>",
		"ccnavi-user-agree.sh", "wip/proposals/todo/")
	mustNotHave(t, reason, "{root}", "{script:")
}

// 文面に差し込みの目印が残らず、案内がワークスペースの sh を指す。ルートが空白を含んでも引用される。
func TestGuidanceNamesTheWorkspaceScriptEvenWhenTheRootHasASpace(t *testing.T) {
	t.Parallel()
	w := newWorkspace(t, "ccnavi named with space")
	stage := base.ScriptCommand(w.root, "ccnavi-stage.sh")
	if !strings.Contains(w.root, " ") || !strings.Contains(stage, `"`) || !strings.HasSuffix(stage, `/.ccnavi/scripts/ccnavi-stage.sh"`) ||
		!strings.Contains(stage, slash(w.root)) {
		t.Fatalf("stage = %s", stage)
	}
	for _, rel := range []string{".claude/hooks/guard.sh", ".claude/settings.json", ".ccnavi/scripts/ccnavi-x.sh", ".ccnavi/config/common/rules.yml"} {
		_, reason, _ := w.writeTo(t, rel, "")
		mustHave(t, reason, stage)
		mustNotHave(t, reason, "{root}", "{script:")
	}
}

// ---- 実行後 ----

// fixture は 4 種類のツリーを持つワークスペース。
//
// 自己防衛の既定は dry-run にしてある。enable だと、設定ファイルを守る側（selfguard）が settings と共通設定 3 本を先に
// 戻してしまい、組み込みの 4 本の検知を単独で見られない。dry-run は disable ではないので 4 本は足され、selfguard は
// 戻さず報告するだけになる。enable との組み合わせは TestWithTheSelfGuardEnabledTheFilesEndUpRestored が見る。
type fixture struct {
	root  string
	trees map[string]string
}

func populate(t *testing.T, repo string) {
	for _, tg := range targets {
		write(t, filepath.Join(repo, filepath.FromSlash(tg.rel)), initial(tg.rel))
	}
	write(t, filepath.Join(repo, filepath.FromSlash(doing)), ticket)
	write(t, filepath.Join(repo, "src", "app.py"), "print(1)\n")
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	dir := copyOf(t, "named-post", func(dir string) {
		root := filepath.Join(dir, "ws")
		run.Git(t, dir, "init", "--quiet", "ws")
		write(t, filepath.Join(root, ".gitignore"), ".claude/worktrees/\nprojects/\nlogs/\n")
		populate(t, root)
		run.Git(t, root, "add", "-A")
		run.Git(t, root, "commit", "--quiet", "-m", "init")
		lib := filepath.Join(root, "projects", "lib")
		run.Git(t, root, "init", "--quiet", lib)
		populate(t, lib)
		run.Git(t, lib, "add", "-A")
		run.Git(t, lib, "commit", "--quiet", "-m", "init")
		worktrees := filepath.Join(root, ".claude", "worktrees")
		run.Git(t, root, "worktree", "add", "--quiet", filepath.Join(worktrees, "w"), "-b", "w")
		run.Git(t, lib, "worktree", "add", "--quiet", filepath.Join(worktrees, "pw"), "-b", "pw")
	})
	root := filepath.Join(dir, "ws")
	worktrees := filepath.Join(root, ".claude", "worktrees")
	return &fixture{root: root, trees: map[string]string{
		"ws": root, "worktree": filepath.Join(worktrees, "w"),
		"project": filepath.Join(root, "projects", "lib"), "project-worktree": filepath.Join(worktrees, "pw"),
	}}
}

func (f *fixture) path(tree, rel string) string {
	return filepath.Join(f.trees[tree], filepath.FromSlash(rel))
}

// hook の既定は、全体モード enable・復元 enable・自己防衛 dry-run・チケット制御 enable・作業ディレクトリはルート。
func (f *fixture) hook(t *testing.T, c call) run.Result {
	t.Helper()
	if c.Mode == "" {
		c.Mode = "enable"
	}
	if c.Restore == "" {
		c.Restore = "enable"
	}
	if c.Guard == "" {
		c.Guard = "dry-run"
	}
	if c.Cwd == "" {
		c.Cwd = f.root
	}
	if _, set := c.Env[env.TicketControl]; !set {
		c.Env = map[string]string{env.TicketControl: "enable"}
	}
	if c.Event == "PostToolUseFailure" && c.Extra == nil {
		c.Extra = map[string]any{"error": "exit 1"}
	}
	return fire(t, f.root, c)
}

func (f *fixture) bash(t *testing.T, tree, command string, c call) run.Result {
	t.Helper()
	c.Cwd, c.Input = f.trees[tree], map[string]any{"command": command}
	return f.hook(t, c)
}

// baseline はセッションの基準とターンの基準を、何も変わっていないうちに取る。
func (f *fixture) baseline(t *testing.T, c call) {
	t.Helper()
	b := c
	b.Input = map[string]any{"command": "ls"}
	f.hook(t, b)
	p := c
	p.Event, p.Prompt = "UserPromptSubmit", "続きをお願いします"
	f.hook(t, p)
}

func (f *fixture) rules(t *testing.T) string {
	return strings.Join(strs(last(t, f.root), "rules"), "|")
}

// undo は書き換えたものをコミットした中身に戻す（git を起こさずに書き戻す）。
func (f *fixture) undo(t *testing.T, tree string) {
	for _, tg := range targets {
		write(t, f.path(tree, tg.rel), initial(tg.rel))
	}
}

func (f *fixture) dirtyAll(t *testing.T, tree string) {
	for _, tg := range targets {
		write(t, f.path(tree, tg.rel), changed(tg.rel))
	}
}

func TestEachPlaceIsDetectedWithItsBuiltinRuleAndRestored(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	f.baseline(t, call{})
	// 守る場所ごとに、ツリーを順に入れ替えて 1 回ずつ（全部の組み合わせは回さない）。どの場所も、どのツリーも 1 回は通る。
	for at, tg := range targets {
		tree := trees[at%len(trees)]
		t.Run(tree+"/"+tg.rel, func(t *testing.T) {
			path := f.path(tree, tg.rel)
			write(t, path, changed(tg.rel))
			got := f.bash(t, tree, "python build.py", call{})
			if got.Code != 2 || !strings.Contains(slash(got.Stderr), tg.rel) || f.rules(t) != tg.rule || read(t, path) != initial(tg.rel) {
				t.Errorf("code=%d rules=%s restored=%v\n%s", got.Code, f.rules(t), read(t, path) == initial(tg.rel), got.Stderr)
			}
		})
	}
}

func TestAFailedToolCallIsCheckedAndRestoredToo(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	f.baseline(t, call{})
	for _, tree := range trees {
		t.Run(tree, func(t *testing.T) {
			f.dirtyAll(t, tree)
			got := f.bash(t, tree, "false", call{Event: "PostToolUseFailure"})
			text := slash(additional(t, got))
			for _, tg := range targets {
				mustHave(t, text, tg.rel)
				if read(t, f.path(tree, tg.rel)) != initial(tg.rel) {
					t.Errorf("%s が復元されていない", tg.rel)
				}
			}
			if f.rules(t) != strings.Join(allRules(), "|") {
				t.Errorf("rules = %s", f.rules(t))
			}
		})
	}
}

func TestWithRestoreOffTheChangeIsReportedButKept(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	// 値ごとに別のツリーで 1 回ずつ。ツリーごとの検知は上のテストが見る。
	for _, c := range []struct{ restore, tree string }{{"dry-run", "worktree"}, {"disable", "project-worktree"}} {
		t.Run(c.restore, func(t *testing.T) {
			session := c.restore + "-" + c.tree
			f.baseline(t, call{Session: session})
			rel := ".claude/hooks/guard.sh"
			write(t, f.path(c.tree, rel), changed(rel))
			got := f.bash(t, c.tree, "python build.py", call{Restore: c.restore, Session: session})
			if got.Code != 2 || read(t, f.path(c.tree, rel)) != changed(rel) || f.rules(t) != hooksRule {
				t.Errorf("code=%d rules=%s\n%s", got.Code, f.rules(t), said(got))
			}
		})
	}
}

// 設定ファイルを守る側（selfguard）が先に戻すものがあるので、検知した側は問わず、できあがりが元に戻っていること。
func TestWithTheSelfGuardEnabledTheFilesEndUpRestored(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	f.baseline(t, call{Guard: "enable"})
	for _, tree := range trees {
		t.Run(tree, func(t *testing.T) {
			f.dirtyAll(t, tree)
			got := f.bash(t, tree, "python build.py", call{Guard: "enable"})
			mustHave(t, slash(said(got)), ".claude/hooks/guard.sh")
			for _, tg := range targets {
				if read(t, f.path(tree, tg.rel)) != initial(tg.rel) {
					t.Errorf("%s が戻っていない", tg.rel)
				}
			}
		})
	}
}

func TestAGlobalDryRunReportsWithoutRestoringEvenWhenRestoreIsEnable(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	f.hook(t, call{Mode: "dry-run", Input: map[string]any{"command": "ls"}})
	for _, tree := range trees {
		t.Run(tree, func(t *testing.T) {
			rel := ".claude/settings.json"
			write(t, f.path(tree, rel), changed(rel))
			got := f.bash(t, tree, "python build.py", call{Mode: "dry-run"})
			mustHave(t, slash(said(got)), rel)
			if read(t, f.path(tree, rel)) != changed(rel) || f.rules(t) != settingsRule {
				t.Errorf("rules=%s", f.rules(t))
			}
		})
	}
}

// ターンの終わりは報告だけ。戻さない。
func TestTheTurnEndReportsEveryPlaceWithoutRestoring(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	for _, tree := range trees {
		t.Run(tree, func(t *testing.T) {
			session := "s-" + tree
			f.hook(t, call{Event: "UserPromptSubmit", Prompt: "続き", Session: session})
			f.dirtyAll(t, tree)
			got := f.hook(t, call{Event: "Stop", Session: session})
			message := slash(systemMessage(t, got))
			for _, tg := range targets {
				mustHave(t, message, tg.rel)
				if read(t, f.path(tree, tg.rel)) != changed(tg.rel) {
					t.Errorf("%s を復元した", tg.rel)
				}
			}
			if f.rules(t) != strings.Join(allRules(), "|") {
				t.Errorf("rules = %s", f.rules(t))
			}
			f.undo(t, tree)
		})
	}
}

// 他人の書きかけ（前から在った変更）とユーザが直した変更は戻さない。
func TestPreexistingAndUserChangesAreNotReverted(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	for _, tree := range trees {
		t.Run("before the session in "+tree, func(t *testing.T) {
			f.dirtyAll(t, tree)
			session := "pre-" + tree
			f.hook(t, call{Event: "SessionStart", Source: "startup", Session: session})
			got := f.bash(t, tree, "ls", call{Session: session})
			if got.Code != 0 {
				t.Errorf("code=%d %s", got.Code, said(got))
			}
			for _, tg := range targets {
				if read(t, f.path(tree, tg.rel)) != changed(tg.rel) {
					t.Errorf("%s を戻した", tg.rel)
				}
			}
			f.undo(t, tree)
		})
		t.Run("after a prompt in "+tree, func(t *testing.T) {
			session := "user-" + tree
			f.hook(t, call{Event: "SessionStart", Source: "startup", Session: session})
			f.dirtyAll(t, tree)
			f.hook(t, call{Event: "UserPromptSubmit", Prompt: "直しました", Session: session})
			got := f.bash(t, tree, "ls", call{Session: session})
			if got.Code != 0 {
				t.Errorf("code=%d %s", got.Code, said(got))
			}
			for _, tg := range targets {
				if read(t, f.path(tree, tg.rel)) != changed(tg.rel) {
					t.Errorf("%s を戻した", tg.rel)
				}
			}
			f.undo(t, tree)
		})
	}
}

// 直下の `wip/handoff/` の普通のコピーは検知しない（保護対象の直し方を下書きに置く場所）。ただし直下でないものと
// ハードリンクの実ファイルは検知する（下書きに見せかけて実物を書き換える経路を塞ぐ）。
func TestOnlyAPlainCopyRightUnderHandoffIsExempt(t *testing.T) {
	t.Parallel()
	const rel = ".claude/hooks/guard.sh"
	t.Run("a plain copy in every tree", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t)
		f.baseline(t, call{})
		for _, tree := range trees {
			for _, tg := range targets {
				write(t, f.path(tree, "wip/handoff/"+tg.rel), changed(tg.rel))
			}
			got := f.bash(t, tree, "python build.py", call{})
			if got.Code != 0 || got.Stderr != "" {
				t.Errorf("%s: code=%d %s", tree, got.Code, got.Stderr)
			}
			for _, tg := range targets {
				if !exists(f.path(tree, "wip/handoff/"+tg.rel)) {
					t.Errorf("%s: コピーが消えた", tg.rel)
				}
			}
		}
	})
	t.Run("a copy that is not right under a tree", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t)
		f.baseline(t, call{})
		for _, where := range []string{"docs/wip/handoff", "wip/handoffx"} {
			write(t, f.path("ws", where+"/"+rel), changed(rel))
			got := f.bash(t, "ws", "python build.py", call{})
			if got.Code != 2 || f.rules(t) != hooksRule {
				t.Errorf("%s: code=%d rules=%s", where, got.Code, f.rules(t))
			}
		}
	})
	t.Run("a hard-linked copy", func(t *testing.T) {
		t.Parallel()
		if !canHardLink(t) {
			t.Skip("ハードリンクを作れない機械")
		}
		f := newFixture(t)
		f.baseline(t, call{})
		copyTo := f.path("ws", "wip/handoff/"+rel)
		if err := os.MkdirAll(filepath.Dir(copyTo), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.Link(f.path("ws", rel), copyTo); err != nil {
			t.Fatal(err)
		}
		got := f.bash(t, "ws", "python build.py", call{})
		if got.Code != 2 || !strings.Contains(f.rules(t), hooksRule) {
			t.Errorf("code=%d rules=%s", got.Code, f.rules(t))
		}
	})
}

// 自己防衛を切れば 4 本は足されない。Stop も何も言わない。
func TestSwitchingTheSelfGuardOffDropsTheFour(t *testing.T) {
	t.Parallel()
	t.Run("a tool call", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t)
		f.baseline(t, call{Guard: "disable"})
		for _, tree := range trees {
			f.dirtyAll(t, tree)
			if got := f.bash(t, tree, "python build.py", call{Guard: "disable"}); got.Code != 0 || got.Stderr != "" {
				t.Errorf("%s: code=%d %s", tree, got.Code, got.Stderr)
			}
			f.undo(t, tree)
		}
	})
	t.Run("the turn end", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t)
		f.hook(t, call{Event: "UserPromptSubmit", Prompt: "続き", Guard: "disable"})
		f.dirtyAll(t, "ws")
		got := f.hook(t, call{Event: "Stop", Guard: "disable"})
		mustNotHave(t, got.Stdout, "systemMessage")
	})
}

// 共通のルールファイルが読めない間も、実行後の保護範囲から 4 本は外れない。
func TestTheFourStayWhileTheCommonRulesAreBroken(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	write(t, f.path("ws", ".ccnavi/config/common/rules.yml"), "deny:\n  - id: [unclosed\n")
	run.Git(t, f.root, "add", "-A")
	run.Git(t, f.root, "commit", "--quiet", "-m", "broken rules")
	f.baseline(t, call{})
	for _, c := range []struct{ rel, rule string }{{".claude/hooks/guard.sh", hooksRule}, {".ccnavi/scripts/ccnavi-x.sh", scriptsRule}} {
		write(t, f.path("ws", c.rel), changed(c.rel))
		got := f.bash(t, "ws", "python build.py", call{})
		if got.Code != 2 || f.rules(t) != c.rule || read(t, f.path("ws", c.rel)) != initial(c.rel) {
			t.Errorf("%s: code=%d rules=%s", c.rel, got.Code, f.rules(t))
		}
	}
}

// ---- 承認済みの置き場（チケット制御の保護。自己防衛の値には依らない） ----

func (f *fixture) widen(t *testing.T, tree string) {
	write(t, f.path(tree, doing), strings.Replace(ticket, `glob: "src/*"`, `glob: "*"`, 1))
}

func TestApprovedPlaceIsDetectedAndRestoredWhateverTheSelfGuard(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	// 自己防衛の値ごとに 2 つのツリーで（どのツリーも 1 回は通る）。
	for _, c := range []struct {
		guard string
		trees []string
	}{{"enable", trees[:2]}, {"disable", trees[2:]}} {
		session := "g-" + c.guard
		f.baseline(t, call{Guard: c.guard, Session: session})
		for _, tree := range c.trees {
			t.Run(c.guard+"/"+tree, func(t *testing.T) {
				f.widen(t, tree)
				got := f.bash(t, tree, "python build.py", call{Guard: c.guard, Session: session})
				if got.Code != 2 || f.rules(t) != approvedRule || read(t, f.path(tree, doing)) != ticket {
					t.Errorf("code=%d rules=%s\n%s", got.Code, f.rules(t), said(got))
				}
			})
		}
	}
}

func TestApprovedPlaceFollowsTheModeAndTheEvent(t *testing.T) {
	t.Parallel()
	t.Run("a global dry-run reports only", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t)
		f.hook(t, call{Mode: "dry-run", Input: map[string]any{"command": "ls"}})
		f.widen(t, "ws")
		got := f.bash(t, "ws", "python build.py", call{Mode: "dry-run"})
		mustHave(t, slash(said(got)), doing)
		if f.rules(t) != approvedRule || read(t, f.path("ws", doing)) == ticket {
			t.Errorf("rules=%s", f.rules(t))
		}
	})
	t.Run("a failure restores and the turn end only reports", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t)
		f.baseline(t, call{})
		f.widen(t, "ws")
		failure := f.bash(t, "ws", "false", call{Event: "PostToolUseFailure"})
		mustHave(t, failure.Stdout, doing)
		if read(t, f.path("ws", doing)) != ticket {
			t.Error("失敗でも復元する")
		}
		f.widen(t, "ws")
		stop := f.hook(t, call{Event: "Stop", Restore: "disable"})
		mustHave(t, systemMessage(t, stop), doing)
		if read(t, f.path("ws", doing)) == ticket {
			t.Error("Stop は復元しない")
		}
	})
	t.Run("changes from before and the user's changes are not reverted", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t)
		f.widen(t, "ws")
		f.hook(t, call{Event: "SessionStart", Source: "startup", Session: "pre"})
		if got := f.bash(t, "ws", "ls", call{Session: "pre"}); got.Code != 0 || read(t, f.path("ws", doing)) == ticket {
			t.Errorf("前から在った変更: code=%d %s", got.Code, said(got))
		}
		write(t, f.path("ws", doing), ticket)
		f.hook(t, call{Event: "SessionStart", Source: "startup", Session: "user"})
		f.widen(t, "ws")
		f.hook(t, call{Event: "UserPromptSubmit", Prompt: "直しました", Session: "user"})
		if got := f.bash(t, "ws", "ls", call{Session: "user"}); got.Code != 0 || read(t, f.path("ws", doing)) == ticket {
			t.Errorf("ユーザの変更: code=%d %s", got.Code, said(got))
		}
	})
	t.Run("with ticket control disabled the protection is dropped", func(t *testing.T) {
		t.Parallel()
		f := newFixture(t)
		off := map[string]string{env.TicketControl: "disable"}
		for _, guard := range []string{"enable", "disable"} {
			session := "d-" + guard
			f.baseline(t, call{Guard: guard, Env: off, Session: session})
			f.widen(t, "ws")
			got := f.bash(t, "ws", "python build.py", call{Guard: guard, Env: off, Session: session})
			if got.Code != 0 || got.Stderr != "" || read(t, f.path("ws", doing)) == ticket {
				t.Errorf("guard=%s: code=%d %s", guard, got.Code, got.Stderr)
			}
			write(t, f.path("ws", doing), ticket)
		}
	})
}

func TestAHandoffCopyInTheApprovedPlaceIsNotExempt(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	f.baseline(t, call{})
	write(t, f.path("ws", "wip/handoff/"+doing), ticket)
	if got := f.bash(t, "ws", "python build.py", call{}); got.Code != 2 || f.rules(t) != approvedRule {
		t.Errorf("code=%d rules=%s", got.Code, f.rules(t))
	}
}

func TestTheApprovedPlaceReportGuidesTheNextMove(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	f.baseline(t, call{})
	f.widen(t, "ws")
	got := f.bash(t, "ws", "python build.py", call{})
	ticketSh := base.ScriptCommand(f.root, "ccnavi-ticket.sh")
	mustHave(t, got.Stderr, ticketSh+" status", ticketSh+" finish", ticketSh+" approval-request", "ccnavi-user-agree.sh")
}

// ccnavi のスクリプトが書く形は外れ、不正な変更は外れない（見分けはコマンド名ではなく、変更の中身の形で行う）。
func TestForgedChangesInTheApprovedPlaceAreReportedAndRestored(t *testing.T) {
	t.Parallel()
	cases := map[string]string{
		"scope":    strings.Replace(ticket, `glob: "src/*"`, `glob: "*"`, 1),
		"body":     strings.Replace(ticket, "body\n", "body with more\n", 1),
		"parent":   strings.Replace(ticket, "id: i0001\n", "id: i0001\nparent: i0000\n", 1),
		"phase":    strings.Replace(ticket, "id: i0001\n", "id: i0001\nphase: implement\n", 1),
		"deletion": "",
	}
	for name, text := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			f := newFixture(t)
			f.baseline(t, call{})
			if name == "deletion" {
				remove(t, f.path("ws", doing))
			} else {
				write(t, f.path("ws", doing), text)
			}
			got := f.bash(t, "ws", "sh .ccnavi/scripts/ccnavi-ticket.sh start i0001", call{})
			if got.Code != 2 || f.rules(t) != approvedRule || read(t, f.path("ws", doing)) != ticket {
				t.Errorf("code=%d rules=%s\n%s", got.Code, f.rules(t), said(got))
			}
		})
	}
}
