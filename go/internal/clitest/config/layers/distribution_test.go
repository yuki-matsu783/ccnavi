package layers

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"unicode/utf8"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/configws"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/yaml"
)

// 配布物（.ccnavi/ の設定・文書・スクリプト）を置いたプロジェクトの `--lint` は問題 0 件になる
// （tests/config/test_distribution_lint.py の写し）。
//
// ccnavi が配るルールと文書が自身の検査に引っかかると、配布直後のプロジェクトでエージェントが `--lint` を打つたびに警告を見ることになる。
// たとえば `additionalContextFile` の文書が上限（maxChars 文字）を超えると、先頭までしか渡らず、残りは渡らない。

// maxChars は 1 回の応答に載せる文書の上限（文字数）。超えた分は渡らない。
const maxChars = 4000

func copyFile(t testing.TB, from, to string) {
	t.Helper()
	data, err := os.ReadFile(from)
	if err != nil {
		t.Fatal(err)
	}
	configws.Write(t, to, string(data))
}

func copyDir(t testing.TB, from, to string) {
	t.Helper()
	entries, err := os.ReadDir(from)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if e.IsDir() {
			copyDir(t, filepath.Join(from, e.Name()), filepath.Join(to, e.Name()))
		} else {
			copyFile(t, filepath.Join(from, e.Name()), filepath.Join(to, e.Name()))
		}
	}
}

func TestDistributedLintHasNoProblem(t *testing.T) {
	t.Parallel()
	root := base.TempDir(t)
	dest := filepath.Join(root, ".ccnavi")
	// 保護対象（共通の設定・スクリプト・CCNAVI.md）は、下書きがあれば下書きの、できあがりの姿を配る。
	commons, err := os.ReadDir(filepath.Join(base.Root(), ".ccnavi", "config", "common"))
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range commons {
		copyFile(t, base.Final(".ccnavi/config/common/"+e.Name()), filepath.Join(dest, "config", "common", e.Name()))
	}
	copyDir(t, filepath.Join(base.Root(), ".ccnavi", "docs"), filepath.Join(dest, "docs"))
	copyDir(t, base.ScriptsDir(), filepath.Join(dest, "scripts"))
	copyFile(t, base.Final(".ccnavi/CCNAVI.md"), filepath.Join(dest, "CCNAVI.md"))

	r := run.Ccnavi(t, []string{"--root", root, "--lint", "--json", "--mode", "enable"}, run.Opts{Dir: root})
	var report struct {
		Problems []json.RawMessage `json:"problems"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &report); err != nil {
		t.Fatalf("%v\n%s\n%s", err, r.Stdout, r.Stderr)
	}
	if len(report.Problems) != 0 {
		t.Errorf("配布物の --lint に問題がある:\n%s", r.Stdout)
	}
}

func TestEveryContextFileOfTheCommonRulesFitsTheLimit(t *testing.T) {
	t.Parallel()
	text, err := os.ReadFile(base.Final(".ccnavi/config/common/rules.yml"))
	if err != nil {
		t.Fatal(err)
	}
	data, err := yaml.Load(string(text))
	if err != nil {
		t.Fatal(err)
	}
	top, ok := data.(*yaml.Map)
	if !ok {
		t.Fatalf("rules.yml の最上位がマップでない: %T", data)
	}
	checked := 0
	for _, rules := range top.All() {
		list, _ := rules.([]any)
		for _, item := range list {
			rule, ok := item.(*yaml.Map)
			if !ok {
				continue
			}
			for _, key := range []string{"additionalContextFile", "additionalContextOnceFile"} {
				rel, _ := rule.Get(key).(string)
				if rel == "" {
					continue
				}
				checked++
				id, _ := rule.Get("id").(string)
				t.Run(id+"/"+rel, func(t *testing.T) {
					body, err := os.ReadFile(filepath.Join(base.Root(), filepath.FromSlash(rel)))
					if err != nil {
						t.Fatalf("文書が無い: %v", err)
					}
					if n := utf8.RuneCount(body); n > maxChars {
						t.Errorf("%d 文字（上限 %d）", n, maxChars)
					}
				})
			}
		}
	}
	if checked == 0 {
		t.Error("additionalContextFile を持つ共通ルールが 1 つも無い")
	}
}

var quickWork = regexp.MustCompile(`(?m)^  quick-work:\n((?:    .*\n)+)`)

// 共通の phases.yml の `quick-work` が `review: true` であることの受入テスト（#379）。
func TestCommonQuickWorkReviewsByMR(t *testing.T) {
	t.Parallel()
	text, err := os.ReadFile(base.Final(".ccnavi/config/common/phases.yml"))
	if err != nil {
		t.Fatal(err)
	}
	m := quickWork.FindStringSubmatch(strings.ReplaceAll(string(text), "\r\n", "\n"))
	if m == nil {
		t.Fatal("quick-work の定義が無い")
	}
	if !regexp.MustCompile(`(?m)^    review: true$`).MatchString(m[1]) {
		t.Errorf("quick-work が review: true でない:\n%s", m[1])
	}
}
