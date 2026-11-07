package flow

import (
	"io"
	"os"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
)

// フローで選べるサブエージェントとスキルの名前（--lint --json --flow の flow.candidates）。
const (
	// sourceBuiltin は Claude Code の組み込み、sourceProject はワークスペースの .claude/ の下のもの。
	sourceBuiltin = "builtin"
	sourceProject = "project"

	agentsDir = "agents"
	// catalogLimit は読むファイルの数の上限。1 本から読む頭の大きさは config.SkillHeadLimit。
	catalogLimit = 200
)

// builtinAgents は Claude Code の組み込みのサブエージェント。
var builtinAgents = []string{"general-purpose", "Explore", "Plan"}

// candidate は選べる名前 1 つ。
type candidate struct{ name, source string }

// candidates は名前の並び（重なりは除いてある）。
type candidates []candidate

func (c candidates) names() []string {
	out := make([]string, len(c))
	for i, x := range c {
		out[i] = x.name
	}
	return out
}

// Catalog はフローで選べるサブエージェントとスキル。
type Catalog struct{ agents, skills candidates }

// Object は JSON に載せる形（{"agents": [{name, source}], "skills": [...]}）。
func (c Catalog) Object() *pyjson.Object {
	list := func(items candidates) []any {
		out := []any{}
		for _, x := range items {
			out = append(out, pyjson.NewObject("name", x.name, "source", x.source))
		}
		return out
	}
	return pyjson.NewObject("agents", list(c.agents), "skills", list(c.skills))
}

// LoadCatalog はフローで選べるサブエージェントとスキルの名前。名前は frontmatter の name、無ければファイル
// （スキルはディレクトリ）の名前。見るのはワークスペースの .claude/agents/*.md と .claude/skills/*/SKILL.md
// だけで、ユーザのホームとプラグインのものは入らない。リンクは辿らない。
func LoadCatalog(root string) Catalog {
	var agents, skills candidates
	for _, name := range builtinAgents {
		agents = append(agents, candidate{name, sourceBuiltin})
	}
	if root != "" {
		dir := ospath.Join(root, config.ClaudeDir, agentsDir)
		for _, e := range entries(dir, root) {
			file := ospath.Join(dir, e.Name())
			if !pathcase.HasExt(e.Name(), config.MdExt) || !ospath.IsRegular(file) {
				continue
			}
			name := frontName(file)
			if name == "" {
				name = e.Name()[:len(e.Name())-len(config.MdExt)]
			}
			agents = append(agents, candidate{name, sourceProject})
		}
		dir = ospath.Join(root, config.ClaudeDir, config.SkillsDir)
		for _, e := range entries(dir, root) {
			if e.Type()&os.ModeSymlink != 0 || !e.IsDir() {
				continue
			}
			file := ospath.Join(dir, e.Name(), config.SkillFile)
			if !ospath.IsRegular(file) {
				continue
			}
			name := frontName(file)
			if name == "" {
				name = e.Name()
			}
			skills = append(skills, candidate{name, sourceProject})
		}
	}
	return Catalog{agents: unique(agents), skills: unique(skills)}
}

func unique(items candidates) candidates {
	seen := map[string]bool{}
	var kept candidates
	for _, x := range items {
		if x.name != "" && !seen[x.name] {
			seen[x.name] = true
			kept = append(kept, x)
		}
	}
	return kept
}

// entries はディレクトリの中身（名前の順、catalogLimit 件まで）。root からそのディレクトリまでの途中
// （.claude 自体を含む）にリンクがあれば読まない。
func entries(dir, root string) []os.DirEntry {
	if pathmatch.HasLink(root, dir) || !ospath.IsDir(dir) {
		return nil
	}
	found, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	return head(found, catalogLimit)
}

// frontName は Markdown の frontmatter の name の値。無い・読めないなら空。リンクは辿らない。
// frontmatter だけを YAML として読む（折り返しや行末の注釈で名前が化けないように）。別名は読まない。
func frontName(file string) string {
	if !ospath.IsRegular(file) {
		return ""
	}
	f, err := os.Open(file)
	if err != nil {
		return ""
	}
	raw, err := io.ReadAll(io.LimitReader(f, config.SkillHeadLimit))
	f.Close()
	if err != nil {
		return ""
	}
	text, _ := fsio.Decode(yaml.TrimBOM(raw), fsio.Replace)
	body, state := yaml.FrontBody(text, yaml.FrontStyle{Indented: true})
	if state != yaml.FrontClosed {
		return ""
	}
	meta, err := yaml.LoadRefusingAliases(body)
	if err != nil {
		return ""
	}
	name, _ := asMap(meta).String("name")
	return ospath.Strip(Clean(name))
}
