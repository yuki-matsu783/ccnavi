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

// maxDepth は組み立てるコレクションの入れ子の上限。PyYAML は再帰の上限（1000 階層）で
// およそ 490 階層の入れ子から読めなくなる。それに合わせて、深い文書を同じく「入れ子が深すぎる」で断る。
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
		return nil, markedError(composerError, "expected a single document in the stream", markPtr(doc.start),
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

// composeNode はイベントの列から節を 1 つ組む。コレクションは子を再帰で組む（depth は入れ子の深さ）。
func (c *composer) composeNode(depth int) (*node, error) {
	if depth > maxDepth {
		return nil, tooDeep()
	}
	// 1. 別名は、アンカーが指す節をそのまま返す（同じ節を共有する。拒む読み手ではここで断る）。
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
			return nil, markedError(composerError, "", nil, "found undefined alias "+pyfmt.Quote(ev.anchor), markPtr(ev.start))
		}
		return n, nil
	}
	// 2. アンカーの重複を断り、節を作ってアンカーに登録する。
	//    登録は中身を組む前に行う（自分を指す別名を、あとで深さの上限で断るため）。
	if ev.anchor != "" {
		if first, dup := c.anchors[ev.anchor]; dup {
			return nil, markedError(composerError, "found duplicate anchor "+pyfmt.Quote(ev.anchor)+"; first occurrence",
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
	// 3. 種類ごとに中身を組む。タグが無い・"!" のときは、スカラーは内容から、列とマップは既定のタグを付ける。
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
