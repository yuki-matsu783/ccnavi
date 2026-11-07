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
	// maxDepth は frontmatter と索引の行の入れ子の上限。`--format json` の字下げは Python では再帰で書くので、
	// これより深いものは書けないものとして扱う（行は frontmatter より 1 段深いので、frontmatter は 31 段まで）。
	maxDepth = 32
)

const (
	frontFence = "---"
	frontEnd   = "..."
	bom        = "\ufeff"
)

var errTooDeep = errors.New("入れ子が深すぎる")

// FrontMatter は頭の frontmatter（`---` で始まる YAML。別名は読まない）。無い・閉じていない・読めない・
// マッピングでない・JSON に書けないなら nil。JSON に無い値（日付・バイト列・NaN など）は文字列にする。
func FrontMatter(raw []byte) *pyjson.Object {
	if len(raw) > headLimit {
		raw = raw[:headLimit]
	}
	text, _ := fsio.DecodeBytes(raw, fsio.Replace)
	lines := ospath.Splitlines(strings.TrimPrefix(text, bom))
	if len(lines) == 0 || rstrip(lines[0]) != frontFence {
		return nil
	}
	end := 0
	for i := 1; i < len(lines); i++ {
		if s := rstrip(lines[i]); s == frontFence || s == frontEnd {
			end = i
			break
		}
	}
	if end == 0 {
		return nil
	}
	data, err := yaml.LoadRefusingAliases(strings.Join(lines[1:end], "\n"))
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

func rstrip(s string) string { return strings.TrimRightFunc(s, ospath.IsSpace) }

// jsonable は YAML の値を JSON に載る形にする（Python の _jsonable）。キーは str()、日時は isoformat()、
// バイト列は UTF-8 として読み、無限と NaN と残りの型は str()。yamljson.ToJSON とは文字列にする規則が違う。
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

// checkJSON はどの出力の形（字下げの JSON を含む）でも書けるかを見る。深すぎる入れ子、NaN・無限、
// Python が文字列にできない桁の整数は書けない。
func checkJSON(v any) error {
	if deep(v) {
		return errTooDeep
	}
	_, err := pyjson.Dumps(v, pyjson.Options{RawUnicode: true, Compact: true, RejectNaN: true})
	return err
}

// deep は入れ子が maxDepth より深いか。再帰しない（深い入れ子で落ちないように）。
func deep(v any) bool {
	type item struct {
		v     any
		depth int
	}
	stack := []item{{v, 1}}
	for len(stack) > 0 {
		top := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		var children []any
		switch x := top.v.(type) {
		case *pyjson.Object:
			for _, inner := range x.All() {
				children = append(children, inner)
			}
		case []any:
			children = x
		default:
			continue
		}
		if top.depth > maxDepth {
			return true
		}
		for _, child := range children {
			stack = append(stack, item{child, top.depth + 1})
		}
	}
	return false
}

// isBlank は Python の not s.strip()。
func isBlank(s string) bool {
	return strings.IndexFunc(s, func(r rune) bool { return !ospath.IsSpace(r) }) < 0
}
