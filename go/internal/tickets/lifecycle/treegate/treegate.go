package treegate

import (
	"fmt"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/sync/mirror"
)

const (
	timeout = 20 * time.Second
	// 文に名指しするパスの数（未コミット）と sha・パスの数（承認の依頼の未 push）。
	shown         = 8
	shownUnpushed = 3
)

// Op はゲートを掛ける操作。値は拒否の文とボードの JSON にも出る。
type Op string

const (
	ApprovalRequest Op = "approval-request"
	Finish          Op = "finish"
	ReviewRequest   Op = "review-request"
	Ready           Op = "ready"
)

// Kind は拒否の種類。`--verify` の `reason` にもこの値が出る。
type Kind string

const (
	DirtyParent  Kind = "dirty_parent"
	UnpushedCode Kind = "unpushed_code"
)

// watch は 1 つの操作が見るもの。ignorePlaces は未 push を置き場の外のパスだけで数えるか、strict は読めない
// とき（git が答えない・比べる origin/<ブランチ> が無い）に止めるか。止めない操作は、一度も送っていない親の
// ブランチでも通る。
type watch struct {
	wipRemovals  bool
	unpushed     bool
	ignorePlaces bool
	strict       bool
}

var watches = map[Op]watch{
	// 親のブランチを push するので、設計途中のコードまで公開しない。
	ApprovalRequest: {unpushed: true, ignorePlaces: true},
	// 子の finish の合流前に、親の側を見る。未 push は合流の後の push で届くので見ない。
	Finish: {strict: true},
	// ユーザが見るのはコードなので、未 push は置き場を除いて数える（置き場の push は別の仕事）。
	ReviewRequest: {unpushed: true, ignorePlaces: true, strict: true},
	// ユーザがリモートを見てマージする。ready が消した wip/ のファイルは write-and-push がコミットする。
	Ready: {wipRemovals: true, unpushed: true, strict: true},
}

// Refusal は操作を止める理由 1 件。Paths は理由になったパス（未 push で置き場を数えないときは sha）。
type Refusal struct {
	Kind   Kind
	Reason string
	Hints  []string
	Paths  []string
}

// Line は理由と次の一手を 1 つにつないだ文。
func (r Refusal) Line() string { return r.Reason + strings.Join(r.Hints, "") }

// InPlace はツリーのルートからの相対パスが、ccnavi 自身の置き場（承認済みチケットと提案）の下か。
func InPlace(conf *config.Settings, rel string) bool {
	tickets, approved := conf.Tickets, conf.Approved
	if tickets == "" {
		tickets = config.DefaultTickets
	}
	if approved == "" {
		approved = config.DefaultApproved
	}
	return place.IsTicketPlace(rel, tickets, approved)
}

// Dirty は置き場の外の未コミットの変更のパス。読めなければ ok が偽。
//
// マーカーはフェーズの終わりに hook が書くので、置き場を数えると「コミットしろ」と言い続ける。未追跡は数えない
// （依頼文を置く使い方があり、MR にも載らない）。-z で読むのは、既定の出力が非 ASCII を 8 進で引用し、日本語の
// ファイルが置き場の外に見えるから。--no-renames は移動元のパスが XY を持たずに入り込むのを避ける。
// wipRemovals なら、ready が消した追跡済みの wip/ のファイルは数えない。
func Dirty(treeRoot string, conf *config.Settings, wipRemovals bool) ([]string, bool) {
	done := git.Run(treeRoot, []string{"status", "--porcelain", "-z", "--untracked-files=no", "--no-renames"},
		git.Options{Timeout: timeout, RawPaths: true})
	if !done.OK() {
		return nil, false
	}
	paths := []string{}
	for _, entry := range strings.Split(done.Out, "\x00") {
		if len(entry) <= 3 || entry[2] != ' ' {
			continue
		}
		path := entry[3:]
		if InPlace(conf, path) {
			continue
		}
		if wipRemovals && strings.Contains(entry[:2], "D") && place.InWip(path) {
			continue
		}
		paths = append(paths, path)
	}
	return paths, true
}

// DiffPaths は ref..HEAD で変わったパス（置き場で絞らない）と、読めなかった理由。
//
// --no-renames は、改名を 1 行にまとめられると置き場の外から中へ動かしたファイルが「置き場の中だけ」に見える
// のを防ぐ。--ignore-submodules=none は `.gitmodules` の `ignore = all` で submodule の進みが消えるのを防ぐ。
// -z のパスは正規化済みなので直さない（`\` を `/` にすると `.ccnavi\tickets\x.py` という 1 個のファイルが
// 置き場の中になる）。
func DiffPaths(treeRoot, ref string) ([]string, string) {
	if ref == "" {
		return nil, "比べる相手がありません。"
	}
	done := git.Run(treeRoot, []string{"diff", "--name-only", "--no-renames", "--ignore-submodules=none", "-z", ref + "..HEAD"},
		git.Options{Timeout: timeout, RawPaths: true})
	if !done.OK() {
		return nil, pyfmt.Head(ref, 12) + " からの差分を読めない"
	}
	paths := []string{}
	for _, p := range strings.Split(done.Out, "\x00") {
		if p != "" {
			paths = append(paths, p)
		}
	}
	return paths, ""
}

// Unpushed は origin/<branch> より進んでいる分（進んでいなければ空）。読めなければ ok が偽。
// ignorePlaces なら進んだ分が変えた置き場の外のパスを、そうでなければ進んだコミットの sha を返す。
func Unpushed(treeRoot string, conf *config.Settings, branch string, ignorePlaces bool) ([]string, bool) {
	done := git.Run(treeRoot, []string{"rev-list", "origin/" + branch + "..HEAD"}, git.Options{Timeout: timeout})
	if !done.OK() {
		return nil, false
	}
	ahead := strings.Fields(done.Out)
	if len(ahead) == 0 || !ignorePlaces {
		return ahead, true
	}
	changed, failed := DiffPaths(treeRoot, "origin/"+branch)
	if failed != "" {
		return nil, false
	}
	out := []string{}
	for _, p := range changed {
		if !InPlace(conf, p) {
			out = append(out, p)
		}
	}
	return out, true
}

// Options は Check の補い。Branch は親のブランチ名（空ならワークツリーの今のブランチ）、Root はワークスペースの
// ルート（承認の依頼の文に載せるコマンドのパス）。PlainDirty なら未コミットをミラーのずれに分けず、ふつうの
// 変更として言う（ワークスペース自身の親の finish）。
type Options struct {
	Branch     string
	Root       string
	PlainDirty bool
}

// Check はこの操作を止める理由。止めなくてよければ空。
func Check(op Op, treeRoot string, conf *config.Settings, o Options) []Refusal {
	w := watches[op]
	var found []Refusal
	paths, ok := Dirty(treeRoot, conf, w.wipRemovals)
	switch {
	case !ok:
		if w.strict {
			tail := ""
			if op == Finish {
				tail = "。何も変えずに止めた"
			}
			found = append(found, Refusal{Kind: DirtyParent, Reason: "親のワークツリーの状態を読めない" + tail})
		}
	case len(paths) > 0:
		found = append(found, DirtyRefusal(op, treeRoot, conf, paths, o.Root, !o.PlainDirty))
	}
	if w.unpushed {
		found = append(found, unpushedRefusal(op, w, treeRoot, conf, o.Branch)...)
	}
	return found
}

// DirtyRefusal は未コミットの変更があるために止めるときの文。paths は Dirty の答え。変更がミラーのコピー先に
// あれば作業の変更ではなく共通レイヤーとのずれなので、コミットを促さずに揃える手順（ユーザの操作）を言う。
func DirtyRefusal(op Op, treeRoot string, conf *config.Settings, paths []string, root string, mirrorGuidance bool) Refusal {
	if op == ApprovalRequest {
		return approvalDirty(treeRoot, paths, root)
	}
	mirrored, others := []string(nil), paths
	if mirrorGuidance {
		mirrored, others = mirror.Split(conf, paths)
	}
	lead := "親のワークツリーに未コミットの変更がある"
	stop := op == Finish
	var reason string
	if len(mirrored) > 0 {
		reason = lead + "（" + mirror.Label(mirrored) + "）。" + mirror.NotWork + mirror.OthersNote(others)
	} else {
		reason = lead + "（" + shownPaths(paths) + "）。"
	}
	if stop && len(others) > 0 {
		reason += "サブエージェントの作業がコミットされていません。"
	}
	if stop {
		reason += "何も変えずに止めた"
	}
	var hints []string
	if stop && len(others) > 0 {
		restore := "親のワークツリー（メインの作業場）には、状態の置き場以外の変更を残さない。" +
			"子のワークツリーへ移してコミットするか、親のワークツリーで " +
			"ccnavi-git.sh restore --staged --worktree -- <パス>（1 つずつ名指し）を打って" +
			"変更を取り除いてから、finish をやり直す"
		if len(mirrored) > 0 {
			restore += "（ミラーのずれは対象外）"
		}
		hints = append(hints, restore)
	}
	if len(mirrored) > 0 {
		hints = append(hints, mirror.Ask)
	}
	return Refusal{Kind: DirtyParent, Reason: reason, Hints: hints, Paths: paths}
}

// approvalDirty は承認を頼むときと承認の結果を知らせるときの文。承認のあとは親のワークツリーへの書き込みが断られる。
func approvalDirty(treeRoot string, paths []string, root string) Refusal {
	gitSh := config.GitWrapper
	if root != "" {
		gitSh = config.ScriptCommand(root, config.GitWrapper)
	}
	restore := "cd " + config.ShellPath(treeRoot) + " && " + gitSh + " restore --staged --worktree -- " + config.ShellPath(paths[0])
	reason := "親 " + nameOf(treeRoot) + " のワークツリーに、提案以外の未コミットの変更がある（" + shownPaths(paths) + "）。" +
		"承認の後は、親のワークツリーへの書き込みが断られ、この変更があると子の finish も止まる。" +
		"自分が書いた変更でなければ、戻す前にユーザへ確かめる（戻すと取り消せない）。" +
		"残す変更は、子のワークツリーへ移してコミットする。" +
		"要らない変更は、パスを 1 つずつ名指しして戻す。例: " + restore
	hint := "親のワークツリーの変更を片付けて、approval-request を打ち直す（何も送っていません）"
	return Refusal{Kind: DirtyParent, Reason: reason, Hints: []string{hint}, Paths: paths}
}

func unpushedRefusal(op Op, w watch, treeRoot string, conf *config.Settings, branch string) []Refusal {
	name := branch
	if name == "" {
		name, _ = tree.BranchOf(treeRoot)
	}
	if name == "" {
		if !w.strict {
			return nil
		}
		return []Refusal{{Kind: UnpushedCode, Reason: "親ブランチの名前を読めない"}}
	}
	ahead, ok := Unpushed(treeRoot, conf, name, w.ignorePlaces)
	if !ok && !w.strict {
		return nil
	}
	if ok && len(ahead) == 0 {
		return nil
	}
	if op == ApprovalRequest {
		return []Refusal{approvalUnpushed(ahead)}
	}
	return []Refusal{{Kind: UnpushedCode, Reason: "親ブランチの HEAD が push されていない", Paths: ahead}}
}

func approvalUnpushed(paths []string) Refusal {
	reason := fmt.Sprintf("親のブランチに未 push のコードの変更が%d件ある（%sなど）。", len(paths), strings.Join(paths[:min(len(paths), shownUnpushed)], "、")) +
		"approval-request は提案だけを送るので、止めた。"
	hint := "コードは先に送るか、手元の変更を整理してから approval-request を打ち直す（何も送っていません）"
	return Refusal{Kind: UnpushedCode, Reason: reason, Hints: []string{hint}, Paths: paths}
}

// nameOf はワークツリーの名前（親の識別子）。
func nameOf(treeRoot string) string {
	p := strings.TrimRight(strings.ReplaceAll(treeRoot, `\`, "/"), "/")
	return p[strings.LastIndex(p, "/")+1:]
}

func shownPaths(paths []string) string {
	more := ""
	if len(paths) > shown {
		more = fmt.Sprintf(" ほか %d 件", len(paths)-shown)
	}
	return strings.Join(paths[:min(len(paths), shown)], ", ") + more
}
