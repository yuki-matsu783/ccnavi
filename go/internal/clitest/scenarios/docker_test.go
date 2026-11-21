package scenarios

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/host"
	"ccnavi/internal/harness/scenario/runner"
	"ccnavi/internal/harness/scenario/world"
)

const (
	rootPAT       = "glpat-root-agent-secret"
	reviewerToken = "glpat-human-reviewer-secret"
	dockerBase    = "http://127.0.0.1:8929"
)

// fakeDocker は Docker の GitLab の偽の相手。DELETE は遅延削除（1 回目は印を付けるだけ）で受け、fail なら消えない。
type fakeDocker struct {
	host.API
	mu            sync.Mutex
	fail, marked  bool
	gone          bool
	versionStatus int
	calls         [][2]string
	human         []string // 人間役の操作（呼び出し:トークン）
}

func (f *fakeDocker) Version(string) (int, any) {
	return f.versionStatus, map[string]any{"version": "19.4.1"}
}
func (f *fakeDocker) CreateProject(_, name string) (int, any) {
	return 201, map[string]any{"id": 7, "path_with_namespace": "root/sc-x"}
}
func (f *fakeDocker) AddMember(string, any, int, int) (int, any) { return 201, map[string]any{} }
func (f *fakeDocker) DeleteProject(token string, id any) (int, any) {
	return f.Call("DELETE", "/projects/7", token, nil, "")
}
func (f *fakeDocker) Call(method, path, token string, _ any, _ string) (int, any) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, [2]string{method, path})
	if token != rootPAT {
		panic("エージェント役以外のトークンで呼んだ")
	}
	if method == "DELETE" {
		if strings.Contains(path, "permanently_remove=true") && !f.fail {
			f.gone = true
		} else {
			f.marked = true
		}
		return 202, map[string]any{"message": "202 Accepted"}
	}
	if f.gone {
		return 404, map[string]any{}
	}
	body := map[string]any{"path_with_namespace": "root/sc-x-deletion_scheduled-7"}
	if f.marked {
		body["marked_for_deletion_on"] = "2026-10-26"
	}
	return 200, body
}

func (f *fakeDocker) deletes() int {
	n := 0
	for _, c := range f.calls {
		if c[0] == "DELETE" {
			n++
		}
	}
	return n
}

func (f *fakeDocker) record(name, token string) {
	f.mu.Lock()
	f.human = append(f.human, name+":"+token)
	f.mu.Unlock()
}

func (f *fakeDocker) ListMRs(token string, _ any, _ string) (int, any) {
	if token == reviewerToken {
		f.record("list_mrs", token)
		return 200, []any{map[string]any{"iid": 3}}
	}
	return 200, []any{map[string]any{"iid": 3, "title": "i0001", "state": "opened"}}
}
func (f *fakeDocker) GetMR(_ string, _ any, iid any) (int, any) {
	return 200, map[string]any{"iid": iid, "title": "i0001", "state": "opened", "draft": true}
}
func (f *fakeDocker) ListDiscussions(string, any, any) (int, any) {
	note := map[string]any{"id": 1, "body": "鍵 " + rootPAT, "resolvable": true, "resolved": false}
	return 200, []any{map[string]any{"id": "d1", "notes": []any{note}}}
}
func (f *fakeDocker) ListApprovals(string, any, any) (int, any) {
	who := map[string]any{"username": "root", "name": "x", "id": 1}
	return 200, map[string]any{"approved": true, "approved_by": []any{map[string]any{"approved_at": "t", "user": who}}}
}
func (f *fakeDocker) ListIssues(string, any, string, int) (int, any) { return 200, []any{} }
func (f *fakeDocker) CreateDiscussion(token string, _ any, _ any, _, _ string) (int, any) {
	f.record("create_discussion", token)
	return 201, map[string]any{"id": "d9"}
}
func (f *fakeDocker) ResolveDiscussion(token string, _ any, _ any, _ string, _ bool) (int, any) {
	f.record("resolve_discussion", token)
	return 200, map[string]any{}
}
func (f *fakeDocker) Approve(token string, _ any, _ any) (int, any) {
	f.record("approve", token)
	return 201, map[string]any{}
}

// dockerRig は偽の Docker の GitLab を相手にする実行器。
func dockerRig(t *testing.T, w ws) (*rig, *fakeDocker) {
	t.Helper()
	g := newRig(t, w)
	fake := &fakeDocker{versionStatus: 200}
	tokens := filepath.Join(w.tmp, "tokens.env")
	write(t, tokens, "GITLAB_TOKEN="+rootPAT+"\nCCNAVI_PROBE_REVIEWER_TOKEN="+reviewerToken+"\nCCNAVI_PROBE_REVIEWER_ID=5\n")
	m := g.r.Manager
	m.Docker = &host.Docker{Base: dockerBase, TokensFile: tokens}
	m.NewClient = func(string) host.API { return fake }
	m.Sleep = func(time.Duration) {}
	m.Git = func(args []string, o host.GitOpts) host.GitResult {
		for _, a := range args { // 偽の GitLab へは push も fetch もしない
			if a == "push" || a == "fetch" {
				return host.GitResult{}
			}
		}
		return world.Git(args, o)
	}
	return g, fake
}

func dockerScenario(t *testing.T, w ws, steps []map[string]any) string {
	return hostScenario(t, w, "docker-gitlab", steps, nil)
}

func TestDockerFinishDeletesTheProjectAndNoTokenReachesTheRecords(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g, fake := dockerRig(t, w)
	g.r.Environ = append(os.Environ(), "CCNAVI_PROBE_REVIEWER_TOKEN="+reviewerToken, "GITLAB_TOKEN=parent-token")
	var env map[string]string
	g.fn = func(in runner.StepInput) (*core.Stream, error) { env = in.Env; return stepOut, nil }
	g.r.Run(w.runOpts(dockerScenario(t, w, []map[string]any{{"prompt": "a"}, {"prompt": "b"}})))
	if env["GITLAB_TOKEN"] != rootPAT || env["FAKE_CRED_URL"] != dockerBase || env["CCNAVI_PROBE_REVIEWER_TOKEN"] != "" || strings.Contains(jsonOf(env), reviewerToken) {
		t.Errorf("step の環境にエージェント役のトークンだけを渡す: %v", env)
	}
	if !fake.gone || fake.deletes() != 2 { // 遅延削除なので 2 回目で確定する
		t.Errorf("消えていない: gone = %v・DELETE %d 回", fake.gone, fake.deletes())
	}
	entries, _ := os.ReadDir(w.out)
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "step-") || e.IsDir() {
			continue
		}
		text := read(t, filepath.Join(w.out, e.Name()))
		if strings.Contains(text, rootPAT) || strings.Contains(text, reviewerToken) {
			t.Errorf("%s にトークンが出ている", e.Name())
		}
	}
	for n := 1; n <= 2; n++ {
		snap := read(t, filepath.Join(w.out, "host-"+itoa(n)+".json"))
		if !contains(snap, `"host": "docker-gitlab"`, "***", `"mrs_approved": 1`) || strings.Contains(snap, rootPAT) {
			t.Errorf("host-%d.json = %s", n, snap)
		}
	}
	if md := read(t, filepath.Join(w.out, "report.md")); !contains(md, "docker-gitlab", "GitLab 19.4.1") {
		t.Error("report に GitLab の版が無い")
	}
}

func TestDockerADeleteFailureIsExit3AndNamesTheProject(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g, fake := dockerRig(t, w)
	fake.fail = true
	if rc := g.r.Run(w.runOpts(dockerScenario(t, w, nil))); rc != 3 {
		t.Fatalf("rc = %d\n%s", rc, g.text())
	}
	rs, _ := judgeReport(w.out)
	for _, r := range rs {
		if r.Kind == "host-delete" {
			if e, _ := r.Expect.(string); !strings.Contains(e, dockerBase+"/root/sc-x") || r.Verdict != core.Fail {
				t.Errorf("host-delete の行 = %+v", r)
			}
			return
		}
	}
	t.Error("host-delete の行が無い")
}

func TestDockerInterruptAndSetupFailureDeleteTheProject(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g, fake := dockerRig(t, w)
	g.fn = func(runner.StepInput) (*core.Stream, error) { panic("割り込み") }
	if p := panics(func() { g.r.Run(w.runOpts(dockerScenario(t, w, nil))) }); p == nil || !fake.gone {
		t.Errorf("割り込みでもプロジェクトを消す: panic = %v・gone = %v", p, fake.gone)
	}
	w2 := hostWs(t)
	g2, fake2 := dockerRig(t, w2)
	path := hostScenario(t, w2, "docker-gitlab", nil, map[string]any{"files": map[string]any{"projects/test-repo/README.md/x": "y"}})
	scn, err := core.LoadFile(path, core.AgentClaude)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := g2.r.StartScenario(scn, path, w2.root, w2.target, w2.out); err == nil || !fake2.gone {
		t.Errorf("setup の失敗でもプロジェクトを消す: %v・gone = %v", err, fake2.gone)
	}
}

func TestDockerAHumanStepKeepsTheProjectUntilRestoreAndRestoreReportsAFailure(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g, fake := dockerRig(t, w)
	steps := []map[string]any{{"prompt": "a"}, {"human": true, "instruction": "i", "check": []string{"c"}}}
	if rc := g.r.Run(w.runOpts(dockerScenario(t, w, steps))); rc != 10 {
		t.Fatalf("rc = %d\n%s", rc, g.text())
	}
	if fake.deletes() != 0 || strings.Contains(read(t, filepath.Join(w.out, "state.json")), rootPAT) || !exists(filepath.Join(w.out, "host-1.json")) || exists(filepath.Join(w.out, "host-2.json")) {
		t.Error("止めた人間の step ではプロジェクトを消さず、state にトークンを出さない")
	}
	fake.fail = true
	if rc := g.r.Restore(w.out); rc != 3 {
		t.Errorf("消せなければ 3: rc = %d", rc)
	}
	fake.fail = false
	if rc := g.r.Restore(w.out); rc != 0 || !fake.gone {
		t.Errorf("消せれば 0: rc = %d・gone = %v", rc, fake.gone)
	}
}

func TestDockerAStoppedDockerRefusesOnlyThatScenario(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g, fake := dockerRig(t, w)
	fake.versionStatus = 0
	docker, err := core.LoadFile(dockerScenario(t, w, nil), core.AgentClaude)
	if err != nil {
		t.Fatal(err)
	}
	bare, err := core.LoadFile(hostScenario(t, w, "bare", nil, nil), core.AgentClaude)
	if err != nil {
		t.Fatal(err)
	}
	err = world.CheckBeforeRun(docker, w.root, w.target, g.r.Manager)
	var r *core.Refusal
	if !errors.As(err, &r) || !strings.Contains(err.Error(), "GitLab") || r.Next == "" {
		t.Errorf("= %v", err)
	}
	if err := world.CheckBeforeRun(bare, w.root, w.target, g.r.Manager); err != nil {
		t.Errorf("docker を使わないシナリオは断らない: %v", err)
	}
}

var humanAPI = []map[string]any{
	{"op": "thread", "body": "直してください", "as": "t1"},
	{"op": "resolve", "thread": "t1"},
}

func TestDockerHumanAPITokenStaysOutOfTheRecordsAndResumeDoesNotRunOpsTwice(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g, fake := dockerRig(t, w)
	steps := []map[string]any{{"prompt": "a"}, {"human": true, "human_api": humanAPI}, {"human": true, "instruction": "手で操作する"}, {"prompt": "b"}}
	if rc := g.r.Run(w.runOpts(dockerScenario(t, w, steps))); rc != 10 {
		t.Fatalf("rc = %d\n%s", rc, g.text())
	}
	done := append([]string(nil), fake.human...)
	if len(done) != 4 { // 2 操作が MR の一覧と本体を 1 回ずつ
		t.Errorf("人間役の呼び出し = %v", done)
	}
	for _, c := range done {
		if !strings.HasSuffix(c, ":"+reviewerToken) {
			t.Errorf("人間役のトークンで呼ぶ: %s", c)
		}
	}
	var texts []string
	entries, _ := os.ReadDir(w.out)
	for _, e := range entries {
		if !strings.HasPrefix(e.Name(), "step-") && !e.IsDir() {
			texts = append(texts, read(t, filepath.Join(w.out, e.Name())))
		}
	}
	diag, _ := filepath.Glob(filepath.Join(w.root, "logs", "diag", "*"))
	for _, p := range diag {
		texts = append(texts, read(t, p))
	}
	all := strings.Join(texts, "\n")
	for _, secret := range []string{reviewerToken, rootPAT, "CCNAVI_PROBE_REVIEWER_TOKEN"} {
		if strings.Contains(all, secret) {
			t.Errorf("記録に %s が出ている", secret)
		}
	}
	if rc := g.r.Next(w.out); rc != 0 && rc != 2 {
		t.Fatalf("next: rc = %d\n%s", rc, g.text())
	}
	if len(fake.human) != len(done) || !fake.gone {
		t.Errorf("再開しても同じ操作を二度実行しない: %v", fake.human)
	}
}
