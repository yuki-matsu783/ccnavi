package check

import (
	"io"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/policy/rulelint"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/layer"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/config/risk"
)

// 苦情の出どころの表記。VS Code 拡張がこの前置きでプロジェクトを引く。
const (
	commonWhere  = "(rules)"
	selfWhere    = "(self)"
	phasesWhere  = "(phases)"
	riskWhere    = "(risk)"
	projectWhere = "(projects/"
)

// LayerWhere はそのレイヤーの苦情の出どころの表記。
func LayerWhere(name string) string {
	switch name {
	case config.LayerCommon:
		return commonWhere
	case config.LayerSelf:
		return selfWhere
	}
	return projectWhere + name + ")"
}

func problem(sev rules.Severity, rule, detail string) rules.Problem {
	return rules.Problem{Severity: sev, Rule: rule, Detail: detail}
}

// prefixed は苦情の rule の前に出どころを付ける。
func prefixed(where string, p rules.Problem) rules.Problem {
	return problem(p.Severity, strings.TrimRightFunc(where+" "+p.Rule, ospath.IsSpace), p.Detail)
}

// namedRules は共通レイヤーのルールの置き場が、既定の場所ではなく差し替えで名指しされたものか。
func namedRules(conf *config.Settings, root string) bool {
	def := ospath.Join(root, config.DefaultRules)
	return pathcase.Fold(ospath.Abspath(conf.Rules)) != pathcase.Fold(ospath.Abspath(def))
}

// CommonRules は共通レイヤーのルールファイルの苦情。無いのは正常（無い = 空）で、不正なときだけ言う。
// 差し替え（診断の --rules、取り込みの下書き）で名指しされたファイルが無いのは、置き場が空なのとは別なので言う。
func CommonRules(conf *config.Settings, root string) []rules.Problem {
	if ospath.DiskExists(conf.Rules) || namedRules(conf, root) {
		return rulelint.FileProblems(conf.Rules, root, rulelint.Options{})
	}
	return nil
}

// CommonPhases は共通レイヤーの phases.yml が読めるか。無いのは正常で、何も言わない。読めなければ判定は共通
// レイヤーを空として扱い、各レイヤーの定義だけを使う（各レイヤーは LayerConfigs が見る）。
func CommonPhases(conf *config.Settings) []rules.Problem {
	_, notes := phases.CommonTypes(conf)
	var out []rules.Problem
	for _, p := range notes {
		out = append(out, problem(p.Severity, phasesWhere, p.Rule+": "+p.Detail))
	}
	return out
}

// CommonRisk は共通レイヤーの配点が読めるか（無いのは不備ではない。組み込みの配点）。script: が指す先が在る
// ことも見る（走らせるときは重いほうになるが、そこで気づくのは子を閉じる時点になる）。
func CommonRisk(conf *config.Settings, root string) []rules.Problem {
	if conf.Risk == "" {
		return nil
	}
	d, notes := risk.CommonDefinition(conf, root)
	if d.Fallback == "" {
		notes = append(notes, risk.ScriptProblems(d, "")...)
	}
	var out []rules.Problem
	for _, p := range notes {
		out = append(out, problem(p.Severity, riskWhere, p.Rule+": "+p.Detail))
	}
	return out
}

// LayerRules はレイヤーのルールファイルに食い違いが無いか（読めること、レイヤーをまたいだ重複と同名の衝突）。
// only を渡せばそのレイヤーだけ。共通レイヤーは CommonRules が見るので、2 つ目以降だけを回す。
// .ccnavi/config/project/ が無いことは言わない（無いのは正常で、言うと本当に言うべきものが埋もれる）。
func LayerRules(stderr io.Writer, conf *config.Settings, root, only string) []rules.Problem {
	var out []rules.Problem
	views := ruleload.Survey(stderr, conf, root)
	if len(views) > 0 {
		views = views[1:]
	}
	for _, view := range views {
		if only != "" && view.Name != only {
			continue
		}
		where := LayerWhere(view.Name)
		if view.Unreadable != "" {
			out = append(out, problem(rules.Error, where, view.Path+" を読めない ("+view.Unreadable+")。"+
				"このレイヤーは空として扱っている。"+
				"共通レイヤーだけで判定しているので、ここに書いた宣言は 1 件も使われていない"))
			continue
		}
		if view.Missing {
			continue
		}
		fromFile := rulelint.FileProblems(view.Path, root, rulelint.Options{
			Home:    layer.Home(conf, root, layer.Project(view.Name)),
			Layer:   true,
			Project: view.Name != config.LayerSelf,
		})
		told := map[rules.Problem]bool{}
		for _, p := range fromFile {
			out = append(out, prefixed(where, p))
			told[problem(p.Severity, p.Rule, p.Detail)] = true
		}
		// Survey もレイヤーのファイルの苦情を持つ。FileProblems が同じファイルを読んで言ったものは数えない
		// （同じ苦情が 2 度並び、件数も水増しされる）。
		for _, p := range view.Problems {
			if !told[problem(p.Severity, p.Rule, p.Detail)] {
				out = append(out, prefixed(where, p))
			}
		}
	}
	return out
}

// LayerConfigs は各レイヤーの phases・risk が共通レイヤーと合成できるか。only を渡せばそのレイヤーだけ、kind
// （KindPhases か KindRisk）を渡せばその種類だけ。見るのは合成したあとの内容で、共通レイヤー自身の苦情は
// CommonPhases・CommonRisk が言うので、レイヤーの側だけを数える。
func LayerConfigs(conf *config.Settings, root, only string, kind config.LayerKind) []rules.Problem {
	var out []rules.Problem
	for _, name := range layer.Names(conf) {
		if only != "" && name != only {
			continue
		}
		where := LayerWhere(name)
		project := layer.Project(name)
		if kind == "" || kind == config.KindPhases {
			_, notes := phases.LayerTypes(conf, root, project)
			for _, p := range notes {
				out = append(out, prefixed(where+" "+phasesWhere, p))
			}
		}
		if kind == "" || kind == config.KindRisk {
			d, notes := risk.LayerDefinition(conf, root, project)
			for _, p := range append(notes, risk.ScriptProblems(d, name)...) {
				out = append(out, prefixed(where+" "+riskWhere, p))
			}
		}
	}
	return out
}

// FileProblems は 1 つのレイヤー（common・self・プロジェクトの名前）の 1 つの種類の設定ファイルを、--lint と
// 同じ水準で検査した苦情。読むファイルは conf が指す先（取り込みの検査では下書きへ差し替えてある）。
func FileProblems(conf *config.Settings, root string, kind config.LayerKind, name string) []rules.Problem {
	if name == config.LayerCommon {
		switch kind {
		case config.KindRules:
			return CommonRules(conf, root)
		case config.KindPhases:
			return CommonPhases(conf)
		}
		return CommonRisk(conf, root)
	}
	if kind == config.KindRules {
		return LayerRules(io.Discard, conf, root, name)
	}
	return LayerConfigs(conf, root, name, kind)
}

// spareScope は scope が当たらなくてもよい置き場（作業の途中で生まれる下書きの置き場）。
const spareScope = "wip/"

// UnmatchedScopes はフェーズ定義の scope の glob で、git が挙げるどのファイルにも当たらないものを warn で言う。
// 共通レイヤーの定義は src/*・tests/* のような配置を前提にするので、違う配置では範囲に合う型が見つからずに
// 止まる。見るのは自身と各プロジェクトのレイヤーそれぞれで共通と合成した結果。inherit と wip/ の下は見ず、
// git が使えないレイヤーは何も言わない（一覧が取れないだけで、設定の不備ではない）。
func UnmatchedScopes(conf *config.Settings, root string) []rules.Problem {
	var out []rules.Problem
	for _, name := range layer.Names(conf) {
		project := layer.Project(name)
		home := layer.Home(conf, root, project)
		types, _ := phases.LayerTypes(conf, root, project)
		if types.Len() == 0 || home == "" {
			continue
		}
		done := git.Run(home, []string{"ls-files", "-z", "--cached", "--others", "--exclude-standard"},
			git.Options{PlainPathspecs: true})
		if !done.OK() {
			continue
		}
		var files []string
		for _, f := range strings.Split(done.Out, "\x00") {
			if f != "" {
				files = append(files, f)
			}
		}
		where := LayerWhere(name)
		for p := range types.All() {
			for i := range p.Scope {
				e := &p.Scope[i]
				if e.Glob == "" || strings.HasPrefix(e.Glob, spareScope) || anyMatches(e, files) {
					continue
				}
				out = append(out, problem(rules.Warn, where+" "+phasesWhere+" "+p.ID,
					"`scope` の `"+e.Glob+"` がどのファイルにも当たりません。"+
						"この配置では範囲に合う型が無く、作業が止まります。"+
						"このレイヤーの `.ccnavi/config/project/phases.yml` に、実際の置き場を"+
						"`scope` に書いた定義を足してください（`"+p.ID+"` と同じ id は使えません。"+
						"別の id と表示名を付けてください。書き方は `/ccnavi-config`、"+
						"流れは `.ccnavi/docs/grow-phases.md` を見てください）"))
			}
		}
	}
	return out
}

func anyMatches(e interface{ Matches(string) bool }, files []string) bool {
	for _, f := range files {
		if e.Matches(f) {
			return true
		}
	}
	return false
}
