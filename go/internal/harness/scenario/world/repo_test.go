package world

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/prepare"
	"ccnavi/internal/infra/ospath"
)

func refusal(t *testing.T, err error) *core.Refusal {
	t.Helper()
	var r *core.Refusal
	if !errors.As(err, &r) {
		t.Fatalf("*core.Refusal ではない: %v", err)
	}
	return r
}

func TestRefusedTargetsSayWhyAndEndWithOneNextLine(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	plain := filepath.Join(w.root, "projects", "plain")
	if err := os.MkdirAll(plain, 0o755); err != nil {
		t.Fatal(err)
	}
	other := filepath.Join(w.tmp, "other")
	makeRepo(t, other, "")
	cases := []struct{ name, target, allowed, why string }{
		{"本体そのもの", w.root, w.root, "ccnavi 本体"},
		{"リポジトリでない（本体の中）", plain, plain, "ccnavi 本体"},
		{"許可された場所と違う", other, w.target, "許可された"},
	}
	for _, tc := range cases {
		_, err := RequireDisposableRepo(w.root, tc.target, tc.allowed)
		if r := refusal(t, err); !strings.Contains(err.Error(), tc.why) {
			t.Errorf("%s: %v（%v）", tc.name, err, r)
		}
	}
	if _, err := RequireDisposableRepo(w.root, filepath.Join(w.tmp, "nothing"), ""); err == nil {
		t.Error("無い場所は断る")
	}
	_, err := RequireDisposableRepo(w.root, w.root, "")
	ls := lines(err.Error())
	if next := 0; true {
		for _, l := range ls {
			if strings.HasPrefix(l, "次: ") {
				next++
			}
		}
		if next != 1 || !strings.HasPrefix(ls[len(ls)-1], "次: ") || !strings.HasPrefix(ls[1], "案内: ") {
			t.Errorf("理由・案内:・最後に 次: 1 行: %q", ls)
		}
	}
}

func TestTheSameTargetInAnySpellingIsAccepted(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	want := ospath.Realpath(w.target)
	if got, err := RequireDisposableRepo(w.root, w.target, ""); err != nil || got != want {
		t.Fatalf("= %q %v", got, err)
	}
	// 大文字小文字の綴り違いは同じものとして比べる（どの機械でも）
	if got, err := RequireDisposableRepo(w.root, w.target, strings.ToUpper(w.target)); err == nil && got != want {
		t.Errorf("= %q", got)
	} else if err != nil && base.CanCaseSensitive() == false {
		t.Errorf("大文字小文字を区別しない機械では通る: %v", err)
	}
	if !base.CanSymlink() {
		return
	}
	link := filepath.Join(w.tmp, "link")
	if err := os.Symlink(w.target, link); err != nil {
		t.Skip("symlink を作れない")
	}
	if got, err := RequireDisposableRepo(w.root, link, w.target); err != nil || got != want {
		t.Errorf("リンク経由 = %q %v", got, err)
	}
}

func TestOriginComparison(t *testing.T) {
	t.Parallel()
	const a, b = "https://example.com/o/r.git", "https://example.com/o/t.git"
	same := newWs(t, a, "https://EXAMPLE.com/o/r/")
	if _, err := RequireDisposableRepo(same.root, same.target, ""); err == nil || !strings.Contains(err.Error(), "origin") {
		t.Errorf("origin が（綴り違いを除いて）同じなら断る: %v", err)
	}
	for name, w := range map[string]ws{"origin が違う": newWs(t, a, b), "本体に origin が無い": newWs(t, "", b)} {
		if _, err := RequireDisposableRepo(w.root, w.target, ""); err != nil {
			t.Errorf("%s: %v", name, err)
		}
	}
}

func TestALinkedWorktreeOfTheBodyIsRefusedEvenWithoutOrigin(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	if err := os.RemoveAll(w.target); err != nil {
		t.Fatal(err)
	}
	run.Git(t, w.root, "worktree", "add", "-q", w.target, "-b", "linked")
	if _, err := RequireDisposableRepo(w.root, w.target, ""); err == nil || !strings.Contains(err.Error(), "同じリポジトリ") {
		t.Errorf("= %v", err)
	}
}

func TestSetupGitRunsInTheTargetNotTheBodyAndLogsIt(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	before := RepoSnapshot(w.root)
	err := RunSetupGit(w.root, w.target, [][]string{{"git", "switch", "-q", "-c", "feat"}, {"git", "branch", "b2"}}, "", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if run.Git(t, w.target, "branch", "--show-current") != "feat" || !strings.Contains(run.Git(t, w.target, "branch"), "b2") {
		t.Error("対象で打っていない")
	}
	if RepoSnapshot(w.root) != before {
		t.Error("本体が変わった")
	}
	log := read(t, filepath.Join(w.root, "logs", "diag", "scenario-runner.log"))
	if !strings.Contains(log, "git switch -q -c feat") || !strings.Contains(log, "rc=0") {
		t.Errorf("診断ログに残らない: %s", log)
	}
}

func TestSetupGitStopsOnABadTargetAndOnAFailedCommand(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	if err := RunSetupGit(w.root, w.root, [][]string{{"git", "branch", "x"}}, w.root, nil, nil); err == nil {
		t.Error("本体は対象にできない")
	}
	if strings.Contains(run.Git(t, w.root, "branch"), "x") {
		t.Error("本体にブランチが出来た")
	}
	err := RunSetupGit(w.root, w.target, [][]string{{"git", "checkout", "nonexistent"}}, "", nil, nil)
	if r := refusal(t, err); !strings.Contains(r.What, "git checkout nonexistent") {
		t.Errorf("失敗したコマンドを名指しする: %v", err)
	}
	err = RunSetupGit(w.root, w.target, [][]string{{"git", "-C", "/", "status"}}, "", nil, nil)
	if r := refusal(t, err); !strings.Contains(r.What, "setup.git を実行しない") {
		t.Errorf("= %v", err)
	}
}

func TestDropGitVarsLeavesTheTargetToBeDecidedByTheWorkingDirectory(t *testing.T) {
	t.Parallel()
	got := DropGitVars([]string{"GIT_DIR=/x", "GIT_WORK_TREE=/y", "GIT_INDEX_FILE=z", "GIT_CONFIG_PARAMETERS=p", "PATH=/bin", "GIT_AUTHOR_NAME=a"})
	if strings.Join(got, ",") != "PATH=/bin,GIT_AUTHOR_NAME=a" {
		t.Errorf("= %v", got)
	}
}

func TestAppendTargetsAreRefusedUnlessTrackedInsideTheTarget(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	write(t, filepath.Join(w.target, "untracked.txt"), "u")
	if err := os.Mkdir(filepath.Join(w.target, "sub"), 0o755); err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(w.root, "outside.txt"), "o")
	write(t, filepath.Join(w.target, "docs1.md"), "t")
	run.Git(t, w.target, "add", "docs1.md")
	if base.CanSymlink() {
		if err := os.Symlink(filepath.Join(w.root, "outside.txt"), filepath.Join(w.target, "escape.txt")); err == nil {
			run.Git(t, w.target, "add", "escape.txt")
		}
	}
	top, err := RequireDisposableRepo(w.root, w.target, "")
	if err != nil {
		t.Fatal(err)
	}
	refused := []string{
		"projects/test-repo/../../outside.txt", "outside.txt", "projects/test-repo/none.md", "projects/test-repo/untracked.txt",
		"projects/test-repo/sub", "README.md", filepath.Join(w.target, "README.md"),
	}
	if exists(filepath.Join(w.target, "escape.txt")) {
		refused = append(refused, "projects/test-repo/escape.txt")
	}
	if os.WriteFile(filepath.Join(w.target, "docs*.md"), []byte("u"), 0o644) == nil {
		refused = append(refused, "projects/test-repo/docs*.md") // glob ではなく文字どおりに比べる
	}
	for _, rel := range refused {
		hooks := prepare.Hooks{ResolveAppend: ResolveAppend, CommitExists: CommitExists}
		before := read(t, filepath.Join(w.target, "README.md"))
		_, _, err := prepare.MakeFiles(hooks, []core.File{{Rel: rel, Body: "追記\n", Append: true}}, w.root, top)
		refusal(t, err)
		if read(t, filepath.Join(w.target, "README.md")) != before || read(t, filepath.Join(w.root, "outside.txt")) != "o" {
			t.Errorf("%s: 断ったのに書いた", rel)
		}
	}
	if got, err := ResolveAppend("projects/test-repo/README.md", w.root, top); err != nil || got != ospath.Realpath(filepath.Join(w.target, "README.md")) {
		t.Errorf("管理下のファイルは通る: %q %v", got, err)
	}
}

func TestResetRestoresEverythingButIgnoredFilesAndRemoteRefs(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	w.dirty(t)
	if err := ResetRepo(w.root, w.target, w.base, ""); err != nil {
		t.Fatal(err)
	}
	g := func(args ...string) string { return run.Git(t, w.target, args...) }
	checks := map[string][2]string{
		"HEAD":         {g("rev-parse", "HEAD"), w.base},
		"main":         {g("rev-parse", "main"), w.base},
		"現在のブランチ":      {g("branch", "--show-current"), "main"},
		"ローカルブランチ":     {g("for-each-ref", "--format=%(refname:short)", "refs/heads"), "main"},
		"タグ":           {g("tag", "-l"), ""},
		"stash":        {g("stash", "list"), ""},
		"ワークツリー":       {strings.Join(lines(g("worktree", "list")), "\n")[:0], ""},
		"status":       {g("status", "--porcelain"), ""},
		"README":       {read(t, filepath.Join(w.target, "README.md")), "元の中身\n"},
		"remote の ref": {g("for-each-ref", "--format=%(refname)", "refs/remotes"), "refs/remotes/origin/keep"},
	}
	for name, c := range checks {
		if c[0] != c[1] {
			t.Errorf("%s = %q, want %q", name, c[0], c[1])
		}
	}
	if n := len(lines(g("worktree", "list"))); n != 1 {
		t.Errorf("ワークツリーが %d 本ある", n)
	}
	if exists(filepath.Join(w.target, "untracked.txt")) || !exists(filepath.Join(w.target, "ignored.log")) {
		t.Error("clean に -x を付けない（無視済みは残し、未追跡は消す）")
	}
}

// ccnaviState は ccnavi が対象の .git に書く記録・marker・ref を作る（ブランチも切る）。
func ccnaviState(t *testing.T, w ws, name string) string {
	t.Helper()
	gitdir := filepath.Join(w.target, ".git")
	write(t, filepath.Join(gitdir, "ccnavi-direct", name), "version 4\n")
	write(t, filepath.Join(gitdir, "ccnavi-integrate", name, "marker"), "m\n")
	write(t, filepath.Join(gitdir, "ccnavi-integrate-done", name), "done\n")
	run.Git(t, w.target, "branch", name)
	run.Git(t, w.target, "update-ref", "refs/ccnavi/direct/"+name+"/s", w.base)
	run.Git(t, w.target, "update-ref", "refs/ccnavi/direct/"+name+"/pending", w.base)
	return gitdir
}

func TestResetRemovesTheArchiveOfTheTargetOnly(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	mine := filepath.Join(w.root, "logs", "archive", "test-repo", "done", "f.md")
	other := filepath.Join(w.root, "logs", "archive", "self", "done", "f.md")
	write(t, mine, "done\n")
	write(t, other, "done\n")
	if err := ResetRepo(w.root, w.target, w.base, ""); err != nil {
		t.Fatal(err)
	}
	if exists(filepath.Join(w.root, "logs", "archive", "test-repo")) || !exists(other) {
		t.Error("対象の退避だけを消す")
	}
}

func TestResetRemovesCcnaviOwnRecordsMarkersAndRefs(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	gitdir := ccnaviState(t, w, "work1")
	write(t, filepath.Join(w.target, "logs", "keep.log"), "判定の記録\n")
	write(t, filepath.Join(w.target, ".gitignore"), "logs/\n")
	run.Git(t, w.target, "add", ".gitignore")
	run.Git(t, w.target, "commit", "-q", "-m", "ignore logs")
	base := run.Git(t, w.target, "rev-parse", "HEAD")
	if err := ResetRepo(w.root, w.target, base, ""); err != nil {
		t.Fatal(err)
	}
	for _, n := range []string{"ccnavi-direct", "ccnavi-integrate", "ccnavi-integrate-done"} {
		if exists(filepath.Join(gitdir, n)) {
			t.Errorf("%s が残っている", n)
		}
	}
	if run.Git(t, w.target, "for-each-ref", "refs/ccnavi") != "" || run.Git(t, w.target, "for-each-ref", "--format=%(refname:short)", "refs/heads") != "main" {
		t.Error("ref とブランチが残っている")
	}
	if !exists(filepath.Join(w.target, "logs", "keep.log")) {
		t.Error("ignore 済みは残す")
	}
}

func TestResetLeavesNothingPrunableAndKeepsRecordsWhenAWorktreeCannotBeRemoved(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	gitdir := ccnaviState(t, w, "work1")
	dir := filepath.Join(w.root, "logs", "direct-tmp", "test-repo")
	alive, gone := filepath.Join(dir, "work1"), filepath.Join(dir, "work2")
	run.Git(t, w.target, "worktree", "add", "-q", "--detach", alive, w.base)
	run.Git(t, w.target, "worktree", "add", "-q", "--detach", gone, w.base)
	if err := os.RemoveAll(gone); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(run.Git(t, w.target, "worktree", "list", "--porcelain"), "prunable") {
		t.Fatal("prunable な登録を作れていない")
	}
	if err := ResetRepo(w.root, w.target, w.base, ""); err != nil {
		t.Fatal(err)
	}
	if n := len(lines(run.Git(t, w.target, "worktree", "list"))); n != 1 || strings.Contains(run.Git(t, w.target, "worktree", "list", "--porcelain"), "prunable") || exists(alive) {
		t.Errorf("一時ワークツリーが残っている（%d 本）", n)
	}

	// 消せないワークツリーがあれば止まり、記録は先に消さない
	w2 := newWs(t, "", "")
	gitdir = ccnaviState(t, w2, "work1")
	locked := filepath.Join(w2.root, "logs", "direct-tmp", "test-repo", "work1")
	run.Git(t, w2.target, "worktree", "add", "-q", "--detach", locked, w2.base)
	run.Git(t, w2.target, "worktree", "lock", locked)
	refusal(t, ResetRepo(w2.root, w2.target, w2.base, ""))
	if !exists(filepath.Join(gitdir, "ccnavi-direct", "work1")) || !strings.Contains(run.Git(t, w2.target, "for-each-ref", "refs/ccnavi"), "refs/ccnavi/direct/work1/s") {
		t.Error("ワークツリーが消せずに止まったのに、記録を先に消した")
	}
}

func TestResetNeverTouchesTheBody(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	gitdir := filepath.Join(w.root, ".git")
	write(t, filepath.Join(gitdir, "ccnavi-direct", "mine"), "x\n")
	run.Git(t, w.root, "update-ref", "refs/ccnavi/direct/mine/s", "HEAD")
	run.Git(t, w.root, "branch", "keep-me")
	refusal(t, ResetRepo(w.root, w.root, w.base, w.root))
	if !exists(filepath.Join(gitdir, "ccnavi-direct", "mine")) || !strings.Contains(run.Git(t, w.root, "for-each-ref", "refs/ccnavi"), "mine/s") || !strings.Contains(run.Git(t, w.root, "branch"), "keep-me") {
		t.Error("本体の状態を触った")
	}
}

func TestResetMovesMainBackWhenItPointsElsewhere(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	g := func(args ...string) string { return run.Git(t, w.target, args...) }
	g("switch", "-q", "-c", "side")
	write(t, filepath.Join(w.target, "s.txt"), "s")
	g("add", "s.txt")
	g("commit", "-q", "-m", "side")
	g("branch", "-f", "main", "side")
	if err := ResetRepo(w.root, w.target, w.base, ""); err != nil {
		t.Fatal(err)
	}
	if g("rev-parse", "main") != w.base || g("branch", "--show-current") != "main" {
		t.Error("main を base に合わせていない")
	}
}

func TestResetWithAnUnknownBaseChangesNothing(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	w.dirty(t)
	refusal(t, ResetRepo(w.root, w.target, strings.Repeat("f", 40), ""))
	if read(t, filepath.Join(w.target, "README.md")) != "元の中身\n未コミット\n" {
		t.Error("base が無いのに作業ツリーを触った")
	}
}

func TestTheLockIsRefusedWhileHeldAndAMarkerBlocksAnotherRun(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	top := ospath.Realpath(w.target)
	lock, err := AcquireTarget(top, w.out, false)
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Dir(lock) != ospath.Realpath(filepath.Join(w.target, ".git")) || strings.TrimSpace(read(t, lock)) == "" {
		t.Errorf("ロックは .git の中で、中身は PID: %s", lock)
	}
	if run.Git(t, w.target, "status", "--porcelain") != "" {
		t.Error("ロックが status を汚す")
	}
	run.Git(t, w.target, "clean", "-fd")
	if !exists(lock) {
		t.Error("git clean でロックが消えた")
	}
	if _, err := AcquireTarget(top, w.out, false); err == nil || !strings.Contains(err.Error(), "別の回が使っている") || !strings.Contains(err.Error(), "を消す") {
		t.Errorf("持っているあいだは断り、消し方を言う: %v", err)
	}
	// 中身が PID として読めなくても、あれば断る（生死を調べて奪わない）
	write(t, lock, "")
	if _, err := AcquireTarget(top, w.out, false); err == nil {
		t.Error("読めないロックも断る")
	}
	st := &core.State{Target: &core.Target{Path: top}, Out: w.out}
	if msg := ReleaseTarget(st, w.root, false, func(string) {}); msg != "" || !st.Released || exists(lock) {
		t.Errorf("放せば入れる: %q %v", msg, st.Released)
	}
	if _, err := AcquireTarget(top, w.out, false); err != nil {
		t.Errorf("放したあとは入れる: %v", err)
	}
}

func TestResumingMatchesTheSameOutInAnySpellingAndTheMarkerHoldsTheRealpath(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	top := ospath.Realpath(w.target)
	if err := os.Mkdir(w.out, 0o755); err != nil {
		t.Fatal(err)
	}
	marker := filepath.Join(w.target, ".git", LeftName)
	lock := filepath.Join(w.target, ".git", LockName)
	spellings := []string{w.out, w.out + string(os.PathSeparator), filepath.Join(w.out, "..", "out")}
	if base.CanSymlink() {
		link := filepath.Join(w.tmp, "link")
		if os.Symlink(w.out, link) == nil {
			spellings = append(spellings, link)
		}
	}
	write(t, marker, ospath.Realpath(w.out))
	for _, s := range spellings {
		if _, err := AcquireTarget(top, s, true); err != nil {
			t.Errorf("同じ出力の置き場 %s なら続けられる: %v", s, err)
		}
		_ = os.Remove(lock)
	}
	if _, err := AcquireTarget(top, filepath.Join(w.tmp, "other"), true); err == nil || !strings.Contains(err.Error(), "restore") {
		t.Errorf("別の回なら、続けるにも断り restore を案内する: %v", err)
	}
	if _, err := AcquireTarget(top, w.out, false); err == nil {
		t.Error("続けるのでなければ断る")
	}
	_ = os.Remove(marker)
	if _, err := AcquireTarget(top, w.out, false); err != nil {
		t.Fatal(err)
	}
	st := &core.State{Target: &core.Target{Path: top}, Out: w.out}
	ReleaseTarget(st, w.root, true, func(string) {})
	if read(t, marker) != ospath.Realpath(w.out) || !st.Held {
		t.Errorf("残し印は実パス: %q", read(t, marker))
	}
}

func TestReleaseWithAFailedResetKeepsTheMarkerAndSaysHowToRestore(t *testing.T) {
	t.Parallel()
	w := newWs(t, "", "")
	top := ospath.Realpath(w.target)
	if _, err := AcquireTarget(top, w.out, false); err != nil {
		t.Fatal(err)
	}
	locked := filepath.Join(w.root, "logs", "wt")
	run.Git(t, w.target, "worktree", "add", "-q", "--detach", locked, w.base)
	run.Git(t, w.target, "worktree", "lock", locked)
	var said []string
	st := &core.State{Target: &core.Target{Path: top, Base: w.base}, Out: w.out}
	msg := ReleaseTarget(st, w.root, false, func(s string) { said = append(said, s) })
	for _, want := range []string{"手作業で戻す必要がある", top, "次: ", "restore"} {
		if !strings.Contains(msg, want) {
			t.Errorf("文面に %q が無い: %s", want, msg)
		}
	}
	if len(said) != 1 || st.Released || !exists(filepath.Join(w.target, ".git", LeftName)) || exists(filepath.Join(w.target, ".git", LockName)) {
		t.Error("戻せなければ残し印を書き、ロックは放す")
	}
	if _, err := AcquireTarget(top, filepath.Join(w.tmp, "out2"), false); err == nil {
		t.Error("戻せていない対象では、次の回を始めさせない")
	}
}
