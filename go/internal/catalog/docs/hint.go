package docs

import (
	"strings"

	"ccnavi/internal/catalog/docindex"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/place"
)

const (
	// CodeMissingFrontmatter は frontmatter の無い md を新しく作ったときの案内の理由コード。
	CodeMissingFrontmatter = "NOTICE_DOC_FRONTMATTER"
	writeTool              = config.ToolWrite
	createdType            = "create"
)

// noFrontmatterNames は frontmatter を付けない決まりの md（中身がそのまま人にもモデルにも見える）。
// 決まりの文書（conventionDoc）の「付けていない」と揃える。
var noFrontmatterNames = []string{"README.md", "CLAUDE.md", "AGENTS.md", "CHANGELOG.md"}

// noFrontmatterPlaces は、決まりの文書の「対象外」の表にある置き場のうち、frontmatter を付けない・付けなくてよいもの
// （ツリーのルートからの glob。`*` は "/" もまたぐ）。表を直したら、ここも直す。テストが表と比べる。チケットと下書きの
// 置き場は設定で動くので、設定の値でも見る。
var noFrontmatterPlaces = []string{
	"wip/proposals/**",
	".ccnavi/approved/**",
	"wip/design/scripts/i*.md",
	"wip/handoff/**",
	"scratchpad/**",
	".claude/skills/yomiyasu/**",
}

// noFrontmatter は fnmatch.translate の和（大文字小文字を区別しない）。"/" は "/" にだけ当てる（rel は "/" 区切り）。
var noFrontmatter = func() *pyre.Regexp {
	parts := make([]string, len(noFrontmatterPlaces))
	for i, g := range noFrontmatterPlaces {
		parts[i] = pyre.TranslateFnmatch(g)
	}
	return pyre.MustCompile(strings.Join(parts, "|"), pyre.IgnoreCase)
}()

// MissingFrontmatter は、Write で新しく作った md に frontmatter か type が無ければ、1 度だけ案内を返す。判定は
// 変えない。既にある md を Edit・上書きしたときは出さない。意図して frontmatter を持たない md を直すたびに促さないため。
// 対象は、書いた先のツリー（ワークスペース・プロジェクト・ワークツリー）の git で索引に入る md のうち、ccnavi
// ディレクトリの下と、決まりの文書で付けない・付けなくてよいとされる名前と置き場を除いたもの。
func MissingFrontmatter(conf *config.Settings, root string, in hookio.Input) string {
	if in.ToolName != writeTool || in.ToolResponse.String("type") != createdType {
		return ""
	}
	var raw string
	if v := in.ToolInput.Value("file_path"); pyfmt.Truthy(v) {
		raw = pyfmt.Str(v)
	}
	// raw.rsplit("/", 1)[-1]。ospath.Basename は Windows でドライブ（`C:README.md`）も外すので使わない。
	name := ospath.Slashed(raw)
	name = name[strings.LastIndex(name, "/")+1:]
	if !strings.HasSuffix(raw, docindex.MdExt) || isNoFrontmatterName(name) {
		return ""
	}
	cwd := in.Cwd
	if cwd == "" {
		cwd = root
	}
	written := ospath.Abspath(ospath.Join(cwd, raw))
	where, ok := tree.TreeOf(root, fsio.FullPath(raw, cwd), conf.Projects)
	if !ok || !tree.HasGit(where.Root) {
		return ""
	}
	rel := tree.Relative(where, written)
	h := home(conf)
	var exempt []string
	for _, p := range []string{h, place.Handoff, strings.Trim(conf.Tickets, "/")} {
		if p != "" {
			exempt = append(exempt, p)
		}
	}
	if rel == "" || strings.HasPrefix(rel, "../") || docindex.Excluded(rel, exempt, keptDirs(h)) || noFrontmatter.Match(rel) {
		return ""
	}
	ignored, err := docindex.Ignored(where.Root, []string{rel}, git.FrontmatterHintTimeout)
	if err != nil || ignored[rel] {
		return ""
	}
	head, ok := docindex.ReadHead(written)
	if !ok {
		return ""
	}
	front := docindex.FrontMatter(head)
	if front != nil && ospath.Strip(typeText(front.Value(fieldType))) != "" {
		return ""
	}
	shown := relTo(written, root)
	if shown == "" {
		shown = rel
	}
	return frontmatterHint(conf, root, shown, front != nil)
}

// typeText は str(v or "")。
func typeText(v any) string {
	if !pyfmt.Truthy(v) {
		return ""
	}
	return pyfmt.Str(v)
}

func isNoFrontmatterName(name string) bool {
	folded := pathcase.Fold(name)
	for _, n := range noFrontmatterNames {
		if folded == pathcase.Fold(n) {
			return true
		}
	}
	return false
}

func frontmatterHint(conf *config.Settings, root, shown string, hasFront bool) string {
	values := texts.Values{"code": CodeMissingFrontmatter, "shown": shown, "command": command(conf, root), "doc": conventionDoc}
	variants := texts.Variants{"what": conventionCase(root)}
	switch {
	case !hasFront:
		return texts.Say(root, "NOTICE_DOCS_FRONTMATTER__NONE", values, variants)
	case pathcase.Fold(ospath.Basename(ospath.Slashed(shown))) == pathcase.Fold(config.SkillFile):
		return texts.Say(root, "NOTICE_DOCS_FRONTMATTER__SKILL", values, variants)
	}
	return texts.Say(root, "NOTICE_DOCS_FRONTMATTER__TYPE", values, variants)
}
