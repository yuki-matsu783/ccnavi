package request

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/lifecycle/closing"
	"ccnavi/internal/tickets/lifecycle/treegate"
	"ccnavi/internal/tickets/model/fields"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/model/useronly"
	"ccnavi/internal/tickets/phase"
)

// ResolveNote は依頼の本文の末尾（HandoffNote のあと）と、fix を選んだあとの文に足す 1 文。fix で直した指摘の
// スレッドはホストの上で未解決のまま残る（仕様）。resolve するのはユーザで、エージェントは fix を理由に
// resolve しない。依頼の目印のキーはこの文を足す前の本文から作る（足す前に投稿した依頼を打ち直しても
// 二重に投稿しない）。
const ResolveNote = "修正を確認したら、ユーザがホスト上で該当スレッドを resolve する。" +
	"エージェントは fix を理由に resolve しない。"

// handoffNote は依頼の本文の末尾に必ず書く 1 文。下書きの置き場の変更を、レビュアーが読む対象に数えないため。
const handoffNote = place.Handoff + "/ 配下はユーザに渡すコピーで、レビューの対象外。integrate で消える。"

// requestFile は state の置き場に書く、投稿待ちの本文の名前（sh がこれを投稿する）。
const requestFile = "review-request-%s-%d.md"

// keyDigits は依頼の目印のキーの桁数。
const keyDigits = 16

// 依頼の文。sh が読むので変えない。
const (
	msgNoState        = "state の置き場が空。投稿する本文を置く場所がありません"
	msgBodyUnread     = "依頼文を読めない (%s)"
	msgBodyEmpty      = "依頼文が空"
	msgUnmetHead      = "ccnavi: 依頼の前提が %d 件満たされていない\n"
	msgCheckOK        = "確認: ok（review-request を打てる状態。何も変えていません）\n"
	msgWriteFailed    = "ccnavi: 本文を書き出せない (%s)\n"
	msgNoURL          = "ccnavi: 結果に投稿の url がありません。投稿されていないならマーカーは置かない\n"
	msgBrokenAfter    = "ccnavi: 投稿の後に前提が成り立たなくなった。マーカーは置かない\n"
	msgRequestedOK    = "OK: レビューを%s（%s）\n"
	msgAgain          = "依頼し直した"
	msgFirst          = "依頼した"
	msgReviewedPhase  = "フェーズ %d はレビュー済み"
	msgRequestedPhase = "フェーズ %d は依頼済み（ユーザが見るものは依頼時のまま）"
	msgNotEnded       = "フェーズが終わっていない（doing/ に子が残っている）"
	msgNoMergedSHA    = "子 %s が親のブランチに合流していない（finish が合流した記録 merged_sha が無い）"
	msgNotAncestor    = "子 %s の合流した先頭（%s）が親の HEAD に無い"
	msgLateFix        = "子 %s の手元のブランチに、合流されていない修正がある（先頭 %s。合流した先頭は %s）。" +
		"finish の後に積んだコミットは親のブランチに入らない。直すときは、同じフェーズに子を足してユーザの承認を受け、その子で直す"
	msgCovered      = "このレビューが含むフェーズ: "
	msgRisk         = "このレビューのリスク: "
	msgRiskEscalate = "（実績のリスクが高いので、宣言に関わらずレビュー必須の扱い）\n"
	msgMRFailed     = "ccnavi: 警告: %s の `mr:` に MR !%d を保存できませんでした: %s\n"
	msgMRSet        = "ccnavi: %s の `mr:` に MR !%d を追記しました\n"
	msgMRDiffers    = "ccnavi: 警告: %s の `mr:` に別の値が既にある。上書きしない。今回のレビューの MR は !%d。\n%s\n"
	msgMRUnreadable = "ccnavi: 警告: %s の frontmatter を読めず、`mr:` を保存できませんでした\n"
)

// Gated は Gate を通った依頼の材料。
type Gated struct {
	Parent   *ticket.Ticket
	Phase    *phase.Phase
	TreeRoot string
	Body     string
}

// Gate は review-request の、ワークツリーの中で分かる前提の検査。本体（Prepare）と `prepare --check` が同じ
// ものを通る。断ったときは理由のあとに `次:` を 1 行足して nil。ホストの状態（マージリクエストの有無・向き先）
// は見ない（sh が取ってくる）。
func Gate(stderr io.Writer, conf *config.Settings, root, cwd string, number int, bodyFile string) (*Gated, error) {
	parent, p, ok := ParentPhase(stderr, conf, root, cwd, number)
	if !ok {
		refused(stderr, root, number)
		return nil, nil
	}
	var unmet []string
	if conf.State == "" {
		unmet = append(unmet, msgNoState)
	}
	treeRoot := tree.WorktreePath(root, parent.ID)
	unmet = append(unmet, unmetIn(root, treeRoot, conf, p)...)
	body, read := fsio.ReadText(resolve(cwd, bodyFile), fsio.Strict)
	if !read {
		unmet = append(unmet, fmt.Sprintf(msgBodyUnread, bodyFile))
		body = ""
	}
	if ospath.Strip(body) == "" {
		unmet = append(unmet, msgBodyEmpty)
	}
	if already := alreadyRequested(conf, treeRoot, p, number); already != "" {
		unmet = append(unmet, already)
	}
	if p.Deferred() {
		at := "None"
		if n, ok := p.ReviewAt(); ok {
			at = strconv.Itoa(n)
		}
		unmet = append(unmet, texts.Say(root, "NOTICE_REVIEW_REQUEST_DEFERRED", texts.Values{"label": p.Label(), "at": at}, nil))
	}
	if len(unmet) > 0 {
		fmt.Fprintf(stderr, msgUnmetHead, len(unmet))
		for _, line := range unmet {
			fmt.Fprintf(stderr, "  - %s\n", line)
		}
		refused(stderr, root, number)
		return nil, nil
	}
	return &Gated{Parent: parent, Phase: p, TreeRoot: treeRoot, Body: body}, nil
}

// refused は断った理由のあとに、打ち直す 1 手を `次:` で足す。
func refused(stderr io.Writer, root string, number int) {
	fmt.Fprintln(stderr, texts.Say(root, "NEXT_REVIEW_REQUEST_REFUSED", texts.Values{"phase": strconv.Itoa(number)}, nil))
}

// Check は `review prepare --check`。Gate を通すだけで何も書かない。sh が取り込みの前に呼ぶ。
func Check(stdout, stderr io.Writer, conf *config.Settings, root, cwd string, number int, bodyFile string) (int, error) {
	gated, err := Gate(stderr, conf, root, cwd, number, bodyFile)
	if gated == nil {
		return config.ExitError, err
	}
	fmt.Fprint(stdout, msgCheckOK)
	return config.ExitOK, nil
}

// Prepare は前提を全部確かめ、投稿する本文を state の置き場に書き出す。標準出力の 1 行がその置き場。
// マージリクエストは作らない（無ければ sh が止める）。
func Prepare(stdout, stderr io.Writer, conf *config.Settings, root, cwd string, number int, bodyFile string) (int, error) {
	gated, err := Gate(stderr, conf, root, cwd, number, bodyFile)
	if gated == nil {
		return config.ExitError, err
	}
	// 計画があれば、このレビューが含むフェーズを機械が先頭に書く（延期した分を読み落とさないように）。
	body := coveredHeader(conf, root, gated.Parent, gated.Phase) + gated.Body
	body = strings.TrimRight(body, "\n") + "\n\n" + handoffNote + "\n"
	// 目印に本文と親のブランチの先頭から作ったキーを入れる。sh は同じ目印の投稿が MR に既にあれば投稿し直さない。
	// 子を足してやり直した依頼は先頭が違うので、別のキーになる。
	head := ""
	if done := git.Run(gated.TreeRoot, []string{"rev-parse", "HEAD"}, git.Options{}); done.OK() {
		head = strings.TrimSpace(done.Out)
	}
	sum := sha256.Sum256([]byte(body + "\n" + head))
	key := hex.EncodeToString(sum[:])[:keyDigits]
	marker := fmt.Sprintf("%s%s:%d key=%s -->\n", host.MarkerRequest, gated.Parent.ID, number, key)
	path := requestPath(conf, gated.Parent.ID, number)
	if failed := fsio.WriteText(path, marker+body+"\n"+ResolveNote+"\n", fsio.LF, fsio.Plain); failed != "" {
		fmt.Fprintf(stderr, msgWriteFailed, failed)
		return config.ExitError, nil
	}
	fmt.Fprintln(stdout, path)
	return config.ExitOK, nil
}

func requestPath(conf *config.Settings, parentID string, number int) string {
	return ospath.Join(conf.State, fmt.Sprintf(requestFile, parentID, number))
}

// Requested は投稿の結果を受けて、依頼のマーカーを置く。依頼済みで出し直せない・結果に投稿の url が無い・
// 投稿の後に前提が崩れた（子の合流・未コミット・未 push）なら置かずに止める。マーカーには HEAD と投稿の
// 情報（MR・url・ホスト・ホストの時刻・投稿者）を書き、投稿待ちの本文を消して、親の mr: に番号を書き足す。
func Requested(stdout, stderr io.Writer, conf *config.Settings, root, cwd string, number int, resultPath string) (int, error) {
	parent, p, ok := ParentPhase(stderr, conf, root, cwd, number)
	if !ok {
		return config.ExitError, nil
	}
	treeRoot := tree.WorktreePath(root, parent.ID)
	if already := alreadyRequested(conf, treeRoot, p, number); already != "" {
		fmt.Fprintf(stderr, "ccnavi: %s\n", already)
		return config.ExitError, nil
	}
	_, again := p.Marks[marks.Requested]
	result, ok := ResultWithMR(stderr, resultPath)
	if !ok {
		return config.ExitError, nil
	}
	if result.URL == "" {
		fmt.Fprint(stderr, msgNoURL)
		return config.ExitError, nil
	}
	if unmet := unmetIn(root, treeRoot, conf, p); len(unmet) > 0 {
		fmt.Fprint(stderr, msgBrokenAfter)
		for _, line := range unmet {
			fmt.Fprintf(stderr, "  - %s\n", line)
		}
		return config.ExitError, nil
	}
	_, head := git.Output(treeRoot, []string{"rev-parse", "HEAD"}, git.Options{Timeout: Timeout})
	home, err := approval.HomeDir(conf, root, parent.ID, "", parent.Project)
	if err != nil {
		return config.ExitError, err
	}
	// since はホストの時計。手元の時計と比べると、依頼直後の指摘が「依頼より前」と判定されて除かれる。
	// poster は GitLab で ccnavi の投稿のスレッドを見分けるのに使う（分からなければ書かず、見分けずに数える）。
	data := pyjson.NewObject("head", strings.TrimSpace(head), "mr", result.MR.Number, "url", result.URL,
		"host", string(result.Host), "since", result.CreatedAt)
	if result.Author != "" {
		data.Set("poster", result.Author)
	}
	if !Mark(stderr, home, parent.ID, number, marks.Requested, data) {
		return config.ExitError, nil
	}
	if conf.State != "" {
		fsio.Remove(requestPath(conf, parent.ID, number))
	}
	if err := noteMR(stderr, conf, root, parent, result.MR.Number); err != nil {
		return config.ExitError, err
	}
	done := msgFirst
	if again {
		done = msgAgain
	}
	where := result.MR.URL
	if where == "" {
		where = result.URL
	}
	fmt.Fprintf(stdout, msgRequestedOK, done, where)
	fmt.Fprintln(stdout, texts.Say(root, "NEXT_REVIEW_REQUESTED",
		texts.Values{"turn": useronly.TurnDefined, "parallel": phase.ParallelRequestNote}, nil))
	return config.ExitOK, nil
}

// noteMR は親の承認済みチケットの mr: が空なら、この MR の番号を書き足す。違う値なら上書きせず警告する。
// 書き足しに失敗してもレビューの依頼は進む（マーカーは置けている）。書いたパスは write-and-push のコミットに入る。
func noteMR(stderr io.Writer, conf *config.Settings, root string, parent *ticket.Ticket, number int) error {
	home, err := approval.HomeDir(conf, root, parent.ID, "", parent.Project)
	if err != nil {
		return err
	}
	failed, result := approval.NoteMR(approval.CopyPath(home, parent.ID), number)
	switch {
	case failed != "":
		fmt.Fprintf(stderr, msgMRFailed, parent.ID, number, failed)
	case result == fields.MRSet:
		fmt.Fprintf(stderr, msgMRSet, parent.ID, number)
	case result == fields.MRDiffers:
		fmt.Fprintf(stderr, msgMRDiffers, parent.ID, number, texts.Say(root, "NEXT_REVIEW_MR_DIFFERS", nil, nil))
	case result == fields.MRUnreadable:
		fmt.Fprintf(stderr, msgMRUnreadable, parent.ID)
	}
	return nil
}

// coveredHeader は依頼文の先頭に置く「このレビューが含むフェーズ」と「このレビューのリスク」。リスクの行は、
// なぜこのフェーズにレビューが要ることになったかを依頼文で読めるように、実績の点と加点した理由を書く。
func coveredHeader(conf *config.Settings, root string, parent *ticket.Ticket, p *phase.Phase) string {
	numbers := reviewNumbers(p)
	var labels, risks []string
	for _, other := range phase.Phases(conf, root, parent.ID, nil) {
		if !containsInt(numbers, other.Number) {
			continue
		}
		labels = append(labels, other.Label())
		if body := other.RiskBody(); body != "" {
			if len(numbers) > 1 {
				body = other.Label() + ": " + body
			}
			risks = append(risks, body)
		}
	}
	head := ""
	if parent.HasPlan() {
		if len(labels) <= 1 && len(p.Covers()) == 0 {
			head = msgCovered + p.Label() + "\n"
		} else {
			head = msgCovered + strings.Join(labels, "、") + "\n"
		}
	}
	if len(risks) > 0 {
		head += msgRisk + strings.Join(risks, " / ") + "\n"
		if p.RiskEscalates() {
			head += msgRiskEscalate
		}
	}
	if head == "" {
		return ""
	}
	return head + "\n"
}

func containsInt(list []int, n int) bool {
	for _, x := range list {
		if x == n {
			return true
		}
	}
	return false
}

// unmetIn は依頼の前提のうち、ワークツリーの中で分かるもの。子が親のブランチに合流したかは、finish が書いた
// merged_sha が親の HEAD の祖先かで見る（子のブランチは見ない。消しても、別の PC で finish しても変わらない）。
// 未追跡は数えない（依頼文をワークツリーに置く使い方が普通にあり、未追跡はマージリクエストに載らない）。
func unmetIn(root, treeRoot string, conf *config.Settings, p *phase.Phase) []string {
	var unmet []string
	if !p.Ended() {
		unmet = append(unmet, msgNotEnded)
	}
	for _, child := range p.Tickets {
		// 取り消した子はここで外れる（cancel は子のブランチを親へ入れない）。
		if s := p.States[child.ID]; s != place.Review && s != place.Done {
			continue
		}
		if child.MergedSHA == "" {
			unmet = append(unmet, fmt.Sprintf(msgNoMergedSHA, child.ID))
			continue
		}
		short := pyfmt.Head(child.MergedSHA, shortSHA)
		if code, _ := git.Output(treeRoot, []string{"merge-base", "--is-ancestor", child.MergedSHA, "HEAD"}, git.Options{Timeout: Timeout}); code != 0 {
			unmet = append(unmet, fmt.Sprintf(msgNotAncestor, child.ID, short))
			continue
		}
		if late := closing.UnmergedFix(root, treeRoot, child, Timeout); late != "" {
			unmet = append(unmet, fmt.Sprintf(msgLateFix, child.ID, pyfmt.Head(late, shortSHA), short))
		}
	}
	for _, r := range treegate.Check(treegate.ReviewRequest, treeRoot, conf, treegate.Options{}) {
		unmet = append(unmet, r.Line())
	}
	return unmet
}

// alreadyRequested は依頼済みで出し直せないならその説明。未依頼か出し直せるなら空。
//
// 出し直せるのは、依頼の後にユーザが見るものが動き、まだレビュー済みになっていないときだけ（confirm が止まる
// ので、ここも止めると打てる手が無くなる）。ccnavi 自身の置き場だけが動いた形では confirm が止まらないので、
// 出し直させると依頼のコメントが増えるだけになる。レビュー済みは依頼の有無より先に見る（close-early は
// 依頼していないフェーズにもレビュー済みを置く）。
func alreadyRequested(conf *config.Settings, treeRoot string, p *phase.Phase, number int) string {
	if _, ok := p.Marks[marks.Reviewed]; ok {
		return fmt.Sprintf(msgReviewedPhase, number)
	}
	mark, ok := p.Marks[marks.Requested]
	if !ok {
		return ""
	}
	code, head := git.Output(treeRoot, []string{"rev-parse", "HEAD"}, git.Options{Timeout: Timeout})
	recorded := markText(mark, "head")
	if recorded == "" {
		return ""
	}
	if code == 0 && strings.TrimSpace(head) != recorded {
		if !IsSHA(recorded) {
			return ""
		}
		changed, failed := treegate.DiffPaths(treeRoot, recorded)
		if failed != "" || len(seenPaths(conf, changed)) > 0 {
			return ""
		}
	}
	return fmt.Sprintf(msgRequestedPhase, number)
}

// resolve は相対パスを、スクリプトを打った場所（--cwd）からの相対で読む。
func resolve(cwd, path string) string {
	if path == "" || ospath.IsAbs(path) {
		return path
	}
	if cwd == "" {
		cwd, _ = ospath.Getwd()
	}
	return ospath.Join(cwd, path)
}
