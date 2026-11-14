package start

import (
	"strconv"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/ids"
)

// ReportArgs は Report・DryRun の材料（ccnavi-start.sh が <名前>=<値> で渡す）。
type ReportArgs struct {
	// Repo は対象リポジトリ（ホストのパスか、読めなければ git のトップ）、Project はプロジェクトの名前（ワークスペース自身なら空）。
	Repo, Project                                                                   string
	WT, Branch, MRURL, MRNumber, FF, Remote, SyncNote, Issue, Title, SyncNext, Real string
	// HostUnseen はホストを見ずに続けた、SyncNoTicket は ccnavi-sync.sh が親チケットも提案も無いと答えた、SyncOK は取り込んだ。
	HostUnseen, SyncNoTicket, SyncOK bool
	// Templates は、提案に使えるテンプレートの数（数えられなければ -1）。
	Templates int
}

// Report は、開始の結果を 1 項目 1 行で出す。
func Report(root string, a ReportArgs) (string, int) {
	var o outcome
	project := a.Project
	if project == "" {
		project = "ワークスペース自身"
	}
	o.out("対象リポジトリ: " + a.Repo + "（" + project + "）")
	o.out("ワークツリー: " + a.WT)
	o.out("ブランチ: " + a.Branch)
	o.out("識別子: " + ids.BranchToID(a.Branch))
	o.out("MR: " + orDash(a.MRURL))
	if a.FF != "" {
		o.out("最新化: " + a.FF)
	}
	if a.HostUnseen {
		o.out("案内: ホストは見ていない（MCP などで確認済みとして扱った）。既存の MR と閉じる issue は、渡された --issue・--mr・--branch のとおりに扱った")
	}
	switch a.Remote {
	case "unchecked":
		o.out(texts.Say(root, "NOTE_START_REMOTE_UNCHECKED", nil, nil))
	case "gone":
		o.out("案内: origin にブランチ " + a.Branch + " が無い（手元だけのブランチ）")
	}
	if a.SyncNote != "" {
		o.out("案内: " + a.SyncNote)
	}
	mr := ""
	if digits(a.MRNumber) {
		mr = a.MRNumber
		o.out("必須: 提案の frontmatter に issue: " + a.Issue + " と mr: " + mr + " を書く（この issue とこの MR の組。書かないと、この MR の番号を含む依頼が、すでにあるチケットに結び付かない。sh .ccnavi/scripts/ccnavi-launcher.sh ticket new の --issue と --mr が書く）")
	}
	if a.SyncNoTicket {
		// 親チケットも提案も無いワークツリーの次の一手。提案を作る ticket new を、埋められる値を埋めて出す。触るファイルは
		// 依頼から読み取って --scope に書くしかないので、<触るファイルの glob> は残す。
		title := a.Title
		if title == "" {
			title = "<タイトル>"
		}
		tail := "--issue " + a.Issue
		if mr != "" {
			tail += " --mr " + mr
		}
		o.out(texts.Say(root, "NEXT_START_TICKET_NEW", texts.Values{"id": ids.BranchToID(a.Branch), "title_q": quote(title),
			"scope_q": quote("<触るファイルの glob>"), "tail_q": tail}, nil))
		if a.Templates > 0 {
			o.out(texts.Say(root, "NOTE_START_TEMPLATES", texts.Values{"count": strconv.Itoa(a.Templates)}, nil))
		}
	}
	if a.SyncNext != "" {
		o.out(texts.Say(root, "NEXT_START_SYNC_RETRY", texts.Values{"id": a.SyncNext}, nil))
	}
	if a.SyncOK {
		o.out(texts.Say(root, "NEXT_START_TICKET_STATUS", texts.Values{"id": ids.BranchToID(a.Branch)}, nil))
	}
	return o.String(), 0
}

// DryArgs は DryRun の材料。
type DryArgs struct {
	// Kind は new-issue（issue を作る前）・mcp（ホストを見ずに続ける）・create（新しく作る）・existing（既にあるブランチで続ける）。
	Kind, Title, Branch, Integration, MRTitle, Number, Issue, HostWhy, MRURL, Real string
	// Resume は作りかけで、残りの段階も続ける。
	Resume bool
}

// DryRun は、何も作らずに作る予定を出す（--dry-run）。
func DryRun(root, cwd string, a DryArgs) (string, int) {
	var o outcome
	switch a.Kind {
	case "new-issue":
		o.out("dry-run: 何も作らない。issue「" + a.Title + "」を作ってから、その番号で --issue と同じに進める予定。")
	case "mcp":
		o.out("dry-run: 何も作らない。ブランチ " + a.Branch + " で続ける予定（ホストは見ていない）。")
	case "create":
		o.out("dry-run: 何も作らない。作る予定は次のとおり。")
		o.out("ブランチ: " + a.Branch + "（起点 origin/" + a.Integration + "）")
		o.out("ワークツリー: " + ospath.Join(root, ".claude", "worktrees", a.Branch))
		o.out("MR のタイトル: " + a.MRTitle + "（Draft。本文: Closes #" + a.Number + "）")
		o.out("issue: " + a.Issue + "（MR を作ると確定する）")
		if a.HostWhy != "" {
			o.out("案内: ホストに届かず、タイトルは仮のもの（" + a.HostWhy + "）")
		}
	default:
		o.out("dry-run: 何も作らない。既存のブランチ " + a.Branch + " で続ける予定。")
		if wt := (repo{cwd}).worktreeOf(a.Branch); wt != "" {
			o.out("ワークツリー: " + wt)
		} else {
			o.out("ワークツリー: " + ospath.Join(root, ".claude", "worktrees", ids.BranchToID(a.Branch)) + "（無いので作る）")
		}
		o.out("ブランチ: " + a.Branch)
		if a.Resume {
			o.out("作りかけなので、残りの段階（初期コミット・push・Draft MR）も続ける予定。MR のタイトル: " + a.MRTitle)
		} else {
			o.out("MR: " + orDash(a.MRURL))
		}
		o.out("issue: " + a.Issue)
	}
	o.out(texts.Say(root, "NEXT_START_DRY_RUN", texts.Values{"real_args_q": a.Real}, nil))
	return o.String(), 0
}

func orDash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

// digits は s が空でない数字だけの並びか。
func digits(s string) bool {
	return s != "" && strings.Trim(s, "0123456789") == ""
}

func itoa(n int) string { return strconv.Itoa(n) }
