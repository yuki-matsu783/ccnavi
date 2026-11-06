package templates

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/ticket"
)

// NameLimit は名前の長さの上限。名前はファイル名になるので、区切りも空白も入れない。
const NameLimit = 64

var namePattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)

// windowsReserved は Windows でファイル名にできない予約名（拡張子が付いても同じ）。新しく作る名前だけを断り、
// 置いてあるファイルの読み込みは断らない。
var windowsReserved = func() map[string]bool {
	out := map[string]bool{"con": true, "prn": true, "aux": true, "nul": true}
	for n := 1; n <= 9; n++ {
		out[fmt.Sprintf("com%d", n)] = true
		out[fmt.Sprintf("lpt%d", n)] = true
	}
	return out
}()

// validName は名前の形か。Python の re の `$` は末尾の改行 1 つの前でも当たるので、それも通す。
func validName(name string) bool {
	return namePattern.MatchString(strings.TrimSuffix(name, "\n")) && len([]rune(name)) <= NameLimit
}

// NewNameProblem は新しく作るテンプレートの名前を断る理由。通れば空。読み込みと同じ形に加えて、末尾の `.` と
// Windows の予約名を断る。管理画面（templatesmodel.ts の validNewTemplateName）と同じ決まり。
func NewNameProblem(name string) string {
	if !validName(name) {
		return fmt.Sprintf("英数字で始め、英数字と `._-` だけ。%d 字まで", NameLimit)
	}
	if strings.HasSuffix(name, ".") {
		return "末尾に `.` は付けられない（Windows でファイル名にできない）"
	}
	stem, _, _ := strings.Cut(name, ".")
	if windowsReserved[pathcase.Fold(stem)] {
		return "Windows の予約名（con prn aux nul com1-9 lpt1-9）は使えない"
	}
	return ""
}

// problems は苦情を溜める。苦情の rule はファイル名（拡張子を除く）。
type problems struct {
	rule string
	list []rules.Problem
}

func (p *problems) add(sev rules.Severity, text string) {
	p.list = append(p.list, rules.Problem{Severity: sev, Rule: p.rule, Detail: text})
}

func (p *problems) error(text string) { p.add(rules.Error, text) }

func (p *problems) failed() bool { return hasError(p.list) }

func hasError(list []rules.Problem) bool {
	return slices.ContainsFunc(list, func(p rules.Problem) bool { return p.Severity == rules.Error })
}

// Parse はテンプレートの中身（バイト）を読む。読めなければ (nil, error を含む苦情)。stem を渡せば name が
// それと同じか確かめる（ファイル名が名前）。読み手はフローと同じ（別名を拒み、UTF-8、BOM は 1 つ外す）。
func Parse(raw []byte, stem string) (*Template, []rules.Problem) {
	said := &problems{rule: stem}
	data, why := flow.ParseYAML(raw)
	if why != "" {
		said.error(why)
		return nil, said.list
	}
	top, ok := data.(*yaml.Map)
	if !ok {
		said.error("最上位がマッピングではない")
		return nil, said.list
	}

	name, ok := top.Get(keyName).(string)
	if !ok || ospath.Strip(name) == "" {
		said.error("`name` が無いか、文字列でない")
		name = ""
	}
	name = ospath.Strip(name)
	if name != "" && !validName(name) {
		said.error(fmt.Sprintf("`name` の `%s` は指定できない（英数字で始め、英数字と `.` `_` `-` だけ。%d 字まで）",
			line(name), NameLimit))
	} else if stem != "" && name != "" && name != stem {
		said.error(fmt.Sprintf("`name` の `%s` がファイル名（%s%s）と違う", name, stem, Suffix))
	}

	texts := map[string]string{}
	for _, key := range []string{keyDescription, keyWhen} {
		switch v := top.Get(key).(type) {
		case nil:
		case string:
			texts[key] = ospath.Strip(v)
		default:
			said.error("`" + key + "` は文字列で書く")
		}
	}
	if texts[keyDescription] == "" && !said.failed() {
		said.error("`description` がありません（一覧に出す説明）")
	}

	plan := readItems(top.Get(keyPlan), keyPlan, said, true)
	feedback := readItems(top.Get(keyFeedback), keyFeedback, said, false)
	flows := readFlows(top.Get(keyFlows), said)
	extra := &yaml.Map{}
	for k, v := range top.All() {
		if s, isStr := k.(string); isStr && slices.Contains(Fields, s) {
			continue
		}
		extra.Put(k, v)
		said.add(rules.Warn, "未知の欄 `"+line(k)+"` は読まない")
	}
	if said.failed() {
		return nil, said.list
	}
	if flows == nil {
		flows = map[int]*yaml.Map{}
	}
	return &Template{
		Name: name, Description: texts[keyDescription], When: texts[keyWhen],
		Plan: plan, Feedback: feedback, Flows: flows, Extra: extra,
	}, said.list
}

// readItems は計画のステップの欄（key）のリストを読む。欄が読めなければ nil。required なら、欄が無いことと
// 空であることを、どちらも苦情にする。ステップは定義の名前だけの文字列か {type, review, deferred, after}。
// 苦情のあるステップは入れない。
func readItems(raw any, key string, said *problems, required bool) []Item {
	if raw == nil {
		if required {
			said.error("`" + key + "` がありません。")
		}
		return nil
	}
	list, ok := raw.([]any)
	if !ok {
		said.error("`" + key + "` はリストで書く")
		return nil
	}
	if required && len(list) == 0 {
		said.error("`" + key + "` が空")
		return nil
	}
	var out []Item
	for i, entry := range list {
		where := fmt.Sprintf("`%s[%d]`", key, i+1)
		if s, isStr := entry.(string); isStr {
			if ospath.Strip(s) == "" {
				said.error(where + " が空")
				continue
			}
			out = append(out, Item{Type: ospath.Strip(s)})
			continue
		}
		m, isMap := entry.(*yaml.Map)
		if !isMap {
			said.error(where + " は定義の名前か {type, review, deferred, after} で書く")
			continue
		}
		for _, k := range m.Keys() {
			if s, isStr := k.(string); !isStr || !slices.Contains(itemFields, s) {
				said.add(rules.Warn, where+" の未知の欄 `"+line(k)+"` は読まない")
			}
		}
		kind, isStr := m.Get(itemType).(string)
		if !isStr || ospath.Strip(kind) == "" {
			said.error(where + " に `type` がありません。")
			continue
		}
		review, deferred, why := ticket.StepFlags(m, where)
		if why != "" {
			said.error(why)
			continue
		}
		after, ok := readAfter(m.Get(itemAfter), where, said)
		if !ok {
			continue
		}
		out = append(out, Item{Type: ospath.Strip(kind), Review: review, Deferred: deferred, After: after})
	}
	return out
}

// readAfter はステップの after（整数の添字のリスト）を昇順・重複なしに整える。重複は warn。
func readAfter(raw any, where string, said *problems) ([]int, bool) {
	if raw == nil {
		return nil, true
	}
	list, ok := raw.([]any)
	if !ok {
		said.error(where + " の `after` は添字のリストで書く")
		return nil, false
	}
	var numbers []int
	for _, v := range list {
		n, isInt := v.(int)
		if !isInt {
			said.error(where + " の `after` は整数の添字で書く")
			return nil, false
		}
		numbers = append(numbers, n)
	}
	slices.Sort(numbers)
	numbers = slices.Compact(numbers)
	if len(numbers) != len(list) {
		said.add(rules.Warn, where+" の `after` に同じ添字を 2 度挙げている")
	}
	return numbers, true
}

// readFlows は flows（全体計画のステップの添字 → フローの本文）を読む。添字は整数か、ASCII の数字だけの文字列。
func readFlows(raw any, said *problems) map[int]*yaml.Map {
	if raw == nil {
		return nil
	}
	m, ok := raw.(*yaml.Map)
	if !ok {
		said.error("`flows` は、全体計画のステップの添字をキーにしたマッピングで書く")
		return nil
	}
	out := map[int]*yaml.Map{}
	for key, body := range m.All() {
		index, ok := flowIndex(key)
		if !ok {
			said.error("`flows` のキー `" + line(key) + "` は全体計画のステップの添字（整数）で書く")
			continue
		}
		if _, dup := out[index]; dup {
			said.error(fmt.Sprintf("`flows` に添字 %d が 2 度ある", index))
			continue
		}
		flowBody, isMap := body.(*yaml.Map)
		if !isMap {
			said.error(fmt.Sprintf("`flows[%d]` はフローの本文（マッピング）で書く", index))
			continue
		}
		out[index] = flowBody
	}
	return out
}

func flowIndex(key any) (int, bool) {
	switch k := key.(type) {
	case int:
		return k, true
	case string:
		if k == "" || strings.Trim(k, "0123456789") != "" {
			return 0, false
		}
		n, err := strconv.Atoi(k)
		return n, err == nil
	}
	return 0, false
}
