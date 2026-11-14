package start

import (
	"fmt"
	"strconv"
	"strings"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/tickets/model/ids"
)

// RouteArgs は ccnavi-start.sh が渡す引数の要点。
type RouteArgs struct {
	What        string // issue か mr（開始の種類）
	Number      string // 開始の番号（--mr があれば MR、無ければ issue）
	HasIssue    bool   // --issue があった
	HasMR       bool   // --mr があった
	Branch      string // --branch の値
	MCPChecked  bool   // --mcp-checked があった
	Integration string // 統合先のブランチ名
}

// Route は、ccnavi-branches.sh が出した候補の JSON と引数、cwd のリポジトリの git から、ccnavi-start.sh が次に進む道を決める。
//
// 出力は 1 行 1 項目。先頭の 3 行はいつも出す: `checked true|false`（ホストを見られたか）、`reason <見られなかった理由>`、
// `total <候補の数>`。続けて `route <道>` と、その道で sh が使う値を出す。道と値は次のとおり（判断の順もこの並び）。
//
//   - mcp: ホストを見られず、--issue・--mr・--branch・--mcp-checked がそろっている。`branch`。--branch が保護された
//     ブランチか使えない書き方なら `problem protected`、手元にも origin にも無ければ `problem branch-missing`
//   - multi: 候補が複数
//   - unchecked: ホストを見られず、候補があるか --mr で来た。`branch`（先頭の候補。無ければ空）
//   - mr-not-found: --mr で来て候補が 0 件
//   - none-unchecked: 候補が 0 件で、ホストを見られず、--mcp-checked が無い
//   - none-branch: 候補が 0 件で、--branch がある（ホストを見られたかは checked の行で分ける）
//   - create: 候補が 0 件で、新しく作る
//   - 候補が 1 件のとき（`branch`・`mrs`・`mr`・`url`・`title` を出す）:
//   - bad-candidate: 候補のブランチ名が使えない書き方か、保護されたブランチ
//   - proposal-only: 手元にも origin にも MR にも実体が無い（チケットの提案だけ）
//   - branch-mismatch: --branch と候補のブランチが違う
//   - mr-exists: --issue で来て、開いた MR があり、この PC にそのブランチのワークツリーが無い
//   - adopt: 開いた MR を採用する。MR が採用できなければ `problem not-open <状態>`・`problem fork`・
//     `problem target <マージ先>`・`problem protected` の最初の 1 つ（`mr-state`・`mr-base` も出す）
//   - nomr: MR が無い。sh が識別子を確かめたあとに進む先を `after stale|resume|no-mr` で出す
//     （stale は統合先より進んでいない古いブランチ、resume は初期コミットだけの作りかけ）
//
// 候補の JSON を読めなければ何も出さずに unreadable。
func Route(cwd string, candidates []byte, a RouteArgs) (string, int) {
	v, err := decode(candidates)
	if err != nil {
		return "", unreadable
	}
	host, herr := field(v, "host")
	checkedV, cerr := field(host, "checked")
	reasonV, rerr := field(host, "reason")
	listV, lerr := field(v, "candidates")
	cands, lerr2 := list(listV)
	if herr != nil || cerr != nil || rerr != nil || lerr != nil || lerr2 != nil {
		return "", unreadable
	}
	checked := checkedV == true
	reason := ""
	if truthy(reasonV) {
		reason = text(reasonV)
	}
	var b strings.Builder
	fmt.Fprintf(&b, "checked %t\nreason %s\ntotal %d\n", checked, clean(reason), len(cands))
	// ホストから来る値（MR の題名・状態など）は改行を空白にしてから出す。改行のまま出すと、値の中に書いた
	// `route adopt` のような行を、sh が答えの行として読んでしまう。
	line := func(k, v string) { fmt.Fprintf(&b, "%s %s\n", k, clean(v)) }
	g := repo{cwd: cwd}

	switch {
	case !checked && a.MCPChecked && a.HasIssue && a.HasMR && a.Branch != "":
		line("route", "mcp")
		line("branch", a.Branch)
		if protected(a.Branch, a.Integration) {
			line("problem", "protected")
		} else if g.branchRef(a.Branch) == "" {
			line("problem", "branch-missing")
		}
		return b.String(), ok
	case len(cands) > 1:
		line("route", "multi")
		return b.String(), ok
	case !checked && (len(cands) >= 1 || a.What == "mr"):
		line("route", "unchecked")
		first := ""
		if len(cands) >= 1 {
			br, _ := field(cands[0], "branch")
			if truthy(br) {
				first = text(br)
			}
		}
		line("branch", first)
		return b.String(), ok
	case len(cands) == 0:
		switch {
		case a.What == "mr":
			line("route", "mr-not-found")
		case !checked && !a.MCPChecked:
			line("route", "none-unchecked")
		case a.Branch != "":
			line("route", "none-branch")
		default:
			line("route", "create")
		}
		return b.String(), ok
	}

	c := cands[0]
	cb, err := field(c, "branch")
	if err != nil {
		return "", unreadable
	}
	branch := text(cb)
	local, _ := field(c, "local")
	origin, _ := field(c, "origin")
	mrsV, _ := field(c, "mrs")
	mrs, err := list(mrsV)
	if err != nil {
		return "", unreadable
	}
	var mr any
	if len(mrs) > 0 {
		mr = mrs[0]
	}
	num, _ := field(mr, "number")
	url, _ := field(mr, "url")
	title, _ := field(mr, "title")
	lineOr := func(k string, v any) {
		s := ""
		if truthy(v) {
			s = text(v)
		}
		line(k, s)
	}
	line("branch", branch)
	line("mrs", strconv.Itoa(len(mrs)))
	lineOr("mr", num)
	lineOr("url", url)
	lineOr("title", title)

	switch {
	case ids.BranchProblem(branch) != "":
		line("route", "bad-candidate")
	case local != true && origin != true && len(mrs) == 0:
		line("route", "proposal-only")
	case a.Branch != "" && branch != a.Branch:
		line("route", "branch-mismatch")
	case len(mrs) > 0 && a.What == "issue" && g.worktreeOf(branch) == "":
		line("route", "mr-exists")
	case len(mrs) > 0:
		line("route", "adopt")
		adoptProblem(&b, mr, branch, a.Integration)
	default:
		line("route", "nomr")
		line("after", g.nomrAfter(branch, a))
	}
	return b.String(), ok
}

// protected は、ブランチ名が親のブランチに使えない書き方か、保護されたブランチ（統合先を含む）か。
func protected(branch, integration string) bool {
	return ids.BranchProblem(branch) != "" || branch == integration
}

// adoptProblem は、開いた MR を採用できない理由の最初の 1 つを書く（状態・フォーク・マージ先・保護の順）。
func adoptProblem(b *strings.Builder, mr any, branch, integration string) {
	state, _ := field(mr, "state")
	base, _ := field(mr, "base")
	fork, _ := field(mr, "fork")
	st, bs := "", ""
	if truthy(state) {
		st = clean(text(state))
	}
	if truthy(base) {
		bs = clean(text(base))
	}
	fmt.Fprintf(b, "mr-state %s\nmr-base %s\n", st, bs)
	switch {
	case st != "open" && st != "opened":
		fmt.Fprintf(b, "problem not-open %s\n", st)
	case fork == true:
		b.WriteString("problem fork\n")
	case bs != integration:
		fmt.Fprintf(b, "problem target %s\n", bs)
	case protected(branch, integration):
		b.WriteString("problem protected\n")
	}
}

// repo は cwd のリポジトリの git を読む。
type repo struct{ cwd string }

func (r repo) refExists(ref string) bool {
	return git.Run(r.cwd, []string{"rev-parse", "--verify", "--quiet", ref + "^{commit}"}, git.Options{}).Code == 0
}

// branchRef は手元のブランチの参照、無ければ origin の追跡参照。どちらも無ければ空。
func (r repo) branchRef(branch string) string {
	for _, ref := range []string{"refs/heads/" + branch, "refs/remotes/origin/" + branch} {
		if r.refExists(ref) {
			return ref
		}
	}
	return ""
}

// worktreeOf はそのブランチをチェックアウトしているワークツリーのパス。無ければ空。
func (r repo) worktreeOf(branch string) string {
	code, out := git.Output(r.cwd, []string{"worktree", "list", "--porcelain"}, git.Options{})
	if code != 0 {
		return ""
	}
	path := ""
	for l := range strings.SplitSeq(out, "\n") {
		if p, found := strings.CutPrefix(l, "worktree "); found {
			path = p
		} else if l == "branch refs/heads/"+branch {
			return path
		}
	}
	return ""
}

// nomrAfter は、MR の無い候補の行き先。統合先より進んでいなければ stale、初期コミットだけの作りかけ（--issue で来た
// とき）なら resume、それ以外は no-mr。名前に `/` を含むブランチは作りかけと見ない。
func (r repo) nomrAfter(branch string, a RouteArgs) string {
	if strings.Contains(branch, "/") {
		return "no-mr"
	}
	ref := r.branchRef(branch)
	if ref == "" || !r.refExists("refs/remotes/origin/"+a.Integration) {
		return "no-mr"
	}
	ahead := 9
	if code, out := git.Output(r.cwd, []string{"rev-list", "--count", "origin/" + a.Integration + ".." + ref}, git.Options{}); code == 0 {
		if n, ok := git.Count(out); ok {
			ahead = n
		}
	}
	subject := ""
	if code, out := git.Output(r.cwd, []string{"log", "-1", "--format=%s", ref}, git.Options{}); code == 0 {
		subject = strings.TrimRight(out, "\n")
	}
	switch {
	case ahead == 0:
		return "stale"
	case ahead == 1 && subject == InitSubject(a.Number) && a.What == "issue":
		return "resume"
	}
	return "no-mr"
}

// InitSubject は、新しく作る親のブランチに積む空の初期コミットの件名。
func InitSubject(number string) string { return "chore: #" + number + " の作業を開始する" }
