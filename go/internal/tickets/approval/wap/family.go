package wap

import (
	"fmt"
	"io"
	"path/filepath"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
)

// Head は答えの頭の行。sh はこれが無ければ「実行ファイルが write-and-push に答えなかった」と読んで止める。
const Head = "write-and-push 1"

// Target は write-and-push の対象か。
type Target string

const (
	Yes  Target = "yes"
	No   Target = "no"
	Stop Target = "stop"
)

// FamilyOf は識別子が属する親子のチケットの親。子の形なら右から 2 階層を剥がした親、そうでなければ自身。
func FamilyOf(id string) string {
	if c, ok := ids.ParseChild(id); ok {
		return c.Parent
	}
	return id
}

// places は置き場のパス（承認済み、レビュー待ち。ツリーからの相対で "/" 区切り）。どちらかが絶対パスなら偽
// （ブランチに乗らないので write-and-push の対象にしない）。
type places struct{ approved, review string }

func relativePlaces(conf *config.Settings) (places, bool) {
	for _, v := range []string{conf.Approved, conf.Tickets} {
		if v != "" && (ospath.IsAbs(v) || ospath.HasDrive(v, true)) {
			return places{}, false
		}
	}
	approved, tickets := conf.Approved, conf.Tickets
	if approved == "" {
		approved = config.DefaultApproved
	}
	if tickets == "" {
		tickets = config.DefaultTickets
	}
	tickets = strings.TrimRight(ospath.Slashed(tickets), "/")
	return places{
		approved: strings.Trim(ospath.Slashed(approved), "/"),
		review:   strings.Trim(tickets, "/") + "/" + string(place.Review),
	}, true
}

// TargetOf は write-and-push の対象かと、その理由と立ち位置。
func TargetOf(conf *config.Settings, root, parent string) (Target, string, standing.Standing) {
	st := standing.NewFamilies(conf, root).StandingAny(parent)
	if _, ok := relativePlaces(conf); !ok {
		return No, "置き場のパスが絶対パス（write-and-push と Chrome の対象外）", st
	}
	if st.Stop != "" || st.Home == nil {
		why := st.Stop
		if why == "" {
			why = "親のワークツリーが決まらない"
		}
		return Stop, why, st
	}
	origin := git.Run(st.Home.Root, []string{"config", "--get", "remote.origin.url"}, git.Options{Timeout: git.MergeTimeout})
	if !origin.OK() || ospath.Strip(origin.Out) == "" {
		return No, "origin がありません（今の手元の動きのまま）", st
	}
	if strings.ContainsAny(st.Home.Root, "\r\n") {
		return Stop, "親のワークツリーのパスに改行がある", st
	}
	return Yes, "", st
}

// Family は `ccnavi write-and-push family <識別子>`。親子のチケットと、write-and-push の対象か。終了コードを返す。
func Family(stdout io.Writer, conf *config.Settings, root, id string) int {
	parent := FamilyOf(id)
	type line struct{ key, value string }
	lines := []line{{"family", parent}}
	where, relative := relativePlaces(conf)
	verdict, why, st := TargetOf(conf, root, parent)
	lines = append(lines, line{"repo", st.Repo})
	// 親のブランチ名。sh は識別子からブランチ名を組み立てず、これを使う（ref・fetch・push・ls-remote）。
	// 読めない・合わないときは branch_refused で理由を言う（sh はその親子のチケットを動かさずに止める）。
	if refused := branchRefusal(parent, st.Branch); refused != "" {
		lines = append(lines, line{"branch_refused", refused})
		if verdict == Yes {
			verdict, why = Stop, refused
		}
	} else {
		lines = append(lines, line{"branch", st.Branch})
	}
	if relative {
		lines = append(lines, line{"approved", where.approved}, line{"review", where.review})
	}
	lines = append(lines, line{"target", string(verdict)})
	if why != "" {
		lines = append(lines, line{"why", why})
	}
	if verdict == Stop {
		for _, hint := range standing.Guidance(root, st) {
			lines = append(lines, line{"hint", hint})
		}
	}
	fmt.Fprintln(stdout, Head)
	for _, l := range lines {
		fmt.Fprintf(stdout, "%s %s\n", l.key, oneLine(l.value))
	}
	// パスは空白をまとめずにそのまま出す（改行を含むパスは TargetOf で止めてある）。
	if verdict == Yes && st.Home != nil {
		fmt.Fprintf(stdout, "tree %s\n", st.Home.Root)
	}
	return config.ExitOK
}

// oneLine は空白の並びを 1 つの空白にし、前後の空白を落とす。
func oneLine(value string) string {
	return ospath.Squeeze(value)
}

// branchRefusal は HEAD から読んだ親のブランチ名を使えない理由（使えれば空）。
func branchRefusal(parent, branch string) string {
	if branch == "" {
		return "親のワークツリー（.claude/worktrees/" + parent + "）の HEAD から親のブランチ名を読めない" +
			"（ブランチ名の `/` を `-` に置き換えたものが " + parent + " になるブランチに居ること）"
	}
	if why := ids.BranchProblem(branch); why != "" {
		return "親のブランチ " + branch + " は使えない（" + why + "）"
	}
	return ""
}

// literalSpec は git の pathspec を字義どおりに読ませる形。
func literalSpec(p string) string { return ":(literal)" + p }

// onDisk はツリーからの相対（"/" 区切り）をディスクのパスにする。
func onDisk(treeRoot, rel string) string { return ospath.Join(treeRoot, filepath.FromSlash(rel)) }
