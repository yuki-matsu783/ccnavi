package core

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/oplog"
)

// opsTree は操作記録（ops）を試す木（ルールは空）。
type opsTree struct {
	t     *testing.T
	root  string
	state string
}

func newOpsTree(t *testing.T) *opsTree {
	t.Helper()
	root := base.TempDir(t)
	ticketkit.Write(t, filepath.Join(root, ".claude", "keep"), "")
	ticketkit.Write(t, base.CommonPath(root, "rules"), `{"version": 1}`)
	return &opsTree{t: t, root: root, state: filepath.Join(root, "logs", "state")}
}

func (o *opsTree) ccnavi(words ...string) run.Result {
	o.t.Helper()
	return run.Ccnavi(o.t, append([]string{"--root", o.root, "--state", o.state}, words...), run.Opts{Dir: base.Root()})
}

// begin は decide の操作記録（段階は reply と issue）を書き、操作ID を返す。
func (o *opsTree) begin() string {
	o.t.Helper()
	return samples.BeginRecord(o.t, o.state, oplog.KindDecide, oplog.Options{
		Repo:   pyjson.NewObject("host", "github.com", "kind", "github", "path", "o/r"),
		Parent: "i0001", Phase: 1,
		Stages: []oplog.StageSpec{{ID: "reply", Kind: "reply"}, {ID: "issue", Kind: "issue"}},
	})
}

// record は操作記録の中身。消えていれば nil。
func (o *opsTree) record(id string) map[string]any {
	path := filepath.Join(o.state, "ops", id+".json")
	if !ticketkit.Exists(path) {
		return nil
	}
	return ticketkit.ReadJSON(o.t, path)
}

// `ccnavi ops …`（操作記録の読み書きの入口）。
func TestOpsCLI(t *testing.T) {
	t.Parallel()
	t.Run("pending は未完了の記録を 1 行ずつ出す", func(t *testing.T) {
		t.Parallel()
		o := newOpsTree(t)
		id := o.begin()
		ticketkit.OK(t, o.ccnavi("ops", "stage-set", id, "reply", "sent"))
		r := ticketkit.OK(t, o.ccnavi("ops", "pending"))
		if got := strings.Fields(r.Stdout); strings.Join(got, " ") != id+" decide issue" {
			t.Errorf("pending = %v", got)
		}
	})
	t.Run("stage-set と done", func(t *testing.T) {
		t.Parallel()
		o := newOpsTree(t)
		id := o.begin()
		for _, stage := range []string{"reply", "issue"} {
			ticketkit.OK(t, o.ccnavi("ops", "stage-set", id, stage, "sent", "https://x/1", "me"))
		}
		shown := ticketkit.ParseJSON(t, o.ccnavi("ops", "show", id).Stdout)
		stages := shown["stages"].([]any)
		if stages[1].(map[string]any)["url"] != "https://x/1" {
			t.Errorf("stages = %v", stages)
		}
		if got := strings.TrimSpace(o.ccnavi("ops", "done", id).Stdout); got != "done" {
			t.Errorf("done = %q", got)
		}
		if o.record(id) != nil {
			t.Error("done のあとも記録が残る")
		}
	})
	t.Run("done は未完了の記録を残す", func(t *testing.T) {
		t.Parallel()
		o := newOpsTree(t)
		id := o.begin()
		if got := strings.TrimSpace(o.ccnavi("ops", "done", id).Stdout); got != "open" {
			t.Errorf("done = %q", got)
		}
		if o.record(id) == nil {
			t.Error("未完了の記録が消えた")
		}
	})
	t.Run("不正な入力は何も書かずに断る", func(t *testing.T) {
		t.Parallel()
		o := newOpsTree(t)
		id := o.begin()
		for _, words := range [][]string{
			{"ops", "stage-set", id, "reply", "weird"},
			{"ops", "stage-set", id, "nope", "sent"},
			{"ops", "stage-set", "../x", "reply", "sent"},
			{"ops", "show", strings.Repeat("0", 32)},
			{"ops", "unknown"},
		} {
			ticketkit.Fails(t, o.ccnavi(words...))
		}
		stages := o.record(id)["stages"].([]any)
		if stages[0].(map[string]any)["state"] != "pending" {
			t.Errorf("段階が書き換わった: %v", stages[0])
		}
	})
}

// エージェントが偽の送信済みを書けないこと（書く副命令はだれにも止め、読む副命令は止めない）。
func TestOpsHookStopsTheWritingSubcommands(t *testing.T) {
	t.Parallel()
	o := newOpsTree(t)
	decision := func(command, agent string) string {
		payload := map[string]any{
			"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": o.root, "session_id": "s1",
			"tool_input": map[string]any{"command": command},
		}
		if agent != "" {
			payload["agent_id"] = agent
		}
		raw, _ := json.Marshal(payload)
		r := run.Ccnavi(t, []string{"--root", o.root, "--mode", "enable"}, run.Opts{Input: string(raw), Dir: base.Root()})
		return ticketkit.Decision(t, r)
	}
	op := strings.Repeat("a", 32)
	for _, command := range []string{"ccnavi ops stage-set " + op + " reply sent", "ccnavi ops done " + op} {
		for _, agent := range []string{"", "a1"} {
			if got := decision(command, agent); got != "deny" {
				t.Errorf("%q（agent=%q）= %q（deny のはず）", command, agent, got)
			}
		}
	}
	for _, command := range []string{"ccnavi ops pending", "ccnavi ops show " + op} {
		if got := decision(command, ""); got == "deny" {
			t.Errorf("%q を止めた", command)
		}
	}
}
