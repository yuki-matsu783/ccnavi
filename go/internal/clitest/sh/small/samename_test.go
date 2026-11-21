package small

import (
	"testing"

	"ccnavi/internal/harness/run"
)

// TestSameNameFoldsOnlyASCIILetters は、sh の ccnavi_same_name が大文字小文字を区別せず名前を比べること。
// 畳むのは ASCII の英字だけ（ccnavi_path_pick と同じ）。実行ファイルの pathcase.Fold との一致表は置かない。
func TestSameNameFoldsOnlyASCIILetters(t *testing.T) {
	t.Parallel()
	cases := []struct {
		a, b string
		want int // 同じなら 0、違えば 1
	}{
		{"abc", "abc", 0},
		{"Foo/Bar", "foo/bar", 0},
		{"FOO/BAR", "foo/bar", 0},
		{"foo/bar", "foo/baz", 1},
		{"foo", "", 1},
		{"", "foo", 1},
		{"it's", "IT'S", 0},
		{"a'b", "a'c", 1},
		{"a\\b", "A\\B", 0},
		{"a\\b", "a/b", 1},
	}
	shells := shellNames()
	if len(shells) == 0 {
		t.Skip("sh が無い")
	}
	common := commonSh()
	for _, shell := range shells {
		for _, c := range cases {
			t.Run(shell+"/"+c.a+"_"+c.b, func(t *testing.T) {
				t.Parallel()
				res := runProgram(t, shell, run.Opts{
					Env: map[string]string{"CCNAVI_A": c.a, "CCNAVI_B": c.b},
				}, "-c", `. "$0"; ccnavi_same_name "$CCNAVI_A" "$CCNAVI_B"`, common)
				if res.Code != c.want {
					t.Errorf("code = %d, want %d", res.Code, c.want)
				}
			})
		}
	}
}
