package place

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
)

// State はチケットの状態。置き場のディレクトリの名前そのもの。
type State string

const (
	// Todo と Review は提案の置き場（wip/proposals/）に並ぶ。
	Todo   State = config.TodoDir
	Review State = "review"
	// Doing と Done は承認済みチケットの置き場（.ccnavi/approved/）に並ぶ。
	Doing State = "doing"
	Done  State = "done"
	// Cancelled は置き場ではなく欄。done/ に在って cancelled_at を持つものをこう呼ぶ。
	Cancelled State = "cancelled"
)

var (
	// States は提案の置き場で走査する状態。
	States = []State{Todo, Review}
	// Finished は作業として終わった状態（レビュー待ちも含む）。
	Finished = []State{Review, Done, Cancelled}
	// GuardedStates は直接の作成・移動を止める置き場。todo/ への作成と編集は自由。
	GuardedStates = []State{Review}
)

// progressOrder は置き場の進み具合の並び。
var progressOrder = []State{Todo, Doing, Review, Done}

// Progress は置き場の進み具合（todo < doing < review < done）。空は doing、取り消しは done として読む
// （承認済みチケットは置き場を持たないことがあり、判定と同じく作業中として数える）。知らない状態は 0。
func (s State) Progress() int {
	if s == "" {
		s = Doing
	}
	if s == Cancelled {
		s = Done
	}
	if i := slices.Index(progressOrder, s); i >= 0 {
		return i
	}
	return 0
}

const (
	// Handoff は下書きの置き場（ツリーのルートからの相対）。設定で動かさない。動かせると、その値を
	// ソースの置き場に向けるだけで承認した範囲を迂回して書ける場所ができる。
	Handoff = config.WipRoot + "/" + config.HandoffName
	// ScratchRoot はワークスペースルート直下の一時の置き場。追跡されず、範囲も適用しない。
	ScratchRoot = "scratchpad"
	// WipRoot は途中の作業の置き場。マージの前に全体を消す。
	WipRoot = config.WipRoot

	// ArchiveName は閉じた親子のチケットの退避の置き場の名前（logs/ の下）。
	ArchiveName = config.ArchiveName
	// PhasesDir・FlowsDir は承認済みの領域の下の、マーカーと子のフローの置き場。
	PhasesDir = "phases"
	FlowsDir  = "flows"
	// ReadyDir は退避の置き場の下の、ready が退避したマーカーの置き場。
	ReadyDir = "ready"
	// OpsRel は操作記録の置き場（ワークスペースルートからの相対）。設定で動かない。
	OpsRel = "logs/state/ops"
)

// ArchiveDir はワークスペースルートからの退避の置き場（logs/ は .gitignore に入っている）。
var ArchiveDir = ospath.Join("logs", ArchiveName)

// under は相対パスがその置き場の下にあるか。前置は "/" の境で切り、大文字小文字は区別しない。
// rel の "\" は "/" に直さない（Linux・macOS では `wip\proposals\x` という名前の 1 ファイルになる）。
// 置き場のパス（設定の値）だけは直す。
func under(rel, placeRel string) bool {
	base := pyfmt.Lower(strings.Trim(strings.ReplaceAll(placeRel, `\`, "/"), "/"))
	return base != "" && strings.HasPrefix(pyfmt.Lower(rel), base+"/")
}

// IsTicketPlace はツリーのルートからの相対パスが、提案か承認済みチケットの置き場の下にあるか。
// 範囲の外でも報告しない（親が次の子を提案する経路と、承認がブランチに乗る経路を残すため）。
// review/ と承認済みチケットは組み込みのルールが別に止める。
func IsTicketPlace(rel, ticketsRel, approvedRel string) bool {
	return under(rel, ticketsRel) || under(rel, approvedRel)
}

// InWip は git が出したパスが途中の作業の置き場の下か。大文字小文字と "\" の区切りを問わない。
func InWip(path string) bool {
	folded := pyfmt.Lower(path)
	return folded == WipRoot || strings.HasPrefix(folded, WipRoot+"/") || strings.HasPrefix(folded, WipRoot+`\`)
}

// LeavesOpenState は finish・cancel・ユーザのレビューでチケットが出ていく元（作業中とレビュー待ち）か。
func LeavesOpenState(rel, ticketsRel, approvedRel string) bool {
	return under(rel, approvedRel+"/"+string(Doing)) || under(rel, ticketsRel+"/"+string(Review))
}

// LandsInFinishedState は finish と cancel がチケットを動かす先（レビュー待ちと閉じた置き場）か。
// doing/ から todo/ へ置き直す形を移動と読まないよう、行き先をこの 2 つに絞る。
func LandsInFinishedState(rel, ticketsRel, approvedRel string) bool {
	return under(rel, ticketsRel+"/"+string(Review)) || under(rel, approvedRel+"/"+string(Done))
}

// ScratchDir はワークスペースルート直下の scratchpad/ の絶対パス。区切りは "/"。
func ScratchDir(root string) string {
	return strings.ReplaceAll(ospath.Join(root, ScratchRoot), `\`, "/")
}

// IsHandoffPlace は相対パスが下書きの置き場（wip/handoff/）の下にあるか。大文字小文字は区別しない。
// "\" は "/" に直さない（under と同じ理由）。
func IsHandoffPlace(rel string) bool {
	return strings.HasPrefix(pyfmt.Casefold(rel), Handoff+"/")
}

// IsUnscoped はチケットの範囲を適用しない場所か（実行前チェックが使う）。
func IsUnscoped(rel, ticketsRel, approvedRel string) bool {
	return IsTicketPlace(rel, ticketsRel, approvedRel) || IsHandoffPlace(rel)
}
