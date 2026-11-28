// Package rulemerge は、ルールとチケットの判定を合わせ、厳しい側を採ることを外から確かめる（応答と記録だけを見る）。
//
// ルールはワークツリーを `*/.claude/worktrees/*` で丸ごと指す 1 本を、表の列ごとに置き換える。
// チケットの範囲は子 i0001-01-01 のもの（allow: src/*、ask: src/ask/*、deny: src/deny/*）。範囲の外には docs/ を使う。
// チケットの無い行は、main から切った別のワークツリー free に書く。
package rulemerge

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

const (
	note = "ルールの additionalContext。判定を決めた側に関わらず載る。"
	// worktrees はワークツリーを丸ごと指す glob。
	worktrees = "*/.claude/worktrees/*"
	// 提案と承認済みチケットの置き場（ツリーのルートからの相対）。
	tickets  = "wip/proposals"
	approved = ".ccnavi/approved"
	// ルールの記録名は `<相対パス>:<id>`。
	rulesFile = ".ccnavi/config/common/rules.yml:"

	// チケットの判定の列。子のワークツリーのルートからの相対パス。
	ticketDeny    = "src/deny/x.py"
	ticketAsk     = "src/ask/x.py"
	ticketAllow   = "src/ok/x.py"
	ticketOutside = "docs/x.md"
)

// silent は Write に何も言わないルール（Bash にだけ効く）。
var silent = map[string]any{
	"version": 1,
	"deny":    []any{map[string]any{"id": "push", "match": "Bash", "glob": "*git push*", "message": "push はユーザが行う"}},
}

// rulesFor は表の列の名前からルールを組む。silent は何も言わないルール、それ以外はワークツリーを丸ごと指す 1 本をその種類に置く。
func rulesFor(section string) map[string]any {
	if section == "silent" {
		return silent
	}
	return map[string]any{
		"version": 1,
		"deny":    silent["deny"],
		section: []any{map[string]any{
			"id": "rule-" + section, "match": "Write|Edit|NotebookEdit", "glob": worktrees,
			"message": "RULE-" + strings.ToUpper(section) + "-MESSAGE", "additionalContext": note,
		}},
	}
}

// ws は承認済みの親 1 本と子 1 本と、チケットの無いワークツリー free を置いた木。
type ws struct {
	*ticketkit.Tree
	child, free string
}

// scope はチケットの範囲（Write|Edit）。Deny は ticketkit.TicketOpts に無いので、本文へ足す。
type scope struct{ Allow, Ask, Deny []string }

// defaultChild は表の列が使う子の範囲。
var defaultChild = scope{Allow: []string{"src/*"}, Ask: []string{"src/ask/*"}, Deny: []string{"src/deny/*"}}

// family は親と子を提案して承認し、子のワークツリーを親のブランチから切って着手した木（組んだ写しを戻す）。
// t.Parallel() は family より前に呼ぶ。
func family(t *testing.T, parent, child scope) *ws {
	t.Helper()
	key := "rulemerge " + strings.Join([]string{
		strings.Join(parent.Allow, ","), strings.Join(parent.Ask, ","),
		strings.Join(child.Allow, ","), strings.Join(child.Ask, ","), strings.Join(child.Deny, ","),
	}, "|")
	tr := ticketkit.Cached(t, key, ticketkit.Options{}, func(tr *ticketkit.Tree) {
		tr.ProposeTicket("i0001", ticketkit.TicketOpts{Allow: parent.Allow, Ask: parent.Ask})
		tr.Propose("i0001-01-01", withDeny(ticketkit.TicketText("i0001-01-01", ticketkit.TicketOpts{
			Parent: "i0001", Phase: 1, Allow: child.Allow, Ask: child.Ask,
		}), child.Deny), "")
		tr.CommitParent("tickets")
		ticketkit.OK(t, tr.Approve())
		tr.StartParent()
		tr.Worktree("i0001-01-01", "i0001")
		ticketkit.OK(t, tr.Ccnavi("ticket", "start", "i0001-01-01"))
		tr.CommitParent("start")
		tr.Worktree("free", "main")
	})
	return &ws{Tree: tr, child: tr.ChildTree("i0001-01-01"), free: tr.ChildTree("free")}
}

// withDeny は提案の本文に deny の欄を足す（allow・ask の後ろ。started_at の前）。
func withDeny(text string, globs []string) string {
	if len(globs) == 0 {
		return text
	}
	lines := []string{"deny:"}
	for _, g := range globs {
		lines = append(lines, "  - match: Write|Edit", `    glob: "`+g+`"`)
	}
	return strings.Replace(text, "started_at:", strings.Join(lines, "\n")+"\nstarted_at:", 1)
}

func (w *ws) useRules(body any) {
	w.T.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		w.T.Fatal(err)
	}
	w.useRulesText(string(raw))
}

// useRulesText は共通設定の rules.yml を書き換える（--rules は診断でだけ有効で、hook には渡せない）。
func (w *ws) useRulesText(text string) {
	w.T.Helper()
	ticketkit.Write(w.T, filepath.Join(w.Root, ".ccnavi", "config", "common", "rules.yml"), text)
}

// hookOpts は hook の呼び出しの指定。
type hookOpts struct {
	mode, agent, session string
	env                  map[string]string
	// core・restore は保護の切り替え（既定は disable）。
	core, restore string
}

// hook は payload を組んで入口へ渡す。
func (w *ws) hook(event, tool, cwd string, input map[string]any, o hookOpts) run.Result {
	w.T.Helper()
	if o.mode == "" {
		o.mode = "enable"
	}
	if o.session == "" {
		o.session = "s1"
	}
	payload := map[string]any{"hook_event_name": event, "tool_name": tool, "cwd": cwd, "session_id": o.session, "tool_input": input}
	if o.agent != "" {
		payload["agent_id"] = o.agent
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		w.T.Fatal(err)
	}
	args := []string{"--mode", o.mode}
	if o.core != "" {
		args = append(args, "--guard-core-files", o.core)
	}
	if o.restore != "" {
		args = append(args, "--restore-if-deny", o.restore)
	}
	return w.Call(ticketkit.Call{Stdin: string(raw), Env: o.env}, args...)
}

// writeHook は Write の実行前チェック。
func (w *ws) writeHook(tree, rel string, o hookOpts) run.Result {
	w.T.Helper()
	return w.hook("PreToolUse", "Write", tree, map[string]any{"file_path": filepath.Join(tree, filepath.FromSlash(rel))}, o)
}

// afterShell は記録を取ってからシェルが rel を書いたことにし、その後の PostToolUse を返す。
// 記録はセッションごと。行ごとにセッションを変えれば、前の行が書いたファイルは「前から在った変更」として記録に入り、
// その行が書いた 1 件だけが報告の対象になる。
func (w *ws) afterShell(tree, rel, session string, o hookOpts) run.Result {
	w.T.Helper()
	o.session = session
	baseline := w.hook("PostToolUse", "Bash", tree, map[string]any{"command": "ls"}, o)
	if baseline.Code != 0 && baseline.Code != 2 {
		w.T.Fatalf("記録を取る呼び出しが失敗 %d: %s", baseline.Code, baseline.Stderr)
	}
	ticketkit.Write(w.T, filepath.Join(tree, filepath.FromSlash(rel)), "generated\n")
	return w.hook("PostToolUse", "Bash", tree, map[string]any{"command": "python gen.py"}, o)
}

// subagentStop はサブエージェント終了時のチェック。
func (w *ws) subagentStop(tree, agent string) run.Result {
	w.T.Helper()
	return w.hook("SubagentStop", "", tree, map[string]any{}, hookOpts{agent: agent})
}

// output は hook の応答の hookSpecificOutput。
type output struct{ decision, reason, context string }

func parse(t testing.TB, r run.Result) output {
	t.Helper()
	if strings.TrimSpace(r.Stdout) == "" {
		return output{}
	}
	var out struct {
		Hook struct {
			Decision string `json:"permissionDecision"`
			Reason   string `json:"permissionDecisionReason"`
			Context  string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		t.Fatalf("hook の出力が JSON でない: %q", r.Stdout)
	}
	return output{out.Hook.Decision, out.Hook.Reason, out.Hook.Context}
}

// decision は応答の判定。欄が無ければ通した（additionalContext だけの応答を含む）。
func decision(t testing.TB, r run.Result) string {
	t.Helper()
	if d := parse(t, r).decision; d != "" {
		return d
	}
	return "allow"
}

// reason は理由。無ければ追加の文脈。
func reason(t testing.TB, r run.Result) string {
	t.Helper()
	return ticketkit.Reason(t, r)
}

// lastRecord は判定の記録の最後の 1 行。
func (w *ws) lastRecord() map[string]any {
	w.T.Helper()
	lines := ticketkit.Lines(ticketkit.Read(w.T, filepath.Join(w.Root, "logs", "decisions.jsonl")))
	if len(lines) == 0 {
		w.T.Fatal("判定の記録が空")
	}
	return ticketkit.ParseJSON(w.T, lines[len(lines)-1])
}

func (w *ws) code() string {
	w.T.Helper()
	s, _ := w.lastRecord()["code"].(string)
	return s
}

func (w *ws) rules() []string {
	w.T.Helper()
	var out []string
	list, _ := w.lastRecord()["rules"].([]any)
	for _, v := range list {
		s, _ := v.(string)
		out = append(out, s)
	}
	return out
}

// slash は Windows の区切りを / に揃える（出力に出るパスの比較用）。
func slash(s string) string { return strings.ReplaceAll(s, `\`, "/") }

// sectionOf は出力のうち、見出し head から次の見出しの手前まで。見出しが無ければ空。
func sectionOf(text, head string) string {
	_, rest, ok := strings.Cut(text, head)
	if !ok {
		return ""
	}
	for _, mark := range []string{"\n■", "\n== "} {
		rest, _, _ = strings.Cut(rest, mark)
	}
	return rest
}
