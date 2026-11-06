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

// RootPlaceholder はワークスペースルートを指す合言葉。glob・regex・文面の中で使え、その実パスに置き換わる。
const RootPlaceholder = "{root}"

// NotRootPlaceholder は「ワークスペースルートの外」を指す合言葉。regex の `^` の直後に 1 回だけ書ける。
const NotRootPlaceholder = "{!root}"

// ScriptPlaceholder は文の差し込み `{script:<名前>}`（名前は `.ccnavi/scripts/` のファイル名）。
var ScriptPlaceholder = regexp.MustCompile(`\{script:([^{}]*)\}`)

// scriptName は ScriptPlaceholder の名前に書ける形。
var scriptName = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)

// IsScriptName は {script:<名前>} の名前として受ける形か。
func IsScriptName(name string) bool { return scriptName.MatchString(name) }

// RealRoot はルートを {!root} の展開に使える形にする（実パスにして、末尾の区切りを除く）。
// 除かないと、ルート直下の普通のファイル名が「外」になる（Windows のドライブ直下は realpath が `C:\` を返す）。
func RealRoot(root string) string {
	return strings.TrimRight(ospath.Realpath(root), `\/`)
}

// RootGlob はワークスペースルートの実パスを glob と文面に埋める形（区切りは "/"）。
func RootGlob(root string) string {
	return strings.TrimRight(ospath.Slashed(ospath.Realpath(root)), "/")
}

// RootPattern はワークスペースルートの実パスを regex に埋める形。区切りは "/" と "\" のどちらにも当てる。
func RootPattern(root string) string {
	var b strings.Builder
	for _, ch := range RealRoot(root) {
		b.WriteString(same(ch))
	}
	return b.String()
}

const anySeparator = `[\\/]`

func isSeparator(ch rune) bool { return ch == '/' || ch == '\\' }

// differs はその 1 字と違う字に当たる文字クラス。区切りは両方の書き方を除く。
func differs(ch rune) string {
	if isSeparator(ch) {
		return `[^\\/]`
	}
	return "[^" + pyre.Escape(string(ch)) + "]"
}

func same(ch rune) string {
	if isSeparator(ch) {
		return anySeparator
	}
	return pyre.Escape(string(ch))
}

// notRootEnd は展開の各段の「ここで文字列が終わる」。`$` は末尾の改行の直前にも当たるので使わない。
const notRootEnd = `\Z`

// NotRootPattern は「ワークスペースルートで始まらない」を先読みなしに書いた式。ルートを 1 字ずつ
// 「ここで終わる／ここが違う／ここは同じ」の入れ子に分ける。繰り返しを含まず、判定はルートの長さに比例する。
// 引数は RealRoot で正規化したパス。`^` は呼ぶ側が書く。
//
// 最内に `\Z` を置かない。そこはルートを最後までたどり終えた位置で、そこで終わる対象はルートそのもの
// （中と数える）。
func NotRootPattern(root string) string {
	body := differs('/')
	runes := []rune(root)
	for i := len(runes) - 1; i >= 0; i-- {
		ch := runes[i]
		body = "(?:" + notRootEnd + "|" + differs(ch) + "|" + same(ch) + body + ")"
	}
	return body
}

// FillText はモデルへ渡す文の差し込みを置き換える。`{script:<名前>}` は sh の打ち方に（名前の形が
// 違えば残す。lint が言う）、`{root}` はルートの実パスに。ルートが渡らない読み方では何もしない。
func FillText(text, root string) string {
	if root == "" {
		return text
	}
	filled := ScriptPlaceholder.ReplaceAllStringFunc(text, func(found string) string {
		name := ScriptPlaceholder.FindStringSubmatch(found)[1]
		if !IsScriptName(name) {
			return found
		}
		return config.ScriptCommand(root, name)
	})
	return FillRoot(filled, root)
}

// FillRoot は文の `{root}` をルートの実パス（"/" 区切り）に置き換える。
func FillRoot(text, root string) string {
	if root == "" || !strings.Contains(text, RootPlaceholder) {
		return text
	}
	return strings.ReplaceAll(text, RootPlaceholder, RootGlob(root))
}

// relativeScripts は `.ccnavi/scripts/` の当たり。手前の `./` と `../` の連なりは相対の書き方なので含める。
var relativeScripts = regexp.MustCompile(`(?:\.{1,2}/)*\.ccnavi/scripts/`)

// pathMid はその手前の 1 字がこれなら、当たりがパスの途中（絶対パス・`$VAR/`・`{root}/`・`~/`）であること。
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

// ReadableEvery は書かれた every から読み取れる刻み。読めない値（真偽・整数でない・1 未満）なら 0、
// 書いていなければ EveryDefault。読めないときにどうするかは呼び手が決める（判定は 1、lint は苦情）。
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
