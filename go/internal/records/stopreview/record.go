package stopreview

import (
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// criterionID は判定基準。R3 は停止理由が正当かの判定で、不備検出の根拠にはしない。
type criterionID string

const (
	r1 criterionID = "R1"
	r2 criterionID = "R2"
	r3 criterionID = "R3"
	r4 criterionID = "R4"
)

var (
	criteria     = []criterionID{r1, r2, r3, r4}
	flawCriteria = []criterionID{r1, r2, r4}
	stopUnit     = []criterionID{r1, r3} // Stop の機会を単位にする基準
)

// outcome は基準ごとの結果。
type outcome string

const (
	pass    outcome = "pass"
	fail    outcome = "fail"
	unknown outcome = "unknown"
)

var outcomes = []outcome{pass, fail, unknown}

// entryKind は不備の行の問題箇所（locations）と修正（fixes）の種類。
type entryKind string

var (
	locationKinds = []entryKind{"final_answer", "proposal", "material"}
	fixKinds      = []entryKind{"write_proposal", "revise_proposal", "send_proposal"}
)

// classification は判定役の分類。
type classification string

const (
	ticketWork    classification = "ticket_work"
	notTicketWork classification = "not_ticket_work"
)

// rewriteQuestion は差し戻しの文の最後に必ず置く一文。
const rewriteQuestion = "本当にユーザの判断が要る問いなら、修正の後にその問いを最後に書き直して終えてください。"

// Return は agent hook がそのまま返す値。差し戻すときだけ OK が偽で、Reason に差し戻しの文が入る。
type Return struct {
	ok     bool
	reason string
}

// OKReturn は止めない返り値（`{"ok": true}`）。
var OKReturn = Return{ok: true}

// Object は返り値の JSON。
func (r Return) Object() *pyjson.Object {
	if r.ok {
		return pyjson.NewObject("ok", true)
	}
	return pyjson.NewObject("ok", false, "reason", r.reason)
}

// rejected は判定結果を受理しない理由。検証が付ける拒否の文か、判定役の誤出力で起きた型エラーの名前。
type rejected string

func (r rejected) Error() string { return string(r) }

// typeError は判定役の誤出力で、集合に入らない値（列・オブジェクト）を集合の要素と照らしたときの拒否の名前。
const typeError rejected = "TypeError"

// unhashable は集合の要素にできない JSON の値（列とオブジェクト）か。
func unhashable(v any) bool {
	switch v.(type) {
	case []any, *pyjson.Object:
		return true
	}
	return false
}

type unit struct {
	criterion criterionID
	target    string
}

// validate は判定結果の構造を確かめる。指摘が正しいことは確かめない。受け取るのは
// `{"attempt_id", "classification", "results": [{criterion, target, result, locations: [{kind, text}],
// mismatch, fixes: [{kind, text}]}]}`。受理した形（本文の無い版への余分な R2 を除いたもの）を返す。
func validate(result any, plan Plan) (*pyjson.Object, error) {
	obj, ok := result.(*pyjson.Object)
	if !ok {
		return nil, rejected("結果がオブジェクトではない")
	}
	if id, ok := obj.Value("attempt_id").(string); !ok || id != plan.AttemptID {
		return nil, rejected("試行 ID が計画と合わない")
	}
	class := classifiedAs(obj)
	if class != ticketWork && class != notTicketWork {
		return nil, rejected("classification が不正")
	}
	rows, ok := obj.Value("results").([]any)
	if !ok {
		return nil, rejected("results がリストではない")
	}
	// 本文が無い版の R2 は対象外。判定役が余分に出した行は、受理せず捨てる。
	// 記録ごと拒否すると、ほかの基準の結果まで失う。実機で余分な行を出す例があった。
	kept := []any{}
	for _, r := range rows {
		if row, ok := r.(*pyjson.Object); ok && row.Value("criterion") == string(r2) {
			target := row.Value("target")
			if unhashable(target) {
				return nil, typeError
			}
			if s, ok := target.(string); ok && slices.Contains(plan.NoR2, s) {
				continue
			}
		}
		kept = append(kept, r)
	}
	// 行ごとの形と、(基準, 対象) の重複を確かめる。
	valid := obj.With("results", kept)
	seen := map[unit]bool{}
	for _, r := range kept {
		if err := checkRow(r, plan, seen); err != nil {
			return nil, err
		}
	}
	// 分類と計画の食い違いを確かめる。提案の操作を見つけているのに「チケット作業でない」は受理しない。
	// チケット作業なら、計画が求める (基準, 対象) の結果がすべて要る。
	if class == notTicketWork && len(plan.Proposals) > 0 {
		return nil, rejected("ccnavi が提案の操作を見つけているのに、チケット作業ではないと分類した")
	}
	if class == ticketWork {
		for _, need := range required(plan) {
			if !seen[need] {
				return nil, rejected(string(need.criterion) + " の結果が足りない（対象 " + need.target + "）")
			}
		}
	}
	return valid, nil
}

// classifiedAs は判定役の分類。文字列でなければ空。
func classifiedAs(obj *pyjson.Object) classification {
	text, _ := obj.Value("classification").(string)
	return classification(text)
}

// required は計画から決まる、結果が要る (基準, 対象) の一覧。本文が無い版には R2 を求めない。
func required(plan Plan) []unit {
	needs := []unit{{r1, StopTarget}, {r3, StopTarget}}
	for _, target := range plan.Targets {
		if target == StopTarget {
			continue
		}
		if !slices.Contains(plan.NoR2, target) {
			needs = append(needs, unit{r2, target})
		}
		needs = append(needs, unit{r4, target})
	}
	return needs
}

func checkRow(r any, plan Plan, seen map[unit]bool) error {
	row, ok := r.(*pyjson.Object)
	if !ok {
		return rejected("結果の行がオブジェクトではない")
	}
	name, ok1 := row.Value("criterion").(string)
	target, ok2 := row.Value("target").(string)
	result, ok3 := row.Value("result").(string)
	if !ok1 || !ok2 || !ok3 {
		return rejected("基準 ID・対象・結果は文字列")
	}
	criterion := criterionID(name)
	if !slices.Contains(criteria, criterion) {
		return rejected("基準 ID が不正: " + pyfmt.Quote(name))
	}
	byStop := slices.Contains(stopUnit, criterion)
	key := unit{criterion, target}
	if byStop {
		if target != StopTarget {
			return rejected(name + " の対象は " + StopTarget)
		}
	} else if !slices.Contains(plan.Targets, target) {
		return rejected(name + " の対象が計画に無い: " + pyfmt.Quote(target))
	}
	if !slices.Contains(outcomes, outcome(result)) {
		return rejected("結果が不正: " + pyfmt.Quote(result))
	}
	if seen[key] {
		return rejected(name + " が対象 " + target + " で重複")
	}
	seen[key] = true
	if criterion == r2 && slices.Contains(plan.NoR2, target) {
		return rejected("本文が無い版 " + target + " には R2 を出さない")
	}
	if outcome(result) != fail {
		return nil
	}
	if err := checkEntries(row.Value("locations"), locationKinds, "問題箇所"); err != nil {
		return err
	}
	if slices.Contains(flawCriteria, criterion) {
		return checkEntries(row.Value("fixes"), fixKinds, "修正")
	}
	return nil
}

func checkEntries(value any, kinds []entryKind, label string) error {
	items, ok := value.([]any)
	if !ok || len(items) == 0 {
		return rejected(label + "が無い")
	}
	for _, item := range items {
		obj, ok := item.(*pyjson.Object)
		if !ok {
			return rejected(label + "の形が不正")
		}
		kind := obj.Value("kind")
		if unhashable(kind) {
			return typeError
		}
		name, _ := kind.(string)
		text, isText := obj.Value("text").(string)
		if _, isStr := kind.(string); !isStr || !slices.Contains(kinds, entryKind(name)) || !isText || ospath.Strip(text) == "" {
			return rejected(label + "の形が不正")
		}
	}
	return nil
}

// resultRows は受理した結果の行（validate が形を確かめたもの）。
func resultRows(valid *pyjson.Object) []*pyjson.Object {
	items, _ := valid.Value("results").([]any)
	rows := make([]*pyjson.Object, 0, len(items))
	for _, item := range items {
		rows = append(rows, item.(*pyjson.Object))
	}
	return rows
}

// classify は Stop 全体の最終分類。
//  1. チケット作業の依頼ではない → 対象外（判定者は AI）
//  2. 材料不足（切り詰め・読めない） → 未検査（「無い」を根拠にした不備も受理しない）
//  3. R1・R2・R4 に受理された不備がある → 不備検出
//  4. 必要な基準に判定不能がある → 未検査
//  5. それ以外 → 終了可
func classify(valid *pyjson.Object, plan Plan, insufficient bool) Final {
	if classifiedAs(valid) == notTicketWork {
		return FinalOutOfScope
	}
	if insufficient {
		return FinalUnchecked
	}
	rows := map[unit]outcome{}
	for _, row := range resultRows(valid) {
		rows[unit{criterionID(row.String("criterion")), row.String("target")}] = outcome(row.String("result"))
	}
	for u, result := range rows {
		if result == fail && slices.Contains(flawCriteria, u.criterion) {
			return finalFlawed
		}
	}
	for _, need := range required(plan) {
		if rows[need] == unknown {
			return FinalUnchecked
		}
	}
	return finalOK
}

// rebuke は差し戻しの文。試行 ID、基準 ID・問題箇所・修正、問いを書き直す一文を必ず含める。
func rebuke(aid string, valid *pyjson.Object) string {
	lines := []string{"Stop の AI 判定（試行 " + aid + "）が提案の不備を見つけました。直してから終えてください。"}
	entries := func(row *pyjson.Object, key, label string) {
		items, _ := row.Value(key).([]any)
		for _, item := range items {
			obj := item.(*pyjson.Object)
			lines = append(lines, "  - "+label+"（"+obj.String("kind")+"）: "+ospath.Strip(obj.String("text")))
		}
	}
	for _, row := range resultRows(valid) {
		if !slices.Contains(flawCriteria, criterionID(row.String("criterion"))) || outcome(row.String("result")) != fail {
			continue
		}
		lines = append(lines, "- "+row.String("criterion")+"（対象 "+row.String("target")+"）")
		entries(row, "locations", "問題箇所")
		if mismatch, ok := row.Value("mismatch").(string); ok && ospath.Strip(mismatch) != "" {
			lines = append(lines, "  - 依頼との不一致: "+ospath.Strip(mismatch))
		}
		entries(row, "fixes", "修正")
	}
	lines = append(lines, rewriteQuestion)
	return strings.Join(lines, "\n")
}

// AcceptInput は record が受け取ったものと、受理の条件。
type AcceptInput struct {
	Result any // 判定役が出した JSON（pyjson.Loads の値）。読めなければ nil
	// effective は Stop の AI 判定の実効値。空なら計画に残した値、それも無ければ dry-run。
	effective config.Mode
	// MaterialVersion は今の材料の版。計画の版と違えば材料が変わったとして拒否する。
	MaterialVersion string
	// StillUnapproved は対象の提案がまだ承認されていないか。承認済みになっていたら材料が変わったとして拒否する。
	// nil なら確かめない。
	StillUnapproved func(target string) bool
	insufficient    bool
}

// Accept は record の本体。結果を検証して最終分類を確定し、agent hook の返り値を返す。
// 計画か試行が無い、または試行が実行中でなければ何もしない。拒否したときは未検査で閉じて止めない。
// 不備検出の結果は、enable のときだけ差し戻す。再判定は差し戻さず、元の試行に解消か未解消を残す。
func (s Store) Accept(key string, in AcceptInput) Return {
	plan, ok := s.ReadPlan(key)
	if !ok {
		return OKReturn
	}
	attempt := s.readAttempt(plan.AttemptID)
	if attempt.Len() == 0 || stateOf(attempt) != running {
		return OKReturn
	}
	// 1. 検証して、拒否なら未検査で閉じる（判定役の誤りで Stop を止め続けない）。
	valid, err := s.check(plan, in)
	if err != nil {
		attempt.Set(keyFinal, string(FinalUnchecked))
		attempt.Set(keyJudge, string(judgeCcnavi))
		attempt.Set(keyReason, string(reasonRejected)+err.Error())
		attempt.Set(keyAccepted, false)
		attempt.Set(keyTainted, plan.Tainted)
		if s.move(attempt, done) {
			s.logEvent(eventFinal, attempt)
		}
		if plan.Rejudge && pyfmt.Truthy(attempt.Value(keyOriginal)) {
			s.resolveOriginal(attempt.String(keyOriginal), FinalUnchecked)
		}
		return OKReturn
	}
	// 2. 受理した結果から最終分類を決めて書く。実効値は引数、計画、dry-run の順で決める。
	effective := in.effective
	if effective == "" {
		effective = plan.Effective
	}
	if effective == "" {
		effective = config.DryRun
	}
	final := classify(valid, plan, in.insufficient || pyfmt.Truthy(attempt.Value(keyInsuff)))
	attempt.Set(keyFinal, string(final))
	attempt.Set(keyJudge, string(judgeAI))
	attempt.Set(keyAccepted, true)
	attempt.Set(keyResults, valid.Value("results"))
	attempt.Set(keyEffective, string(effective))
	attempt.Set(keyTainted, plan.Tainted)
	// 3. 差し戻すのは、不備検出で enable、かつ再判定でないときだけ。それ以外は done にして止めない。
	sendsBack := final == finalFlawed && effective == config.Enable && !plan.Rejudge
	target := done
	if sendsBack {
		target = sentBack
		attempt.Set(keySentAt, fsio.UnixSeconds(time.Now()))
	}
	s.move(attempt, target)
	s.logEvent(eventFinal, attempt)
	if plan.Rejudge && pyfmt.Truthy(attempt.Value(keyOriginal)) {
		s.resolveOriginal(attempt.String(keyOriginal), final)
	}
	if !sendsBack {
		return OKReturn
	}
	s.markSentBack(key)
	return Return{ok: false, reason: rebuke(attempt.String(keyAttemptID), valid)}
}

// check は材料の版・結果の形・承認状態を確かめる。どれかに当たれば拒否の理由を返す。
func (s Store) check(plan Plan, in AcceptInput) (*pyjson.Object, error) {
	if plan.MaterialVersion != in.MaterialVersion {
		return nil, rejected("材料の版が変わった")
	}
	valid, err := validate(in.Result, plan)
	if err != nil {
		return nil, err
	}
	if in.StillUnapproved != nil {
		for _, target := range plan.Targets {
			if target != StopTarget && !in.StillUnapproved(target) {
				return nil, rejected("承認状態が変わった")
			}
		}
	}
	return valid, nil
}
