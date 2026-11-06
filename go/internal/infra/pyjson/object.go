package pyjson

import (
	"fmt"
	"iter"
	"slices"
)

// Object は JSON のオブジェクト。Python の dict と同じく、入れた順を保ち、同じキーを入れ直すと
// 位置は最初のまま値だけを変える。ゼロ値は空のオブジェクトとして使える。
type Object struct {
	keys []string
	vals map[string]any
}

// NewObject は key, value, key, value, ... の順に並べたオブジェクトを作る。キーは文字列で渡す
// （文字列でないキーは fmt.Sprint の形にする）。
func NewObject(kv ...any) *Object {
	o := &Object{}
	for i := 0; i+1 < len(kv); i += 2 {
		key, ok := kv[i].(string)
		if !ok {
			key = fmt.Sprint(kv[i])
		}
		o.Set(key, kv[i+1])
	}
	return o
}

// Set は key の値を置く。
func (o *Object) Set(key string, value any) {
	if o.vals == nil {
		o.vals = map[string]any{}
	}
	if _, ok := o.vals[key]; !ok {
		o.keys = append(o.keys, key)
	}
	o.vals[key] = value
}

// Get は key の値。無ければ (nil, false)。nil の Object でも使える。
func (o *Object) Get(key string) (any, bool) {
	if o == nil {
		return nil, false
	}
	v, ok := o.vals[key]
	return v, ok
}

// Value は key の値。無ければ nil（Python の dict.get）。
func (o *Object) Value(key string) any {
	v, _ := o.Get(key)
	return v
}

// String は key の値が文字列ならそれ、ほかは空。
func (o *Object) String(key string) string {
	s, _ := o.Value(key).(string)
	return s
}

// Len は要素の数。
func (o *Object) Len() int {
	if o == nil {
		return 0
	}
	return len(o.keys)
}

// Keys は入れた順のキー。
func (o *Object) Keys() []string {
	if o == nil {
		return nil
	}
	return append([]string(nil), o.keys...)
}

// All は入れた順に (キー, 値) を返す。pyfmt.Items を満たす。
func (o *Object) All() iter.Seq2[any, any] {
	return func(yield func(any, any) bool) {
		if o == nil {
			return
		}
		for _, k := range o.keys {
			if !yield(k, o.vals[k]) {
				return
			}
		}
	}
}

// With は o に kv（key, value, ... の順）を重ねた写し（Python の dict(o, **kv)）。o は変えない。
// 既にあるキーは位置を保って値だけを変え、無いキーは後ろに足す。nil の Object でも使える。
func (o *Object) With(kv ...any) *Object {
	out := &Object{}
	for _, k := range o.Keys() {
		out.Set(k, o.vals[k])
	}
	for i := 0; i+1 < len(kv); i += 2 {
		key, ok := kv[i].(string)
		if !ok {
			key = fmt.Sprint(kv[i])
		}
		out.Set(key, kv[i+1])
	}
	return out
}

// Pop は key を除き、除いた値を返す（Python の dict.pop(key, None)）。無ければ (nil, false)。
func (o *Object) Pop(key string) (any, bool) {
	v, ok := o.Get(key)
	if ok {
		delete(o.vals, key)
		o.keys = slices.DeleteFunc(o.keys, func(k string) bool { return k == key })
	}
	return v, ok
}
