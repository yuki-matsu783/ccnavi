package refusal

import "testing"

func TestOf(t *testing.T) {
	cases := []struct{ out, want string }{
		{"error: Your local changes to the following files would be overwritten by merge:\n\ta.txt\n\tb/c.txt\nPlease commit\n",
			"書きかけの a.txt b/c.txt と重なる。コミットか退避をしてから再実行してください"},
		{"fatal: Unable to create '/r/.git/index.lock': File exists.\n",
			"索引のロック（index.lock）が残っている。別の git が動いていないか確かめ、異常終了して残ったものならユーザが削除します。"},
		{"Author identity unknown\n*** Please tell me who you are.\n", "コミットするユーザの名前（user.name・user.email）が決まっていない"},
		{"error: gpg failed to sign the data\n", "コミットの署名に失敗した"},
		{"pre-commit hook failed\nmore\n", "git の hook が止めた（pre-commit hook failed）"},
		{"fatal: something\n", "git が拒んだ（fatal: something）"},
		{"", "git が拒んだ（）"},
	}
	for _, c := range cases {
		if got := Of(c.out); got != c.want {
			t.Errorf("Of(%q) = %q, want %q", c.out, got, c.want)
		}
	}
}
