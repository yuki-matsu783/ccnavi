package yaml

import (
	"regexp"
	"strings"
)

// YAML 1.1 の型のタグ。
const (
	tagPrefix    = "tag:yaml.org,2002:"
	tagNull      = tagPrefix + "null"
	tagBool      = tagPrefix + "bool"
	tagInt       = tagPrefix + "int"
	tagFloat     = tagPrefix + "float"
	tagBinary    = tagPrefix + "binary"
	tagTimestamp = tagPrefix + "timestamp"
	tagOmap      = tagPrefix + "omap"
	tagPairs     = tagPrefix + "pairs"
	tagSet       = tagPrefix + "set"
	tagStr       = tagPrefix + "str"
	tagSeq       = tagPrefix + "seq"
	tagMap       = tagPrefix + "map"
	tagMerge     = tagPrefix + "merge"
	tagValue     = tagPrefix + "value"
	tagYAML      = tagPrefix + "yaml"
)

// boolWords は YAML 1.1 の真偽の語（小文字）。YAML 1.2 と違い yes/no/on/off も真偽になる。
// y/n は含まない（PyYAML も含めない）。綴りは小文字・先頭だけ大文字・大文字の 3 通りを読む。
var boolWords = map[string]bool{"yes": true, "no": false, "true": true, "false": false, "on": true, "off": false}

// fullMatch は Python の re.match(r"^(?:...)$") と同じ。Python の $ は末尾の改行 1 つの前でも当たる。
func fullMatch(pattern string) *regexp.Regexp {
	return regexp.MustCompile(`^(?:` + pattern + `)\n?\z`)
}

func boolPattern() string {
	var words []string
	for w := range boolWords {
		words = append(words, w, strings.ToUpper(w[:1])+w[1:], strings.ToUpper(w))
	}
	return strings.Join(words, "|")
}

// timestampPattern は日付か日時（YAML 1.1 の timestamp）。組み立てでも同じ形で欄を取り出す。
const timestampPattern = `(?P<year>[0-9][0-9][0-9][0-9])-(?P<month>[0-9][0-9]?)-(?P<day>[0-9][0-9]?)` +
	`(?:(?:[Tt]|[ \t]+)(?P<hour>[0-9][0-9]?):(?P<minute>[0-9][0-9]):(?P<second>[0-9][0-9])` +
	`(?:\.(?P<fraction>[0-9]*))?` +
	`(?:[ \t]*(?P<tz>Z|(?P<tz_sign>[-+])(?P<tz_hour>[0-9][0-9]?)(?::(?P<tz_minute>[0-9][0-9]))?))?)?`

// implicitResolver はクォートしない値から型を決める規則。first は値の先頭に来うる文字（"" は空の値）。
type implicitResolver struct {
	tag   string
	re    *regexp.Regexp
	first string
	empty bool
}

// implicitResolvers は PyYAML の Resolver と同じ規則を同じ順に並べる（先に当たったものを使う）。
// YAML 1.1 の読み: 0 始まりは 8 進、0o17 は文字列、1e3 は文字列（指数に符号と小数点が要る）、
// 1:30 は 60 進の 90、2026-01-01 は日付。
var implicitResolvers = []implicitResolver{
	{tag: tagBool, re: fullMatch(boolPattern()), first: "yYnNtTfFoO"},
	{tag: tagFloat, re: fullMatch(`[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?` +
		`|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?` +
		`|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*` +
		`|[-+]?\.(?:inf|Inf|INF)` +
		`|\.(?:nan|NaN|NAN)`), first: "-+0123456789."},
	{tag: tagInt, re: fullMatch(`[-+]?0b[0-1_]+` +
		`|[-+]?0[0-7_]+` +
		`|[-+]?(?:0|[1-9][0-9_]*)` +
		`|[-+]?0x[0-9a-fA-F_]+` +
		`|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+`), first: "-+0123456789"},
	{tag: tagMerge, re: fullMatch(`<<`), first: "<"},
	{tag: tagNull, re: fullMatch(`~|null|Null|NULL|`), first: "~nN", empty: true},
	{tag: tagTimestamp, re: fullMatch(`[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]` +
		`|[0-9][0-9][0-9][0-9]-[0-9][0-9]?-[0-9][0-9]?(?:[Tt]|[ \t]+)[0-9][0-9]?:[0-9][0-9]:[0-9][0-9]` +
		`(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?`), first: "0123456789"},
	{tag: tagValue, re: fullMatch(`=`), first: "="},
	{tag: tagYAML, re: fullMatch(`!|&|\*`), first: "!&*"},
}

// resolveScalar はタグの無いスカラーのタグ。implicit（クォートしない値か "!"）のときだけ値を見る。
func resolveScalar(value string, implicit bool) string {
	if implicit {
		for _, r := range implicitResolvers {
			var applies bool
			if value == "" {
				applies = r.empty
			} else {
				applies = strings.ContainsRune(r.first, []rune(value)[0])
			}
			if applies && r.re.MatchString(value) {
				return r.tag
			}
		}
	}
	return tagStr
}
