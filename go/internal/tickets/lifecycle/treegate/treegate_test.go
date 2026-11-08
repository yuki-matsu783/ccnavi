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
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// goldenOps は記録した答え（testdata/treegate.json）に載る操作。Reviewed は記録に載せない。
var goldenOps = []Op{ApprovalRequest, Finish, ReviewRequest, Ready}

var ops = append(slices.Clone(goldenOps), Reviewed)

const placeFile = "wip/proposals/todo/i0001.md"

// parentTree は bare のリモートがある親のツリー（main を push 済み）。
func parentTree(t *testing.T) string {
	t.Helper()
	root := testkit.ShapedWorkspace(t)
	testkit.Remote(t, root)
	for rel, text := range map[string]string{
		"src/keep.py": "print(1)\n", placeFile: "v1\n", "wip/note.md": "n\n",
		".ccnavi/config/common/rules.yml": "a\n", "skills/process-test/SKILL.md": "s\n",
		"wip/proposals/todo/日本語.md": "j\n",
	} {
		testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), text)
	}
	testkit.Commit(t, root, "first")
	testkit.Git(t, root, "push", "-q", "origin", "main")
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
	testkit.Put(t, filepath.Join(dir, filepath.FromSlash(rel)), "new\n")
	testkit.Commit(t, dir, "x")
}

// 宣言した操作の全部で、条件ごとに止める・通すが宣言どおりか。
func TestWatches(t *testing.T) {
	conf := &config.Settings{}
	cases := []struct {
		name  string
		setup func(t *testing.T, dir string)
		want  map[Op][]Kind
	}{
		{"何も無ければ全部通る", func(*testing.T, string) {}, nil},
		{"置き場の外の未コミットは全部止める", func(t *testing.T, dir string) {
			testkit.Put(t, filepath.Join(dir, "src", "keep.py"), "print(2)\n")
		}, map[Op][]Kind{ApprovalRequest: {dirtyParent}, Finish: {dirtyParent}, ReviewRequest: {dirtyParent}, Ready: {dirtyParent}}},
		{"置き場の中の未コミットは数えない", func(t *testing.T, dir string) {
			testkit.Put(t, filepath.Join(dir, filepath.FromSlash(placeFile)), "v2\n")
			testkit.Put(t, filepath.Join(dir, "wip", "proposals", "todo", "日本語.md"), "k\n")
		}, nil},
		{"ready だけが消した wip/ を通す", func(t *testing.T, dir string) {
			os.Remove(filepath.Join(dir, "wip", "note.md"))
		}, map[Op][]Kind{ApprovalRequest: {dirtyParent}, Finish: {dirtyParent}, ReviewRequest: {dirtyParent}}},
		{"未 push のコードはそれを見る操作が止める", func(t *testing.T, dir string) { commitFile(t, dir, "src/new.py") },
			map[Op][]Kind{ApprovalRequest: {unpushedCode}, ReviewRequest: {unpushedCode}, Ready: {unpushedCode}, Reviewed: {unpushedCode}}},
		{"未 push の置き場は ready だけが止める", func(t *testing.T, dir string) { commitFile(t, dir, "wip/proposals/todo/i0002.md") },
			map[Op][]Kind{Ready: {unpushedCode}}},
		{"一度も送っていないブランチは承認の依頼だけが通る", func(t *testing.T, dir string) {
			testkit.Git(t, dir, "checkout", "-q", "-b", "fresh")
		}, map[Op][]Kind{ReviewRequest: {unpushedCode}, Ready: {unpushedCode}, Reviewed: {unpushedCode}}},
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
	testkit.Put(t, filepath.Join(dir, "src", "keep.py"), "print(2)\n")
	if got := Check(Finish, dir, &config.Settings{}, Options{}); len(got) != 1 || !reflect.DeepEqual(got[0].paths, []string{"src/keep.py"}) {
		t.Fatalf("%+v", got)
	}
	dir = parentTree(t)
	commitFile(t, dir, "src/new.py")
	got := Check(ApprovalRequest, dir, &config.Settings{}, Options{})
	if len(got) != 1 || !strings.Contains(got[0].Reason, "src/new.py") || !reflect.DeepEqual(got[0].paths, []string{"src/new.py"}) || len(got[0].Hints) == 0 {
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

// ミラーのずれの文はずれ以外の件数を数え、大文字小文字を畳み、長い一覧を切る。
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

func oracleTrees(t *testing.T) []string {
	var trees []string
	add := func(setup func(dir string)) {
		dir := parentTree(t)
		setup(dir)
		trees = append(trees, dir)
	}
	add(func(string) {})
	add(func(dir string) {
		testkit.Put(t, filepath.Join(dir, "src", "keep.py"), "print(2)\n")
		testkit.Put(t, filepath.Join(dir, "wip", "proposals", "todo", "日本語.md"), "k\n")
	})
	add(func(dir string) { os.Remove(filepath.Join(dir, "wip", "note.md")) })
	add(func(dir string) { commitFile(t, dir, "src/new.py") })
	add(func(dir string) { commitFile(t, dir, "wip/proposals/todo/i0002.md") })
	add(func(dir string) { testkit.Git(t, dir, "checkout", "-q", "-b", "fresh") })
	add(func(dir string) {
		for _, rel := range []string{".ccnavi/config/common/rules.yml", "skills/process-test/SKILL.md", "src/keep.py"} {
			testkit.Put(t, filepath.Join(dir, filepath.FromSlash(rel)), "drift\n")
		}
	})
	add(func(dir string) {
		for n := range 10 {
			testkit.Put(t, filepath.Join(dir, "src", fmt.Sprintf("f%02d.py", n)), "x\n")
			testkit.Git(t, dir, "add", "-N", "src")
		}
		testkit.Put(t, filepath.Join(dir, "src", "keep.py"), "print(3)\n")
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
		paths := r.paths
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

// 用意したツリーを読ませ、拒否の種類・文・次の一手・パス、未コミット・未 push・差分の読みが
// 記録した答えと 1 字も違わないことを見る。
func TestMatchesGolden(t *testing.T) {
	trees := oracleTrees(t)
	opNames := []string{}
	for _, op := range goldenOps {
		opNames = append(opNames, string(op))
	}
	want := golden.Load[[]map[string]any](t, "treegate")
	conf := &config.Settings{}
	for i, tree := range trees {
		got := map[string]any{}
		for _, op := range goldenOps {
			got[string(op)] = refusalsOf(Check(op, tree, conf, Options{}), tree)
			got[string(op)+"+root"] = refusalsOf(Check(op, tree, conf, Options{Root: tree, PlainDirty: true}), tree)
		}
		got["dirty"] = optional(Dirty(tree, conf, false))
		got["dirty_wip"] = optional(Dirty(tree, conf, true))
		a, aok := unpushed(tree, conf, "main", false)
		b, bok := unpushed(tree, conf, "main", true)
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
			t.Fatalf("golden の答えが %d 件しかない", len(want))
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
					t.Errorf("ツリー %d の %s が golden と違う\nGo:     %v\ngolden: %v", i, k, g.(map[string]any)[k], w[k])
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
