package integrate

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/tree"
)

// candidateWorkspace は記録の無い直接作業 2 本（どちらもコミットあり）と、その候補の記録。
func candidateWorkspace(t *testing.T) (string, *config.Settings, string) {
	root := workspace(t)
	var entries []any
	for i, name := range []string{"aaa", "bbb"} {
		addWorktree(t, root, name, "main")
		put(t, filepath.Join(worktree(root, name), "src", "x.txt"), name+"\n")
		commitAll(t, worktree(root, name), name)
		entries = append(entries, map[string]any{"name": name, "path": worktree(root, name), "seen": i + 1, "checked": 0})
	}
	conf := settingsOf(t, root)
	path := filepath.Join(conf.State, "direct-s1.json")
	data, _ := json.Marshal(map[string]any{"session": "s1", "entries": entries})
	put(t, path, string(data))
	return root, conf, path
}

func checkedOf(t *testing.T, path string) map[string]float64 {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var saved struct {
		Entries []struct {
			Name    string  `json:"name"`
			Checked float64 `json:"checked"`
		} `json:"entries"`
	}
	if err := json.Unmarshal(data, &saved); err != nil {
		t.Fatal(err)
	}
	out := map[string]float64{}
	for _, e := range saved.Entries {
		out[e.Name] = e.Checked
	}
	return out
}

// 期限が過ぎていれば git を起こさず、候補もそのまま残す。
func TestAnExpiredDeadlineKeepsTheCandidates(t *testing.T) {
	root, conf, path := candidateWorkspace(t)
	looked := 0
	look := func(*config.Settings, string, tree.Tree, time.Time) Probe { looked++; return Probe{State: Hit} }
	if got := directAtStop(conf, root, root, "s1", time.Now().Add(-time.Second), look); got != nil {
		t.Fatalf("期限切れで促した: %+v", got)
	}
	if looked != 0 {
		t.Errorf("期限切れで %d 本を調べた", looked)
	}
	if got := checkedOf(t, path); len(got) != 2 || got["aaa"] != 0 || got["bbb"] != 0 {
		t.Errorf("候補が変わった: %v", got)
	}
}

// 不明の結果は候補を外さず、調べた時刻だけを進める（次は後回しになる）。
func TestAnUnknownProbeKeepsTheCandidate(t *testing.T) {
	root, conf, path := candidateWorkspace(t)
	var seen []string
	look := func(_ *config.Settings, _ string, here tree.Tree, _ time.Time) Probe {
		seen = append(seen, here.Name)
		return Probe{State: Unknown}
	}
	directAtStop(conf, root, root, "s1", time.Time{}, look)
	slices.Sort(seen)
	if !slices.Equal(seen, []string{"aaa", "bbb"}) {
		t.Errorf("調べた候補: %v", seen)
	}
	got := checkedOf(t, path)
	if len(got) != 2 || got["aaa"] <= 0 || got["bbb"] <= 0 {
		t.Errorf("候補の記録: %v", got)
	}
}

// 促しの文の git は、ラッパー（ccnavi-git.sh）を通るものだけ。
func TestTheNudgeHasNoGitOutsideTheWrapper(t *testing.T) {
	root := workspace(t)
	d := &Direct{Name: "scratch", Worktree: root + "/.claude/worktrees/scratch", Head: "h", Dirty: true, Ahead: 1,
		Integration: "main", IntegTree: root}
	for _, d := range []*Direct{d, {Name: d.Name, Worktree: d.Worktree, Head: "h", Ahead: 1, Integration: "main",
		IntegTree: root, NoRecord: true}} {
		text := strings.ReplaceAll(IntegrateNudge(root, d), "ccnavi-git.sh", "")
		word := func(r byte) bool {
			return r >= 0x80 || r == '_' || r == '.' || r == '-' || ('0' <= r && r <= '9') || ('a' <= r && r <= 'z') || ('A' <= r && r <= 'Z')
		}
		for i := strings.Index(text, "git"); i >= 0; {
			before := i == 0 || !(word(text[i-1]) || text[i-1] == '/')
			after := i+3 >= len(text) || !word(text[i+3])
			if before && after {
				t.Errorf("ラッパーを通らない git: %q", text[max(0, i-20):min(len(text), i+23)])
			}
			next := strings.Index(text[i+3:], "git")
			if next < 0 {
				break
			}
			i += 3 + next
		}
	}
}
