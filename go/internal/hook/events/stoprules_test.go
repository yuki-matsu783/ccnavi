package events

import (
	"bytes"
	"encoding/json"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/testkit"
)

const stopNudgeText = "ここまでの作業を振り返り、スキルの候補が要るかを見ること。"

// stopRule は `match: Stop` の allow のルール 1 本。extra は欄の名前と値の組。
func stopRule(id string, extra ...any) map[string]any {
	r := map[string]any{"id": id, "match": "Stop", "glob": "*"}
	for i := 0; i+1 < len(extra); i += 2 {
		r[extra[i].(string)] = extra[i+1]
	}
	return r
}

// stopRuleSet はルールファイルの中身（JSON は YAML として読める）。deny の push は常に置く。
func stopRuleSet(allow []map[string]any, deny, ask []map[string]any) string {
	deny = append([]map[string]any{{"id": "push", "match": "Bash", "glob": "*git push*", "message": "push はユーザが行う"}}, deny...)
	body, _ := json.Marshal(map[string]any{"version": 1, "deny": deny, "ask": ask, "allow": allow})
	return string(body)
}

// stopTurn は 1 回の Stop。nudged は止めたか。
type stopTurn struct {
	session string
	active  bool
	agent   string
}

var (
	every2 = []map[string]any{stopRule("skill-review", "additionalContext", stopNudgeText, "every", 2)}
	every3 = []map[string]any{stopRule("skill-review", "additionalContext", stopNudgeText, "every", 3)}
)

// `match: Stop` のルールの数えと、止める回・止めない回。止めた回の文と記録は、別のテストが見る。
func TestStopRulesCountAndGates(t *testing.T) {
	turn := stopTurn{}
	sessionB := stopTurn{session: "b"}
	active := stopTurn{active: true}
	sub := stopTurn{agent: "a1"}
	cases := []struct {
		name  string
		allow []map[string]any
		deny  []map[string]any
		ask   []map[string]any
		files map[string]string // ワークスペースからの相対
		turns []stopTurn
		want  []bool
	}{
		{"Stop のルールが無ければ止めない",
			[]map[string]any{{"id": "src", "match": "Write", "glob": "*/src/*"}}, nil, nil, nil,
			[]stopTurn{turn, turn, turn}, []bool{false, false, false}},
		{"every の回数ごとに 1 度", every3, nil, nil, nil,
			[]stopTurn{turn, turn, turn, turn, turn, turn}, []bool{false, false, true, false, false, true}},
		{"数えはセッションごと", every2, nil, nil, nil,
			[]stopTurn{{session: "a"}, sessionB, {session: "a"}}, []bool{false, false, true}},
		{"stop_hook_active の回は止めず数えない", every2, nil, nil, nil,
			[]stopTurn{turn, active, active, turn}, []bool{false, false, false, true}},
		{"サブエージェントは止めず数えない", every2, nil, nil, nil,
			[]stopTurn{sub, sub, sub, sub, turn, turn}, []bool{false, false, false, false, false, true}},
		{"every が無い・1 のルールは実行時にも使わない",
			[]map[string]any{stopRule("each", "additionalContext", stopNudgeText),
				stopRule("once", "additionalContextOnce", stopNudgeText, "every", 1)},
			nil, nil, nil, []stopTurn{turn, turn, turn}, []bool{false, false, false}},
		{"deny・ask に置いた Stop は止めない", nil,
			[]map[string]any{stopRule("stop-deny", "message", "止める")},
			[]map[string]any{stopRule("stop-ask", "additionalContext", stopNudgeText)}, nil,
			[]stopTurn{turn, turn, turn, turn}, []bool{false, false, false, false}},
		{"プロジェクトの設定の Stop は見ない", nil, nil, nil,
			map[string]string{"projects/lib/.git/HEAD": "x", "projects/lib/.ccnavi/config/project/rules.yml": stopRuleSet(
				[]map[string]any{stopRule("lib-review", "additionalContext", "外の文", "every", 2)}, nil, nil)},
			[]stopTurn{turn, turn, turn, turn}, []bool{false, false, false, false}},
		{"プロジェクトに写した共通ルールが古くても二重に数えない", every2, nil, nil,
			map[string]string{"projects/lib/.git/HEAD": "x", "projects/lib/.ccnavi/config/project/rules.yml": stopRuleSet(
				[]map[string]any{stopRule("skill-review", "additionalContext", stopNudgeText, "every", 3)}, nil, nil)},
			[]stopTurn{turn, turn, turn, turn},
			[]bool{false, true, false, true}},
		{"ワークスペースの設定のルールも当たり、同じ id は 1 本だけ数える", every2, nil, nil,
			map[string]string{".ccnavi/config/project/rules.yml": stopRuleSet(
				[]map[string]any{stopRule("skill-review", "additionalContext", "ワークスペースの設定", "every", 3)}, nil, nil)},
			[]stopTurn{turn, turn, turn, turn}, []bool{false, true, false, true}},
		{"数えの記録が読めなければ止めない", every2, nil, nil,
			map[string]string{"logs/state/stop-s1.json": "{壊れた"},
			[]stopTurn{turn, turn, turn, turn}, []bool{false, false, false, false}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			root := stopWorkspace(t, c.allow, c.deny, c.ask, c.files)
			conf := testkit.Settings(t, root)
			var got []bool
			for _, s := range c.turns {
				text, _ := runStopRules(t, conf, root, s)
				got = append(got, text != "")
			}
			if !slices.Equal(got, c.want) {
				t.Errorf("止めた回 %v、want %v", got, c.want)
			}
		})
	}
}

func stopWorkspace(t *testing.T, allow, deny, ask []map[string]any, files map[string]string) string {
	t.Helper()
	root := testkit.TempRoot(t)
	testkit.CopyMessages(t, root)
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), stopRuleSet(allow, deny, ask))
	for rel, text := range files {
		testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), text)
	}
	return root
}

// runStopRules は stopRulesNudge を 1 回呼ぶ。止めた文と、その回の記録を返す。
func runStopRules(t *testing.T, conf *config.Settings, root string, s stopTurn) (string, *audit.Record) {
	t.Helper()
	session := s.session
	if session == "" {
		session = "s1"
	}
	record := &audit.Record{}
	in := hookio.Input{Event: hookio.Stop, SessionID: session, Cwd: root, StopHookActive: s.active, AgentID: s.agent}
	var stderr bytes.Buffer
	return stopRulesNudge(&stderr, conf, root, in, record), record
}

// 数えを覚える置き場が無い回は止めない（覚えられないまま止めると毎回止まる）。
func TestStopRulesWithoutAStateDirectoryNeverStop(t *testing.T) {
	root := stopWorkspace(t, every2, nil, nil, nil)
	conf := testkit.Settings(t, root)
	conf.State = ""
	for range 4 {
		if text, _ := runStopRules(t, conf, root, stopTurn{}); text != "" {
			t.Fatalf("置き場が無いのに止めた: %q", text)
		}
	}
}

// 止めた文は、ルールの文が何であっても決まった前置きで始まる。本文のファイルはワークスペースルートの版だけを読む。
func TestStopRulesTextAndRecord(t *testing.T) {
	root := stopWorkspace(t,
		[]map[string]any{
			stopRule("two", "additionalContext", "二", "every", 2),
			stopRule("three", "additionalContext", "三", "every", 3),
			stopRule("file", "additionalContextFile", "docs/review.md", "every", 2),
		}, nil, nil,
		map[string]string{
			"docs/review.md":              "ワークスペースの手順",
			"projects/lib/docs/review.md": "プロジェクトの手順",
			"projects/lib/.git/HEAD":      "x",
		})
	conf := testkit.Settings(t, root)
	if text, _ := runStopRules(t, conf, root, stopTurn{}); text != "" {
		t.Fatalf("1 回目で止めた: %q", text)
	}
	text, record := runStopRules(t, conf, root, stopTurn{})
	prefix := codeRuleNudge + ": これはタスクの続きではない。"
	if !strings.HasPrefix(text, prefix) {
		t.Fatalf("前置きで始まらない: %q", text)
	}
	for _, want := range []string{"問いを最後にもう一度書いて", "「振り返り: 無し」", "二", "ワークスペースの手順"} {
		if !strings.Contains(text, want) {
			t.Errorf("%q が無い: %q", want, text)
		}
	}
	if strings.Contains(text, "プロジェクトの手順") || strings.Contains(text, "三") {
		t.Errorf("読まないはずの文が載った: %q", text)
	}
	if strings.Index(text, "振り返り: 無し") > strings.Index(text, "二") {
		t.Errorf("前置きがルールの文の後にある: %q", text)
	}
	// 記録に残すのは、渡したルールだけ。
	if record.Code != codeRuleNudge || record.Decision != audit.Nudge || !record.Enforced ||
		!slices.Equal(record.Rules, []string{".ccnavi/config/common/rules.yml:two", ".ccnavi/config/common/rules.yml:file"}) {
		t.Errorf("記録: %+v", record)
	}
}
