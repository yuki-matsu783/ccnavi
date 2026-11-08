package scaffold

import (
	"fmt"
	"io"
	"path/filepath"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
)

// parentWorktree は cwd が親 ident のワークツリー（識別子と同名で、HEAD が親のブランチ）の中か。中なら
// (ツリー, true, "")、外なら理由。提案を書く入口（ticket new・ticket revise）が同じ見方をする。
func parentWorktree(conf *config.Settings, root, cwd, ident string) (tree.Tree, string) {
	where, ok := tree.TreeOf(root, cwd, conf.Projects)
	if !ok || where.Kind != tree.KindWorktree || pyfmt.NFC(where.Name) != ident {
		start := config.ScriptCommand(root, config.StartWrapper)
		return tree.Tree{}, "cwd が .claude/worktrees/" + ident + " の中ではない。" +
			"提案は親のワークツリー（識別子と同名）に書く。" +
			"先に '" + start + " --mr <MR の番号>'（MR が無ければ '--issue <issue の番号>'）で、" +
			"親のブランチを直接チェックアウトした親のワークツリーを作り、その中から実行します"
	}
	if standing.HeadBranch(where.Root, ident) == "" {
		return tree.Tree{}, "親のワークツリー（.claude/worktrees/" + ident + "）の HEAD のブランチ名の `/` を `-` に" +
			"置き換えたものが " + ident + " になっていない。親のブランチ名は MR のブランチ" +
			"（`ccnavi-start.sh` が出す）で、ワークツリーはそのブランチを直接チェックアウトする"
	}
	return where, ""
}

// identProblem は識別子が親の提案に使えない理由（共通の前半）。子の形は op ごとに文が違うので呼び手が言う。
func identProblem(ident string) string {
	if why := ids.IDProblem(ident); why != "" {
		return "識別子 " + pyfmt.Repr(ident) + " が指定できない（" + why + "）"
	}
	return ""
}

func isChild(ident string) bool {
	_, ok := ids.ParseChild(ident)
	return ok
}

// proposalPath はツリーの提案の置き場の <状態>/<識別子>.md。
func proposalPath(conf *config.Settings, top string, state place.State, ident string) string {
	return ospath.Join(ticketsDir(conf, top), string(state), ident+config.MdExt)
}

func ticketsDir(conf *config.Settings, top string) string {
	tickets := conf.Tickets
	if tickets == "" {
		tickets = config.DefaultTickets
	}
	return ospath.Join(top, filepath.FromSlash(tickets))
}

// writeNew は親ディレクトリを作って新規に書く（在れば書かない）。書けなければ理由の文。
func writeNew(path string, content []byte, what string) string {
	dir := ospath.Dirname(path)
	if err := fsio.MakeDirs(dir); err != nil {
		return fmt.Sprintf("%s を作れない（%s）", dir, fsio.ErrorText(err))
	}
	if failed := fsio.WriteFile(path, content, fsio.NewDurable); failed != "" {
		return fmt.Sprintf("%s に保存できませんでした（%s）", what, failed)
	}
	return ""
}

// refuser は「ccnavi: ticket <op>: 理由。何も反映しません。」を標準エラーに書いて 1 を返す。
func refuser(stderr io.Writer, op string) func(string) int {
	return func(why string) int {
		fmt.Fprintf(stderr, "ccnavi: ticket %s: %s。何も反映しません。\n", op, why)
		return config.ExitError
	}
}
