package flow

import (
	"bytes"
	"io"
	"os"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/tickets/model/ticket"
)

// フローで選べるサブエージェントとスキルの名前（--lint --json --flow の flow.candidates）。
const (
	// SourceBuiltin は Claude Code の組み込み、SourceProject はワークスペースの .claude/ の下のもの。
	SourceBuiltin = "builtin"
	SourceProject = "project"

	agentsDir   = "agents"
	skillsDir   = "skills"
	skillFile   = "SKILL.md"
	markdownExt = ".md"
	// catalogLimit は読むファイルの数、headLimit は 1 本から読む頭の大きさ（バイト）の上限。
	catalogLimit = 200
	headLimit    = 8 * 1024
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
			if !pathcase.HasExt(e.Name(), markdownExt) || !regularFile(file) {
				continue
			}
			name := frontName(file)
			if name == "" {
				name = e.Name()[:len(e.Name())-len(markdownExt)]
			}
			agents = append(agents, Candidate{name, SourceProject})
		}
		dir = ospath.Join(root, config.ClaudeDir, skillsDir)
		for _, e := range entries(dir, root) {
			if e.Type()&os.ModeSymlink != 0 || !e.IsDir() {
				continue
			}
			file := ospath.Join(dir, e.Name(), skillFile)
			if !regularFile(file) {
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

// regularFile はリンクでない、ふつうのファイルか（辿らずに見る）。
func regularFile(file string) bool {
	info, err := os.Lstat(file)
	return err == nil && info.Mode().IsRegular()
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
	if !regularFile(file) {
		return ""
	}
	f, err := os.Open(file)
	if err != nil {
		return ""
	}
	raw, err := io.ReadAll(io.LimitReader(f, headLimit))
	f.Close()
	if err != nil {
		return ""
	}
	text, _ := fsio.Decode(bytes.TrimPrefix(raw, utf8BOM), fsio.Replace)
	lines := ospath.Splitlines(text)
	if len(lines) == 0 || ospath.Strip(lines[0]) != ticket.Fence {
		return ""
	}
	end := -1
	for i, line := range lines[1:] {
		if ospath.Strip(line) == ticket.Fence {
			end = i + 1
			break
		}
	}
	if end < 0 {
		return ""
	}
	meta, err := yaml.LoadRefusingAliases(strings.Join(lines[1:end], "\n"))
	if err != nil {
		return ""
	}
	name, _ := asMap(meta).String("name")
	return ospath.Strip(Clean(name))
}
