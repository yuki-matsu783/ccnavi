package diagnose

import (
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/layer"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/config/risk"
	"ccnavi/internal/tickets/model/ticket"
)

// unreadableFallback は読めなかった理由が空のときに言う語。
const unreadableFallback = "読めない"

// inheritScope は範囲が親の範囲そのまま（定義の scope が無い・空）であることを言う 1 語。
const inheritScope = "inherit"

// layerConfig はそのレイヤーの phases か risk のパス。共通レイヤーは設定が持つ既定そのもの。
func layerConfig(conf *config.Settings, root, name string, kind config.LayerKind) string {
	if name == config.LayerCommon {
		if kind == config.KindPhases {
			return conf.Phases
		}
		return conf.Risk
	}
	return layer.File(conf, root, layer.Project(name), kind)
}

// written は範囲の 1 件の書かれた表記。
func written(e ticket.Entry) string {
	if e.Glob != "" {
		return e.Glob
	}
	return e.Regex
}

// scopeWords は定義の範囲を書かれた表記で並べる。継ぐ（範囲が無い）なら inheritScope の 1 語。
func scopeWords(pt *phases.PhaseType) []string {
	if len(pt.Scope) == 0 {
		return []string{inheritScope}
	}
	out := make([]string, len(pt.Scope))
	for i, e := range pt.Scope {
		out[i] = written(e)
	}
	return out
}

func problemsText(notes []rules.Problem) string {
	parts := make([]string, len(notes))
	for i, n := range notes {
		parts[i] = n.String()
	}
	if text := strings.Join(parts, "; "); text != "" {
		return text
	}
	return unreadableFallback
}

// layerPhaseSet はそのレイヤーのファイルに書かれたフェーズ定義と、読めなかった理由。無いレイヤーは nil。
// 判定が使う和（共通 + 親の project: のレイヤー）は phases.LayerTypes が組む。
func layerPhaseSet(path string) (*phases.Types, string) {
	if path == "" || !ospath.IsFile(path) {
		return nil, ""
	}
	types, notes := phases.Load(path)
	if types == nil {
		return nil, problemsText(notes)
	}
	return types, ""
}

// layerPhaseTypes は layerPhaseSet を書いた順の並びにしたもの。
func layerPhaseTypes(path string) ([]*phases.PhaseType, string) {
	types, why := layerPhaseSet(path)
	var out []*phases.PhaseType
	for pt := range types.All() {
		out = append(out, pt)
	}
	return out, why
}

// layerRisk はそのレイヤーのリスクの項目と、読めなかった理由。script: に書ける場所がレイヤーごとに違うので、
// 読み方もレイヤーで分ける。
func layerRisk(conf *config.Settings, name, path string) ([]*risk.Factor, string) {
	if path == "" || !ospath.IsFile(path) {
		return nil, ""
	}
	if name == config.LayerCommon {
		d, _ := risk.Load(path)
		if d.Fallback != "" {
			return nil, d.Fallback
		}
		return d.Factors, ""
	}
	d, notes := risk.LoadLayer(path, []string{conf.LayerScriptHome()})
	if d == nil {
		return nil, problemsText(notes)
	}
	return d.Factors, ""
}

// factorValue は項目の加点条件の値（数える項目は閾値、ほかは文）。
func factorValue(f *risk.Factor) any {
	switch f.Kind {
	case risk.KindLines, risk.KindFiles, risk.KindDeleted:
		return f.Limit
	}
	return f.Text
}

// form はルールの書き方。書いた表記と翻訳後の式。when は書いた形も持つ（翻訳後の式が 1 本に定まらない）。
type form struct {
	kind, written, pattern string
	when                   *pyjson.Object
}

// ruleForm はルールの書き方。when の written は組み合わせ条件を 1 行にした形だけにする。kind が when を言うので、
// WrittenBody の頭の `when: ` を残すと、並べて出す画面で「when when: …」と二重になる。
func ruleForm(r *rules.Rule) form {
	pattern := ""
	if r.Compiled != nil {
		pattern = r.Compiled.Pattern()
	}
	if r.When != nil {
		return form{kind: rules.WhenKey, written: r.When.Describe(), pattern: pattern, when: r.When.Written()}
	}
	if r.Glob != "" {
		return form{kind: string(rules.WhenGlob), written: r.Glob, pattern: pattern}
	}
	return form{kind: string(rules.WhenRegex), written: r.Regex, pattern: pattern}
}

// put は書き方の欄を o の後ろに足す。
func (f form) put(o *pyjson.Object) *pyjson.Object {
	o.Set("kind", f.kind)
	o.Set("written", f.written)
	o.Set("pattern", f.pattern)
	if f.when != nil {
		o.Set("when", f.when)
	}
	return o
}
