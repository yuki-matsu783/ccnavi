package branch

import (
	"io"
	"slices"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/ctxfile"
)

// Kind は指定の種類。`ccnavi branches` の引数と JSON の kind、指定の記録（`<種類>:<番号>`）に出る。
type Kind string

const (
	issue Kind = "issue"
	mr    Kind = "mr"
)

const (
	// maxRefs は 1 回の依頼から拾う指定の上限。貼り付けた一覧から何十本も拾って指示を長くしない。
	maxRefs = 5
	// maxPrompt は依頼文のうち読む長さ（符号位置）。長いログで hook の判定の期限を使い切らないように頭だけを読む。
	maxPrompt = 20000
	// cssWindow は `#` の前を CSS の色として見る長さ。
	cssWindow = 40
)

// 依頼文の式。番号は 0 で始まらない（`#000`・`#012345` のような色や連番を読まない）。本文の形（語のあとの番号・
// `#N`・`!N`）は、番号の直後に行末・空白・句読点・閉じ括弧・助詞だけを許す（`手順#2を`・`issue 2つ目` を読まない）。
// `#N`・`!N` の直前は行頭・空白・開き括弧・引用符・読点・助詞だけ。URL は GitHub と GitLab の issues・pull・
// merge_requests で、前の 2 要素（owner/repo）をリポジトリの手がかりに残す。
const (
	numPattern     = `(?P<num>[1-9][0-9]{0,8})(?![0-9A-Za-z_])`
	afterPattern   = `(?=\Z|\s|[.,;:!?)\]}"'` + "`" + `]|[)」』）、。！？：，．…]|[をのがにはでともへや]|から|まで|だけ|より|って|です)`
	bodyNumPattern = `(?P<num>[1-9][0-9]{0,8})` + afterPattern
	beforePattern  = `(?:^|(?<=[\s(\[（「『、。,"'をのがにはでともへや]))`
)

var (
	fencePattern      = pyre.MustCompile("```.*?(?:```|\\Z)", pyre.DotAll)
	inlineCodePattern = pyre.MustCompile("`[^`\\n]*`", 0)
	urlIssuePattern   = pyre.MustCompile(`(?:(?P<repo>[\w.-]{1,100}/[\w.-]{1,100})/)?(?:-/)?issues/`+numPattern, 0)
	urlMRPattern      = pyre.MustCompile(`(?:(?P<repo>[\w.-]{1,100}/[\w.-]{1,100})/)?(?:-/)?(?:pull|pulls|merge_requests)/`+numPattern, 0)
	urlPattern        = pyre.MustCompile(`https?://\S+`, 0)
	wordIssuePattern  = pyre.MustCompile(`(?<![A-Za-z0-9_])issues?[ \t]*[:：#＃-]?[ \t]*`+bodyNumPattern, pyre.IgnoreCase)
	wordMRPattern     = pyre.MustCompile(`(?<![A-Za-z0-9_])`+
		`(?:MR|PR|merge[ \t]+request|pull[ \t]+request|マージリクエスト|プルリクエスト|プルリク)`+
		`[ \t]*[:：#＃!-]?[ \t]*`+bodyNumPattern, pyre.IgnoreCase)
	hashPattern = pyre.MustCompile(beforePattern+`[#＃]`+bodyNumPattern, 0)
	bangPattern = pyre.MustCompile(beforePattern+`!`+bodyNumPattern, 0)
	// `#` の前がこれなら CSS の色として読む（`color: #333333`）。
	cssBeforePattern = pyre.MustCompile(`(?:color|background|border|fill|stroke|outline|shadow)[-a-z]*\s*:\s*$`, pyre.IgnoreCase)
	// ユーザの指定ではない文。貼り付けた内容・system-reminder・サブエージェントの報告の枠は閉じタグまで（閉じが
	// 無ければ末尾まで）。`[ccnavi]` で始まる通知は次の空行までの 1 段落。他のセッションからの通知は末尾まで。
	noiseTagPattern     = pyre.MustCompile(`<(?P<tag>pasted_content|system-reminder|agent-message)\b[^>]*>.*?(?:</(?P=tag)\s*>|\Z)`, pyre.DotAll|pyre.IgnoreCase)
	noticePattern       = pyre.MustCompile(`^[ \t]*\[ccnavi\].*?(?:\n[ \t]*\n|\Z)`, pyre.DotAll|pyre.Multiline)
	otherSessionPattern = pyre.MustCompile(`Another Claude session sent a message.*\Z`, pyre.DotAll|pyre.IgnoreCase)
)

// Ref は依頼文の中の issue・MR の指定 1 つ。Repo は URL から読めたときの `owner/repo`（読めなければ空）。
type Ref struct {
	kind   Kind
	number int
	repo   string
}

// Label は指定の表示（`issue #152`・`MR !5（acme/w）`）。
func (r Ref) Label() string {
	mark, name := "#", "issue"
	if r.kind != issue {
		mark, name = "!", "MR"
	}
	out := name + " " + mark + strconv.Itoa(r.number)
	if r.repo != "" {
		out += "（" + r.repo + "）"
	}
	return out
}

func (r Ref) mark() string { return string(r.kind) + ":" + strconv.Itoa(r.number) }

// blank は当たった範囲を同じ長さ（符号位置）の空白に置き換える（位置は変えない）。
func blank(text string, patterns ...*pyre.Regexp) string {
	for _, p := range patterns {
		text = sub(p, text, func(f pyre.Found) string { return strings.Repeat(" ", utf8.RuneCountInString(f.Text())) })
	}
	return text
}

func sub(p *pyre.Regexp, text string, repl func(pyre.Found) string) string {
	if p == nil {
		return text
	}
	return p.Sub(text, repl)
}

func findAll(p *pyre.Regexp, text string) []pyre.Found {
	if p == nil {
		return nil
	}
	return p.FindAll(text)
}

func number(f pyre.Found) int {
	n, _ := strconv.Atoi(named(f, "num"))
	return n
}

func named(f pyre.Found, name string) string {
	s, _ := f.Named(name)
	return s
}

// PromptRefs は依頼文の中の issue・MR の指定。見つけた順に、同じ種類と番号は 1 つにまとめる。
func PromptRefs(text string) []Ref {
	if text == "" {
		return nil
	}
	space := func(pyre.Found) string { return " " }
	body := blank(sub(inlineCodePattern, sub(fencePattern, pyfmt.Head(text, maxPrompt), space), space),
		noiseTagPattern, otherSessionPattern, noticePattern)
	type hit struct {
		at  int
		ref Ref
	}
	var found []hit
	for _, p := range []struct {
		re   *pyre.Regexp
		kind Kind
	}{{urlIssuePattern, issue}, {urlMRPattern, mr}} {
		for _, m := range findAll(p.re, body) {
			found = append(found, hit{m.Start, Ref{p.kind, number(m), named(m, "repo")}})
		}
	}
	// URL の中身を消してから語と記号を探す（`.../issues/152#issuecomment-1` を 2 度数えない）。
	rest := blank(body, urlPattern)
	for _, m := range findAll(wordIssuePattern, rest) {
		found = append(found, hit{m.Start, Ref{kind: issue, number: number(m)}})
	}
	for _, m := range findAll(wordMRPattern, rest) {
		found = append(found, hit{m.Start, Ref{kind: mr, number: number(m)}})
	}
	// 語で読んだ分も消す（`PR #12` の `#12` を issue としても数えない）。
	rest = blank(rest, wordIssuePattern, wordMRPattern)
	for _, m := range findAll(bangPattern, rest) {
		found = append(found, hit{m.Start, Ref{kind: mr, number: number(m)}})
	}
	runes := []rune(rest)
	for _, m := range findAll(hashPattern, rest) {
		if cssBeforePattern != nil && cssBeforePattern.Search(string(runes[max(0, m.Start-cssWindow):m.Start])) {
			continue
		}
		found = append(found, hit{m.Start, Ref{kind: issue, number: number(m)}})
	}
	sort.SliceStable(found, func(i, j int) bool { return found[i].at < found[j].at })
	var refs []Ref
	seen := map[string]int{}
	for _, h := range found {
		key := h.ref.mark()
		if at, ok := seen[key]; ok {
			// URL から読めたリポジトリの手がかりは残す。
			if h.ref.repo != "" && refs[at].repo == "" {
				refs[at] = h.ref
			}
			continue
		}
		seen[key] = len(refs)
		refs = append(refs, h.ref)
	}
	// `#5` と `!5` のように同じ番号が両方に出るのは普通（同じ MR を言い換えた）なので、そのまま並べる。
	return refs[:min(len(refs), maxRefs)]
}

// PromptContext は依頼文に issue・MR の指定があれば、`ccnavi-start.sh` で着手させる指示。無ければ空。チケット制御が
// disable なら出さない。同じ番号の案内は同じセッションで 1 回だけで、案内した番号はセッションごとの記録に残す
// （読めない・書けないときは案内する側に倒す）。すでにチケットに結び付く番号は、そのチケットのワークツリーで
// 続けるよう案内する。
func PromptContext(stderr io.Writer, conf *config.Settings, root, text, cwd, session string) string {
	if !conf.TicketsEnabled() {
		return ""
	}
	refs := PromptRefs(text)
	if len(refs) == 0 {
		return ""
	}
	told, remember := ctxfile.ToldRefs(stderr, conf.State, session)
	if remember {
		refs = slices.DeleteFunc(refs, func(r Ref) bool { return told[r.mark()] })
		if len(refs) == 0 {
			return ""
		}
	}
	var lines []string
	var free []Ref
	for _, r := range refs {
		if b := bound(conf, root, cwd, r); b != nil {
			lines = append(lines, boundLine(r, b))
			if remember {
				told[r.mark()] = true
			}
		} else {
			free = append(free, r)
		}
	}
	if len(free) > 0 {
		if len(lines) > 0 {
			lines = append(lines, "")
		}
		lines = append(lines, startLines(conf, root, free)...)
	}
	if remember {
		for _, r := range free {
			told[r.mark()] = true
		}
		ctxfile.RememberRefs(stderr, conf.State, session, told)
	}
	return strings.Join(lines, "\n")
}

// boundTo は指定がすでに結び付いている先。
type boundTo struct {
	branch    string
	tickets   []string
	worktrees []string
}

// bound は指定がすでに結び付いている先。手元の候補（collect と同じ集め方）だけを見て、照合は厳密にする（issue は
// チケットの issue:、MR はチケットの mr: の一致。名前に番号を含むだけのブランチは数えない）。読めないときと、
// URL で別のリポジトリが示されているときは nil（案内する側に倒す）。
func bound(conf *config.Settings, root, cwd string, r Ref) *boundTo {
	if r.repo != "" {
		return nil
	}
	data, failed := collect(conf, root, cwd, r.kind, r.number, host{})
	if failed != "" {
		return nil
	}
	for _, c := range data.candidates {
		if slices.Contains(c.sources, sourceTicket) && len(c.tickets) > 0 {
			var links []string
			for _, t := range c.tickets {
				links = append(links, t.label())
			}
			return &boundTo{c.branch, links, c.worktrees}
		}
	}
	return nil
}

func boundLine(r Ref, b *boundTo) string {
	where := "そのチケットのワークツリーで続ける（ワークツリーはまだ無い。ccnavi-ticket.sh status でチケットの状態を確かめる）"
	if len(b.worktrees) > 0 {
		where = "そのチケットのワークツリー（" + strings.Join(b.worktrees, "・") + "）で続ける"
	}
	return "[ccnavi] 依頼の " + r.Label() + " は、すでに " + strings.Join(b.tickets, "・") + "（ブランチ " + b.branch + "）に" +
		"結び付いている。実装の依頼なら、" + where + "。--check を最初に打つ。" +
		"説明・調査だけなら、作業場所を変えずに答えてよい。"
}

// projectOfRepo は `owner/repo` を origin に持つ場所。自身なら空と真、プロジェクトならその名前と真、無ければ偽。
// origin のパスの最後の 2 要素を、大文字小文字を区別せずに比べる。
func projectOfRepo(conf *config.Settings, root, repo string) (string, bool) {
	want := pathcase.Fold(repo)
	if pathcase.Fold(tree.OriginRepo(tree.MainTree(root).Root)) == want {
		return "", true
	}
	for _, t := range tree.Projects(conf.Projects) {
		if pathcase.Fold(tree.OriginRepo(t.Root)) == want {
			return t.Project, true
		}
	}
	return "", false
}

func startLines(conf *config.Settings, root string, refs []Ref) []string {
	sh := config.ScriptCommand(root, config.StartWrapper)
	var labels, starts, notes []string
	for _, r := range refs {
		labels = append(labels, r.Label())
	}
	for _, r := range refs {
		where, known := "", true
		if r.repo != "" {
			where, known = projectOfRepo(conf, root, r.repo)
		}
		if !known {
			notes = append(notes, r.Label()+" のリポジトリ "+r.repo+" は projects/ の中に無い（まだ clone していない）。"+
				"ccnavi-start.sh は実行せず、clone が要ることをユーザに伝えて止まる。")
			continue
		}
		args := "--" + string(r.kind) + " " + strconv.Itoa(r.number)
		if r.repo == "" {
			starts = append(starts, "'"+sh+" "+args+"'")
			continue
		}
		args += " --repo " + r.repo
		// 実行する場所を絶対パスで名指しする（cwd がどこでも届く）。ワークスペース自身も同じ形。
		place := root
		if where != "" {
			place = ospath.Join(conf.Projects, where)
		}
		starts = append(starts, "'cd "+config.ShellPath(place)+" && "+sh+" "+args+"'")
	}
	lines := []string{"[ccnavi] 依頼に " + strings.Join(labels, "・") + " の指定がある。"}
	if len(starts) > 0 {
		lines = append(lines, "実装の依頼なら、着手前に '"+sh+" --check'、続けて "+strings.Join(starts, "・")+" を打ち、"+
			"出力の「次:」に従う。Draft MR とワークツリーができる。")
	}
	lines = append(lines, notes...)
	return append(lines, "調べ・質問に答えるだけなら、チケット作業にせずそのまま答えてよい。どちらか分からなければ、ユーザに聞く。")
}
