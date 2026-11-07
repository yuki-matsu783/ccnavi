package hint

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/testkit"
)

const (
	parentWrong = "---\nversion: 1\nticket: feature-1-memo\nparent: feature-1-memo\nphase: 0\n---\n"
	parentBare  = "---\nversion: 1\nticket: feature-1-memo\ntitle: t\nmr: 3\n---\n"
	parentFull  = "---\nversion: 1\nticket: feature-1-memo\nissue: 1\nmr: 3\nplan: [a]\nphases: {a: {}}\n---\n"
	childText   = "---\nversion: 1\nticket: feature-1-memo-01-01\nparent: feature-1-memo\nphase: 1\n---\n"
	todo        = "wip/proposals/todo/feature-1-memo.md"
	todoChild   = "wip/proposals/todo/feature-1-memo-01-01.md"
)

func proposalHint(t *testing.T, root, rel, text, tool, kind string) string {
	t.Helper()
	path := testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), text)
	in := hookio.Input{
		Event: hookio.PostToolUse, ToolName: tool, Cwd: root,
		ToolInput:    pyjson.NewObject("file_path", path),
		ToolResponse: pyjson.NewObject("type", kind, "filePath", path),
	}
	return ProposalHint(testkit.Settings(t, root), root, in)
}

func TestProposalHint(t *testing.T) {
	root := testkit.TempRoot(t)
	testkit.Put(t, filepath.Join(root, ".ccnavi", "scripts", "ccnavi-common.sh"), "")
	for _, c := range []struct {
		name, rel, text, tool, kind string
		has                         []string
		lacks                       []string
	}{
		{"parent: を書いた親は消すよう言う", todo, parentWrong, config.ToolWrite, createdType,
			[]string{codeParentKey, "`parent:` と `phase:` の行を消して", "ccnavi-launcher.sh ticket new"}, nil},
		{"欠けた欄を名指しする", todo, parentBare, config.ToolWrite, createdType,
			[]string{codeParentFields, "`plan:`・`phases:`"}, []string{"`mr:`"}},
		{"issue の欠け", todo, strings.Replace(parentFull, "issue: 1\n", "", 1), config.ToolWrite, createdType, []string{"`issue:`"}, nil},
		{"空の plan は欠けと数える", todo, strings.Replace(parentFull, "plan: [a]", "plan: []", 1), config.ToolWrite, createdType, []string{"`plan:`"}, nil},
		{"想定外の位置のワークツリーでも言う", ".claude/worktrees/feature-1-memo/" + todo, parentWrong, config.ToolWrite, createdType, []string{codeParentKey}, nil},
	} {
		got := proposalHint(t, root, c.rel, c.text, c.tool, c.kind)
		for _, want := range c.has {
			if !strings.Contains(got, want) {
				t.Errorf("%s: %q が無い: %s", c.name, want, got)
			}
		}
		for _, bad := range c.lacks {
			if strings.Contains(got, bad) {
				t.Errorf("%s: %q がある: %s", c.name, bad, got)
			}
		}
	}
	for name, got := range map[string]string{
		"揃った親":        proposalHint(t, root, todo, parentFull, config.ToolWrite, createdType),
		"子":           proposalHint(t, root, todoChild, childText, config.ToolWrite, createdType),
		"別の親を指す子":     proposalHint(t, root, todoChild, strings.Replace(childText, "parent: feature-1-memo", "parent: feature-2-other", 1), config.ToolWrite, createdType),
		"作り直しでない書き換え": proposalHint(t, root, todo, parentWrong, config.ToolWrite, "update"),
		"Edit":        proposalHint(t, root, todo, parentWrong, config.ToolEdit, createdType),
		"review の置き場": proposalHint(t, root, "wip/proposals/review/feature-1-memo.md", parentWrong, config.ToolWrite, createdType),
		"置き場の外":       proposalHint(t, root, "docs/feature-1-memo.md", parentWrong, config.ToolWrite, createdType),
	} {
		if got != "" {
			t.Errorf("%s: %q", name, got)
		}
	}
	// 文は Python と 1 字も違わない。
	const oracle = `
import json, os, sys
from ccnavi.hook import proposalhint
from ccnavi.infra import hookio, settings
c = json.load(sys.stdin)
root = c["root"]
out = []
for rel, text in c["cases"]:
    path = os.path.join(root, *rel.split("/"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    p = hookio.Input(event="PostToolUse", tool_name="Write", tool_input={"file_path": path}, cwd=root,
                     tool_response={"type": "create", "filePath": path})
    out.append(proposalhint.hint(settings.load(root)[0], root, p).replace(root, "<root>"))
print(json.dumps(out))
`
	cases := [][2]string{{todo, parentWrong}, {todo, parentBare}, {todo, parentFull}}
	want := testkit.Answers[[]string](t, "", oracle, map[string]any{"root": root, "cases": cases})
	for i, k := range cases {
		if got := testkit.Rel(root, proposalHint(t, root, k[0], k[1], config.ToolWrite, createdType)); got != want[i] {
			t.Errorf("%s:\nGo:     %s\nPython: %s", k[0], got, want[i])
		}
	}
}

// runGit は手元の時計でコミットする（reflog の時刻で「直前のコミット」を見るので、時刻を固定しない）。
func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", dir, "-c", "user.email=a@b", "-c", "user.name=t"}, args...)...)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, stderr.String())
	}
	return strings.TrimSpace(string(out))
}

type commitWorkspace struct {
	t      *testing.T
	root   string
	common string
	conf   *config.Settings
}

func newCommitWorkspace(t *testing.T) *commitWorkspace {
	root := filepath.Join(testkit.GitRoot(t), "ws")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	runGit(t, root, "init", "--quiet", "-b", "main")
	testkit.Put(t, filepath.Join(root, "README"), "x\n")
	testkit.CopyMessages(t, root)
	testkit.Put(t, filepath.Join(root, ".gitignore"), "/logs/\n/.ccnavi/\n/.claude/\n")
	runGit(t, root, "add", "-A")
	runGit(t, root, "commit", "--quiet", "-m", "init")
	common := runGit(t, root, "rev-parse", "--git-common-dir")
	if !filepath.IsAbs(common) {
		common = filepath.Join(root, common)
	}
	return &commitWorkspace{t, root, common, testkit.Settings(t, root)}
}

// worktree は `.claude/worktrees/<名前>` を切り、直接作業の記録を置く。
func (w *commitWorkspace) worktree(name string) string {
	path := filepath.Join(w.root, ".claude", "worktrees", name)
	runGit(w.t, w.root, "worktree", "add", "--quiet", "-b", name, path)
	snap := runGit(w.t, w.root, "rev-parse", "main")
	testkit.Put(w.t, filepath.Join(w.common, "ccnavi-direct", name), "version 4\nbase_branch main\nsnap_sha "+snap+"\n")
	return path
}

func (w *commitWorkspace) commit(path, text string) {
	testkit.Put(w.t, filepath.Join(path, "work.txt"), text)
	runGit(w.t, path, "add", "work.txt")
	runGit(w.t, path, "commit", "--quiet", "-m", "work")
}

// ticket は同じ名前の提案を置いて、チケットに結び付ける。
func (w *commitWorkspace) ticket(name string) {
	testkit.Put(w.t, filepath.Join(w.root, filepath.FromSlash(w.conf.Tickets), "todo", name+".md"), "---\n---\n")
}

func (w *commitWorkspace) say(tool, cwd, command, agent, session string) string {
	in := hookio.Input{Event: hookio.PostToolUse, ToolName: tool, SessionID: session, Cwd: cwd, AgentID: agent}
	if tool == config.ToolBash {
		if command == "" {
			command = "sh " + filepath.Join(w.root, ".ccnavi", "scripts", "ccnavi-git.sh") + " commit -m x"
		}
		in.ToolInput = pyjson.NewObject("command", command)
	} else {
		in.ToolInput = pyjson.NewObject("prompt", "実装して")
	}
	return CommitNotice(&bytes.Buffer{}, w.conf, w.root, in)
}

func (w *commitWorkspace) bash(cwd, command string) string {
	return w.say(config.ToolBash, cwd, command, "", "s1")
}

func TestCommitNotice(t *testing.T) {
	t.Run("ワークツリーごとに 1 回だけ言う", func(t *testing.T) {
		w := newCommitWorkspace(t)
		a, b := w.worktree("fix-1-a"), w.worktree("fix-2-b")
		w.commit(a, "y\n")
		w.commit(b, "y\n")
		first := w.bash(a, "")
		if !strings.Contains(first, "fix-1-a") || !strings.Contains(first, "haiku") || strings.Count(first, "次:") != 1 {
			t.Fatalf("1 回目: %s", first)
		}
		w.commit(a, "z\n")
		if got := w.bash(a, ""); got != "" {
			t.Errorf("2 回目: %s", got)
		}
		if got := w.bash(b, ""); !strings.Contains(got, "fix-2-b") {
			t.Errorf("別のワークツリー: %s", got)
		}
	})
	t.Run("コマンドの cd を追う", func(t *testing.T) {
		w := newCommitWorkspace(t)
		path := w.worktree("fix-1-a")
		w.commit(path, "y\n")
		sh := filepath.Join(w.root, ".ccnavi", "scripts", "ccnavi-git.sh")
		if got := w.bash(w.root, "cd "+path+" && sh "+sh+" commit -m x"); !strings.Contains(got, "fix-1-a") {
			t.Errorf("%s", got)
		}
	})
	t.Run("サブエージェント・dry-run・チケットに結び付く名前・コミットの無い回は黙る", func(t *testing.T) {
		w := newCommitWorkspace(t)
		empty := w.worktree("fix-3-c")
		path := w.worktree("fix-1-a")
		w.commit(path, "y\n")
		sh := filepath.Join(w.root, ".ccnavi", "scripts", "ccnavi-git.sh")
		bound := w.worktree("fix-2-b")
		w.commit(bound, "y\n")
		w.ticket("fix-2-b")
		for name, got := range map[string]string{
			"サブエージェント":   w.say(config.ToolBash, path, "", "sub1", "s1"),
			"dry-run":    w.bash(path, "sh "+sh+" commit --dry-run"),
			"status":     w.bash(path, "sh "+sh+" status"),
			"チケットに結び付く":  w.bash(bound, ""),
			"コミットの無いツリー": w.bash(empty, ""),
			"ワークスペースルート": w.bash(w.root, ""),
		} {
			if got != "" {
				t.Errorf("%s: %s", name, got)
			}
		}
	})
	t.Run("Agent が返ったら言い、メインの commit と 1 回を分け合う", func(t *testing.T) {
		w := newCommitWorkspace(t)
		path := w.worktree("fix-1-a")
		w.commit(path, "y\n")
		bound := w.worktree("fix-2-b")
		w.commit(bound, "y\n")
		w.ticket("fix-2-b")
		if got := w.say(config.ToolAgent, w.root, "", "sub1", "s1"); got != "" {
			t.Errorf("サブエージェントの中の Agent: %s", got)
		}
		if got := w.say(config.ToolRead, w.root, "", "", "s1"); got != "" {
			t.Errorf("Read: %s", got)
		}
		first := w.say(config.ToolAgent, w.root, "", "", "s1")
		if !strings.Contains(first, "fix-1-a") || strings.Contains(first, "fix-2-b") || strings.Count(first, "次:") != 1 {
			t.Fatalf("Agent: %s", first)
		}
		if got := w.say(config.ToolAgent, w.root, "", "", "s1"); got != "" {
			t.Errorf("2 回目: %s", got)
		}
		if got := w.bash(path, ""); got != "" {
			t.Errorf("commit と分け合わない: %s", got)
		}
		if got := w.say(config.ToolTask, w.root, "", "", "s2"); strings.Count(got, "次:") != 1 {
			t.Errorf("別のセッションの Task: %s", got)
		}
	})
}

// 直前のコミットは時刻で切る。古い commit だけなら言わない。
func TestJustCommittedIsRecent(t *testing.T) {
	w := newCommitWorkspace(t)
	path := w.worktree("fix-1-a")
	if justCommitted(path, time.Now().Add(git.CommitHintTimeout)) {
		t.Error("コミットの無いワークツリー（reflog の最後は branch）を直前の commit と読んだ")
	}
	w.commit(path, "y\n")
	if !justCommitted(path, time.Now().Add(git.CommitHintTimeout)) {
		t.Error("直前の commit を読めない")
	}
}
