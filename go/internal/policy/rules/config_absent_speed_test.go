package rules

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/yaml"
)

// 共通ルールの regex（組み合わせ条件と except の中も）は、長い入力ですぐ返る（tests/config/test_config_absent.py の
// BuiltinSpeedTest から移した）。判定の期限はルールの合間でしか見ないので、1 本の照合が後戻りで長引くと、hook の
// timeout に達して呼び出しが止められずに通る。
func TestCommonRegexesReturnQuickly(t *testing.T) {
	const limit = 100 * time.Millisecond
	data, err := os.ReadFile(filepath.Join("..", "..", "..", "..", ".ccnavi", "config", "common", "rules.yml"))
	if err != nil {
		t.Fatal(err)
	}
	doc, err := yaml.Load(string(data))
	if err != nil {
		t.Fatal(err)
	}
	byRule := map[string][]string{}
	var walk func(node any, id string)
	walk = func(node any, id string) {
		switch v := node.(type) {
		case *yaml.Map:
			if own, ok := v.Get("id").(string); ok {
				id = own
			}
			for key, value := range v.All() {
				if text, ok := value.(string); ok && key == "regex" {
					byRule[id] = append(byRule[id], strings.NewReplacer("{root}", "/ws", "{!root}", "").Replace(text))
					continue
				}
				walk(value, id)
			}
		case []any:
			for _, item := range v {
				walk(item, id)
			}
		}
	}
	walk(doc, "")
	// 残しているルールの regex が検査の対象に入っていること（入っていなければ速さを見たことにならない）。
	for _, id := range []string{"workspace-root", "session-scratchpad", "find-writes", "heredoc"} {
		if len(byRule[id]) == 0 {
			t.Errorf("%s の regex が無い", id)
		}
	}
	slow := []string{
		strings.Repeat("/", 30000), "echo " + strings.Repeat("/", 30000) + "; git push",
		strings.Repeat("\x00a", 15000), strings.Repeat("\x00/", 15000),
		strings.Repeat("rm ", 8000), strings.Repeat("git -c ", 8000), strings.Repeat("rm -r a ", 3200),
		strings.Repeat("git -C ", 8000), strings.Repeat("rm -f a ", 3200), strings.Repeat("git reset ", 2400),
		strings.Repeat("/", 24000), strings.Repeat("a/", 12000), strings.Repeat("git --x/git ", 2000),
		"mv " + strings.Repeat("a/", 12000), "echo > " + strings.Repeat("a/", 12000),
		"cp " + strings.Repeat("a/ ", 8000) + ".", `echo "` + strings.Repeat(">a", 12000),
		`echo "` + strings.Repeat(">a/", 8000), `echo "> ` + strings.Repeat("a/", 12000),
	}
	for id, expressions := range byRule {
		for _, expression := range expressions {
			regex, err := pyre.Compile(expression, pyre.IgnoreCase)
			if err != nil {
				t.Errorf("%s: %v", id, err)
				continue
			}
			for _, text := range slow {
				start := time.Now()
				regex.Search(text)
				if took := time.Since(start); took > limit {
					t.Errorf("%s: %q… に %v", id, text[:16], took)
				}
			}
		}
	}
}
