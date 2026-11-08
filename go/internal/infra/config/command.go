package config

import (
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/platformtag"
	"ccnavi/internal/infra/pyfmt"
)

// 文面で案内するコマンドの組み立て。案内した形をそのまま打てて、打った形が判定（止めている間の例外や
// サブエージェントの禁止）にも当たるように、区切りは "/"、引用は要るときだけにそろえる。

const (
	// GitWrapper は git の入口の sh。
	GitWrapper = "ccnavi-git.sh"
	// TicketWrapper はチケット操作の sh（approval-request などの入口）。
	TicketWrapper = "ccnavi-ticket.sh"
	// ReviewWrapper はレビューの操作の sh（review-request・review-decide などの入口）。
	ReviewWrapper = "ccnavi-review.sh"
	// StartWrapper はチケットの着手（ワークツリーを切ってセッションを始める）の sh。
	StartWrapper = "ccnavi-start.sh"
	// WorktreeWrapper は直接作業のワークツリーを切る sh。
	WorktreeWrapper = "ccnavi-worktree.sh"
	// StageWrapper は保護対象を下書きで直す sh。
	StageWrapper = "ccnavi-stage.sh"
	// LauncherScript は実行ファイルを振り分けて起動する sh（名前の持ち主は platformtag）。
	LauncherScript = platformtag.LauncherName
	// ScriptsDir は ccnavi ディレクトリの下の sh の置き場。
	ScriptsDir = "scripts"
	// defaultBin は実行ファイルの指定が無いときの案内。PATH に置いたユーザはそのまま打てる。
	defaultBin = "ccnavi"
	// Launcher は文面でエージェントに打たせる起動の sh。配布先では `ccnavi` が PATH に無い。ルートで打つ形。
	Launcher = "sh " + DefaultProjectHome + "/" + ScriptsDir + "/" + LauncherScript
)

// ScriptCommand は `.ccnavi/scripts/` の sh をワークスペースルートから書いた打ち方。
// スクリプトはワークスペースにしか無く、プロジェクトから切ったワークツリーでは相対パスが届かない。
func ScriptCommand(root, name string) string {
	base := strings.TrimRight(ospath.Slashed(ospath.Realpath(root)), "/")
	return "sh " + quoted(base+"/"+DefaultProjectHome+"/"+ScriptsDir+"/"+name)
}

// ShellPath は `cd <パス> && …` に置くパス。区切りを "/" にそろえるのは Windows のパスだけ
// （POSIX の `\` は名前の一部）。
func ShellPath(path string) string {
	if ospath.Windows || ospath.HasDrive(path, true) && strings.HasPrefix(path[2:], `\`) {
		path = ospath.Slashed(path)
	}
	return quoted(path)
}

// ShellWord はパスでない 1 語（タイトル・名前）。区切りは変えず、要るときだけ引用する。
func ShellWord(text string) string { return quoted(text) }

// HasGitWrapper はワークスペースルートに git のラッパーがあるか。
func HasGitWrapper(root string) bool {
	return root != "" && ospath.IsFile(ospath.Join(root, DefaultProjectHome, ScriptsDir, GitWrapper))
}

// GitWrapperCommand は git の入口の打ち方。ラッパーが無ければ ok が偽で、呼び手はユーザに頼む文にする
// （生の git は組み込みで止まるので、`git` を案内するとその行が止まる）。
func GitWrapperCommand(root string) (string, bool) {
	if !HasGitWrapper(root) {
		return "", false
	}
	return ScriptCommand(root, GitWrapper), true
}

// GitCommand はラッパーがあればその打ち方、無ければ `git`。HasGitWrapper が偽なら文に入れないこと。
func GitCommand(root string) string {
	if command, ok := GitWrapperCommand(root); ok {
		return command
	}
	return "git"
}

// BinCommand は ccnavi 自身の打ち方。hook が起動しているのと同じパスを案内する。
// `.sh`（大小を問わない。Windows・macOS では同じファイル）は `sh` を頭に付ける。
func BinCommand(binPath string) string {
	if binPath == "" {
		return defaultBin
	}
	path := ospath.Slashed(binPath)
	// 拡張子の判定は pyfmt.Lower で行う。pathcase.Fold（casefold）は ſ を s にして、`.ſh` を `.sh` と見てしまうため使わない。
	if strings.HasSuffix(pyfmt.Lower(path), ".sh") {
		return "sh " + quoted(path)
	}
	return quoted(path)
}

// bareUnsafe は引用せずにシェルへ渡せない文字（空白は Unicode の空白も数える）。
const bareUnsafe = `'"\$` + "`" + `!*?[]{}()<>|&;#~`

// doubleQuoteUnsafe は二重引用符の中でも意味を持つ文字。
const doubleQuoteUnsafe = `"\$` + "`" + `!`

// quoted は引用が要るときだけ引用する。文面は案内を '...' で囲むので、まず "..." を使い、
// "..." の中でも意味を持つ文字があるときだけ単引用符（shlex.quote）にする。
func quoted(path string) string {
	bare := path != "" && !strings.ContainsFunc(path, func(r rune) bool {
		return ospath.IsSpace(r) || strings.ContainsRune(bareUnsafe, r)
	})
	if bare {
		return path
	}
	if !strings.ContainsAny(path, doubleQuoteUnsafe) {
		return `"` + path + `"`
	}
	return "'" + strings.ReplaceAll(path, "'", `'"'"'`) + "'"
}
