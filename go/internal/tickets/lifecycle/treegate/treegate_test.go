package treegate

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/lifecycle/internal/gitfixture"
	"ccnavi/internal/tickets/model/pyoracle"
)

var ops = []Op{ApprovalRequest, Finish, ReviewRequest, Ready}

const placeFile = "wip/proposals/todo/i0001.md"

// parentTree は bare のリモートを持つ親のツリー（main を push 済み）。
func parentTree(t *testing.T) string {
	t.Helper()
	root := gitfixture.Workspace(t)
	gitfixture.Remote(t, root)
	for rel, text := range map[string]string{
		"src/keep.py": "print(1)\n", placeFile: "v1\n", "wip/note.md": "n\n",
		".ccnavi/config/common/rules.yml": "a\n", "skills/process-test/SKILL.md": "s\n",
		"wip/proposals/todo/日本語.md": "j\n",
	} {
		gitfixture.Put(t, filepath.Join(root, filepath.FromSlash(rel)), text)
	}
	gitfixture.Commit(t, root, "first")
	gitfixture.Git(t, root, "push", "-q", "origin", "main")
	return root
}

func kinds(rs []Refusal) []Kind {
	out := []Kind{}
	for _, r := range rs {
		out = append(out, r.Kind)
	}
	return out
}

func commitFile(t *testing.T, dir, rel string) {
	gitfixture.Put(t, filepath.Join(dir, filepath.FromSlash(rel)), "new\n")
	gitfixture.Commit(t, dir, "x")
}

// 宣言した操作の全部で、条件ごとに止める・通すが宣言どおりか（Python の tests/core/test_treegate.py）。
func TestWatches(t *testing.T) {
	conf := &config.Settings{}
	cases := []struct {
		name  string
		setup func(t *testing.T, dir string)
		want  map[Op][]Kind
	}{
		{"何も無ければ全部通る", func(*testing.T, string) {}, nil},
		{"置き場の外の未コミットは全部止める", func(t *testing.T, dir string) {
			gitfixture.Put(t, filepath.Join(dir, "src", "keep.py"), "print(2)\n")
		}, map[Op][]Kind{ApprovalRequest: {DirtyParent}, Finish: {DirtyParent}, ReviewRequest: {DirtyParent}, Ready: {DirtyParent}}},
		{"置き場の中の未コミットは数えない", func(t *testing.T, dir string) {
			gitfixture.Put(t, filepath.Join(dir, filepath.FromSlash(placeFile)), "v2\n")
			gitfixture.Put(t, filepath.Join(dir, "wip", "proposals", "todo", "日本語.md"), "k\n")
		}, nil},
		{"ready だけが消した wip/ を通す", func(t *testing.T, dir string) {
			os.Remove(filepath.Join(dir, "wip", "note.md"))
		}, map[Op][]Kind{ApprovalRequest: {DirtyParent}, Finish: {DirtyParent}, ReviewRequest: {DirtyParent}}},
		{"未 push のコードはそれを見る操作が止める", func(t *testing.T, dir string) { commitFile(t, dir, "src/new.py") },
			map[Op][]Kind{ApprovalRequest: {UnpushedCode}, ReviewRequest: {UnpushedCode}, Ready: {UnpushedCode}}},
		{"未 push の置き場は ready だけが止める", func(t *testing.T, dir string) { commitFile(t, dir, "wip/proposals/todo/i0002.md") },
			map[Op][]Kind{Ready: {UnpushedCode}}},
		{"一度も送っていないブランチは承認の依頼だけが通る", func(t *testing.T, dir string) {
			gitfixture.Git(t, dir, "checkout", "-q", "-b", "fresh")
		}, map[Op][]Kind{ReviewRequest: {UnpushedCode}, Ready: {UnpushedCode}}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			dir := parentTree(t)
			c.setup(t, dir)
			for _, op := range ops {
				want := c.want[op]
				if want == nil {
					want = []Kind{}
				}
				if got := kinds(Check(op, dir, conf, Options{})); !reflect.DeepEqual(got, want) {
					t.Errorf("%s: %v, want %v", op, got, want)
				}
			}
		})
	}
	if len(watches) != len(ops) {
		t.Errorf("宣言した操作が %d 個", len(watches))
	}
}

func TestDirtyNamesThePathAndApprovalNamesTheCode(t *testing.T) {
	dir := parentTree(t)
	gitfixture.Put(t, filepath.Join(dir, "src", "keep.py"), "print(2)\n")
	if got := Check(Finish, dir, &config.Settings{}, Options{}); len(got) != 1 || !reflect.DeepEqual(got[0].Paths, []string{"src/keep.py"}) {
		t.Fatalf("%+v", got)
	}
	dir = parentTree(t)
	commitFile(t, dir, "src/new.py")
	got := Check(ApprovalRequest, dir, &config.Settings{}, Options{})
	if len(got) != 1 || !strings.Contains(got[0].Reason, "src/new.py") || !reflect.DeepEqual(got[0].Paths, []string{"src/new.py"}) || len(got[0].Hints) == 0 {
		t.Fatalf("%+v", got)
	}
}

func TestInPlaceFoldsCase(t *testing.T) {
	conf := &config.Settings{}
	for rel, want := range map[string]bool{"WIP/Proposals/todo/x.md": true, ".CCNAVI/Approved/doing/x.md": true, "wip/proposalsX/x.md": false} {
		if InPlace(conf, rel) != want {
			t.Errorf("%s: %v", rel, !want)
		}
	}
}

// ミラーのずれの文はずれ以外の件数を数え、大文字小文字を畳み、長い一覧を切る（Python の test_archive）。
func TestMirrorDriftText(t *testing.T) {
	conf := &config.Settings{}
	var drift []string
	for n := range 11 {
		drift = append(drift, fmt.Sprintf("skills/process-test/references/common/r%d.md", n))
	}
	text := DirtyRefusal(Ready, "", conf, append([]string{"SKILLS/process-test/SKILL.md", "src/keep.py"}, drift...), "", true).Line()
	for _, want := range []string{"ずれ以外の変更が 1 件ある", "ほか 2 件", "--yes --expect <値>"} {
		if !strings.Contains(text, want) {
			t.Errorf("%q が無い: %s", want, text)
		}
	}
	if strings.Contains(DirtyRefusal(Ready, "", conf, drift, "", true).Line(), "ずれ以外") {
		t.Error("ずれだけなのに、ずれ以外と言った")
	}
}

const oracle = `
import json, sys
from ccnavi.infra import settings
from ccnavi.tickets.lifecycle import treegate
c = json.load(sys.stdin)
conf = settings.Settings()
def refusals(rs, tree):
    return [[r.kind, r.reason.replace(tree, "<tree>"), [h.replace(tree, "<tree>") for h in r.hints], list(r.paths)] for r in rs]
out = []
for tree in c["trees"]:
    e = {}
    for op in c["ops"]:
        e[op] = refusals(treegate.check(op, tree, conf), tree)
        e[op + "+root"] = refusals(treegate.check(op, tree, conf, root=tree, mirror_guidance=False), tree)
    e["dirty"] = treegate.tree_dirty(tree, conf)
    e["dirty_wip"] = treegate.tree_dirty(tree, conf, allow_wip_removals=True)
    e["unpushed"] = [treegate.unpushed(tree, conf, "main", ignore_places=b) for b in (False, True)]
    e["diff"] = [list(treegate.diff_paths(tree, r)) for r in ("origin/main", "", "nothing")]
    out.append(e)
json.dump(out, sys.stdout, ensure_ascii=False)
`

func oracleTrees(t *testing.T) []string {
	var trees []string
	add := func(setup func(dir string)) {
		dir := parentTree(t)
		setup(dir)
		trees = append(trees, dir)
	}
	add(func(string) {})
	add(func(dir string) {
		gitfixture.Put(t, filepath.Join(dir, "src", "keep.py"), "print(2)\n")
		gitfixture.Put(t, filepath.Join(dir, "wip", "proposals", "todo", "日本語.md"), "k\n")
	})
	add(func(dir string) { os.Remove(filepath.Join(dir, "wip", "note.md")) })
	add(func(dir string) { commitFile(t, dir, "src/new.py") })
	add(func(dir string) { commitFile(t, dir, "wip/proposals/todo/i0002.md") })
	add(func(dir string) { gitfixture.Git(t, dir, "checkout", "-q", "-b", "fresh") })
	add(func(dir string) {
		for _, rel := range []string{".ccnavi/config/common/rules.yml", "skills/process-test/SKILL.md", "src/keep.py"} {
			gitfixture.Put(t, filepath.Join(dir, filepath.FromSlash(rel)), "drift\n")
		}
	})
	add(func(dir string) {
		for n := range 10 {
			gitfixture.Put(t, filepath.Join(dir, "src", fmt.Sprintf("f%02d.py", n)), "x\n")
			gitfixture.Git(t, dir, "add", "-N", "src")
		}
		gitfixture.Put(t, filepath.Join(dir, "src", "keep.py"), "print(3)\n")
	})
	return trees
}

func refusalsOf(rs []Refusal, tree string) []any {
	out := []any{}
	for _, r := range rs {
		hints := []string{}
		for _, h := range r.Hints {
			hints = append(hints, strings.ReplaceAll(h, tree, "<tree>"))
		}
		paths := r.Paths
		if paths == nil {
			paths = []string{}
		}
		out = append(out, []any{string(r.Kind), strings.ReplaceAll(r.Reason, tree, "<tree>"), hints, paths})
	}
	return out
}

func optional(paths []string, ok bool) any {
	if !ok {
		return nil
	}
	if paths == nil {
		return []string{}
	}
	return paths
}

// Python の treegate と同じツリーを読ませ、拒否の種類・文・次の一手・パス、未コミット・未 push・差分の読みが
// 1 字も違わないことを見る。
func TestMatchesPython(t *testing.T) {
	trees := oracleTrees(t)
	opNames := []string{}
	for _, op := range ops {
		opNames = append(opNames, string(op))
	}
	want := pyoracle.Answers[[]map[string]any](t, "treegate", oracle, map[string]any{"trees": trees, "ops": opNames})
	conf := &config.Settings{}
	for i, tree := range trees {
		got := map[string]any{}
		for _, op := range ops {
			got[string(op)] = refusalsOf(Check(op, tree, conf, Options{}), tree)
			got[string(op)+"+root"] = refusalsOf(Check(op, tree, conf, Options{Root: tree, PlainDirty: true}), tree)
		}
		got["dirty"] = optional(Dirty(tree, conf, false))
		got["dirty_wip"] = optional(Dirty(tree, conf, true))
		a, aok := Unpushed(tree, conf, "main", false)
		b, bok := Unpushed(tree, conf, "main", true)
		got["unpushed"] = []any{optional(a, aok), optional(b, bok)}
		diffs := []any{}
		for _, ref := range []string{"origin/main", "", "nothing"} {
			paths, why := DiffPaths(tree, ref)
			if paths == nil {
				paths = []string{}
			}
			diffs = append(diffs, []any{paths, why})
		}
		got["diff"] = diffs
		if i >= len(want) {
			t.Fatalf("Python の答えが %d 件しかない", len(want))
		}
		if g, w := normalize(t, got), want[i]; !reflect.DeepEqual(g, w) {
			keys := slices.Sorted(func(yield func(string) bool) {
				for k := range w {
					if !yield(k) {
						return
					}
				}
			})
			for _, k := range keys {
				if !reflect.DeepEqual(g.(map[string]any)[k], w[k]) {
					t.Errorf("ツリー %d の %s が Python と違う\nGo:     %v\nPython: %v", i, k, g.(map[string]any)[k], w[k])
				}
			}
		}
	}
}

func normalize(t *testing.T, v any) any {
	t.Helper()
	data, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatal(err)
	}
	return out
}
