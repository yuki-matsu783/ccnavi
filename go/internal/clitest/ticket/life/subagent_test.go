package life

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// SubagentStart が、同じワークツリーで動く別の編集するサブエージェントを案内する（#433）。起動したサブエージェントの additionalContext に、
// 同じツリーで実行中の別の編集するサブエージェントがいることを 1〜3 文で足す。止めない。
//   - 同じツリーで編集するサブエージェントが 2 つ動けば、後から起動したほうに案内が出る。先に動いていたほうには出ない
//   - 読み取り専用（Explore、tools に書き込み系が無い定義）は、案内を受けず、相手にも数えない
//   - 定義が無い・tools に制限が無いサブエージェントは、編集するものとして数える
//   - ツリーが違えば出ない。終わったサブエージェントは数えない。別のセッションのサブエージェントは数えない
//   - 再開で SubagentStart がまた走っても、同じ相手を重ねて案内しない。新しい相手には案内する

const (
	sharedMark = "別の編集するサブエージェントが動いています"
	editor     = "general-purpose"
)

// sharedStep は SubagentStart（stop なら SubagentStop）の 1 回。want は案内の有無（"" なら見ない）。
type sharedStep struct {
	id, typ  string
	cwd      string // "root"（既定）か "parent"
	session  string
	tools    []string
	stop     bool
	want     string // "yes"・"no"・""
	contains []string
}

func sharedStart(tr *ticketkit.Tree, s sharedStep) string {
	tr.T.Helper()
	cwd := tr.Root
	if s.cwd == "parent" {
		cwd = tr.ParentTree
	}
	session := s.session
	if session == "" {
		session = "s1"
	}
	event := "SubagentStart"
	if s.stop {
		event = "SubagentStop"
	}
	payload := map[string]any{"hook_event_name": event, "cwd": cwd, "session_id": session, "agent_id": s.id}
	if !s.stop {
		payload["agent_type"] = s.typ
		if s.tools != nil {
			payload["tools"] = s.tools
		}
	}
	return ticketkit.Reason(tr.T, ticketkit.OK(tr.T, rawCall(tr, payload)))
}

func defineAgent(tr *ticketkit.Tree, name, front string) {
	tr.T.Helper()
	ticketkit.Write(tr.T, filepath.Join(tr.Root, ".claude", "agents", name+".md"), "---\nname: "+name+"\n"+front+"---\n本文\n")
}

func TestSharedTreeAgents(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name    string
		defines map[string]string
		steps   []sharedStep
	}{
		{"the_later_editor_is_told_and_the_earlier_one_is_not", nil, []sharedStep{
			{id: "a1", typ: editor, want: "no"},
			{id: "a2", typ: editor, want: "yes", contains: []string{"ワークスペースルート", "decisions.md", editor + " 1 件", "スキル候補"}},
		}},
		{"read_only_agents_are_neither_told_nor_counted", nil, []sharedStep{
			{id: "r1", typ: "Explore", want: "no"}, {id: "a1", typ: editor, want: "no"},
			{id: "r2", typ: "Plan", want: "no"}, {id: "a2", typ: editor, want: "yes"},
		}},
		{"a_definition_without_write_tools_is_read_only", map[string]string{"reader": "tools: Read, Grep, Glob\n"}, []sharedStep{
			{id: "a1", typ: editor, want: "no"}, {id: "r1", typ: "reader", want: "no"},
		}},
		{"a_definition_with_a_write_tool_or_no_limit_is_an_editor", map[string]string{"writer": "tools: Read, Edit\n", "open": "description: 制限なし\n"}, []sharedStep{
			{id: "a1", typ: "writer", want: "no"}, {id: "a2", typ: "open", want: "yes"},
		}},
		{"a_definition_wins_over_a_builtin_name", map[string]string{"Explore": "tools: Read, Write\n"}, []sharedStep{
			{id: "a1", typ: editor, want: "no"}, {id: "a2", typ: "Explore", want: "yes"},
		}},
		{"disallowed_tools_do_not_make_a_read_only_agent", map[string]string{"narrow": "disallowedTools: Write, Edit, NotebookEdit\n"}, []sharedStep{
			{id: "a1", typ: editor, want: "no"}, {id: "a2", typ: "narrow", want: "yes"},
		}},
		{"an_unknown_agent_type_counts_as_an_editor", nil, []sharedStep{
			{id: "a1", typ: "no-such-definition", want: "no"}, {id: "a2", typ: "", want: "yes"},
		}},
		{"payload_tools_win_over_the_definition", nil, []sharedStep{
			{id: "a1", typ: editor, want: "no"}, {id: "r1", typ: editor, tools: []string{"Read", "Grep"}, want: "no"},
			{id: "a2", typ: editor, tools: []string{"Read", "Write"}, want: "yes"},
		}},
		{"other_trees_do_not_count", nil, []sharedStep{
			{id: "a1", typ: editor, want: "no"}, {id: "a2", typ: editor, cwd: "parent", want: "no"}, {id: "a3", typ: editor, cwd: "parent", want: "yes"},
		}},
		{"a_stopped_agent_is_not_counted", nil, []sharedStep{
			{id: "a1", typ: editor}, {id: "a1", stop: true}, {id: "a2", typ: editor, want: "no"},
		}},
		{"another_session_is_not_counted", nil, []sharedStep{
			{id: "a1", typ: editor, session: "s1"}, {id: "a2", typ: editor, session: "s2", want: "no"},
		}},
		{"a_resumed_start_does_not_repeat_the_same_peer", nil, []sharedStep{
			{id: "a1", typ: editor}, {id: "a2", typ: editor, want: "yes"}, {id: "a2", typ: editor, want: "no"},
		}},
		{"a_new_peer_is_told_after_a_resume", nil, []sharedStep{
			{id: "a1", typ: editor}, {id: "a2", typ: editor, want: "yes"}, {id: "a3", typ: editor},
			{id: "a2", typ: editor, want: "yes", contains: []string{editor + " 1 件"}},
		}},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := phasesTree(t)
			for name, front := range c.defines {
				defineAgent(tr, name, front)
			}
			for _, s := range c.steps {
				text := sharedStart(tr, s)
				switch s.want {
				case "yes":
					ticketkit.Has(t, text, sharedMark)
				case "no":
					ticketkit.Lacks(t, text, sharedMark)
				}
				for _, w := range s.contains {
					ticketkit.Has(t, text, w)
				}
			}
		})
	}
}

// 読み取りと確かめられない一覧は、編集するものとして数える。
func TestOnlyConfirmedReadToolsMakeAReadOnlyAgent(t *testing.T) {
	t.Parallel()
	tr := phasesTree(t)
	sharedStart(tr, sharedStep{id: "peer", typ: editor})
	for i, tools := range [][]string{{"*"}, {"Read", "Edit(src/**)"}, {"Read", "Bash"}, {"Read", "mcp__fs__write_file"}, {}} {
		// 案内した相手は agent_id ごとに覚えるので、繰り返しごとに別の ID で起動する。
		id := "probe-" + string(rune('0'+i))
		ticketkit.Has(t, sharedStart(tr, sharedStep{id: id, typ: editor, tools: tools}), sharedMark)
	}
	ticketkit.Lacks(t, sharedStart(tr, sharedStep{id: "probe-read", typ: editor, tools: []string{"read", "GREP"}}), sharedMark)
}

// ---- 親の局面を 1 行で名指しする（#207、REQ-TKT-35 の後半）

// 子の行（`  <子>（親 X、フェーズ N: タイトル）: ...`）のあとに、親の局面を `親 <X> の局面: <ボードと同じ局面の文字列>` の形で 1 行足す。
// 局面の文はボードと同じ計算（tickets/phase の Stage）で、行は hook/childnote が組む。

const stageLine = "親 i0001 の局面: "

func startText(tr *ticketkit.Tree, cwd string) string {
	tr.T.Helper()
	return ticketkit.Reason(tr.T, ticketkit.OK(tr.T, tr.Hook("SubagentStart", "", cwd, nil, ticketkit.HookOpts{AgentID: "sub-1"})))
}

// boardStage はボードが出す親の局面（SubagentStart と同じ計算）。期待値の元にする。
func boardStage(tr *ticketkit.Tree) string {
	tr.T.Helper()
	var b struct {
		Parents []struct {
			Ticket string `json:"ticket"`
			Stage  string `json:"stage"`
		} `json:"parents"`
	}
	jsonOf(tr.T, ticketkit.OK(tr.T, tr.Ccnavi("--explain", "--json")).Stdout, &b)
	for _, p := range b.Parents {
		if p.Ticket == "i0001" {
			return p.Stage
		}
	}
	tr.T.Fatal("板に i0001 が無い")
	return ""
}

// assertLineAfterChild は局面の行が出ていて、子の行より後ろにある。
func assertLineAfterChild(t *testing.T, text, child, stage string) {
	t.Helper()
	at := strings.Index(text, stageLine+stage)
	if at < 0 {
		t.Fatalf("局面の行が無い（%q）:\n%s", stageLine+stage, text)
	}
	if childAt := strings.Index(text, child); childAt < 0 || at <= childAt {
		t.Errorf("局面の行が子の行（%s）より前:\n%s", child, text)
	}
}

func TestTheStageLineComesAfterTheChildLine(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name string
		from func(tr *ticketkit.Tree, child string) string // 起動する cwd
	}{
		{"the_parent_tree", func(tr *ticketkit.Tree, _ string) string { return tr.ParentTree }},
		{"a_child_tree_names_the_stage_of_its_own_parent", func(_ *ticketkit.Tree, child string) string { return child }},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := phasesTree(t)
			openFamily(tr, samples.Plan("research", "design"), launchChild{childName, 1, researchScope})
			child := tr.RunChild(childName)
			stage := boardStage(tr)
			if !strings.HasPrefix(stage, "作業中") {
				t.Fatalf("局面 = %q", stage)
			}
			assertLineAfterChild(t, startText(tr, c.from(tr, child)), childName, stage)
		})
	}
}

// 並行する枝は、ボードと同じ局面の文字列（番号を並べた形）がそのまま出る。親が複数の子を持っても、行は 1 回だけ。
func TestParallelBranchesAreListedAsPhaseStageGivesThemAndOnce(t *testing.T) {
	t.Parallel()
	tr := phasesTree(t)
	openFamily(tr, parallelPlan, launchChild{childName, 1, researchScope}, launchChild{launchChild2, 2, designScope})
	tr.RunChild(childName)
	tr.RunChild(launchChild2)
	stage := boardStage(tr)
	if !strings.HasPrefix(stage, "作業中") || !strings.Contains(stage, "1") || !strings.Contains(stage, "2") {
		t.Fatalf("局面 = %q", stage)
	}
	text := startText(tr, tr.ParentTree)
	assertLineAfterChild(t, text, childName, stage)
	ticketkit.Has(t, text, launchChild2)
	if n := strings.Count(text, "の局面: "); n != 1 {
		t.Errorf("局面の行が %d 回:\n%s", n, text)
	}
}

// 止まっている枝がある局面。もう片方の枝の子が開いている間に、SubagentStart を起こす。
func TestTheStageOfAPhaseWaitingForItsReview(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name, want string
		setup      func(tr *ticketkit.Tree)
	}{
		{"to_be_prepared", "レビュー準備中", func(*ticketkit.Tree) {}},
		{"requested", "レビュー待ち", func(tr *ticketkit.Tree) {
			ticketkit.Write(t, filepath.Join(tr.Approved, "phases", "i0001", "2.requested"), `{"at": "x"}`)
		}},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := phasesTree(t)
			openFamily(tr, parallelPlan, launchChild{childName, 1, researchScope}, launchChild{launchChild2, 2, designScope})
			tr.RunChild(childName)
			tr.RunChild(launchChild2, ticketkit.File{Path: "wip/design/a.md", Text: "設計\n"})
			// 設計（mr）の子を閉じ、レビューが要る枝を止める。研究の子は開いたまま。
			ticketkit.OK(t, tr.Ccnavi("ticket", "finish", launchChild2))
			tr.CommitParent("close 02")
			tr.Merge(launchChild2)
			c.setup(tr)
			stage := boardStage(tr)
			ticketkit.Has(t, stage, c.want)
			assertLineAfterChild(t, startText(tr, tr.ParentTree), childName, stage)
		})
	}
}

// 「全体計画待ち」は SubagentStart では起きない。局面の行は subagent に渡る文脈にだけ入れ、ユーザ向けの systemMessage には足さない。
func TestTheStageLineIsWrittenForTheAgentOnly(t *testing.T) {
	t.Parallel()
	tr := phasesTree(t)
	openFamily(tr, samples.Plan("research", "design"), launchChild{childName, 1, researchScope})
	child := tr.RunChild(childName)
	ticketkit.Lacks(t, startText(tr, tr.ParentTree), "全体計画待ち")
	ticketkit.Lacks(t, startText(tr, child), "全体計画待ち")
	r := tr.Hook("SubagentStart", "", tr.ParentTree, nil, ticketkit.HookOpts{AgentID: "sub-1"})
	var out struct {
		Hook struct {
			AdditionalContext string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
		SystemMessage string `json:"systemMessage"`
	}
	jsonOf(t, r.Stdout, &out)
	ticketkit.Has(t, out.Hook.AdditionalContext, stageLine)
	ticketkit.Lacks(t, out.SystemMessage, "の局面")
}
