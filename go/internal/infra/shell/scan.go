package shell

import (
	"regexp"
	"strings"
	"unicode/utf8"
)

// 走査は shlex に渡す前に原文を 1 回通し、引用の状態を自分で持ったまま、シェルが実行するのに shlex が
// 見ないもの（コマンド置換・プロセス置換・ヒアドキュメントの本文・コメント・改行）を片付ける。
// 読み違えると止まらずに通る形は、読み解かずに理由を付けて止める。

// シェルによって読みが分かれる形の表記。書き直しの文面に並べる。
const (
	formCaseInSubst       = "case inside $( )"
	formArithmeticOrSubst = "$((…) …)"
	formTooDeep           = "$( ) nested more than 16 deep"
)

// maxScanFrames は走査の入れ子の上限。Python の実装は再帰の上限（1000 フレーム）に当たると深すぎる
// 入れ子として縮退していたので、同じ深さで止める。値は Python で測った境目に合わせてある。
const maxScanFrames = 995

// placeholder は切り出した中身の代わりに外側へ残す 1 文字。外側の語の数と形を保つ
// （`> "$(pwd)/x"` は `> $/x` になり、リダイレクト先の後半が語として残る）。
const placeholder = "$"

const (
	// commandStop は引用の外で走査が止まって調べる文字。ほかはまとめて飛ばす（1 文字ずつ見ると、
	// 大きなヒアドキュメントで判定の期限に影響する）。
	commandStop = "\\'\"`$#\n<>()"
	doubleStop  = "\\`$\""
	bodyStop    = "\\`$"
	// wordEnd は語の終わりになる文字（コマンドの文脈）。
	wordEnd = " \t\r\n;&|()<>"
	// beforeWord の直後は語の始まり。`#` がコメントになるのも `case` が予約語になるのもここだけ。
	beforeWord = " \t\r;&|()<>"
	// braceBreak が引用の外に出たら語が終わる。生の CR は語の中の文字（bash は `{git,<CR>push}` も広げる）。
	braceBreak = " \t;&|"
)

// sequence はどちらかのシェルが広げる範囲のブレース（数か 1 文字の範囲、刻みが付いてもよい）。
// Python の \d は Unicode の数字なので \p{Nd} にする。
var sequence = regexp.MustCompile(`^(?:(-?\p{Nd}+\.\.-?\p{Nd}+|.\.\..)(\.\.-?\p{Nd}+)?)$`)

// unreadable は走査が読みを止めた理由。form は書き直しを求める形のときの表記。
type unreadable struct {
	reason Reason
	form   string
}

func (e *unreadable) Error() string { return string(e.reason) }

func stop(reason Reason, form string) error { return &unreadable{reason, form} }

type substitution struct {
	body   string
	quoted bool
}

type heredoc struct {
	delim  string
	quoted bool
}

type scanned struct {
	// outer は shlex に渡す文字列。full は同じ語の数で、置換を全文で書き戻した形。
	outer, full string
	found       []substitution
	// heads は引用の外でヒアドキュメントの始まりと読んだ `<<` の数。
	heads  int
	braces []string
}

type scanner struct {
	s         string
	i         int
	out, full strings.Builder
	found     []substitution
	pending   []heredoc
	heads     int
	braces    []string
	frames    *int
}

// scan は原文を走査する。読みを止めたら *unreadable を返す。
func scan(src string) (scanned, error) {
	frames := 0
	x := &scanner{s: src, frames: &frames}
	if err := x.command(true, false); err != nil {
		return scanned{}, err
	}
	if len(x.pending) > 0 {
		// 本文が始まらないまま終わったヒアドキュメント（`cat <<'EOF' > f` だけ）。
		return scanned{}, stop(ReasonUnterminated, "")
	}
	return scanned{x.out.String(), x.full.String(), x.found, x.heads, x.braces}, nil
}

func (x *scanner) enter() error {
	*x.frames++
	if *x.frames > maxScanFrames {
		return stop(ReasonAmbiguousSubst, formTooDeep)
	}
	return nil
}

func (x *scanner) leave() { *x.frames-- }

func (x *scanner) emit(text string, collect bool) {
	if collect {
		x.out.WriteString(text)
		x.full.WriteString(text)
	}
}

// escaped は位置 i の `\` と次の 1 文字。
func (x *scanner) escaped() string {
	_, size := utf8.DecodeRuneInString(x.s[min(x.i+1, len(x.s)):])
	return x.s[x.i:min(x.i+1+size, len(x.s))]
}

// fold は引用の中の `>` と、`<<` の一部でない `<` を身代わりの文字にする。
func fold(text string) string {
	text = strings.ReplaceAll(text, ">", quotedGT)
	if !strings.Contains(text, "<") {
		return text
	}
	var b strings.Builder
	for i := 0; i < len(text); i++ {
		if text[i] == '<' && (i == 0 || text[i-1] != '<') && (i+1 == len(text) || text[i+1] != '<') {
			b.WriteString(quotedLT)
		} else {
			b.WriteByte(text[i])
		}
	}
	return b.String()
}

// command は引用の外を読む。closing なら `$( )` の中で、対になる `)` の手前で戻る。
func (x *scanner) command(collect, closing bool) error {
	if err := x.enter(); err != nil {
		return err
	}
	defer x.leave()
	s := x.s
	depth := 0
	wordStart := true
	// 閉じる位置を探すだけの走査では並べない。中身は read が読み直す。
	braces := &braceFinder{x: x, record: collect}
	for x.i < len(s) {
		end := len(s)
		if k := strings.IndexAny(s[x.i:], commandStop); k >= 0 {
			end = x.i + k
		}
		if end > x.i {
			chunk := s[x.i:end]
			if closing {
				if err := x.refuseCase(chunk, wordStart); err != nil {
					return err
				}
			}
			braces.text(chunk, x.i)
			x.emit(chunk, collect)
			wordStart = strings.IndexByte(beforeWord, chunk[len(chunk)-1]) >= 0
			x.i = end
			continue
		}
		c := s[x.i]
		switch {
		case c == '\\':
			braces.opaque()
			e := x.escaped()
			x.emit(e, collect)
			x.i += len(e)
			wordStart = false
			continue
		case c == '\'':
			braces.opaque()
			if err := x.single(collect); err != nil {
				return err
			}
			wordStart = false
			continue
		case c == '"':
			braces.opaque()
			if err := x.double(collect); err != nil {
				return err
			}
			wordStart = false
			continue
		case c == '`':
			return x.backquote()
		case c == '$':
			ok, err := x.dollar(collect, false)
			if err != nil {
				return err
			}
			if ok {
				braces.opaque()
				wordStart = false
				continue
			}
		case c == '#' && wordStart:
			// コメントは行末まで。shlex に任せると語の途中の `#` からも消える（`echo a#b; git push`）。
			if j := strings.IndexByte(s[x.i:], '\n'); j >= 0 {
				x.i += j
			} else {
				x.i = len(s)
			}
			continue
		case c == '\n':
			// 引用の外の改行はコマンドの区切り。shlex は空白と読み、2 行目が 1 行目の引数になる。
			braces.reset()
			x.emit(" ; ", collect)
			x.i++
			wordStart = true
			if err := x.heredocBodies(collect); err != nil {
				return err
			}
			continue
		case strings.HasPrefix(s[x.i:], "<<<"):
			// here-string は本文を持たないので、演算子のまま渡す。
			braces.reset()
			x.emit("<<<", collect)
			x.i += 3
			wordStart = true
			continue
		case strings.HasPrefix(s[x.i:], "<<"):
			braces.reset()
			if err := x.heredocHead(collect); err != nil {
				return err
			}
			wordStart = true
			continue
		case (c == '<' || c == '>') && strings.HasPrefix(s[x.i+1:], "("):
			// プロセス置換。中身は $( ) と同じく独立したコマンドとして走る。
			braces.reset()
			if err := x.substitution(collect, false); err != nil {
				return err
			}
			wordStart = false
			continue
		}
		if closing {
			if c == '(' {
				depth++
			} else if c == ')' {
				if depth == 0 {
					braces.reset()
					return nil
				}
				depth--
			}
		}
		if strings.IndexByte("<>()", c) >= 0 {
			braces.reset()
		} else {
			braces.text(s[x.i:x.i+1], x.i)
		}
		x.emit(s[x.i:x.i+1], collect)
		x.i++
		wordStart = strings.IndexByte(beforeWord, c) >= 0
	}
	braces.reset()
	if closing {
		return stop(ReasonUnterminatedSubst, "")
	}
	return nil
}

// refuseCase は `$( )` の中の予約語 `case` で読みを止める。case の `)` を置換の終わりと読むか
// case の一部と読むかがシェルで分かれる（bash 3.2 と zsh）。
func (x *scanner) refuseCase(chunk string, wordStart bool) error {
	for p := strings.Index(chunk, "case"); p >= 0; {
		before := wordStart
		if p > 0 {
			before = strings.IndexByte(beforeWord, chunk[p-1]) >= 0
		}
		at := x.i + p + 4
		if before && (at >= len(x.s) || strings.IndexByte(wordEnd, x.s[at]) >= 0) {
			return stop(ReasonAmbiguousSubst, formCaseInSubst)
		}
		q := strings.Index(chunk[p+1:], "case")
		if q < 0 {
			break
		}
		p += 1 + q
	}
	return nil
}

// single は単一引用。中は全部文字。文字として書く方法は必ずここに残す。
func (x *scanner) single(collect bool) error {
	j := strings.IndexByte(x.s[x.i+1:], '\'')
	if j < 0 {
		return stop(ReasonUnterminated, "")
	}
	j += x.i + 1
	x.emit(fold(x.s[x.i:j+1]), collect)
	x.i = j + 1
	return nil
}

// ansiC は `$'…'`。`\` が無ければ単一引用と同じに読む（`$` を残すと `git $'push'` がどのルールにも
// ヒットしない）。`\` があればエスケープを展開しないと中身が決まらないので止める。
func (x *scanner) ansiC(collect bool) error {
	s := x.s
	for j := x.i + 2; j < len(s); j++ {
		switch s[j] {
		case '\\':
			return stop(ReasonAnsiC, "")
		case '\'':
			x.emit(fold("'"+s[x.i+2:j+1]), collect)
			x.i = j + 1
			return nil
		}
	}
	return stop(ReasonUnterminated, "")
}

func (x *scanner) double(collect bool) error {
	if err := x.enter(); err != nil {
		return err
	}
	defer x.leave()
	x.emit(`"`, collect)
	x.i++
	return x.quotedText(collect, '"')
}

// quotedText は二重引用の中か、区切りを引用しないヒアドキュメントの本文（terminator が 0）。
// どちらもシェルが見るのは `$` とバッククォートと `\` だけ。
func (x *scanner) quotedText(collect bool, terminator byte) error {
	if err := x.enter(); err != nil {
		return err
	}
	defer x.leave()
	s := x.s
	stops := bodyStop
	keep := func(t string) string { return t }
	if terminator != 0 {
		stops = doubleStop
		keep = fold
	}
	for x.i < len(s) {
		end := len(s)
		if k := strings.IndexAny(s[x.i:], stops); k >= 0 {
			end = x.i + k
		}
		if end > x.i {
			x.emit(keep(s[x.i:end]), collect)
			x.i = end
			continue
		}
		c := s[x.i]
		if terminator != 0 && c == terminator {
			x.emit(s[x.i:x.i+1], collect)
			x.i++
			return nil
		}
		switch c {
		case '\\':
			e := x.escaped()
			x.emit(e, collect)
			x.i += len(e)
			continue
		case '`':
			return x.backquote()
		case '$':
			ok, err := x.dollar(collect, true)
			if err != nil {
				return err
			}
			if ok {
				continue
			}
		}
		x.emit(keep(s[x.i:x.i+1]), collect)
		x.i++
	}
	if terminator != 0 {
		return stop(ReasonUnterminated, "")
	}
	return nil
}

// dollar は `$` の後ろが置換か展開なら読み進めて真を返す。ただの `$` なら偽。
func (x *scanner) dollar(collect, quoted bool) (bool, error) {
	if err := x.enter(); err != nil {
		return false, err
	}
	defer x.leave()
	rest := x.s[x.i+1:]
	switch {
	case strings.HasPrefix(rest, "(("):
		return true, x.arithmetic(collect, quoted)
	case strings.HasPrefix(rest, "("):
		return true, x.substitution(collect, quoted)
	case strings.HasPrefix(rest, "{"):
		return true, x.brace(collect, quoted)
	case strings.HasPrefix(rest, "'") && !quoted:
		return true, x.ansiC(collect)
	}
	return false, nil
}

// substitution は `$(` `<(` `>(` の後ろから対になる `)` まで。中では引用の状態が最初からに戻る
// （`"$(echo "a)b")"` の内側の `"` は外側を閉じない）。
func (x *scanner) substitution(collect, quoted bool) error {
	if err := x.enter(); err != nil {
		return err
	}
	defer x.leave()
	x.i += 2
	start := x.i
	if err := x.command(false, true); err != nil {
		return err
	}
	body := x.s[start:x.i]
	x.i++
	if collect {
		source := x.s[start-2 : x.i]
		x.found = append(x.found, substitution{body, quoted})
		x.out.WriteString(placeholder)
		// 全文の形は外側の 1 語として書き戻す。二重引用の中なら一度閉じて引用し直す
		// （shlex は二重引用の中の `\$` と `\\` を外さないので、エスケープでは書けない）。
		if quoted {
			x.full.WriteString(`"` + quote(source) + `"`)
		} else {
			x.full.WriteString(quote(source))
		}
	}
	return nil
}

// backquote は中身を読まずに止める。中の `\` の外れ方は引用の中かどうかで変わり、入れ子は外した後を
// 読み直さないと分からない。`$( )` で必ず書き直せる。
func (x *scanner) backquote() error {
	form := "`"
	if j := strings.IndexByte(x.s[x.i+1:], '`'); j >= 0 {
		form = x.s[x.i : x.i+j+2]
	}
	return stop(ReasonBackquote, form)
}

// arithmetic は `$(( ))`。算術ではコマンドは走らないが中の置換は走る。算術の文字は残し、
// dropArithmetic が除く。
func (x *scanner) arithmetic(collect, quoted bool) error {
	if err := x.enter(); err != nil {
		return err
	}
	defer x.leave()
	s := x.s
	x.emit("$((", collect)
	x.i += 3
	depth := 0
	for x.i < len(s) {
		switch s[x.i] {
		case '\\':
			e := x.escaped()
			x.emit(e, collect)
			x.i += len(e)
			continue
		case '`':
			return x.backquote()
		case '$':
			ok, err := x.dollar(collect, quoted)
			if err != nil {
				return err
			}
			if ok {
				continue
			}
		case '(':
			depth++
		case ')':
			if depth == 0 {
				if strings.HasPrefix(s[x.i:], "))") {
					x.emit("))", collect)
					x.i += 2
					return nil
				}
				// `$((cmd) | x)`。算術かコマンド置換かが表記から決まらない。
				return stop(ReasonAmbiguousSubst, formArithmeticOrSubst)
			}
			depth--
		}
		x.emit(s[x.i:x.i+1], collect)
		x.i++
	}
	return stop(ReasonUnterminatedSubst, "")
}

// brace は `${ }`。既定値や添字の中の置換は走る。中の `{ }` は対にして数える。引用の外なら中の
// ブレース展開も並べる（bash は広げないが zsh は `${x:-{a,b}}` を広げる）。
func (x *scanner) brace(collect, quoted bool) error {
	if err := x.enter(); err != nil {
		return err
	}
	defer x.leave()
	s := x.s
	x.emit("${", collect)
	x.i += 2
	braces := &braceFinder{x: x, record: collect && !quoted}
	for x.i < len(s) {
		c := s[x.i]
		switch {
		case c == '}' && len(braces.open) == 0:
			x.emit("}", collect)
			x.i++
			return nil
		case c == '\\':
			braces.opaque()
			e := x.escaped()
			x.emit(e, collect)
			x.i += len(e)
			continue
		case c == '"':
			braces.opaque()
			if err := x.double(collect); err != nil {
				return err
			}
			continue
		case c == '\'' && !quoted:
			braces.opaque()
			if err := x.single(collect); err != nil {
				return err
			}
			continue
		case c == '`':
			return x.backquote()
		case c == '$':
			ok, err := x.dollar(collect, quoted)
			if err != nil {
				return err
			}
			if ok {
				braces.opaque()
				continue
			}
		}
		_, size := utf8.DecodeRuneInString(s[x.i:])
		braces.text(s[x.i:x.i+size], x.i)
		x.emit(s[x.i:x.i+size], collect)
		x.i += size
	}
	return stop(ReasonUnterminated, "")
}

// heredocHead は `<<` と区切りの語を読み、区切りを覚えておく。区切りのどこかが引用されていれば
// 本文は文字。されていなければ本文の置換はシェルが実行する。
func (x *scanner) heredocHead(collect bool) error {
	s := x.s
	start := x.i
	x.i += 2
	if strings.HasPrefix(s[x.i:], "-") {
		x.i++
	}
	for x.i < len(s) && (s[x.i] == ' ' || s[x.i] == '\t') {
		x.i++
	}
	var delim strings.Builder
	// parts は区切りの語を読んだか。`<<''` は空の区切りで、本文を待つ。
	parts := false
	quoted := false
	for x.i < len(s) && strings.IndexByte(wordEnd, s[x.i]) < 0 {
		parts = true
		switch c := s[x.i]; c {
		case '\'', '"':
			quoted = true
			j := strings.IndexByte(s[x.i+1:], c)
			if j < 0 {
				return stop(ReasonUnterminated, "")
			}
			delim.WriteString(s[x.i+1 : x.i+1+j])
			x.i += j + 2
		case '\\':
			quoted = true
			e := x.escaped()
			delim.WriteString(e[1:])
			x.i += len(e)
		default:
			_, size := utf8.DecodeRuneInString(s[x.i:])
			delim.WriteString(s[x.i : x.i+size])
			x.i += size
		}
	}
	x.emit(s[start:x.i], collect)
	if collect {
		x.heads++
	}
	if parts {
		x.pending = append(x.pending, heredoc{delim.String(), quoted})
	}
	return nil
}

// heredocBodies は改行の直後に、待っている本文と終端の行を順に読んで除く。本文はプログラムに渡す
// 文字で、実行位置として数えると禁止語を引用した文書を書けなくなる。
//
// 終端の行は前後の空白を除いて比べる。シェル（完全一致）より早く閉じることはあっても遅くは閉じない。
// 早く閉じれば残りをコマンドとして読む（厳しい側）。遅く閉じると後ろのコマンドを本文として捨てる。
func (x *scanner) heredocBodies(collect bool) error {
	if err := x.enter(); err != nil {
		return err
	}
	defer x.leave()
	s := x.s
	for len(x.pending) > 0 {
		h := x.pending[0]
		x.pending = x.pending[1:]
		start := x.i
		for {
			if x.i >= len(s) {
				return stop(ReasonUnterminated, "")
			}
			end := len(s)
			next := len(s)
			if j := strings.IndexByte(s[x.i:], '\n'); j >= 0 {
				end = x.i + j
				next = end + 1
			}
			if strip(s[x.i:end]) == h.delim {
				if !h.quoted {
					if err := x.body(s[start:x.i], collect); err != nil {
						return err
					}
				}
				x.i = next
				break
			}
			x.i = next
		}
	}
	return nil
}

// body は区切りを引用しない本文から、置換の中身だけを拾う。
func (x *scanner) body(text string, collect bool) error {
	if err := x.enter(); err != nil {
		return err
	}
	defer x.leave()
	if !collect {
		return nil
	}
	inner := &scanner{s: text, frames: x.frames}
	if err := inner.quotedText(true, 0); err != nil {
		return err
	}
	for _, f := range inner.found {
		x.found = append(x.found, substitution{f.body, true})
	}
	return nil
}

// ---- ブレース展開

type openBrace struct {
	start int
	comma bool
	// inner は中身の文字。引用・エスケープ・置換・入れ子が入ったら opaque で、範囲の端にならない。
	inner  strings.Builder
	opaque bool
	found  []string
}

// braceFinder は 1 つの文脈（引用の外のコマンド、`${ }` の中）でブレース展開を探す。引用・エスケープ・
// 置換の中身はここに届かないので、そこの `,` `}` は数えない（`{"a,b"}` はシェルでも広がらない）。
type braceFinder struct {
	x      *scanner
	record bool
	open   []*openBrace
}

// text は引用の外の文字。at は chunk の先頭の、原文での位置。
func (b *braceFinder) text(chunk string, at int) {
	if len(b.open) == 0 && !strings.Contains(chunk, "{") {
		return
	}
	for k, c := range chunk {
		switch {
		case strings.ContainsRune(braceBreak, c):
			b.reset()
		case c == '{':
			b.opaque()
			b.open = append(b.open, &openBrace{start: at + k})
		case len(b.open) == 0:
		case c == ',':
			b.open[len(b.open)-1].comma = true
		case c == '}':
			b.close(at + k + 1)
		default:
			if top := b.open[len(b.open)-1]; !top.opaque {
				top.inner.WriteRune(c)
			}
		}
	}
}

// opaque は引用・エスケープ・置換が語に入ったこと。
func (b *braceFinder) opaque() {
	if len(b.open) > 0 {
		b.open[len(b.open)-1].opaque = true
	}
}

func (b *braceFinder) close(end int) {
	brace := b.open[len(b.open)-1]
	b.open = b.open[:len(b.open)-1]
	found := brace.found
	if brace.comma || (!brace.opaque && sequence.MatchString(brace.inner.String())) {
		// 入れ子は外側の表記だけを並べる。直す先は 1 つ。
		found = []string{b.x.s[brace.start:end]}
	}
	b.keep(found)
}

// reset は語の終わり。閉じなかったブレースの中の展開はシェルでも広がる（`{x{a,b}`）。
func (b *braceFinder) reset() {
	for len(b.open) > 0 {
		brace := b.open[len(b.open)-1]
		b.open = b.open[:len(b.open)-1]
		b.keep(brace.found)
	}
}

func (b *braceFinder) keep(found []string) {
	if len(b.open) > 0 {
		top := b.open[len(b.open)-1]
		top.found = append(top.found, found...)
	} else if b.record {
		b.x.braces = append(b.x.braces, found...)
	}
}
