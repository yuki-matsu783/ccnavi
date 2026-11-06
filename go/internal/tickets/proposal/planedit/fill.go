package planedit

import (
	"cmp"
	"maps"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/candidates"
)

// Filled はコピーした結果。Why が空でなければ書いていない（その理由の文）。
type Filled struct {
	Why string
	// Changed は書き換えたか（変わらなければ偽）。
	Changed bool
	Copied  []string
	// Dropped はコピーから外した、計画が使わない定義。
	Dropped []string
	// Held は承認済みチケットのコピーから差し込んだ定義（子が承認された番号が使うもの）。
	Held []string
}

// Fill は target（承認待ちの親の提案）の phases: を、計画が使う定義（phases.yml の共通レイヤーと親の project: の
// レイヤーの和）に差し替えて書く（無ければ plan: の前に足す）。current は改版ならその親の承認済みチケットで、
// 子が承認された番号が使う定義はそのコピーから差し込む（改版の承認が同じ規則で確かめる）。
func Fill(conf *config.Settings, root string, target, current *ticket.Ticket) Filled {
	name := target.ID
	// コピーする定義を決める計画と書き換える本文は、同じ 1 回の読みから取る（間に提案が書き換わって、
	// 前の計画に合わせた phases: が新しい本文に入らないように）。
	raw, ok := fsio.ReadBytes(target.Path)
	if !ok {
		return Filled{Why: target.Path + " を読めない。何も反映しません。"}
	}
	text, ok := decode(raw)
	if !ok {
		return Filled{Why: target.Path + " を UTF-8 として読めない。何も反映しません。"}
	}
	read, _ := ticket.Parse(text)
	if read == nil || read.ID != name || read.IsChild() {
		return Filled{Why: target.Path + " を親の提案として読めない。何も反映しません。"}
	}
	read.Project = target.Project
	if !read.HasPlan() {
		return Filled{Why: name + " は計画（plan:）を持たないので、差し込む定義がありません。"}
	}
	cfg := phases.LoadTypes(conf, root, target.Project)
	if cfg == nil {
		return Filled{Why: "フェーズ定義（共通レイヤーと " + cmp.Or(target.Project, "自身") + " のレイヤーの phases.yml）が" +
			"どちらも無いか読めないので、コピーできない。何も反映しません。"}
	}
	held := heldTypes(conf, root, current)
	copied := &yaml.Map{}
	var names, missing []string
	for _, step := range read.Numbered() {
		typ := step.Item.Type
		if slices.Contains(names, typ) || slices.Contains(missing, typ) {
			continue
		}
		pt := cmp.Or(held[typ], cfg.Get(typ))
		if pt == nil {
			missing = append(missing, typ)
			continue
		}
		names = append(names, typ)
		copied.Put(typ, pt.AsCopy())
	}
	if len(missing) > 0 {
		return Filled{Why: "計画のステップが使う定義 " + strings.Join(missing, ", ") + " が phases.yml にありません。" +
			"計画を直すか、ユーザに phases.yml へ追加してもらう。何も反映しません。"}
	}
	built, why := withCopy(text, copied)
	if why != "" {
		return Filled{Why: target.Path + ": " + why + "。何も反映しません。"}
	}
	var dropped []string
	if phases.HasCopy(read) {
		for pt := range phases.TypesOf(read).All() {
			if !slices.Contains(names, pt.ID) {
				dropped = append(dropped, pt.ID)
			}
		}
		slices.Sort(dropped)
	}
	done := Filled{Copied: names, Dropped: dropped, Held: slices.Sorted(maps.Keys(held))}
	if built == text {
		return done
	}
	if failed := fsio.WriteFile(target.Path, []byte(built), fsio.Durable); failed != "" {
		return Filled{Why: target.Path + " に保存できませんでした（" + failed + "）"}
	}
	done.Changed = true
	return done
}

// heldTypes は改版なら、子が承認された番号が使う定義（承認済みチケットのコピーの値）。新規なら空。
func heldTypes(conf *config.Settings, root string, current *ticket.Ticket) map[string]*phases.PhaseType {
	held := map[string]*phases.PhaseType{}
	if current == nil {
		return held
	}
	types := phases.TypesOf(current)
	for n := range candidates.FixedNumbers(conf, root, current.ID) {
		if item := current.ItemAt(n); item != nil {
			if pt := types.Get(item.Type); pt != nil {
				held[item.Type] = pt
			}
		}
	}
	return held
}

// withCopy は phases: の値だけを copied に差し替えた全文と、書けない理由（書けるなら空）。
func withCopy(text string, copied *yaml.Map) (string, string) {
	f, why := frontOf(text)
	if why != "" {
		return "", why
	}
	whole := &yaml.Map{}
	whole.Put(ticket.PhasesKey, copied)
	dumped, err := yaml.Dump(whole, yaml.DumpOptions{})
	if err != nil {
		return "", err.Error()
	}
	block := strings.Split(strings.TrimRight(dumped, "\n"), "\n")
	start := slices.IndexFunc(f.lines[1:f.end], func(l string) bool { return keyLine(l, ticket.PhasesKey) })
	var stop int
	if start >= 0 {
		start++
		if stop, why = f.valueEnd(start, ticket.PhasesKey, false, false); why != "" {
			return "", why
		}
	} else {
		start = f.end
		if plan := slices.IndexFunc(f.lines[1:f.end], func(l string) bool { return keyLine(l, string(phases.Plan)) }); plan >= 0 {
			start = plan + 1
		}
		stop = start
	}
	built := f.replaced(start, stop, block)
	if why := copyChangedElsewhere(text, built, copied); why != "" {
		return "", why
	}
	return built, ""
}

// copyChangedElsewhere は組んだ全文を読み直し、phases のほかの欄と本文が元と同じで、差し込んだ定義と合うか。
func copyChangedElsewhere(old, new string, copied *yaml.Map) string {
	after, why := changedElsewhere(old, new, []string{ticket.PhasesKey}, "`"+ticket.PhasesKey+":` の値")
	if after == nil {
		return why
	}
	const mismatch = "組んだ全文の phases: が差し込んだ定義と合わないので反映しません"
	written, problems := phases.ReadCopy(after.Get(ticket.PhasesKey))
	wanted, _ := phases.ReadCopy(copied)
	if slices.ContainsFunc(problems, func(p rules.Problem) bool { return p.Severity == rules.Error }) || written.Len() != wanted.Len() {
		return mismatch
	}
	for pt := range wanted.All() {
		if w := written.Get(pt.ID); w == nil || !phases.Same(w, pt) {
			return mismatch
		}
	}
	if read, _ := ticket.Parse(new); read == nil {
		return "組んだ全文がチケットとして読めないので反映しません"
	}
	return ""
}
