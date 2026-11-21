package wap

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
	"ccnavi/internal/infra/env"
)

const (
	parentID = "i0001"
	childID  = parentID + "-01-01"
	childBID = parentID + "-01-02"
	approved = ".ccnavi/approved"
	// wipNote は ready が消す wip/ の追跡済みのファイル（親のワークツリーにコミットして push する）。
	wipNote = "wip/note.md"
)

// kit は 1 本のテストの木。ワークスペース（main）、bare のリモート、親のワークツリー .claude/worktrees/i0001。
// 親は承認済み（計画は既定で mr で見る 1 フェーズ、子 1 つ）で、送ってある。
type kit struct {
	t       *testing.T
	base    string
	ws      string
	scripts string
	remote  string
	bin     string // 実行ファイルの代わり（--guard-ticket-approval の置き方を揃える sh）
	state   string
	tree    string // 親のワークツリー
	stub    hoststub.Stub
	gitPath string
	exePath string
	plan    []string
}

// opts は sh・実行ファイルを 1 回起こすときの追加。
type opts struct {
	dir    string
	input  string
	env    map[string]string
	script string // review の道具だけが使う（既定は ccnavi-review.sh）
}

func need(t *testing.T, tools ...string) {
	t.Helper()
	for _, tool := range tools {
		if _, err := exec.LookPath(tool); err != nil {
			t.Skipf("%s が無い", tool)
		}
	}
}

func write(t testing.TB, path, text string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func writeExec(t testing.TB, path, text string) string {
	t.Helper()
	write(t, path, text)
	if err := os.Chmod(path, 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

func read(t testing.TB, path string) string { return ticketkit.Read(t, path) }

func exists(path string) bool { return ticketkit.Exists(path) }

func isDir(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

func remove(t testing.TB, path string) {
	t.Helper()
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
}

func parts(rel string) string { return filepath.Join(strings.Split(rel, "/")...) }

func has(t testing.TB, s, sub string)   { t.Helper(); ticketkit.Has(t, s, sub) }
func lacks(t testing.TB, s, sub string) { t.Helper(); ticketkit.Lacks(t, s, sub) }

func eq[T comparable](t testing.TB, what string, got, want T) {
	t.Helper()
	if got != want {
		t.Errorf("%s = %v, want %v", what, got, want)
	}
}

func eqList(t testing.TB, what string, got, want []string) {
	t.Helper()
	if strings.Join(got, "\x00") != strings.Join(want, "\x00") {
		t.Errorf("%s = %q, want %q", what, got, want)
	}
}

// code は終了コードを確かめる。違えば出力つきで書く。
func code(t testing.TB, r run.Result, want int) {
	t.Helper()
	if r.Code != want {
		t.Errorf("終了コード %d（%d のはず）\nstdout: %s\nstderr: %s", r.Code, want, r.Stdout, r.Stderr)
	}
}

func okRun(t testing.TB, r run.Result) run.Result { t.Helper(); return ticketkit.OK(t, r) }

func linesWith(s, prefix string) []string {
	var out []string
	for _, line := range strings.Split(s, "\n") {
		if strings.HasPrefix(line, prefix) {
			out = append(out, line)
		}
	}
	return out
}

// newKit は木を組む。テストを並行にする（t.Parallel を呼ぶ）。
func newKit(t *testing.T, plan ...string) *kit {
	t.Helper()
	need(t, "sh", "git")
	t.Parallel()
	if len(plan) == 0 {
		plan = []string{"design"}
	}
	gitPath, err := exec.LookPath("git")
	if err != nil {
		t.Fatal(err)
	}
	k := &kit{t: t, base: base.TempDir(t), plan: plan, gitPath: filepath.ToSlash(gitPath), exePath: exe.ShPath(t)}
	k.build()
	return k
}

func (k *kit) build() {
	t := k.t
	k.ws = filepath.Join(k.base, "ws")
	k.scripts = filepath.Join(k.ws, ".ccnavi", "scripts")
	if err := os.MkdirAll(k.scripts, 0o755); err != nil {
		t.Fatal(err)
	}
	from := base.ScriptsDir()
	entries, err := os.ReadDir(from)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".sh") {
			continue
		}
		data, err := os.ReadFile(filepath.Join(from, e.Name()))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(k.scripts, e.Name()), data, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	base.PutMessages(t, k.ws)
	run.Git(t, k.base, "init", "-q", "-b", "main", k.ws)
	k.configure(k.ws)
	write(t, filepath.Join(k.ws, ".gitignore"), ".claude/worktrees/\nlogs/\n")
	write(t, base.CommonPath(k.ws, "rules"), samples.Rules)
	write(t, base.ConfigPath(k.ws, "phases"), samples.Phases)
	write(t, filepath.Join(k.ws, "src", "keep.py"), "print(1)\n")
	run.Git(t, k.ws, "add", "-A")
	run.Git(t, k.ws, "commit", "-q", "-m", "seed")
	k.remote = filepath.Join(k.base, "origin.git")
	run.Git(t, k.base, "init", "-q", "--bare", "-b", "main", k.remote)
	run.Git(t, k.ws, "remote", "add", "origin", k.remote)
	run.Git(t, k.ws, "push", "-q", "-u", "origin", "main")
	run.Git(t, k.ws, "remote", "set-head", "origin", "main")
	k.bin = writeExec(t, filepath.Join(k.base, "bin", "ccnavi"), strings.ReplaceAll(exeWrapper, "@EXE@", k.exePath))
	k.state = filepath.Join(k.ws, "logs", "state")
	// ホストのスタブ（git remote get-url だけがホストの形を答える。本物のネットワークには出ない）
	k.stub = hoststub.Build(t, k.base, true)
	k.tree = k.parentTree()
	k.approveFamily()
	run.Git(t, k.tree, "push", "-q", "-u", "origin", parentID)
}

// configure は sh が打つ git のための作者と署名なしを、そのリポジトリの設定に書く。
func (k *kit) configure(dir string) {
	for _, kv := range [][2]string{{"user.email", "t@example.invalid"}, {"user.name", "t"}, {"commit.gpgsign", "false"}} {
		run.Git(k.t, dir, "config", kv[0], kv[1])
	}
}

func (k *kit) parentTree() string {
	tree := filepath.Join(k.ws, ".claude", "worktrees", parentID)
	run.Git(k.t, k.ws, "worktree", "add", "-q", tree, "-b", parentID, "main")
	return tree
}

// approveFamily は親 1 つと子 1 つを承認し、承認をコミットする。
func (k *kit) approveFamily() {
	t := k.t
	k.approveChildren(parentID+","+childID, "approval-request", "approve", func() {
		write(t, filepath.Join(k.tree, "wip", "proposals", "todo", parentID+".md"),
			samples.ParentText(parentID, samples.Plan(k.plan...), samples.ParentOpts{Allow: []string{"src/*", "wip/*"}}))
		write(t, filepath.Join(k.tree, "wip", "proposals", "todo", childID+".md"),
			samples.ChildTextReview(childID, parentID, 1, []string{"wip/design/*"}, samples.Off))
	})
}

func (k *kit) approveChildren(ids, requestMessage, approveMessage string, put func()) {
	t := k.t
	put()
	run.Git(t, k.tree, "add", "-A")
	run.Git(t, k.tree, "commit", "-q", "-m", requestMessage)
	preview := okRun(t, k.exe(opts{}, "--agree", "--preview", "--json"))
	digest := ticketkit.ParseJSON(t, preview.Stdout)["digest"].(string)
	okRun(t, k.exe(opts{}, "user", "agree", "--yes", ids, "--digest", digest, "--json"))
	run.Git(t, k.tree, "add", "-A")
	run.Git(t, k.tree, "commit", "-q", "-m", approveMessage)
}

// ---- 起こす

// env は子の環境の差分（実行ファイルの代わり・ロックの待ち）。extra が優先する。
func (k *kit) env(extra map[string]string) map[string]string {
	out := map[string]string{env.EntryPath: k.bin, "CCNAVI_LOCK_WAIT": "2"}
	for name, value := range extra {
		out[name] = value
	}
	return out
}

// hostEnv は ccnavi-ticket.sh に渡す環境。ホストのスタブの置き場を PATH の先頭に足す。
func (k *kit) hostEnv(extra map[string]string) map[string]string {
	merged := map[string]string{}
	for name, value := range extra {
		merged[name] = value
	}
	for name, value := range k.stub.Env(hoststub.EnvOpts{Path: extra[env.Path], Token: extra[env.GitLabToken]}) {
		merged[name] = value
	}
	return merged
}

// exe は実行ファイルの代わり（bin）を `--root ws` つきで起こす。
func (k *kit) exe(o opts, args ...string) run.Result {
	k.t.Helper()
	return run.Sh(k.t, k.bin, append([]string{"--root", k.ws}, args...), run.Opts{Dir: orDir(o.dir, k.ws), Input: o.input, Env: k.env(o.env)})
}

func orDir(dir, fallback string) string {
	if dir == "" {
		return fallback
	}
	return dir
}

// sh は ws の scripts の sh を起こす。ccnavi-ticket.sh は、origin の URL を指定しなければホストのスタブを付ける。
func (k *kit) sh(name string, o opts, args ...string) run.Result {
	k.t.Helper()
	extra := o.env
	if name == "ccnavi-ticket.sh" {
		if _, ok := extra["FAKE_ORIGIN_URL"]; !ok {
			extra = k.hostEnv(extra)
		}
	}
	return run.Sh(k.t, filepath.Join(k.scripts, name), args, run.Opts{Dir: orDir(o.dir, k.ws), Input: o.input, Env: k.env(extra)})
}

func (k *kit) ticket(args ...string) run.Result {
	k.t.Helper()
	return k.sh("ccnavi-ticket.sh", opts{}, args...)
}

func (k *kit) ticketWith(env map[string]string, args ...string) run.Result {
	k.t.Helper()
	return k.sh("ccnavi-ticket.sh", opts{env: env}, args...)
}

// ticketEnv は ccnavi-ticket.sh を直に起こす（非同期など）ときの環境。
func (k *kit) ticketEnv(extra map[string]string) map[string]string { return k.env(k.hostEnv(extra)) }

// ---- git

func (k *kit) try(dir string, args ...string) run.Result {
	k.t.Helper()
	return run.Command(k.t, "git", args, run.Opts{Dir: dir})
}

func (k *kit) git(dir string, args ...string) string { k.t.Helper(); return run.Git(k.t, dir, args...) }

func (k *kit) sha(tree, ref string) string {
	k.t.Helper()
	r := k.try(tree, "rev-parse", "--verify", "-q", ref)
	if r.Code != 0 {
		return ""
	}
	return strings.TrimSpace(r.Stdout)
}

func (k *kit) head() string { k.t.Helper(); return k.sha(k.tree, "HEAD") }

// remoteSHA は origin の refs/heads/<branch>（無ければ空）。
func (k *kit) remoteSHA(branch string) string {
	k.t.Helper()
	out := k.git(k.ws, "ls-remote", "origin", "refs/heads/"+branch)
	sha, _, _ := strings.Cut(out, "\t")
	return sha
}

func (k *kit) remoteParent() string { k.t.Helper(); return k.remoteSHA(parentID) }

// pusher はリモートを動かす別の clone（ワークスペースからは「他の機械が push した」ように見える）。
func (k *kit) pusher() string {
	path := filepath.Join(k.base, "pusher")
	if !isDir(path) {
		k.git(k.base, "clone", "-q", k.remote, path)
		k.configure(path)
	}
	k.git(path, "fetch", "-q", "--prune", "origin")
	return path
}

func (k *kit) remoteCommit(rel, text string) string { return k.remoteCommitOn(parentID, rel, text) }

func (k *kit) remoteCommitOn(branch, rel, text string) string {
	path := k.pusher()
	k.git(path, "checkout", "-q", "-B", branch, "origin/"+branch)
	write(k.t, filepath.Join(path, parts(rel)), text)
	k.git(path, "add", "--", rel)
	k.git(path, "commit", "-q", "-m", "remote "+rel)
	k.git(path, "push", "-q", "origin", branch)
	return k.sha(path, "HEAD")
}

func (k *kit) dirty() string { return k.dirtyIn(k.tree) }

func (k *kit) dirtyIn(tree string) string {
	k.t.Helper()
	return k.try(tree, "status", "--porcelain", "--untracked-files=all").Stdout
}

func (k *kit) committed(rev string) []string { return k.committedIn(k.tree, rev) }

func (k *kit) committedIn(tree, rev string) []string {
	k.t.Helper()
	var out []string
	for _, line := range strings.Split(k.try(tree, "show", "--name-only", "--format=", rev).Stdout, "\n") {
		if line != "" {
			out = append(out, line)
		}
	}
	sort.Strings(out)
	return out
}

func (k *kit) subjects(n int) []string {
	k.t.Helper()
	return strings.Split(strings.TrimSpace(k.try(k.tree, "log", "-"+strconv.Itoa(n), "--format=%s").Stdout), "\n")
}

// wipNoteCommitted は wip/ の下の追跡済みのファイルを親のワークツリーに書き、コミットして push する。
func (k *kit) wipNoteCommitted() string {
	write(k.t, filepath.Join(k.tree, parts(wipNote)), "メモ\n")
	k.git(k.tree, "add", "--", wipNote)
	k.git(k.tree, "commit", "-q", "-m", "docs: wip note")
	k.git(k.tree, "push", "-q", "origin", parentID)
	return wipNote
}

func (k *kit) copyPath(ident, where string) string {
	return filepath.Join(k.tree, parts(approved), where, ident+".md")
}

func (k *kit) mark(n int, kind, data string) string {
	rel := approved + "/phases/" + parentID + "/" + strconv.Itoa(n) + "." + kind
	write(k.t, filepath.Join(k.tree, parts(rel)), data)
	return rel
}

func (k *kit) lockDir() string { return filepath.Join(k.state, "locks", "self", parentID) }

func (k *kit) childTree() string {
	tree := filepath.Join(k.ws, ".claude", "worktrees", childID)
	k.git(k.ws, "worktree", "add", "-q", tree, "-b", childID, parentID)
	return tree
}

func (k *kit) mergeHead() string {
	path := strings.TrimSpace(k.try(k.tree, "rev-parse", "--git-path", "MERGE_HEAD").Stdout)
	if !filepath.IsAbs(path) {
		path = filepath.Join(k.tree, path)
	}
	return path
}

// shim は git の前に body を挟む置き場を作り、PATH の値（先頭に足したもの）を返す。body の中の "$REAL" は本物の git。
func (k *kit) shim(name, body string) string {
	dir := filepath.Join(k.base, name)
	writeExec(k.t, filepath.Join(dir, "git"), "#!/bin/sh\nREAL='"+k.gitPath+"'\n"+body+"\nexec \"$REAL\" \"$@\"\n")
	return dir + string(os.PathListSeparator) + os.Getenv(env.Path)
}

// rawShim は git の代わりの sh をそのまま置く。
func (k *kit) rawShim(name, text string) string {
	dir := filepath.Join(k.base, name)
	writeExec(k.t, filepath.Join(dir, "git"), text)
	return dir + string(os.PathListSeparator) + os.Getenv(env.Path)
}

// finishPhaseOne はフェーズ 1 の子を着手して閉じる。
func (k *kit) finishPhaseOne() {
	t := k.t
	okRun(t, k.ticket("start", parentID))
	tree := k.childTree()
	okRun(t, k.ticket("start", childID))
	write(t, filepath.Join(tree, "wip", "design", "a.md"), "a\n")
	k.git(tree, "add", "-A")
	k.git(tree, "commit", "-q", "-m", "work")
	okRun(t, k.ticket("finish", childID))
}

func (k *kit) rejectAllPushes() {
	writeExec(k.t, filepath.Join(k.remote, "hooks", "pre-receive"), "#!/bin/sh\necho busy >&2\nexit 1\n")
}

// rejectPushes は pre-receive が最初の times 回の push を断る（負ならずっと）。
func (k *kit) rejectPushes(times int) {
	counter := filepath.Join(k.base, "rejected")
	write(k.t, counter, "0\n")
	writeExec(k.t, filepath.Join(k.remote, "hooks", "pre-receive"), "#!/bin/sh\n"+
		"n=$(cat '"+counter+"'); echo $((n + 1)) > '"+counter+"'\n"+
		`if [ `+strconv.Itoa(times)+` -lt 0 ] || [ "$n" -lt `+strconv.Itoa(times)+` ]; then echo busy >&2; exit 1; fi`+"\n")
}

// proc は非同期に起こした sh（途中でシグナルを送る試験用）。
type proc struct {
	cmd            *exec.Cmd
	stdout, stderr bytes.Buffer
}

// spawn は ws から sh の name を起こして待たずに返す。環境は env に ccnavi-ticket.sh のホストのスタブを足したもの。
func (k *kit) spawn(name string, env map[string]string, args ...string) *proc {
	k.t.Helper()
	p := &proc{}
	p.cmd = exec.Command("sh", append([]string{filepath.Join(k.scripts, name)}, args...)...)
	p.cmd.Dir = k.ws
	p.cmd.Env = run.Environment(run.Opts{Env: k.ticketEnv(env)})
	p.cmd.Stdout, p.cmd.Stderr = &p.stdout, &p.stderr
	p.cmd.WaitDelay = 30 * time.Second
	base.Uncache()
	if err := p.cmd.Start(); err != nil {
		k.t.Fatal(err)
	}
	k.t.Cleanup(func() { _ = p.cmd.Process.Kill() })
	return p
}

// termWhen は mark ができるのを待ち、TERM を送って終わるのを待つ。標準エラーを返す。
func (p *proc) termWhen(t *testing.T, mark string) string {
	t.Helper()
	deadline := time.Now().Add(60 * time.Second)
	for !exists(mark) && time.Now().Before(deadline) {
		time.Sleep(50 * time.Millisecond)
	}
	if !exists(mark) {
		t.Fatal("目印まで届かなかった")
	}
	if err := p.cmd.Process.Signal(syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}
	_ = p.cmd.Wait()
	return p.stderr.String()
}
