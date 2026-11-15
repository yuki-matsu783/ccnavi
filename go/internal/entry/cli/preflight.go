package cli

import (
	"fmt"
	"io"
	"os"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/lifecycle/preflight"
)

// runPreflight は `ccnavi preflight <問い> …`（ccnavi-common-host.sh の ccnavi_preflight と ccnavi_host_access 用）。sh が
// ネットワークから取ってきた事実を受け、事前確認の判定と文を 1 行 1 項目で答える。
//
//   - plan <名前>=<値>…: 手元の項目から、ホストの API とリモートの git を確かめに行くか（`api yes|no`・`git yes|no`）
//   - check <名前>=<値>…: 項目ごとの結果（`item <項目> <結果>`・`line <項目> <行>`）、`result`・`fix`・`auth`・
//     `access-unknown yes`、確かめきれなくても続けてよいなら `continue yes`、勧める対処の `next <行>`
//   - next <名前>=<値>…: 勧める対処（fix=）の `次:` の行
//   - access github|gitlab <プロジェクトの応答> [<member の応答>]: 書き込みの権限（`access ok|ng|unknown` と `why <理由>`）
//   - uid <user の応答>: GitLab の自分の id（`uid <id>`。読めなければ何も出さない）
func runPreflight(stdout, stderr io.Writer, root string, args []string) int {
	usage := func() int {
		fmt.Fprintf(stderr, "ccnavi: preflight の引数が読めない（%s）\n", pyfmt.Repr(args))
		return config.ExitError
	}
	if len(args) == 0 {
		return usage()
	}
	var out []string
	say := func(key, v string) {
		for _, line := range strings.Split(v, "\n") {
			out = append(out, key+" "+line)
		}
	}
	switch op, rest := args[0], args[1:]; op {
	case "plan", "check", "next":
		kv, valid := pairs(rest, "top", "parse", "parse-why", "masked", "kind", "host", "path", "cli", "token", "connect",
			"transport", "probe", "access", "access-why", "git", "git-err", "fix")
		if !valid {
			return usage()
		}
		f := preflight.Facts{Top: kv["top"], Root: root, Parse: kv["parse"], ParseWhy: kv["parse-why"], Masked: kv["masked"],
			Kind: kv["kind"], Host: kv["host"], Path: kv["path"], CLI: kv["cli"], Token: kv["token"], Connect: kv["connect"],
			Transport: kv["transport"], Probe: kv["probe"], Access: kv["access"], AccessWhy: kv["access-why"], Git: kv["git"],
			GitErr: kv["git-err"]}
		switch op {
		case "plan":
			api, remote := preflight.Plan(f)
			say("api", yesNo(api))
			say("git", yesNo(remote))
		case "next":
			if next := preflight.Next(root, f.Top, kv["fix"], f.Kind, f.Host, f.Path); next != "" {
				say("next", next)
			}
		default:
			r := preflight.Check(f)
			for _, item := range preflight.Items {
				say("item", item+" "+r.Kind[item])
				say("line", item+" "+r.Line(item))
			}
			say("result", r.Total)
			for _, kv := range [][2]string{{"fix", r.Fix}, {"auth", r.Auth}} {
				if kv[1] != "" {
					say(kv[0], kv[1])
				}
			}
			if r.AccessUnknown {
				say("access-unknown", "yes")
			}
			if r.Continue() {
				say("continue", "yes")
			}
			if next := preflight.Next(root, f.Top, r.Fix, f.Kind, f.Host, f.Path); next != "" {
				say("next", next)
			}
		}
	case "access":
		if len(rest) < 2 || len(rest) > 3 || rest[0] != "github" && rest[0] != "gitlab" {
			return usage()
		}
		project, err := os.ReadFile(rest[1])
		if err != nil {
			return usage()
		}
		var member []byte
		if len(rest) == 3 {
			member, _ = os.ReadFile(rest[2])
		}
		state, why := preflight.Access(rest[0], project, member)
		say("access", state)
		if why != "" {
			say("why", why)
		}
	case "uid":
		if len(rest) != 1 {
			return usage()
		}
		user, _ := os.ReadFile(rest[0])
		if id := preflight.UserID(user); id != "" {
			say("uid", id)
		}
	default:
		return usage()
	}
	if len(out) > 0 {
		fmt.Fprintln(stdout, strings.Join(out, "\n"))
	}
	return config.ExitOK
}

func yesNo(b bool) string {
	if b {
		return "yes"
	}
	return "no"
}
