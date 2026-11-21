package flow

import (
	"testing"

	"ccnavi/internal/harness/ticketkit"
)

// Tree は ticketkit.Tree に、このパッケージのテストだけが使う道具（kit_extra_test.go・chrome_kit_test.go・flow_kit_test.go）を足す包み。
// 道具は ticketkit に無い型へ付けられないので、埋め込みで持つ。
type Tree struct{ *ticketkit.Tree }

// New は ticketkit.New の木を包んで返す。
func New(t testing.TB, opts ...ticketkit.Options) *Tree {
	t.Helper()
	return &Tree{ticketkit.New(t, opts...)}
}

// Cached は ticketkit.Cached の木を包んで返す（build にも包んだ木を渡す）。
func Cached(t testing.TB, key string, o ticketkit.Options, build func(tr *Tree)) *Tree {
	t.Helper()
	return &Tree{ticketkit.Cached(t, key, o, func(tr *ticketkit.Tree) { build(&Tree{tr}) })}
}

// RequestedTree は ticketkit.RequestedTree の木を包んで返す。
func RequestedTree(t testing.TB) (*Tree, ticketkit.Fixture) {
	t.Helper()
	tr, f := ticketkit.RequestedTree(t)
	return &Tree{tr}, f
}
