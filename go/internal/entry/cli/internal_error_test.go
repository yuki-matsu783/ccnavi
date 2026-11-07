package cli

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/testkit"
)

// hook の処理が ccnavi 自身の失敗で終わった回（tests/guard/test_internal_error.py から移した）。
// 失敗はイベントの処理の中からは起こせないので、受け皿（internalError・hookCrashed・onPanic）を直に呼ぶ。

func crashInput(event hookio.Event, tool string, field, value string) hookio.Input {
	in := hookio.Input{Event: event, ToolName: tool, ToolInput: pyjson.NewObject()}
	if field != "" {
		in.ToolInput.Set(field, value)
	}
	return in
}

// 判定に達しなかった回の結論は、イベントとツールの種類で決まる。PreToolUse は安全と確かめた読み取りと報告のツール
// だけを通し、PostToolUse は enable で検査していないことをモデルに言い、ほかのイベントは止めずにユーザに知らせる。
func TestInternalErrorFollowsTheEvent(t *testing.T) {
	root := testkit.TempRoot(t)
	testkit.CopyMessages(t, root)
	fail := failure{kind: "RuntimeError", where: "x.go:1"}
	logPath := filepath.Join("logs", "diag", "ccnavi.log")
	for _, c := range []struct {
		name   string
		mode   config.Mode
		in     hookio.Input
		want   int
		stderr []string
		stdout []string
	}{
		{"pre enable", config.Enable, crashInput(hookio.PreToolUse, config.ToolBash, "command", "echo hi"), config.ExitBlock,
			[]string{"[ccnavi] internal-error", "RuntimeError", logPath, "ユーザに"}, nil},
		{"pre dry-run safe", config.DryRun, crashInput(hookio.PreToolUse, config.ToolBash, "command", "ls"), config.ExitOK,
			[]string{"[ccnavi] internal-error"}, nil},
		{"pre dry-run unsafe", config.DryRun, crashInput(hookio.PreToolUse, config.ToolBash, "command", "echo hi"),
			config.ExitBlock, nil, nil},
		{"report tool", config.Enable, crashInput(hookio.PreToolUse, config.ToolSubagentHandback, "result", "ok"),
			config.ExitOK, []string{"[ccnavi] internal-error"}, nil},
		{"structured output", config.Enable, crashInput(hookio.PreToolUse, config.ToolStructuredOutput, "result", "ok"),
			config.ExitOK, nil, nil},
		{"unknown enable", config.Enable, crashInput(hookio.PreToolUse, "UnknownTool", "", ""), config.ExitBlock, nil, nil},
		{"unknown dry-run", config.DryRun, crashInput(hookio.PreToolUse, "UnknownTool", "", ""), config.ExitOK, nil, nil},
		{"write dry-run", config.DryRun, crashInput(hookio.PreToolUse, config.ToolWrite, "file_path", "/tmp/x"),
			config.ExitBlock, nil, nil},
		{"post", config.Enable, crashInput(hookio.PostToolUse, config.ToolBash, "command", "echo hi"), config.ExitBlock,
			[]string{"検査は行われていません"}, nil},
		{"stop", config.Enable, crashInput(hookio.Stop, "", "", ""), config.ExitOK, nil,
			[]string{"systemMessage", "internal-error", "止めたものはありません"}},
	} {
		c.in.Cwd = root
		var stdout, stderr bytes.Buffer
		record := &audit.Record{Mode: c.mode, Event: string(c.in.Event), Code: "X", Rules: []string{"r"}}
		got := internalError(&stdout, &stderr, c.mode, &config.Settings{}, root, c.in, record, fail)
		if got != c.want {
			t.Errorf("%s: 終了コード %d（stderr %q）", c.name, got, stderr.String())
		}
		for _, s := range c.stderr {
			if !strings.Contains(stderr.String(), s) {
				t.Errorf("%s: stderr に %q が無い: %q", c.name, s, stderr.String())
			}
		}
		// 失敗の文は出さない（型と場所だけ）。
		if strings.Contains(stderr.String(), "x.go:1") {
			t.Errorf("%s: 場所を stderr に出した", c.name)
		}
		if c.stdout == nil && stdout.Len() != 0 {
			t.Errorf("%s: 標準出力 %q", c.name, stdout.String())
		}
		for _, s := range c.stdout {
			if !strings.Contains(stdout.String(), s) {
				t.Errorf("%s: stdout に %q が無い: %q", c.name, s, stdout.String())
			}
		}
		if record.Decision != audit.Skip || record.Reason != audit.ReasonInternalError ||
			record.Detail != "RuntimeError at x.go:1" || record.Code != "" || record.Rules != nil {
			t.Errorf("%s: 記録 %+v", c.name, record)
		}
	}
}

func logRows(t *testing.T, path string) []map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		row := map[string]any{}
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatal(err)
		}
		out = append(out, row)
	}
	return out
}

// payload を読む前に落ちたら、イベントが分からないので止めて記録する。記録を試した後に落ちた回は、記録を重ねない。
func TestHookCrashedStopsAndRecordsOnce(t *testing.T) {
	root := testkit.TempRoot(t)
	testkit.CopyMessages(t, root)
	conf := &config.Settings{Log: filepath.Join(root, "logs", "decisions.jsonl")}
	fail := failure{kind: "RuntimeError"}

	var stdout, stderr bytes.Buffer
	if code := hookCrashed(&stdout, &stderr, conf, root, &hookSoFar{mode: config.DryRun}, fail); code != config.ExitBlock {
		t.Errorf("payload の前: 終了コード %d", code)
	}
	if !strings.Contains(stderr.String(), "[ccnavi] internal-error") || stdout.Len() != 0 {
		t.Errorf("payload の前: stdout %q stderr %q", stdout.String(), stderr.String())
	}
	rows := logRows(t, conf.Log)
	if len(rows) != 1 || rows[0]["reason"] != "internal-error" || rows[0]["detail"] != "RuntimeError" {
		t.Fatalf("記録 %v", rows)
	}

	// payload を読んだ後は、イベントごとに決めた形で終える。Stop は止めない。
	stop := hookio.Input{Event: hookio.Stop}
	stdout.Reset()
	so := &hookSoFar{mode: config.Enable, payload: &stop}
	if code := hookCrashed(&stdout, &stderr, conf, root, so, fail); code != config.ExitOK ||
		!strings.Contains(stdout.String(), "internal-error") {
		t.Errorf("Stop: 終了コード %d stdout %q", code, stdout.String())
	}
	if rows := logRows(t, conf.Log); len(rows) != 2 || rows[1]["event"] != "Stop" {
		t.Fatalf("Stop の記録 %v", rows)
	}

	// 記録で落ちた回（recorded）は、受け皿で同じ記録を試さない。報告のツールは通し、知らないツールは止める。
	for tool, want := range map[string]int{config.ToolSubagentHandback: config.ExitOK, "UnknownTool": config.ExitBlock} {
		in := crashInput(hookio.PreToolUse, tool, "result", "ok")
		in.Cwd = root
		so := &hookSoFar{mode: config.Enable, payload: &in, recorded: true}
		if code := hookCrashed(&stdout, &stderr, conf, root, so, fail); code != want {
			t.Errorf("%s: 終了コード %d", tool, code)
		}
	}
	if rows := logRows(t, conf.Log); len(rows) != 2 {
		t.Errorf("記録を重ねた: %v", rows)
	}
}

// panic は受け皿に届き、失敗の型と panic した行を持つ。
func TestOnPanicNamesTheKindAndThePlace(t *testing.T) {
	var got failure
	func() {
		defer onPanic(func(_ any, f failure) { got = f })
		panic(errors.New("想定外"))
	}()
	if got.kind != "errorString" || !strings.HasPrefix(got.where, "internal_error_test.go:") {
		t.Errorf("%+v", got)
	}
	func() {
		defer onPanic(func(_ any, f failure) { got = f })
		panic("文字列")
	}()
	if got.kind != "panic" {
		t.Errorf("%+v", got)
	}
}

// 経路が決まる前（引数を読む途中）に落ちた回。hook として起こされた形（引数なし）は内部エラーの応答で止め、記録を
// 既定の置き場に書く。引数のある起動は、打った人に見える失敗として 1 で終える。
func TestRunCrashedBeforeTheRoute(t *testing.T) {
	root := testkit.TempRoot(t)
	testkit.CopyMessages(t, root)
	t.Setenv("CLAUDE_PROJECT_DIR", root)
	fail := failure{kind: "RuntimeError"}

	var stdout, stderr bytes.Buffer
	if code := runCrashed(&stdout, &stderr, nil, "boom", fail); code != config.ExitBlock {
		t.Errorf("引数なし: 終了コード %d", code)
	}
	if !strings.Contains(stderr.String(), "[ccnavi] internal-error") {
		t.Errorf("引数なし: stderr %q", stderr.String())
	}
	if rows := logRows(t, filepath.Join(root, config.DefaultLog)); len(rows) != 1 || rows[0]["reason"] != "internal-error" {
		t.Errorf("引数なし: 記録 %v", rows)
	}

	stderr.Reset()
	if code := runCrashed(&stdout, &stderr, []string{"ticket", "status"}, "boom", fail); code != config.ExitError ||
		!strings.Contains(stderr.String(), "想定外の失敗で止まった: boom") {
		t.Errorf("引数あり: 終了コード %d stderr %q", code, stderr.String())
	}
}
