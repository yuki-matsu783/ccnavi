package docindex

import (
	"errors"
	"math"
	"math/big"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
)

const (
	// headLimit は frontmatter を探す頭の長さ。本文は読まない。
	headLimit = 64 * 1024
	// maxDepth は frontmatter と索引の行の入れ子の上限。Python は `--format json` の字下げを再帰で書くので、
	// これより深いものは書けないものとして扱う。行は frontmatter より 1 段深いので、frontmatter は 31 段までとなる。
	maxDepth = 32
)

// frontStyle は索引の囲いの読み方（行末の空白だけを除き、`...` でも閉じる）。
var frontStyle = yaml.FrontStyle{DocEnd: true, SkipBOM: true}

var errTooDeep = errors.New("入れ子が深すぎる")

// FrontMatter は頭の frontmatter（`---` で始まる YAML。別名は読まない）を返す。
// 無い、閉じていない、読めない、マッピングでない、JSON に書けないのいずれかなら nil を返す。
// JSON に無い値（日付・バイト列・NaN など）は文字列にする。
func FrontMatter(raw []byte) *pyjson.Object {
	if len(raw) > headLimit {
		raw = raw[:headLimit]
	}
	text, _ := fsio.DecodeBytes(raw, fsio.Replace)
	body, state := yaml.FrontBody(text, frontStyle)
	if state != yaml.FrontClosed {
		return nil
	}
	data, err := yaml.LoadRefusingAliases(body)
	if err != nil {
		return nil
	}
	if _, ok := data.(*yaml.Map); !ok {
		return nil
	}
	front, ok := jsonable(data).(*pyjson.Object)
	if !ok || checkJSON(front) != nil {
		return nil
	}
	return front
}

// jsonable は YAML の値を JSON に載る形にする（Python の _jsonable）。キーは str()、日時は isoformat() にする。
// バイト列は UTF-8 として読み、無限と NaN と残りの型は str() にする。yamljson.ToJSON とは文字列にする規則が違う。
func jsonable(v any) any {
	switch x := v.(type) {
	case *yaml.Map:
		out := pyjson.NewObject()
		for k, inner := range x.All() {
			out.Set(pyfmt.Str(k), jsonable(inner))
		}
		return out
	case []any:
		return jsonableList(x)
	case yaml.Tuple:
		return jsonableList(x)
	case nil, bool, int, *big.Int, string:
		return x
	case float64:
		if math.IsInf(x, 0) || math.IsNaN(x) {
			return pyfmt.Str(x)
		}
		return x
	case yaml.Date:
		return x.ISOFormat()
	case yaml.DateTime:
		return x.ISOFormat()
	case []byte:
		text, _ := fsio.DecodeBytes(x, fsio.Replace)
		return text
	}
	return pyfmt.Str(v)
}

func jsonableList(items []any) []any {
	out := make([]any, len(items))
	for i, inner := range items {
		out[i] = jsonable(inner)
	}
	return out
}

// checkJSON は、どの出力の形（字下げの JSON を含む）でも書けるかを確かめる。
// 深すぎる入れ子、NaN・無限、Python が文字列にできない桁の整数は書けないものとする。
func checkJSON(v any) error {
	if deep(v) {
		return errTooDeep
	}
	opts := compactOptions
	opts.RejectNaN = true
	_, err := pyjson.Dumps(v, opts)
	return err
}

// deep は入れ子が maxDepth より深いか。
func deep(v any) bool {
	found := false
	Walk(v, func(node any, depth int) bool {
		if _, isLeaf := children(node); !isLeaf && depth > maxDepth {
			found = true
		}
		return !found
	})
	return found
}

// Walk は JSON の値の入れ子を、書かれた順に頭から辿る（再帰しない。深い入れ子で落ちないように）。visit は
// 入れ物（*pyjson.Object・[]any）とそのほかの値の両方に、深さ（v が 1）とともに呼ぶ。偽を返したらやめる。
// nil の *pyjson.Object は飛ばす。
func Walk(v any, visit func(node any, depth int) bool) {
	type item struct {
		v     any
		depth int
	}
	stack := []item{{v, 1}}
	for len(stack) > 0 {
		top := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if obj, ok := top.v.(*pyjson.Object); ok && obj == nil {
			continue
		}
		if !visit(top.v, top.depth) {
			return
		}
		inner, _ := children(top.v)
		for i := len(inner) - 1; i >= 0; i-- {
			stack = append(stack, item{inner[i], top.depth + 1})
		}
	}
}

// children は入れ物の中身と、入れ物でない（葉）か。
func children(v any) ([]any, bool) {
	switch x := v.(type) {
	case *pyjson.Object:
		var values []any
		for _, inner := range x.All() {
			values = append(values, inner)
		}
		return values, false
	case []any:
		return x, false
	}
	return nil, true
}

// isBlank は Python の not s.strip()。
func isBlank(s string) bool {
	return strings.IndexFunc(s, func(r rune) bool { return !ospath.IsSpace(r) }) < 0
}
