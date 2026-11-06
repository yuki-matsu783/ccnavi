package yaml

import (
	"fmt"
	"strconv"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/infra/pyfmt"
)

// 文字の組。PyYAML の走査器がそのまま使う並び（"\x00" は文書の終わりの印）。
const (
	breakz       = "\x00\r\n\u0085\u2028\u2029"
	breaks       = "\r\n\u0085\u2028\u2029"
	blankz       = "\x00 \t\r\n\u0085\u2028\u2029"
	spaceOrBreak = "\x00 \r\n\u0085\u2028\u2029"
	hexDigits    = "0123456789ABCDEFabcdef"
	uriChars     = "-;/?:@&=+$,_.!~*'()[]%"
	// plainStart は、クォートしない値の先頭に来られない文字（次の文字次第で来られる - ? : を含む）。
	plainStart = "\x00 \t\r\n\u0085\u2028\u2029-?:,[]{}#&*!|>'\"%@`"
	// simpleKeyLimit は、":" を待つ単純なキーの長さの上限（文字数）。
	simpleKeyLimit = 1024
)

func in(r rune, set string) bool { return strings.ContainsRune(set, r) }

func isWordChar(r rune) bool {
	return r >= '0' && r <= '9' || r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || r == '-' || r == '_'
}

type tokenKind int

const (
	tDirective tokenKind = iota
	tDocumentStart
	tDocumentEnd
	tStreamStart
	tStreamEnd
	tBlockSequenceStart
	tBlockMappingStart
	tBlockEnd
	tFlowSequenceStart
	tFlowMappingStart
	tFlowSequenceEnd
	tFlowMappingEnd
	tKey
	tValue
	tBlockEntry
	tFlowEntry
	tAlias
	tAnchor
	tTag
	tScalar
)

// tokenIDs は誤りの文に出る字句の名前（PyYAML の Token.id）。
var tokenIDs = [...]string{
	"<directive>", "<document start>", "<document end>", "<stream start>", "<stream end>",
	"<block sequence start>", "<block mapping start>", "<block end>", "[", "{", "]", "}",
	"?", ":", "-", ",", "<alias>", "<anchor>", "<tag>", "<scalar>",
}

type token struct {
	kind       tokenKind
	start, end Mark
	value      string // 別名・アンカー・スカラーの中身
	plain      bool
	style      rune
	// 指示子
	name         string
	major, minor int
	handle       string // %TAG のハンドル、タグのハンドル（hasHandle が偽なら無し）
	prefix       string // %TAG の接頭辞、タグの接尾辞
	hasHandle    bool
}

type simpleKey struct {
	level       int
	tokenNumber int
	required    bool
	index, line int
	column      int
	mark        Mark
}

type scanner struct {
	buf          []rune
	ptr          int
	line, column int

	done           bool
	flowLevel      int
	tokens         []*token
	tokensTaken    int
	indent         int
	indents        []int
	allowSimpleKey bool
	possible       []simpleKey // 入れた順を保つ（PyYAML の dict と同じ順で古くなったかを見る）
}

func newScanner(buf []rune) *scanner {
	s := &scanner{buf: buf, indent: -1, allowSimpleKey: true}
	m := s.mark()
	s.tokens = append(s.tokens, &token{kind: tStreamStart, start: m, end: m})
	return s
}

func (s *scanner) peek(i int) rune {
	if s.ptr+i < len(s.buf) {
		return s.buf[s.ptr+i]
	}
	return 0
}

func (s *scanner) prefix(n int) string {
	end := min(s.ptr+n, len(s.buf))
	return string(s.buf[s.ptr:end])
}

func (s *scanner) forward(n int) {
	for ; n > 0; n-- {
		ch := s.buf[s.ptr]
		s.ptr++
		if in(ch, "\n\u0085\u2028\u2029") || ch == '\r' && s.buf[s.ptr] != '\n' {
			s.line++
			s.column = 0
		} else if ch != '\ufeff' {
			s.column++
		}
	}
}

func (s *scanner) mark() Mark {
	return Mark{Line: s.line, Column: s.column, buffer: s.buf, pointer: s.ptr}
}

func (s *scanner) fail(context string, contextMark *Mark, problem string) error {
	return markedError(context, contextMark, problem, markPtr(s.mark()))
}

func repr(r rune) string { return pyfmt.Quote(string(r)) }

// checkToken は次の字句がどれかの種類か。字句が無ければ偽。
func (s *scanner) checkToken(kinds ...tokenKind) (bool, error) {
	if err := s.fill(); err != nil {
		return false, err
	}
	if len(s.tokens) == 0 {
		return false, nil
	}
	if len(kinds) == 0 {
		return true, nil
	}
	for _, k := range kinds {
		if s.tokens[0].kind == k {
			return true, nil
		}
	}
	return false, nil
}

func (s *scanner) peekToken() (*token, error) {
	if err := s.fill(); err != nil {
		return nil, err
	}
	if len(s.tokens) == 0 {
		return nil, nil
	}
	return s.tokens[0], nil
}

func (s *scanner) getToken() (*token, error) {
	if err := s.fill(); err != nil {
		return nil, err
	}
	if len(s.tokens) == 0 {
		return nil, nil
	}
	s.tokensTaken++
	t := s.tokens[0]
	s.tokens = s.tokens[1:]
	return t, nil
}

func (s *scanner) fill() error {
	for {
		more, err := s.needMoreTokens()
		if err != nil || !more {
			return err
		}
		if err := s.fetchMoreTokens(); err != nil {
			return err
		}
	}
}

func (s *scanner) needMoreTokens() (bool, error) {
	if s.done {
		return false, nil
	}
	if len(s.tokens) == 0 {
		return true, nil
	}
	if err := s.staleSimpleKeys(); err != nil {
		return false, err
	}
	n, ok := s.nextPossibleSimpleKey()
	return ok && n == s.tokensTaken, nil
}

func (s *scanner) fetchMoreTokens() error {
	if err := s.scanToNextToken(); err != nil {
		return err
	}
	if err := s.staleSimpleKeys(); err != nil {
		return err
	}
	s.unwindIndent(s.column)
	ch := s.peek(0)
	switch {
	case ch == 0:
		return s.fetchStreamEnd()
	case ch == '%' && s.column == 0:
		return s.fetchDirective()
	case ch == '-' && s.checkDocumentIndicator("---"):
		return s.fetchDocumentIndicator(tDocumentStart)
	case ch == '.' && s.checkDocumentIndicator("..."):
		return s.fetchDocumentIndicator(tDocumentEnd)
	case ch == '[':
		return s.fetchFlowCollectionStart(tFlowSequenceStart)
	case ch == '{':
		return s.fetchFlowCollectionStart(tFlowMappingStart)
	case ch == ']':
		return s.fetchFlowCollectionEnd(tFlowSequenceEnd)
	case ch == '}':
		return s.fetchFlowCollectionEnd(tFlowMappingEnd)
	case ch == ',':
		return s.fetchFlowEntry()
	case ch == '-' && in(s.peek(1), blankz):
		return s.fetchBlockEntry()
	case ch == '?' && (s.flowLevel > 0 || in(s.peek(1), blankz)):
		return s.fetchKey()
	case ch == ':' && (s.flowLevel > 0 || in(s.peek(1), blankz)):
		return s.fetchValue()
	case ch == '*':
		return s.fetchAnchor(tAlias)
	case ch == '&':
		return s.fetchAnchor(tAnchor)
	case ch == '!':
		return s.fetchTag()
	case ch == '|' && s.flowLevel == 0:
		return s.fetchBlockScalar(false)
	case ch == '>' && s.flowLevel == 0:
		return s.fetchBlockScalar(true)
	case ch == '\'':
		return s.fetchFlowScalar(false)
	case ch == '"':
		return s.fetchFlowScalar(true)
	case s.checkPlain():
		return s.fetchPlain()
	}
	return s.fail("while scanning for the next token", nil,
		fmt.Sprintf("found character %s that cannot start any token", repr(ch)))
}

func (s *scanner) nextPossibleSimpleKey() (int, bool) {
	if len(s.possible) == 0 {
		return 0, false
	}
	low := s.possible[0].tokenNumber
	for _, k := range s.possible[1:] {
		low = min(low, k.tokenNumber)
	}
	return low, true
}

func (s *scanner) staleSimpleKeys() error {
	kept := s.possible[:0]
	for _, k := range s.possible {
		if k.line != s.line || s.ptr-k.index > simpleKeyLimit {
			if k.required {
				return markedError("while scanning a simple key", markPtr(k.mark),
					"could not find expected ':'", markPtr(s.mark()))
			}
			continue
		}
		kept = append(kept, k)
	}
	s.possible = kept
	return nil
}

func (s *scanner) savePossibleSimpleKey() error {
	required := s.flowLevel == 0 && s.indent == s.column
	if s.allowSimpleKey {
		if err := s.removePossibleSimpleKey(); err != nil {
			return err
		}
		s.possible = append(s.possible, simpleKey{
			level: s.flowLevel, tokenNumber: s.tokensTaken + len(s.tokens), required: required,
			index: s.ptr, line: s.line, column: s.column, mark: s.mark(),
		})
	}
	return nil
}

func (s *scanner) findKey(level int) int {
	for i, k := range s.possible {
		if k.level == level {
			return i
		}
	}
	return -1
}

func (s *scanner) removePossibleSimpleKey() error {
	if i := s.findKey(s.flowLevel); i >= 0 {
		k := s.possible[i]
		if k.required {
			return markedError("while scanning a simple key", markPtr(k.mark),
				"could not find expected ':'", markPtr(s.mark()))
		}
		s.possible = append(s.possible[:i], s.possible[i+1:]...)
	}
	return nil
}

func (s *scanner) unwindIndent(column int) {
	if s.flowLevel > 0 {
		return
	}
	for s.indent > column {
		m := s.mark()
		s.indent = s.indents[len(s.indents)-1]
		s.indents = s.indents[:len(s.indents)-1]
		s.tokens = append(s.tokens, &token{kind: tBlockEnd, start: m, end: m})
	}
}

func (s *scanner) addIndent(column int) bool {
	if s.indent < column {
		s.indents = append(s.indents, s.indent)
		s.indent = column
		return true
	}
	return false
}

func (s *scanner) insertToken(at int, t *token) {
	s.tokens = append(s.tokens, nil)
	copy(s.tokens[at+1:], s.tokens[at:])
	s.tokens[at] = t
}

func (s *scanner) simple(kind tokenKind) {
	start := s.mark()
	s.forward(1)
	s.tokens = append(s.tokens, &token{kind: kind, start: start, end: s.mark()})
}

func (s *scanner) fetchStreamEnd() error {
	s.unwindIndent(-1)
	if err := s.removePossibleSimpleKey(); err != nil {
		return err
	}
	s.allowSimpleKey = false
	s.possible = nil
	m := s.mark()
	s.tokens = append(s.tokens, &token{kind: tStreamEnd, start: m, end: m})
	s.done = true
	return nil
}

func (s *scanner) fetchDirective() error {
	s.unwindIndent(-1)
	if err := s.removePossibleSimpleKey(); err != nil {
		return err
	}
	s.allowSimpleKey = false
	t, err := s.scanDirective()
	if err != nil {
		return err
	}
	s.tokens = append(s.tokens, t)
	return nil
}

func (s *scanner) checkDocumentIndicator(indicator string) bool {
	return s.column == 0 && s.prefix(3) == indicator && in(s.peek(3), blankz)
}

func (s *scanner) fetchDocumentIndicator(kind tokenKind) error {
	s.unwindIndent(-1)
	if err := s.removePossibleSimpleKey(); err != nil {
		return err
	}
	s.allowSimpleKey = false
	start := s.mark()
	s.forward(3)
	s.tokens = append(s.tokens, &token{kind: kind, start: start, end: s.mark()})
	return nil
}

func (s *scanner) fetchFlowCollectionStart(kind tokenKind) error {
	if err := s.savePossibleSimpleKey(); err != nil {
		return err
	}
	s.flowLevel++
	s.allowSimpleKey = true
	s.simple(kind)
	return nil
}

func (s *scanner) fetchFlowCollectionEnd(kind tokenKind) error {
	if err := s.removePossibleSimpleKey(); err != nil {
		return err
	}
	s.flowLevel--
	s.allowSimpleKey = false
	s.simple(kind)
	return nil
}

func (s *scanner) fetchFlowEntry() error {
	s.allowSimpleKey = true
	if err := s.removePossibleSimpleKey(); err != nil {
		return err
	}
	s.simple(tFlowEntry)
	return nil
}

func (s *scanner) fetchBlockEntry() error {
	if s.flowLevel == 0 {
		if !s.allowSimpleKey {
			return s.fail("", nil, "sequence entries are not allowed here")
		}
		if s.addIndent(s.column) {
			m := s.mark()
			s.tokens = append(s.tokens, &token{kind: tBlockSequenceStart, start: m, end: m})
		}
	}
	s.allowSimpleKey = true
	if err := s.removePossibleSimpleKey(); err != nil {
		return err
	}
	s.simple(tBlockEntry)
	return nil
}

func (s *scanner) fetchKey() error {
	if s.flowLevel == 0 {
		if !s.allowSimpleKey {
			return s.fail("", nil, "mapping keys are not allowed here")
		}
		if s.addIndent(s.column) {
			m := s.mark()
			s.tokens = append(s.tokens, &token{kind: tBlockMappingStart, start: m, end: m})
		}
	}
	s.allowSimpleKey = s.flowLevel == 0
	if err := s.removePossibleSimpleKey(); err != nil {
		return err
	}
	s.simple(tKey)
	return nil
}

func (s *scanner) fetchValue() error {
	if i := s.findKey(s.flowLevel); i >= 0 {
		k := s.possible[i]
		s.possible = append(s.possible[:i], s.possible[i+1:]...)
		s.insertToken(k.tokenNumber-s.tokensTaken, &token{kind: tKey, start: k.mark, end: k.mark})
		if s.flowLevel == 0 && s.addIndent(k.column) {
			s.insertToken(k.tokenNumber-s.tokensTaken, &token{kind: tBlockMappingStart, start: k.mark, end: k.mark})
		}
		s.allowSimpleKey = false
	} else {
		if s.flowLevel == 0 {
			if !s.allowSimpleKey {
				return s.fail("", nil, "mapping values are not allowed here")
			}
			if s.addIndent(s.column) {
				m := s.mark()
				s.tokens = append(s.tokens, &token{kind: tBlockMappingStart, start: m, end: m})
			}
		}
		s.allowSimpleKey = s.flowLevel == 0
		if err := s.removePossibleSimpleKey(); err != nil {
			return err
		}
	}
	s.simple(tValue)
	return nil
}

func (s *scanner) fetchAnchor(kind tokenKind) error {
	if err := s.savePossibleSimpleKey(); err != nil {
		return err
	}
	s.allowSimpleKey = false
	t, err := s.scanAnchor(kind)
	if err != nil {
		return err
	}
	s.tokens = append(s.tokens, t)
	return nil
}

func (s *scanner) fetchTag() error {
	if err := s.savePossibleSimpleKey(); err != nil {
		return err
	}
	s.allowSimpleKey = false
	t, err := s.scanTag()
	if err != nil {
		return err
	}
	s.tokens = append(s.tokens, t)
	return nil
}

func (s *scanner) fetchBlockScalar(folded bool) error {
	s.allowSimpleKey = true
	if err := s.removePossibleSimpleKey(); err != nil {
		return err
	}
	t, err := s.scanBlockScalar(folded)
	if err != nil {
		return err
	}
	s.tokens = append(s.tokens, t)
	return nil
}

func (s *scanner) fetchFlowScalar(double bool) error {
	if err := s.savePossibleSimpleKey(); err != nil {
		return err
	}
	s.allowSimpleKey = false
	t, err := s.scanFlowScalar(double)
	if err != nil {
		return err
	}
	s.tokens = append(s.tokens, t)
	return nil
}

func (s *scanner) fetchPlain() error {
	if err := s.savePossibleSimpleKey(); err != nil {
		return err
	}
	s.allowSimpleKey = false
	s.tokens = append(s.tokens, s.scanPlain())
	return nil
}

func (s *scanner) checkPlain() bool {
	ch := s.peek(0)
	return !in(ch, plainStart) ||
		!in(s.peek(1), blankz) && (ch == '-' || s.flowLevel == 0 && in(ch, "?:"))
}

// scanToNextToken は空白・注釈・改行を飛ばす。飛ばすのは半角空白だけで、タブは飛ばさない
// （YAML 1.1 の PyYAML は、クォートの中・注釈・ブロックスカラーの中のほかのタブを断る）。
func (s *scanner) scanToNextToken() error {
	if s.ptr == 0 && s.peek(0) == '\ufeff' {
		s.forward(1)
	}
	for {
		for s.peek(0) == ' ' {
			s.forward(1)
		}
		if s.peek(0) == '#' {
			for !in(s.peek(0), breakz) {
				s.forward(1)
			}
		}
		if s.scanLineBreak() == "" {
			return nil
		}
		if s.flowLevel == 0 {
			s.allowSimpleKey = true
		}
	}
}

func (s *scanner) scanDirective() (*token, error) {
	start := s.mark()
	s.forward(1)
	name, err := s.scanDirectiveName(start)
	if err != nil {
		return nil, err
	}
	t := &token{kind: tDirective, name: name, start: start}
	switch name {
	case "YAML":
		if t.major, t.minor, err = s.scanYAMLDirectiveValue(start); err != nil {
			return nil, err
		}
		t.end = s.mark()
	case "TAG":
		if t.handle, t.prefix, err = s.scanTagDirectiveValue(start); err != nil {
			return nil, err
		}
		t.end = s.mark()
	default:
		t.end = s.mark()
		for !in(s.peek(0), breakz) {
			s.forward(1)
		}
	}
	if err := s.scanIgnoredLine("while scanning a directive", start); err != nil {
		return nil, err
	}
	return t, nil
}

func (s *scanner) scanDirectiveName(start Mark) (string, error) {
	n := 0
	for isWordChar(s.peek(n)) {
		n++
	}
	if n == 0 {
		return "", s.fail("while scanning a directive", &start,
			"expected alphabetic or numeric character, but found "+repr(s.peek(n)))
	}
	value := s.prefix(n)
	s.forward(n)
	if !in(s.peek(0), spaceOrBreak) {
		return "", s.fail("while scanning a directive", &start,
			"expected alphabetic or numeric character, but found "+repr(s.peek(0)))
	}
	return value, nil
}

func (s *scanner) skipSpaces() {
	for s.peek(0) == ' ' {
		s.forward(1)
	}
}

func (s *scanner) scanYAMLDirectiveValue(start Mark) (int, int, error) {
	s.skipSpaces()
	major, err := s.scanYAMLDirectiveNumber(start)
	if err != nil {
		return 0, 0, err
	}
	if s.peek(0) != '.' {
		return 0, 0, s.fail("while scanning a directive", &start,
			"expected a digit or '.', but found "+repr(s.peek(0)))
	}
	s.forward(1)
	minor, err := s.scanYAMLDirectiveNumber(start)
	if err != nil {
		return 0, 0, err
	}
	if !in(s.peek(0), spaceOrBreak) {
		return 0, 0, s.fail("while scanning a directive", &start,
			"expected a digit or ' ', but found "+repr(s.peek(0)))
	}
	return major, minor, nil
}

func (s *scanner) scanYAMLDirectiveNumber(start Mark) (int, error) {
	if ch := s.peek(0); ch < '0' || ch > '9' {
		return 0, s.fail("while scanning a directive", &start, "expected a digit, but found "+repr(ch))
	}
	n := 0
	for ch := s.peek(n); ch >= '0' && ch <= '9'; ch = s.peek(n) {
		n++
	}
	value, err := strconv.Atoi(s.prefix(n))
	if err != nil {
		value = 2 // 桁あふれは 1 でない版として扱う
	}
	s.forward(n)
	return value, nil
}

func (s *scanner) scanTagDirectiveValue(start Mark) (string, string, error) {
	s.skipSpaces()
	handle, err := s.scanTagHandle("directive", start)
	if err != nil {
		return "", "", err
	}
	if ch := s.peek(0); ch != ' ' {
		return "", "", s.fail("while scanning a directive", &start, "expected ' ', but found "+repr(ch))
	}
	s.skipSpaces()
	prefix, err := s.scanTagURI("directive", start)
	if err != nil {
		return "", "", err
	}
	if ch := s.peek(0); !in(ch, spaceOrBreak) {
		return "", "", s.fail("while scanning a directive", &start, "expected ' ', but found "+repr(ch))
	}
	return handle, prefix, nil
}

// scanIgnoredLine は指示子やブロックスカラーの頭の後ろ（空白と注釈だけが来てよい）を読む。
func (s *scanner) scanIgnoredLine(context string, start Mark) error {
	s.skipSpaces()
	if s.peek(0) == '#' {
		for !in(s.peek(0), breakz) {
			s.forward(1)
		}
	}
	if ch := s.peek(0); !in(ch, breakz) {
		return s.fail(context, &start, "expected a comment or a line break, but found "+repr(ch))
	}
	s.scanLineBreak()
	return nil
}

func (s *scanner) scanAnchor(kind tokenKind) (*token, error) {
	start := s.mark()
	name := "anchor"
	if s.peek(0) == '*' {
		name = "alias"
	}
	s.forward(1)
	n := 0
	for isWordChar(s.peek(n)) {
		n++
	}
	if n == 0 {
		return nil, s.fail("while scanning an "+name, &start,
			"expected alphabetic or numeric character, but found "+repr(s.peek(n)))
	}
	value := s.prefix(n)
	s.forward(n)
	if ch := s.peek(0); !in(ch, "\x00 \t\r\n\u0085\u2028\u2029?:,]}%@`") {
		return nil, s.fail("while scanning an "+name, &start,
			"expected alphabetic or numeric character, but found "+repr(ch))
	}
	return &token{kind: kind, value: value, start: start, end: s.mark()}, nil
}

func (s *scanner) scanTag() (*token, error) {
	start := s.mark()
	t := &token{kind: tTag, start: start}
	ch := s.peek(1)
	switch {
	case ch == '<':
		s.forward(2)
		suffix, err := s.scanTagURI("tag", start)
		if err != nil {
			return nil, err
		}
		if s.peek(0) != '>' {
			return nil, s.fail("while parsing a tag", &start, "expected '>', but found "+repr(s.peek(0)))
		}
		s.forward(1)
		t.prefix = suffix
	case in(ch, blankz):
		t.prefix = "!"
		s.forward(1)
	default:
		n := 1
		useHandle := false
		for !in(ch, spaceOrBreak) {
			if ch == '!' {
				useHandle = true
				break
			}
			n++
			ch = s.peek(n)
		}
		t.hasHandle = true
		t.handle = "!"
		if useHandle {
			handle, err := s.scanTagHandle("tag", start)
			if err != nil {
				return nil, err
			}
			t.handle = handle
		} else {
			s.forward(1)
		}
		suffix, err := s.scanTagURI("tag", start)
		if err != nil {
			return nil, err
		}
		t.prefix = suffix
	}
	if ch := s.peek(0); !in(ch, spaceOrBreak) {
		return nil, s.fail("while scanning a tag", &start, "expected ' ', but found "+repr(ch))
	}
	t.end = s.mark()
	return t, nil
}

func (s *scanner) scanBlockScalar(folded bool) (*token, error) {
	var chunks strings.Builder
	start := s.mark()
	s.forward(1)
	chomping, increment, err := s.scanBlockScalarIndicators(start)
	if err != nil {
		return nil, err
	}
	if err := s.scanIgnoredLine("while scanning a block scalar", start); err != nil {
		return nil, err
	}
	minIndent := max(s.indent+1, 1)
	var indent int
	var lineBreaks []string
	var end Mark
	if increment == 0 {
		var maxIndent int
		lineBreaks, maxIndent, end = s.scanBlockScalarIndentation()
		indent = max(minIndent, maxIndent)
	} else {
		indent = minIndent + increment - 1
		lineBreaks, end = s.scanBlockScalarBreaks(indent)
	}
	lineBreak := ""
	for s.column == indent && s.peek(0) != 0 {
		for _, b := range lineBreaks {
			chunks.WriteString(b)
		}
		leadingNonSpace := !in(s.peek(0), " \t")
		n := 0
		for !in(s.peek(n), breakz) {
			n++
		}
		chunks.WriteString(s.prefix(n))
		s.forward(n)
		lineBreak = s.scanLineBreak()
		lineBreaks, end = s.scanBlockScalarBreaks(indent)
		if s.column != indent || s.peek(0) == 0 {
			break
		}
		if folded && lineBreak == "\n" && leadingNonSpace && !in(s.peek(0), " \t") {
			if len(lineBreaks) == 0 {
				chunks.WriteString(" ")
			}
		} else {
			chunks.WriteString(lineBreak)
		}
	}
	if chomping != chompStrip {
		chunks.WriteString(lineBreak)
	}
	if chomping == chompKeep {
		for _, b := range lineBreaks {
			chunks.WriteString(b)
		}
	}
	style := '|'
	if folded {
		style = '>'
	}
	return &token{kind: tScalar, value: chunks.String(), start: start, end: end, style: style}, nil
}

type chomping int

const (
	chompClip chomping = iota
	chompStrip
	chompKeep
)

const errIndentZero = "expected indentation indicator in the range 1-9, but found 0"

func (s *scanner) scanBlockScalarIndicators(start Mark) (chomping, int, error) {
	chomp := chompClip
	increment := 0
	readChomp := func() bool {
		switch s.peek(0) {
		case '+':
			chomp = chompKeep
		case '-':
			chomp = chompStrip
		default:
			return false
		}
		s.forward(1)
		return true
	}
	readIncrement := func() error {
		ch := s.peek(0)
		if ch < '0' || ch > '9' {
			return nil
		}
		increment = int(ch - '0')
		if increment == 0 {
			return s.fail("while scanning a block scalar", &start, errIndentZero)
		}
		s.forward(1)
		return nil
	}
	if readChomp() {
		if err := readIncrement(); err != nil {
			return 0, 0, err
		}
	} else if ch := s.peek(0); ch >= '0' && ch <= '9' {
		if err := readIncrement(); err != nil {
			return 0, 0, err
		}
		readChomp()
	}
	if ch := s.peek(0); !in(ch, spaceOrBreak) {
		return 0, 0, s.fail("while scanning a block scalar", &start,
			"expected chomping or indentation indicators, but found "+repr(ch))
	}
	return chomp, increment, nil
}

func (s *scanner) scanBlockScalarIndentation() ([]string, int, Mark) {
	var chunks []string
	maxIndent := 0
	end := s.mark()
	for in(s.peek(0), " \r\n\u0085\u2028\u2029") {
		if s.peek(0) != ' ' {
			chunks = append(chunks, s.scanLineBreak())
			end = s.mark()
		} else {
			s.forward(1)
			maxIndent = max(maxIndent, s.column)
		}
	}
	return chunks, maxIndent, end
}

func (s *scanner) scanBlockScalarBreaks(indent int) ([]string, Mark) {
	var chunks []string
	end := s.mark()
	for s.column < indent && s.peek(0) == ' ' {
		s.forward(1)
	}
	for in(s.peek(0), breaks) {
		chunks = append(chunks, s.scanLineBreak())
		end = s.mark()
		for s.column < indent && s.peek(0) == ' ' {
			s.forward(1)
		}
	}
	return chunks, end
}

func (s *scanner) scanFlowScalar(double bool) (*token, error) {
	var chunks strings.Builder
	start := s.mark()
	quote := s.peek(0)
	s.forward(1)
	if err := s.scanFlowScalarNonSpaces(&chunks, double, start); err != nil {
		return nil, err
	}
	for s.peek(0) != quote {
		if err := s.scanFlowScalarSpaces(&chunks, start); err != nil {
			return nil, err
		}
		if err := s.scanFlowScalarNonSpaces(&chunks, double, start); err != nil {
			return nil, err
		}
	}
	s.forward(1)
	style := '\''
	if double {
		style = '"'
	}
	return &token{kind: tScalar, value: chunks.String(), start: start, end: s.mark(), style: style}, nil
}

var escapeReplacements = map[rune]string{
	'0': "\x00", 'a': "\x07", 'b': "\x08", 't': "\x09", '\t': "\x09", 'n': "\x0a", 'v': "\x0b",
	'f': "\x0c", 'r': "\x0d", 'e': "\x1b", ' ': " ", '"': "\"", '\\': "\\", '/': "/",
	'N': "\u0085", '_': "\u00a0", 'L': "\u2028", 'P': "\u2029",
}

var escapeCodes = map[rune]int{'x': 2, 'u': 4, 'U': 8}

func (s *scanner) scanFlowScalarNonSpaces(chunks *strings.Builder, double bool, start Mark) error {
	for {
		n := 0
		for !in(s.peek(n), "'\"\\\x00 \t\r\n\u0085\u2028\u2029") {
			n++
		}
		if n > 0 {
			chunks.WriteString(s.prefix(n))
			s.forward(n)
		}
		ch := s.peek(0)
		switch {
		case !double && ch == '\'' && s.peek(1) == '\'':
			chunks.WriteByte('\'')
			s.forward(2)
		case double && ch == '\'' || !double && in(ch, "\"\\"):
			chunks.WriteRune(ch)
			s.forward(1)
		case double && ch == '\\':
			s.forward(1)
			ch = s.peek(0)
			if r, ok := escapeReplacements[ch]; ok {
				chunks.WriteString(r)
				s.forward(1)
			} else if length, ok := escapeCodes[ch]; ok {
				s.forward(1)
				for k := range length {
					if !in(s.peek(k), hexDigits) {
						return s.fail("while scanning a double-quoted scalar", &start,
							fmt.Sprintf("expected escape sequence of %d hexadecimal numbers, but found %s", length, repr(s.peek(k))))
					}
				}
				code, _ := strconv.ParseUint(s.prefix(length), 16, 32)
				if code > utf8.MaxRune {
					return buildError("ValueError", "chr() arg not in range(0x110000)")
				}
				// 孤立したサロゲートは Go の文字列に置けないので U+FFFD になる。
				chunks.WriteRune(rune(code))
				s.forward(length)
			} else if in(ch, breaks) {
				s.scanLineBreak()
				lines, err := s.scanFlowScalarBreaks(start)
				if err != nil {
					return err
				}
				for _, l := range lines {
					chunks.WriteString(l)
				}
			} else {
				return s.fail("while scanning a double-quoted scalar", &start,
					"found unknown escape character "+repr(ch))
			}
		default:
			return nil
		}
	}
}

func (s *scanner) scanFlowScalarSpaces(chunks *strings.Builder, start Mark) error {
	n := 0
	for in(s.peek(n), " \t") {
		n++
	}
	whitespaces := s.prefix(n)
	s.forward(n)
	ch := s.peek(0)
	switch {
	case ch == 0:
		return s.fail("while scanning a quoted scalar", &start, "found unexpected end of stream")
	case in(ch, breaks):
		lineBreak := s.scanLineBreak()
		lines, err := s.scanFlowScalarBreaks(start)
		if err != nil {
			return err
		}
		if lineBreak != "\n" {
			chunks.WriteString(lineBreak)
		} else if len(lines) == 0 {
			chunks.WriteByte(' ')
		}
		for _, l := range lines {
			chunks.WriteString(l)
		}
	default:
		chunks.WriteString(whitespaces)
	}
	return nil
}

func (s *scanner) atDocumentSeparator() bool {
	p := s.prefix(3)
	return (p == "---" || p == "...") && in(s.peek(3), blankz)
}

func (s *scanner) scanFlowScalarBreaks(start Mark) ([]string, error) {
	var chunks []string
	for {
		if s.atDocumentSeparator() {
			return nil, s.fail("while scanning a quoted scalar", &start, "found unexpected document separator")
		}
		for in(s.peek(0), " \t") {
			s.forward(1)
		}
		if !in(s.peek(0), breaks) {
			return chunks, nil
		}
		chunks = append(chunks, s.scanLineBreak())
	}
}

func (s *scanner) scanPlain() *token {
	var chunks strings.Builder
	start := s.mark()
	end := start
	indent := s.indent + 1
	var spaces []string
	stop := blankz
	if s.flowLevel > 0 {
		stop += ",[]{}"
	}
	for s.peek(0) != '#' {
		n := 0
		for {
			ch := s.peek(n)
			if in(ch, blankz) || ch == ':' && in(s.peek(n+1), stop) || s.flowLevel > 0 && in(ch, ",?[]{}") {
				break
			}
			n++
		}
		if n == 0 {
			break
		}
		s.allowSimpleKey = false
		for _, sp := range spaces {
			chunks.WriteString(sp)
		}
		chunks.WriteString(s.prefix(n))
		s.forward(n)
		end = s.mark()
		spaces = s.scanPlainSpaces()
		if len(spaces) == 0 || s.peek(0) == '#' || s.flowLevel == 0 && s.column < indent {
			break
		}
	}
	return &token{kind: tScalar, value: chunks.String(), plain: true, start: start, end: end}
}

// scanPlainSpaces はクォートしない値の中の空白と改行を読む。区切りにするのは半角空白だけ。
func (s *scanner) scanPlainSpaces() []string {
	var chunks []string
	n := 0
	for s.peek(n) == ' ' {
		n++
	}
	whitespaces := s.prefix(n)
	s.forward(n)
	ch := s.peek(0)
	if in(ch, breaks) {
		lineBreak := s.scanLineBreak()
		s.allowSimpleKey = true
		if s.atDocumentSeparator() {
			return nil
		}
		var lines []string
		for in(s.peek(0), " \r\n\u0085\u2028\u2029") {
			if s.peek(0) == ' ' {
				s.forward(1)
				continue
			}
			lines = append(lines, s.scanLineBreak())
			if s.atDocumentSeparator() {
				return nil
			}
		}
		if lineBreak != "\n" {
			chunks = append(chunks, lineBreak)
		} else if len(lines) == 0 {
			chunks = append(chunks, " ")
		}
		chunks = append(chunks, lines...)
	} else if whitespaces != "" {
		chunks = append(chunks, whitespaces)
	}
	return chunks
}

func (s *scanner) scanTagHandle(name string, start Mark) (string, error) {
	if ch := s.peek(0); ch != '!' {
		return "", s.fail("while scanning a "+name, &start, "expected '!', but found "+repr(ch))
	}
	n := 1
	ch := s.peek(n)
	if ch != ' ' {
		for isWordChar(ch) {
			n++
			ch = s.peek(n)
		}
		if ch != '!' {
			s.forward(n)
			return "", s.fail("while scanning a "+name, &start, "expected '!', but found "+repr(ch))
		}
		n++
	}
	value := s.prefix(n)
	s.forward(n)
	return value, nil
}

func (s *scanner) scanTagURI(name string, start Mark) (string, error) {
	var chunks strings.Builder
	n := 0
	ch := s.peek(n)
	for isWordChar(ch) || in(ch, uriChars) {
		if ch == '%' {
			chunks.WriteString(s.prefix(n))
			s.forward(n)
			n = 0
			text, err := s.scanURIEscapes(name, start)
			if err != nil {
				return "", err
			}
			chunks.WriteString(text)
		} else {
			n++
		}
		ch = s.peek(n)
	}
	if n > 0 {
		chunks.WriteString(s.prefix(n))
		s.forward(n)
	}
	if chunks.Len() == 0 {
		return "", s.fail("while parsing a "+name, &start, "expected URI, but found "+repr(ch))
	}
	return chunks.String(), nil
}

func (s *scanner) scanURIEscapes(name string, start Mark) (string, error) {
	var codes []byte
	m := s.mark()
	for s.peek(0) == '%' {
		s.forward(1)
		for k := range 2 {
			if !in(s.peek(k), hexDigits) {
				return "", s.fail("while scanning a "+name, &start,
					"expected URI escape sequence of 2 hexadecimal numbers, but found "+repr(s.peek(k)))
			}
		}
		code, _ := strconv.ParseUint(s.prefix(2), 16, 8)
		codes = append(codes, byte(code))
		s.forward(2)
	}
	if problem := utf8Problem(codes); problem != "" {
		return "", markedError("while scanning a "+name, &start, problem, &m)
	}
	return string(codes), nil
}

// utf8Problem は bytes.decode("utf-8") の UnicodeDecodeError の文。読めれば空。
func utf8Problem(data []byte) string {
	for i := 0; i < len(data); {
		b := data[i]
		if b < 0x80 {
			i++
			continue
		}
		need, lo, hi := 0, byte(0x80), byte(0xbf)
		switch {
		case b >= 0xc2 && b <= 0xdf:
			need = 1
		case b == 0xe0:
			need, lo = 2, 0xa0
		case b >= 0xe1 && b <= 0xec || b == 0xee || b == 0xef:
			need = 2
		case b == 0xed:
			need, hi = 2, 0x9f
		case b == 0xf0:
			need, lo = 3, 0x90
		case b >= 0xf1 && b <= 0xf3:
			need = 3
		case b == 0xf4:
			need, hi = 3, 0x8f
		default:
			return decodeProblem(data, i, i, "invalid start byte")
		}
		j := i + 1
		for k := 0; k < need; k, j = k+1, j+1 {
			if j >= len(data) {
				return decodeProblem(data, i, j-1, "unexpected end of data")
			}
			first := k == 0
			if first && (data[j] < lo || data[j] > hi) || !first && (data[j] < 0x80 || data[j] > 0xbf) {
				return decodeProblem(data, i, j-1, "invalid continuation byte")
			}
		}
		i = j
	}
	return ""
}

func decodeProblem(data []byte, from, to int, reason string) string {
	if from == to {
		return fmt.Sprintf("'utf-8' codec can't decode byte 0x%02x in position %d: %s", data[from], from, reason)
	}
	return fmt.Sprintf("'utf-8' codec can't decode bytes in position %d-%d: %s", from, to, reason)
}

func (s *scanner) scanLineBreak() string {
	ch := s.peek(0)
	switch {
	case in(ch, "\r\n\u0085"):
		if s.prefix(2) == "\r\n" {
			s.forward(2)
		} else {
			s.forward(1)
		}
		return "\n"
	case in(ch, "\u2028\u2029"):
		s.forward(1)
		return string(ch)
	}
	return ""
}
