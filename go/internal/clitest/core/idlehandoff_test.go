package core

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
)

// 放置したセッションの引き継ぎ促し（`ccnavi idle`）の受入テスト。待機の始めと起こす指示・ユーザの入力での取り消し・
// 知らせが 1 セッション 1 回であることを、`ccnavi idle` を起こして見る。会話の大きさの読み方・閾値と基準の刻み・状態遷移の細部は
// go/internal/records/handoff の go test が見る。

const (
	idleSession = "sess-1"
	// 起こす指示に付ける目印（ユーザが打った文と見分ける）。
	idleMarker = "[idle-handoff]"
)

// assistantRow は会話の記録の 1 行（assistant の使用量）。
func assistantRow(tokens, short, long int) string {
	b, _ := json.Marshal(map[string]any{"type": "assistant", "isSidechain": false, "message": map[string]any{"usage": map[string]any{
		"input_tokens":                2,
		"cache_read_input_tokens":     tokens - 2 - short - long,
		"cache_creation_input_tokens": short + long,
		"cache_creation":              map[string]any{"ephemeral_5m_input_tokens": short, "ephemeral_1h_input_tokens": long},
	}}})
	return string(b)
}

type idleEnv struct {
	t          *testing.T
	dir, root  string
	transcript string
}

func newIdleEnv(t *testing.T) *idleEnv {
	dir := emptyRoot(t)
	root := mkdir(t, filepath.Join(dir, "ws"))
	return &idleEnv{t, dir, root, writeFile(t, dir, "t.jsonl", assistantRow(500_000, 0, 10)+"\n")}
}

func (e *idleEnv) payload(event string, extra map[string]any) string {
	body := map[string]any{"hook_event_name": event, "session_id": idleSession, "transcript_path": e.transcript}
	for k, v := range extra {
		body[k] = v
	}
	return mustJSON(e.t, body)
}

func (e *idleEnv) idle(verb, payload string, env map[string]string, rest ...string) run.Result {
	e.t.Helper()
	merged := map[string]string{"CCNAVI_HANDOFF_WAIT_SECONDS": "7"}
	for k, v := range env {
		merged[k] = v
	}
	return run.Ccnavi(e.t, append([]string{"--root", e.root, "idle", verb}, rest...), run.Opts{Input: payload, Env: merged})
}

// lines は `キー 値` の行を map にする。
func lines(text string) map[string]string {
	m := map[string]string{}
	for _, l := range strings.Split(text, "\n") {
		if k, v, ok := strings.Cut(l, " "); ok {
			m[k] = v
		}
	}
	return m
}

func (e *idleEnv) started(session string) map[string]string {
	e.t.Helper()
	r := e.idle("start", e.payload("Stop", map[string]any{"session_id": session}), nil)
	if r.Code != 0 {
		e.t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	return lines(r.Stdout)
}

func TestIdleStartThenCheckFiresAndResetCancels(t *testing.T) {
	t.Parallel()
	e := newIdleEnv(t)
	first := e.started(idleSession)
	if first["wait"] != "7" || first["session"] != idleSession || first["token"] == "" {
		t.Errorf("first = %v", first)
	}
	fired := e.idle("check", "", nil, idleSession, first["token"], "7")
	if !strings.HasPrefix(fired.Stdout, "fire\n") || !strings.Contains(fired.Stdout, idleMarker) {
		t.Errorf("fired = %q", fired.Stdout)
	}
	// 新しい待機を始めてから、ユーザの入力で取り消すと起こさない。
	second := e.started("sess-3")
	e.idle("reset", e.payload("UserPromptSubmit", map[string]any{"prompt": "続けます", "session_id": "sess-3"}), nil)
	if skipped := e.idle("check", "", nil, "sess-3", second["token"], "7"); !strings.HasPrefix(skipped.Stdout, "skip") {
		t.Errorf("skipped = %q", skipped.Stdout)
	}
}

func TestIdleStartSaysWhyItDoesNotStart(t *testing.T) {
	t.Parallel()
	e := newIdleEnv(t)
	cases := []struct {
		name    string
		payload string
		env     map[string]string
		want    string
	}{
		{"サブエージェント", e.payload("Stop", map[string]any{"agent_id": "agent-1"}), nil, "skip subagent\n"},
		{"sdk", e.payload("Stop", nil), map[string]string{"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}, "skip unattended\n"},
		{"無人", e.payload("Stop", nil), map[string]string{"CLAUDE_CODE_SESSION_ATTENDED": "0"}, "skip unattended\n"},
		{"閾値 0 は切る", e.payload("Stop", nil), map[string]string{"CCNAVI_HANDOFF_SUGGEST_TOKENS": "0"}, "skip disabled\n"},
		{"閾値に届かない", e.payload("Stop", nil), map[string]string{"CCNAVI_HANDOFF_SUGGEST_TOKENS": "600000"}, "skip not-due\n"},
	}
	for _, c := range cases {
		if got := e.idle("start", c.payload, c.env).Stdout; got != c.want {
			t.Errorf("%s: stdout = %q, want %q", c.name, got, c.want)
		}
	}
}

// notice は systemMessage の JSON を 1 回だけ出し、起こした後は報告を 1 回だけ出す。
func TestIdleNoticeIsOncePerSessionAndReportsAfterFiring(t *testing.T) {
	t.Parallel()
	e := newIdleEnv(t)
	first := e.idle("notice", e.payload("Stop", nil), nil)
	second := e.idle("notice", e.payload("Stop", nil), nil)
	mustContain(t, "1 回目", first.Stdout, "systemMessage")
	if second.Stdout != "" {
		t.Errorf("2 回目が出た: %q", second.Stdout)
	}
	// 別のセッションで、起こした後の報告を見る。
	started := e.started("sess-2")
	e.idle("check", "", nil, "sess-2", started["token"], "7")
	reported := e.idle("notice", e.payload("Stop", map[string]any{"session_id": "sess-2"}), nil)
	again := e.idle("notice", e.payload("Stop", map[string]any{"session_id": "sess-2"}), nil)
	mustContain(t, "起こした後の報告", reported.Stdout, "放置されたため")
	if again.Stdout != "" {
		t.Errorf("報告が 2 回出た: %q", again.Stdout)
	}
}

func TestIdleDoesNotStopWithoutAPayload(t *testing.T) {
	t.Parallel()
	e := newIdleEnv(t)
	for _, verb := range []string{"notice", "start", "reset"} {
		if r := e.idle(verb, "", nil); r.Code != 0 {
			t.Errorf("%s: code = %d: %s", verb, r.Code, r.Stderr)
		}
	}
}
