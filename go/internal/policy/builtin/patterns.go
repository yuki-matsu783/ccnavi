package builtin

import "strings"

// 止める形の式。Python の re と同じ答えが要る（所有的な繰り返しとアトミックグループを使う）ので、
// rules が pyre で読む。読みの約束は次の 3 つ。
//
//   - 語を読む繰り返しは所有的（`*+`）にし、どの語をどの枠で読むかが 1 通りに決まる形にする
//   - ある始まりでヒットするなら、もっと前の始まりでもヒットする式（rm と reset）は、コマンドの切れ目から
//     最初の始まりまでを 1 度だけ探してそこに決める（`rm rm rm …` で始まりごとに読み直さない）
//   - パスの前置き（`/usr/bin/git` の `/usr/bin/`）は読まない。名前の直前の `/` でヒットする

const (
	// before と after は名前の前後の切れ目。`legit`・`ccnavi-git.sh`・`push.default` にはヒットしない。
	before = `(?:^|[^\w.-])`
	after  = `(?:[^\w.-]|$)`
	// wordEnd は語の終わり。`\x00` はコマンドの切れ目なので、次のコマンドへまたがない。
	wordEnd = `(?:[\s\x00]|$)`
	// first はコマンドの切れ目から名前の前の切れ目までを読み、最初の始まりに決める（アトミックグループを
	// 開いたまま返す。閉じるのは使う側）。`??` で、切れ目の直後に名前がある形を先に見る。
	first = `(?:^|\x00)(?>(?:[^\x00]*?[^\w.\x00-])??`
	// inWord は語の中の、名前の前の切れ目までを読む。
	inWord = `(?:[^\s\x00]*?[^\w\s\x00.-])??`
)

// ---- git
//
// 語の中の字を、名前の前の切れ目になる字（anchor）とそれ以外（plain）に分ける。
const (
	plain  = `[\w.-]`
	anchor = `[^\w\s\x00.-]`
)

// plainBut は plain のうち、字 c（大文字小文字を問わない）を除いたもの。
func plainBut(c string) string { return `(?:[^\W` + c + `]|[.-])` }

// tail は、最後の切れ目の後ろが `git` / `git.exe` でない語の、その後ろの部分（空でない）。選択肢は長いほうから
// 並べる。短いほう（`g`）を先に置くと、`gitx` の頭の `g` だけでヒットしてしまう。所有的な繰り返しは読み直さないので、
// 残り（`itx`）で読みが止まるためである。
var tail = `(?:` +
	`git\.exe` + plain + `++` +
	`|git\.ex(?:` + plainBut("e") + plain + `*+)?` +
	`|git\.e(?:` + plainBut("x") + plain + `*+)?` +
	`|git\.(?:` + plainBut("e") + plain + `*+)?` +
	`|git[\w-]` + plain + `*+` +
	`|gi(?:` + plainBut("t") + plain + `*+)?` +
	`|g(?:` + plainBut("i") + plain + `*+)?` +
	`|` + plainBut("g") + plain + `*+` +
	`)`

var uptoAnchor = `(?:` + plain + `*+` + anchor + `)++`

// gitFree は中に git の始まりを持たない語。git のオプションとその値はこの語だけを読む（始まりを持つ語は
// その始まりから読み直せば同じ続きでヒットするので、`git -c git -c …` でも読み直しが積もらない）。
var (
	gitFree       = `(?:` + uptoAnchor + tail + `?|` + tail + `)`
	gitFreeOption = `-` + plain + `*+(?:` + uptoAnchor + tail + `?)?`
)

// gitOptions はサブコマンドの前に挟める git のグローバルオプション。値を空白で区切って取るものは値まで読み、
// それ以外は `-` で始まる語を 1 つ読む。値まで読んだ語は読み直さないので、`git commit -m push` や
// `git -c push.default=current status` の `push` はサブコマンドとして読まれない。値とのあいだの空白は 1 字だけ読む。
// 空の語 `git -C "" push` は空白 2 字になり、まとめて読むと次の `push` を値として読んで外れるためである。
// 読みを通らずに来た文字列では、空白が何字続いても語の区切りになるので、`git  -C  x  push` の `x` は値になる。
// どちらの読みでもヒットするように、空白を 1 字だけ読む形とまとめて読む形を「または」で並べる。
var gitOptions = `(?:` + gitOptionsWith(`\s`) + `|` + gitOptionsWith(`\s++`) + `)`

// gitOptionsWith は git のグローバルオプションの並び。gap は値とのあいだの空白の読み方。
func gitOptionsWith(gap string) string {
	return `(?:\s++(?:(?:-c|--(?:git-dir|work-tree|namespace|config-env|super-prefix` +
		`|attr-source))` + gap + gitFree + `?|` + gitFreeOption + `))*+`
}

// gitSub は `git <オプション> <sub>` と、git のサブコマンドを直接起こす `git-<sub>`。
func gitSub(sub string) string {
	return `(?:git(?:\.exe)?` + gitOptions + `\s++` + sub + `|git-` + sub + `(?:\.exe)?)`
}

var gitPushRegex = before + gitSub("push") + after

// resetOther は reset の後ろの語のうち、`--`（ここから後ろはパス）と `--ha`・`--har`・`--hard` でないもの。
// `--hard` は reset の後ろのどこにあってもよく、略（`--har`）も git は受け付ける。
const resetOther = `(?:(?:[^\s\x00-]|-[^\s\x00-]|--[^\s\x00h]|--h[^\s\x00a]|--ha[^\s\x00r]` +
	`|--har[^\s\x00d]|--hard[^\s\x00])[^\s\x00]*+|--h|-)`

// resetRestart は `--` の後ろ（パス）を `--hard` に読まず、同じコマンドの次の reset から読み直す。
var resetRestart = `(?:(?:` + resetOther + `\s++)*?--(?:\s++[^\s\x00]++)*?\s++` + inWord + gitSub("reset") +
	`\s\s*+)*+`

var gitResetHardRegex = first + gitSub("reset") + `\s)\s*+` + resetRestart +
	`(?:` + resetOther + `\s++)*?--ha(?:rd?)?` + wordEnd

// ---- rm
//
// 再帰と強制の両方を持つ rm。短い形はまとめても分けても、長い形（略も）でもよく、順序もオペランドとの
// 前後も問わない。片方だけは止めない。コマンドの中で最初の rm に決めて語を 1 つずつ読み、最初の選択肢の語で
// 「両方」ならヒット、「再帰」なら後ろに「強制」を、「強制」なら後ろに「再帰」を探す。
const (
	rmNeither   = `[a-eg-qs-z]`
	rmBoth      = `-` + rmNeither + `*+(?:r[a-eg-z]*+f|f[a-qs-z]*+r)[a-z]*+`
	rmRecursive = `(?:-[a-qs-z]*+r[a-z]*+|--r[a-z]*+)`
	rmForce     = `(?:-[a-eg-z]*+f[a-z]*+|--f[a-z]*+)`
	// 片方だけの選択肢。`--` の手前で読みを切り替えるときに使う（両方を持つ語はそこでヒットにする）。
	rmRecursiveOnly = `(?:-[a-eg-qs-z]*+r[a-eg-z]*+|--r[a-z]*+)`
	rmForceOnly     = `(?:-[a-eg-qs-z]*+f[a-qs-z]*+|--f[a-z]*+)`
)

var rmStart = first + `rm(?:\.exe)?\s)`

// rmOther は flags のどの字の選択肢でもない語。`-` の後ろが英字だけで flags の字を含む語、`--` の後ろが
// flags の字で始まる英字だけの語、`--` そのもの（ここから後ろはオペランド）を除く。
func rmOther(flags string) string {
	var rest strings.Builder
	for c := 'a'; c <= 'z'; c++ {
		if !strings.ContainsRune(flags, c) {
			rest.WriteRune(c)
		}
	}
	letters := `[` + rest.String() + `]`
	const other = `[^\s\x00a-z]`
	return `(?:[^\s\x00-][^\s\x00]*+` +
		`|-(?:` + letters + `++(?:` + other + `[^\s\x00]*+)?|[^\s\x00a-z-][^\s\x00]*+|)` +
		`|-` + letters + `*+[` + flags + `][a-z]*+` + other + `[^\s\x00]*+` +
		`|--(?:[^\s\x00` + flags + `][^\s\x00]*+|[` + flags + `][a-z]*+` + other + `[^\s\x00]*+)` +
		`)`
}

// rmRestart は `--` の後ろ（オペランド）を選択肢に読まず、同じコマンドの次の rm から読み直す。
// `--` までにヒットが決まる並びは、ここを通らずに下でヒットする。
var rmRestart = `(?:(?:` + rmOther("rf") + `\s++)*+` +
	`(?:` + rmRecursiveOnly + `\s++(?:` + rmOther("f") + `\s++)*+` +
	`|` + rmForceOnly + `\s++(?:` + rmOther("r") + `\s++)*+)?` +
	`--(?:\s++[^\s\x00]++)*?\s++` + inWord + `rm(?:\.exe)?\s++)*+`

var recursiveDeleteRegex = rmStart + `\s*+` + rmRestart + `(?:` + rmOther("rf") + `\s++)*+` +
	`(?:` + rmBoth + wordEnd +
	`|` + rmRecursive + `\s++(?:` + rmOther("f") + `\s++)*+` + rmForce + wordEnd +
	`|` + rmForce + `\s++(?:` + rmOther("r") + `\s++)*+` + rmRecursive + wordEnd + `)`

// credentialsRegex は認証情報の場所の形。先頭に境界（行頭か、語を作る字・`.`・`,`・`-`・引用の中の空白の目印 `\x01` でない字）を付け、
// `jq '.a,.env.X'` や `process.env` を巻き込まない。引用符・`:`・`=`・`@` のあと（`open('.env')`・`HEAD:.env`・
// `--file=.env`・`@.env`）は止める。`,` のあとは通す（`{a,.env}` はブレース展開のルールが止める）。
// `.env` の後ろに続く `.envrc`・`.env.local`・`.env.example` は止める。キーのファイルは、拡張子として
// 書かれた形（`x.pem`・`x.pem.bak`・`x.key.json`）で止める。
const credentialsRegex = `(^|[^\w.,\x01-])\.(env|dev\.vars|netrc|npmrc|pypirc)` +
	`|(^|[^\w.,\x01-])\.(aws|ssh)[\\/]` +
	`|[\w\-\\/]\.(pem|key)\b` +
	`|[\\/]secrets[\\/]` +
	`|[\\/]credentials([\\/]|\.json\b)` +
	`|\b(id_rsa|id_ed25519)\b`
