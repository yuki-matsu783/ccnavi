package start

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
)

// ccnavi-session-start.sh と ccnavi-dirs.sh の受入テスト。使い捨ての木に sh をコピーして外から呼ぶ。
//
// 見るのは次の 4 つ。
//  1. 入口は、取り込み（fetch）が失敗しても、clone 済みリポジトリの一覧（dirs）を出して 0 で終わる
//  2. 一覧は 10 件までで、超えると残りの件数を添える
//  3. projects/ が無いとき、または .git を持つものが無いときは何も出さない
//  4. 10 件ちょうどなら案内を出さない

type sessionEnv struct {
	t       testing.TB
	project string
	scripts string
}

func newSession(t testing.TB) *sessionEnv {
	t.Helper()
	need(t, "sh", "git")
	dir := base.TempDir(t)
	s := &sessionEnv{t: t, project: filepath.Join(dir, "project"), scripts: filepath.Join(dir, "scripts")}
	mkdir(t, s.project)
	copyScriptsTo(t, s.scripts, "ccnavi-session-start.sh", "ccnavi-dirs.sh")
	return s
}

func (s *sessionEnv) run(script string) run.Result {
	s.t.Helper()
	return runScript(s.t, filepath.Join(s.scripts, script), s.project, map[string]string{
		"CLAUDE_PROJECT_DIR": s.project, "CCNAVI_ENTRY_PATH": exe.Path(s.t),
	})
}

// commitAt はコミットの時刻を ts にした空のコミットを 1 件持つリポジトリにする。
func (s *sessionEnv) commitAt(path string, ts int) {
	s.t.Helper()
	date := fmt.Sprintf("%d +0000", ts)
	env := map[string]string{
		"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
		"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
	}
	for _, args := range [][]string{{"init", "-q"}, {"commit", "-q", "--allow-empty", "-m", "x"}} {
		code(s.t, run.Command(s.t, "git", append([]string{"-C", path}, args...), run.Opts{Env: env}), 0)
	}
}

func (s *sessionEnv) makeDirs(count int) {
	s.t.Helper()
	for i := 0; i < count; i++ {
		path := filepath.Join(s.project, "projects", fmt.Sprintf("d%02d", i))
		mkdir(s.t, path)
		s.commitAt(path, 1_000_000_000+i)
	}
}

// names は先頭の見出しを除いた行（前後の空白を落とす）。
func names(stdout string) []string {
	var out []string
	for _, line := range strings.Split(strings.TrimRight(stdout, "\n"), "\n")[1:] {
		out = append(out, strings.TrimSpace(line))
	}
	return out
}

func TestSessionStartListsNewestFirstUpToTen(t *testing.T) {
	t.Parallel()
	s := newSession(t)
	s.makeDirs(12)
	r := code(t, s.run("ccnavi-dirs.sh"), 0)
	lines := strings.Split(strings.TrimRight(r.Stdout, "\n"), "\n")
	var want []string
	for i := 11; i > 1; i-- {
		want = append(want, fmt.Sprintf("- d%02d", i))
	}
	got := names(r.Stdout)[:10]
	eq(t, strings.Join(got, "|"), strings.Join(want, "|"), "一覧")
	has(t, lines[11], "ほかに 2 件")
}

func TestSessionStartNoMoreNoteWhenWithinTen(t *testing.T) {
	t.Parallel()
	s := newSession(t)
	s.makeDirs(10)
	lacks(t, s.run("ccnavi-dirs.sh").Stdout, "ほかに")
}

func TestSessionStartPrintsNothingWithoutRepositories(t *testing.T) {
	t.Parallel()
	for name, prepare := range map[string]func(s *sessionEnv){
		"no directories":           func(s *sessionEnv) {},
		"top level directories":    func(s *sessionEnv) { mkdir(s.t, filepath.Join(s.project, "src", ".git")) },
		"a directory without .git": func(s *sessionEnv) { mkdir(s.t, filepath.Join(s.project, "projects", "plain")) },
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			s := newSession(t)
			prepare(s)
			r := code(t, s.run("ccnavi-dirs.sh"), 0)
			eq(t, r.Stdout, "", "標準出力")
		})
	}
}

func TestSessionStartOrdersByLatestCommitNotDirMtime(t *testing.T) {
	t.Parallel()
	s := newSession(t)
	s.makeDirs(3)
	// 並べ替えが、ディレクトリの更新時刻でなくコミット時刻で決まることを見る。d02 のコミットは一番新しいまま、更新時刻だけを未来にする。
	old := filepath.Join(s.project, "projects", "d00")
	touchLater(t, old)
	mkdir(t, filepath.Join(s.project, "projects", "nocommit", ".git"))
	r := s.run("ccnavi-dirs.sh")
	eq(t, strings.Join(names(r.Stdout), "|"), "- d02|- d01|- d00|- nocommit", "一覧")
}

func TestSessionStartSkipsDirectoriesWithoutGit(t *testing.T) {
	t.Parallel()
	s := newSession(t)
	s.makeDirs(1)
	mkdir(t, filepath.Join(s.project, "projects", "plain"))
	r := s.run("ccnavi-dirs.sh")
	has(t, r.Stdout, "- d00")
	lacks(t, r.Stdout, "plain")
}

func TestSessionStartCountsAGitFileAndOddNames(t *testing.T) {
	t.Parallel()
	s := newSession(t)
	projects := filepath.Join(s.project, "projects")
	write(t, filepath.Join(projects, "sub mod", ".git"), "gitdir: ../x\n")
	mkdir(t, filepath.Join(projects, "-dash", ".git"))
	r := s.run("ccnavi-dirs.sh")
	has(t, r.Stdout, "- sub mod", "- -dash")
}

func TestSessionStartEntryRunsDirsEvenWhenFetchFails(t *testing.T) {
	t.Parallel()
	s := newSession(t)
	s.makeDirs(1)
	write(t, filepath.Join(s.scripts, "ccnavi-fetch.sh"), "echo fetch-ran\nexit 1\n")
	r := code(t, s.run("ccnavi-session-start.sh"), 0)
	has(t, r.Stdout, "fetch-ran", "- d00")
}
