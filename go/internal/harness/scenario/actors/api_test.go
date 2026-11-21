package actors

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/host"
)

const (
	rootPAT     = "root-pat-0001"
	reviewerPAT = "reviewer-pat-0002"
)

// humanAPI は人間役の呼び出しを記録する偽の GitLab。
type humanAPI struct {
	host.API
	calls            []string
	tokens           map[string]bool
	mrs              []any
	discussionStatus int
}

func (h *humanAPI) note(call, token string) {
	h.calls = append(h.calls, call)
	h.tokens[token] = true
}

func (h *humanAPI) ListMRs(token string, id any, state string) (int, any) {
	h.note("list_mrs", token)
	return 200, h.mrs
}

func (h *humanAPI) CreateDiscussion(token string, id any, iid any, body, tag string) (int, any) {
	h.note("create_discussion "+body, token)
	return h.discussionStatus, map[string]any{"id": "d2"}
}

func (h *humanAPI) ResolveDiscussion(token string, id any, iid any, did string, resolved bool) (int, any) {
	h.note("resolve "+did, token)
	return 200, map[string]any{}
}

func (h *humanAPI) Approve(token string, id any, iid any) (int, any) {
	h.note("approve", token)
	return 201, map[string]any{}
}

func newHuman(t *testing.T) (*host.Manager, *humanAPI) {
	t.Helper()
	tokens := filepath.Join(base.TempDir(t), "tokens.env")
	body := "GITLAB_TOKEN=" + rootPAT + "\nCCNAVI_PROBE_REVIEWER_TOKEN=" + reviewerPAT + "\n"
	if err := os.WriteFile(tokens, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	api := &humanAPI{tokens: map[string]bool{}, mrs: []any{map[string]any{"iid": 3}}, discussionStatus: 201}
	m := &host.Manager{Docker: &host.Docker{Base: "http://127.0.0.1:8929", TokensFile: tokens}, NewClient: func(string) host.API { return api }, Sleep: func(time.Duration) {}}
	return m, api
}

func runAPI(t *testing.T, m *host.Manager, st *core.State, ops []core.Op) (int, string) {
	t.Helper()
	var out strings.Builder
	st.Host = &host.Host{Kind: host.DockerGitLab, ProjectID: 7}
	saves := 0
	rc := RunAPI(m, st, 1, ops, base.TempDir(t), func() error { saves++; return nil }, func(s string) { out.WriteString(s + "\n") })
	if saves == 0 {
		t.Error("state を 1 度も書いていない")
	}
	return rc, out.String()
}

var threadThenResolve = []core.Op{{Op: core.OpThread, Body: "直してください", As: "t1"}, {Op: core.OpResolve, Thread: "t1"}}

// 操作は書いた順に、人間役のトークンで実行し、止まらずに進む。
func TestHumanAPIOpsRunInOrderWithTheHumanToken(t *testing.T) {
	t.Parallel()
	m, api := newHuman(t)
	st := &core.State{}
	rc, out := runAPI(t, m, st, threadThenResolve)
	want := "list_mrs|create_discussion 直してください|list_mrs|resolve d2"
	if rc != 0 || strings.Join(api.calls, "|") != want {
		t.Errorf("rc = %d, calls = %v", rc, api.calls)
	}
	if len(api.tokens) != 1 || !api.tokens[reviewerPAT] {
		t.Errorf("使ったトークン = %v（人間役だけのはず）", api.tokens)
	}
	if len(st.HumanOps) != 2 || st.HumanOps[0].Op != "thread" || st.HumanOps[1].DiscussionID != "d2" || st.Threads["t1"].IID != 3 || st.NextStep != 2 || st.Halt != nil {
		t.Errorf("state = %+v", st)
	}
	if !strings.Contains(out, "thread（t1）: 成功（HTTP 201）") {
		t.Errorf("out = %q", out)
	}
	if strings.Contains(out, "直してください") || strings.Contains(out, reviewerPAT) {
		t.Error("本文かトークンを表示に出した")
	}
}

func TestHumanAPIApproveUsesTheOnlyMR(t *testing.T) {
	t.Parallel()
	m, api := newHuman(t)
	if rc, _ := runAPI(t, m, &core.State{}, []core.Op{{Op: core.OpApprove}}); rc != 0 || strings.Join(api.calls, "|") != "list_mrs|approve" {
		t.Errorf("rc = %d, calls = %v", rc, api.calls)
	}
}

// MR がちょうど 1 件でなければ、API を打たずに止める。
func TestHumanAPIZeroOrTwoMRsHaltTheRun(t *testing.T) {
	t.Parallel()
	for name, mrs := range map[string][]any{"0 件": {}, "2 件": {map[string]any{"iid": 1}, map[string]any{"iid": 2}}} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			m, api := newHuman(t)
			api.mrs = mrs
			st := &core.State{}
			rc, _ := runAPI(t, m, st, threadThenResolve)
			if rc != 11 || st.Halt == nil || st.Halt.Kind != "human_api" || strings.Join(api.calls, "|") != "list_mrs" {
				t.Errorf("rc = %d, halt = %+v, calls = %v", rc, st.Halt, api.calls)
			}
		})
	}
}

// HTTP の失敗は止め、試し直さない（thread は冪等でない）。
func TestHumanAPIHTTPFailureHaltsAndIsNotRetried(t *testing.T) {
	t.Parallel()
	m, api := newHuman(t)
	api.discussionStatus = 500
	st := &core.State{}
	rc, _ := runAPI(t, m, st, threadThenResolve)
	if rc != 11 || st.Halt.Kind != "human_api" || strings.Join(api.calls, "|") != "list_mrs|create_discussion 直してください" || st.NextStep != 2 {
		t.Errorf("rc = %d, halt = %+v, calls = %v", rc, st.Halt, api.calls)
	}
	if len(st.HumanOps) != 1 || st.HumanOps[0].Error != "HTTP 500" {
		t.Errorf("human_ops = %+v", st.HumanOps)
	}
}

func TestHumanAPIResolveNeedsAThreadMadeInThisRun(t *testing.T) {
	t.Parallel()
	m, api := newHuman(t)
	st := &core.State{}
	rc, _ := runAPI(t, m, st, []core.Op{{Op: core.OpResolve, Thread: "t9"}})
	if rc != 11 || !strings.Contains(st.Halt.Reasons[0], "t9") || strings.Join(api.calls, "|") != "list_mrs" {
		t.Errorf("rc = %d, halt = %+v", rc, st.Halt)
	}
}

func TestHumanAPIWithoutTheHumanTokenHaltsWithoutUsingTheAgentToken(t *testing.T) {
	t.Parallel()
	m, api := newHuman(t)
	if err := os.WriteFile(m.Docker.TokensFile, []byte("GITLAB_TOKEN="+rootPAT+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	st := &core.State{}
	if rc, _ := runAPI(t, m, st, []core.Op{{Op: core.OpApprove}}); rc != 11 || len(api.calls) != 0 {
		t.Errorf("rc = %d, calls = %v", rc, api.calls)
	}
}
