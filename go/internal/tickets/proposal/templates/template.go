package templates

import (
	"fmt"
	"slices"
	"strings"
	"unicode"

	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/ticket"
)

// 欄の名前。fields は書く順で、未知の欄（extra）を見分けるのにも使う。
const (
	keyName        = "name"
	keyDescription = "description"
	keyWhen        = "when"
	keyPlan        = "plan"
	keyFeedback    = "feedback"
	keyFlows       = "flows"
	// fieldExtra は統合で中身が違うのに既知の欄が全部同じときに挙げる名前。
	fieldExtra = "extra"
)

// fields はテンプレートの既知の欄（書く順）。
var fields = []string{keyName, keyDescription, keyWhen, keyPlan, keyFeedback, keyFlows}

// ステップの欄。
const (
	itemType     = "type"
	itemReview   = "review"
	itemDeferred = "deferred"
	itemAfter    = "after"
)

var itemFields = []string{itemType, itemReview, itemDeferred, itemAfter}

const (
	// Suffix はテンプレートのファイルの拡張子（フローと同じ）。
	Suffix = flow.Suffix
	// LayerDraft は下書きを読んだ Entry.Layer に入れる印。
	LayerDraft = "draft"
	// TodoMark は機械が下書きの description に入れる、一般化が済んでいない印。残っていれば検査が warn で言う。
	TodoMark = "TODO"
)

// Item は計画の 1 ステップ。After はその計画の中の添字（1 始まり、昇順で重複なし）。
type Item struct {
	Type     string
	Review   ticket.Need
	Deferred bool
	After    []int
}

// Plain は型の名前だけで書けるか（`- design`）。
func (i Item) Plain() bool { return !i.Review.Said() && !i.Deferred && len(i.After) == 0 }

// asData は書き戻せる形。offset は After に足す数（提案の絶対番号に直すとき）。
func (i Item) asData(offset int) any {
	if i.Plain() {
		return i.Type
	}
	out := &yaml.Map{}
	out.Put(itemType, i.Type)
	if i.Review.Said() {
		out.Put(itemReview, i.Review == ticket.Needed)
	}
	if i.Deferred {
		out.Put(itemDeferred, true)
	}
	if len(i.After) > 0 {
		after := make([]any, len(i.After))
		for k, n := range i.After {
			after[k] = n + offset
		}
		out.Put(itemAfter, after)
	}
	return out
}

// Equal は 4 つの欄が同じか。After は並びも見る。
func (i Item) Equal(o Item) bool {
	return i.Type == o.Type && i.Review == o.Review && i.Deferred == o.Deferred && slices.Equal(i.After, o.After)
}

// Template は 1 つの親の作業・レビュー計画と、子のフローの本文。Flows のキーは全体計画のステップの添字
// （1 始まり）、値はフローのファイルと同じ形のマップ。extra は未知の欄で、書き戻しで落とさない。
type Template struct {
	Name        string
	Description string
	When        string
	Plan        []Item
	Feedback    []Item
	Flows       map[int]*yaml.Map
	extra       *yaml.Map
}

// flowIndexes は Flows の添字（昇順）。
func (t *Template) flowIndexes() []int {
	out := make([]int, 0, len(t.Flows))
	for i := range t.Flows {
		out = append(out, i)
	}
	slices.Sort(out)
	return out
}

func items(list []Item, offset int) []any {
	out := make([]any, len(list))
	for k, item := range list {
		out[k] = item.asData(offset)
	}
	return out
}

func (t *Template) flowsData() *yaml.Map {
	out := &yaml.Map{}
	for _, i := range t.flowIndexes() {
		out.Put(i, t.Flows[i])
	}
	return out
}

// asData は書き戻せる形。読み直すと同じ Template になる。
func (t *Template) asData() *yaml.Map {
	out := &yaml.Map{}
	out.Put(keyName, t.Name)
	out.Put(keyDescription, t.Description)
	if t.When != "" {
		out.Put(keyWhen, t.When)
	}
	out.Put(keyPlan, items(t.Plan, 0))
	if len(t.Feedback) > 0 {
		out.Put(keyFeedback, items(t.Feedback, 0))
	}
	if len(t.Flows) > 0 {
		out.Put(keyFlows, t.flowsData())
	}
	for k, v := range t.extra.All() {
		out.Put(k, v)
	}
	return out
}

// Dump はテンプレートを YAML の文面にする。読み直すと同じ Template になる。
func Dump(t *Template) string {
	text, _ := toYAML(t.asData())
	return text
}

// toYAML はテンプレート・フロー・合成した提案を YAML の文面にする 1 か所。日本語はそのまま、キーの順は渡した
// まま、行は折らない。改行を含む文字列は literal block で書く（書けない文字列は PyYAML の選ぶ引用符）。
func toYAML(data any) (string, error) {
	return yaml.Dump(data, yaml.DumpOptions{Width: noFold, Literal: literalOK, NoAliases: true})
}

// noFold は行を折らない幅。YAML の出力が長い行を勝手に折らないよう、実質無限の値にする。
const noFold = 1_000_000

// dumpFlow はフローの本文を、フローのファイルの文面にする。(文面, 書けない理由)。子のフローの下書きに書く文面と、
// 検査が読み直す文面は同じ。
func dumpFlow(body *yaml.Map) (string, string) {
	text, err := toYAML(body)
	if err != nil {
		return "", fmt.Sprintf("YAML に変換できません (%s)", line(err.Error()))
	}
	return text, ""
}

// literalOK は literal block（|）で中身を変えずに書ける文字列か。行末の空白、先頭の空白・改行、末尾の空行、
// タブ・制御文字・書式文字を含むものは書けない。
func literalOK(text string) bool {
	if !strings.Contains(text, "\n") || text[0] == ' ' || text[0] == '\n' || strings.HasSuffix(text, "\n\n") {
		return false
	}
	for _, r := range text {
		if r != '\n' && hiddenCategory(r) {
			return false
		}
	}
	for l := range strings.SplitSeq(text, "\n") {
		if strings.HasSuffix(l, " ") {
			return false
		}
	}
	return true
}

// hiddenCategory は Unicode の区分が Cc・Cf・Zl・Zp か。
func hiddenCategory(r rune) bool {
	return unicode.In(r, unicode.Cc, unicode.Cf, unicode.Zl, unicode.Zp)
}
