package cli

import (
	"fmt"
	"io"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/records/stopreview"
	"ccnavi/internal/tickets/lifecycle/ticketsh"
)

// runTicketSh は `ticket-sh <動詞> …`（ccnavi-ticket.sh の段取り用）。答えは 1 行 1 項目（reply の形）。
//
//   - `id <ccnavi-ticket.sh の引数>…`: 識別子（`id <識別子>`。使えなければ `bad yes` と `msg <文>`）
//   - `child <子> <親のワークツリー> <親のブランチ> <親>`: 子のワークツリーを用意する（ticketsh.Child）
//   - `unsent <操作> <親のブランチ> <mr|->`: 一度も送っていない親で止めたときの次の手の文（`err` の行）
//   - `result-begin`・`result-end <結果ファイル> <終了コード> <始めた時刻> <ccnavi-ticket.sh の引数>…`: approval-request の
//     結果ファイル（stopreview.BeginResult・EndResult）
func runTicketSh(stdout, stderr io.Writer, root string, conf *config.Settings, args []string) int {
	usage := func() int {
		fmt.Fprintf(stderr, "ccnavi: ticket-sh の引数が読めない（%s）\n", pyfmt.Repr(args))
		return config.ExitError
	}
	if len(args) == 0 {
		return usage()
	}
	switch op, rest := args[0], args[1:]; {
	case op == "id":
		io.WriteString(stdout, ticketsh.IDAnswer(rest))
	case op == "child" && len(rest) == 4:
		io.WriteString(stdout, ticketsh.Child(root, rest[0], rest[1], rest[2], rest[3]))
	case op == "unsent" && len(rest) == 3:
		mr := rest[2]
		if mr == "-" {
			mr = ""
		}
		if text := ticketsh.UnsentNext(root, rest[0], rest[1], mr); text != "" {
			for _, line := range strings.Split(text, "\n") {
				fmt.Fprintf(stdout, "err %s\n", line)
			}
		}
	case op == "result-begin" && len(rest) == 0:
		path, started, ok := stopreview.BeginResult(conf.State, time.Now())
		if !ok {
			return config.ExitError
		}
		fmt.Fprintf(stdout, "file %s\nstarted %d\n", path, started)
	case op == "result-end" && len(rest) >= 3:
		rc, err1 := strconv.Atoi(rest[1])
		started, err2 := strconv.ParseInt(rest[2], 10, 64)
		if err1 != nil || err2 != nil {
			return usage()
		}
		stopreview.EndResult(rest[0], ticketsh.ID(rest[3:]), rc, started, time.Now())
	default:
		return usage()
	}
	return config.ExitOK
}
