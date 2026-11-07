package integrate

import (
	"strconv"
	"strings"

	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
)

// CodeIntegrateNudge は直接作業の integrate を促すときの理由コード。
const CodeIntegrateNudge = "NUDGE_INTEGRATE"

// unknownBase は統合先のツリーのブランチが読めないときに案内に出す名前。
const unknownBase = "<取り込み先のブランチ>"

func left(d *Direct) string {
	var parts []string
	if d.Dirty {
		parts = append(parts, "未コミットの変更")
	}
	if d.Ahead != 0 {
		where := d.Base
		if where == "" {
			where = d.Integration
		}
		parts = append(parts, "取り込み先（"+where+"）に入っていないコミット "+strconv.Itoa(d.Ahead)+" 件")
	}
	return strings.Join(parts, "と")
}

// IntegrateNudge は Stop を止めて渡す文。状態ごとの文は文面ファイルが持ち（NUDGE_INTEGRATE__*）、ここは
// どの状態かを決めて条件つきの補足（NOTE_INTEGRATE_*）を足す。
func IntegrateNudge(root string, d *Direct) string {
	head := hookio.Tagged(CodeIntegrateNudge, "worktree: "+d.Name)
	values := texts.Values{"name": d.Name, "integ_tree": d.IntegTree}
	// 版を知らない記録は、操作させず、ユーザに片付けを頼んで終える。
	if d.Marker == MarkerUnknown {
		return head + "\n" + texts.Say(root, "NUDGE_INTEGRATE__UNKNOWN_MARKER", values, nil)
	}
	values["left"] = left(d)
	var extra []string
	if d.Dirty {
		extra = append(extra, texts.Say(root, "NOTE_INTEGRATE_COMMIT_FIRST", texts.Values{"work_tree": d.Worktree}, nil))
	}
	keepGoing := texts.Say(root, "NOTE_INTEGRATE_KEEP_GOING", nil, nil)
	lines := []string{head}
	if d.NoRecord {
		base, ok := tree.BranchOf(d.IntegTree)
		if !ok {
			base = unknownBase
		}
		values["base"] = base
		lines = append(lines, texts.Say(root, "NUDGE_INTEGRATE__ADOPT", values, nil))
		return strings.Join(append(append(lines, extra...), keepGoing), "\n")
	}
	lines = append(lines, texts.Say(root, "NUDGE_INTEGRATE__RUN", values, nil))
	lines = append(lines, extra...)
	if d.OriginAhead != 0 {
		lines = append(lines, texts.Say(root, "NOTE_INTEGRATE_ORIGIN_AHEAD",
			texts.Values{"base": d.Base, "count": strconv.Itoa(d.OriginAhead)}, nil))
	}
	return strings.Join(append(lines, keepGoing), "\n")
}

// BlockedNotice は促せないときにユーザへ言う文（systemMessage）。残りを言うのは、取り込み先に入っていない
// コミットがあるときだけ。
func BlockedNotice(d *Direct) string {
	var where string
	switch {
	case d.Ahead != 0:
		where = left(d) + "がありますが"
	case d.Marker != MarkerNone:
		where = "取り込みの途中ですが"
	default:
		return hookio.Head + "直接作業 " + d.Name + " は、今は取り込めません。" + d.Blocked
	}
	return hookio.Head + "直接作業 " + d.Name + " は " + where + "、今は取り込みを促せません。" + d.Blocked
}
