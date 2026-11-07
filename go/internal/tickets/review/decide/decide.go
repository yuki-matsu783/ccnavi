package decide

import (
	"bufio"
	"fmt"
	"io"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/oplog"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
	"ccnavi/internal/tickets/review/request"
)

// state の置き場に書く下書きの名前（sh がこれを投稿する）。issue の続きのコメントは `<名前>.part<N>.md`。
const (
	commentFile = "review-decide-%s-%d.md"
	issueFile   = "review-issue-%s-%d.md"
)

// ターミナルで打つ 1 文字と、画面に出す呼び名。
var (
	choiceKeys = []struct {
		letter string
		choice Choice
	}{{"k", Keep}, {"f", Fix}, {"i", Issue}}
	choiceLabels = map[Choice]string{
		Keep:  "対応しない（受け入れて進む）",
		Fix:   "このフェーズで直す（続きの子チケットを起こす）",
		Issue: "issue に回す（受け入れて、別の issue に切り出す）",
	}
)

// 決め方の文。sh とボードが読むので変えない。
const (
	msgNoRequest       = "ccnavi: 依頼の記録がありません。先に review-request してください\n"
	msgMovedRedo       = "ccnavi: %s。%s\n"
	msgNoDigest        = "ccnavi: --yes には --digest（見せた指摘のダイジェスト）が必要です。\n"
	msgBadDigest       = "ccnavi: --digest を読めない。指摘を見せ直してください。何も置いていない\n"
	msgBadChoicesJSON  = "ccnavi: --yes の選択を JSON として読めない\n"
	msgBadChoicesShape = "ccnavi: --yes の選択は {キー: %s} の形で渡す\n"
	msgCheckOK         = "確認: ok（decide を打てる状態。何も変えていません）\n"
	msgChanges         = "ccnavi: %s。decide でも通せない。変更要求があるため、レビュー済みにはできません。" +
		"修正する場合は同じフェーズに新しい子を提案し、ユーザの承認を受けてください\n"
	msgNeedAccept   = "ccnavi: 未解決を受け入れるなら --accept-unresolved を付けてください。受け入れないなら '%s confirm' で足りる\n"
	msgNoThreads    = "フェーズ %d（親 %s）で未解決（Unresolved）の指摘なし\n"
	msgThreads      = "フェーズ %d（親 %s）で未解決（Unresolved）の指摘: %d 件\n"
	msgChooseFailed = "ccnavi: 選んだものを %s に保存できませんでした（%s）\n"
	msgAskHead      = "未解決（Unresolved）の指摘の対応方針を 1 件ずつ選びます。選択肢以外を入力すると、何もせずにやめます。\n"
	msgAskItem      = "[%d/%d] %s\n"
	msgAskPrompt    = "選択（k / f / i）: "
	msgUndecided    = "ccnavi: 決めなかった。何も置いていない\n"
	msgNoPlan       = "ccnavi: 受け入れの計画 %s を読めない。何も置いていない\n"
	msgMismatch     = "ccnavi: 見せた指摘と今の指摘が違う。何も置いていない\n"
	msgUneven       = "ccnavi: 選択が見せた指摘と揃っていない（足りないか、余分がある）\n"
	msgNotAccepted  = "ccnavi: 受け入れが済んでいない: %s。何も置いていない\n"
	msgAppliedLost  = "ccnavi: 適用した印を操作記録に書けない（%s）。投稿の段階は続けられる\n"
	msgOpUnread     = "ccnavi: 操作記録を読めない（%s）。何も置いていない\n"
	msgOpMissing    = "ccnavi: 操作記録 %s が無い。何も置いていない\n"
	msgPlanKeys     = "計画のスレッドと選択が合わない"
	msgPlanAbsent   = "スレッド %s が結果に無い"
	msgPlanOpen     = "スレッド %s がホストで解決済みになっていない"
	msgNoStateOp    = "ccnavi: state の置き場が空。判断を保存できず、何も適用していない\n"
	msgOpFailed     = "ccnavi: 判断を保存できず、何も適用していない（%s）\n"
	msgCannotStart  = "ccnavi: 受け入れを始められない: %s\n"
	msgPlanFailed   = "ccnavi: 受け入れの計画を %s に保存できませんでした（%s）\n"
	msgNoStateIssue = "ccnavi: state の置き場が空。issue に回す下書きを置けない\n"
	msgNoFollowup   = "ccnavi: 続きの子チケットを起こせない: %s\n"
	msgFollowupMade = "続きの子チケット %s を %s/%s/ に起こした（フェーズ %d、範囲は見た子の和、本文に指摘 %d 件）。" +
		"フェーズは再び開かれ、マーカーは消えた。"
	msgIssueFailed   = "ccnavi: issue の下書きを書き出せない (%s)\n"
	msgPartFailed    = "ccnavi: issue の続きの下書きを書き出せない (%s)\n"
	msgCommentFailed = "ccnavi: 記録を書き出せない (%s)\n"
	msgOKFollowup    = "OK: フェーズ %d は続きの子 %s で応える\n"
	msgOKExtra       = "OK: 今回の判断（%d 件）は適用済み。追加分 %d 件は判断待ち（フェーズ %d はレビュー済みにしていない）\n"
	msgOKAccepted    = "OK: フェーズ %d はレビュー済み（未解決 %d 件を受け入れた）\n"
	msgOKNone        = "OK: フェーズ %d はレビュー済み（未解決（Unresolved）の指摘なし）\n"
)

// 操作記録の applied の欄。
const (
	appliedOnHost   = "accepted_on_host"
	appliedLocal    = "local"
	appliedFollowup = "followup"
)

// passed は gate を通った材料（親・フェーズ・依頼のマーカー）。
type passed struct {
	parent    *ticket.Ticket
	phase     *phase.Phase
	requested *pyjson.Object
}

// gate は decide の、ホストに聞かずに分かる前提を検査する。確かめるのは次の 3 つである。親とフェーズがあること。依頼の記録があること。依頼のあとに見せたものが動いていないこと。本体と Check が同じものを通る。断ったときは、理由のあとに `次:` を 1 行足して nil を返す。ホストの状態（変更要求・未解決の指摘・ダイジェスト）は見ない。sh が取ってくるからである。
func gate(stderr io.Writer, conf *config.Settings, root, cwd string, number int) *passed {
	parent, p, ok := request.ParentPhase(stderr, conf, root, cwd, number)
	if !ok {
		refused(stderr, root, number)
		return nil
	}
	mark, ok := p.Marks[marks.Requested]
	if !ok {
		fmt.Fprint(stderr, msgNoRequest)
		refused(stderr, root, number)
		return nil
	}
	moved := request.MovedInTree(conf, tree.WorktreePath(root, parent.ID), mark, parent.Project != "")
	if moved.Text != "" {
		fmt.Fprintf(stderr, msgMovedRedo, moved.Text, request.RedoRequest(root, number))
		for _, line := range request.MovedExplanation(moved) {
			fmt.Fprintln(stderr, line)
		}
		refused(stderr, root, number)
		return nil
	}
	return &passed{parent: parent, phase: p, requested: mark}
}

// refused は断った理由のあとに、打ち直す 1 手を `次:` で足す。
func refused(stderr io.Writer, root string, number int) {
	fmt.Fprintln(stderr, texts.Say(root, "NEXT_DECIDE_REFUSED", texts.Values{"phase": strconv.Itoa(number)}, nil))
}

// parseChoices は `--yes` の選択とダイジェストの形を検める。何も置かない。断ったときは `次:` を足す。
func parseChoices(stderr io.Writer, root string, number int, choicesText, digest string) (Shown, *pyjson.Object, bool) {
	shown, choices, ok := parseChoicesRaw(stderr, choicesText, digest)
	if !ok {
		refused(stderr, root, number)
	}
	return shown, choices, ok
}

func parseChoicesRaw(stderr io.Writer, choicesText, digest string) (Shown, *pyjson.Object, bool) {
	digest = ospath.Strip(digest)
	if digest == "" {
		fmt.Fprint(stderr, msgNoDigest)
		return Shown{}, nil, false
	}
	shown, ok := ParseDigest(digest)
	if !ok {
		fmt.Fprint(stderr, msgBadDigest)
		return Shown{}, nil, false
	}
	value, err := pyjson.Loads(choicesText)
	if err != nil {
		fmt.Fprint(stderr, msgBadChoicesJSON)
		return Shown{}, nil, false
	}
	choices, ok := value.(*pyjson.Object)
	if ok {
		for _, key := range choices.Keys() {
			if c, isText := choices.Value(key).(string); !isText || !slices.Contains(choiceOrder, Choice(c)) {
				ok = false
				break
			}
		}
	}
	if !ok {
		names := make([]string, len(choiceOrder))
		for i, c := range choiceOrder {
			names[i] = string(c)
		}
		fmt.Fprintf(stderr, msgBadChoicesShape, strings.Join(names, " / "))
		return Shown{}, nil, false
	}
	return shown, choices, true
}

// Check は `--entry sh user reviewed N --check --accept-unresolved`。置く前に分かる前提だけを通す（読むだけ）。
// 選択とダイジェストを渡されたときは、その形も Yes と同じ検査で検める。sh が取り込み・ロックの前に呼ぶ。
func Check(stdout, stderr io.Writer, conf *config.Settings, root, cwd string, number int, choicesText, digest string) int {
	if gate(stderr, conf, root, cwd, number) == nil {
		return config.ExitError
	}
	if choicesText != "" || digest != "" {
		if _, _, ok := parseChoices(stderr, root, number, choicesText, digest); !ok {
			return config.ExitError
		}
	}
	fmt.Fprint(stdout, msgCheckOK)
	return config.ExitOK
}

// decision は、残った指摘を決められる状態かを確かめ、見せる材料を組む。決められなければ nil を返す。carry は受け入れ済みのスレッドの id（`--accept-done`）である。sh が返信と解決をしたあとの結果では、それらは解決済みになっている。しかし、見せた指摘の一部として数え直す。見せたときと同じ並びでダイジェストを組み直し、見せたあとに変わっていないことを確かめるためである。
func decision(stderr io.Writer, conf *config.Settings, root, cwd string, number int, resultPath string, carry []string) *Decision {
	gated := gate(stderr, conf, root, cwd, number)
	if gated == nil {
		return nil
	}
	result, ok := request.Matching(stderr, resultPath, gated.requested)
	if !ok {
		return nil
	}
	if len(request.ChangesRequested(result)) > 0 {
		fmt.Fprintf(stderr, msgChanges, host.ChangesRequestedNote(result.Host))
		return nil
	}
	poster := ""
	if v := gated.requested.Value("poster"); pyfmt.Truthy(v) {
		poster = pyfmt.Str(v)
	}
	ledger := request.LedgerOf(conf, root, gated.parent)
	var unresolved []host.Thread
	for _, t := range result.Threads {
		if host.IsUnresolved(t, result.Host, poster, ledger) || t.ID != "" && slices.Contains(carry, t.ID) {
			unresolved = append(unresolved, t)
		}
	}
	return &Decision{Parent: gated.parent, Phase: gated.phase, Result: result, Unresolved: unresolved}
}

func dumpLine(stdout io.Writer, v any) {
	text, err := pyjson.Dumps(v, pyjson.Options{RawUnicode: true})
	if err == nil {
		fmt.Fprintln(stdout, text)
	}
}

// Preview は残った指摘を見せる（`--preview --json`）。ボードのオーバーレイが読み、適用はこの digest を
// `--digest` で返したときだけ通る。
func Preview(stdout, stderr io.Writer, conf *config.Settings, root, cwd string, number int, resultPath string) int {
	d := decision(stderr, conf, root, cwd, number, resultPath, nil)
	if d == nil {
		return config.ExitError
	}
	dumpLine(stdout, JSON(d))
	return config.ExitOK
}

// Reviewed はユーザがターミナルで行う決め方。残った指摘を見せ、1 件ずつ行き先を選ばせて置く。変更要求は通せない。
func Reviewed(stdin *bufio.Reader, stdout, stderr io.Writer, conf *config.Settings, root, cwd string, number int, acceptUnresolved bool, resultPath string) (int, error) {
	if !acceptUnresolved {
		fmt.Fprintf(stderr, msgNeedAccept, config.ScriptCommand(root, config.ReviewWrapper))
		return config.ExitError, nil
	}
	d := decision(stderr, conf, root, cwd, number, resultPath, nil)
	if d == nil {
		return config.ExitError, nil
	}
	choices := pyjson.NewObject()
	if len(d.Unresolved) == 0 {
		// 選ぶものが無い。選び方の案内は出さず、レビュー済みにするだけ。
		fmt.Fprintf(stdout, msgNoThreads, number, d.Parent.ID)
	} else {
		fmt.Fprintf(stdout, msgThreads, number, d.Parent.ID, len(d.Unresolved))
		var ok bool
		if choices, ok = ask(stdin, stdout, stderr, d); !ok {
			return config.ExitError, nil
		}
	}
	summary, err := apply(stdout, stderr, conf, root, d, choices, "")
	if summary == nil {
		return config.ExitError, err
	}
	return config.ExitOK, nil
}

// Choose は対話の decide の前半（`--choose-out <ファイル>`）。残った指摘を見せて選ばせ、選択とダイジェストを
// 書くだけ。置くのは sh が write-and-push の中で `--yes` で打つ（ロックを持ったままユーザを待たない）。
func Choose(stdin *bufio.Reader, stdout, stderr io.Writer, conf *config.Settings, root, cwd string, number int, resultPath, outPath string) int {
	d := decision(stderr, conf, root, cwd, number, resultPath, nil)
	if d == nil {
		return config.ExitError
	}
	choices := pyjson.NewObject()
	if len(d.Unresolved) > 0 {
		fmt.Fprintf(stdout, msgThreads, number, d.Parent.ID, len(d.Unresolved))
		var ok bool
		if choices, ok = ask(stdin, stdout, stderr, d); !ok {
			return config.ExitError
		}
	} else {
		fmt.Fprintf(stdout, msgNoThreads, number, d.Parent.ID)
	}
	chosen := pyjson.NewObject("choices", choices, "digest", Digest(d))
	if failed := fsio.WriteJSONAs(outPath, chosen, fsio.JSONFormat{FinalNewline: true}); failed != "" {
		fmt.Fprintf(stderr, msgChooseFailed, outPath, failed)
		return config.ExitError
	}
	return config.ExitOK
}

// ask は残った指摘の行き先を 1 件ずつターミナルで選ばせる。決めなければ偽。
func ask(stdin *bufio.Reader, stdout, stderr io.Writer, d *Decision) (*pyjson.Object, bool) {
	fmt.Fprint(stdout, msgAskHead)
	for _, k := range choiceKeys {
		fmt.Fprintf(stdout, "  %s  %s\n", k.letter, choiceLabels[k.choice])
	}
	choices := pyjson.NewObject()
	for i, t := range d.Unresolved {
		fmt.Fprintf(stdout, msgAskItem, i+1, len(d.Unresolved), request.ThreadLabel(t))
		fmt.Fprint(stdout, msgAskPrompt)
		answer := fsio.ReadAnswer(stdin)
		picked := Choice("")
		for _, k := range choiceKeys {
			if k.letter == answer {
				picked = k.choice
			}
		}
		if picked == "" {
			fmt.Fprint(stderr, msgUndecided)
			return nil, false
		}
		choices.Set(host.ThreadKey(t), string(picked))
	}
	return choices, true
}

// YesOptions は Yes の計画の段。PlanOut（`--accept-plan`）はホストにも票にも置かず判断と計画を書く段、PlanDone
// （`--accept-done`）は sh が計画のスレッドに返信して解決したあとに置く段。どちらも空なら一度に置く。
type YesOptions struct {
	PlanOut  string
	PlanDone string
}

// Yes は、オーバーレイ（または sh の write-and-push）で押された選択を置く（`--yes <選択> --digest <ダイジェスト> --json`）。ターミナルで打つという制約の代わりに、見せた指摘と今の指摘のダイジェストが合うことを求める。エージェントがこれをシェルで打つ形は、組み込みの deny が止める。受け入れる指摘（keep・issue）には、置く前に sh が返信を残して解決する。そのため、実行ファイルは 3 回に分けて呼ばれる（PlanOut → sh の返信と解決 → PlanDone）。答えは JSON で返す。合わなければ、置かずに mismatch を返す。返信と解決を済ませた分は、操作記録に残す。
func Yes(stdout, stderr io.Writer, conf *config.Settings, root, cwd string, number int, resultPath, choicesText, digest string, o YesOptions) (int, error) {
	shown, choices, ok := parseChoices(stderr, root, number, choicesText, digest)
	if !ok {
		return config.ExitError, nil
	}
	var planned *host.AcceptPlan
	var carry []string
	if o.PlanDone != "" {
		if planned = host.ReadAcceptPlan(o.PlanDone); planned == nil {
			fmt.Fprintf(stderr, msgNoPlan, o.PlanDone)
			return config.ExitError, nil
		}
		carry = planned.IDs
	}
	d := decision(stderr, conf, root, cwd, number, resultPath, carry)
	if d == nil {
		return config.ExitError, nil
	}
	current := Digest(d)
	if changed := ChangedSinceShown(d, shown); len(changed) > 0 {
		if planned != nil && planned.Op != "" {
			// ホストに書いたあとに外れた。返信・解決は効いたユーザの選択なので記録に残す（取り消さない）。
			_ = oplog.AppliedSet(conf.State, planned.Op, appliedOnHost, append([]string{}, planned.Keys...))
		}
		dumpLine(stdout, pyjson.NewObject("version", version, "ok", false, "mismatch", true,
			"digest", pyjson.NewObject("expected", digest, "current", current), "changed", changed))
		fmt.Fprint(stderr, msgMismatch)
		return config.ExitError, nil
	}
	// 見せたあとに追加された指摘は今回の判断の対象にしない（未対応として残す）。
	var kept, extra []host.Thread
	for _, t := range d.Unresolved {
		if _, ok := shown.Versions[host.ThreadKey(t)]; ok {
			kept = append(kept, t)
		} else {
			extra = append(extra, t)
		}
	}
	d.Unresolved, d.Extra = kept, extra
	if !sameKeys(choices.Keys(), shown.Keys) {
		fmt.Fprint(stderr, msgUneven)
		return config.ExitError, nil
	}
	if !precheck(stderr, conf, root, d, pick(d.Unresolved, choices)) {
		return config.ExitError, nil
	}
	if o.PlanOut != "" {
		op := beginOp(stderr, conf, d, choices, ospath.Strip(digest))
		if op == "" {
			return config.ExitError, nil
		}
		return writePlan(stderr, d, choices, o.PlanOut, current, op), nil
	}
	op := ""
	if planned != nil {
		op = planned.Op
	}
	if op != "" && !knownOp(stderr, conf, op) {
		return config.ExitError, nil
	}
	if planned != nil {
		if problem := planProblem(d, choices, planned); problem != "" {
			fmt.Fprintf(stderr, msgNotAccepted, problem)
			return config.ExitError, nil
		}
	}
	summary, err := apply(io.Discard, stderr, conf, root, d, choices, op)
	if summary == nil {
		return config.ExitError, err
	}
	if op != "" {
		if err := oplog.AppliedSet(conf.State, op, appliedLocal, true); err != nil {
			fmt.Fprintf(stderr, msgAppliedLost, err)
		}
	}
	summary.Set("op", op)
	answer := pyjson.NewObject("version", version, "ok", true)
	for _, key := range summary.Keys() {
		answer.Set(key, summary.Value(key))
	}
	dumpLine(stdout, answer)
	return config.ExitOK, nil
}

// sameKeys は 2 つのキーの集まりが同じか（並びは問わない）。
func sameKeys(a, b []string) bool {
	set := func(keys []string) map[string]bool {
		m := map[string]bool{}
		for _, k := range keys {
			m[k] = true
		}
		return m
	}
	sa, sb := set(a), set(b)
	if len(sa) != len(sb) {
		return false
	}
	for k := range sa {
		if !sb[k] {
			return false
		}
	}
	return true
}

// knownOp は計画の操作ID の記録が在るか。無ければ、計画が別の PC のものか消えている。
func knownOp(stderr io.Writer, conf *config.Settings, op string) bool {
	record, err := oplog.Load(conf.State, op)
	if err != nil {
		fmt.Fprintf(stderr, msgOpUnread, err)
		return false
	}
	if record == nil {
		fmt.Fprintf(stderr, msgOpMissing, op)
		return false
	}
	return true
}

// acceptedThreads は受け入れる指摘（keep・issue）と、スレッドに残す返信の本文。
func acceptedThreads(d *Decision, choices *pyjson.Object) []host.AcceptItem {
	why := AcceptWhy(strconv.Itoa(d.Phase.Number))
	var out []host.AcceptItem
	for _, t := range d.Unresolved {
		c, _ := choices.Value(host.ThreadKey(t)).(string)
		if Choice(c) == Keep || Choice(c) == Issue {
			out = append(out, host.AcceptItem{Thread: t, Reply: host.AcceptReply(why, Choice(c) == Issue, "")})
		}
	}
	return out
}

// planProblem は計画と、sh が取り直した結果が合うか。受け入れる指摘と計画のスレッドが同じで、全部がホストで
// 解決済みであること（ホストの実状態で確かめる）。合わなければ説明。
func planProblem(d *Decision, choices *pyjson.Object, planned *host.AcceptPlan) string {
	var want []string
	for _, item := range acceptedThreads(d, choices) {
		want = append(want, host.ThreadKey(item.Thread))
	}
	got := slices.Clone(planned.Keys)
	slices.Sort(want)
	slices.Sort(got)
	if !slices.Equal(got, want) {
		return msgPlanKeys
	}
	byID := map[string]host.Thread{}
	for _, t := range d.Result.Threads {
		if t.ID != "" {
			byID[t.ID] = t
		}
	}
	for _, id := range planned.IDs {
		t, ok := byID[id]
		if !ok {
			return fmt.Sprintf(msgPlanAbsent, id)
		}
		if !t.Resolved {
			return fmt.Sprintf(msgPlanOpen, host.ThreadKey(t))
		}
	}
	return ""
}

// beginOp はユーザの判断を操作記録に保存する（返信・解決の前）。保存できなければ空。記録は構造化した中身だけを
// 持ち（見せた指摘・選択・予定した段階）、送る本文は送るたびに shown・choices から作り直す。
func beginOp(stderr io.Writer, conf *config.Settings, d *Decision, choices *pyjson.Object, shownDigest string) string {
	if conf.State == "" {
		fmt.Fprint(stderr, msgNoStateOp)
		return ""
	}
	picked := pick(d.Unresolved, choices)
	var stages []oplog.StageSpec
	if len(picked[Keep]) > 0 || len(picked[Issue]) > 0 {
		stages = append(stages, oplog.StageSpec{ID: stageReply, Kind: oplog.StageReply})
	}
	if len(picked[Issue]) > 0 {
		stages = append(stages, oplog.StageSpec{ID: stageIssue, Kind: oplog.StageIssue})
		_, parts := IssueTexts(d, picked[Issue], "")
		for n := range parts {
			stages = append(stages, oplog.StageSpec{ID: stagePartPrefix + strconv.Itoa(n+1), Kind: oplog.StageComment})
		}
	}
	stages = append(stages, oplog.StageSpec{ID: stageNote, Kind: oplog.StageComment})
	shown := make([]any, 0, len(d.Unresolved))
	for _, t := range d.Unresolved {
		shown = append(shown, pyjson.NewObject(
			"key", host.ThreadKey(t), "id", t.ID, "url", t.URL, "path", t.Path, "line", t.Line,
			"version", host.ThreadVersion(t), "body", t.Body,
			"last_comment_id", t.LastCommentID, "last_comment_at", t.LastCommentAt, "last_comment_author", t.LastCommentAuthor,
		))
	}
	head := ""
	if v := d.Phase.Marks[marks.Requested].Value("head"); pyfmt.Truthy(v) {
		head = pyfmt.Str(v)
	}
	op, err := oplog.Begin(conf.State, oplog.KindDecide, oplog.Options{
		Repo:        pyjson.NewObject("host", string(d.Result.Host), "mr_url", d.Result.MR.URL),
		Parent:      d.Parent.ID,
		Phase:       d.Phase.Number,
		MR:          d.Result.MR.Number,
		RequestHead: head,
		Shown:       shown,
		Choices:     choices,
		Plan:        pyjson.NewObject("digest", shownDigest),
		Stages:      stages,
	})
	if err != nil {
		fmt.Fprintf(stderr, msgOpFailed, err)
		return ""
	}
	oplog.DiscardUntouched(conf.State, oplog.KindDecide, d.Parent.ID, d.Phase.Number, op)
	return op
}

// writePlan は受け入れの計画を書く。受け入れる指摘が無くても空の計画を書く（sh は何もしない）。
func writePlan(stderr io.Writer, d *Decision, choices *pyjson.Object, outPath, digest, op string) int {
	items := acceptedThreads(d, choices)
	threads := make([]host.Thread, len(items))
	for i, item := range items {
		threads[i] = item.Thread
	}
	if problem := host.AcceptPlanProblem(threads); problem != "" {
		fmt.Fprintf(stderr, msgCannotStart, problem)
		return config.ExitError
	}
	if failed := host.WriteAcceptPlan(outPath, items, digest, op); failed != "" {
		fmt.Fprintf(stderr, msgPlanFailed, outPath, failed)
		return config.ExitError
	}
	return config.ExitOK
}

// SettledChildren はこのフェーズの、作業として終わった子。
func SettledChildren(p *phase.Phase) []*ticket.Ticket {
	var out []*ticket.Ticket
	for _, t := range p.Tickets {
		if slices.Contains(place.Finished(), p.States[t.ID]) {
			out = append(out, t)
		}
	}
	return out
}

// precheck は置く前に、ローカルで失敗しうることを確かめる。ホストに触る前（PlanOut の段）にも呼ぶ（受け入れの
// 返信と解決を済ませてから置けないと分かると、解決済みのスレッドだけが残る）。
func precheck(stderr io.Writer, conf *config.Settings, root string, d *Decision, picked map[Choice][]host.Thread) bool {
	// issue に回す分は state の置き場に下書きを書いて sh に渡す。置き場が無ければ、何も置く前に断る。
	if len(picked[Issue]) > 0 && conf.State == "" {
		fmt.Fprint(stderr, msgNoStateIssue)
		return false
	}
	// 直す指摘があるなら、起こす続きの子がチケットとして読めるか（子が多いと範囲の和が上限を超える）を先に見る。
	if fix := picked[Fix]; len(fix) > 0 {
		children := SettledChildren(d.Phase)
		if len(children) == 0 {
			children = d.Phase.Tickets
		}
		if problem := approval.FollowupProblem(conf, root, d.Parent, d.Phase.Number, children, Transcribe(d, fix, "")); problem != "" {
			fmt.Fprintf(stderr, msgNoFollowup, problem)
			return false
		}
	}
	return true
}

func draftPath(conf *config.Settings, name string, d *Decision) string {
	return ospath.Join(conf.State, fmt.Sprintf(name, d.Parent.ID, d.Phase.Number))
}

func partPath(issuePath string, n int) string {
	stem, ext := ospath.Splitext(issuePath)
	return fmt.Sprintf("%s.part%d%s", stem, n, ext)
}

// apply は、選んだ行き先を置く。ターミナルとオーバーレイが、同じここを通る。置けなければ nil を返す。答えは、オーバーレイに返す JSON の中身である。
//   - keep・issue: 受け入れる。返信と解決は、置く前に sh が済ませてある。次の confirm は、ホストで解決済みのスレッドを数えない。
//   - fix: 続きの子を同じ番号で doing/ に起こす。フェーズは再び開かれ、レビュー済みのマーカーは置かない。
//   - どれも直さず、見せたあとに追加された指摘も無いなら、レビュー済みのマーカーを置く。
func apply(stdout, stderr io.Writer, conf *config.Settings, root string, d *Decision, choices *pyjson.Object, op string) (*pyjson.Object, error) {
	parent, p := d.Parent, d.Phase
	home, err := approval.HomeDir(conf, root, parent.ID, "", parent.Project)
	if err != nil {
		return nil, err
	}
	picked := pick(d.Unresolved, choices)
	accepted := keyList(append(slices.Clone(picked[Keep]), picked[Issue]...))
	fix := picked[Fix]
	if !precheck(stderr, conf, root, d, picked) {
		return nil, nil
	}
	// 前の回の下書き（投稿に失敗して残ったもの）は破棄する。残すと、この回に選んでいないものを sh が投稿する。
	if conf.State != "" {
		for _, name := range []string{commentFile, issueFile} {
			stale := draftPath(conf, name, d)
			if ospath.DiskExists(stale) {
				fsio.Remove(stale)
			}
			for n := 1; ospath.DiskExists(partPath(stale, n)); n++ {
				fsio.Remove(partPath(stale, n))
			}
		}
	}
	// 追加分があって直す指摘も無いなら、フェーズはレビュー済みにせず子も動かさない（追加分は次の decide で扱う）。
	if len(fix) > 0 || len(d.Extra) == 0 {
		settled, err := request.SettleChildren(stdout, stderr, conf, root, parent, p, request.ByDecide)
		if !settled {
			return nil, err
		}
	}
	followup := ""
	if len(fix) > 0 {
		followup = followupFromChoice(stdout, stderr, conf, root, d, Transcribe(d, fix, op), op)
		if followup == "" {
			return nil, nil
		}
	} else if len(d.Extra) == 0 {
		account, route := decideActor()
		if !request.Mark(stderr, home, parent.ID, p.Number, marks.Reviewed, request.ReviewedMark(d.Result.MR.Number, accepted, account, route, "")) {
			return nil, nil
		}
	}
	issueDraft := ""
	if len(picked[Issue]) > 0 {
		issueDraft = writeIssue(stderr, conf, d, picked[Issue], op)
	}
	if op != "" && followup != "" {
		_ = oplog.AppliedSet(conf.State, op, appliedFollowup, followup)
	}
	if conf.State != "" {
		writeComment(stderr, conf, d, picked, followup)
	}
	switch {
	case followup != "":
		fmt.Fprintf(stdout, msgOKFollowup, p.Number, followup)
	case len(d.Extra) > 0:
		fmt.Fprintf(stdout, msgOKExtra, len(accepted), len(d.Extra), p.Number)
	case len(accepted) > 0:
		fmt.Fprintf(stdout, msgOKAccepted, p.Number, len(accepted))
	default:
		fmt.Fprintf(stdout, msgOKNone, p.Number)
	}
	return pyjson.NewObject(
		"parent", parent.ID, "phase", p.Number, "reviewed", followup == "" && len(d.Extra) == 0,
		"pending_extra", keyList(d.Extra), "followup", followup,
		"kept", keyList(picked[Keep]), "fix", keyList(fix), "issue", keyList(picked[Issue]),
		"issue_draft", issueDraft, "prompt", decidedPrompt(root, d, picked, followup),
	), nil
}

// followupFromChoice はユーザが選んだ続きの子を doing/ に起こし、識別子を返す。起こせなければ空。
func followupFromChoice(stdout, stderr io.Writer, conf *config.Settings, root string, d *Decision, items []string, op string) string {
	ident, failed := approval.Followup(conf, root, d.Parent, d.Phase.Number, SettledChildren(d.Phase), items, stderr, op)
	if failed != "" {
		fmt.Fprintf(stderr, msgNoFollowup, failed)
		return ""
	}
	fmt.Fprintln(stdout, FollowupLine(conf, ident, d.Phase.Number, len(items)))
	fmt.Fprintln(stdout, texts.Say(root, "NEXT_DECIDE_FOLLOWUP", texts.Values{"words": nextOf(root, ident)}, nil))
	for _, line := range phase.ReopenImpact(conf, root, d.Parent.ID, d.Phase.Number) {
		fmt.Fprintln(stdout, line)
	}
	return ident
}

// FollowupLine は続きの子を起こしたときに見せる行。
func FollowupLine(conf *config.Settings, ident string, number, items int) string {
	return fmt.Sprintf(msgFollowupMade, ident, conf.Approved, place.Doing, number, items)
}

// writeIssue は issue に回す指摘の下書き（1 行目がタイトル、空行のあとが本文）。指摘は全文で転記し、長いときは
// 続きのコメントの下書きも書く。書けなければ空。
func writeIssue(stderr io.Writer, conf *config.Settings, d *Decision, threads []host.Thread, op string) string {
	main, parts := IssueTexts(d, threads, op)
	path := draftPath(conf, issueFile, d)
	if failed := fsio.WriteText(path, main, fsio.LF, fsio.Plain); failed != "" {
		fmt.Fprintf(stderr, msgIssueFailed, failed)
		return ""
	}
	for n, part := range parts {
		if failed := fsio.WriteText(partPath(path, n+1), part, fsio.LF, fsio.Plain); failed != "" {
			fmt.Fprintf(stderr, msgPartFailed, failed)
			return ""
		}
	}
	return path
}

// writeComment は MR に書き込む、決めた内容のコメントの下書きを書く（sh が投稿する）。
func writeComment(stderr io.Writer, conf *config.Settings, d *Decision, picked map[Choice][]host.Thread, followup string) {
	if failed := fsio.WriteText(draftPath(conf, commentFile, d), CommentText(d, picked, followup), fsio.LF, fsio.Plain); failed != "" {
		fmt.Fprintf(stderr, msgCommentFailed, failed)
	}
}
