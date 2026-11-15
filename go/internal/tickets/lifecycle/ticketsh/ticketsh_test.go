package ticketsh

import (
	"strings"
	"testing"
)

// 識別子は、サブコマンドの後ろの `--` と `--reason <値>` とほかのオプションを読み飛ばした最初の語。
func TestID(t *testing.T) {
	for _, c := range []struct {
		args []string
		want string
	}{
		{[]string{"start", "i0001"}, "i0001"},
		{[]string{"start", "--", "i0001"}, "i0001"},
		{[]string{"cancel", "--reason", "i0009", "i0001"}, "i0001"},
		{[]string{"finish", "--check", "-x", "i0001", "i0002"}, "i0001"},
		{[]string{"start", "-", "i0001"}, "-"},
		{[]string{"status"}, ""},
		{nil, ""},
	} {
		if got := ID(c.args); got != c.want {
			t.Errorf("%q: %q, want %q", c.args, got, c.want)
		}
	}
	if got := IDAnswer([]string{"start", "../x"}); !strings.Contains(got, "bad yes") || !strings.Contains(got, "start には識別子を指定してください（../x は") {
		t.Errorf("IDAnswer: %q", got)
	}
}

// 次の手は approval-request と start にだけ出す。
func TestUnsentNextOnlyForTheFirstOperations(t *testing.T) {
	root := t.TempDir()
	if got := UnsentNext(root, "finish", "feature/1-x", ""); got != "" {
		t.Errorf("finish: %q", got)
	}
	got := UnsentNext(root, "start", "feature/1-x", "")
	if !strings.HasPrefix(got, "次の手: ") || !strings.Contains(got, "--mr <MR の番号>") || !strings.Contains(got, "git push -u origin feature/1-x") {
		t.Errorf("start: %q", got)
	}
}
