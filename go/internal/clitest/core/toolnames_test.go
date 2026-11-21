package core

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

// 手で書く match（共通ルールと開発用 hook）は、Edit を挙げたら MultiEdit も挙げる。
// MultiEdit は Edit と同じく既存のファイルを直す。片方だけだと、同じ書き込みがツールの選び方だけでルールから外れる。
// ツール名の一覧そのものは go/internal/infra/config の go test が見る。
func TestEditComesWithMultiEdit(t *testing.T) {
	t.Parallel()
	type found struct{ where, match string }
	var all []found
	rules, err := os.ReadFile(filepath.Join(base.Root(), ".ccnavi", "config", "common", "rules.yml"))
	if err != nil {
		t.Fatal(err)
	}
	matchLine := regexp.MustCompile(`^\s*match:\s*(\S+)`)
	for i, line := range strings.Split(string(rules), "\n") {
		if m := matchLine.FindStringSubmatch(line); m != nil {
			all = append(all, found{"rules.yml:" + strconv.Itoa(i+1), m[1]})
		}
	}
	settings, err := os.ReadFile(filepath.Join(base.Root(), ".claude", "settings.json"))
	if err != nil {
		t.Fatal(err)
	}
	var parsed struct {
		Hooks map[string][]struct{ Matcher string } `json:"hooks"`
	}
	if err := json.Unmarshal(settings, &parsed); err != nil {
		t.Fatal(err)
	}
	for _, entries := range parsed.Hooks {
		for _, e := range entries {
			all = append(all, found{"settings.json", e.Matcher})
		}
	}
	for _, f := range all {
		names := strings.Split(f.match, "|")
		if slices.Contains(names, "Edit") && !slices.Contains(names, "MultiEdit") {
			t.Errorf("%s: %q は Edit だけで MultiEdit が無い", f.where, f.match)
		}
	}
}
