package candidates

import (
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/ticket"
)

// 欄の検査。承認は提案の中身を変えずに動かすので、提案に書いた欄はそのまま承認済みチケットの値になる。
// 読み取り（ticket.Parse）は承認済みチケットのために寛容なまま、書き直せる提案だけをここで正す。

// fieldProblems は先行関係の欄・スクリプトだけが書く欄・続きの子の目印・欄の短縮形の苦情（この順）。
func fieldProblems(t *ticket.Ticket) []rules.Problem {
	var out []rules.Problem
	for _, check := range []func(*ticket.Ticket) []rules.Problem{
		workflowField, scriptFieldProblems, recordFieldProblems, shorthandProblems,
	} {
		out = append(out, check(t)...)
	}
	return out
}

func refuse(t *ticket.Ticket, detail string) []rules.Problem {
	return []rules.Problem{{Severity: rules.Error, Rule: t.ID, Detail: detail}}
}

// workflowField は `workflow:` を拒む。欄は読まないので、書いてあると効くと読み手に思わせる。
func workflowField(t *ticket.Ticket) []rules.Problem {
	if !t.Raw.Has(ticket.WorkflowKey) {
		return nil
	}
	return refuse(t, "`"+ticket.WorkflowKey+"` の欄は提案に書かない（読まない）。"+
		"先行関係は計画のステップの `after` から決まる")
}

// shorthandProblems は欄の短縮形（`issue: "#12"`・`human_review: true/false`）を拒む。
func shorthandProblems(t *ticket.Ticket) []rules.Problem {
	var out []rules.Problem
	if issue, ok := t.Raw.String("issue"); ok && strings.HasPrefix(strings.TrimSpace(issue), "#") {
		written := strings.TrimSpace(issue)
		out = append(out, refuse(t, "`issue: "+written+"` は短縮形。番号だけを書く"+
			"（`issue: "+strings.TrimSpace(strings.TrimLeft(written, "#"))+"`）")...)
	}
	if _, ok := t.Raw.Bool("human_review"); ok {
		out = append(out, refuse(t, "`human_review` は {required: true か false, reason: 理由} で書く"+
			"（true/false だけは書かない）")...)
	}
	return out
}

// scriptFieldProblems はスクリプトだけが書く欄の空でない値を拒む。review/ の 2 つめの保護（completed_at）や
// 着手の基準点（base_sha）は、これらの欄をスクリプトだけが書くことを前提にしている。
func scriptFieldProblems(t *ticket.Ticket) []rules.Problem {
	var found []string
	for _, name := range ticket.ScriptFields() {
		v := t.Raw.Get(name)
		if s, isStr := v.(string); v == nil || (isStr && s == "") {
			continue
		}
		if strings.TrimSpace(pyfmt.Str(v)) != "" {
			found = append(found, "`"+name+"`")
		}
	}
	if len(found) == 0 {
		return nil
	}
	return refuse(t, strings.Join(found, ", ")+" はスクリプト（ccnavi-ticket.sh）だけが書く欄。"+
		"提案には値を書かない（空にするか削除します）。改版の提案では、承認済みチケットをコピーしたときに"+
		"入る started_at・completed_at・base_sha・merged_sha・cancelled_at・cancel_reason・"+
		"finished_to・settled_by を"+
		"空にする"+
		"（改版は承認済みチケットの側の値を残し、計画・`phases:`・範囲だけを差し替える）。"+
		"`"+config.Launcher+" ticket revise <親>` が作る雛形は、これらの欄を外してある")
}

// recordFieldProblems は続きの子の目印を拒む。目印はユーザの判断（レビューの行き先）だけが残すもので、提案に
// 書かせると続きの子を装える。
func recordFieldProblems(t *ticket.Ticket) []rules.Problem {
	if !t.Raw.Has(approval.FollowupKey) {
		return nil
	}
	return refuse(t, "`"+approval.FollowupKey+"` は続きの子を起こすレビューの行き先だけが残すもの。"+
		"提案には書かない（改版の提案で承認済みチケットをコピーしたときも削除します）")
}
