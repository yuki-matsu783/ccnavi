package post

import (
	"strings"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/policy/texts"
)

const (
	// codePreexisting は前から在った変更の知らせ。違反と同じコードで送ると、直前の実行が壊したものと元から汚れていた
	// ものを受け取った側が見分けられない。
	codePreexisting = "POST_PREEXISTING"
	// sideBySideLabel は横で作業する指定が有効なチェックアウトで見つかった変更につける呼び名。
	sideBySideLabel = "出所不明の変更（ユーザか別のセッションの可能性）"
	// approvedArea は報告のパスに含まれていれば承認済みの置き場の案内を添える区切り。
	approvedArea = "/.ccnavi/approved/"
	unknownTool  = "(unknown tool)"
)

// outcome はこの 1 件の戻しの結果。
type outcome struct {
	restored bool   // 戻した（moved が空なら git の内容へ戻しただけ）
	moved    string // 現れたファイルを退避した先
	would    bool   // 予行。本番なら戻していた
	// notRestored は戻す働きは有効なのに、この 1 件が戻す対象ではない（ask・範囲外・受け入れた変更のうえ）。
	notRestored bool
	shadowed    bool // ユーザの発言で受け入れた変更のうえに、そのあと中身が変わったもの
	sideBySide  bool
}

// violation は 1 件を、それだけで読んで成立する差し戻しの文に組む。何にヒットしたか・どの設定が言っているか・
// 直前に何が走ったか・どう戻すか。戻していない 1 件には戻す手順を必ず載せ、戻さなかった理由も言う（手順だけだと
// 「自分で戻せ」としか読めない。ask のルールは文を持てないので、ここで言わないと誰も言わない）。
func violation(f *finding, in hookio.Input, o outcome, gitCommand, root string) string {
	change := f.change
	lines := []string{
		"[ccnavi] " + f.code + " (" + sourceOf(f.group) + ")",
		"path: " + change.Path + " (" + ospath.Strip(change.Status) + " / " + string(change.Kind) + ")",
	}
	if f.treeName != "" {
		lines = append(lines, "tree: "+f.treeName+" ("+f.treeRoot+")")
	}
	lines = append(lines, "after: "+callOf(in))
	if o.sideBySide && !o.restored {
		lines = append(lines, "side-by-side: "+texts.Say(root, "NOTICE_POST_SIDE_BY_SIDE", texts.Values{"label": sideBySideLabel}, nil))
	}
	switch {
	case !o.restored:
		if !o.sideBySide {
			// 横で作業する変更は、誰の変更かをユーザが決める。戻す手順を並べると上の文と食い違う。
			undo := git.Undo(change, gitCommand)
			if undo == "" && gitCommand == "" {
				undo = texts.Say(root, "NOTICE_POST_UNDO_ASK_USER", nil, nil)
			}
			lines = append(lines, "undo: "+undo)
		}
		if o.notRestored {
			why := "ask"
			switch {
			case o.shadowed:
				why = "shadowed"
			case f.code == CodeTicketScope:
				why = "scope"
			}
			lines = append(lines, "not-restored: "+texts.Say(root, "NOTICE_POST_NOT_RESTORED", nil, texts.Variants{"what": why}))
		}
		if o.would {
			what := "put it back to its committed content."
			if change.Kind == git.KindNew {
				what = "moved this file aside."
			}
			lines = append(lines, string(selfguard.ActionWould)+": ccnavi did not touch this path. With "+
				env.RestoreIfDeny+"=enable it would have "+what)
		}
	case o.moved != "":
		lines = append(lines, "restored: ccnavi moved this file to "+o.moved+". It was not deleted.")
	default:
		lines = append(lines, "restored: ccnavi put this path back to its committed content.")
	}
	if inApprovedArea(change.Path) {
		lines = append(lines, texts.Say(root, "NOTE_POST_APPROVED_AREA", nil, nil))
	}
	if said := messagesOf(f.group); said != "" {
		lines = append(lines, said)
	}
	return strings.Join(lines, "\n")
}

// inApprovedArea は報告のパスが承認済みの置き場の下か。
func inApprovedArea(path string) bool {
	return strings.Contains("/"+pyfmt.Lower(strings.ReplaceAll(path, `\`, "/")), approvedArea)
}

// preexisting はセッションが始まる前から在った変更の文。戻すなと書く（書き落とすと、違反と同じ手順を踏んで
// ほかのセッションやユーザの書きかけを消しにいく）。
func preexisting(f *finding, root string) string {
	change := f.change
	return strings.Join([]string{
		"[ccnavi] " + codePreexisting + " (" + sourceOf(f.group) + ")",
		"path: " + change.Path + " (" + ospath.Strip(change.Status) + " / " + string(change.Kind) + ")",
		texts.Say(root, "NOTE_POST_PREEXISTING", nil, nil),
		messagesOf(f.group),
	}, "\n")
}

// sourceOf はどの設定がこの場所を守ると言っているか（記録名）。実行前の理由と同じ名前で、直しに行く先が分かる。
func sourceOf(group []*rules.Rule) string {
	named := make([]string, len(group))
	for i, rule := range group {
		named[i] = rules.RecordName(rule)
	}
	return "rule: " + strings.Join(named, ",")
}

// messagesOf はヒットしたルールの文。同じパスに複数ヒットしたら全部載せる（1 つを選ぶと、ほかの文が誰にも届かない）。
func messagesOf(group []*rules.Rule) string {
	var out []string
	seen := map[string]bool{}
	for _, rule := range group {
		said := texts.Spoken(rule)
		if !seen[said] {
			seen[said] = true
			out = append(out, said)
		}
	}
	return strings.Join(out, "\n")
}

// callOf は直前に走った呼び出しを 1 行で。
func callOf(in hookio.Input) string {
	subject := in.Field("command")
	if subject == "" {
		subject = in.Field("file_path")
	}
	if subject == "" {
		if in.ToolName == "" {
			return unknownTool
		}
		return in.ToolName
	}
	shown := strings.Join(strings.FieldsFunc(subject, ospath.IsSpace), " ")
	if len([]rune(shown)) > texts.ShownSubjectLimit {
		shown = pyfmt.Head(shown, texts.ShownSubjectLimit) + "…"
	}
	return in.ToolName + "(" + shown + ")"
}
