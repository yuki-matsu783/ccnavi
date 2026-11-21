package post

// ターンの分離（発言のあとにしか進めない手順のゲート）と、ターン中の印を、hook の入口から実際の呼び出しの順で確かめる。
// ゲートの細かい場合分け（発言の形ごとの数え方・付き添いの有無・サブエージェント・失敗や拒否された依頼）は、Go の
// hook/events の TestTurnGateMatchesGolden と hookio の TestIsUserPrompt が見る。ここでは入口（標準入出力・終了コード・記録・
// 置き場のファイル）に届くところだけを見る。

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

const (
	gitSh    = "sh .ccnavi/scripts/ccnavi-git.sh"
	reviewSh = "sh .ccnavi/scripts/ccnavi-review.sh"
	integr   = gitSh + " integrate foo"
	integDo  = gitSh + " integrate-done foo -m 'msg x'"
	request  = reviewSh + " review-request --phase 1 --body-file req.md"
	confirm  = reviewSh + " confirm --phase 1"
	notice   = "<task-notification>\n<status>completed</status>\n</task-notification>"
	needTurn = "DENY_NEEDS_USER_TURN"
)

type gate struct {
	root     string
	attended string // CLAUDE_CODE_SESSION_ATTENDED。空なら渡さない（付き添いの有無が未設定）
	calls    int
}

func newGate(t *testing.T, attended string) *gate {
	t.Helper()
	root := base.TempDir(t)
	if err := os.MkdirAll(filepath.Join(root, ".ccnavi", "config", "common"), 0o755); err != nil {
		t.Fatal(err)
	}
	return &gate{root: root, attended: attended}
}

func (g *gate) hook(t *testing.T, event string, c call) run.Result {
	t.Helper()
	c.Event = event
	c.Env = map[string]string{env.Mode: "enable"}
	if g.attended != "" {
		c.Env[env.ClaudeSessionAttended] = g.attended
	}
	return fire(t, g.root, c)
}

func (g *gate) mode(t *testing.T, mode, event string, c call) run.Result {
	t.Helper()
	c.Event = event
	c.Env = map[string]string{env.Mode: mode}
	if g.attended != "" {
		c.Env[env.ClaudeSessionAttended] = g.attended
	}
	return fire(t, g.root, c)
}

func (g *gate) say(t *testing.T, text string) run.Result {
	t.Helper()
	return g.hook(t, "UserPromptSubmit", call{Prompt: text})
}

func (g *gate) run(t *testing.T, command string, extra map[string]any) run.Result {
	t.Helper()
	g.calls++
	body := map[string]any{"tool_use_id": "toolu_" + strconv.Itoa(g.calls)}
	for k, v := range extra {
		body[k] = v
	}
	return g.hook(t, "PreToolUse", call{Input: map[string]any{"command": command}, Extra: body})
}

func stopped(r run.Result) bool { return strings.Contains(r.Stdout, needTurn) }
func asked(r run.Result) bool   { return strings.Contains(r.Stdout, `"permissionDecision": "ask"`) }

func (g *gate) entries() []string {
	names, _ := os.ReadDir(filepath.Join(g.root, "logs", "state"))
	var out []string
	for _, n := range names {
		if strings.HasPrefix(n.Name(), "turngate-") {
			out = append(out, n.Name())
		}
	}
	return out
}

func (g *gate) decisions(t *testing.T, decision string) []map[string]any {
	var out []map[string]any
	for _, row := range records(t, g.root) {
		if str(row, "decision") == decision {
			out = append(out, row)
		}
	}
	return out
}

// 同じ発言の中では止まり、ユーザの発言を 1 回はさむと通る。バックグラウンド作業の完了通知は発言に数えない。
// 止めた記録は拒否コードと宣言の名前を残し、通ったあとは記録が消える。
func TestAStepThatNeedsAUserTurnIsStoppedUntilTheUserSpeaks(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name, first, second, rule, text string
	}{
		{"integrate-done after integrate", integr, integDo, "builtin:turn-gate:integrate-user-turn", "キー foo"},
		{"confirm after review-request", request, confirm, "builtin:turn-gate:review-confirm-user-turn", "ユーザのレビュー"},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			g := newGate(t, "0")
			g.say(t, "お願いします")
			if stopped(g.run(t, c.first, nil)) {
				t.Fatal("最初の 1 手は止めない")
			}

			done := g.run(t, c.second, nil)

			if !stopped(done) {
				t.Fatalf("同じ発言の中は止まる: %s", done.Stdout)
			}
			mustHave(t, done.Stdout, `"permissionDecision": "deny"`, c.rule, c.text)
			denied := g.decisions(t, "deny")
			if len(denied) != 1 || str(denied[0], "code") != needTurn || strings.Join(strs(denied[0], "rules"), "|") != c.rule {
				t.Errorf("止めた記録は拒否コードと宣言を残す: %v", denied)
			}

			g.hook(t, "Stop", call{})
			g.say(t, notice)
			if !stopped(g.run(t, c.second, nil)) {
				t.Error("完了通知は発言に数えない")
			}
			g.hook(t, "Stop", call{})
			g.say(t, "確認しました")
			if stopped(g.run(t, c.second, nil)) {
				t.Error("ユーザの発言を挟むと通る")
			}
			if len(g.entries()) != 0 {
				t.Errorf("通ったあとは記録が消える: %v", g.entries())
			}
		})
	}
}

// 付き添いのあるセッションでは、`integrate-done` は発言を待たず確認（ask）に出る。同じコマンドに並べた呼び出しだけは確認でなく
// 止まる。`confirm` は付き添いがあっても確認に出さず止める。サブエージェントは確認に出さず発言を待つ。
func TestAnAttendedSessionIsAskedNotStoppedExceptForChainsConfirmsAndSubagents(t *testing.T) {
	t.Parallel()
	t.Run("integrate-done asks and the record says so", func(t *testing.T) {
		t.Parallel()
		g := newGate(t, "1")
		g.say(t, "お願いします")
		g.run(t, integr, nil)

		done := g.run(t, integDo, nil)

		if !asked(done) || stopped(done) {
			t.Fatalf("確認に出る: %s", done.Stdout)
		}
		mustHave(t, done.Stdout, "builtin:turn-gate:integrate-user-turn", "ユーザの確認が要ります")
		var rows []map[string]any
		for _, row := range g.decisions(t, "ask") {
			if str(row, "code") == "RULE_ASK" {
				rows = append(rows, row)
			}
		}
		if len(rows) != 1 || str(rows[0], "code") != "RULE_ASK" || strings.Join(strs(rows[0], "rules"), "|") != "builtin:turn-gate:integrate-user-turn" {
			t.Errorf("record = %v", rows)
		}
	})
	t.Run("a chain in one command is stopped", func(t *testing.T) {
		t.Parallel()
		g := newGate(t, "1")
		g.say(t, "お願いします")
		done := g.run(t, integr+" && "+integDo, nil)
		if !stopped(done) || asked(done) {
			t.Errorf("stdout = %s", done.Stdout)
		}
	})
	t.Run("confirm is stopped, and opened by a user turn", func(t *testing.T) {
		t.Parallel()
		g := newGate(t, "1")
		g.say(t, "お願いします")
		g.run(t, request, nil)
		done := g.run(t, confirm, nil)
		if !stopped(done) || asked(done) {
			t.Fatalf("stdout = %s", done.Stdout)
		}
		g.say(t, "レビューが済みました")
		if got := g.run(t, confirm, nil); stopped(got) || strings.Contains(got.Stdout, "builtin:turn-gate") {
			t.Errorf("stdout = %s", got.Stdout)
		}
	})
	t.Run("a subagent waits for the user", func(t *testing.T) {
		t.Parallel()
		g := newGate(t, "1")
		g.say(t, "お願いします")
		if asked(g.run(t, integDo, map[string]any{"agent_id": "sub1", "agent_type": "x"})) {
			t.Error("サブエージェントは確認に出さない")
		}
	})
	t.Run("an unset attendance is an attended session", func(t *testing.T) {
		t.Parallel()
		g := newGate(t, "")
		g.say(t, "お願いします")
		if !asked(g.run(t, integDo, nil)) {
			t.Error("付き添いの有無が未設定なら対話として扱う")
		}
	})
}

// 壊れたゲートの記録は止め（通すと発言を待つ約束が消える）、次のユーザの発言で消える。完了通知では消えない。
// ゲートは dry-run でも止まり、判定を切っているモードでは働かない。再開と起動は記録を消し、compact は消さない。
func TestABrokenGateRecordStopsAndAModeOrASessionStartChangesWhatApplies(t *testing.T) {
	t.Parallel()
	t.Run("a broken record", func(t *testing.T) {
		t.Parallel()
		g := newGate(t, "0")
		g.say(t, "お願いします")
		g.run(t, integr, nil)
		name := g.entries()[0]
		write(t, filepath.Join(g.root, "logs", "state", name), "{not json")

		done := g.run(t, integDo, nil)
		if !stopped(done) || !strings.Contains(done.Stdout, name) {
			t.Fatalf("壊れた記録は止める: %s", done.Stdout)
		}
		g.say(t, notice)
		if !stopped(g.run(t, integDo, nil)) {
			t.Error("完了通知では消えない")
		}
		g.say(t, "どうぞ")
		if len(g.entries()) != 0 || stopped(g.run(t, integDo, nil)) {
			t.Error("次のユーザの発言で消える")
		}
	})
	t.Run("dry-run still stops", func(t *testing.T) {
		t.Parallel()
		g := newGate(t, "0")
		g.mode(t, "dry-run", "UserPromptSubmit", call{Prompt: "お願いします"})
		g.mode(t, "dry-run", "PreToolUse", call{Input: map[string]any{"command": integr}})
		done := g.mode(t, "dry-run", "PreToolUse", call{Input: map[string]any{"command": integDo}})
		if !stopped(done) || !strings.Contains(done.Stdout, "this stop still applies") {
			t.Errorf("stdout = %s", done.Stdout)
		}
	})
	t.Run("a disabled mode does nothing", func(t *testing.T) {
		t.Parallel()
		g := newGate(t, "0")
		g.mode(t, "disable", "UserPromptSubmit", call{Prompt: "お願いします"})
		g.mode(t, "disable", "PreToolUse", call{Input: map[string]any{"command": integr}})
		if stopped(g.mode(t, "disable", "PreToolUse", call{Input: map[string]any{"command": integDo}})) {
			t.Error("判定を切っているモードでは働かない")
		}
	})
	for _, c := range []struct {
		source  string
		keeps   bool
		comment string
	}{{"resume", false, "再開は記録を破棄する"}, {"startup", false, "起動は記録を破棄する"}, {"compact", true, "ターンの途中で compact が走りうるので破棄しない"}} {
		t.Run("session start "+c.source, func(t *testing.T) {
			t.Parallel()
			g := newGate(t, "0")
			g.say(t, "お願いします")
			g.run(t, integr, nil)
			g.hook(t, "SessionStart", call{Source: c.source})
			if got := stopped(g.run(t, integDo, nil)); got != c.keeps {
				t.Errorf("%s: stopped=%v entries=%v", c.comment, got, g.entries())
			}
		})
	}
}

// ---- ターン中の印 ----

// 印はセッションごとのファイルで、ユーザの発言で置き、ツール呼び出しで更新時刻を新しくし、Stop と中断で消す。
// VS Code 拡張は `--explain --json` の `turns` でこれを読む。印の読み書きそのもの（古い印・壊れた印・置き場が無いとき）は
// Go の records/turn のテストが確かめる。
type board struct{ root string }

func newBoard(t *testing.T) *board {
	root := base.TempDir(t)
	if err := os.MkdirAll(filepath.Join(root, ".ccnavi", "config", "common"), 0o755); err != nil {
		t.Fatal(err)
	}
	return &board{root}
}

func (b *board) hook(t *testing.T, event, session, mode string, c call) {
	t.Helper()
	if session != "" {
		c.Session = session
	}
	c.Event = event
	c.Input = map[string]any{"command": "ls"}
	// 動作モードを切るのは環境変数だけ（プロジェクト内から来る `--mode disable` は無視される）。
	if mode == "" {
		mode = "enable"
	}
	c.Env = map[string]string{env.Mode: mode}
	fire(t, b.root, c)
}

func (b *board) turns(t *testing.T) []map[string]any {
	t.Helper()
	got := run.Ccnavi(t, []string{"--explain", "--json", "--root", b.root}, run.Opts{Dir: base.Root()})
	var out struct{ Turns []map[string]any }
	if err := json.Unmarshal([]byte(got.Stdout), &out); err != nil {
		t.Fatalf("stdout = %q: %v", got.Stdout, err)
	}
	return out.Turns
}

func (b *board) active(t *testing.T) string {
	var names []string
	for _, turn := range b.turns(t) {
		names = append(names, str(turn, "session"))
	}
	sort.Strings(names)
	return strings.Join(names, ",")
}

func (b *board) marker(session string) string {
	return filepath.Join(b.root, "logs", "state", "turn-"+session+".json")
}

func TestTheTurnMarkerExistsOnlyBetweenAPromptAndTheEndOfTheTurn(t *testing.T) {
	t.Parallel()
	type step struct {
		event, session, mode string
		c                    call
		want                 string // ターン中のセッション
	}
	prompt := call{Prompt: "お願いします"}
	cases := []struct {
		name  string
		steps []step
	}{
		{"prompt to stop", []step{
			{"SessionStart", "", "", call{Source: "startup"}, ""},
			{"UserPromptSubmit", "", "", prompt, "s1"},
			{"PreToolUse", "", "", call{}, "s1"},
			{"PostToolUse", "", "", call{}, "s1"},
			{"Stop", "", "", call{}, ""},
		}},
		{"a user interrupt closes the turn, a subagent's does not, an ordinary failure does not", []step{
			{"UserPromptSubmit", "", "", prompt, "s1"},
			{"PostToolUseFailure", "", "", call{Extra: map[string]any{"is_interrupt": true, "agent_id": "sub1"}}, "s1"},
			{"PostToolUseFailure", "", "", call{Extra: map[string]any{"error": "boom"}}, "s1"},
			{"PostToolUseFailure", "", "", call{Extra: map[string]any{"is_interrupt": true}}, ""}, // 中断では Stop が来ないことがある
		}},
		{"a completion notice starts a turn", []step{
			{"UserPromptSubmit", "", "", call{Prompt: notice}, "s1"},
		}},
		{"a tool call after the stop does not make a marker again", []step{
			{"UserPromptSubmit", "", "", prompt, "s1"},
			{"Stop", "", "", call{}, ""},
			{"PostToolUse", "", "", call{Extra: map[string]any{"agent_id": "sub1"}}, ""},
		}},
		{"resume discards the marker of the earlier process and compact does not", []step{
			{"UserPromptSubmit", "", "", prompt, "s1"},
			{"SessionStart", "", "", call{Source: "compact"}, "s1"},
			{"SessionStart", "", "", call{Source: "resume"}, ""},
		}},
		{"sessions are independent", []step{
			{"UserPromptSubmit", "a", "", call{Prompt: "一つ目"}, "a"},
			{"UserPromptSubmit", "b", "", call{Prompt: "二つ目"}, "a,b"},
			{"Stop", "a", "", call{}, "b"},
			{"SessionStart", "a", "", call{Source: "resume"}, "b"},
		}},
		{"a disabled mode sets no marker", []step{
			{"UserPromptSubmit", "", "disable", prompt, ""},
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			b := newBoard(t)
			for i, s := range c.steps {
				b.hook(t, s.event, s.session, s.mode, s.c)
				if got := b.active(t); got != s.want {
					t.Fatalf("手順 %d（%s）のあと: ターン中 = %q, want %q", i, s.event, got, s.want)
				}
			}
		})
	}
}

// 印の更新時刻が古いと別の判断（古い印は消える）になるので、ツール呼び出しのたびに新しくする。
func TestAToolCallRefreshesTheMarkerAge(t *testing.T) {
	t.Parallel()
	b := newBoard(t)
	b.hook(t, "UserPromptSubmit", "", "", call{Prompt: "お願いします"})
	old := time.Now().Add(-10 * time.Minute)
	if err := os.Chtimes(b.marker("s1"), old, old); err != nil {
		t.Fatal(err)
	}
	b.hook(t, "PreToolUse", "", "", call{})
	info, err := os.Stat(b.marker("s1"))
	if err != nil {
		t.Fatal(err)
	}
	if !info.ModTime().After(old.Add(5 * time.Minute)) {
		t.Errorf("更新時刻が新しくなっていない: %v", info.ModTime())
	}
}

// 促しで Stop を止めると、モデルは同じターンの続きを動かす。続きの間も印が要る。
func TestAStopThatIsBlockedKeepsTheMarkerBecauseTheTurnContinues(t *testing.T) {
	t.Parallel()
	b := newBoard(t)
	// 2 回に 1 度止める Stop のルールで、2 回目の Stop を止めさせる。
	rule, _ := json.Marshal(map[string]any{"version": 1, "allow": []any{map[string]any{
		"id": "nudge", "match": "Stop", "glob": "*", "additionalContext": "続ける", "every": 2}}})
	write(t, base.CommonPath(b.root, "rules"), string(rule))
	post := func(event string, c call) run.Result {
		c.Event = event
		c.Env = map[string]string{env.Mode: "enable"}
		c.Input = map[string]any{"command": "ls"}
		return fire(t, b.root, c)
	}
	post("UserPromptSubmit", call{Prompt: "お願いします"})
	if strings.Contains(post("Stop", call{}).Stdout, "block") {
		t.Fatal("1 回目の Stop は止めない")
	}
	post("UserPromptSubmit", call{Prompt: "続きを"})
	if got := post("Stop", call{}); !strings.Contains(got.Stdout, "block") || b.active(t) != "s1" {
		t.Fatalf("止めた Stop ではターンが続くので印を残す: %q active=%q", got.Stdout, b.active(t))
	}
	post("Stop", call{Extra: map[string]any{"stop_hook_active": true}})
	if b.active(t) != "" {
		t.Error("続きの Stop で印が消える")
	}
}

// 板の JSON の `turns` は、ターン中のセッションだけを session・since・seen の 3 項目で出す。
func TestTheBoardJSONListsTurnsInProgress(t *testing.T) {
	t.Parallel()
	b := newBoard(t)
	if got := b.turns(t); len(got) != 0 {
		t.Fatalf("ターン中でなければ turns は空: %v", got)
	}
	b.hook(t, "UserPromptSubmit", "a", "", call{Prompt: "一つ目"})
	b.hook(t, "UserPromptSubmit", "b", "", call{Prompt: "二つ目"})
	b.hook(t, "Stop", "a", "", call{})
	turns := b.turns(t)
	if len(turns) != 1 || str(turns[0], "session") != "b" || len(turns[0]) != 3 {
		t.Fatalf("turns = %v", turns)
	}
	for _, key := range []string{"session", "since", "seen"} {
		if _, ok := turns[0][key]; !ok {
			t.Errorf("%s が無い: %v", key, turns[0])
		}
	}
}
