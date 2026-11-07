package lint

import (
	"fmt"
	"maps"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/lifecycle/branch"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// チケットのブランチの検査。ブランチ名・番号・接頭辞の設定・既にあるブランチ・ワークツリーを見る。どれも warn で、
// 拒否は ccnavi-git.sh が受け持つ。

// branchRule は親のブランチ名の規則についての warn の結び。
const branchRule = "（親のブランチ名の規則）"

// gitdirHead は `.git` ファイルの頭。
const gitdirHead = "gitdir:"

// branchNameProblems は新しく切る親の識別子を、ブランチ名として使える形にできるか。既存のブランチを採用した親
// （adopted）には、名前の形式と番号の重なりを言わない（ブランチ名は採用の時点で決まっている）。大文字小文字だけが
// 違う識別子（大文字小文字を区別しないファイルシステムでブランチとワークツリーがぶつかる）と、末尾が `-<2 桁>` の
// 親の識別子（子の識別子そのもの、または子の識別子の途中と紛れる）も言う。
func branchNameProblems(conf *config.Settings, root string, proposals, copies, closed, review []*ticket.Ticket,
	integrationOf func(string) string, adopted map[string]bool) []rules.Problem {
	var out []rules.Problem
	warn := func(detail string) { out = append(out, ticketProblem(rules.Warn, detail)) }
	everyone := slices.Concat(copies, closed, review, proposals)
	settled := map[string]bool{}
	for _, t := range slices.Concat(copies, closed, review) {
		settled[t.ID] = true
	}
	// ticket next-id と同じ集め方（手元のブランチとワークツリーも数える）。数えきれなければ、番号を決めずにそう言う。
	known := &branch.Known{Copies: copies, Closed: closed, Review: review, Proposals: proposals}
	if _, counting := branch.SerialNames(conf, root, known); len(counting) > 0 {
		warn("次の通し番号を数えきれなかった（" + strings.Join(counting, "。") + "）。番号を決めずに、ユーザに確かめる" + branchRule)
	}
	prefixes := conf.BranchPrefixes
	said := map[string]bool{}
	var fresh []*ticket.Ticket
	for _, t := range proposals {
		if t.State != place.Todo || settled[t.ID] || said[t.ID] {
			continue
		}
		said[t.ID] = true
		if adopted[t.ID] {
			continue
		}
		fresh = append(fresh, t)
		for _, text := range t.BranchNameProblems(integrationOf(t.Project), prefixes) {
			warn(t.ID + ": " + text + branchRule)
		}
	}
	out = append(out, numberProblems(everyone, fresh, prefixes)...)

	var folds []string
	spellings := map[string]map[string]bool{}
	for _, t := range everyone {
		key := pyfmt.Casefold(t.ID)
		if spellings[key] == nil {
			folds = append(folds, key)
			spellings[key] = map[string]bool{}
		}
		spellings[key][t.ID] = true
	}
	for _, key := range folds {
		if names := slices.Sorted(maps.Keys(spellings[key])); len(names) > 1 {
			warn(strings.Join(names, " と ") + " は大文字小文字だけが違う。大文字小文字を区別しないファイルシステムでは、" +
				"ブランチとワークツリーの名前がぶつかる" + branchRule)
		}
	}

	parents := map[string]bool{}
	for _, t := range everyone {
		if !t.IsChild() {
			parents[t.ID] = true
		}
	}
	for _, name := range slices.Sorted(maps.Keys(parents)) {
		if !ids.HasChildTail(name) {
			continue
		}
		how := "識別子の末尾が `-<2 桁>` で、別の親の子の識別子の途中（`<親>-<2 桁のフェーズ番号>`）と紛れる"
		if child, ok := ids.ParseChild(name); ok {
			how = "識別子が子の形（`<親>-<2 桁のフェーズ番号>-<2 桁の連番>`）と一致する。親子のチケットをまとめるとき " +
				child.Parent + " の子として扱われる"
		}
		warn(name + " は親なのに、" + how + "。親の識別子の末尾を `-<2 桁>` にしないでください" + branchRule)
	}
	return out
}

// numberProblems は新規の提案の親の識別子の番号が、同じリポジトリの別の親と重なるか。番号は issue の番号で、issue の
// 番号はリポジトリごとなので、比べるのは同じリポジトリの親どうしだけ。
func numberProblems(everyone, fresh []*ticket.Ticket, prefixes []string) []rules.Problem {
	type key struct {
		project string
		number  int
	}
	owners := map[key]map[string]bool{}
	for _, t := range everyone {
		if t.IsChild() {
			continue
		}
		if n, ok := ids.IdentifierNumber(t.ID, prefixes); ok {
			k := key{t.Project, n}
			if owners[k] == nil {
				owners[k] = map[string]bool{}
			}
			owners[k][t.ID] = true
		}
	}
	var out []rules.Problem
	for _, t := range fresh {
		if t.IsChild() {
			continue
		}
		n, ok := ids.IdentifierNumber(t.ID, prefixes)
		if !ok {
			continue
		}
		var others []string
		for _, id := range slices.Sorted(maps.Keys(owners[key{t.Project, n}])) {
			if id != t.ID {
				others = append(others, id)
			}
		}
		if len(others) > 0 {
			out = append(out, ticketProblem(rules.Warn, fmt.Sprintf("%s: 識別子の番号 %d が %s と重なる。"+
				"別のリポジトリの issue や直接作業の通し番号と同じ数になっていないか確かめる%s", t.ID, n, strings.Join(others, ", "), branchRule)))
		}
	}
	return out
}

// prefixSettingProblems は CCNAVI_BRANCH_PREFIXES に書かれた、先頭の語に使えない語。
func prefixSettingProblems(conf *config.Settings) []rules.Problem {
	if len(conf.BranchPrefixesRejected) == 0 {
		return nil
	}
	return []rules.Problem{{Severity: rules.Warn, Rule: whereSettings, Detail: fmt.Sprintf(
		"%s の %s は先頭の語に指定できないので読まない（英小文字で始まり、英小文字と数字だけでできた語。main・master・"+
			"develop・release は指定できない）。使うリストは %s%s", env.BranchPrefixes, strings.Join(conf.BranchPrefixesRejected, ", "),
		strings.Join(conf.BranchPrefixes, ", "), branchRule)}}
}

// worktreeProblems はワークツリーの側。チケットの無いツリー、元リポジトリの食い違い（書き込みが止まるので error）、
// ツリーの無い承認済みチケット。known は未承認・閉じた・審査中を含む識別子で、直接作業の名前だけをこれと照らす。
func worktreeProblems(conf *config.Settings, root string, worktrees []tree.Tree, index map[string]*ticket.Ticket,
	copies []*ticket.Ticket, known map[string]bool) []rules.Problem {
	var out []rules.Problem
	for _, w := range worktrees {
		bound := index[w.Name]
		if bound == nil {
			out = append(out, ticketProblem(rules.Warn, "ワークツリー "+w.Name+" にチケットがありません。そこへの書き込みはルールだけで判定する"))
			if text := ids.DirectNameWarning(w.Name, conf.BranchPrefixes, root); text != "" && !known[w.Name] {
				out = append(out, ticketProblem(rules.Warn, text))
			}
			continue
		}
		owner := bound.Project
		if bound.IsChild() {
			if parent := index[bound.Parent]; parent != nil {
				owner = parent.Project
			}
		}
		if owner != w.Project {
			out = append(out, ticketProblem(rules.Error, fmt.Sprintf("ワークツリー %s の元リポジトリ（%s）が承認済みチケットの "+
				"project（%s）と違う。そこへの書き込みは止まる。承認済みチケットが指すリポジトリから切り直してください",
				w.Name, orWorkspace(w.Project), orWorkspace(owner))))
		}
	}
	names := map[string]bool{}
	for _, w := range worktrees {
		names[w.Name] = true
	}
	for _, t := range copies {
		if !names[t.ID] {
			out = append(out, ticketProblem(rules.Warn, t.ID+" は承認済みだがワークツリー "+tree.WorktreePath(root, t.ID)+
				" がありません。ワークツリーを作るまで範囲は有効にならない"))
		}
	}
	return out
}

func orWorkspace(project string) string {
	if project == "" {
		return "ワークスペース"
	}
	return project
}

// placeProblems は未承認の親の提案の置き場所と中身の形。親の提案が識別子と同名でないワークツリーに在る・子の提案が
// 親の識別子と同名のワークツリーに無い（親が取り込み済みにならない）、計画を持たない親（ticket start が止まる）。
// ワークスペースルートとプロジェクトの元ツリーの提案は置き場所を言わない。
func placeProblems(root string, proposals, settledList []*ticket.Ticket, repoOf map[string]string) []rules.Problem {
	settled := map[string]bool{}
	for _, t := range settledList {
		settled[t.ID] = true
	}
	// 親のワークツリーは ccnavi-start.sh が作る（MR の元ブランチを直接チェックアウトする）。
	shown := root
	if shown == "" {
		shown = "<ルート>"
	}
	cut := "sh " + shown + "/" + config.DefaultProjectHome + "/" + config.ScriptsDir + "/" + config.StartWrapper + " --mr <MR の番号>"
	var out []rules.Problem
	said := map[[2]string]bool{}
	for _, t := range proposals {
		if t.State != place.Todo {
			continue
		}
		at := t.Tree
		if repo, ok := repoOf[at]; at != "" && ok && repo == at {
			at = "" // プロジェクトの元ツリー
		}
		if t.IsChild() {
			if at == "" || at == t.Parent || settled[t.Parent] || t.Parent == "" {
				continue
			}
			k := [2]string{t.ID, at}
			if said[k] {
				continue
			}
			said[k] = true
			out = append(out, ticketProblem(rules.Warn, fmt.Sprintf("%s: 子の提案を %s に書いている。親 %s のワークツリーは"+
				"識別子と同じ名前にし、子の提案はそこに置く。ワークツリーが無ければ `%s`（MR が無ければ `--issue <issue の番号>`）で作り、"+
				"提案を移して元を削除します。このままだと親が取り込み済みにならない", t.ID, at, t.Parent, cut)))
			continue
		}
		if settled[t.ID] {
			continue
		}
		if at != "" && at != t.ID {
			out = append(out, ticketProblem(rules.Warn, fmt.Sprintf("%s: 提案 %s を %s に書いている。親のワークツリーは"+
				"識別子と同じ名前にする。`%s`（MR が無ければ `--issue <issue の番号>`）で作り、提案を移して元を削除します。"+
				"このままだと親が取り込み済みにならない", t.ID, t.ID, at, cut)))
		}
		if !t.HasPlan() {
			out = append(out, ticketProblem(rules.Warn, fmt.Sprintf("%s: 親 %s に plan がありません。"+
				"plan の無い親は `ticket start` が止まる（子や子の提案があっても同じ）。"+
				"`ccnavi-launcher.sh --docs --keyword 提案書式` と `--keyword phases` で書式と型の決め方を引き、"+
				"phases.yml の定義を使う plan を追加します。", t.ID, t.ID)))
		}
	}
	return out
}

// unregisteredWorktrees は `.claude/worktrees/` 直下の、どこにも登録の無いディレクトリ。ワークツリーの一覧はファイル
// だけで決める（実行前チェックは git を起こさない）が、ここは --lint の中だけなので `git worktree list` にも聞く。
// 他のリポジトリの `.git/worktrees` に登録されたものは言わない。git に聞けなければ何も言わない。
func unregisteredWorktrees(root string, worktrees []tree.Tree) []rules.Problem {
	base := tree.WorktreesPath(root)
	names, err := ospath.DiskListdir(base)
	if err != nil {
		return nil
	}
	known := map[string]bool{}
	for _, w := range worktrees {
		known[w.Name] = true
	}
	var pending []string
	for _, n := range names {
		if !known[pyfmt.NFC(n)] && ospath.IsDir(ospath.Join(base, n)) && !strings.HasPrefix(n, ".") {
			pending = append(pending, n)
		}
	}
	if len(pending) == 0 {
		return nil
	}
	code, text := git.Output(root, []string{"worktree", "list", "--porcelain"}, git.Options{Timeout: git.ReadTimeout})
	if code != 0 {
		return nil
	}
	listed := map[string]bool{}
	for _, line := range ospath.Splitlines(text) {
		if rest, ok := strings.CutPrefix(line, "worktree "); ok {
			listed[ospath.Realpath(ospath.Strip(rest))] = true
		}
	}
	registry := ospath.Realpath(ospath.Join(root, tree.GitWorktrees))
	var out []rules.Problem
	for _, name := range pending {
		path := ospath.Join(base, name)
		if listed[ospath.Realpath(path)] || registeredElsewhere(path, registry) {
			continue
		}
		out = append(out, ticketProblem(rules.Warn, ".claude/worktrees/"+name+" は git に登録がありません。中身を確かめ、不要なら"+
			"ユーザに確かめてから ccnavi-clean.sh "+name))
	}
	return out
}

// registeredElsewhere は `.git` が、ワークスペースのものではない `.git/worktrees/` を指しているか。
func registeredElsewhere(path, registry string) bool {
	text, err := ospath.ReadDiskText(ospath.Join(path, tree.DotGit))
	if err != nil {
		return false
	}
	line := ospath.Strip(text)
	if !strings.HasPrefix(line, gitdirHead) {
		return false
	}
	gitdir := strings.ReplaceAll(ospath.Strip(line[len(gitdirHead):]), `\`, "/")
	if !ospath.IsAbs(gitdir) {
		gitdir = ospath.Join(path, gitdir)
	}
	gitdir = ospath.Realpath(gitdir)
	return strings.Contains(strings.ReplaceAll(gitdir, `\`, "/"), "/"+ospath.Slashed(tree.GitWorktrees)+"/") && !strings.HasPrefix(gitdir, registry+ospath.Sep)
}
