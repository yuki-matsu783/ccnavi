package judge

import (
	"bufio"
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/records/audit"
)

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
	conf.GuardTicketApproval = string(selfguard.Resolve(&stderr, "", conf.GuardTicketApproval, env.GuardTicketApproval, selfguard.GateChoices()))
	conf.TicketControl = string(selfguard.Resolve(&stderr, "", conf.TicketControl, env.TicketControl, selfguard.GateChoices()))
	mode := config.ResolveMode(&stderr, "", conf)
	conf.RestoreIfDeny = string(selfguard.Resolve(&stderr, "", conf.RestoreIfDeny, env.RestoreIfDeny, selfguard.Choices()))
	conf.GuardCoreFiles = string(selfguard.Resolve(&stderr, "", conf.GuardCoreFiles, env.GuardCoreFiles, selfguard.Choices()))
	conf.GuardUnwatched = string(selfguard.Resolve(&stderr, "", conf.GuardUnwatched, env.GuardUnwatched, selfguard.GateChoices()))
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

// checkCases はワークスペースを作って case を順に回し、答え（ルートを置き換え、時刻と経過時間を落としたもの）を Go の
// 答えの見本（答えが確かめ済みだった Go の出力から取った）と比べる。
func checkCases(t *testing.T, build func(t *testing.T) string, cases []judgeCase) {
	t.Helper()
	root := build(t)
	var seen []judgeAnswer
	for _, c := range cases {
		seen = append(seen, runGo(t, root, c.at(root)).normalized(root))
	}
	golden.Check(t, strings.TrimPrefix(t.Name(), "Test"), seen)
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
