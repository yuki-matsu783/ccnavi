package skills

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// Python の projskills.index と突き合わせる。名前・説明の読み方、1 行への畳み方、上限での切り方を見る。

const indexOracle = `
import json, sys
from ccnavi.catalog import projskills
from ccnavi.infra import settings
c = json.load(sys.stdin)
conf = settings.load(c["root"])[0]
json.dump([projskills.index(conf, c["root"], cwd) for cwd in c["cwds"]], sys.stdout, ensure_ascii=False)
`

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
		testkit.Put(t, filepath.Join(lib, skillsDir, name, skillFile), text)
	}
	for i := range extra {
		name := "z" + strconv.Itoa(100+i)
		testkit.Put(t, filepath.Join(lib, skillsDir, name, skillFile), skill(name, strings.Repeat("説明", 60)))
	}
	testkit.Put(t, filepath.Join(root, "projects", "self", skillsDir, "x", skillFile), skill("x", "予約名"))
	return root, []string{lib, filepath.Join(lib, "src"), root, filepath.Join(root, "projects", "self"), ""}
}

func TestIndexMatchesPython(t *testing.T) {
	for _, extra := range []int{0, 40} {
		pyRoot, pyCwds := indexFixture(t, extra)
		goRoot, goCwds := indexFixture(t, extra)
		want := testkit.Answers[[]string](t, "", indexOracle, map[string]any{"root": pyRoot, "cwds": pyCwds})
		if !strings.Contains(want[0], "- deploy:") {
			t.Fatalf("Python の目録 = %q", want[0])
		}
		conf := testkit.Settings(t, goRoot)
		for i, cwd := range goCwds {
			got := ""
			if project := projectOf(conf, goRoot, cwd); project != "" {
				got = index(conf, project)
			}
			if got = strings.ReplaceAll(got, goRoot, pyRoot); got != want[i] {
				t.Errorf("extra=%d cwd=%s:\nGo:\n%s\nPython:\n%s", extra, cwd, got, want[i])
			}
		}
	}
}
