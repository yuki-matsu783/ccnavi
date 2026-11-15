package refusal

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

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

func TestOverlap(t *testing.T) {
	if !Overlap("error: Your local changes to the following files would be overwritten by merge:\n\ta.txt\nAborting\n") {
		t.Error("重なりを見落とした")
	}
	if Overlap("fatal: something\n") || Overlap("error: x would be overwritten by merge:\nAborting\n") {
		t.Error("重なっていないのに重なりと言った")
	}
}

// sh の写し（ccnavi_no_remote_ref）と同じ表（tests/fixtures/no-remote-ref.json）で、NoRemoteRef を確かめる。
func TestNoRemoteRefTable(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(testkit.RepoRoot(t), "tests", "fixtures", "no-remote-ref.json"))
	if err != nil {
		t.Fatal(err)
	}
	var table struct {
		Cases []struct {
			Err   string `json:"err"`
			NoRef bool   `json:"no_ref"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &table); err != nil || len(table.Cases) == 0 {
		t.Fatal("表が読めない", err)
	}
	for _, c := range table.Cases {
		if got := NoRemoteRef(c.Err); got != c.NoRef {
			t.Errorf("%q: %v", c.Err, got)
		}
	}
}

func TestAuthFailed(t *testing.T) {
	for _, out := range []string{
		"fatal: Authentication failed for 'https://h/g/p.git/'\n",
		"fatal: could not read Username for 'https://h': terminal prompts disabled\n",
		"git@h: Permission denied (publickey).\n",
		"fatal: unable to access 'https://h/': The requested URL returned error: 403\n",
		"remote: HTTP Basic: Access denied\n",
	} {
		if !AuthFailed(out) {
			t.Errorf("認証の失敗を見落とした: %q", out)
		}
	}
	if AuthFailed("fatal: unable to access 'https://h/': Could not resolve host: h\n") {
		t.Error("届かないだけなのに認証の失敗と言った")
	}
}
