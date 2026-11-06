package config

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
)

// ツール名の組。
var (
	// WriteTools はワークツリーのファイルを書き換えるツール。範囲を問うのと、保護領域の宣言とみなすのはこれだけ。
	// Bash は入れない。コマンドの中のパスは追い切れず、当たったり外れたりする判定になる。
	// シェル経由の書き込みは実行後チェックが実物を見て拾う。
	// MultiEdit は 1 回で同じファイルを何か所も直す。現行の Claude Code にも編集ツールの組として残る。
	WriteTools = []string{ToolWrite, ToolEdit, ToolMultiEdit, ToolNotebookEdit}
	// WrittenFileTools は書いたファイルを記録に残すツール（MultiEdit を含む）。
	WrittenFileTools = []string{ToolWrite, ToolEdit, ToolMultiEdit}
	// AgentTools はサブエージェントの起動。Claude Code の版によって名前が違う。
	AgentTools = []string{ToolAgent, ToolTask}
	// AskUserTools はユーザに質問するツール。サブエージェントには渡らない。
	AskUserTools = []string{ToolAskUserQuestion}
	// ReportTools はサブエージェントが親へ報告を返すツール。判定に達せなくても止めない
	// （止めると報告を返せずに終われない）。
	ReportTools = []string{ToolSubagentHandback, ToolStructuredOutput}
	// ReadTools は読むだけで、パスを持つツール。
	ReadTools = []string{ToolRead, ToolGrep, ToolGlob}
	// ShellTools はシェルを実行するツール。
	ShellTools = []string{ToolBash, ToolPowerShell}
	// SearchTools は探す場所を省けるツール。省いたら cwd を対象にする（空のままだとどのルールにも当たらず通る）。
	SearchTools = []string{ToolGrep, ToolGlob}

	// PathTools は行き先のパスで判定するツール。
	PathTools = concat(ReadTools, WriteTools)
	// StrictTools は判定に達せなかったときに止めるツール。
	StrictTools = concat(WriteTools, AgentTools)
	// ReadOnlyTools は実行後チェックを省けるツール。省いても次の書きうるツールの後に同じ変更が見える。
	// 知らない名前（MCP は自由に名付けられる）は書きうる側に置くので、ここは狭く保つ。
	ReadOnlyTools = concat(ReadTools, []string{ToolWebFetch, ToolWebSearch})
)

func concat(groups ...[]string) []string {
	var out []string
	for _, g := range groups {
		out = append(out, g...)
	}
	return out
}
