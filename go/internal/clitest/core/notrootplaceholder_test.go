package core

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
)

// ルールの `{!root}`（ワークスペースルートの外）を、hook として呼んだときの判定の受入テスト。
// `{!root}` の展開（外に数えるものの表・長さの上限・書き方の誤り）は go/internal/policy/rules の go test が見る。
func TestNotRootOutsideIsDeniedAndInsideIsNot(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	// 「ワークスペースルートの外」を書ける唯一の形。
	putCommonRules(t, root, map[string]any{"deny": []rule{{
		"id": "outside-workspace", "match": "Write|Edit|NotebookEdit", "regex": "^{!root}", "message": "ワークスペースの外です。",
	}}})
	outside := filepath.Join(emptyRoot(t), "draft.yml")
	judge := func(path, mode string) map[string]any {
		return judgeTool(t, root, "Write", path, "--mode", mode, "--guard-core-files", "disable")
	}
	out := judge(outside, "enable")
	if str(out, "permissionDecision") != "deny" || !strings.Contains(str(out, "permissionDecisionReason"), "outside-workspace") {
		t.Errorf("外が止まらない: %v", out)
	}
	if out := judge(filepath.Join(root, "README.md"), "enable"); str(out, "permissionDecision") == "deny" {
		t.Errorf("中が止まった: %v", out)
	}
	// dry-run は止めない。「止まらない」だけを見ると、ルールがヒットしていない実装でも通る。
	// `enable` なら止めていたことを告げる文が出ていることまで縛る。
	out = judge(outside, "dry-run")
	text, _ := json.Marshal(out)
	if str(out, "permissionDecision") == "deny" || !strings.Contains(string(text), "outside-workspace") {
		t.Errorf("dry-run = %s", text)
	}
}
