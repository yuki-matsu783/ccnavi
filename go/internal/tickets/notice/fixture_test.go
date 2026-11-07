package notice

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/tickets/testkit"
)

// テストの道具。ワークツリーを無視するワークスペースと、承認済みチケットの見本を作る（git とファイルは testkit）。

// workspace は main に空でないコミットを 1 つ持つワークスペースを作り、文面ファイルを写す。
func workspace(t *testing.T) string {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q", "-b", "main")
	testkit.Put(t, filepath.Join(root, ".gitignore"), "/scratchpad\n/logs\n/remote.git\n/.ccnavi/messages\n/.claude/\n")
	testkit.Git(t, root, "add", ".gitignore")
	testkit.Git(t, root, "commit", "-q", "-m", "init")
	testkit.CopyMessages(t, root)
	return root
}

// touch は更新時刻を決める（並びを Python と Go で同じにする）。
func touch(t *testing.T, path string, when time.Time) {
	t.Helper()
	if err := os.Chtimes(path, when, when); err != nil {
		t.Fatal(err)
	}
}

// 承認済みチケットの見本。

func approvedPath(root, parent, state, id string) string {
	return filepath.Join(testkit.Worktree(root, parent), ".ccnavi", "approved", state, id+".md")
}

const phasesCopy = "phases:\n  work:\n    kind: work\n    title: 作業\n    review: false\n    scope:\n    - src/*\n" +
	"  research:\n    kind: work\n    title: 調査\n    review: false\n    scope:\n    - wip/research/*\n"

// parentText は親の見本。plan は計画の行（"  - work" の形）、allow は範囲の glob、extra は frontmatter に足す行。
func parentText(id string, plan, allow []string, extra ...string) string {
	head := "---\nversion: 1\nticket: " + id + "\nmr: 7\nissue: 1\n"
	if len(plan) > 0 {
		head += phasesCopy + "plan:\n" + strings.Join(plan, "\n") + "\n"
	}
	for _, e := range extra {
		head += e + "\n"
	}
	return head + "human_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" + allowLines(allow) +
		"started_at: \"\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n"
}

// childText は子の見本。
func childText(id, parent, phase string, allow []string, extra ...string) string {
	text := "---\nversion: 1\nticket: " + id + "\nparent: " + parent + "\nphase: " + phase + "\n" +
		"human_review:\n  required: false\n  reason: t\ntitle: 子 " + id + "\nrationale: r\n" + allowLines(allow)
	for _, e := range extra {
		text += e + "\n"
	}
	return text + "---\n\n本文\n"
}

func allowLines(globs []string) string {
	out := "allow:\n"
	for _, g := range globs {
		if strings.HasPrefix(g, "re:") {
			out += "  - match: Write|Edit\n    regex: \"" + strings.TrimPrefix(g, "re:") + "\"\n"
			continue
		}
		out += "  - match: Write|Edit\n    glob: \"" + g + "\"\n"
	}
	return out
}
