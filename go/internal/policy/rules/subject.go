package rules

import (
	"slices"
	"strings"
	"unicode"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
)

// SubjectField はツールと、そのツールでルールを当てる tool_input の欄。
type SubjectField struct {
	tool  string
	Field string
}

// subjectFields は、ツールごとにルールを当てる欄。ここに無いツールは対象を持たず、判定に届かないまま通る。
// 名前は Claude Code の権限ルール `ToolName(指定子)` から括弧を除いたもの。並びは画面の候補の順。
// 対象を差し込む側（試験と lint）もこの表を読む。欄の名前を SubjectOf の中だけに持つと、ツールを足したときに
// そちらが気づかないうちに食い違うため。
var subjectFields = []SubjectField{
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

// SubjectFields は subjectFields の写しを返す（呼び手が書き換えても元は変わらない）。
func SubjectFields() []SubjectField { return slices.Clone(subjectFields) }

// agentPlaceholder は起動の見出しも依頼文も無いサブエージェントの起動の対象。
const agentPlaceholder = "(agent)"

// SubjectFieldOf はツールのルールを当てる欄。無ければ空。
func SubjectFieldOf(tool string) string {
	for _, f := range subjectFields {
		if f.tool == tool {
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
	if slices.Contains(config.AgentTools(), tool) {
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
	if slices.Contains(config.SearchTools(), tool) && value == "" {
		value = "."
	}
	if slices.Contains(config.PathTools(), tool) {
		return fsio.FullPath(value, in.Cwd)
	}
	return value
}

// writeVerbs は書き込みの操作を表す語。ツール名の語がこれで始まれば、書き込み系のツールと見る。
var writeVerbs = []string{
	"write", "edit", "create", "delete", "remove", "rename", "move", "copy", "put", "patch", "update", "upload",
	"save", "append", "insert", "replace", "apply", "overwrite", "modify", "mkdir", "unlink", "touch", "rm", "mv", "cp",
}

// targetKeys は、名前にこれを含む tool_input の欄を書き込み先と見る（`_`・`-` を除き小文字にして比べる）。
var targetKeys = []string{"path", "file", "dest", "target", "dir"}

// UnreadWriteTarget は、書き込み系のツールなのに、ルールを当てる対象（書き込み先）が読めない呼び出しか。
// 読めない書き込みは保護の対象か決められないので、判定が止める側に回す（通すと、設定ファイルも保護領域も素通りする）。
//
// 書き込み系と見るのは次の 2 つ。
//   - WriteTools（Write・Edit など）で、SubjectFields の欄が空（`Write` に `path` だけを渡した、など）
//   - SubjectFields に無いツール（MCP の `mcp__<server>__<tool>` など）で、名前の語が書き込みの操作（writeVerbs）で
//     始まり、tool_input に書き込み先らしい欄（targetKeys）がある。名前だけで決めると、ファイルに触れない
//     `create_issue` なども止まる。欄だけで決めると、読むだけの `read_file` も止まる
func UnreadWriteTarget(in hookio.Input) bool {
	if SubjectOf(in) != "" {
		return false
	}
	if slices.Contains(config.WriteTools(), in.ToolName) {
		return true
	}
	if SubjectFieldOf(in.ToolName) != "" {
		return false
	}
	return writeVerbName(in.ToolName) && hasTargetKey(in)
}

// writeVerbName はツール名の語のどれかが書き込みの操作で始まるか。MCP のツールはサーバの名前を除いて見る
// （`mcp__file-editor__read_file` を書き込みと読まない）。語は `_`・`-`・`.`・空白と、小文字から大文字への変わり目で切る。
func writeVerbName(tool string) bool {
	if rest, ok := strings.CutPrefix(tool, "mcp__"); ok {
		if _, name, found := strings.Cut(rest, "__"); found {
			tool = name
		}
	}
	var words []string
	var word []rune
	previous := rune(0)
	for _, r := range tool {
		switch {
		case r == '_' || r == '-' || r == '.' || unicode.IsSpace(r):
			words, word = append(words, string(word)), nil
		case unicode.IsUpper(r) && unicode.IsLower(previous):
			words, word = append(words, string(word)), []rune{unicode.ToLower(r)}
		default:
			word = append(word, unicode.ToLower(r))
		}
		previous = r
	}
	words = append(words, string(word))
	return slices.ContainsFunc(words, func(w string) bool {
		return w != "" && slices.ContainsFunc(writeVerbs, func(verb string) bool { return strings.HasPrefix(w, verb) })
	})
}

// hasTargetKey は tool_input に、値の入った書き込み先らしい欄があるか。
func hasTargetKey(in hookio.Input) bool {
	for _, key := range in.ToolInput.Keys() {
		folded := strings.ToLower(strings.NewReplacer("_", "", "-", "").Replace(key))
		if !slices.ContainsFunc(targetKeys, func(k string) bool { return strings.Contains(folded, k) }) {
			continue
		}
		switch v := in.ToolInput.Value(key).(type) {
		case string:
			if v != "" {
				return true
			}
		case nil:
		default:
			// 文字列でない値（パスの並びなど）も書き込み先になりうる。
			return true
		}
	}
	return false
}
