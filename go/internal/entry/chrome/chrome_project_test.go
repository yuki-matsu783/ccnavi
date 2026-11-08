package chrome

import (
	"encoding/json"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"ccnavi/internal/infra/pyjson"
)

// プロジェクトの親子のチケットの仮のツリーでは、プロジェクトのレイヤーは統合先の .ccnavi/config/project/ を
// そのまま写す。同期が配るミラー（プロジェクトの .ccnavi/config/common/）と config の外のファイルは写さず、
// 共通レイヤーはワークスペースの統合先のものだけを読む。
func TestTheProjectLayerIsTheIntegrationConfigAsItIs(t *testing.T) {
	const (
		rules  = "{\"version\": 1, \"deny\": []}\n"
		phases = "version: 1\nphases: {}\n"
		risks  = "version: 1\nfactors: []\n"
		mirror = "{\"version\": 1, \"deny\": [{\"id\": \"mirror-only\", \"match\": \"Bash\", \"glob\": \"*x*\"}]}\n"
		common = "version: 1\nfactors: []\n# 共通\n"
	)
	request := map[string]any{"snapshot": map[string]any{
		"integration": map[string]any{"name": "trunk"},
		"branches": map[string]any{"trunk": map[string]any{"head": "0", "files": map[string]any{
			".ccnavi/config/project/rules.yml":  rules,
			".ccnavi/config/project/phases.yml": phases,
			".ccnavi/config/project/risks.yml":  risks,
			".ccnavi/config/common/rules.yml":   mirror,
			"README.md":                         "r\n",
		}}},
		"project": "web",
		"workspace": map[string]any{
			"integration": map[string]any{"name": "main"},
			"files":       map[string]any{".ccnavi/config/common/risks.yml": common},
		},
	}}
	data, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := pyjson.Loads(string(data))
	if err != nil {
		t.Fatal(err)
	}
	s, err := readSnapshot(parsed.(*pyjson.Object))
	if err != nil {
		t.Fatal(err)
	}
	root := filepath.Join(t.TempDir(), "memfs")
	if err := s.build(root, nil); err != nil {
		t.Fatal(err)
	}
	project := filepath.Join(root, "projects", "web")
	got := map[string]string{}
	err = filepath.WalkDir(project, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || d.Name() == "HEAD" {
			return err
		}
		body, err := os.ReadFile(path)
		rel, _ := filepath.Rel(project, path)
		got[filepath.ToSlash(rel)] = string(body)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{
		".ccnavi/config/project/rules.yml":  rules,
		".ccnavi/config/project/phases.yml": phases,
		".ccnavi/config/project/risks.yml":  risks,
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("プロジェクトに写したもの: %v", got)
	}
	if body, err := os.ReadFile(filepath.Join(root, ".ccnavi", "config", "common", "risks.yml")); err != nil || string(body) != common {
		t.Errorf("共通レイヤーがワークスペースの統合先のものでない: %q %v", body, err)
	}
}
