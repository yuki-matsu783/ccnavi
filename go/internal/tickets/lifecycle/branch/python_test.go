package branch

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// Python の branchfind・base_bound・next_id と同じワークスペースを 2 つ作って聞き、依頼文の指定・候補の一覧
// （JSON と行）・着手の指示（セッションごとの記録も）・取り込み先の結び付き・通し番号が 1 字も違わないことを見る。

func ticketText(id, extra string) string {
	return "---\nversion: 1\nticket: " + id + "\n" + extra + "human_review:\n  required: true\n  reason: t\n" +
		"title: 親 " + id + "\nrationale: r\nallow:\n  - match: Write|Edit\n    glob: \"src/*\"\n---\n\n本文\n"
}

// fixture のワークスペース。origin の URL は acme/w（追跡ブランチは update-ref で作る）。
//
//	i0001            親のワークツリー。issue: 152、mr: 5、branch: feature/152-login を持つ承認済みチケット
//	feature-9-new    issue: 152 を持つ承認待ちの提案（ワークスペースの todo/）
//	feature-7-a      done/ の閉じた親（ブランチ名が識別子と一致）
//	projects/proj    origin が grp/Proj のプロジェクト
func fixture(t *testing.T) string {
	root := testkit.ShapedWorkspace(t)
	testkit.Git(t, root, "remote", "add", "origin", "https://host.example/acme/w.git")
	parent := testkit.AddWorktree(t, root, "i0001", "main")
	testkit.Put(t, testkit.Approved(root, "i0001", "doing", "i0001"), ticketText("i0001", "issue: 152\nmr: 5\nbranch: feature/152-login\n"))
	testkit.Put(t, testkit.Approved(root, "i0001", "done", "feature-7-a"), ticketText("feature-7-a", "issue: 7\n"))
	testkit.Commit(t, parent, "approve")
	testkit.Put(t, filepath.Join(root, "wip", "proposals", "todo", "feature-9-new.md"), ticketText("feature-9-new", "issue: 152\n"))
	for _, name := range []string{"feature-152-login", "feature/152-login", "feature-1520-x", "claude/tender-volta-qmy99q", "feature-7-a", "x-152"} {
		testkit.Git(t, root, "branch", name, "main")
	}
	for _, name := range []string{"fix/152-y", "feature-152-login", "HEAD"} {
		testkit.Git(t, root, "update-ref", "refs/remotes/origin/"+name, "main")
	}
	testkit.AddWorktree(t, root, "feature-20-z", "main")
	testkit.Git(t, testkit.Worktree(root, "feature-20-z"), "checkout", "-q", "feature-152-login")
	proj := filepath.Join(root, "projects", "proj")
	if err := os.MkdirAll(proj, 0o755); err != nil {
		t.Fatal(err)
	}
	testkit.Git(t, proj, "init", "-q", "-b", "main")
	testkit.Git(t, proj, "remote", "add", "origin", "git@host.example:grp/Proj.git")
	testkit.Commit(t, proj, "init")
	testkit.Git(t, proj, "branch", "feature-152-p", "main")
	testkit.Put(t, filepath.Join(root, "host.json"), `{"checked": true, "host": "gitlab\n", "repo": "acme/w", "mrs": [`+
		`{"number": 5, "branch": "feature-152-login", "state": "opened", "url": "https://h/5", "title": "t\tx", "base": "main", "fork": true},`+
		`{"number": true, "branch": "b"}, {"number": 6, "branch": ""}, "x", {"number": 8, "branch": "only-host", "state": "merged"}]}`)
	testkit.Put(t, filepath.Join(root, "unchecked.json"), `{"checked": false, "reason": "glab が無い\n"}`)
	testkit.Put(t, filepath.Join(root, "broken.json"), `[`)
	return root
}

type report struct {
	Kind, Number, Result, Cwd string
	JSON                      bool
}

func reports(root string) []report {
	proj := filepath.Join(root, "projects", "proj")
	var out []report
	for _, j := range []bool{false, true} {
		out = append(out,
			report{"issue", "152", "host.json", root, j}, report{"issue", "152", "unchecked.json", root, j},
			report{"mr", "5", "host.json", root, j}, report{"mr", "5", "", root, j}, report{"issue", "7", "broken.json", root, j},
			report{"issue", "152", "", proj, j})
	}
	return append(out, report{"pr", "1", "", root, false}, report{"issue", "012", "", root, false},
		report{"issue", "1234567890", "", root, false}, report{"issue", "1", "", "/elsewhere", false})
}

var contexts = []string{
	"#152 を直して", "issue 152 と !5 と #3", "https://github.com/acme/w/issues/152 を見て",
	"https://gitlab.com/grp/proj/-/issues/4 を直す", "https://github.com/other/x/issues/2", "#152 をもう一度", "何もない",
}

var bounds = []string{"i0001", "I0001", "feature-9-new", "feature-7-a", "feature/152-login", "nothing", "main"}

var nextIDs = [][]string{{}, {"feature", "login"}, {"bad", "x"}, {"feature", "x-01"}, {"feature"}, {"feature", "a b"},
	{"feature", strings.Repeat("長", 60)}}

func TestMatchesPython(t *testing.T) {
	goRoot := fixture(t)
	texts := slices.Concat(refMisses, contexts)
	for text := range refHits {
		texts = append(texts, text)
	}
	slices.Sort(texts)
	want := golden.Load[map[string]any](t, "branch")
	root := goRoot
	conf := testkit.Settings(t, root)
	rel := func(s string) string { return testkit.Rel(root, s) }
	got := map[string]any{}
	refs := []any{}
	for _, text := range texts {
		found := []any{}
		for _, r := range PromptRefs(text) {
			found = append(found, []any{string(r.kind), r.number, r.repo})
		}
		refs = append(refs, found)
	}
	got["refs"] = refs
	reps := []any{}
	for _, r := range reports(root) {
		var o, e bytes.Buffer
		path := ""
		if r.Result != "" {
			path = filepath.Join(root, r.Result)
		}
		code := Report(&o, &e, conf, root, r.Cwd, r.Kind, r.Number, path, r.JSON)
		reps = append(reps, []any{code, rel(o.String()), rel(e.String())})
	}
	got["reports"] = reps
	ctx := []any{}
	for _, text := range contexts {
		var e bytes.Buffer
		ctx = append(ctx, []any{rel(PromptContext(&e, conf, root, text, root, "s1")), rel(e.String())})
		ctx = append(ctx, []any{rel(PromptContext(&e, conf, root, text, root, "")), rel(e.String())})
	}
	got["contexts"] = ctx
	answers := []any{}
	for _, b := range bounds {
		a, why := BaseBound(conf, root, root, b)
		answers = append(answers, []any{string(a), why})
	}
	a, why := BaseBound(conf, root, filepath.Join(root, "projects", "proj"), "feature-152-p")
	answers = append(answers, []any{string(a), why})
	a, why = BaseBound(conf, root, "/elsewhere", "main")
	got["bounds"] = append(answers, []any{string(a), why})
	names, problems := SerialNames(conf, root, nil)
	slices.Sort(names)
	if problems == nil {
		problems = []string{}
	}
	got["serial"] = []any{names, problems}
	ids := []any{}
	for _, args := range nextIDs {
		var o, e bytes.Buffer
		ids = append(ids, []any{RunNextID(&o, &e, conf, root, args), o.String(), e.String()})
	}
	got["next"] = ids
	data, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	var norm map[string]any
	if err := json.Unmarshal(data, &norm); err != nil {
		t.Fatal(err)
	}
	for key, w := range want {
		g, _ := norm[key].([]any)
		ws, _ := w.([]any)
		if len(g) != len(ws) {
			t.Errorf("%s: %d 件, Python は %d 件", key, len(g), len(ws))
			continue
		}
		for i := range ws {
			if !reflect.DeepEqual(g[i], ws[i]) {
				what := ""
				if key == "refs" {
					what = texts[i]
				}
				t.Errorf("%s[%d] %q が Python と違う\nGo:     %v\nPython: %v", key, i, what, g[i], ws[i])
			}
		}
	}
}
