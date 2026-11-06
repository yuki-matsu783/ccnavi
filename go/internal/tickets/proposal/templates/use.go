package templates

import (
	"fmt"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/ids"
)

// 使う側（ccnavi ticket new <識別子> --template <名前>）。テンプレートから親の提案の計画と子のフローの下書きの
// 中身を組む。書くのは scaffold。

// FirstSeq は子の連番。親の提案が承認される前の足場では、子はそのフェーズの最初の子 <親>-<NN>-01 と決める
// （予告にすぎない。別の連番で起こすなら下書きの名前を合わせる）。
const FirstSeq = 1

// Found は引けたテンプレート。Warns は使えるが言っておく苦情、Notes は統合結果の知らせ。
type Found struct {
	Entry    *Entry
	Template *Template
	Layer    string
	Warns    []string
	Notes    []string
}

// Resolve は layer を足した結果からテンプレートを名前で引く。使えなければ (nil, 理由)。使えるのは、統合結果で
// 取り込まれ、読めて、error の苦情が無いもの。同名で中身が違うと共通の側が引かれ、そのことは Notes で言う。
func Resolve(conf *config.Settings, root, layer, name string) (*Found, string) {
	s := Load(conf, root)
	if s.Layer(layer) == nil {
		var names []string
		for _, l := range s.Layers {
			names = append(names, l.Name)
		}
		known := strings.Join(names, " / ")
		if known == "" {
			known = "無い"
		}
		return nil, fmt.Sprintf("レイヤー %s はありません（%s）", pyfmt.Repr(layer), known)
	}
	e := s.Find(layer, name)
	if e == nil {
		var usable []string
		for _, other := range s.Adopted(layer) {
			if other.Usable() {
				usable = append(usable, other.Name)
			}
		}
		slices.Sort(usable)
		listed := strings.Join(usable, ", ")
		if listed == "" {
			listed = "無い"
		}
		return nil, fmt.Sprintf("テンプレート %s がありません（%s で利用できるもの: %s）。一覧は '%s --templates'",
			pyfmt.Repr(name), layer, listed, config.Launcher)
	}
	if e.Effective && e.Layer == config.LayerCommon {
		e = Recheck(e, TypesFor(conf, root, layer), &s.Catalog)
	}
	if !e.Effective || e.Template == nil {
		why := e.DroppedDetail
		if why == "" {
			why = "読めない"
		}
		return nil, fmt.Sprintf("テンプレート %s（%s）は利用できない: %s", pyfmt.Repr(name), e.Rel, why)
	}
	errs := details(e.Problems(), rules.Error)
	if len(errs) > 0 {
		return nil, fmt.Sprintf("テンプレート %s（%s）に error がある。直してから使う: %s",
			pyfmt.Repr(name), e.Rel, strings.Join(errs, " / "))
	}
	var notes []string
	for _, l := range s.Layers {
		for _, other := range l.Entries {
			if other.Name == name && other.Path != e.Path && other.Dropped != DropNone && other.DroppedDetail != "" {
				notes = append(notes, other.DroppedDetail)
			}
		}
	}
	return &Found{Entry: e, Template: e.Template, Layer: layer, Warns: details(e.Problems(), rules.Warn), Notes: notes}, ""
}

func details(list []rules.Problem, sev rules.Severity) []string {
	var out []string
	for _, p := range list {
		if p.Severity == sev {
			out = append(out, p.Detail)
		}
	}
	return out
}

// Absolute は提案に書くステップ。after を提案の絶対番号（フィードバック計画は全体計画の続き）に付け直す。
func Absolute(t *Template) (plan, feedback []Item) {
	shifted := func(list []Item, offset int) []Item {
		out := make([]Item, len(list))
		for i, item := range list {
			after := make([]int, len(item.After))
			for k, n := range item.After {
				after[k] = n + offset
			}
			out[i] = Item{Type: item.Type, Review: item.Review, Deferred: item.Deferred, After: after}
		}
		return out
	}
	return shifted(t.Plan, 0), shifted(t.Feedback, len(t.Plan))
}

// ChildID は全体計画の index 番目のステップの最初の子の識別子。
func ChildID(parent string, index int) string { return ids.ChildID(parent, index, FirstSeq) }

// ChildProblem は子の識別子が組めない理由（親の識別子が長すぎるなど）。組めれば空。
func ChildProblem(parent string, t *Template) string {
	for _, index := range t.FlowIndexes() {
		child := ChildID(parent, index)
		if why := ids.IDProblem(child); why != "" {
			return "子の識別子 " + child + " が指定できない（" + why + "）。短い識別子にする"
		}
	}
	return ""
}

// Draft は子のフローの下書き 1 本。Index は全体計画のステップの添字。
type Draft struct {
	Child string
	Index int
	Text  string
}

// FlowDrafts は子のフローの下書き（添字順）と、組めない理由。
func FlowDrafts(parent string, t *Template) ([]Draft, string) {
	var out []Draft
	for _, index := range t.FlowIndexes() {
		text, why := DumpFlow(t.Flows[index])
		if why != "" {
			return nil, fmt.Sprintf("`flows[%d]`: %s", index, why)
		}
		out = append(out, Draft{ChildID(parent, index), index, text})
	}
	return out, ""
}
