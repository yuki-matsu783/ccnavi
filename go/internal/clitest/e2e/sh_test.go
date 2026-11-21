package e2e

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// モード B の受入。実際のワークスペースを組み立てて sh を外から呼ぶ。
// ここで確かめるのは、保護済み sh が「自分の根」をワークスペースルートとして正しく取れること、その結果として記録・実行ファイル・
// 状態の置き場がワークスペース側に揃うこと、モード A（projects/ が無い形）が退行しないこと。sh は別のプロセスで動き、
// git のワークツリーの実物（.git の gitdir）と Windows のパスの表記が要るので、ここで見る。

// TestLogGoesToWorkspaceWhereverCommandRuns は、記録がワークスペースの logs/<プロジェクト>/ に出ることを、起こす場所を変えて見る。
func TestLogGoesToWorkspaceWhereverCommandRuns(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	cases := []struct {
		name, cwd, placed, forbidden string
	}{
		{"project", f.p1, "logs/p1", f.p1},
		{"worktree cut from a project", f.wp1, "logs/p1", f.wp1},
		{"worktree cut from the workspace", f.w0, "logs", f.w0},
		{"workspace root", f.root, "logs", ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f.clearLogs()
			r := f.sh(run.Opts{Dir: c.cwd}, "ccnavi-git.sh", "status")
			if r.Code != 0 {
				t.Fatalf("code = %d: %s", r.Code, r.Stderr)
			}
			f.assertLogsIn(t, c.placed, r)
			if c.forbidden != "" {
				assertNoLogsIn(t, c.forbidden)
			}
		})
	}
}

// TestReturnedLogPathOpensFromAProject は、返すパスが cwd がプロジェクトでも開ける形であることを見る。
func TestReturnedLogPathOpensFromAProject(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	r := f.sh(run.Opts{Dir: f.p1}, "ccnavi-git.sh", "status")
	found := logsUnder(filepath.Join(f.root, "logs", "p1"))
	if len(found) == 0 {
		t.Fatal("記録が無い")
	}
	name := filepath.Base(found[0])
	if !strings.Contains(r.Stdout, name) {
		t.Fatalf("記録の名前が返っていない: %q", r.Stdout)
	}
	opened := false
	for _, piece := range strings.Fields(strings.ReplaceAll(r.Stdout, "\n", " ")) {
		candidate := strings.Trim(piece, "()")
		// ラッパースクリプトは `log=<パス>` の形で返す。接頭辞を取り除いてから開く。
		if _, after, ok := strings.Cut(candidate, "="); ok {
			candidate = after
		}
		if !strings.HasSuffix(candidate, ".log") {
			continue
		}
		// cwd（プロジェクトの中）から開けるか、絶対で開けるかを見る。ワークスペースからしか開けないパスは、モード B では届かない。
		if isFilePath(filepath.Join(f.p1, candidate)) || isFilePath(candidate) {
			opened = true
		}
	}
	if !opened {
		t.Fatalf("返されたパスが cwd から開けない: %q", r.Stdout)
	}
}

// TestTicketScriptFindsBinaryFromInsideProject は、実行ファイルをワークスペースの dist/ に見つけることを見る。
func TestTicketScriptFindsBinaryFromInsideProject(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	r := f.sh(run.Opts{Dir: f.p1}, "ccnavi-ticket.sh", "cancel", "nope", "--reason", "x")
	if strings.Contains(r.Stderr, "実行ファイルが無い") {
		t.Fatal("プロジェクトの中を探しに行っている")
	}
}

// TestReviewScriptDoesNotCreateDotClaudeInProject は、プロジェクトに .claude/ を作らないことを見る（--lint が禁じている形）。
func TestReviewScriptDoesNotCreateDotClaudeInProject(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	r := f.sh(run.Opts{Dir: f.p1}, "ccnavi-review.sh", "origin")
	assertGotPastTheRoot(t, r)
	if _, err := os.Stat(filepath.Join(f.p1, ".claude")); err == nil {
		t.Fatal("プロジェクトに .claude/ を作った（--lint が禁じている形）")
	}
}

// TestChildWorktreeCutFromProjectCannotPush は、子チケットのワークツリーからは送らないことを見る。
func TestChildWorktreeCutFromProjectCannotPush(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	writeFile(t, filepath.Join(f.root, ".ccnavi", "approved", "doing", "wp1.md"),
		"---\nversion: 1\nticket: wp1\nparent: oya\n---\n本文\n")
	bare := filepath.Join(f.tmp, "origin.git")
	gitRun(t, f.tmp, "init", "-q", "--bare", bare)
	gitRun(t, f.p1, "remote", "add", "origin", bare)
	r := f.sh(run.Opts{Dir: f.wp1}, "ccnavi-git.sh", "push", "-u", "origin", "wp1")
	if r.Code != 2 {
		t.Fatalf("送れてしまった: code = %d, stdout = %q", r.Code, r.Stdout)
	}
	if !strings.Contains(r.Stderr, "子チケット") {
		t.Fatalf("子チケットだと言っていない: %q", r.Stderr)
	}
}

// TestWorktreeAddIsInsideWorkspace は、ワークツリーをワークスペースの .claude/worktrees/ の下に切ることを見る。
func TestWorktreeAddIsInsideWorkspace(t *testing.T) {
	t.Parallel()
	t.Run("a relative destination inside a project is rejected", func(t *testing.T) {
		f := newFixture(t)
		r := f.sh(run.Opts{Dir: f.p1}, "ccnavi-git.sh", "worktree", "add", ".claude/worktrees/x", "-b", "x")
		if r.Code != 2 {
			t.Fatalf("通ってしまった: %q", r.Stdout)
		}
		if _, err := os.Stat(filepath.Join(f.p1, ".claude", "worktrees", "x")); err == nil {
			t.Fatal("プロジェクトの中にワークツリーができた")
		}
		if !strings.Contains(r.Stderr, "../../.claude/worktrees/") {
			t.Fatalf("正しいパスを案内していない: %q", r.Stderr)
		}
	})
	t.Run("the correct spelling from inside a project works", func(t *testing.T) {
		f := newFixture(t)
		r := f.sh(run.Opts{Dir: f.p1}, "ccnavi-git.sh", "worktree", "add", "../../.claude/worktrees/x2", "-b", "x2")
		if r.Code != 0 {
			t.Fatalf("code = %d: %s", r.Code, r.Stderr)
		}
		if !isDirPath(filepath.Join(f.root, ".claude", "worktrees", "x2")) {
			t.Fatal("ワークスペースの下にワークツリーができていない")
		}
	})
	t.Run("an unknown option is rejected", func(t *testing.T) {
		f := newFixture(t)
		r := f.sh(run.Opts{Dir: f.p1}, "ccnavi-git.sh", "worktree", "add", "--no-such-option", "../../.claude/worktrees/x3", "-b", "x3")
		if r.Code != 2 {
			t.Fatalf("知らないオプションが通った: %q", r.Stdout)
		}
	})
}

// TestOriginCredentialNeverLeaks は、origin に埋まった資格情報がどの出口にも出ないことを見る。
// 伏せた結果は目で比べず、元のトークンの断片で探す（「消えているつもりで残っている」形を捕まえるため）。
func TestOriginCredentialNeverLeaks(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, url string
		secrets   []string
	}{
		{"an @ sign inside the token does not survive", "ssh://oauth2:glpat-AAA@BBB@example.invalid:2222/g/p.git", []string{"glpat-AAA", "BBB"}},
		{"an unreadable spelling does not leak on failure", "weird://oauth2:glpat-CCC@example.invalid/g/p.git", []string{"glpat-CCC"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := newFixture(t)
			gitRun(t, f.p1, "remote", "add", "origin", c.url)
			r := f.sh(run.Opts{Dir: f.p1}, "ccnavi-review.sh", "origin")
			assertGotPastTheRoot(t, r)
			blob := r.Stdout + r.Stderr
			// origin を読む処理まで届いたことも確かめる。届く前に失敗した実行を「外に出なかった」と数えると、検査の漏れが残ったまま通る。
			if !strings.Contains(blob, "origin") {
				t.Fatalf("origin を読む処理まで届いていない: %q", blob)
			}
			for _, secret := range c.secrets {
				if strings.Contains(blob, secret) {
					t.Fatalf("資格情報が出ている: %q", blob)
				}
			}
		})
	}
}

// TestWorkspaceRootDiscovery は、ワークスペースルートの探し方（上書き・目印・外で止まる）を見る。
func TestWorkspaceRootDiscovery(t *testing.T) {
	t.Parallel()
	t.Run("the override is honoured", func(t *testing.T) {
		f := newFixture(t)
		other := filepath.Join(f.tmp, "ws2")
		buildWorkspace(t, f.src, other)
		r := f.sh(run.Opts{Dir: f.p1, Env: map[string]string{env.Workspace: other}}, "ccnavi-git.sh", "status")
		if r.Code != 0 {
			t.Fatalf("code = %d: %s", r.Code, r.Stderr)
		}
		if len(logsUnder(filepath.Join(other, "logs"))) == 0 {
			t.Fatal("CCNAVI_WORKSPACE の指す先に記録が出ていない")
		}
	})
	t.Run("an override without the marker fails", func(t *testing.T) {
		f := newFixture(t)
		empty := filepath.Join(f.tmp, "not-a-workspace")
		if err := os.MkdirAll(empty, 0o755); err != nil {
			t.Fatal(err)
		}
		r := f.sh(run.Opts{Dir: f.p1, Env: map[string]string{env.Workspace: empty}}, "ccnavi-git.sh", "status")
		if r.Code == 0 {
			t.Fatal("目印の無い場所を何も言わずに受け入れた")
		}
	})
	t.Run("outside any workspace it stops and says how", func(t *testing.T) {
		f := newFixture(t)
		stray := filepath.Join(f.tmp, "stray")
		makeRepo(t, stray)
		r := f.sh(run.Opts{Dir: stray}, "ccnavi-git.sh", "status")
		if r.Code == 0 {
			t.Fatal("ワークスペースの外で動いた")
		}
		if !strings.Contains(r.Stderr, env.Workspace) {
			t.Fatalf("抜け道を案内していない: %q", r.Stderr)
		}
	})
}

// TestHookSkipsTreeWithoutTests は、tests/ を持たないツリーでターンを止めないことを見る。
func TestHookSkipsTreeWithoutTests(t *testing.T) {
	t.Parallel()
	f := newFixture(t)
	hook := filepath.Join(f.root, ".claude", "hooks", "test-py.sh")
	if !isFilePath(hook) {
		t.Skip("test-py.sh がコピーされていない")
	}
	r := run.Sh(t, hook, nil, run.Opts{Dir: f.p1, Input: "{}"})
	if r.Code != 0 {
		t.Fatalf("tests/ の無いツリーで止まった: stdout=%q stderr=%q", r.Stdout, r.Stderr)
	}
	if strings.Contains(r.Stdout+r.Stderr, "Start directory is not importable") {
		t.Fatal("tests/ の無いツリーを探しに行っている")
	}
}

// TestModeALogsStayAtWorkspaceRoot は、projects/ が無いワークスペースでモード B が入る前と同じに動くことを見る（モード A）。
func TestModeALogsStayAtWorkspaceRoot(t *testing.T) {
	t.Parallel()
	f := newModeAFixture(t)
	r := f.sh(run.Opts{}, "ccnavi-git.sh", "status")
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	if len(logsUnder(filepath.Join(f.root, "logs"))) == 0 {
		t.Fatal("logs/ に出ていない")
	}
	if isDirPath(filepath.Join(f.root, "logs", "projects")) {
		t.Fatal("プロジェクトの区画ができている")
	}
}

// TestModeAWorktreeWritesToWorkspace は、モード A のワークツリーからの記録がワークスペースに出ることを見る。
func TestModeAWorktreeWritesToWorkspace(t *testing.T) {
	t.Parallel()
	f := newModeAFixture(t)
	r := f.sh(run.Opts{Dir: f.w0}, "ccnavi-git.sh", "status")
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	if len(logsUnder(filepath.Join(f.root, "logs"))) == 0 {
		t.Fatal("ワークツリーからの記録がワークスペースに出ていない")
	}
}

// TestModeAEmptyPlacementDirChangesNothing は、空の projects/ があるだけで置き場が変わらないことを見る。
func TestModeAEmptyPlacementDirChangesNothing(t *testing.T) {
	t.Parallel()
	f := newModeAFixture(t)
	if err := os.MkdirAll(filepath.Join(f.root, "projects"), 0o755); err != nil {
		t.Fatal(err)
	}
	r := f.sh(run.Opts{}, "ccnavi-git.sh", "status")
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	if len(logsUnder(filepath.Join(f.root, "logs"))) == 0 {
		t.Fatal("空の置き場があるだけで置き場が変わった")
	}
}
