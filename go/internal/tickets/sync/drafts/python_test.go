package drafts

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
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/proposal/templates"
	"ccnavi/internal/tickets/testkit"
)

// Python の tickets/sync/template_drafts と同じワークスペースで、閉じるときの案内・下書きの作成（断る理由と、
// 書いた下書きのバイト列）・全ツリーの下書きの答えと --lint の苦情が 1 字も違わないことを見る。
// Python 側と Go 側は同じ中身の別のディレクトリで、同じ順に呼ぶ。ルートのパスは ROOT に置き換えて比べる。

const draftsOracle = `
import hashlib, json, os, sys
from ccnavi.infra import settings
from ccnavi.tickets.proposal import plan_templates
from ccnavi.tickets.sync import template_drafts as td
c = json.load(sys.stdin)
root = c["root"]
esc = json.dumps(root)[1:-1]
dump = lambda v: json.dumps(v, ensure_ascii=True, indent=1).replace(esc, "ROOT")
conf, _ = settings.load(root)
out = []
for call in c["calls"]:
    if call[0] == "notice":
        out.append(td.close_notice(conf, root, call[1]).replace(root, "ROOT"))
    elif call[0] == "make":
        m = td.make(conf, root, call[1], call[2], to=call[3], layer=call[4])
        out.append(dump({"ok": m.ok, "error": m.error, "name": m.name, "path": m.rel, "target": m.target,
            "layer": m.layer, "sha": m.sha, "tree": m.tree, "tree_root": m.tree_root, "notes": m.notes,
            "problems": [{"severity": p.severity, "rule": p.rule, "detail": p.detail} for p in m.problems],
            "template": plan_templates.dump(m.template) if m.template else ""}))
    else:
        scanned = plan_templates.scan(conf, root)
        found = td.scan(conf, root, scanned.candidates)
        out.append(dump({"drafts": [td.draft_json(d, scanned) for d in found.drafts],
            "lint": [{"severity": p.severity, "rule": p.rule, "detail": p.detail} for p in td.lint_problems(found)]}))
files = {}
for parent, dirs, names in os.walk(root):
    for n in names:
        full = os.path.join(parent, n)
        with open(full, "rb") as f:
            files[os.path.relpath(full, root).replace(os.sep, "/")] = hashlib.sha256(f.read()).hexdigest()
print(json.dumps({"out": out, "files": files}))
`

type draftsAnswer struct {
	Out   []string          `json:"out"`
	Files map[string]string `json:"files"`
}

const phasesText = `version: 1
phases:
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

const copied = `phases:
  design:
    kind: work
    title: 設計
    review: true
    scope:
    - wip/design/*
  implement:
    kind: work
    title: 実装とテスト
    review: true
    scope:
    - src/*
    - tests/*
  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: true
    scope: inherit
`

const body = "## 目的\n\n本文\n\n## 範囲\n\n本文\n\n## 対象外\n\n本文\n\n## 完了の確かめ方\n\n本文\n\n## 計画の理由\n\n本文\n"

func parent(id, extra string) string {
	return "---\nversion: 1\nticket: " + id + "\nmr: 1\nissue: 1\n" + copied +
		"plan:\n  - design\n  - {type: implement, after: [1]}\n" + extra +
		"human_review:\n  required: true\n  reason: t\ntitle: 親の  作業\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\nstarted_at: \"\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n" + body
}

func child(id, phase string) string {
	return "---\nversion: 1\nticket: " + id + "\nparent: i0001\nphase: " + phase +
		"\nhuman_review:\n  required: true\n  reason: t\ntitle: 子\nrationale: r\n" +
		"allow:\n  - match: Write|Edit\n    glob: \"wip/design/*\"\nstarted_at: \"\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n"
}

const flowText = `id: wf-1
name: 調査の手順
version: 1.0.0
nodes:
  - {id: s, type: start, name: 開始, position: {x: 0, y: 0}, data: {}}
  - {id: p1, type: prompt, name: 読む, position: {x: 1, y: 0}, data: {prompt: 既存の振る舞いを読む}}
  - {id: e, type: end, name: 終了, position: {x: 2, y: 0}, data: {}}
connections:
  - {id: c1, from: s, to: p1, fromPort: output, toPort: input}
  - {id: c2, from: p1, to: e, fromPort: output, toPort: input}
`

const doneDir = ".ccnavi/approved/done/"
const flowsDir = ".ccnavi/approved/flows/"

func fixture() map[string]string {
	return map[string]string{
		".ccnavi/config/common/phases.yml": phasesText,
		".ccnavi/config/common/rules.yml":  `{"version": 1, "deny": [{"id": "x", "match": "Bash", "regex": "y", "message": "m"}]}`,
		doneDir + "i0001.md": parent("i0001",
			"feedback:\n  - {type: implement-feedback, after: [2]}\n  - {type: implement-feedback, after: [3, 9]}\n"),
		doneDir + "i0001-01-01.md":                               child("i0001-01-01", "1"),
		doneDir + "i0001-01-02.md":                               child("i0001-01-02", "1"),
		doneDir + "i0001-02-01.md":                               child("i0001-02-01", "2"),
		doneDir + "i0001-03-01.md":                               child("i0001-03-01", "3"),
		flowsDir + "i0001-01-02.yml":                             flowText,
		flowsDir + "i0001-01-01.yml":                             strings.Replace(flowText, "読む", "読み直す", 1),
		flowsDir + "i0001-02-01.yml":                             "- a\n",
		flowsDir + "i0001-03-01.yml":                             flowText,
		".ccnavi/approved/doing/i0002.md":                        parent("i0002", ""),
		doneDir + "i0004.md":                                     strings.Replace(parent("i0004", ""), "plan:\n  - design\n  - {type: implement, after: [1]}\n", "", 1),
		doneDir + "i0006.md":                                     parent("i0006", ""),
		doneDir + "i0007.md":                                     strings.Replace(parent("i0007", ""), "  - {type: implement, after: [1]}\n", "", 1),
		doneDir + "i0003.md":                                     strings.Replace(parent("i0003", ""), "base_sha: \"\"\n", "base_sha: \"\"\ncancelled_at: \"2026-01-01T00:00:00+09:00\"\ncancel_reason: r\n", 1),
		doneDir + "i0005.md":                                     parent("i0005", ""),
		"wip/handoff/.ccnavi/config/project/templates/i0005.yml": "name: i0005\nplan: [design]\n",
		"wip/handoff/.ccnavi/config/common/templates/bad.yml":    "name: other\nplan: [nope]\n",
		"wip/handoff/.ccnavi/config/common/templates/":           "",
		".ccnavi/config/common/templates/same.yml":               "name: same\ndescription: d\nwhen: w\nplan: [design, {type: implement, after: [1]}]\n",
	}
}

type call []any

var calls = []call{
	{"notice", "i0001"}, {"notice", "i0002"}, {"notice", "i0005"}, {"notice", "i0009"}, {"notice", "x/y"}, {"notice", "i0006"}, {"notice", "i0007"}, {"notice", "i0003"},
	{"make", "i0003", "", "", ""},
	{"make", "x/y", "", "", ""},
	{"make", "i0002", "", "", ""},
	{"make", "i0009", "", "", ""},
	{"make", "i0004", "", "", ""},
	{"make", "i0001", "bad name", "", ""},
	{"make", "i0001", "", "common", "self"},
	{"make", "i0001", "", "elsewhere", ""},
	{"make", "i0001", "", "project", "nolayer"},
	{"make", "i0001", "", "", ""},
	{"make", "i0001", "", "", ""},
	{"make", "i0001", "shared", "common", ""},
	{"make", "i0001", "same", "common", ""},
	{"notice", "i0001"},
	{"scan"},
}

func lay(t *testing.T, dir string) {
	t.Helper()
	for rel, text := range fixture() {
		path := filepath.Join(dir, filepath.FromSlash(rel))
		if strings.HasSuffix(rel, "/") {
			if err := os.MkdirAll(path, 0o755); err != nil {
				t.Fatal(err)
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

func problemsJSON(ps []rules.Problem) []any {
	out := []any{}
	for _, p := range ps {
		out = append(out, pyjson.NewObject("severity", string(p.Severity), "rule", p.Rule, "detail", p.Detail))
	}
	return out
}

func strs(in []string) []any {
	out := []any{}
	for _, s := range in {
		out = append(out, s)
	}
	return out
}

func dumped(t *testing.T, v any, root string) string {
	t.Helper()
	text, err := pyjson.Dumps(v, pyjson.Options{Indent: 1})
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

func goCall(t *testing.T, conf *config.Settings, root string, c call) string {
	switch c[0] {
	case "notice":
		return strings.ReplaceAll(CloseNotice(conf, root, c[1].(string)), root, "ROOT")
	case "make":
		m, why := Make(conf, root, Request{Parent: c[1].(string), Name: c[2].(string), To: Target(c[3].(string)), Layer: c[4].(string)})
		if m == nil {
			m = &Made{}
		}
		tmpl := ""
		if m.Template != nil {
			tmpl = templates.Dump(m.Template)
		}
		return dumped(t, pyjson.NewObject("ok", why == "", "error", why, "name", m.Name, "path", m.Rel,
			"target", m.Target, "layer", m.Layer, "sha", m.SHA, "tree", m.Tree, "tree_root", m.TreeRoot,
			"notes", strs(m.Notes), "problems", problemsJSON(m.Problems), "template", tmpl), root)
	}
	scanned := templates.Load(conf, root)
	found := Scan(conf, root, &scanned.Catalog)
	rows := []any{}
	for _, d := range found.Drafts {
		rows = append(rows, DraftJSON(d, scanned))
	}
	return dumped(t, pyjson.NewObject("drafts", rows, "lint", problemsJSON(LintProblems(found))), root)
}

func TestMatchesPython(t *testing.T) {
	clearEnv(t)
	top, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	py, mine := filepath.Join(top, "py"), filepath.Join(top, "go")
	lay(t, py)
	lay(t, mine)
	answer := testkit.Answers[draftsAnswer](t, "drafts", draftsOracle, map[string]any{"root": py, "calls": calls})
	conf, _ := config.Load(mine)
	for i, c := range calls {
		if got := goCall(t, conf, mine, c); got != answer.Out[i] {
			t.Errorf("%v が違う\n--- go\n%s\n--- python\n%s", c, got, answer.Out[i])
		}
	}
	files := map[string]string{}
	err = filepath.WalkDir(mine, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(mine, path)
		sum := sha256.Sum256(raw)
		files[filepath.ToSlash(rel)] = hex.EncodeToString(sum[:])
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	for rel, sum := range answer.Files {
		if files[rel] != sum {
			t.Errorf("%s のバイト列が違う", rel)
		}
	}
	if len(files) != len(answer.Files) {
		t.Errorf("ファイルの数が違う: %d, Python は %d", len(files), len(answer.Files))
	}
}
