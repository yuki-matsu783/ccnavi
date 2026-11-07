package scaffold

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/fields"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

// feedbackNone は「フィードバック計画は見たうえで対応なし」の行。
const feedbackNone = "feedback: []"

// stripped は承認済みチケットのコピーから、改版に残さない欄（スクリプトだけが書く欄と続きの子の目印）を外した本文。
func stripped(text string) string {
	drop := append(append([]string{}, ticket.ScriptFields...), approval.FollowupKey)
	if kept, ok := fields.ScriptShape(text, drop); ok {
		text = kept
	}
	return strings.TrimRight(text, "\n") + "\n"
}

// withFeedbackNone は frontmatter の閉じの前に feedback: [] を足す。閉じが見つからなければ偽。
func withFeedbackNone(text string) (string, bool) {
	lines := ospath.Splitlines(text)
	for i := 1; i < len(lines); i++ {
		if ospath.Strip(lines[i]) == ticket.Fence {
			out := append(append(append([]string{}, lines[:i]...), feedbackNone), lines[i:]...)
			return strings.Join(out, "\n") + "\n", true
		}
	}
	return "", false
}

// Revise は ticket revise <識別子> [--feedback] の本体。承認済みの親（doing/）をコピーした改版の雛形を、同じ識別子で
// todo/ に書く。書けたら 0、書かなければ 1（理由は標準エラー）。既にある提案は上書きしない。feedback は
// feedback: []（対応なし）の改版で、全体計画のフェーズが全部閉じてレビューが済んだ局面でだけ作れる。
func Revise(stdout, stderr io.Writer, conf *config.Settings, root, cwd, ident string, feedback bool) int {
	refuse := refuser(stderr, "revise")
	if why := identProblem(ident); why != "" {
		return refuse(why)
	}
	if isChild(ident) {
		return refuse(ident + " は子の識別子の形。改版できるのは親だけ")
	}
	where, why := parentWorktree(conf, root, cwd, ident)
	if why != "" {
		return refuse(why)
	}
	source := ospath.Join(conf.ApprovedDir(where.Root), string(place.Doing), ident+".md")
	text, err := ospath.ReadDiskText(source)
	if err != nil {
		return refuse("承認済みの親が " + source + " に無い。承認前の提案は、改版ではなく提案そのものを直す")
	}
	path := proposalPath(conf, where.Root, place.Todo, ident)
	if ospath.DiskLexists(path) {
		return refuse(path + " が既にある。書きかけの改版は上書きしないので、それを直す")
	}
	if feedback {
		copies, _ := approval.Scan(conf, root, false, nil)
		current := approval.ByID(copies)[ident]
		if current == nil {
			return refuse(ident + " が承認済みチケットとして読めない")
		}
		if current.FeedbackPlanned {
			return refuse("フィードバック計画は 1 回だけ作れる。この親には既にある")
		}
		if stage := phase.Stage(conf, root, current, nil); stage != phase.StageFeedbackWaiting {
			return refuse("全体計画のフェーズが全部閉じてレビューが済んでから出す（今の局面: " + stage + "）")
		}
	}
	body := stripped(text)
	if feedback {
		added, ok := withFeedbackNone(body)
		if !ok {
			return refuse(source + " の frontmatter の閉じ（---）が見つからない。承認済みの親を確かめる")
		}
		body = added
	}
	if failed := writeNew(path, []byte(body), path); failed != "" {
		return refuse(failed)
	}
	fmt.Fprintf(stdout, "%s の改版の雛形を %s に書いた（承認済みチケットのコピー。スクリプトだけが書く欄は外した）\n", ident, path)
	which := "edit"
	if feedback {
		which = "feedback"
		fmt.Fprint(stdout, "フィードバック計画は `feedback: []`（対応なし）にした\n")
		// 対応のフェーズを並べる場合は、確かめる前に直す。先に言ってから次の一手を言う
		fmt.Fprintln(stdout, texts.Say(root, "NOTE_REVISE_FEEDBACK", texts.Values{"path": path}, nil))
	}
	fmt.Fprintln(stdout, texts.Say(root, "NEXT_REVISE", texts.Values{"ident": ident, "path": path},
		texts.Variants{"next": which, "note": which}))
	return config.ExitOK
}
