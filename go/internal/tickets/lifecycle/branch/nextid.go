package branch

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"slices"
	"strconv"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/ticket"
)

// askUser は数えきれなかったときに添える一言。
const askUser = "番号を決められないので、ユーザに確かめてください"

// 終了コード（ticket next-id）。
const (
	exitBadForm   = 1
	exitUncounted = 2
)

// Known は読み込み済みのチケットと提案（SerialNames が置き場を読み直さないために呼び手が渡す）。
type Known struct {
	Copies, Closed, Review, Proposals []*ticket.Ticket
}

// SerialNames は通し番号に数える名前と、数えきれなかった理由。数えるのは今の親チケット・提案（承認待ち・作業中・
// レビュー待ち・閉じたもの）、ワークスペースと各プロジェクトの手元のブランチ名（識別子の形に直す）、
// `.claude/worktrees/` の直下のディレクトリ名。origin の追跡ブランチは数えない（直接作業のブランチは push しない）。
// どこかのリポジトリのブランチを数えられなければ理由を返す（番号を決めてはいけない）。known が nil なら読む。
func SerialNames(conf *config.Settings, root string, known *Known) ([]string, []string) {
	var problems []string
	if known == nil {
		raw := approval.ReadRaw(conf, root)
		copies, _ := approval.Scan(conf, root, false, raw)
		closed, _ := approval.Scan(conf, root, true, raw)
		review, _ := approval.ScanReview(conf, root, raw)
		proposals, _ := ticket.Scan(root, conf.Tickets, conf.Projects, nil)
		known = &Known{copies, closed, review, proposals}
	}
	var names []string
	for _, group := range [][]*ticket.Ticket{known.Copies, known.Closed, known.Review, known.Proposals} {
		for _, t := range group {
			names = append(names, t.ID)
		}
	}
	type repo struct{ root, label string }
	repos := []repo{{root, "ワークスペース"}}
	for _, t := range tree.Projects(conf.Projects) {
		repos = append(repos, repo{t.Root, "プロジェクト " + t.Name})
	}
	for _, r := range repos {
		if !ospath.DiskExists(ospath.Join(r.root, ".git")) {
			// git のリポジトリでない（モード B のワークスペースなど）。数えるものが無いだけで、数えきれないのではない。
			continue
		}
		done := git.Run(r.root, []string{"for-each-ref", "--format=%(refname)", headsPrefix}, git.Options{})
		if !done.OK() {
			why := ""
			switch {
			case done.Failure != nil:
				why = done.Failure.Error()
			case ospath.Strip(done.Stderr) != "":
				why = ospath.Strip(done.Stderr)
			default:
				why = "終了コード " + strconv.Itoa(done.Code)
			}
			problems = append(problems, r.label+"のブランチを数えられなかった（"+why+"）")
			continue
		}
		for _, line := range ospath.Splitlines(done.Out) {
			if line = ospath.Strip(line); line != "" {
				names = append(names, ids.BranchToID(strings.TrimPrefix(line, headsPrefix)))
			}
		}
	}
	homes := ospath.Join(root, tree.WorktreesDir)
	entries, err := ospath.DiskListdir(homes)
	if err != nil && !errors.Is(err, fs.ErrNotExist) {
		problems = append(problems, tree.WorktreesDir+" を読めなかった（"+fsio.ErrorText(err)+"）")
	}
	for _, e := range entries {
		if ospath.IsDir(ospath.Join(homes, e)) {
			names = append(names, e)
		}
	}
	return names, problems
}

// identifierProblem は `<先頭の語>-<番号>-<slug>` を親の識別子にできない理由。できれば空。
func identifierProblem(prefix, slug string, serial int, prefixes []string) string {
	if !slices.Contains(prefixes, prefix) {
		return "先頭の語 " + prefix + " は指定できない（指定できるのは " + strings.Join(prefixes, ", ") + "）"
	}
	ident := prefix + "-" + strconv.Itoa(serial) + "-" + slug
	if _, ok := ids.FormOf(ident, prefixes); !ok {
		return "slug " + slug + " は指定できない（先頭は英数字か日本語の字、続きは識別子に使える字。空白と `/` は書けない）"
	}
	if ids.HasChildTail(ident) {
		return ident + " は末尾が `-<2 桁>` で、子の識別子の途中（`<親>-<フェーズ番号>`）と紛れる。slug の末尾を変える"
	}
	if problem := ids.IDProblem(ident); problem != "" {
		return problem
	}
	if problem := ids.BranchProblem(ident); problem != "" {
		return problem
	}
	if n := utf8.RuneCountInString(ident); n > ids.SuggestedIDLength {
		return fmt.Sprintf("%s は %d 文字で、推奨の長さ（%d 文字）を超えています。slug を短くしてください。", ident, n, ids.SuggestedIDLength)
	}
	return ""
}

// RunNextID は `ccnavi ticket next-id [<先頭の語> <slug>]`。引数なしなら次の通し番号だけを、`<先頭の語> <slug>` なら
// `<先頭の語>-<番号>-<slug>` を 1 行で出す。番号は今ある名前の番号の最大 + 1 で、予約はしない。読むだけで、
// チケット制御が disable でも使える。終了コードは 0 が成功、1 が識別子にできない形、2 が数えきれなかったとき。
func RunNextID(stdout, stderr io.Writer, conf *config.Settings, root string, args []string) int {
	names, problems := SerialNames(conf, root, nil)
	if len(problems) > 0 {
		fmt.Fprintf(stderr, "ccnavi: ticket next-id: %s。%s\n", strings.Join(problems, "。"), askUser)
		return exitUncounted
	}
	serial := ids.NextSerial(names, conf.BranchPrefixes, false)
	if len(args) == 0 {
		fmt.Fprintf(stdout, "%d\n", serial)
		return config.ExitOK
	}
	if len(args) != 2 {
		fmt.Fprint(stderr, "ccnavi: ticket next-id は引数なしか <先頭の語> <slug> の 2 つで実行します。\n")
		return exitBadForm
	}
	prefix, slug := args[0], args[1]
	if problem := identifierProblem(prefix, slug, serial, conf.BranchPrefixes); problem != "" {
		fmt.Fprintf(stderr, "ccnavi: ticket next-id: %s\n", problem)
		return exitBadForm
	}
	fmt.Fprintf(stdout, "%s-%d-%s\n", prefix, serial, slug)
	return config.ExitOK
}
