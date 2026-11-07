package notice

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/model/pyoracle"
)

// pendingWorkspace は取り込みの途中の記録と跡を並べたワークスペース。
//
//	w4   版の無い marker（staged の昔の形）
//	w5   版 3 の marker
//	w9   marker の無いディレクトリ
//	v1   版 4、K が無く確認待ち
//	v2   版 4、HEAD が記録と違う
//	v3   版 4、K が取り込み先に載っていない
//	v4b  版 4、K の親が記録と違う
//	v6   版 4、欄が欠けている
//	left1 一時ワークツリーだけが残る、left2 専用の ref だけが残る、w1 の s・p1 の pending は普通の状態
//	d1   片付けの途中の done の記録（ブランチが残る）、d2 は片付け済みの done の記録
func pendingWorkspace(t *testing.T) string {
	root := workspace(t)
	gitDir := filepath.Join(root, ".git")
	head := headOf(t, root)
	treeSHA := strings.TrimSpace(runGit(t, root, "rev-parse", "HEAD^{tree}"))
	k := strings.TrimSpace(runGit(t, root, "commit-tree", treeSHA, "-p", head, "-m", "K"))
	marker := func(name string, lines ...string) {
		dir := filepath.Join(gitDir, integrate.RecordDir, name)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if lines != nil {
			put(t, filepath.Join(dir, markerFile), strings.Join(lines, "\n")+"\n")
		}
	}
	direct := func(name string) {
		put(t, filepath.Join(gitDir, tree.DirectDir, name), "version 4\nbase_branch main\nsnap_sha "+head+"\ncommon_dir "+gitDir+"\n")
	}
	marker("w4", "state staged", "branch w4", "branch_sha "+head)
	marker("w5", "version 3", "head "+head)
	marker("w9")
	for _, name := range []string{"v1", "v2", "v3", "v4b"} {
		direct(name)
	}
	marker("v1", "version 4", "head "+head, "tip "+head, "c_tree "+treeSHA, "tmp /x/tmp", "empty no")
	marker("v2", "version 4", "head "+strings.Repeat("0", 40), "tip "+head, "c_tree "+treeSHA)
	marker("v3", "version 4", "head "+head, "tip "+head, "c_tree "+treeSHA, "k "+k)
	marker("v4b", "version 4", "head "+strings.Repeat("0", 40), "tip "+head, "c_tree "+treeSHA, "k "+k)
	marker("v6", "version 4", "head "+head)
	if err := os.MkdirAll(filepath.Join(root, config.DirectTmpDir, tree.Self, "left1"), 0o755); err != nil {
		t.Fatal(err)
	}
	runGit(t, root, "update-ref", "refs/ccnavi/direct/left2/c", head)
	direct("w1")
	runGit(t, root, "update-ref", "refs/ccnavi/direct/w1/s", head)
	runGit(t, root, "update-ref", "refs/ccnavi/direct/p1/pending", head)
	put(t, filepath.Join(gitDir, integrate.DoneDir, "d1"), "version 4\nk x\n")
	runGit(t, root, "branch", "d1")
	put(t, filepath.Join(gitDir, integrate.DoneDir, "d2"), "version 4\nk x\n")
	return root
}

// landedWorkspace は K が取り込み先に載った記録 1 件だけのワークスペース（片付けの途中）。
func landedWorkspace(t *testing.T) string {
	root := workspace(t)
	gitDir := filepath.Join(root, ".git")
	head := headOf(t, root)
	treeSHA := strings.TrimSpace(runGit(t, root, "rev-parse", "HEAD^{tree}"))
	k := strings.TrimSpace(runGit(t, root, "commit-tree", treeSHA, "-p", head, "-m", "K"))
	runGit(t, root, "merge", "--ff-only", "-q", k)
	put(t, filepath.Join(gitDir, tree.DirectDir, "v4"), "version 4\nbase_branch main\nsnap_sha "+head+"\n")
	put(t, filepath.Join(gitDir, integrate.RecordDir, "v4", markerFile),
		"version 4\nhead "+head+"\ntip "+head+"\nc_tree "+treeSHA+"\nk "+k+"\n")
	return root
}

const pendingOracle = `
import json, os, sys, time
from ccnavi.infra import settings
from ccnavi.tickets.lifecycle import stop_integrate as osi
from ccnavi.tickets.notice import pending_integrate as pi
c = json.load(sys.stdin)
out = {}
for key, root in c["roots"].items():
    conf = settings.Settings(projects="")
    marks = os.path.join(root, ".git", osi.RECORD_DIR)
    lines = {}
    reported = set()
    for name in sorted(os.listdir(marks)) if os.path.isdir(marks) else []:
        reported.add(name)
        fields = osi._read_record(os.path.join(marks, name, "marker"))
        if fields.get("version") == "4":
            lines[name] = pi._v4_line(root, root, name, fields, time.monotonic() + 60)
        else:
            lines[name] = pi._unknown_line(root, root, name, fields.get("version", ""))
    for name, line in pi._leftovers(root, root, reported, time.monotonic() + 60):
        lines["left:" + name] = line
    out[key] = {
        "lines": {k: v.replace(root, "<root>") for k, v in lines.items()},
        "notice": pi.pending_notice(root, conf, time.monotonic() + 60).replace(root, "<root>"),
        "expired": pi.pending_notice(root, conf, time.monotonic() - 1).replace(root, "<root>"),
        "key": pi.repo_key(root, root),
    }
out["braces"] = pi._v4_line(c["roots"]["many"], c["braces"], "v7", {"version": "4"}, time.monotonic() + 60).replace(c["roots"]["many"], "<root>")
json.dump(out, sys.stdout, ensure_ascii=False)
`

func TestPendingIntegrateMatchesPython(t *testing.T) {
	roots := map[string]string{"many": pendingWorkspace(t), "landed": landedWorkspace(t), "empty": workspace(t)}
	braces := filepath.Join(roots["many"], "a{b}c{0}")
	want := pyoracle.Answers[map[string]any](t, "pending", pendingOracle, map[string]any{"roots": roots, "braces": braces})
	got := map[string]any{}
	for key, root := range roots {
		conf := &config.Settings{}
		deadline := time.Now().Add(time.Minute)
		marks := filepath.Join(root, ".git", integrate.RecordDir)
		lines := map[string]any{}
		reported := map[string]bool{}
		entries, _ := os.ReadDir(marks)
		for _, e := range entries {
			reported[e.Name()] = true
			fields := integrate.ReadRecord(filepath.Join(marks, e.Name(), markerFile))
			if fields["version"] == tree.DirectVersion {
				lines[e.Name()] = rel(root, v4Line(root, root, e.Name(), fields, deadline))
			} else {
				lines[e.Name()] = rel(root, unknownLine(root, root, e.Name(), fields["version"]))
			}
		}
		for _, l := range leftovers(root, root, reported, deadline) {
			lines["left:"+l.name] = rel(root, l.text)
		}
		got[key] = map[string]any{
			"lines":   lines,
			"notice":  rel(root, PendingIntegrate(conf, root, deadline)),
			"expired": rel(root, PendingIntegrate(conf, root, time.Now().Add(-time.Second))),
			"key":     RepoKey(root, root),
		}
	}
	got["braces"] = rel(roots["many"], v4Line(roots["many"], braces, "v7", map[string]string{"version": "4"}, time.Now().Add(time.Minute)))
	sameAsPython(t, got, want)
	if !strings.HasPrefix(got["landed"].(map[string]any)["notice"].(string), pendingHead+"\nv4（") {
		t.Errorf("5 件未満は 1 件ずつ言う: %v", got["landed"])
	}
	if got["empty"].(map[string]any)["notice"] != "" {
		t.Errorf("記録が無ければ空: %v", got["empty"])
	}
}

// 5 件以上は名前を全件 1 行に並べる。1 件ごとに増えるのは名前と区切りだけ。
func TestPendingIntegrateManyRecordsAreOneLine(t *testing.T) {
	root := workspace(t)
	conf := &config.Settings{}
	sizes := map[int]int{}
	for _, count := range []int{5, 8, 12} {
		marks := filepath.Join(root, ".git", integrate.RecordDir)
		if err := os.RemoveAll(marks); err != nil {
			t.Fatal(err)
		}
		var names []string
		for i := range count {
			name := fmt.Sprintf("rec%03d", i)
			names = append(names, name)
			put(t, filepath.Join(marks, name, markerFile), "version 3\n")
		}
		text := PendingIntegrate(conf, root, time.Now().Add(time.Minute))
		if strings.Contains(text, "を知りません") || !strings.Contains(text, strings.Join(names, "、")) {
			t.Errorf("%d 件:\n%s", count, text)
		}
		sizes[count] = len([]rune(text))
	}
	for _, pair := range [][2]int{{5, 8}, {8, 12}} {
		if per := (sizes[pair[1]] - sizes[pair[0]]) / (pair[1] - pair[0]); per != len([]rune("rec000、")) {
			t.Errorf("1 件ごとに増えるのは名前と区切りだけ: %d", per)
		}
	}
}
