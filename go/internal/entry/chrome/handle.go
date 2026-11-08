package chrome

import (
	"errors"
	"fmt"

	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// schema は要求と答えの形の版。拡張の CCNAVI_SCHEMA と揃える。
const schema = 1

// refusal は要求そのものを受けない理由。答えの error に文面を入れて返す。
type refusal struct{ text string }

func (r *refusal) Error() string { return r.text }

func refused(text string) error { return &refusal{text} }

func refusedf(format string, args ...any) error { return &refusal{fmt.Sprintf(format, args...)} }

type handler func(req *pyjson.Object, root string) (*pyjson.Object, error)

var ops map[string]handler

func init() {
	ops = map[string]handler{
		"placement": opPlacement,
		"families":  opFamilies,
		"closure":   opClosure,
		"board":     opBoard,
		"plan":      opPlan,
		"confirm":   opConfirm,
		"decide":    opDecide,
		"compat":    opCompat,
		"start":     opStart,
		"adopt":     opAdopt,
		"version":   opVersion,
	}
}

// Handle は要求 1 つに答える。root は仮のツリーを組む場所（WASM では仮想のファイルシステムの /ws、手元の試験では
// 一時ディレクトリ）。失敗しても error にせず、答えの error で返す（拡張の Worker が止まらないように）。
func Handle(request, root string) string {
	// 要求の拒否（refusal）はその文面を、想定外の失敗は失敗の文を、答えの error に入れる。
	body, err := answer(request, root)
	var r *refusal
	switch {
	case errors.As(err, &r):
		body = pyjson.NewObject("error", r.text)
	case err != nil:
		body = pyjson.NewObject("error", "ccnavi: 想定外の失敗で止まった: "+err.Error())
	}
	// schema を先頭のキーに置いて、本文のキーを続ける。拡張は schema で答えの形の版を確かめる。
	out := pyjson.NewObject("schema", schema)
	for _, k := range body.Keys() {
		out.Set(k, body.Value(k))
	}
	// 日本語はエスケープせずそのまま出す（拡張の画面に出る文面で、JSON を読む側が変換し直さずに済む）。
	text, err := pyjson.Dumps(out, pyjson.Options{RawUnicode: true})
	if err != nil {
		return fmt.Sprintf(`{"schema": %d, "error": "答えを JSON にできません"}`, schema)
	}
	return text
}

func answer(request, root string) (body *pyjson.Object, err error) {
	// 操作の中の panic は、WASM のプロセスごと落とさずに失敗の答えにする。
	defer func() {
		if r := recover(); r != nil {
			body, err = nil, fmt.Errorf("%v", r)
		}
	}()
	value, err := pyjson.Loads(request)
	if err != nil {
		return nil, refused("要求を JSON として読めません")
	}
	req, ok := value.(*pyjson.Object)
	if !ok || !pyfmt.EqualsInt(req.Value("schema"), schema) {
		return nil, refusedf("要求の形式の版が一致しません（拡張と ccnavi の組み立てがずれています。期待する版は %d です）", schema)
	}
	op, _ := req.Value("op").(string)
	h, ok := ops[op]
	if !ok {
		return nil, refusedf("未対応の操作です：%s", pyfmt.Repr(req.Value("op")))
	}
	return h(req, root)
}

// 要求の値の読み手。JSON の型が違えば ok が偽。

func text(req *pyjson.Object, key string) (string, bool) {
	v, ok := req.Value(key).(string)
	return v, ok
}

// integer は整数として読む。JSON の数は float64 で届くが、整数の欄に小数や真偽が入った要求は読まない。
func integer(v any) (int, bool) {
	switch v.(type) {
	case bool, float64, nil, string:
		return 0, false
	}
	return pyfmt.IntOf(v)
}

// stringList は文字列のリスト。リストでない（nil を含む）・文字列でない要素があれば ok が偽。
func stringList(v any) ([]string, bool) {
	list, ok := v.([]any)
	if !ok {
		return nil, false
	}
	out := make([]string, 0, len(list))
	for _, item := range list {
		s, ok := item.(string)
		if !ok {
			return nil, false
		}
		out = append(out, s)
	}
	return out, true
}

// orEmpty は値が空（nil・false・空文字・0）なら空のリスト、そうでなければ文字列のリストとして読む。
func orEmpty(v any) ([]string, bool) {
	if !pyfmt.Truthy(v) {
		return []string{}, true
	}
	return stringList(v)
}

// orNull は nil のポインタを JSON の null にし、そうでなければ指す値にする。
func orNull[T any](v *T) any {
	if v == nil {
		return nil
	}
	return *v
}

// list は nil のスライスを空のスライスにする（JSON で null でなく [] として出すため）。
func list[T any](xs []T) []T {
	if xs == nil {
		return []T{}
	}
	return xs
}
