package life

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// 直接作業の候補の記録と、それを使う Stop の促し。登録は成功した Write・Edit・NotebookEdit の書き込み先で、git を呼ばない。確かめるのは Stop で、
// cwd のツリーのあとに、記録の候補を checked の古い順に調べる。外すのは「ヒットしない」と確認できたものだけで、期限不足・git の失敗などの
// 「不明」は外さない（期限と「不明」、記録の上限は本体の directcand・integrate のテストが見る）。

func candidatePath(tr *ticketkit.Tree, session string) string {
	return filepath.Join(tr.State, "direct-"+session+".json")
}

// postWrite は成功した書き込みの PostToolUse。
func postWrite(tr *ticketkit.Tree, path string, tool, session, agent, cwd string) {
	tr.T.Helper()
	if tool == "" {
		tool = "Write"
	}
	if session == "" {
		session = "s1"
	}
	if cwd == "" {
		cwd = tr.Root
	}
	input := map[string]any{"file_path": path}
	if tool == "NotebookEdit" {
		input = map[string]any{"notebook_path": path}
	}
	tr.Hook("PostToolUse", tool, cwd, input, ticketkit.HookOpts{Session: session, AgentID: agent, Extra: map[string]any{"tool_response": map[string]any{}}})
}

func candidateEntries(tr *ticketkit.Tree, session string) []string {
	tr.T.Helper()
	path := candidatePath(tr, session)
	if !ticketkit.Exists(path) {
		return nil
	}
	var rec struct {
		Entries []struct{ Name string } `json:"entries"`
	}
	jsonOf(tr.T, ticketkit.Read(tr.T, path), &rec)
	var names []string
	for _, e := range rec.Entries {
		names = append(names, e.Name)
	}
	return names
}

func askedNamed(t *testing.T, r run.Result, name string) string {
	t.Helper()
	b := body(t, r)
	if !blocks(b) {
		t.Fatalf("引き止めていない: %s", r.Stdout)
	}
	ticketkit.Has(t, reasonOf(b), "(worktree: "+name+")")
	return reasonOf(b)
}

// rawCall は payload（hook_event_name・cwd など）をそのまま渡す。
func rawCall(tr *ticketkit.Tree, payload map[string]any, extra ...string) run.Result {
	tr.T.Helper()
	return tr.CallJSON(payload, append([]string{"--mode", "enable"}, extra...)...)
}

func TestReadsAndTheMainTreeAreNotRegistered(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	for _, tool := range []string{"Read", "Grep", "Bash"} {
		postWrite(tr, filepath.Join(path, "src", "a.py"), tool, "", "", "")
	}
	postWrite(tr, filepath.Join(tr.Root, "src", "main.py"), "", "", "", "")
	if got := candidateEntries(tr, "s1"); len(got) != 0 {
		t.Fatalf("登録した: %v", got)
	}
	postWrite(tr, filepath.Join(path, "src", "a.py"), "Edit", "", "", "")
	if got := candidateEntries(tr, "s1"); len(got) != 1 || got[0] != "scratch" {
		t.Errorf("登録 = %v", got)
	}
}

func TestARelativePathIsResolvedFromTheCwd(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	postWrite(tr, "src/x.py", "", "", "", path)
	if got := candidateEntries(tr, "s1"); len(got) != 1 || got[0] != "scratch" {
		t.Errorf("登録 = %v", got)
	}
}

func TestARecentTailEntryIsNotRewritten(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	postWrite(tr, filepath.Join(path, "a.py"), "", "", "", "")
	file := candidatePath(tr, "s1")
	first, err := os.Stat(file)
	if err != nil {
		t.Fatal(err)
	}
	postWrite(tr, filepath.Join(path, "b.py"), "", "", "", "")
	second, err := os.Stat(file)
	if err != nil {
		t.Fatal(err)
	}
	if !second.ModTime().Equal(first.ModTime()) {
		t.Error("末尾の新しい項目を書き直した")
	}
}

func TestTheRootCwdIsAskedThroughACandidate(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	postWrite(tr, filepath.Join(path, "src", "a.py"), "", "", "", "")
	reason := askedIntegrate(t, integrateStop(tr, tr.Root))
	ticketkit.Has(t, reason, "worktree: scratch")
	// 同じ状態では 1 回だけ。
	quiet(t, integrateStop(tr, tr.Root), codeIntegrate)
}

// 促しの文の `…` のコマンドは、新しいシェルの 1 行として読めて（引用が閉じている）、cd の行き先が実在する。テストのワークスペースには
// スクリプトが無いので、実行はしない。
func TestTheNudgeCommandsRunFromAFreshShellAtTheRoot(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	writeIn(t, path, "src/draft.py", "y\n")
	reason := askedIntegrate(t, integrateStop(tr, path))
	var commands []string
	for _, m := range regexp.MustCompile("`([^`]+)`").FindAllStringSubmatch(reason, -1) {
		commands = append(commands, m[1])
	}
	adopt := false
	for _, c := range commands {
		adopt = adopt || strings.Contains(c, " --adopt ")
	}
	if !adopt {
		t.Errorf("--adopt のコマンドが無い: %v", commands)
	}
	for _, c := range commands {
		// 打てる形になっているか（<…> 以外に未解決の差し込みが無い）。
		if strings.Contains(c, "{") || !strings.HasPrefix(c, "cd ") {
			t.Errorf("打てる形でない: %s", c)
		}
		if strings.Contains(c, " integrate") || strings.Contains(c, " add ") || strings.Contains(c, " commit ") {
			continue
		}
		done := run.Command(t, "sh", []string{"-n", "-c", c}, run.Opts{Dir: string(filepath.Separator)})
		if done.Code != 0 {
			t.Errorf("シェルとして読めない: %s\n%s", c, done.Stderr)
		}
		target := strings.Trim(strings.TrimSpace(strings.TrimPrefix(strings.Split(c, " && ")[0], "cd ")), `"'`)
		if info, err := os.Stat(target); err != nil || !info.IsDir() {
			t.Errorf("cd の行き先が無い: %s", c)
		}
	}
}

func TestTicketControlDoesNotMatterForACandidate(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	postWrite(tr, filepath.Join(path, "src", "a.py"), "", "", "", "")
	askedIntegrate(t, integrateStop(tr, tr.Root, stopOpts{Extra: []string{"--ticket-control", "disable"}}))
}

func TestASubagentWriteIsAskedAfterItFinishes(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	startAgent(tr, "a1", tr.Root)
	postWrite(tr, filepath.Join(path, "src", "a.py"), "", "", "a1", "")
	quiet(t, integrateStop(tr, tr.Root), codeIntegrate)
	ticketkit.OK(t, stopAgent(tr, "a1", tr.Root))
	askedIntegrate(t, integrateStop(tr, tr.Root))
}

func TestARunningSubagentDefersTheNudgeWithoutTicketControlToo(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	off := []string{"--ticket-control", "disable"}
	start := map[string]any{"hook_event_name": "SubagentStart", "cwd": path, "session_id": "s1", "agent_id": "a1", "agent_type": "general-purpose"}
	rawCall(tr, start, off...)
	stopPayload := map[string]any{"hook_event_name": "Stop", "cwd": path, "session_id": "s1"}
	pointOrigin := func() {
		if !contains(strings.Fields(tr.Git(tr.Root, "remote")), "origin") {
			tr.Git(tr.Root, "remote", "add", "origin", tr.Root+".origin.git")
		}
		tr.Git(tr.Root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
	}
	pointOrigin()
	quiet(t, rawCall(tr, stopPayload, off...), codeIntegrate)
	end := map[string]any{"hook_event_name": "SubagentStop", "cwd": path, "session_id": "s1", "agent_id": "a1", "agent_type": "general-purpose"}
	rawCall(tr, end, off...)
	askedIntegrate(t, rawCall(tr, stopPayload, off...))
}

func TestAnotherSessionsRecordIsNotUsed(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	postWrite(tr, filepath.Join(path, "src", "a.py"), "", "other", "", "")
	quiet(t, integrateStop(tr, tr.Root, stopOpts{Session: "s1"}), codeIntegrate)
}

// コミットが編集のあとに Bash でできても、促す。
func TestACommitMadeByBashAfterTheLastEditIsStillAsked(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, false)
	postWrite(tr, filepath.Join(path, "src", "a.py"), "", "", "", "")
	quiet(t, integrateStop(tr, tr.Root), codeIntegrate)
	commitWork(tr, path, "src/late.py")
	askedIntegrate(t, integrateStop(tr, tr.Root))
}

func TestAnAlreadyNudgedCwdLetsAnotherCandidateBeAsked(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	first := direct(tr, true)
	second := tr.Worktree("other", "main")
	commitWork(tr, second, "src/a.py")
	postWrite(tr, filepath.Join(second, "src", "a.py"), "", "", "", "")
	askedIntegrate(t, integrateStop(tr, first))
	reason := askedNamed(t, integrateStop(tr, first), "other")
	ticketkit.Has(t, reason, "other")
}

func TestAMissingDirectoryIsDroppedFromTheRecord(t *testing.T) {
	t.Parallel()
	tr := integrateTree(t)
	path := direct(tr, true)
	postWrite(tr, filepath.Join(path, "src", "a.py"), "", "", "", "")
	tr.Git(tr.Root, "worktree", "remove", "--force", path)
	quiet(t, integrateStop(tr, tr.Root), codeIntegrate)
	if got := candidateEntries(tr, "s1"); len(got) != 0 {
		t.Errorf("記録 = %v", got)
	}
}
