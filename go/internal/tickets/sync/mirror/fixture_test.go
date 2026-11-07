package mirror

import (
	"encoding/base64"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// テストの道具。共通レイヤー・工程スキル・プロジェクトを置いたワークスペースを作る。

const (
	commonRules = "version: 1\ndeny:\n" +
		"  - {id: credentials, match: Write|Edit, glob: \"*/.env*\", message: credentials are not edited.}\n" +
		"allow:\n  - {id: ws-src, match: Write|Edit, glob: \"*/src/*\"}\n"
	commonPhases = "version: 1\nphases:\n  work:\n    kind: work\n    title: 作業\n    review: false\n    scope: inherit\n"
	commonRisk   = "version: 1\nlevels: {medium: 20, high: 40, critical: 70}\nfactors:\n" +
		"  - {id: big-diff, points: 25, lines_over: 5, message: 行数が多い}\n"
	scriptFactors = "  - {id: counted, points: 5, script: .ccnavi/config/common/scripts/count.sh, message: 数えた}\n" +
		"  - {id: again, points: 2, script: .ccnavi/config/common/scripts/count.sh, message: もう一度}\n"
	expectations    = "version: 1\nexpectations: []\n"
	featureTemplate = "name: feature\ndescription: 機能を設計する\nwhen: 新しい機能を足すとき\nplan: [work]\n"
)

// countScript は UTF-8 でなく、改行に \r を含むスクリプト。バイト列のまま写ることを見る。
var countScript = []byte("# \x82\xa0\rprintf 1\n")

func putBytes(t *testing.T, path string, data []byte) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
}

func read(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

// link はリンクを張る。張れない環境ではテストを飛ばす。
func link(t *testing.T, target, path string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, path); err != nil {
		t.Skipf("シンボリックリンクが作れない: %v", err)
	}
}

func commonPath(root string, rel ...string) string {
	return filepath.Join(append([]string{root, ".ccnavi", "config", "common"}, rel...)...)
}

func skillPath(root, name string, rel ...string) string {
	return filepath.Join(append([]string{root, ".claude", "skills", name}, rel...)...)
}

// skill はワークスペースの工程スキルを作る。refs は references/common/ からの相対 → 中身。
func skill(t *testing.T, root, name, body string, refs map[string]string) {
	t.Helper()
	testkit.Put(t, skillPath(root, name, "SKILL.md"), "---\nname: "+name+"\nprocess: true\n---\n"+body)
	for rel, text := range refs {
		testkit.Put(t, skillPath(root, name, "references", "common", filepath.FromSlash(rel)), text)
	}
}

// workspace は共通レイヤー（設定 3 本）と、プロジェクト app・lib（.git だけ）を置いたワークスペース。
func workspace(t *testing.T) string {
	t.Helper()
	root := testkit.TempRoot(t)
	testkit.Put(t, commonPath(root, "rules.yml"), commonRules)
	testkit.Put(t, commonPath(root, "phases.yml"), commonPhases)
	testkit.Put(t, commonPath(root, "risks.yml"), commonRisk)
	for _, name := range []string{"app", "lib"} {
		if err := os.MkdirAll(filepath.Join(root, "projects", name, ".git"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func settingsOf(root string) *config.Settings {
	conf, _ := config.Load(root)
	return conf
}

func project(root, name string) string { return filepath.Join(root, "projects", name) }

// tree はディレクトリの下の中身（"/" 区切りの相対 → base64、ディレクトリは末尾 "/" で空、リンクは "@"）。.git は見ない。
func dump(t *testing.T, base string) map[string]string {
	t.Helper()
	out := map[string]string{}
	err := filepath.WalkDir(base, func(path string, d fs.DirEntry, err error) error {
		if err != nil || path == base {
			return err
		}
		rel := filepath.ToSlash(strings.TrimPrefix(path, base+string(filepath.Separator)))
		switch {
		case d.Name() == ".git":
			if d.IsDir() {
				return filepath.SkipDir
			}
		case d.Type()&fs.ModeSymlink != 0:
			out[rel] = "@"
		case d.IsDir():
			out[rel+"/"] = ""
		default:
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			out[rel] = base64.StdEncoding.EncodeToString(data)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// startedParent は lib から切った親 i0001 のワークツリーに、着手済みの承認済みチケットを置く。ワークツリーを返す。
func startedParent(t *testing.T, root string) string {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	lib := project(root, "lib")
	if err := os.RemoveAll(filepath.Join(lib, ".git")); err != nil {
		t.Fatal(err)
	}
	testkit.Git(t, root, "init", "-q", "-b", "main")
	testkit.Git(t, lib, "init", "-q", "-b", "main")
	testkit.Put(t, filepath.Join(lib, "README.md"), "lib\n")
	testkit.Git(t, lib, "add", "-A")
	testkit.Git(t, lib, "commit", "-q", "-m", "init")
	worktree := filepath.Join(root, ".claude", "worktrees", "i0001")
	testkit.Git(t, lib, "worktree", "add", "-q", "-b", "i0001", worktree)
	testkit.Put(t, filepath.Join(worktree, ".ccnavi", "approved", "doing", "i0001.md"), parentTicket)
	testkit.CopyMessages(t, root)
	return worktree
}

const parentTicket = "---\nversion: 1\nticket: i0001\nproject: lib\nmr: 1\nissue: 1\n" +
	"phases:\n  work:\n    kind: work\n    title: 作業\n    review: false\n    scope: inherit\n" +
	"plan:\n  - work\nhuman_review:\n  required: true\n  reason: t\ntitle: 親\nrationale: r\n" +
	"allow:\n  - match: Write|Edit\n    glob: \"src/*\"\n" +
	"started_at: \"2026-01-02T03:04:05+09:00\"\ncompleted_at: \"\"\nbase_sha: \"\"\n---\n\n本文\n"
