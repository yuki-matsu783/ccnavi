package flow

import (
	"bytes"
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
	// SourceBuiltin は Claude Code の組み込み、SourceProject はワークスペースの .claude/ の下のもの。
	SourceBuiltin = "builtin"
	SourceProject = "project"

	agentsDir   = "agents"
	markdownExt = ".md"
	// catalogLimit は読むファイルの数の上限。1 本から読む頭の大きさは config.SkillHeadLimit。
	catalogLimit = 200
)

// builtinAgents は Claude Code の組み込みのサブエージェント。
var builtinAgents = []string{"general-purpose", "Explore", "Plan"}

// Candidate は選べる名前 1 つ。
type Candidate struct{ Name, Source string }

// Candidates は名前の並び（重なりは除いてある）。
type Candidates []Candidate

func (c Candidates) names() []string {
	out := make([]string, len(c))
	for i, x := range c {
		out[i] = x.Name
	}
	return out
}

// Catalog はフローで選べるサブエージェントとスキル。
type Catalog struct{ Agents, Skills Candidates }

// Object は JSON に載せる形（{"agents": [{name, source}], "skills": [...]}）。
func (c Catalog) Object() *pyjson.Object {
	list := func(items Candidates) []any {
		out := []any{}
		for _, x := range items {
			out = append(out, pyjson.NewObject("name", x.Name, "source", x.Source))
		}
		return out
	}
	return pyjson.NewObject("agents", list(c.Agents), "skills", list(c.Skills))
}

// LoadCatalog はフローで選べるサブエージェントとスキルの名前。名前は frontmatter の name、無ければファイル
// （スキルはディレクトリ）の名前。見るのはワークスペースの .claude/agents/*.md と .claude/skills/*/SKILL.md
// だけで、ユーザのホームとプラグインのものは入らない。リンクは辿らない。
func LoadCatalog(root string) Catalog {
	var agents, skills Candidates
	for _, name := range builtinAgents {
		agents = append(agents, Candidate{name, SourceBuiltin})
	}
	if root != "" {
		dir := ospath.Join(root, config.ClaudeDir, agentsDir)
		for _, e := range entries(dir, root) {
			file := ospath.Join(dir, e.Name())
			if !pathcase.HasExt(e.Name(), markdownExt) || !ospath.IsRegular(file) {
				continue
			}
			name := frontName(file)
			if name == "" {
				name = e.Name()[:len(e.Name())-len(markdownExt)]
			}
			agents = append(agents, Candidate{name, SourceProject})
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
			skills = append(skills, Candidate{name, SourceProject})
		}
	}
	return Catalog{Agents: unique(agents), Skills: unique(skills)}
}

func unique(items Candidates) Candidates {
	seen := map[string]bool{}
	var kept Candidates
	for _, x := range items {
		if x.Name != "" && !seen[x.Name] {
			seen[x.Name] = true
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
	text, _ := fsio.Decode(bytes.TrimPrefix(raw, utf8BOM), fsio.Replace)
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
