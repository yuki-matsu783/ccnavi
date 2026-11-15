package catchup

import (
	"cmp"
	"fmt"
	"io"
	"slices"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/lifecycle/refusal"
)

// fetchHead は ccnavi-fetch.sh への答えの頭の行。
const fetchHead = "fetch 1"

// fetchUsage は `ccnavi fetch <段取り>` の使い方。
const fetchUsage = "ccnavi: fetch plan|forward|track|retarget|base|advance <引数>...（ccnavi-fetch.sh 用）"

// Fetch は `ccnavi fetch <段取り> <引数>...`（ccnavi-fetch.sh 用）。fetch・ls-remote・時間の枠・ロックは sh が持ち、
// ここは何を進めるかの判定と、早送り・update-ref・origin/HEAD の向け直しと、その文を持つ。動いたときだけ out を出す
// （何も動かなかったときに毎回同じ行を返すと、セッションの頭の文脈がそれで埋まる）。
//
//	plan                                 親子のチケットの親のワークツリー（family <P> <ブランチ>）
//	forward <P> <ブランチ> [locked|busy]  親のブランチを origin/<ブランチ> まで早送りする（取ってきた後）。早送りが
//	                                     要れば `next lock` と答え、sh がロックを 1 回だけ試して locked か busy で聞き直す
//	track <ツリー> <ブランチ>             承認済みチケットを持つツリーのブランチを upstream まで早送りする（取ってきた後）
//	retarget <リポジトリ> <symref> [fetched]
//	                                     ホストの既定ブランチが手元の統合先と違えば `next fetch <名前>`。取ってきたら
//	                                     fetched で聞き直し、origin/HEAD を向け直す
//	base <リポジトリ> <見たツリー>         統合先を進めるなら `next fetch <統合先>`。統合先をチェックアウトしている
//	                                     ツリーを第 1 周が見た（<見たツリー> のファイルに並ぶ）なら、そこで済んでいる
//	advance <リポジトリ> <統合先>          統合先を origin/<統合先> まで進める（取ってきた後）
func Fetch(stdout, stderr io.Writer, root string, conf *config.Settings, args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, fetchUsage)
		return config.ExitError
	}
	var a answer
	a.line(fetchHead)
	switch {
	case len(args) == 1 && args[0] == "plan":
		for _, name := range worktreeNames(root) {
			if _, branch, why := familyAt(root, conf, name); why == isFamily {
				a.line("family", name, branch)
			}
		}
	case args[0] == "forward" && (len(args) == 3 || len(args) == 4):
		mode := ""
		if len(args) == 4 {
			mode = args[3]
		}
		forward(&a, root, args[1], args[2], mode)
	case len(args) == 3 && args[0] == "track":
		track(&a, args[1], args[2])
	case args[0] == "retarget" && (len(args) == 3 || len(args) == 4 && args[3] == "fetched"):
		retargetHost(&a, args[1], readText(args[2]), len(args) == 4)
	case len(args) == 3 && args[0] == "base":
		base(&a, args[1], readText(args[2]))
	case len(args) == 3 && args[0] == "advance":
		advance(&a, args[1], args[2])
	default:
		fmt.Fprintln(stderr, fetchUsage)
		return config.ExitError
	}
	return a.write(stdout)
}

// syncCommand は文に出す、取り込みを頼むコマンド。
func syncCommand(root, name string) string {
	return "sh " + root + "/.ccnavi/scripts/ccnavi-sync.sh " + name
}

// forward は親子のチケットの早送り。書きかけとの重なりは git に任せ、拒まれたら重なったパスを言う。途中の状態
// （MERGE_HEAD）を残さないのが早送りだけにした理由で、hook の時間の枠で切られても書きかけが残らない。分かれていれば
// 「取り込みが要る」と 1 行言う（取り込みは手で打つ ccnavi-sync.sh の仕事）。
func forward(a *answer, root, name, branch, mode string) {
	path := tree.WorktreePath(root, name)
	remote := commitOf(path, "refs/remotes/origin/"+branch)
	local := commitOf(path, "HEAD")
	switch {
	case remote == "" || local == "" || remote == local || isAncestor(path, remote, local):
	case !isAncestor(path, local, remote):
		// 分岐か、浅い clone で履歴が足りないだけかは見分けない。見るのは一致しているかどうかだけ。
		a.out("%s: リモートの最新と一致しないので、取り込みが必要です（ローカル %s、リモート %s。浅い clone なら git fetch --unshallow。%s）",
			name, shortOf(path, local), shortOf(path, remote), syncCommand(root, name))
	case mode == "":
		a.next("lock")
	case mode == "busy":
		a.out("%s: 他の操作の最中なので進めなかった。終わってから %s で取り込んでください", name, syncCommand(root, name))
	default:
		behind := cmp.Or(countOf(path, local+".."+remote), "?")
		if ok, said := fastForward(path, remote); ok {
			a.out("%s: 親ブランチ %s を %s コミット進めた", name, branch, behind)
		} else {
			a.out("%s: 早送りできなかった。%s（取り込みは %s）", name, refusal.Of(said), syncCommand(root, name))
		}
	}
}

// dirtyTracked は、追跡しているファイルに未コミットの変更があるか。そこに居るのはユーザか別のセッションの書きかけで、
// セッションの頭に走る hook が動かしてよいものではない。
func dirtyTracked(dir string) bool {
	out, _ := git.Text(dir, []string{"status", "--porcelain", "--untracked-files=no"}, git.Options{})
	return out != ""
}

// track は、承認済みチケットを持つツリーがチェックアウトしているブランチを upstream まで早送りする。
func track(a *answer, dir, branch string) {
	name := ospath.Basename(dir)
	behind := cmp.Or(countOf(dir, "HEAD..@{u}"), "0")
	switch {
	case behind == "0":
	case dirtyTracked(dir):
		a.out("%s: リモートが %s コミット先に進んでいるが、未コミットの変更があるので進めない", name, behind)
	default:
		if ok, _ := fastForward(dir, "@{u}"); ok {
			a.out("%s: 親ブランチ %s を %s コミット進めた", name, branch, behind)
			return
		}
		// 分岐か、浅い clone で履歴が足りないだけかは見分けない。見るのは一致しているかどうかだけ。
		a.out("%s: %s がリモートの最新と一致しないので進めない（ローカル %s、リモート %s）。分岐しているか、clone が浅くて履歴が足りない。ユーザに更新してもらってください（浅いなら git fetch --unshallow）",
			name, branch, shortOf(dir, "HEAD"), shortOf(dir, "@{u}"))
	}
}

// retargetHost は、手元の origin/HEAD をホストの既定ブランチに向け直す。手元の origin/HEAD は clone のときに
// 作られたまま、普通の fetch では更新されない。`git remote set-head origin -a` は手元に origin/<名前> が無いと失敗する
// ので、sh が取ってきてから向け直す。大文字小文字だけ違う名前は同じものとして扱う。向け直したときだけ 1 行出す。
func retargetHost(a *answer, repo, symref string, fetched bool) {
	name := hostDefault(repo, symref)
	old := integrate.IntegrationName(repo)
	switch {
	case name == "" || sameName(name, old):
	case !fetched:
		a.next("fetch", name)
	case retarget(repo, name):
		a.out("%s: 統合先を %s から %s に合わせた（ホストの既定ブランチ）", ospath.Basename(repo), cmp.Or(old, "未設定"), name)
	}
}

// base は、ワークツリーの起点になる統合先（手元の origin/HEAD、無ければ origin/main・origin/master）を進めるかを決める。
// 統合先をチェックアウトしているツリーを第 1 周が見たなら、統合先もそこで済んでいる（結果が「進めなかった」でも理由は
// そこで言っている）ので、同じことを 2 度取ってこない。
func base(a *answer, repo, seen string) {
	name := integrate.IntegrationName(repo)
	if name == "" {
		return
	}
	if here := treeOf(repo, name); here != "" &&
		slices.ContainsFunc(ospath.Splitlines(seen), func(p string) bool { return p != "" && pathcase.SameReal(p, here) }) {
		return
	}
	a.next("fetch", name)
}

// advance は統合先を origin/<統合先> まで進める。チェックアウトされていなくても進める（ワークスペースルートが統合先
// 以外に居るセッションでも、これから切るワークツリーの起点を新しくする）。進めるのは fast-forward だけで、チェック
// アウトしているツリーに未コミットの変更があれば触らない。
func advance(a *answer, repo, name string) {
	label := ospath.Basename(repo)
	ref := "refs/remotes/origin/" + name
	local := "refs/heads/" + name
	old, _ := git.Text(repo, []string{"rev-parse", "--verify", "--quiet", local}, git.Options{})
	if old == "" {
		// 手元に無い。無いままでは `worktree add ... <統合先>` の起点に指せない。
		if !git.Run(repo, []string{"update-ref", "-m", "ccnavi-fetch", local, ref}, git.Options{}).OK() {
			return
		}
		git.Run(repo, []string{"branch", "--quiet", "--set-upstream-to", "origin/" + name, name}, git.Options{})
		a.out("%s: ローカルに統合先ブランチ %s が無かったので、origin/%s から作った", label, name, name)
		return
	}
	behind := cmp.Or(countOf(repo, local+".."+ref), "0")
	if behind == "0" {
		return
	}
	if ahead := cmp.Or(countOf(repo, ref+".."+local), "0"); ahead != "0" {
		// 進められない理由（本当の分岐か、浅い clone で履歴が足りないだけか）は見分けない。
		a.out("%s: ローカルの統合先ブランチ %s がリモートの最新と一致しない（ローカル %s、リモート %s）。分岐しているか、clone が浅くて履歴が足りない。ユーザに更新を頼んでください（浅いなら git fetch --unshallow）",
			label, name, shortOf(repo, local), shortOf(repo, ref))
		return
	}
	stuck := fmt.Sprintf("%s: ローカルの統合先ブランチ %s を進められなかった。ユーザに合流を頼んでください", label, name)
	if here := treeOf(repo, name); here != "" {
		if dirtyTracked(here) {
			a.out("%s: ローカルの統合先ブランチ %s がリモートより %s コミット古いが、未コミットの変更があるので進めない", label, name, behind)
			return
		}
		if ok, _ := fastForward(here, ref); !ok {
			a.out("%s", stuck)
			return
		}
	} else if !git.Run(repo, []string{"update-ref", "-m", "ccnavi-fetch", local, ref, old}, git.Options{}).OK() {
		a.out("%s", stuck)
		return
	}
	a.out("%s: 統合先ブランチ %s を %s コミット進めた", label, name, behind)
}
