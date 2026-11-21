package flow

// 子チケットのフローの受入テスト。元: tests/ticket/test_flow.py。
//
//  1. エージェントの書き込みは、どのツリーの置き場でも組み込みの保護が止める。ユーザが保存したフローを
//     実行後チェックが範囲外の変更として報告しない（H1）
//  2. 着手中の子のフローへの書き込みを止める。解いたパスと解く前のパスの両方で。着手の前と、終わった後は止めない
//  3. SubagentStart がフローのファイルを名指しし、手順と、askUserQuestion / subAgent のノードでの動き方を渡す。
//     フローが不正でも残りの文は渡す
//  4. --explain --json の子に flow の欄が出る（ボードが読む）。閉じた子でフローが無ければ出さない
//  5. 入れ子のサブエージェントが差し戻しを無視して終わったら、systemMessage にも載せる
//  6. エージェントの下書き（wip/proposals/flows/<子>.yml）は書ける。効力は無く、SubagentStart も着手のハッシュも
//     読まない。置き場と有無は flow.draft に出る
//
// 置き場の決め方・YAML のフローを手順に並べること・不正な・大きい・リンクのフローの扱い・文の量の上限・ccnavi の接頭辞の真似は
// go の flow のテストが見る。
import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

// ---- 1. エージェントはどのツリーの置き場にも書けない。ユーザの保存は報告しない。

// 組み込みの保護（builtin:guard-project-home）が、承認済みの領域の flows/ をどのツリーでも止める。
func TestAgentWritesToTheFlowAreDeniedInEveryTree(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	childTree := tr.Worktree(flowChild, "i0001")
	project := filepath.Join(tr.Root, "projects", "p")
	Write(t, filepath.Join(project, "keep"), "")
	tr.Git(project, "init", "--quiet", "-b", "main")
	for _, where := range []string{tr.Root, tr.ParentTree, childTree, project} {
		for _, tool := range []string{"Write", "Edit", "NotebookEdit"} {
			out := hookOut(t, tr.writeTo(tr.flowIn(where, flowChild), writeOpts{Tool: tool, Guard: "enable"}))
			if js(out, "permissionDecision") != "deny" {
				t.Errorf("%s %s: deny でない: %v", where, tool, out)
			}
			Has(t, js(out, "permissionDecisionReason"), "builtin:guard-project-home")
			Has(t, js(out, "permissionDecisionReason"), "子のフロー")
		}
	}
	// 組み込みを切っても、ルールの 1 本（このハーネスでは */.ccnavi/*）が止める。
	if got := js(hookOut(t, tr.writeTo(tr.flowPath(), writeOpts{AgentID: "sub-1"})), "permissionDecision"); got != "deny" {
		t.Errorf("組み込みを切ったときの判定 = %q", got)
	}
	// シェルから書く形は builtin:guard-setting-files が止める。
	rel := ".ccnavi/approved/flows/i0001-01-01.yml"
	for _, command := range []string{"echo x > " + rel, "cp /tmp/x " + rel, "tee " + rel + " < /dev/null"} {
		out := hookOut(t, tr.bashCheck(command, "enable"))
		if js(out, "permissionDecision") != "deny" {
			t.Errorf("%q: deny でない: %v", command, out)
		}
		Has(t, js(out, "permissionDecisionReason"), "builtin:guard-setting-files")
	}
}

// ユーザがボードで保存したフロー（hook を通らない書き込み）を、次のエージェントの呼び出しの実行後チェックが
// 範囲外の変更として報告しない（H1）。未コミットでも、コミットしても。
func TestAFlowSavedByAPersonIsNotBlamedOnTheAgent(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	post := func(name string) string {
		path := Write(t, filepath.Join(tr.ParentTree, "wip", name+".md"), name+"\n")
		return Out(tr.Hook("PostToolUse", "Write", tr.ParentTree, map[string]any{"file_path": path, "content": name}))
	}
	Lacks(t, post("a"), "POST_TICKET_SCOPE")
	Write(t, tr.flowIn(tr.ParentTree, "i0001-01-02"), workflowYAML)
	Write(t, tr.flowPath(), "nodes: []\n")
	said := post("b")
	for _, w := range []string{"POST_TICKET_SCOPE", "POST_VIOLATION", "git clean"} {
		Lacks(t, said, w)
	}
	Lacks(t, strings.ReplaceAll(said, `\`, "/"), "flows/")
	tr.CommitParent("person saves the flow")
	said = post("c")
	for _, w := range []string{"POST_TICKET_SCOPE", "POST_VIOLATION"} {
		Lacks(t, said, w)
	}
	Lacks(t, strings.ReplaceAll(said, `\`, "/"), "flows/")
	// 実行後チェックは有効。範囲の外の変更（以前の置き場 references/ も）はそのまま報告する。
	Write(t, filepath.Join(tr.ParentTree, "references", flowChild, "flow.json"), "{}")
	Has(t, post("d"), "POST_TICKET_SCOPE")
}

func TestFlowsAreNotReadAsTickets(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	ids := tr.boardTicketIDs()
	if len(ids) != 2 || ids[0] == ids[1] {
		t.Errorf("チケット = %v（i0001 と %s のはず）", ids, flowChild)
	}
	for _, l := range Lines(tr.Ccnavi("--lint").Stdout) {
		if strings.HasPrefix(l, "error:") && strings.Contains(l, "flows") {
			t.Errorf("flows を読もうとした: %s", l)
		}
	}
}

// ---- 2〜4. ロックと SubagentStart

func TestTheFlowCanBeEditedBeforeTheChildStarts(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	tr.assertNotLocked(tr.writeTo(tr.flowPath(), writeOpts{}))
	shown := tr.boardFlow(flowChild)
	if js(shown, "rel") != ".ccnavi/approved/flows/"+flowChild+".yml" || !jb(shown, "exists") || jb(shown, "locked") || jb(shown, "linked") {
		t.Errorf("flow = %v", shown)
	}
	if !samePath(t, js(shown, "path"), tr.flowPath()) || !samePath(t, js(shown, "tree"), tr.ParentTree) {
		t.Errorf("flow = %v", shown)
	}
	if _, ok := shown["declared"]; ok {
		t.Error("declared がある")
	}
	if tr.boardFlow("i0001") != nil {
		t.Error("親に flow 欄がある")
	}
}

func TestTheFlowIsLockedWhileTheChildIsInProgress(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	childTree := tr.RunChild(flowChild)
	tr.assertLocked(tr.writeTo(tr.flowPath(), writeOpts{}))
	// どのツリーの置き場も同じ手順書として止める。
	tr.assertLocked(tr.writeTo(tr.flowIn(childTree, flowChild), writeOpts{}))
	tr.assertLocked(tr.writeTo(tr.flowIn(tr.Root, flowChild), writeOpts{}))
	// 誰が書いても同じ（サブエージェントでも）。大文字小文字も問わない。
	upper := filepath.Join(tr.ParentTree, ".ccnavi", "Approved", "FLOWS", "I0001-01-01.YML")
	tr.assertLocked(tr.writeTo(upper, writeOpts{AgentID: "sub-1"}))
	// 相対パス・.. を挟んだパス・NotebookEdit。
	tr.assertLocked(tr.writeTo(filepath.Join(".ccnavi", "approved", "flows", flowChild+".yml"), writeOpts{Cwd: tr.ParentTree}))
	dotted := filepath.Join(tr.ParentTree, ".ccnavi", "approved") + string(filepath.Separator) + "." + string(filepath.Separator) + "x" + string(filepath.Separator) + ".." + string(filepath.Separator) + "flows"
	tr.assertLocked(tr.writeTo(dotted+string(filepath.Separator)+flowChild+".yml", writeOpts{}))
	tr.assertLocked(tr.writeTo(tr.flowPath(), writeOpts{Tool: "NotebookEdit"}))
	if !jb(tr.boardFlow(flowChild), "locked") {
		t.Error("ボードが locked と言わない")
	}
	// 別の子の置き場は止めない。
	tr.assertNotLocked(tr.writeTo(tr.flowIn(tr.ParentTree, "i0001-01-09"), writeOpts{}))
}

// 置き場を指すリンク越しのパスも、リンクに差し替えたフローのパスも止める（H2）。
func TestLinksDoNotGetAroundTheLock(t *testing.T) {
	t.Parallel()
	if !base.CanSymlink() {
		t.Skip("シンボリックリンクを作れない")
	}
	tr := flowTree(t)
	real := Write(t, filepath.Join(tr.ParentTree, "wip", "flow-real.yml"), workflowYAML)
	removeFile(t, tr.flowPath())
	if err := os.Symlink(real, tr.flowPath()); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(tr.ParentTree, "alias")
	if err := os.Symlink(filepath.Dir(tr.flowPath()), alias); err != nil {
		t.Fatal(err)
	}
	tr.CommitParent("links")
	childTree := tr.RunChild(flowChild)
	tr.assertLocked(tr.writeTo(filepath.Join(alias, flowChild+".yml"), writeOpts{}))
	tr.assertLocked(tr.writeTo(tr.flowPath(), writeOpts{}))
	// リンクのフローは読まずに、そう言う。
	text := Reason(t, tr.subagentStart(childTree))
	Has(t, text, "シンボリックリンク")
	Lacks(t, text, "[askUserQuestion]")
	if !jb(tr.boardFlow(flowChild), "linked") {
		t.Error("ボードが linked と言わない")
	}
}

// フローのロックはゲートで、dry-run でも止める。
func TestDryRunStillHoldsTheLock(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	tr.RunChild(flowChild)
	r := tr.Hook("PreToolUse", "Write", tr.ParentTree, map[string]any{"file_path": tr.flowPath()}, HookOpts{Mode: "dry-run"})
	Has(t, r.Stdout, codeLocked)
	Has(t, r.Stdout, `"deny"`)
}

func TestTheLockIsReleasedWhenTheChildFinishes(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	childTree := tr.RunChild(flowChild, File{"wip/research/summary.md", "まとめ\n"})
	if !Exists(childTree) {
		t.Fatal("子のワークツリーが無い")
	}
	OK(t, tr.CloseChild(flowChild))
	tr.assertNotLocked(tr.writeTo(tr.flowPath(), writeOpts{}))
	// finish でレビュー待ちに入った子は、ボードでは閲覧だけ（閉じるまで編集させない）。
	if !jb(tr.boardFlow(flowChild), "locked") {
		t.Error("レビュー待ちの子の flow が locked でない")
	}
}

// 取り消して閉じた子でも、フローが在れば欄を出す（見返せる）。無ければ出さない。
func TestAClosedChildShowsTheFlowOnlyWhenItExists(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	tr.RunChild(flowChild)
	OK(t, tr.Ccnavi("ticket", "cancel", flowChild, "--reason", "やめる"))
	shown := tr.boardFlow(flowChild)
	if !jb(shown, "exists") || jb(shown, "locked") {
		t.Errorf("flow = %v", shown)
	}
	removeFile(t, tr.flowPath())
	if tr.boardFlow(flowChild) != nil {
		t.Error("フローが無いのに欄が出る")
	}
}

func TestSubagentStartNamesTheFlowAndHowToHandleNodes(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	childTree := tr.RunChild(flowChild)
	r := tr.subagentStart(childTree)
	text := js(hookOut(t, r), "additionalContext")
	if !strings.Contains(strings.ToLower(text), strings.ToLower(tr.flowPath())) {
		t.Errorf("フローのパスが無い: %s", text)
	}
	for _, w := range []string{
		"作業の前に読み、その順に進めてください", fenceOpen, "3. [askUserQuestion] 方針", "[fancyNewNode] 未来の種類",
		// askUserQuestion: 止まってメインに返す。
		"問いと選択肢を添えてメインに返して",
		// subAgent: 入れ子で起こし、description に担当の子の識別子を 1 つだけ書く（ccnavi がその子のワークツリーで案内する）。
		// 担当以外の子は書かず、isolation は使わない。Agent が無ければメインに返す。
		"description（作業名の欄）に担当の子の識別子 " + flowChild + " を 1 つだけ書き", "ccnavi が範囲とフローの案内をプロンプトに書き足す",
		"担当以外の子の識別子は書かないでください", "isolation は使いません", childTree, "wip/research/*", "Agent ツールが無ければ", "起動してほしいエージェントの種類・プロンプト",
	} {
		Has(t, text, w)
	}
	Lacks(t, text, "ロック")
}

func TestSubagentStartWithoutAFlowSaysNothingAboutIt(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	removeFile(t, tr.flowPath())
	tr.CommitParent("no flow")
	childTree := tr.RunChild(flowChild)
	text := Reason(t, tr.subagentStart(childTree))
	Has(t, text, flowChild)
	Lacks(t, text, "フロー")
}

func TestAnUnreadableFlowIsNamedNotRaised(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	Write(t, tr.flowPath(), "nodes:\n  - id: s\n    type: [start\n")
	tr.CommitParent("broken flow")
	childTree := tr.RunChild(flowChild)
	r := tr.subagentStart(childTree)
	wantCode(t, r, 0)
	Has(t, Reason(t, r), "フローを読めない")
}

// 型の崩れたフローでも SubagentStart は失敗せず、子の一覧と範囲は渡る（H3）。形の誤りは --lint --flow と同じ理由の 1 行で言い、
// 手順は並べない。
func TestAMalformedFlowKeepsTheRestOfSubagentStart(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	Write(t, tr.flowPath(), "nodes:\n  - {id: s, type: start}\nconnections: 5\n")
	tr.CommitParent("bad flow")
	childTree := tr.RunChild(flowChild)
	r := tr.subagentStart(childTree)
	wantCode(t, r, 0)
	text := Reason(t, r)
	Has(t, text, "allow: wip/research/*")
	Has(t, text, "フローを読めない: `connections` がリストではない")
	Lacks(t, text, "1. [start]")
}

// ---- 5. 入れ子のサブエージェントの差し戻し（G4）

// bouncedChild は研究の子 i0001-01-01 を着手し、範囲外の変更をコミットして差し戻させた木と子のツリー。
func bouncedChild(t *testing.T, agent string) (*Tree, string) {
	t.Helper()
	tr := cachedTree(t, "flow-nested-bounce", func(tr *Tree) {
		tr.Propose("i0001", parentOf("i0001", "research"), "")
		tr.Propose(flowChild, child(flowChild, 1, "wip/research/*"), "")
		tr.CommitParent("tickets")
		OK(tr.T, tr.Approve())
	})
	childTree := tr.RunChild(flowChild)
	Write(t, filepath.Join(childTree, "src", "stray.py"), "x\n")
	tr.Git(childTree, "add", "-A")
	tr.Git(childTree, "commit", "--quiet", "-m", "stray")
	wantCode(t, tr.Hook("SubagentStop", "", childTree, nil, HookOpts{AgentID: agent}), 2)
	return tr, childTree
}

func launched(tr *Tree, nested bool, id, description string) map[string]any {
	opts := HookOpts{Extra: map[string]any{"tool_response": map[string]any{"agentId": id}}}
	if nested {
		opts.AgentID = "child-1"
	}
	r := tr.Hook("PostToolUse", "Agent", tr.ParentTree, map[string]any{"description": description}, opts)
	var out map[string]any
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		tr.T.Fatalf("%v: %s", err, r.Stdout)
	}
	return out
}

func TestANestedIgnoredBounceIsAlsoASystemMessage(t *testing.T) {
	t.Parallel()
	tr, _ := bouncedChild(t, "grand-1")
	out := launched(tr, true, "grand-1", "孫")
	Has(t, js(out, "hookSpecificOutput", "additionalContext"), "差し戻されたまま")
	Has(t, js(out, "systemMessage"), "差し戻されたまま")
}

func TestATopLevelLaunchKeepsTheMessageForTheModelOnly(t *testing.T) {
	t.Parallel()
	tr, _ := bouncedChild(t, "sub-9")
	out := launched(tr, false, "sub-9", "子")
	Has(t, js(out, "hookSpecificOutput", "additionalContext"), "差し戻されたまま")
	if _, ok := out["systemMessage"]; ok {
		t.Error("systemMessage がある")
	}
}

// ---- 6. エージェントの下書き。書けるが効力は無い。判定と組み込みの保護は今までどおり。

func TestTheAgentMayWriteTheDraftButNotTheFlow(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	for _, tool := range []string{"Write", "Edit"} {
		for _, agent := range []string{"", "sub-1"} {
			r := tr.writeTo(tr.draftIn(tr.ParentTree, flowChild), writeOpts{Tool: tool, AgentID: agent, Guard: "enable"})
			wantCode(t, r, 0)
			if decision(t, r) == "deny" {
				t.Errorf("%s %q: 下書きを止めた: %s", tool, agent, r.Stdout)
			}
			Lacks(t, r.Stdout, "builtin:guard")
		}
	}
	// シェルから書いても止めない。
	if r := tr.bashCheck("cp /tmp/x wip/proposals/flows/"+flowChild+".yml", "enable"); decision(t, r) == "deny" {
		t.Errorf("シェルの下書きを止めた: %s", r.Stdout)
	}
	// 効力のあるフローは今までどおり止まる。
	r := tr.writeTo(tr.flowPath(), writeOpts{Guard: "enable"})
	if decision(t, r) != "deny" {
		t.Errorf("フローを止めない: %s", r.Stdout)
	}
	Has(t, Reason(t, r), "builtin:guard-project-home")
}

// 着手中のロックが止めるのはフローだけ。下書きは止めない（取り込みはボードが止める）。
func TestTheDraftCanBeWrittenWhileTheChildIsInProgress(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	tr.RunChild(flowChild)
	tr.assertLocked(tr.writeTo(tr.flowPath(), writeOpts{}))
	r := tr.writeTo(tr.draftIn(tr.ParentTree, flowChild), writeOpts{})
	tr.assertNotLocked(r)
	if decision(t, r) == "deny" {
		t.Errorf("下書きを止めた: %s", r.Stdout)
	}
}

// 下書きは範囲の外の変更として報告しない（提案の置き場は丸ごとチケットの範囲の外）。
func TestTheDraftIsNotPartOfThePostCheck(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	Write(t, tr.draftIn(tr.ParentTree, flowChild), workflowYAML)
	ok := Write(t, filepath.Join(tr.ParentTree, "wip", "a.md"), "a\n")
	said := Out(tr.Hook("PostToolUse", "Write", tr.ParentTree, map[string]any{"file_path": ok, "content": "a"}))
	Lacks(t, said, "POST_TICKET_SCOPE")
	Lacks(t, said, "POST_VIOLATION")
}

func TestSubagentStartDoesNotReadTheDraft(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	removeFile(t, tr.flowPath())
	Write(t, tr.draftIn(tr.ParentTree, flowChild), workflowYAML)
	tr.CommitParent("draft only")
	childTree := tr.RunChild(flowChild)
	Write(t, tr.draftIn(childTree, flowChild), workflowYAML)
	text := Reason(t, tr.subagentStart(childTree))
	Has(t, text, flowChild)
	Lacks(t, text, "フロー")
	Lacks(t, text, "方針")
	Lacks(t, strings.ReplaceAll(text, `\`, "/"), "proposals/flows")
}

// 着手のハッシュは下書きを読まない。着手のあとに下書きを書いても知らせない。
func TestTheStartDigestDoesNotReadTheDraft(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	Write(t, tr.draftIn(tr.ParentTree, flowChild), "nodes: []\n")
	tr.CommitParent("draft")
	childTree := tr.RunChild(flowChild)
	data := ReadJSON(t, filepath.Join(tr.Approved, "phases", "i0001", flowChild+".flow.json"))
	if js(data, "fingerprint") != fingerprint(t, tr.flowPath()) || js(data, "path") != ".ccnavi/approved/flows/"+flowChild+".yml" {
		t.Errorf("record = %v", data)
	}
	Write(t, tr.draftIn(tr.ParentTree, flowChild), workflowYAML)
	for _, out := range []string{tr.subagentStart(childTree).Stdout, tr.subagentStop(childTree).Stdout} {
		Lacks(t, out, codeChanged)
		Lacks(t, out, "systemMessage")
	}
}

func TestTheBoardGetsTheDraftPlaceAndWhetherItExists(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	shown := jm(tr.boardFlow(flowChild), "draft")
	if js(shown, "rel") != "wip/proposals/flows/"+flowChild+".yml" || !samePath(t, js(shown, "path"), tr.draftIn(tr.ParentTree, flowChild)) || jb(shown, "exists") || jb(shown, "linked") {
		t.Errorf("draft = %v", shown)
	}
	Write(t, tr.draftIn(tr.ParentTree, flowChild), workflowYAML)
	shown = jm(tr.boardFlow(flowChild), "draft")
	if !jb(shown, "exists") || jb(shown, "linked") {
		t.Errorf("draft = %v", shown)
	}
	// 下書きは走査されず、チケットとして読まれない。
	if ids := tr.boardTicketIDs(); len(ids) != 2 {
		t.Errorf("チケット = %v", ids)
	}
	for _, l := range Lines(tr.Ccnavi("--lint").Stdout) {
		if strings.HasPrefix(l, "error:") && strings.Contains(strings.ReplaceAll(l, `\`, "/"), "proposals/flows") {
			t.Errorf("下書きを読もうとした: %s", l)
		}
	}
}

// 承認のダイジェスト（--agree --preview --json の digest）に下書きは入らない。
func TestTheDraftIsNotInTheApprovalDigest(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	other := "i0001-01-02"
	tr.Propose(other, child(other, 1, "wip/research/*"), "")
	tr.CommitParent("another child")
	digest := func() string {
		return js(ParseJSON(t, tr.Ccnavi("--agree", "--preview", "--json").Stdout), "digest")
	}
	before := digest()
	if before == "" {
		t.Fatal("digest が空")
	}
	Write(t, tr.draftIn(tr.ParentTree, other), workflowYAML)
	Write(t, tr.draftIn(tr.ParentTree, flowChild), workflowYAML)
	if after := digest(); after != before {
		t.Errorf("下書きでダイジェストが変わった: %s → %s", before, after)
	}
	// 承認の前の子にも、提案のツリーの置き場で欄が出る。
	shown := jm(tr.boardFlow(other), "draft")
	if !jb(shown, "exists") || !samePath(t, js(shown, "path"), tr.draftIn(tr.ParentTree, other)) {
		t.Errorf("draft = %v", shown)
	}
}

func TestALinkedDraftIsNamed(t *testing.T) {
	t.Parallel()
	if !base.CanSymlink() {
		t.Skip("シンボリックリンクを作れない")
	}
	tr := flowTree(t)
	real := Write(t, filepath.Join(tr.ParentTree, "wip", "draft-real.yml"), workflowYAML)
	draft := tr.draftIn(tr.ParentTree, flowChild)
	if err := os.MkdirAll(filepath.Dir(draft), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(real, draft); err != nil {
		t.Fatal(err)
	}
	shown := jm(tr.boardFlow(flowChild), "draft")
	if !jb(shown, "exists") || !jb(shown, "linked") {
		t.Errorf("draft = %v", shown)
	}
}
