package mirror

import (
	"path/filepath"
	"slices"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/config/risk"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/proposal/templates"
)

// 共通レイヤーからミラーへ写すもの: 設定 3 本と期待する判定、配点の script: が指す共通のスクリプト、
// error の無いテンプレート。ミラーとして（単体 clone では共通レイヤーとして）読めない設定があれば何も写さない。

// expectationsName は共通レイヤーの期待する判定のファイル名。
const expectationsName = "rule-expectations.yml"

// 苦情を入れる where（Python の parse の第 2 引数）。
const (
	phasesWhere = "(phases)"
	riskWhere   = "(risk)"
)

// commonSources は共通レイヤーのうち写すもの（ミラーの置き場からの相対 → 中身）。写せなければ理由。
func commonSources(conf *config.Settings, root string) (map[string][]byte, string) {
	base := conf.CommonLayerDir(root)
	out := map[string][]byte{}
	riskName := config.LayerFileNames[config.KindRisk]
	names := make([]string, 0, len(config.LayerKinds)+1)
	for _, kind := range config.LayerKinds {
		names = append(names, config.LayerFileNames[kind])
	}
	for _, name := range append(names, expectationsName) {
		raw, existed, why := readStrict(ospath.Join(base, name))
		if why != "" {
			return nil, "共通レイヤーの " + name + " を読めない (" + why + ")"
		}
		if existed {
			out[name] = raw
		}
	}
	for _, kind := range config.LayerKinds {
		name := config.LayerFileNames[kind]
		if raw, ok := out[name]; ok {
			if why := unreadableAsCommon(kind, raw, root); why != "" {
				return nil, "共通レイヤーの " + name + " を、ミラーとして読めない: " + why
			}
		}
	}
	prefix := scriptPrefix()
	for _, rel := range scriptValues(out[riskName]) {
		raw, existed, why := readStrict(ospath.Join(root, filepath.FromSlash(rel)))
		if why != "" || !existed {
			if why == "" {
				why = missing
			}
			return nil, "共通レイヤーの配点が指すスクリプト " + rel + " を読めない (" + why + ")"
		}
		out[strings.TrimPrefix(rel, prefix)] = raw
	}
	for rel, raw := range commonTemplates(conf, root) {
		out[rel] = raw
	}
	return out, ""
}

// commonTemplates は error の無い共通のテンプレート。置き場が無い・読めない（リンクなど）ときは 1 本も写さない
// （dotfiles 管理でリンクにすることがあり、ほかの設定の写しは止めない）。error のある 1 本は飛ばす（--lint が言う）。
func commonTemplates(conf *config.Settings, root string) map[string][]byte {
	read := templates.ReadLayer(config.LayerCommon, root, conf.CommonTemplatesDir(root), false)
	if read.Unreadable != "" {
		return nil
	}
	types := templates.TypesFor(conf, root, config.LayerCommon)
	catalog := flow.LoadCatalog(root)
	out := map[string][]byte{}
	for _, entry := range read.Entries {
		problems := slices.Clone(entry.Notes)
		if entry.Template != nil {
			problems = append(problems, templates.Check(entry.Template, types, &catalog)...)
		}
		if hasError(problems) {
			continue
		}
		out[config.TemplatesDir+"/"+entry.Name+templates.Suffix] = entry.Raw
	}
	return out
}

// scriptPrefix は共通のスクリプトの script: の値のうち、ミラーの置き場（`config/common/` まで）を指す先頭。
// 写した先でも同じ値で指せるよう、ミラーの中の置き場はこれを外した相対にする。
func scriptPrefix() string {
	head, _, _ := strings.Cut(risk.ScriptHomes[0], config.CommonDir+"/")
	return head + config.CommonDir + "/"
}

// scriptValues は共通レイヤーの配点が指す共通のスクリプト（ワークスペースルートからの相対。書かれた順、重なりなし）。
func scriptValues(raw []byte) []string {
	if len(raw) == 0 {
		return nil
	}
	text, _ := fsio.Decode(raw, fsio.Replace)
	definition, _ := risk.Parse(text, riskWhere, risk.ScriptHomes)
	if definition == nil {
		return nil
	}
	var found []string
	for _, f := range definition.Factors {
		if f.Kind == risk.KindScript && strings.HasPrefix(f.Text, risk.ScriptHomes[0]) && !slices.Contains(found, f.Text) {
			found = append(found, f.Text)
		}
	}
	return found
}

// unreadableAsCommon はミラーとして（単体 clone では共通レイヤーとして）読めない理由。読めれば空。
func unreadableAsCommon(kind config.LayerKind, content []byte, root string) string {
	if !utf8.Valid(content) {
		return "UTF-8 として読めない"
	}
	text := string(content)
	switch kind {
	case config.KindPhases:
		types, problems := phases.Parse(text, phasesWhere)
		return errorDetails(problems, types == nil, "フェーズ定義として読めない")
	case config.KindRisk:
		definition, problems := risk.Parse(text, riskWhere, risk.ScriptHomes)
		return errorDetails(problems, definition == nil, "配点として読めない")
	}
	data, err := yaml.Load(text)
	if err != nil {
		return "YAML として読めない"
	}
	top, ok := data.(*yaml.Map)
	if !ok {
		return "マッピングではない"
	}
	_, problems := rules.Parse(top, root)
	return errorDetails(problems, false, "")
}

// errorDetails は error の苦情を "; " でつないだもの。苦情が無く failed なら fallback。
func errorDetails(problems []rules.Problem, failed bool, fallback string) string {
	var details []string
	for _, p := range problems {
		if p.Severity == rules.Error {
			details = append(details, p.Detail)
		}
	}
	if len(details) > 0 {
		return strings.Join(details, "; ")
	}
	if failed {
		return fallback
	}
	return ""
}

func hasError(problems []rules.Problem) bool {
	return slices.ContainsFunc(problems, func(p rules.Problem) bool { return p.Severity == rules.Error })
}

// commonPlan はそのツリーのミラーに加える変更。写せない理由があれば、それを返す（何も写さない）。
func commonPlan(conf *config.Settings, root, treeRoot string) ([]Change, string) {
	wanted, why := commonSources(conf, root)
	if why != "" || len(wanted) == 0 {
		return nil, why
	}
	base := conf.CommonLayerDir(treeRoot)
	if !inside(treeRoot, base) || pathmatch.HasLink(treeRoot, base) {
		return nil, relPlain(treeRoot, base) + linkedPath
	}
	present, why := scan(base, ospath.Dirname(base))
	if why != "" {
		return nil, why
	}
	changes, why := updates(treeRoot, base, wanted)
	if why != "" {
		return nil, why
	}
	gone, why := removals(treeRoot, base, present, func(rel string) bool {
		_, kept := wanted[rel]
		return !kept
	})
	if why != "" {
		return nil, why
	}
	return append(changes, gone...), ""
}

// applyCommon はミラーへ変更を書き、空になったディレクトリをミラーの下から片付ける。
func applyCommon(conf *config.Settings, treeRoot string, changes []Change) string {
	return apply(changes, conf.CommonLayerDir(treeRoot))
}
