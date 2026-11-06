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

// 判定基準。R3 は停止理由が正当かの判定で、不備検出の根拠にはしない。
const (
	r1 = "R1"
	r2 = "R2"
	r3 = "R3"
	r4 = "R4"
)

var (
	criteria      = []string{r1, r2, r3, r4}
	flawCriteria  = []string{r1, r2, r4}
	stopUnit      = []string{r1, r3} // Stop の機会を単位にする基準
	outcomes      = []string{pass, fail, unknown}
	locationKinds = []string{"final_answer", "proposal", "material"}
	fixKinds      = []string{"write_proposal", "revise_proposal", "send_proposal"}
)

// 基準ごとの結果。
const (
	pass    = "pass"
	fail    = "fail"
	unknown = "unknown"
)

// 判定役の分類。
const (
	ticketWork    = "ticket_work"
	notTicketWork = "not_ticket_work"
)

// rewriteQuestion は差し戻しの文の最後に必ず置く一文。
const rewriteQuestion = "本当にユーザの判断が要る問いなら、修正の後にその問いを最後に書き直して終えてください。"

// Return は agent hook がそのまま返す値。差し戻すときだけ OK が偽で、Reason に差し戻しの文が入る。
type Return struct {
	OK     bool
	Reason string
}

// OKReturn は止めない返り値（`{"ok": true}`）。
var OKReturn = Return{OK: true}

// Object は返り値の JSON。
func (r Return) Object() *pyjson.Object {
	if r.OK {
		return pyjson.NewObject("ok", true)
	}
	return pyjson.NewObject("ok", false, "reason", r.Reason)
}

// rejected は判定結果を受理しない理由。Python の Rejected の文か、判定役の誤出力で起きた例外の型の名前。
type rejected string

func (r rejected) Error() string { return string(r) }

// typeError は判定役の誤出力で Python が TypeError を出していたところ（集合に入らない値を照らした）。
const typeError rejected = "TypeError"

// unhashable は Python の set・frozenset に入れられない JSON の値（列とオブジェクト）か。
func unhashable(v any) bool {
	switch v.(type) {
	case []any, *pyjson.Object:
		return true
	}
	return false
}

type unit struct{ criterion, target string }

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
	classification := obj.Value("classification")
	if classification != ticketWork && classification != notTicketWork {
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
		if row, ok := r.(*pyjson.Object); ok && row.Value("criterion") == r2 {
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
	valid := obj.With("results", kept)
	seen := map[unit]bool{}
	for _, r := range kept {
		if err := checkRow(r, plan, seen); err != nil {
			return nil, err
		}
	}
	if classification == notTicketWork && len(plan.Proposals) > 0 {
		return nil, rejected("ccnavi が提案の操作を見つけているのに、チケット作業ではないと分類した")
	}
	if classification == ticketWork {
		for _, need := range required(plan) {
			if !seen[need] {
				return nil, rejected(need.criterion + " の結果が足りない（対象 " + need.target + "）")
			}
		}
	}
	return valid, nil
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
	criterion, ok1 := row.Value("criterion").(string)
	target, ok2 := row.Value("target").(string)
	outcome, ok3 := row.Value("result").(string)
	if !ok1 || !ok2 || !ok3 {
		return rejected("基準 ID・対象・結果は文字列")
	}
	if !slices.Contains(criteria, criterion) {
		return rejected("基準 ID が不正: " + pyfmt.Quote(criterion))
	}
	byStop := slices.Contains(stopUnit, criterion)
	key := unit{criterion, target}
	if byStop {
		if target != StopTarget {
			return rejected(criterion + " の対象は " + StopTarget)
		}
	} else if !slices.Contains(plan.Targets, target) {
		return rejected(criterion + " の対象が計画に無い: " + pyfmt.Quote(target))
	}
	if !slices.Contains(outcomes, outcome) {
		return rejected("結果が不正: " + pyfmt.Quote(outcome))
	}
	if seen[key] {
		return rejected(criterion + " が対象 " + target + " で重複")
	}
	seen[key] = true
	if criterion == r2 && slices.Contains(plan.NoR2, target) {
		return rejected("本文が無い版 " + target + " には R2 を出さない")
	}
	if outcome != fail {
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

func checkEntries(value any, kinds []string, label string) error {
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
		if _, isStr := kind.(string); !isStr || !slices.Contains(kinds, name) || !isText || ospath.Strip(text) == "" {
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
	if valid.Value("classification") == notTicketWork {
		return FinalOutOfScope
	}
	if insufficient {
		return FinalUnchecked
	}
	rows := map[unit]string{}
	for _, row := range resultRows(valid) {
		rows[unit{row.String("criterion"), row.String("target")}] = row.String("result")
	}
	for u, outcome := range rows {
		if outcome == fail && slices.Contains(flawCriteria, u.criterion) {
			return FinalFlawed
		}
	}
	for _, need := range required(plan) {
		if rows[need] == unknown {
			return FinalUnchecked
		}
	}
	return FinalOK
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
		if !slices.Contains(flawCriteria, row.String("criterion")) || row.String("result") != fail {
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
	// Effective は Stop の AI 判定の実効値。空なら計画に残した値、それも無ければ dry-run。
	Effective config.Mode
	// MaterialVersion は今の材料の版。計画の版と違えば材料が変わったとして拒否する。
	MaterialVersion string
	// StillUnapproved は対象の提案がまだ承認されていないか。承認済みになっていたら材料が変わったとして拒否する。
	// nil なら確かめない。
	StillUnapproved func(target string) bool
	Insufficient    bool
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
	valid, err := s.check(plan, in)
	if err != nil {
		attempt.Set(keyFinal, string(FinalUnchecked))
		attempt.Set(keyJudge, judgeCcnavi)
		attempt.Set(keyReason, "rejected: "+err.Error())
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
	effective := in.Effective
	if effective == "" {
		effective = plan.Effective
	}
	if effective == "" {
		effective = config.DryRun
	}
	final := classify(valid, plan, in.Insufficient || pyfmt.Truthy(attempt.Value(keyInsuff)))
	attempt.Set(keyFinal, string(final))
	attempt.Set(keyJudge, judgeAI)
	attempt.Set(keyAccepted, true)
	attempt.Set(keyResults, valid.Value("results"))
	attempt.Set(keyEffective, string(effective))
	attempt.Set(keyTainted, plan.Tainted)
	sendsBack := final == FinalFlawed && effective == config.Enable && !plan.Rejudge
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
	return Return{OK: false, Reason: rebuke(attempt.String(keyAttemptID), valid)}
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
