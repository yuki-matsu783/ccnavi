package flow

import (
	"bytes"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"math"
	"math/big"
	"os"
	"sort"
	"strings"
	"syscall"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
)

// FileLimit は読むファイルの大きさの上限（バイト）。超えたら読まずに知らせる。
const FileLimit = 256 * 1024

// 読まない理由。
const (
	linked     = "ファイルか、ツリーのルートからそこまでの途中がシンボリックリンクなので読まない"
	notRegular = "ふつうのファイルではない（名前付きパイプ・デバイスなど）ので読まない"
	hardLinked = "ハードリンク（ほかのパスからも同じ中身を開ける）なので読まない。" +
		"承認済みの領域の外のパスから書き換えられる可能性がある"
	swapped = "開いているあいだに別のファイルに差し替わったので読まない"
	aliased = "YAML の別名（`*名前`）があるので読まない。" +
		"別名を使うと、同じ部分木を何度も辿らせて中身を膨らませられる"
	notUTF8 = "UTF-8 として読めない"
)

var utf8BOM = []byte("\xef\xbb\xbf")

// ReadBytes はフローのファイル（フローと同じ読み手で読む文書）の中身。読まないなら (nil, 理由)。
// 読むのはふつうのファイルで名前が 1 つのものだけ（名前付きパイプは開くと応答しなくなり、ハードリンクは
// 承認済みの領域の外の名前から書き換えられる）。treeRoot を渡せば、そこからファイルまでの途中にリンクが
// あるものも読まない。確かめてから開くまでに差し替えられても、開いたものが違えば読まない。
func ReadBytes(file, treeRoot string) ([]byte, string) {
	if treeRoot != "" && pathmatch.HasLink(treeRoot, file) {
		return nil, linked
	}
	before, err := os.Lstat(file)
	if err != nil {
		return nil, unreadable(err)
	}
	if before.Mode()&os.ModeSymlink != 0 {
		return nil, linked
	}
	if !before.Mode().IsRegular() {
		return nil, notRegular
	}
	if n, ok := ospath.HardLinks(file); ok && n > 1 {
		return nil, hardLinked
	}
	if before.Size() > FileLimit {
		return nil, fmt.Sprintf("大きすぎるので読まない（%d バイト、上限 %d）", before.Size(), FileLimit)
	}
	f, err := os.Open(file)
	if err != nil {
		return nil, unreadable(err)
	}
	defer f.Close()
	after, err := f.Stat()
	if err != nil {
		return nil, unreadable(err)
	}
	if !after.Mode().IsRegular() {
		return nil, notRegular
	}
	if !os.SameFile(before, after) {
		return nil, swapped
	}
	if n, ok := ospath.HardLinks(file); ok && n > 1 {
		return nil, hardLinked
	}
	raw, err := io.ReadAll(io.LimitReader(f, FileLimit+1))
	if err != nil {
		return nil, unreadable(err)
	}
	if len(raw) > FileLimit {
		return nil, fmt.Sprintf("大きすぎるので読まない（上限 %d バイト）", FileLimit)
	}
	return raw, ""
}

// unreadable は OSError の strerror を添えた理由（Python の exc.strerror）。番号の無いエラーは型の名前。
func unreadable(err error) string {
	why := "OSError"
	var errno syscall.Errno
	if errors.As(err, &errno) {
		text := fsio.ErrorText(errno)
		if _, rest, ok := strings.Cut(text, "] "); ok {
			text = rest
		}
		why = text
	}
	return "読めない (" + Clean(why) + ")"
}

// Load はフローを読む。(中身, 読めない理由)。読み方は ReadBytes と Parse。
func Load(file, treeRoot string) (*yaml.Map, string) {
	raw, why := ReadBytes(file, treeRoot)
	if raw == nil {
		return nil, why
	}
	return Parse(raw)
}

// Parse はフローの中身（バイト）を読み、形を確かめる。(中身, 読めない理由)。
func Parse(raw []byte) (*yaml.Map, string) {
	data, why := ParseYAML(raw)
	if why != "" {
		return nil, why
	}
	if why := ShapeProblem(data); why != "" {
		return nil, why
	}
	return data.(*yaml.Map), ""
}

// ParseYAML は YAML の 1 文書として読む（形は問わない）。フローと同じ読み手を使う別の文書（テンプレート）が、
// 形の検査だけ自分で行うためのもの。文字は UTF-8（先頭の BOM は 1 つ外す）で、不正なら読まない（置き換え文字で
// 埋めて読むと、不正な部分を省いた手順が渡る）。別名は読まない（billion laughs）。空の文書は (nil, "")。
func ParseYAML(raw []byte) (any, string) {
	raw = bytes.TrimPrefix(raw, utf8BOM)
	if _, err := fsio.Decode(raw, fsio.Strict); err != nil {
		var bad *fsio.DecodeError
		if errors.As(err, &bad) {
			return nil, fmt.Sprintf("%s（%d バイト目）", notUTF8, bad.Start+1)
		}
		return nil, notUTF8
	}
	data, err := yaml.LoadRefusingAliases(string(raw))
	if errors.Is(err, yaml.ErrAlias) {
		return nil, aliased
	}
	if err != nil {
		return nil, "YAML として読めない (" + Line(yamlProblem(err)) + ")"
	}
	return data, ""
}

// yamlProblem は YAML の読めない理由を 1 行に。問題と、あれば位置（行・桁は 1 始まり）。
func yamlProblem(err error) string {
	var e *yaml.Error
	if !errors.As(err, &e) || e.Problem == "" {
		return err.Error()
	}
	if e.ProblemMark != nil {
		return fmt.Sprintf("%s、%d 行 %d 桁", e.Problem, e.ProblemMark.Line+1, e.ProblemMark.Column+1)
	}
	return e.Problem
}

// JSONMark は AsJSON の目印のキー。JSON にそのまま載らない値をこのキーを持つオブジェクトで表す。
const JSONMark = "$ccnavi"

// safeInt は JSON の数として拡張（JavaScript）が崩さずに読める整数の範囲。
const safeInt = 1<<53 - 1

func marked(kind string, kv ...any) *pyjson.Object {
	return pyjson.NewObject(append([]any{JSONMark, kind}, kv...)...)
}

// AsJSON は読めた中身を JSON に載せる形にする（--lint --json --flow の flow.data）。拡張のフロー編集画面は
// 自分の YAML の読み手（1.2）が読んだ中身とこれを見比べ、食い違えば開かない・保存しない。文字列・真偽値・
// null・配列・文字列をキーとするマップと ±(2**53 - 1) までの整数はそのまま載せ、ほかは
// {"$ccnavi": <種類>, ...} の目印にする（float・int・map・date・datetime・bytes・set・tuple・other）。
func AsJSON(v any) any {
	switch x := v.(type) {
	case nil, bool, string:
		return x
	case int:
		if -safeInt <= x && x <= safeInt {
			return x
		}
		return marked("int", "text", pyfmt.Str(x))
	case *big.Int:
		if x.IsInt64() && -safeInt <= x.Int64() && x.Int64() <= safeInt {
			return int(x.Int64())
		}
		text := x.String()
		if len(strings.TrimPrefix(text, "-")) > pyfmt.MaxIntDigits {
			text = "(桁が多すぎる)"
		}
		return marked("int", "text", text)
	case float64:
		switch {
		case math.IsNaN(x):
			return marked("float", "text", "nan")
		case math.IsInf(x, 1):
			return marked("float", "text", "inf")
		case math.IsInf(x, -1):
			return marked("float", "text", "-inf")
		}
		return marked("float", "value", x)
	case []any:
		out := make([]any, len(x))
		for i, item := range x {
			out[i] = AsJSON(item)
		}
		return out
	case *yaml.Map:
		if keys, ok := x.StringKeys(); ok && !x.Has(JSONMark) {
			out := pyjson.NewObject()
			for _, k := range keys {
				out.Set(k, AsJSON(x.Get(k)))
			}
			return out
		}
		items := []any{}
		for k, item := range x.All() {
			items = append(items, []any{AsJSON(k), AsJSON(item)})
		}
		return marked("map", "items", items)
	case yaml.DateTime:
		return marked("datetime", "text", x.ISOFormat())
	case yaml.Date:
		return marked("date", "text", x.ISOFormat())
	case []byte:
		return marked("bytes", "text", base64.StdEncoding.EncodeToString(x))
	case yaml.Set:
		shown := make([]string, len(x))
		for i, item := range x {
			shown[i] = pyfmt.Repr(item)
		}
		sort.Strings(shown)
		return marked("set", "text", Line(pyfmt.Repr(shown)))
	case yaml.Tuple:
		return marked("tuple", "text", Line(x.PyRepr()))
	}
	return marked("other", "text", fmt.Sprintf("%T", v))
}
