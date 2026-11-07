package planedit

import (
	"errors"
	"fmt"
	"math/big"
	"regexp"
	"slices"
	"strconv"

	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
)

// version は `--plan-order <親> --json` の答えの版。欄を足すだけなら上げない。
const version = 1

// refusalKind は振り直せない線の種類。
type refusalKind string

const (
	refusedCycle      refusalKind = "cycle"
	refusedLocked     refusalKind = "locked"
	refusedUnpackable refusalKind = "unpackable"
)

// refusal は振り直せない線。number はその番号。
type refusal struct {
	kind   refusalKind
	number int
	text   string
}

func (r refusal) json() *pyjson.Object {
	return pyjson.NewObject("kind", string(r.kind), "number", r.number, "text", r.text)
}

// Order は計画ごとの、元の番号 → 直接の先行（元の番号）。渡さない計画は提案のまま。
type Order map[phases.Part]map[int][]int

// renumbered は振り直した計画。moved は元の番号 → 振り直した番号（全体計画とフィードバック計画）。
type renumbered struct {
	plan     []ticket.PlanItem
	feedback []ticket.PlanItem
	moved    map[int]int
	refused  []refusal
}

// parts は答えに並べる順。
var parts = []phases.Part{phases.Plan, phases.FeedbackPart}

type section struct {
	key   phases.Part
	first int
	items []ticket.PlanItem
}

// sectionsOf は (欄, 最初の番号, ステップ)。フィードバック計画は空でも欄として数える。
func sectionsOf(t *ticket.Ticket) []section {
	out := []section{{phases.Plan, 1, t.Plan}}
	if t.FeedbackPlanned {
		out = append(out, section{phases.FeedbackPart, len(t.Plan) + 1, t.Feedback})
	}
	return out
}

func (s section) numbers() []int {
	out := make([]int, len(s.items))
	for i := range s.items {
		out[i] = s.first + i
	}
	return out
}

var digits = regexp.MustCompile(`\A[0-9]+\z`)

// ParseOrder は `--order` の JSON を読む。形は {"plan": {"1": [], "2": [3]}, "feedback": {...}}。番号は元の番号で、
// 渡した計画は全部の番号の先行をそこから取る（書いていない番号は先行なし）。読めなければ (nil, 理由)。
func ParseOrder(text string, parent *ticket.Ticket) (Order, string) {
	// JSON の形を 1 段ずつ確かめる。計画（plan・feedback）→ 番号 → 先行のリストの順で、範囲外・自分自身・非整数の先行は断る。
	data, err := pyjson.Loads(text)
	if err != nil {
		return nil, "JSON として読めない（" + errorClass(err) + "）"
	}
	obj, ok := data.(*pyjson.Object)
	if !ok {
		return nil, "`{\"plan\": {...}, \"feedback\": {...}}` の形で渡す"
	}
	ranges := map[phases.Part]section{}
	for _, s := range sectionsOf(parent) {
		ranges[s.key] = s
	}
	order := Order{}
	for k, value := range obj.All() {
		key := phases.Part(k.(string))
		if !slices.Contains(parts, key) {
			return nil, "`" + string(key) + "` は読まない。渡せるのは plan と feedback"
		}
		s, ok := ranges[key]
		if !ok {
			return nil, "提案にフィードバック計画（feedback）がありません。"
		}
		entries, ok := value.(*pyjson.Object)
		if !ok {
			return nil, "`" + string(key) + "` は番号 → 先行の番号のリストのマップで渡す"
		}
		span := fmt.Sprintf("（%d〜%d 番）", s.first, s.first+len(s.items)-1)
		inRange := func(n int) bool { return n >= s.first && n < s.first+len(s.items) }
		part := map[int][]int{}
		for name, preds := range entries.All() {
			written := name.(string)
			n, err := strconv.Atoi(written)
			if !digits.MatchString(written) || err != nil || !inRange(n) {
				return nil, "`" + string(key) + "` の番号 " + pyfmt.Repr(written) + " は計画にありません" + span
			}
			list, ok := preds.([]any)
			if !ok {
				return nil, fmt.Sprintf("`%s` の %d 番の先行は番号のリストで渡す", key, n)
			}
			var got []int
			for _, v := range list {
				m, isInt := v.(int)
				big, isBig := v.(*big.Int)
				switch {
				case !isInt && !isBig:
					return nil, fmt.Sprintf("`%s` の %d 番の先行 %s は番号ではない", key, n, pyfmt.Repr(v))
				case isBig:
					return nil, fmt.Sprintf("`%s` の %d 番の先行 %s は同じ計画の番号ではない%s", key, n, big.String(), span)
				case m == n:
					return nil, fmt.Sprintf("`%s` の %d 番は自分を先行にできない", key, n)
				case !inRange(m):
					return nil, fmt.Sprintf("`%s` の %d 番の先行 %d は同じ計画の番号ではない%s", key, n, m, span)
				}
				if !slices.Contains(got, m) {
					got = append(got, m)
				}
			}
			slices.Sort(got)
			part[n] = got
		}
		order[key] = part
	}
	return order, ""
}

// errorClass は JSON を読めなかった理由の分類名。利用者に見える文面に出る。
func errorClass(err error) string {
	var decode *pyjson.DecodeError
	switch {
	case errors.As(err, &decode):
		return "JSONDecodeError"
	case errors.Is(err, pyjson.ErrTooDeep):
		return "RecursionError"
	}
	return "ValueError"
}

// renumber は線（order）から番号を振り直す。断る線が 1 つでもあれば提案のまま返す。order が nil なら線は無いもの
// として扱う。固定した番号に入る線の変更（locked）と循環（cycle）を断り、固定した番号を動かさずに詰められなければ
// 断る（unpackable）。
func renumber(parent *ticket.Ticket, order Order, fixed map[int]bool) renumbered {
	var refused []refusal
	moved := map[int]int{}
	built := map[phases.Part][]ticket.PlanItem{}
	for _, s := range sectionsOf(parent) {
		numbers := s.numbers()
		given, ok := order[s.key]
		if !ok {
			// 渡さない計画は提案のまま（after の形の誤りも持ったまま）。
			for _, n := range numbers {
				moved[n] = n
			}
			built[s.key] = slices.Clone(s.items)
			continue
		}
		preds := map[int][]int{}
		for _, n := range numbers {
			preds[n] = given[n]
		}
		// 1. 子が承認された番号（fixed）の先行は変えられない。変わる線があれば断る。
		var locked []int
		for _, n := range numbers {
			if fixed[n] && !sameSet(preds[n], s.items[n-s.first].After) {
				locked = append(locked, n)
			}
		}
		for _, n := range locked {
			refused = append(refused, refusal{refusedLocked, n, fmt.Sprintf(
				"%d 番（%s）は子が承認されているので、入る線（先行のステップ）を変えられない", n, s.items[n-s.first].Type)})
		}
		// 2. 循環する線を断る。
		cycle := cycleOf(preds, numbers)
		if len(cycle) > 0 {
			path := ""
			for i, n := range append(slices.Clone(cycle), cycle[0]) {
				if i > 0 {
					path += " → "
				}
				path += strconv.Itoa(n)
			}
			refused = append(refused, refusal{refusedCycle, cycle[0], "循環になる線（" + path + "）"})
		}
		if len(locked) > 0 || len(cycle) > 0 {
			continue
		}
		// 3. 番号を振り直す（placed は元の番号 → 新しい番号）。位置を 1 から順に、先行が全部置かれたステップを入れていく。
		// 子が承認された番号は動かさない（その位置には元のステップだけが入り、ほかのステップはその位置に入れない）。
		// 置けるステップが無い位置があれば、固定した番号を動かさずには詰められないので断る。
		placed := map[int]int{}
		isPlaced := func(m int) bool { _, ok := placed[m]; return ok }
		allPlaced := func(ms []int) bool { return !slices.ContainsFunc(ms, func(m int) bool { return !isPlaced(m) }) }
		stuck := 0
		for _, pos := range numbers {
			pick := 0
			if fixed[pos] {
				if allPlaced(preds[pos]) {
					pick = pos
				}
			} else {
				for _, m := range numbers {
					if !isPlaced(m) && !fixed[m] && allPlaced(preds[m]) {
						pick = m
						break
					}
				}
			}
			if pick == 0 {
				stuck = pos
				break
			}
			placed[pick] = pos
		}
		if stuck != 0 {
			refused = append(refused, refusal{refusedUnpackable, stuck, fmt.Sprintf(
				"子が承認された番号を動かさずに番号を振れない（%d 番に置けるステップがありません）。"+
					"詰められないので、この線は引けない", stuck)})
			continue
		}
		// 4. 新しい並びに組み直す。after は新しい番号に写して昇順にそろえる。読み誤りの記録（AfterErrors・AfterRepeated）は、振り直した線では不要なので空にする。
		back := map[int]int{}
		for old, pos := range placed {
			moved[old] = pos
			back[pos] = old
		}
		items := make([]ticket.PlanItem, len(numbers))
		for i, pos := range numbers {
			item := s.items[back[pos]-s.first]
			var after []int
			for _, m := range preds[back[pos]] {
				after = append(after, placed[m])
			}
			slices.Sort(after)
			item.After, item.AfterErrors, item.AfterRepeated = after, nil, nil
			items[i] = item
		}
		built[s.key] = items
	}
	// 断る線が 1 つでもあれば、どの計画も動かさず元のまま返す（一部だけ振り直した状態を残さない）。
	if len(refused) > 0 {
		identity := map[int]int{}
		for _, step := range parent.Numbered() {
			identity[step.Number] = step.Number
		}
		return renumbered{slices.Clone(parent.Plan), slices.Clone(parent.Feedback), identity, refused}
	}
	return renumbered{built[phases.Plan], built[phases.FeedbackPart], moved, nil}
}

func sameSet(a, b []int) bool {
	x, y := slices.Clone(a), slices.Clone(b)
	slices.Sort(x)
	slices.Sort(y)
	return slices.Equal(slices.Compact(x), slices.Compact(y))
}

// cycleOf は先行を辿って循環があれば、その番号の並び（後続側から先行へ）。無ければ空。
func cycleOf(preds map[int][]int, numbers []int) []int {
	state := map[int]int{}
	var stack []int
	var visit func(n int) []int
	visit = func(n int) []int {
		state[n] = 1
		stack = append(stack, n)
		for _, m := range preds[n] {
			if state[m] == 1 {
				return slices.Clone(stack[slices.Index(stack, m):])
			}
			if _, seen := state[m]; !seen {
				if found := visit(m); found != nil {
					return found
				}
			}
		}
		stack = stack[:len(stack)-1]
		state[n] = 2
		return nil
	}
	for _, n := range numbers {
		if _, seen := state[n]; !seen {
			if found := visit(n); found != nil {
				slices.Reverse(found)
				return found
			}
		}
	}
	return nil
}
