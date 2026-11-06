package config

import (
	"regexp"
	"runtime"
	"strings"

	"ccnavi/internal/infra/ospath"
)

// 文面で案内するコマンドの組み立て。案内した形をそのまま打てて、打った形が判定（止めている間の例外や
// サブエージェントの禁止）にも当たるように、区切りは "/"、引用は要るときだけにそろえる。

const (
	gitWrapper = "ccnavi-git.sh"
	scriptsDir = "scripts"
	// defaultBin は実行ファイルの指定が無いときの案内。PATH に置いたユーザはそのまま打てる。
	defaultBin = "ccnavi"
	// Launcher は文面でエージェントに打たせる起動の sh。配布先では `ccnavi` が PATH に無い。ルートで打つ形。
	Launcher = "sh " + DefaultProjectHome + "/" + scriptsDir + "/ccnavi-launcher.sh"
)

// ScriptCommand は `.ccnavi/scripts/` の sh をワークスペースルートから書いた打ち方。
// スクリプトはワークスペースにしか無く、プロジェクトから切ったワークツリーでは相対パスが届かない。
func ScriptCommand(root, name string) string {
	base := strings.TrimRight(strings.ReplaceAll(ospath.Realpath(root), `\`, "/"), "/")
	return "sh " + quoted(base+"/"+DefaultProjectHome+"/"+scriptsDir+"/"+name)
}

var windowsDrive = regexp.MustCompile(`^[A-Za-z]:\\`)

// ShellPath は `cd <パス> && …` に置くパス。区切りを "/" にそろえるのは Windows のパスだけ
// （POSIX の `\` は名前の一部）。
func ShellPath(path string) string {
	if runtime.GOOS == "windows" || windowsDrive.MatchString(path) {
		path = strings.ReplaceAll(path, `\`, "/")
	}
	return quoted(path)
}

// ShellWord はパスでない 1 語（タイトル・名前）。区切りは変えず、要るときだけ引用する。
func ShellWord(text string) string { return quoted(text) }

// HasGitWrapper はワークスペースルートに git のラッパーがあるか。
func HasGitWrapper(root string) bool {
	return root != "" && ospath.IsFile(ospath.Join(root, DefaultProjectHome, scriptsDir, gitWrapper))
}

// GitWrapperCommand は git の入口の打ち方。ラッパーが無ければ ok が偽で、呼び手はユーザに頼む文にする
// （生の git は組み込みで止まるので、`git` を案内するとその行が止まる）。
func GitWrapperCommand(root string) (string, bool) {
	if !HasGitWrapper(root) {
		return "", false
	}
	return ScriptCommand(root, gitWrapper), true
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
	path := strings.ReplaceAll(binPath, `\`, "/")
	if strings.HasSuffix(strings.ToLower(path), ".sh") {
		return "sh " + quoted(path)
	}
	return quoted(path)
}

// bareUnsafe は引用せずにシェルへ渡せない文字（空白は Python の \s と同じく Unicode の空白も数える）。
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
