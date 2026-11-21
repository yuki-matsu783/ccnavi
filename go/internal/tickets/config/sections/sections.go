package sections

import (
	"errors"
	"fmt"
	"io/fs"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/stopreview"
	"ccnavi/internal/tickets/config/layer"
	"ccnavi/internal/tickets/model/form"
)

// Version は ticket-sections.yml の版。
const Version = 1

// 種類のキー（ファイルの最上位）。
const (
	keyParent = "parent"
	keyChild  = "child"
)

// File は 1 つのファイルに書かれた並び。キーが無い種類は lists に入らない（空の並びとは別。空の並びは
// その種類の見出しを見ないと決めたもの）。
type File struct {
	lists map[string][]form.Heading
}

func problem(sev rules.Severity, rule, detail string) rules.Problem {
	return rules.Problem{Severity: sev, Rule: rule, Detail: detail}
}

// Load はファイルを読む。無ければ nil で苦情も無し。壊れていれば nil と苦情。
func Load(path string) (*File, []rules.Problem) {
	text, err := fsio.ReadDiskText(path, fsio.Strict)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return nil, nil
		}
		return nil, []rules.Problem{problem(rules.Error, path, path+" を読めない ("+err.Error()+")")}
	}
	return Parse(text, path)
}

// Parse は ticket-sections.yml の本文を読む。where は苦情の出どころ。error が 1 つでもあれば nil（一部だけを使うと、
// 書き手の意図しない見出しの組で判定することになる）。
func Parse(text, where string) (*File, []rules.Problem) {
	data, err := yaml.Load(text)
	if err != nil {
		return nil, []rules.Problem{problem(rules.Error, where, "YAML として読めない: "+err.Error())}
	}
	top, ok := data.(*yaml.Map)
	if !ok {
		return nil, []rules.Problem{problem(rules.Error, where, "最上位がマップではない")}
	}
	if v := top.Get("version"); !pyfmt.EqualsInt(v, Version) {
		return nil, []rules.Problem{problem(rules.Error, where,
			fmt.Sprintf("版 %s は扱えない（このビルドが読むのは %d）", pyfmt.Repr(v), Version))}
	}
	var problems []rules.Problem
	f := &File{lists: map[string][]form.Heading{}}
	for key := range top.All() {
		name := pyfmt.Str(key)
		if name != "version" && name != keyParent && name != keyChild {
			problems = append(problems, problem(rules.Error, where, "知らないキー `"+name+"`（書けるのは version・parent・child）"))
		}
	}
	for _, kind := range []string{keyParent, keyChild} {
		raw, ok := top.Lookup(kind)
		if !ok {
			continue
		}
		list, own := headings(kind, raw)
		problems = append(problems, own...)
		f.lists[kind] = list
	}
	for _, p := range problems {
		if p.Severity == rules.Error {
			return nil, problems
		}
	}
	return f, problems
}

// headings は 1 つの種類の並びを読む。名前は大文字小文字を区別せずに一意にする（本文の見出しも区別せずに探す）。
func headings(kind string, raw any) ([]form.Heading, []rules.Problem) {
	items, ok := raw.([]any)
	if !ok {
		return nil, []rules.Problem{problem(rules.Error, kind, "`"+kind+"` が並び（リスト）ではない")}
	}
	var out []form.Heading
	var problems []rules.Problem
	seen := map[string]bool{}
	for i, item := range items {
		at := fmt.Sprintf("%s[%d]", kind, i)
		fields, ok := item.(*yaml.Map)
		if !ok {
			problems = append(problems, problem(rules.Error, at, "項がマップではない（`- name: <見出し>` の形で書く）"))
			continue
		}
		for key := range fields.All() {
			if k := pyfmt.Str(key); k != "name" && k != "todo" {
				problems = append(problems, problem(rules.Error, at, "知らないキー `"+k+"`（書けるのは name・todo）"))
			}
		}
		name, ok := fields.Get("name").(string)
		name = ospath.Strip(name)
		if !ok || name == "" {
			problems = append(problems, problem(rules.Error, at, "`name` が無いか空か、文字列ではない"))
			continue
		}
		todo := ""
		if v, has := fields.Lookup("todo"); has {
			text, isText := v.(string)
			if !isText {
				problems = append(problems, problem(rules.Error, at, "`todo` が文字列ではない"))
				continue
			}
			todo = ospath.Strip(text)
		}
		key := pyfmt.Casefold(name)
		if seen[key] {
			problems = append(problems, problem(rules.Error, at, "見出し `"+name+"` が重なる（大文字小文字は区別しない）"))
			continue
		}
		seen[key] = true
		out = append(out, form.Heading{Name: name, Todo: todo})
	}
	return out, problems
}

// over は base に f の並びを重ねる。f にキーがある種類だけ置き換える。
func (f *File) over(base form.Sections) form.Sections {
	if f == nil {
		return base
	}
	if list, ok := f.lists[keyParent]; ok {
		base.Parent = list
	}
	if list, ok := f.lists[keyChild]; ok {
		base.Child = list
	}
	return base
}

// CommonFile は共通レイヤーの ticket-sections.yml と、その苦情。無いか壊れていれば nil。
func CommonFile(conf *config.Settings) (*File, []rules.Problem) {
	if conf.Sections == "" {
		return nil, nil
	}
	return Load(conf.Sections)
}

// Common は共通レイヤーの見出し。Stop の判定の基準が使う（判定はプロジェクトを選ばない）。
func Common(conf *config.Settings) form.Sections {
	f, _ := CommonFile(conf)
	return f.over(form.Sections{})
}

// Layer は使う見出し（共通レイヤーに、親の project: が指すレイヤーを重ねたもの）と、そのレイヤーの苦情。
// レイヤーのファイルが壊れていれば共通レイヤーだけを使う。共通レイヤーの苦情は返さない（言う場所は --lint の
// 共通レイヤーの項で、二重に言わない）。
func Layer(conf *config.Settings, root, project string) (form.Sections, []rules.Problem) {
	out := Common(conf)
	path := layer.File(conf, root, project, config.KindSections)
	if path == "" {
		return out, nil
	}
	f, notes := Load(path)
	return f.over(out), notes
}

// For は判定が使う見出し。
func For(conf *config.Settings, root, project string) form.Sections {
	out, _ := Layer(conf, root, project)
	return out
}

// StopCriteria は Stop の AI 判定の基準。R2 に共通レイヤーの見出しを入れる。計画を作る hook と、材料・記録の
// 入口が同じ基準（同じ材料の版）になるよう、組み立てはここ 1 か所にする。
func StopCriteria(conf *config.Settings, root string) string {
	s := Common(conf)
	return stopreview.LoadCriteria(root, form.Names(s.Parent), form.Names(s.Child))
}
