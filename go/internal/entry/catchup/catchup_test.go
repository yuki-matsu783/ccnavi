package catchup

import (
	"strings"
	"testing"

	"ccnavi/internal/policy/rules"
)

func TestReadsWhatShFetched(t *testing.T) {
	dir := t.TempDir()
	symref := func(name string) string { return "ref: refs/heads/" + name + "\tHEAD\n0123abcd\tHEAD\n" }
	for _, c := range []struct{ text, want string }{
		{symref("trunk"), "trunk"},
		{symref("feature/x"), "feature/x"},
		{symref("-x"), ""},        // 後ろの git の引数でオプションに読まれる
		{symref("bad..name"), ""}, // ブランチ名の形でない
		{"0123abcd\tHEAD\n", ""},
		{"", ""},
	} {
		if got := hostDefault(dir, c.text); got != c.want {
			t.Errorf("hostDefault(%q) = %q, want %q", c.text, got, c.want)
		}
	}
	heads := "aaa\trefs/heads/i0131-x\nbbb\trefs/heads/main\n"
	for _, c := range []struct {
		name string
		want bool
	}{{"main", true}, {"i0131", false}, {"i0131-x", true}, {"mai", false}} {
		if got := hasHead(heads, c.name); got != c.want {
			t.Errorf("hasHead(%q) = %v", c.name, got)
		}
	}
}

// said は答えのうち out の行と、止めたか（failed）。
func said(a *answer) (string, bool) {
	var out []string
	failed := false
	for _, line := range strings.Split(strings.TrimSuffix(a.String(), "\n"), "\n") {
		if text, ok := strings.CutPrefix(line, "out "); ok {
			out = append(out, text)
		}
		failed = failed || line == "failed"
	}
	return strings.Join(out, "\n"), failed
}

func TestMergedAnswersWithoutTidying(t *testing.T) {
	// 片付け（Tidy）に進まない答え。マージ済みかを確かめられないときと、マージされていないときの止め方。
	for _, c := range []struct {
		name, where, rc, said, recorded string
		fail                            bool
		has                             []string
	}{
		{"open request of a present branch says nothing", "present", "0", "none", "", false, nil},
		{"unknown for a present branch only says so", "present", "3", "unknown", "", false, []string{"次の取り込みで片付ける"}},
		{"none counts only with exit 0", "present", "2", "none", "", false, []string{"確かめられなかった"}},
		{"another branch is not cleaned", "present", "0", "merged 42 abc1234 other", "", true,
			[]string{"元ブランチ other が親のブランチ i0001 と違う。手元は片付けない"}},
		{"a gone branch with an open request stops with ways back", "absent", "0", "none", "", true,
			[]string{"消し間違いか破棄か", "戻し方 1", "戻し方 2", "sh /s/ccnavi-sync.sh i0001"}},
		{"a gone branch that cannot be checked stops", "absent", "3", "unknown", "", true, []string{"止めた"}},
		{"a retired family waits for the merge", "retired", "0", "none", "-", false,
			[]string{"まだマージされていない", "worktree retire --abandon i0001"}},
		{"a retired family without the source branch stops", "retired", "0", "merged 42 abc1234", "-", true,
			[]string{"元ブランチの名前をホストから取れなかった"}},
		{"a retired record of another branch stops", "retired", "0", "merged 42 abc1234 i0002", "i0001", true,
			[]string{"片付けの記録のブランチ i0001 と違う"}},
		{"a retired family that cannot be checked stops", "retired", "3", "", "-", true, []string{"手元は片付けずに止めた"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			s := &syncRun{here: "/s"}
			fields := strings.Fields(c.said)
			isMerged := len(fields) > 0 && fields[0] == "merged"
			none := c.rc == "0" && c.said == "none"
			number, sha, from := field(fields, 1), field(fields, 2), field(fields, 3)
			if c.where == "retired" {
				s.mergedRetired("i0001", "self", c.recorded, "42", "", isMerged, none, number, sha, from)
			} else {
				s.mergedFamily(c.where, "i0001", "self", "i0001", isMerged, none, number, sha, from)
			}
			out, failed := said(&s.a)
			if failed != c.fail {
				t.Errorf("failed = %v, want %v (%s)", failed, c.fail, out)
			}
			if len(c.has) == 0 && out != "" {
				t.Errorf("何も言わないはず: %s", out)
			}
			for _, h := range c.has {
				if !strings.Contains(out, h) {
					t.Errorf("%q が無い: %s", h, out)
				}
			}
		})
	}
}

func TestChecksShowWarningsAndStopOnErrors(t *testing.T) {
	s := &syncRun{here: "/s"}
	s.checks("i0001", []problem{{rules.Warn, "層がずれている"}, {rules.Error, "正とする側が合わない"}, {rules.Info, "黙る"}})
	out, failed := said(&s.a)
	want := "  注意: 層がずれている\n" +
		"i0001: 取り込みの後の検査で止める理由がある。直してから、sh /s/ccnavi-sync.sh i0001 を再実行してください（検査し直す）\n" +
		"  - 正とする側が合わない"
	if !failed || out != want {
		t.Errorf("failed=%v out=\n%s", failed, out)
	}
	s = &syncRun{here: "/s"}
	s.checks("i0001", []problem{{rules.Warn, "層がずれている"}})
	if out, failed := said(&s.a); failed || out != "  注意: 層がずれている" {
		t.Errorf("warn だけなら止めない: failed=%v out=%s", failed, out)
	}
}

// 認証に失敗したときの案内は、origin のホストに合わせてログインを頼む道具とホストを言う。
func TestAuthText(t *testing.T) {
	cases := []struct{ url, want, not string }{
		{"http://127.0.0.1:8929/o/r.git", "glab auth login --hostname 127.0.0.1:8929 を頼む", "gh auth"},
		{"https://github.com/o/r.git", "gh auth login を頼む", "glab"},
		{"https://acme.ghe.com/o/r.git", "gh auth login --hostname acme.ghe.com を頼む", "glab"},
		{"junk", "glab auth login（GitHub なら gh auth login）を頼む", "--hostname"},
	}
	for _, c := range cases {
		got := authText(c.url)
		if !strings.Contains(got, c.want) || strings.Contains(got, c.not) || !strings.Contains(got, "git fetch origin") {
			t.Errorf("%s: %s", c.url, got)
		}
	}
}

// 認証の失敗の見分けは git の文言（LC_ALL=C）で行い、届かないだけの失敗は認証と言わない。
func TestAuthFailedOnly(t *testing.T) {
	var a answer
	authNote(&a, t.TempDir(), "fatal: unable to access 'http://x/': Could not resolve host: x\n")
	if a.String() != "" {
		t.Errorf("届かないだけなのに案内した: %q", a.String())
	}
}
