package rules

import (
	"slices"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
)

// SubjectField はツールと、そのツールでルールを当てる tool_input の欄。
type SubjectField struct {
	Tool  string
	Field string
}

// SubjectFields は、ツールごとにルールを当てる欄。ここに無いツールは対象を持たず、判定に届かないまま通る。
// 名前は Claude Code の権限ルール `ToolName(指定子)` から括弧を除いたもの。並びは画面の候補の順。
// 対象を差し込む側（試験と lint）もこの表を読む。欄の名前を SubjectOf の中だけに持つと、ツールを足したときに
// そちらが気づかないうちに食い違うため。
var SubjectFields = []SubjectField{
	{config.ToolBash, "command"},
	{config.ToolPowerShell, "command"},
	{config.ToolRead, "file_path"},
	{config.ToolGrep, "path"},
	{config.ToolGlob, "path"},
	{config.ToolEdit, "file_path"},
	{config.ToolMultiEdit, "file_path"},
	{config.ToolWrite, "file_path"},
	{config.ToolNotebookEdit, "notebook_path"},
	{config.ToolSkill, "skill"},
	{config.ToolAgent, "description"},
	{config.ToolTask, "description"},
	{config.ToolWebFetch, "url"},
}

// agentPlaceholder は起動の見出しも依頼文も無いサブエージェントの起動の対象。
const agentPlaceholder = "(agent)"

// SubjectFieldOf はツールのルールを当てる欄。無ければ空。
func SubjectFieldOf(tool string) string {
	for _, f := range SubjectFields {
		if f.Tool == tool {
			return f.Field
		}
	}
	return ""
}

// SubjectOf はこのツールの呼び出しでルールを当てる文字列。パスで判定するツールは cwd から解いた絶対パス。
func SubjectOf(in hookio.Input) string {
	tool := in.ToolName
	field := SubjectFieldOf(tool)
	if field == "" {
		return ""
	}
	if slices.Contains(config.AgentTools, tool) {
		// 起動には対象の文字列が無い。レビューが済むまで止める対象なので、見出しを対象にする。
		for _, v := range []string{in.Field(field), in.Field("prompt")} {
			if v != "" {
				return v
			}
		}
		return agentPlaceholder
	}
	value := in.Field(field)
	if tool == config.ToolNotebookEdit && value == "" {
		value = in.Field("file_path")
	}
	if slices.Contains(config.SearchTools, tool) && value == "" {
		value = "."
	}
	if slices.Contains(config.PathTools, tool) {
		return fsio.FullPath(value, in.Cwd)
	}
	return value
}
