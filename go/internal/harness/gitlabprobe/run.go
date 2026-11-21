package gitlabprobe

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"ccnavi/internal/harness/gitlabapi"
	"ccnavi/internal/infra/env"
)

// probe は 1 回の実行の状態。
type probe struct {
	cfg        Config
	out, root  string
	stamp      string
	log        *os.File
	env        []string
	client     *gitlabapi.Client
	results    []Result
	apiSeq     int
	projectIDs []int
}

type done struct {
	code        int
	stdout, err string
}

func (d done) both() string { return d.stdout + d.err }

// Run は probe を回してまとめを出し、終了コードを返す（FAIL があれば 1）。
func Run(cfg Config, stderr io.Writer) int {
	if cfg.Stdout == nil {
		cfg.Stdout = os.Stdout
	}
	p := &probe{cfg: cfg}
	if err := p.setup(); err != nil {
		fmt.Fprintln(stderr, err)
		return 2
	}
	defer p.log.Close()
	func() {
		defer p.cleanupProjects()
		p.probeAll()
	}()
	return p.finish()
}

func (p *probe) setup() error {
	now := time.Now
	if p.cfg.Now != nil {
		now = p.cfg.Now
	}
	p.stamp = now().Format("0102-150405")
	out := p.cfg.Out
	if out == "" {
		dir, err := os.MkdirTemp("", "ccnavi-probe-")
		if err != nil {
			return err
		}
		out = dir
	}
	p.out = filepath.Join(out, "glrun-"+p.stamp)
	p.root = filepath.Join(p.out, "repo")
	if err := os.MkdirAll(p.out, 0o755); err != nil {
		return err
	}
	f, err := os.Create(filepath.Join(p.out, "steps.log"))
	if err != nil {
		return err
	}
	p.log = f
	p.env = p.buildEnv()
	p.client = gitlabapi.NewClient(p.cfg.GitLab)
	p.client.Recorder = p.recordAPI
	return nil
}

// buildEnv は子に渡す環境。親の CCNAVI_* と CLAUDE_PROJECT_DIR は外す。一時リポジトリをワークスペースにする（sh の ticket 系は cwd からこの根を使う）。
func (p *probe) buildEnv() []string {
	var out []string
	for _, kv := range p.cfg.Environ {
		name, _, _ := strings.Cut(kv, "=")
		if strings.HasPrefix(name, "CCNAVI_") || name == "CLAUDE_PROJECT_DIR" {
			continue
		}
		out = append(out, kv)
	}
	return append(out,
		env.GitLabToken+"="+p.cfg.RootToken,
		env.EntryPath+"="+p.cfg.Exe,
		env.Workspace+"="+p.root,
		env.GuardTicketApproval+"=disable",
		env.Mode+"=enable",
		env.GuardCoreFiles+"=disable",
		env.RestoreIfDeny+"=disable",
		"GIT_TERMINAL_PROMPT=0",
		"GCM_INTERACTIVE=never",
		"GIT_AUTHOR_NAME=probe", "GIT_AUTHOR_EMAIL=probe@example.com",
		"GIT_COMMITTER_NAME=probe", "GIT_COMMITTER_EMAIL=probe@example.com",
	)
}

func (p *probe) say(format string, a ...any) {
	text := fmt.Sprintf(format, a...)
	fmt.Fprintln(p.cfg.Stdout, text)
	fmt.Fprintln(p.log, text)
}

func (p *probe) record(name string, ok bool, detail string) { p.add(name, ok, false, detail) }

// skip は「確かめられなかった」（CE の変更要求など）。PASS には数えない。
func (p *probe) skip(name, detail string) { p.add(name, false, true, detail) }

func (p *probe) add(name string, ok, skip bool, detail string) {
	state := "FAIL"
	switch {
	case skip:
		state = "SKIP"
	case ok:
		state = "PASS"
	}
	p.results = append(p.results, Result{name, state, detail})
	p.say("%s  %s  %s", state, name, detail)
}

func (p *probe) redact(text string) string {
	return strings.NewReplacer(p.cfg.RootToken, "<root-token>", p.cfg.ReviewerToken, "<reviewer-token>").Replace(text)
}

// 詳細の先頭・末尾の n 字。
func head(s string, n int) string {
	r := []rune(strings.TrimSpace(s))
	if len(r) > n {
		r = r[:n]
	}
	return string(r)
}

func tail(s string, n int) string {
	r := []rune(strings.TrimSpace(s))
	if len(r) > n {
		r = r[len(r)-n:]
	}
	return string(r)
}

func (p *probe) run(args []string, cwd, stdin string) done {
	cmd := exec.Command(args[0], args[1:]...)
	cmd.Dir, cmd.Env = cwd, p.env
	if stdin != "" {
		cmd.Stdin = strings.NewReader(stdin)
	}
	var so, se bytes.Buffer
	cmd.Stdout, cmd.Stderr = &so, &se
	err := cmd.Run()
	d := done{stdout: so.String(), err: se.String()}
	if exit, ok := err.(*exec.ExitError); ok {
		d.code = exit.ExitCode()
	} else if err != nil {
		d.code, d.err = 127, d.err+err.Error()
	}
	fmt.Fprintf(p.log, "\n$ (%s) %s\n  rc=%d\n", cwd, p.redact(strings.Join(args, " ")), d.code)
	if s := strings.TrimSpace(d.stdout); s != "" {
		fmt.Fprintf(p.log, "  stdout: %s\n", strings.ReplaceAll(p.redact(s), "\n", "\n          "))
	}
	if s := strings.TrimSpace(d.err); s != "" {
		fmt.Fprintf(p.log, "  stderr: %s\n", strings.ReplaceAll(p.redact(s), "\n", "\n          "))
	}
	return d
}

// git は失敗したら panic で止める（probe の土台が崩れている）。
func (p *probe) git(cwd string, args ...string) string {
	d := p.run(append([]string{"git"}, args...), cwd, "")
	if d.code != 0 {
		panic(fmt.Sprintf("git %s\n%s\n%s", strings.Join(args, " "), d.stdout, d.err))
	}
	return d.stdout
}

func (p *probe) exe(stdin string, args ...string) done {
	return p.run(append([]string{p.cfg.Exe, "--root", p.root}, args...), p.cfg.Root, stdin)
}

func (p *probe) hook(event, tool, cwd string, input map[string]any) string {
	payload, _ := json.Marshal(map[string]any{"hook_event_name": event, "tool_name": tool, "cwd": cwd, "session_id": "probe", "tool_input": input})
	d := p.exe(string(payload))
	if strings.TrimSpace(d.stdout) == "" {
		return ""
	}
	var parsed struct {
		Out map[string]any `json:"hookSpecificOutput"`
	}
	if json.Unmarshal([]byte(d.stdout), &parsed) != nil {
		return d.stdout
	}
	if s := gitlabapi.Text(parsed.Out["permissionDecisionReason"]); s != "" {
		return s
	}
	return gitlabapi.Text(parsed.Out["additionalContext"])
}

func (p *probe) script(name string) string {
	return filepath.ToSlash(filepath.Join(p.cfg.Root, ".ccnavi", "scripts", name))
}

func (p *probe) sh(script, cwd string, args ...string) done { return p.shIn(script, cwd, "", args...) }

func (p *probe) shIn(script, cwd, stdin string, args ...string) done {
	return p.run(append([]string{"sh", script}, args...), cwd, stdin)
}

func (p *probe) recordAPI(e gitlabapi.Event) {
	if e.Kind == "retry" {
		fmt.Fprintf(p.log, "\nAPI %s %s 失敗 (%s) %d 回目\n", e.Method, e.Path, e.Error, e.Attempt)
		return
	}
	p.apiSeq++
	slug := e.Tag
	if slug == "" {
		slug = strings.ReplaceAll(strings.Trim(e.Path, "/"), "/", "_")
		if r := []rune(slug); len(r) > 60 {
			slug = string(r[:60])
		}
	}
	data, _ := json.MarshalIndent(map[string]any{"status": e.Status, "path": e.Path, "payload": e.Payload, "body": e.Body}, "", " ")
	_ = os.WriteFile(filepath.Join(p.out, fmt.Sprintf("api-%02d-%s-%s.json", p.apiSeq, e.Method, slug)), data, 0o644)
	fmt.Fprintf(p.log, "\nAPI %s %s -> %d\n", e.Method, e.Path, e.Status)
}

func write(path, text string) string {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		panic(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		panic(err)
	}
	return path
}

func readText(path string) string {
	data, _ := os.ReadFile(path)
	return string(data)
}

func (p *probe) worktree(name, base string) string {
	path := filepath.Join(p.root, ".claude", "worktrees", name)
	p.git(p.root, "worktree", "add", "--quiet", path, "-b", name, base)
	return path
}

func (p *probe) propose(tree, name string, o TicketOpts) string {
	return write(filepath.Join(tree, "wip", "proposals", "todo", name+".md"), TicketText(name, o))
}

func (p *probe) commitAll(tree, msg string) {
	p.git(tree, "add", "-A")
	// 変更が無い段階（start など）でも止まらない
	p.git(tree, "commit", "--quiet", "--allow-empty", "-m", msg)
}

func (p *probe) mrOf(pid int, branch string) map[string]any {
	return p.client.FindMR(p.cfg.RootToken, pid, branch)
}

func (p *probe) notes(pid, iid int) []any { return p.client.Notes(p.cfg.RootToken, pid, iid) }

func (p *probe) requestMarked(pid, iid int) bool {
	for _, n := range p.notes(pid, iid) {
		first, _, _ := strings.Cut(gitlabapi.Text(gitlabapi.AsMap(n)["body"]), "\n")
		if requestMarker.MatchString(strings.TrimSpace(first)) {
			return true
		}
	}
	return false
}

// waitBranch は push 直後にブランチが API に見えるまで待つ（400 source_branch does not exist を避ける）。
func (p *probe) waitBranch(pid int, branch string) {
	for i := 0; i < 10; i++ {
		if status, _ := p.client.GetBranch(p.cfg.RootToken, pid, branch); status == 200 {
			return
		}
		time.Sleep(time.Second)
	}
}

func (p *probe) cleanupProjects() {
	if p.cfg.Keep {
		p.say("プロジェクト %v は消さない（CCNAVI_PROBE_KEEP）", p.projectIDs)
		return
	}
	for _, id := range p.projectIDs {
		status, _ := p.client.DeleteProject(p.cfg.RootToken, id)
		after, seen := p.client.Call("GET", fmt.Sprintf("/projects/%d", id), p.cfg.RootToken, nil, "project-after")
		marked := gitlabapi.AsMap(seen)["marked_for_deletion_on"]
		p.record(fmt.Sprintf("自分が作ったプロジェクト %d を消す", id), status == 200 || status == 202 || status == 204,
			fmt.Sprintf("DELETE status=%d 直後の GET status=%d marked_for_deletion_on=%v", status, after, marked))
	}
}

func (p *probe) finish() int {
	p.say("\n==== まとめ ====")
	failed := false
	for _, r := range p.results {
		p.say("%s  %s", r.State, r.Name)
		failed = failed || r.State == "FAIL"
	}
	p.say("\n%s。置き場: %s", SummaryLine(p.results), p.out)
	if failed {
		return 1
	}
	return 0
}
