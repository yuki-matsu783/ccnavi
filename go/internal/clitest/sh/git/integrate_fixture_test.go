package git

import (
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// `ccnavi-git.sh integrate` / `integrate --abort` / `integrate-done` の受入テスト。
//
// 直接作業（チケットを起こさない）のワークツリーの成果を、切ったときの取り込み先のブランチへ 1 コミットで載せる流れ。確認している間、
// ユーザのチェックアウトの index とワークツリーには書かず、一時ワークツリーで取り込み結果を作り、最後に
// `merge --ff-only --no-overwrite-ignore` で載せる。使い捨てのワークスペースを毎回組み、sh を外から呼ぶ。読み返すのは終了コード・出力と、
// git とファイルシステムに残ったものだけ。記録（ccnavi-direct/<名前>）は ccnavi-worktree.sh が書く形を、このテストが同じ形で書く。
// 判定と操作は実行ファイル（ccnavi git integrate）が行う。

// gitAtLeast は git のバージョンが major.minor 以上か。
func gitAtLeast(major, minor int) bool {
	out, err := exec.Command("git", "version").Output()
	if err != nil {
		return false
	}
	fields := strings.Fields(string(out))
	if len(fields) < 3 {
		return false
	}
	parts := strings.Split(fields[2], ".")
	if len(parts) < 2 {
		return false
	}
	ma, err1 := strconv.Atoi(parts[0])
	mi, err2 := strconv.Atoi(parts[1])
	return err1 == nil && err2 == nil && (ma > major || (ma == major && mi >= minor))
}

// itg はワークスペース（ルートは main）と、記録つきの直接作業のワークツリー wt。
type itg struct {
	t   *testing.T
	tmp string
	ws  string
	bin string
	wt  string
	// path が空でなければ、子の PATH にする（git のスタブを差し込むとき）。
	path string
}

// newItg は sh と git（v4 を要るなら 2.40 以上）が無ければ飛ばす。
func newItg(t *testing.T, needV4 bool) *itg {
	t.Helper()
	need(t, "sh", "git")
	if needV4 && !gitAtLeast(2, 40) {
		t.Skip("git 2.40 以上が要る")
	}
	tmp := base.TempDir(t)
	f := &itg{t: t, tmp: tmp, ws: filepath.Join(tmp, "ws")}
	copyScripts(t, f.ws, "ccnavi-git.sh", "ccnavi-clean.sh")
	base.PutMessages(t, f.ws)
	f.bin = exeWrapper(t, filepath.Join(tmp, "bin"))
	initRepo(t, f.ws, "main")
	write(t, filepath.Join(f.ws, ".gitignore"), "/.ccnavi/\n/.claude/worktrees/\n/logs/\n*.local\n")
	write(t, filepath.Join(f.ws, "keep.txt"), "1\n2\n3\n")
	write(t, filepath.Join(f.ws, "gone.txt"), "bye\n")
	write(t, filepath.Join(f.ws, "other.txt"), "x\n")
	write(t, filepath.Join(f.ws, "wip/handoff/d.md"), "draft\n")
	git(t, f.ws, "add", ".")
	git(t, f.ws, "commit", "-q", "-m", "seed")
	git(t, f.ws, "update-ref", "refs/remotes/origin/main", "HEAD")
	git(t, f.ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
	f.wt = f.cut("wt", "", "main")
	return f
}

func (f *itg) p(rel string) string { return filepath.Join(f.ws, filepath.FromSlash(rel)) }

func (f *itg) commonDir(repo string) string {
	f.t.Helper()
	if repo == "" {
		repo = f.ws
	}
	dir, err := filepath.EvalSymlinks(filepath.Join(repo, ".git"))
	if err != nil {
		f.t.Fatal(err)
	}
	return dir
}

// cut は ccnavi-worktree.sh が切ったのと同じ形（記録・s の ref・ワークツリー）を作る。
func (f *itg) cut(name, repo, baseBranch string) string {
	f.t.Helper()
	if repo == "" {
		repo = f.ws
	}
	head := gitT(f.t, repo, "rev-parse", "HEAD")
	wt := f.p(".claude/worktrees/" + name)
	real, err := filepath.EvalSymlinks(repo)
	if err != nil {
		f.t.Fatal(err)
	}
	write(f.t, filepath.Join(f.commonDir(repo), "ccnavi-direct", name),
		"version 4\n"+
			"base_branch "+baseBranch+"\nbase_sha "+head+"\nsnap_sha "+head+"\nsnap_wip no\n"+
			"side_by_side no\nmain_tree "+real+"\n"+
			"common_dir "+f.commonDir(repo)+"\nconfirm -\ncreated 0\n")
	git(f.t, repo, "update-ref", "refs/ccnavi/direct/"+name+"/s", head)
	git(f.t, repo, "worktree", "add", "-q", wt, "-b", name, "--no-track", head)
	return wt
}

// sh は ccnavi-git.sh を打つ。標準入力は空。
func (f *itg) sh(args ...string) run.Result { f.t.Helper(); return f.shIn("", nil, args...) }

func (f *itg) shEnv(env map[string]string, args ...string) run.Result {
	f.t.Helper()
	return f.shIn("", env, args...)
}

func (f *itg) shIn(cwd string, env map[string]string, args ...string) run.Result {
	f.t.Helper()
	if cwd == "" {
		cwd = f.ws
	}
	merged := map[string]string{"CCNAVI_ENTRY_PATH": f.bin}
	if f.path != "" {
		merged["PATH"] = f.path
	}
	for k, v := range env {
		merged[k] = v
	}
	return run.Sh(f.t, slash(filepath.Join(f.ws, ".ccnavi", "scripts", "ccnavi-git.sh")), args, run.Opts{Dir: cwd, Env: merged})
}

// workInBranch は作業ワークツリーで、変更・追加・削除を 2 コミットに分けて積む。
func (f *itg) workInBranch() { f.t.Helper(); f.workIn(f.wt) }

func (f *itg) workIn(wt string) {
	f.t.Helper()
	write(f.t, filepath.Join(wt, "keep.txt"), "1\n2\nthree\n")
	write(f.t, filepath.Join(wt, "new.txt"), "new\n")
	git(f.t, wt, "add", ".")
	git(f.t, wt, "commit", "-q", "-m", "w1")
	git(f.t, wt, "rm", "-q", "gone.txt")
	git(f.t, wt, "commit", "-q", "-m", "w2")
}

func (f *itg) advanceMain(name, text string) {
	f.t.Helper()
	write(f.t, f.p(name), text)
	git(f.t, f.ws, "commit", "-q", "-am", "main moves")
}

func (f *itg) marker(name string) string {
	return filepath.Join(f.commonDir(""), "ccnavi-integrate", name, "marker")
}

func (f *itg) markerValue(key, name string) string {
	f.t.Helper()
	for _, line := range strings.Split(read(f.t, f.marker(name)), "\n") {
		if v, ok := strings.CutPrefix(line, key+" "); ok {
			return v
		}
	}
	return ""
}

func (f *itg) record(name string) string {
	return filepath.Join(f.commonDir(""), "ccnavi-direct", name)
}

func (f *itg) tmpWT(name, repo string) string { return f.p("logs/direct-tmp/" + repo + "/" + name) }

func (f *itg) status() string { return git(f.t, f.ws, "status", "--porcelain") }

func (f *itg) hasBranch(name string) bool {
	return gitOK(f.ws, "show-ref", "--verify", "--quiet", "refs/heads/"+name)
}

func (f *itg) ref(kind, name string) string {
	out, _ := gitTry(f.ws, "rev-parse", "--verify", "--quiet", "refs/ccnavi/direct/"+name+"/"+kind)
	return strings.TrimSpace(out)
}

func (f *itg) head() string { return gitT(f.t, f.ws, "rev-parse", "HEAD") }

// state はルートの HEAD・index・ワークツリーの状態。確認の間は変わらない。
type state struct{ head, tree, status string }

func (f *itg) snapshot() state { return f.repoState(f.ws) }

func (f *itg) repoState(repo string) state {
	f.t.Helper()
	return state{gitT(f.t, repo, "rev-parse", "HEAD"), gitT(f.t, repo, "write-tree"), git(f.t, repo, "status", "--porcelain")}
}

func (f *itg) hook(name, body string) {
	f.t.Helper()
	writeExec(f.t, filepath.Join(f.commonDir(""), "hooks", name), "#!/bin/sh\n"+body)
}

// code は終了コードが want であることを確かめる（省略は 0）。
func (f *itg) code(r run.Result, want ...int) run.Result {
	f.t.Helper()
	w := 0
	if len(want) > 0 {
		w = want[0]
	}
	if r.Code != w {
		f.t.Fatalf("code = %d, want %d\n%s%s", r.Code, w, r.Stdout, r.Stderr)
	}
	return r
}

func contains(t *testing.T, got string, wants ...string) {
	t.Helper()
	for _, w := range wants {
		if !strings.Contains(got, w) {
			t.Errorf("%q が含まれない:\n%s", w, got)
		}
	}
}

func notContains(t *testing.T, got string, bads ...string) {
	t.Helper()
	for _, b := range bads {
		if strings.Contains(got, b) {
			t.Errorf("%q が含まれている:\n%s", b, got)
		}
	}
}

func (f *itg) mustExist(path, what string) {
	f.t.Helper()
	if !exists(path) {
		f.t.Errorf("%s が無い: %s", what, path)
	}
}

func (f *itg) mustNotExist(path, what string) {
	f.t.Helper()
	if exists(path) {
		f.t.Errorf("%s が残っている: %s", what, path)
	}
}

func (f *itg) eq(got, want any, what string) {
	f.t.Helper()
	if got != want {
		f.t.Errorf("%s = %v, want %v", what, got, want)
	}
}

func (f *itg) commitDraft(rel, text string) {
	f.t.Helper()
	write(f.t, filepath.Join(f.wt, filepath.FromSlash(rel)), text)
	git(f.t, f.wt, "add", "-f", rel)
	git(f.t, f.wt, "commit", "-q", "-m", "draft")
}

// strayCommit は parent の上に tree のコミットを 1 つ作る（ref は動かさない）。
func (f *itg) strayCommit(treeish, parent, msg string) string {
	f.t.Helper()
	return gitT(f.t, f.ws, "commit-tree", treeish, "-p", parent, "-m", msg)
}

func (f *itg) touch(path string) { f.t.Helper(); write(f.t, path, "") }
