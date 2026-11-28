package samples

import (
	"fmt"
	"strings"

	"ccnavi/internal/infra/yaml"
)

// 親チケットの見本に phases:（計画が使うフェーズ定義のコピー）を差し込む道具。
//
// 計画を持つ親は、計画が使う定義だけを phases.yml からコピーした phases: を持つ。コピーするのは計画（plan: / feedback:）の
// ステップが使う定義だけで、順序の欄（order・after・overlap・requires）はコピーしない。実行ファイルの
// `--plan-order <親> --fill-phases` と同じことをテストの側で行う。実行ファイルの関数を呼ばないのは、
// 見本が実装の書き方に引きずられないようにするため。

// Definition はフェーズ定義 1 つ（id と欄）。
type Definition struct {
	ID   string
	Body *yaml.Map
}

// Definitions は phases.yml の本文から、定義を書いてある順に返す。読めない本文は空。
func Definitions(phasesYML string) []Definition {
	data, err := yaml.Load(phasesYML)
	if err != nil {
		return nil
	}
	top, ok := data.(*yaml.Map)
	if !ok {
		return nil
	}
	phases, ok := top.Get("phases").(*yaml.Map)
	if !ok {
		return nil
	}
	var out []Definition
	for k, v := range phases.All() {
		if body, ok := v.(*yaml.Map); ok {
			out = append(out, Definition{ID: textOf(k), Body: body})
		}
	}
	return out
}

func textOf(k any) string { return fmt.Sprint(k) }

// CombinedPhases は共通設定とプロジェクトの設定の phases.yml を足した本文。同じ id は前のものを残す。
// 判定は共通設定 + 親の project: が指す設定の和を読むので、コピーもその和から取る。定義が 1 つも無ければ空。
func CombinedPhases(phasesYMLs ...string) string {
	merged := &yaml.Map{}
	for _, text := range phasesYMLs {
		for _, d := range Definitions(text) {
			if !merged.Has(d.ID) {
				merged.Put(d.ID, d.Body)
			}
		}
	}
	if merged.Len() == 0 {
		return ""
	}
	top := &yaml.Map{}
	top.Put("version", 1)
	top.Put("phases", merged)
	return dump(top)
}

func dump(v any) string {
	out, err := yaml.Dump(v, yaml.DumpOptions{})
	if err != nil {
		panic("samples: " + err.Error())
	}
	return out
}

// UsedTypes は frontmatter の計画のステップが使う定義の名前（出てきた順、重なりなし）。
func UsedTypes(front *yaml.Map) []string {
	var names []string
	for _, key := range []string{"plan", "feedback"} {
		items, _ := front.Get(key).([]any)
		for _, item := range items {
			var v any = item
			if m, ok := item.(*yaml.Map); ok {
				v = m.Get("type")
			}
			name, ok := v.(string)
			name = strings.TrimSpace(name)
			if !ok || name == "" {
				continue
			}
			dup := false
			for _, have := range names {
				dup = dup || have == name
			}
			if !dup {
				names = append(names, name)
			}
		}
	}
	return names
}

// PhasesBlock は phases: の行（末尾の改行つき）。phases.yml に無い名前はコピーしない。
func PhasesBlock(phasesYML string, names []string) string {
	defs := Definitions(phasesYML)
	picked := &yaml.Map{}
	for _, name := range names {
		for _, d := range defs {
			if d.ID == name {
				picked.Put(name, d.Body)
			}
		}
	}
	top := &yaml.Map{}
	top.Put("phases", picked)
	return dump(top)
}

// InsertPhases は親チケットの本文の plan: の行の前に、計画が使う定義の phases: を差し込む。
// plan: が無い（子や計画の無い親）、もう phases: がある本文は、そのまま返す。
func InsertPhases(text, phasesYML string) string {
	lines := strings.Split(text, "\n")
	if strings.TrimSpace(lines[0]) != "---" {
		return text
	}
	end := -1
	for i := 1; i < len(lines); i++ {
		if strings.TrimSpace(lines[i]) == "---" {
			end = i
			break
		}
	}
	if end < 0 {
		return text
	}
	data, err := yaml.Load(strings.Join(lines[1:end], "\n"))
	front, ok := data.(*yaml.Map)
	if err != nil || !ok || !front.Has("plan") || front.Has("phases") {
		return text
	}
	at := -1
	for i := 1; i < end; i++ {
		if strings.HasPrefix(lines[i], "plan:") {
			at = i
			break
		}
	}
	if at < 0 {
		return text
	}
	added := strings.Split(strings.TrimRight(PhasesBlock(phasesYML, UsedTypes(front)), "\n"), "\n")
	out := append([]string{}, lines[:at]...)
	out = append(out, added...)
	out = append(out, lines[at:]...)
	return strings.Join(out, "\n")
}
