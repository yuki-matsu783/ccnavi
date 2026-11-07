package host

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/infra/pyjson"
)

// scenes は Chrome 拡張と sh が共有するホストの応答の見本の置き場（リポジトリのルートから）。
var scenes = []string{"extensions", "chrome", "ccnavi-approval", "test", "fixtures", "host"}

type conclusionOf struct {
	ChangesRequested []string `json:"changes_requested"`
	Unresolved       []string `json:"unresolved"`
}

// 見本から sh が組んだ JSON（expected.json）から出る結論（変更要求と未解決のスレッド）が、見本の
// conclusion.json と同じ。expected.json は tests/sh/test_review_host_fixture.py が sh で組んで比べる。
// CCNAVI_HOST_FIXTURE=1 を付けて回すと conclusion.json を書き直す（expected.json を録り直した後）。
func TestHostFixtureConclusions(t *testing.T) {
	for _, kind := range []Kind{GitHub, GitLab} {
		base := append(append([]string{}, scenes...), string(kind))
		entries, err := os.ReadDir(filepath.Join(append([]string{repoRoot}, base...)...))
		if err != nil {
			t.Fatal(err)
		}
		for _, e := range entries {
			if !e.IsDir() {
				continue
			}
			scene := append(append([]string{}, base...), e.Name())
			file := func(name string) []string { return append(append([]string{}, scene...), name) }
			t.Run(string(kind)+"/"+e.Name(), func(t *testing.T) {
				poster := ""
				if kind == GitLab {
					// GitLab は依頼を投稿したアカウントの ccnavi の依頼のスレッドを数えない
					poster = readJSON(t, file("scene.json")...).(*pyjson.Object).String("poster")
				}
				got := conclude(ResultOf(readJSON(t, file("expected.json")...)), poster)
				path := filepath.Join(append([]string{repoRoot}, file("conclusion.json")...)...)
				if os.Getenv("CCNAVI_HOST_FIXTURE") != "" {
					body, _ := json.MarshalIndent(got, "", "  ")
					if err := os.WriteFile(path, append(body, '\n'), 0o644); err != nil {
						t.Fatal(err)
					}
				}
				raw, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				var want conclusionOf
				if err := json.Unmarshal(raw, &want); err != nil {
					t.Fatal(err)
				}
				if !reflect.DeepEqual(got, want) {
					t.Errorf("結論 = %+v, want %+v", got, want)
				}
			})
		}
	}
}

func conclude(r Result, poster string) conclusionOf {
	out := conclusionOf{ChangesRequested: []string{}, Unresolved: []string{}}
	for _, rv := range Effective(r.Reviews) {
		if strings.ToUpper(rv.State) == "CHANGES_REQUESTED" {
			out.ChangesRequested = append(out.ChangesRequested, rv.URL)
		}
	}
	for _, th := range Unresolved(r.Threads, r.Host, poster, nil) {
		out.Unresolved = append(out.Unresolved, th.ID)
	}
	sort.Strings(out.ChangesRequested)
	sort.Strings(out.Unresolved)
	return out
}
