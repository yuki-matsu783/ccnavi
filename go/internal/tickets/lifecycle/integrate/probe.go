package integrate

import (
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/stop"
	"ccnavi/internal/tickets/model/directcand"
)

// state は 1 本のツリーを調べた結果。
type state string

const (
	hit     state = "hit"     // 促すか、理由を言う相手になる
	Clear   state = "clear"   // 今は促す必要が無いと確認できた
	unknown state = "unknown" // 期限不足・git の失敗・決められないものがあり、確認できない
)

// Probe は 1 本のツリーを調べた結果。drop は Clear のうち候補から外してよいと確認できたもの（ディレクトリが
// 無い、HEAD のブランチが名前と違う、チケットに結び付く、integrate が受けない名前）。
type Probe struct {
	state state
	found *Direct
	drop  bool
}

// DirectAtStop はメインエージェントが終わろうとしたとき、integrate を促す（か理由を言う）直接作業。cwd のツリー
// を先に調べ、次にこのセッションの候補の記録を、調べた時刻の古い順（未調査が先、同じなら書き込みの新しい順）
// に調べる。調べ始めた候補は結果が不明でも調べた時刻を進め、外してよいと確認できた候補は記録から外す。
// 期限が足りなくなったら打ち切る。session が空なら候補は見ない。
func DirectAtStop(conf *config.Settings, root, cwd, session string, deadline time.Time) *Direct {
	return directAtStop(conf, root, cwd, session, deadline, probe)
}

type prober func(conf *config.Settings, root string, here tree.Tree, deadline time.Time) Probe

func directAtStop(conf *config.Settings, root, cwd, session string, deadline time.Time, look prober) *Direct {
	if cwd == "" {
		cwd, _ = ospath.Getwd()
	}
	skip := ""
	if here, ok := tree.TreeOf(root, cwd, conf.Projects); ok && !here.IsMain() {
		got := look(conf, root, here, deadline)
		skip = here.Name
		if got.found != nil && !already(conf, session, got.found) {
			return got.found
		}
	}
	if session == "" || conf.State == "" {
		return nil
	}
	var entries []directcand.Entry
	for _, e := range directcand.Load(conf, root, session) {
		if skip == "" || pathcase.Fold(e.Name) != pathcase.Fold(skip) {
			entries = append(entries, e)
		}
	}
	slices.SortStableFunc(entries, func(a, b directcand.Entry) int {
		if a.Checked != b.Checked {
			return cmpFloat(a.Checked, b.Checked)
		}
		return cmpFloat(b.Seen, a.Seen)
	})
	checked := map[string]float64{}
	dropped := map[string]bool{}
	var result *Direct
	for _, item := range entries {
		if git.Expired(deadline) {
			break
		}
		checked[item.Name] = fsio.UnixSeconds(time.Now())
		if !ospath.IsDir(item.Path) {
			dropped[item.Name] = true
			continue
		}
		cand, ok := tree.TreeOf(root, item.Path, conf.Projects)
		if !ok || cand.IsMain() || pathcase.Fold(cand.Name) != pathcase.Fold(item.Name) {
			dropped[item.Name] = true
			continue
		}
		got := look(conf, root, cand, deadline)
		if got.drop {
			dropped[item.Name] = true
		}
		if got.found != nil && !already(conf, session, got.found) {
			result = got.found
			break
		}
	}
	directcand.Update(conf, session, checked, dropped)
	return result
}

func cmpFloat(a, b float64) int {
	switch {
	case a < b:
		return -1
	case a > b:
		return 1
	}
	return 0
}

func already(conf *config.Settings, session string, d *Direct) bool {
	return session != "" && stop.NudgedBefore(conf.State, session, d)
}

// probe は 1 本のワークツリーを 3 つの結果のどれかで調べる。
//
// 直接作業でない（DirectCheck の理由）なら Clear で手放し、決められなければ Unknown。記録があれば
// probeRecorded に任せる。記録が無ければ統合先に入っていない分を数え、無ければ Clear（未コミットの変更だけ
// では促さない）。統合先が決まらない・取り込み先のツリーが無いときは理由を付けて Hit。取り込みの途中の
// 記録が版 4 なら Clear、版を知らなければ Hit。同じ HEAD の片付けた記録があれば Clear。
func probe(conf *config.Settings, root string, here tree.Tree, deadline time.Time) Probe {
	reason, unsure := DirectCheck(conf, root, here, deadline)
	if reason != "" {
		return Probe{state: Clear, drop: true}
	}
	if unsure {
		return Probe{state: unknown}
	}
	head := git.Head(here.Root, git.Options{Deadline: deadline})
	if head == "" {
		return Probe{state: unknown}
	}
	owner := tree.ProjectRoot(conf.Projects, here.Project)
	if owner == "" {
		owner = root
	}
	dirty, known := git.Dirty(here.Root, git.Options{Deadline: deadline})
	if !known {
		return Probe{state: unknown}
	}
	if rec := DirectRecord(owner, here.Name); rec != nil {
		return probeRecorded(owner, here, head, dirty, rec, deadline)
	}
	integration, ok := Integration(owner, deadline)
	if !ok {
		return Probe{state: unknown}
	}
	var ahead int
	if integration != "" {
		ahead, ok = aheadOf(here.Root, integration, deadline)
	} else {
		ahead, ok = unique(here.Root, here.Name, deadline)
	}
	if !ok {
		return Probe{state: unknown}
	}
	if ahead == 0 {
		return Probe{state: Clear}
	}
	found := &Direct{Name: here.Name, worktree: here.Root, head: head, dirty: dirty, ahead: ahead,
		integration: integration, noRecord: true}
	if integration == "" {
		if git.Run(owner, []string{"remote", "get-url", "origin"}, git.Options{}).OK() {
			found.Blocked = "統合先 (origin/HEAD) が決まらない。ユーザに `git fetch origin` のあと " +
				"`git remote set-head origin -a` を頼んでください"
		} else {
			found.Blocked = "origin が無いので統合先が決まらない。ユーザに origin の追加を頼んでください"
		}
		return Probe{state: hit, found: found}
	}
	integTree, ok := integrationTree(owner, integration, deadline)
	if !ok {
		return Probe{state: unknown}
	}
	found.integTree = integTree
	if integTree == "" {
		found.Blocked = "統合先（" + integration + "）をチェックアウトしているツリーが見つかりません。" +
			"どのツリーで取り込むかをユーザに確かめてください"
		return Probe{state: hit, found: found}
	}
	marks, ok := GitPath(integTree, RecordDir, deadline)
	if !ok {
		return Probe{state: unknown}
	}
	found.marker = MarkerKind(marks, here.Name)
	switch found.marker {
	case MarkerV4:
		return Probe{state: Clear}
	case MarkerUnknown:
		found.noRecord = false
		return Probe{state: hit, found: found}
	}
	recorded, known := HasRecord(integTree, here.Name, head, &dirty, deadline)
	if !known {
		return Probe{state: unknown}
	}
	if recorded {
		return Probe{state: Clear}
	}
	found.Blocked = integrationBusy(integTree, deadline)
	return Probe{state: hit, found: found}
}

// probeRecorded は記録（取り込み先と起点 S）がある直接作業を調べる。入っていないコミットは S..先頭 で数える。
// 促せないのは、記録が壊れている・共通の前提チェックが通らない・取り込み先が切り替わった・ユーザの
// チェックアウトに途中の操作があるときだけ。ユーザの書きかけでは止めない。
func probeRecorded(owner string, here tree.Tree, head string, dirty bool, rec *Record, deadline time.Time) Probe {
	base := rec.Fields["base_branch"]
	found := &Direct{Name: here.Name, worktree: here.Root, head: head, dirty: dirty, integration: base,
		integTree: owner, base: base}
	if rec.broken {
		found.Blocked = "直接作業の記録（ccnavi-direct）が壊れていて、取り込み先と起点を読めません。" +
			"操作せず、ユーザに確かめてください"
		return Probe{state: hit, found: found}
	}
	mine, mineOK := tree.CommonDir(here.Root)
	main, mainOK := tree.CommonDir(owner)
	recorded := rec.Fields["common_dir"]
	same := mineOK && mainOK && pathcase.Fold(mine) == pathcase.Fold(main) &&
		recorded != "" && pathcase.Real(recorded) == pathcase.Fold(main)
	if !same {
		found.Blocked = "記録の git の共通ディレクトリが、ユーザのチェックアウトと作業ワークツリーのものと" +
			"違います。別のリポジトリの記録かもしれないので、操作せずユーザに確かめてください"
		return Probe{state: hit, found: found}
	}
	found.marker = MarkerKind(ospath.Join(rec.Common, RecordDir), here.Name)
	if current, _ := tree.BranchOf(owner); current != base {
		shown := current
		if shown == "" {
			shown = "detached HEAD"
		}
		found.Blocked = "ユーザのチェックアウト（" + owner + "）の取り込み先が切り替わっています" +
			"（記録: " + base + "、今: " + shown + "）。" +
			"ユーザに戻してもらってください（エージェントは checkout しません）"
		return Probe{state: hit, found: found}
	}
	if busy := integrationBusy(owner, deadline); busy != "" {
		found.Blocked = busy
		return Probe{state: hit, found: found}
	}
	snap := rec.Fields["snap_sha"]
	done := git.Run(here.Root, []string{"rev-parse", "--verify", "--quiet", snap + "^{commit}"}, git.Options{Deadline: deadline})
	if done.Failure != nil {
		return Probe{state: unknown}
	}
	if !done.OK() {
		found.Blocked = "記録の起点（" + snap + "）のコミットを読めません。操作せず、ユーザに確かめてください"
		return Probe{state: hit, found: found}
	}
	ahead, ok := AheadSince(here.Root, snap, deadline)
	if !ok {
		return Probe{state: unknown}
	}
	found.ahead = ahead
	found.originAhead, _ = count(git.Run(owner,
		[]string{"rev-list", "--count", "refs/heads/" + base + "..refs/remotes/origin/" + base}, git.Options{Deadline: deadline}))
	switch {
	case found.marker == MarkerUnknown:
		return Probe{state: hit, found: found}
	case found.marker != markerNone || found.ahead == 0:
		return Probe{state: Clear}
	}
	return Probe{state: hit, found: found}
}

// aheadOf は統合先に入っていないコミットの数。手元のブランチを先に見て、無ければ origin/<統合先>。
// 統合先の ref を引けない・数えられないときは偽。
func aheadOf(worktree, integration string, deadline time.Time) (int, bool) {
	for _, ref := range []string{"refs/heads/" + integration, "refs/remotes/origin/" + integration} {
		done := git.Run(worktree, []string{"rev-parse", "--verify", "--quiet", ref + "^{commit}"}, git.Options{Deadline: deadline})
		if done.Failure != nil {
			return 0, false
		}
		if !done.OK() {
			continue
		}
		return count(git.Run(worktree, []string{"rev-list", "--count", ref + "..HEAD"}, git.Options{Deadline: deadline}))
	}
	return 0, false
}

// unique は統合先が決まらないときの目安。ほかのどのブランチにも入っていないコミットの数。
func unique(worktree, name string, deadline time.Time) (int, bool) {
	return count(git.Run(worktree,
		[]string{"rev-list", "--count", "HEAD", "--not", "--exclude=" + name, "--branches", "--remotes"}, git.Options{Deadline: deadline}))
}

// integrationTree は統合先のブランチをチェックアウトしているツリー。見つからなければ空、読めなければ偽。
func integrationTree(owner, integration string, deadline time.Time) (string, bool) {
	done := git.Run(owner, []string{"worktree", "list", "--porcelain"}, git.Options{Deadline: deadline})
	if !done.OK() {
		return "", false
	}
	want := "branch refs/heads/" + integration
	path := ""
	for _, line := range ospath.Splitlines(done.Out) {
		if rest, ok := strings.CutPrefix(line, "worktree "); ok {
			path = rest
		} else if ospath.Strip(line) == want {
			return path, true
		}
	}
	return "", true
}
