package contractrecord

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	cs "ccnavi/internal/harness/contractshape"
	"ccnavi/internal/harness/fakegitlab"
	"ccnavi/internal/harness/gitlabapi"
)

const image = "gitlab/gitlab-ce@sha256:test"

// fakeReal は「本物の GitLab」の代わりに模擬を立てる。場面は模擬が最初から持っている物を使い、場面の組み立て（本物にだけ要る）は差し替える。
// 模擬が答えるのは sh と受入シナリオが使う要求だけなので、送る列もそれに絞る。
type fakeReal struct {
	cfg     Config
	stdout  *bytes.Buffer
	stderr  *bytes.Buffer
	scenes  int
	deletes int
}

func newFakeReal(t *testing.T) *fakeReal {
	t.Helper()
	srv := fakegitlab.Start(t, cs.SceneState(), 0)
	requests, err := cs.LoadRequests()
	if err != nil {
		t.Fatal(err)
	}
	client := gitlabapi.NewClient(srv.URL)
	client.Sleep = func(time.Duration) {}
	f := &fakeReal{stdout: &bytes.Buffer{}, stderr: &bytes.Buffer{}}
	f.cfg = Config{
		Root: "unused", API: client,
		Token: fakegitlab.Token, ReviewerToken: "rev", ReviewerID: 2,
		Image: image, RunningImage: func() string { return image },
		Requests: requests,
		BuildScene: func(Config, string) (*Scene, error) {
			f.scenes++
			return &Scene{Context: cs.SceneContext(), Delete: func() { f.deletes++ }}, nil
		},
		Now:    func() time.Time { return time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC) },
		Stdout: f.stdout, Stderr: f.stderr,
		DryRun: true,
	}
	return f
}

func TestARecordingGoesThroughTheWholePipeline(t *testing.T) {
	t.Parallel()
	f := newFakeReal(t)
	if rc := Record(f.cfg); rc != 0 {
		t.Fatalf("rc = %d: %s", rc, f.stderr.String())
	}
	// 2 回録る（場面は 2 回組み、どちらも片付ける）
	if f.scenes != 2 || f.deletes != 2 {
		t.Errorf("場面 %d 回・片付け %d 回", f.scenes, f.deletes)
	}
	var doc struct {
		Source        string
		GitlabImage   string         `json:"gitlab_image"`
		RecordedAt    string         `json:"recorded_at"`
		GitlabVersion any            `json:"gitlab_version"`
		Requests      map[string]any `json:"requests"`
		NotRecorded   []string       `json:"not_recorded"`
	}
	if err := json.Unmarshal(f.stdout.Bytes(), &doc); err != nil {
		t.Fatal(err)
	}
	if doc.Source != "gitlab" || doc.GitlabImage != image || doc.RecordedAt != "2026-01-02" || len(doc.Requests) != len(f.cfg.Requests) {
		t.Errorf("記録の中身が違う: %s %s %s %d", doc.Source, doc.GitlabImage, doc.RecordedAt, len(doc.Requests))
	}
}

func TestNothingIsWrittenWhenAPlacementCheckFails(t *testing.T) {
	t.Parallel()
	for name, mutate := range map[string]func(*fakeReal) string{
		"動いているイメージが版の正と違う": func(f *fakeReal) string {
			f.cfg.RunningImage = func() string { return "gitlab/other" }
			return "イメージ"
		},
		"機密（トークン）が応答に混ざった": func(f *fakeReal) string {
			f.cfg.API = leaky{f.cfg.API.(*gitlabapi.Client)}
			return "禁止文字列"
		},
	} {
		f := newFakeReal(t)
		want := mutate(f)
		if rc := Record(f.cfg); rc != 1 {
			t.Errorf("%s: rc = %d、1 のはず（%s）", name, rc, f.stderr.String())
			continue
		}
		if !strings.Contains(f.stderr.String(), "recorded.json は置かない") || !strings.Contains(f.stderr.String(), want) || f.stdout.Len() != 0 {
			t.Errorf("%s: stderr = %q、stdout = %q", name, f.stderr.String(), f.stdout.String())
		}
	}
}

// leaky はすべての応答に、機密の形のキーを混ぜる。
type leaky struct{ *gitlabapi.Client }

func (l leaky) Call(method, path, token string, payload any, tag string) (int, any) {
	status, body := l.Client.Call(method, path, token, payload, tag)
	if m, ok := body.(map[string]any); ok {
		m["glpat-secret"] = 1 // 形に残るのはキーの名前
	}
	return status, body
}

func TestAFileIsPlacedAtomicallyAndOnlyWhenNotADryRun(t *testing.T) {
	// 契約のデータの置き場を一時ディレクトリに向ける（本物の recorded.json に触らない）
	root := base.TempDir(t)
	data := filepath.Join(root, "tests", "contract", "gitlab")
	if err := os.MkdirAll(data, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"allow.json", "forbidden.json", "requests.json"} {
		src, err := os.ReadFile(filepath.Join(cs.DataDir(), name))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(data, name), src, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	old := cs.Root
	cs.Root = root
	defer func() { cs.Root = old }()
	f := newFakeReal(t)
	f.cfg.DryRun = false
	if rc := Record(f.cfg); rc != 0 {
		t.Fatalf("rc = %d: %s", rc, f.stderr.String())
	}
	text, err := os.ReadFile(filepath.Join(data, "recorded.json"))
	if err != nil || !strings.HasSuffix(string(text), "}\n") {
		t.Fatalf("recorded.json: %v", err)
	}
	if !strings.Contains(f.stdout.String(), "置いた:") {
		t.Errorf("案内が無い: %q", f.stdout.String())
	}
	// 一時ファイルは残らない
	if matches, _ := filepath.Glob(filepath.Join(data, ".recorded-*")); len(matches) != 0 {
		t.Errorf("一時ファイルが残った: %v", matches)
	}
}

func TestRefusalsHappenBeforeAnythingIsCreated(t *testing.T) {
	t.Parallel()
	for name, tc := range map[string]struct {
		mutate func(*fakeReal)
		stderr string
	}{
		"トークン無し":      {func(f *fakeReal) { f.cfg.ReviewerToken = "" }, "トークンが無い"},
		"人間役の id が無い": {func(f *fakeReal) { f.cfg.ReviewerID = 0 }, "トークンが無い"},
		"認証できない": {func(f *fakeReal) {
			f.cfg.API = unauthorized{f.cfg.API.(*gitlabapi.Client)}
		}, "認証できない"},
	} {
		f := newFakeReal(t)
		tc.mutate(f)
		if rc := Record(f.cfg); rc != 2 || !strings.Contains(f.stderr.String(), tc.stderr) || f.scenes != 0 {
			t.Errorf("%s: rc = %d、場面 %d、%q", name, rc, f.scenes, f.stderr.String())
		}
	}
}

type unauthorized struct{ *gitlabapi.Client }

func (unauthorized) Version(string) (int, any) { return 401, map[string]any{} }

func TestReadsThatChangeShapeBetweenTheTwoRecordingsAreCaught(t *testing.T) {
	t.Parallel()
	node := func(types ...string) *cs.Node { return &cs.Node{T: types, Keys: nil} }
	req := []cs.Request{{Name: "r", Reads: []string{"id"}}}
	entry := func(types ...string) map[string]cs.Entry {
		return map[string]cs.Entry{"r": {Status: 200, Shape: &cs.Node{T: []string{"object"}, Keys: map[string]*cs.Node{"id": node(types...)}}}}
	}
	if got := readsDifferences(req, entry("integer"), entry("integer")); len(got) != 0 {
		t.Errorf("一致しているのに差と言った: %v", got)
	}
	got := readsDifferences(req, entry("integer"), entry("integer", "null"))
	if len(got) != 1 || !strings.Contains(got[0], "r: id") {
		t.Errorf("差を 1 件言うはず: %v", got)
	}
}

func TestTheTextIsSortedByKeyWithOneSpaceIndent(t *testing.T) {
	t.Parallel()
	image, ver, at := "i", "1", "2026-01-01"
	text, err := Text(cs.Document{Version: 1, Source: "gitlab", GitlabImage: &image, GitlabVersion: &ver, RecordedAt: &at, NotRecorded: []string{}, Requests: map[string]cs.Entry{
		"b": {Status: 200, Shape: &cs.Node{T: []string{"integer"}}},
		"a": {Status: 200, Shape: &cs.Node{T: []string{"string"}}},
	}})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(text, "{\n \"gitlab_image\": \"i\",\n") || strings.Index(text, `"a"`) > strings.Index(text, `"b"`) {
		t.Errorf("キーの順・字下げが違う:\n%s", text)
	}
}

func TestTokensComeFromTheFileAndEnvironmentOverridesIt(t *testing.T) {
	t.Parallel()
	file := filepath.Join(base.TempDir(t), "tokens.env")
	if err := os.WriteFile(file, []byte("GITLAB_TOKEN=file-root\nCCNAVI_PROBE_REVIEWER_TOKEN=file-rev\nCCNAVI_PROBE_REVIEWER_ID=7\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	token, rev, id := ConfigFromEnv(file, func(string) string { return "" })
	if token != "file-root" || rev != "file-rev" || id != 7 {
		t.Errorf("ファイル: %s %s %d", token, rev, id)
	}
	token, _, _ = ConfigFromEnv(file, func(k string) string { return map[string]string{"GITLAB_TOKEN": "env-root"}[k] })
	if token != "env-root" {
		t.Errorf("環境変数が先のはず: %s", token)
	}
}
