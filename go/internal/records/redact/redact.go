package redact

import (
	"regexp"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

	"ccnavi/internal/infra/ospath"
)

// 伏せ方。短い値（short 字未満）は全部を Stars にし、長い値は頭と尻を残す。残すのは、同じ値が
// 繰り返し出ているか、どの種類のトークンかを記録から見分けるため。短い値で残すと値の大半が読める。
const (
	short    = 18
	keepHead = 6
	keepTail = 4
	// Stars は伏せた部分に置く字。
	Stars = "***"
)

// Python の re の `\s`（str.isspace と同じ集合）。RE2 の `\s` は ASCII だけなので、字の集合で書く。
const spaceSet = `\t\n\v\f\r\x1c-\x1f \x{85}\x{a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}`

const (
	sp    = `[` + spaceSet + `]`
	notSp = `[^` + spaceSet + `]`
	// Python の IGNORECASE は英字の i に İ（U+0130）と ı（U+0131）も当てる。RE2 の (?i) は当てないので足す。
	// ſ（U+017F）と K（U+212A）は RE2 も当てる。
	dottedI = `ı\x{130}`
	// 引用の外で、値の終わりとして読む字。`\x00` は unwrapped がコマンドをつなぐ目印。
	bareBody = `[^` + spaceSet + `'"&;|,)}<>` + "`" + `\x00]+`
	// 値。引用されていれば閉じ引用まで、されていなければ区切りの字の手前まで。
	value = `(?:'(?P<sq>[^'\n]*)'|"(?P<dq>[^"\n]*)"|(?P<bare>` + bareBody + `))`
	// 名前が秘密を指す `名前=値` の名前の終わり（GITHUB_TOKEN・db_password・MYSQL_PWD など）。途中に含むだけの
	// 名前（tokenizer）は読まない。pwd は前に _ か - が付いた形だけ（単体の PWD はいまの場所）。
	keyWords = `token|password|passwd|[_-]pwd|secret|secret[_-]?key|api[_-]?key|apikey|access[_-]?key|` +
		`private[_-]?key|client[_-]?secret|auth[_-]?token|credentials?|cookie`
	flagWords = `password|passwd|token|secret|api-key|apikey|access-token|auth-token`
	// 名前の頭の長さの上限。限らないと `-` が続くだけの文字列で手間が長さの 2 乗で増え、実行前チェックの
	// 期限を過ぎる（Python で 1.5 万字 10 秒）。これより長い名前は取りこぼす。
	nameHead = `{0,64}`
	// コマンドの範囲は、名前から次のコマンドの切れ目の手前まで。
	segmentEnd = "\n;|&\x00"
)

// iword は IGNORECASE の語の i に dottedI を足す。
func iword(words string) string {
	return strings.ReplaceAll(words, "i", `[i`+dottedI+`]`)
}

func mustRE(pattern string) *regexp.Regexp { return regexp.MustCompile(pattern) }

// form は Python の 1 つの正規表現。start で始まりの候補を探し、before が通れば whole をその位置に
// 固定して照らし、after が通れば当たりとする。before が偽なら skip（nil なら 1 字先）から探し直す。
type form struct {
	start  *regexp.Regexp
	whole  *regexp.Regexp
	before func(text string, at int) bool
	after  func(text string, end int) bool
	skip   func(text string, at int) int
}

// segment はコマンドを限って読むフラグ。`-p` や `-u` はほかのコマンドでは別の意味なので
// （mkdir -p・ssh -p 22・sort -u）、head のコマンドの中でだけ option を読む。
type segment struct {
	head   *regexp.Regexp
	option form
}

func prevRune(text string, at int) (rune, bool) {
	if at <= 0 {
		return 0, false
	}
	r, _ := utf8.DecodeLastRuneInString(text[:at])
	return r, true
}

func nextRune(text string, at int) (rune, bool) {
	if at >= len(text) {
		return 0, false
	}
	r, _ := utf8.DecodeRuneInString(text[at:])
	return r, true
}

// isWord は Python の re の `\w`（str.isalnum か "_"）。
func isWord(r rune) bool { return r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r) }

func isASCIIAlnum(r rune) bool {
	return r < utf8.RuneSelf && (r >= '0' && r <= '9' || r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z')
}

// foldsToASCII は IGNORECASE で英字に当たる ASCII の外の字。
func foldsToASCII(r rune) bool { return r == 0x130 || r == 0x131 || r == 0x17f || r == 0x212a }

// isNameRune は IGNORECASE の `[A-Za-z0-9_.-]`。
func isNameRune(r rune) bool {
	return isASCIIAlnum(r) || r == '_' || r == '.' || r == '-' || foldsToASCII(r)
}

// isFlagRune は IGNORECASE の `[A-Za-z0-9-]`。
func isFlagRune(r rune) bool { return isASCIIAlnum(r) || r == '-' || foldsToASCII(r) }

// wordStart は `\b`（始まりの字は語の字）。前が語の字でない。
func wordStart(text string, at int) bool {
	r, ok := prevRune(text, at)
	return !ok || !isWord(r)
}

// wordEnd は終わりの `\b`（終わりの字は語の字）。後ろが語の字でない。
func wordEnd(text string, end int) bool {
	r, ok := nextRune(text, end)
	return !ok || !isWord(r)
}

// afterSpace は `(?<=\s)`。
func afterSpace(text string, at int) bool {
	r, ok := prevRune(text, at)
	return ok && ospath.IsSpace(r)
}

func notAfter(in func(rune) bool) func(string, int) bool {
	return func(text string, at int) bool {
		r, ok := prevRune(text, at)
		return !ok || !in(r)
	}
}

// pastRun は at から in の字が続く間を飛ばした位置。続きの中の位置は、前の字で後読みに落ちる。
func pastRun(in func(rune) bool) func(string, int) int {
	return func(text string, at int) int {
		for at < len(text) {
			r, size := utf8.DecodeRuneInString(text[at:])
			if !in(r) {
				break
			}
			at += size
		}
		return at
	}
}

var forms = []form{
	// 秘密キーの本文。見出しと結びの行は残す。結びが無ければ終わりまで（Python の `$` は最後の改行の手前）。
	{
		start: mustRE(`-----BEGIN [A-Z ]*PRIVATE KEY-----`),
		whole: mustRE(`^(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----(?P<bare>.*?)(?:-----END [A-Z ]*PRIVATE KEY-----|\n?\z)`),
	},
	// 形そのものがトークンだと言えるもの。
	{
		start: mustRE(`AKIA|ASIA|gh[pousr]_|github_pat_|glpat-|xox[abprs]-|sk-|[sr]k_(?:live|test)_|AIza|npm_|eyJ`),
		whole: mustRE(`^(?P<bare>` +
			`(?:AKIA|ASIA)[0-9A-Z]{16}` + // AWS のアクセスキー ID
			`|gh[pousr]_[A-Za-z0-9]{20,}` + // GitHub の各種トークン
			`|github_pat_[A-Za-z0-9_]{20,}` + // GitHub の fine-grained トークン
			`|glpat-[A-Za-z0-9_-]{20,}` + // GitLab の個人アクセストークン
			`|xox[abprs]-[A-Za-z0-9-]{10,}` + // Slack
			`|sk-[A-Za-z0-9_-]{20,}` + // OpenAI・Anthropic の API キー
			`|[sr]k_(?:live|test)_[A-Za-z0-9]{10,}` + // Stripe
			`|AIza[0-9A-Za-z_-]{30,}` + // Google の API キー
			`|npm_[A-Za-z0-9]{30,}` + // npm
			`|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}` + // JWT
			`)`),
		before: notAfter(isASCIIAlnum),
		after: func(text string, end int) bool {
			r, ok := nextRune(text, end)
			return !ok || !isASCIIAlnum(r)
		},
	},
	// HTTP の Authorization ヘッダの値。方式の語（Bearer・Basic など）は残す。
	{
		start:  mustRE(`(?i)(?:proxy-)?` + iword(`authorization`) + sp + `*:`),
		whole:  mustRE(`^(?i)(?:proxy-)?` + iword(`authorization`) + sp + `*:` + sp + `*(?:[A-Za-z` + dottedI + `]+` + sp + `+)?(?P<bare>` + bareBody + `)`),
		before: wordStart,
	},
	{
		start:  mustRE(`(?i)bearer` + sp + `+`),
		whole:  mustRE(`^(?i)bearer` + sp + `+(?P<bare>[A-Za-z0-9._~+/=` + dottedI + `-]+)`),
		before: wordStart,
	},
	// URL に埋めた資格情報。値に `@` が入ることがあるので、ホストの手前の最後の `@` までを伏せる
	// （ccnavi-common.sh の ccnavi_mask_url と同じ読み）。空白と `/` はまたがない。
	{
		start: mustRE(`://`),
		whole: mustRE(`^://[^/` + spaceSet + `:@'"\x00]+:(?P<bare>[^` + spaceSet + `/'"\x00]+)@`),
	},
	// HTTP の Cookie ヘッダの値。`;` で並ぶ値を全部、引用か行の終わりまで伏せる。
	{
		start:  mustRE(`(?i)(?:set-)?` + iword(`cookie`) + sp + `*:`),
		whole:  mustRE(`^(?i)(?:set-)?` + iword(`cookie`) + sp + `*:` + sp + `*(?P<bare>[^'"\n\x00]+)`),
		before: wordStart,
	},
	// 名前が秘密を指す `名前=値` / `名前: 値`。名前の途中からは始めない。
	{
		start:  mustRE(`(?i)[A-Za-z0-9_.` + dottedI + `-]*(?:` + iword(keyWords) + `)["']?` + sp + `*[=:]`),
		whole:  mustRE(`^(?i)[A-Za-z0-9_.` + dottedI + `-]` + nameHead + `(?:` + iword(keyWords) + `)["']?` + sp + `*[=:]` + sp + `*` + value),
		before: notAfter(isNameRune),
		skip:   pastRun(isNameRune),
	},
	// 空白で値を渡すフラグ（`--password xxx`）。`=` の形は上の名前の形が読む。
	{
		start:  mustRE(`(?i)--(?:` + iword(flagWords) + `)` + sp + `+`),
		whole:  mustRE(`^(?i)--(?:` + iword(flagWords) + `)` + sp + `+` + value),
		before: notAfter(isFlagRune),
	},
}

var segments = []segment{
	// mysql 系の `-p<パスワード>`（空白を挟まない形だけがパスワードになる）。
	{
		head:   mustRE(`(?:mysql|mariadb)[\p{L}\p{N}_]*`),
		option: form{start: mustRE(`-p`), whole: mustRE(`^-p` + value), before: afterSpace},
	},
	// curl の `-u user:<パスワード>` / `--user user:<パスワード>`。
	{
		head: mustRE(`curl`),
		option: form{
			start:  mustRE(`-u|--user`),
			whole:  mustRE(`^(?:-u|--user)[` + spaceSet + `=]*['"]?[^` + spaceSet + `:'"\x00]+:(?P<bare>[^` + spaceSet + `'"\x00]+)`),
			before: afterSpace,
		},
	},
	// curl の `-b <クッキー>` / `--cookie <クッキー>`。
	{
		head:   mustRE(`curl`),
		option: form{start: mustRE(`-b|--cookie`), whole: mustRE(`^(?:-b|--cookie)[` + spaceSet + `=]+` + value), before: afterSpace},
	},
	// sshpass の `-p <パスワード>`。見るのは sshpass 自身のオプションだけで、後ろのコマンド（ssh -p 22）は読まない。
	{
		head: mustRE(`sshpass`),
		option: form{
			start:  mustRE(`sshpass`),
			whole:  mustRE(`^sshpass(?:` + sp + `+-[^p` + spaceSet + `]` + notSp + `*)*` + sp + `+-p` + sp + `*` + value),
			before: func(_ string, at int) bool { return at == 0 },
		},
	},
	// docker login の `-p <パスワード>`（--password は上のフラグの形が読む）。
	{
		head:   mustRE(`docker` + sp + `+login`),
		option: form{start: mustRE(`-p`), whole: mustRE(`^-p` + sp + `*` + value), before: afterSpace},
	},
	// redis-cli の `-a <パスワード>` / `--pass <パスワード>`。
	{
		head:   mustRE(`redis-cli`),
		option: form{start: mustRE(`-a|--pass`), whole: mustRE(`^(?:-a|--pass)` + sp + `+` + value), before: afterSpace},
	},
	// `aws configure set aws_secret_access_key <値>`（名前と値を空白で分ける形）。
	{
		head: mustRE(`aws` + sp + `+configure`),
		option: form{
			start:  mustRE(`aws_secret_access_key|aws_session_token`),
			whole:  mustRE(`^(?:aws_secret_access_key|aws_session_token)` + sp + `+` + value),
			before: afterSpace,
		},
	},
}

// valueGroups は値の範囲を読むグループ。書いた順に、先に当たったものを採る。
var valueGroups = []string{"sq", "dq", "bare"}

type span struct{ start, end int }

// Redact は秘密の形をした部分を伏せた文字列を返す。見つからなければそのまま返す。
func Redact(text string) string {
	if text == "" {
		return text
	}
	found := spans(text)
	if len(found) == 0 {
		return text
	}
	var b strings.Builder
	last := 0
	for _, s := range found {
		b.WriteString(text[last:s.start])
		b.WriteString(Mask(text[s.start:s.end]))
		last = s.end
	}
	b.WriteString(text[last:])
	return b.String()
}

// Mask は値 1 つを伏せる。短ければ全部、長ければ頭と尻を残す。
func Mask(value string) string {
	runes := []rune(value)
	if len(runes) < short {
		return Stars
	}
	return string(runes[:keepHead]) + Stars + string(runes[len(runes)-keepTail:])
}

// spans は伏せる範囲を、重なりをまとめて前から並べる。同じ値に 2 つの形が当たる
// （GITHUB_TOKEN=ghp_… は名前でもトークンの形でも当たる）ので、先に集めて 1 度だけ伏せる。
// 形ごとに順に置き換えると、伏せた後を次の形がもう 1 度伏せ、残した頭と尻まで消える。
func spans(text string) []span {
	var found []span
	for _, f := range forms {
		found = f.spans(text, 0, found)
	}
	for _, seg := range segments {
		found = seg.spans(text, found)
	}
	sort.Slice(found, func(i, j int) bool {
		if found[i].start != found[j].start {
			return found[i].start < found[j].start
		}
		return found[i].end < found[j].end
	})
	var merged []span
	for _, s := range found {
		if n := len(merged); n > 0 && s.start <= merged[n-1].end {
			merged[n-1].end = max(merged[n-1].end, s.end)
			continue
		}
		merged = append(merged, s)
	}
	return merged
}

// spans は Python の finditer と同じ順で当たりを探し、値の範囲（offset を足したもの）を out に足す。
// 変数を指すだけの値と空の値は外す。
func (f form) spans(text string, offset int, out []span) []span {
	for s := 0; s <= len(text); {
		loc := f.start.FindStringIndex(text[s:])
		if loc == nil {
			break
		}
		at := s + loc[0]
		if f.before != nil && !f.before(text, at) {
			if f.skip != nil {
				s = max(f.skip(text, at), at+1)
			} else {
				s = at + runeSize(text, at)
			}
			continue
		}
		m := f.whole.FindStringSubmatchIndex(text[at:])
		if m == nil || m[1] == 0 || (f.after != nil && !f.after(text, at+m[1])) {
			s = at + runeSize(text, at)
			continue
		}
		if v, ok := f.value(text, at, m); ok {
			out = append(out, span{v.start + offset, v.end + offset})
		}
		s = at + m[1]
	}
	return out
}

func (f form) value(text string, at int, m []int) (span, bool) {
	for _, name := range valueGroups {
		i := f.whole.SubexpIndex(name)
		if i < 0 || m[2*i] < 0 {
			continue
		}
		v := span{at + m[2*i], at + m[2*i+1]}
		got := text[v.start:v.end]
		if ospath.Strip(got) == "" || strings.HasPrefix(got, "$") {
			return span{}, false
		}
		return v, true
	}
	return span{}, false
}

func (seg segment) spans(text string, out []span) []span {
	for s := 0; s < len(text); {
		loc := seg.head.FindStringIndex(text[s:])
		if loc == nil {
			break
		}
		at, headEnd := s+loc[0], s+loc[1]
		if !wordStart(text, at) || !wordEnd(text, headEnd) {
			s = at + runeSize(text, at)
			continue
		}
		end := len(text)
		if i := strings.IndexAny(text[headEnd:], segmentEnd); i >= 0 {
			end = headEnd + i
		}
		out = seg.option.spans(text[at:end], at, out)
		s = end
	}
	return out
}

func runeSize(text string, at int) int {
	if at >= len(text) {
		return 1
	}
	_, size := utf8.DecodeRuneInString(text[at:])
	return size
}
