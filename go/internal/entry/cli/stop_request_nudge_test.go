package cli

import (
	"bytes"
	"errors"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/approval"
)

// 書く側は、置き場（親のワークツリー）が決まらなければ 1 行の理由で止まる（終了 1）。ほかの失敗は想定外と言う。
func TestFinishSaysTheParentWorktreeIsNotSettled(t *testing.T) {
	var stderr bytes.Buffer
	if code := finish(&stderr, config.ExitOK, &approval.NoParentError{Reason: "決まらない"}); code != config.ExitError {
		t.Errorf("終了コード %d", code)
	}
	want := "ccnavi: 親のワークツリーが決まらないので止めた: 決まらない。親のワークツリーを準備してから（" +
		config.StartWrapper + "）打ち直してください\n"
	if stderr.String() != want {
		t.Errorf("%q", stderr.String())
	}
	stderr.Reset()
	if code := finish(&stderr, config.ExitOK, errors.New("x")); code != config.ExitError || stderr.String() != "ccnavi: 想定外の失敗で止まった: x\n" {
		t.Errorf("%d %q", code, stderr.String())
	}
	if code := finish(&stderr, 3, nil); code != 3 {
		t.Errorf("成功は呼び手の終了コード: %d", code)
	}
}
