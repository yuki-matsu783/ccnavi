package judge

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/testkit"
)

// judgeOracle は Python の実行ファイルの hook の経路（cli の設定の解き方と judge.decide_before と記録）を、
// 1 件ずつ環境変数を替えて回し、標準出力・終了コード・標準エラー・記録の 1 行を返す。
const judgeOracle = `
import io, json, os, sys, time
from ccnavi.hook import judge
from ccnavi.infra import hookio, modes, settings
from ccnavi.policy import selfguard, subjects
from ccnavi.records import audit
c = json.load(sys.stdin)
root = c["root"]
base = {k: v for k, v in os.environ.items() if not k.startswith(("CCNAVI_", "CLAUDE_"))}
out = []
for case in c["cases"]:
    os.environ.clear()
    os.environ.update(base)
    os.environ.update(case["env"])
    err, stdout = io.StringIO(), io.StringIO()
    answer = {"code": 0, "stdout": "", "stderr": "", "record": None, "error": ""}
    try:
        conf, _ = settings.load(root)
        conf.guard_ticket_approval = selfguard.resolve(err, "", conf.guard_ticket_approval,
            settings.GUARD_TICKET_APPROVAL_ENV, selfguard.GATE_SETTINGS)
        conf.ticket_control = selfguard.resolve(err, "", conf.ticket_control,
            settings.TICKET_CONTROL_ENV, selfguard.GATE_SETTINGS)
        mode = modes.resolve_mode(err, None, conf)
        conf.restore_if_deny = selfguard.resolve(err, "", conf.restore_if_deny, settings.RESTORE_IF_DENY_ENV)
        conf.guard_core_files = selfguard.resolve(err, "", conf.guard_core_files, settings.GUARD_CORE_FILES_ENV)
        conf.guard_unwatched = selfguard.resolve(err, "", conf.guard_unwatched,
            settings.GUARD_UNWATCHED_ENV, selfguard.GATE_SETTINGS)
        payload = hookio.decode(io.StringIO(case["payload"]))
        record = audit.Record(mode=mode, permission_mode=payload.permission_mode, event=payload.event,
            tool=payload.tool_name, session=payload.session_id, agent=payload.agent_id)
        record.subject = subjects.subject_of(payload)
        answer["code"] = judge.decide_before(stdout, err, mode, conf, root, payload, record,
            time.monotonic() + judge.DEADLINE_SECONDS)
        audit.Log(conf.log).write(record)
        with open(conf.log, encoding="utf-8") as f:
            answer["record"] = json.loads(f.read().splitlines()[-1])
    except Exception as exc:
        answer["error"] = type(exc).__name__ + ": " + str(exc)
    answer["stdout"], answer["stderr"] = stdout.getvalue(), err.getvalue()
    out.append(answer)
print(json.dumps(out, ensure_ascii=False))
`

// judgeCase は 1 回の呼び出し。Payload と Env の {ROOT} は、回すワークスペースのルートに置き換える。
type judgeCase struct {
	Name    string            `json:"name,omitempty"`
	Payload string            `json:"payload"`
	Env     map[string]string `json:"env"`
}

type judgeAnswer struct {
	Code   int            `json:"code"`
	Stdout string         `json:"stdout"`
	Stderr string         `json:"stderr"`
	Record map[string]any `json:"record"`
	Error  string         `json:"error"`
}

const rootMark = "{ROOT}"

func (c judgeCase) at(root string) judgeCase {
	envs := map[string]string{}
	for k, v := range c.Env {
		envs[k] = strings.ReplaceAll(v, rootMark, root)
	}
	return judgeCase{Name: c.Name, Payload: strings.ReplaceAll(c.Payload, rootMark, jsonInner(root)), Env: envs}
}

// jsonInner は JSON の文字列の中に置く形（Windows の `\` を逃がす）。
func jsonInner(s string) string {
	b, _ := json.Marshal(s)
	return string(b[1 : len(b)-1])
}

// setEnv は ccnavi と Claude Code の環境変数を外してから、case の値を入れる。戻す関数を返す。
func setEnv(t *testing.T, values map[string]string) func() {
	t.Helper()
	saved := os.Environ()
	for _, kv := range saved {
		name, _, _ := strings.Cut(kv, "=")
		if strings.HasPrefix(name, "CCNAVI_") || strings.HasPrefix(name, "CLAUDE_") {
			os.Unsetenv(name)
		}
	}
	for k, v := range values {
		os.Setenv(k, v)
	}
	return func() {
		os.Clearenv()
		for _, kv := range saved {
			name, value, _ := strings.Cut(kv, "=")
			os.Setenv(name, value)
		}
	}
}

// runGo は judgeOracle と同じ手順を Go で回す。
func runGo(t *testing.T, root string, c judgeCase) judgeAnswer {
	t.Helper()
	restore := setEnv(t, c.Env)
	defer restore()
	var stdout, stderr bytes.Buffer
	answer := judgeAnswer{}
	conf, _ := config.Load(root)
	conf.GuardTicketApproval = string(selfguard.Resolve(&stderr, "", conf.GuardTicketApproval, env.GuardTicketApproval, selfguard.GateChoices))
	conf.TicketControl = string(selfguard.Resolve(&stderr, "", conf.TicketControl, env.TicketControl, selfguard.GateChoices))
	mode := config.ResolveMode(&stderr, "", conf)
	conf.RestoreIfDeny = string(selfguard.Resolve(&stderr, "", conf.RestoreIfDeny, env.RestoreIfDeny, selfguard.Choices))
	conf.GuardCoreFiles = string(selfguard.Resolve(&stderr, "", conf.GuardCoreFiles, env.GuardCoreFiles, selfguard.Choices))
	conf.GuardUnwatched = string(selfguard.Resolve(&stderr, "", conf.GuardUnwatched, env.GuardUnwatched, selfguard.GateChoices))
	in, err := hookio.Decode(strings.NewReader(c.Payload))
	if err != nil {
		answer.Error = err.Error()
		return answer
	}
	record := &audit.Record{Mode: mode, PermissionMode: in.PermissionMode, Event: string(in.Event), Tool: in.ToolName,
		Session: in.SessionID, Agent: in.AgentID}
	record.Subject = rules.SubjectOf(in)
	code, err := Before(&stdout, &stderr, mode, conf, root, in, record, time.Now().Add(config.HookDeadline.Value()))
	answer.Code = code
	if err != nil {
		answer.Error = err.Error()
	} else if err := audit.NewLog(conf.Log).Write(record); err != nil {
		answer.Error = err.Error()
	} else {
		answer.Record = lastRecord(t, conf.Log)
	}
	answer.Stdout, answer.Stderr = stdout.String(), stderr.String()
	return answer
}

func lastRecord(t *testing.T, path string) map[string]any {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var last string
	s := bufio.NewScanner(f)
	s.Buffer(make([]byte, 1<<20), 1<<26)
	for s.Scan() {
		last = s.Text()
	}
	var row map[string]any
	if err := json.Unmarshal([]byte(last), &row); err != nil {
		t.Fatal(err)
	}
	return row
}

// normalized はルートを {ROOT} に戻し、時刻と経過時間を落とした答え。
func (a judgeAnswer) normalized(root string) judgeAnswer {
	fix := func(s string) string {
		return strings.ReplaceAll(strings.ReplaceAll(s, jsonInner(root), rootMark), root, rootMark)
	}
	out := judgeAnswer{Code: a.Code, Stdout: fix(a.Stdout), Stderr: fix(a.Stderr), Error: a.Error}
	if a.Error != "" {
		out.Error = "error"
	}
	if a.Record != nil {
		data, _ := json.Marshal(a.Record)
		var row map[string]any
		_ = json.Unmarshal([]byte(fix(string(data))), &row)
		delete(row, "ts")
		delete(row, "ms")
		out.Record = row
	}
	return out
}

// compareWithPython は、同じ形のワークスペースを 2 つ作り、片方で Python、もう片方で Go を回して突き合わせる。
func compareWithPython(t *testing.T, build func(t *testing.T) string, cases []judgeCase) {
	t.Helper()
	pyRoot, goRoot := build(t), build(t)
	var pyCases []judgeCase
	for _, c := range cases {
		pyCases = append(pyCases, c.at(pyRoot))
	}
	py := testkit.Answers[[]judgeAnswer](t, "", judgeOracle, map[string]any{"root": pyRoot, "cases": pyCases})
	if len(py) != len(cases) {
		t.Fatalf("Python の答えが %d 件（%d 件を渡した）", len(py), len(cases))
	}
	failed := 0
	for i, c := range cases {
		want := py[i].normalized(pyRoot)
		got := runGo(t, goRoot, c.at(goRoot)).normalized(goRoot)
		if diff := answerDiff(got, want); diff != "" {
			failed++
			if failed <= 30 {
				t.Errorf("case %d %s\npayload: %s\nenv: %v\n%s", i, c.Name, c.Payload, c.Env, diff)
			}
		}
	}
	if failed > 0 {
		t.Errorf("%d / %d 件が Python と違う", failed, len(cases))
	}
}

func answerDiff(got, want judgeAnswer) string {
	var b strings.Builder
	if got.Code != want.Code {
		fmt.Fprintf(&b, "code: Go %d, Python %d\n", got.Code, want.Code)
	}
	if got.Error != want.Error {
		fmt.Fprintf(&b, "error: Go %q, Python %q\n", got.Error, want.Error)
	}
	if got.Stdout != want.Stdout {
		fmt.Fprintf(&b, "stdout:\n Go:     %s\n Python: %s\n", got.Stdout, want.Stdout)
	}
	if got.Stderr != want.Stderr {
		fmt.Fprintf(&b, "stderr:\n Go:     %q\n Python: %q\n", got.Stderr, want.Stderr)
	}
	g, _ := json.Marshal(got.Record)
	w, _ := json.Marshal(want.Record)
	if !bytes.Equal(g, w) {
		fmt.Fprintf(&b, "record:\n Go:     %s\n Python: %s\n", g, w)
	}
	return b.String()
}

// readCases は testdata の JSONL の case を読む。
func readCases(t *testing.T, name string) []judgeCase {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	var out []judgeCase
	for line := range strings.SplitSeq(strings.TrimSpace(string(data)), "\n") {
		var c judgeCase
		if err := json.Unmarshal([]byte(line), &c); err != nil {
			t.Fatal(err)
		}
		out = append(out, c)
	}
	return out
}
