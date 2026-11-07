package shellguard

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/model/place"
)

// WriteKind は親のワークツリーへの書き込みの形。文面を分ける。
type WriteKind string

const (
	// KindWrite は書き込み先の語（リダイレクト・tee・cp の行き先など）か、Write・Edit のツール。
	KindWrite WriteKind = "write"
	// KindCode はインタプリタに渡したコードと、読み切れないコマンド行の文字列。中を読み切れないので、
	// 親のワークツリーのパスと書き込みを疑う語が並べば止める側に倒す。
	KindCode WriteKind = "code"
)

// writeHint は書き込みを疑う語。読むだけのコードを止めないための線引きで、広めに取る。`open(` は書くモード
// （`'w'`・`"a"`・`'x'`・`+`）を添えたときだけ数える（読むだけの `open(p).read()` を通す）。
var writeHint = pyre.MustCompile(
	`open\s*\([^)]*['"][rbt]*[wax][bt+]*['"]|write|append|unlink|remove|rename|replace\s*\(|rmtree|copy|move|mkdir|`+
		`makedirs|truncate|chmod|symlink|touch|system|exec|spawn|popen|subprocess|`+
		`out-file|set-content|add-content|new-item|>`,
	pyre.IgnoreCase,
)

// targetExpands は書き込み先の語のうち、読めない展開（変数・置換・グロブ・`~`・`{`）。
const targetExpands = "$`*?[~{"

// ParentWrite はサブエージェントの、親のワークツリーへのシェルからの書き込み。What は書き込みの形（語か、コードの
// 短い形）、Parent は親のワークツリーの名前（居場所を読めなければ `?`）。見つからなければ What が空。
type ParentWrite struct {
	What   string
	Parent string
	Kind   WriteKind
}

// CheckParentWrite は、サブエージェントがシェルから親のワークツリー（`.claude/worktrees/<名前>` で、そのツリーの
// チケットが親のもの）へ書き込む形を探す。layers は実行役が中で実行するコマンドの層（改行で区切った形）。
//
// 書き込み先は `cd` と実行役の行き先を追った居場所から解く。インタプリタに渡したコードは、親のワークツリーのパス
// （絶対パス・`.claude/worktrees/<親>`・`../<親>`）を含むか、居場所が親のワークツリーで、書き込みを疑う語があれば止める。
// 走査が読み切れないコマンド行は、文字列に親のパスと書き込みを疑う語が並べば止める。下書き・提案・承認済みの置き場は
// Write と同じく対象にしない（置き場ごとの保護が別に掛かっている）。
func CheckParentWrite(conf *config.Settings, root, cwd, subject string, degraded bool, layers []string) ParentWrite {
	parents := parentTrees(conf, root)
	if len(parents) == 0 {
		return ParentWrite{}
	}
	all := append([]string{subject}, layers...)
	unread := false
	for n, text := range all {
		places := shell.PlacedCommands(text, n > 0)
		if len(places) == 0 {
			unread = unread || ospath.Strip(text) != ""
			continue
		}
		for _, p := range places {
			if found := checkCommand(conf, root, cwd, parents, p); found.What != "" {
				return found
			}
		}
	}
	if degraded || unread {
		for _, text := range all {
			if name := mentionedParent(root, parents, text); name != "" && writeHint.Search(text) {
				return ParentWrite{short(text), name, KindCode}
			}
		}
	}
	return ParentWrite{}
}

// parentTrees は親のワークツリーの名前（`.claude/worktrees/` の下で、自身のチケットが親のもの）。名前の順。
func parentTrees(conf *config.Settings, root string) []string {
	base := tree.WorktreesPath(root)
	names, err := ospath.DiskListdir(base)
	if err != nil {
		return nil
	}
	var out []string
	for _, n := range names {
		if ospath.IsDir(ospath.Join(base, n)) && IsParentTree(conf, root, n) {
			out = append(out, n)
		}
	}
	return out
}

func checkCommand(conf *config.Settings, root, cwd string, parents []string, p shell.Placed) ParentWrite {
	s := scanCommand(cwd, p)
	for _, c := range s.codes {
		if found := checkCode(conf, root, parents, c); found.What != "" {
			return found
		}
	}
	for _, t := range s.targets {
		if found := checkTarget(conf, root, parents, s.base, s.known, t); found.Parent != "" {
			return found
		}
	}
	return ParentWrite{}
}

func isParentName(parents []string, name string) bool {
	folded := pathcase.Fold(name)
	return slices.ContainsFunc(parents, func(p string) bool { return pathcase.Fold(p) == folded })
}

func checkCode(conf *config.Settings, root string, parents []string, c code) ParentWrite {
	if !writeHint.Search(c.text) {
		return ParentWrite{}
	}
	if name := mentionedParent(root, parents, c.text); name != "" {
		return ParentWrite{short(c.text), name, KindCode}
	}
	if c.known {
		if where := parentAt(conf, root, c.base, listed(parents)); where != "" {
			return ParentWrite{short(c.text), where, KindCode}
		}
	}
	return ParentWrite{}
}

// mentionedParent は文字列に親のワークツリーのパスがあれば、その親の名前。名前の続きの字（英数字・`-_.`）が
// 後ろに付くものは別の名前なので数えない。
func mentionedParent(root string, parents []string, text string) string {
	folded := strings.ReplaceAll(text, `\`, "/")
	runes := []rune(folded)
	base := tree.WorktreesPath(root)
	for _, name := range parents {
		forms := []string{
			strings.ReplaceAll(ospath.Join(base, name), `\`, "/"),
			strings.ReplaceAll(ospath.Realpath(ospath.Join(base, name)), `\`, "/"),
			"worktrees/" + name,
			"../" + name,
		}
		for _, form := range forms {
			re, err := pyre.Compile(pyre.Escape(form), pyre.IgnoreCase)
			if err != nil {
				continue
			}
			for _, m := range re.FindAll(folded) {
				if m.End >= len(runes) || !continuesName(runes[m.End]) {
					return name
				}
			}
		}
	}
	return ""
}

func continuesName(r rune) bool { return pyfmt.IsWord(r) || strings.ContainsRune("-.", r) }

func checkTarget(conf *config.Settings, root string, parents []string, base string, known bool, target string) ParentWrite {
	word := strings.ReplaceAll(target, `\`, "/")
	if word == "" {
		return ParentWrite{}
	}
	if e := strings.IndexAny(word, targetExpands); e >= 0 {
		// 展開より前の固定の部分（最後の `/` まで）が親のワークツリーの中なら止める。
		word = dirPart(word[:e])
		if word == "" || !ospath.IsAbs(word) && !known {
			return ParentWrite{}
		}
	}
	if !ospath.IsAbs(word) {
		if !known {
			return ParentWrite{target, unknownParent, KindWrite}
		}
		word = ospath.Join(base, word)
	}
	name := parentAt(conf, root, word, listed(parents))
	if name == "" {
		return ParentWrite{}
	}
	return ParentWrite{target, name, KindWrite}
}

// ParentWriteAt は、Write・Edit の書き込み先 path が親のワークツリー（`.claude/worktrees/<名前>` で、その名前が親の
// チケット。ワークツリーが git に登録されているか・在るかは問わない）の中なら、その親の名前。下書き・提案・承認済みの
// 置き場は空（置き場ごとの保護が別に掛かっている）。シェルの書き込み先（CheckParentWrite）も同じ parentAt で見る。
func ParentWriteAt(conf *config.Settings, root, path string) string {
	return parentAt(conf, root, path, func(name string) bool { return IsParentTree(conf, root, name) })
}

// parentAt は path が親のワークツリーの中で、置き場ごとの保護に任せる置き場でなければ、その親の名前。親かどうかは
// isParent で見る（シェルの検査は在るディレクトリから先に集めた親の名前、Write・Edit はチケットの役割を直に見る）。
func parentAt(conf *config.Settings, root, path string, isParent func(string) bool) string {
	name := WorktreeName(root, path)
	if name == "" || !isParent(name) || unscoped(conf, root, name, path) {
		return ""
	}
	return name
}

// listed は先に集めた親のワークツリーの名前に入っているか（大文字小文字は畳む）。
func listed(parents []string) func(string) bool {
	return func(name string) bool { return isParentName(parents, name) }
}

// unscoped は下書き・提案・承認済みの置き場か（Write と同じく、置き場ごとの保護に任せる）。
func unscoped(conf *config.Settings, root, name, path string) bool {
	rest, ok := tree.PartsUnder(ospath.Join(root, tree.WorktreesDir, name), path)
	if !ok || len(rest) == 0 {
		return false
	}
	return place.IsUnscoped(strings.Join(rest, "/"), conf.Tickets, conf.Approved)
}
