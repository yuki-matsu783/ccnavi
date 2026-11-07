package agentkind

import (
	"errors"
	"io"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/yaml"
)

// Kind はサブエージェントの種類。
type Kind string

const (
	Edit Kind = "edit"
	read Kind = "read"
)

// readOnlyBuiltin は組み込みで読み取り専用のサブエージェント（畳んだ名前）。同名のプロジェクトの定義があれば
// そちらを採る（書き込めるものに上書きされうる）。
var readOnlyBuiltin = []string{"explore", "plan", "claude-code-guide"}

// readOnlyTools は読み取り専用と確かめられたツール（畳んだ名前）。Bash（コードから書く経路を読み切れない）・`*`・
// `Edit(src/**)` のような括弧付き・MCP のツールは、読み取りと確かめられないので入れない。
var readOnlyTools = []string{
	"read", "grep", "glob", "ls", "webfetch", "websearch", "notebookread", "todowrite",
	"agent", "task", "subagenthandback", "structuredoutput",
}

const (
	// agentsDir はサブエージェントの定義の置き場（ルートの .claude/ の下）。
	agentsDir = "agents"
	// headLimit は定義の frontmatter を読む長さの上限（バイト）。
	headLimit = 16384
	// keyTools・keyName は定義の frontmatter の欄。
	keyTools = "tools"
	keyName  = "name"
)

// Of はサブエージェントの種類。tools は payload に一覧があるときだけ渡し（nil は無い）、あれば定義より優先する。
// roots は定義を探すルートで、近い順に並べる。disallowedTools は読み取りの根拠にしない（外していない Bash や MCP が残る）。
func Of(roots []string, agentType string, tools []string) Kind {
	if tools != nil {
		return kindOfTools(tools)
	}
	name := pyfmt.Casefold(ospath.Strip(agentType))
	if name == "" {
		return Edit
	}
	meta := definition(roots, name)
	if meta == nil {
		if slices.Contains(readOnlyBuiltin, name) {
			return read
		}
		return Edit
	}
	if !meta.Has(keyTools) {
		return Edit
	}
	allowed, ok := names(meta.Get(keyTools))
	if !ok {
		return Edit
	}
	return kindOfTools(allowed)
}

// kindOfTools は、一覧が空でなく、全部が読み取りと確かめられたツールなら Read。
func kindOfTools(tools []string) Kind {
	if len(tools) == 0 {
		return Edit
	}
	for _, n := range tools {
		if !slices.Contains(readOnlyTools, pyfmt.Casefold(ospath.Strip(n))) {
			return Edit
		}
	}
	return read
}

// names は tools の値（カンマ区切りの文字列か、文字列の列）。読めなければ偽。
func names(value any) ([]string, bool) {
	if s, ok := value.(string); ok {
		var out []string
		for _, p := range strings.Split(s, ",") {
			if p = ospath.Strip(p); p != "" {
				out = append(out, p)
			}
		}
		return out, true
	}
	return yaml.Strings(value)
}

// definition は name の定義の frontmatter。見つからない・読めないなら nil。リンクは辿らない。
func definition(roots []string, name string) *yaml.Map {
	folded := pathcase.Fold(name)
	for _, root := range roots {
		folder := ospath.Join(root, config.ClaudeDir, agentsDir)
		entries, err := ospath.DiskListdir(folder)
		if err != nil {
			continue
		}
		for _, entry := range entries {
			path := ospath.Join(folder, entry)
			stem, ok := strings.CutSuffix(entry, config.MdExt)
			if !ok || !ospath.IsRegular(path) {
				continue
			}
			meta := front(path)
			if meta == nil {
				continue
			}
			named, ok := meta.Get(keyName).(string)
			if !ok {
				named = stem
			}
			if pathcase.Fold(ospath.Strip(named)) == folded {
				return meta
			}
		}
	}
	return nil
}

// front は frontmatter のマップ。無い・閉じていない・読めない・マップでないなら nil。
func front(path string) *yaml.Map {
	f, _, err := fsio.OpenNoFollow(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	head := make([]byte, headLimit)
	n, err := io.ReadFull(f, head)
	if err != nil && !errors.Is(err, io.EOF) && !errors.Is(err, io.ErrUnexpectedEOF) {
		return nil
	}
	text, _ := fsio.DecodeBytes(head[:n], fsio.Replace)
	body, state := yaml.FrontBody(text, yaml.FrontStyle{Indented: true, SkipBOM: true})
	if state != yaml.FrontClosed {
		return nil
	}
	meta, err := yaml.Load(body)
	if err != nil {
		return nil
	}
	m, _ := meta.(*yaml.Map)
	return m
}
