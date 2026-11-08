package scope

import (
	"errors"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// Limit は範囲の外へ出した上限。文面でその上限を名指しするために残す（名指しが無いと、承認で見た範囲の中なのに
// 止まった理由を、止められた側が読めない）。
type Limit string

const (
	limitNone   Limit = ""
	limitTicket Limit = "ticket"
	LimitParent Limit = "parent"
	LimitType   Limit = "type"
	// LimitBlocked はチケット自体が信頼できない（Ticket.Blocked）。範囲を当てる前に止める。
	LimitBlocked Limit = "blocked"
)

// Verdict は子のワークツリーの 1 つのパスについて、範囲の上限を合わせた判定。
type Verdict struct {
	// Decision は Allow・Ask・Deny か ticket.Outside。
	Decision rules.Decision
	// Limit は最初に外へ出した上限。中なら limitNone。
	Limit Limit
	Type  *phases.PhaseType
}

// Outside は範囲の外として止める判定か。
func (v Verdict) Outside() bool { return outside(v.Decision) }

func outside(d rules.Decision) bool { return d == ticket.Outside || d == rules.Deny }

// Of は、子の範囲を親の範囲と定義の上限で切り詰める。
// 厳しい側を採るので、順（子 → 親 → 定義）は判定を変えない。Limit は、最初に外へ出した上限を名指しする。
// 定義の上限は、中か外かしか言わない。子が ask と書いた場所が定義の中なら、ask のまま残る。
// 統合の子は親の範囲を当てない。上限は固定の定義と子の allow だけ。
//
// 最初に Blocked を見る。承認のときにしか当たらない構造の検査に引っかかったチケットは、範囲を当てても意味が無い。
// 親が引けない子は、どの範囲で切り詰めるかが決まらない。そのため、範囲の中でも外でも止める。
func Of(child, parent *ticket.Ticket, pt *phases.PhaseType, rel string) Verdict {
	if child.Blocked != "" {
		return Verdict{Decision: rules.Deny, Limit: LimitBlocked, Type: pt}
	}
	v := Verdict{Decision: child.Decide(rel), Type: pt}
	if v.Outside() {
		v.Limit = limitTicket
	}
	narrow := func(by rules.Decision, limit Limit) {
		v.Decision = ticket.Combine(v.Decision, by)
		if v.Limit == limitNone && v.Outside() {
			v.Limit = limit
		}
	}
	if parent != nil && !child.IsConsolidate() {
		narrow(parent.Decide(rel), LimitParent)
	}
	if pt != nil && !pt.Inherit {
		narrow(pt.Decide(rel), LimitType)
	}
	return v
}

// planItem は子の番号が指す、親の計画のステップ。親に計画が無い・計画に無いなら nil。
func planItem(child, parent *ticket.Ticket) *ticket.PlanItem {
	n, isChild := child.ChildPhase()
	if parent == nil || !parent.HasPlan() || !isChild {
		return nil
	}
	return parent.ItemAt(n)
}

// TypeFor は、子の番号の定義を返す。親に計画が無い、番号が計画に無い、定義が引けないときは nil を返す。
// 定義は親に固定したコピーから引き、phases.yml は読まない。承認のあとに直しても、進行中の子の上限が変わらないようにするため。
// コピーに定義が無い親は、判定の側の検査で止まる。子も blocked で止まるので、定義で切り詰めずに通る形は残らない。
func TypeFor(child, parent *ticket.Ticket) *phases.PhaseType {
	if child.IsConsolidate() {
		return phases.ConsolidateType()
	}
	item := planItem(child, parent)
	if item == nil {
		return nil
	}
	return phases.TypesOf(parent).Get(item.Type)
}

// Finding は範囲の外のパス 1 つと、その判定。
type Finding struct {
	Path string
	Verdict
}

// 読めなかった理由。
var (
	errNoWorktree = errors.New("ワークツリーがありません。")
	errNoDiff     = errors.New("子の仕事の始まりからの差分を読めない")
	errNoStatus   = errors.New("ワークツリーの状態を読めない")
)

// diffArgs は、子の仕事の差分のパスを NUL 区切りで読む。
//   - 既定の出力は、非 ASCII と空白を含むパスを引用して 8 進にする。そのまま照らすと、範囲の中の日本語のファイルが必ず範囲外になる。
//   - --no-renames は、改名の移動元を出させる。範囲外のファイルを範囲の中へ改名したものを見落とさないため。
//   - --ignore-submodules=none は、.gitmodules の ignore = all（追跡されるので外から届く）で submodule の進みが消えるのを防ぐ。
func diffArgs(span string) []string {
	return []string{"diff", "--name-only", "--no-renames", "--ignore-submodules=none", "-z", span}
}

// uncommittedStatus の dirty は、submodule の中の未コミットの変更が親のコミットに乗らないから（乗るのはポインタの
// 移動）。none にすると submodule の中の生成物まで範囲外として報告する。
var uncommittedStatus = git.Porcelain{NUL: true, Untracked: git.UntrackedAll, NoRenames: true, IgnoreDirtySubmodules: true}

var scopeOptions = git.Options{Timeout: git.StateTimeout, RawPaths: true}

// committed は子の仕事の始まり（git.ChildBase）から head までに子が変えたパス。統合先を取り込んだ merge が
// 持ち込んだだけのパスは、子が書いたものではないので数えない。
func committed(root string, child *ticket.Ticket, head string) (string, map[string]bool, error) {
	worktree := tree.WorktreePath(root, child.ID)
	if !ospath.IsDir(worktree) {
		return "", nil, errNoWorktree
	}
	base, err := git.ChildBase(worktree, tree.WorktreePath(root, child.Parent), git.StateTimeout, head)
	if err != nil {
		return "", nil, err
	}
	diff := git.Run(worktree, diffArgs(base+".."+head), scopeOptions)
	code, out := diff.Code, diff.Out
	if code != 0 {
		return "", nil, errNoDiff
	}
	paths := map[string]bool{}
	for _, p := range strings.Split(out, "\x00") {
		if p != "" {
			paths[p] = true
		}
	}
	for p := range git.TakenFromRemote(worktree, base, head, git.StateTimeout) {
		delete(paths, p)
	}
	return worktree, paths, nil
}

// Findings は子のワークツリーに残っている範囲外の変更。見るのは子の仕事の始まりから HEAD までのコミット済みの
// 差分と、未コミットの変更の両方（未コミットだけでは、範囲外を書いてコミットしたものが反映されない）。
func Findings(conf *config.Settings, root string, child, parent *ticket.Ticket) ([]Finding, error) {
	worktree, paths, err := committed(root, child, "HEAD")
	if err != nil {
		return nil, err
	}
	status := git.Status(worktree, uncommittedStatus, scopeOptions)
	code, out := status.Code, status.Out
	if code != 0 {
		return nil, errNoStatus
	}
	for _, entry := range strings.Split(out, "\x00") {
		if len(entry) > 3 && entry[2] == ' ' {
			paths[entry[3:]] = true
		}
	}
	outside, _ := outsideOf(conf, child, parent, paths)
	return outside, nil
}

// MergeFindings は、finish が親へ合流する前の検査を行う。答えは、置き場の変更と範囲外の変更。
// 見るのは、子の仕事の始まりから head（呼ぶ側が 1 回だけ読んだ子の先頭）までの、コミット済みの変更だけ。
// 合流するのも同じ head なので、検査のあとのコミットは合流に載らない。
// 置き場（承認済みの領域と提案。マーカーと記録を含む）の変更は、範囲にはかけず別に挙げる。1 つでもあれば合流しない。
// 子のコミットで親へ持ち込ませないため。
func MergeFindings(conf *config.Settings, root string, child, parent *ticket.Ticket, head string) ([]string, []Finding, error) {
	_, paths, err := committed(root, child, head)
	if err != nil {
		return nil, nil, err
	}
	outside, places := outsideOf(conf, child, parent, paths)
	return places, outside, nil
}

// outsideOf は、パスのうち範囲の外のもの（Of）と、範囲にかけないチケットの置き場のパスを返す。下書きの置き場は追跡されるので外す。実行前チェックとそろえる。
//
// git の -z の表記をそのまま使う。git はどの OS でも区切りを "/" で返す。
// "\" を "/" に直すと、Linux・macOS で `wip\handoff\x.py` という名前のファイルが置き場の中と判定され、報告されなくなる。
func outsideOf(conf *config.Settings, child, parent *ticket.Ticket, paths map[string]bool) ([]Finding, []string) {
	pt := TypeFor(child, parent)
	sorted := make([]string, 0, len(paths))
	for p := range paths {
		sorted = append(sorted, p)
	}
	slices.Sort(sorted)
	var outside []Finding
	var places []string
	for _, rel := range sorted {
		if place.IsTicketPlace(rel, conf.Tickets, conf.Approved) {
			places = append(places, rel)
			continue
		}
		if place.IsHandoffPlace(rel) {
			continue
		}
		if v := Of(child, parent, pt, rel); v.Outside() {
			outside = append(outside, Finding{Path: rel, Verdict: v})
		}
	}
	return outside, places
}
