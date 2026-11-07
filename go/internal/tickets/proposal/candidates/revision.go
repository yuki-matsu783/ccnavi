package candidates

import (
	"fmt"
	"maps"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/phase"
)

// revisionProblems は親の改版を受けてよいか。変えてよいのは計画・コピー（phases:）・範囲。コピーは子が承認
// された番号（固定した番号）が使う定義だけ承認済みチケットの値のまま、ほかは今の phases.yml と同じ。
func revisionProblems(conf *config.Settings, root string, revised, current *ticket.Ticket, cfg *phases.Types) []rules.Problem {
	fixed := FixedNumbers(conf, root, current.ID)
	heldTypes := phases.TypesOf(current)
	held := map[string]*phases.PhaseType{}
	for n := range fixed {
		if item := current.ItemAt(n); item != nil {
			if pt := heldTypes.Get(item.Type); pt != nil {
				held[item.Type] = pt
			}
		}
	}
	problems := PlanProblems(revised, cfg, held, false)
	if hasError(problems) {
		return problems
	}
	errorf := func(detail string) {
		problems = append(problems, rules.Problem{Severity: rules.Error, Rule: revised.ID, Detail: detail})
	}
	// 範囲を変える改版は、作業中の子が居ないときだけ（子の範囲は親の範囲の中に収まる前提で承認されている）。
	// 書かれる節が違うなら読めない項が混ざっていないかを見る（読めない項は entries に入らず、署名に出ない）。
	if slices.ContainsFunc(rules.Sections(), func(s rules.Decision) bool {
		return !yaml.Equal(revised.Raw.Get(string(s)), current.Raw.Get(string(s)))
	}) {
		problems = append(problems, unreadableScope(revised)...)
	}
	if !approval.SameScope(revised, current) {
		if busy := openChildren(conf, root, current.ID); len(busy) > 0 {
			errorf("範囲を変える改版は、作業中の子が居ないときだけ出せる" +
				"（作業中の子: " + strings.Join(busy, ", ") + "）。" +
				"`ccnavi-ticket.sh cancel <子> --reason <理由>` で子を取り消してから、" +
				"改版を出し直す。範囲を変えたあとの子は、新しい子として提案する")
		}
	}
	if revised.Title != current.Title || revised.Issue != current.Issue || revised.IssueRepo != current.IssueRepo {
		errorf("改版で変えられるのは plan・feedback・phases・範囲だけ。" +
			"タイトルかissue番号が承認済みチケットと違う")
	}
	// mr: は承認のあとに ccnavi が書き足すことがある（review-request）。片方が空なら比べない。
	if revised.MR != 0 && current.MR != 0 && revised.MR != current.MR {
		errorf("改版で変えられるのは plan・feedback・phases・範囲だけ。" +
			"`mr:` が承認済みチケットと違う")
	}
	problems = append(problems, LockProblems(revised, current, fixed, nil)...)
	// フィードバック計画の番号は全体計画の続きなので、立てたあと（と立てる改版）は全体計画を変えさせない。
	planChanged := !approval.SamePlan(revised, current, phases.Plan)
	if planChanged && (current.FeedbackPlanned || revised.FeedbackPlanned) {
		when := "立てる改版で"
		if current.FeedbackPlanned {
			when = "立てたあと"
		}
		errorf("フィードバック計画を" + when + "は全体計画を変えられない" +
			"（フィードバック計画の番号は全体計画の続きで、ステップを追加すると番号がずれる。" +
			"全体計画を変えるなら、フィードバック計画を出す前に改版する）")
	}
	// フィードバック計画は無い状態から 1 回だけ、全体計画の最後のレビューが済んでから。
	if !approval.SamePlan(revised, current, phases.FeedbackPart) {
		if current.FeedbackPlanned {
			elsewhere := "残りは別 issue に回してください（ccnavi-user-decide.sh）"
			errorf("フィードバック計画は 1 回だけ作成でき、変更できません。ただし、承認済みの" +
				"フィードバック作業フェーズの定義の上限と親の範囲に収まる修正は、その" +
				"フェーズに子チケットを追加して対応できます（設計の修正でも、定義が親の範囲の" +
				"設計書を持つ場合は同様です）。収まらない場合は、ユーザによる定義の変更か、" +
				"別作業への切り出しが必要です。切り出す場合は、" + elsewhere)
		} else if !phase.PlanFinished(conf, root, current) {
			errorf("フィードバック計画は、全体計画のフェーズが全部閉じてレビューが済んでから出す。" +
				"作業が終わるまで対応は計画できない")
		}
	}
	return problems
}

// unreadableScope は範囲の節に判定が読めない項が混ざっていないか。改版は提案の節をそのまま書くが、画面の差分は
// 読めた項から組むので、読めなかった項は画面に出ないまま残る。数が合わなければ拒む。
func unreadableScope(t *ticket.Ticket) []rules.Problem {
	raw := 0
	for _, section := range rules.Sections() {
		value := t.Raw.Get(string(section))
		if value == nil {
			continue
		}
		items, ok := value.([]any)
		if !ok {
			return []rules.Problem{unreadable(t, "`"+string(section)+"` がリストではない")}
		}
		raw += len(items)
	}
	if raw != len(t.Entries) {
		return []rules.Problem{unreadable(t, fmt.Sprintf("判定が読めない項が %d 件ある（`%s --lint` で確かめる）",
			raw-len(t.Entries), config.Launcher))}
	}
	return nil
}

func unreadable(t *ticket.Ticket, why string) rules.Problem {
	return rules.Problem{Severity: rules.Error, Rule: t.ID, Detail: "範囲の節に、判定が読めない項がある改版は出せない: " + why}
}

// openChildren はこの親の子で作業中（doing/）のもの。閉じた子とレビュー待ちの子は数えない。数えるのは正とする
// ツリーの側だけ（子のワークツリーに残った版を数えると、取り消しても改版が通らなくなる）。
func openChildren(conf *config.Settings, root, parentID string) []string {
	opened, _ := approval.Scan(conf, root, false, nil)
	var out []string
	for _, t := range opened {
		if t.Parent == parentID && t.State == place.Doing {
			out = append(out, t.ID)
		}
	}
	slices.Sort(out)
	return out
}

// FixedNumbers はこの親で子が承認された（開いていても閉じていても）番号。改版の錠が固定する番号。
func FixedNumbers(conf *config.Settings, root, parentID string) map[int]bool {
	out := map[int]bool{}
	for _, t := range approval.ReadRaw(conf, root).Everything() {
		if t.Parent == parentID && t.IsChild() {
			out[t.Phase] = true
		}
	}
	return out
}

type stepKey struct {
	typ      string
	review   ticket.Need
	deferred bool
}

func keyOf(item *ticket.PlanItem) stepKey { return stepKey{item.Type, item.Review, item.Deferred} }

func numberedMap(t *ticket.Ticket) map[int]*ticket.PlanItem {
	out := map[int]*ticket.PlanItem{}
	for _, s := range t.Numbered() {
		out[s.Number] = s.Item
	}
	return out
}

// LockProblems は改版の番号ごとの錠。fixed（子が承認された番号）のステップは、定義・review・番号・推移的な先行を
// 変えさせない。推移的な形で比べるのは、--explain の「先行 …」を写しても直接の先行だけを書いても、同じ意味なら
// 通すため。固定した番号に固定していない番号が先行する形では、その先行もステップの同一性で比べる（番号だけだと
// 先行の番号に別のステップを入れ替える改版が通る）。延期の引き受け手は、どちらかが固定した番号のときだけ比べる。
// moved は番号を振り直したとき（--plan-order）の元の番号 → 振り直した番号で、延期は元のステップで比べる。
func LockProblems(revised, current *ticket.Ticket, fixed map[int]bool, moved map[int]int) []rules.Problem {
	var problems []rules.Problem
	errorf := func(format string, args ...any) {
		problems = append(problems, rules.Problem{Severity: rules.Error, Rule: revised.ID, Detail: fmt.Sprintf(format, args...)})
	}
	heldItems, newItems := numberedMap(current), numberedMap(revised)
	held, fresh := phases.Compute(current), phases.Compute(revised)
	for _, n := range slices.Sorted(maps.Keys(fixed)) {
		before, after := heldItems[n], newItems[n]
		if before == nil {
			continue
		}
		if after == nil || keyOf(after) != keyOf(before) {
			shown := "無い"
			if after != nil {
				shown = after.Type
			}
			errorf("%d 番目（%s）は子が承認されているので、定義・review・番号を変えられない（改版では %s）。子の phase が指す先が変わる",
				n, before.Type, shown)
			continue
		}
		if !slices.Equal(held.Waits[n], fresh.Waits[n]) {
			errorf("%d 番目（%s）は子が承認されているので、先行を変えられない（いま先行: %s、改版: %s）",
				n, before.Type, numbers(held.Waits[n]), numbers(fresh.Waits[n]))
			continue
		}
		for _, m := range held.Waits[n] {
			if fixed[m] {
				continue
			}
			was, now := heldItems[m], newItems[m]
			if now == nil || was == nil || keyOf(now) != keyOf(was) {
				shown := "?"
				if was != nil {
					shown = was.Type
				}
				errorf("%d 番目に子の無い %d 番目が先行しているので、%d 番目のステップ（%s）を入れ替えられない", n, m, m, shown)
			}
		}
	}
	at := func(n int) int {
		if m, ok := moved[n]; ok {
			return m
		}
		return n
	}
	back := map[int]int{}
	for old, renumbered := range moved {
		back[renumbered] = old
	}
	seen := map[int]bool{}
	for n := range held.ReviewAt {
		seen[n] = true
	}
	for m := range fresh.ReviewAt {
		if old, ok := back[m]; ok {
			seen[old] = true
		} else {
			seen[m] = true
		}
	}
	for _, n := range slices.Sorted(maps.Keys(seen)) {
		beforeAt, afterAt := held.ReviewAt[n], fresh.ReviewAt[at(n)]
		if beforeAt == afterAt {
			continue
		}
		if (beforeAt != 0 && fixed[beforeAt]) || (afterAt != 0 && fixed[afterAt]) {
			errorf("延期の引き受け手が変わる（%d 番目の延期: %s → %s）。子が承認された番号が引き受ける延期は、延期先を変えられない",
				n, orNone(beforeAt), orNone(afterAt))
		}
	}
	return problems
}

func orNone(n int) string {
	if n == 0 {
		return "無し"
	}
	return strconv.Itoa(n)
}

func numbers(found []int) string {
	if len(found) == 0 {
		return "先行なし"
	}
	parts := make([]string, len(found))
	for i, n := range found {
		parts[i] = strconv.Itoa(n)
	}
	return strings.Join(parts, ", ")
}
