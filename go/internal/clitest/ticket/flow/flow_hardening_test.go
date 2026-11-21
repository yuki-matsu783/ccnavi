package flow

// 子チケットのフローの、2 回目の敵対的なレビューで直したことの受入テスト。元: tests/ticket/test_flow_hardening.py。
//
//  1. 名前付きパイプ（FIFO）を読まない。SubagentStart が固まらない
//  2. ハードリンクのフローを読まない。ハードリンクの別名への書き込みもロックで止める
//  3. フローは正とするツリーの版だけを読む。着手のときにハッシュを記録し、着手のあとに書き換わったら
//     SubagentStart と SubagentStop が知らせる。案内は「書けない」と言わない
//  4. 親のツリーからの起動では手順を並べず、各子のフローのパスと「自分の担当だけ」を言う
//  5. 承認の前に提案のツリーへ保存したフローを、承認で承認済みチケットのツリーへ動かさない
//  6. lint は承認済みの領域のファイルを「ワークツリーにしかない」と言わない（ユーザの決定）
import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/samples"
)

const (
	changedNote = "着手後にフローが書き換わった"
	notRegular  = "ふつうのファイルではない（名前付きパイプ・デバイスなど）ので読まない"
)

// 読むのは正とするツリーの版だけ（M-2a）。案内は「書けない」と言わない（M-2c）。
func TestTheChildWorktreeCopyOfTheFlowIsNotRead(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	removeFile(t, tr.flowPath())
	tr.CommitParent("no flow in the parent tree")
	childTree := tr.RunChild(flowChild)
	// 子のワークツリーの版に、エージェントがシェルから書いた版。
	Write(t, tr.flowIn(childTree, flowChild), workflowYAML)
	text := Reason(t, tr.subagentStart(childTree))
	Has(t, text, flowChild)
	Lacks(t, text, "フロー:")
	Lacks(t, text, "[askUserQuestion]")
	shown := tr.boardFlow(flowChild)
	if jb(shown, "exists") || !samePath(t, js(shown, "tree"), tr.ParentTree) {
		t.Errorf("flow = %v", shown)
	}
}

func TestTheBriefingSaysTheFlowIsHumanOwnedNotUnwritable(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	childTree := tr.RunChild(flowChild)
	text := Reason(t, tr.subagentStart(childTree))
	Has(t, text, "合意済みの手順。編集しない")
	Lacks(t, text, "エージェントは書けない")
}

// ---- 着手のときにハッシュを記録し、着手のあとに書き換わったら知らせる（M-2b）。止めない。

func TestStartRecordsTheDigestOfTheFlow(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	tr.RunChild(flowChild)
	record := ReadJSON(t, filepath.Join(tr.Approved, "phases", "i0001", flowChild+".flow.json"))
	if !strings.HasPrefix(js(record, "fingerprint"), "sha256:") || js(record, "fingerprint") != fingerprint(t, tr.flowPath()) ||
		js(record, "path") != ".ccnavi/approved/flows/"+flowChild+".yml" || js(record, "at") == "" {
		t.Errorf("record = %v", record)
	}
}

func TestAnUnchangedFlowSaysNothing(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	childTree := tr.RunChild(flowChild)
	start, stop := tr.subagentStart(childTree), tr.subagentStop(childTree)
	Lacks(t, start.Stdout, changedNote)
	Lacks(t, start.Stdout, "systemMessage")
	if strings.TrimSpace(stop.Stdout) != "" {
		t.Errorf("stop = %q", stop.Stdout)
	}
}

// シェルから行き先を追えない形で書き換えた、を直に書いて真似る。止めずに知らせる。
func TestAFlowRewrittenAfterTheStartIsReported(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	childTree := tr.RunChild(flowChild)
	Write(t, tr.flowPath(), "nodes:\n  - {id: s, type: prompt, name: x}\n")
	for _, r := range []struct{ out, name string }{{tr.subagentStart(childTree).Stdout, "start"}, {tr.subagentStop(childTree).Stdout, "stop"}} {
		o := ParseJSON(t, r.out)
		Has(t, js(o, "hookSpecificOutput", "additionalContext"), changedNote)
		Has(t, js(o, "systemMessage"), changedNote)
		Has(t, js(o, "systemMessage"), codeChanged)
		Has(t, js(o, "systemMessage"), flowChild)
	}
}

func TestARemovedFlowIsReported(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	childTree := tr.RunChild(flowChild)
	removeFile(t, tr.flowPath())
	msg := js(ParseJSON(t, tr.subagentStart(childTree).Stdout), "systemMessage")
	Has(t, msg, changedNote)
	Has(t, msg, "いま 無い")
}

func TestAFlowThatAppearsAfterTheStartIsReported(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	removeFile(t, tr.flowPath())
	tr.CommitParent("no flow")
	childTree := tr.RunChild(flowChild)
	if got := js(ReadJSON(t, filepath.Join(tr.Approved, "phases", "i0001", flowChild+".flow.json")), "fingerprint"); got != "absent" {
		t.Errorf("fingerprint = %q", got)
	}
	Write(t, tr.flowPath(), workflowYAML)
	Has(t, js(ParseJSON(t, tr.subagentStart(childTree).Stdout), "systemMessage"), changedNote)
}

// 範囲外の変更で差し戻すときも、同じ文に知らせを載せる。
func TestTheNoticeRidesWithABounceOnStop(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	childTree := tr.RunChild(flowChild)
	Write(t, filepath.Join(childTree, "src", "stray.py"), "x\n")
	Write(t, tr.flowPath(), "{}")
	stop := tr.subagentStop(childTree)
	wantCode(t, stop, 2)
	Has(t, stop.Stderr, changedNote)
}

// 着手のあとにフローを名前付きパイプに差し替えても、SubagentStart は固まらない。
func TestAFifoSweptInAfterTheStartDoesNotHangSubagentStart(t *testing.T) {
	t.Parallel()
	if !hasFifo {
		t.Skip("mkfifo が無い")
	}
	tr := flowTree(t)
	childTree := tr.RunChild(flowChild)
	removeFile(t, tr.flowPath())
	if err := mkfifo(tr.flowPath()); err != nil {
		t.Skip("mkfifo できない:", err)
	}
	var out []byte
	fired := withFifoWatchdog(tr.flowPath(), 10*time.Second, func() { out = []byte(tr.subagentStart(childTree).Stdout) })
	if fired {
		t.Fatal("FIFO を開いて固まった")
	}
	o := ParseJSON(t, string(out))
	Has(t, js(o, "hookSpecificOutput", "additionalContext"), notRegular)
	Has(t, js(o, "systemMessage"), changedNote)
}

// ハードリンクの別名への書き込みもロックが止める。
func TestAHardLinkAliasIsLockedWhileInProgress(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	alias := filepath.Join(tr.ParentTree, "wip", "notes.yml")
	if err := os.MkdirAll(filepath.Dir(alias), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Link(tr.flowPath(), alias); err != nil {
		t.Skip("ハードリンクを作れない:", err)
	}
	// 着手の前は止めない（ロックは着手中だけ）。
	tr.assertNotLocked(tr.writeTo(alias, writeOpts{}))
	childTree := tr.RunChild(flowChild)
	tr.assertLocked(tr.writeTo(alias, writeOpts{}))
	tr.assertLocked(tr.writeTo(alias, writeOpts{Tool: "Edit"}))
	// 読まずに、そう言う。
	text := Reason(t, tr.subagentStart(childTree))
	Has(t, text, "ハードリンク")
	Lacks(t, text, fenceOpen)
	// 名前が 1 つのふつうのファイルは、ロックの走査もしない。
	tr.assertNotLocked(tr.writeTo(Write(t, filepath.Join(tr.ParentTree, "wip", "plain.yml"), "{}"), writeOpts{}))
}

// 親のツリーからの起動では、手順を並べずパスだけを言う。
func TestParentCwdListsPathsOnlyAndTheChildCwdGetsTheSteps(t *testing.T) {
	t.Parallel()
	tr := cachedTree(t, "flow-parent-briefing", func(tr *Tree) {
		tr.Propose("i0001", samples.ParentText("i0001", samples.Plan("research"), samples.ParentOpts{Allow: []string{"src/*", "wip/*"}}), "")
		for i, kid := range []string{"i0001-01-01", "i0001-01-02"} {
			tr.Propose(kid, child(kid, 1, "wip/research/r"+string(rune('1'+i))+"/*"), "")
		}
		tr.CommitParent("tickets")
		OK(tr.T, tr.Approve())
		for _, kid := range []string{"i0001-01-01", "i0001-01-02"} {
			Write(tr.T, filepath.Join(tr.Approved, "flows", kid+".yml"), workflowYAML)
		}
		tr.CommitParent("flows")
		tr.StartParent()
	})
	text := Reason(t, tr.subagentStart(tr.ParentTree))
	Lacks(t, text, fenceOpen)
	Lacks(t, text, "[askUserQuestion]")
	for _, kid := range []string{"i0001-01-01", "i0001-01-02"} {
		want := "フロー: " + filepath.Join(tr.Approved, "flows", kid+".yml")
		Has(t, strings.ToLower(text), strings.ToLower(want))
	}
	Has(t, text, "自分の担当の子チケットのフローだけを読んで従ってください。他の子のフローには従わない")
	childTree := tr.RunChild("i0001-01-01")
	own := Reason(t, tr.subagentStart(childTree))
	Has(t, own, fenceOpen)
	Has(t, own, "3. [askUserQuestion] 方針")
	Lacks(t, own, "i0001-01-02")
}

// 子の提案は親のワークツリーに書く。フローも同じツリーに保存し、承認で動かさない。
func TestTheFlowStaysInTheParentTreeAndIsReadByTheChild(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.Propose("i0001", samples.ParentText("i0001", samples.Plan("research"), samples.ParentOpts{Allow: []string{"src/*", "wip/*", "tests/*"}}), "")
	tr.CommitParent("tickets")
	OK(t, tr.Approve())
	tr.Propose(flowChild, child(flowChild, 1, "wip/research/*"), "")
	before := tr.boardFlow(flowChild)
	if !samePath(t, js(before, "tree"), tr.ParentTree) {
		t.Fatalf("flow = %v", before)
	}
	Write(t, js(before, "path"), workflowYAML)
	r := OK(t, tr.Approve())
	Lacks(t, r.Stdout, "フロー")
	after := tr.boardFlow(flowChild)
	if !samePath(t, js(after, "tree"), tr.ParentTree) || js(after, "path") != js(before, "path") || !jb(after, "exists") {
		t.Errorf("flow = %v", after)
	}
	Has(t, Read(t, js(after, "path")), "name: 調査の手順")
	childTree := tr.RunChild(flowChild)
	Has(t, Reason(t, tr.subagentStart(childTree)), "3. [askUserQuestion] 方針")
}

func TestNoFlowMeansNothingIsSaid(t *testing.T) {
	t.Parallel()
	tr := newTree(t)
	tr.Propose("i0001", samples.ParentText("i0001", samples.Plan("research"), samples.ParentOpts{Allow: []string{"src/*", "wip/*", "tests/*"}}), "")
	tr.CommitParent("tickets")
	OK(t, tr.Approve())
	tr.Propose(flowChild, child(flowChild, 1, "wip/research/*"), "")
	Lacks(t, OK(t, tr.Approve()).Stdout, "フロー")
}

// 承認済みの領域のファイルは「ワークツリーにしかない」と言わない（ユーザの決定）。ほかの置き場の物は言う。
func TestApprovedFilesInAWorktreeAreNotWarnedButOtherLayerFilesAre(t *testing.T) {
	t.Parallel()
	tr := flowTree(t)
	Write(t, filepath.Join(tr.ParentTree, ".ccnavi", "notes", "memo.txt"), "x\n")
	if !Exists(tr.flowPath()) {
		t.Fatal("フローが無い")
	}
	var lines []string
	for _, l := range Lines(tr.Ccnavi("--lint").Stdout) {
		if strings.Contains(l, "ワークツリーにしかない") {
			lines = append(lines, l)
		}
	}
	found := false
	for _, l := range lines {
		if strings.Contains(strings.ReplaceAll(l, `\`, "/"), "/approved/") {
			t.Errorf("承認済みの領域を言った: %s", l)
		}
		found = found || strings.Contains(l, "notes/memo.txt")
	}
	if !found {
		t.Errorf("notes/memo.txt を言わない: %v", lines)
	}
}
