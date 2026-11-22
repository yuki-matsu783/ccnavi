package self

import (
	"encoding/json"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/infra/env"
)

// SessionStart で、読めないレイヤーのルールを知らせること。
//
// 読めないレイヤーは空として扱われ、そのレイヤーの deny が黙って効かなくなる。どのレイヤーを読めないと数えるかは
// policy/ruleload のテストが見る。
func TestSessionStartSaysAnUnreadableLayerToTheModelAndTheUser(t *testing.T) {
	t.Parallel()
	root := base.TempDir(t)
	mkdirs(t, root+"/.claude")
	write(t, base.ConfigPath(root, "rules"), "version: 1\ndeny: [: broken\n") // ワークスペースのプロジェクトのレイヤー（self）
	body := payload{"hook_event_name": "SessionStart", "source": "startup", "cwd": root}
	r := hook(t, root, []string{"--mode", "enable", "--guard-core-files", "disable"}, body, map[string]string{env.Learning: "enable"})
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	var out struct {
		SystemMessage string `json:"systemMessage"`
		Specific      struct {
			AdditionalContext string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		t.Fatalf("%v: %s", err, r.Stdout)
	}
	if !strings.Contains(out.SystemMessage, "読めません") || !strings.Contains(out.Specific.AdditionalContext, "読めません") {
		t.Errorf("ユーザとモデルの両方に「読めません」と言うはず: %+v", out)
	}
}
