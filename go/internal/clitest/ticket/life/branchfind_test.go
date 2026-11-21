package life

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// issue・MR に紐づくブランチを探す仕組み。
//  1. UserPromptSubmit: 依頼文に issue・MR の指定があれば、ccnavi-start.sh を打って着手させ、ホストに届かないときは MCP で代行させる
//     指示が additionalContext に載る。表記はワークスペースルートの絶対パスから書く。指定が無い・外れ・チケット制御が disable のときは
//     載らない。判定（止める・聞く）は返さない
//  2. サブコマンド `ccnavi branches <issue|mr> <番号> checked <github|gitlab> <ホスト> <リポジトリ> <id> --result <応答>`: 手元の候補
//     （名前に番号を含むブランチ・ワークツリー・issue: を持つチケット）を集め、sh が取ってきたホストの応答と合わせて出す。
//     ホストの結果が無いときは「ホストは見ていない」と言う。チケットは識別子になるブランチ（`/` を `-` に置き換えて識別子と一致するもの）に結び付ける

// phasesTree は定義の見本（samples.Phases）の木。
func phasesTree(t *testing.T) *ticketkit.Tree {
	t.Helper()
	return ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
}

// researchParent は計画が research だけの親の提案の本文（issue 0 なら 1、mr 0 なら 1）。
func researchParent(name string, issue, mr int) string {
	text := samples.ParentText(name, samples.Plan("research"), samples.ParentOpts{Issue: issue})
	if mr != 0 {
		text = strings.Replace(text, "\nmr: 1\n", fmt.Sprintf("\nmr: %d\n", mr), 1)
	}
	return text
}

// proposeOwn は承認する提案を、識別子と同じ名前のワークツリーに書いてコミットする。
func proposeOwn(tr *ticketkit.Tree, name, text string) {
	tr.T.Helper()
	tree := tr.Worktree(name, "main")
	tr.Propose(name, text, tree)
	tr.Git(tree, "add", "-A")
	tr.Git(tree, "commit", "--quiet", "-m", "approval-request "+name)
}

// prompt は UserPromptSubmit に text を渡し、追加の文脈を返す（出力が無ければ空）。判定は返さないことも見る。
func prompt(tr *ticketkit.Tree, text, session string, extra ...string) string {
	tr.T.Helper()
	if session == "" {
		session = "s1"
	}
	r := ticketkit.OK(tr.T, tr.CallJSON(map[string]any{"hook_event_name": "UserPromptSubmit", "cwd": tr.ParentTree, "session_id": session, "prompt": text},
		append([]string{"--mode", "enable"}, extra...)...))
	if strings.TrimSpace(r.Stdout) == "" {
		return ""
	}
	var out struct {
		Hook map[string]any `json:"hookSpecificOutput"`
	}
	jsonOf(tr.T, r.Stdout, &out)
	if _, ok := out.Hook["permissionDecision"]; ok {
		tr.T.Errorf("判定を返した: %s", r.Stdout)
	}
	s, _ := out.Hook["additionalContext"].(string)
	return s
}

func startShQuoted(tr *ticketkit.Tree, args string) string {
	return "'" + base.ScriptCommand(tr.Root, "ccnavi-start.sh") + " " + args + "'"
}

func TestAnIssueAndAnMRGetOneInstructionEach(t *testing.T) {
	t.Parallel()
	tr := phasesTree(t)
	said := prompt(tr, "#152 と !5 を見て直して", "")
	sh := base.ScriptCommand(tr.Root, "ccnavi-start.sh")
	for _, w := range []string{"'" + sh + " --issue 152'", "'" + sh + " --mr 5'", strings.ReplaceAll(realPath(t, tr.Root), `\`, "/"),
		// 短い指示。終了コードごとの説明は ccnavi-start.sh の出力の「次:」に任せる。
		"着手前に '" + sh + " --check'、続けて", "出力の「次:」に従う", "Draft MR とワークツリーができる",
		// 実装と調査の両方の分岐が入る。
		"実装の依頼なら", "調べ・質問に答えるだけなら、チケット作業にせずそのまま答えてよい", "どちらか分からなければ、ユーザに聞く"} {
		ticketkit.Has(t, said, w)
	}
	for _, gone := range []string{"終了コード 3", "終了コード 4", "返事を待つ", "<先頭の語>-<番号>-<slug>", "ccnavi-branches.sh"} {
		ticketkit.Lacks(t, said, gone)
	}
}

func realPath(t *testing.T, p string) string {
	t.Helper()
	real, err := filepath.EvalSymlinks(p)
	if err != nil {
		t.Fatal(err)
	}
	return real
}

func TestTheShortInstructionHasBothBranches(t *testing.T) {
	t.Parallel()
	said := prompt(phasesTree(t), "#1 を直して", "")
	ticketkit.Has(t, said, "実装の依頼なら")
	ticketkit.Has(t, said, "調べ・質問に答えるだけなら")
	if n := len(strings.Split(said, "\n")); n >= 4 {
		t.Errorf("指示が %d 行", n)
	}
}

// 指定ではないものには指示を足さない。
func TestPromptsThatAreNotReferencesGetNoInstruction(t *testing.T) {
	t.Parallel()
	notice := "[ccnavi] 取り込み済み: https://example.com/acme/widgets/pull/296 を閉じた\n"
	for _, c := range []struct {
		name, text string
		extra      []string
	}{
		{"a_number_inside_a_word", "手順#2を見直して", nil},
		{"a_number_in_code_quotes", "`#12` を直して", nil},
		{"a_ccnavi_notice", notice, nil},
		{"pasted_content", "<pasted_content>ログ #10 と MR !7</pasted_content>", nil},
		{"a_system_reminder", "<system-reminder>issue 11</system-reminder>", nil},
		{"another_session_message", "Another Claude session sent a message:\nPR #12 を出した", nil},
		{"no_reference", "README の見出し # 概要 を直して。色は #fff", nil},
		{"ticket_control_disabled", "#152 を直して", []string{"--ticket-control", "disable"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			if said := prompt(phasesTree(t), c.text, "", c.extra...); said != "" {
				t.Errorf("指示を足した: %s", said)
			}
		})
	}
}

// 通知や貼り付けの外にも指定があるときは、外の分だけ拾う。
func TestOnlyTheReferencesOutsideANoticeOrPastedContentAreTaken(t *testing.T) {
	t.Parallel()
	tr := phasesTree(t)
	notice := "[ccnavi] 取り込み済み: https://example.com/acme/widgets/pull/296 を閉じた\n"
	said := prompt(tr, notice+"\n#10 を直して", "s2")
	ticketkit.Has(t, said, startShQuoted(tr, "--issue 10"))
	ticketkit.Lacks(t, said, "296")
	said = prompt(tr, "<pasted_content>#10</pasted_content> と !7", "s4")
	ticketkit.Has(t, said, "--mr 7")
	ticketkit.Lacks(t, said, "--issue 10")
}

func TestANumberTiedToATicketDoesNotSayStart(t *testing.T) {
	t.Parallel()
	tr := phasesTree(t)
	proposeOwn(tr, "feature-152-login", researchParent("feature-152-login", 152, 0))
	tr.Approve()
	said := prompt(tr, "#152 を直して", "")
	for _, w := range []string{"すでに feature-152-login(doing)", "--check を最初に打つ", "結び付いている", "実装の依頼なら、そのチケットのワークツリー（", "説明・調査だけなら、作業場所を変えずに答えてよい"} {
		ticketkit.Has(t, said, w)
	}
	ticketkit.Lacks(t, said, "ccnavi-start.sh --issue")
	// 結び付かない番号は今までどおり。
	ticketkit.Has(t, prompt(tr, "#9 を直して", "s5"), "--issue 9")
}

func TestABranchNameWithTheNumberIsNotAnMRBinding(t *testing.T) {
	t.Parallel()
	tr := phasesTree(t)
	proposeOwn(tr, "fix-12-foo", researchParent("fix-12-foo", 3, 0))
	tr.Approve()
	said := prompt(tr, "MR !12 を見て", "")
	ticketkit.Has(t, said, "--mr 12")
	ticketkit.Lacks(t, said, "結び付いている")
}

func TestAMRFieldOfAnApprovedTicketSaysContinue(t *testing.T) {
	t.Parallel()
	tr := phasesTree(t)
	proposeOwn(tr, "feature-3-foo", researchParent("feature-3-foo", 0, 12))
	tr.Approve()
	said := prompt(tr, "MR !12 を見て", "")
	ticketkit.Has(t, said, "すでに feature-3-foo(doing)")
	ticketkit.Lacks(t, said, "--mr 12")
	// 番号が別なら結び付かない。
	other := prompt(tr, "MR !13 を見て", "s2")
	ticketkit.Has(t, other, "--mr 13")
	ticketkit.Lacks(t, other, "結び付いている")
}

func TestAMRFieldOfAProposalSaysContinueAndIsNotAnIssueBinding(t *testing.T) {
	t.Parallel()
	tr := phasesTree(t)
	tr.Propose("feature-3-foo", researchParent("feature-3-foo", 0, 12), "")
	said := prompt(tr, "MR !12 を見て", "")
	ticketkit.Has(t, said, "すでに feature-3-foo(todo)")
	ticketkit.Has(t, said, "結び付いている")
	ticketkit.Lacks(t, said, "--mr 12")
	// mr: は issue の結び付きではない。
	ticketkit.Has(t, prompt(tr, "#12 を直して", "s2"), "--issue 12")
}

func TestTheSameNumberIsSaidOncePerSession(t *testing.T) {
	t.Parallel()
	tr := phasesTree(t)
	ticketkit.Has(t, prompt(tr, "#152 を直して", ""), "--issue 152")
	if said := prompt(tr, "#152 の続き", ""); said != "" {
		t.Errorf("同じ番号をもう 1 度言った: %s", said)
	}
	// 別の番号は言う。別のセッションは別に数える。
	ticketkit.Has(t, prompt(tr, "#152 と #153", ""), "--issue 153")
	ticketkit.Has(t, prompt(tr, "#152 を直して", "other"), "--issue 152")
}

// 指示を足すだけで止めないので、dry-run でも同じ文を足す（モードが disable なら hook は何もしない）。
func TestDryRunStillSaysIt(t *testing.T) {
	t.Parallel()
	ticketkit.Has(t, prompt(phasesTree(t), "#152 を直して", "", "--mode", "dry-run"), "--issue 152")
}

// ---- branches

func branchesTree(t *testing.T) *ticketkit.Tree {
	t.Helper()
	tr := phasesTree(t)
	for _, name := range []string{"feature-152-login", "fix/152-typo", "issue-152", "feature-1520-other"} {
		tr.Git(tr.Root, "branch", name, "main")
	}
	// 手元に無く origin にだけあるブランチ（取ってきた ref）。
	sha := strings.TrimSpace(tr.Git(tr.Root, "rev-parse", "main"))
	tr.Git(tr.Root, "update-ref", "refs/remotes/origin/152-from-host", sha)
	tr.Git(tr.Root, "update-ref", "refs/remotes/origin/HEAD", sha)
	tr.Git(tr.Root, "worktree", "add", "--quiet", tr.ChildTree("feature-152-login"), "feature-152-login")
	return tr
}

// branchesOut は `branches <kind> <number> <result…> --cwd <cwd> <extra…>`。result はホストの結果の渡し方
// （checked ...・unchecked <理由> と --result <ファイル>）。
func branchesOut(tr *ticketkit.Tree, kind string, number string, result []string, cwd string, extra ...string) string {
	tr.T.Helper()
	return branchesRun(tr, kind, number, result, cwd, extra...).Stdout
}

func branchesRun(tr *ticketkit.Tree, kind, number string, result []string, cwd string, extra ...string) run.Result {
	tr.T.Helper()
	if cwd == "" {
		cwd = tr.Root
	}
	args := append([]string{"branches", kind, number}, result...)
	args = append(args, "--cwd", cwd)
	return tr.Ccnavi(append(args, extra...)...)
}

type candidate struct {
	Branch    string   `json:"branch"`
	Sources   []string `json:"sources"`
	Local     bool     `json:"local"`
	Origin    bool     `json:"origin"`
	Worktrees []string `json:"worktrees"`
	Tickets   []struct {
		Ticket   string `json:"ticket"`
		State    string `json:"state"`
		Approved bool   `json:"approved"`
	} `json:"tickets"`
	MRs []struct {
		Number int `json:"number"`
	} `json:"mrs"`
}

type branchesData struct {
	Host           struct{ Checked bool }    `json:"host"`
	Candidates     []candidate               `json:"candidates"`
	IssueTickets   []struct{ Ticket string } `json:"issue_tickets"`
	TicketsChecked bool                      `json:"tickets_checked"`
}

func branchesJSON(tr *ticketkit.Tree, kind, number string, result []string, extra ...string) branchesData {
	tr.T.Helper()
	r := branchesRun(tr, kind, number, result, "", append(extra, "--json")...)
	if r.Code != 0 {
		tr.T.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	var d branchesData
	jsonOf(tr.T, r.Stdout, &d)
	return d
}

func (d branchesData) byName() map[string]candidate {
	m := map[string]candidate{}
	for _, c := range d.Candidates {
		m[c.Branch] = c
	}
	return m
}

func (d branchesData) names() []string {
	var out []string
	for _, c := range d.Candidates {
		out = append(out, c.Branch)
	}
	return out
}

// hostResult は sh が取ってきたホストの応答（のまま）を渡す形。
func hostResult(tr *ticketkit.Tree, kind, name string, data any, pid string) []string {
	tr.T.Helper()
	raw, err := json.Marshal(data)
	if err != nil {
		tr.T.Fatal(err)
	}
	path := ticketkit.Write(tr.T, filepath.Join(tr.Root, "host.json"), string(raw))
	return []string{"checked", kind, name, "acme/widgets", pid, "--result", path}
}

func TestLocalCandidatesWithoutTheHost(t *testing.T) {
	t.Parallel()
	tr := branchesTree(t)
	text := branchesOut(tr, "issue", "152", nil, "")
	ticketkit.Has(t, text, "ホストは見ていない")
	var names []string
	byName := map[string]string{}
	for _, line := range ticketkit.Lines(text) {
		if !strings.HasPrefix(line, "候補 ") {
			continue
		}
		if strings.Contains(line, "在りか=") {
			names = append(names, strings.Fields(line)[1])
		}
		byName[strings.Fields(line)[1]] = line
	}
	slices.Sort(names)
	if want := []string{"152-from-host", "feature-152-login", "fix/152-typo", "issue-152"}; !slices.Equal(names, want) {
		t.Errorf("候補 = %v", names)
	}
	ticketkit.Has(t, byName["feature-152-login"], "ワークツリー=.claude/worktrees/feature-152-login")
	ticketkit.Has(t, byName["feature-152-login"], "在りか=手元")
	ticketkit.Has(t, byName["152-from-host"], "在りか=origin")
	ticketkit.Has(t, text, "候補 4 件")
}

// Web セッションのブランチ名の末尾に番号の数字が混ざっていても、候補にしない。
func TestAWebSessionBranchWithTheDigitsInItsSuffixIsNotACandidate(t *testing.T) {
	t.Parallel()
	tr := branchesTree(t)
	tr.Git(tr.Root, "branch", "claude/tender-volta-qmy99q", "main")
	if d := branchesJSON(tr, "issue", "99", nil); len(d.Candidates) != 0 {
		t.Errorf("候補 = %v", d.names())
	}
	text := branchesOut(tr, "issue", "99", nil, "")
	ticketkit.Lacks(t, text, "qmy99q")
	ticketkit.Has(t, text, "候補なし")
}

func TestACandidateFoundOnlyByNameIsCalledAWeakGround(t *testing.T) {
	t.Parallel()
	tr := branchesTree(t)
	text := branchesOut(tr, "issue", "152", nil, "")
	ticketkit.Has(t, text, "案内: ")
	ticketkit.Has(t, text, "弱い根拠")
	ticketkit.Lacks(t, text, "\n次:")
	// issue: を持たない親でも、識別子と一致するブランチはチケットが付くので言わない。
	tr.Git(tr.Root, "branch", "feature-300-other", "main")
	tr.Propose("feature-300-other", researchParent("feature-300-other", 9, 0), "")
	ticketkit.Lacks(t, branchesOut(tr, "issue", "300", nil, ""), "feature-300-other は")
	// チケットに結び付く候補だけなら言わない。
	tr.Propose("feature-153-slash", researchParent("feature-153-slash", 153, 0), "")
	ticketkit.Lacks(t, branchesOut(tr, "issue", "153", nil, ""), "弱い根拠")
}

// 候補は識別子になるブランチ。チケットに結び付く候補が先に並ぶ。
func TestTicketsWithTheIssueJoinTheCandidates(t *testing.T) {
	t.Parallel()
	tr := branchesTree(t)
	tr.Propose("feature-152-login", researchParent("feature-152-login", 152, 0), "")
	tr.Propose("feature-9-other", researchParent("feature-9-other", 9, 0), "")
	d := branchesJSON(tr, "issue", "152", nil)
	if d.Host.Checked {
		t.Error("ホストを見たことになっている")
	}
	login := d.byName()["feature-152-login"]
	if !slices.Contains(login.Sources, "ticket") || !slices.Contains(login.Sources, "name") {
		t.Errorf("由来 = %v", login.Sources)
	}
	if len(login.Tickets) != 1 || login.Tickets[0].Ticket != "feature-152-login" || login.Tickets[0].State != "todo" || login.Tickets[0].Approved {
		t.Errorf("チケット = %+v", login.Tickets)
	}
	if !slices.Equal(login.Worktrees, []string{".claude/worktrees/feature-152-login"}) {
		t.Errorf("ワークツリー = %v", login.Worktrees)
	}
	if d.Candidates[0].Branch != "feature-152-login" {
		t.Errorf("先頭 = %s", d.Candidates[0].Branch)
	}
	if len(d.IssueTickets) != 1 || d.IssueTickets[0].Ticket != "feature-152-login" {
		t.Errorf("issue のチケット = %v", d.IssueTickets)
	}
}

// 識別子は feature-153-slash。ブランチは feature/153-slash（`/` を `-` に置き換えると一致）。
func TestATicketBindsToTheBranchThatConvertsToItsIdentifier(t *testing.T) {
	t.Parallel()
	tr := branchesTree(t)
	tr.Git(tr.Root, "branch", "feature/153-slash", "main")
	tr.Propose("feature-153-slash", researchParent("feature-153-slash", 153, 0), "")
	d := branchesJSON(tr, "issue", "153", nil)
	named, ok := d.byName()["feature/153-slash"]
	if !ok || !slices.Contains(named.Sources, "ticket") || !named.Local || len(named.Tickets) != 1 || named.Tickets[0].Ticket != "feature-153-slash" {
		t.Errorf("候補 = %+v", named)
	}
	if _, ok := d.byName()["feature-153-slash"]; ok {
		t.Error("識別子そのもののブランチを候補にした")
	}
	ticketkit.Has(t, branchesOut(tr, "issue", "153", nil, ""), "チケット feature-153-slash")
}

// GitHub の開いた PR の一覧。issue を参照する PR だけが候補になり、番号が数でないものは使わない。
func TestHostMergeRequestsJoinTheCandidates(t *testing.T) {
	t.Parallel()
	tr := branchesTree(t)
	result := hostResult(tr, "github", "github.com", []map[string]any{
		{"number": 7, "head": map[string]any{"ref": "topic/login", "repo": map[string]any{"full_name": "acme/widgets"}}, "state": "open",
			"html_url": "https://github.com/acme/widgets/pull/7", "title": "Closes #152", "base": map[string]any{"ref": "main"}},
		{"number": "x", "head": map[string]any{"ref": "bad"}, "title": "#152"},
		{"number": 8, "head": map[string]any{"ref": "unrelated"}, "title": "other"},
	}, "-")
	d := branchesJSON(tr, "issue", "152", result)
	if !d.Host.Checked {
		t.Error("ホストを見たことになっていない")
	}
	topic, ok := d.byName()["topic/login"]
	if !ok || !slices.Equal(topic.Sources, []string{"mr"}) || len(topic.MRs) != 1 || topic.MRs[0].Number != 7 || topic.Local || topic.Origin {
		t.Errorf("topic/login = %+v", topic)
	}
	for _, name := range []string{"bad", "unrelated"} {
		if _, ok := d.byName()[name]; ok {
			t.Errorf("%s を候補にした", name)
		}
	}
	text := branchesOut(tr, "issue", "152", result, "")
	ticketkit.Has(t, text, "ホスト: github.com acme/widgets を見た")
	ticketkit.Has(t, text, "候補 topic/login  在りか=ホストだけ  由来=MR  MR=!7(open)")
}

// GitLab の MR 1 本。このプロジェクト（id 42）から出ていなければフォーク。
func TestAnMRListsItsSourceBranchOnly(t *testing.T) {
	t.Parallel()
	tr := branchesTree(t)
	result := hostResult(tr, "gitlab", "gitlab.com", map[string]any{"iid": 5, "source_branch": "issue-152", "state": "opened", "source_project_id": 99}, "42")
	d := branchesJSON(tr, "mr", "5", result)
	if !slices.Equal(d.names(), []string{"issue-152"}) || !d.Candidates[0].Local {
		t.Errorf("候補 = %v", d.names())
	}
	ticketkit.Has(t, branchesOut(tr, "mr", "5", result, ""), "MR=!5(opened,フォーク)")
}

func TestTicketsWithTheMRFieldJoinTheCandidates(t *testing.T) {
	t.Parallel()
	tr := branchesTree(t)
	tr.Propose("feature-3-foo", researchParent("feature-3-foo", 0, 12), "")
	tr.Propose("feature-4-bar", researchParent("feature-4-bar", 0, 13), "")
	d := branchesJSON(tr, "mr", "12", nil)
	if !slices.Equal(d.names(), []string{"feature-3-foo"}) || !slices.Equal(d.Candidates[0].Sources, []string{"ticket"}) {
		t.Errorf("候補 = %+v", d.Candidates)
	}
	if len(d.IssueTickets) != 1 || d.IssueTickets[0].Ticket != "feature-3-foo" {
		t.Errorf("チケット = %v", d.IssueTickets)
	}
	if d99 := branchesJSON(tr, "mr", "99", nil); len(d99.Candidates) != 0 {
		t.Errorf("99 の候補 = %v", d99.names())
	}
	// issue の枝は mr: を見ない。
	if di := branchesJSON(tr, "issue", "12", nil); len(di.IssueTickets) != 0 {
		t.Errorf("issue 12 のチケット = %v", di.IssueTickets)
	}
}

func TestAnMRWithoutTheHostSaysSoAndHasNoCandidate(t *testing.T) {
	t.Parallel()
	text := branchesOut(branchesTree(t), "mr", "5", []string{"unchecked", "origin が無い"}, "")
	ticketkit.Has(t, text, "ホストは見ていない（origin が無い）")
	ticketkit.Has(t, text, "候補なし")
}

func TestTicketControlDisabledSkipsTheTickets(t *testing.T) {
	t.Parallel()
	tr := branchesTree(t)
	tr.Propose("feature-152-login", researchParent("feature-152-login", 152, 0), "")
	d := branchesJSON(tr, "issue", "152", nil, "--ticket-control", "disable")
	if d.TicketsChecked || len(d.IssueTickets) != 0 {
		t.Errorf("チケットを見た: %+v", d)
	}
}

func TestBranchesRefusesBadArgumentsAndAPlaceOutsideTheWorkspace(t *testing.T) {
	t.Parallel()
	tr := branchesTree(t)
	for _, c := range [][2]string{{"pr", "1"}, {"issue", "0"}, {"issue", "x"}, {"mr", "-1"}} {
		if r := branchesRun(tr, c[0], c[1], nil, ""); r.Code != 1 {
			t.Errorf("%v: code %d", c, r.Code)
		}
	}
	r := branchesRun(tr, "issue", "1", nil, filepath.Dir(tr.Root))
	if r.Code != 1 {
		t.Errorf("code %d", r.Code)
	}
	ticketkit.Has(t, r.Stderr, "ワークスペースの外")
}
