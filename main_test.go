// Acceptance tests. Everything here drives the built binary from outside: a
// payload on stdin, and nothing read back but stdout, stderr and the exit code.
// No internal function is called, so the tests stay true when the inside moves.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// binary is the compiled tool under test, built once for the whole package.
var binary string

func TestMain(m *testing.M) {
	code, err := build(m)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	os.Exit(code)
}

func build(m *testing.M) (int, error) {
	dir, err := os.MkdirTemp("", "ccnavi-test")
	if err != nil {
		return 0, err
	}
	defer os.RemoveAll(dir)

	binary = filepath.Join(dir, "ccnavi")
	if runtime.GOOS == "windows" {
		binary += ".exe"
	}

	out, err := exec.Command("go", "build", "-o", binary, ".").CombinedOutput()
	if err != nil {
		return 0, fmt.Errorf("build failed: %v\n%s", err, out)
	}
	return m.Run(), nil
}

// result is everything a caller of the hook can see.
type result struct {
	stdout string
	stderr string
	code   int
}

func run(t *testing.T, payload string, env ...string) result {
	t.Helper()

	cmd := exec.Command(binary, "--rules", filepath.Join("testdata", "rules.json"))
	cmd.Stdin = strings.NewReader(payload)

	// The mode is pinned rather than inherited. This repository runs ccnavi on
	// itself, so the session that runs these tests already carries a mode, and a
	// test that reads it would report on that session instead of on the code.
	// A case that wants another mode appends it, and the later value wins.
	cmd.Env = append(os.Environ(), "CCNAVI_MODE=block")
	cmd.Env = append(cmd.Env, env...)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	if err := cmd.Run(); err != nil {
		var exit *exec.ExitError
		if !errorsAs(err, &exit) {
			t.Fatalf("run %s: %v", binary, err)
		}
	}
	return result{stdout.String(), stderr.String(), cmd.ProcessState.ExitCode()}
}

func errorsAs(err error, target **exec.ExitError) bool {
	e, ok := err.(*exec.ExitError)
	if ok {
		*target = e
	}
	return ok
}

// verdict is the response shape Claude Code reads for a tool call.
type verdict struct {
	HookSpecificOutput struct {
		HookEventName            string `json:"hookEventName"`
		PermissionDecision       string `json:"permissionDecision"`
		PermissionDecisionReason string `json:"permissionDecisionReason"`
		AdditionalContext        string `json:"additionalContext"`
	} `json:"hookSpecificOutput"`
}

func decode(t *testing.T, r result) verdict {
	t.Helper()
	var v verdict
	if err := json.Unmarshal([]byte(r.stdout), &v); err != nil {
		t.Fatalf("stdout is not the expected JSON: %v\nstdout: %q\nstderr: %q", err, r.stdout, r.stderr)
	}
	return v
}

func preToolUse(tool, field, value string) string {
	return fmt.Sprintf(`{"hook_event_name":"PreToolUse","tool_name":%q,"tool_input":{%q:%q}}`,
		tool, field, value)
}

func TestDeniedCallCarriesReasonAndAlternative(t *testing.T) {
	got := run(t, preToolUse("Bash", "command", "git push origin main"))

	if got.code != 0 {
		t.Errorf("exit code = %d, want 0 (the verdict travels in the JSON)", got.code)
	}
	v := decode(t, got)
	if v.HookSpecificOutput.PermissionDecision != "deny" {
		t.Errorf("decision = %q, want deny", v.HookSpecificOutput.PermissionDecision)
	}
	if v.HookSpecificOutput.HookEventName != "PreToolUse" {
		t.Errorf("hookEventName = %q, want PreToolUse", v.HookSpecificOutput.HookEventName)
	}
	// The point of the tool: a refusal that does not name an alternative leaves
	// the agent to invent one.
	if !strings.Contains(v.HookSpecificOutput.PermissionDecisionReason, "ask the user") {
		t.Errorf("reason gives no alternative: %q", v.HookSpecificOutput.PermissionDecisionReason)
	}
}

func TestAllowedCallSaysNothing(t *testing.T) {
	got := run(t, preToolUse("Bash", "command", "git status"))

	if got.code != 0 {
		t.Errorf("exit code = %d, want 0", got.code)
	}
	if got.stdout != "" {
		t.Errorf("stdout = %q, want empty for a call that passes", got.stdout)
	}
}

func TestEveryMatchingReasonComesBackAtOnce(t *testing.T) {
	got := run(t, preToolUse("Bash", "command", "sed -i s/a/b/ .env"))

	v := decode(t, got)
	reason := v.HookSpecificOutput.PermissionDecisionReason
	for _, want := range []string{"sed", "perl"} {
		if !strings.Contains(reason, want) {
			t.Errorf("reason is missing %q, so the agent has to retry to find it: %q", want, reason)
		}
	}
}

func TestUnknownEventDoesNotStopTheWork(t *testing.T) {
	got := run(t, `{"hook_event_name":"SomethingElse","tool_name":"Bash","tool_input":{"command":"git push"}}`)

	if got.code != 0 {
		t.Errorf("exit code = %d, want 0: an event we do not check must not block", got.code)
	}
	if got.stdout != "" {
		t.Errorf("stdout = %q, want empty", got.stdout)
	}
}

func TestDirectRunFailsInsteadOfSucceedingSilently(t *testing.T) {
	got := run(t, "")

	if got.code == 0 {
		t.Error("exit code = 0 with no payload; a misinstalled hook would look like a working one")
	}
	if !strings.Contains(got.stderr, "hook") {
		t.Errorf("stderr does not explain how to use the tool: %q", got.stderr)
	}
}

func TestWarnModeReportsWithoutBlocking(t *testing.T) {
	got := run(t, preToolUse("Bash", "command", "git push origin main"), "CCNAVI_MODE=warn")

	v := decode(t, got)
	if v.HookSpecificOutput.PermissionDecision != "" {
		t.Errorf("decision = %q, want none: warn mode reports but does not stop the call",
			v.HookSpecificOutput.PermissionDecision)
	}
	if !strings.Contains(v.HookSpecificOutput.AdditionalContext, "git push") {
		t.Errorf("warn mode did not report what block would have stopped: %q",
			v.HookSpecificOutput.AdditionalContext)
	}
}

func TestOffJudgesNothing(t *testing.T) {
	got := run(t, preToolUse("Bash", "command", "git push origin main"), "CCNAVI_MODE=off")

	if got.code != 0 || got.stdout != "" {
		t.Errorf("off mode produced code=%d stdout=%q, want 0 and empty", got.code, got.stdout)
	}
}

// record is one line of the log, read back the way a person debugging would.
type record struct {
	Mode     string   `json:"mode"`
	Event    string   `json:"event"`
	Tool     string   `json:"tool"`
	Subject  string   `json:"subject"`
	Decision string   `json:"decision"`
	Enforced bool     `json:"enforced"`
	Reason   string   `json:"reason"`
	Rules    []string `json:"rules"`
}

// runLogged sends several payloads through one log file and reads it back.
func runLogged(t *testing.T, mode string, payloads ...string) []record {
	t.Helper()
	logPath := filepath.Join(t.TempDir(), "log.jsonl")

	for _, p := range payloads {
		cmd := exec.Command(binary,
			"--rules", filepath.Join("testdata", "rules.json"),
			"--log", logPath)
		cmd.Stdin = strings.NewReader(p)
		cmd.Env = append(os.Environ(), "CCNAVI_MODE="+mode)
		if err := cmd.Run(); err != nil {
			var exit *exec.ExitError
			if !errorsAs(err, &exit) {
				t.Fatalf("run: %v", err)
			}
		}
	}

	raw, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatalf("no log was written: %v", err)
	}

	var got []record
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		var r record
		if err := json.Unmarshal([]byte(line), &r); err != nil {
			t.Fatalf("log line is not JSON: %v\nline: %q", err, line)
		}
		got = append(got, r)
	}
	return got
}

func TestEveryCallIsRecordedIncludingTheOnesLetThrough(t *testing.T) {
	got := runLogged(t, "block",
		preToolUse("Bash", "command", "git push origin main"),
		preToolUse("Bash", "command", "go build ./..."),
		preToolUse("Task", "prompt", "something"),
		`{"hook_event_name":"SessionStart"}`,
	)

	if len(got) != 4 {
		t.Fatalf("wrote %d lines, want 4: a call with no record cannot be told from a guard that never ran", len(got))
	}

	want := []struct {
		decision string
		reason   string
	}{
		{"deny", ""},
		{"allow", ""},
		{"skip", "no-subject"},
		{"skip", "event-not-checked"},
	}
	for i, w := range want {
		if got[i].Decision != w.decision {
			t.Errorf("line %d decision = %q, want %q", i+1, got[i].Decision, w.decision)
		}
		if got[i].Reason != w.reason {
			t.Errorf("line %d reason = %q, want %q", i+1, got[i].Reason, w.reason)
		}
	}
}

func TestWarnModeRecordsTheVerdictItDidNotApply(t *testing.T) {
	got := runLogged(t, "warn", preToolUse("Bash", "command", "git push origin main"))

	if len(got) != 1 {
		t.Fatalf("wrote %d lines, want 1", len(got))
	}
	// Counting what warn mode would have stopped is the whole reason to run it, so
	// the judgment has to survive into the record even though nothing happened.
	if got[0].Decision != "deny" {
		t.Errorf("decision = %q, want deny", got[0].Decision)
	}
	if got[0].Enforced {
		t.Error("enforced = true, but warn mode let the call through")
	}
	if len(got[0].Rules) != 1 || got[0].Rules[0] != "git-push" {
		t.Errorf("rules = %v, want the id of the rule that matched", got[0].Rules)
	}
}

func TestOffIsRecordedSoSilenceStillMeansSomething(t *testing.T) {
	got := runLogged(t, "off", preToolUse("Bash", "command", "git push origin main"))

	if len(got) != 1 {
		t.Fatalf("wrote %d lines, want 1", len(got))
	}
	if got[0].Decision != "skip" || got[0].Reason != "mode-off" {
		t.Errorf("decision = %q reason = %q, want skip/mode-off", got[0].Decision, got[0].Reason)
	}
}

func TestWorkingDirectoryDoesNotChangeTheVerdict(t *testing.T) {
	here := run(t, preToolUse("Bash", "command", "git push origin main"))

	abs, err := filepath.Abs(filepath.Join("testdata", "rules.json"))
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(binary, "--rules", abs)
	cmd.Dir = t.TempDir()
	cmd.Env = append(os.Environ(), "CCNAVI_MODE=block")
	cmd.Stdin = strings.NewReader(preToolUse("Bash", "command", "git push origin main"))
	out, _ := cmd.Output()

	if string(out) != here.stdout {
		t.Errorf("verdict changed with the working directory:\n  here: %q\n  elsewhere: %q", here.stdout, out)
	}
}
