package cli

import (
	"os"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
)

// defaultRoot はワークスペースルート（.claude を持つディレクトリ。ワークツリーは除く）。hook は走るディレクトリを
// 選べないので、作業ディレクトリそのものではなく上へ辿る。Claude Code が渡す CLAUDE_PROJECT_DIR があればそれを使う
// （あることに依存はしない）。
func defaultRoot() string {
	if fromEnv := os.Getenv(env.ClaudeProjectDir); fromEnv != "" {
		return fromEnv
	}
	if found := findProjectRoot(); found != "" {
		return found
	}
	return "."
}

// isWorktreeDir は `<ワークスペースルート>/.claude/worktrees/<名前>` の位置にあるディレクトリか。Windows で clone した
// ワークツリーは表記が変わることがあるので、大文字小文字は区別しない。
func isWorktreeDir(directory string) bool {
	holder := pathcase.Fold(ospath.Dirname(directory))
	return strings.HasSuffix(holder, string(os.PathSeparator)+pathcase.Fold(tree.WorktreesDir))
}

// findProjectRoot は作業ディレクトリから上って .claude を探す。ワークツリーは自分も .claude を持つので、
// その位置のディレクトリは飛ばして親のワークスペースルートまで上る。
func findProjectRoot() string {
	directory, err := ospath.Getwd()
	if err != nil {
		return ""
	}
	for {
		if ospath.IsDir(ospath.Join(directory, config.ClaudeDir)) && !isWorktreeDir(directory) {
			return directory
		}
		parent := ospath.Dirname(directory)
		if parent == directory {
			return ""
		}
		directory = parent
	}
}
