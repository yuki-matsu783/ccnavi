package pyre

import (
	"errors"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/pyfmt"
)

// inst は照合に使う形。旗（大文字小文字・改行・ASCII）はここで解いておく。
type inst struct {
	op      opcode
	test    func(rune) bool // 1 字を読むもの（LITERAL・NOT_LITERAL・ANY・IN）
	alts    [][]*inst
	group   int
	sub     []*inst
	simple  func(rune) bool // 繰り返しの中身が 1 字を読むものだけなら、その照合
	min     int
	max     int
	kind    repeatKind
	at      atCode
	multi   bool
	ascii   bool
	ignore  bool
	behind  int // 後読みの幅。-1 なら先読み
	yes, no []*inst
}

// Regexp は組み立てた式。並行に使ってよい。
type Regexp struct {
	pattern string
	prog    []*inst
	groups  int
	names   map[string]int
	anchor  bool // 先頭でしか当たらない（\A か、複数行でない ^ で始まる）
}

// Compile は Python の re.compile(pattern, flags)。
func Compile(pattern string, flags Flag) (*Regexp, error) {
	s, st, err := parsePattern(pattern, flags)
	if err != nil {
		return nil, err
	}
	prog, err := compileSeq(s, st.flags, st)
	if err != nil {
		return nil, err
	}
	re := &Regexp{pattern: pattern, prog: prog, groups: st.groups(), names: st.groupdict}
	if len(prog) > 0 && prog[0].op == opAt && (prog[0].at == atBeginningString || prog[0].at == atBeginning && !prog[0].multi) {
		re.anchor = true
	}
	return re, nil
}

// MustCompile は Compile と同じだが、組めなければ panic する。起動時に組む、このビルドに書いた固定の式だけに使う
// （組めないのはビルドの不備で、テストが起動の時点で落ちる）。利用者が書いた式は Compile で読む。
func MustCompile(pattern string, flags Flag) *Regexp {
	re, err := Compile(pattern, flags)
	if err != nil {
		panic("pyre: 固定の式 " + strconv.Quote(pattern) + " を組めない: " + err.Error())
	}
	return re
}

func combine(flags, add, del Flag) Flag {
	if add&typeFlags != 0 {
		flags &^= typeFlags
	}
	return (flags | add) &^ del
}

func compileSeq(s seq, fl Flag, st *parseState) ([]*inst, error) {
	out := make([]*inst, 0, len(s))
	for _, n := range s {
		in, err := compileNode(n, fl, st)
		if err != nil {
			return nil, err
		}
		out = append(out, in)
	}
	return out, nil
}

// unitTest は 1 字を読むノードの照合。1 字を読むノードでなければ nil。
func unitTest(n *node, fl Flag) func(rune) bool {
	switch n.op {
	case opLiteral:
		return literalTest(n.ch, false, fl)
	case opNotLiteral:
		return literalTest(n.ch, true, fl)
	case opAny:
		if fl&DotAll != 0 {
			return func(rune) bool { return true }
		}
		return func(r rune) bool { return r != '\n' }
	case opIn:
		return setTest(n.set, n.negate, fl)
	}
	return nil
}

// simpleTest は Python の _simple（中身が 1 字を読むノード 1 つ）。そうでなければ nil。
func simpleTest(s seq, fl Flag) func(rune) bool {
	if len(s) != 1 {
		return nil
	}
	n := s[0]
	if n.op == opGroup && n.group == 0 {
		return simpleTest(n.sub, combine(fl, n.add, n.del))
	}
	return unitTest(n, fl)
}

func compileNode(n *node, fl Flag, st *parseState) (*inst, error) {
	if t := unitTest(n, fl); t != nil {
		return &inst{op: n.op, test: t}, nil
	}
	in := &inst{op: n.op, group: n.group}
	var err error
	switch n.op {
	case opBranch:
		for _, a := range n.alts {
			c, err := compileSeq(a, fl, st)
			if err != nil {
				return nil, err
			}
			in.alts = append(in.alts, c)
		}
	case opGroup:
		in.sub, err = compileSeq(n.sub, combine(fl, n.add, n.del), st)
	case opRepeat:
		in.min, in.max, in.kind = n.min, n.max, n.kind
		if t := simpleTest(n.sub, fl); t != nil {
			in.simple = t
		} else {
			in.sub, err = compileSeq(n.sub, fl, st)
		}
	case opAtomic:
		in.sub, err = compileSeq(n.sub, fl, st)
	case opAt:
		in.at, in.multi, in.ascii = n.at, fl&Multiline != 0, fl&unicodeOn == 0
	case opGroupRef:
		in.ignore, in.ascii = fl&IgnoreCase != 0, fl&unicodeOn == 0
	case opGroupExists:
		if in.yes, err = compileSeq(n.yes, fl, st); err == nil && n.hasNo {
			in.no, err = compileSeq(n.no, fl, st)
		}
	case opAssert, opAssertNot:
		in.behind = -1
		if n.behind {
			lo, hi := width(n.sub, st)
			if lo > maxCode {
				return nil, &Error{Msg: "looks too much behind"}
			}
			if lo != hi {
				return nil, &Error{Msg: "look-behind requires fixed-width pattern"}
			}
			in.behind = lo
		}
		in.sub, err = compileSeq(n.sub, fl, st)
	}
	if err != nil {
		return nil, err
	}
	return in, nil
}

// Pattern は書いた式（Python の Pattern.pattern）。
func (re *Regexp) Pattern() string { return re.pattern }

// Group は名前の付いたグループの番号と、その名前があるか（Python の groupindex）。
func (re *Regexp) Group(name string) (int, bool) {
	g, ok := re.names[name]
	return g, ok
}

// Search は文字列のどこかに当たるか（Python の search）。照合を打ち切ったときは偽（当たったかを言えない）。
// 打ち切りを当たりとして扱う呼び手（止める側の照合）は SearchChecked を使う。
func (re *Regexp) Search(s string) bool {
	ok, _ := re.SearchChecked(s)
	return ok
}

// SearchChecked は Search と同じだが、照合を打ち切ったら ErrDepthLimit を返す（当たりは偽）。
func (re *Regexp) SearchChecked(s string) (bool, error) {
	_, ok, err := re.search([]rune(s), 0, false)
	return ok, err
}

// Match は先頭から当たるか（Python の match）。
func (re *Regexp) Match(s string) bool {
	_, ok := re.MatchIn(s, 0, maxPos)
	return ok
}

// FullMatch は文字列全体に当たるか（Python の fullmatch）。
func (re *Regexp) FullMatch(s string) bool {
	m := re.newMatcher([]rune(s), true, false, 0)
	_, ok := m.try(re.prog, 0)
	return ok
}

// maxPos は endpos を文字列の終わりまでにするときに渡す値。
const maxPos = int(^uint(0) >> 1)

// Found は当たった 1 件（Python の Match）。位置は符号位置。
type Found struct {
	Start, End int
	text       []rune
	marks      []int
	re         *Regexp
}

// Text は当たった部分。
func (f Found) Text() string { return string(f.text[f.Start:f.End]) }

// Named は名前の付いたグループが捕えた文字列と、捕えたか（Python の m.group(name) が None でないか）。
func (f Found) Named(name string) (string, bool) {
	g, ok := f.re.names[name]
	if !ok {
		return "", false
	}
	s, e := f.marks[2*g], f.marks[2*g+1]
	if s < 0 || e < 0 || e < s {
		return "", false
	}
	return string(f.text[s:e]), true
}

// window は Python の pos・endpos を文字列の長さに収め、endpos で切った文字列を返す（sre と同じく、
// 後ろは endpos で終わったものとして読み、前は pos より手前も後読みと \b が見る）。pos が endpos を
// 越えれば偽（Python は None を返す）。
func window(s string, pos, endpos int) ([]rune, int, bool) {
	text := []rune(s)
	pos = min(max(pos, 0), len(text))
	endpos = min(max(endpos, 0), len(text))
	if pos > endpos {
		return nil, 0, false
	}
	return text[:endpos], pos, true
}

// Find は Python の search（最初に当たった所）。
func (re *Regexp) Find(s string) (Found, bool) { return re.FindIn(s, 0, maxPos) }

// FindIn は Python の search(s, pos, endpos)。位置は符号位置で、範囲の外は Python と同じく端に寄せる。
func (re *Regexp) FindIn(s string, pos, endpos int) (Found, bool) {
	text, pos, ok := window(s, pos, endpos)
	if !ok {
		return Found{}, false
	}
	m, ok, _ := re.search(text, pos, false)
	if !ok {
		return Found{}, false
	}
	return Found{Start: m.start, End: m.end, text: text, marks: slices.Clone(m.marks), re: re}, true
}

// MatchIn は Python の match(s, pos, endpos)。pos から当たるか。^ と \A は pos でなく文字列の先頭にだけ当たる。
func (re *Regexp) MatchIn(s string, pos, endpos int) (Found, bool) {
	text, pos, ok := window(s, pos, endpos)
	if !ok {
		return Found{}, false
	}
	m := re.newMatcher(text, false, false, pos)
	end, ok := m.try(re.prog, pos)
	if !ok {
		return Found{}, false
	}
	return Found{Start: pos, End: end, text: text, marks: m.marks, re: re}, true
}

// FindAll は Python の finditer。空の一致のすぐ後ろは、空でない一致だけを採る（3.7 からの規則）。
func (re *Regexp) FindAll(s string) []Found { return re.FindAllIn(s, 0, maxPos) }

// FindAllIn は Python の finditer(s, pos, endpos)。
func (re *Regexp) FindAllIn(s string, pos, endpos int) []Found {
	text, pos, ok := window(s, pos, endpos)
	if !ok {
		return nil
	}
	var out []Found
	advance := false
	for pos <= len(text) {
		m, ok, _ := re.search(text, pos, advance)
		if !ok {
			break
		}
		f := Found{Start: m.start, End: m.end, text: text, marks: slices.Clone(m.marks), re: re}
		out = append(out, f)
		advance = f.End == f.Start
		pos = f.End
	}
	return out
}

// Sub は Python の re.sub(pattern, repl, s) で repl に関数を渡した形。当たった所を repl の答えに置き換える。
// 置き換えの文字列のテンプレート（\1・\g<name>）は読まない（固定の文字列なら、それを返す関数を渡す）。
func (re *Regexp) Sub(s string, repl func(Found) string) string {
	found := re.FindAll(s)
	if len(found) == 0 {
		return s
	}
	text := found[0].text
	var b strings.Builder
	last := 0
	for _, f := range found {
		b.WriteString(string(text[last:f.Start]))
		b.WriteString(repl(f))
		last = f.End
	}
	b.WriteString(string(text[last:]))
	return b.String()
}

type span struct {
	start, end int
	marks      []int
}

func (re *Regexp) search(text []rune, pos int, advance bool) (span, bool, error) {
	last := len(text)
	if re.anchor {
		last = min(pos, last)
		if pos > 0 {
			return span{}, false, nil
		}
	}
	m := re.newMatcher(text, false, false, 0)
	for start := pos; start <= last; start++ {
		// 当たらなかった試しは捕えた位置を元に戻して終わるので、同じ matcher を使い回せる。
		m.advance, m.start = advance && start == pos, start
		if end, ok := m.try(re.prog, start); ok {
			return span{start: start, end: end, marks: m.marks}, true, nil
		}
		if m.aborted {
			return span{}, false, ErrDepthLimit
		}
	}
	return span{}, false, nil
}

// DepthLimit は照合の入れ子（run の深さ）の上限。後戻りの照合は続きを呼び出しで積むので、繰り返しを含む式を
// 長い文字列に当てると深さが文字数に比例して伸び、Go のスタックの上限（越えると回復できずにプロセスが落ちる）に届く。
// Python の sre はヒープに積むので落ちない。上限を越えた照合は打ち切り、ErrDepthLimit で知らせる。
// 値は数 KB の通常のコマンドでは届かず、最も深く積む形でもスタックが 128 MB ほどに収まる大きさ
// （1 段に数百バイト積む。32 ビットの機械のスタックの上限 250 MB にも収まる）。
const DepthLimit = 1 << 18

// ErrDepthLimit は照合が DepthLimit を越えて打ち切られたこと。当たったかどうかは分からない。
var ErrDepthLimit = errors.New("pyre: 照合の入れ子が上限を越えたので打ち切った")

type matcher struct {
	s        []rune
	marks    []int
	stack    []int
	matchAll bool
	advance  bool
	start    int
	// depth は run の入れ子の深さ。aborted は DepthLimit を越えて照合を打ち切ったこと（立ったら答えは使わない）。
	depth   int
	aborted bool
}

func (re *Regexp) newMatcher(s []rune, matchAll, advance bool, start int) *matcher {
	marks := make([]int, 2*re.groups)
	for i := range marks {
		marks[i] = -1
	}
	return &matcher{s: s, marks: marks, matchAll: matchAll, advance: advance, start: start}
}

func (m *matcher) try(prog []*inst, start int) (int, bool) {
	end := -1
	ok := m.run(prog, start, func(j int) bool {
		if m.matchAll && j != len(m.s) || m.advance && j == m.start {
			return false
		}
		end = j
		return true
	})
	return end, ok
}

type cont func(int) bool

func accept(int) bool { return true }

// run は prog を i から照合し、続き k に渡す。入れ子が DepthLimit を越えたら打ち切る。
// 打ち切った後は、どの経路も偽で戻る（否定の先読みが偽を「当たらなかった」と読んで通らないよう、
// 呼び手は答えより先に aborted を見る）。
func (m *matcher) run(prog []*inst, i int, k cont) bool {
	if m.aborted {
		return false
	}
	if m.depth >= DepthLimit {
		m.aborted = true
		return false
	}
	m.depth++
	ok := m.step(prog, i, k)
	m.depth--
	return ok && !m.aborted
}

func (m *matcher) step(prog []*inst, i int, k cont) bool {
	for idx, n := range prog {
		if n.test != nil {
			if i < len(m.s) && n.test(m.s[i]) {
				i++
				continue
			}
			return false
		}
		if n.op == opAt {
			if !m.at(n, i) {
				return false
			}
			continue
		}
		next := k
		if rest := prog[idx+1:]; len(rest) > 0 {
			next = func(j int) bool { return m.run(rest, j, k) }
		}
		return m.node(n, i, next)
	}
	return k(i)
}

func (m *matcher) isWord(i int, ascii bool) bool {
	if i < 0 || i >= len(m.s) {
		return false
	}
	if ascii {
		return isASCIIWord(m.s[i])
	}
	return pyfmt.IsWord(m.s[i])
}

func (m *matcher) at(n *inst, i int) bool {
	end := len(m.s)
	switch n.at {
	case atBeginning:
		return i == 0 || n.multi && m.s[i-1] == '\n'
	case atBeginningString:
		return i == 0
	case atEnd:
		if n.multi {
			return i == end || m.s[i] == '\n'
		}
		return i == end || i+1 == end && m.s[i] == '\n'
	case atEndString:
		return i == end
	case atBoundary:
		if end == 0 {
			return false
		}
		return m.isWord(i-1, n.ascii) != m.isWord(i, n.ascii)
	default:
		if end == 0 {
			return false
		}
		return m.isWord(i-1, n.ascii) == m.isWord(i, n.ascii)
	}
}

// save は捕えた位置を控えの山に積み、その場所を返す。back で戻し、drop で山を下ろす（呼ぶたびに割り当てない）。
func (m *matcher) save() int {
	at := len(m.stack)
	m.stack = append(m.stack, m.marks...)
	return at
}

func (m *matcher) back(at int) {
	copy(m.marks, m.stack[at:at+len(m.marks)])
	m.stack = m.stack[:at]
}

func (m *matcher) drop(at int) { m.stack = m.stack[:at] }

func (m *matcher) node(n *inst, i int, k cont) bool {
	switch n.op {
	case opBranch:
		for _, alt := range n.alts {
			if m.run(alt, i, k) {
				return true
			}
		}
		return false
	case opGroup:
		if n.group == 0 {
			return m.run(n.sub, i, k)
		}
		g := 2 * n.group
		s0, e0 := m.marks[g], m.marks[g+1]
		m.marks[g] = i
		if m.run(n.sub, i, func(j int) bool {
			e := m.marks[g+1]
			m.marks[g+1] = j
			if k(j) {
				return true
			}
			m.marks[g+1] = e
			return false
		}) {
			return true
		}
		m.marks[g], m.marks[g+1] = s0, e0
		return false
	case opRepeat:
		if n.simple != nil {
			return m.repeatOne(n, i, k)
		}
		switch n.kind {
		case greedy:
			return m.maxUntil(n, i, 0, -1, k)
		case lazy:
			return m.minUntil(n, i, 0, -1, k)
		default:
			return m.possessive(n, i, k)
		}
	case opAtomic:
		saved := m.save()
		end := -1
		if !m.run(n.sub, i, func(j int) bool { end = j; return true }) {
			m.drop(saved)
			return false
		}
		if k(end) {
			return true
		}
		m.back(saved)
		return false
	case opAssert:
		from := i
		if n.behind >= 0 {
			if i < n.behind {
				return false
			}
			from = i - n.behind
		}
		saved := m.save()
		if !m.run(n.sub, from, accept) {
			m.back(saved)
			return false
		}
		if k(i) {
			return true
		}
		m.back(saved)
		return false
	case opAssertNot:
		if n.behind < 0 || i >= n.behind {
			saved := m.save()
			from := i
			if n.behind >= 0 {
				from = i - n.behind
			}
			hit := m.run(n.sub, from, accept)
			m.back(saved)
			if hit {
				return false
			}
		}
		return k(i)
	case opGroupRef:
		s, e := m.marks[2*n.group], m.marks[2*n.group+1]
		if s < 0 || e < 0 || e < s {
			return false
		}
		size := e - s
		if i+size > len(m.s) {
			return false
		}
		c := caser{ascii: n.ascii}
		for x := range size {
			a, b := m.s[s+x], m.s[i+x]
			if a != b && !(n.ignore && c.lower(a) == c.lower(b)) {
				return false
			}
		}
		return k(i + size)
	case opGroupExists:
		s, e := m.marks[2*n.group], m.marks[2*n.group+1]
		if 2*n.group < len(m.marks) && s >= 0 && e >= 0 && e >= s {
			return m.run(n.yes, i, k)
		}
		return m.run(n.no, i, k)
	}
	return false
}

func unlimited(n *inst, count int) bool { return n.max == maxRepeat || count < n.max }

// repeatOne は 1 字を読む中身の繰り返し（Python の REPEAT_ONE・MIN_REPEAT_ONE・POSSESSIVE_REPEAT_ONE）。
func (m *matcher) repeatOne(n *inst, i int, k cont) bool {
	if n.kind == lazy {
		c := 0
		for ; c < n.min; c++ {
			if i+c >= len(m.s) || !n.simple(m.s[i+c]) {
				return false
			}
		}
		for {
			if k(i + c) {
				return true
			}
			if !unlimited(n, c) || i+c >= len(m.s) || !n.simple(m.s[i+c]) {
				return false
			}
			c++
		}
	}
	c := 0
	for unlimited(n, c) && i+c < len(m.s) && n.simple(m.s[i+c]) {
		c++
	}
	if c < n.min {
		return false
	}
	if n.kind == possessive {
		return k(i + c)
	}
	for ; c >= n.min; c-- {
		if k(i + c) {
			return true
		}
	}
	return false
}

// maxUntil は Python の REPEAT と MAX_UNTIL。last は前の回を始めた位置で、空の回のあとはもう回さない。
func (m *matcher) maxUntil(n *inst, i, count, last int, k cont) bool {
	if count < n.min {
		return m.run(n.sub, i, func(j int) bool { return m.maxUntil(n, j, count+1, last, k) })
	}
	if unlimited(n, count) && i != last {
		if m.run(n.sub, i, func(j int) bool { return m.maxUntil(n, j, count+1, i, k) }) {
			return true
		}
	}
	return k(i)
}

// minUntil は Python の REPEAT と MIN_UNTIL。
func (m *matcher) minUntil(n *inst, i, count, last int, k cont) bool {
	if count < n.min {
		return m.run(n.sub, i, func(j int) bool { return m.minUntil(n, j, count+1, last, k) })
	}
	if k(i) {
		return true
	}
	if !unlimited(n, count) || i == last {
		return false
	}
	return m.run(n.sub, i, func(j int) bool { return m.minUntil(n, j, count+1, i, k) })
}

// possessive は Python の POSSESSIVE_REPEAT。回ごとに最初の当たりを採り、戻らない。
func (m *matcher) possessive(n *inst, i int, k cont) bool {
	saved := m.save()
	pos, count := i, 0
	end := -1
	take := func(j int) bool { end = j; return true }
	for ; count < n.min; count++ {
		if !m.run(n.sub, pos, take) {
			m.back(saved)
			return false
		}
		pos = end
	}
	last := -1
	for unlimited(n, count) && pos != last {
		last = pos
		before := m.save()
		if !m.run(n.sub, pos, take) {
			m.back(before)
			break
		}
		m.drop(before)
		count++
		pos = end
	}
	if k(pos) {
		return true
	}
	m.back(saved)
	return false
}

// Escape は Python の re.escape。
func Escape(s string) string {
	var b strings.Builder
	for _, r := range s {
		if strings.ContainsRune("()[]{}?*+-|^$\\.&~# \t\n\r\v\f", r) {
			b.WriteByte('\\')
		}
		b.WriteRune(r)
	}
	return b.String()
}
