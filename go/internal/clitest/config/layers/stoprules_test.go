package layers

import (
	"encoding/json"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/configws"
	"ccnavi/internal/harness/run"
)

// `match: Stop` のルールを、hook を通して確かめる数本。
//
// 数えの規則・止めた文・記録は go/internal/hook/events/stoprules_test.go、lint の苦情は go/internal/policy/rulelint の見本、
// `--test Stop` と期待する判定は go/internal/entry/diagnose が見る。ここに残すのは、実行ファイルと hook を通した次の経路だけ。
//
//  1. `every: N` で N 回に 1 度、`decision: block` で止め、ルールの文を `reason` に載せる
//  2. `dry-run` でも同じ回に止める（止めないと文がモデルに届かない）。`disable` では止めない
//  3. 数えは compact・再開・clear では消えず、起動（startup）でだけ消える

const (
	nudgeText = "ここまでの作業を振り返り、スキルの候補が要るかを見ること。"
	nudgeCode = "NUDGE_STOP_RULE"
)

type stopper struct {
	t    *testing.T
	root string
}

func newStopper(t *testing.T, every int) *stopper {
	t.Helper()
	s := &stopper{t: t, root: base.TempDir(t)}
	body := configws.Obj{
		"version": 1,
		"deny":    []configws.Obj{{"id": "push", "match": "Bash", "glob": "*git push*", "message": "push はユーザが行う"}},
		"ask":     []configws.Obj{},
		"allow":   []configws.Obj{{"id": "skill-review", "match": "Stop", "glob": "*", "additionalContext": nudgeText, "every": every}},
	}
	configws.Write(t, base.CommonPath(s.root, "rules"), configws.Body(body))
	return s
}

// stop は Stop を 1 回。mode が空なら `--mode` を渡さない。disable を言えるのは起動した環境だけ（フラグの disable は enable になる）。
func (s *stopper) stop(mode string, env map[string]string) map[string]any {
	s.t.Helper()
	payload := configws.Body(configws.Obj{"hook_event_name": "Stop", "session_id": "s1", "cwd": s.root, "stop_hook_active": false})
	args := []string{"--root", s.root}
	if mode != "" {
		args = append(args, "--mode", mode)
	}
	r := run.Ccnavi(s.t, args, run.Opts{Input: payload, Dir: base.Root(), Env: env})
	if r.Code != 0 {
		s.t.Fatalf("code = %d\n%s", r.Code, r.Stderr)
	}
	body := map[string]any{}
	if strings.TrimSpace(r.Stdout) != "" {
		if err := json.Unmarshal([]byte(r.Stdout), &body); err != nil {
			s.t.Fatalf("%v\n%s", err, r.Stdout)
		}
	}
	return body
}

// blocked は止めた文。止めていなければ空。
func (s *stopper) blocked(body map[string]any) string {
	s.t.Helper()
	if body["decision"] != "block" {
		if raw, _ := json.Marshal(body); strings.Contains(string(raw), nudgeCode) {
			s.t.Errorf("止めていないのに %s が出た: %s", nudgeCode, raw)
		}
		return ""
	}
	reason, _ := body["reason"].(string)
	return reason
}

func (s *stopper) sessionStart(source string) {
	s.t.Helper()
	payload := configws.Body(configws.Obj{"hook_event_name": "SessionStart", "session_id": "s1", "cwd": s.root, "source": source})
	if r := run.Ccnavi(s.t, []string{"--root", s.root}, run.Opts{Input: payload, Dir: base.Root()}); r.Code != 0 {
		s.t.Fatalf("code = %d\n%s", r.Code, r.Stderr)
	}
}

func (s *stopper) isBlocked(times int) []bool {
	s.t.Helper()
	var out []bool
	for range times {
		out = append(out, s.blocked(s.stop("enable", nil)) != "")
	}
	return out
}

func TestStopRuleEveryCountsStops(t *testing.T) {
	t.Parallel()
	s := newStopper(t, 3)
	var got []string
	for range 6 {
		got = append(got, s.blocked(s.stop("enable", nil)))
	}
	for i, g := range got {
		if (g != "") != (i == 2 || i == 5) {
			t.Errorf("%d 回目: 止めた = %v", i+1, g != "")
		}
	}
	if !strings.HasPrefix(got[2], nudgeCode+": ") || !strings.Contains(got[2], nudgeText) {
		t.Errorf("止めた文 = %q", got[2])
	}
}

func TestStopRuleDryRunBlocksLikeEnable(t *testing.T) {
	t.Parallel()
	s := newStopper(t, 2)
	if first := s.stop("dry-run", nil); first["decision"] != nil {
		t.Errorf("1 回目から止めた: %v", first)
	}
	body := s.stop("dry-run", nil)
	if body["decision"] != "block" || !strings.Contains(body["reason"].(string), nudgeText) {
		t.Errorf("2 回目に止めない: %v", body)
	}
	if msg, _ := body["systemMessage"].(string); strings.Contains(msg, "would have blocked") {
		t.Errorf("dry-run でも止める: %q", msg)
	}
}

func TestStopRuleModeDisableNeverBlocks(t *testing.T) {
	t.Parallel()
	s := newStopper(t, 2)
	off := map[string]string{"CCNAVI_MODE": "disable"}
	for i := range 4 {
		if got := s.blocked(s.stop("", off)); got != "" {
			t.Errorf("%d 回目に止めた: %q", i+1, got)
		}
	}
}

func TestStopRuleCountSurvivesCompactResumeAndClear(t *testing.T) {
	t.Parallel()
	s := newStopper(t, 3)
	var got []bool
	for _, source := range []string{"compact", "resume", "clear"} {
		got = append(got, s.blocked(s.stop("enable", nil)) != "")
		s.sessionStart(source)
	}
	if want := []bool{false, false, true}; !slices.Equal(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestStopRuleCountIsDroppedOnStartup(t *testing.T) {
	t.Parallel()
	s := newStopper(t, 3)
	s.stop("enable", nil)
	s.stop("enable", nil)
	s.sessionStart("startup")
	if got, want := s.isBlocked(3), []bool{false, false, true}; !slices.Equal(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}
