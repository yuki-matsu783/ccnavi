package yaml

import (
	"fmt"
	"strings"
)

// emitter は PyYAML の Emitter（allow_unicode=True・字下げ 2・改行 "\n"）を写したもの。
// canonical・版と %TAG の指示・明示の --- と ...・folded（>）は ccnavi が使わないので持たない。
// 事象は Serializer が先に全部並べるので、先読みは events[pos:] を見る。
// 桁はコードポイントで数える（Python の len(str)）。literal を書く間は桁を進めない（PyYAML と同じ）。
type emitter struct {
	out    strings.Builder
	events []outEvent
	pos    int
	ev     outEvent

	states []func()
	state  func()

	indents []int
	indent  int // -1 は PyYAML の None

	flowLevel                                      int
	rootCtx, sequenceCtx, mappingCtx, simpleKeyCtx bool

	column                int
	whitespace, indention bool
	openEnded             bool

	bestIndent, bestWidth int

	preparedAnchor, preparedTag *string
	analysis                    *scalarAnalysis
	style                       scalarStyle
}

const (
	defaultWidth    = 80
	defaultIndent   = 2
	breakChars      = "\n\u0085\u2028\u2029"
	spaceBreakChars = "\x00 \t\r\n\u0085\u2028\u2029"
)

// escapes は PyYAML の ESCAPE_REPLACEMENTS。
var escapes = map[rune]string{
	0: "0", '\a': "a", '\b': "b", '\t': "t", '\n': "n", '\v': "v", '\f': "f", '\r': "r", 0x1B: "e",
	'"': `"`, '\\': `\`, 0x85: "N", 0xA0: "_", 0x2028: "L", 0x2029: "P",
}

func emit(events []outEvent, width int) string {
	e := &emitter{events: events, indent: -1, whitespace: true, indention: true, bestIndent: defaultIndent, bestWidth: defaultWidth}
	if width > e.bestIndent*2 {
		e.bestWidth = width
	}
	e.state = e.expectStreamStart
	for e.state != nil && e.pos < len(e.events) {
		e.ev = e.events[e.pos]
		e.pos++
		e.state()
	}
	return e.out.String()
}

// next は先読みの 1 件目（無ければ偽）。
func (e *emitter) next() (outEvent, bool) {
	if e.pos < len(e.events) {
		return e.events[e.pos], true
	}
	return outEvent{}, false
}

func (e *emitter) push(s func()) { e.states = append(e.states, s) }

func (e *emitter) pop() func() {
	s := e.states[len(e.states)-1]
	e.states = e.states[:len(e.states)-1]
	return s
}

func (e *emitter) popIndent() {
	e.indent = e.indents[len(e.indents)-1]
	e.indents = e.indents[:len(e.indents)-1]
}

func (e *emitter) increaseIndent(flow, indentless bool) {
	e.indents = append(e.indents, e.indent)
	switch {
	case e.indent < 0 && flow:
		e.indent = e.bestIndent
	case e.indent < 0:
		e.indent = 0
	case !indentless:
		e.indent += e.bestIndent
	}
}

func (e *emitter) expectStreamStart() { e.state = e.expectFirstDocumentStart }

func (e *emitter) expectFirstDocumentStart() { e.expectDocumentStart() }

// expectDocumentStart は文書の始まり。ccnavi は 1 文書を暗黙の始まりで書くので --- は出ない。
func (e *emitter) expectDocumentStart() {
	if e.ev.kind == outStreamEnd {
		if e.openEnded {
			e.writeIndicator("...", true, false, false)
			e.writeIndent()
		}
		e.state = nil
		return
	}
	e.state = e.expectDocumentRoot
}

func (e *emitter) expectDocumentEnd() {
	e.writeIndent()
	e.state = e.expectDocumentStart
}

func (e *emitter) expectDocumentRoot() {
	e.push(e.expectDocumentEnd)
	e.expectNode(true, false, false, false)
}

func (e *emitter) expectNode(root, sequence, mapping, simpleKey bool) {
	e.rootCtx, e.sequenceCtx, e.mappingCtx, e.simpleKeyCtx = root, sequence, mapping, simpleKey
	switch e.ev.kind {
	case outAlias:
		e.processAnchor("*")
		e.state = e.pop()
	case outScalar:
		e.processAnchor("&")
		e.processTag()
		e.increaseIndent(true, false)
		e.processScalar()
		e.popIndent()
		e.state = e.pop()
	case outSequenceStart:
		e.processAnchor("&")
		e.processTag()
		if e.flowLevel > 0 || e.ev.flow || e.checkEmpty(outSequenceStart, outSequenceEnd) {
			e.writeIndicator("[", true, true, false)
			e.flowLevel++
			e.increaseIndent(true, false)
			e.state = e.expectFirstFlowSequenceItem
		} else {
			e.increaseIndent(false, e.mappingCtx && !e.indention)
			e.state = e.expectFirstBlockSequenceItem
		}
	case outMappingStart:
		e.processAnchor("&")
		e.processTag()
		if e.flowLevel > 0 || e.ev.flow || e.checkEmpty(outMappingStart, outMappingEnd) {
			e.writeIndicator("{", true, true, false)
			e.flowLevel++
			e.increaseIndent(true, false)
			e.state = e.expectFirstFlowMappingKey
		} else {
			e.increaseIndent(false, false)
			e.state = e.expectFirstBlockMappingKey
		}
	}
}

// 流れの列。

func (e *emitter) expectFirstFlowSequenceItem() { e.flowSequenceItem(true) }

func (e *emitter) expectFlowSequenceItem() { e.flowSequenceItem(false) }

func (e *emitter) flowSequenceItem(first bool) {
	if e.ev.kind == outSequenceEnd {
		e.popIndent()
		e.flowLevel--
		e.writeIndicator("]", false, false, false)
		e.state = e.pop()
		return
	}
	if !first {
		e.writeIndicator(",", false, false, false)
	}
	if e.column > e.bestWidth {
		e.writeIndent()
	}
	e.push(e.expectFlowSequenceItem)
	e.expectNode(false, true, false, false)
}

// 流れのマップ。

func (e *emitter) expectFirstFlowMappingKey() { e.flowMappingKey(true) }

func (e *emitter) expectFlowMappingKey() { e.flowMappingKey(false) }

func (e *emitter) flowMappingKey(first bool) {
	if e.ev.kind == outMappingEnd {
		e.popIndent()
		e.flowLevel--
		e.writeIndicator("}", false, false, false)
		e.state = e.pop()
		return
	}
	if !first {
		e.writeIndicator(",", false, false, false)
	}
	if e.column > e.bestWidth {
		e.writeIndent()
	}
	if e.checkSimpleKey() {
		e.push(e.expectFlowMappingSimpleValue)
		e.expectNode(false, false, true, true)
	} else {
		e.writeIndicator("?", true, false, false)
		e.push(e.expectFlowMappingValue)
		e.expectNode(false, false, true, false)
	}
}

func (e *emitter) expectFlowMappingSimpleValue() {
	e.writeIndicator(":", false, false, false)
	e.push(e.expectFlowMappingKey)
	e.expectNode(false, false, true, false)
}

func (e *emitter) expectFlowMappingValue() {
	if e.column > e.bestWidth {
		e.writeIndent()
	}
	e.writeIndicator(":", true, false, false)
	e.push(e.expectFlowMappingKey)
	e.expectNode(false, false, true, false)
}

// ブロックの列とマップ。

func (e *emitter) expectFirstBlockSequenceItem() { e.blockSequenceItem(true) }

func (e *emitter) expectBlockSequenceItem() { e.blockSequenceItem(false) }

func (e *emitter) blockSequenceItem(first bool) {
	if !first && e.ev.kind == outSequenceEnd {
		e.popIndent()
		e.state = e.pop()
		return
	}
	e.writeIndent()
	e.writeIndicator("-", true, false, true)
	e.push(e.expectBlockSequenceItem)
	e.expectNode(false, true, false, false)
}

func (e *emitter) expectFirstBlockMappingKey() { e.blockMappingKey(true) }

func (e *emitter) expectBlockMappingKey() { e.blockMappingKey(false) }

func (e *emitter) blockMappingKey(first bool) {
	if !first && e.ev.kind == outMappingEnd {
		e.popIndent()
		e.state = e.pop()
		return
	}
	e.writeIndent()
	if e.checkSimpleKey() {
		e.push(e.expectBlockMappingSimpleValue)
		e.expectNode(false, false, true, true)
	} else {
		e.writeIndicator("?", true, false, true)
		e.push(e.expectBlockMappingValue)
		e.expectNode(false, false, true, false)
	}
}

func (e *emitter) expectBlockMappingSimpleValue() {
	e.writeIndicator(":", false, false, false)
	e.push(e.expectBlockMappingKey)
	e.expectNode(false, false, true, false)
}

func (e *emitter) expectBlockMappingValue() {
	e.writeIndent()
	e.writeIndicator(":", true, false, true)
	e.push(e.expectBlockMappingKey)
	e.expectNode(false, false, true, false)
}

// 調べる。

// checkEmpty は今の事象が start で、次が end か（空の列・マップ）。
func (e *emitter) checkEmpty(start, end outKind) bool {
	n, ok := e.next()
	return e.ev.kind == start && ok && n.kind == end
}

func (e *emitter) checkSimpleKey() bool {
	length := 0
	if e.ev.kind != outSequenceEnd && e.ev.kind != outMappingEnd && e.ev.anchor != "" {
		if e.preparedAnchor == nil {
			a := e.ev.anchor
			e.preparedAnchor = &a
		}
		length += len([]rune(*e.preparedAnchor))
	}
	if e.ev.kind == outScalar || e.ev.kind == outSequenceStart || e.ev.kind == outMappingStart {
		if e.preparedTag == nil {
			t := prepareTag(e.ev.tag)
			e.preparedTag = &t
		}
		length += len([]rune(*e.preparedTag))
	}
	if e.ev.kind == outScalar {
		if e.analysis == nil {
			e.analysis = analyzeScalar(e.ev.value)
		}
		length += len(e.analysis.scalar)
	}
	return length < 128 && (e.ev.kind == outAlias ||
		(e.ev.kind == outScalar && !e.analysis.empty && !e.analysis.multiline) ||
		e.checkEmpty(outSequenceStart, outSequenceEnd) || e.checkEmpty(outMappingStart, outMappingEnd))
}

func (e *emitter) processAnchor(indicator string) {
	if e.ev.anchor == "" {
		e.preparedAnchor = nil
		return
	}
	if e.preparedAnchor == nil {
		a := e.ev.anchor
		e.preparedAnchor = &a
	}
	if *e.preparedAnchor != "" {
		e.writeIndicator(indicator+*e.preparedAnchor, true, false, false)
	}
	e.preparedAnchor = nil
}

func (e *emitter) processTag() {
	tag := e.ev.tag
	if e.ev.kind == outScalar {
		if e.style == styleNone {
			e.style = e.chooseScalarStyle()
		}
		if (e.style == stylePlain && e.ev.implicit[0]) || (e.style != stylePlain && e.ev.implicit[1]) {
			e.preparedTag = nil
			return
		}
	} else if e.ev.implicit[0] {
		e.preparedTag = nil
		return
	}
	if e.preparedTag == nil {
		t := prepareTag(tag)
		e.preparedTag = &t
	}
	if *e.preparedTag != "" {
		e.writeIndicator(*e.preparedTag, true, false, false)
	}
	e.preparedTag = nil
}

func (e *emitter) chooseScalarStyle() scalarStyle {
	if e.analysis == nil {
		e.analysis = analyzeScalar(e.ev.value)
	}
	a := e.analysis
	if e.ev.style == styleDouble {
		return styleDouble
	}
	if e.ev.style == styleNone && e.ev.implicit[0] {
		if !(e.simpleKeyCtx && (a.empty || a.multiline)) &&
			(e.flowLevel > 0 && a.allowFlowPlain || e.flowLevel == 0 && a.allowBlockPlain) {
			return stylePlain
		}
	}
	if e.ev.style == styleLiteral && e.flowLevel == 0 && !e.simpleKeyCtx && a.allowBlock {
		return styleLiteral
	}
	if (e.ev.style == styleNone || e.ev.style == styleSingle) && a.allowSingleQuoted && !(e.simpleKeyCtx && a.multiline) {
		return styleSingle
	}
	return styleDouble
}

func (e *emitter) processScalar() {
	if e.analysis == nil {
		e.analysis = analyzeScalar(e.ev.value)
	}
	if e.style == styleNone {
		e.style = e.chooseScalarStyle()
	}
	split := !e.simpleKeyCtx
	text := e.analysis.scalar
	switch e.style {
	case styleDouble:
		e.writeDoubleQuoted(text, split)
	case styleSingle:
		e.writeSingleQuoted(text, split)
	case styleLiteral:
		e.writeLiteral(text)
	default:
		e.writePlain(text, split)
	}
	e.analysis = nil
	e.style = styleNone
}

// prepareTag は PyYAML の prepare_tag（既定の接頭辞 "!" と "tag:yaml.org,2002:" → "!!"）。
func prepareTag(tag string) string {
	if tag == "!" {
		return tag
	}
	handle, suffix := "", tag
	// 接頭辞を辞書順に見て、最後に当たったものを使う。
	for _, p := range [][2]string{{"!", "!"}, {tagPrefix, "!!"}} {
		if strings.HasPrefix(tag, p[0]) && (p[0] == "!" || len(p[0]) < len(tag)) {
			handle, suffix = p[1], tag[len(p[0]):]
		}
	}
	var b strings.Builder
	for _, ch := range suffix {
		if '0' <= ch && ch <= '9' || 'A' <= ch && ch <= 'Z' || 'a' <= ch && ch <= 'z' ||
			strings.ContainsRune("-;/?:@&=+$,_.~*'()[]", ch) || (ch == '!' && handle != "!") {
			b.WriteRune(ch)
			continue
		}
		for _, c := range []byte(string(ch)) {
			fmt.Fprintf(&b, "%%%02X", c)
		}
	}
	if handle != "" {
		return handle + b.String()
	}
	return "!<" + b.String() + ">"
}

type scalarAnalysis struct {
	scalar                                                         []rune
	empty, multiline                                               bool
	allowFlowPlain, allowBlockPlain, allowSingleQuoted, allowBlock bool
}

func isIn(ch rune, set string) bool { return strings.ContainsRune(set, ch) }

// analyzeScalar は PyYAML の analyze_scalar（allow_unicode=True）。
func analyzeScalar(scalar []rune) *scalarAnalysis {
	if len(scalar) == 0 {
		return &scalarAnalysis{scalar: scalar, empty: true, allowBlockPlain: true, allowSingleQuoted: true}
	}
	var blockIndicators, flowIndicators, lineBreaks, special bool
	var leadingSpace, leadingBreak, trailingSpace, trailingBreak, breakSpace, spaceBreak bool
	text := string(scalar)
	if strings.HasPrefix(text, "---") || strings.HasPrefix(text, "...") {
		blockIndicators, flowIndicators = true, true
	}
	precededByWS := true
	followedByWS := len(scalar) == 1 || isIn(scalar[1], spaceBreakChars)
	previousSpace, previousBreak := false, false
	for index, ch := range scalar {
		if index == 0 {
			if isIn(ch, "#,[]{}&*!|>'\"%@`") {
				flowIndicators, blockIndicators = true, true
			}
			if isIn(ch, "?:") {
				flowIndicators = true
				if followedByWS {
					blockIndicators = true
				}
			}
			if ch == '-' && followedByWS {
				flowIndicators, blockIndicators = true, true
			}
		} else {
			if isIn(ch, ",?[]{}") {
				flowIndicators = true
			}
			if ch == ':' {
				flowIndicators = true
				if followedByWS {
					blockIndicators = true
				}
			}
			if ch == '#' && precededByWS {
				flowIndicators, blockIndicators = true, true
			}
		}
		if isIn(ch, breakChars) {
			lineBreaks = true
		}
		if !(ch == '\n' || 0x20 <= ch && ch <= 0x7E) {
			unicode := (ch == 0x85 || 0xA0 <= ch && ch <= 0xD7FF || 0xE000 <= ch && ch <= 0xFFFD ||
				0x10000 <= ch && ch < 0x10FFFF) && ch != 0xFEFF
			if !unicode {
				special = true
			}
		}
		switch {
		case ch == ' ':
			if index == 0 {
				leadingSpace = true
			}
			if index == len(scalar)-1 {
				trailingSpace = true
			}
			if previousBreak {
				breakSpace = true
			}
			previousSpace, previousBreak = true, false
		case isIn(ch, breakChars):
			if index == 0 {
				leadingBreak = true
			}
			if index == len(scalar)-1 {
				trailingBreak = true
			}
			if previousSpace {
				spaceBreak = true
			}
			previousSpace, previousBreak = false, true
		default:
			previousSpace, previousBreak = false, false
		}
		precededByWS = isIn(ch, spaceBreakChars)
		followedByWS = index+2 >= len(scalar) || isIn(scalar[index+2], spaceBreakChars)
	}
	a := &scalarAnalysis{scalar: scalar, multiline: lineBreaks,
		allowFlowPlain: true, allowBlockPlain: true, allowSingleQuoted: true, allowBlock: true}
	if leadingSpace || leadingBreak || trailingSpace || trailingBreak {
		a.allowFlowPlain, a.allowBlockPlain = false, false
	}
	if trailingSpace {
		a.allowBlock = false
	}
	if breakSpace {
		a.allowFlowPlain, a.allowBlockPlain, a.allowSingleQuoted = false, false, false
	}
	if spaceBreak || special {
		a.allowFlowPlain, a.allowBlockPlain, a.allowSingleQuoted, a.allowBlock = false, false, false, false
	}
	if lineBreaks {
		a.allowFlowPlain, a.allowBlockPlain = false, false
	}
	if flowIndicators {
		a.allowFlowPlain = false
	}
	if blockIndicators {
		a.allowBlockPlain = false
	}
	return a
}

// 書く。

func (e *emitter) write(data []rune) {
	e.column += len(data)
	e.out.WriteString(string(data))
}

func (e *emitter) writeIndicator(indicator string, needWhitespace, whitespace, indention bool) {
	data := indicator
	if !e.whitespace && needWhitespace {
		data = " " + indicator
	}
	e.whitespace = whitespace
	e.indention = e.indention && indention
	e.openEnded = false
	e.write([]rune(data))
}

func (e *emitter) writeIndent() {
	indent := max(e.indent, 0)
	if !e.indention || e.column > indent || (e.column == indent && !e.whitespace) {
		e.writeLineBreak("\n")
	}
	if e.column < indent {
		e.whitespace = true
		e.out.WriteString(strings.Repeat(" ", indent-e.column))
		e.column = indent
	}
}

func (e *emitter) writeLineBreak(data string) {
	e.whitespace = true
	e.indention = true
	e.column = 0
	e.out.WriteString(data)
}

// writeBreaks は改行の並びを書く。"\n" 以外の改行文字はその字のまま書く。
func (e *emitter) writeBreaks(breaks []rune) {
	for _, br := range breaks {
		e.writeLineBreak(string(br))
	}
}

func (e *emitter) writeSingleQuoted(text []rune, split bool) {
	e.writeIndicator("'", true, false, false)
	spaces, breaks := false, false
	start := 0
	for end := 0; end <= len(text); end++ {
		ch := rune(-1)
		if end < len(text) {
			ch = text[end]
		}
		switch {
		case spaces:
			if ch != ' ' {
				if start+1 == end && e.column > e.bestWidth && split && start != 0 && end != len(text) {
					e.writeIndent()
				} else {
					e.write(text[start:end])
				}
				start = end
			}
		case breaks:
			if ch < 0 || !isIn(ch, breakChars) {
				if text[start] == '\n' {
					e.writeLineBreak("\n")
				}
				e.writeBreaks(text[start:end])
				e.writeIndent()
				start = end
			}
		default:
			if (ch < 0 || isIn(ch, " "+breakChars) || ch == '\'') && start < end {
				e.write(text[start:end])
				start = end
			}
		}
		if ch == '\'' {
			e.write([]rune("''"))
			start = end + 1
		}
		if ch >= 0 {
			spaces = ch == ' '
			breaks = isIn(ch, breakChars)
		}
	}
	e.writeIndicator("'", false, false, false)
}

func (e *emitter) writeDoubleQuoted(text []rune, split bool) {
	e.writeIndicator(`"`, true, false, false)
	start := 0
	for end := 0; end <= len(text); end++ {
		ch := rune(-1)
		if end < len(text) {
			ch = text[end]
		}
		if ch < 0 || isIn(ch, "\"\\\u0085\u2028\u2029\uFEFF") ||
			!(0x20 <= ch && ch <= 0x7E || 0xA0 <= ch && ch <= 0xD7FF || 0xE000 <= ch && ch <= 0xFFFD) {
			if start < end {
				e.write(text[start:end])
				start = end
			}
			if ch >= 0 {
				var data string
				switch r, ok := escapes[ch]; {
				case ok:
					data = `\` + r
				case ch <= 0xFF:
					data = fmt.Sprintf(`\x%02X`, ch)
				case ch <= 0xFFFF:
					data = fmt.Sprintf(`\u%04X`, ch)
				default:
					data = fmt.Sprintf(`\U%08X`, ch)
				}
				e.write([]rune(data))
				start = end + 1
			}
		}
		if 0 < end && end < len(text)-1 && (ch == ' ' || start >= end) && e.column+(end-start) > e.bestWidth && split {
			var data []rune
			if start < end { // Python の text[start:end] は start > end でも空を返す
				data = append(data, text[start:end]...)
				start = end
			}
			data = append(data, '\\')
			e.write(data)
			e.writeIndent()
			e.whitespace = false
			e.indention = false
			if text[start] == ' ' {
				e.write([]rune(`\`))
			}
		}
	}
	e.writeIndicator(`"`, false, false, false)
}

func (e *emitter) blockHints(text []rune) string {
	hints := ""
	if len(text) > 0 {
		if isIn(text[0], " "+breakChars) {
			hints += fmt.Sprint(e.bestIndent)
		}
		switch {
		case !isIn(text[len(text)-1], breakChars):
			hints += "-"
		case len(text) == 1 || isIn(text[len(text)-2], breakChars):
			hints += "+"
		}
	}
	return hints
}

func (e *emitter) writeLiteral(text []rune) {
	hints := e.blockHints(text)
	e.writeIndicator("|"+hints, true, false, false)
	if strings.HasSuffix(hints, "+") {
		e.openEnded = true
	}
	e.writeLineBreak("\n")
	breaks := true
	start := 0
	for end := 0; end <= len(text); end++ {
		ch := rune(-1)
		if end < len(text) {
			ch = text[end]
		}
		if breaks {
			if ch < 0 || !isIn(ch, breakChars) {
				e.writeBreaks(text[start:end])
				if ch >= 0 {
					e.writeIndent()
				}
				start = end
			}
		} else if ch < 0 || isIn(ch, breakChars) {
			e.out.WriteString(string(text[start:end]))
			if ch < 0 {
				e.writeLineBreak("\n")
			}
			start = end
		}
		if ch >= 0 {
			breaks = isIn(ch, breakChars)
		}
	}
}

func (e *emitter) writePlain(text []rune, split bool) {
	if e.rootCtx {
		e.openEnded = true
	}
	if len(text) == 0 {
		return
	}
	if !e.whitespace {
		e.write([]rune(" "))
	}
	e.whitespace = false
	e.indention = false
	spaces, breaks := false, false
	start := 0
	for end := 0; end <= len(text); end++ {
		ch := rune(-1)
		if end < len(text) {
			ch = text[end]
		}
		switch {
		case spaces:
			if ch != ' ' {
				if start+1 == end && e.column > e.bestWidth && split {
					e.writeIndent()
					e.whitespace = false
					e.indention = false
				} else {
					e.write(text[start:end])
				}
				start = end
			}
		case breaks:
			if ch < 0 || !isIn(ch, breakChars) {
				if text[start] == '\n' {
					e.writeLineBreak("\n")
				}
				e.writeBreaks(text[start:end])
				e.writeIndent()
				e.whitespace = false
				e.indention = false
				start = end
			}
		default:
			if ch < 0 || isIn(ch, " "+breakChars) {
				e.write(text[start:end])
				start = end
			}
		}
		if ch >= 0 {
			spaces = ch == ' '
			breaks = isIn(ch, breakChars)
		}
	}
}
