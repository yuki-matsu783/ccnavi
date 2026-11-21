package core

import (
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/ticketkit"
)

// サブエージェントの起動にいつも付く「スキル候補」の 1 行。
const candidateNote = "[ccnavi] 作業中に手順の見落としやすい点やスキルの誤りに気づいたら、最後の報告に" +
	"「スキル候補」の節を追加して書いてください（対象のスキル・何を直すか・根拠）。" +
	"スキルのファイルは自分では書かない。"

func stop(tr *ticketkit.Tree, cwd, agent, session string) (code int, stdout, stderr string) {
	r := tr.Hook("SubagentStop", "", cwd, nil, ticketkit.HookOpts{AgentID: agent, Session: session})
	return r.Code, r.Stdout, r.Stderr
}

// 範囲外のファイルを範囲の中へ改名しても、移動元が数から消えない。
//
// 回帰: `git diff --name-only` は改名を 1 行（移動先）にまとめるので、まとめさせると範囲外から範囲の中への移動が止められずに
// 通った。
func TestARenameIntoTheScopeStillShowsTheSource(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	child := tr.ChildTree("i0001-01-01")
	if err := os.MkdirAll(filepath.Join(child, "src", "a"), 0o755); err != nil {
		t.Fatal(err)
	}
	tr.Git(child, "mv", "src/keep.py", "src/a/keep.py")
	tr.Git(child, "commit", "--quiet", "-m", "範囲の中へ移す")
	code, out, errText := stop(tr, child, "sub-1", "")
	if code != 2 {
		t.Errorf("終了コード %d（2 のはず）\n%s%s", code, out, errText)
	}
	ticketkit.Has(t, errText, "POST_TICKET_SCOPE")
}

// submodule の進みが数から消えない。未コミットでも、コミット後でも。
//
// `.gitmodules` は追跡されるファイルで、そこに `ignore = all` があると git は submodule の変更を差分にも状態にも出さない。
// 設定は他の機械から届く。
func TestASubmoduleThatMovedIsCounted(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	sub := newSubmoduleSource(t, tr)
	// file:// の submodule は既定で拒まれる（CVE-2022-39253）。テストの中だけ通す。
	if _, err := ticketkit.TryGit(tr.ParentTree, "-c", "protocol.file.allow=always", "submodule", "add", "--quiet", sub, "vendor/sub"); err != nil {
		t.Skipf("submodule を作れない機械（%v）", err)
	}
	appendLine(t, filepath.Join(tr.ParentTree, ".gitmodules"), "\tignore = all\n")
	tr.CommitParent("submodule を足す")

	tr.Family(ticketkit.FamilyOpts{})
	child := tr.ChildTree("i0001-01-01")
	ticketkit.Write(t, filepath.Join(sub, "f.txt"), "b\n")
	ticketkit.Git(t, sub, "commit", "--quiet", "-am", "s2")
	after := ticketkit.Git(t, sub, "rev-parse", "HEAD")
	tr.Git(child, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--quiet", "vendor/sub")
	// まずワークツリーの側。index はそのままで、submodule の中だけを進める。
	tr.Git(filepath.Join(child, "vendor", "sub"), "checkout", "--quiet", after[:len(after)-1])
	code, out, errText := stop(tr, child, "sub-1", "")
	if code != 2 {
		t.Errorf("未コミットの進みが数えられない: %d\n%s%s", code, out, errText)
	}
	ticketkit.Has(t, errText, "POST_TICKET_SCOPE")
	// コミットに乗せた側。こちらは基準点からの差分で見る。
	tr.Git(child, "add", "--", "vendor/sub")
	tr.Git(child, "commit", "--quiet", "-m", "submodule を進める")
	code, out, errText = stop(tr, child, "sub-2", "")
	if code != 2 {
		t.Errorf("コミットした進みが数えられない: %d\n%s%s", code, out, errText)
	}
	ticketkit.Has(t, errText, "POST_TICKET_SCOPE")
}

// 範囲外の変更は、サブエージェントの終了で 1 度だけ差し戻す。2 度目は通し、理由を添える。親の cwd からは開いている子の全部を
// 見るが、担当の子が分からないので差し戻さない。
func TestSubagentStopBouncesOutOfScopeOnce(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	child := tr.ChildTree("i0001-01-01")
	ticketkit.Write(t, filepath.Join(child, "src", "b", "stray.py"), "x\n")
	tr.Git(child, "add", "-A")
	tr.Git(child, "commit", "--quiet", "-m", "stray")
	code, out, errText := stop(tr, child, "sub-1", "")
	if code != 2 {
		t.Fatalf("1 度目の終了コード %d\n%s%s", code, out, errText)
	}
	ticketkit.Has(t, errText, "POST_TICKET_SCOPE")
	ticketkit.Has(t, errText, "src/b/stray.py")
	second := tr.Hook("SubagentStop", "", child, nil, ticketkit.HookOpts{AgentID: "sub-1"})
	ticketkit.OK(t, second)
	ticketkit.Has(t, ticketkit.Reason(t, second), "POST_TICKET_SCOPE")
	ticketkit.OK(t, tr.Hook("SubagentStop", "", tr.ParentTree, nil, ticketkit.HookOpts{AgentID: "sub-2"}))
}

// agent_id が来ない payload でも、別の子で作業する相手を巻き込まない（記録の名前を `unknown` 1 つにまとめると、最初の 1 体が
// 差し戻されたあと、同じ置き場を見るサブエージェントが誰も差し戻されなくなる。agent_id を持たない記録は消せないので、
// 名前は置き場ごとに分ける）。記録はセッションで分ける（state の置き場はワークスペースに 1 つしか無い）。
func TestSubagentStopBouncesEachWorktreeAndEachSession(t *testing.T) {
	t.Parallel()
	t.Run("agent_id が無くても置き場ごとに差し戻す", func(t *testing.T) {
		t.Parallel()
		tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
		first, second := tr.ChildTree("i0001-01-01"), tr.ChildTree("i0001-01-02")
		ticketkit.Write(t, filepath.Join(first, "src", "b", "stray.py"), "x\n")
		ticketkit.Write(t, filepath.Join(second, "docs", "stray.md"), "x\n")
		if code, out, errText := stop(tr, first, "", ""); code != 2 {
			t.Fatalf("1 体目 %d\n%s%s", code, out, errText)
		}
		code, _, errText := stop(tr, second, "", "")
		if code != 2 {
			t.Errorf("別の子の相手まで通した: %d", code)
		}
		ticketkit.Has(t, errText, "POST_TICKET_SCOPE")
	})
	t.Run("別のセッションの記録で通さない", func(t *testing.T) {
		t.Parallel()
		tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
		child := tr.ChildTree("i0001-01-01")
		ticketkit.Write(t, filepath.Join(child, "src", "b", "stray.py"), "x\n")
		if code, _, _ := stop(tr, child, "sub-1", "s1"); code != 2 {
			t.Fatalf("1 体目 %d", code)
		}
		if code, _, _ := stop(tr, child, "sub-1", "s1"); code != 0 {
			t.Errorf("同じセッションの同じ相手は 1 度だけ: %d", code)
		}
		code, _, errText := stop(tr, child, "sub-1", "s2")
		if code != 2 {
			t.Errorf("別のセッションの記録で通した: %d", code)
		}
		ticketkit.Has(t, errText, "POST_TICKET_SCOPE")
	})
}

func TestSubagentStartListsOpenChildren(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	text := ticketkit.Reason(t, tr.Hook("SubagentStart", "", tr.ParentTree, nil, ticketkit.HookOpts{AgentID: "sub-1"}))
	for _, want := range []string{"i0001-01-01", "i0001-01-02", "src/a/*"} {
		ticketkit.Has(t, text, want)
	}
}

// 渡すのは cwd のワークツリーに関わる子だけ。別のセッションが main や無関係なワークツリーで調査を委譲したとき、無関係な子の範囲を
// 案内すると、調査役が自分の居場所を迷う。
func TestSubagentStartSaysNothingOutsideTheFamily(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	for _, cwd := range []string{tr.Root, tr.Worktree("research-abc", "main")} {
		r := tr.Hook("SubagentStart", "", cwd, nil, ticketkit.HookOpts{AgentID: "sub-r"})
		ticketkit.OK(t, r)
		// 子の一覧は渡らない。どの起動にも付く「スキル候補」の 1 行だけ。
		if got := ticketkit.Reason(t, r); got != candidateNote {
			t.Errorf("%s: %q", cwd, got)
		}
	}
	// 子のワークツリーからは、その子だけ。
	text := ticketkit.Reason(t, tr.Hook("SubagentStart", "", tr.ChildTree("i0001-01-01"), nil, ticketkit.HookOpts{AgentID: "sub-1"}))
	ticketkit.Has(t, text, "i0001-01-01")
	ticketkit.Lacks(t, text, "i0001-01-02")
}

// 差し戻しを無視した終了を含め、サブエージェントの終わり方（outcome）が記録に残る。問題が無ければ記録は ok で、何も言わない。
func TestOutcomeOKIsRecordedAndSaysNothing(t *testing.T) {
	t.Parallel()
	tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
	child := tr.ChildTree("i0001-01-01")
	ticketkit.OK(t, tr.Hook("SubagentStop", "", child, nil, ticketkit.HookOpts{AgentID: "sub-o"}))
	outcome := ticketkit.ReadJSON(t, filepath.Join(tr.State, "subagent-s1-sub-o.outcome"))
	if outcome["outcome"] != "ok" {
		t.Errorf("outcome = %v", outcome)
	}
	done := tr.Hook("PostToolUse", "Agent", tr.ParentTree, map[string]any{"description": "子"},
		ticketkit.HookOpts{Extra: map[string]any{"tool_response": map[string]any{"agentId": "sub-o"}}})
	ticketkit.Lacks(t, ticketkit.Reason(t, done), "SUBAGENT")
}
