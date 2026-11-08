package selfguard

import (
	"path/filepath"
	"regexp"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/infra/tree"
)

// 下書きの置き場（各ツリー直下の `wip/handoff/`）。守る場所を直すときは、ここへ写したコピーを直す
// （.ccnavi/docs/scratchpad.md）。コピーにはルールとしての効力が無いので書けるようにする。ただし、つながった（リンク・ハードリンク）
// 対象と、そこにリンクを作る形は止める。式は固定の形なので RE2 で読む。

// handoffParts は置き場（ワークスペースルートからの相対）。
var handoffParts = []string{config.WipRoot, config.HandoffName}

// handoffPlace はツリー（ワークスペース・`projects/<名前>`・その中のワークツリー）の直下の置き場の下にヒットする式。
// 当てる先は fold して `/` 区切りにした絶対パス。
func handoffPlace(root string) *regexp.Regexp {
	base := slashFold(ospath.Realpath(root))
	quoted := make([]string, len(handoffParts))
	for i, p := range handoffParts {
		quoted[i] = regexp.QuoteMeta(p)
	}
	return regexp.MustCompile(`^` + regexp.QuoteMeta(base) +
		`(?:/projects/[^/]+)?(?:/` + regexp.QuoteMeta(filepath.ToSlash(tree.WorktreesDir)) + `/[^/]+)?/` + strings.Join(quoted, "/") + `/`)
}

func slashFold(path string) string { return ospath.Slashed(pathcase.Fold(path)) }

// handoffLinked は、置き場の中の対象が、リンクを通る・ハードリンクでほかの名前とつながる・`..` を含むかを返す。
// 確かめられなければ、つながっているとみなす。
func handoffLinked(subject, spelledPath string) bool {
	for _, part := range strings.Split(spelledPath, "/") {
		if part == ".." {
			return true
		}
	}
	if slashFold(ospath.Realpath(subject)) != spelledPath {
		return true
	}
	// 普通のファイルでない・無い（新しい下書き）なら、ハードリンクは見ない。
	if !ospath.IsFile(subject) {
		return false
	}
	n, ok := ospath.HardLinks(subject)
	return !ok || n > 1
}

// handoffWriteExempt は名指しのツールの保護から、置き場の中のつながっていない対象を外す関数（外すなら空を返す）。
// root が空なら何も外さない（nil）。
func handoffWriteExempt(root string) func(string) string {
	if root == "" {
		return nil
	}
	pattern := handoffPlace(root)
	return func(subject string) string {
		s := slashFold(subject)
		if !pattern.MatchString(s) || handoffLinked(subject, s) {
			return subject
		}
		return ""
	}
}

// handoffDeletePath は削除の対象 1 つ（絶対パス。`..` は畳まない綴り）が、置き場の中のつながっていないものか。
func handoffDeletePath(root, path string) bool {
	if root == "" || !ospath.IsAbs(path) {
		return false
	}
	s := slashFold(path)
	return handoffPlace(root).MatchString(s) && !handoffLinked(path, s)
}

// plainWord は削除の対象として読む語。シェルが別のパスに直す文字（`~` `$` `*` `{` 引用符・空白など）を含まない。
var plainWord = regexp.MustCompile(`^[A-Za-z0-9_./:@+,%-]+$`)

// HandoffDeleteWord は削除の対象の語 1 つが、置き場の中のつながっていないファイルか。絶対パスか、base（そのコマンドが
// 居る場所。空なら絶対パスだけ）から解ける相対パスのとき。素の rm とラッパーの rm が同じ検査を通る。
func HandoffDeleteWord(root, base, word string) bool {
	if !plainWord.MatchString(word) {
		return false
	}
	if ospath.IsAbs(word) {
		return handoffDeletePath(root, word)
	}
	if base == "" {
		return false
	}
	return handoffDeletePath(root, ospath.Join(ospath.Realpath(base), word))
}

// deleteTargets は削除コマンド 1 本（`rm`・`git rm`・`ccnavi-git.sh rm`、`sh`・`bash` を前に置いてもよい）の対象の語。
// 削除と読めない形・読み切れない形（`--pathspec…`）は空。
func deleteTargets(words []string) []string {
	k := 0
	if len(words) > 0 && (commandBase(words[0]) == "sh" || commandBase(words[0]) == "bash") {
		k = 1
	}
	if k >= len(words) {
		return nil
	}
	var rest []string
	switch name := commandBase(words[k]); {
	case name == "rm":
		rest = words[k+1:]
	case (name == "git" || name == config.GitWrapper) && k+1 < len(words) && words[k+1] == "rm":
		rest = words[k+2:]
	default:
		return nil
	}
	// `--` より前のオプションは飛ばし、`--` の後ろはすべて対象の語として数える。
	var targets []string
	dashes := false
	for _, word := range rest {
		switch {
		case !dashes && word == "--":
			dashes = true
		case dashes || !strings.HasPrefix(word, "-"):
			targets = append(targets, word)
		case strings.HasPrefix(word, "--pathspec"):
			return nil
		}
	}
	return targets
}

// handoffDeleteExempt はシェルの保護から、置き場の中だけを消すコマンドを取り除く関数。対象の語がすべて置き場の中と
// 読めるときだけ取り除き、ほかのコマンドと書き込みは残す。取り除くのは絶対パスの語だけ。`sh -c` の層の中の `cd` は見えないため。
func handoffDeleteExempt(root string) func(string) string {
	if root == "" {
		return nil
	}
	return func(subject string) string {
		// コマンドごとに見て、置き場の中だけを消すコマンドを取り除く。語が引用でつながったコマンド（WordSep を含む）は読み切れないので残す。
		commands := strings.Split(subject, shell.Sep)
		var kept []string
		for _, command := range commands {
			var targets []string
			if !strings.Contains(command, shell.WordSep) {
				targets = deleteTargets(strings.Split(command, " "))
			}
			if len(targets) > 0 && allPlain(root, targets) {
				continue
			}
			kept = append(kept, command)
		}
		if len(kept) < len(commands) {
			return strings.Join(kept, shell.Sep)
		}
		return subject
	}
}

func allPlain(root string, targets []string) bool {
	for _, t := range targets {
		if !HandoffDeleteWord(root, "", t) {
			return false
		}
	}
	return true
}

// handoffLinkTarget は名指しのツールで、置き場の中のつながった対象に書く形だけを残す関数。利用者のルールの except が
// 置き場を外していても、ハードリンクでつながった守る場所へは書かせないため。
func handoffLinkTarget(root string) func(string) string {
	if root == "" {
		return nil
	}
	pattern := handoffPlace(root)
	return func(subject string) string {
		s := slashFold(subject)
		if pattern.MatchString(s) && handoffLinked(subject, s) {
			return subject
		}
		return ""
	}
}

// リンクを作るコマンド。ln・link と、リンクを作る選択肢を付けた cp。
// shell.IsRunner・shell.IsAssignment には寄せない。別の表を持つのは、寄せると答えが変わるため。
// linkPrefixes は busybox・xargs を含み doas・stdbuf などを含まない。assignment は配列の要素（`a[1]=`）を代入と読まない。
var (
	linkers       = map[string]bool{"ln": true, "link": true, "gln": true}
	linkPrefixes  = map[string]bool{"sudo": true, "env": true, "command": true, "exec": true, "nice": true, "nohup": true, "time": true, "busybox": true, "xargs": true, "timeout": true}
	cpLinkOptions = []string{"--link", "--symbolic-link"}
	// cpOptionLeast は省略した長い選択肢として読む最短の字数（`--l` は --link と読む。`--` だけは読まない）。
	cpOptionLeast = 3
	segments      = regexp.MustCompile("[\x00;&|\n()`]+")
	linkOption    = regexp.MustCompile(`^-[A-Za-z]*[ls][A-Za-z]*$`)
	assignment    = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*\+?=`)
)

func makesLink(words []string) bool {
	k := 0
	for k < len(words) && (assignment.MatchString(words[k]) || linkPrefixes[commandBase(words[k])] || strings.HasPrefix(words[k], "-")) {
		k++
	}
	if k >= len(words) {
		return false
	}
	name := commandBase(words[k])
	if linkers[name] {
		return true
	}
	if name != "cp" && name != "gcp" {
		return false
	}
	for _, word := range words[k+1:] {
		option, _, _ := strings.Cut(word, "=")
		// GNU の長い選択肢は省略できる（`--li` は `--link`）。
		if len([]rune(option)) >= cpOptionLeast {
			for _, full := range cpLinkOptions {
				if strings.HasPrefix(full, option) {
					return true
				}
			}
		}
		if linkOption.MatchString(word) {
			return true
		}
	}
	return false
}

// commandBase はコマンドの名前（ディレクトリと `.exe` を除き、大文字小文字を畳む）。shell.Base と違い、casefold してから
// `.exe` だけを除く（`RM` は rm、`x.cmd` は x.cmd のまま）。判定が変わらないよう、shell.Base とは別に持つ。
func commandBase(word string) string {
	name := strings.ReplaceAll(word, `\`, "/")
	name = pathcase.Fold(name[strings.LastIndex(name, "/")+1:])
	return strings.TrimSuffix(name, ".exe")
}

// handoffLinkCommand はシェルで、置き場の中にリンクを作る形だけを残す関数。リンクを作るコマンドがあり、コマンド行に
// `handoff` の綴りがあるか作業ディレクトリが置き場の中なら残す。行き先は変数や cd で組めるので、読み分けない。
func handoffLinkCommand(root, cwd string) func(string) string {
	if root == "" {
		return nil
	}
	pattern := handoffPlace(root)
	in := cwd != "" && pattern.MatchString(slashFold(ospath.Realpath(cwd))+"/")
	return func(subject string) string {
		text := strings.ReplaceAll(subject, shell.WordSep, " ")
		linked := false
		for _, seg := range segments.Split(text, -1) {
			if makesLink(ospath.Fields(seg)) {
				linked = true
				break
			}
		}
		if linked && (in || strings.Contains(pathcase.Fold(text), config.HandoffName)) {
			return subject
		}
		return ""
	}
}
