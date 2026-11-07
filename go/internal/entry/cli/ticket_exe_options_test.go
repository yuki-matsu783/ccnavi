package cli

import (
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/tickets/model/useronly"
)

// 入口の止めが知っている実行ファイルのオプション（useronly.ExeOptions）は、引数の読み手が受けるオプションと同じ。
// 読み手にだけ足すと、入口の止めがそのオプションを使い方の誤りと見なす。
func TestExeOptionsMatchTheParser(t *testing.T) {
	flags := []string{"-h", "-r"}
	for _, a := range newParser().Actions() {
		for _, flag := range a.OptionStrings {
			if strings.HasPrefix(flag, "--") && !slices.Contains(flags, flag) {
				flags = append(flags, flag)
			}
		}
	}
	want := slices.Clone(useronly.ExeOptions())
	slices.Sort(flags)
	slices.Sort(want)
	want = slices.Compact(want)
	if !slices.Equal(flags, want) {
		for _, f := range flags {
			if !slices.Contains(want, f) {
				t.Errorf("ExeOptions に無い: %s", f)
			}
		}
		for _, f := range want {
			if !slices.Contains(flags, f) {
				t.Errorf("読み手が受けない: %s", f)
			}
		}
	}
}
