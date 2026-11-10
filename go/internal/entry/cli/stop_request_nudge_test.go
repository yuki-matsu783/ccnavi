package cli

import (
	"bytes"
	"errors"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/testkit"
)

// 書く側は、置き場（親のワークツリー）が決まらなければ 1 行の理由で止まる（終了 1）。ほかの失敗は想定外と言う。
func TestFinishSaysTheParentWorktreeIsNotSettled(t *testing.T) {
	var stderr bytes.Buffer
	root := t.TempDir()
	testkit.CopyMessages(t, root)
	if code := finish(&stderr, root, config.ExitOK, approval.NewNoParentError("決まらない")); code != config.ExitError {
		t.Errorf("終了コード %d", code)
	}
	// what・why・next の 3 行が、文面ファイルの文で出る。
	for _, want := range []string{
		"ccnavi: 親のワークツリーが決まらないため止めました: 決まらない。\n",
		"なぜ: ",
		"次: 親のワークツリーを準備してから（sh ",
		config.StartWrapper + "）打ち直す",
	} {
		if !strings.Contains(stderr.String(), want) {
			t.Errorf("%q が無い: %q", want, stderr.String())
		}
	}
	stderr.Reset()
	if code := finish(&stderr, root, config.ExitOK, errors.New("x")); code != config.ExitError || stderr.String() != "ccnavi: 想定外の失敗で止まった: x\n" {
		t.Errorf("%d %q", code, stderr.String())
	}
	if code := finish(&stderr, root, 3, nil); code != 3 {
		t.Errorf("成功は呼び手の終了コード: %d", code)
	}
}
