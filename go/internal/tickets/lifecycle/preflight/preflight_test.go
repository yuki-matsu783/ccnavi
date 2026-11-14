package preflight

import "testing"

// 書き込みの権限は、GitHub は permissions.push、GitLab は member の access_level（読めなければプロジェクトの permissions）で決める。
func TestAccess(t *testing.T) {
	cases := []struct {
		kind            string
		project, member string
		want            string
	}{
		{"github", `{"permissions":{"push":true}}`, "", "ok"},
		{"github", `{"permissions":{"push":false}}`, "", "ng"},
		{"github", `{}`, "", "unknown"},
		{"gitlab", `{}`, `{"access_level":30}`, "ok"},
		{"gitlab", `{"permissions":{"project_access":{"access_level":20},"group_access":{"access_level":40}}}`, "", "ok"},
		{"gitlab", `{"permissions":{"project_access":{"access_level":20}}}`, `{"message":"404"}`, "ng"},
		{"gitlab", `{}`, "", "unknown"},
	}
	for _, c := range cases {
		var member []byte
		if c.member != "" {
			member = []byte(c.member)
		}
		if got, _ := Access(c.kind, []byte(c.project), member); got != c.want {
			t.Errorf("%s %s %s: %s（期待 %s）", c.kind, c.project, c.member, got, c.want)
		}
	}
	if UserID([]byte(`{"id":12}`)) != "12" || UserID([]byte(`{"id":"x"}`)) != "" {
		t.Error("UserID")
	}
}

// 項目の行は、結果の語と理由。権限を判定できないときは「注意」で続ける。
func TestLine(t *testing.T) {
	r := Result{Kind: map[string]string{"api": unverified, "access": ok, "git": skipped}, Why: map[string]string{"api": "届かない", "access": "x"}, AccessUnknown: true}
	for item, want := range map[string]string{"api": "api: 未確認 (届かない)", "access": "access: 注意（x。続行する）", "git": "git: 省略"} {
		if got := r.Line(item); got != want {
			t.Errorf("%s: %q", item, got)
		}
	}
}
