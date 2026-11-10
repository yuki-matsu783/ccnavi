package fault

import (
	"errors"
	"fmt"
	"testing"
)

type wrapped struct{ Detail }

func TestOfFindsDetailThroughWrapping(t *testing.T) {
	err := fmt.Errorf("外側: %w", &wrapped{New("ERROR_X", Values{"a": "1"}, "短い文")})
	d, ok := Of(err)
	if !ok || d.ID != "ERROR_X" || d.Values["a"] != "1" || d.What != "短い文" {
		t.Fatalf("%+v %v", d, ok)
	}
	if err.Error() != "外側: 短い文" {
		t.Fatal(err.Error())
	}
}

func TestOfIgnoresPlainErrors(t *testing.T) {
	if _, ok := Of(errors.New("x")); ok {
		t.Fatal("Detail の無いエラーを拾った")
	}
}
