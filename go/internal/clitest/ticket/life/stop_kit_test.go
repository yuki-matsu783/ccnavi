package life

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// Stop の促し（finish・integrate・request・feedback）の受入が共有する道具。道具を外から呼んで応答だけを見る。

// Stop の促しの印（理由の先頭に付く）。
const (
	codeFinish    = "NUDGE_TICKET_FINISH"
	codeIntegrate = "NUDGE_INTEGRATE"
	codeRequest   = "NUDGE_TICKET_REQUEST"
	codeFeedback  = "NUDGE_TICKET_FEEDBACK"
	codeStopRule  = "NUDGE_STOP_RULE"
)

// stopOpts は stop の任意の指定。零値は「Stop・enable・セッション s1・連鎖の 1 回目」。
type stopOpts struct {
	Active  bool
	Mode    string
	Event   string
	AgentID string
	Session string
	Extra   []string
}

// stop は cwd で Stop（または Event）の hook を 1 回起こす。
func stop(tr *ticketkit.Tree, cwd string, opts ...stopOpts) run.Result {
	tr.T.Helper()
	var o stopOpts
	if len(opts) > 0 {
		o = opts[0]
	}
	if o.Event == "" {
		o.Event = "Stop"
	}
	if o.Session == "" {
		o.Session = "s1"
	}
	if o.Mode == "" {
		o.Mode = "enable"
	}
	payload := map[string]any{"hook_event_name": o.Event, "cwd": cwd, "session_id": o.Session, "stop_hook_active": o.Active}
	if o.AgentID != "" {
		payload["agent_id"] = o.AgentID
	}
	if o.Mode == "disable" {
		// disable を言えるのは起動した環境だけ（フラグの disable は enable になる）。
		raw, err := json.Marshal(payload)
		if err != nil {
			tr.T.Fatal(err)
		}
		return tr.Call(ticketkit.Call{Stdin: string(raw), Env: map[string]string{"CCNAVI_MODE": "disable"}}, o.Extra...)
	}
	return tr.CallJSON(payload, append([]string{"--mode", o.Mode}, o.Extra...)...)
}

// body は hook の出力（JSON）。出力が空なら空の map。終了コードが 0 でなければ止める。
func body(t testing.TB, r run.Result) map[string]any {
	t.Helper()
	if r.Code != 0 {
		t.Fatalf("終了コード %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	out := map[string]any{}
	if strings.TrimSpace(r.Stdout) != "" {
		jsonOf(t, r.Stdout, &out)
	}
	return out
}

// blocks は hook の出力が引き止めている（decision: block）か。
func blocks(b map[string]any) bool { return b["decision"] == "block" }

func reasonOf(b map[string]any) string {
	s, _ := b["reason"].(string)
	return s
}

// quiet は引き止めておらず、code の促しも出していないことを確かめる。
func quiet(t testing.TB, r run.Result, code string) {
	t.Helper()
	b := body(t, r)
	if _, ok := b["decision"]; ok {
		t.Errorf("引き止めた: %s", r.Stdout)
	}
	ticketkit.Lacks(t, r.Stdout, code)
}

// notNudged は code の促しだけを出していないことを確かめる（別の理由で引き止めることはある）。
func notNudged(t testing.TB, r run.Result, code string) {
	t.Helper()
	body(t, r)
	ticketkit.Lacks(t, r.Stdout, code)
}

// asked は code の促しで引き止めたことを確かめて、理由の文を返す。
func asked(t testing.TB, r run.Result, code string) string {
	t.Helper()
	b := body(t, r)
	if !blocks(b) {
		t.Fatalf("引き止めていない: %s", r.Stdout)
	}
	reason := reasonOf(b)
	ticketkit.Has(t, reason, "[ccnavi] "+code)
	return reason
}

func commitWork(tr *ticketkit.Tree, tree, rel string) {
	tr.T.Helper()
	writeIn(tr.T, tree, rel, "x\n")
	tr.Git(tree, "add", "-A")
	tr.Git(tree, "commit", "--quiet", "-m", "work")
}

// addStopRule は共通のルールに「Stop の 2 回に 1 回」のルールを足す（促しと同じ Stop で重なる形を作る）。
func addStopRule(tr *ticketkit.Tree) {
	tr.T.Helper()
	path := baseCommonRules(tr.Root)
	var rules map[string]any
	jsonOf(tr.T, ticketkit.Read(tr.T, path), &rules)
	allow, _ := rules["allow"].([]any)
	rules["allow"] = append(allow, map[string]any{"id": "review", "match": "Stop", "glob": "*", "every": 2, "additionalContext": "振り返る"})
	raw, err := json.Marshal(rules)
	if err != nil {
		tr.T.Fatal(err)
	}
	ticketkit.Write(tr.T, path, string(raw))
}

// runningMark は実行中のサブエージェントの印のファイル。
func runningMark(tr *ticketkit.Tree, agent, session string) string {
	if session == "" {
		session = "s1"
	}
	return filepath.Join(tr.State, "subagent-"+session+"-"+agent+".running")
}

func startAgent(tr *ticketkit.Tree, agent, cwd string) {
	tr.T.Helper()
	ticketkit.OK(tr.T, tr.Hook("SubagentStart", "", cwd, nil, ticketkit.HookOpts{AgentID: agent}))
}

func stopAgent(tr *ticketkit.Tree, agent, cwd string) run.Result {
	tr.T.Helper()
	return tr.Hook("SubagentStop", "", cwd, nil, ticketkit.HookOpts{AgentID: agent})
}

// holdsWhileASubagentRuns は、実行中のサブエージェントが居る間は委任して待つ回なので促さず、見送った回は「1 回だけ」を進めないので、
// 終わったあとの Stop でまた促せることを確かめる。cwd は促しの出る場所、nudge はその場所での Stop。
func holdsWhileASubagentRuns(tr *ticketkit.Tree, cwd string, nudge func() run.Result, code string) {
	t := tr.T
	t.Helper()
	startAgent(tr, "a1", cwd)
	if !ticketkit.Exists(runningMark(tr, "a1", "")) {
		t.Error("実行中の印が無い")
	}
	notNudged(t, nudge(), code)
	notNudged(t, nudge(), code)
	ticketkit.OK(t, stopAgent(tr, "a1", cwd))
	if ticketkit.Exists(runningMark(tr, "a1", "")) {
		t.Error("終わったのに印が残った")
	}
	asked(t, nudge(), code)
	notNudged(t, nudge(), code)
}

func lastDecisionLine(tr *ticketkit.Tree) map[string]any {
	tr.T.Helper()
	lines := ticketkit.Lines(ticketkit.Read(tr.T, filepath.Join(tr.Root, "logs", "decisions.jsonl")))
	var line map[string]any
	jsonOf(tr.T, lines[len(lines)-1], &line)
	return line
}

func mustRemove(t testing.TB, path string) {
	t.Helper()
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
}
