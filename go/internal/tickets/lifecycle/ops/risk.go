package ops

import (
	"fmt"
	"io"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/risk"
	"ccnavi/internal/tickets/lifecycle/closing"
	"ccnavi/internal/tickets/lifecycle/stop"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

const (
	textRecordRisk = "NEXT_OPS_RECORD_RISK"

	answerYes = "yes"
	answerNo  = "no"

	// judgePromptPrefix は定性の判定の問いを書き出すファイル名の頭（state の下）。
	judgePromptPrefix = "risk-judge-"
)

// RecordRisk は定性のリスク項目の判定を記録する（判断したのはサブエージェント、記録するのは親）。判定は子の HEAD に
// 結ぶ。HEAD が動けば判定は使えず、閉じるときに取り直しになる。
func RecordRisk(stdout, stderr io.Writer, conf *config.Settings, root, id, factorID, answer, reason string) int {
	answer = pyfmt.Lower(ospath.Strip(answer))
	if answer != answerYes && answer != answerNo {
		io.WriteString(stderr, "ccnavi: 判定は yes か no\n")
		return 1
	}
	reason = ospath.Strip(reason)
	if reason == "" {
		io.WriteString(stderr, "ccnavi: record-risk には --reason <根拠> を指定してください。\n")
		return 1
	}
	found := closing.Find(stderr, conf, root, id)
	if found == nil {
		return 1
	}
	if !found.IsChild() {
		fmt.Fprintf(stderr, "ccnavi: %s は子ではない。判定は子の差分に付ける\n", id)
		return 1
	}
	definition := risk.LoadDefinition(conf, root, projectOf(conf, root, found))
	factor := definition.Factor(factorID)
	if factor == nil || factor.Kind != risk.KindJudge {
		var names []string
		for _, f := range definition.Judges() {
			names = append(names, f.ID)
		}
		listed := strings.Join(names, ", ")
		if listed == "" {
			listed = "(無い)"
		}
		fmt.Fprintf(stderr, "ccnavi: %s は定性の項目ではない（%s）。あるのは: %s\n", factorID, definition.Source, listed)
		return 1
	}
	worktree := tree.WorktreePath(root, id)
	head := stop.HeadSHA(worktree, noDeadline)
	if head == "" {
		fmt.Fprintf(stderr, "ccnavi: %s の HEAD を読めない\n", worktree)
		return 1
	}
	where, err := approval.HomeDir(conf, root, id, found.Parent, found.Project)
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: 判定を記録できない: %s\n", err)
		return 1
	}
	record, ok := marks.ReadRecord(where, found.Parent, id, marks.Judge)
	if !ok {
		record = pyjson.NewObject()
	}
	record.Set(factorID, pyjson.NewObject(
		"hit", answer == answerYes,
		"reason", reason,
		"head", head,
		"at", fsio.Stamp(),
		// その項目がどのレイヤーに書いてあるか。
		"source", factor.Source,
	))
	if failed := marks.WriteRecord(where, found.Parent, id, marks.Judge, record); failed != "" {
		fmt.Fprintf(stderr, "ccnavi: 判定を記録できない: %s\n", failed)
		return 1
	}
	points := "加点なし"
	if answer == answerYes {
		points = "+" + strconv.Itoa(factor.Points)
	}
	fmt.Fprintf(stdout, "OK: %s の %s を %s と記録した（%s、HEAD %s）\n", id, factorID, answer, points, pyfmt.Head(head, shaShown))
	io.WriteString(stdout, texts.Say(root, textRecordRisk, texts.Values{"ticket": id}, nil)+"\n")
	return 0
}

// projectOf はこのチケットのレイヤーを決める project:。正とするのは承認済みチケットの側で、子は親の承認済み
// チケットから継ぐ。提案に書いてある値はユーザが承認していないので、判定の根拠にしない。
func projectOf(conf *config.Settings, root string, found *ticket.Ticket) string {
	if !found.IsChild() {
		return found.Project
	}
	copies, _ := approval.Scan(conf, root, false, nil)
	parent := approval.ByID(copies)[found.Parent]
	if parent == nil {
		closed, _ := approval.Scan(conf, root, true, nil)
		parent = approval.ByID(closed)[found.Parent]
	}
	if parent != nil {
		return parent.Project
	}
	return found.Project
}

// scoreChild は、子の実績のリスクを数えて記録する。返すのは、閉じたあとに出す行（点と内訳）である。閉じられなければ偽を返し、理由は stderr に出す。親・単独・統合の子は測らない。
//
// 数えるのは、子の仕事の始まり（取り込み直後の親の先頭と子の共通の祖先）から、子の先頭までである。着手の基準点から数えると、衝突を解くために子へ取り込んだ親の、ほかの子の変更が混ざってしまう。合流の途中なら、子の先頭の代わりに MERGE_HEAD まで数える。検査のあとに子が積んだコミットは、合流に載らないからである。
func scoreChild(stderr io.Writer, conf *config.Settings, root string, found *ticket.Ticket) ([]string, bool) {
	if !found.IsChild() || found.IsConsolidate() {
		return nil, true
	}
	worktree := tree.WorktreePath(root, found.ID)
	definition := risk.LoadDefinition(conf, root, projectOf(conf, root, found))
	if definition.Fallback != "" {
		fmt.Fprintf(stderr, "ccnavi: %s\n", definition.Fallback)
	}
	head := mergingHead(root, found)
	if head == "" {
		head = "HEAD"
	}
	var diff *risk.Diff
	base, err := git.ChildBase(worktree, tree.WorktreePath(root, found.Parent), 0, head)
	why := ""
	if err != nil {
		why = err.Error()
	} else {
		diff, why = risk.Measure(worktree, base, head)
	}
	if diff == nil {
		fmt.Fprintf(stderr, "ccnavi: %s のリスクを測れない: %s\n", found.ID, why)
		return nil, false
	}
	where, err := approval.HomeDir(conf, root, found.ID, found.Parent, found.Project)
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: リスクを記録できない: %s\n", err)
		return nil, false
	}
	judgements, ok := marks.ReadRecord(where, found.Parent, found.ID, marks.Judge)
	if !ok {
		judgements = pyjson.NewObject()
	}
	// record-risk の記録は、この終了が読んだ入力として一覧に載せ、この write-and-push でコミットする。
	fsio.NoteInput(marks.RecordPath(where, found.Parent, found.ID, marks.Judge))
	vars := map[string]string{
		env.BaseSHA: diff.Base,
		env.Head:    diff.Head,
		env.Ticket:  found.ID,
		env.Parent:  found.Parent,
	}
	score := risk.Evaluate(definition, diff, root, worktree, vars, judgements)
	if len(score.Pending) > 0 {
		askJudges(stderr, conf, root, found, diff, score.Pending, worktree)
		return nil, false
	}
	record := score.Object()
	record.Set("head", diff.Head)
	record.Set("base", diff.Base)
	record.Set("at", fsio.Stamp())
	record.Set("summary", diff.Summary())
	if len(definition.Dropped) > 0 {
		// 空として扱ったレイヤーの名前を残す。共通レイヤーだけで測ったことが、あとから記録を読んだユーザに分かる。
		record.Set("fallback", strings.Join(definition.Dropped, ","))
	}
	if failed := marks.WriteRecord(where, found.Parent, found.ID, marks.Risk, record); failed != "" {
		fmt.Fprintf(stderr, "ccnavi: リスクを記録できない: %s\n", failed)
		return nil, false
	}
	lines := score.Lines()
	lines[0] += "（" + diff.Summary() + "、配点 " + definition.Source + "）"
	if risk.Escalates(string(score.Level)) {
		lines = append(lines, "  実績のリスクが高いので、宣言に関わらずこのフェーズは人間レビューが必要です")
	}
	return lines, true
}

// askJudges は判定の無い定性の項目を言い、問いを state の下に書き出す（書けなければ問いの置き場を言わない）。
func askJudges(stderr io.Writer, conf *config.Settings, root string, found *ticket.Ticket, diff *risk.Diff, pending []*risk.Factor, worktree string) {
	prompt := risk.JudgePrompt(found.ID, found.Parent, diff, pending, worktree, root)
	where := ""
	if conf.State != "" {
		path := ospath.Join(conf.State, judgePromptPrefix+found.ID+".md")
		if failed := fsio.WriteText(path, prompt, fsio.LF, fsio.Plain); failed != "" {
			fmt.Fprintf(stderr, "ccnavi: 問いを書き出せない (%s)\n", failed)
		} else {
			where = path
		}
	}
	names := make([]string, len(pending))
	for i, f := range pending {
		names[i] = f.ID
	}
	fmt.Fprintf(stderr, "ccnavi: %s を閉じる前に、定性のリスク項目の判定が必要です: %s\n"+
		"  問いと差分の要約を渡してサブエージェントに判断させ、報告を '%s record-risk %s <項目> yes|no --reason <根拠>' "+
		"で記録してから閉じ直してください\n", found.ID, strings.Join(names, ", "), config.ScriptCommand(root, ticketScript), found.ID)
	if where != "" {
		fmt.Fprintf(stderr, "  問い: %s\n", where)
	}
}

// Cancel は doing/ から done/ へ動かし、取り消しの時刻と理由を書く（理由が要る）。advise が偽なら、子の取り消しの
// あとの `次:` を出さない（close-early が未着手の子をまとめて取り消すとき）。取り消した親には言う次が無い。
func Cancel(stdout, stderr io.Writer, conf *config.Settings, root, id, reason string, advise bool) int {
	found := cancelGate(stderr, conf, root, id, reason)
	if found == nil {
		return 1
	}
	reason = ospath.Strip(reason)
	fields := [][2]string{{"cancelled_at", fsio.Stamp()}, {"cancel_reason", reason}}
	return move(stdout, stderr, conf, root, found, place.Done, fields, "取り消し: "+reason, advise)
}
