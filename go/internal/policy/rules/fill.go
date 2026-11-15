package rules

import (
	"math/big"
	"regexp"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyre"
)

// RootPlaceholder はワークスペースルートを指す合言葉。glob・regex・文面の中で使え、実パスに置き換わる。
const RootPlaceholder = "{root}"

// notRootPlaceholder は「ワークスペースルートの外」を指す合言葉。regex では `^` の直後に 1 回だけ書ける。
const notRootPlaceholder = "{!root}"

// scriptPlaceholder は文の差し込み `{script:<名前>}`（名前は `.ccnavi/scripts/` のファイル名）。
var scriptPlaceholder = regexp.MustCompile(`\{script:([^{}]*)\}`)

// ScriptPlaceholders は text の中の `{script:<名前>}` を書かれた順に返す。各要素は [書いたとおりの表記, 名前]。
func ScriptPlaceholders(text string) [][]string {
	return scriptPlaceholder.FindAllStringSubmatch(text, -1)
}

// scriptName は scriptPlaceholder の名前に書ける形。
var scriptName = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)

// IsScriptName は {script:<名前>} の名前として受け付ける形かどうかを返す。
func IsScriptName(name string) bool { return scriptName.MatchString(name) }

// realRoot はルートを {!root} の展開に使える形にする。実パスにして、末尾の区切りを除く。
// 除かないと、ルート直下の普通のファイル名が「外」になってしまう。Windows のドライブ直下では realpath が `C:\` を返すため。
func realRoot(root string) string {
	return strings.TrimRight(ospath.Realpath(root), `\/`)
}

// RootGlob はワークスペースルートの実パスを glob と文面に埋める形にする（区切りは "/"）。
func RootGlob(root string) string {
	return strings.TrimRight(ospath.Slashed(ospath.Realpath(root)), "/")
}

// RootPattern はワークスペースルートの実パスを regex に埋める形にする。区切りは "/" と "\" のどちらにも当たる。
func RootPattern(root string) string {
	var b strings.Builder
	for _, ch := range realRoot(root) {
		b.WriteString(same(ch))
	}
	return b.String()
}

func isSeparator(ch rune) bool { return ch == '/' || ch == '\\' }

// differs は、その 1 字と違う字に当たる文字クラス。区切りは両方の書き方を除く。
func differs(ch rune) string {
	if isSeparator(ch) {
		return `[^\\/]`
	}
	return "[^" + pyre.Escape(string(ch)) + "]"
}

func same(ch rune) string {
	if isSeparator(ch) {
		return pyre.AnySeparator
	}
	return pyre.Escape(string(ch))
}

// notRootEnd は展開の各階層で「ここで文字列が終わる」ことを表す式。`$` は末尾の改行の直前にも当たるので使わない。
const notRootEnd = `\Z`

// notRootPattern は「ワークスペースルートで始まらない」を、先読みなしに書いた式。ルートを 1 字ずつ
// 「ここで終わる」「ここが違う」「ここは同じ」の入れ子に分けて書く。繰り返しを含まないので、判定の時間はルートの長さに比例する。
// 引数は realRoot で正規化したパスを渡す。先頭の `^` は呼ぶ側が書く。
//
// 最内には `\Z` を置かない。そこはルートを最後までたどり終えた位置で、そこで終わる対象はルートそのもの（中と数える）になるため。
func notRootPattern(root string) string {
	body := differs('/')
	runes := []rune(root)
	for i := len(runes) - 1; i >= 0; i-- {
		ch := runes[i]
		body = "(?:" + notRootEnd + "|" + differs(ch) + "|" + same(ch) + body + ")"
	}
	return body
}

// FillText はモデルへ渡す文の差し込みを置き換える。`{script:<名前>}` は sh の打ち方に変える（名前の形が違えば残す。lint が指摘する）。
// `{root}` はルートの実パスに変える。ルートが渡らない読み方では何もしない。
func FillText(text, root string) string {
	if root == "" {
		return text
	}
	filled := scriptPlaceholder.ReplaceAllStringFunc(text, func(found string) string {
		name := scriptPlaceholder.FindStringSubmatch(found)[1]
		if !IsScriptName(name) {
			return found
		}
		return config.ScriptCommand(root, name)
	})
	return fillRoot(filled, root)
}

// fillRoot は文の `{root}` をルートの実パス（"/" 区切り）に置き換える。
func fillRoot(text, root string) string {
	if root == "" || !strings.Contains(text, RootPlaceholder) {
		return text
	}
	return strings.ReplaceAll(text, RootPlaceholder, RootGlob(root))
}

// relativeScripts は `.ccnavi/scripts/` の当たり。手前の `./` と `../` の連なりは相対の書き方なので含める。
var relativeScripts = regexp.MustCompile(`(?:\.{1,2}/)*\.ccnavi/scripts/`)

// pathMid は、その手前の 1 字がこれに当たるとき、当たりがパスの途中（絶対パス・`$VAR/`・`{root}/`・`~/`）であることを表す。
const pathMid = `/\}~$._-`

// HasRelativeScripts は文に、cwd に左右される相対パスの `.ccnavi/scripts/` があるか。
func HasRelativeScripts(text string) bool {
	for _, hit := range relativeScripts.FindAllStringIndex(text, -1) {
		if hit[0] == 0 {
			return true
		}
		before, _ := utf8.DecodeLastRuneInString(text[:hit[0]])
		alnum := before < utf8.RuneSelf && (before >= '0' && before <= '9' || before >= 'a' && before <= 'z' || before >= 'A' && before <= 'Z')
		if alnum || strings.ContainsRune(pathMid, before) {
			continue
		}
		return true
	}
	return false
}

// ReadableEvery は、書かれた every から読み取れる刻みを返す。読めない値（真偽・整数でない・1 未満）なら 0、
// 書いていなければ EveryDefault を返す。読めないときの扱いは呼び手が決める（判定は 1、lint は苦情にする）。
func ReadableEvery(written any) int {
	switch v := written.(type) {
	case nil:
		return EveryDefault
	case int:
		if v >= 1 {
			return v
		}
	case *big.Int:
		if v.Sign() > 0 {
			return int(^uint(0) >> 1)
		}
	}
	return 0
}
