package handoff

import (
	"crypto/sha256"
	"encoding/hex"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/pyoracle"
)

// Python の tickets/sync/handoff と同じワークスペースを読ませ、見るだけの JSON（ハッシュ・差分・断る理由）、
// 取り込みの JSON、取り込んだあとのファイルのバイト列、取り込み待ちの一覧が 1 字も違わないことを見る。
// Python 側と Go 側は同じ中身の別のディレクトリで動かし、ルートのパスは ROOT に置き換えて比べる。

const handoffOracle = `
import hashlib, json, os, sys
from ccnavi.infra import settings
from ccnavi.tickets.sync import handoff
c = json.load(sys.stdin)
out = []
for case in c["cases"]:
    root = case["root"]
    esc = json.dumps(root)[1:-1]
    conf, _ = settings.load(root)
    plan = handoff.prepare(conf, root, case["wanted"] or None)
    row = {"view": json.dumps(handoff.plan_json(plan), ensure_ascii=True, indent=1).replace(esc, "ROOT"), "applied": ""}
    if case["apply"]:
        done = handoff.apply(plan, case["expect"] or plan.expect, via_board=case["board"])
        row["applied"] = json.dumps(handoff.applied_json(done), ensure_ascii=True, indent=1).replace(esc, "ROOT")
    files = {}
    for parent, dirs, names in os.walk(root):
        for n in names:
            full = os.path.join(parent, n)
            if os.path.isfile(full) and not os.path.islink(full):
                with open(full, "rb") as f:
                    files[os.path.relpath(full, root).replace(os.sep, "/")] = hashlib.sha256(f.read()).hexdigest()
    row["files"] = files
    row["pending"] = handoff.pending(root)
    row["defense"] = [e.defense for e in plan.entries]
    out.append(row)
print(json.dumps(out))
`

type handoffAnswer struct {
	View    string            `json:"view"`
	Applied string            `json:"applied"`
	Files   map[string]string `json:"files"`
	Pending []string          `json:"pending"`
	Defense []string          `json:"defense"`
}

const (
	docsRel     = ".ccnavi/docs/guide.md"
	scriptRel   = ".ccnavi/scripts/ccnavi-x.sh"
	settingsRel = ".claude/settings.json"
	rulesRel    = ".ccnavi/config/common/rules.yml"
	phasesRel   = ".ccnavi/config/common/phases.yml"
	projRules   = ".ccnavi/config/project/rules.yml"
	draftRoot   = "wip/handoff/"
)

const commonRules = `{"version": 1, "deny": [{"id": "guard-approved", "match": "Write|Edit|NotebookEdit", ` +
	`"glob": "*/.ccnavi/*", "except": [{"regex": "[\\\\/]wip[\\\\/]handoff[\\\\/]"}], ` +
	`"message": "ガードの設定と承認済みチケットです。ユーザに依頼してください。"}]}`

const phasesText = `version: 1
phases:
  research:
    kind: work
    title: 調査
    review: false
    scope: ["wip/research/*"]
  design:
    kind: work
    title: 設計
    review: true
    scope: ["wip/design/*"]
  implement:
    kind: work
    title: 実装とテスト
    review: true
    scope: ["src/*", "tests/*"]
  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: true
    scope: inherit
`

const templateText = "name: feature\ndescription: 機能を設計から実装まで進める\nwhen: 新しい機能を足すとき\n" +
	"plan:\n  - design\n  - {type: implement, after: [1]}\n"

const layerRules = "version: 1\ndeny:\n  - id: no-env\n    match: Bash\n    regex: 'cat \\.env'\n    message: 読まない\n" +
	"  - id: guard-mine\n    match: Write\n    glob: '*/secret/*'\n    except: [{glob: '*/secret/ok/*'}]\n    message: 書かない\n" +
	"ask:\n  - id: rm\n    match: Bash\n    regex: 'rm -rf'\n"

type scenario struct {
	name   string
	files  map[string]string
	wanted []string
	apply  bool
	expect string
	board  bool
}

func base() map[string]string {
	return map[string]string{
		rulesRel:    commonRules,
		phasesRel:   phasesText,
		docsRel:     "# 手引き\n\n古い文\n",
		scriptRel:   "#!/bin/sh\necho old\n",
		settingsRel: "{\"hooks\": {}}\n",
		projRules:   layerRules,
	}
}

func longDoc(change bool) string {
	var b strings.Builder
	for i := 0; i < 260; i++ {
		switch {
		case i%3 == 0:
			b.WriteString("\n")
		case i%7 == 0:
			b.WriteString("- 同じ行\n")
		case change && i%41 == 0:
			b.WriteString("書き換えた行 " + string(rune('a'+i%26)) + "\n")
		default:
			b.WriteString("行 " + strings.Repeat("x", i%13) + "\n")
		}
	}
	return b.String()
}

var scenarios = []scenario{
	{name: "empty"},
	{name: "docs", files: map[string]string{
		draftRoot + docsRel:                "# 手引き\r\n\n新しい文\u200b\n\ufeff末尾",
		draftRoot + ".ccnavi/docs/nfd.md":  "e\u0301\n",
		draftRoot + "learnings/i0001.md":   "学び\n",
		draftRoot + "repro.sh":             "echo\n",
		draftRoot + ".ccnavi/docs/.x.part": "途中\n",
	}},
	{name: "docs-new-and-same", files: map[string]string{
		draftRoot + ".ccnavi/CCNAVI.md": "x\n",
		draftRoot + docsRel:             "# 手引き\n\n古い文\n",
	}},
	{name: "long-diff", files: map[string]string{
		docsRel:             longDoc(false),
		draftRoot + docsRel: longDoc(true),
	}},
	{name: "invisible", files: map[string]string{
		draftRoot + docsRel: "# 手引き\n\n古い文\n\x00",
	}},
	{name: "bytes", files: map[string]string{
		draftRoot + ".ccnavi/docs/a.md":        "\xff\xfe",
		docsRel:                                "# \xe6\x89\n",
		draftRoot + docsRel:                    "# \xe6\x89\x8b\n",
		".ccnavi/docs/b.md":                    "\xff\n",
		draftRoot + ".ccnavi/docs/b.md":        "\xfe\n",
		draftRoot + ".ccnavi/messages/new.yml": "version: 1\nmessages:\n  X_Y:\n    kind: deny\n",
	}},
	{name: "templates", files: map[string]string{
		draftRoot + ".ccnavi/config/project/templates/feature.yml": templateText,
		draftRoot + ".ccnavi/config/project/templates/other.yml":   templateText,
		draftRoot + ".ccnavi/config/common/templates/todo.yml": "name: todo\ndescription: TODO 直す\nwhen: とき\n" +
			"plan: [design]\n# \u202e\n",
		".ccnavi/config/common/templates/feature.yml": strings.Replace(templateText, "機能を", "別の", 1),
	}},
	{name: "layer-rules", files: map[string]string{
		draftRoot + projRules: strings.Replace(strings.Replace(layerRules, "  - id: no-env\n    match: Bash\n    regex: 'cat \\.env'\n    message: 読まない\n", "", 1),
			"ask:", "  - id: guard-new\n    match: Edit\n    glob: '*/x/*'\n    message: x\nask:", 1),
		draftRoot + rulesRel:                            "version: 1\ndeny: []\n",
		draftRoot + ".ccnavi/config/project/phases.yml": phasesText,
	}, board: true},
	{name: "layer-rules-add-deny", files: map[string]string{
		draftRoot + projRules: strings.Replace(layerRules, "ask:", "  - id: more\n    match: Bash\n    regex: 'x'\n    message: y\nask:", 1),
	}, apply: true, board: true},
	{name: "defense", files: map[string]string{
		draftRoot + scriptRel:                           "#!/bin/sh\r\necho new\r\n",
		draftRoot + "handoff-.claude/settings.json":     "{\"hooks\": \n",
		draftRoot + "handoff-.claude/hooks/new.sh":      "echo\n",
		draftRoot + ".git/config":                       "x\n",
		draftRoot + "handoff-.claude/skills/x/SKILL.md": "# s\n",
	}},
	{name: "defense-settings", files: map[string]string{
		draftRoot + "handoff-.claude/settings.json": "{\"hooks\": {\"a\": 1}}\n",
	}, apply: true},
	{name: "board-refuses-defense", files: map[string]string{
		draftRoot + "handoff-.claude/settings.json": "{\"hooks\": {\"a\": 1}}\n",
		draftRoot + docsRel:                         "# 手引き\n\n新\n",
	}, apply: true, board: true},
	{name: "apply", files: map[string]string{
		draftRoot + docsRel: "# 手引き\n\n新しい文\n",
		draftRoot + ".ccnavi/config/project/templates/feature.yml": templateText,
		draftRoot + ".ccnavi/docs/same.md":                         "同じ\n",
		".ccnavi/docs/same.md":                                     "同じ\n",
	}, wanted: []string{docsRel, "wip/handoff/.CCNAVI/config/project/templates/Feature.yml", ".ccnavi/docs/same.md"},
		apply: true, board: true},
	{name: "wrong-expect", files: map[string]string{
		draftRoot + docsRel: "# 手引き\n\n新しい文\n",
	}, apply: true, expect: "0000"},
	{name: "wanted-absolute", files: map[string]string{draftRoot + docsRel: "x\n"}, wanted: []string{"/etc/passwd"}},
	{name: "wanted-drive", files: map[string]string{draftRoot + docsRel: "x\n"}, wanted: []string{`C:\x`}},
	{name: "wanted-parent", files: map[string]string{draftRoot + docsRel: "x\n"}, wanted: []string{"a/../b"}},
	{name: "wanted-empty", files: map[string]string{draftRoot + docsRel: "x\n"}, wanted: []string{"  "}},
	{name: "wanted-missing", files: map[string]string{draftRoot + docsRel: "x\n"}, wanted: []string{".ccnavi/docs/no.md"},
		apply: true},
	{name: "nothing-chosen", files: map[string]string{draftRoot + docsRel: "# 手引き\n\n古い文\n"}, apply: true},
}

func build(t *testing.T, dir string, files map[string]string) {
	t.Helper()
	for rel, text := range files {
		path := filepath.Join(dir, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func merged(extra map[string]string) map[string]string {
	out := base()
	for k, v := range extra {
		out[k] = v
	}
	return out
}

func listFiles(t *testing.T, root string) map[string]string {
	t.Helper()
	out := map[string]string{}
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if !d.Type().IsRegular() {
			return nil
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(root, path)
		sum := sha256.Sum256(raw)
		out[filepath.ToSlash(rel)] = hex.EncodeToString(sum[:])
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func dumped(t *testing.T, o *pyjson.Object, root string) string {
	t.Helper()
	text, err := pyjson.Dumps(o, pyjson.Options{Indent: 1})
	if err != nil {
		t.Fatal(err)
	}
	esc, _ := pyjson.Dumps(root, pyjson.Options{})
	return strings.ReplaceAll(text, esc[1:len(esc)-1], "ROOT")
}

func clearEnv(t *testing.T) {
	t.Helper()
	for _, kv := range os.Environ() {
		if k, _, _ := strings.Cut(kv, "="); strings.HasPrefix(k, "CCNAVI_") || k == "CLAUDE_PROJECT_DIR" {
			t.Setenv(k, "")
			os.Unsetenv(k)
		}
	}
}

func TestMatchesPython(t *testing.T) {
	clearEnv(t)
	top := t.TempDir()
	type pyCase struct {
		Root   string   `json:"root"`
		Wanted []string `json:"wanted"`
		Apply  bool     `json:"apply"`
		Expect string   `json:"expect"`
		Board  bool     `json:"board"`
	}
	var cases []pyCase
	var goRoots []string
	for i, s := range scenarios {
		py := filepath.Join(top, "py", s.name)
		mine := filepath.Join(top, "go", s.name)
		build(t, py, merged(s.files))
		build(t, mine, merged(s.files))
		cases = append(cases, pyCase{py, s.wanted, s.apply, s.expect, s.board})
		goRoots = append(goRoots, mine)
		_ = i
	}
	answers := pyoracle.Answers[[]handoffAnswer](t, "handoff", handoffOracle, map[string]any{"cases": cases})
	if len(answers) != len(scenarios) {
		t.Fatalf("答えが %d 件（%d 件のはず）", len(answers), len(scenarios))
	}
	for i, s := range scenarios {
		t.Run(s.name, func(t *testing.T) {
			root := goRoots[i]
			conf, _ := config.Load(root)
			plan := Prepare(conf, root, s.wanted)
			if got := dumped(t, plan.JSON(), root); got != answers[i].View {
				t.Errorf("見るだけの JSON が違う\n--- go\n%s\n--- python\n%s", got, answers[i].View)
			}
			defense := []string{}
			for _, e := range plan.Entries {
				defense = append(defense, e.Defense)
			}
			if strings.Join(defense, "\n") != strings.Join(answers[i].Defense, "\n") {
				t.Errorf("防御の記述の理由が違う: %q, Python は %q", defense, answers[i].Defense)
			}
			applied := ""
			if s.apply {
				expect := s.expect
				if expect == "" {
					expect = plan.Expect()
				}
				applied = dumped(t, Apply(plan, expect, s.board).JSON(), root)
			}
			if applied != answers[i].Applied {
				t.Errorf("取り込みの JSON が違う\n--- go\n%s\n--- python\n%s", applied, answers[i].Applied)
			}
			files := listFiles(t, root)
			for rel, sum := range answers[i].Files {
				if files[rel] != sum {
					t.Errorf("%s のバイト列が違う", rel)
				}
			}
			for rel := range files {
				if _, ok := answers[i].Files[rel]; !ok {
					t.Errorf("%s は Python の側に無い", rel)
				}
			}
			pending := Pending(root)
			if strings.Join(pending, "\n") != strings.Join(answers[i].Pending, "\n") {
				t.Errorf("取り込み待ちが違う: %q, Python は %q", pending, answers[i].Pending)
			}
		})
	}
}
