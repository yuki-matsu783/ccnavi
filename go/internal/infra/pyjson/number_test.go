package pyjson

import "testing"

func TestNumber(t *testing.T) {
	cases := []struct {
		in    any
		bools Bools
		want  float64
		ok    bool
	}{
		{3, BoolsRejected, 3, true}, {1.5, BoolsRejected, 1.5, true}, {true, BoolsRejected, 0, false},
		{true, BoolsAsNumbers, 1, true}, {"1", BoolsAsNumbers, 0, false}, {nil, BoolsAsNumbers, 0, false},
	}
	for _, c := range cases {
		if got, ok := Number(c.in, c.bools); got != c.want || ok != c.ok {
			t.Errorf("Number(%v, %v) = %v, %v", c.in, c.bools, got, ok)
		}
	}
	for v, want := range map[any]int{5: 5, 0: 0, -1: 0, true: 0, 2.0: 0} {
		if got := PositiveInt(v); got != want {
			t.Errorf("PositiveInt(%v) = %d", v, got)
		}
	}
}
