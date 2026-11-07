package pyre

import (
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"
	"unicode"

	"ccnavi/internal/infra/pyfmt"
)

// Flag は Python の re の旗。値も Python と同じ。
type Flag uint

const (
	IgnoreCase Flag = 2
	locale     Flag = 4
	Multiline  Flag = 8
	DotAll     Flag = 16
	unicodeOn  Flag = 32
	Verbose    Flag = 64
	ascii      Flag = 256

	typeFlags = ascii | locale | unicodeOn
)

// maxRepeat は繰り返しの上限なし（Python の MAXREPEAT）。
const maxRepeat = 4294967295

// maxGroups は Python の MAXGROUPS。
const maxGroups = 1073741823

// maxNesting は Python が RecursionError で読めなくなる入れ子の段数（3.13・再帰の上限 1000 で 496 段）。
const maxNesting = 495

// maxCode は Python の MAXCODE。後読みの幅の上限。
const maxCode = 1<<32 - 1

// maxWidth は幅の飽和値（Python の MAXWIDTH の代わり）。
const maxWidth = math.MaxInt64

var (
	// ErrTooDeep は入れ子が深すぎて Python が RecursionError で止まる式。
	ErrTooDeep = errors.New("maximum recursion depth exceeded")
	// errOverflow は繰り返しの数が Python の上限を超える式（Python は OverflowError）。
	errOverflow = errors.New("the repetition number is too large")
)

// Error は読めない式（Python の re.error）。Error() は str(re.error) と同じ文。
type Error struct {
	msg     string
	pattern string
	pos     int // 符号位置。hasPos が偽なら位置なし
	hasPos  bool
}

func (e *Error) Error() string {
	if !e.hasPos {
		return e.msg
	}
	msg := fmt.Sprintf("%s at position %d", e.msg, e.pos)
	runes := []rune(e.pattern)
	if strings.ContainsRune(e.pattern, '\n') {
		head := runes[:min(e.pos, len(runes))]
		line := strings.Count(string(head), "\n") + 1
		var col int
		if i := lastRune(head, '\n'); i >= 0 {
			col = e.pos - i
		} else {
			col = e.pos + 1
		}
		msg = fmt.Sprintf("%s (line %d, column %d)", msg, line, col)
	}
	return msg
}

func lastRune(s []rune, r rune) int {
	for i := len(s) - 1; i >= 0; i-- {
		if s[i] == r {
			return i
		}
	}
	return -1
}

type opcode uint8

const (
	opLiteral opcode = iota
	opNotLiteral
	opAny
	opIn
	opBranch
	opGroup // SUBPATTERN（group が 0 なら捕獲しない）
	opRepeat
	opAtomic
	opAt
	opGroupRef
	opGroupExists
	opAssert
	opAssertNot
	opFailure
)

type repeatKind uint8

const (
	greedy repeatKind = iota
	lazy
	possessive
)

type atCode uint8

const (
	atBeginning atCode = iota
	atBeginningString
	atEnd
	atEndString
	atBoundary
	atNonBoundary
)

type category uint8

const (
	catDigit category = iota
	catNotDigit
	catSpace
	catNotSpace
	catWord
	catNotWord
)

type setKind uint8

const (
	setLiteral setKind = iota
	setRange
	setCategory
)

type setItem struct {
	kind   setKind
	lo, hi rune
	cat    category
}

type node struct {
	op       opcode
	ch       rune
	negate   bool
	set      []setItem
	alts     []seq
	group    int
	add, del Flag
	sub      seq
	min, max int
	kind     repeatKind
	at       atCode
	behind   bool
	yes, no  seq
	hasNo    bool
}

type seq []*node

// parseState は Python の _parser.State。
type parseState struct {
	flags        Flag
	groupdict    map[string]int
	names        []string // 名前の付いたグループを付けた順に
	groupwidths  []*[2]int
	lookbehind   int // -1 なら後読みの外
	grouprefpos  map[int]int
	grouprefKeys []int
}

func (s *parseState) groups() int { return len(s.groupwidths) }

// tokenizer は Python の _parser.Tokenizer。位置は符号位置。
type tokenizer struct {
	pattern string
	src     []rune
	index   int
	next    string
	has     bool
}

func newTokenizer(pattern string) (*tokenizer, error) {
	t := &tokenizer{pattern: pattern, src: []rune(pattern)}
	if err := t.advance(); err != nil {
		return nil, err
	}
	return t, nil
}

func (t *tokenizer) advance() error {
	index := t.index
	if index >= len(t.src) {
		t.next, t.has = "", false
		return nil
	}
	char := string(t.src[index])
	if t.src[index] == '\\' {
		index++
		if index >= len(t.src) {
			return &Error{msg: "bad escape (end of pattern)", pattern: t.pattern, pos: len(t.src) - 1, hasPos: true}
		}
		char += string(t.src[index])
	}
	t.index = index + 1
	t.next, t.has = char, true
	return nil
}

func (t *tokenizer) match(s string) (bool, error) {
	if t.has && t.next == s {
		return true, t.advance()
	}
	return false, nil
}

// get は次の字（無ければ ok が偽）。
func (t *tokenizer) get() (string, bool, error) {
	this, ok := t.next, t.has
	return this, ok, t.advance()
}

func (t *tokenizer) nextIn(set string) bool {
	return t.has && len([]rune(t.next)) == 1 && strings.Contains(set, t.next)
}

func (t *tokenizer) getwhile(n int, set string) (string, error) {
	var b strings.Builder
	for range n {
		if !t.nextIn(set) {
			break
		}
		b.WriteString(t.next)
		if err := t.advance(); err != nil {
			return "", err
		}
	}
	return b.String(), nil
}

func (t *tokenizer) getuntil(terminator, name string) (string, error) {
	var result []rune
	for {
		c, ok, err := t.get()
		if err != nil {
			return "", err
		}
		if !ok {
			if len(result) == 0 {
				return "", t.error("missing "+name, 0)
			}
			return "", t.error(fmt.Sprintf("missing %s, unterminated name", terminator), len(result))
		}
		if c == terminator {
			if len(result) == 0 {
				return "", t.error("missing "+name, 1)
			}
			break
		}
		result = append(result, []rune(c)...)
	}
	return string(result), nil
}

func (t *tokenizer) tell() int {
	if t.has {
		return t.index - len([]rune(t.next))
	}
	return t.index
}

func (t *tokenizer) seek(index int) error {
	t.index = index
	return t.advance()
}

func (t *tokenizer) error(msg string, offset int) *Error {
	return &Error{msg: msg, pattern: t.pattern, pos: t.tell() - offset, hasPos: true}
}

func (t *tokenizer) checkgroupname(name string, offset int) error {
	if !isIdentifier(name) {
		return t.error("bad character in group name "+pyfmt.Repr(name), runeLen(name)+offset)
	}
	return nil
}

func runeLen(s string) int { return len([]rune(s)) }

// isIdentifier は Python の str.isidentifier の近似（XID_Start・XID_Continue を Unicode の分類で読む）。
func isIdentifier(s string) bool {
	if s == "" {
		return false
	}
	for i, r := range []rune(s) {
		start := r == '_' || unicode.IsLetter(r) || unicode.Is(unicode.Nl, r)
		if i == 0 && !start {
			return false
		}
		if !start && !unicode.In(r, unicode.Mn, unicode.Mc, unicode.Nd, unicode.Pc) {
			return false
		}
	}
	return true
}

const (
	digits      = "0123456789"
	octDigits   = "01234567"
	hexDigits   = "0123456789abcdefABCDEF"
	asciiLetter = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
	whitespace  = " \t\n\r\v\f"
	special     = ".\\[{()*+?^$|"
	repeatChars = "*+?{"
)

var escapes = map[string]rune{
	`\a`: '\a', `\b`: '\b', `\f`: '\f', `\n`: '\n', `\r`: '\r', `\t`: '\t', `\v`: '\v', `\\`: '\\',
}

var flagLetters = map[rune]Flag{
	'i': IgnoreCase, 'L': locale, 'm': Multiline, 's': DotAll, 'x': Verbose, 'a': ascii, 'u': unicodeOn,
}

// categoryEscape は \d などの文字クラス。in が偽なら位置の検査（\A \b \B \Z）。
func categoryEscape(escape string) (n *node, in bool, ok bool) {
	switch escape {
	case `\A`:
		return &node{op: opAt, at: atBeginningString}, false, true
	case `\b`:
		return &node{op: opAt, at: atBoundary}, false, true
	case `\B`:
		return &node{op: opAt, at: atNonBoundary}, false, true
	case `\Z`:
		return &node{op: opAt, at: atEndString}, false, true
	}
	cats := map[string]category{`\d`: catDigit, `\D`: catNotDigit, `\s`: catSpace, `\S`: catNotSpace, `\w`: catWord, `\W`: catNotWord}
	if c, found := cats[escape]; found {
		return &node{op: opIn, set: []setItem{{kind: setCategory, cat: c}}}, true, true
	}
	return nil, false, false
}

func parseHex(s string) rune {
	v, _ := strconv.ParseUint(s, 16, 32)
	return rune(v)
}

// classEscape は文字クラスの中のエスケープ（Python の _class_escape）。返すのは 1 件の要素。
func (t *tokenizer) classEscape(escape string) (setItem, bool, error) {
	if r, ok := escapes[escape]; ok {
		return setItem{kind: setLiteral, lo: r}, false, nil
	}
	if n, in, ok := categoryEscape(escape); ok && in {
		return n.set[0], true, nil
	}
	er := []rune(escape)
	bad := func() (setItem, bool, error) {
		return setItem{}, false, t.error("bad escape "+escape, runeLen(escape))
	}
	if len(er) < 2 {
		return bad()
	}
	c := er[1]
	switch {
	case c == 'x' || c == 'u' || c == 'U':
		want := map[rune]int{'x': 2, 'u': 4, 'U': 8}[c]
		more, err := t.getwhile(want, hexDigits)
		if err != nil {
			return setItem{}, false, err
		}
		escape += more
		if runeLen(escape) != want+2 {
			return setItem{}, false, t.error("incomplete escape "+escape, runeLen(escape))
		}
		v := parseHex(escape[2:])
		if c == 'U' && v > unicode.MaxRune {
			return bad()
		}
		return setItem{kind: setLiteral, lo: v}, false, nil
	case c == 'N':
		return setItem{}, false, t.namedEscape()
	case strings.ContainsRune(octDigits, c):
		more, err := t.getwhile(2, octDigits)
		if err != nil {
			return setItem{}, false, err
		}
		escape += more
		v, _ := strconv.ParseUint(escape[1:], 8, 32)
		if v > 0o377 {
			return setItem{}, false, t.error(fmt.Sprintf("octal escape value %s outside of range 0-0o377", escape), runeLen(escape))
		}
		return setItem{kind: setLiteral, lo: rune(v)}, false, nil
	case strings.ContainsRune(digits, c):
		return bad()
	}
	if len(er) == 2 {
		if strings.ContainsRune(asciiLetter, c) {
			return bad()
		}
		return setItem{kind: setLiteral, lo: c}, false, nil
	}
	return bad()
}

// namedEscape は \N{名前}。Unicode の名前の表を持たないので、名前を読んだうえで未定義として断る。
func (t *tokenizer) namedEscape() error {
	ok, err := t.match("{")
	if err != nil {
		return err
	}
	if !ok {
		return t.error("missing {", 0)
	}
	name, err := t.getuntil("}", "character name")
	if err != nil {
		return err
	}
	return t.error("undefined character name "+pyfmt.Repr(name), runeLen(name)+len(`\N{}`))
}

// escape は式の中のエスケープ（Python の _escape）。
func (p *parser) escape(escape string) (*node, error) {
	t := p.src
	if n, _, ok := categoryEscape(escape); ok {
		return n, nil
	}
	if r, ok := escapes[escape]; ok {
		return &node{op: opLiteral, ch: r}, nil
	}
	er := []rune(escape)
	bad := func() (*node, error) { return nil, t.error("bad escape "+escape, runeLen(escape)) }
	c := er[1]
	switch {
	case c == 'x' || c == 'u' || c == 'U':
		item, _, err := t.classEscape(escape)
		if err != nil {
			return nil, err
		}
		return &node{op: opLiteral, ch: item.lo}, nil
	case c == 'N':
		return nil, t.namedEscape()
	case c == '0':
		more, err := t.getwhile(2, octDigits)
		if err != nil {
			return nil, err
		}
		escape += more
		v, _ := strconv.ParseUint(escape[1:], 8, 32)
		return &node{op: opLiteral, ch: rune(v)}, nil
	case strings.ContainsRune(digits, c):
		if t.nextIn(digits) {
			d, _, err := t.get()
			if err != nil {
				return nil, err
			}
			escape += d
			e := []rune(escape)
			if strings.ContainsRune(octDigits, e[1]) && strings.ContainsRune(octDigits, e[2]) && t.nextIn(octDigits) {
				d, _, err := t.get()
				if err != nil {
					return nil, err
				}
				escape += d
				v, _ := strconv.ParseUint(escape[1:], 8, 32)
				if v > 0o377 {
					return nil, t.error(fmt.Sprintf("octal escape value %s outside of range 0-0o377", escape), runeLen(escape))
				}
				return &node{op: opLiteral, ch: rune(v)}, nil
			}
		}
		group, _ := strconv.Atoi(escape[1:])
		if group < p.state.groups() {
			if !p.state.checkgroup(group) {
				return nil, t.error("cannot refer to an open group", runeLen(escape))
			}
			if err := p.checklookbehindgroup(group); err != nil {
				return nil, err
			}
			return &node{op: opGroupRef, group: group}, nil
		}
		return nil, t.error(fmt.Sprintf("invalid group reference %d", group), runeLen(escape)-1)
	}
	if len(er) == 2 {
		if strings.ContainsRune(asciiLetter, c) {
			return bad()
		}
		return &node{op: opLiteral, ch: c}, nil
	}
	return bad()
}

func (s *parseState) checkgroup(gid int) bool {
	return gid < s.groups() && s.groupwidths[gid] != nil
}

type parser struct {
	src   *tokenizer
	state *parseState
}

func (p *parser) checklookbehindgroup(gid int) error {
	if p.state.lookbehind >= 0 {
		if !p.state.checkgroup(gid) {
			return p.src.error("cannot refer to an open group", 0)
		}
		if gid >= p.state.lookbehind {
			return p.src.error("cannot refer to group defined in the same lookbehind subpattern", 0)
		}
	}
	return nil
}

func (p *parser) opengroup(name string, named bool) (int, error) {
	gid := p.state.groups()
	p.state.groupwidths = append(p.state.groupwidths, nil)
	if p.state.groups() > maxGroups {
		return 0, errors.New("too many groups")
	}
	if named {
		if ogid, ok := p.state.groupdict[name]; ok {
			return 0, fmt.Errorf("redefinition of group name %s as group %d; was group %d", pyfmt.Repr(name), gid, ogid)
		}
		p.state.groupdict[name] = gid
		p.state.names = append(p.state.names, name)
	}
	return gid, nil
}

// parsePattern は Python の _parser.parse。返すのは式と、式の中の旗を足した旗。
func parsePattern(pattern string, flags Flag) (seq, *parseState, error) {
	t, err := newTokenizer(pattern)
	if err != nil {
		return nil, nil, err
	}
	state := &parseState{flags: flags, groupdict: map[string]int{}, groupwidths: []*[2]int{nil}, lookbehind: -1, grouprefpos: map[int]int{}}
	p := &parser{src: t, state: state}
	s, err := p.parseSub(flags&Verbose != 0, 0)
	if err != nil {
		return nil, nil, err
	}
	if state.flags&locale != 0 {
		return nil, nil, errors.New("cannot use LOCALE flag with a str pattern")
	}
	if state.flags&ascii == 0 {
		state.flags |= unicodeOn
	} else if state.flags&unicodeOn != 0 {
		return nil, nil, errors.New("ASCII and UNICODE flags are incompatible")
	}
	if t.has {
		return nil, nil, t.error("unbalanced parenthesis", 0)
	}
	for _, g := range state.grouprefKeys {
		if g >= state.groups() {
			return nil, nil, &Error{msg: fmt.Sprintf("invalid group reference %d", g), pattern: pattern, pos: state.grouprefpos[g], hasPos: true}
		}
	}
	return s, state, nil
}

func (p *parser) parseSub(verbose bool, nested int) (seq, error) {
	if nested/2 > maxNesting {
		return nil, ErrTooDeep
	}
	var items []seq
	for {
		item, err := p.parse(verbose, nested+1, nested == 0 && len(items) == 0)
		if err != nil {
			return nil, err
		}
		items = append(items, item)
		ok, err := p.src.match("|")
		if err != nil {
			return nil, err
		}
		if !ok {
			break
		}
		if nested == 0 {
			verbose = p.state.flags&Verbose != 0
		}
	}
	if len(items) == 1 {
		return items[0], nil
	}
	return seq{{op: opBranch, alts: items}}, nil
}

func (p *parser) parse(verbose bool, nested int, first bool) (seq, error) {
	src := p.src
	var sub seq
	for {
		if !src.has {
			break
		}
		if src.next == "|" || src.next == ")" {
			break
		}
		this, _, err := src.get()
		if err != nil {
			return nil, err
		}
		if verbose {
			if len(this) == 1 && strings.Contains(whitespace, this) {
				continue
			}
			if this == "#" {
				for {
					c, ok, err := src.get()
					if err != nil {
						return nil, err
					}
					if !ok || c == "\n" {
						break
					}
				}
				continue
			}
		}
		r := []rune(this)
		switch {
		case r[0] == '\\':
			n, err := p.escape(this)
			if err != nil {
				return nil, err
			}
			sub = append(sub, n)
		case !strings.ContainsRune(special, r[0]):
			sub = append(sub, &node{op: opLiteral, ch: r[0]})
		case this == "[":
			n, err := p.charClass()
			if err != nil {
				return nil, err
			}
			sub = append(sub, n)
		case strings.Contains(repeatChars, this):
			sub, err = p.repeat(sub, this)
			if err != nil {
				return nil, err
			}
		case this == ".":
			sub = append(sub, &node{op: opAny})
		case this == "(":
			var stop bool
			sub, stop, err = p.group(sub, verbose, nested, first)
			if err != nil {
				return nil, err
			}
			if stop {
				verbose = p.state.flags&Verbose != 0
			}
		case this == "^":
			sub = append(sub, &node{op: opAt, at: atBeginning})
		case this == "$":
			sub = append(sub, &node{op: opAt, at: atEnd})
		}
	}
	// 捕獲しない、旗の無いグループは開く。
	var out seq
	for _, n := range sub {
		if n.op == opGroup && n.group == 0 && n.add == 0 && n.del == 0 {
			out = append(out, n.sub...)
			continue
		}
		out = append(out, n)
	}
	return out, nil
}

func (p *parser) charClass() (*node, error) {
	src := p.src
	here := src.tell() - 1
	var set []setItem
	negate, err := src.match("^")
	if err != nil {
		return nil, err
	}
	for {
		this, ok, err := src.get()
		if err != nil {
			return nil, err
		}
		if !ok {
			return nil, src.error("unterminated character set", src.tell()-here)
		}
		if this == "]" && len(set) > 0 {
			break
		}
		var code1 setItem
		var in1 bool
		if this[0] == '\\' {
			code1, in1, err = src.classEscape(this)
			if err != nil {
				return nil, err
			}
		} else {
			code1 = setItem{kind: setLiteral, lo: []rune(this)[0]}
		}
		dash, err := src.match("-")
		if err != nil {
			return nil, err
		}
		if !dash {
			set = append(set, code1)
			continue
		}
		that, ok, err := src.get()
		if err != nil {
			return nil, err
		}
		if !ok {
			return nil, src.error("unterminated character set", src.tell()-here)
		}
		if that == "]" {
			set = append(set, code1, setItem{kind: setLiteral, lo: '-'})
			break
		}
		var code2 setItem
		var in2 bool
		if that[0] == '\\' {
			code2, in2, err = src.classEscape(that)
			if err != nil {
				return nil, err
			}
		} else {
			code2 = setItem{kind: setLiteral, lo: []rune(that)[0]}
		}
		msg := fmt.Sprintf("bad character range %s-%s", this, that)
		if in1 || in2 || code1.kind != setLiteral || code2.kind != setLiteral {
			return nil, src.error(msg, runeLen(this)+1+runeLen(that))
		}
		if code2.lo < code1.lo {
			return nil, src.error(msg, runeLen(this)+1+runeLen(that))
		}
		set = append(set, setItem{kind: setRange, lo: code1.lo, hi: code2.lo})
	}
	set = uniq(set)
	if len(set) == 1 && set[0].kind == setLiteral {
		if negate {
			return &node{op: opNotLiteral, ch: set[0].lo}, nil
		}
		return &node{op: opLiteral, ch: set[0].lo}, nil
	}
	return &node{op: opIn, negate: negate, set: set}, nil
}

func uniq(set []setItem) []setItem {
	seen := map[setItem]bool{}
	var out []setItem
	for _, s := range set {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	return out
}

func (p *parser) repeat(sub seq, this string) (seq, error) {
	src := p.src
	here := src.tell()
	var lo, hi int
	switch this {
	case "?":
		lo, hi = 0, 1
	case "*":
		lo, hi = 0, maxRepeat
	case "+":
		lo, hi = 1, maxRepeat
	case "{":
		if src.has && src.next == "}" {
			return append(sub, &node{op: opLiteral, ch: '{'}), nil
		}
		lo, hi = 0, maxRepeat
		var los, his string
		for src.nextIn(digits) {
			d, _, err := src.get()
			if err != nil {
				return nil, err
			}
			los += d
		}
		comma, err := src.match(",")
		if err != nil {
			return nil, err
		}
		if comma {
			for src.nextIn(digits) {
				d, _, err := src.get()
				if err != nil {
					return nil, err
				}
				his += d
			}
		} else {
			his = los
		}
		closed, err := src.match("}")
		if err != nil {
			return nil, err
		}
		if !closed {
			if err := src.seek(here); err != nil {
				return nil, err
			}
			return append(sub, &node{op: opLiteral, ch: '{'}), nil
		}
		if los != "" {
			v, ok := repeatCount(los)
			if !ok {
				return nil, errOverflow
			}
			lo = v
		}
		if his != "" {
			v, ok := repeatCount(his)
			if !ok {
				return nil, errOverflow
			}
			hi = v
			if hi < lo {
				return nil, src.error("min repeat greater than max repeat", src.tell()-here)
			}
		}
	}
	if len(sub) == 0 || sub[len(sub)-1].op == opAt {
		return nil, src.error("nothing to repeat", src.tell()-here+runeLen(this))
	}
	last := sub[len(sub)-1]
	if last.op == opRepeat {
		return nil, src.error("multiple repeat", src.tell()-here+runeLen(this))
	}
	item := seq{last}
	if last.op == opGroup && last.group == 0 && last.add == 0 && last.del == 0 {
		item = last.sub
	}
	kind := greedy
	if ok, err := src.match("?"); err != nil {
		return nil, err
	} else if ok {
		kind = lazy
	} else if ok, err := src.match("+"); err != nil {
		return nil, err
	} else if ok {
		kind = possessive
	}
	sub[len(sub)-1] = &node{op: opRepeat, min: lo, max: hi, kind: kind, sub: item}
	return sub, nil
}

// repeatCount は {m,n} の数。Python の MAXREPEAT 以上なら偽。
func repeatCount(s string) (int, bool) {
	s = strings.TrimLeft(s, "0")
	if len(s) > 10 {
		return 0, false
	}
	if s == "" {
		return 0, true
	}
	v, _ := strconv.ParseUint(s, 10, 64)
	if v >= maxRepeat {
		return 0, false
	}
	return int(v), true
}

func (p *parser) group(sub seq, verbose bool, nested int, first bool) (seq, bool, error) {
	src := p.src
	start := src.tell() - 1
	capture := true
	atomic := false
	name, named := "", false
	var addFlags, delFlags Flag
	unterminated := func() error { return src.error("missing ), unterminated subpattern", src.tell()-start) }
	q, err := src.match("?")
	if err != nil {
		return nil, false, err
	}
	if q {
		char, ok, err := src.get()
		if err != nil {
			return nil, false, err
		}
		if !ok {
			return nil, false, src.error("unexpected end of pattern", 0)
		}
		switch {
		case char == "P":
			lt, err := src.match("<")
			if err != nil {
				return nil, false, err
			}
			if lt {
				name, err = src.getuntil(">", "group name")
				if err != nil {
					return nil, false, err
				}
				if err := src.checkgroupname(name, 1); err != nil {
					return nil, false, err
				}
				named = true
				break
			}
			eq, err := src.match("=")
			if err != nil {
				return nil, false, err
			}
			if eq {
				name, err = src.getuntil(")", "group name")
				if err != nil {
					return nil, false, err
				}
				if err := src.checkgroupname(name, 1); err != nil {
					return nil, false, err
				}
				gid, ok := p.state.groupdict[name]
				if !ok {
					return nil, false, src.error("unknown group name "+pyfmt.Repr(name), runeLen(name)+1)
				}
				if !p.state.checkgroup(gid) {
					return nil, false, src.error("cannot refer to an open group", runeLen(name)+1)
				}
				if err := p.checklookbehindgroup(gid); err != nil {
					return nil, false, err
				}
				return append(sub, &node{op: opGroupRef, group: gid}), false, nil
			}
			c, ok, err := src.get()
			if err != nil {
				return nil, false, err
			}
			if !ok {
				return nil, false, src.error("unexpected end of pattern", 0)
			}
			return nil, false, src.error("unknown extension ?P"+c, runeLen(c)+2)
		case char == ":":
			capture = false
		case char == "#":
			for {
				if !src.has {
					return nil, false, src.error("missing ), unterminated comment", src.tell()-start)
				}
				c, _, err := src.get()
				if err != nil {
					return nil, false, err
				}
				if c == ")" {
					break
				}
			}
			return sub, false, nil
		case char == "=" || char == "!" || char == "<":
			behind := false
			saved := p.state.lookbehind
			if char == "<" {
				c, ok, err := src.get()
				if err != nil {
					return nil, false, err
				}
				if !ok {
					return nil, false, src.error("unexpected end of pattern", 0)
				}
				if c != "=" && c != "!" {
					return nil, false, src.error("unknown extension ?<"+c, runeLen(c)+2)
				}
				char = c
				behind = true
				if saved < 0 {
					p.state.lookbehind = p.state.groups()
				}
			}
			body, err := p.parseSub(verbose, nested+1)
			if err != nil {
				return nil, false, err
			}
			if behind && saved < 0 {
				p.state.lookbehind = -1
			}
			closed, err := src.match(")")
			if err != nil {
				return nil, false, err
			}
			if !closed {
				return nil, false, unterminated()
			}
			switch {
			case char == "=":
				return append(sub, &node{op: opAssert, behind: behind, sub: body}), false, nil
			case len(body) > 0:
				return append(sub, &node{op: opAssertNot, behind: behind, sub: body}), false, nil
			default:
				return append(sub, &node{op: opFailure}), false, nil
			}
		case char == "(":
			condname, err := src.getuntil(")", "group name")
			if err != nil {
				return nil, false, err
			}
			var condgroup int
			if !isASCIIDecimal(condname) {
				if err := src.checkgroupname(condname, 1); err != nil {
					return nil, false, err
				}
				g, ok := p.state.groupdict[condname]
				if !ok {
					return nil, false, src.error("unknown group name "+pyfmt.Repr(condname), runeLen(condname)+1)
				}
				condgroup = g
			} else {
				v, ok := repeatCount(condname)
				if !ok || v >= maxGroups {
					return nil, false, src.error(fmt.Sprintf("invalid group reference %s", strings.TrimLeft(condname, "0")), runeLen(condname)+1)
				}
				condgroup = v
				if condgroup == 0 {
					return nil, false, src.error("bad group number", runeLen(condname)+1)
				}
				if _, ok := p.state.grouprefpos[condgroup]; !ok {
					p.state.grouprefpos[condgroup] = src.tell() - runeLen(condname) - 1
					p.state.grouprefKeys = append(p.state.grouprefKeys, condgroup)
				}
			}
			if err := p.checklookbehindgroup(condgroup); err != nil {
				return nil, false, err
			}
			yes, err := p.parse(verbose, nested+1, false)
			if err != nil {
				return nil, false, err
			}
			n := &node{op: opGroupExists, group: condgroup, yes: yes}
			bar, err := src.match("|")
			if err != nil {
				return nil, false, err
			}
			if bar {
				no, err := p.parse(verbose, nested+1, false)
				if err != nil {
					return nil, false, err
				}
				n.no, n.hasNo = no, true
				if src.has && src.next == "|" {
					return nil, false, src.error("conditional backref with more than two branches", 0)
				}
			}
			closed, err := src.match(")")
			if err != nil {
				return nil, false, err
			}
			if !closed {
				return nil, false, unterminated()
			}
			return append(sub, n), false, nil
		case char == ">":
			capture = false
			atomic = true
		default:
			r := []rune(char)[0]
			if _, ok := flagLetters[r]; ok || char == "-" {
				add, del, global, err := p.parseFlags(char)
				if err != nil {
					return nil, false, err
				}
				if global {
					if !first || len(sub) > 0 {
						return nil, false, src.error("global flags not at the start of the expression", src.tell()-start)
					}
					return sub, true, nil
				}
				addFlags, delFlags = add, del
				capture = false
				break
			}
			return nil, false, src.error("unknown extension ?"+char, runeLen(char)+1)
		}
	}
	group := 0
	if capture {
		g, err := p.opengroup(name, named)
		if err != nil {
			return nil, false, src.error(err.Error(), runeLen(name)+1)
		}
		group = g
	}
	subVerbose := (verbose || addFlags&Verbose != 0) && delFlags&Verbose == 0
	body, err := p.parseSub(subVerbose, nested+1)
	if err != nil {
		return nil, false, err
	}
	closed, err := src.match(")")
	if err != nil {
		return nil, false, err
	}
	if !closed {
		return nil, false, unterminated()
	}
	if group > 0 {
		lo, hi := width(body, p.state)
		p.state.groupwidths[group] = &[2]int{lo, hi}
	}
	if atomic {
		return append(sub, &node{op: opAtomic, sub: body}), false, nil
	}
	return append(sub, &node{op: opGroup, group: group, add: addFlags, del: delFlags, sub: body}), false, nil
}

func isASCIIDecimal(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// parseFlags は Python の _parse_flags。global が真なら式全体の旗（state に足した）。
func (p *parser) parseFlags(char string) (add, del Flag, global bool, err error) {
	src := p.src
	if char != "-" {
		for {
			flag := flagLetters[[]rune(char)[0]]
			if char == "L" {
				return 0, 0, false, src.error("bad inline flags: cannot use 'L' flag with a str pattern", 0)
			}
			add |= flag
			if flag&typeFlags != 0 && add&typeFlags != flag {
				return 0, 0, false, src.error("bad inline flags: flags 'a', 'u' and 'L' are incompatible", 0)
			}
			c, ok, err := src.get()
			if err != nil {
				return 0, 0, false, err
			}
			if !ok {
				return 0, 0, false, src.error("missing -, : or )", 0)
			}
			char = c
			if char == ")" || char == "-" || char == ":" {
				break
			}
			if _, known := flagLetters[[]rune(char)[0]]; !known || runeLen(char) != 1 {
				msg := "missing -, : or )"
				if isAlpha(char) {
					msg = "unknown flag"
				}
				return 0, 0, false, src.error(msg, runeLen(char))
			}
		}
	}
	if char == ")" {
		p.state.flags |= add
		return 0, 0, true, nil
	}
	if char == "-" {
		c, ok, err := src.get()
		if err != nil {
			return 0, 0, false, err
		}
		if !ok {
			return 0, 0, false, src.error("missing flag", 0)
		}
		char = c
		if _, known := flagLetters[[]rune(char)[0]]; !known || runeLen(char) != 1 {
			msg := "missing flag"
			if isAlpha(char) {
				msg = "unknown flag"
			}
			return 0, 0, false, src.error(msg, runeLen(char))
		}
		for {
			flag := flagLetters[[]rune(char)[0]]
			if flag&typeFlags != 0 {
				return 0, 0, false, src.error("bad inline flags: cannot turn off flags 'a', 'u' and 'L'", 0)
			}
			del |= flag
			c, ok, err := src.get()
			if err != nil {
				return 0, 0, false, err
			}
			if !ok {
				return 0, 0, false, src.error("missing :", 0)
			}
			char = c
			if char == ":" {
				break
			}
			if _, known := flagLetters[[]rune(char)[0]]; !known || runeLen(char) != 1 {
				msg := "missing :"
				if isAlpha(char) {
					msg = "unknown flag"
				}
				return 0, 0, false, src.error(msg, runeLen(char))
			}
		}
	}
	if add&del != 0 {
		return 0, 0, false, src.error("bad inline flags: flag turned on and off", 1)
	}
	return add, del, false, nil
}

// isAlpha は Python の str.isalpha（エスケープの 2 字は偽）。
func isAlpha(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if !unicode.IsLetter(r) {
			return false
		}
	}
	return true
}

// width は Python の SubPattern.getwidth。値は maxWidth で飽和する。
func width(s seq, st *parseState) (int, int) {
	lo, hi := 0, 0
	for _, n := range s {
		var i, j int
		switch n.op {
		case opBranch:
			i, j = maxWidth, 0
			for _, a := range n.alts {
				l, h := width(a, st)
				i, j = min(i, l), max(j, h)
			}
		case opAtomic, opGroup:
			i, j = width(n.sub, st)
		case opRepeat:
			l, h := width(n.sub, st)
			i = satMul(l, n.min)
			if n.max == maxRepeat && h > 0 {
				j = maxWidth
			} else {
				j = satMul(h, n.max)
			}
		case opLiteral, opNotLiteral, opAny, opIn:
			i, j = 1, 1
		case opGroupRef:
			if w := st.groupwidths[n.group]; w != nil {
				i, j = w[0], w[1]
			}
		case opGroupExists:
			i, j = width(n.yes, st)
			if n.hasNo {
				l, h := width(n.no, st)
				i, j = min(i, l), max(j, h)
			} else {
				i = 0
			}
		}
		lo, hi = satAdd(lo, i), satAdd(hi, j)
	}
	return lo, hi
}

func satAdd(a, b int) int {
	if a > maxWidth-b {
		return maxWidth
	}
	return a + b
}

func satMul(a, b int) int {
	if a == 0 || b == 0 {
		return 0
	}
	if a > maxWidth/b {
		return maxWidth
	}
	return a * b
}
