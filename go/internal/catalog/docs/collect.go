package docs

import (
	"errors"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"ccnavi/internal/catalog/docindex"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
)

const (
	// workspaceName は案内と標準エラーでワークスペース自身を指す名前。
	workspaceName = "ワークスペース"
	// docsDir は ccnavi ディレクトリの下で、使い方の文書を置く場所。ccnavi ディレクトリの中でここだけは載せる。
	docsDir = "docs"
)

// source は索引を組む 1 つの git のワークツリー。prefix は concept_id の頭に付ける（ワークスペースルートからの相対）。
type source struct {
	name, base, prefix string
	excluded, kept     []string
}

// home は ccnavi ディレクトリ（ツリーのルートからの相対、両端の "/" を除く）。
func home(conf *config.Settings) string {
	h := conf.ProjectHome
	if h == "" {
		h = config.DefaultProjectHome
	}
	return strings.Trim(h, "/")
}

// keptDirs は ccnavi ディレクトリを除くなかで、索引に載せる例外。
func keptDirs(home string) []string {
	if home == "" {
		return nil
	}
	return []string{home + "/" + docsDir}
}

// relTo は root からの相対（"/" 区切り）。root 自身か外なら空。
func relTo(path, root string) string {
	rel, ok := ospath.Relpath(ospath.Realpath(path), ospath.Realpath(root))
	if !ok {
		return ""
	}
	rel = filepath.ToSlash(rel)
	if rel == "." || rel == ".." || strings.HasPrefix(rel, "../") {
		return ""
	}
	return rel
}

// places は、ワークスペースルートと、プロジェクトの置き場の直下のプロジェクト（それぞれ別の git）を返す。
// ワークスペースからは ccnavi ディレクトリとプロジェクトの置き場を外す。プロジェクトの md は、そのプロジェクトで拾うため。
func places(conf *config.Settings, root string) []source {
	h := home(conf)
	var own []string
	if h != "" {
		own = []string{h}
	}
	workspace := append([]string(nil), own...)
	if conf.Projects != "" {
		if rel := relTo(conf.Projects, root); rel != "" {
			workspace = append(workspace, rel)
		}
	}
	kept := keptDirs(h)
	found := []source{{name: workspaceName, base: root, excluded: workspace, kept: kept}}
	for _, project := range tree.Projects(conf.Projects) {
		prefix := relTo(project.Root, root)
		if prefix == "" {
			prefix = ospath.Basename(ospath.Normpath(conf.Projects)) + "/" + project.Project
		}
		found = append(found, source{name: project.Project, base: project.Root, prefix: prefix, excluded: own, kept: kept})
	}
	return found
}

// withoutGit はプロジェクトの置き場の直下にあって `.git` を持たないディレクトリの名前。
func withoutGit(conf *config.Settings) []string {
	if conf.Projects == "" {
		return nil
	}
	names, err := ospath.DiskListdir(conf.Projects) // 名前の順
	if err != nil {
		return nil
	}
	var found []string
	for _, name := range names {
		dir := ospath.Join(conf.Projects, name)
		if ospath.IsDir(dir) && !tree.HasGit(dir) {
			found = append(found, name)
		}
	}
	return found
}

// collected はワークスペースとプロジェクトを合わせた索引。パスはワークスペースルートから。
type collected struct {
	rows []docindex.Row
	// unignored は md を持つが ccnavi-index.jsonl を無視していないので対象外にしたツリーの名前。
	unignored []string
	// failed は git への問い合わせに失敗したツリー（名前: 理由）。対象外とも git の外とも別。
	failed []string
	// foreign は ccnavi のものでないので触らなかった ccnavi-index.jsonl。
	foreign []string
	// ignoreAdded は無視の行を足した .gitignore。未コミットの差分が残る。
	ignoreAdded []string
	problems    []string
	// indexable はどれかのツリーが md を持ち、索引の対象だった。
	indexable bool
	timedOut  bool
}

// collectOptions は collect の選び方。
type collectOptions struct {
	refresh, parse bool
	deadline       time.Time
}

// collect は、ワークスペースとプロジェクトの索引を新しくして集める。`.git` の無いツリーは、何も言わずに飛ばす。
// 同じ concept_id（NFC で比べる）は 1 つにまとめ、concept_id の順に並べる。
func collect(conf *config.Settings, root string, o collectOptions) *collected {
	result := &collected{}
	seen := map[string]bool{}
	for _, p := range places(conf, root) {
		if docindex.Past(o.deadline) {
			result.timedOut = true
			break
		}
		built, err := docindex.Build(p.base, docindex.Options{Excluded: p.excluded, Kept: p.kept,
			Refresh: o.refresh, Parse: o.parse, Deadline: o.deadline})
		if errors.Is(err, docindex.ErrNotRepository) {
			continue
		}
		if err != nil {
			result.failed = append(result.failed, p.name+": "+err.Error())
			result.timedOut = result.timedOut || docindex.Past(o.deadline)
			continue
		}
		if built.Unignored {
			result.unignored = append(result.unignored, p.name)
		}
		result.timedOut = result.timedOut || built.TimedOut
		result.indexable = result.indexable || built.Indexable
		at := func(rel string) string { return docindex.JoinRel(p.prefix, rel) }
		for _, f := range built.Foreign {
			result.foreign = append(result.foreign, at(f))
		}
		if built.IgnoreAdded != "" {
			result.ignoreAdded = append(result.ignoreAdded, at(built.IgnoreAdded))
		}
		for _, problem := range built.Problems {
			result.problems = append(result.problems, at(problem))
		}
		for _, row := range built.Rows {
			moved := row.Prefixed(p.prefix)
			key := pyfmt.NFC(moved.ConceptID())
			if seen[key] {
				continue
			}
			seen[key] = true
			result.rows = append(result.rows, moved)
		}
	}
	sort.SliceStable(result.rows, func(i, j int) bool { return result.rows[i].ConceptID() < result.rows[j].ConceptID() })
	return result
}
