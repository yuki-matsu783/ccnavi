package git

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"sort"
	"strings"
	"testing"
	"time"
)

// isolate はホストの git の設定（init.defaultBranch・commit.gpgsign など）をテストから締め出す。
// 締め出さないとテストの答えが機械で変わる。
func isolate(t *testing.T) {
	t.Helper()
	if _, err := exec.LookPath(program); err != nil {
		t.Skip("git が無い")
	}
	empty := filepath.Join(t.TempDir(), "gitconfig")
	os.WriteFile(empty, nil, 0o644)
	t.Setenv("GIT_CONFIG_GLOBAL", empty)
	t.Setenv("GIT_CONFIG_NOSYSTEM", "1")
}

func gitIn(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command(program, append([]string{"-c", "user.email=t@example.com", "-c", "user.name=t"}, args...)...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

func commitFile(t *testing.T, dir, name, text string) {
	t.Helper()
	os.WriteFile(filepath.Join(dir, name), []byte(text), 0o644)
	gitIn(t, dir, "add", "--", name)
	gitIn(t, dir, "commit", "-q", "-m", name)
}

func tempRoot(t *testing.T) string {
	t.Helper()
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return root
}

func sortedKeys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// 上流から取り込んだコミット（fast-forward・merge）は数えず、このツリーの未 push のコミットだけを数える。
func TestCommittedCountsOnlyUnpushedCommits(t *testing.T) {
	isolate(t)
	cases := []struct {
		name string
		act  func(t *testing.T, up, clone string) (base string)
		want []string
	}{
		{"pull --ff-only", func(t *testing.T, up, clone string) string {
			gitIn(t, clone, "pull", "-q", "--ff-only")
			return ""
		}, nil},
		{"checkout <上流>", func(t *testing.T, up, clone string) string {
			gitIn(t, clone, "fetch", "-q", "origin")
			gitIn(t, clone, "checkout", "-q", "origin/main")
			return ""
		}, nil},
		{"reset --hard <上流>", func(t *testing.T, up, clone string) string {
			gitIn(t, clone, "fetch", "-q", "origin")
			gitIn(t, clone, "reset", "-q", "--hard", "origin/main")
			return ""
		}, nil},
		{"未 push のコミット", func(t *testing.T, up, clone string) string {
			commitFile(t, clone, "mine", "x\n")
			return ""
		}, []string{"mine"}},
		{"pull の後のコミットだけ", func(t *testing.T, up, clone string) string {
			gitIn(t, clone, "pull", "-q", "--ff-only")
			commitFile(t, clone, "mine", "x\n")
			return ""
		}, []string{"mine"}},
		{"同じターンで push したもの（設計書で許した穴）", func(t *testing.T, up, clone string) string {
			gitIn(t, clone, "pull", "-q", "--ff-only")
			gitIn(t, up, "config", "receive.denyCurrentBranch", "ignore")
			commitFile(t, clone, "mine", "x\n")
			gitIn(t, clone, "push", "-q", "origin", "HEAD:main")
			return ""
		}, nil},
		{"commit-tree して reset --hard", func(t *testing.T, up, clone string) string {
			base := gitIn(t, clone, "rev-parse", "HEAD")
			os.WriteFile(filepath.Join(clone, "forged"), []byte("x\n"), 0o644)
			gitIn(t, clone, "add", ".")
			tree := gitIn(t, clone, "write-tree")
			sha := gitIn(t, clone, "commit-tree", tree, "-p", base, "-m", "forged")
			gitIn(t, clone, "reset", "-q", "--hard", sha)
			return ""
		}, []string{"forged"}},
		{"merge の pull", func(t *testing.T, up, clone string) string {
			commitFile(t, clone, "mine", "x\n")
			base := gitIn(t, clone, "rev-parse", "HEAD")
			gitIn(t, clone, "pull", "-q", "--no-rebase", "--no-edit")
			return base
		}, nil},
		{"rebase の pull", func(t *testing.T, up, clone string) string {
			commitFile(t, clone, "mine", "x\n")
			gitIn(t, clone, "pull", "-q", "--rebase")
			return ""
		}, []string{"mine"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			root := tempRoot(t)
			up, clone := filepath.Join(root, "up"), filepath.Join(root, "clone")
			os.Mkdir(up, 0o755)
			gitIn(t, up, "init", "-q", "-b", "main")
			commitFile(t, up, "a", "x\n")
			gitIn(t, root, "clone", "-q", up, clone)
			base := gitIn(t, clone, "rev-parse", "HEAD")
			commitFile(t, up, "b", "x\n")
			if b := c.act(t, up, clone); b != "" {
				base = b
			}
			changes, reason := Committed(clone, base, 0)
			var got []string
			for _, ch := range changes {
				got = append(got, ch.Path)
			}
			if reason != "" || !slices.Equal(got, c.want) {
				t.Fatalf("got %v (%s), want %v", got, reason, c.want)
			}
		})
	}
	t.Run("リモートが無い", func(t *testing.T) {
		repo := filepath.Join(tempRoot(t), "solo")
		os.Mkdir(repo, 0o755)
		gitIn(t, repo, "init", "-q", "-b", "main")
		commitFile(t, repo, "a", "x\n")
		base := gitIn(t, repo, "rev-parse", "HEAD")
		commitFile(t, repo, "c", "x\n")
		changes, _ := Committed(repo, base, 0)
		if len(changes) != 1 || changes[0].Path != "c" || changes[0].Kind != KindCommitted || changes[0].Full != filepath.Join(repo, "c") {
			t.Fatalf("changes = %+v", changes)
		}
	})
}

func TestReadClassifiesChanges(t *testing.T) {
	isolate(t)
	repo := tempRoot(t)
	gitIn(t, repo, "init", "-q", "-b", "main")
	commitFile(t, repo, "kept", "x\n")
	commitFile(t, repo, "gone", "x\n")
	os.WriteFile(filepath.Join(repo, "kept"), []byte("y\n"), 0o644)
	os.Remove(filepath.Join(repo, "gone"))
	os.MkdirAll(filepath.Join(repo, "新しい"), 0o755)
	os.WriteFile(filepath.Join(repo, "新しい", "a b.txt"), nil, 0o644)
	os.WriteFile(filepath.Join(repo, "staged"), nil, 0o644)
	gitIn(t, repo, "add", "staged")
	changes, reason := Read(repo, 0)
	got := map[string]Change{}
	for _, c := range changes {
		got[c.Path] = c
	}
	want := map[string]Kind{"kept": KindChanged, "gone": kindGone, "新しい/a b.txt": KindNew, "staged": KindNew}
	if reason != "" || len(got) != len(want) {
		t.Fatalf("changes = %+v (%s)", changes, reason)
	}
	for path, kind := range want {
		if got[path].Kind != kind {
			t.Errorf("%s: %s, want %s", path, got[path].Kind, kind)
		}
	}
	if !got["staged"].Staged || got["新しい/a b.txt"].Staged || got["新しい/a b.txt"].Full != filepath.Join(repo, "新しい", "a b.txt") {
		t.Errorf("staged/full が違う: %+v", got)
	}
	if _, reason := Read("", 0); reason != reasonNoWorktree {
		t.Error("ワークツリーが無い理由")
	}
	if _, reason := Read(t.TempDir(), 0); reason != reasonFailed {
		t.Errorf("リポジトリでない: %s", reason)
	}
}

// index を書き換える git（restore・rm --cached）は lock を残さないよう長く待ち、読む git は短いまま。
func TestTimeoutsOfStateCalls(t *testing.T) {
	top := t.TempDir()
	os.WriteFile(filepath.Join(top, "b.txt"), []byte("b"), 0o644)
	type call struct {
		args    []string
		timeout time.Duration
	}
	seen := func(run func()) []call {
		var calls []call
		before := runGit
		runGit = func(cwd string, args []string, o Options) Done {
			calls = append(calls, call{args, o.timeout()})
			return Done{}
		}
		defer func() { runGit = before }()
		run()
		return calls
	}
	aside := filepath.Join(top, "aside")
	writes := map[string]func(){
		"restore": func() { Restore(top, Change{Kind: KindChanged, Path: "a.txt"}, aside) },
		"rm":      func() { Restore(top, Change{Kind: KindNew, Path: "b.txt", Staged: true}, aside) },
		"commit":  func() { RestoreCommitted(top, "c.txt") },
	}
	for name, run := range writes {
		calls := seen(run)
		if len(calls) != 1 || calls[0].timeout != 5*time.Second {
			t.Errorf("%s: %+v", name, calls)
		}
	}
	reads := []func(){
		func() { Read(top, 0) },
		func() { Head(top, Options{Timeout: StateTimeout}) },
		func() { Committed(top, "HEAD", 0) },
		func() { CommittedText(top, "c.txt", 0) },
	}
	for _, run := range reads {
		for _, c := range seen(run) {
			if c.timeout != 2*time.Second {
				t.Errorf("%v: %v", c.args, c.timeout)
			}
		}
	}
	if !ospathExists(filepath.Join(aside, "b.txt")) {
		t.Error("現れたファイルは消さずに退避する")
	}
}

func ospathExists(p string) bool {
	_, err := os.Lstat(p)
	return err == nil
}

func TestUndoWritesOnlyWrapperForms(t *testing.T) {
	wrapper := "sh /w/.ccnavi/scripts/ccnavi-git.sh"
	cases := []struct {
		change Change
		want   string
	}{
		{Change{Kind: KindChanged, Path: "p/x.txt"}, wrapper + ` restore --staged --worktree -- p/x.txt`},
		{Change{Kind: kindGone, Path: "p/a b.txt"}, wrapper + ` restore --staged --worktree -- "p/a b.txt"`},
		// ラッパーは rm -f と clean を通さない。索引から外してシェルの rm で消す。
		{Change{Kind: KindNew, Path: "p/x.txt", Staged: true}, wrapper + ` restore --staged -- p/x.txt && rm -- p/x.txt`},
		// "$" とバッククォートは "..." の中でも展開されるので、単引用符で囲む。
		{Change{Kind: KindNew, Path: "p/$x`y`.txt"}, "rm -- 'p/$x`y`.txt'"},
		{Change{Kind: KindCommitted, Path: "p/x.txt"}, ""},
	}
	for _, c := range cases {
		if got := Undo(c.change, wrapper); got != c.want {
			t.Errorf("Undo(%+v) = %q", c.change, got)
		}
	}
	if Undo(cases[0].change, "") != "" {
		t.Error("ラッパーが無ければ手順を返さない")
	}
}

// 子の仕事の始まりは、親の HEAD と子の HEAD の共通の祖先。統合先から取り込んだだけのパスは子のものではない。
func TestChildBaseAndTakenFromRemote(t *testing.T) {
	isolate(t)
	setup := func(t *testing.T) (base, parent, child, start string) {
		base = tempRoot(t)
		parent, child = filepath.Join(base, "parent"), filepath.Join(base, "child")
		os.Mkdir(parent, 0o755)
		gitIn(t, parent, "init", "-q", "-b", "p")
		commitFile(t, parent, "seed", "x\n")
		start = gitIn(t, parent, "rev-parse", "HEAD")
		gitIn(t, parent, "worktree", "add", "-q", "-b", "c", child, "p")
		return
	}
	changed := func(t *testing.T, child, base string) []string {
		return strings.Fields(gitIn(t, child, "diff", "--name-only", base+"..HEAD"))
	}
	upstream := func(t *testing.T, base, parent, start string, files map[string]string) {
		up := filepath.Join(base, "up")
		gitIn(t, parent, "worktree", "add", "-q", "-b", "m", up, start)
		keys := sortedKeys(map[string]bool{})
		for name := range files {
			keys = append(keys, name)
		}
		sort.Strings(keys)
		for _, name := range keys {
			commitFile(t, up, name, files[name])
		}
		gitIn(t, parent, "update-ref", "refs/remotes/origin/m", gitIn(t, up, "rev-parse", "HEAD"))
	}

	t.Run("親が先へ進んでも始まりは動かない", func(t *testing.T) {
		_, parent, child, start := setup(t)
		commitFile(t, child, "mine", "x\n")
		commitFile(t, parent, "other", "x\n")
		got, err := ChildBase(child, parent, 0, "")
		if err != nil || got != start || !slices.Equal(changed(t, child, got), []string{"mine"}) {
			t.Fatalf("got %s %v", got, err)
		}
	})
	t.Run("親を取り込んでもほかの子の変更を混ぜない", func(t *testing.T) {
		_, parent, child, start := setup(t)
		commitFile(t, child, "mine", "x\n")
		commitFile(t, parent, "other", "x\n")
		gitIn(t, child, "merge", "-q", "--no-edit", "p")
		commitFile(t, child, "mine2", "x\n")
		got, _ := ChildBase(child, parent, 0, "")
		if !slices.Contains(changed(t, child, start), "other") || !slices.Equal(changed(t, child, got), []string{"mine", "mine2"}) {
			t.Fatalf("changed = %v", changed(t, child, got))
		}
	})
	t.Run("親へ合流した子は合流した先頭から", func(t *testing.T) {
		_, parent, child, _ := setup(t)
		commitFile(t, child, "mine", "x\n")
		gitIn(t, parent, "merge", "-q", "--no-ff", "--no-edit", "c")
		commitFile(t, child, "again", "x\n")
		got, _ := ChildBase(child, parent, 0, "")
		if !slices.Equal(changed(t, child, got), []string{"again"}) {
			t.Fatalf("changed = %v", changed(t, child, got))
		}
	})
	t.Run("読めなければ理由", func(t *testing.T) {
		base, _, child, _ := setup(t)
		if got, err := ChildBase(child, filepath.Join(base, "nowhere"), 0, ""); got != "" || err == nil {
			t.Fatal("理由が返らない")
		}
		if _, err := ChildBase("", "x", 0, ""); err != errNoTrees {
			t.Fatal(err)
		}
	})

	taken := []struct {
		name  string
		files map[string]string
		act   func(t *testing.T, parent, child string)
		want  []string
	}{
		{"統合先から取り込んだだけのパス", map[string]string{"theirs": "t\n"}, func(t *testing.T, parent, child string) {
			commitFile(t, child, "mine", "x\n")
			gitIn(t, child, "merge", "-q", "--no-edit", "origin/m")
		}, []string{"theirs"}},
		{"衝突を解いたパスは子のもの", map[string]string{"seed": "theirs\n"}, func(t *testing.T, parent, child string) {
			commitFile(t, child, "seed", "mine\n")
			cmd := exec.Command(program, "-c", "user.email=t@example.com", "-c", "user.name=t", "merge", "--no-edit", "origin/m")
			cmd.Dir = child
			cmd.Run()
			commitFile(t, child, "seed", "resolved\n")
		}, nil},
		{"取り込んだ後に直したパスは子のもの", map[string]string{"theirs": "t\n", "other": "o\n"}, func(t *testing.T, parent, child string) {
			commitFile(t, child, "mine", "x\n")
			gitIn(t, child, "merge", "-q", "--no-edit", "origin/m")
			commitFile(t, child, "theirs", "edited\n")
		}, []string{"other"}},
		{"リモートに無いブランチの merge は取り込みでない", map[string]string{"theirs": "t\n"}, func(t *testing.T, parent, child string) {
			gitIn(t, parent, "update-ref", "-d", "refs/remotes/origin/m")
			commitFile(t, child, "mine", "x\n")
			gitIn(t, child, "merge", "-q", "--no-edit", "m")
		}, nil},
	}
	for _, c := range taken {
		t.Run(c.name, func(t *testing.T) {
			base, parent, child, start := setup(t)
			upstream(t, base, parent, start, c.files)
			c.act(t, parent, child)
			from, _ := ChildBase(child, parent, 0, "")
			if got := sortedKeys(TakenFromRemote(child, from, "HEAD", 0)); !slices.Equal(got, c.want) && !(len(got) == 0 && len(c.want) == 0) {
				t.Fatalf("got %v, want %v", got, c.want)
			}
		})
	}
}

func TestBranchNamesAndCommittedText(t *testing.T) {
	isolate(t)
	repo := tempRoot(t)
	gitIn(t, repo, "init", "-q", "-b", "main")
	commitFile(t, repo, "a.txt", "本文\r\n")
	gitIn(t, repo, "branch", "日本語")
	gitIn(t, repo, "update-ref", "refs/remotes/origin/up", "HEAD")
	gitIn(t, repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/up")
	if got := sortedKeys(BranchNames(repo, 0)); !slices.Equal(got, []string{"main", "up", "日本語"}) {
		t.Errorf("BranchNames = %v", got)
	}
	// 本文はテキストの読みなので改行が "\n" にそろう。
	if text, present, ok := CommittedText(repo, "a.txt", 0); text != "本文\n" || !present || !ok {
		t.Errorf("CommittedText = %q %v %v", text, present, ok)
	}
	if _, present, ok := CommittedText(repo, "none", 0); present || !ok {
		t.Error("HEAD に無いのは読めた答え")
	}
	if blob, ok := Blob(repo, "HEAD", "a.txt", Options{}); !ok || string(blob) != "本文\r\n" {
		t.Errorf("Blob はバイト列のまま: %q", blob)
	}
	if blob, ok := Blob(repo, "HEAD", "none", Options{}); blob != nil || !ok {
		t.Error("版に無いパスは (nil, true)")
	}
	if TopLevel(filepath.Join(repo, "x", "y")) != repo || TopLevel(t.TempDir()) != "" {
		t.Error("TopLevel")
	}
}

// 起こせなかったときの理由は OS のエラーの文（`[Errno 2] No such file or directory: 'git'` の形）で、git が無いのと期限切れを分けられる。
func TestRunFailures(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("文は POSIX のもの")
	}
	isolate(t)
	missing := filepath.Join(t.TempDir(), "nowhere")
	cases := []struct {
		cwd     string
		text    string
		missing bool
	}{
		{missing, "[Errno 2] No such file or directory: '" + missing + "'", true},
		{"", "[Errno 2] No such file or directory: ''", true},
		{"/etc/passwd", "[Errno 20] Not a directory: '/etc/passwd'", false},
	}
	for _, c := range cases {
		done := Run(c.cwd, []string{"status"}, Options{})
		if done.OK() || done.Code != 1 || done.Failure == nil || done.Failure.Error() != c.text || done.Missing() != c.missing {
			t.Errorf("Run(%q) = %+v", c.cwd, done)
		}
	}
	t.Setenv("PATH", t.TempDir())
	done := Run(".", []string{"status"}, Options{})
	if !done.Missing() || done.Failure.Error() != "[Errno 2] No such file or directory: 'git'" {
		t.Errorf("git が無い: %+v", done)
	}
	if code, out := Output(".", []string{"status"}, Options{}); code != 1 || out != "" {
		t.Error("Output は起こせなければ (1, \"\")")
	}
}

func TestRunTimeoutAndDeadline(t *testing.T) {
	isolate(t)
	repo := tempRoot(t)
	gitIn(t, repo, "init", "-q", "-b", "main")
	input := "x"
	done := Run(repo, []string{"hash-object", "--stdin"}, Options{Timeout: time.Nanosecond, input: &input, RawPaths: true})
	want := "Command '['git', '-c', 'core.quotePath=false', 'hash-object', '--stdin']' timed out after 1e-09 seconds"
	if !done.TimedOut() || done.Failure.Error() != want {
		t.Errorf("期限切れ: %+v", done)
	}
	if done := Run(repo, []string{"status"}, Options{Deadline: time.Now().Add(600 * time.Millisecond)}); !done.TimedOut() || done.Failure.Error() != "期限不足" {
		t.Errorf("残りが下限以下なら起こさない: %+v", done)
	}
	if done := Run(repo, []string{"hash-object", "--stdin"}, Options{Deadline: time.Now().Add(10 * time.Second), input: &input}); !done.OK() || len(strings.TrimSpace(done.Out)) != 40 {
		t.Errorf("期限が十分なら起こす: %+v", done)
	}
	// ゼロ値の期限は「期限無し」で、起こす（git.Expired も偽）。
	if Expired(time.Time{}) {
		t.Error("ゼロ値の期限は過ぎていない")
	}
	if got := Head(repo, Options{Deadline: time.Now().Add(600 * time.Millisecond)}); got != "" {
		t.Errorf("期限の足りない Head は空: %q", got)
	}
}

// 起こす git には GIT_OPTIONAL_LOCKS=0（期限で殺しても index.lock を残さない）と GIT_NO_LAZY_FETCH=1 を渡し、
// 親の環境も残す。PlainPathspecs はユーザの pathspec の読み方を外す。
func TestEnviron(t *testing.T) {
	t.Setenv(envOptionalLocks, "1")
	t.Setenv("GIT_LITERAL_PATHSPECS", "1")
	for _, plain := range []bool{false, true} {
		env := environ(plain)
		count := func(prefix string) int {
			n := 0
			for _, kv := range env {
				if strings.HasPrefix(kv, prefix) {
					n++
				}
			}
			return n
		}
		if !slices.Contains(env, envOptionalLocks+"=0") || count(envOptionalLocks+"=") != 1 ||
			!slices.Contains(env, envNoLazyFetch+"=1") || !slices.Contains(env, "PATH="+os.Getenv("PATH")) ||
			(count("GIT_LITERAL_PATHSPECS=") == 0) != plain {
			t.Errorf("plain=%v env=%v", plain, env)
		}
	}
}

func TestPorcelainArgs(t *testing.T) {
	cases := []struct {
		p    Porcelain
		want string
	}{
		{Porcelain{}, "status --porcelain"},
		{Porcelain{Untracked: UntrackedAll}, "status --porcelain --untracked-files=all"},
		{Porcelain{Paths: []string{"a b"}}, "status --porcelain -- a b"},
		{Porcelain{NUL: true, Untracked: UntrackedNo, NoRenames: true}, "status --porcelain -z --untracked-files=no --no-renames"},
		{Porcelain{NUL: true, Untracked: UntrackedAll, NoRenames: true, IgnoreDirtySubmodules: true, Paths: []string{}},
			"status --porcelain -z --untracked-files=all --no-renames --ignore-submodules=dirty --"},
	}
	for _, c := range cases {
		if got := strings.Join(c.p.args(), " "); got != c.want {
			t.Errorf("%+v: %q, want %q", c.p, got, c.want)
		}
	}
}

func TestCount(t *testing.T) {
	cases := []struct {
		out string
		n   int
		ok  bool
	}{
		{"3\n", 3, true}, {" 12 ", 12, true}, {"0", 0, true}, {"", 0, false}, {"-1", 0, false}, {"+1", 0, false}, {"1a", 0, false}, {"1 2", 0, false},
	}
	for _, c := range cases {
		if n, ok := Count(c.out); n != c.n || ok != c.ok {
			t.Errorf("Count(%q) = %d, %v", c.out, n, ok)
		}
	}
}

func TestIsAncestor(t *testing.T) {
	isolate(t)
	repo := tempRoot(t)
	gitIn(t, repo, "init", "-q", "-b", "main")
	gitIn(t, repo, "commit", "-q", "--allow-empty", "-m", "a")
	first := Head(repo, Options{})
	gitIn(t, repo, "commit", "-q", "--allow-empty", "-m", "b")
	if !IsAncestor(repo, first, "HEAD", Options{}).OK() {
		t.Error("1 つめのコミットは HEAD の祖先")
	}
	if done := IsAncestor(repo, "HEAD", first, Options{}); done.Code != NotAncestorCode {
		t.Errorf("HEAD は 1 つめの祖先ではない: %+v", done)
	}
	if done := IsAncestor(repo, "nope", "HEAD", Options{}); done.OK() || done.Code == NotAncestorCode {
		t.Errorf("無い版は読めない: %+v", done)
	}
}
