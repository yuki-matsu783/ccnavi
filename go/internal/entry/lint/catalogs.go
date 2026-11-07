package lint

import (
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/records/wiki"
	"ccnavi/internal/tickets/proposal/templates"
	"ccnavi/internal/tickets/sync/drafts"
)

// テンプレート・文面ファイル・wiki の検査。検査の中身は各パッケージ（取り込み前の検査と同じ関数）にあり、ここは
// 出どころの表記を付ける。置き場が無いのは正常で、何も言わない。

const (
	whereTemplates = "(templates)"
	// whereTemplateDrafts は下書きの苦情の出どころ。レイヤーの名前（`(templates/<名前>)`）と紛れない表記にする。
	whereTemplateDrafts = "(template-drafts)"
	whereMessages       = "(messages)"
)

func templateWhere(layer string) string {
	if layer == config.LayerCommon {
		return whereTemplates
	}
	return "(templates/" + layer + ")"
}

// templateProblems は作業・レビュー計画のテンプレートの苦情。下書きには効力が無いので、下書きの苦情は error に
// せず warn と info だけで言う（その深刻度は drafts が付ける）。
func templateProblems(conf *config.Settings, root string) []rules.Problem {
	scanned := templates.Load(conf, root)
	var found []rules.Problem
	if scanned.HasAny() {
		for _, lp := range scanned.Problems() {
			detail := lp.Problem.Detail
			if lp.Problem.Rule != "" {
				detail = lp.Problem.Rule + ": " + detail
			}
			found = append(found, rules.Problem{Severity: lp.Problem.Severity, Rule: templateWhere(lp.Layer), Detail: detail})
		}
	}
	for _, p := range drafts.LintProblems(drafts.Scan(conf, root, &scanned.Catalog)) {
		found = append(found, rules.Problem{Severity: p.Severity, Rule: whereTemplateDrafts, Detail: p.Detail})
	}
	return found
}

// messageProblems は文面ファイル（`.ccnavi/messages/*.yml`。レイヤーごとの messages/ は読まない）の苦情。
func messageProblems(root string) []rules.Problem {
	var out []rules.Problem
	for _, p := range texts.Load(root).Problems {
		out = append(out, rules.Problem{Severity: p.Severity, Rule: whereMessages, Detail: p.Rule + ": " + p.Detail})
	}
	return out
}

// wikiProblems は学びの蓄積の wiki（`docs/wiki/`）の frontmatter が壊れたファイル（--learnings はそれを数えない）。
// ワークスペース自身と各プロジェクトのユーザのチェックアウトを見る。
func wikiProblems(conf *config.Settings, root string) []rules.Problem {
	var out []rules.Problem
	for _, main := range mainTrees(conf, root) {
		label := tree.RepoKey(main.Project)
		for _, item := range wiki.LoadTree(main.Root).Broken {
			what := "採否の記録"
			if item.Kind == wiki.KindPattern {
				what = "パターン"
			}
			out = append(out, rules.Problem{Severity: rules.Warn, Rule: "(wiki:" + label + ")",
				Detail: item.File + ": " + what + "の frontmatter を読めない（" + item.Problem + "）。数えない"})
		}
	}
	return out
}

// mainTrees はワークスペース自身と各プロジェクトのユーザのチェックアウト。
func mainTrees(conf *config.Settings, root string) []tree.Tree {
	return append([]tree.Tree{tree.MainTree(root)}, tree.Projects(conf.Projects)...)
}
