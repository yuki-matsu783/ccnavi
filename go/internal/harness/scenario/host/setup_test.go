package host

import (
	"os"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/hoststub"
)

func TestValidate(t *testing.T) {
	t.Parallel()
	issue := map[string]any{"repo": "projects/test-repo", "title": "t"}
	cases := []struct {
		name    string
		setup   map[string]any
		wantErr string // 空なら通る
	}{
		{"既定は real", map[string]any{"files": map[string]any{}}, ""},
		{"bare", map[string]any{"host": "bare"}, ""},
		{"fake-gitlab", map[string]any{"host": "fake-gitlab", "host_state": map[string]any{"project": "a/b", "issues": []any{}}}, ""},
		{"real と issue は併用できる", map[string]any{"host": "real", "issue": issue}, ""},
		{"知らない相手", map[string]any{"host": "mock"}, "setup.host は"},
		{"空の相手", map[string]any{"host": ""}, "setup.host は"},
		{"None の相手", map[string]any{"host": nil}, "setup.host は"},
		{"真偽の相手", map[string]any{"host": true}, "setup.host は"},
		{"大文字の相手", map[string]any{"host": "Bare"}, "setup.host は"},
		{"bare と issue", map[string]any{"host": "bare", "issue": issue}, "setup.issue"},
		{"fake-gitlab と issue", map[string]any{"host": "fake-gitlab", "issue": issue}, "setup.issue"},
		{"docker-gitlab と issue", map[string]any{"host": "docker-gitlab", "issue": issue}, "setup.issue"},
		{"host_state は fake-gitlab だけ(bare)", map[string]any{"host": "bare", "host_state": map[string]any{}}, "host_state"},
		{"host_state は fake-gitlab だけ(既定)", map[string]any{"host_state": map[string]any{}}, "host_state"},
		{"host_state は docker-gitlab に書けない", map[string]any{"host": "docker-gitlab", "host_state": map[string]any{}}, "host_state"},
		{"host_state は dict", map[string]any{"host": "fake-gitlab", "host_state": []any{}}, "dict"},
		{"project は ns/proj の形", map[string]any{"host": "fake-gitlab", "host_state": map[string]any{"project": "proj"}}, "<namespace>/<project>"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			err := Validate("UC-99.yml", tc.setup)
			if (err == nil) != (tc.wantErr == "") || (err != nil && !strings.Contains(err.Error(), tc.wantErr)) {
				t.Errorf("Validate = %v, want %q", err, tc.wantErr)
			}
		})
	}
}

func TestKinds(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		kind              Kind
		fake, stub, snaps bool
	}{
		{Bare, true, true, false},
		{FakeGitLab, true, true, true},
		{DockerGitLab, false, true, true},
		{Real, false, false, false},
	} {
		snaps := false
		for _, k := range SnapshotKinds() {
			snaps = snaps || k == tc.kind
		}
		if IsFake(tc.kind) != tc.fake || UsesStubGit(tc.kind) != tc.stub || snaps != tc.snaps {
			t.Errorf("%s: fake=%v stub=%v snapshot=%v", tc.kind, IsFake(tc.kind), UsesStubGit(tc.kind), snaps)
		}
	}
}

func TestProjectNameIsPrefixedFoldedAndUnique(t *testing.T) {
	t.Parallel()
	now := time.Date(2026, 10, 25, 1, 2, 3, 0, time.UTC)
	a, b := ProjectName("UC-08", now, "ab12"), ProjectName("uc-08", now, "ab12")
	if a != "sc-uc-08-20261025010203-ab12" || a != b {
		t.Errorf("a = %q, b = %q（大文字小文字で名前を分けない）", a, b)
	}
	if ProjectName("UC-08", now, "cd34") == a {
		t.Error("乱数が違えば名前も違う")
	}
}

func TestStepEnv(t *testing.T) {
	t.Parallel()
	h := &Host{Kind: Bare, Bin: "/stub/bin", Routes: "/stub/routes.json", Log: "/stub/log",
		TargetCommon: "/t/.git", Bare: "/stub/origin.git", RealOrigin: realOriginURL, Project: fakeProject, Port: 4321}
	parent := []string{
		"PATH=/usr/bin", "HOME=/home/u", "HTTPS_PROXY=http://p", "NODE_EXTRA_CA_CERTS=/ca",
		"GITHUB_TOKEN=real-gh", "GH_TOKEN=real-gh2", "GITLAB_TOKEN=real-gl",
		"CCNAVI_PROBE_REVIEWER_TOKEN=real-probe", "github_token=lower",
	}
	t.Run("本物のトークンは落とし、ほかは継ぐ", func(t *testing.T) {
		t.Parallel()
		env := h.StepEnv(parent, "")
		for _, name := range []string{"GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN", "CCNAVI_PROBE_REVIEWER_TOKEN", "github_token"} {
			if _, ok := env[name]; ok {
				t.Errorf("%s が残っている", name)
			}
		}
		for name, want := range map[string]string{"HOME": "/home/u", "HTTPS_PROXY": "http://p", "NODE_EXTRA_CA_CERTS": "/ca",
			"PATH": "/stub/bin" + string(os.PathListSeparator) + "/usr/bin", "FAKE_BARE": "/stub/origin.git",
			"FAKE_REAL_ORIGIN": realOriginURL, "FAKE_TARGET_COMMON": "/t/.git"} {
			if env[name] != want {
				t.Errorf("%s = %q, want %q", name, env[name], want)
			}
		}
		if env["FAKE_ORIGIN_URL"] != hoststub.OriginURL {
			t.Errorf("FAKE_ORIGIN_URL = %q", env["FAKE_ORIGIN_URL"])
		}
	})
	t.Run("fake-gitlab は偽のトークンとローカルの origin だけ", func(t *testing.T) {
		t.Parallel()
		f := *h
		f.Kind, f.Token = FakeGitLab, "fake-token"
		env := f.StepEnv([]string{"PATH=/usr/bin", "GITLAB_TOKEN=real-gl", "GITHUB_TOKEN=real-gh"}, "")
		if env["GITLAB_TOKEN"] != "fake-token" || env["GITHUB_TOKEN"] != "" ||
			env["FAKE_ORIGIN_URL"] != "http://127.0.0.1:4321/ns/proj.git" {
			t.Errorf("env = %v", env)
		}
	})
}

// docker-gitlab の git は、利用者の credential helper に PAT が渡らないよう同じ key を空で打ち消してから helper を足す。
func TestGitViaResetsTheHelperBeforeSettingIt(t *testing.T) {
	t.Parallel()
	h := &Host{Kind: DockerGitLab, Bare: dockerBase + "/root/p.git", RealOrigin: "o", Base: dockerBase}
	via := h.GitVia()
	key := "credential." + dockerBase + ".helper"
	reset, set := -1, -1
	for i, a := range via {
		switch {
		case a == key+"=":
			reset = i
		case strings.HasPrefix(a, key+"=") && strings.Contains(a, "GITLAB_TOKEN"):
			set = i
		}
		if strings.Contains(a, rootPAT) {
			t.Errorf("引数にトークンが入っている: %q", a)
		}
	}
	if reset < 0 || set < 0 || reset >= set {
		t.Errorf("via = %q", via)
	}
	if n := len((&Host{Kind: Bare, Bare: "b", RealOrigin: "o"}).GitVia()); n != 2 {
		t.Errorf("docker 以外に helper を足している（%d 個）", n)
	}
}
