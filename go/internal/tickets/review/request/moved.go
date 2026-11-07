package request

import (
	"fmt"
	"regexp"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/treegate"
	"ccnavi/internal/tickets/sync/mirror"
)

// MovedKind は動いたと数えた理由の種類。止めたあとの説明（MovedExplanation）の出し分けにだけ使い、判定には
// 使わない。
type MovedKind string

const (
	// KindMoved は依頼時の HEAD と今の HEAD が違う。
	KindMoved MovedKind = "moved"
	// KindMirror はプロジェクトの親で、レビューの対象を動かしたのがミラー側だけ。
	KindMirror MovedKind = "mirror"
)

// Moved は依頼の後に動いたかの答え。Text が空なら動いていない（判定）。Kind が空なら説明を足さない
// （依頼時の HEAD の記録が無い・HEAD を読めない・push されていないだけのときは、判定の文で足りる）。
type Moved struct {
	Text string
	Kind MovedKind
}

const (
	msgNoRecordedHead = "依頼時の親の HEAD が記録されていない"
	msgHeadMoved      = "依頼の後に親の HEAD が動いている（依頼時 %s、いま %s）"
	msgHeadUnread     = "親の HEAD を読めない"
	msgUnpushed       = "親ブランチの HEAD が push されていない"
	msgExplainMoved   = "依頼時のコミットといまの HEAD が違う。何が変わったかユーザに確かめてもらい、依頼し直す。"
	msgExplainMirror  = "動かしたのはミラーのずれ（共通レイヤーのコピーか `skills/`）のコミット。" +
		"ユーザが見たものと HEAD が違うので、再 `review-request` が要る。"
	shortSHA = 12
)

// shaForm は依頼のマーカーに記録された HEAD の形。
var shaForm = regexp.MustCompile(`^[0-9a-f]{7,64}$`)

// isSHA はマーカーの head が sha の形をしているか。マーカーは親のブランチに乗って他の機械から届くので、
// 中身を git の revision としてそのまま渡さない（`HEAD` や `@` は差分を空にし、`-` で始まる値はオプションになる）。
func isSHA(value string) bool {
	// Python の re の `$` は末尾の改行 1 つの前でも当たる。
	return shaForm.MatchString(strings.TrimSuffix(value, "\n"))
}

// MovedDetail は、依頼の後にユーザが見るものが動いたかを返す。手元の confirm と Chrome のレビュー済みが、この関数で同じように決める。head は親のブランチの今の先頭である。changed は、依頼時の先頭から head までに変わったパス（置き場で絞る前）である。読めなかった場合（差分を取れない、ホストの一覧が打ち切られた、依頼時のコミットが祖先でない）は listed が偽になり、動いたと数える。project はプロジェクトの親のときに真になる。その場合だけ、ミラー側が動いたもの（KindMirror）を見る。ワークスペース自身の親では、共通レイヤーが本体なので、ずれとは言わない。
//
// ccnavi 自身の置き場（承認済みチケットと提案）だけを変えたコミットは、動いたと数えない。依頼のマーカーはそこに置かれ、親のブランチにコミットされる。数えると、依頼の直後に必ず動いたことになってしまうからである。
func MovedDetail(conf *config.Settings, requested *pyjson.Object, head string, changed []string, listed, project bool) Moved {
	recorded := markText(requested, "head")
	if recorded == "" {
		// 記録が無ければ照合できない。通すと依頼の後のコミットが全部「ユーザが見たもの」になる。
		return Moved{Text: msgNoRecordedHead}
	}
	if head == recorded {
		return Moved{}
	}
	moved := fmt.Sprintf(msgHeadMoved, pyfmt.Head(recorded, shortSHA), pyfmt.Head(head, shortSHA))
	if !isSHA(recorded) || !listed {
		return Moved{moved, KindMoved}
	}
	seen := seenPaths(conf, changed)
	if len(seen) == 0 {
		return Moved{}
	}
	if _, others := mirror.Split(conf, seen); project && len(others) == 0 {
		return Moved{moved, KindMirror}
	}
	return Moved{moved, KindMoved}
}

// seenPaths はユーザが見るもの（ccnavi 自身の置き場の外）のパス。git の -z の表記をそのまま見る。
func seenPaths(conf *config.Settings, paths []string) []string {
	var seen []string
	for _, p := range paths {
		if !treegate.InPlace(conf, p) {
			seen = append(seen, p)
		}
	}
	return seen
}

// MovedInTree は、MovedDetail を親のワークツリーの git から読んで決める。動いていなくても、親のブランチの HEAD が push されていなければ止める。ユーザがマージで見るのはリモートの HEAD だからである。push の判定でも、置き場は数えない。
func MovedInTree(conf *config.Settings, treeRoot string, requested *pyjson.Object, project bool) Moved {
	code, out := git.Output(treeRoot, []string{"rev-parse", "HEAD"}, git.Options{Timeout: gitTimeout})
	head := strings.TrimSpace(out)
	if code != 0 {
		return Moved{Text: msgHeadUnread}
	}
	recorded := markText(requested, "head")
	var paths []string
	listed := false
	if recorded != "" && head != recorded && isSHA(recorded) {
		found, failed := treegate.DiffPaths(treeRoot, recorded)
		paths, listed = found, failed == ""
	}
	if moved := MovedDetail(conf, requested, head, paths, listed, project); moved.Text != "" {
		return moved
	}
	branch, _ := tree.BranchOf(treeRoot)
	if branch == "" {
		return Moved{Text: msgUnpushed}
	}
	if ahead, ok := treegate.Unpushed(treeRoot, conf, branch, true); !ok || len(ahead) > 0 {
		return Moved{Text: msgUnpushed}
	}
	return Moved{}
}

// MovedExplanation は依頼の後に動いて止めたときの説明（標準エラーの行）。判定は変えない。何が変わったかは
// 断定せず、確かめてもらって依頼し直すことだけを言う。ミラー側だけが動いたときは直し方が違うので 1 文足す。
func MovedExplanation(m Moved) []string {
	if m.Text == "" || m.Kind == "" {
		return nil
	}
	lines := []string{msgExplainMoved}
	if m.Kind == KindMirror {
		lines = append(lines, msgExplainMirror)
	}
	return lines
}
