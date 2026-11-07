package launch

import (
	"slices"
	"strconv"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/hook/childnote"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// 止める理由の識別子（文面ファイル .ccnavi/messages/deny-agent.yml の識別子と同じ）と組み込みのルールの名前。
const (
	codeIsolation        = "DENY_AGENT_ISOLATION"
	codeNoChild          = "DENY_AGENT_NO_CHILD"
	codeMultiTarget      = "DENY_AGENT_MULTI_TARGET"
	codeParentTree       = "DENY_AGENT_PARENT_TREE"
	codeChildUnavailable = "DENY_AGENT_CHILD_UNAVAILABLE"
	codeCwdConflict      = "DENY_AGENT_CWD_CONFLICT"

	ruleIsolation        = rules.ReservedIDPrefix + "agent-isolation"
	ruleNoChild          = rules.ReservedIDPrefix + "agent-no-child"
	ruleMultiTarget      = rules.ReservedIDPrefix + "agent-multi-target"
	ruleParentTree       = rules.ReservedIDPrefix + "agent-parent-tree"
	ruleChildUnavailable = rules.ReservedIDPrefix + "agent-child-unavailable"
	ruleCwdConflict      = rules.ReservedIDPrefix + "agent-cwd-conflict"
)

// listedChildren は止める理由に名前を並べる子の数。
const listedChildren = 5

// 文面の場合分けの欄と場合。
const (
	variantNext = "next"
	variantWhat = "what"
)

func isolationStop(root string) *Stop {
	return &Stop{codeIsolation, ruleIsolation, texts.Say(root, codeIsolation, nil, nil), config.Aux}
}

func multiTargetStop(root string, targets []*target, gate bool) *Stop {
	names := make([]string, len(targets))
	for i, t := range targets {
		names[i] = t.name
	}
	text := texts.Say(root, codeMultiTarget,
		texts.Values{"count": strconv.Itoa(len(targets)), "names": strings.Join(names, ", ")}, nil)
	kind := config.Aux
	if gate {
		kind = config.Gate
	}
	return &Stop{codeMultiTarget, ruleMultiTarget, text, kind}
}

func idsOf(children []*ticket.Ticket) string {
	ids := make([]string, 0, listedChildren)
	for _, c := range children[:min(len(children), listedChildren)] {
		ids = append(ids, c.ID)
	}
	return strings.Join(ids, "、")
}

func parentTreeStop(root string, t *target, b *book) *Stop {
	var children []*ticket.Ticket
	if t.ticket != nil {
		children = b.working(t.ticket)
	}
	listed := idsOf(children)
	next := "none"
	if listed != "" {
		next = "working"
	}
	text := texts.Say(root, codeParentTree, texts.Values{"name": t.name, "listed": listed}, texts.Variants{variantNext: next})
	return &Stop{codeParentTree, ruleParentTree, text, config.Gate}
}

func childUnavailableStop(root string, t *target, why string) *Stop {
	c := t.ticket
	startable := c != nil && t.state == place.Doing && c.Blocked == "" && c.CompletedAt == "" &&
		(c.StartedAt == "" || t.worktree == nil)
	next := "wait"
	if startable {
		next = "start"
	}
	text := texts.Say(root, codeChildUnavailable, texts.Values{"name": t.name, "reason": why},
		texts.Variants{variantNext: next})
	return &Stop{codeChildUnavailable, ruleChildUnavailable, text, config.Gate}
}

func cwdConflictStop(root, given string, t *target) *Stop {
	path := ""
	if t.worktree != nil {
		path = t.worktree.Root
	}
	text := texts.Say(root, codeCwdConflict, texts.Values{"given": given, "name": t.name, "path": path}, nil)
	return &Stop{codeCwdConflict, ruleCwdConflict, text, config.Aux}
}

func noChildStop(root string, parent *ticket.Ticket, working []*ticket.Ticket) *Stop {
	rest := len(working) - listedChildren
	what := "few"
	if rest > 0 {
		what = "many"
	}
	text := texts.Say(root, codeNoChild,
		texts.Values{"parent": parent.ID, "listed": idsOf(working), "rest": strconv.Itoa(max(rest, 0))},
		texts.Variants{variantWhat: what})
	return &Stop{codeNoChild, ruleNoChild, text, config.Gate}
}

// 担当の案内の囲みと長さの上限（文字）。
const (
	guideOpen  = hookio.Head + "担当の案内（ここから）"
	guideClose = hookio.Head + "担当の案内（ここまで）"
	guideLimit = 4000
)

// plan は担当が決まったときの差し替えたプロンプト。案内を組めない・要らないなら空。
func plan(conf *config.Settings, root, prompt string, t *target, b *book) string {
	if b == nil || t.worktree == nil {
		return ""
	}
	guide := guidance(conf, root, t, b)
	if guide == "" {
		return ""
	}
	return pyfmt.JoinNonEmpty(pyfmt.ParagraphBreak, stripGuide(prompt), guide)
}

// stripGuide はプロンプトにある案内の囲みを、中身を信用せずに取り除く。開きから閉じまでを全部、閉じの無い開きは
// 末尾まで、開きの無い閉じはその印だけ。
func stripGuide(prompt string) string {
	text := prompt
	for {
		start := strings.Index(text, guideOpen)
		if start < 0 {
			break
		}
		end := strings.Index(text[start+len(guideOpen):], guideClose)
		if end < 0 {
			text = text[:start]
		} else {
			text = text[:start] + text[start+len(guideOpen)+end+len(guideClose):]
		}
	}
	// 除いたあとの連結で、偽の閉じが新しくできないように、残らなくなるまで繰り返す。
	for strings.Contains(text, guideClose) {
		text = strings.ReplaceAll(text, guideClose, "")
	}
	return ospath.Rstrip(text)
}

// width は行の並びが案内に占める文字数（行ごとの改行を含む）。
func width(lines []string) int {
	n := 0
	for _, line := range lines {
		n += utf8.RuneCountInString(line) + 1
	}
	return n
}

// guidance はプロンプトの末尾に足す担当の案内（guideLimit 文字以内）。必須の行（担当・ワークツリーの絶対パス・
// 決まりの行・閉じ・フローのファイル）の分を先に取り、残りに範囲・フローの手順・局面を並べる。収まらない並びは丸ごと
// 省き、末尾を切り捨てない。省いた並びがあるか手順を並べなかったときだけ、フローのファイルの行を残す。
func guidance(conf *config.Settings, root string, t *target, b *book) string {
	if t.role == roleDirect {
		lines := append([]string{guideOpen, "担当: 直接作業のワークツリー " + t.worktree.Name, "ワークツリー: " + t.worktree.Root},
			childnote.RuleLines()...)
		return strings.Join(append(lines, guideClose), "\n")
	}
	child := t.ticket
	flowPath, _, flowExists := flow.Resolve(conf, root, child)
	head := []string{guideOpen, "担当の子: " + child.ID, "ワークツリー: " + t.worktree.Root}
	more := ""
	if flowExists {
		more = "続きは " + flowPath + " を読む"
	}
	rules := childnote.RuleLines()
	room := max(0, guideLimit-width(head)-width(rules)-width([]string{guideClose}))
	if more != "" {
		room = max(0, room-width([]string{more}))
	}
	block := childnote.ChildBlock(conf, root, child, b.index, b.pool(conf, root), room, true)
	var body []string
	for _, line := range block.Lines {
		if !slices.Contains(block.Brief, line) {
			body = append(body, line)
		}
	}
	lines := head
	cut := false
	for _, group := range [][]string{body, block.Brief, childnote.StageLines(conf, root, b.index, []*ticket.Ticket{child})} {
		if len(group) == 0 {
			continue
		}
		if size := width(group); size <= room {
			lines = append(lines, group...)
			room -= size
		} else {
			cut = true
		}
	}
	lines = append(lines, rules...)
	if more != "" && (cut || len(block.Brief) == 0) {
		lines = append(lines, more)
	}
	return strings.Join(append(lines, guideClose), "\n")
}
