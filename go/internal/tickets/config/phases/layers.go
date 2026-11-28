package phases

import (
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/layer"
)

// 使う定義は、共通設定の phases.yml と、親の project: が指す設定（空ならワークスペース自身）の和。
// どちらが無くても正常（無い = 空）で、両方無いときだけ定義が無い。足すだけで、設定が共通設定の
// 定義を上書きすることはない。

// typesPath はそのプロジェクトの設定の phases.yml（layer.File）。
func typesPath(conf *config.Settings, root, project string) string {
	return layer.File(conf, root, project, config.KindPhases)
}

// CommonTypes は共通設定の定義と、その苦情。ファイルが無いか壊れていれば nil。単体 clone では共通
// 設定は同期で配られたミラーになる。苦情を言う場所は --lint の共通設定の項。
func CommonTypes(conf *config.Settings) (*Types, []rules.Problem) {
	if conf.Phases == "" || !ospath.DiskExists(conf.Phases) {
		return nil, nil
	}
	types, notes := Load(conf.Phases)
	types.markSource(config.LayerCommon)
	return types, notes
}

// merge は共通設定の定義に、行き先の設定の定義を id ごとに足す。同じ id で全欄が一致すれば重複として
// 設定の側を捨て（info）、中身が違えば error。表示名の重なりも設定をまたいで error。捨てるのはいつも
// 設定の側（共通設定の定義は全プロジェクトで同じに読まれるべきなので、1 つの設定の書き方で中身が
// 変わらないようにする）。
func merge(common, extra *Types, layer string) (*Types, []rules.Problem) {
	merged := common.clone()
	var problems []rules.Problem
	// 共通設定の定義が正。設定の定義は、同じ id（中身が同じなら捨て、違えばエラー）、表示名の重なり（エラー）を除いたものだけ足す。
	titles := map[string]string{}
	for p := range common.All() {
		titles[p.Title] = p.ID
	}
	for p := range extra.All() {
		p.Source = layer
		if prior := common.Get(p.ID); prior != nil {
			if Same(prior, p) {
				problems = append(problems, problem(rules.Info, p.ID, "`"+p.ID+"` は共通設定と全欄が同じなので、"+
					layer+" の側を破棄した。共通設定の 1 本を使う"))
			} else {
				problems = append(problems, problem(rules.Error, p.ID, "`"+p.ID+"` は共通設定と同じ id で中身が違う"+
					"（違う欄: "+strings.Join(Differing(prior, p), ", ")+"）。"+
					layer+" の `"+p.ID+"` は使わず、共通設定の定義を使う。"+
					"中身を変えたいなら "+layer+" の側に別の id を付ける"))
			}
			continue
		}
		if prior, ok := titles[p.Title]; ok {
			problems = append(problems, problem(rules.Error, p.ID, "表示名 `"+p.Title+"` が共通設定の `"+prior+
				"` と重なる。"+layer+" の `"+p.ID+"` は使わない。別の表示名を付ける"))
			continue
		}
		titles[p.Title] = p.ID
		merged.put(p)
	}
	return merged, problems
}

// LayerTypes は使うフェーズ定義（共通設定 + 親の project: が指す設定）と、その設定の苦情。
// ワークスペースの設定とプロジェクトの設定どうしは足さない。両方無ければ nil。壊れたファイルは空として扱う。
// 共通設定の苦情は返さない（言う場所は --lint の共通設定の項で、二重に言わない）。
func LayerTypes(conf *config.Settings, root, project string) (*Types, []rules.Problem) {
	common, _ := CommonTypes(conf)
	path := typesPath(conf, root, project)
	if path == "" || !ospath.DiskExists(path) {
		return common, nil
	}
	name := layer.Name(project)
	extra, notes := Load(path)
	extra.markSource(name)
	if extra == nil {
		return common, notes
	}
	if common == nil {
		return extra, notes
	}
	merged, problems := merge(common, extra, name)
	return merged, append(notes, problems...)
}

// LoadTypes は判定が使うフェーズ定義。どちらの設定にも無ければ nil（番号だけの挙動）。
func LoadTypes(conf *config.Settings, root, project string) *Types {
	types, _ := LayerTypes(conf, root, project)
	return types
}
