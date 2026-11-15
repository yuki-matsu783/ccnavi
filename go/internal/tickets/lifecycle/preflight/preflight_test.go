package preflight

import (
	"os"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

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

// checkRepo は origin と origin/HEAD と作成者のあるリポジトリ（手元で決まる項目が全部 ok）。
func checkRepo(t *testing.T) string {
	t.Helper()
	top := testkit.Workspace(t)
	testkit.Git(t, top, "remote", "add", "origin", "https://github.com/acme/widgets.git")
	testkit.Git(t, top, "update-ref", "refs/remotes/origin/main", "HEAD")
	testkit.Git(t, top, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
	testkit.Git(t, top, "config", "user.name", "t")
	testkit.Git(t, top, "config", "user.email", "t@example.invalid")
	return top
}

// sh が渡した事実（接続・プロジェクトの取得・権限・ls-remote）から、項目ごとの結果・まとめ・勧める対処を決める。
func TestCheck(t *testing.T) {
	top := checkRepo(t)
	reached := Facts{Top: top, Parse: ok, Kind: "github", Host: "github.com", Path: "acme/widgets", CLI: "gh", Token: "GITHUB_TOKEN",
		Connect: "0", Transport: "curl", Probe: ok, Access: ok, Git: "0"}
	with := func(mod func(*Facts)) Facts { f := reached; mod(&f); return f }
	for _, c := range []struct {
		name  string
		f     Facts
		items map[string]string
		total string
		fix   string
		cont  bool
	}{
		{"全部 ok", reached, map[string]string{"tools": ok, "origin": ok, "integration": ok, "api": ok, "access": ok, "git": ok, "ident": ok}, ok, "", false},
		{"401 か 403 は認証の NG", with(func(f *Facts) { f.Probe, f.Access = "auth", "" }), map[string]string{"api": ng, "access": skipped, "git": ok}, ng, "auth", false},
		{"404 はリポジトリの NG", with(func(f *Facts) { f.Probe, f.Access = "notfound", "" }), map[string]string{"api": ng, "access": skipped}, ng, "notfound", false},
		{"5xx は未確認で、git が通れば続ける", with(func(f *Facts) { f.Probe, f.Access = "network", "" }), map[string]string{"api": unverified, "access": skipped, "git": ok}, unverified, "network", true},
		{"JSON でない応答も未確認", with(func(f *Facts) { f.Probe, f.Access = "badjson", "" }), map[string]string{"api": unverified}, unverified, "network", true},
		{"CLI もトークンも無い", with(func(f *Facts) { f.Connect, f.Probe, f.Access = "4", "", "" }), map[string]string{"api": unverified, "access": skipped, "git": ok}, unverified, "auth", true},
		{"CLI も curl も無い", with(func(f *Facts) { f.Connect, f.Probe, f.Access = "5", "", "" }), map[string]string{"api": unverified}, unverified, "tool", true},
		{"書き込みの権限が無い", with(func(f *Facts) { f.Access, f.AccessWhy = ng, "push できない" }), map[string]string{"api": ok, "access": ng}, ng, "access", false},
		{"権限を判定できなければ注意で続ける", with(func(f *Facts) { f.Access, f.AccessWhy = "unknown", "x" }), map[string]string{"access": ok}, ok, "", false},
		{"git の認証の失敗は NG", with(func(f *Facts) { f.Git, f.GitErr = "128", "fatal: Authentication failed for x" }), map[string]string{"git": ng}, ng, "git-auth", false},
		{"git に届かなければ未確認で、続けない", with(func(f *Facts) { f.Git, f.GitErr = "128", "fatal: unable to access: Could not resolve host" }), map[string]string{"git": unverified}, unverified, "network", false},
		{"API にも git にも届かなければ続けない", with(func(f *Facts) { f.Probe, f.Access, f.Git = "network", "", "128" }), map[string]string{"api": unverified, "git": unverified}, unverified, "network", false},
		{"origin をホストのものとして読めない", Facts{Top: top, Parse: ng, Masked: "/tmp/x.git"}, map[string]string{"origin": ng, "api": skipped, "git": skipped}, ng, "origin", false},
	} {
		t.Run(c.name, func(t *testing.T) {
			r := Check(c.f)
			for item, want := range c.items {
				if r.Kind[item] != want {
					t.Errorf("%s: %s（期待 %s）", item, r.Kind[item], want)
				}
			}
			if r.Total != c.total || r.Fix != c.fix || r.Continue() != c.cont {
				t.Errorf("まとめ %s・対処 %s・続ける %v（期待 %s・%s・%v）", r.Total, r.Fix, r.Continue(), c.total, c.fix, c.cont)
			}
		})
	}
	if api, remote := Plan(Facts{Top: top, Parse: ng}); api || remote {
		t.Error("origin が読めないのにホストへ行く")
	}
}

// 作成者（author と committer）は手元の設定だけで決める。無ければ ident が NG で、対処は作成者の設定。
func TestCheckIdent(t *testing.T) {
	top := checkRepo(t)
	testkit.Git(t, top, "config", "--unset", "user.name")
	testkit.Git(t, top, "config", "--unset", "user.email")
	for k, v := range map[string]string{"GIT_CONFIG_GLOBAL": os.DevNull, "GIT_CONFIG_NOSYSTEM": "1", "HOME": t.TempDir(),
		"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly", "GIT_CONFIG_VALUE_0": "true"} {
		t.Setenv(k, v)
	}
	for _, k := range []string{"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"} {
		t.Setenv(k, "")
		os.Unsetenv(k)
	}
	facts := Facts{Top: top, Parse: ok, Connect: "0", Transport: "curl", Probe: ok, Access: ok, Git: "0"}
	if r := Check(facts); r.Kind["ident"] != ng || r.Fix != "ident" {
		t.Errorf("作成者が無い: %s %s", r.Kind["ident"], r.Fix)
	}
	t.Setenv("GIT_AUTHOR_NAME", "a")
	t.Setenv("GIT_AUTHOR_EMAIL", "a@example.invalid")
	if r := Check(facts); r.Kind["ident"] != ng {
		t.Errorf("author だけで通った: %s", r.Kind["ident"])
	}
	t.Setenv("GIT_COMMITTER_NAME", "c")
	t.Setenv("GIT_COMMITTER_EMAIL", "c@example.invalid")
	if r := Check(facts); r.Kind["ident"] != ok || r.Total != ok {
		t.Errorf("環境の author と committer で通らない: %s %s", r.Kind["ident"], r.Total)
	}
}
