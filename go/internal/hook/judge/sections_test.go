package judge

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// 3 つのタイプ（deny・ask・allow）の強さと、権限モードへの委譲。強さの順は deny > ルールが置いた確認 > allow > 委譲。
// ルールが置いた確認（RULE_ASK）と、どのルールも言及しないための確認（UNDECLARED）は区別して返す。

func sectionRule(id, match, glob, message string) map[string]any {
	return map[string]any{"id": id, "match": match, "glob": glob, "message": message}
}

// sectionsWorkspace は共通設定のルールにこの表の rules だけを置いたワークスペース。YAML は JSON の上位互換なので JSON で書く。
func sectionsWorkspace(t *testing.T, sections map[string]any) string {
	t.Helper()
	root := testkit.TempRoot(t)
	body := map[string]any{"version": 1}
	for k, v := range sections {
		body[k] = v
	}
	data, _ := json.Marshal(body)
	testkit.Put(t, filepath.Join(root, config.DefaultRules), string(data))
	testkit.CopyMessages(t, root)
	return root
}

// sectionsOutput は hookSpecificOutput（無ければ空）。
func sectionsOutput(t *testing.T, root, tool, subject string) (decision, reason string) {
	t.Helper()
	field := "command"
	if tool != config.ToolBash {
		field = "file_path"
	}
	// 権限モードを知らない呼び出し。モードを知らなければ、判定を持たない呼び出しは確認にする。
	a := runGo(t, root, judgeCase{Payload: call(tool, root, map[string]any{field: subject}, "permission_mode", ""), Env: baseEnv})
	if strings.TrimSpace(a.Stdout) == "" {
		return "", ""
	}
	var out struct {
		Hook struct {
			Decision string `json:"permissionDecision"`
			Reason   string `json:"permissionDecisionReason"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(a.Stdout), &out); err != nil {
		t.Fatalf("標準出力が JSON でない: %v: %q", err, a.Stdout)
	}
	return out.Hook.Decision, out.Hook.Reason
}

func mustContain(t *testing.T, what, text string, wants ...string) {
	t.Helper()
	for _, w := range wants {
		if !strings.Contains(text, w) {
			t.Errorf("%s に %q が無い: %.300s", what, w, text)
		}
	}
}

// 既定が許可ではなく確認であること。ccnavi は判定を持たないから聞くので、危険の表明だと読ませない。
func TestUnmentionedCallAsksWithoutClaimingDanger(t *testing.T) {
	root := sectionsWorkspace(t, map[string]any{"deny": []any{sectionRule("push", "Bash", "*git push*", "文面")}})
	decision, reason := sectionsOutput(t, root, config.ToolBash, "ls -la")
	if decision != "ask" {
		t.Fatalf("%q", decision)
	}
	mustContain(t, "理由", reason, "UNDECLARED", "not a warning about the call itself", "allow section")
}

func TestSectionStrengthOrder(t *testing.T) {
	root := sectionsWorkspace(t, map[string]any{
		"deny": []any{
			sectionRule("a", "Bash", "*git push*", "拒否の文面"),
			sectionRule("b", "Bash", "* origin *", "拒否の文面 2 つ目"),
			sectionRule("secrets", "Write", "*/secrets/*", "止めます"),
		},
		"ask": []any{
			sectionRule("g", "Bash", "*git *", "確認の文面"),
			sectionRule("everything", "Write", "*/secrets/*", "聞きます"),
			sectionRule("migrations", "Write", "*/migrations/*", "ユーザが中身を見ます"),
		},
		"allow": []any{
			sectionRule("ls", "Bash", "*ls *", ""),
			sectionRule("c", "Bash", "*", ""),
			sectionRule("anything", "Write", "*", ""),
		},
	})
	// allow にヒットすれば通る。
	if decision, _ := sectionsOutput(t, root, config.ToolBash, "ls -la"); decision != "" {
		t.Errorf("allow: %q", decision)
	}
	// ルールが置いた確認は未言及と区別して返り、置いた文面が届く。
	decision, reason := sectionsOutput(t, root, config.ToolWrite, filepath.Join(root, "migrations", "0001.sql"))
	if decision != "ask" {
		t.Errorf("ask: %q", decision)
	}
	mustContain(t, "ask の理由", reason, "RULE_ASK", "ユーザが中身を見ます")
	// deny が明示的な ask より強い。弱い側の文面は返さない。
	decision, reason = sectionsOutput(t, root, config.ToolWrite, filepath.Join(root, "secrets", "x.txt"))
	if decision != "deny" {
		t.Errorf("deny: %q", decision)
	}
	mustContain(t, "deny の理由", reason, "止めます")
	if strings.Contains(reason, "聞きます") {
		t.Errorf("弱い側の文面を返した: %s", reason)
	}
	// タイプをまたいでヒットしても強いほうだけ。同じタイプなら全部返す。
	_, reason = sectionsOutput(t, root, config.ToolBash, "git push origin main")
	mustContain(t, "同じタイプの理由", reason, "拒否の文面", "拒否の文面 2 つ目")
	if strings.Contains(reason, "確認の文面") {
		t.Errorf("弱い側の文面を返した: %s", reason)
	}
}

// 対象を確定できなかっただけで、禁じられたことをしたわけではないので確認にする。コメントだけの行は何も走らないので何も返さない。
func TestUnreadableCommandAsksAndCommentOnlyReturnsNothing(t *testing.T) {
	root := sectionsWorkspace(t, map[string]any{
		"deny":  []any{sectionRule("push", "Bash", "*git push*", "文面")},
		"allow": []any{sectionRule("anything", "Bash", "*", "")},
	})
	decision, reason := sectionsOutput(t, root, config.ToolBash, "cat <<'EOF'\nhello\n")
	if decision != "ask" {
		t.Errorf("%q", decision)
	}
	mustContain(t, "理由", reason, "PARSE_UNCERTAIN")
	if decision, reason := sectionsOutput(t, root, config.ToolBash, "# git push origin main"); decision != "" || reason != "" {
		t.Errorf("コメントだけ: %q %q", decision, reason)
	}
}
