package git

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
	"ccnavi/internal/infra/ospath"
)

// changedFiles は HEAD の木と C の木の差のファイル名（辞書順）。
func (f *itg) changedFiles(cTree string) []string {
	f.t.Helper()
	out := strings.Fields(git(f.t, f.ws, "diff-tree", "-r", "--name-only", "HEAD^{tree}", cTree))
	sort.Strings(out)
	return out
}

func sameList(a, b []string) bool { return strings.Join(a, "\x00") == strings.Join(b, "\x00") }

// ---- integrate

func TestIntegratePreparesCInATempWorktreeWithoutTouchingTheRoot(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.advanceMain("other.txt", "y\n")
	shkit.Write(t, f.p("mine.txt"), "user untracked\n")
	shkit.Write(t, f.p("other.txt"), "y\nedited\n")
	git(t, f.ws, "add", "other.txt")
	before := f.snapshot()
	r := f.code(f.sh("integrate", "wt"))
	f.eq(f.snapshot(), before, "ルートの状態")
	contains(t, r.Stdout, "取り込み結果を一時ワークツリーに用意した（3 ファイル", "変わるファイル:", "  keep.txt", "  new.txt", "  gone.txt", "保存していない編集")
	notContains(t, r.Stdout, "  other.txt", "取り込まない下書き") // 起点のままの下書き（wip/handoff/d.md）は並べない
	if !regexp.MustCompile(`(?m)^案内: .*integrate-done wt -m`).MatchString(r.Stdout) {
		t.Errorf("案内が無い:\n%s", r.Stdout)
	}
	if n := strings.Count("\n"+r.Stdout, "\n次:"); n != 1 {
		t.Errorf("次: の行が %d 本", n)
	}
	tmp := f.tmpWT("wt", "self")
	f.eq(shkit.Read(t, filepath.Join(tmp, "keep.txt")), "1\n2\nthree\n", "keep.txt")
	f.mustNotExist(filepath.Join(tmp, "gone.txt"), "gone.txt")
	f.eq(shkit.Read(t, filepath.Join(tmp, "other.txt")), "y\n", "other.txt")
	contains(t, shkit.Read(t, f.marker("wt")), "version 4")
	f.eq(gitT(t, tmp, "write-tree"), f.markerValue("c_tree", "wt"), "c_tree")
	if f.ref("t", "wt") == "" || f.ref("c", "wt") == "" {
		t.Error("t か c の参照が無い")
	}
	// 差分はエージェントの分だけ（ルートの staged の編集も、進んだ main も入らない）。
	if got := f.changedFiles(f.markerValue("c_tree", "wt")); !sameList(got, []string{"gone.txt", "keep.txt", "new.txt"}) {
		t.Errorf("changed = %v", got)
	}
}

func TestIntegrateASamePlaceCommitByTheUserIsRefusedWithPaths(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.advanceMain("keep.txt", "1\n2\nTHREE\n")
	before := f.snapshot()
	r := f.code(f.sh("integrate", "wt"), 1)
	contains(t, r.Stdout, "衝突した", "keep.txt", "merge main")
	f.eq(f.snapshot(), before, "ルートの状態")
	f.mustNotExist(f.tmpWT("wt", "self"), "一時ワークツリー")
	f.mustNotExist(f.marker("wt"), "marker")
}

func TestIntegrateNoDiffIsReportedAndDoneOnlyCleans(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.code(f.sh("integrate", "--abort", "wt"), 2)
	contains(t, f.code(f.sh("integrate", "wt")).Stdout, "取り込む差分は無かった")
	before := f.head()
	f.code(f.sh("integrate-done", "wt"))
	f.eq(f.head(), before, "HEAD")
	if f.hasBranch("wt") {
		t.Error("ブランチが残っている")
	}
	f.mustNotExist(f.wt, "ワークツリー")
	f.mustNotExist(f.marker("wt"), "marker")
}

// 取り込み先のブランチを開いていなければ、integrate も integrate-done も断る。
func TestIntegrateRefusesWhenTheBaseBranchChanged(t *testing.T) {
	t.Parallel()
	for _, stage := range []string{"integrate", "integrate-done"} {
		t.Run(stage, func(t *testing.T) {
			t.Parallel()
			f := newItg(t, true)
			f.workInBranch()
			if stage == "integrate-done" {
				f.code(f.sh("integrate", "wt"))
			}
			git(t, f.ws, "checkout", "-q", "-b", "elsewhere")
			args := []string{"integrate", "wt"}
			if stage == "integrate-done" {
				args = []string{"integrate-done", "wt", "-m", "feat: x"}
			}
			r := f.code(f.sh(args...), 2)
			contains(t, r.Stderr, "取り込み先（main）を開いていません")
			if stage == "integrate" {
				contains(t, r.Stderr, "elsewhere")
			}
		})
	}
}

func TestIntegrateDoneAndCleanupPassWithAListedDraft(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.commitDraft("wip/handoff/notes.md", "memo\n")
	f.code(f.sh("integrate", "wt"))
	r := f.code(f.sh("integrate-done", "wt", "-m", "feat: x"))
	notContains(t, r.Stdout, "取り込まない下書き")
	f.mustNotExist(f.wt, "ワークツリー")
	if f.hasBranch("wt") {
		t.Error("ブランチが残っている")
	}
}

func TestIntegrateAnOldGitIsRefused(t *testing.T) {
	t.Parallel()
	f := newItg(t, false)
	realGit, err := lookGit()
	if err != nil {
		t.Skip("git の場所が分からない")
	}
	shim := filepath.Join(f.tmp, "oldgit")
	shkit.WriteExec(t, filepath.Join(shim, "git"), "#!/bin/sh\nif [ \"$1\" = version ]; then echo \"git version 2.39.0\"; exit 0; fi\nexec '"+shkit.Slash(realGit)+"' \"$@\"\n")
	f.path = shim + string(os.PathListSeparator) + os.Getenv("PATH")
	f.workInBranch()
	r := f.code(f.sh("integrate", "wt"), 2)
	contains(t, r.Stderr, "2.40")
}

// 知らないファイルがある一時ワークツリーは捨てない。
func TestIntegrateAReadyMadeTempWorktreeWithUnknownFilesIsNotThrownAway(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	mystery := filepath.Join(f.tmpWT("wt", "self"), "mystery.txt")
	shkit.Write(t, mystery, "somebody's file\n")
	r := f.code(f.sh("integrate", "wt"), 2)
	contains(t, r.Stderr, "片付けられません")
	f.eq(shkit.Read(t, mystery), "somebody's file\n", "mystery.txt")
}

func TestIntegrateAgainRebuildsTheTempWorktree(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	shkit.Write(t, filepath.Join(f.wt, "more.txt"), "more\n")
	git(t, f.wt, "add", ".")
	git(t, f.wt, "commit", "-q", "-m", "more")
	f.code(f.sh("integrate", "wt"))
	f.eq(shkit.Read(t, filepath.Join(f.tmpWT("wt", "self"), "more.txt")), "more\n", "more.txt")
}

// 一時ワークツリーで確かめるときにできた生成物（__pycache__・node_modules・out）は、integrate-done を止めない。
func TestIntegrateTheTempWorktreeCanBeVerifiedAndGeneratedFilesDoNotBlock(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	shkit.Write(t, filepath.Join(f.wt, "package.json"), "{}\n")
	git(t, f.wt, "add", ".")
	git(t, f.wt, "commit", "-q", "-m", "package")
	f.code(f.sh("integrate", "wt"))
	tmp := f.tmpWT("wt", "self")
	shkit.Write(t, filepath.Join(tmp, "__pycache__", "x.pyc"), "bytecode")
	shkit.Write(t, filepath.Join(tmp, "node_modules", "m", "index.js"), "m")
	shkit.Write(t, filepath.Join(tmp, "out", "bundle.js"), "b")
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"))
	f.mustNotExist(tmp, "一時ワークツリー")
}

// ---- integrate-done

func TestIntegrateDoneLandsOneCommitAndCleansUp(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.advanceMain("other.txt", "y\n")
	before := f.head()
	f.code(f.sh("integrate", "wt"))
	noMsg := f.code(f.sh("integrate-done", "wt"), 2)
	contains(t, noMsg.Stderr, "-m <メッセージ>")
	done := f.code(f.sh("integrate-done", "wt", "-m", "feat: 取り込む"))
	contains(t, done.Stdout, "取り込みました", "ok  git integrate-done  wt を片付けた")
	f.eq(strings.Fields(git(t, f.ws, "log", "-1", "--format=%P"))[0], before, "親")
	f.eq(gitT(t, f.ws, "log", "-1", "--format=%s"), "feat: 取り込む", "件名")
	f.eq(f.status(), "", "status")
	f.eq(shkit.Read(t, f.p("keep.txt")), "1\n2\nthree\n", "keep.txt")
	f.eq(shkit.Read(t, f.p("other.txt")), "y\n", "other.txt")
	f.mustNotExist(f.p("gone.txt"), "gone.txt")
	f.mustNotExist(f.wt, "ワークツリー")
	f.mustNotExist(f.tmpWT("wt", "self"), "一時ワークツリー")
	f.mustNotExist(f.marker("wt"), "marker")
	f.mustNotExist(f.record("wt"), "記録")
	if f.hasBranch("wt") {
		t.Error("ブランチが残っている")
	}
	for _, kind := range []string{"s", "t", "c", "k"} {
		f.eq(f.ref(kind, "wt"), "", "ref "+kind)
	}
	contains(t, shkit.Read(t, filepath.Join(f.commonDir(""), "ccnavi-integrate-done", "wt")), "version 4", "k ", "s ", "t ", "c_tree ", "head ", "base_branch main")
	// origin/main より進んでいるので、push はユーザに頼む。
	contains(t, done.Stdout, "次: ユーザに", "git push origin main")
	contains(t, f.code(f.sh("integrate-done", "wt")).Stdout, "片付け済み")
}

func TestIntegrateTheUsersOtherWorkIsCarriedOver(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	shkit.Write(t, f.p("other.txt"), "x\nuser edit\n")
	shkit.Write(t, f.p("staged.txt"), "staged\n")
	git(t, f.ws, "add", "staged.txt")
	shkit.Write(t, f.p("fresh.txt"), "untracked\n")
	f.code(f.sh("integrate", "wt"))
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"))
	f.eq(shkit.Read(t, f.p("other.txt")), "x\nuser edit\n", "other.txt")
	f.eq(shkit.Read(t, f.p("fresh.txt")), "untracked\n", "fresh.txt")
	lines := strings.Split(f.status(), "\n")
	for _, want := range []string{"A  staged.txt", " M other.txt", "?? fresh.txt"} {
		found := false
		for _, l := range lines {
			found = found || l == want
		}
		if !found {
			t.Errorf("status に %q が無い: %v", want, lines)
		}
	}
}

func TestIntegrateTheSameFileUncommittedStopsWithoutChangesAndResumes(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	shkit.Write(t, f.p("keep.txt"), "1\n2\nmine\n")
	head := f.head()
	r := f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 1)
	contains(t, r.Stdout, "keep.txt")
	f.eq(f.head(), head, "HEAD")
	f.eq(shkit.Read(t, f.p("keep.txt")), "1\n2\nmine\n", "keep.txt")
	if !f.hasBranch("wt") {
		t.Error("ブランチが消えた")
	}
	contains(t, shkit.Read(t, f.marker("wt")), "k ")
	// K が待っている間は、integrate を打ち直させない。
	contains(t, f.code(f.sh("integrate", "wt"), 2).Stderr, "integrate-done")
	// 退避してもらって打ち直すと、K から再開して完了する。
	git(t, f.ws, "checkout", "--", "keep.txt")
	f.code(f.sh("integrate-done", "wt"))
	f.eq(shkit.Read(t, f.p("keep.txt")), "1\n2\nthree\n", "keep.txt")
	f.eq(len(strings.Fields(git(t, f.ws, "rev-list", head+"..HEAD"))), 1, "載ったコミット")
	if f.hasBranch("wt") {
		t.Error("ブランチが残っている")
	}
}

// ignored のファイル・ディレクトリ・グローバルの excludes で ignored のファイルには、取り込みで上書きしない。integrate が重なりを知らせ、
// integrate-done は何も載せずに止まる。
func TestIntegrateAnIgnoredPathIsNotOverwritten(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name, rel, user string
		prepare         func(f *itg) map[string]string
	}{
		{"ファイル", "added.local", "user's ignored file\n", func(f *itg) map[string]string { return nil }},
		{"ディレクトリ", "gen/a.txt", "user's\n", func(f *itg) map[string]string {
			shkit.Write(f.t, f.p(".git/info/exclude"), "gen/\n")
			return nil
		}},
		{"グローバルの excludes", "added.gignored", "user's globally ignored file\n", func(f *itg) map[string]string {
			excludes := shkit.Write(f.t, filepath.Join(f.tmp, "global-excludes"), "*.gignored\n")
			config := shkit.Write(f.t, filepath.Join(f.tmp, "global-gitconfig"), "[core]\n\texcludesFile = "+shkit.Slash(excludes)+"\n")
			return map[string]string{"GIT_CONFIG_GLOBAL": config}
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			f := newItg(t, true)
			env := tc.prepare(f)
			shkit.Write(t, filepath.Join(f.wt, filepath.FromSlash(tc.rel)), "agent\n")
			git(t, f.wt, "add", "-f", tc.rel)
			git(t, f.wt, "commit", "-q", "-m", "tracked despite ignore")
			shkit.Write(t, f.p(tc.rel), tc.user)
			named := f.code(f.shEnv(env, "integrate", "wt"))
			contains(t, named.Stdout, "ignored のファイルと重なっている", filepath.Base(tc.rel))
			head := f.head()
			f.code(f.shEnv(env, "integrate-done", "wt", "-m", "feat: x"), 1)
			f.eq(f.head(), head, "HEAD")
			f.eq(shkit.Read(t, f.p(tc.rel)), tc.user, "ユーザのファイル")
			f.mustExist(f.marker("wt"), "marker")
		})
	}
}

func TestIntegrateAMovedHeadAsksToStartOver(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	f.advanceMain("other.txt", "y\n")
	r := f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 2)
	contains(t, r.Stderr, "integrate --abort wt")
	if !f.hasBranch("wt") {
		t.Error("ブランチが消えた")
	}
}

func TestIntegrateAMovedBranchTipAsksToStartOver(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	shkit.Write(t, filepath.Join(f.wt, "late.txt"), "late\n")
	contains(t, f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 2).Stderr, "未コミットの変更")
	git(t, f.wt, "add", ".")
	git(t, f.wt, "commit", "-q", "-m", "late")
	contains(t, f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 2).Stderr, "動いています")
}

func TestIntegrateAHookThatRejectsTheCommitRecordsNothing(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	f.hook("pre-commit", "echo 'no commits today' >&2\nexit 1\n")
	head := f.head()
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 1)
	f.eq(f.head(), head, "HEAD")
	notContains(t, shkit.Read(t, f.marker("wt")), "k ")
	f.eq(f.ref("k", "wt"), "", "ref k")
}

func TestIntegrateAHookThatChangesTheContentRecordsNothing(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	f.hook("pre-commit", "echo hook >hooked.txt\ngit add hooked.txt\n")
	head := f.head()
	r := f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 1)
	contains(t, r.Stdout, "フック")
	f.eq(f.head(), head, "HEAD")
	notContains(t, shkit.Read(t, f.marker("wt")), "k ")
	f.eq(f.ref("k", "wt"), "", "ref k")
	// 一時ワークツリーは C に戻してあるので、フックを直せば打ち直せる。
	shkit.Remove(t, filepath.Join(f.commonDir(""), "hooks", "pre-commit"))
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"))
}

func TestIntegrateAKThatDoesNotMatchTheRecordIsNotAdopted(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	stray := f.strayCommit("HEAD^{tree}", "HEAD", "stray")
	shkit.Write(t, f.marker("wt"), shkit.Read(t, f.marker("wt"))+"k "+stray+"\n")
	head := f.head()
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 2)
	f.eq(f.head(), head, "HEAD")
}

// 片付けが止まっても、K が載ったあとはユーザがコミットしても打ち直せば完了する。載ったあとは --abort で戻せない（revert を案内する）。
func TestIntegrateACleanupThatStopsResumesEvenAfterTheUserCommitted(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	mystery := filepath.Join(f.tmpWT("wt", "self"), "mystery.txt")
	shkit.Write(t, mystery, "somebody's file\n")
	r := f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 1)
	contains(t, r.Stdout, "取り込みました")
	f.eq(shkit.Read(t, mystery), "somebody's file\n", "mystery.txt")
	contains(t, f.code(f.sh("integrate", "--abort", "wt"), 2).Stderr, "git revert")
	f.advanceMain("other.txt", "z\n")
	shkit.Remove(t, mystery)
	f.code(f.sh("integrate-done", "wt"))
	f.mustNotExist(f.tmpWT("wt", "self"), "一時ワークツリー")
	f.mustNotExist(f.marker("wt"), "marker")
	if f.hasBranch("wt") {
		t.Error("ブランチが残っている")
	}
}

func TestIntegrateACleanupAfterARevertStillCompletes(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	mystery := filepath.Join(f.tmpWT("wt", "self"), "mystery.txt")
	shkit.Write(t, mystery, "x\n")
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 1)
	git(t, f.ws, "revert", "--no-edit", "HEAD")
	shkit.Remove(t, mystery)
	f.code(f.sh("integrate-done", "wt"))
	if f.hasBranch("wt") {
		t.Error("ブランチが残っている")
	}
}

// 自己防衛の対象（共通設定の rules.yml・プロジェクトの設定）を含む取り込みは、ターミナルでユーザが打つ。
func TestIntegrateAGuardedFileNeedsTheTerminal(t *testing.T) {
	t.Parallel()
	t.Run("ワークスペース", func(t *testing.T) {
		t.Parallel()
		f := newItg(t, true)
		rel := ".ccnavi/config/common/rules.yml"
		shkit.Write(t, filepath.Join(f.wt, filepath.FromSlash(rel)), "ask: []\n")
		git(t, f.wt, "add", "-f", rel)
		git(t, f.wt, "commit", "-q", "-m", "rules")
		contains(t, f.code(f.sh("integrate", "wt")).Stdout, "自己防衛")
		head := f.head()
		done := f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 2)
		contains(t, done.Stderr, "ターミナル")
		f.eq(f.head(), head, "HEAD")
	})
	t.Run("プロジェクト", func(t *testing.T) {
		t.Parallel()
		f := newItg(t, true)
		project := f.makeProject("p")
		wt := f.cut("pwt", project, "main")
		rel := ".ccnavi/config/project/rules.yml"
		shkit.Write(t, filepath.Join(wt, filepath.FromSlash(rel)), "ask: []\n")
		git(t, wt, "add", "-f", rel)
		git(t, wt, "commit", "-q", "-m", "rules")
		r := f.code(f.shIn(project, nil, "integrate", "pwt"))
		contains(t, r.Stdout, "自己防衛", rel)
		head := gitT(t, project, "rev-parse", "HEAD")
		done := f.code(f.shIn(project, nil, "integrate-done", "pwt", "-m", "feat: p"), 2)
		contains(t, done.Stderr, "ターミナル")
		f.eq(gitT(t, project, "rev-parse", "HEAD"), head, "プロジェクトの HEAD")
	})
}

func TestIntegrateACleanupKeepsTheBranchWhenItMoved(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	mystery := filepath.Join(f.tmpWT("wt", "self"), "mystery.txt")
	shkit.Write(t, mystery, "x\n")
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 1)
	shkit.Remove(t, mystery)
	// ブランチを動かす（取り込んだあとに積んだコミット）。
	tip := gitT(t, f.ws, "rev-parse", "wt")
	git(t, f.ws, "update-ref", "refs/heads/wt", f.strayCommit(tip+"^{tree}", tip, "late"))
	f.code(f.sh("integrate-done", "wt"), 1)
	if !f.hasBranch("wt") {
		t.Error("動いたブランチを消した")
	}
}

// ---- abort

func TestIntegrateAbortBeforeDoneRemovesTheTempWorktreeAndRefsOnly(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	tip := gitT(t, f.ws, "rev-parse", "wt")
	before := f.snapshot()
	f.code(f.sh("integrate", "--abort", "wt"))
	f.eq(f.snapshot(), before, "ルートの状態")
	f.mustNotExist(f.tmpWT("wt", "self"), "一時ワークツリー")
	f.mustNotExist(f.marker("wt"), "marker")
	f.eq(f.ref("t", "wt"), "", "ref t")
	f.eq(f.ref("c", "wt"), "", "ref c")
	if f.ref("s", "wt") == "" {
		t.Error("s の参照が消えた")
	}
	if !shkit.IsFile(f.record("wt")) || !shkit.IsDir(f.wt) {
		t.Error("記録かワークツリーが消えた")
	}
	f.eq(gitT(t, f.ws, "rev-parse", "wt"), tip, "ブランチの先")
}

func TestIntegrateAbortAfterKWasMadeButNotLanded(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	shkit.Write(t, f.p("keep.txt"), "1\n2\nmine\n")
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 1)
	if f.ref("k", "wt") == "" {
		t.Fatal("K ができていない")
	}
	f.code(f.sh("integrate", "--abort", "wt"))
	f.eq(f.ref("k", "wt"), "", "ref k")
	f.eq(shkit.Read(t, f.p("keep.txt")), "1\n2\nmine\n", "keep.txt")
	f.mustNotExist(f.tmpWT("wt", "self"), "一時ワークツリー")
}

// ---- 並行

func TestIntegrateTwoOperationsAtOnceOneIsRefusedByTheLock(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	started := filepath.Join(f.tmp, "hook-started")
	f.hook("pre-commit", ": >'"+shkit.Slash(started)+"'\nsleep 2\n")
	slow := make(chan run.Result, 1)
	go func() {
		slow <- f.sh("integrate-done", "wt", "-m", "feat: x")
	}()
	// integrate-done がロックを持ったままフックに入るまで待つ。
	if !waitFor(started, 90*time.Second) {
		t.Fatal("フックが始まらない")
	}
	contains(t, f.code(f.sh("integrate", "wt"), 2).Stderr, "動いています")
	if r := <-slow; r.Code != 0 {
		t.Errorf("遅いほうの code = %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
}

// ---- 境界

// makeProject は projects/<name> に、コミットと origin/HEAD を持つプロジェクトのリポジトリを作る。
func (f *itg) makeProject(name string) string {
	f.t.Helper()
	project := f.p("projects/" + name)
	seedRepoWithFile(f.t, project, "a.txt", "a\n", "seed "+name)
	git(f.t, project, "update-ref", "refs/remotes/origin/main", "HEAD")
	git(f.t, project, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
	return project
}

func seedRepoWithFile(t *testing.T, repo, name, text, msg string) {
	t.Helper()
	initRepo(t, repo, "main")
	shkit.Write(t, filepath.Join(repo, name), text)
	git(t, repo, "add", ".")
	git(t, repo, "commit", "-q", "-m", msg)
}

func (f *itg) projectWork(wt string) {
	f.t.Helper()
	shkit.Write(f.t, filepath.Join(wt, "a.txt"), "a\nagent\n")
	git(f.t, wt, "commit", "-q", "-am", "agent")
}

// プロジェクトのリポジトリは、それ自身で取り込む。ワークスペースと、ほかのプロジェクトは動かさない。
func TestIntegrateOnlyTheProjectMovesAndTheWorkspaceAndTheOtherProjectStay(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	p, q := f.makeProject("p"), f.makeProject("q")
	wt := f.cut("pwt", p, "main")
	f.projectWork(wt)
	wsBefore, qBefore := f.snapshot(), f.repoState(q)
	pHead := gitT(t, p, "rev-parse", "HEAD")
	f.code(f.shIn(p, nil, "integrate", "pwt"))
	if !shkit.IsDir(f.tmpWT("pwt", "p")) {
		t.Error("プロジェクトの一時ワークツリーが無い")
	}
	f.code(f.shIn(p, nil, "integrate-done", "pwt", "-m", "feat: p"))
	f.eq(gitT(t, p, "rev-list", "--count", pHead+"..HEAD"), "1", "載ったコミット")
	f.eq(shkit.Read(t, filepath.Join(p, "a.txt")), "a\nagent\n", "a.txt")
	f.mustNotExist(wt, "ワークツリー")
	f.eq(f.snapshot(), wsBefore, "ワークスペース")
	f.eq(f.repoState(q), qBefore, "ほかのプロジェクト")
}

func TestIntegrateTheOtherRepositorysLeftoversAreNotTouched(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	initRepo(t, f.p("projects/q"), "main")
	left := f.tmpWT("qwt", "q")
	shkit.Write(t, filepath.Join(left, "x"), "x\n")
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"))
	f.eq(shkit.Read(t, filepath.Join(left, "x")), "x\n", "ほかのリポジトリの残り")
}

// ---- 敵対的レビューの指摘に対する回帰テスト。外すと落ちる条件を 1 つずつ縛る。

func (f *itg) resolveConflict() {
	f.t.Helper()
	gitTry(f.wt, "merge", "-q", "main")
	shkit.Write(f.t, filepath.Join(f.wt, "keep.txt"), "1\n2\nresolved\n")
	git(f.t, f.wt, "add", "keep.txt")
	git(f.t, f.wt, "commit", "-q", "-m", "resolve")
}

// kMadeButNotLanded は K を作ったが、ユーザの未コミットの変更で fast-forward が止まった状態にする。
func (f *itg) kMadeButNotLanded() {
	f.t.Helper()
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	shkit.Write(f.t, f.p("keep.txt"), "1\n2\nmine\n")
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 1)
	git(f.t, f.ws, "checkout", "--", "keep.txt")
}

// landedButCleanupStopped は K を載せたあと、一時ワークツリーの未知のファイルで片付けが止まった状態にする。そのファイルのパスを返す。
func (f *itg) landedButCleanupStopped() string {
	f.t.Helper()
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	mystery := filepath.Join(f.tmpWT("wt", "self"), "mystery.txt")
	shkit.Write(f.t, mystery, "somebody's file\n")
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 1)
	return mystery
}

// 衝突を解いたあとの収束: 解いたあとに取り込み先が動いても、結果に漏れない。
func TestIntegrateAResolvedConflictConverges(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.advanceMain("keep.txt", "1\n2\nTHREE\n")
	f.code(f.sh("integrate", "wt"), 1)
	f.resolveConflict()
	f.advanceMain("other.txt", "later\n")
	f.code(f.sh("integrate", "wt"))
	if got := f.changedFiles(f.markerValue("c_tree", "wt")); !sameList(got, []string{"gone.txt", "keep.txt", "new.txt"}) {
		t.Errorf("changed = %v", got)
	}
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"))
	f.eq(shkit.Read(t, f.p("keep.txt")), "1\n2\nresolved\n", "keep.txt")
	f.mustExist(f.p("new.txt"), "new.txt")
	f.eq(shkit.Read(t, f.p("other.txt")), "later\n", "other.txt")
}

// push 済み（origin/<名前> がある）でも、記録があれば親とみなさない。
func TestIntegrateAPushedDirectBranchWithARecordIsNotTakenForAParent(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	git(t, f.ws, "update-ref", "refs/remotes/origin/wt", "HEAD")
	f.code(f.sh("integrate", "wt"))
}

func TestIntegrateANameBoundToATicketIsRefused(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	shkit.Write(t, f.p("wip/proposals/todo/wt.md"), "---\n---\n")
	r := f.code(f.sh("integrate", "wt"), 2)
	contains(t, r.Stderr, "チケットに結び付く")
	f.mustNotExist(f.tmpWT("wt", "self"), "一時ワークツリー")
}

func TestIntegrateDoesNotRewriteTheRootsIndex(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	path := f.p("keep.txt")
	shkit.Write(t, path, "1\n2\n3\n")
	future := time.Now().Add(5 * time.Second)
	if err := os.Chtimes(path, future, future); err != nil {
		t.Fatal(err)
	}
	index := filepath.Join(f.commonDir(""), "index")
	before := shkit.Read(t, index)
	f.code(f.sh("integrate", "wt"))
	if shkit.Read(t, index) != before {
		t.Error("ルートの index が書き換わった")
	}
}

// git が引用符を付ける名前（バックスラッシュ・タブ）を扱える。
func TestIntegrateNamesThatGitQuotesAreHandled(t *testing.T) {
	t.Parallel()
	if ospath.Windows {
		t.Skip("ファイル名に使えない文字を含む")
	}
	f := newItg(t, true)
	draft := `wip/handoff/a\b.md`
	shkit.Write(t, f.p(draft), "draft two\n")
	git(t, f.ws, "add", "-f", draft)
	git(t, f.ws, "commit", "-q", "-m", "draft")
	wt := f.cut("wt2", "", "main")
	git(t, wt, "rm", "-q", "--", draft)
	shkit.Write(t, filepath.Join(wt, "tab\tname.txt"), "t\n")
	git(t, wt, "add", ".")
	git(t, wt, "commit", "-q", "-m", "agent")
	r := f.code(f.sh("integrate", "wt2"))
	contains(t, r.Stdout, "  tab\tname.txt")
	got := strings.Split(git(t, f.ws, "ls-tree", "-r", "-z", "--name-only", f.markerValue("c_tree", "wt2")), "\x00")
	has := func(name string) bool {
		for _, g := range got {
			if g == name {
				return true
			}
		}
		return false
	}
	if !has(draft) || !has("tab\tname.txt") {
		t.Errorf("ls-tree = %q", got)
	}
}

// K の記録前に落ちた打ち直し: 手でコミットした K を、打ち直しで採る。
func TestIntegrateAKCommittedBeforeTheRecordIsAdoptedOnRetry(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	tmp := f.tmpWT("wt", "self")
	git(t, tmp, "commit", "-q", "-m", "committed by hand")
	k := gitT(t, tmp, "rev-parse", "HEAD")
	notContains(t, shkit.Read(t, f.marker("wt")), "k ")
	f.code(f.sh("integrate-done", "wt"))
	f.eq(f.head(), k, "HEAD")
	if f.hasBranch("wt") {
		t.Error("ブランチが残っている")
	}
}

func TestIntegrateAKWithTheWrongParentIsNotAdopted(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.advanceMain("other.txt", "y\n")
	f.code(f.sh("integrate", "wt"))
	old := gitT(t, f.ws, "rev-parse", "HEAD~1")
	k := f.strayCommit(f.markerValue("c_tree", "wt"), old, "k")
	shkit.Write(t, f.marker("wt"), shkit.Read(t, f.marker("wt"))+"k "+k+"\n")
	head := f.head()
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 2)
	f.eq(f.head(), head, "HEAD")
}

// 一時ワークツリーの追跡済みの編集は、staged でも無くても integrate-done を止める。
func TestIntegrateATrackedEditInTheTempWorktreeStopsDone(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	tmp := f.tmpWT("wt", "self")
	shkit.Write(t, filepath.Join(tmp, "keep.txt"), "edited while verifying\n")
	head := f.head()
	for _, staged := range []bool{false, true} {
		if staged {
			git(t, tmp, "add", "keep.txt")
		}
		r := f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 2)
		contains(t, r.Stderr, "違います")
		f.eq(f.head(), head, "HEAD")
	}
}

// 片付けは、一時ワークツリーの追跡済みの編集を staged でもそうでなくても消さない。
func TestIntegrateCleanupKeepsATrackedEditInTheTempWorktree(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	shkit.Remove(t, f.landedButCleanupStopped())
	keep := filepath.Join(f.tmpWT("wt", "self"), "keep.txt")
	shkit.Write(t, keep, "edited later\n")
	for _, staged := range []bool{false, true} {
		if staged {
			git(t, f.tmpWT("wt", "self"), "add", "keep.txt")
		}
		f.code(f.sh("integrate-done", "wt"), 1)
		f.eq(shkit.Read(t, keep), "edited later\n", "keep.txt")
	}
}

func TestIntegrateCleanupKeepsTheWorktreeWhenItLeftTheBranch(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	git(t, f.wt, "checkout", "-q", "-b", "elsewhere")
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 1)
	if !shkit.IsDir(f.wt) || !f.hasBranch("wt") {
		t.Error("ブランチを離れたワークツリーを消した")
	}
}

func TestIntegrateCleanupKeepsTheWorktreeWithUncommittedChanges(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.kMadeButNotLanded()
	shkit.Write(t, filepath.Join(f.wt, "keep.txt"), "uncommitted\n")
	r := f.code(f.sh("integrate-done", "wt"), 1)
	contains(t, r.Stdout, "未コミットの変更があるので、削除していません")
	f.eq(shkit.Read(t, filepath.Join(f.wt, "keep.txt")), "uncommitted\n", "keep.txt")
	if !f.hasBranch("wt") {
		t.Error("ブランチが消えた")
	}
}

func TestIntegrateCleanupKeepsTheWorktreeWithANewIgnoredDraft(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.kMadeButNotLanded()
	note := filepath.Join(f.wt, "wip", "handoff", "note.local")
	shkit.Write(t, note, "not in the start point\n")
	contains(t, f.code(f.sh("integrate-done", "wt"), 1).Stdout, "まだコミットしていないファイル")
	f.mustExist(note, "下書き")
}

func TestIntegrateCleanupStopsBeforeDeletingTheWorktreeWhenTheBranchMoved(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.kMadeButNotLanded()
	tip := gitT(t, f.ws, "rev-parse", "wt")
	late := f.strayCommit(tip+"^{tree}", tip, "late")
	git(t, f.ws, "update-ref", "refs/heads/wt", late)
	git(t, f.wt, "reset", "-q", "--hard", late)
	f.code(f.sh("integrate-done", "wt"), 1)
	if !shkit.IsDir(f.wt) || !f.hasBranch("wt") {
		t.Error("動いたブランチのワークツリーを消した")
	}
}

func TestIntegrateAHeadThatMovedAfterKWasMadeAsksToStartOver(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	shkit.Write(t, f.p("keep.txt"), "1\n2\nmine\n")
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 1)
	shkit.Write(t, f.p("other.txt"), "moved\n")
	git(t, f.ws, "commit", "-q", "-m", "user", "--", "other.txt")
	contains(t, f.code(f.sh("integrate-done", "wt"), 2).Stderr, "動きました")
}

func TestIntegrateADoneRecordOfAnotherNameIsNotPruned(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	other := shkit.Write(t, filepath.Join(f.commonDir(""), "ccnavi-integrate-done", "other-name"), "version 4\nbase_branch main\n")
	old := time.Now().Add(-30 * 24 * time.Hour)
	if err := os.Chtimes(other, old, old); err != nil {
		t.Fatal(err)
	}
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	f.code(f.sh("integrate-done", "wt", "-m", "feat: x"))
	f.mustExist(other, "別の名前の完了記録")
}

// ---- 版が 4 でない marker

// 版が 4 でない marker は壊れた記録として、どの入口も「版を知りません」で止まる。
func TestIntegrateEveryEntryRefusesAMarkerThatIsNotVersion4(t *testing.T) {
	t.Parallel()
	for name, tc := range map[string]struct{ text, known string }{
		"版が無い": {"state staged\nbranch wt\n", "を知りません"},
		"版が 3": {"version 3\nhead x\n", "版（3）を知りません"},
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			f := newItg(t, false)
			f.workInBranch()
			shkit.Write(t, f.marker("wt"), tc.text)
			for _, args := range [][]string{{"integrate", "wt"}, {"integrate", "--abort", "wt"}, {"integrate-done", "wt"}} {
				r := f.code(f.sh(args...), 2)
				contains(t, r.Stderr, tc.known, "git reset --merge", shkit.Slash(filepath.Dir(f.marker("wt"))))
			}
			f.mustExist(f.marker("wt"), "marker")
			if !f.hasBranch("wt") {
				t.Error("ブランチが消えた")
			}
		})
	}
}

func TestIntegrateDoneWithoutAMarkerIsRefused(t *testing.T) {
	t.Parallel()
	f := newItg(t, false)
	f.workInBranch()
	contains(t, f.code(f.sh("integrate-done", "wt", "-m", "x"), 2).Stderr, "取り込みの記録がありません")
	if !f.hasBranch("wt") {
		t.Error("ブランチが消えた")
	}
}

func TestIntegrateDoneOnACleanedUpNameSaysSo(t *testing.T) {
	t.Parallel()
	f := newItg(t, false)
	git(t, f.ws, "worktree", "remove", "--force", f.wt)
	git(t, f.ws, "branch", "-D", "wt")
	contains(t, f.code(f.sh("integrate-done", "wt")).Stdout, "片付け済み")
}

// ---- 片付けの各段階で止まっても、打ち直せば完了する

// installFailingGit は、環境変数 FAIL_GIT_MATCH の値を引数に含む git の呼び出しだけ、終了コード 1 で失敗させるスタブを PATH に置く。
func (f *itg) installFailingGit() {
	f.t.Helper()
	realGit, err := lookGit()
	if err != nil {
		f.t.Skip("git の場所が分からない")
	}
	shim := filepath.Join(f.tmp, "failgit")
	shkit.WriteExec(f.t, filepath.Join(shim, "git"), "#!/bin/sh\n"+
		"if [ -n \"$FAIL_GIT_MATCH\" ]; then\n"+
		"  case \" $* \" in\n"+
		"    *\"$FAIL_GIT_MATCH\"*) echo \"fake git: failed on request\" >&2; exit 1 ;;\n"+
		"  esac\n"+
		"fi\n"+
		"exec '"+shkit.Slash(realGit)+"' \"$@\"\n")
	f.path = shim + string(os.PathListSeparator) + os.Getenv("PATH")
}

// doneStoppedAt は match にヒットする git が失敗する状態で integrate-done -m を打つ（K は載り、片付けは途中で止まる）。スタブは外してから返す。
func (f *itg) doneStoppedAt(match, name, cwd string) run.Result {
	f.t.Helper()
	r := f.shIn(cwd, map[string]string{"FAIL_GIT_MATCH": match}, "integrate-done", name, "-m", "feat: x")
	f.path = ""
	return r
}

// assertLandedOnce は K がちょうど 1 つ載ったこと（終了コードは 0 でない）を確かめ、K を返す。
func (f *itg) assertLandedOnce(before string, r run.Result, repo string) string {
	f.t.Helper()
	if r.Code == 0 {
		f.t.Fatalf("止まるはずが 0 で終わった\n%s%s", r.Stdout, r.Stderr)
	}
	contains(f.t, r.Stdout, "取り込みました")
	if repo == "" {
		repo = f.ws
	}
	head := gitT(f.t, repo, "rev-parse", "HEAD")
	if head == before {
		f.t.Fatal("HEAD が動いていない")
	}
	f.eq(strings.Fields(git(f.t, repo, "log", "-1", "--format=%P"))[0], before, "親")
	return head
}

func (f *itg) assertFullyCleaned(before, k string) {
	f.t.Helper()
	f.eq(f.head(), k, "HEAD")
	f.eq(gitT(f.t, f.ws, "rev-list", "--count", before+"..HEAD"), "1", "載ったコミット")
	f.mustNotExist(f.wt, "ワークツリー")
	f.mustNotExist(f.tmpWT("wt", "self"), "一時ワークツリー")
	if f.hasBranch("wt") {
		f.t.Error("ブランチが残っている")
	}
	f.mustNotExist(f.marker("wt"), "marker")
	f.mustNotExist(f.record("wt"), "記録")
	for _, kind := range []string{"s", "t", "c", "k"} {
		f.eq(f.ref(kind, "wt"), "", "ref "+kind)
	}
}

// 作業ワークツリーの削除で止まっても、止めた段階の先が残り、打ち直せば完了する。
func TestIntegrateAStopAtTheWorkWorktreeResumes(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	f.installFailingGit()
	before := f.head()
	k := f.assertLandedOnce(before, f.doneStoppedAt("worktree remove /", "wt", ""), "")
	if !shkit.Exists(f.wt) || !shkit.Exists(f.tmpWT("wt", "self")) || !f.hasBranch("wt") || !shkit.IsFile(f.marker("wt")) || !shkit.IsFile(f.record("wt")) {
		t.Error("止めた段階の先が残っていない")
	}
	for _, kind := range []string{"s", "t", "c", "k"} {
		if f.ref(kind, "wt") == "" {
			t.Errorf("ref %s が無い", kind)
		}
	}
	f.code(f.sh("integrate-done", "wt"))
	f.assertFullyCleaned(before, k)
}

// 消す順は t c k s pending。c だけ失敗させると、t は消え、c 以降の消せるものも消える。
func TestIntegrateAStopInTheMiddleOfTheDedicatedRefsResumes(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	f.installFailingGit()
	before := f.head()
	k := f.assertLandedOnce(before, f.doneStoppedAt("update-ref -d refs/ccnavi/direct/wt/c", "wt", ""), "")
	f.eq(f.ref("t", "wt"), "", "ref t")
	if f.ref("c", "wt") == "" || !shkit.IsFile(f.marker("wt")) || !shkit.IsFile(f.record("wt")) {
		t.Error("c の参照か marker か記録が消えた")
	}
	f.code(f.sh("integrate-done", "wt"))
	f.assertFullyCleaned(before, k)
}

// 一時ワークツリーに残った ignored のファイルは、片付けでも --abort でも消さない。
func TestIntegrateDoneKeepsAnIgnoredFileLeftInTheTempWorktree(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	left := filepath.Join(f.tmpWT("wt", "self"), "residue.local")
	shkit.Write(t, left, "somebody's ignored file\n")
	r := f.code(f.sh("integrate-done", "wt", "-m", "feat: x"), 1)
	contains(t, r.Stdout, "取り込みました", "residue.local")
	f.eq(shkit.Read(t, left), "somebody's ignored file\n", "residue.local")
	f.mustNotExist(f.wt, "ワークツリー")
	f.mustExist(f.marker("wt"), "marker")
	// 残りをユーザが消せば、打ち直しで完了する。
	shkit.Remove(t, left)
	f.code(f.sh("integrate-done", "wt"))
	f.mustNotExist(f.tmpWT("wt", "self"), "一時ワークツリー")
	f.mustNotExist(f.marker("wt"), "marker")
	if f.hasBranch("wt") {
		t.Error("ブランチが残っている")
	}
}

func TestIntegrateAbortKeepsAnIgnoredFileLeftInTheTempWorktree(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	f.workInBranch()
	f.code(f.sh("integrate", "wt"))
	left := filepath.Join(f.tmpWT("wt", "self"), "residue.local")
	shkit.Write(t, left, "somebody's ignored file\n")
	contains(t, f.code(f.sh("integrate", "--abort", "wt")).Stdout, "residue.local")
	f.eq(shkit.Read(t, left), "somebody's ignored file\n", "residue.local")
	f.mustNotExist(f.marker("wt"), "marker")
}

// あるプロジェクトの片付けが止まっても、ほかのプロジェクトの途中の状態は動かさない。
func TestIntegrateAStoppedCleanupOfOneProjectDoesNotTouchTheOther(t *testing.T) {
	t.Parallel()
	f := newItg(t, true)
	p, q := f.makeProject("p"), f.makeProject("q")
	qwt := f.cut("qwt", q, "main")
	f.projectWork(qwt)
	f.code(f.shIn(q, nil, "integrate", "qwt"))
	qTmp := f.tmpWT("qwt", "q")
	qMarker := filepath.Join(f.commonDir(q), "ccnavi-integrate", "qwt", "marker")
	qRecord := filepath.Join(f.commonDir(q), "ccnavi-direct", "qwt")
	qState := func() [5]string {
		return [5]string{boolText(shkit.IsDir(qTmp)), gitT(t, qTmp, "write-tree"), git(t, q, "for-each-ref", "refs/ccnavi/direct/"), shkit.Read(t, qMarker), shkit.Read(t, qRecord)}
	}
	beforeQ := qState()
	contains(t, beforeQ[2], "refs/ccnavi/direct/qwt/t", "refs/ccnavi/direct/qwt/c")
	pwt := f.cut("pwt", p, "main")
	f.projectWork(pwt)
	f.code(f.shIn(p, nil, "integrate", "pwt"))
	pHead := gitT(t, p, "rev-parse", "HEAD")
	f.installFailingGit()
	r := f.doneStoppedAt("update-ref -d refs/heads/pwt", "pwt", p)
	k := f.assertLandedOnce(pHead, r, p)
	if qState() != beforeQ {
		t.Error("止まった片付けが、ほかのプロジェクトの状態を動かした")
	}
	f.code(f.shIn(p, nil, "integrate-done", "pwt"))
	f.eq(gitT(t, p, "rev-parse", "HEAD"), k, "プロジェクトの HEAD")
	f.eq(gitT(t, p, "rev-list", "--count", pHead+"..HEAD"), "1", "載ったコミット")
	f.mustNotExist(pwt, "ワークツリー")
	f.mustNotExist(f.tmpWT("pwt", "p"), "一時ワークツリー")
	if qState() != beforeQ {
		t.Error("打ち直しが、ほかのプロジェクトの状態を動かした")
	}
}

func boolText(b bool) string {
	if b {
		return "true"
	}
	return "false"
}
