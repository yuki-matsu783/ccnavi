package start

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
	"ccnavi/internal/harness/ticketkit"
)

// `ccnavi-ticket.sh` の start・finish・approval-request・consolidate が打つ事前確認の受入テスト。
//
// ホストの API と git のリモートが使える状態かを、ロックを取る前に確かめる（直接作業では確かめない）。ホストはスタブ（失敗する gh / glab、
// routes.json で答える curl）で、本物のネットワークには出ない。項目ごとの判定は Go の preflight の表が見る。

const (
	preParent = "i0001"
	preChild  = "i0001-01-01"
)

// preflightExe は実行ファイルの代わり。`user` の入口は、ラッパーのオプション（--root など）の後の最初の語。ターミナル要求を切る
// オプションを前に置くと読み替わらないので、`user` の呼び出しでは後ろに付ける（ほかは `--` を挟む呼び出しがあるので前に付ける）。
const preflightExe = `#!/bin/sh
skip=""; first=""
for a in "$@"; do
  if [ -n "$skip" ]; then skip=""; continue; fi
  case "$a" in
  --root | --cwd | --record-writes | --record-tree) skip=1 ;;
  --record-commit | --record-merge) skip=1 ;;
  --root=* | --cwd=* | --record-writes=* | --record-tree=*) ;;
  --record-commit=* | --record-merge=*) ;;
  *) first="$a"; break ;;
  esac
done
if [ "$first" = user ]; then
  exec '%EXE%' "$@" --guard-ticket-approval disable
fi
exec '%EXE%' --guard-ticket-approval disable "$@"
`

func preGood() routes {
	return routes{
		"GET " + hoststub.Project:                    ok(obj{"id": 42}),
		"GET " + hoststub.API + "/user":              ok(obj{"id": 5}),
		"GET " + hoststub.Project + "/members/all/5": ok(obj{"access_level": 40}),
	}
}

// preflightEnv はワークスペース（main）、bare のリモート、承認済みで送ってある親のワークツリー .claude/worktrees/i0001。
type preflightEnv struct {
	t       testing.TB
	tr      *ticketkit.Tree
	bare    string
	scripts string
	bin     string
	stub    hoststub.Stub
}

func newPreflight(t testing.TB) *preflightEnv {
	t.Helper()
	shkit.Need(t, "sh", "git")
	tr := ticketkit.New(t)
	shkit.Identity(t, tr.Root)
	tr.ProposeTicket(preParent, ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}})
	tr.ProposeTicket(preChild, ticketkit.TicketOpts{Parent: preParent, Phase: 1, Allow: []string{"wip/design/*"}, NoReview: true})
	tr.CommitParent("approval-request")
	ticketkit.OK(t, tr.Approve())
	p := &preflightEnv{t: t, tr: tr}
	// 全部の sh を写す（ccnavi-ticket.sh は多くの部品を呼ぶ）。
	src := base.ScriptsDir()
	entries, err := os.ReadDir(src)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range entries {
		if !e.IsDir() && strings.HasSuffix(e.Name(), ".sh") {
			names = append(names, e.Name())
		}
	}
	p.scripts = shkit.CopyScripts(t, tr.Root, names...)
	base.PutMessages(t, tr.Root)
	appendTo(t, filepath.Join(tr.Root, ".git", "info", "exclude"), "logs/\n.ccnavi/\n")
	p.bare = filepath.Join(tr.Root, "origin.git")
	git(t, tr.Root, "init", "-q", "--bare", "-b", "main", p.bare)
	git(t, tr.Root, "remote", "add", "origin", p.bare)
	git(t, tr.Root, "push", "-q", "-u", "origin", "main")
	git(t, tr.Root, "remote", "set-head", "origin", "main")
	git(t, tr.ParentTree, "push", "-q", "-u", "origin", preParent)
	p.bin = shkit.WriteExec(t, filepath.Join(tr.Root, "..", "bin-"+filepath.Base(tr.Root), "ccnavi"), strings.ReplaceAll(preflightExe, "%EXE%", exeShPath(t)))
	p.stub = hoststub.Build(t, base.TempDir(t), true)
	return p
}

func (p *preflightEnv) setRoutes(r routes) { p.stub.WriteRoutes(p.t, r) }

func (p *preflightEnv) requests() []string {
	data, err := os.ReadFile(p.stub.Log)
	if err != nil {
		return nil
	}
	return strings.Split(strings.TrimRight(string(data), "\n"), "\n")
}

// ticket は ccnavi-ticket.sh を起こす。token があれば GITLAB_TOKEN に渡し、path があれば PATH の後ろ側にする。
func (p *preflightEnv) ticket(token, path string, args ...string) run.Result {
	p.t.Helper()
	env := p.stub.Env(hoststub.EnvOpts{Token: token, Path: path})
	env["CCNAVI_ENTRY_PATH"] = p.bin
	env["CCNAVI_LOCK_WAIT"] = "2"
	return runScript(p.t, filepath.Join(p.scripts, "ccnavi-ticket.sh"), p.tr.Root, env, args...)
}

func (p *preflightEnv) lockDir() string { return filepath.Join(p.tr.State, "locks", "self", preParent) }

func (p *preflightEnv) remoteSHA() string {
	p.t.Helper()
	line := git(p.t, p.tr.Root, "ls-remote", "origin", "refs/heads/"+preParent)
	f := strings.Fields(line)
	if len(f) == 0 {
		return ""
	}
	return f[0]
}

func (p *preflightEnv) dirty() string {
	return git(p.t, p.tr.ParentTree, "status", "--porcelain", "--untracked-files=all")
}

// gitShim は ls-remote --heads だけ差し替える git のスタブ。呼ばれたことを ls-remote.log に残す。PATH に足す値と、記録のパスを返す。
func (p *preflightEnv) gitShim(body string) (string, string) {
	p.t.Helper()
	dir := filepath.Dir(p.bin)
	shim := filepath.Join(dir, "shim")
	log := filepath.Join(dir, "ls-remote.log")
	shkit.WriteExec(p.t, filepath.Join(shim, "git"), "#!/bin/sh\n"+
		"case \" $* \" in\n"+
		"*\" ls-remote --heads \"*)\n"+
		"  echo ls-remote >> '"+shkit.Slash(log)+"'\n"+
		body+"\n"+
		"  ;;\n"+
		"esac\n"+
		"self=$(dirname \"$0\")\n"+
		"rest=''\n"+
		"oldifs=$IFS; IFS=:\n"+
		"for p in $PATH; do [ \"$p\" = \"$self\" ] || rest=\"$rest:$p\"; done\n"+
		"IFS=$oldifs\n"+
		"PATH=\"${rest#:}\"\n"+
		"exec git \"$@\"\n")
	return shim + string(os.PathListSeparator) + os.Getenv("PATH"), log
}

func (p *preflightEnv) untouched(head, remote string) {
	p.t.Helper()
	eq(p.t, sha(p.t, p.tr.ParentTree, "HEAD"), head, "親の HEAD")
	eq(p.t, p.remoteSHA(), remote, "リモートの親")
	truth(p.t, !shkit.Exists(p.lockDir()), "ロックを取った")
	eq(p.t, p.dirty(), "", "親の作業ツリー")
}

func TestPreflightInsufficientRoleStopsStartWithoutLockWriteOrPush(t *testing.T) {
	t.Parallel()
	p := newPreflight(t)
	good := preGood()
	good["GET "+hoststub.Project+"/members/all/5"] = ok(obj{"access_level": 20})
	p.setRoutes(good)
	head, remote := sha(t, p.tr.ParentTree, "HEAD"), p.remoteSHA()
	r := code(t, p.ticket("t0k", "", "start", preParent), 1)
	has(t, r.Stderr, "確認: access: NG", "次: ")
	lacks(t, out(r), "t0k")
	eq(t, r.Stdout, "", "標準出力")
	p.untouched(head, remote)
	lacks(t, strings.Join(p.requests(), " "), "POST")
}

// ホストの API を確かめられなくても、git のリモートは確かめる（ls-remote を打つ）。
func TestPreflightUnverifiedAPIWithAWorkingGitContinuesWithANote(t *testing.T) {
	t.Parallel()
	p := newPreflight(t)
	path, log := p.gitShim("  :")
	r := code(t, p.ticket("", path, "start", preParent), 0)
	has(t, r.Stderr, "確認: api: 未確認", "確認: access: 省略", "このあとの review-request・confirm・ready はホストの API が要る")
	lacks(t, r.Stdout, "確認:")
	eq(t, p.remoteSHA(), sha(t, p.tr.ParentTree, "HEAD"), "リモートの親")
	has(t, shkit.Read(t, log), "ls-remote")
}

func TestPreflightAnUnverifiedGitStopsWithoutWriting(t *testing.T) {
	t.Parallel()
	p := newPreflight(t)
	path, _ := p.gitShim("  echo 'fatal: unable to access: Could not resolve host' >&2\n  exit 128")
	head, remote := sha(t, p.tr.ParentTree, "HEAD"), p.remoteSHA()
	r := code(t, p.ticket("", path, "start", preParent), 1)
	has(t, r.Stderr, "確認: git: 未確認")
	p.untouched(head, remote)
}

// 全部 ok なら何も出さない。事前確認だけが `GET user` を読む（外側と内側の 2 回起動しても、確かめるのは内側の 1 回）。
func TestPreflightProposeKeepsItsFirstLineAndAsksTheHostOnce(t *testing.T) {
	t.Parallel()
	p := newPreflight(t)
	p.setRoutes(preGood())
	r := p.ticket("t0k", "", "approval-request", preParent)
	n := 0
	for _, line := range p.requests() {
		if line == "GET "+hoststub.API+"/user" {
			n++
		}
	}
	eq(t, n, 1, "GET user の回数")
	lacks(t, out(r), "確認:")
	lacks(t, r.Stderr, "注意:")
}

// 取り消しのほかの動詞（start・finish・approval-request・consolidate）は、sh の同じ 1 行で事前確認する。ここでは子の finish を代表に見る。
func TestPreflightChildFinishIsCheckedButCancelIsNot(t *testing.T) {
	t.Parallel()
	p := newPreflight(t)
	good := preGood()
	good["GET "+hoststub.Project+"/members/all/5"] = ok(obj{"access_level": 20})
	p.setRoutes(good)
	r := code(t, p.ticket("t0k", "", "finish", preChild), 1)
	has(t, r.Stderr, "確認: access: NG")
	// 取り消しは、権限が足りないときでも事前確認で止めない
	cancel := p.ticket("t0k", "", "cancel", preParent, "--reason", "やめた")
	lacks(t, cancel.Stderr, "確認:")
}

func TestPreflightStatusAndNextIDDoNotCheck(t *testing.T) {
	t.Parallel()
	p := newPreflight(t)
	p.setRoutes(preGood())
	p.ticket("t0k", "", "status", preParent)
	p.ticket("t0k", "", "next-id")
	eq(t, len(p.requests()), 0, "ホストへの要求の数")
}
