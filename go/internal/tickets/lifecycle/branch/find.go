package branch

import (
	"fmt"
	"io"
	"math/big"
	"slices"
	"sort"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// source は候補の由来。並びの順（チケット、MR、名前）にも使う。
type source string

const (
	sourceTicket source = "ticket"
	sourceMR     source = "mr"
	sourceName   source = "name"
)

var (
	sourceOrder = map[source]int{sourceTicket: 0, sourceMR: 1, sourceName: 2}
	sourceLabel = map[source]string{sourceMR: "MR", sourceTicket: "チケット", sourceName: "名前に番号"}
)

const (
	headsPrefix  = "refs/heads/"
	originPrefix = "refs/remotes/origin/"
	// ホストから来た表記のうち出力に載せる長さ（符号位置）。
	cleanLimit       = 200
	cleanBranchLimit = 250
	cleanStateLimit  = 20
	cleanURLLimit    = 500
	// maxNumberDigits は番号として読む桁数（`[1-9][0-9]{0,8}`）。
	maxNumberDigits = 9
)

// ticketLink は候補に結び付くチケット 1 枚。issue の 0 は無し（JSON は null）。
type ticketLink struct {
	ticket   string
	state    place.State
	approved bool
	title    string
	issue    int
}

func linkOf(t *ticket.Ticket) ticketLink {
	return ticketLink{t.ID, t.State, t.State != place.Todo, t.Title, t.Issue}
}

func (t ticketLink) json() *pyjson.Object {
	var issue any
	if t.issue != 0 {
		issue = t.issue
	}
	return pyjson.NewObject("ticket", t.ticket, "state", string(t.state), "approved", t.approved, "title", t.title, "issue", issue)
}

func (t ticketLink) label() string { return t.ticket + "(" + string(t.state) + ")" }

// candidate は候補のブランチ 1 本。mrs はホストの MR（host の mrs の要素）。
type candidate struct {
	branch        string
	local, origin bool
	sources       []source
	mrs           []*pyjson.Object
	worktrees     []string
	tickets       []ticketLink
}

func (c *candidate) addSource(s source) {
	if !slices.Contains(c.sources, s) {
		c.sources = append(c.sources, s)
	}
}

func (c *candidate) json() *pyjson.Object {
	sources := []any{}
	for _, s := range c.sources {
		sources = append(sources, string(s))
	}
	mrs := []any{}
	for _, m := range c.mrs {
		mrs = append(mrs, m)
	}
	worktrees := []any{}
	for _, w := range c.worktrees {
		worktrees = append(worktrees, w)
	}
	tickets := []any{}
	for _, t := range c.tickets {
		tickets = append(tickets, t.json())
	}
	return pyjson.NewObject("branch", c.branch, "local", c.local, "origin", c.origin, "sources", sources, "mrs", mrs,
		"worktrees", worktrees, "tickets", tickets)
}

// host は sh がホストを見たかどうかと、見た結果。
type host struct {
	checked            bool
	reason, name, repo string
	mrs                []*pyjson.Object
}

func (h host) json() *pyjson.Object {
	return pyjson.NewObject("checked", h.checked, "reason", h.reason, "name", h.name, "repo", h.repo)
}

// clean はホストから来た表記を、改行や制御文字で行を割らせない形にする。
func clean(value any, limit int) string {
	text, ok := value.(string)
	if !ok {
		return ""
	}
	text = strings.Map(func(r rune) rune {
		if r < 0x20 || r == 0x7f {
			return ' '
		}
		return r
	}, text)
	return pyfmt.Head(ospath.Strip(text), limit)
}

// readHost は sh が書いたホストの結果（checked・reason・host・repo・mrs のキーを持つ JSON）。無い・読めない・
// 形が違うなら「見ていない」として理由を付ける。
func readHost(path string) host {
	if path == "" {
		return host{reason: "ホストの結果が渡されていない（--result がありません）"}
	}
	value, err := fsio.ReadJSON(path)
	data, ok := value.(*pyjson.Object)
	if err != nil || !ok {
		return host{reason: "ホストの結果を読めない"}
	}
	if checked, _ := data.Value("checked").(bool); !checked {
		reason := clean(data.Value("reason"), cleanLimit)
		if reason == "" {
			reason = "理由は分からない"
		}
		return host{reason: reason}
	}
	var mrs []*pyjson.Object
	list, _ := data.Value("mrs").([]any)
	for _, raw := range list {
		m, ok := raw.(*pyjson.Object)
		if !ok {
			continue
		}
		number := m.Value("number")
		switch number.(type) {
		case int, *big.Int:
		default:
			continue
		}
		branch := clean(m.Value("branch"), cleanBranchLimit)
		if branch == "" {
			continue
		}
		fork, _ := m.Value("fork").(bool)
		mrs = append(mrs, pyjson.NewObject(
			"number", number,
			"branch", branch,
			"state", clean(m.Value("state"), cleanStateLimit),
			"url", clean(m.Value("url"), cleanURLLimit),
			"title", clean(m.Value("title"), cleanLimit),
			"base", clean(m.Value("base"), cleanBranchLimit),
			"fork", fork,
		))
	}
	return host{checked: true, name: clean(data.Value("host"), cleanLimit), repo: clean(data.Value("repo"), cleanLimit), mrs: mrs}
}

// namesNumber はブランチ名が番号を含むか。ccnavi の命名 `<先頭の語>-<番号>-<slug>` に合う名前は 2 つ目の番号だけで
// 比べ（`feature-102-…-1-…` は 102）、形に合わない名前は番号の前後が区切り（`-` `/` `_` `.`）か端であること。
// `claude/tender-volta-qmy99q` の乱数や `i152` を候補にしない。`ccnavi-branches.sh` の jq も同じ規則なので、
// 片方だけを直さないこと。
func namesNumber(name string, number int, prefixes []string) bool {
	if ident, ok := ids.IdentifierNumber(pyfmt.Casefold(name), prefixes); ok {
		return ident == number
	}
	want := strconv.Itoa(number)
	isSep := func(c byte) bool { return strings.IndexByte("-/_.", c) >= 0 }
	for at := 0; ; at++ {
		i := strings.Index(name[at:], want)
		if i < 0 {
			return false
		}
		at += i
		end := at + len(want)
		if (at == 0 || isSep(name[at-1])) && (end == len(name) || isSep(name[end])) {
			return true
		}
	}
}

// refsOf は手元のブランチと origin のブランチ。読めなければ理由。
func refsOf(repoRoot string) (map[string]bool, map[string]bool, string) {
	done := git.Run(repoRoot, []string{"for-each-ref", "--format=%(refname)", headsPrefix, originPrefix}, git.Options{})
	if !done.OK() {
		switch {
		case done.Failure != nil:
			return nil, nil, done.Failure.Error()
		case ospath.Strip(done.Stderr) != "":
			return nil, nil, ospath.Strip(done.Stderr)
		}
		return nil, nil, "git for-each-ref が失敗した"
	}
	local, origin := map[string]bool{}, map[string]bool{}
	for _, line := range ospath.Splitlines(done.Out) {
		if name, ok := strings.CutPrefix(line, headsPrefix); ok {
			local[name] = true
		} else if name, ok := strings.CutPrefix(line, originPrefix); ok && name != "" && name != "HEAD" {
			origin[name] = true
		}
	}
	return local, origin, ""
}

// repoOf は cwd のリポジトリ（元のツリー）・プロジェクト名・読めない理由。
func repoOf(conf *config.Settings, root, cwd string) (*tree.Tree, string, string) {
	var at tree.Tree
	ok := false
	if cwd != "" {
		at, ok = tree.TreeOf(root, cwd, conf.Projects)
	}
	if !ok {
		return nil, "", cwd + " はワークスペースの外"
	}
	if at.Project == "" {
		main := tree.MainTree(root)
		return &main, "", ""
	}
	for _, t := range tree.Projects(conf.Projects) {
		if t.Project == at.Project {
			return &t, at.Project, ""
		}
	}
	return nil, at.Project, "プロジェクト " + at.Project + " が見つからない"
}

// checkedOutIn はこのリポジトリでブランチをチェックアウトしているツリー（元のツリーとワークツリー）。
func checkedOutIn(conf *config.Settings, root string, repo tree.Tree) map[string][]string {
	out := map[string][]string{}
	trees := []tree.Tree{repo}
	for _, t := range tree.Worktrees(root, conf.Projects) {
		if t.Project == repo.Project {
			trees = append(trees, t)
		}
	}
	for _, t := range trees {
		if name, ok := tree.BranchOf(t.Root); ok {
			out[name] = append(out[name], ospath.Slashed(ospath.RelOr(t.Root, root)))
		}
	}
	return out
}

// ticketsOf はこのリポジトリの承認済みチケット（作業中・レビュー待ち・閉じた）と、承認待ちの提案。
func ticketsOf(conf *config.Settings, root, project string) []*ticket.Ticket {
	doing, _ := approval.Scan(conf, root, false, nil)
	for _, t := range doing {
		if t.State == "" {
			t.State = place.Doing
		}
	}
	done, _ := approval.Scan(conf, root, true, nil)
	for _, t := range done {
		t.State = place.Done
	}
	review, _ := approval.ScanReview(conf, root, nil)
	proposals, _ := ticket.Scan(root, conf.Tickets, conf.Projects, nil)
	todo := slices.DeleteFunc(proposals, func(t *ticket.Ticket) bool { return t.State != place.Todo })
	var found []*ticket.Ticket
	for _, t := range slices.Concat(doing, review, done, todo) {
		if t.Project == project {
			found = append(found, t)
		}
	}
	return found
}

// branchOfTicket はチケットが示すブランチ。識別子と同じ名前を先に、無ければ "/" を "-" に置き換えて識別子と一致する
// 手元か origin のブランチ。どれも無ければ識別子。
func branchOfTicket(t *ticket.Ticket, names []string) string {
	if slices.Contains(names, t.ID) {
		return t.ID
	}
	for _, n := range names {
		if ids.BranchToID(n) == t.ID {
			return n
		}
	}
	return t.ID
}

// found は collect の答え（`ccnavi branches --json` の形）。
type found struct {
	kind           Kind
	number         int
	project        string
	repoRoot       string
	host           host
	ticketsChecked bool
	candidates     []*candidate
	issueTickets   []ticketLink
}

func (f *found) json() *pyjson.Object {
	candidates := []any{}
	for _, c := range f.candidates {
		candidates = append(candidates, c.json())
	}
	tickets := []any{}
	for _, t := range f.issueTickets {
		tickets = append(tickets, t.json())
	}
	return pyjson.NewObject("kind", string(f.kind), "number", f.number,
		"repo", pyjson.NewObject("project", f.project, "root", f.repoRoot),
		"host", f.host.json(), "tickets_checked", f.ticketsChecked, "candidates", candidates, "issue_tickets", tickets)
}

// collect は候補を集める。答えは候補と、止める理由（あれば）。ホストの MR の元ブランチ、番号の種類ごとのチケット
// （issue は issue:、MR は mr: を持つ親。同じリポジトリだけ）と名前（issue だけ）を候補に入れ、各候補にワークツリーと
// 識別子になるブランチのチケットを添えて、由来の順（チケット、MR、名前）に並べる。
func collect(conf *config.Settings, root, cwd string, kind Kind, number int, h host) (*found, string) {
	repo, project, failed := repoOf(conf, root, cwd)
	if repo == nil {
		return nil, failed
	}
	local, origin, failed := refsOf(repo.Root)
	if failed != "" {
		return nil, "ブランチを読めない: " + failed
	}
	checkedOut := checkedOutIn(conf, root, *repo)
	var tickets []*ticket.Ticket
	if conf.TicketsEnabled() {
		tickets = ticketsOf(conf, root, project)
	}
	names := map[string]bool{}
	for n := range local {
		names[n] = true
	}
	for n := range origin {
		names[n] = true
	}
	sortedNames := make([]string, 0, len(names))
	for n := range names {
		sortedNames = append(sortedNames, n)
	}
	slices.Sort(sortedNames)

	byBranch := map[string]*candidate{}
	var order []*candidate
	candidateOf := func(branch string) *candidate {
		if c, ok := byBranch[branch]; ok {
			return c
		}
		c := &candidate{branch: branch, local: local[branch], origin: origin[branch]}
		byBranch[branch] = c
		order = append(order, c)
		return c
	}
	for _, m := range h.mrs {
		c := candidateOf(m.String("branch"))
		c.addSource(sourceMR)
		c.mrs = append(c.mrs, m)
	}
	var linked []ticketLink
	for _, t := range tickets {
		if t.IsChild() {
			continue
		}
		hit := false
		switch kind {
		case issue:
			hit = t.Issue == number && t.IssueRepo == ""
		case mr:
			hit = t.MR == number
		}
		if hit {
			linked = append(linked, linkOf(t))
			candidateOf(branchOfTicket(t, sortedNames)).addSource(sourceTicket)
		}
	}
	if kind == issue {
		for _, name := range sortedNames {
			if namesNumber(name, number, conf.BranchPrefixes) {
				candidateOf(name).addSource(sourceName)
			}
		}
	}
	for _, c := range order {
		c.worktrees = slices.Sorted(slices.Values(checkedOut[c.branch]))
		for _, t := range tickets {
			if ids.BranchToID(c.branch) == t.ID {
				c.tickets = append(c.tickets, linkOf(t))
			}
		}
	}
	rank := func(c *candidate) int {
		best := 9
		for _, s := range c.sources {
			best = min(best, sourceOrder[s])
		}
		return best
	}
	sort.SliceStable(order, func(i, j int) bool {
		if a, b := rank(order[i]), rank(order[j]); a != b {
			return a < b
		}
		return order[i].branch < order[j].branch
	})
	return &found{kind, number, project, ospath.Slashed(ospath.RelOr(repo.Root, root)), h, conf.TicketsEnabled(), order, linked}, ""
}

func where(c *candidate) string {
	var places []string
	if c.local {
		places = append(places, "手元")
	}
	if c.origin {
		places = append(places, "origin")
	}
	switch {
	case len(places) > 0:
		return strings.Join(places, ",")
	case len(c.mrs) > 0:
		return "ホストだけ"
	}
	return "まだ無い"
}

// render は 1 候補 1 行の形。エージェントが読んでユーザに見せる。
func render(f *found) string {
	mark := "MR !" + strconv.Itoa(f.number)
	if f.kind == issue {
		mark = "issue #" + strconv.Itoa(f.number)
	}
	place := "ワークスペース"
	if f.project != "" {
		place = "プロジェクト " + f.project
	}
	lines := []string{"ccnavi-branches: " + mark + " に紐づくブランチ（" + place + ": " + f.repoRoot + "）"}
	if f.host.checked {
		lines = append(lines, "ホスト: "+f.host.name+" "+f.host.repo+" を見た")
	} else {
		lines = append(lines, "ホストは見ていない（"+f.host.reason+"）。手元の候補だけを出す")
	}
	if !f.ticketsChecked {
		lines = append(lines, "チケットは見ていない（チケット制御が disable）")
	}
	dash := func(s string) string {
		if s == "" {
			return "-"
		}
		return s
	}
	var weak []string
	for _, c := range f.candidates {
		var sources, mrs, tickets []string
		for _, s := range c.sources {
			label, ok := sourceLabel[s]
			if !ok {
				label = string(s)
			}
			sources = append(sources, label)
		}
		for _, m := range c.mrs {
			fork := ""
			if v, _ := m.Value("fork").(bool); v {
				fork = ",フォーク"
			}
			mrs = append(mrs, fmt.Sprintf("!%v(%s%s)", m.Value("number"), m.String("state"), fork))
		}
		for _, t := range c.tickets {
			tickets = append(tickets, t.label())
		}
		lines = append(lines, strings.Join([]string{
			"候補 " + c.branch,
			"在りか=" + where(c),
			"由来=" + strings.Join(sources, ","),
			"MR=" + dash(strings.Join(mrs, ",")),
			"ワークツリー=" + dash(strings.Join(c.worktrees, ",")),
			"チケット=" + dash(strings.Join(tickets, ",")),
		}, "  "))
		if len(c.sources) == 1 && c.sources[0] == sourceName && len(c.tickets) == 0 {
			weak = append(weak, c.branch)
		}
	}
	for _, t := range f.issueTickets {
		state := "提案（承認前）"
		if t.approved {
			state = "承認済み"
		}
		lines = append(lines, "チケット "+t.ticket+"  "+state+"  状態="+string(t.state)+"  タイトル="+dash(t.title))
	}
	if len(weak) > 0 {
		lines = append(lines, "案内: "+strings.Join(weak, "・")+" は由来が「名前に番号」だけで、MR にもチケットにも結び付いていない。"+
			"名前の一致は弱い根拠なので、無関係なブランチでないかを確かめてからユーザに見せる")
	}
	if n := len(f.candidates); n > 0 {
		lines = append(lines, fmt.Sprintf("候補 %d 件。ユーザに見せ、既存のブランチで続けるか・新しく切るか・やめるかを聞いて返事を待つ", n))
	} else {
		lines = append(lines, "候補なし")
	}
	return strings.Join(lines, "\n") + "\n"
}

// numberText は `[1-9][0-9]{0,8}` に全体が合うか。
func numberText(s string) bool {
	if s == "" || len(s) > maxNumberDigits || s[0] < '1' || s[0] > '9' {
		return false
	}
	return strings.Trim(s, "0123456789") == ""
}

// Report は `ccnavi branches <issue|mr> <番号>`。0 は出した、1 は引数か場所の誤り。
func Report(stdout, stderr io.Writer, conf *config.Settings, root, cwd, kind, number, resultPath string, asJSON bool) int {
	if Kind(kind) != issue && Kind(kind) != mr {
		fmt.Fprintf(stderr, "ccnavi: branches の種類は issue か mr（%s は違う）\n", pyfmt.Repr(kind))
		return 1
	}
	if !numberText(number) {
		fmt.Fprintf(stderr, "ccnavi: branches の番号 %s は正の整数ではない\n", pyfmt.Repr(number))
		return 1
	}
	n, _ := strconv.Atoi(number)
	data, failed := collect(conf, root, cwd, Kind(kind), n, readHost(resultPath))
	if failed != "" {
		fmt.Fprintf(stderr, "ccnavi: %s\n", failed)
		return 1
	}
	if asJSON {
		text, err := pyjson.Dumps(data.json(), pyjson.Options{RawUnicode: true})
		if err != nil {
			fmt.Fprintf(stderr, "ccnavi: %s\n", err)
			return 1
		}
		fmt.Fprintf(stdout, "%s\n", text)
		return 0
	}
	fmt.Fprint(stdout, render(data))
	return 0
}
