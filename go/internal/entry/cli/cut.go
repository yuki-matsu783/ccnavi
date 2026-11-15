package cli

import (
	"fmt"
	"io"
	"os"

	"ccnavi/internal/entry/shask"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/lifecycle/cut"
)

// cutUsage は `worktree direct` の使い方。
const cutUsage = "ccnavi: worktree direct plan -- <ccnavi-worktree.sh の引数>… か worktree direct cut fetch=<ok|failed> fetch-out=<ファイル> heads=<ファイル> heads-err=<ファイル> -- <ccnavi-worktree.sh の引数>…\n"

// directCut は `worktree direct plan|cut …`（ccnavi-worktree.sh 用）。答えは 1 行 1 項目（reply の形）。cut の 4 語は sh が
// 取ってきた fetch と ls-remote の結果で、heads が空なら ls-remote は通らなかった（fetch が通らなければ打たない）。
// 引数は ccnavi-worktree.sh が受けたまま（`--` の後ろ）。
func directCut(stdout, stderr io.Writer, conf *config.Settings, root, cwd string, words []string) int {
	in := cut.Input{Root: root, Cwd: cwd, Conf: conf, Leftover: func(path, name string) cut.Leftover {
		f := shask.Leftover(root, conf, path, name)
		return cut.Leftover{Leftover: f.Leftover, Proposal: f.Proposal, Doing: f.Doing, MR: f.MR, Stage: f.Stage}
	}}
	switch {
	case len(words) >= 1 && words[0] == "plan":
		in.Args = words[1:]
		io.WriteString(stdout, cut.Plan(in))
		return config.ExitOK
	case len(words) >= 5 && words[0] == "cut":
		kv, valid := pairs(words[1:5], "fetch", "fetch-out", "heads", "heads-err")
		if !valid {
			break
		}
		read := func(path string) (string, bool) {
			if path == "" {
				return "", true
			}
			text, err := os.ReadFile(path)
			if err != nil {
				fmt.Fprintf(stderr, "ccnavi: worktree direct cut の %s を読めない\n", pyfmt.Repr(path))
				return "", false
			}
			return string(text), true
		}
		remote := cut.Remote{FetchOK: kv["fetch"] == "ok", HeadsOK: kv["heads"] != ""}
		var got [3]bool
		remote.FetchOut, got[0] = read(kv["fetch-out"])
		remote.Heads, got[1] = read(kv["heads"])
		remote.HeadsErr, got[2] = read(kv["heads-err"])
		if !got[0] || !got[1] || !got[2] {
			return config.ExitError
		}
		in.Args = words[5:]
		io.WriteString(stdout, cut.Cut(in, remote))
		return config.ExitOK
	}
	io.WriteString(stderr, cutUsage)
	return config.ExitError
}
