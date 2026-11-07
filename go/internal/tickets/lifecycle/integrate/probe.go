package integrate

import (
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/stop"
	"ccnavi/internal/tickets/model/directcand"
)

// State は 1 本のツリーを調べた結果。
type State string

const (
	Hit     State = "hit"     // 促すか、理由を言う相手になる
	Clear   State = "clear"   // 今は促す必要が無いと確認できた
	Unknown State = "unknown" // 期限不足・git の失敗・決められないものがあり、確認できない
)

// Probe は 1 本のツリーを調べた結果。Drop は Clear のうち候補から外してよいと確認できたもの（ディレクトリが
// 無い、HEAD のブランチが名前と違う、チケットに結び付く、integrate が受けない名前）。
type Probe struct {
	State State
	Found *Direct
	Drop  bool
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
		if got.Found != nil && !already(conf, session, got.Found) {
			return got.Found
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
		if Expired(deadline) {
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
		if got.Drop {
			dropped[item.Name] = true
		}
		if got.Found != nil && !already(conf, session, got.Found) {
			result = got.Found
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
		return Probe{State: Clear, Drop: true}
	}
	if unsure {
		return Probe{State: Unknown}
	}
	head := stop.HeadSHA(here.Root, deadline)
	if head == "" {
		return Probe{State: Unknown}
	}
	owner := tree.ProjectRoot(conf.Projects, here.Project)
	if owner == "" {
		owner = root
	}
	dirty, known := Dirty(here.Root, deadline)
	if !known {
		return Probe{State: Unknown}
	}
	if rec := DirectRecord(owner, here.Name); rec != nil {
		return probeRecorded(owner, here, head, dirty, rec, deadline)
	}
	integration, ok := Integration(owner, deadline)
	if !ok {
		return Probe{State: Unknown}
	}
	var ahead int
	if integration != "" {
		ahead, ok = aheadOf(here.Root, integration, deadline)
	} else {
		ahead, ok = unique(here.Root, here.Name, deadline)
	}
	if !ok {
		return Probe{State: Unknown}
	}
	if ahead == 0 {
		return Probe{State: Clear}
	}
	found := &Direct{Name: here.Name, Worktree: here.Root, Head: head, Dirty: dirty, Ahead: ahead,
		Integration: integration, NoRecord: true}
	if integration == "" {
		if runDefault(owner, []string{"remote", "get-url", "origin"}, time.Time{}).OK() {
			found.Blocked = "統合先 (origin/HEAD) が決まらない。ユーザに `git fetch origin` のあと " +
				"`git remote set-head origin -a` を頼んでください"
		} else {
			found.Blocked = "origin が無いので統合先が決まらない。ユーザに origin の追加を頼んでください"
		}
		return Probe{State: Hit, Found: found}
	}
	integTree, ok := integrationTree(owner, integration, deadline)
	if !ok {
		return Probe{State: Unknown}
	}
	found.IntegTree = integTree
	if integTree == "" {
		found.Blocked = "統合先（" + integration + "）をチェックアウトしているツリーが見つかりません。" +
			"どのツリーで取り込むかをユーザに確かめてください"
		return Probe{State: Hit, Found: found}
	}
	marks, ok := GitPath(integTree, RecordDir, deadline)
	if !ok {
		return Probe{State: Unknown}
	}
	found.Marker = MarkerKind(marks, here.Name)
	switch found.Marker {
	case MarkerV4:
		return Probe{State: Clear}
	case MarkerUnknown:
		found.NoRecord = false
		return Probe{State: Hit, Found: found}
	}
	recorded, known := HasRecord(integTree, here.Name, head, &dirty, deadline)
	if !known {
		return Probe{State: Unknown}
	}
	if recorded {
		return Probe{State: Clear}
	}
	found.Blocked = integrationBusy(integTree, deadline)
	return Probe{State: Hit, Found: found}
}

// probeRecorded は記録（取り込み先と起点 S）がある直接作業を調べる。入っていないコミットは S..先頭 で数える。
// 促せないのは、記録が壊れている・共通の前提チェックが通らない・取り込み先が切り替わった・ユーザの
// チェックアウトに途中の操作があるときだけ。ユーザの書きかけでは止めない。
func probeRecorded(owner string, here tree.Tree, head string, dirty bool, rec *Record, deadline time.Time) Probe {
	base := rec.Fields["base_branch"]
	found := &Direct{Name: here.Name, Worktree: here.Root, Head: head, Dirty: dirty, Integration: base,
		IntegTree: owner, Base: base}
	if rec.Broken {
		found.Blocked = "直接作業の記録（ccnavi-direct）が壊れていて、取り込み先と起点を読めません。" +
			"操作せず、ユーザに確かめてください"
		return Probe{State: Hit, Found: found}
	}
	mine, mineOK := tree.CommonDir(here.Root)
	main, mainOK := tree.CommonDir(owner)
	recorded := rec.Fields["common_dir"]
	same := mineOK && mainOK && pathcase.Fold(mine) == pathcase.Fold(main) &&
		recorded != "" && pathcase.Fold(ospath.Realpath(recorded)) == pathcase.Fold(main)
	if !same {
		found.Blocked = "記録の git の共通ディレクトリが、ユーザのチェックアウトと作業ワークツリーのものと" +
			"違います。別のリポジトリの記録かもしれないので、操作せずユーザに確かめてください"
		return Probe{State: Hit, Found: found}
	}
	found.Marker = MarkerKind(ospath.Join(rec.Common, RecordDir), here.Name)
	if current, _ := tree.BranchOf(owner); current != base {
		shown := current
		if shown == "" {
			shown = "detached HEAD"
		}
		found.Blocked = "ユーザのチェックアウト（" + owner + "）の取り込み先が切り替わっています" +
			"（記録: " + base + "、今: " + shown + "）。" +
			"ユーザに戻してもらってください（エージェントは checkout しません）"
		return Probe{State: Hit, Found: found}
	}
	if busy := integrationBusy(owner, deadline); busy != "" {
		found.Blocked = busy
		return Probe{State: Hit, Found: found}
	}
	snap := rec.Fields["snap_sha"]
	done := runDefault(here.Root, []string{"rev-parse", "--verify", "--quiet", snap + "^{commit}"}, deadline)
	if done.Failure != nil {
		return Probe{State: Unknown}
	}
	if !done.OK() {
		found.Blocked = "記録の起点（" + snap + "）のコミットを読めません。操作せず、ユーザに確かめてください"
		return Probe{State: Hit, Found: found}
	}
	ahead, ok := AheadSince(here.Root, snap, deadline)
	if !ok {
		return Probe{State: Unknown}
	}
	found.Ahead = ahead
	found.OriginAhead, _ = count(runDefault(owner,
		[]string{"rev-list", "--count", "refs/heads/" + base + "..refs/remotes/origin/" + base}, deadline))
	switch {
	case found.Marker == MarkerUnknown:
		return Probe{State: Hit, Found: found}
	case found.Marker != MarkerNone || found.Ahead == 0:
		return Probe{State: Clear}
	}
	return Probe{State: Hit, Found: found}
}

// aheadOf は統合先に入っていないコミットの数。手元のブランチを先に見て、無ければ origin/<統合先>。
// 統合先の ref を引けない・数えられないときは偽。
func aheadOf(worktree, integration string, deadline time.Time) (int, bool) {
	for _, ref := range []string{"refs/heads/" + integration, "refs/remotes/origin/" + integration} {
		done := runDefault(worktree, []string{"rev-parse", "--verify", "--quiet", ref + "^{commit}"}, deadline)
		if done.Failure != nil {
			return 0, false
		}
		if !done.OK() {
			continue
		}
		return count(runDefault(worktree, []string{"rev-list", "--count", ref + "..HEAD"}, deadline))
	}
	return 0, false
}

// unique は統合先が決まらないときの目安。ほかのどのブランチにも入っていないコミットの数。
func unique(worktree, name string, deadline time.Time) (int, bool) {
	return count(runDefault(worktree,
		[]string{"rev-list", "--count", "HEAD", "--not", "--exclude=" + name, "--branches", "--remotes"}, deadline))
}

// integrationTree は統合先のブランチをチェックアウトしているツリー。見つからなければ空、読めなければ偽。
func integrationTree(owner, integration string, deadline time.Time) (string, bool) {
	done := runDefault(owner, []string{"worktree", "list", "--porcelain"}, deadline)
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
