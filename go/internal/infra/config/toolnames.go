package config

import (
	"slices"
	"strings"
)

// Claude Code のツール名。ツールの増減はここだけを直す（判定ごとに書くと直し漏れで食い違う）。
const (
	ToolBash             = "Bash"
	ToolPowerShell       = "PowerShell"
	ToolRead             = "Read"
	ToolGrep             = "Grep"
	ToolGlob             = "Glob"
	ToolWrite            = "Write"
	ToolEdit             = "Edit"
	ToolMultiEdit        = "MultiEdit"
	ToolNotebookEdit     = "NotebookEdit"
	ToolAgent            = "Agent"
	ToolTask             = "Task"
	ToolSkill            = "Skill"
	ToolWebFetch         = "WebFetch"
	ToolWebSearch        = "WebSearch"
	ToolAskUserQuestion  = "AskUserQuestion"
	ToolSubagentHandback = "SubagentHandback"
	ToolStructuredOutput = "StructuredOutput"
	ToolArtifact         = "Artifact"
)

// ツール名の組。
var (
	// writeTools はワークツリーのファイルを書き換えるツール。範囲を問うのと、保護領域の宣言とみなすのはこれだけ。
	// Bash は入れない。コマンドの中のパスは追い切れず、当たったり外れたりする判定になる。
	// シェル経由の書き込みは実行後チェックが実物を見て拾う。
	// MultiEdit は 1 回で同じファイルを何か所も直す。現行の Claude Code にも編集ツールのひとつとして残っている。
	writeTools = []string{ToolWrite, ToolEdit, ToolMultiEdit, ToolNotebookEdit}
	// WriteMatch はルールの match: に書く WriteTools（`|` でつなぐ。Python の WRITE_MATCH）。
	WriteMatch = strings.Join(writeTools, "|")
	// writtenFileTools は書いたファイルを記録に残すツール（MultiEdit を含む）。
	writtenFileTools = []string{ToolWrite, ToolEdit, ToolMultiEdit}
	// agentTools はサブエージェントの起動。Claude Code の版によって名前が違う。
	agentTools = []string{ToolAgent, ToolTask}
	// askUserTools はユーザに質問するツール。サブエージェントには渡らない。
	askUserTools = []string{ToolAskUserQuestion}
	// reportTools はサブエージェントが親へ報告を返すツール。判定に達せなくても止めない
	// （止めると報告を返せずに終われない）。
	reportTools = []string{ToolSubagentHandback, ToolStructuredOutput}
	// readTools は読むだけで、パスを持つツール。
	readTools = []string{ToolRead, ToolGrep, ToolGlob}
	// shellTools はシェルを実行するツール。
	shellTools = []string{ToolBash, ToolPowerShell}
	// searchTools は探す場所を省けるツール。省いたら cwd を対象にする（空のままだとどのルールにも当たらず通る）。
	searchTools = []string{ToolGrep, ToolGlob}

	// pathTools は行き先のパスで判定するツール。
	pathTools = concat(readTools, writeTools)
	// strictTools は判定に達せなかったときに止めるツール。
	strictTools = concat(writeTools, agentTools)
	// readOnlyTools は実行後チェックを省けるツール。省いても次の書きうるツールの後に同じ変更が見える。
	// 知らない名前（MCP は自由に名付けられる）は書きうる側に置くので、ここは狭く保つ。
	readOnlyTools = concat(readTools, []string{ToolWebFetch, ToolWebSearch})
)

// AgentTools は agentTools の写しを返す（呼び手が書き換えても元は変わらない）。
func AgentTools() []string { return slices.Clone(agentTools) }

// AskUserTools は askUserTools の写しを返す（呼び手が書き換えても元は変わらない）。
func AskUserTools() []string { return slices.Clone(askUserTools) }

// PathTools は pathTools の写しを返す（呼び手が書き換えても元は変わらない）。
func PathTools() []string { return slices.Clone(pathTools) }

// ReadOnlyTools は readOnlyTools の写しを返す（呼び手が書き換えても元は変わらない）。
func ReadOnlyTools() []string { return slices.Clone(readOnlyTools) }

// ReadTools は readTools の写しを返す（呼び手が書き換えても元は変わらない）。
func ReadTools() []string { return slices.Clone(readTools) }

// ReportTools は reportTools の写しを返す（呼び手が書き換えても元は変わらない）。
func ReportTools() []string { return slices.Clone(reportTools) }

// SearchTools は searchTools の写しを返す（呼び手が書き換えても元は変わらない）。
func SearchTools() []string { return slices.Clone(searchTools) }

// ShellTools は shellTools の写しを返す（呼び手が書き換えても元は変わらない）。
func ShellTools() []string { return slices.Clone(shellTools) }

// WriteTools は writeTools の写しを返す（呼び手が書き換えても元は変わらない）。
func WriteTools() []string { return slices.Clone(writeTools) }

// WrittenFileTools は writtenFileTools の写しを返す（呼び手が書き換えても元は変わらない）。
func WrittenFileTools() []string { return slices.Clone(writtenFileTools) }

func concat(groups ...[]string) []string {
	var out []string
	for _, g := range groups {
		out = append(out, g...)
	}
	return out
}
