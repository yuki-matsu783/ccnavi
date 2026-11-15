// Package ticketsh は、ccnavi-ticket.sh の段取りのうち、引数の識別子の読み・子のワークツリーの用意・一度も送っていない
// 親で止めたときの次の手の文を持つ。ネットワークには出ない。答えは reply の形（1 行 1 項目）。
package ticketsh

import (
	"strings"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/reply"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/lifecycle/treeadd"
	"ccnavi/internal/tickets/model/ids"
)

// ID は、ccnavi-ticket.sh の引数（先頭はサブコマンド）から識別子を、実行ファイルの ticket の引数の読み（argparse）と同じに
// 読む。`--` と `--reason <値>` とほかのオプションを読み飛ばした最初の語。`start -- <親>` のように書いて write-and-push を
// 経ずに通らないよう、sh と実行ファイルで読み方を揃える。
func ID(args []string) string {
	skip := false
	for i, a := range args {
		switch {
		case i == 0:
		case skip:
			skip = false
		case a == "--reason":
			skip = true
		case strings.HasPrefix(a, "--") || len(a) > 1 && strings.HasPrefix(a, "-"):
		default:
			return a
		}
	}
	return ""
}

// IDAnswer は `ticket-sh id` の答え。識別子が使えれば `id <識別子>`、使えなければ `bad <サブコマンド>` と断りの文 `msg`。
func IDAnswer(args []string) string {
	var o reply.Lines
	id := ID(args)
	if ids.IsValidFamily(id) {
		o.Val("id", id)
		return o.String()
	}
	sub, shown := "", id
	if len(args) > 0 {
		sub = args[0]
	}
	if shown == "" {
		shown = "（無い）"
	}
	o.Val("bad", "yes")
	o.Val("msg", sub+" には識別子を指定してください（"+shown+" は有効な識別子の形式ではありません）。")
	return o.String()
}

// Child は子の start で、このPCに子のワークツリーが無ければ、取り込み直後の親のブランチ（親のワークツリー parentTree の
// HEAD）から .claude/worktrees/<子> に作る。別のPCで承認された子でも、どのPCでも同じ場所に作る。子のブランチが手元に
// 残っていれば、親の先頭が含むものだけ親の先頭に揃えて使う（含まない = 手元だけに積んだ仕事があるので、消さずに止める）。
// ここで止めるのは git の状態を見た判定で、ccnavi の状態（チケット・マーカー）を読んで操作を止める判定ではない。
// 答えは、既にあれば `have <パス>`、作れば `made <親>`、標準エラーに出す行（err）と、止めたときの `fail`。
func Child(root, child, parentTree, parentBranch, family string) string {
	var o reply.Lines
	dest := tree.WorktreePath(root, child)
	if ospath.DiskLexists(dest) {
		o.Val("have", dest)
		return o.String()
	}
	from := "HEAD"
	if git.Run(parentTree, []string{"show-ref", "--verify", "--quiet", "refs/heads/" + child}, git.Options{}).OK() {
		if !git.Run(parentTree, []string{"merge-base", "--is-ancestor", "refs/heads/" + child, "HEAD"}, git.Options{}).OK() {
			o.Err("ccnavi-ticket: 手元のブランチ " + child + " に親のブランチ " + parentBranch + " に無いコミットがある。子のワークツリーを作らずに止めた。内容を確かめて、親へ取り込むか、ユーザが消してから再実行してください")
			o.Fail("child-branch-ahead", 1, "")
			return o.String()
		}
		// 親の先頭が含む子のブランチだけを動かすので、手元だけのコミットは失わない。worktree add -B はラッパーが断る形
		// なので、先に揃えてから既にあるブランチを出す形にする。
		if !git.Run(parentTree, []string{"branch", "-f", child, "HEAD"}, git.Options{}).OK() {
			o.Err("ccnavi-ticket: 手元のブランチ " + child + " を親のブランチの先頭に揃えられなかった")
			o.Fail("child-branch-reset", 1, "")
			return o.String()
		}
		from = ""
	}
	if said, ok := treeadd.Add(root, parentTree, dest, child, from); !ok {
		first, _, _ := strings.Cut(said, "\n")
		o.Err("ccnavi-ticket: 子のワークツリー " + dest + " を作れなかった（" + first + "）")
		o.Fail("child-worktree-add", 1, "")
		return o.String()
	}
	o.Err("ccnavi-ticket: 子のワークツリー .claude/worktrees/" + child + " を親のブランチ " + parentBranch + " の先頭から作った")
	o.Val("made", family)
	return o.String()
}

// UnsentNext は、親のブランチが一度も送られていないために止まったときの次の手（共通の段取りは事実だけを言う）。
// 次の手は、手で切った親が最初に打つ approval-request・start のためのもので、ほかの操作には出さない。MR の有無は
// ホストの事柄でエージェントは調べられないので、決めるのはユーザ。mr は親チケットの mr 欄（無ければ空）。
func UnsentNext(root, op, parentBranch, mr string) string {
	if op != "start" && op != "approval-request" {
		return ""
	}
	if mr == "" {
		mr = "<MR の番号>"
	}
	b := parentBranch
	start := shell.SingleQuote(ospath.Join(root, ".ccnavi", "scripts", "ccnavi-start.sh"))
	return strings.Join([]string{
		"次の手: ユーザに状況を伝えて、どれにするか聞いて返事を待つ。直接実装には切り替えない",
		"  1. issue に紐づく MR がすでにある: その MR のブランチで作業するかを聞く。作業するなら sh " + start + " --mr " + mr + "（--issue <番号> と一緒に渡せる）",
		"  2. MR は無く、issue はある: " + b + " を使わず sh " + start + " --issue <番号> で新しく切る（ブランチ・ワークツリー・Draft MR を作る）。提案は新しい親のワークツリーへ移す。" + b + " にコミットがあれば先に伝える",
		"  3. " + b + " を使い続ける: ユーザが git push -u origin " + b + " と MR（説明欄に Closes #<番号>）の作成をしてから、sh " + start + " --mr " + mr + " で採用する",
		texts.Say(root, "NOTE_TICKET_NEW_ISSUE", nil, nil),
	}, "\n")
}
