package argparse

import "ccnavi/internal/infra/pyfmt"

// Namespace は読めた値（Python の vars(args)）。値は nil（None）・bool・string・int（Int）・[]any（並び）。
type Namespace map[string]any

// IsNone は値が None か。
func (ns Namespace) IsNone(dest string) bool { return ns[dest] == nil }

// Str は文字列の値。None と文字列でない値は空。
func (ns Namespace) Str(dest string) string {
	s, _ := ns[dest].(string)
	return s
}

// Truthy は Python の bool(args.dest)。
func (ns Namespace) Truthy(dest string) bool { return pyfmt.Truthy(ns[dest]) }

// Int は整数の値。None なら false。
func (ns Namespace) Int(dest string) (int, bool) {
	n, ok := ns[dest].(int)
	return n, ok
}

// Strings は文字列の並び。None なら nil（空の並びは空のスライス）。
func (ns Namespace) Strings(dest string) []string {
	list, ok := ns[dest].([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(list))
	for _, v := range list {
		s, _ := v.(string)
		out = append(out, s)
	}
	return out
}

// Changed は値が既定値と違うか（Python の getattr(args, dest, default) != default）。
func (ns Namespace) Changed(a *Action) bool {
	v, ok := ns[a.Dest]
	if !ok {
		return false
	}
	return !pyEqual(v, a.Default)
}

// pyEqual は Python の ==。bool は int として比べる（False == 0）。
func pyEqual(a, b any) bool {
	if x, ok := asInt(a); ok {
		y, ok := asInt(b)
		return ok && x == y
	}
	switch x := a.(type) {
	case nil:
		return b == nil
	case string:
		y, ok := b.(string)
		return ok && x == y
	case []any:
		y, ok := b.([]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for i := range x {
			if !pyEqual(x[i], y[i]) {
				return false
			}
		}
		return true
	}
	return false
}

func asInt(v any) (int, bool) {
	switch x := v.(type) {
	case bool:
		if x {
			return 1, true
		}
		return 0, true
	case int:
		return x, true
	}
	return 0, false
}
