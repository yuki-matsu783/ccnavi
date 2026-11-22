package rules

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// ユーザへの確認（ask）の前の発言を、hook の入口から確かめる（本体は hook/askcontext）。
//
//   - `AskUserQuestion` は、直前の発言に 2 つの節が無ければ止まり、揃えば通る
//   - ccnavi が確認に出す呼び出し（ターン分離のゲートの確認・ルールの `ask`）も、節が無ければ確認でなく止まる
//   - 非対話・サブエージェント・別のツールの呼び出しは検査しない
//   - dry-run では止めず、返していたはずだと言う
//
// 発言の読み方（最後のユーザの発言より後ろだけを見る・ツールの結果の行は区切りにしない・サイドチェーンは見ない・
// 会話記録に発言が遅れて書かれても待てば通る・判定できないときは検査しない）は hook/askcontext の単体テストが持つ。

const (
	bothSections = "## 前提・背景\nファイルを消す必要がある。\n## 想定結果\n許可すると消える。断ると残る。"
	gitSh        = "sh .ccnavi/scripts/ccnavi-git.sh"
	doneCommand  = gitSh + " integrate-done foo -m 'msg x'"
	askCode      = "DENY_ASK_NEEDS_CONTEXT" // 止めたときの拒否コード
	askRuleID    = "builtin:ask-context"    // 記録の rules に載る名前
)

func assistantRow(text string) payload {
	return payload{"type": "assistant", "message": payload{"content": []any{payload{"type": "text", "text": text}}}}
}

func promptRow() payload {
	return payload{"type": "user", "message": payload{"content": "お願いします"}}
}

// callRow はこの呼び出し自身の tool_use の行。あれば、直前の発言は書き込み済み。
func callRow() payload {
	block := payload{"type": "tool_use", "id": "toolu_test", "name": "X", "input": payload{}}
	return payload{"type": "assistant", "message": payload{"content": []any{block}}}
}

type askRepo struct {
	root, transcript string
	attended         string // CLAUDE_CODE_SESSION_ATTENDED の値（空なら渡さない）
}

func newAskRepo(t testing.TB, attended string) askRepo {
	t.Helper()
	root := base.TempDir(t)
	mkdirs(t, filepath.Join(root, ".ccnavi", "config", "common"))
	return askRepo{root: root, transcript: filepath.Join(root, "transcript.jsonl"), attended: attended}
}

// put は会話記録を書く。末尾に、この呼び出し自身の行を足す。
func (a askRepo) put(t testing.TB, rows ...payload) {
	t.Helper()
	var lines []string
	for _, row := range append(rows, callRow()) {
		b, err := json.Marshal(row)
		if err != nil {
			t.Fatal(err)
		}
		lines = append(lines, string(b))
	}
	write(t, a.transcript, strings.Join(lines, "\n")+"\n")
}

// call は PreToolUse を 1 回渡す。extra は payload に足す欄。
func (a askRepo) call(t testing.TB, tool string, input map[string]any, mode string, extra payload) run.Result {
	t.Helper()
	body := payload{
		"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": tool, "tool_use_id": "toolu_test",
		"tool_input": input, "transcript_path": a.transcript,
	}
	for k, v := range extra {
		body[k] = v
	}
	e := map[string]string{env.Mode: mode, env.AskContextWaitSeconds: "0.3"}
	if a.attended != "" {
		e[env.ClaudeSessionAttended] = a.attended
	}
	return fire(t, a.root, nil, body, e)
}

func (a askRepo) askUser(t testing.TB, mode string, extra payload) run.Result {
	t.Helper()
	return a.call(t, "AskUserQuestion", map[string]any{"questions": []any{payload{"question": "消してよいですか", "options": []any{}}}}, mode, extra)
}

func (a askRepo) bash(t testing.TB, command, mode string) run.Result {
	t.Helper()
	return a.call(t, "Bash", map[string]any{"command": command}, mode, nil)
}

// stopped は確認ではなく拒否コード付きの deny で止めたか。
func stopped(t testing.TB, r run.Result) bool {
	t.Helper()
	return parse(t, r).Decision == "deny" && strings.Contains(r.Stdout, askCode)
}

func TestAnAskWithoutTheSectionsIsStopped(t *testing.T) {
	t.Parallel()
	a := newAskRepo(t, "1")
	a.put(t, promptRow(), assistantRow("消してよいですか？"))
	r := a.askUser(t, "enable", nil)
	if !stopped(t, r) {
		t.Fatalf("止めるはず: %s", r.Stdout)
	}
	mustHave(t, r.Stdout, askRuleID, "「前提・背景」・「想定結果」", "消してよいですか")
	// 止めた記録は拒否コードと rules を残す。
	rec := lastRecord(t, a.root)
	if str(rec, "code") != askCode || str(rec, "decision") != "deny" {
		t.Errorf("記録: %v", rec)
	}
	if got := strs(rec, "rules"); len(got) != 1 || got[0] != askRuleID {
		t.Errorf("rules = %v", got)
	}
}

func TestAnAskWithTheSectionsPasses(t *testing.T) {
	t.Parallel()
	a := newAskRepo(t, "1")
	a.put(t, promptRow(), assistantRow(bothSections))
	r := a.askUser(t, "enable", nil)
	if stopped(t, r) || strings.Contains(r.Stdout, "deny") {
		t.Errorf("通すはず: %s", r.Stdout)
	}
}

func TestDryRunDoesNotStopTheAskAndSaysItWould(t *testing.T) {
	t.Parallel()
	a := newAskRepo(t, "1")
	a.put(t, promptRow(), assistantRow("消してよいですか？"))
	r := a.askUser(t, "dry-run", nil)
	mustHave(t, r.Stdout, "dry-run", askCode)
	if parse(t, r).Decision == "deny" {
		t.Errorf("止めてはいけない: %s", r.Stdout)
	}
}

func TestOnlyTheMainAgentsAskIsChecked(t *testing.T) {
	t.Parallel()
	a := newAskRepo(t, "1")
	a.put(t, promptRow(), assistantRow("消してよいですか？"))
	if stopped(t, a.askUser(t, "enable", payload{"agent_id": "a1"})) {
		t.Error("サブエージェントの呼び出しは検査しない")
	}
	a.put(t, promptRow(), assistantRow("調べます"))
	// 権限モードを渡す。渡さないと ccnavi が未知のモードとして確認に出し、検査に当たる。
	r := a.call(t, "Bash", map[string]any{"command": "ls"}, "enable", payload{"permission_mode": "default"})
	if stopped(t, r) {
		t.Errorf("ほかのツールは検査しない: %s", r.Stdout)
	}
}

func TestAnUnattendedSessionIsNotChecked(t *testing.T) {
	t.Parallel()
	a := newAskRepo(t, "0")
	a.put(t, promptRow(), assistantRow("消してよいですか？"))
	if stopped(t, a.askUser(t, "enable", nil)) {
		t.Error("非対話は検査しない")
	}
}

// ccnavi が確認に出す呼び出し（ターン分離のゲートの確認）。

func TestAGateConfirmWithoutTheSectionsIsStoppedNotAsked(t *testing.T) {
	t.Parallel()
	a := newAskRepo(t, "1")
	a.put(t, promptRow(), assistantRow("取り込みます"))
	r := a.bash(t, doneCommand, "enable")
	if !stopped(t, r) || strings.Contains(r.Stdout, `"permissionDecision": "ask"`) {
		t.Errorf("確認でなく止めるはず: %s", r.Stdout)
	}
	// dry-run でもゲートの確認は止めたまま。
	r = a.bash(t, doneCommand, "dry-run")
	if !stopped(t, r) {
		t.Errorf("dry-run でも止めるはず: %s", r.Stdout)
	}
	mustHave(t, r.Stdout, "builtin:turn-gate:integrate-user-turn")
}

func TestAGateConfirmWithTheSectionsIsAsked(t *testing.T) {
	t.Parallel()
	a := newAskRepo(t, "1")
	a.put(t, promptRow(), assistantRow(bothSections))
	r := a.bash(t, doneCommand, "enable")
	if parse(t, r).Decision != "ask" || stopped(t, r) {
		t.Errorf("確認に出るはず: %s", r.Stdout)
	}
}

// ルールの `ask`（運用の rules.yml の direct-worktree-confirm）が返す確認。

const adoptCommand = "sh /repo/.ccnavi/scripts/ccnavi-worktree.sh feature-1 --base main"

func (a askRepo) withRepoRules(t testing.TB) {
	t.Helper()
	write(t, filepath.Join(a.root, ".ccnavi", "config", "common", "rules.yml"), read(t, rulesFile()))
}

func TestARuleAskWithoutTheSectionsIsStoppedAndKeepsTheRuleName(t *testing.T) {
	t.Parallel()
	a := newAskRepo(t, "1")
	a.withRepoRules(t)
	a.put(t, promptRow(), assistantRow("ワークツリーを切ります"))
	r := a.bash(t, adoptCommand, "enable")
	if !stopped(t, r) || parse(t, r).Decision == "ask" {
		t.Fatalf("確認でなく止めるはず: %s", r.Stdout)
	}
	// 確認の本文と、確認を要求したルールの名前は残る。
	mustHave(t, r.Stdout, "direct-worktree-confirm")
	rec := lastRecord(t, a.root)
	if str(rec, "code") != askCode {
		t.Errorf("code = %v", rec["code"])
	}
	rules := strings.Join(strs(rec, "rules"), " ")
	mustHave(t, rules, askRuleID, "direct-worktree-confirm")
}

func TestARuleAskWithTheSectionsIsAsked(t *testing.T) {
	t.Parallel()
	a := newAskRepo(t, "1")
	a.withRepoRules(t)
	a.put(t, promptRow(), assistantRow(bothSections))
	r := a.bash(t, adoptCommand, "enable")
	if parse(t, r).Decision != "ask" || stopped(t, r) {
		t.Errorf("確認に出るはず: %s", r.Stdout)
	}
	mustHave(t, r.Stdout, "direct-worktree-confirm")
}

// 確認（ask）の理由が、あとのスクリプト側の拒否を隠さないこと（direct-worktree-confirm の `--adopt`）。
//
// 承認前の親ワークツリーに `ccnavi-worktree.sh <親> --adopt` を打つと、先にルールの確認が当たる。非対話では確認の理由が
// エージェントに返るが、「了承されたら打ち直す」とだけ読めると、打ち直してもスクリプトが断る。理由が、通らない条件を先に言う。

func adoptReason(t testing.TB, command, attended string) (decision, reason string) {
	t.Helper()
	root := base.TempDir(t)
	write(t, filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), read(t, rulesFile()))
	r := fire(t, root, nil, pre("Bash", map[string]any{"command": command}), map[string]string{env.ClaudeSessionAttended: attended})
	out := parse(t, r)
	return out.Decision, out.Reason
}

func TestAnUnattendedAdoptIsAskedAndSaysWhatWillNotPass(t *testing.T) {
	t.Parallel()
	decision, said := adoptReason(t, "sh /repo/.ccnavi/scripts/ccnavi-worktree.sh parent-1 --adopt --base main", "0")
	if decision != "ask" {
		t.Errorf("ask のはず: %q", decision)
	}
	mustHave(t, said, "direct-worktree-confirm", "了承", "チケットに結び付く", "了承されても通りません", "マージリクエストで出す")
}

func TestAnotherSpecAddsNoAdoptNote(t *testing.T) {
	t.Parallel()
	decision, said := adoptReason(t, "sh /repo/.ccnavi/scripts/ccnavi-worktree.sh feature-1 --base main", "0")
	if decision != "ask" {
		t.Errorf("ask のはず: %q", decision)
	}
	mustHave(t, said, "了承")
	mustNotHave(t, said, "通りません")
}

func TestAnAttendedSessionGetsNoAdoptNote(t *testing.T) {
	t.Parallel()
	decision, said := adoptReason(t, "sh /repo/.ccnavi/scripts/ccnavi-worktree.sh parent-1 --adopt --base main", "1")
	if decision != "ask" {
		t.Errorf("ask のはず: %q", decision)
	}
	mustNotHave(t, said, "通りません")
}
