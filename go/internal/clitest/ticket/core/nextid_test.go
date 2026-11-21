package core

import (
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// idWorkspace は `ticket next-id` と識別子の形の warn を試す木（.claude と git のリポジトリだけ。ルールは要らない）。
type idWorkspace struct {
	t  *testing.T
	ws string
}

func newIDWorkspace(t *testing.T) *idWorkspace {
	t.Helper()
	ws := base.TempDir(t)
	ticketkit.Write(t, filepath.Join(ws, ".claude", "keep"), "")
	ticketkit.Git(t, ws, "init", "--quiet", "-b", "main")
	return &idWorkspace{t: t, ws: ws}
}

// leadingNumber は識別子の `<先頭の語>-<番号>-` の番号。
var leadingNumber = regexp.MustCompile(`^[a-z]+-(\d+)-`)

// idTicketText は識別子の提案・承認済みチケットの最小の本文。issue の欄は必須なので、識別子の番号があればそれに合わせる
// （番号の食い違いを見るテストを妨げない）。
func idTicketText(name, parent string, issue int, approved bool) string {
	lines := []string{"---", "version: 1", "ticket: " + name, "title: t"}
	if parent == "" {
		lines = append(lines, "mr: 1")
		if issue == 0 {
			issue = 1
			if found := leadingNumber.FindStringSubmatch(name); found != nil {
				issue, _ = strconv.Atoi(found[1])
			}
		}
	} else {
		lines = append(lines, "parent: "+parent, "phase: 1")
	}
	if issue != 0 {
		lines = append(lines, "issue: "+strconv.Itoa(issue))
	}
	if approved {
		lines = append(lines, `ccnavi_approved: {approved_at: "2026-01-01T00:00:00Z", source_tree: "", source_path: p}`)
	}
	lines = append(lines, "allow:", `- {glob: "src/*", match: "Write|Edit"}`, "---", "")
	return strings.Join(lines, "\n")
}

func (w *idWorkspace) propose(name string) {
	ticketkit.Write(w.t, filepath.Join(w.ws, "wip", "proposals", "todo", name+".md"), idTicketText(name, "", 0, false))
}

func (w *idWorkspace) proposeWithIssue(name string, issue int) {
	ticketkit.Write(w.t, filepath.Join(w.ws, "wip", "proposals", "todo", name+".md"), idTicketText(name, "", issue, false))
}

func (w *idWorkspace) place(state, name, parent string) {
	ticketkit.Write(w.t, filepath.Join(w.ws, ".ccnavi", "approved", state, name+".md"), idTicketText(name, parent, 0, true))
}

func (w *idWorkspace) nextID(env map[string]string, words ...string) run.Result {
	w.t.Helper()
	return run.Ccnavi(w.t, append([]string{"--root", w.ws, "ticket", "next-id"}, words...), run.Opts{Env: env})
}

func (w *idWorkspace) commitEmpty() {
	w.t.Helper()
	ticketkit.Git(w.t, w.ws, "commit", "--allow-empty", "-qm", "x")
}

// 番号は、承認待ち・作業中・閉じた親の識別子の番号の最大 + 1。子の識別子は数えない。引数なしなら番号だけ、`<先頭の語> <slug>` なら
// 識別子を出す。チケット制御を切っていても動く。
func TestNextIDCountsParentsInEveryPlace(t *testing.T) {
	t.Parallel()
	for _, row := range []struct {
		name  string
		set   func(w *idWorkspace)
		words []string
		env   map[string]string
		want  string
	}{
		{"最初は 1", func(w *idWorkspace) {}, nil, nil, "1\n"},
		{"承認待ち・作業中・閉じたを数える", func(w *idWorkspace) {
			w.propose("feature-3-a")
			w.place("doing", "fix-7-b", "")
			w.place("done", "chore-12-c", "")
		}, nil, nil, "13\n"},
		{"子は数えない", func(w *idWorkspace) {
			w.place("doing", "feature-4-a", "")
			w.place("doing", "feature-4-a-01-01", "feature-4-a")
		}, nil, nil, "5\n"},
		{"識別子を組み立てる", func(w *idWorkspace) { w.propose("feature-9-a") }, []string{"chore", "skill-commit-push"}, nil, "chore-10-skill-commit-push\n"},
		{"日本語の slug", func(w *idWorkspace) {}, []string{"feature", "統合先の解決"}, nil, "feature-1-統合先の解決\n"},
		{"チケット制御を切っていても動く", func(w *idWorkspace) { w.propose("feature-3-a") }, nil, map[string]string{"CCNAVI_TICKET_CONTROL": "disable"}, "4\n"},
	} {
		t.Run(row.name, func(t *testing.T) {
			t.Parallel()
			w := newIDWorkspace(t)
			row.set(w)
			r := ticketkit.OK(t, w.nextID(row.env, row.words...))
			if r.Stdout != row.want {
				t.Errorf("stdout = %q（%q のはず）", r.Stdout, row.want)
			}
		})
	}
}

// 使えない形（統合先や保護名・形に合わない番号・禁止の記号・長すぎる・語が足りない）は、標準出力に何も出さず理由を言って止まる。
func TestNextIDRefusesUnusableForms(t *testing.T) {
	t.Parallel()
	w := newIDWorkspace(t)
	for _, row := range []struct {
		words []string
		why   string
	}{
		{[]string{"zzz", "x"}, "先頭の語 zzz は指定できない"},
		{[]string{"feature", "bad-12"}, "末尾が `-<2 桁>`"},
		{[]string{"feature", "a/b"}, "slug a/b は指定できない"},
		{[]string{"feature", "ab..c"}, ".."},
		{[]string{"feature", "a."}, "."},
		{[]string{"feature", "x.lock"}, ".lock"},
		{[]string{"feature", strings.Repeat("x", 45)}, "推奨の長さ"},
		{[]string{"feature"}, "2 つで実行します。"},
	} {
		r := ticketkit.Fails(t, w.nextID(nil, row.words...))
		if r.Stdout != "" {
			t.Errorf("%v: stdout = %q", row.words, r.Stdout)
		}
		ticketkit.Has(t, r.Stderr, row.why)
	}
}

// 既にあるローカルブランチとワークツリーのディレクトリも数える。origin の追跡ブランチは数えない。ブランチと同じ名前のタグや、
// 子の形の名前も数える。
func TestNextIDCountsLocalBranchesAndWorktreeDirectories(t *testing.T) {
	t.Parallel()
	w := newIDWorkspace(t)
	w.commitEmpty()
	ticketkit.Git(t, w.ws, "branch", "chore-12-x")
	if got := w.nextID(nil).Stdout; got != "13\n" {
		t.Errorf("ブランチを数えない: %q", got)
	}
	if err := os.MkdirAll(filepath.Join(w.ws, ".claude", "worktrees", "fix-20-y"), 0o755); err != nil {
		t.Fatal(err)
	}
	if got := w.nextID(nil).Stdout; got != "21\n" {
		t.Errorf("ワークツリーのディレクトリを数えない: %q", got)
	}
	// origin の追跡ブランチは数えない。
	ticketkit.Git(t, w.ws, "update-ref", "refs/remotes/origin/feature-99-z", "HEAD")
	if got := w.nextID(nil).Stdout; got != "21\n" {
		t.Errorf("追跡ブランチを数えた: %q", got)
	}
	ticketkit.Git(t, w.ws, "branch", "feature-50-a")
	ticketkit.Git(t, w.ws, "tag", "feature-50-a")
	if got := w.nextID(nil).Stdout; got != "51\n" {
		t.Errorf("同名のタグがあると数えない: %q", got)
	}
	ticketkit.Git(t, w.ws, "branch", "fix-60-x-10-19")
	if got := w.nextID(nil).Stdout; got != "61\n" {
		t.Errorf("子の形の名前を数えない: %q", got)
	}
}

// git のリポジトリでないワークスペースは異常ではない（番号 1）。数えられないプロジェクト（壊れた .git）があれば、番号を出さず
// 終了コード 2 でユーザに確かめてもらう。
func TestNextIDOutsideARepositoryAndWithABrokenProject(t *testing.T) {
	t.Parallel()
	t.Run("git のリポジトリでない", func(t *testing.T) {
		t.Parallel()
		w := newIDWorkspace(t)
		if err := os.RemoveAll(filepath.Join(w.ws, ".git")); err != nil {
			t.Fatal(err)
		}
		r := ticketkit.OK(t, w.nextID(nil))
		if r.Stdout != "1\n" {
			t.Errorf("stdout = %q", r.Stdout)
		}
	})
	t.Run("数えられないプロジェクト", func(t *testing.T) {
		t.Parallel()
		w := newIDWorkspace(t)
		// `.git` があるが中身が壊れていて、ブランチを数えられないプロジェクト。
		ticketkit.Write(t, filepath.Join(w.ws, "projects", "p", ".git"), "gitdir: /nonexistent/ccnavi-next-id\n")
		for _, words := range [][]string{nil, {"feature", "x"}} {
			r := w.nextID(nil, words...)
			if r.Code != 2 || r.Stdout != "" {
				t.Errorf("%v: code=%d stdout=%q", words, r.Code, r.Stdout)
			}
			ticketkit.Has(t, r.Stderr, "ユーザに確かめてください")
		}
	})
}
