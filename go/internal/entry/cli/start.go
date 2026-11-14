package cli

import (
	"fmt"
	"io"
	"os"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/lifecycle/start"
	"ccnavi/internal/tickets/proposal/templates"
)

// runStart は `ccnavi start <読み方> <引数>… <応答のファイル>…`（ccnavi-start.sh 用）。sh が取ってきたホストの応答を読み、
// 1 行 1 項目で答える。応答を読めないときは何も出さずに 1。
func runStart(stdout, stderr io.Writer, root string, conf *config.Settings, args []string) int {
	usage := func() int {
		fmt.Fprintf(stderr, "ccnavi: start の引数が読めない（%s）\n", pyfmt.Repr(args))
		return config.ExitError
	}
	if len(args) == 0 {
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
	case op == "guidance":
		ga, valid := guidanceArgs(rest)
		if !valid {
			return usage()
		}
		files, answer = nil, func([][]byte) (string, int) { return start.Guidance(root, ga) }
	case op == "args":
		wd, _ := os.Getwd()
		files, answer = nil, func([][]byte) (string, int) { return start.Args(wd, root, rest) }
	case op == "branch-ok" && len(rest) >= 2 && (rest[1] == "strict" || rest[1] == "lenient"):
		kv, valid := pairs(rest[2:], "heads", "err", "hint", "exit")
		if !valid {
			return usage()
		}
		code, _ := strconv.Atoi(kv["exit"])
		wd, _ := ospath.Getwd()
		files, answer = nil, func([][]byte) (string, int) {
			return start.BranchOK(wd, start.BranchArgs{Branch: rest[0], Strict: rest[1] == "strict", Heads: kv["heads"],
				HeadsErr: kv["err"], Hint: kv["hint"], IDExit: code})
		}
	case op == "merged" && len(rest) == 3 && host(rest[0]):
		files, answer = rest[2:], func(in [][]byte) (string, int) { return start.Merged(rest[0], rest[1], in[0]) }
	case op == "resolve" && len(rest) >= 1:
		kv, valid := pairs(rest[1:], "issue", "issue-n", "issue-r", "own", "mr", "orig")
		if !valid {
			return usage()
		}
		ra := start.ResolveArgs{Trust: rest[0] == "-", IssueArg: kv["issue"], IssueN: kv["issue-n"], IssueR: kv["issue-r"],
			Own: kv["own"], MR: kv["mr"], Orig: kv["orig"]}
		if !ra.Trust {
			files = rest[:1]
		}
		answer = func(in [][]byte) (string, int) {
			if len(in) > 0 {
				ra.Linked = string(in[0])
			}
			return start.Resolve(root, ra)
		}
	case op == "issue-state":
		kv, valid := pairs(rest, "state", "number", "real", "reopened")
		if !valid {
			return usage()
		}
		files, answer = nil, func([][]byte) (string, int) {
			return start.IssueState(root, kv["state"], kv["number"], kv["real"], kv["reopened"] != "")
		}
	case op == "prepare" && len(rest) >= 1:
		kv, valid := pairs(rest[1:], "from", "ff", "initial", "integration", "number", "real")
		if !valid {
			return usage()
		}
		wd, _ := ospath.Getwd()
		files, answer = nil, func([][]byte) (string, int) {
			return start.Prepare(root, wd, start.PrepareArgs{Branch: rest[0], From: kv["from"], FastForward: kv["ff"] != "",
				Initial: kv["initial"] != "", Integration: kv["integration"], Number: kv["number"], Real: kv["real"]})
		}
	case op == "report":
		kv, valid := pairs(rest, "top", "repo", "wt", "branch", "mr-url", "mr", "ff", "remote", "sync-note", "issue", "title",
			"sync-next", "host-unseen", "sync-no-ticket", "sync-ok")
		if !valid {
			return usage()
		}
		files, answer = nil, func([][]byte) (string, int) { return start.Report(root, reportArgs(root, conf, kv)) }
	case op == "dry" && len(rest) >= 1:
		kv, valid := pairs(rest[1:], "title", "branch", "integration", "mr-title", "number", "issue", "host-why", "mr-url", "real", "resume")
		if !valid {
			return usage()
		}
		wd, _ := ospath.Getwd()
		files, answer = nil, func([][]byte) (string, int) {
			return start.DryRun(root, wd, start.DryArgs{Kind: rest[0], Title: kv["title"], Branch: kv["branch"],
				Integration: kv["integration"], MRTitle: kv["mr-title"], Number: kv["number"], Issue: kv["issue"],
				HostWhy: kv["host-why"], MRURL: kv["mr-url"], Real: kv["real"], Resume: kv["resume"] != ""})
		}
	case op == "route" && len(rest) >= 1:
		ra, valid := routeArgs(rest[1:])
		if !valid {
			return usage()
		}
		wd, _ := ospath.Getwd()
		files, answer = rest[:1], func(in [][]byte) (string, int) { return start.Route(wd, in[0], ra) }
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

// routeArgs は `ccnavi start route` の <名前>=<値> を読む。知らない名前があれば偽。
func routeArgs(pairs []string) (start.RouteArgs, bool) {
	var a start.RouteArgs
	for _, p := range pairs {
		k, v, found := strings.Cut(p, "=")
		if !found {
			return a, false
		}
		switch k {
		case "what":
			a.What = v
		case "number":
			a.Number = v
		case "issue":
			a.HasIssue = v != ""
		case "mr":
			a.HasMR = v != ""
		case "branch":
			a.Branch = v
		case "mcp":
			a.MCPChecked = v != ""
		case "integration":
			a.Integration = v
		default:
			return a, false
		}
	}
	return a, a.What == "issue" || a.What == "mr"
}

// guidanceArgs は `ccnavi start guidance` の <名前>=<値> を読む。知らない名前があるか、段階が無ければ偽。
func guidanceArgs(pairs []string) (start.GuidanceArgs, bool) {
	var a start.GuidanceArgs
	fields := map[string]*string{"stage": &a.Stage, "why": &a.Why, "kind": &a.Kind, "path": &a.Path, "host": &a.Host,
		"what": &a.What, "number": &a.Number, "issue": &a.Issue, "mr": &a.MR, "branch": &a.Branch,
		"stop-branch": &a.StopBranch, "stop-mr": &a.StopMR, "stop-target": &a.StopTarget, "stop-title": &a.StopTitle,
		"stop-wt": &a.StopWT, "orig": &a.Orig, "total": &a.Total}
	for _, p := range pairs {
		k, v, found := strings.Cut(p, "=")
		field, known := fields[k]
		if !found || !known {
			return a, false
		}
		*field = v
	}
	return a, a.Stage != ""
}

// pairs は <名前>=<値> の並びを読む。known に無い名前があれば偽。
func pairs(words []string, known ...string) (map[string]string, bool) {
	kv := map[string]string{}
	for _, w := range words {
		k, v, found := strings.Cut(w, "=")
		if !found || !slices.Contains(known, k) {
			return nil, false
		}
		kv[k] = v
	}
	return kv, true
}

// reportArgs は `ccnavi start report` の値に、プロジェクトの名前と、提案に使えるテンプレートの数を足す。
func reportArgs(root string, conf *config.Settings, kv map[string]string) start.ReportArgs {
	here, _ := tree.TreeOf(root, ospath.Abspath(kv["top"]), integrate.ProjectsDir(conf, root))
	layer := config.LayerSelf
	if here.Project != "" {
		layer = here.Project
	}
	count := -1
	if slices.Contains(layerNames(conf, root), layer) {
		count = templates.UsableCount(conf, root, templates.Load(conf, root), layer)
	}
	return start.ReportArgs{Repo: kv["repo"], Project: here.Project, WT: kv["wt"], Branch: kv["branch"], MRURL: kv["mr-url"],
		MRNumber: kv["mr"], FF: kv["ff"], Remote: kv["remote"], SyncNote: kv["sync-note"], Issue: kv["issue"], Title: kv["title"],
		SyncNext: kv["sync-next"], HostUnseen: kv["host-unseen"] != "", SyncNoTicket: kv["sync-no-ticket"] != "",
		SyncOK: kv["sync-ok"] != "", Templates: count}
}
