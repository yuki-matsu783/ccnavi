package host

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"ccnavi/internal/harness/fakegitlab"
	"ccnavi/internal/harness/gitlabapi"
	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/records/diaglog"
)

// API は相手の GitLab の API。使うメソッドだけを持つ（gitlabapi.Client が満たす。テストは偽の相手を入れる）。
type API interface {
	Reader
	Call(method, path, token string, payload any, tag string) (int, any)
	Version(token string) (int, any)
	CreateProject(token, path string) (int, any)
	DeleteProject(token string, id any) (int, any)
	AddMember(token string, id any, userID, level int) (int, any)
	CreateDiscussion(token string, id any, iid any, body, tag string) (int, any)
	ResolveDiscussion(token string, id any, iid any, discussionID string, resolved bool) (int, any)
	Approve(token string, id any, iid any) (int, any)
}

// Reader は step の終わりの相手側の状態を読む API だけ。読むだけにすることは、呼び手（Take）が守る。
type Reader interface {
	ListMRs(token string, id any, state string) (int, any)
	GetMR(token string, id any, iid any) (int, any)
	ListDiscussions(token string, id any, iid any) (int, any)
	ListApprovals(token string, id any, iid any) (int, any)
	ListIssues(token string, id any, state string, perPage int) (int, any)
}

// Docker は Docker の GitLab への接続（M1 の docker 操作が決める）。
type Docker struct {
	// Base は GitLab の根（`http://127.0.0.1:8929`）。
	Base string
	// TokensFile はトークンのファイル（gitlabapi.TokenKey・ReviewerTokenKey・ReviewerIDKey の行）。読むのは使うたび。
	TokensFile string
}

// Manager は相手の用意と片付け。起こした模擬を持つ（プロセス内の表。Host.Dir がキー）。
type Manager struct {
	// Git は本物の git を実行する関数。必須。
	Git GitFunc
	// BuildStub は dir に PATH のスタブを置く（curl が false なら curl は置かない）。必須（hoststub.Build は testing が要るので、呼び手が包む）。
	BuildStub func(dir string, curl bool) (hoststub.Stub, error)
	// Docker は docker-gitlab を使うときの接続。nil なら docker-gitlab は使えない。
	Docker *Docker
	// NewClient は base の GitLab の API 入口を作る。nil なら gitlabapi.NewClient（テストは偽の相手を返す）。
	NewClient func(base string) API
	// Sleep は Docker のプロジェクトが消えるのを待つ間隔。nil なら time.Sleep。
	Sleep func(time.Duration)
	// TempDir は置き場（ccnavi-host-*）を作る親。空なら os.TempDir。
	TempDir string
	// Out は案内の出力先。nil なら os.Stdout。
	Out io.Writer

	mu      sync.Mutex
	servers map[string]*fakegitlab.Server
}

func (m *Manager) out() io.Writer {
	if m.Out != nil {
		return m.Out
	}
	return os.Stdout
}

func (m *Manager) sleep(d time.Duration) {
	if m.Sleep != nil {
		m.Sleep(d)
		return
	}
	time.Sleep(d)
}

func (m *Manager) client(base string) API {
	if m.NewClient != nil {
		return m.NewClient(base)
	}
	return gitlabapi.NewClient(base)
}

func log(root string) *diaglog.Logger { return diaglog.Get(LogName, root) }

// docker は docker-gitlab の接続: (API 入口, エージェント役のトークン, 根の URL, トークンのファイルの値)。
// トークンは呼ぶたびにファイルから読む。
func (m *Manager) docker() (API, string, string, map[string]string, error) {
	if m.Docker == nil {
		return nil, "", "", nil, errors.New("docker-gitlab の接続先が決まっていない")
	}
	values := gitlabapi.ReadEnvFile(m.Docker.TokensFile)
	token := values[gitlabapi.TokenKey]
	if token == "" {
		return nil, "", "", nil, fmt.Errorf("トークンのファイル %s に %s が無い", m.Docker.TokensFile, gitlabapi.TokenKey)
	}
	return m.client(m.Docker.Base), token, m.Docker.Base, values, nil
}

// AgentToken は docker-gitlab のエージェント役のトークン。
func (m *Manager) AgentToken() (string, error) {
	_, token, _, _, err := m.docker()
	return token, err
}

// CheckDocker は Docker の GitLab が応答するかを HTTP で見る（何も作らない）。応答すれば GitLab の版。
func (m *Manager) CheckDocker() (string, error) {
	client, token, base, _, err := m.docker()
	if err != nil {
		return "", err
	}
	status, body := client.Version(token)
	if status != 200 {
		return "", fmt.Errorf("%s の GitLab が応答しない（HTTP %d）", base, status)
	}
	return gitlabapi.Text(gitlabapi.AsMap(body)["version"]), nil
}

// Setup は Prepare の入力（setup.host と setup.host_state）。
type Setup struct {
	// Kind は setup.host（空なら Default）。
	Kind Kind
	// State は fake-gitlab の初期状態の上書き（DefaultState に重ねる）。
	State map[string]any
}

// Prepare はスタブの置き場を作り、fake-gitlab のときは模擬を、docker-gitlab のときはプロジェクトを作る。
// 失敗したら、作ったものを消してからエラーを返す。
func (m *Manager) Prepare(root, top string, setup Setup, scenarioID string) (*Host, error) {
	kind := setup.Kind
	if kind == "" {
		kind = Default
	}
	place, err := os.MkdirTemp(m.TempDir, "ccnavi-host-")
	if err != nil {
		return nil, err
	}
	h := &Host{Kind: kind, Dir: place, Top: top}
	if err := m.prepare(root, h, setup, scenarioID); err != nil {
		m.Teardown(root, h)
		return nil, err
	}
	return h, nil
}

func (m *Manager) prepare(root string, h *Host, setup Setup, scenarioID string) error {
	top, place := h.Top, h.Dir
	var err error
	if h.RealOrigin, err = realOrigin(top, m.Git); err != nil {
		return err
	}
	h.RemoteRefs = readRemoteRefs(top, m.Git)
	if h.Kind != DockerGitLab {
		h.Bare = filepath.Join(place, "origin.git")
		if err := makeBare(top, h.Bare, m.Git); err != nil {
			return err
		}
	}
	common := m.Git([]string{"rev-parse", "--path-format=absolute", "--git-common-dir"}, GitOpts{Dir: top})
	h.TargetCommon = common.out()
	stub, err := m.BuildStub(place, h.Kind == Bare)
	if err != nil {
		return err
	}
	h.Bin, h.Routes, h.Log = stub.BinDir, stub.RoutesPath, stub.Log
	if h.Kind == DockerGitLab {
		if err := m.makeDockerProject(root, h, scenarioID); err != nil {
			return err
		}
	}
	if h.Kind == FakeGitLab {
		state := DefaultState()
		for k, v := range setup.State {
			state[k] = v
		}
		h.Project, _ = state["project"].(string)
		h.StatePath = filepath.Join(place, "fake-gitlab.json")
		server, err := fakegitlab.New(fakegitlab.Config{StatePath: h.StatePath, Initial: state, Skew: fakegitlab.DefaultSkew})
		if err != nil {
			return err
		}
		m.setServer(place, server)
		h.Port, h.Token = server.Port(), server.Token
	}
	log(root).Info("ホストのスタブを置いた", "host", string(h.Kind), "dir", place, "port", h.Port, "target", top)
	return nil
}

func (m *Manager) setServer(dir string, s *fakegitlab.Server) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.servers == nil {
		m.servers = map[string]*fakegitlab.Server{}
	}
	m.servers[dir] = s
}

func (m *Manager) takeServer(dir string) *fakegitlab.Server {
	m.mu.Lock()
	defer m.mu.Unlock()
	s := m.servers[dir]
	delete(m.servers, dir)
	return s
}

// Server は起こしてある模擬（テストが受けた要求を見るため）。無ければ nil。
func (m *Manager) Server(h *Host) *fakegitlab.Server {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.servers[h.Dir]
}

// makeDockerProject は Docker の GitLab に使い捨てのプロジェクトを作り、人間役をメンバーに加え、main を push する。
// 識別子は作った直後に h へ入れる（以後の失敗でも Teardown が消せる）。root の PAT は h に入れない（state.json に残る）。
func (m *Manager) makeDockerProject(root string, h *Host, scenarioID string) error {
	client, token, base, values, err := m.docker()
	if err != nil {
		return err
	}
	if status, body := client.Version(token); status == 200 {
		h.Version = gitlabapi.Text(gitlabapi.AsMap(body)["version"])
	}
	reviewer, err := strconv.Atoi(values[gitlabapi.ReviewerIDKey])
	if err != nil {
		return fmt.Errorf("トークンのファイルの %s が数字ではない", gitlabapi.ReviewerIDKey)
	}
	name := ProjectName(scenarioID, time.Now(), randHex())
	status, body := client.CreateProject(token, name)
	created := gitlabapi.AsMap(body)
	id, ok := gitlabapi.Int(created["id"])
	if status != 201 || !ok {
		return fmt.Errorf("プロジェクト %s を作れない（HTTP %d）", name, status)
	}
	h.ProjectID, h.Project, h.Base = id, gitlabapi.Text(created["path_with_namespace"]), base
	h.URL = base + "/" + h.Project
	h.Bare = h.URL + ".git" // origin の通信先（git のスタブが向け直す先）
	log(root).Info("プロジェクトを作った", "project_id", h.ProjectID, "url", h.URL)
	if status, _ := client.AddMember(token, h.ProjectID, reviewer, developer); status != 200 && status != 201 {
		return fmt.Errorf("人間役をメンバーに加えられない（HTTP %d）", status)
	}
	env := gitEnvFor(token)
	via := h.GitVia()
	r := m.Git(append(append([]string{}, via...), "push", "-q", "origin", "refs/heads/main:refs/heads/main"), GitOpts{Dir: h.Top, Env: env})
	if r.Code != 0 {
		return fmt.Errorf("main を push できない: %s", strings.TrimSpace(r.Stderr))
	}
	// 対象に残る本物の origin の remote-tracking ref が、新規プロジェクトの issue #1 などの番号を含む
	// ブランチ候補と衝突しないよう、消す。戻しは Teardown の restoreRemoteRefs。
	r = m.Git(append(append([]string{}, via...), "fetch", "-q", "--prune", "--no-tags", "origin"), GitOpts{Dir: h.Top, Env: env})
	if r.Code != 0 {
		return fmt.Errorf("古い refs/remotes/origin/* を消せない: %s", strings.TrimSpace(r.Stderr))
	}
	return nil
}

func randHex() string {
	b := make([]byte, 2)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// gitEnvFor は docker-gitlab の credential helper が読む環境（helper は GITLAB_TOKEN を返し、端末に聞かない）。
func gitEnvFor(token string) map[string]string {
	return map[string]string{env.GitLabToken: token, "GIT_TERMINAL_PROMPT": "0"}
}

// GitEnv は runner 自身の git にだけ渡す環境（claude の環境や state.json には出さない）。docker-gitlab 以外は無し。
func (m *Manager) GitEnv(h *Host) (map[string]string, error) {
	if h.Kind != DockerGitLab {
		return nil, nil
	}
	token, err := m.AgentToken()
	if err != nil {
		return nil, err
	}
	return gitEnvFor(token), nil
}

// StepEnv は claude に渡す環境（親の環境を継承し、本物のトークン系だけ除いて、スタブを足す）。
func (m *Manager) StepEnv(h *Host, parent []string) (map[string]string, error) {
	token := ""
	if h.Kind == DockerGitLab {
		var err error
		if token, err = m.AgentToken(); err != nil {
			return nil, err
		}
	}
	return h.StepEnv(parent, token), nil
}

// Access は相手側の状態を読む API の入口。
type Access struct {
	Client  API
	Token   string
	Project any
	// Secrets は、記録に混ざったら `***` に置き換えるトークンの値（人間役のものを含む）。
	Secrets []string
}

// APIAccess は相手側の状態を読む API の入口。fake-gitlab は模擬のポートとトークン、docker-gitlab はエージェント役のトークン。
// Client は書き込みのメソッドも持つ。読むだけにすることは、呼び手（Take）が守る。
func (m *Manager) APIAccess(h *Host) (Access, error) {
	switch h.Kind {
	case DockerGitLab:
		client, token, _, values, err := m.docker()
		if err != nil {
			return Access{}, err
		}
		var secrets []string
		for k, v := range values {
			if strings.HasSuffix(strings.ToUpper(k), "TOKEN") && v != "" {
				secrets = append(secrets, v)
			}
		}
		return Access{client, token, h.ProjectID, secrets}, nil
	case FakeGitLab:
		client := m.client(fmt.Sprintf("http://127.0.0.1:%d", h.Port))
		if c, ok := client.(*gitlabapi.Client); ok {
			c.Sleep = func(time.Duration) {} // 模擬は手元で答えるので、失敗を待って試し直さない
		}
		return Access{client, h.Token, url.PathEscape(h.Project), []string{h.Token}}, nil
	}
	return Access{}, fmt.Errorf("%s は相手側の API を読めない", h.Kind)
}

// Hold は人間の操作で止めるとき。模擬は止め、置き場（bare・スタブ・模擬の状態）は残す。
func (m *Manager) Hold(root string, h *Host) {
	if s := m.takeServer(h.Dir); s != nil {
		s.Close()
		log(root).Info("模擬を止めた（置き場は残す）", "dir", h.Dir, "port", h.Port)
	}
}

// Resume は `next` で続けるとき。止めてある模擬を、残した状態ファイルのまま（初期状態なしで）同じポートで起こし直す。
// ポートが取れなければ、空けてから打ち直すよう言って止まる（まれなので、別のポートで続けることはしない）。
func (m *Manager) Resume(root string, h *Host) error {
	if h.Kind != FakeGitLab || m.Server(h) != nil {
		return nil
	}
	server, err := fakegitlab.New(fakegitlab.Config{StatePath: h.StatePath, Skew: fakegitlab.DefaultSkew, Port: h.Port})
	if err != nil {
		return fmt.Errorf("ポート %d が使われている。空けてから next を打ち直す", h.Port)
	}
	m.setServer(h.Dir, server)
	log(root).Info("模擬を起こし直した", "dir", h.Dir, "port", h.Port)
	return nil
}

// deleteProject は Docker の GitLab のプロジェクトを消す。消せたら "" 、消せなかったら理由。
// GitLab 19 は遅延削除で、1 回目の DELETE（202）のあとも GET が 200（marked_for_deletion_on 付き）のまま残る。
// その間に 2 回目を `permanently_remove=true&full_path=<改名後のパス>` で打つ。
func (m *Manager) deleteProject(root string, h *Host) string {
	client, token, _, _, err := m.docker()
	if err != nil {
		return err.Error()
	}
	status, _ := client.DeleteProject(token, h.ProjectID)
	if status == 404 {
		return ""
	}
	if status != 200 && status != 202 && status != 204 {
		return fmt.Sprintf("DELETE が HTTP %d", status)
	}
	path := fmt.Sprintf("/projects/%d", h.ProjectID)
	permanent := false
	for range deleteWait {
		status, body := client.Call("GET", path, token, nil, "")
		if status == 404 {
			log(root).Info("プロジェクトを消した", "project_id", h.ProjectID, "url", h.URL)
			return ""
		}
		if info := gitlabapi.AsMap(body); !permanent && info["marked_for_deletion_on"] != nil && info["marked_for_deletion_on"] != "" {
			full := url.QueryEscape(gitlabapi.Text(info["path_with_namespace"]))
			client.Call("DELETE", path+"?permanently_remove=true&full_path="+full, token, nil, "")
			permanent = true
		}
		m.sleep(time.Second)
	}
	return fmt.Sprintf("%d 秒待っても 404 にならない", deleteWait)
}

// Teardown は模擬を止め、refs/remotes/origin/* を戻し、置き場を消す。何度呼んでもよい。
// refs を戻せなければ案内を出して、置き場を残したまま true を返す。docker-gitlab のプロジェクトは消せるまで h に残し、
// 消せなかったら true を返す（再び呼べば、消す試みだけ続ける）。
func (m *Manager) Teardown(root string, h *Host) (failed bool) {
	if h == nil {
		return false
	}
	if h.ProjectID != 0 && !h.Deleted {
		if why := m.deleteProject(root, h); why == "" {
			h.Deleted = true
		} else {
			failed = true
			log(root).Error("プロジェクトを消せなかった", "project_id", h.ProjectID, "err", why)
			fmt.Fprintf(m.out(), "案内: docker-gitlab のプロジェクト %s（ID %d）を消せなかった: %s。手作業で消す\n", h.URL, h.ProjectID, why)
		}
	}
	if h.Down {
		return failed
	}
	if s := m.takeServer(h.Dir); s != nil {
		s.Close()
	}
	if h.RemoteRefs != nil {
		if info, err := os.Stat(h.Top); err == nil && info.IsDir() {
			if err := restoreRemoteRefs(h.Top, h.RemoteRefs, m.Git); err != nil {
				// まれな失敗なので、直し方を言って止める（置き場は残す。直してから打ち直せば、戻す試みだけ続く）。
				log(root).Error("refs/remotes/origin を戻せなかった", "target", h.Top, "err", err.Error())
				fmt.Fprintf(m.out(), "案内: %s の refs/remotes/origin/* を戻せなかった: %v。直してから restore を打ち直す\n", h.Top, err)
				return true
			}
		}
	}
	_ = os.RemoveAll(h.Dir)
	h.Down = true
	log(root).Info("ホストのスタブを片付けた", "host", string(h.Kind), "dir", h.Dir)
	return failed
}

// HumanAccess は人間役の API 入口とトークンのファイルの値（人間役のトークンはここから読む）。
func (m *Manager) HumanAccess() (API, map[string]string, error) {
	client, _, _, values, err := m.docker()
	return client, values, err
}
