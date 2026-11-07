package directcand

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyjson"

	"ccnavi/internal/tickets/testkit"
)

type fixture struct {
	root, state, scratch string
	conf                 *config.Settings
}

// newFixture はワークスペースと、直接作業のワークツリー scratch を作る。
func newFixture(t *testing.T) fixture {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "--quiet", "-b", "main")
	testkit.Git(t, root, "commit", "--quiet", "--allow-empty", "-m", "init")
	scratch := filepath.Join(root, ".claude", "worktrees", "scratch")
	testkit.Git(t, root, "worktree", "add", "--quiet", "-b", "scratch", scratch)
	state := filepath.Join(root, "logs", "state")
	return fixture{root, state, scratch, &config.Settings{State: state}}
}

func (f fixture) write(t *testing.T, tool, path, cwd, session string, now time.Time) {
	t.Helper()
	field := fieldFilePath
	if tool == config.ToolNotebookEdit {
		field = fieldNotebook
	}
	if cwd == "" {
		cwd = f.root
	}
	in := hookio.Input{Event: hookio.PostToolUse, ToolName: tool, ToolInput: pyjson.NewObject(field, path), SessionID: session, Cwd: cwd}
	Touch(f.conf, f.root, in, now)
}

func (f fixture) names(t *testing.T) []string {
	t.Helper()
	var out []string
	for _, r := range read(f.state, "s1") {
		out = append(out, r.name)
	}
	return out
}

func TestWritesRegisterOnlyDirectWorktrees(t *testing.T) {
	f := newFixture(t)
	now := time.Now()
	for _, tool := range []string{config.ToolRead, config.ToolGrep, config.ToolBash} {
		f.write(t, tool, filepath.Join(f.scratch, "src", "a.py"), "", "s1", now)
	}
	f.write(t, config.ToolWrite, filepath.Join(f.root, "src", "main.py"), "", "s1", now)
	if got := f.names(t); len(got) != 0 {
		t.Fatalf("読み・メインのツリーが登録された: %v", got)
	}
	f.write(t, config.ToolEdit, filepath.Join(f.scratch, "src", "a.py"), "", "s1", now)
	got := Load(f.conf, f.root, "s1")
	if len(got) != 1 || got[0].Name != "scratch" || got[0].Checked != 0 {
		t.Fatalf("候補: %+v", got)
	}
	if Load(f.conf, f.root, "other") != nil {
		t.Error("別のセッションの記録が読めた")
	}
}

func TestRelativePathResolvesFromCwd(t *testing.T) {
	f := newFixture(t)
	f.write(t, config.ToolNotebookEdit, "src/x.ipynb", f.scratch, "s1", time.Now())
	if got := f.names(t); len(got) != 1 || got[0] != "scratch" {
		t.Errorf("候補: %v", got)
	}
}

func TestRootReachedThroughASymlinkStillRegisters(t *testing.T) {
	f := newFixture(t)
	link := f.root + "-link"
	if err := os.Symlink(f.root, link); err != nil {
		t.Skip(err)
	}
	t.Cleanup(func() { os.Remove(link) })
	in := hookio.Input{ToolName: config.ToolWrite, SessionID: "s1", Cwd: link,
		ToolInput: pyjson.NewObject(fieldFilePath, filepath.Join(link, ".claude", "worktrees", "scratch", "a.py"))}
	Touch(f.conf, link, in, time.Now())
	if got := f.names(t); len(got) != 1 {
		t.Errorf("候補: %v", got)
	}
}

func TestRecentTailEntryIsNotRewritten(t *testing.T) {
	f := newFixture(t)
	now := time.Now()
	f.write(t, config.ToolWrite, filepath.Join(f.scratch, "a.py"), "", "s1", now)
	path := pathOf(f.state, "s1")
	first, _ := os.ReadFile(path)
	f.write(t, config.ToolWrite, filepath.Join(f.scratch, "b.py"), "", "s1", now.Add(time.Second))
	if again, _ := os.ReadFile(path); string(again) != string(first) {
		t.Error("見たばかりの候補を書き直した")
	}
	f.write(t, config.ToolWrite, filepath.Join(f.scratch, "b.py"), "", "s1", now.Add(2*refreshWindow))
	if got := read(f.state, "s1"); len(got) != 1 || got[0].entry().Seen <= float64(now.Unix()) {
		t.Errorf("時間が空いても更新しない: %+v", got)
	}
}

func TestRecordIsCappedAndKeepsNumberTypes(t *testing.T) {
	state := t.TempDir()
	var rows []row
	for i := range 60 {
		rows = append(rows, row{fmt.Sprintf("n%d", i), fmt.Sprintf("/x/n%d", i), float64(i), 0})
	}
	save(state, "s1", rows)
	got := read(state, "s1")
	if len(got) != MaxEntries || got[0].name != "n10" {
		t.Fatalf("上限: %d %s", len(got), got[0].name)
	}
	raw, _ := os.ReadFile(pathOf(state, "s1"))
	if !strings.HasPrefix(string(raw), `{"session": "s1", "entries": [{"name": "n10", "path": "/x/n10", "seen": 10.0, "checked": 0}`) {
		t.Errorf("形: %.120s", raw)
	}
	Update(&config.Settings{State: state}, "s1", map[string]float64{"n10": 5.5}, map[string]bool{"n11": true})
	got = read(state, "s1")
	if len(got) != MaxEntries-1 || got[0].entry().Checked != 5.5 || got[1].name != "n12" {
		t.Errorf("Update: %+v", got[:2])
	}
}

func TestIntegrableName(t *testing.T) {
	for name, want := range map[string]bool{
		"scratch": true, "fix-1.2_a": true, "作業": false, "a作業": true, "": false,
		"main": false, "release": false, "release-1": false, "-x": false, ".x": false,
		"a..b": false, "a/b": false, `a\b`: false, "a b": false, "a@b": false,
	} {
		if got := IntegrableName(name); got != want {
			t.Errorf("IntegrableName(%q) = %v", name, got)
		}
	}
}
