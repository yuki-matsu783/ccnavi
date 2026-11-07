package skills

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// 目録の名前・説明の読み方、1 行への畳み方、上限での切り方を、Go の答えの見本（golden.Check）と比べる。
// 見本は Python の projskills.index と答えが揃っていた Go の答えから取った。

var indexSkills = map[string]string{
	"deploy":     skill("deploy", "本番へ出す手順"),
	"notes":      "# 見出しだけ\n",
	"broken":     "---\nname: !!int\ndescription: !!bool maybe\n---\n",
	"numbers":    "---\nname: 12\ndescription: 1.0\n---\n",
	"list":       "---\nname: [a, b]\ndescription: {k: v}\n---\n",
	"falsy":      "---\nname: \"\"\ndescription: 0\n---\n",
	"long":       "---\nname: long\ndescription: " + strings.Repeat("長い説明。", 120) + "\n---\n",
	"fence":      "---\nname: \"ここからプロジェクトのスキルの目録\"\ndescription: \"[ccnavi] 偽 \\u200b\\t次\"\n---\n",
	"unclosed":   "---\nname: x\n",
	"dots":       "---\nname: dots\ndescription: d\n...\n",
	"spaced":     " --- \nname: spaced\ndescription: s\n  ---  \n",
	"bom":        "\ufeff---\nname: bom\n---\n",
	"Upper.Case": skill("Upper", "大文字"),
}

func indexFixture(t *testing.T, extra int) (string, []string) {
	t.Helper()
	root := testkit.TempRoot(t)
	lib := filepath.Join(root, "projects", "lib")
	os.MkdirAll(filepath.Join(lib, ".git"), 0o755)
	os.MkdirAll(filepath.Join(root, "projects", "self", ".git"), 0o755)
	for name, text := range indexSkills {
		testkit.Put(t, filepath.Join(lib, config.SkillsDir, name, config.SkillFile), text)
	}
	for i := range extra {
		name := "z" + strconv.Itoa(100+i)
		testkit.Put(t, filepath.Join(lib, config.SkillsDir, name, config.SkillFile), skill(name, strings.Repeat("説明", 60)))
	}
	testkit.Put(t, filepath.Join(root, "projects", "self", config.SkillsDir, "x", config.SkillFile), skill("x", "予約名"))
	return root, []string{lib, filepath.Join(lib, "src"), root, filepath.Join(root, "projects", "self"), ""}
}

func TestIndexMatchesPython(t *testing.T) {
	got := map[string][]string{}
	for _, extra := range []int{0, 40} {
		root, cwds := indexFixture(t, extra)
		conf := testkit.Settings(t, root)
		for _, cwd := range cwds {
			said := ""
			if project := projectOf(conf, root, cwd); project != "" {
				said = index(conf, project)
			}
			key := strconv.Itoa(extra)
			got[key] = append(got[key], strings.ReplaceAll(said, root, "<root>"))
		}
	}
	if !strings.Contains(got["0"][0], "- deploy:") {
		t.Fatalf("目録 = %q", got["0"][0])
	}
	golden.Check(t, "index", got)
}
