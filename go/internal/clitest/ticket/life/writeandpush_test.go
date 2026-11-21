package life

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// write-and-push の実行ファイルの側。write-and-push は、親子のチケットで状態を書く操作を「ロック → 取り込み → 書く → コミット → push」の
// 1 操作にする形。実行ファイルは対象かの見分けと書いたパスの一覧だけを持ち、git の操作は sh が持つ。sh の側（ロック・取り込み・コミット・
// push・戻し）は clitest/sh/wap が見る。`ccnavi write-and-push family`（対象かの見分け）と sort（置き場の変更の見分け）は本体の
// tickets/approval/wap の単体テストが見る。
//  1. --record-tree: 書いたパスの一覧の基点を親のワークツリーにし、置き場の外に書けば error（一覧は書く。例外は無い）
//  2. ユーザの判断の入口の sh（ccnavi-user-close-early.sh）はエージェントから止める

const approvedDir = ".ccnavi/approved"

func recordPlace(tr *ticketkit.Tree) string {
	return filepath.Join(tr.Root, "logs", "state", "write-and-push", "self")
}

func readList(path string) []string {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var out []string
	for _, l := range strings.Split(string(raw), "\n") {
		if l != "" {
			out = append(out, l)
		}
	}
	return out
}

// runRecorded は --record-writes・--record-tree を付けて起こし、結果と書いたパスの一覧を返す。
func runRecorded(tr *ticketkit.Tree, target string, args ...string) (run.Result, []string) {
	tr.T.Helper()
	r := tr.Ccnavi(append([]string{"--record-writes", target, "--record-tree", tr.ParentTree}, args...)...)
	return r, readList(target)
}

func TestTheRecordedListIsRelativeToTheParentTree(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	tr.CommitParent("settle")
	tr.Worktree("i0001-01-02", "i0001")
	r, listed := runRecorded(tr, filepath.Join(recordPlace(tr), "i0001.t.writes"), "ticket", "cancel", childName, "--reason", "r")
	ticketkit.OK(t, r)
	for _, want := range []string{approvedDir + "/done/i0001-01-01.md", approvedDir + "/doing/i0001-01-01.md"} {
		found := false
		for _, l := range listed {
			found = found || l == want
		}
		if !found {
			t.Errorf("%s が一覧に無い: %v", want, listed)
		}
	}
	for _, p := range listed {
		if filepath.IsAbs(p) {
			t.Errorf("絶対パス: %s", p)
		}
	}
}

// 承認は提案（wip/proposals/todo/）を消す。write-and-push の置き場の外なので error。
func TestWritingOutsideThePlacesIsAnErrorAndTheListIsKept(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	tr.CommitParent("settle")
	tr.Propose("i0001-01-02", samples.ChildText("i0001-01-02", "i0001", 1, []string{"wip/research/*"}), "")
	tr.CommitParent("approval-request")
	var preview struct{ Digest string }
	jsonOf(t, tr.Ccnavi("--agree", "--preview", "--json").Stdout, &preview)
	r, listed := runRecorded(tr, filepath.Join(recordPlace(tr), "i0001.t.writes"), "user", "agree", "--yes", "i0001-01-02", "--digest", preview.Digest, "--json")
	if r.Code != 1 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	ticketkit.Has(t, r.Stderr, "置き場の外に書き込みがあった")
	all := strings.Join(listed, "\n")
	ticketkit.Has(t, all, "wip/proposals/todo/i0001-01-02.md")
	ticketkit.Has(t, all, approvedDir+"/doing/i0001-01-02.md")
}

func TestRecordTreeNeedsRecordWrites(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	r := tr.Ccnavi("--record-tree", tr.ParentTree, "ticket", "start", "i0001")
	if r.Code != 1 {
		t.Errorf("code %d", r.Code)
	}
	ticketkit.Has(t, r.Stderr, "--record-writes と一緒に")
}

func TestTheHumanEntriesAreDeniedToTheAgent(t *testing.T) {
	t.Parallel()
	tr := authority(t)
	decide := func(command string) string {
		raw, err := json.Marshal(map[string]any{"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": tr.ParentTree, "session_id": "s1", "tool_input": map[string]any{"command": command}})
		if err != nil {
			t.Fatal(err)
		}
		return ticketkit.Decision(t, tr.Call(ticketkit.Call{Stdin: string(raw)}, "--guard-ticket-approval", "enable", "--mode", "enable"))
	}
	for _, command := range []string{
		"sh .ccnavi/scripts/ccnavi-user-close-early.sh --reason r",
		// シェルに選択肢を付けた形も同じ。
		"sh -x .ccnavi/scripts/ccnavi-user-push-approved.sh",
		"ccnavi worktree retire --abandon i0001",
	} {
		if d := decide(command); d != "deny" {
			t.Errorf("%s = %q（deny のはず）", command, d)
		}
	}
	// 依頼・確認・Draft 外しはエージェントの経路のまま。
	if d := decide("sh .ccnavi/scripts/ccnavi-review.sh confirm"); d == "deny" {
		t.Error("確認の経路まで止めた")
	}
}

// 実際の実行ファイルで、--record-tree 付きの依頼・行き先・Draft 外しが置き場だけを書く。state の置き場は --state で動かしている。
func TestRequestDecideAndReadyWriteOnlyThePlaces(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
	target := filepath.Join(tr.Root, "logs", "state", "write-and-push", "self", "r.writes")
	type recording struct {
		args   []string
		result run.Result
		listed []string
	}
	var recorded []recording
	record := func(args ...string) run.Result {
		r := tr.Ccnavi(append([]string{"--record-writes", target, "--record-tree", tr.ParentTree}, args...)...)
		recorded = append(recorded, recording{args, r, readList(target)})
		return r
	}
	tr.PhaseFamily(samples.Plan("design"))
	tr.Propose(childName, samples.ChildText(childName, "i0001", 1, []string{"wip/design/*"}), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	f := remoteDraft(tr)
	tr.RunChild(childName, ticketkit.File{Path: "wip/design/plan.md", Text: "d\n"})
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", childName))
	tr.CommitParent("close 01")
	tr.Merge(childName)
	tr.Parent("push", "--quiet", "origin", "i0001")
	// 依頼（prepare は書かないので記録を付けず、requested だけ記録する）。
	body := ticketkit.Write(t, filepath.Join(tr.Root, "body.md"), "見てほしい\n")
	ticketkit.OK(t, tr.Ccnavi(append(tr.PhaseArgs("1"), "--body-file", body, "review", "prepare")...))
	posted := ticketkit.Write(t, filepath.Join(tr.Root, "posted.json"), `{"host": "fixture", "mr": {"number": 7, "url": "u/7", "draft": true}, "url": "u/7#1", "created_at": "2026-01-01T00:00:00Z"}`)
	ticketkit.OK(t, record(append(tr.PhaseArgs("1"), "review", "requested", "--result", posted)...))
	var shown struct{ Digest string }
	jsonOf(t, ticketkit.OK(t, tr.Ccnavi("--cwd", tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved", "--preview", "--json", "--result", f.Path)).Stdout, &shown)
	ticketkit.OK(t, record("--cwd", tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved", "--yes", "{}", "--digest", shown.Digest, "--json", "--result", f.Path))
	tr.StartParent()
	tr.Propose("i0001", samples.ParentText("i0001", samples.Plan("design"), samples.ParentOpts{Feedback: []samples.Step{}, Copy: tr.Phases}), "")
	ticketkit.OK(t, tr.Approve())
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", "i0001"))
	tr.CommitParent("状態の移動")
	tr.Parent("rm", "-r", "-q", "wip")
	tr.Parent("commit", "--quiet", "-m", "chore: wip を片付ける")
	tr.Parent("push", "--quiet", "origin", "i0001")
	ticketkit.OK(t, record("--cwd", tr.ParentTree, "review", "ready", "--result", f.Path))
	if mr := ticketkit.ReadJSON(t, filepath.Join(tr.Root, "logs", "archive", "self", "phases", "i0001", "ready.json"))["mr"]; mr == nil {
		t.Error("ready.json に mr が無い")
	}
	if len(recorded) != 3 {
		t.Fatalf("記録した呼び出し = %d 件", len(recorded))
	}
	for _, rec := range recorded {
		ticketkit.Lacks(t, rec.result.Stderr, "置き場の外")
		for _, p := range rec.listed {
			if !strings.HasPrefix(p, approvedDir+"/") && !strings.HasPrefix(p, "wip/proposals/review/") {
				t.Errorf("%v が置き場の外を書いた: %s", rec.args, p)
			}
		}
	}
}

// 対話の decide の前半（--choose-out）: 選んで書くだけ。
func TestChooseWritesOnlyTheChoices(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
	buildRequested(tr)
	f := tr.Fixture()
	tr.CommitParent("requested")
	f.SetThreads(ticketkit.Thread{ID: "t0", URL: "u/7#t0", Body: "x"})
	f.SetReviews()
	out := filepath.Join(tr.State, "choose.json")
	reviewed := func(stdin string, extra ...string) run.Result {
		args := append([]string{"--cwd", tr.ParentTree, "user", "reviewed", "1", "--accept-unresolved"}, extra...)
		return tr.Call(ticketkit.Call{Stdin: stdin}, args...)
	}
	ticketkit.OK(t, reviewed("k\n", "--choose-out", out, "--result", f.Path))
	answer := map[string]string{}
	for _, line := range ticketkit.Lines(ticketkit.Read(t, out)) {
		k, v, _ := strings.Cut(line, " ")
		answer[k] = v
	}
	var choices map[string]string
	jsonOf(t, answer["choices"], &choices)
	if len(choices) != 1 || choices["u/7#t0"] != "keep" {
		t.Errorf("choices = %v", choices)
	}
	if status := strings.TrimSpace(tr.Parent("status", "--porcelain")); status != "" {
		t.Errorf("ツリーが汚れた: %q", status)
	}
	outside := reviewed("k\n", "--choose-out", filepath.Join(tr.Root, "x.json"), "--result", f.Path)
	if outside.Code != 1 {
		t.Errorf("置き場の外への書き出し: code %d", outside.Code)
	}
	ticketkit.OK(t, reviewed("", "--yes", answer["choices"], "--digest", answer["digest"], "--json", "--result", f.Path))
}

// write-and-push の対象の親子のチケット（取り込み済みで origin がある）では、write-and-push を通らない状態の操作を断る。
func bypassTree(t *testing.T) *ticketkit.Tree {
	t.Helper()
	tr := authorityWith(t, ticketkit.Options{Phases: samples.Phases, NoMark: true})
	tr.Git(tr.Root, "remote", "add", "origin", filepath.Join(tr.Root, "nowhere.git"))
	return tr
}

func TestStateOperationsWithoutRecordTreeAreRefused(t *testing.T) {
	t.Parallel()
	tr := bypassTree(t)
	ticketkit.Has(t, tr.Ccnavi("write-and-push", "family", "i0001").Stdout, "target yes")
	for _, args := range [][]string{{"finish", childName}, {"start", "i0001"}, {"approval-request", "i0001"}} {
		refused := tr.Ccnavi(append([]string{"ticket"}, args...)...)
		if refused.Code != 1 {
			t.Errorf("%v: code %d\n%s%s", args, refused.Code, refused.Stdout, refused.Stderr)
		}
		ticketkit.Has(t, refused.Stderr, "write-and-push の対象")
		ticketkit.Has(t, refused.Stderr, "ccnavi-ticket.sh")
	}
	fixture := ticketkit.Write(t, filepath.Join(tr.Root, "r.json"), `{"host": "x"}`)
	for _, args := range [][]string{
		{"review", "confirm", "--phase", "1", "--result", fixture},
		{"review", "ready", "--result", fixture},
		{"user", "reviewed", "1", "--accept-unresolved", "--result", fixture},
	} {
		r := tr.Ccnavi(append([]string{"--cwd", tr.ParentTree}, args...)...)
		if r.Code != 1 {
			t.Errorf("%v: code %d", args, r.Code)
		}
		ticketkit.Has(t, r.Stderr, "write-and-push の対象")
	}
}

func TestRecordRiskAndHumanEntriesAreNotRefusedHere(t *testing.T) {
	t.Parallel()
	tr := bypassTree(t)
	risk := tr.Ccnavi("ticket", "record-risk", childName, "x", "yes", "--reason", "r")
	ticketkit.Lacks(t, risk.Stderr, "write-and-push の対象")
}
