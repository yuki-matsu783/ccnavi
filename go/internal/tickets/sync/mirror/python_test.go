package mirror

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/testkit"
)

// Python の tickets/sync（mirrorsync・promptsync・mirror_gap・skillsync.installed）と同じワークスペースを作って
// 両方に揃えさせ、`user mirror --json` の JSON（拡張との契約。expect の値を含む）、揃えた後の写し先のバイト列、
// 発言ごとの写しの行が 1 字も違わないことを見る。書くので、Python と Go は同じ形の別のワークスペースで走らせる。

const mirrorOracle = `
import base64, json, os, sys, yaml
from ccnavi.infra import settings, yamlread
yamlread.LOADER = yaml.SafeLoader
from ccnavi.tickets.sync import mirror_gap, mirrorsync, promptsync, skillsync
c = json.load(sys.stdin)
def dumps(v):
    return json.dumps(v, ensure_ascii=True, indent=1)
def dump(base):
    out = {}
    for parent, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in dirs + names:
            p = os.path.join(parent, n)
            rel = os.path.relpath(p, base).replace(os.sep, "/")
            if n == ".git":
                continue
            if os.path.islink(p):
                out[rel] = "@"
            elif os.path.isdir(p):
                out[rel + "/"] = ""
            else:
                with open(p, "rb") as f:
                    out[rel] = base64.b64encode(f.read()).decode()
    return out
out = {"user": [], "prompt": []}
for root in c["user"]:
    conf, _ = settings.load(root)
    row = {"views": [dumps(mirrorsync.plan_json(mirrorsync.prepare(conf, root, w))) for w in c["wanted"]]}
    row["stale"] = dumps(mirrorsync.applied_json(mirrorsync.apply(conf, mirrorsync.prepare(conf, root), "0" * 64)))
    plan = mirrorsync.prepare(conf, root, ["lib", "app"])
    row["applied"] = dumps(mirrorsync.applied_json(mirrorsync.apply(conf, plan, plan.expect)))
    row["after"] = dumps(mirrorsync.plan_json(mirrorsync.prepare(conf, root)))
    row["files"] = dump(os.path.join(root, "projects"))
    row["installed"] = [skillsync.installed(root, n) for n in c["skills"]]
    out["user"].append(json.loads(json.dumps(row).replace(root, "<root>")))
for root in c["prompt"]:
    conf, _ = settings.load(root)
    row = {"first": promptsync.sync(conf, root), "second": promptsync.sync(conf, root)}
    with open(os.path.join(root, ".ccnavi", "config", "common", "risks.yml"), "a", encoding="utf-8") as f:
        f.write("# 直した\n")
    row["third"] = promptsync.sync(conf, root)
    row["files"] = dump(os.path.join(root, ".claude", "worktrees"))
    out["prompt"].append(json.loads(json.dumps(row).replace(root, "<root>")))
conf, _ = settings.load(c["user"][0])
mirrored, others = mirror_gap.split(conf, c["paths"])
out["gap"] = [mirrored, others, mirror_gap.label(mirrored), mirror_gap.others_note(others), mirror_gap.NOT_WORK, mirror_gap.ASK]
json.dump(out, sys.stdout, ensure_ascii=False)
`

type userRow struct {
	Views     []string          `json:"views"`
	Stale     string            `json:"stale"`
	Applied   string            `json:"applied"`
	After     string            `json:"after"`
	Files     map[string]string `json:"files"`
	Installed []string          `json:"installed"`
}

type promptRow struct {
	First  []string          `json:"first"`
	Second []string          `json:"second"`
	Third  []string          `json:"third"`
	Files  map[string]string `json:"files"`
}

type mirrorAnswers struct {
	User   []userRow   `json:"user"`
	Prompt []promptRow `json:"prompt"`
	Gap    []any       `json:"gap"`
}

var (
	oracleWanted = [][]string{nil, {"LIB"}, {"nothing", "app"}}
	oracleSkills = []string{"process-test", "said-yes", "plain", ""}
	oraclePaths  = []string{".ccnavi/config/common/rules.yml", "Skills/a/SKILL.md", "src/a.go", "skills2/x", "README.md"}
)

// userVariants は `user mirror` を走らせるワークスペースの形。fullWorkspace に足す変更。
var userVariants = []struct {
	name string
	edit func(t *testing.T, root string)
}{
	{"そのまま", func(*testing.T, string) {}},
	{"app のミラーがリンク", func(t *testing.T, root string) {
		link(t, filepath.Join(root, "elsewhere"), commonPath(project(root, "app")))
	}},
	{"共通の rules が読めない", func(t *testing.T, root string) {
		testkit.Put(t, commonPath(root, "rules.yml"), "version: 99\n")
	}},
	{"工程スキルの common がリンク", func(t *testing.T, root string) {
		if err := os.RemoveAll(skillPath(root, "process-test", "references", "common")); err != nil {
			t.Fatal(err)
		}
		link(t, filepath.Join(root, "elsewhere"), skillPath(root, "process-test", "references", "common"))
	}},
	{"共通が空", func(t *testing.T, root string) {
		for _, dir := range []string{commonPath(root), filepath.Join(root, ".claude")} {
			if err := os.RemoveAll(dir); err != nil {
				t.Fatal(err)
			}
		}
	}},
}

// fullWorkspace は写すものを一通り持つワークスペース。lib には前に写したミラーと skills/ が残っている。
func fullWorkspace(t *testing.T) string {
	t.Helper()
	root := workspace(t)
	if err := os.MkdirAll(filepath.Join(root, "elsewhere"), 0o755); err != nil {
		t.Fatal(err)
	}
	testkit.Put(t, commonPath(root, "risks.yml"), commonRisk+scriptFactors)
	putBytes(t, commonPath(root, "scripts", "count.sh"), countScript)
	testkit.Put(t, commonPath(root, "rule-expectations.yml"), expectations)
	testkit.Put(t, commonPath(root, "templates", "feature.yml"), featureTemplate)
	testkit.Put(t, commonPath(root, "templates", "broken.yml"), "plan: [work\n")
	testkit.Put(t, commonPath(root, "templates", "notes.txt"), "x\n")
	skill(t, root, "process-test", "# 本文\n", map[string]string{"a.md": "A\n", "sub/b.md": "B\n", "note.txt": "x\n"})
	testkit.Put(t, skillPath(root, "process-test", "references", "local", "x.md"), "local\n")
	testkit.Put(t, skillPath(root, "said-yes", "SKILL.md"), "---\nprocess: yes\n---\n")
	testkit.Put(t, skillPath(root, "plain", "SKILL.md"), "---\nname: plain\n---\n")
	lib := project(root, "lib")
	testkit.Put(t, commonPath(lib, "rules.yml"), strings.ReplaceAll(commonRules, "\n", "\r\n"))
	testkit.Put(t, commonPath(lib, "old.yml"), "version: 1\n")
	testkit.Put(t, commonPath(lib, "templates", "old.yml"), featureTemplate)
	testkit.Put(t, filepath.Join(lib, "skills", "process-test", "SKILL.md"), "前のもの\n")
	testkit.Put(t, filepath.Join(lib, "skills", "process-test", "references", "common", "deep", "gone.md"), "gone\n")
	testkit.Put(t, filepath.Join(lib, "skills", "process-test", "references", "common", "keep.txt"), "keep\n")
	testkit.Put(t, filepath.Join(lib, "skills", "process-test", "references", "own.md"), "mine\n")
	if err := os.MkdirAll(filepath.Join(root, "projects", "Zed", ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	return root
}

func dumps(t *testing.T, v *pyjson.Object) string {
	t.Helper()
	text, err := pyjson.Dumps(v, pyjson.Options{Indent: 1})
	if err != nil {
		t.Fatal(err)
	}
	return text
}

// rooted は root を <root> に置き換える（Python の答えと同じ表記にする）。nil は空の並びにそろえる（JSON の []）。
func rooted(root string, row []string) []string {
	out := make([]string, len(row))
	for i, s := range row {
		out[i] = strings.ReplaceAll(s, root, "<root>")
	}
	return out
}

func goUser(t *testing.T, root string) userRow {
	conf := settingsOf(root)
	var row userRow
	for _, wanted := range oracleWanted {
		row.Views = append(row.Views, dumps(t, PlanJSON(Prepare(conf, root, wanted))))
	}
	row.Stale = dumps(t, AppliedJSON(Apply(conf, Prepare(conf, root, nil), strings.Repeat("0", 64))))
	plan := Prepare(conf, root, []string{"lib", "app"})
	row.Applied = dumps(t, AppliedJSON(Apply(conf, plan, plan.Expect())))
	row.After = dumps(t, PlanJSON(Prepare(conf, root, nil)))
	row.Files = dump(t, filepath.Join(root, "projects"))
	for _, name := range oracleSkills {
		row.Installed = append(row.Installed, Installed(root, name))
	}
	row.Views = rooted(root, row.Views)
	texts := rooted(root, []string{row.Stale, row.Applied, row.After})
	row.Stale, row.Applied, row.After = texts[0], texts[1], texts[2]
	return row
}

func goPrompt(t *testing.T, root string) promptRow {
	conf := settingsOf(root)
	row := promptRow{First: Sync(conf, root), Second: Sync(conf, root)}
	f, err := os.OpenFile(commonPath(root, "risks.yml"), os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString("# 直した\n"); err != nil {
		t.Fatal(err)
	}
	f.Close()
	row.Third = Sync(conf, root)
	row.Files = dump(t, filepath.Join(root, ".claude", "worktrees"))
	row.First, row.Second, row.Third = rooted(root, row.First), rooted(root, row.Second), rooted(root, row.Third)
	return row
}

func promptWorkspace(t *testing.T, broken bool) string {
	root := fullWorkspace(t)
	startedParent(t, root)
	if broken {
		testkit.Put(t, commonPath(root, "phases.yml"), "version: 1\nphases: 3\n")
	}
	return root
}

func TestMatchesPython(t *testing.T) {
	var pyUser, goUsers, pyPrompt, goPrompts []string
	for _, v := range userVariants {
		for _, roots := range []*[]string{&pyUser, &goUsers} {
			root := fullWorkspace(t)
			v.edit(t, root)
			*roots = append(*roots, root)
		}
	}
	for _, broken := range []bool{false, true} {
		pyPrompt = append(pyPrompt, promptWorkspace(t, broken))
		goPrompts = append(goPrompts, promptWorkspace(t, broken))
	}
	answers := testkit.Answers[mirrorAnswers](t, "mirror", mirrorOracle, map[string]any{
		"user": pyUser, "prompt": pyPrompt, "wanted": oracleWanted, "skills": oracleSkills, "paths": oraclePaths,
	})
	for i, v := range userVariants {
		got := goUser(t, goUsers[i])
		if !reflect.DeepEqual(got, answers.User[i]) {
			t.Errorf("%s:\n go %+v\n py %+v", v.name, got, answers.User[i])
		}
	}
	for i := range pyPrompt {
		got := goPrompts[i]
		row := goPrompt(t, got)
		if !reflect.DeepEqual(row, answers.Prompt[i]) {
			t.Errorf("発言 %d:\n go %+v\n py %+v", i, row, answers.Prompt[i])
		}
	}
	mirrored, others := Split(settingsOf(goUsers[0]), oraclePaths)
	gap := []any{toAny(mirrored), toAny(others), Label(mirrored), OthersNote(others), NotWork, Ask}
	if !reflect.DeepEqual(gap, answers.Gap) {
		t.Errorf("ずれ:\n go %q\n py %q", gap, answers.Gap)
	}
}

func toAny(list []string) []any {
	out := make([]any, len(list))
	for i, s := range list {
		out[i] = s
	}
	return out
}
