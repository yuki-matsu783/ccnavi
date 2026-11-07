package yaml

import "ccnavi/internal/infra/pyfmt"

type eventKind int

const (
	eStreamStart eventKind = iota
	eStreamEnd
	eDocumentStart
	eDocumentEnd
	eAlias
	eScalar
	eSequenceStart
	eSequenceEnd
	eMappingStart
	eMappingEnd
)

type event struct {
	kind       eventKind
	anchor     string // 空はアンカー無し
	tag        string // 空はタグ無し
	implicit   bool   // スカラー: 型を値から決める（クォートしない値か "!"）。コレクション: タグが無いか "!"
	value      string
	start, end Mark
	flow       bool
}

// defaultTagHandles は %TAG が無いときのタグの頭。
var defaultTagHandles = map[string]string{"!": "!", "!!": "tag:yaml.org,2002:"}

// parser は PyYAML の Parser と同じ状態機械で、字句をイベントに組む。
type parser struct {
	sc         *scanner
	current    *event
	tagHandles map[string]string
	states     []func() (*event, error)
	marks      []Mark
	state      func() (*event, error)
}

func newParser(buf []rune) *parser {
	p := &parser{sc: newScanner(buf)}
	p.state = p.parseStreamStart
	return p
}

func (p *parser) peekEvent() (*event, error) {
	if p.current == nil && p.state != nil {
		ev, err := p.state()
		if err != nil {
			return nil, err
		}
		p.current = ev
	}
	return p.current, nil
}

func (p *parser) checkEvent(kind eventKind) (bool, error) {
	ev, err := p.peekEvent()
	if err != nil || ev == nil {
		return false, err
	}
	return ev.kind == kind, nil
}

func (p *parser) getEvent() (*event, error) {
	ev, err := p.peekEvent()
	p.current = nil
	return ev, err
}

func (p *parser) popState() {
	p.state = p.states[len(p.states)-1]
	p.states = p.states[:len(p.states)-1]
}

func (p *parser) popMark() {
	p.marks = p.marks[:len(p.marks)-1]
}

func (p *parser) parseStreamStart() (*event, error) {
	t, err := p.sc.getToken()
	if err != nil {
		return nil, err
	}
	p.state = p.parseImplicitDocumentStart
	return &event{kind: eStreamStart, start: t.start, end: t.end}, nil
}

func (p *parser) parseImplicitDocumentStart() (*event, error) {
	explicit, err := p.sc.checkToken(tDirective, tDocumentStart, tStreamEnd)
	if err != nil {
		return nil, err
	}
	if explicit {
		return p.parseDocumentStart()
	}
	p.tagHandles = defaultTagHandles
	t, err := p.sc.peekToken()
	if err != nil {
		return nil, err
	}
	p.states = append(p.states, p.parseDocumentEnd)
	p.state = p.parseBlockNode
	return &event{kind: eDocumentStart, start: t.start, end: t.start}, nil
}

func (p *parser) parseDocumentStart() (*event, error) {
	for {
		ok, err := p.sc.checkToken(tDocumentEnd)
		if err != nil {
			return nil, err
		}
		if !ok {
			break
		}
		if _, err := p.sc.getToken(); err != nil {
			return nil, err
		}
	}
	end, err := p.sc.checkToken(tStreamEnd)
	if err != nil {
		return nil, err
	}
	if end {
		t, err := p.sc.getToken()
		if err != nil {
			return nil, err
		}
		p.state = nil
		return &event{kind: eStreamEnd, start: t.start, end: t.end}, nil
	}
	t, err := p.sc.peekToken()
	if err != nil {
		return nil, err
	}
	start := t.start
	if err := p.processDirectives(); err != nil {
		return nil, err
	}
	ok, err := p.sc.checkToken(tDocumentStart)
	if err != nil {
		return nil, err
	}
	if !ok {
		t, err := p.sc.peekToken()
		if err != nil {
			return nil, err
		}
		return nil, markedError(parserError, "", nil, "expected '<document start>', but found "+quoteID(t), markPtr(t.start))
	}
	t, err = p.sc.getToken()
	if err != nil {
		return nil, err
	}
	p.states = append(p.states, p.parseDocumentEnd)
	p.state = p.parseDocumentContent
	return &event{kind: eDocumentStart, start: start, end: t.end}, nil
}

func quoteID(t *token) string { return pyfmt.Quote(tokenIDs[t.kind]) }

func (p *parser) parseDocumentEnd() (*event, error) {
	t, err := p.sc.peekToken()
	if err != nil {
		return nil, err
	}
	start, end := t.start, t.start
	ok, err := p.sc.checkToken(tDocumentEnd)
	if err != nil {
		return nil, err
	}
	if ok {
		t, err := p.sc.getToken()
		if err != nil {
			return nil, err
		}
		end = t.end
	}
	p.state = p.parseDocumentStart
	return &event{kind: eDocumentEnd, start: start, end: end}, nil
}

func (p *parser) parseDocumentContent() (*event, error) {
	empty, err := p.sc.checkToken(tDirective, tDocumentStart, tDocumentEnd, tStreamEnd)
	if err != nil {
		return nil, err
	}
	if !empty {
		return p.parseBlockNode()
	}
	t, err := p.sc.peekToken()
	if err != nil {
		return nil, err
	}
	p.popState()
	return emptyScalar(t.start), nil
}

func (p *parser) processDirectives() error {
	hasVersion := false
	p.tagHandles = map[string]string{}
	for {
		ok, err := p.sc.checkToken(tDirective)
		if err != nil {
			return err
		}
		if !ok {
			break
		}
		t, err := p.sc.getToken()
		if err != nil {
			return err
		}
		switch t.name {
		case "YAML":
			if hasVersion {
				return markedError(parserError, "", nil, "found duplicate YAML directive", markPtr(t.start))
			}
			if t.major != 1 {
				return markedError(parserError, "", nil, "found incompatible YAML document (version 1.* is required)", markPtr(t.start))
			}
			hasVersion = true
		case "TAG":
			if _, dup := p.tagHandles[t.handle]; dup {
				return markedError(parserError, "", nil, "duplicate tag handle "+pyfmt.Quote(t.handle), markPtr(t.start))
			}
			p.tagHandles[t.handle] = t.prefix
		}
	}
	for k, v := range defaultTagHandles {
		if _, ok := p.tagHandles[k]; !ok {
			p.tagHandles[k] = v
		}
	}
	return nil
}

func (p *parser) parseBlockNode() (*event, error) { return p.parseNode(true, false) }

func (p *parser) parseFlowNode() (*event, error) { return p.parseNode(false, false) }

func (p *parser) parseBlockNodeOrIndentlessSequence() (*event, error) { return p.parseNode(true, true) }

func (p *parser) parseNode(block, indentlessSequence bool) (*event, error) {
	sc := p.sc
	if ok, err := sc.checkToken(tAlias); err != nil {
		return nil, err
	} else if ok {
		t, err := sc.getToken()
		if err != nil {
			return nil, err
		}
		p.popState()
		return &event{kind: eAlias, anchor: t.value, start: t.start, end: t.end}, nil
	}
	var anchor string
	var tagToken *token
	var start, end, tagMark *Mark
	takeTag := func() error {
		t, err := sc.getToken()
		if err != nil {
			return err
		}
		tagToken = t
		tagMark = markPtr(t.start)
		end = markPtr(t.end)
		return nil
	}
	if ok, err := sc.checkToken(tAnchor); err != nil {
		return nil, err
	} else if ok {
		t, err := sc.getToken()
		if err != nil {
			return nil, err
		}
		start, end = markPtr(t.start), markPtr(t.end)
		anchor = t.value
		if ok, err := sc.checkToken(tTag); err != nil {
			return nil, err
		} else if ok {
			if err := takeTag(); err != nil {
				return nil, err
			}
		}
	} else if ok, err := sc.checkToken(tTag); err != nil {
		return nil, err
	} else if ok {
		if err := takeTag(); err != nil {
			return nil, err
		}
		start = tagMark
		if ok, err := sc.checkToken(tAnchor); err != nil {
			return nil, err
		} else if ok {
			t, err := sc.getToken()
			if err != nil {
				return nil, err
			}
			end = markPtr(t.end)
			anchor = t.value
		}
	}
	tag := ""
	if tagToken != nil {
		if tagToken.hasHandle {
			prefix, ok := p.tagHandles[tagToken.handle]
			if !ok {
				return nil, markedError(parserError, "while parsing a node", start,
					"found undefined tag handle "+pyfmt.Quote(tagToken.handle), tagMark)
			}
			tag = prefix + tagToken.prefix
		} else {
			tag = tagToken.prefix
		}
	}
	if start == nil {
		t, err := sc.peekToken()
		if err != nil {
			return nil, err
		}
		start, end = markPtr(t.start), markPtr(t.start)
	}
	implicit := tag == "" || tag == "!"
	ev := &event{anchor: anchor, tag: tag, implicit: implicit, start: *start}
	if indentlessSequence {
		if ok, err := sc.checkToken(tBlockEntry); err != nil {
			return nil, err
		} else if ok {
			t, err := sc.peekToken()
			if err != nil {
				return nil, err
			}
			ev.kind, ev.end = eSequenceStart, t.end
			p.state = p.parseIndentlessSequenceEntry
			return ev, nil
		}
	}
	t, err := sc.peekToken()
	if err != nil {
		return nil, err
	}
	switch {
	case t.kind == tScalar:
		if _, err := sc.getToken(); err != nil {
			return nil, err
		}
		ev.kind, ev.end, ev.value = eScalar, t.end, t.value
		ev.implicit = t.plain && tag == "" || tag == "!"
		p.popState()
	case t.kind == tFlowSequenceStart:
		ev.kind, ev.end, ev.flow = eSequenceStart, t.end, true
		p.state = p.parseFlowSequenceFirstEntry
	case t.kind == tFlowMappingStart:
		ev.kind, ev.end, ev.flow = eMappingStart, t.end, true
		p.state = p.parseFlowMappingFirstKey
	case block && t.kind == tBlockSequenceStart:
		ev.kind, ev.end = eSequenceStart, t.start
		p.state = p.parseBlockSequenceFirstEntry
	case block && t.kind == tBlockMappingStart:
		ev.kind, ev.end = eMappingStart, t.start
		p.state = p.parseBlockMappingFirstKey
	case anchor != "" || tag != "":
		// 中身の無いノード（`key: !!str` など）は空のスカラー。
		ev.kind, ev.end = eScalar, *end
		p.popState()
	default:
		node := "flow"
		if block {
			node = "block"
		}
		return nil, markedError(parserError, "while parsing a "+node+" node", start,
			"expected the node content, but found "+quoteID(t), markPtr(t.start))
	}
	return ev, nil
}

func emptyScalar(m Mark) *event {
	return &event{kind: eScalar, implicit: true, start: m, end: m}
}

// check は次の字句が kinds のどれかなら真。
func (p *parser) check(kinds ...tokenKind) (bool, error) { return p.sc.checkToken(kinds...) }

func (p *parser) parseBlockSequenceFirstEntry() (*event, error) {
	t, err := p.sc.getToken()
	if err != nil {
		return nil, err
	}
	p.marks = append(p.marks, t.start)
	return p.parseBlockSequenceEntry()
}

func (p *parser) parseBlockSequenceEntry() (*event, error) {
	if ok, err := p.check(tBlockEntry); err != nil {
		return nil, err
	} else if ok {
		t, err := p.sc.getToken()
		if err != nil {
			return nil, err
		}
		if ok, err := p.check(tBlockEntry, tBlockEnd); err != nil {
			return nil, err
		} else if !ok {
			p.states = append(p.states, p.parseBlockSequenceEntry)
			return p.parseBlockNode()
		}
		p.state = p.parseBlockSequenceEntry
		return emptyScalar(t.end), nil
	}
	return p.blockEnd("while parsing a block collection", eSequenceEnd)
}

// blockEnd はブロックのコレクションの終わり（<block end>）を読む。
func (p *parser) blockEnd(context string, kind eventKind) (*event, error) {
	t, err := p.sc.peekToken()
	if err != nil {
		return nil, err
	}
	if t.kind != tBlockEnd {
		return nil, markedError(parserError, context, markPtr(p.marks[len(p.marks)-1]),
			"expected <block end>, but found "+quoteID(t), markPtr(t.start))
	}
	if _, err := p.sc.getToken(); err != nil {
		return nil, err
	}
	p.popState()
	p.popMark()
	return &event{kind: kind, start: t.start, end: t.end}, nil
}

func (p *parser) parseIndentlessSequenceEntry() (*event, error) {
	if ok, err := p.check(tBlockEntry); err != nil {
		return nil, err
	} else if ok {
		t, err := p.sc.getToken()
		if err != nil {
			return nil, err
		}
		if ok, err := p.check(tBlockEntry, tKey, tValue, tBlockEnd); err != nil {
			return nil, err
		} else if !ok {
			p.states = append(p.states, p.parseIndentlessSequenceEntry)
			return p.parseBlockNode()
		}
		p.state = p.parseIndentlessSequenceEntry
		return emptyScalar(t.end), nil
	}
	t, err := p.sc.peekToken()
	if err != nil {
		return nil, err
	}
	p.popState()
	return &event{kind: eSequenceEnd, start: t.start, end: t.start}, nil
}

func (p *parser) parseBlockMappingFirstKey() (*event, error) {
	t, err := p.sc.getToken()
	if err != nil {
		return nil, err
	}
	p.marks = append(p.marks, t.start)
	return p.parseBlockMappingKey()
}

func (p *parser) parseBlockMappingKey() (*event, error) {
	if ok, err := p.check(tKey); err != nil {
		return nil, err
	} else if ok {
		t, err := p.sc.getToken()
		if err != nil {
			return nil, err
		}
		if ok, err := p.check(tKey, tValue, tBlockEnd); err != nil {
			return nil, err
		} else if !ok {
			p.states = append(p.states, p.parseBlockMappingValue)
			return p.parseBlockNodeOrIndentlessSequence()
		}
		p.state = p.parseBlockMappingValue
		return emptyScalar(t.end), nil
	}
	return p.blockEnd("while parsing a block mapping", eMappingEnd)
}

func (p *parser) parseBlockMappingValue() (*event, error) {
	ok, err := p.check(tValue)
	if err != nil {
		return nil, err
	}
	if !ok {
		p.state = p.parseBlockMappingKey
		t, err := p.sc.peekToken()
		if err != nil {
			return nil, err
		}
		return emptyScalar(t.start), nil
	}
	t, err := p.sc.getToken()
	if err != nil {
		return nil, err
	}
	if ok, err := p.check(tKey, tValue, tBlockEnd); err != nil {
		return nil, err
	} else if !ok {
		p.states = append(p.states, p.parseBlockMappingKey)
		return p.parseBlockNodeOrIndentlessSequence()
	}
	p.state = p.parseBlockMappingKey
	return emptyScalar(t.end), nil
}

func (p *parser) parseFlowSequenceFirstEntry() (*event, error) {
	t, err := p.sc.getToken()
	if err != nil {
		return nil, err
	}
	p.marks = append(p.marks, t.start)
	return p.parseFlowSequenceEntry(true)
}

func (p *parser) parseFlowSequenceNext() (*event, error) { return p.parseFlowSequenceEntry(false) }

func (p *parser) parseFlowSequenceEntry(first bool) (*event, error) {
	closing, err := p.check(tFlowSequenceEnd)
	if err != nil {
		return nil, err
	}
	if !closing {
		if !first {
			if ok, err := p.check(tFlowEntry); err != nil {
				return nil, err
			} else if !ok {
				t, err := p.sc.peekToken()
				if err != nil {
					return nil, err
				}
				return nil, markedError(parserError, "while parsing a flow sequence", markPtr(p.marks[len(p.marks)-1]),
					"expected ',' or ']', but got "+quoteID(t), markPtr(t.start))
			}
			if _, err := p.sc.getToken(); err != nil {
				return nil, err
			}
		}
		t, err := p.sc.peekToken()
		if err != nil {
			return nil, err
		}
		if t.kind == tKey {
			p.state = p.parseFlowSequenceEntryMappingKey
			return &event{kind: eMappingStart, implicit: true, start: t.start, end: t.end, flow: true}, nil
		}
		if t.kind != tFlowSequenceEnd {
			p.states = append(p.states, p.parseFlowSequenceNext)
			return p.parseFlowNode()
		}
	}
	t, err := p.sc.getToken()
	if err != nil {
		return nil, err
	}
	p.popState()
	p.popMark()
	return &event{kind: eSequenceEnd, start: t.start, end: t.end}, nil
}

func (p *parser) parseFlowSequenceEntryMappingKey() (*event, error) {
	t, err := p.sc.getToken()
	if err != nil {
		return nil, err
	}
	if ok, err := p.check(tValue, tFlowEntry, tFlowSequenceEnd); err != nil {
		return nil, err
	} else if !ok {
		p.states = append(p.states, p.parseFlowSequenceEntryMappingValue)
		return p.parseFlowNode()
	}
	p.state = p.parseFlowSequenceEntryMappingValue
	return emptyScalar(t.end), nil
}

func (p *parser) parseFlowSequenceEntryMappingValue() (*event, error) {
	ok, err := p.check(tValue)
	if err != nil {
		return nil, err
	}
	if !ok {
		p.state = p.parseFlowSequenceEntryMappingEnd
		t, err := p.sc.peekToken()
		if err != nil {
			return nil, err
		}
		return emptyScalar(t.start), nil
	}
	t, err := p.sc.getToken()
	if err != nil {
		return nil, err
	}
	if ok, err := p.check(tFlowEntry, tFlowSequenceEnd); err != nil {
		return nil, err
	} else if !ok {
		p.states = append(p.states, p.parseFlowSequenceEntryMappingEnd)
		return p.parseFlowNode()
	}
	p.state = p.parseFlowSequenceEntryMappingEnd
	return emptyScalar(t.end), nil
}

func (p *parser) parseFlowSequenceEntryMappingEnd() (*event, error) {
	p.state = p.parseFlowSequenceNext
	t, err := p.sc.peekToken()
	if err != nil {
		return nil, err
	}
	return &event{kind: eMappingEnd, start: t.start, end: t.start}, nil
}

func (p *parser) parseFlowMappingFirstKey() (*event, error) {
	t, err := p.sc.getToken()
	if err != nil {
		return nil, err
	}
	p.marks = append(p.marks, t.start)
	return p.parseFlowMappingKey(true)
}

func (p *parser) parseFlowMappingNext() (*event, error) { return p.parseFlowMappingKey(false) }

func (p *parser) parseFlowMappingKey(first bool) (*event, error) {
	closing, err := p.check(tFlowMappingEnd)
	if err != nil {
		return nil, err
	}
	if !closing {
		if !first {
			if ok, err := p.check(tFlowEntry); err != nil {
				return nil, err
			} else if !ok {
				t, err := p.sc.peekToken()
				if err != nil {
					return nil, err
				}
				return nil, markedError(parserError, "while parsing a flow mapping", markPtr(p.marks[len(p.marks)-1]),
					"expected ',' or '}', but got "+quoteID(t), markPtr(t.start))
			}
			if _, err := p.sc.getToken(); err != nil {
				return nil, err
			}
		}
		t, err := p.sc.peekToken()
		if err != nil {
			return nil, err
		}
		if t.kind == tKey {
			if _, err := p.sc.getToken(); err != nil {
				return nil, err
			}
			if ok, err := p.check(tValue, tFlowEntry, tFlowMappingEnd); err != nil {
				return nil, err
			} else if !ok {
				p.states = append(p.states, p.parseFlowMappingValue)
				return p.parseFlowNode()
			}
			p.state = p.parseFlowMappingValue
			return emptyScalar(t.end), nil
		}
		if t.kind != tFlowMappingEnd {
			p.states = append(p.states, p.parseFlowMappingEmptyValue)
			return p.parseFlowNode()
		}
	}
	t, err := p.sc.getToken()
	if err != nil {
		return nil, err
	}
	p.popState()
	p.popMark()
	return &event{kind: eMappingEnd, start: t.start, end: t.end}, nil
}

func (p *parser) parseFlowMappingValue() (*event, error) {
	ok, err := p.check(tValue)
	if err != nil {
		return nil, err
	}
	if !ok {
		p.state = p.parseFlowMappingNext
		t, err := p.sc.peekToken()
		if err != nil {
			return nil, err
		}
		return emptyScalar(t.start), nil
	}
	t, err := p.sc.getToken()
	if err != nil {
		return nil, err
	}
	if ok, err := p.check(tFlowEntry, tFlowMappingEnd); err != nil {
		return nil, err
	} else if !ok {
		p.states = append(p.states, p.parseFlowMappingNext)
		return p.parseFlowNode()
	}
	p.state = p.parseFlowMappingNext
	return emptyScalar(t.end), nil
}

func (p *parser) parseFlowMappingEmptyValue() (*event, error) {
	p.state = p.parseFlowMappingNext
	t, err := p.sc.peekToken()
	if err != nil {
		return nil, err
	}
	return emptyScalar(t.start), nil
}
