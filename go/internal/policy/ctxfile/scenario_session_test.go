package ctxfile

import (
	"bytes"
	"testing"

	"ccnavi/internal/policy/rules"
)

// 1 度だけの文はセッションごとに数える（tests/scenarios/test_runner.py から移した）。受入シナリオは 1 本ごとに
// 別のセッションで回すので、前のシナリオで渡した印が次のシナリオに持ち越されない。
func TestOnceIsCountedPerSession(t *testing.T) {
	state := t.TempDir()
	var stderr bytes.Buffer
	told := func(session string) bool {
		_, once := Deliver(&stderr, state, input(session, ""), []*rules.Rule{onceRule()}, Options{})
		return once
	}
	if !told("scn-1") || told("scn-1") || !told("scn-2") {
		t.Error("1 度だけの文の数えがセッションをまたいだ")
	}
}
