package cli

import (
	"fmt"
	"io"
	"os"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/lifecycle/start"
)

// runStart は `ccnavi start <読み方> <引数>… <応答のファイル>…`（ccnavi-start.sh 用）。sh が取ってきたホストの応答を読み、
// 1 行 1 項目で答える。応答を読めないときは何も出さずに 1。
func runStart(stdout, stderr io.Writer, args []string) int {
	usage := func() int {
		fmt.Fprintf(stderr, "ccnavi: start の引数が読めない（%s）\n", pyfmt.Repr(args))
		return config.ExitError
	}
	if len(args) < 2 {
		return usage()
	}
	host := func(s string) bool { return s == "github" || s == "gitlab" }
	var files []string
	var answer func(in [][]byte) (string, int)
	switch op, rest := args[0], args[1:]; {
	case op == "issue" && len(rest) == 2 && host(rest[0]):
		files, answer = rest[1:], func(in [][]byte) (string, int) { return start.Issue(rest[0], in[0]) }
	case op == "linked-page" && len(rest) == 1:
		files, answer = rest, func(in [][]byte) (string, int) { return start.LinkedPage(in[0]) }
	case op == "linked-body" && len(rest) == 2:
		files, answer = rest[1:], func(in [][]byte) (string, int) { return start.LinkedBody(rest[0], in[0]) }
	case op == "linked-gitlab" && len(rest) == 2:
		files, answer = rest[1:], func(in [][]byte) (string, int) { return start.LinkedGitLab(rest[0], in[0]) }
	case op == "similar" && len(rest) >= 4 && host(rest[0]) && (rest[1] == "any" || rest[1] == "checked"):
		files, answer = rest[3:], func(in [][]byte) (string, int) { return start.Similar(rest[0], rest[1], rest[2], in) }
	case op == "list-open" && len(rest) == 3 && host(rest[0]):
		files, answer = rest[1:], func(in [][]byte) (string, int) { return start.ListOpen(rest[0], in[0], in[1]) }
	case op == "pick" && (len(rest) == 3 || len(rest) == 4) && host(rest[1]):
		pid := ""
		if len(rest) == 4 {
			pid = rest[2]
		}
		files, answer = rest[len(rest)-1:], func(in [][]byte) (string, int) { return start.Pick(rest[0], rest[1], pid, in[0]) }
	default:
		return usage()
	}
	in := make([][]byte, len(files))
	for i, f := range files {
		data, err := os.ReadFile(f)
		if err != nil {
			fmt.Fprintf(stderr, "ccnavi: 応答のファイルを読めない（%s）\n", err)
			return config.ExitError
		}
		in[i] = data
	}
	out, code := answer(in)
	fmt.Fprint(stdout, out)
	return code
}
