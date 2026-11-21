package host

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
)

// fakeDocker は Docker の GitLab の偽の相手。DELETE は遅延削除（1 回目は印を付けるだけ）で受け、fail なら消えない。
type fakeDocker struct {
	API
	fail, marked, gone bool
	versionStatus      int
	calls              [][2]string
}

func (f *fakeDocker) Version(token string) (int, any) {
	return f.versionStatus, map[string]any{"version": "19.4.1"}
}

func (f *fakeDocker) CreateProject(token, name string) (int, any) {
	return 201, map[string]any{"id": 7, "path_with_namespace": "root/" + name}
}

func (f *fakeDocker) AddMember(string, any, int, int) (int, any) { return 201, map[string]any{} }

func (f *fakeDocker) DeleteProject(token string, id any) (int, any) {
	return f.Call("DELETE", "/projects/7", token, nil, "")
}

func (f *fakeDocker) Call(method, path, token string, payload any, tag string) (int, any) {
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

// dockerManager は偽の Docker の GitLab とトークンのファイルを持つ Manager。
func dockerManager(t *testing.T) (*Manager, *fakeDocker, *bytes.Buffer, *[][]string) {
	t.Helper()
	tmp := base.TempDir(t)
	tokens := filepath.Join(tmp, "tokens.env")
	content := "GITLAB_TOKEN=" + rootPAT + "\nCCNAVI_PROBE_REVIEWER_TOKEN=" + reviewerPAT + "\nCCNAVI_PROBE_REVIEWER_ID=5\n"
	if err := os.WriteFile(tokens, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	fake := &fakeDocker{versionStatus: 200}
	var out bytes.Buffer
	var gits [][]string
	m := &Manager{
		Docker:    &Docker{Base: dockerBase, TokensFile: tokens},
		NewClient: func(string) API { return fake },
		Sleep:     func(time.Duration) {},
		TempDir:   tmp,
		Out:       &out,
		Git: func(args []string, o GitOpts) GitResult {
			gits = append(gits, args)
			if len(args) > 0 && args[0] == "rev-parse" {
				return GitResult{Stdout: "/top/.git\n"}
			}
			return GitResult{}
		},
	}
	return m, fake, &out, &gits
}

func TestDockerAPIAccessMasksBothTokensInTheFile(t *testing.T) {
	t.Parallel()
	m, fake, _, _ := dockerManager(t)
	access, err := m.APIAccess(&Host{Kind: DockerGitLab, ProjectID: 1})
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]bool{}
	for _, s := range access.Secrets {
		got[s] = true
	}
	if access.Client != API(fake) || access.Token != rootPAT || access.Project != 1 || len(got) != 2 || !got[rootPAT] || !got[reviewerPAT] {
		t.Errorf("access = %+v", access)
	}
}

func TestDockerNeedsTheTokenFile(t *testing.T) {
	t.Parallel()
	m, _, _, _ := dockerManager(t)
	m.Docker.TokensFile = filepath.Join(base.TempDir(t), "none")
	if _, err := m.CheckDocker(); err == nil || !strings.Contains(err.Error(), "GITLAB_TOKEN") {
		t.Errorf("CheckDocker = %v", err)
	}
	m.Docker = nil
	if _, err := m.AgentToken(); err == nil {
		t.Error("接続先が無いのに通った")
	}
}

func TestCheckDockerReportsAStoppedGitLab(t *testing.T) {
	t.Parallel()
	m, fake, _, _ := dockerManager(t)
	if v, err := m.CheckDocker(); err != nil || v != "19.4.1" {
		t.Errorf("CheckDocker = %q, %v", v, err)
	}
	fake.versionStatus = 0
	if _, err := m.CheckDocker(); err == nil || !strings.Contains(err.Error(), "応答しない") {
		t.Errorf("CheckDocker = %v", err)
	}
}

func TestStepAndGitEnvCarryTheAgentTokenOnly(t *testing.T) {
	t.Parallel()
	m, _, _, _ := dockerManager(t)
	h := &Host{Kind: DockerGitLab, Base: dockerBase, Bare: dockerBase + "/root/p.git", RealOrigin: "o", Bin: "/b", Routes: "/r", Log: "/l", TargetCommon: "/c"}
	env, err := m.StepEnv(h, []string{"PATH=/usr/bin", "CCNAVI_PROBE_REVIEWER_TOKEN=" + reviewerPAT, "GITLAB_TOKEN=parent-token"})
	if err != nil {
		t.Fatal(err)
	}
	if env["GITLAB_TOKEN"] != rootPAT || env["FAKE_CRED_URL"] != dockerBase {
		t.Errorf("env = %v", env)
	}
	for k, v := range env {
		if v == reviewerPAT || k == "CCNAVI_PROBE_REVIEWER_TOKEN" {
			t.Errorf("人間役のトークンが渡った: %s", k)
		}
	}
	got, _ := m.GitEnv(h)
	if got["GITLAB_TOKEN"] != rootPAT || got["GIT_TERMINAL_PROMPT"] != "0" || len(got) != 2 {
		t.Errorf("GitEnv = %v", got)
	}
	if got, _ := m.GitEnv(&Host{Kind: Bare}); got != nil {
		t.Errorf("docker 以外は無し: %v", got)
	}
}

// プロジェクトを作ったら main を push し、そのあと古い refs/remotes/origin/* を消す（番号を含むブランチ候補との衝突を避ける）。
func TestMakeProjectPushesMainThenPrunesStaleRemoteRefs(t *testing.T) {
	t.Parallel()
	m, _, _, gits := dockerManager(t)
	h := &Host{Kind: DockerGitLab, RealOrigin: "o", Top: "top"}
	if err := m.makeDockerProject(base.TempDir(t), h, "UC-20"); err != nil {
		t.Fatal(err)
	}
	if h.ProjectID != 7 || !strings.HasPrefix(h.Project, "root/sc-uc-20-") || h.Bare != dockerBase+"/"+h.Project+".git" || h.Version != "19.4.1" {
		t.Errorf("host = %+v", h)
	}
	var verbs []string
	for _, args := range *gits {
		for _, a := range args {
			if a == "push" || a == "fetch" {
				verbs = append(verbs, a)
			}
		}
	}
	if strings.Join(verbs, " ") != "push fetch" {
		t.Fatalf("verbs = %v", verbs)
	}
	fetch := (*gits)[len(*gits)-1]
	joined := " " + strings.Join(fetch, " ") + " "
	if !strings.Contains(joined, " --prune ") || !strings.Contains(joined, " --no-tags ") || fetch[len(fetch)-1] != "origin" {
		t.Errorf("fetch = %v", fetch)
	}
}

func TestTeardownDeletesTheDockerProject(t *testing.T) {
	t.Parallel()
	m, fake, out, _ := dockerManager(t)
	place := filepath.Join(base.TempDir(t), "place")
	if err := os.Mkdir(place, 0o755); err != nil {
		t.Fatal(err)
	}
	h := &Host{Kind: DockerGitLab, Dir: place, ProjectID: 7, URL: dockerBase + "/root/sc-x"}
	if failed := m.Teardown("", h); failed {
		t.Fatalf("失敗した: %s", out)
	}
	if !fake.gone || fake.deletes() != 2 || !h.Deleted || !h.Down { // 遅延削除なので 2 回目で確定する
		t.Errorf("gone = %v, deletes = %d, host = %+v", fake.gone, fake.deletes(), h)
	}
	if _, err := os.Stat(place); !os.IsNotExist(err) {
		t.Error("置き場が残っている")
	}
	before := len(fake.calls)
	m.Teardown("", h) // 2 回目は何もしない
	if len(fake.calls) != before {
		t.Error("消したあとにも API を呼んだ")
	}
}

// 消せなかったプロジェクトは名指しで案内し、h に残す（再び呼べば消す試みだけ続く）。
func TestTeardownNamesAProjectItCouldNotDelete(t *testing.T) {
	t.Parallel()
	m, fake, out, _ := dockerManager(t)
	fake.fail = true
	h := &Host{Kind: DockerGitLab, Dir: filepath.Join(base.TempDir(t), "place"), ProjectID: 7, URL: dockerBase + "/root/sc-x"}
	if failed := m.Teardown("", h); !failed {
		t.Fatal("消せなかったのに成功を返した")
	}
	if h.Deleted || !strings.Contains(out.String(), dockerBase+"/root/sc-x") || !strings.Contains(out.String(), "手作業で消す") {
		t.Errorf("deleted = %v, out = %q", h.Deleted, out)
	}
	fake.fail = false
	if failed := m.Teardown("", h); failed || !h.Deleted {
		t.Errorf("再び呼んでも消せなかった: failed = %v", failed)
	}
}
