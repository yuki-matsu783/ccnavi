package land

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// 直接作業の取り込み（integrate）の、一時ワークツリーを作る前に止まる判断と、下書きの数え方を、使い捨てのワークスペースで
// 確かめる。sh と実行ファイルは起こさない（Run を直に呼ぶ）。取り込みの流れ全体（C を作る・載せる・片付ける）は
// tests/sh/test_integrate_sh.py が通しで見る。

type fixture struct {
	t  *testing.T
	ws string
	wt string // 記録つきの作業ワークツリー wt
}

func gitIn(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimRight(string(out), "\r\n")
}

func write(t *testing.T, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

// newFixture は、ワークスペース（ルートは main。origin/main が在る）と、記録つきの直接作業のワークツリー wt を組む。
func newFixture(t *testing.T) *fixture {
	t.Helper()
	ws, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	ws = filepath.Join(ws, "ws")
	if err := os.MkdirAll(ws, 0o755); err != nil {
		t.Fatal(err)
	}
	testkit.CopyMessages(t, ws)
	gitIn(t, ws, "init", "-q", "-b", "main")
	gitIn(t, ws, "config", "user.email", "t@example.invalid")
	gitIn(t, ws, "config", "user.name", "t")
	write(t, filepath.Join(ws, ".gitignore"), "/.ccnavi/\n/.claude/worktrees/\n/logs/\n*.local\n")
	write(t, filepath.Join(ws, "keep.txt"), "1\n2\n3\n")
	write(t, filepath.Join(ws, "gone.txt"), "bye\n")
	write(t, filepath.Join(ws, "other.txt"), "x\n")
	write(t, filepath.Join(ws, "wip/handoff/d.md"), "draft\n")
	gitIn(t, ws, "add", ".")
	gitIn(t, ws, "commit", "-q", "-m", "seed")
	gitIn(t, ws, "update-ref", "refs/remotes/origin/main", "HEAD")
	gitIn(t, ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
	f := &fixture{t: t, ws: ws}
	f.wt = f.cut("wt")
	return f
}

func (f *fixture) common() string {
	real, err := filepath.EvalSymlinks(filepath.Join(f.ws, ".git"))
	if err != nil {
		f.t.Fatal(err)
	}
	return real
}

func (f *fixture) record(name string) string {
	return filepath.Join(f.common(), "ccnavi-direct", name)
}

func (f *fixture) tmp(name string) string {
	return filepath.Join(f.ws, "logs", "direct-tmp", "self", name)
}

// cut は ccnavi-worktree.sh が切ったのと同じ形（記録・s の ref・ワークツリー）を作る。
func (f *fixture) cut(name string) string {
	head := gitIn(f.t, f.ws, "rev-parse", "HEAD")
	wt := filepath.Join(f.ws, ".claude", "worktrees", name)
	write(f.t, f.record(name), "version 4\n"+
		"base_branch main\nbase_sha "+head+"\nsnap_sha "+head+"\nsnap_wip no\n"+
		"side_by_side no\nmain_tree "+f.ws+"\ncommon_dir "+f.common()+"\nconfirm -\ncreated 0\n")
	gitIn(f.t, f.ws, "update-ref", "refs/ccnavi/direct/"+name+"/s", head)
	gitIn(f.t, f.ws, "worktree", "add", "-q", wt, "-b", name, "--no-track", head)
	return wt
}

// workInBranch は、作業ワークツリーで変更・追加・削除を 2 コミットに分けて積む。
func (f *fixture) workInBranch() {
	write(f.t, filepath.Join(f.wt, "keep.txt"), "1\n2\nthree\n")
	write(f.t, filepath.Join(f.wt, "new.txt"), "new\n")
	gitIn(f.t, f.wt, "add", ".")
	gitIn(f.t, f.wt, "commit", "-q", "-m", "w1")
	gitIn(f.t, f.wt, "rm", "-q", "gone.txt")
	gitIn(f.t, f.wt, "commit", "-q", "-m", "w2")
}

// commitDraft は wip/handoff/ の下書きをワークツリーにコミットする（無視されていても入れる）。
func (f *fixture) commitDraft(wt, rel, text string) {
	write(f.t, filepath.Join(wt, rel), text)
	gitIn(f.t, wt, "add", "-f", rel)
	gitIn(f.t, wt, "commit", "-q", "-m", "draft")
}

// cutWithOriginal は、起点（S）に元のファイルを持つワークツリー wt2 を切る。
func (f *fixture) cutWithOriginal(rel, text string) string {
	write(f.t, filepath.Join(f.ws, rel), text)
	gitIn(f.t, f.ws, "add", "-f", rel)
	gitIn(f.t, f.ws, "commit", "-q", "-m", "original")
	gitIn(f.t, f.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
	return f.cut("wt2")
}

// run は cwd（空ならルート）で Run を呼び、終了コードと標準出力・標準エラーを返す。
func (f *fixture) run(cwd string, args ...string) (int, string, string) {
	f.t.Helper()
	if cwd == "" {
		cwd = f.ws
	}
	conf, _ := config.Load(f.ws)
	var out, errOut bytes.Buffer
	code := Run(Input{
		Names: Names{Self: "sh ccnavi-git.sh", SelfQ: "sh 'ccnavi-git.sh'", WorktreeQ: "sh 'ccnavi-worktree.sh'", TicketQ: "sh 'ccnavi-ticket.sh'"},
		Root:  f.ws, Cwd: cwd, Conf: conf, MaxLines: 40,
	}, &out, &errOut, args)
	return code, out.String(), errOut.String()
}

// 一時ワークツリーを作る前に止まる判断。拒否は終了コード 2 で、取り込み結果の一時ワークツリーを作らない。
func TestIntegrateRefusals(t *testing.T) {
	for _, c := range []struct {
		name  string
		setup func(f *fixture)
		cwd   func(f *fixture) string // 省くとルート
		args  []string
		has   []string
		not   []string
	}{
		{name: "the base branch changed", setup: func(f *fixture) { gitIn(t, f.ws, "checkout", "-q", "-b", "elsewhere") },
			has: []string{"取り込み先（main）を開いていません", "elsewhere"}},
		{name: "from a worktree", cwd: func(f *fixture) string { return f.wt },
			has: []string{"ユーザのチェックアウト"}},
		{name: "a dirty source worktree", setup: func(f *fixture) { write(t, filepath.Join(f.wt, "wip.txt"), "x\n") },
			has: []string{"未コミットの変更"}},
		{name: "a ticket-bound base branch", setup: func(f *fixture) {
			write(t, filepath.Join(f.ws, "wip", "proposals", "todo", "main.md"), "---\nticket: main\n---\nbody\n")
		}, has: []string{"チケット"}},
		{name: "without a record it points at adopt", setup: func(f *fixture) {
			if err := os.Remove(f.record("wt")); err != nil {
				t.Fatal(err)
			}
		}, has: []string{"--adopt --base <取り込み先>"}, not: []string{"マージリクエスト"}},
		{name: "without a record a ticket-bound name is not pointed at adopt", setup: func(f *fixture) {
			if err := os.Remove(f.record("wt")); err != nil {
				t.Fatal(err)
			}
			write(t, filepath.Join(f.ws, "wip", "proposals", "todo", "wt.md"), "---\n---\n")
		}, has: []string{"チケットに結び付く名前です", "マージリクエストで出します", "ccnavi-ticket.sh", "status"}, not: []string{"--adopt"}},
		{name: "a record of another repository", setup: func(f *fixture) {
			data, err := os.ReadFile(f.record("wt"))
			if err != nil {
				t.Fatal(err)
			}
			write(t, f.record("wt"), strings.ReplaceAll(string(data), f.common(), "/somewhere/else/.git"))
		}, has: []string{"common_dir"}},
		{name: "a name that is a path", args: []string{"integrate", "../etc"}, has: []string{"名前"}},
		{name: "a name with a slash", args: []string{"integrate", "a/b"}, has: []string{"名前"}},
		{name: "no name", args: []string{"integrate"}, has: []string{"名前を 1 つだけ"}},
		{name: "a base-branch name", args: []string{"integrate", "main"}, has: []string{"統合先に使う名前"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			f := newFixture(t)
			f.workInBranch()
			if c.setup != nil {
				c.setup(f)
			}
			cwd := ""
			if c.cwd != nil {
				cwd = c.cwd(f)
			}
			args := c.args
			if args == nil {
				args = []string{"integrate", "wt"}
			}
			code, _, stderr := f.run(cwd, args...)
			if code != 2 {
				t.Fatalf("終了コード %d, want 2\n%s", code, stderr)
			}
			for _, h := range c.has {
				if !strings.Contains(stderr, h) {
					t.Errorf("文に %q が無い: %s", h, stderr)
				}
			}
			for _, h := range c.not {
				if strings.Contains(stderr, h) {
					t.Errorf("文に %q がある: %s", h, stderr)
				}
			}
			if exists(f.tmp("wt")) {
				t.Error("拒否したのに一時ワークツリーができている")
			}
		})
	}
}

// 下書き（wip/handoff/ の写し）と元の照らし合わせ。元と同じ写しは通して並べず、元の無い写しは並べて通す。元と違う写しと、
// 種類の保留と、追跡していない新しい下書きは止める。比べるときは大文字小文字を畳む（ASCII 以外も）。
func TestIntegrateDrafts(t *testing.T) {
	for _, c := range []struct {
		name     string
		original [2]string // 起点に在る元のファイルと中身（空なら wt を使う）
		draft    [2]string // 作業ワークツリーにコミットする下書き
		untrack  string    // 追跡しない下書き（無視される）
		code     int
		stdout   []string
		stderr   []string
		notOut   []string
	}{
		{name: "a draft without an original is listed and does not stop integrate",
			draft: [2]string{"wip/handoff/notes.md", "memo\n"}, code: 0,
			stdout: []string{"案内: 取り込まない下書き: wip/handoff/notes.md（"}},
		{name: "an unedited copy passes and is not listed",
			original: [2]string{"docs/guide.md", "original\n"}, draft: [2]string{"wip/handoff/docs/guide.md", "original\n"}, code: 0,
			notOut: []string{"取り込まない下書き"}},
		{name: "an edited copy outside the kinds is refused",
			original: [2]string{"tools/clean.js", "old\n"}, draft: [2]string{"wip/handoff/tools/clean.js", "edited\n"}, code: 2,
			stderr: []string{"wip/handoff/tools/clean.js", "ccnavi-user-handoff.sh"}},
		{name: "a copy is matched to its original ignoring case",
			original: [2]string{"docs/guide.md", "old\n"}, draft: [2]string{"wip/handoff/Docs/GUIDE.MD", "edited\n"}, code: 2,
			stderr: []string{"wip/handoff/Docs/GUIDE.MD"}},
		{name: "a non-ASCII original is matched ignoring case",
			original: [2]string{"docs/café.md", "old\n"}, draft: [2]string{"wip/handoff/DOCS/CAFÉ.md", "edited\n"}, code: 2,
			stderr: []string{"ccnavi-user-handoff.sh"}},
		{name: "a pending draft of a kind is refused by the kind check",
			original: [2]string{".ccnavi/config/project/rules.yml", "rules: []\n"},
			draft:    [2]string{"wip/handoff/.ccnavi/config/project/rules.yml", "rules: [x]\n"}, code: 2,
			stderr: []string{"取り込んでいない下書き"}},
		{name: "an untracked new draft is still refused",
			untrack: "wip/handoff/note.local", code: 2, stderr: []string{"wip/handoff/note.local"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			f := newFixture(t)
			name, wt := "wt", f.wt
			if c.original[0] != "" {
				name, wt = "wt2", f.cutWithOriginal(c.original[0], c.original[1])
			} else {
				f.workInBranch()
			}
			if c.draft[0] != "" {
				f.commitDraft(wt, c.draft[0], c.draft[1])
			}
			if c.untrack != "" {
				write(t, filepath.Join(wt, c.untrack), "ignored\n")
			}
			code, stdout, stderr := f.run("", "integrate", name)
			if code != c.code {
				t.Fatalf("終了コード %d, want %d\n%s%s", code, c.code, stdout, stderr)
			}
			for _, h := range c.stdout {
				if !strings.Contains(stdout, h) {
					t.Errorf("出力に %q が無い: %s", h, stdout)
				}
			}
			for _, h := range c.notOut {
				if strings.Contains(stdout, h) {
					t.Errorf("出力に %q がある: %s", h, stdout)
				}
			}
			for _, h := range c.stderr {
				if !strings.Contains(stderr, h) {
					t.Errorf("文に %q が無い: %s", h, stderr)
				}
			}
			if c.code == 2 && exists(f.tmp(name)) {
				t.Error("拒否したのに一時ワークツリーができている")
			}
			if c.code != 0 || len(c.draft[0]) == 0 {
				return
			}
			// 元の無い下書きは、取り込み結果にも一時ワークツリーにも入らない
			if c.original[0] == "" {
				marker := filepath.Join(f.common(), "ccnavi-integrate", name, "marker")
				data, _ := os.ReadFile(marker)
				var cTree string
				for _, line := range strings.Split(string(data), "\n") {
					if v, ok := strings.CutPrefix(line, "c_tree "); ok {
						cTree = v
					}
				}
				names := gitIn(t, f.ws, "ls-tree", "-r", "--name-only", cTree)
				if strings.Contains(names, c.draft[0]) || !strings.Contains(names, "new.txt") {
					t.Errorf("取り込み結果の中身:\n%s", names)
				}
				if exists(filepath.Join(f.tmp(name), c.draft[0])) {
					t.Error("一時ワークツリーに下書きが入っている")
				}
			}
		})
	}

	t.Run("a deleted draft is left alone in the result", func(t *testing.T) {
		f := newFixture(t)
		f.workInBranch()
		gitIn(t, f.wt, "rm", "-q", "wip/handoff/d.md")
		gitIn(t, f.wt, "commit", "-q", "-m", "drop the draft")
		code, stdout, stderr := f.run("", "integrate", "wt")
		if code != 0 {
			t.Fatalf("終了コード %d\n%s%s", code, stdout, stderr)
		}
		if strings.Contains(stdout, "d.md") {
			t.Errorf("消した下書きが並んでいる: %s", stdout)
		}
		if got, _ := os.ReadFile(filepath.Join(f.tmp("wt"), "wip/handoff/d.md")); string(got) != "draft\n" {
			t.Errorf("一時ワークツリーの d.md = %q", got)
		}
	})
}
