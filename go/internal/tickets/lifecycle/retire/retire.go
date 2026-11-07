package retire

import (
	"fmt"
	"io"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/lifecycle/worktrees"
	"ccnavi/internal/tickets/model/archive"
)

const (
	cleanBranchScript = "ccnavi-user-clean-branch.sh"
	originPrefix      = "refs/remotes/origin/"
	headsPrefix       = "refs/heads/"

	textRetireParent = "DENY_RETIRE_PARENT"
)

// status は子のブランチ 1 本の結果。
type status string

const (
	deleted status = "deleted"
	kept    status = "kept"
	ask     status = "ask"
	none    status = "none"
)

// outcome は子のブランチ 1 本の結果と言うこと（kept と ask は理由と次の手を含む）。
type outcome struct {
	status status
	text   string
}

// ---- git（ローカルの読み取りと、ブランチの削除だけ）

func commitOf(repo, rev string) string {
	done := git.Run(repo, []string{"rev-parse", "--verify", "--quiet", rev + "^{commit}"}, git.Options{})
	if !done.OK() {
		return ""
	}
	return ospath.Strip(done.Out)
}

func tipOf(repo, name string) string { return commitOf(repo, headsPrefix+name) }

func isAncestor(repo, older, newer string) bool {
	return git.IsAncestor(repo, older, newer, git.Options{}).OK()
}

// checkedOutAt はそのブランチをチェックアウトしているワークツリーのパス。無ければ空。
func checkedOutAt(repo, name string) string {
	done := git.Run(repo, []string{"worktree", "list", "--porcelain"}, git.Options{RawPaths: true})
	if !done.OK() {
		return "（ワークツリーの一覧を読めない）"
	}
	for _, block := range strings.Split(done.Out, "\n\n") {
		lines := ospath.Splitlines(block)
		if slices.Contains(lines, "branch "+headsPrefix+name) {
			if where, ok := strings.CutPrefix(lines[0], "worktree "); ok {
				return where
			}
			return "?"
		}
	}
	return ""
}

// deleteBranch は `git branch -D`。失敗した理由（無ければ空）。条件の確認は呼び手が済ませてある。
func deleteBranch(repo, name string) string {
	done := git.Run(repo, []string{"branch", "-D", "--", name}, git.Options{})
	if done.OK() {
		return ""
	}
	if said := done.FirstLine(); said != "" {
		return said
	}
	return "理由不明"
}

func handCommand(repo, name string) string { return "git -C " + repo + " branch -D " + name }

func keep(repo, name, why string) outcome {
	return outcome{kept, "ブランチ " + name + " は残した: " + why + "\n  確かめたうえで手で消すなら: " + handCommand(repo, name)}
}

// judgeChild は子のブランチを消してよいか確かめて、よければ消す。reference は「push 済みの親」を指す版
// （origin/<親のブランチ>、または MR の先頭の sha）。tool は確認が取れた後に打つコマンド（取り消した子の案内）。
func judgeChild(repo string, c child, reference string, confirmed bool, tool string) outcome {
	name := c.ticket
	tip := tipOf(repo, name)
	if tip == "" {
		return outcome{status: none}
	}
	base := ""
	if reference != "" {
		base = commitOf(repo, reference)
	}
	if c.kind == cancelled {
		extra := ""
		if base != "" && !isAncestor(repo, tip, base) {
			extra = "（親に入っていないコミットがある）"
		}
		if !confirmed {
			text := "- " + name + extra
			if tool != "" {
				text = strings.TrimRightFunc(text+"\n  ユーザが打つ: "+tool, ospath.IsSpace)
			}
			return outcome{ask, text}
		}
		return dropBranch(repo, name, tip, "取り消した子。ユーザが確認した")
	}
	if c.mergedSHA == "" {
		return keep(repo, name, "合流した先頭の sha（merged_sha）が記録に無く、合流済みか確かめられない")
	}
	mergedHead := commitOf(repo, c.mergedSHA)
	if mergedHead == "" {
		return keep(repo, name, "合流した先頭 "+pyfmt.Head(c.mergedSHA, git.ShortSHA)+" が手元に無い")
	}
	if tip != mergedHead {
		return keep(repo, name, "ブランチの先頭 "+pyfmt.Head(tip, git.ShortSHA)+" が、合流した先頭 "+pyfmt.Head(mergedHead, git.ShortSHA)+
			" と違う（合流のあとに進んでいる）")
	}
	if base == "" {
		return keep(repo, name, "push 済みの親の先頭を読めず、合流した先頭が含まれるか確かめられない")
	}
	if !isAncestor(repo, mergedHead, base) {
		return keep(repo, name, "合流した先頭 "+pyfmt.Head(mergedHead, git.ShortSHA)+" が push 済みの親に含まれない")
	}
	return dropBranch(repo, name, tip, "合流済み")
}

func dropBranch(repo, name, tip, why string) outcome {
	if where := checkedOutAt(repo, name); where != "" {
		return keep(repo, name, "ワークツリー（"+where+"）がチェックアウトしている")
	}
	if failed := deleteBranch(repo, name); failed != "" {
		return keep(repo, name, "git branch -D が失敗した（"+failed+"）")
	}
	return outcome{deleted, "ブランチ " + name + " を削除しました（" + why + "。先頭 " + pyfmt.Head(tip, git.ShortSHA) + "）"}
}

// ---- ready の後（片付け 1）

// dropWorktree はワークツリーがあれば消す。消した・もともと無いなら真。
func dropWorktree(stdout io.Writer, root, name, cwd, after string) bool {
	if !ospath.DiskLexists(tree.WorktreePath(root, name)) {
		return true
	}
	d := worktrees.Drop(root, name, cwd)
	worktrees.Report(stdout, root, d, after)
	return d.Removed()
}

func say(stdout io.Writer, o outcome) {
	if o.text != "" {
		fmt.Fprintf(stdout, "%s\n", o.text)
	}
}

// confirmCommand は取り消した子のブランチを消す、ユーザが打つコマンド（`!` は呼び手が付ける）。エージェントから
// は hook が止める。ユーザが確認したことを、ユーザ自身が打つことで担保する。
func confirmCommand(root, name string) string {
	return config.ScriptCommand(root, cleanBranchScript) + " " + name + " --confirmed"
}

// parentOf はそのチケットの parent。承認済みの置き場（作業中・閉じた・レビュー待ち）を先に、無ければ手元の退避を
// 見る。見つからなければ空。
func parentOf(conf *config.Settings, root, project, ident string) string {
	raw := approval.ReadRaw(conf, root)
	open, _ := approval.Scan(conf, root, false, raw)
	closed, _ := approval.Scan(conf, root, true, raw)
	review, _ := approval.ScanReview(conf, root, raw)
	for _, t := range slices.Concat(open, closed, review) {
		if same(t.ID, ident) {
			return t.Parent
		}
	}
	if found, ok := archive.ArchivedFields(root, project, ident); ok {
		return found.Parent
	}
	return ""
}

// parentRefusal は親のワークツリーを消せないので、子に触る前に断る文。
func parentRefusal(root, family string, refused worktrees.Dropped) string {
	which := "other"
	switch refused.Status {
	case worktrees.CwdInside:
		which = "cwd"
	case worktrees.Dirty:
		which = "dirty"
	}
	command := config.ScriptCommand(root, config.LauncherScript) + " worktree retire " + family
	return texts.Say(root, textRetireParent, texts.Values{
		"parent": family,
		"path":   refused.Path,
		"detail": strings.ReplaceAll(refused.Detail, "\n", "、"),
		"retire": command,
	}, texts.Variants{"what": which, "next": which})
}

// RunRetire は `worktree retire <親>`。Draft を外した後に、子のワークツリーとブランチ、親のワークツリーを片付ける。
// 親のワークツリーを消せない（cwd が中・未コミットなど）ときは、子に触る前に何も消さずに断る（Drop と同じ判定）。
// 消さなかったものがあれば 1。
func RunRetire(stdout, stderr io.Writer, conf *config.Settings, root, family, cwd string) int {
	family = worktrees.FamilyFor(conf, root, family)
	held, broken := heldEmpty(), ""
	if conf.State != "" {
		held, broken = heldRecord(recordPath(conf, family))
	}
	if broken != "" {
		fmt.Fprintf(stderr, "ccnavi: 親 %s の片付けの記録を読めない（%s）。子の一覧が分からないので、何も消さない。直すか消してから打ち直す\n", family, broken)
		return 1
	}
	if refused := worktrees.Blocked(root, family, cwd); refused != nil {
		fmt.Fprintf(stderr, "%s\n", parentRefusal(root, family, *refused))
		return 1
	}
	children := map[string]child{}
	for _, c := range childrenFrom(held) {
		children[c.ticket] = c
	}
	for _, c := range closedChildren(conf, root, family) {
		if _, ok := children[c.ticket]; !ok {
			children[c.ticket] = c
		}
	}
	parentPath := tree.WorktreePath(root, family)
	branch := textOf(held.Value(keyBranch))
	if branch == "" && ospath.IsDir(parentPath) {
		branch, _ = tree.BranchOf(parentPath)
	}
	reference := ""
	if branch != "" {
		reference = originPrefix + branch
	}
	code := 0
	var asking []outcome
	names := make([]string, 0, len(children))
	for name := range children {
		names = append(names, name)
	}
	slices.Sort(names)
	for _, name := range names {
		c := children[name]
		repo := repoDir(conf, root, c.project)
		if owner, _ := worktrees.Owner(tree.WorktreePath(root, name)); owner != "" {
			repo = owner
		}
		if !dropWorktree(stdout, root, name, cwd, "。子のブランチは条件を確かめて別に片付ける") {
			code = 1
		}
		o := judgeChild(repo, c, reference, false, "! "+confirmCommand(root, name))
		if o.status == ask {
			asking = append(asking, o)
			continue
		}
		say(stdout, o)
		if o.status == kept {
			code = 1
		}
	}
	if len(asking) > 0 {
		fmt.Fprint(stdout, "取り消した子のブランチ（親に取り込まれていないコミットを含みうる）。"+
			"この一覧をユーザに見せる。消すのはユーザで、消してよければ次のコマンドを"+
			"ユーザが先頭に ! を付けて打つ（エージェントからは打てない）:\n")
		for _, o := range asking {
			fmt.Fprintf(stdout, "%s\n", o.text)
		}
		code = 1
	}
	if !dropWorktree(stdout, root, family, cwd, "。ブランチは MR のマージを確かめてから片付ける") {
		code = 1
	}
	return code
}

// RunBranch は `worktree branch <名前> [confirmed]`。片付けで残した子のブランチを、確かめ直して消す。ccnavi が
// 片付ける子として記録したブランチだけ。取り消した子は confirmed（ユーザが確認した）が要る。
func RunBranch(stdout, stderr io.Writer, conf *config.Settings, root, name string, confirmed bool) int {
	var found *child
	record := heldEmpty()
	directory := recordDir(conf)
	var names []string
	if directory != "" {
		names, _ = ospath.DiskListdir(directory)
	}
	for _, entry := range names {
		parent, ok := strings.CutSuffix(entry, ".json")
		if !ok {
			continue
		}
		// 壊れた記録は飛ばして続ける（1 本の壊れで、ほかの親の子のブランチ片付けを止めない）。
		held, broken := heldRecord(ospath.Join(directory, entry))
		if broken != "" || !same(textOf(held.Value(keyParent)), parent) {
			continue
		}
		for _, c := range childrenFrom(held) {
			if same(c.ticket, name) {
				c := c
				found, record = &c, held
			}
		}
	}
	if found == nil {
		fmt.Fprintf(stderr, "ccnavi: %s は、親を閉じたときに片付ける子として記録したブランチではない。任意のブランチは消さない\n", name)
		return 1
	}
	name = found.ticket // 以降は記録の綴りで扱う（ブランチは git が大文字小文字を区別する）
	// 記録は書き換えれば消す対象を広げられるので、記録の子が実際にこの親の子かを確かめ直す。
	parent := textOf(record.Value(keyParent))
	actual := parentOf(conf, root, found.project, name)
	if parent == "" || !same(actual, parent) {
		shownParent, shownActual := parent, actual
		if shownParent == "" {
			shownParent = "空"
		}
		if shownActual == "" {
			shownActual = "なし"
		}
		fmt.Fprintf(stderr, "ccnavi: %s は、記録の親（%s）の子として承認済みチケットにも退避にも見つからない（見つかった親: %s）。記録を信じて消さない\n",
			name, shownParent, shownActual)
		return 1
	}
	reference := ""
	if branch := textOf(record.Value(keyBranch)); branch != "" {
		reference = originPrefix + branch
	}
	o := judgeChild(repoDir(conf, root, found.project), *found, reference, confirmed, "")
	switch o.status {
	case none:
		fmt.Fprintf(stdout, "ブランチ %s はありません（消すものがありません）\n", name)
		return 0
	case ask:
		fmt.Fprintf(stderr, "ccnavi: %s は取り消した子のブランチ。消すのはユーザで、一覧を見て消してよければユーザが '! %s' を打ちます\n",
			name, confirmCommand(root, name))
		return 1
	}
	say(stdout, o)
	if o.status == deleted {
		return 0
	}
	return 1
}

// ---- MR がマージされたと分かった後（片付け 2）

// parentHeadBlocker は親のワークツリーが在るのに、そのままブランチの確認に進めない理由。進めるなら空。
// ワークツリーの HEAD が記録のブランチを指し、その ref が手元に在るときだけ、確認が実際の HEAD を見たことになる。
func parentHeadBlocker(parentPath, branch string, hasRef bool) string {
	head, ok := tree.BranchOf(parentPath)
	if !ok || head != branch || !hasRef {
		return "親のワークツリーの HEAD が記録のブランチ " + branch + " を指していない、" +
			"またはその ref が手元に無いので、成果が失われないか確かめられない" +
			"（git -C " + shell.Quote(parentPath) + " status で HEAD とブランチを確かめてください）"
	}
	return ""
}

// mergedBlocker は親のブランチを消してはいけない理由。消してよければ空。手元の先頭が push 済み（手元の
// remote-tracking ref）に含まれることも確かめる。MR の先頭は sh が渡す値で、ブランチ自身の先頭を渡せば必ず
// 「含まれる」になるため。
func mergedBlocker(repo, branch, tip, headSHA string) string {
	pushed := commitOf(repo, originPrefix+branch)
	if pushed == "" {
		return "push 済みの先頭（origin/" + branch + "）が手元に無く、未 push のコミットが無いか確かめられない"
	}
	if !isAncestor(repo, tip, pushed) {
		return "手元の先頭 " + pyfmt.Head(tip, git.ShortSHA) + " が、push 済みの先頭（origin/" + branch + "）" + pyfmt.Head(pushed, git.ShortSHA) +
			" に含まれない（未 push のコミットがある）"
	}
	mergedHead := commitOf(repo, headSHA)
	if mergedHead == "" {
		return "マージされた MR の先頭 " + pyfmt.Head(headSHA, git.ShortSHA) + " が手元に無く、含まれるか確かめられない"
	}
	if !isAncestor(repo, tip, mergedHead) {
		return "手元の先頭 " + pyfmt.Head(tip, git.ShortSHA) + " が、マージされた MR の先頭 " + pyfmt.Head(mergedHead, git.ShortSHA) +
			" に含まれない（未 push のコミットがある）"
	}
	return ""
}

// RunMerged は `sync tidy <親> <リポジトリ> <ブランチ> <sha>`。マージ済みの MR の先頭 sha を基準に片付ける。
// 1 行ずつ言い、最後の行は `record yes|no`（親のワークツリーも親のブランチも残っていなければ yes で、記録を
// 消した、または元から無い）。
//
// 順は、ブランチの確認、ワークツリーの削除、ブランチの削除。同じ名前のブランチを作り直して使っている親の
// ワークツリー（MR の先頭に含まれない先頭を持つ）を先に消さないため。ブランチが確認を通らなければ、ワークツリー
// も消さない。記録が読めなければ子は片付けず、記録も消さない。
func RunMerged(stdout io.Writer, conf *config.Settings, root, family, project, branch, headSHA, cwd string) int {
	repo := repoDir(conf, root, project)
	clean := true
	tip := tipOf(repo, branch)
	parentPath := tree.WorktreePath(root, family)
	blocker := ""
	if ospath.DiskLexists(parentPath) {
		blocker = parentHeadBlocker(parentPath, branch, tip != "")
	}
	if blocker == "" && tip != "" {
		blocker = mergedBlocker(repo, branch, tip, headSHA)
	}
	if blocker != "" {
		fmt.Fprintf(stdout, "親 %s のワークツリーは、ブランチが片付けの条件を満たさないので消しません\n", family)
		if tip != "" {
			say(stdout, keep(repo, branch, blocker))
		} else {
			fmt.Fprintf(stdout, "親のワークツリーは消しませんでした: %s\n", blocker)
		}
		clean = false
	} else {
		if !dropWorktree(stdout, root, family, cwd, "。ブランチは MR の先頭に含まれるか確かめて片付ける") {
			clean = false
		}
		if tip != "" {
			o := dropBranch(repo, branch, tip, "MR がマージ済みで、先頭が MR に含まれる")
			say(stdout, o)
			if o.status != deleted {
				clean = false
			}
		}
	}
	path := recordPath(conf, family)
	held, broken := heldEmpty(), ""
	if path != "" {
		held, broken = heldRecord(path)
	}
	if broken != "" {
		fmt.Fprintf(stdout, "親 %s の片付けの記録を読めないので、子は片付けません: %s\n", family, broken)
		clean = false
	}
	var asking []outcome
	for _, c := range childrenFrom(held) {
		if !dropWorktree(stdout, root, c.ticket, cwd, "。ブランチは別に片付ける") {
			clean = false
		}
		o := judgeChild(repoDir(conf, root, c.project), c, headSHA, false, "! "+confirmCommand(root, c.ticket))
		switch o.status {
		case ask:
			asking = append(asking, o)
		case none:
		default:
			say(stdout, o)
			clean = clean && o.status == deleted
		}
	}
	if len(asking) > 0 {
		fmt.Fprint(stdout, "取り消した子のブランチが残っている。一覧をユーザに見せる。消すのはユーザで、"+
			"消してよければ次のコマンドをユーザが先頭に ! を付けて打つ:\n")
		for _, o := range asking {
			fmt.Fprintf(stdout, "%s\n", o.text)
		}
	}
	if clean && len(asking) == 0 && path != "" && ospath.DiskLexists(path) {
		fsio.Unlink(path)
	}
	answer := "no"
	if clean {
		answer = "yes"
	}
	fmt.Fprintf(stdout, "record %s\n", answer)
	return 0
}
