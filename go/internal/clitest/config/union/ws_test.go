package union

import (
	"testing"

	"ccnavi/internal/harness/configws"
)

// ws は configws.WS に、このパッケージのテストだけが使う道具（_test.go の方法）を足すための包み。
type ws struct{ *configws.WS }

func newWS(t testing.TB, kind string) *ws { return &ws{configws.NewWS(t, kind)} }

// as はサブテストの t で動く写し。
func (w *ws) as(t testing.TB) *ws { return &ws{w.WS.As(t)} }
