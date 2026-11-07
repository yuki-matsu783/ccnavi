package integrate

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/stop"
	"ccnavi/internal/tickets/testkit"
)

// Python の tickets/lifecycle/stop_integrate と同じワークスペースを 2 つ作って、直接作業のワークツリーごとに
// 調べた結果・直接作業か・結び付く理由・記録・促しの文と、Stop の候補の調べ方が 1 字も違わないことを見る。

const integrateOracle = `
import json, os, sys
from ccnavi.infra import fsio, settings, tree
from ccnavi.tickets.lifecycle import stop_integrate as osi
from ccnavi.tickets.model import direct_candidates
c = json.load(sys.stdin)
root = c["root"]
def rel(s):
    return s.replace(root, "<root>")
conf, _ = settings.load(root)
def direct(d):
    if d is None:
        return None
    return [d.name, rel(d.worktree), d.head, d.dirty, d.ahead, d.integration, rel(d.integ_tree), rel(d.blocked),
            d.base, d.no_record, d.marker, d.origin_ahead, d.key, d.digest, rel(osi.integrate_nudge(root, d)),
            rel(osi.blocked_notice(d))]
out = {"trees": {}}
for name in c["names"]:
    here = tree.tree_of(root, os.path.join(root, ".claude", "worktrees", name), conf.projects)
    got = osi.probe(root, conf, here)
    rec = osi.direct_record(root, name)
    head = osi.ops_stop.worktree_head_sha(here.root)
    out["trees"][name] = {
        "probe": [got.state, got.drop, direct(got.found)],
        "check": list(osi.direct_check(root, conf, here)),
        "bound": rel(osi.bound_reason(root, conf, here)),
        "record": None if rec is None else [{k: rel(v) for k, v in rec.items()}],
        "has": [osi.has_record(root, name, head, d) for d in (None, False, True)],
        "marker": osi.marker_kind(os.path.join(root, ".git", osi.RECORD_DIR), name),
    }
out["integration"] = [osi.integration_name(root), osi._integration(root)]
out["roots"] = [rel(r) for r in osi._tree_roots(root, conf)]
path = direct_candidates.path_of(conf.state, "s1")
os.makedirs(conf.state, exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump({"session": "s1", "entries": [{"name": n, "path": os.path.join(root, ".claude", "worktrees", n),
               "seen": s, "checked": 0} for n, s in c["candidates"]]}, f)
first = osi.direct_at_stop(root, conf, root, "s1")
out["first"] = direct(first)
out["left"] = [[e["name"], e["checked"] > 0] for e in fsio.read_dict(path)["entries"]]
out["remember"] = osi.remember_nudge(conf.state, "s1", first)
out["again"] = direct(osi.direct_at_stop(root, conf, root, "s1"))
out["here"] = direct(osi.direct_at_stop(root, conf, os.path.join(root, ".claude", "worktrees", "dirty1"), ""))
json.dump(out, sys.stdout, ensure_ascii=False)
`

var directNames = []string{"scratch", "clean", "dirty1", "rec", "broken", "mk", "v4m", "donec", "i0001", "wb", "feat", "away"}

// candidates は Stop の候補の記録（名前と最後に書き込んだ時刻）。調べる順は時刻の新しい順。
var candidates = [][]any{{"clean", 4}, {"gone", 3}, {"i0001", 2}, {"scratch", 1}}

// integrateWorkspace の形（名前はワークツリー）。
//
//	scratch 記録の無い直接作業。コミットがある（--adopt を案内する）
//	clean   コミットが無い
//	dirty1  コミットと未コミットの変更がある
//	rec     記録のある直接作業。origin/main が手元の main より先にある
//	broken  記録の起点が欠けている
//	mk      記録があり、版を知らない取り込みの途中の記録がある
//	v4m     版 4 の取り込みの途中の記録がある
//	donec   片付けた記録のコピーが今の HEAD と同じ
//	i0001   ワークスペースルートに同じ名前の提案がある
//	wb      ブランチがワークツリーの名前と違う
//	feat    origin に同じ名前のブランチがある（親のブランチのワークツリー）
//	away    記録の取り込み先と、ユーザのチェックアウトのブランチが違う
func integrateWorkspace(t *testing.T) string {
	root := clock.Workspace(t)
	remote := filepath.Join(t.TempDir(), "remote.git")
	clock.Git(t, root, "init", "-q", "--bare", remote)
	clock.Git(t, root, "remote", "add", "origin", remote)
	clock.Git(t, root, "push", "-q", "origin", "main")
	clock.Git(t, root, "remote", "set-head", "origin", "main")
	snap := strings.TrimSpace(clock.Git(t, root, "rev-parse", "HEAD"))
	gitDir := filepath.Join(root, ".git")
	commit := func(name, file string) {
		testkit.Put(t, filepath.Join(testkit.Worktree(root, name), "src", file), file+"\n")
		commitAll(t, testkit.Worktree(root, name), file)
	}
	record := func(name string, lines ...string) {
		testkit.Put(t, filepath.Join(gitDir, "ccnavi-direct", name), strings.Join(lines, "\n")+"\n")
	}
	full := []string{"version 4", "base_branch main", "snap_sha " + snap, "common_dir " + gitDir}
	for _, name := range directNames {
		testkit.AddWorktree(t, root, name, "main")
	}
	commit("scratch", "a")
	commit("dirty1", "b")
	testkit.Put(t, filepath.Join(testkit.Worktree(root, "dirty1"), "new.txt"), "n\n")
	commit("rec", "c")
	record("rec", full...)
	clock.Git(t, root, "branch", "-q", "ahead", "main")
	testkit.Put(t, filepath.Join(root, "ahead.txt"), "x\n")
	clock.Git(t, root, "add", "ahead.txt")
	clock.Git(t, root, "commit", "-q", "-m", "ahead")
	clock.Git(t, root, "update-ref", "refs/remotes/origin/main", "HEAD")
	clock.Git(t, root, "reset", "-q", "--hard", snap)
	commit("broken", "d")
	record("broken", "version 4", "base_branch main")
	commit("mk", "e")
	record("mk", full...)
	testkit.Put(t, filepath.Join(gitDir, RecordDir, "mk", "marker"), "version 3\n")
	commit("v4m", "f")
	testkit.Put(t, filepath.Join(gitDir, RecordDir, "v4m", "marker"), "version 4\n")
	commit("donec", "g")
	head := strings.TrimSpace(clock.Git(t, testkit.Worktree(root, "donec"), "rev-parse", "HEAD"))
	testkit.Put(t, filepath.Join(gitDir, DoneDir, "donec"), "state staged\nbranch_sha "+head+"\n")
	commit("i0001", "h")
	testkit.Put(t, filepath.Join(root, "wip", "proposals", "todo", "I0001.md"), "---\nticket: i0001\n---\n")
	clock.Git(t, testkit.Worktree(root, "wb"), "checkout", "-q", "-b", "other")
	commit("feat", "i")
	clock.Git(t, testkit.Worktree(root, "feat"), "push", "-q", "origin", "feat")
	commit("away", "j")
	record("away", "version 4", "base_branch develop", "snap_sha "+snap, "common_dir "+gitDir)
	return root
}

func TestMatchesPython(t *testing.T) {
	pyRoot, goRoot := integrateWorkspace(t), integrateWorkspace(t)
	want := testkit.Answers[map[string]any](t, "integrate", integrateOracle,
		map[string]any{"root": pyRoot, "names": directNames, "candidates": candidates})
	conf := testkit.Settings(t, goRoot)
	rel := func(s string) string { return strings.ReplaceAll(s, goRoot, "<root>") }
	direct := func(d *Direct) any {
		if d == nil {
			return nil
		}
		return []any{d.Name, rel(d.Worktree), d.Head, d.Dirty, d.Ahead, d.Integration, rel(d.IntegTree), rel(d.Blocked),
			d.Base, d.NoRecord, d.Marker, d.OriginAhead, d.Key(), d.Digest(), rel(IntegrateNudge(goRoot, d)), rel(BlockedNotice(d))}
	}
	trees := map[string]any{}
	for _, name := range directNames {
		here, ok := tree.TreeOf(goRoot, testkit.Worktree(goRoot, name), conf.Projects)
		if !ok {
			t.Fatal(name)
		}
		got := probe(conf, goRoot, here, time.Time{})
		var rec any
		if r := DirectRecord(goRoot, name); r != nil {
			fields := map[string]string{"_common": rel(r.Common)}
			for k, v := range r.Fields {
				fields[k] = rel(v)
			}
			if r.Broken {
				fields["broken"] = "yes"
			}
			rec = []any{fields}
		}
		head := git.Head(here.Root, git.Options{})
		has := []any{}
		for _, d := range []*bool{nil, new(false), new(true)} {
			if recorded, known := HasRecord(goRoot, name, head, d, time.Time{}); known {
				has = append(has, recorded)
			} else {
				has = append(has, nil)
			}
		}
		reason, unsure := DirectCheck(conf, goRoot, here, time.Time{})
		trees[name] = map[string]any{
			"probe":  []any{got.State, got.Drop, direct(got.Found)},
			"check":  []any{reason, unsure},
			"bound":  rel(BoundReason(conf, goRoot, here)),
			"record": rec,
			"has":    has,
			"marker": MarkerKind(filepath.Join(goRoot, ".git", RecordDir), name),
		}
	}
	integration, _ := Integration(goRoot, time.Time{})
	var roots []string
	for _, r := range TreeRoots(conf, goRoot) {
		roots = append(roots, rel(r))
	}
	path := filepath.Join(conf.State, "direct-s1.json")
	var entries []any
	for _, c := range candidates {
		entries = append(entries, map[string]any{"name": c[0], "path": testkit.Worktree(goRoot, c[0].(string)), "seen": c[1], "checked": 0})
	}
	data, _ := json.Marshal(map[string]any{"session": "s1", "entries": entries})
	testkit.Put(t, path, string(data))
	first := DirectAtStop(conf, goRoot, goRoot, "s1", time.Time{})
	var left []any
	var saved struct {
		Entries []struct {
			Name    string  `json:"name"`
			Checked float64 `json:"checked"`
		} `json:"entries"`
	}
	read, _ := os.ReadFile(path)
	if err := json.Unmarshal(read, &saved); err != nil {
		t.Fatal(err)
	}
	for _, e := range saved.Entries {
		left = append(left, []any{e.Name, e.Checked > 0})
	}
	remember := stop.RememberNudge(conf.State, "s1", first)
	got := map[string]any{
		"trees":       trees,
		"integration": []any{IntegrationName(goRoot), integration},
		"roots":       roots,
		"first":       direct(first),
		"left":        left,
		"remember":    remember,
		"again":       direct(DirectAtStop(conf, goRoot, goRoot, "s1", time.Time{})),
		"here":        direct(DirectAtStop(conf, goRoot, testkit.Worktree(goRoot, "dirty1"), "", time.Time{})),
	}
	testkit.SameJSON(t, got, want)
}
