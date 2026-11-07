package ops

import (
	"fmt"
	"io"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/learning"
	"ccnavi/internal/tickets/lifecycle/closing"
	"ccnavi/internal/tickets/lifecycle/plan"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// Verb は状態を動かす操作の名前（`--check` が受け、断る文の `次:` に入る）。
type Verb string

const (
	VerbStart  Verb = "start"
	VerbFinish Verb = "finish"
	VerbCancel Verb = "cancel"
)

const (
	ticketScript = "ccnavi-ticket.sh"

	textRefused       = "NEXT_OPS_REFUSED"
	textRefusedCancel = "NEXT_OPS_REFUSED__CANCEL"
)

// refused は断った理由のあとに、打ち直す 1 手を `次:` で足す。
func refused(stderr io.Writer, root string, verb Verb, id string) {
	var line string
	if verb == VerbCancel {
		line = texts.Say(root, textRefusedCancel, texts.Values{"ticket": id}, nil)
	} else {
		line = texts.Say(root, textRefused, texts.Values{"verb": string(verb), "ticket": id}, nil)
	}
	io.WriteString(stderr, line+"\n")
}

// startGate は start の、副作用の前に分かる検査。子のワークツリーは sh が先に作るので、ここでは見ない。
func startGate(stderr io.Writer, conf *config.Settings, root, id string) *ticket.Ticket {
	found := startFound(stderr, conf, root, id)
	if found == nil {
		refused(stderr, root, VerbStart, id)
	}
	return found
}

func startFound(stderr io.Writer, conf *config.Settings, root, id string) *ticket.Ticket {
	found := closing.Find(stderr, conf, root, id)
	if found == nil {
		return nil
	}
	if found.State != place.Doing {
		tail := ""
		if found.State == place.Todo {
			tail = "先にユーザに 'ccnavi-user-agree.sh' を通してもらってください"
		}
		fmt.Fprintf(stderr, "ccnavi: %s は承認済みの作業中ではない（いまは %s/）。%s\n", id, found.State, tail)
		return nil
	}
	if found.StartedAt != "" {
		fmt.Fprintf(stderr, "ccnavi: %s は着手済み（%s）\n", id, found.StartedAt)
		return nil
	}
	if closing.MisplacedHome(stderr, conf, root, found) || plan.MissingPlan(stderr, found) ||
		closing.ParentNotStarted(stderr, conf, root, found) || closing.PredecessorsUnmet(stderr, conf, root, found) {
		return nil
	}
	return found
}

// finishGate は finish の、親への合流の前に分かる検査。合流のあとでないと分からない検査（成果物の追跡・親の学びの
// 置き場）は Finish の本体が見る。
func finishGate(stderr io.Writer, conf *config.Settings, root, id string) *ticket.Ticket {
	found := finishFound(stderr, conf, root, id)
	if found == nil {
		refused(stderr, root, VerbFinish, id)
	}
	return found
}

func finishFound(stderr io.Writer, conf *config.Settings, root, id string) *ticket.Ticket {
	found := closing.Find(stderr, conf, root, id)
	if found == nil {
		return nil
	}
	if found.State != place.Doing {
		fmt.Fprintf(stderr, "ccnavi: %s は作業中ではない（いまは %s/）\n", id, found.State)
		return nil
	}
	if found.StartedAt == "" {
		fmt.Fprintf(stderr, "ccnavi: %s は未着手。先に '%s start %s' を通してください\n",
			id, config.ScriptCommand(root, ticketScript), id)
		return nil
	}
	if closing.MisplacedHome(stderr, conf, root, found) || closing.ParentStillBusy(stderr, conf, root, found) ||
		learning.RecordBlocked(stderr, conf, root, found) {
		return nil
	}
	return found
}

// cancelGate は cancel の検査。どの条件も書く前に分かる。
func cancelGate(stderr io.Writer, conf *config.Settings, root, id, reason string) *ticket.Ticket {
	found := cancelFound(stderr, conf, root, id, reason)
	if found == nil {
		refused(stderr, root, VerbCancel, id)
	}
	return found
}

func cancelFound(stderr io.Writer, conf *config.Settings, root, id, reason string) *ticket.Ticket {
	if ospath.Strip(reason) == "" {
		io.WriteString(stderr, "ccnavi: 取り消しには --reason <理由> が必要です。\n")
		return nil
	}
	found := closing.Find(stderr, conf, root, id)
	if found == nil {
		return nil
	}
	if found.State == place.Todo {
		fmt.Fprintf(stderr, "ccnavi: %s は未承認の提案（todo/）。取り消しの記録は不要なので、ファイルを削除すればよい\n", id)
		return nil
	}
	if found.State != place.Doing {
		fmt.Fprintf(stderr, "ccnavi: %s は作業中ではない（いまは %s/）\n", id, found.State)
		return nil
	}
	if closing.ParentStillBusy(stderr, conf, root, found) {
		return nil
	}
	return found
}

// Check は `ticket start|finish|cancel <識別子> --check`。副作用の前に分かる検査だけを通す（読むだけ）。sh が
// 取り込み・コミット・子のワークツリーの作成の前に呼ぶ。どの置き場にも無いチケットは判断しない（別の PC で承認・
// 着手された分は、取り込みのあとに本体が見る）。
func Check(stdout, stderr io.Writer, conf *config.Settings, root string, verb Verb, id, reason string) int {
	if closing.Absent(conf, root, id) {
		fmt.Fprintf(stdout, "案内: %s は手元で引けない。取り込みのあとに本体が確かめます\n", id)
		return 0
	}
	var ok *ticket.Ticket
	switch verb {
	case VerbStart:
		ok = startGate(stderr, conf, root, id)
	case VerbFinish:
		ok = finishGate(stderr, conf, root, id)
	default:
		ok = cancelGate(stderr, conf, root, id, reason)
	}
	if ok == nil {
		return 1
	}
	fmt.Fprintf(stdout, "確認: ok（%s を打てる状態。何も変えていません）\n", verb)
	return 0
}
