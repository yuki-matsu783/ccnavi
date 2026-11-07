package lint

import (
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/flow"
)

const parentDir = ".."

// flowRead は読めたフローの中身（flow.AsJSON の形）と、SubagentStart で渡る手順の行。ok が偽なら読めていない。
type flowRead struct {
	data     any
	rendered []any
	ok       bool
}

// flowProblems は子のフローのファイル 1 本が、SubagentStart が読むのと同じ読み（flow.Load）で読めるか
// （`--lint --flow`）。別に書くと、画面が「正しい」と言ったフローを SubagentStart が読めない食い違いになる。
// 読めない・無いのは error（確かめたつもりで何も確かめていない形を作らない）。ツリーの中のファイルなら、ルートからの
// 途中のリンクも見る。読めたフローには、手順として怪しいところを warn で足す。どれも detail は渡したパスで始まる。
// 中身を返すのは、拡張が値の意味（`0755` や `yes`）を自分で決めずに済ませるため。
func flowProblems(path, root string, catalog *flow.Catalog) ([]rules.Problem, flowRead) {
	shown := flow.Clean(path)
	fail := func(why string) ([]rules.Problem, flowRead) {
		return []rules.Problem{{Severity: rules.Error, Rule: whereFlow, Detail: shown + ": " + why}}, flowRead{}
	}
	if !ospath.DiskLexists(path) {
		return fail("無い")
	}
	inside := false
	if root != "" {
		if rel, ok := ospath.Relpath(path, root); ok {
			inside = rel != parentDir && !strings.HasPrefix(rel, parentDir+ospath.Sep) && !ospath.IsAbs(rel)
		}
	}
	base := ""
	if inside {
		base = root
	}
	data, why := flow.Load(path, base)
	if why != "" {
		return fail(why)
	}
	said := flow.StructureProblems(data)
	if catalog != nil {
		said = append(said, flow.NameProblems(data, *catalog)...)
	}
	var warns []rules.Problem
	for _, line := range said {
		warns = append(warns, rules.Problem{Severity: rules.Warn, Rule: whereFlow, Detail: shown + ": " + line})
	}
	lines, _ := flow.Render(data, flow.ChildTextLimit)
	rendered := make([]any, len(lines))
	for i, line := range lines {
		rendered[i] = line
	}
	return warns, flowRead{data: flow.AsJSON(data), rendered: rendered, ok: true}
}
