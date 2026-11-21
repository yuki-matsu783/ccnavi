package core

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/run"
)

// 記録から秘密の形を伏せる（redact）の、記録への配線の受入テスト。伏せるのは記録の 1 行だけで、判定は伏せる前の文字列で下す。
// 形ごとの伏せ方と、記録の欄を切る前に伏せることは go/internal/records（redact・audit）の go test が見る。

// 形だけを持つ見本。実際のトークンではない。
const longSecret = "s3cr3t-value-that-is-long-enough"

func TestRedactJudgementSeesTheUnredactedCommand(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	// ルールが秘密の値そのものにヒットするなら、伏せた記録ではなく伏せる前の文字列でヒットする。
	writeFile(t, root, ".ccnavi/config/common/rules.yml",
		"version: 1\ndeny:\n  - id: no-this-token\n    match: Bash\n    glob: '*"+longSecret+"*'\n    message: 止める\n")
	payload := map[string]any{
		"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash",
		"tool_input": map[string]any{"command": "curl -H 'Authorization: Bearer " + longSecret + "' x"},
	}
	proc := run.Ccnavi(t, []string{"--ticket-control", "disable"}, run.Opts{
		Input: mustJSON(t, payload), Dir: root,
		Env: map[string]string{"CLAUDE_PROJECT_DIR": root, "CCNAVI_MODE": "enable"},
	})
	mustContain(t, "出力", proc.Stdout+proc.Stderr, "no-this-token")

	// 記録の既定の置き場（`--log` は診断でだけ効く）。
	f, err := os.Open(filepath.Join(root, "logs", "decisions.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var lines []map[string]any
	scan := bufio.NewScanner(f)
	for scan.Scan() {
		var row map[string]any
		if err := json.Unmarshal(scan.Bytes(), &row); err != nil {
			t.Fatal(err)
		}
		lines = append(lines, row)
	}
	if len(lines) != 1 {
		t.Fatalf("記録の行 = %d", len(lines))
	}
	rules, _ := json.Marshal(lines[0]["rules"])
	if lines[0]["decision"] != "deny" || string(rules) != `[".ccnavi/config/common/rules.yml:no-this-token"]` {
		t.Errorf("line = %v", lines[0])
	}
	mustNotContain(t, "記録の subject", str(lines[0], "subject"), longSecret)
}
