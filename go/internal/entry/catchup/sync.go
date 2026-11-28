package catchup

import (
	"bytes"
	"cmp"
	"fmt"
	"io"
	"os"
	"os/signal"
	"slices"
	"strings"
	"syscall"

	"ccnavi/internal/entry/shask"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/diaglog"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/lifecycle/refusal"
	"ccnavi/internal/tickets/lifecycle/retire"
	"ccnavi/internal/tickets/model/ids"
)

// syncHead は ccnavi-sync.sh への答えの頭の行。無ければ sh は「実行ファイルが答えない」として止める。
const syncHead = "sync 1"

// syncUsage は `ccnavi sync <段取り>` の使い方。
const syncUsage = "ccnavi: sync plan|integ|fetched|family|pull|merged|recheck|end <scripts> <引数>...（ccnavi-sync.sh 用）"

// syncRun は ccnavi-sync.sh の 1 回の起動で共通の値。here は sh の置き場（文に出すコマンドの頭）。
type syncRun struct {
	root, here, cwd string
	conf            *config.Settings
	a               answer
}

// Sync は `ccnavi sync <段取り> <sh の置き場> <引数>...`（ccnavi-sync.sh 用）。段取りは sh が外との出入りを挟む順に並ぶ。
//
//	plan [<P>...]                                    取り込む親子のチケットとリポジトリを選ぶ
//	integ <リポジトリ> <has> <rc> <一覧> <err> <symref> 統合先を決める（ls-remote の後）
//	fetched <リポジトリ> <has> <統合先> <host> <rc> <err> 統合先を取ってきた後、origin/HEAD を向け直す
//	family <P> <リポジトリ> <ブランチ> <一覧>            親子のチケット 1 つ（ロックを取った後）
//	pull <P> <リポジトリ> <ブランチ> <rc> <err>          親のブランチを取ってきた後、早送りか merge
//	merged <present|absent|retired> <P> <リポジトリ> <ブランチ> <MR> <rc> <答え> <一覧>
//	                                                  MR がマージ済みかを聞いた後、片付ける
//	recheck <P> <リポジトリ> <ブランチ> <一覧>           取り込みの後の検査
//	end <答え>                                         終了コードを決める（それまでの答えを並べたファイル）
//
// <has> は、そのリポジトリに親子のチケットがあるか（yes・no。無ければ失敗しても終了コードを 1 にしない）。
// <rc>・<err> は sh が打った ls-remote・fetch の終了コードと標準エラーのファイル、<一覧>・<symref>・<答え> は
// 取ってきたものを整えずに書いたファイル。答えの 1 行目は syncHead。返す終了コードは、merge の最中に信号を受けたときの
// 130 のほかは 0（判定の結果は答えの行で言う）。
func Sync(stdout, stderr io.Writer, root string, conf *config.Settings, cwd string, args []string) int {
	if len(args) < 2 {
		fmt.Fprintln(stderr, syncUsage)
		return config.ExitError
	}
	s := &syncRun{root: root, here: args[1], cwd: cwd, conf: conf}
	s.a.Line(syncHead)
	rest := args[2:]
	code := config.ExitOK
	switch {
	case args[0] == "plan":
		s.plan(rest)
	case args[0] == "integ" && len(rest) == 6:
		s.integ(rest[0], rest[1] == "yes", rest[2], rest[3], rest[4], rest[5])
	case args[0] == "fetched" && len(rest) == 6:
		s.fetched(rest[0], rest[1] == "yes", rest[2], rest[3] == "yes", rest[4], rest[5])
	case args[0] == "family" && len(rest) == 4:
		s.family(rest[0], rest[2], rest[3])
	case args[0] == "pull" && len(rest) == 5:
		code = s.pull(rest[0], rest[2], rest[3], rest[4])
	case args[0] == "merged" && len(rest) == 8:
		s.merged(rest[0], rest[1], rest[2], rest[3], rest[4], rest[5], rest[6], rest[7])
	case args[0] == "recheck" && len(rest) == 4:
		s.recheck(rest[0], rest[1], rest[2], rest[3])
	case args[0] == "end" && len(rest) == 1:
		s.a.Exit(exitOf(readText(rest[0])))
	default:
		fmt.Fprintln(stderr, syncUsage)
		return config.ExitError
	}
	s.a.write(stdout)
	return code
}

// ---- plan

// planned は plan が選んだ親子のチケット。
type planned struct{ name, key, branch, path string }

// plan は取り込む親子のチケット（family <P> <リポジトリ> <ブランチ> <親のワークツリー>。ワークツリーは空白を含みうるので最後）、親のワークツリーを片付けた後の親子のチケット
// （retired <P> <リポジトリ> <MR> <ブランチ|->）、リポジトリ（repo <リポジトリ> <has> <ディレクトリ>。origin の無い
// リポジトリは skip <文>）を、sh が処理する順に答える。選べなかった親子のチケットは、その場で out・failed で言う。
//
// <P> を渡せば渡したものだけ、省けば `.claude/worktrees/` の下の親のワークツリー全部と、片付けの記録（retire/<P>.json）
// のうちワークツリーが無いもの。ready の後はワークツリーが無く、片付けの記録だけが残る。
func (s *syncRun) plan(wants []string) {
	for _, want := range wants {
		if !ids.IsValidFamily(want) {
			s.a.Line("err", fmt.Sprintf("ccnavi-sync: %s は親の識別子の形ではありません（親のブランチ名ではなく識別子を渡します）。", want))
			s.a.Line("exit", "2")
			return
		}
	}
	var families []planned
	var retired []retire.Entry
	var keys []string
	addKey := func(key string) {
		if !slices.Contains(keys, key) {
			keys = append(keys, key)
		}
	}
	has := func(name string) bool {
		return slices.ContainsFunc(families, func(f planned) bool { return f.name == name }) ||
			slices.ContainsFunc(retired, func(e retire.Entry) bool { return e.Parent == name })
	}
	addFamily := func(name, path, branch string) {
		key := familyKey(s.root, s.conf, path, name)
		families = append(families, planned{name, key, branch, path})
		addKey(key)
	}
	entries := retire.Entries(s.conf)
	// gone は、そのワークツリーが無いか（片付けの記録を対象にするのは、ワークツリーを片付けた後だけ）。
	gone := func(name string) bool { return !ospath.DiskLexists(tree.WorktreePath(s.root, name)) }
	// fromRecord は、片付けの記録を対象に選ぶか、列挙できない記録を 1 行言う。どちらかなら真。
	fromRecord := func(name string) bool {
		for _, e := range entries {
			if e.Parent != name {
				continue
			}
			if e.Incomplete != "" {
				s.a.out("%s: ready の後の片付けの記録（retire/%s.json）が利用できない（%s）。クローズしたか判断できないので手元は片付けない。MR がマージ済みならブランチとログを手で片付け、破棄した親ならユーザが sh %s/ccnavi-launcher.sh worktree retire --abandon %s で記録を消してください",
					name, name, e.Incomplete, s.here, name)
			} else if !has(name) {
				retired = append(retired, e)
				addKey(e.Repo)
			}
			return true
		}
		return false
	}
	if len(wants) > 0 {
		for _, want := range wants {
			if has(want) || gone(want) && fromRecord(want) {
				continue
			}
			s.wanted(want, addFamily)
		}
	} else {
		addKey(tree.Self)
		for _, p := range tree.Projects(integrate.ProjectsDir(s.conf, s.root)) {
			if !ospath.IsLink(ospath.Join(integrate.ProjectsDir(s.conf, s.root), p.Name)) {
				addKey(p.Project)
			}
		}
		for _, name := range worktreeNames(s.root) {
			if path, branch, why := familyAt(s.root, s.conf, name); why == isFamily {
				addFamily(name, path, branch)
			}
		}
		for _, e := range entries {
			if gone(e.Parent) && !has(e.Parent) {
				fromRecord(e.Parent)
			}
		}
	}
	for _, key := range keys {
		hasFamily := yesNo(slices.ContainsFunc(families, func(f planned) bool { return f.key == key }))
		dir := s.repoDir(key)
		switch {
		case git.Run(dir, []string{"remote", "get-url", "origin"}, git.Options{}).OK():
			s.a.Line("repo", key, hasFamily, dir)
		case hasFamily == "yes":
			// origin の無いリポジトリは取り込みの対象外（今の手元の動きのまま）。
			s.a.Line("skip", key+": origin がありません。取り込みの対象外（今の手元の動きのまま）")
		}
	}
	for _, f := range families {
		s.a.Line("family", f.name, f.key, f.branch, f.path)
	}
	for _, e := range retired {
		s.a.Line("retired", e.Parent, e.Repo, e.MR, cmp.Or(e.Branch, "-"))
	}
}

// wanted は、渡された <P> を親のワークツリーとして選ぶ。選べなければ理由を言って止めたものに数える。
func (s *syncRun) wanted(want string, add func(name, path, branch string)) {
	path, branch, why := familyAt(s.root, s.conf, want)
	switch why {
	case isFamily:
		add(want, path, branch)
	case noTree:
		s.a.fail("%s: 親のワークツリー（.claude/worktrees/%s）がありません。切り直してから再実行してください（sh %s/ccnavi-git.sh fetch origin <ブランチ> のあと worktree add .claude/worktrees/%s <ブランチ>。ブランチ名の / を - に置き換えたものが %s になる）",
			want, want, s.here, want, want)
	case detached:
		s.a.fail("%s: 親のワークツリー（.claude/worktrees/%s）の HEAD からブランチ名を読めない（ブランチの外にいる）。取り込まずに止めた。ブランチ名の / を - に置き換えたものが %s になるブランチに戻してから再実行してください",
			want, want, want)
	case mismatch:
		s.a.fail("%s: 親のワークツリー（.claude/worktrees/%s）の HEAD のブランチ（%s）の / を - に置き換えたものが %s と合わない。取り込まずに止めた。ブランチ名の / を - に置き換えたものが %s になるブランチに戻してから再実行してください",
			want, want, branch, want, want)
	case notParent:
		// ワークツリーを切った直後（ccnavi-start.sh）は、これが正常。failed とは別の語で言い、止めたのがこれだけなら
		// end が終了コード 4 にする。
		s.a.out("%s: .claude/worktrees/%s に親チケットも提案もありません。親のワークツリーではないので取り込まない", want, want)
		s.a.Line("noticket")
	default:
		s.a.fail("%s: .claude/worktrees/%s に親チケットか提案があるかを実行ファイルに確かめられなかった（実行ファイルが無い、またはチケットの frontmatter を読めない）。取り込まない", want, want)
	}
}

// repoDir はリポジトリの名前（self かプロジェクト名）のディレクトリ。
func (s *syncRun) repoDir(key string) string {
	if key == tree.Self {
		return s.root
	}
	return tree.ProjectRoot(integrate.ProjectsDir(s.conf, s.root), key)
}

// yesNo は真偽を yes・no にする。
func yesNo(b bool) string {
	if b {
		return "yes"
	}
	return "no"
}

// ---- リポジトリごと

// label はリポジトリの文の頭（ワークスペースは空、プロジェクトは `<名前>: `）。
func label(key string) string {
	if key == tree.Self {
		return ""
	}
	return key + ": "
}

// repoFail は、リポジトリの段取りが止まったことを言う。親子のチケットの無いリポジトリ（引数を省いた回の
// プロジェクトなど）は、名指しして続け、終了コードを 1 にしない。
func (s *syncRun) repoFail(hasFamily bool, format string, args ...any) {
	if hasFamily {
		s.a.fail(format, args...)
	} else {
		s.a.out(format, args...)
	}
	s.a.next("skip")
}

// integ は統合先の名前を決める。ホストのデフォルトブランチ（`ls-remote --symref origin HEAD` が指すもの。一覧に
// あるもの）、読めなければ手元の origin/HEAD、無ければ一覧にある main・master。決まれば `next fetch <統合先> <host>`
// （host はホストに聞いて得た名前か。fetched が origin/HEAD を向け直すかを決める）。
func (s *syncRun) integ(key string, hasFamily bool, rc, headsFile, errFile, symrefFile string) {
	if rc != "0" {
		s.repoFail(hasFamily, "%sリモートのブランチの一覧を取ってこられなかった（オフラインか、認証の失敗。%s）。止める", label(key), firstLine(errFile))
		return
	}
	dir := s.repoDir(key)
	name, host := defaultBranch(dir, readText(headsFile), readText(symrefFile))
	if name == "" {
		s.repoFail(hasFamily, "%s %s 取り込みを止めた", label(key), integrate.IntegrationHint(dir))
		return
	}
	s.a.out("%s 統合先: %s", label(key), name)
	s.a.next("fetch", name, yesNo(host))
}

// defaultBranch は integ の決め方。host はホストに聞いて得た名前か。
func defaultBranch(dir, heads, symref string) (name string, host bool) {
	name = hostDefault(dir, symref)
	host = name != ""
	local := tree.OriginHead(dir)
	if !host && !strings.HasPrefix(local, "-") {
		name = local
	}
	// ホストの答えが手元の origin/HEAD と綴りの大文字小文字だけ違うなら、同じ名前として手元の綴りを使う。
	if host && local != "" && pathcase.SameName(local, name) {
		return local, false
	}
	if name != "" && hasHead(heads, name) {
		return name, host
	}
	for _, try := range []string{"main", "master"} {
		if hasHead(heads, try) {
			return try, false
		}
	}
	return "", false
}

// hasHead は ls-remote の一覧に refs/heads/<名前> があるか。前方一致で取り違えないよう、2 列目を完全一致で比べる
// （i0131 と i0131-x）。
func hasHead(heads, name string) bool {
	for _, line := range ospath.Splitlines(heads) {
		if _, ref, ok := strings.Cut(line, "\t"); ok && ref == "refs/heads/"+name {
			return true
		}
	}
	return false
}

// fetched は、統合先を取ってきた後。取ってこられなければ止める。ホストに聞いた名前と手元の origin/HEAD が違えば、
// fetch が成功した今、向け直す（fetch の前には向け直さない）。続ければ `next go`。
func (s *syncRun) fetched(key string, hasFamily bool, name string, host bool, rc, errFile string) {
	if rc != "0" {
		s.repoFail(hasFamily, "%s 統合先 %s を取ってこられなかった（%s）。止める", label(key), name, firstLine(errFile))
		return
	}
	if dir := s.repoDir(key); host {
		if old := tree.OriginHead(dir); old != name && retarget(dir, name) {
			s.a.out("%s 統合先を %s から %s に合わせた（ホストの既定ブランチ）", label(key), cmp.Or(old, "未設定"), name)
		}
	}
	s.a.next("go")
}

// ---- 親子のチケットごと（sh がロックを取った後）

// family は、親子のチケット 1 つの最初の判定。親のブランチがリモートにあれば `next fetch <ブランチ>`（取ってきたら
// pull）、無ければ送った形跡を見て、クローズしたかを聞く `next ask <MR>`（聞いたら merged absent）か、今のままと言う。
func (s *syncRun) family(name, branch, headsFile string) {
	path := tree.WorktreePath(s.root, name)
	if here, _ := tree.BranchOf(path); here != branch {
		s.a.fail("%s: 親のワークツリーが %s の上に居る。親のブランチ %s に戻してから再実行してください", name, cmp.Or(here, "（ブランチの外）"), branch)
		return
	}
	if busy := tree.BusyOf(path); busy != "" {
		s.a.fail("%s: 親のワークツリーに途中の操作（%s）がある。済ませるか取りやめてから再実行してください（この sh は変更しない）", name, busy)
		return
	}
	if hasHead(readText(headsFile), branch) {
		s.a.next("fetch", branch)
		return
	}
	// リモートに無い。送った形跡（push のゲートと同じ述語）が無ければ、一度も送っていない親子のチケット。
	p := shask.ParentOf(s.root, s.conf, path, name, branch)
	if !p.Trace {
		if p.Approved == "unreadable" {
			s.a.fail("%s: 承認済みの親チケットを読めないので、送った親子のチケットかを確かめられない（チケットの frontmatter を直してから再実行してください）", name)
			return
		}
		s.a.out("%s: リモートにありません（まだ送っていない親子のチケット）。今の手元の動きのまま（ccnavi-start.sh を通さずに切った親は、approval-request・start がリモートへ送る前に止まる）", name)
		return
	}
	if !isNumber(p.MR) {
		s.a.fail("%s", noMRNote(name, "。止めた（親チケットの mr 欄に MR の番号を書く。ユーザが確かめてください）"))
		return
	}
	s.a.next("ask", p.MR)
}

// isNumber は MR の番号の形か。番号が無ければ聞かない（ディレクトリのブランチから MR を探すと、HEAD が別の
// ブランチなら別の MR を拾うため）。
func isNumber(text string) bool {
	return text != "" && strings.Trim(text, "0123456789") == ""
}

// noMRNote は MR の番号が無いときの 1 行。
func noMRNote(name, tail string) string {
	return name + ": 親チケットの mr 欄に MR の番号が無いので、クローズしたか（MR がマージ済みか）判断できない" + tail
}

// pull は、親のブランチを取ってきた後。早送りできれば早送り、分かれていれば merge する。merge が失敗したら、この
// 起動が始めた merge だけを取りやめる。続けて、MR がマージ済みかを聞く `next ask <MR>`（ホストはマージの後も
// ブランチを残すことがある）。merge の最中に信号を受けたら、取りやめてから 130 を返す。
func (s *syncRun) pull(name, branch, rc, errFile string) int {
	path := tree.WorktreePath(s.root, name)
	if rc != "0" {
		s.a.fail("%s: 親のブランチ %s を取ってこられなかった（%s）", name, branch, firstLine(errFile))
	} else if s.take(name, branch, path) {
		return 130
	}
	// 番号が無ければ推定せず 1 行言う。親が承認待ち（todo/ の提案）のときは、承認済みのチケットだけを読む決まりで
	// 番号を読めないのが正常なので言わない。
	p := shask.ParentOf(s.root, s.conf, path, name, branch)
	switch {
	case isNumber(p.MR):
		s.a.next("ask", p.MR)
	case p.Held == "yes" && p.Approved == "no":
		diaglog.Get("ccnavi-sync", s.root).Debug("承認待ちの親なので mr 欄の警告を出さない", "parent", name)
	default:
		s.a.out("%s", noMRNote(name, "（親のブランチはリモートにある）"))
	}
	return config.ExitOK
}

// take は origin/<ブランチ> を取り込む。信号で切られたら真。
func (s *syncRun) take(name, branch, path string) (interrupted bool) {
	remote := git.Commit(path, "refs/remotes/origin/"+branch, git.Options{})
	local := git.Commit(path, "HEAD", git.Options{})
	switch {
	case remote == "" || local == "":
		s.a.fail("%s: 先頭を読めなかった", name)
	case remote == local:
		s.a.out("%s: リモートと同じ", name)
	case git.IsAncestor(path, remote, local, git.Options{}).OK():
		s.a.out("%s: 手元が %s 件先に進んでいる（まだ送っていない）。取り込むものはありません。", name, cmp.Or(countOf(path, remote+".."+local), "?"))
	case git.IsAncestor(path, local, remote, git.Options{}).OK():
		behind := cmp.Or(countOf(path, local+".."+remote), "?")
		if ok, said := fastForward(path, remote); ok {
			s.a.out("%s: リモートの %s 件を早送りで取り込んだ", name, behind)
		} else {
			s.a.fail("%s: 早送りできなかった。%s", name, refusal.Of(said))
			s.overlapped(said)
		}
	case !git.Run(path, []string{"diff", "--cached", "--quiet"}, git.Options{}).OK():
		// 分かれている。非 ff の merge は、重ならないステージ済みの変更があっても拒む（git 2.43）ので、先に見て言う。
		s.a.fail("%s: リモートと分かれていて merge が必要ですが、ステージ済みの変更がある。コミットするか sh %s/ccnavi-git.sh restore --staged <パス> で外してから再実行してください", name, s.here)
	default:
		return s.merge(name, branch, path, remote)
	}
	return false
}

// merge は分かれた親のブランチを merge する。途中の操作が無いのは family で確かめてあるので、この後の MERGE_HEAD は
// この merge のもの。失敗したとき・信号を受けたときは、それを取りやめる（merge --abort）。信号の戻しは操作を始めた
// 側が持つ決まりで、sh の trap は子を待つだけ。信号で切られたら真。
func (s *syncRun) merge(name, branch, path, remote string) (interrupted bool) {
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM, syscall.SIGHUP)
	defer signal.Stop(signals)
	done := git.Run(path, []string{"merge", "--no-edit", "--quiet", "-m", "ccnavi: origin/" + branch + " を取り込む", remote}, mergeOptions)
	interrupted = len(signals) > 0
	if done.OK() && !interrupted {
		s.a.out("%s: リモートと分かれていたので merge で取り込んだ", name)
		return false
	}
	conflicted, _ := git.Text(path, []string{"diff", "--name-only", "--diff-filter=U"}, git.Options{})
	if gitdir, ok := tree.GitDir(path); ok && ospath.DiskLexists(ospath.Join(gitdir, "MERGE_HEAD")) {
		git.Run(path, []string{"merge", "--abort"}, git.Options{Timeout: git.MergeTimeout})
	}
	switch {
	case interrupted:
	case conflicted != "":
		s.a.fail("%s: リモートと分かれていて merge が衝突した（%s）。取り込みをやめた（merge --abort）。どちらを採るかはユーザが決める",
			name, strings.Join(ospath.Splitlines(conflicted), " "))
	default:
		s.a.fail("%s: リモートと分かれていて merge できなかった。%s", name, refusal.Of(done.Stderr+done.Out))
		s.overlapped(done.Stderr + done.Out)
	}
	return interrupted
}

// overlapped は、取り込みが書きかけと重なって止まったなら overlap の行を足す。end はこれを終了コード 3 にし、
// write-and-push が「書きかけを先にコミットしてから取り込み直す」と言い分ける（文を読ませないため）。
func (s *syncRun) overlapped(said string) {
	if refusal.Overlap(said) {
		s.a.Line("overlap")
	}
}

// exitOf は、1 回の起動の答え（各段取りの答えと sh が足した failed の行を並べたもの）から sync の終了コードを決める。
// 3 取り込みが書きかけと重なって止まったものがある（ほかに止めたものがあっても 3）、1 止めたものがある、4 止めたのは
// 親チケットも提案も無い親のワークツリー（noticket）だけ、0 それ以外。
func exitOf(answers string) int {
	lines := ospath.Splitlines(answers)
	switch {
	case slices.Contains(lines, "overlap"):
		return 3
	case slices.Contains(lines, "failed"):
		return 1
	case slices.Contains(lines, "noticket"):
		return 4
	}
	return 0
}

// merged は、MR がマージ済みかを `ccnavi-review.sh merged <MR>` に聞いた後。答えの 1 行目は
// `merged <番号> <先頭の sha> <元ブランチ>`・`none`（終了コード 0 のときだけ「マージされていない」と読む）・それ以外
// （確かめられない）。where は聞いた場面。
//
//	present  親のブランチがリモートにある。マージ済みなら片付ける。確かめられなければ 1 行言うが、取り込みは済んで
//	         いるので止めない（次の取り込みでまた聞く）
//	absent   親のブランチがリモートに無い。閉じたかの根拠は MR がマージ済みかだけで、マージされていなければ戻し方を
//	         言って止める。確かめられなければ、統合先の done/ や手元の退避からは推定せずに止める
//	retired  親のワークツリーを片付けた後（片付けの記録だけが残る）。ブランチ名は、ワークツリーも HEAD も無いので
//	         ホストが答えた MR の元ブランチを使い、記録のブランチ（- は無い）と合うことを確かめる
func (s *syncRun) merged(where, name, key, branch, mr, rc, answerFile, headsFile string) {
	said := firstLine(answerFile)
	fields := strings.Fields(said)
	isMerged := len(fields) > 0 && fields[0] == "merged"
	none := rc == "0" && said == "none"
	var number, sha, from string
	if isMerged {
		number, sha, from = field(fields, 1), field(fields, 2), field(fields, 3)
	}
	switch where {
	case "present", "absent":
		s.mergedFamily(where, name, key, branch, isMerged, none, number, sha, from)
	case "retired":
		s.mergedRetired(name, key, branch, mr, headsFile, isMerged, none, number, sha, from)
	}
}

// field は fields の i 番目（無ければ空）。
func field(fields []string, i int) string {
	if i < len(fields) {
		return fields[i]
	}
	return ""
}

// mergedFamily は merged の present と absent。
func (s *syncRun) mergedFamily(where, name, key, branch string, isMerged, none bool, number, sha, from string) {
	present := where == "present"
	switch {
	case isMerged && from != "" && from != branch:
		stop := "手元は片付けずに止めた"
		if present {
			stop = "手元は片付けない"
		}
		s.a.fail("%s: マージリクエスト（%s）の元ブランチ %s が親のブランチ %s と違う。%s（親チケットの mr 欄を確かめてください）",
			name, number, from, branch, stop)
	case isMerged && present:
		s.a.out("%s: 親のブランチ %s はリモートに残っているが、マージリクエスト（%s）はマージ済み（クローズ済みの親子のチケット）。手元を片付ける",
			name, branch, number)
		s.tidy(name, key, branch, sha)
	case isMerged:
		s.a.out("%s: リモートから消えたが、マージリクエスト（%s）はマージ済み（クローズ済みの親子のチケット）。手元を片付ける", name, number)
		s.tidy(name, key, branch, sha)
	case none && present:
	case none:
		s.a.fail("%s: 親のブランチ %s がリモートに無く、マージリクエストもマージされていないので、消し間違いか破棄かを決められない。このブランチへの push は止まる（push のゲート）。どちらかをユーザに確かめてもらう",
			name, branch)
		s.a.out("  戻し方 1（改名・消し間違い）: ユーザが手元の親のワークツリーで git push origin %s を一度打つ（ホストの MR の画面から戻してもよい）。戻したら sh %s/ccnavi-sync.sh %s を打ち直す",
			branch, s.here, name)
		s.a.out("  戻し方 2（破棄した）: sh %s/ccnavi-git.sh worktree remove .claude/worktrees/%s で親のワークツリーを片付ける。ready の後にクローズした親の記録は、ユーザが sh %s/ccnavi-launcher.sh worktree retire --abandon %s で消す（エージェントは打たない）",
			s.here, name, s.here, name)
	case present:
		s.a.out("%s: マージリクエストがマージ済みかを確かめられなかった（ccnavi-review.sh merged が結果を返さなかった）。クローズしていれば次の取り込みで片付ける", name)
	default:
		s.a.fail("%s: 親のブランチがリモートに無い。マージリクエストがマージ済みかを確かめられなかった（ccnavi-review.sh merged が結果を返さなかった。gh・glab か、curl とトークンが必要です）。止めた。確かめられる道具を用意して再実行するか、ユーザが確かめてください", name)
	}
}

// mergedRetired は merged の retired。マージされていなければ片付けない（待っている）。
func (s *syncRun) mergedRetired(name, key, recorded, mr, headsFile string, isMerged, none bool, number, sha, from string) {
	switch {
	case isMerged && from == "":
		s.a.fail("%s: マージリクエスト（%s）はマージ済みだが、元ブランチの名前をホストから取れなかった。手元は片付けない", name, number)
	case isMerged && recorded != "-" && recorded != "" && recorded != from:
		s.a.fail("%s: マージリクエスト（%s）の元ブランチ %s が、片付けの記録のブランチ %s と違う。手元は片付けない（記録の MR の番号を確かめてください）",
			name, number, from, recorded)
	case isMerged:
		state := "リモートから消えた"
		if hasHead(readText(headsFile), from) {
			state = "リモートに残っている"
		}
		s.a.out("%s: ワークツリーは片付け済み。親のブランチ %s は%s。マージリクエスト（%s）はマージ済み。手元を片付ける", name, from, state, number)
		s.tidy(name, key, from, sha)
	case none:
		s.a.out("%s: ワークツリーは片付け済み。マージリクエスト（%s）はまだマージされていないので片付けない。破棄した親なら、ユーザが sh %s/ccnavi-launcher.sh worktree retire --abandon %s で記録を消す",
			name, mr, s.here, name)
	default:
		s.a.fail("%s: ワークツリーは片付け済みだが、マージリクエスト（%s）がマージ済みかを確かめられなかった（ccnavi-review.sh merged が結果を返さなかった。gh・glab か、curl とトークンが必要です）。手元は片付けずに止めた", name, mr)
	}
}

// tidy は MR がマージ済みと分かった後の手元の片付け（Tidy。`sync tidy` と同じ）。親のワークツリーと手元のブランチは、
// 手元の先頭が MR の先頭に含まれ（未 push のコミットが無い）、未コミットの変更が無いときだけ消す。消さなかったものは
// 理由と手で消すコマンドを字下げして 1 行ずつ言う。sha が分からないときは、含まれるか確かめられないので何も消さない。
func (s *syncRun) tidy(name, key, branch, sha string) {
	if sha == "" {
		s.a.out("%s: マージリクエストの先頭の sha をホストから取れなかったので、手元のブランチは片付けない（含まれるか確かめられない）", name)
		return
	}
	var out, errOut bytes.Buffer
	if Tidy(&out, &errOut, s.root, s.conf, s.cwd, name, key, branch, sha) != config.ExitOK {
		s.a.fail("%s: 手元の片付けを実行できなかった（%s）。実行ファイルを新しくして再実行してください", name, firstOf(errOut.String()))
		return
	}
	last := ""
	for _, line := range ospath.Splitlines(out.String()) {
		if !strings.HasPrefix(line, "record ") {
			s.a.out("  %s", line)
		}
		last = line
	}
	switch last {
	case "record yes":
		s.a.out("%s: 片付けた", name)
		diaglog.Get("ccnavi-sync", s.root).Info("マージ済みの親を片付けた", "family", name, "repo", key)
	case "record no":
		s.a.out("%s: 消さなかったものがある（理由は上。直してから再実行すれば片付く）", name)
	}
}

// recheck は取り込みの後の検査（Check と同じ判定）。親のブランチがリモートにあり、親のワークツリーが残っている
// （手元の片付けで消していない）ものだけ見る。warn は表示だけ、error があれば言って止めたものに数える。直してから
// 同じ P で打ち直すと検査し直す。
func (s *syncRun) recheck(name, key, branch, headsFile string) {
	if !hasHead(readText(headsFile), branch) || !ospath.IsDir(tree.WorktreePath(s.root, name)) {
		return
	}
	s.checks(name, checked(s.conf, s.root, name, key))
}

// checks は取り込みの後の検査の結果を言う。
func (s *syncRun) checks(name string, problems []problem) {
	var errors []string
	for _, p := range problems {
		switch p.severity {
		case rules.Error:
			errors = append(errors, p.detail)
		case rules.Warn:
			s.a.out("  注意: %s", p.detail)
		}
	}
	if len(errors) == 0 {
		return
	}
	s.a.fail("%s: 取り込みの後の検査で止める理由がある。直してから、sh %s/ccnavi-sync.sh %s を再実行してください（検査し直す）", name, s.here, name)
	for _, e := range errors {
		s.a.out("  - %s", e)
	}
}

// ---- ファイル

// readText は sh が書いたファイルの中身。読めなければ空。
func readText(path string) string {
	text, _ := os.ReadFile(path)
	return string(text)
}

// firstLine はファイルの 1 行目。
func firstLine(path string) string { return firstOf(readText(path)) }

// firstOf は文の 1 行目。
func firstOf(text string) string {
	if lines := ospath.Splitlines(text); len(lines) > 0 {
		return lines[0]
	}
	return ""
}
