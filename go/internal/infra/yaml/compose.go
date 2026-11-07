package yaml

import "ccnavi/internal/infra/pyfmt"

type nodeKind int

const (
	nScalar nodeKind = iota
	nSequence
	nMapping
)

// nodeIDs は誤りの文に出るノードの名前。
var nodeIDs = [...]string{"scalar", "sequence", "mapping"}

type node struct {
	kind  nodeKind
	tag   string
	value string // スカラーの中身
	items []*node
	pairs []nodePair
	start Mark
}

type nodePair struct{ key, value *node }

// maxDepth は組み立てるコレクションの入れ子の上限。Python の実行ファイルは再帰の上限（1000 段）で
// およそ 490 段の入れ子から読めなくなっていた。それに合わせて、深い文書を同じく「入れ子が深すぎる」で断る。
const maxDepth = 480

type composer struct {
	p             *parser
	anchors       map[string]*node
	refuseAliases bool
}

// singleNode は文書を 1 つだけ組む。文書が無ければ nil、2 つ以上あれば読めない。
func (c *composer) singleNode() (*node, error) {
	if _, err := c.p.getEvent(); err != nil {
		return nil, err
	}
	var doc *node
	end, err := c.p.checkEvent(eStreamEnd)
	if err != nil {
		return nil, err
	}
	if !end {
		if doc, err = c.composeDocument(); err != nil {
			return nil, err
		}
	}
	end, err = c.p.checkEvent(eStreamEnd)
	if err != nil {
		return nil, err
	}
	if !end {
		ev, err := c.p.getEvent()
		if err != nil {
			return nil, err
		}
		return nil, markedError(ComposerError, "expected a single document in the stream", markPtr(doc.start),
			"but found another document", markPtr(ev.start))
	}
	_, err = c.p.getEvent()
	return doc, err
}

func (c *composer) composeDocument() (*node, error) {
	if _, err := c.p.getEvent(); err != nil {
		return nil, err
	}
	c.anchors = map[string]*node{}
	n, err := c.composeNode(1)
	if err != nil {
		return nil, err
	}
	_, err = c.p.getEvent()
	return n, err
}

func (c *composer) composeNode(depth int) (*node, error) {
	if depth > maxDepth {
		return nil, tooDeep()
	}
	ev, err := c.p.peekEvent()
	if err != nil {
		return nil, err
	}
	if ev.kind == eAlias {
		if _, err := c.p.getEvent(); err != nil {
			return nil, err
		}
		if c.refuseAliases {
			return nil, aliasError()
		}
		n, ok := c.anchors[ev.anchor]
		if !ok {
			return nil, markedError(ComposerError, "", nil, "found undefined alias "+pyfmt.Quote(ev.anchor), markPtr(ev.start))
		}
		return n, nil
	}
	if ev.anchor != "" {
		if first, dup := c.anchors[ev.anchor]; dup {
			return nil, markedError(ComposerError, "found duplicate anchor "+pyfmt.Quote(ev.anchor)+"; first occurrence",
				markPtr(first.start), "second occurrence", markPtr(ev.start))
		}
	}
	if _, err := c.p.getEvent(); err != nil {
		return nil, err
	}
	n := &node{tag: ev.tag, start: ev.start}
	if ev.anchor != "" {
		c.anchors[ev.anchor] = n
	}
	switch ev.kind {
	case eScalar:
		n.kind, n.value = nScalar, ev.value
		if n.tag == "" || n.tag == "!" {
			n.tag = resolveScalar(ev.value, ev.implicit)
		}
	case eSequenceStart:
		n.kind = nSequence
		if n.tag == "" || n.tag == "!" {
			n.tag = tagSeq
		}
		for {
			end, err := c.p.checkEvent(eSequenceEnd)
			if err != nil {
				return nil, err
			}
			if end {
				break
			}
			item, err := c.composeNode(depth + 1)
			if err != nil {
				return nil, err
			}
			n.items = append(n.items, item)
		}
		if _, err := c.p.getEvent(); err != nil {
			return nil, err
		}
	case eMappingStart:
		n.kind = nMapping
		if n.tag == "" || n.tag == "!" {
			n.tag = tagMap
		}
		for {
			end, err := c.p.checkEvent(eMappingEnd)
			if err != nil {
				return nil, err
			}
			if end {
				break
			}
			key, err := c.composeNode(depth + 1)
			if err != nil {
				return nil, err
			}
			value, err := c.composeNode(depth + 1)
			if err != nil {
				return nil, err
			}
			n.pairs = append(n.pairs, nodePair{key, value})
		}
		if _, err := c.p.getEvent(); err != nil {
			return nil, err
		}
	}
	return n, nil
}

func aliasError() *Error { return &Error{text: ErrAlias.Error(), cause: ErrAlias} }
