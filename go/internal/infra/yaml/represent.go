package yaml

import (
	"encoding/base64"
	"errors"
	"fmt"
	"math"
	"math/big"
	"reflect"
	"strings"

	"ccnavi/internal/infra/pyfmt"
)

// ErrUnrepresentable は書き出せない型の値（PyYAML の RepresenterError）。
var ErrUnrepresentable = errors.New("cannot represent an object")

// DumpOptions は yaml.safe_dump の引数のうち、ccnavi が使う組み合わせ。allow_unicode=True・sort_keys=False は
// いつも付けたものとして書く（呼び手のすべてがそう呼ぶ）。
type DumpOptions struct {
	// Width は行を折る幅（width）。0 は既定の 80。PyYAML と同じく 4 以下は既定になる。
	Width int
	// Flow は default_flow_style=True。偽なら列とマップをブロックで書く。
	Flow bool
	// Quoted は default_style='"'。文字列だけでなく、すべてのスカラーを二重引用符で書く。
	Quoted bool
	// Literal が真を返す文字列（キーも）は literal block（|）を求める。書けなければ PyYAML が引用符を選ぶ。
	Literal func(string) bool
	// NoAliases は ignore_aliases を常に真にする（同じ値が 2 か所にあっても &id001 を付けない）。
	NoAliases bool
}

// Dump は PyYAML の yaml.safe_dump(data, allow_unicode=True, sort_keys=False, ...) と同じ文字列を返す。
// 値は Load が返す型（nil・bool・int・*big.Int・float64・string・[]byte・[]any・*Map・Set・Tuple・Date・DateTime）と
// []string を受ける。同じ *Map・列が 2 か所にあれば、PyYAML と同じく &id001 と *id001 で書く。
func Dump(data any, opts DumpOptions) (string, error) {
	r := &representer{opts: opts, seen: map[identity]*dnode{}}
	root, err := r.represent(data)
	if err != nil {
		return "", err
	}
	return emit(serialize(root), opts.Width), nil
}

// Quoted は文字列を、YAML が文字列として読み戻せる二重引用符の表記にする。
// Python の yaml.safe_dump(value, allow_unicode=True, default_style='"').strip()。80 字を超えると折り、
// 続きの行は 2 字下げる（折れた表記は複数行になる）。
func Quoted(value string) string {
	out, _ := Dump(value, DumpOptions{Quoted: true})
	return strings.TrimSpace(out)
}

// Put は key の値を置く。同じキー（Python の等しさ）があれば値だけを変え、位置は最初のまま（dict の代入）。
func (m *Map) Put(key, value any) { m.set(key, value) }

type dumpKind int

const (
	scalarNode dumpKind = iota
	sequenceNode
	mappingNode
)

// scalarStyle は節や事象に付ける書き方。styleNone は「決めていない」（PyYAML の None）。
type scalarStyle int

const (
	styleNone scalarStyle = iota
	stylePlain
	styleSingle
	styleDouble
	styleLiteral
)

type dnode struct {
	kind  dumpKind
	tag   string
	value string
	style scalarStyle
	items []*dnode
	pairs [][2]*dnode
	flow  bool
}

// identity は PyYAML の id(data) に当たる。Go で同一性を持つ値（*Map と、要素のある列）だけを見る。
type identity struct {
	ptr  uintptr
	kind reflect.Kind
	n    int
}

type representer struct {
	opts DumpOptions
	seen map[identity]*dnode
}

func (r *representer) key(data any) (identity, bool) {
	if r.opts.NoAliases {
		return identity{}, false
	}
	v := reflect.ValueOf(data)
	switch data.(type) {
	case *Map:
		if v.IsNil() {
			return identity{}, false
		}
		return identity{ptr: v.Pointer(), kind: reflect.Pointer}, true
	case []any, []string, Set, Tuple:
		// 空の列は Go では同一性を持たない（PyYAML は空の tuple だけを別名にしない）。
		if v.Len() == 0 {
			return identity{}, false
		}
		return identity{ptr: v.Pointer(), kind: reflect.Slice, n: v.Len()}, true
	}
	return identity{}, false
}

func (r *representer) represent(data any) (*dnode, error) {
	id, tracked := r.key(data)
	if tracked {
		if n, ok := r.seen[id]; ok {
			return n, nil
		}
	}
	remember := func(n *dnode) *dnode {
		if tracked {
			r.seen[id] = n
		}
		return n
	}
	switch x := data.(type) {
	case nil:
		return r.scalar(tagNull, "null", styleNone), nil
	case string:
		style := styleNone
		if r.opts.Literal != nil && r.opts.Literal(x) {
			style = styleLiteral
		}
		return r.scalar(tagStr, x, style), nil
	case []byte:
		return r.scalar(tagBinary, encodeBytes(x), styleLiteral), nil
	case bool:
		return r.scalar(tagBool, map[bool]string{true: "true", false: "false"}[x], styleNone), nil
	case int:
		return r.scalar(tagInt, fmt.Sprint(x), styleNone), nil
	case *big.Int:
		return r.scalar(tagInt, x.String(), styleNone), nil
	case float64:
		return r.scalar(tagFloat, floatText(x), styleNone), nil
	case Date:
		return r.scalar(tagTimestamp, x.ISOFormat(), styleNone), nil
	case DateTime:
		return r.scalar(tagTimestamp, x.PyStr(), styleNone), nil
	case []any:
		return r.sequence(remember(&dnode{kind: sequenceNode, tag: tagSeq}), x)
	case Tuple:
		return r.sequence(remember(&dnode{kind: sequenceNode, tag: tagSeq}), x)
	case []string:
		items := make([]any, len(x))
		for i, s := range x {
			items[i] = s
		}
		return r.sequence(remember(&dnode{kind: sequenceNode, tag: tagSeq}), items)
	case *Map:
		n := remember(&dnode{kind: mappingNode, tag: tagMap, flow: r.opts.Flow})
		for k, v := range x.All() {
			if err := r.pair(n, k, v); err != nil {
				return nil, err
			}
		}
		return n, nil
	case Set:
		n := remember(&dnode{kind: mappingNode, tag: tagSet, flow: r.opts.Flow})
		for _, k := range x {
			if err := r.pair(n, k, nil); err != nil {
				return nil, err
			}
		}
		return n, nil
	}
	return nil, fmt.Errorf("%w: %T", ErrUnrepresentable, data)
}

func (r *representer) scalar(tag, value string, style scalarStyle) *dnode {
	if style == styleNone && r.opts.Quoted {
		style = styleDouble
	}
	return &dnode{kind: scalarNode, tag: tag, value: value, style: style}
}

func (r *representer) sequence(n *dnode, items []any) (*dnode, error) {
	n.flow = r.opts.Flow
	for _, item := range items {
		child, err := r.represent(item)
		if err != nil {
			return nil, err
		}
		n.items = append(n.items, child)
	}
	return n, nil
}

func (r *representer) pair(n *dnode, key, value any) error {
	k, err := r.represent(key)
	if err != nil {
		return err
	}
	v, err := r.represent(value)
	if err != nil {
		return err
	}
	n.pairs = append(n.pairs, [2]*dnode{k, v})
	return nil
}

// floatText は SafeRepresenter.represent_float。
func floatText(f float64) string {
	switch {
	case math.IsNaN(f):
		return ".nan"
	case math.IsInf(f, 1):
		return ".inf"
	case math.IsInf(f, -1):
		return "-.inf"
	}
	value := strings.ToLower(pyfmt.Float(f))
	if !strings.Contains(value, ".") && strings.Contains(value, "e") {
		value = strings.Replace(value, "e", ".0e", 1)
	}
	return value
}

// encodeBytes は Python の base64.encodebytes（76 字ごとに改行し、最後にも改行を付ける）。
func encodeBytes(data []byte) string {
	const line = 76
	text := base64.StdEncoding.EncodeToString(data)
	var b strings.Builder
	for len(text) > 0 {
		n := min(line, len(text))
		b.WriteString(text[:n] + "\n")
		text = text[n:]
	}
	return b.String()
}

// 事象（PyYAML の events）。
type outKind int

const (
	outStreamStart outKind = iota
	outStreamEnd
	outDocumentStart
	outDocumentEnd
	outAlias
	outScalar
	outSequenceStart
	outSequenceEnd
	outMappingStart
	outMappingEnd
)

type outEvent struct {
	kind     outKind
	anchor   string
	tag      string
	implicit [2]bool // スカラーは (plain で読めば同じタグ, 引用符で読めば同じタグ)、集まりは [0] だけ
	value    []rune
	style    scalarStyle
	flow     bool
}

// serialize は PyYAML の Serializer。2 回以上出てくる節に id001 から順に名前を付ける。
func serialize(root *dnode) []outEvent {
	anchors := map[*dnode]string{}
	seen := map[*dnode]bool{}
	last := 0
	var mark func(n *dnode)
	mark = func(n *dnode) {
		if seen[n] {
			if anchors[n] == "" {
				last++
				anchors[n] = fmt.Sprintf("id%03d", last)
			}
			return
		}
		seen[n] = true
		for _, item := range n.items {
			mark(item)
		}
		for _, p := range n.pairs {
			mark(p[0])
			mark(p[1])
		}
	}
	mark(root)

	events := []outEvent{{kind: outStreamStart}, {kind: outDocumentStart}}
	done := map[*dnode]bool{}
	var walk func(n *dnode)
	walk = func(n *dnode) {
		anchor := anchors[n]
		if done[n] {
			events = append(events, outEvent{kind: outAlias, anchor: anchor})
			return
		}
		done[n] = true
		switch n.kind {
		case scalarNode:
			events = append(events, outEvent{
				kind: outScalar, anchor: anchor, tag: n.tag, value: []rune(n.value), style: n.style,
				implicit: [2]bool{n.tag == resolveScalar(n.value, true), n.tag == tagStr},
			})
		case sequenceNode:
			events = append(events, outEvent{kind: outSequenceStart, anchor: anchor, tag: n.tag, implicit: [2]bool{n.tag == tagSeq}, flow: n.flow})
			for _, item := range n.items {
				walk(item)
			}
			events = append(events, outEvent{kind: outSequenceEnd})
		case mappingNode:
			events = append(events, outEvent{kind: outMappingStart, anchor: anchor, tag: n.tag, implicit: [2]bool{n.tag == tagMap}, flow: n.flow})
			for _, p := range n.pairs {
				walk(p[0])
				walk(p[1])
			}
			events = append(events, outEvent{kind: outMappingEnd})
		}
	}
	walk(root)
	return append(events, outEvent{kind: outDocumentEnd}, outEvent{kind: outStreamEnd})
}
