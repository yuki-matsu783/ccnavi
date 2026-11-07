package selfguard

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/ospath"
)

// 守る対象の組み立てと、バックアップと戻しの手順を、決めた形のディレクトリに当てて Go の答えの見本（golden.Check）と
// 比べる。パスは置き換えてから比べる。

func gitRun(t *testing.T, dir string, args ...string) {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "init.defaultBranch=main"}, args...)...)
	cmd.Dir = dir
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
}

// workspace はワークスペース（git）と、そこから切ったワークツリー w1、自分の git を持つプロジェクト lib と、lib から切った
// ワークツリー p1 を作る。
func workspace(t *testing.T) (root string) {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	root = filepath.Join(ospath.Realpath(t.TempDir()), "ws")
	for rel, text := range map[string]string{
		".claude/settings.json":                         `{"hooks": {}}`,
		".ccnavi/config/common/rules.yml":               "version: 1\n",
		".ccnavi/config/common/phases.yml":              "phases: []\n",
		".ccnavi/config/project/rules.yml":              "version: 1\n",
		".gitignore":                                    ".claude/worktrees/\nprojects/\nlogs/\n",
		"projects/lib/.ccnavi/config/project/rules.yml": "version: 1\n",
	} {
		write(t, filepath.Join(root, rel), text)
	}
	write(t, filepath.Join(root, "projects", "lib", "README"), "lib\n")
	copyMessages(t, root)
	gitRun(t, root, "init", "-q")
	gitRun(t, root, "add", "-A")
	gitRun(t, root, "commit", "-qm", "init")
	gitRun(t, root, "worktree", "add", "-q", filepath.Join(root, ".claude", "worktrees", "w1"))
	lib := filepath.Join(root, "projects", "lib")
	gitRun(t, lib, "init", "-q")
	gitRun(t, lib, "add", "-A")
	gitRun(t, lib, "commit", "-qm", "init")
	gitRun(t, lib, "worktree", "add", "-q", filepath.Join(root, ".claude", "worktrees", "p1"))
	return root
}

// copyMessages はリポジトリの文面ファイルを root に写す（報告の文を引くため）。
func copyMessages(t *testing.T, root string) {
	t.Helper()
	from := filepath.Join(repoRoot(t), ".ccnavi", "messages")
	entries, err := os.ReadDir(from)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		data, err := os.ReadFile(filepath.Join(from, e.Name()))
		if err != nil {
			t.Fatal(err)
		}
		write(t, filepath.Join(root, ".ccnavi", "messages", e.Name()), string(data))
	}
}

type layerJSON struct {
	Origin string `json:"origin"`
	Layer  string `json:"layer"`
	Kind   string `json:"kind"`
	Path   string `json:"path"`
}

func layersOf(root string) []layerJSON {
	return []layerJSON{
		{string(config.OriginCommon), config.LayerCommon, string(config.KindPhases), filepath.Join(root, ".ccnavi/config/common/phases.yml")},
		{string(config.OriginCommon), config.LayerCommon, string(config.KindRisk), filepath.Join(root, ".ccnavi/config/common/risks.yml")},
		{string(config.OriginSelf), config.LayerSelf, string(config.KindRules), filepath.Join(root, ".ccnavi/config/project/rules.yml")},
		{string(config.OriginProject), "lib", string(config.KindRules), filepath.Join(root, "projects/lib/.ccnavi/config/project/rules.yml")},
		{string(config.OriginProject), "self", string(config.KindRules), filepath.Join(root, "projects/self/.ccnavi/config/project/rules.yml")},
		{string(config.OriginProject), "common", string(config.KindPhases), filepath.Join(root, "projects/common/.ccnavi/config/project/phases.yml")},
		{string(config.OriginMirror), "lib", string(config.KindRules), filepath.Join(root, "projects/lib/.ccnavi/config/common/rules.yml")},
		{string(config.OriginSelf), config.LayerSelf, string(config.KindRules), filepath.Join(root, "elsewhere.yml")},
		{string(config.OriginSelf), "x", string(config.KindRisk), ""},
	}
}

func goLayers(in []layerJSON) []config.LayerFile {
	var out []config.LayerFile
	for _, l := range in {
		out = append(out, config.LayerFile{Origin: config.Origin(l.Origin), Layer: l.Layer, Kind: config.LayerKind(l.Kind), Path: l.Path})
	}
	return out
}

type targetJSON struct {
	Key   string `json:"key"`
	Path  string `json:"path"`
	Label string `json:"label"`
	Heavy bool   `json:"heavy"`
	Top   string `json:"top"`
	Copy  bool   `json:"copy"`
}

var binPathKey = regexp.MustCompile(`bin-path-[0-9a-f]{8}`)

func TestTargetsAgainstPython(t *testing.T) {
	if ospath.Windows {
		t.Skip("リンクを作れない機械がある")
	}
	root := workspace(t)
	dir := filepath.Dir(root)
	launcher := filepath.Join(root, ".ccnavi", "scripts", "ccnavi-launcher.sh")
	write(t, launcher, "#!/bin/sh\n")
	pathDir := filepath.Join(dir, "path")
	exe := filepath.Join(pathDir, "ccnavi")
	write(t, exe, "#!/bin/sh\n")
	if err := os.Chmod(exe, 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", pathDir)
	projects := filepath.Join(root, "projects")
	var all []json.RawMessage
	for _, c := range []struct{ rules, bin, projects string }{
		{filepath.Join(root, ".ccnavi/config/common/rules.yml"), "", projects},
		{filepath.Join(root, ".ccnavi/config/common/rules.yml"), launcher, projects},
		{filepath.Join(dir, "outside", "rules.yml"), filepath.Join(dir, "bin", "ccnavi"), ""},
		{"", "", ""},
	} {
		var got []targetJSON
		for _, x := range Targets(root, c.rules, c.bin, goLayers(layersOf(root)), c.projects) {
			got = append(got, targetJSON{x.Key, x.Path, x.Label, x.Heavy, x.Top, x.Copy})
		}
		data, _ := json.Marshal(got)
		// PATH で見つけた実体の鍵は、絶対パスの要約を持つ。
		norm := binPathKey.ReplaceAllString(strings.ReplaceAll(string(data), dir, "<dir>"), "bin-path-<digest>")
		all = append(all, json.RawMessage(norm))
	}
	golden.Check(t, "targets", all)
}

// 手順の突き合わせ。op は write・remove・touch・step・wipe（バックアップの置き場ごと消す）。
type stepOp struct {
	Op      string `json:"op"`
	Path    string `json:"path,omitempty"`
	Text    string `json:"text,omitempty"`
	Mtime   int64  `json:"mtime,omitempty"`
	Name    string `json:"name,omitempty"`
	Setting string `json:"setting,omitempty"`
	Session string `json:"session,omitempty"`
}

type stepResult struct {
	Outcomes [][3]string `json:"outcomes"`
	Report   string      `json:"report"`
}

type stepsAnswer struct {
	Steps []stepResult       `json:"steps"`
	Files map[string]*string `json:"files"`
}

var stamped = regexp.MustCompile(`\d{8}-\d{6}-`)

func goSteps(t *testing.T, root, state, bin string, ops []stepOp, watch []string) stepsAnswer {
	t.Helper()
	found := Targets(root, filepath.Join(root, ".ccnavi/config/common/rules.yml"), bin, nil, "")
	var res stepsAnswer
	for _, op := range ops {
		path := filepath.Join(root, op.Path)
		switch op.Op {
		case "write":
			write(t, path, op.Text)
			if op.Mtime != 0 {
				stamp := time.Unix(op.Mtime, 0)
				os.Chtimes(path, stamp, stamp)
			}
		case "touch":
			stamp := time.Unix(op.Mtime, 0)
			os.Chtimes(path, stamp, stamp)
		case "remove":
			os.Remove(path)
		case "wipe":
			os.RemoveAll(filepath.Join(state, backupDir, op.Session))
		default:
			step := map[string]Step{"before": Before, "after": After, "accept": Accept, "at_start": AtStart}[op.Name]
			got := step(config.Mode(op.Setting), state, op.Session, root, found)
			r := stepResult{Outcomes: [][3]string{}, Report: Report(root, got)}
			for _, o := range got {
				r.Outcomes = append(r.Outcomes, [3]string{o.Target.Key, string(o.Action), o.Detail})
			}
			res.Steps = append(res.Steps, r)
		}
	}
	res.Files = map[string]*string{}
	for _, rel := range watch {
		if data, err := os.ReadFile(filepath.Join(root, rel)); err == nil {
			s := string(data)
			res.Files[rel] = &s
		} else {
			res.Files[rel] = nil
		}
	}
	return res
}

func normalized(a stepsAnswer, root, state string) string {
	data, _ := json.MarshalIndent(a, "", " ")
	s := strings.ReplaceAll(strings.ReplaceAll(string(data), state, "<state>"), root, "<root>")
	return stamped.ReplaceAllString(s, "<stamp>-")
}

func TestStepsAgainstPython(t *testing.T) {
	if ospath.Windows {
		t.Skip("リンクを作れない機械がある")
	}
	const (
		settings = ".claude/settings.json"
		local    = ".claude/settings.local.json"
		rulesRel = ".ccnavi/config/common/rules.yml"
		binRel   = "dist/ccnavi"
		copyRel  = ".claude/worktrees/w1/.claude/settings.json"
	)
	step := func(name, setting, session string) stepOp {
		return stepOp{Op: "step", Name: name, Setting: setting, Session: session}
	}
	scenarios := map[string][]stepOp{
		"書き換えを戻す": {
			{Op: "write", Path: binRel, Text: "ELF 1", Mtime: 1_700_000_000},
			step("at_start", "enable", "s1"), step("accept", "enable", "s1"), step("before", "enable", "s1"),
			{Op: "write", Path: rulesRel, Text: "deny: []\n"}, {Op: "write", Path: settings, Text: "{}"},
			{Op: "write", Path: local, Text: "{}"}, {Op: "write", Path: copyRel, Text: "{}"},
			step("after", "enable", "s1"), step("before", "enable", "s1"), step("after", "enable", "s1"),
		},
		"dry-run は言うだけ": {
			{Op: "write", Path: binRel, Text: "ELF 1", Mtime: 1_700_000_000},
			step("at_start", "dry-run", "s1"), step("accept", "dry-run", "s1"), step("before", "dry-run", "s1"),
			{Op: "write", Path: rulesRel, Text: "deny: []\n"}, {Op: "write", Path: binRel, Text: "ELF 2", Mtime: 1_700_000_100},
			step("after", "dry-run", "s1"), {Op: "remove", Path: settings}, step("before", "dry-run", "s1"),
			step("after", "disable", "s1"), step("before", "disable", "s1"),
		},
		"消えたものは戻し受け入れたものは戻さない": {
			{Op: "write", Path: binRel, Text: "ELF 1", Mtime: 1_700_000_000},
			step("at_start", "enable", "s1"), step("accept", "enable", "s1"),
			{Op: "remove", Path: settings}, step("before", "enable", "s1"), step("after", "enable", "s1"),
			{Op: "remove", Path: settings}, {Op: "remove", Path: binRel}, step("accept", "enable", "s1"),
			step("before", "enable", "s1"), step("after", "enable", "s1"),
			{Op: "write", Path: settings, Text: `{"again": 1}`}, {Op: "write", Path: binRel, Text: "ELF 3", Mtime: 1_700_000_300},
			step("after", "enable", "s1"), step("before", "enable", "s1"),
			{Op: "write", Path: settings, Text: "{}"}, {Op: "write", Path: binRel, Text: "ELF 4", Mtime: 1_700_000_400},
			step("after", "enable", "s1"),
		},
		"実行ファイルの取り直しと戻し": {
			{Op: "write", Path: binRel, Text: "ELF 1", Mtime: 1_700_000_000},
			step("at_start", "enable", "s1"), step("before", "enable", "s1"),
			{Op: "write", Path: binRel, Text: "ELF 2", Mtime: 1_700_000_100}, step("after", "enable", "s1"),
			{Op: "write", Path: binRel, Text: "ELF 5", Mtime: 1_700_000_500}, step("before", "enable", "s1"),
			{Op: "write", Path: binRel, Text: "ELF 6", Mtime: 1_700_000_600}, step("after", "enable", "s1"),
			{Op: "remove", Path: binRel}, step("after", "enable", "s1"), step("after", "enable", "s1"),
			{Op: "wipe", Session: "s1"}, step("after", "enable", "s1"),
		},
		"バックアップが無ければ git": {
			step("accept", "enable", "s1"), {Op: "wipe", Session: "s1"},
			{Op: "write", Path: rulesRel, Text: "deny: []\n"}, step("after", "dry-run", "s1"), step("after", "enable", "s1"),
			step("after", "enable", "s1"), {Op: "remove", Path: settings}, step("before", "enable", "s2"),
			{Op: "remove", Path: copyRel}, step("before", "enable", "s3"), {Op: "remove", Path: local}, step("before", "enable", "s4"),
			{Op: "write", Path: local, Text: "{}"}, step("after", "enable", "s4"),
		},
		"セッションの識別子が無い": {
			step("accept", "enable", ""), {Op: "write", Path: settings, Text: "x"}, step("after", "enable", ""),
			step("at_start", "enable", "../evil/x"), step("accept", "enable", "../evil/x"),
		},
	}
	watch := []string{settings, local, rulesRel, binRel, copyRel}
	all := map[string]json.RawMessage{}
	for name, ops := range scenarios {
		root := workspace(t)
		state := filepath.Join(filepath.Dir(root), "state")
		got := goSteps(t, root, state, filepath.Join(root, binRel), ops, watch)
		all[name] = json.RawMessage(normalized(got, root, state))
	}
	golden.Check(t, "steps", all)
}
