package notice

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/wiki"
	"ccnavi/internal/tickets/testkit"
)

func TestDecideLearning(t *testing.T) {
	row := func(name string, count int) wiki.Row { return wiki.Row{Name: name, Target: "docs", Count: count} }
	seen := func(kv ...any) *pyjson.Object { return pyjson.NewObject(kv...) }
	five := []wiki.Row{row("a", 1), row("b", 1), row("c", 1), row("d", 1), row("e", 1)}
	for _, c := range []struct {
		name string
		rows []wiki.Row
		seen *pyjson.Object
		show bool
		keep []string
	}{
		{"候補が 5 件で新しい", five, seen(), true, []string{"a", "b", "c", "d", "e"}},
		{"候補が 4 件", five[:4], seen(), false, nil},
		{"閾値以上が 1 件", []wiki.Row{row("hot", 3)}, seen(), true, []string{"hot"}},
		{"変化が無い", five, seen("a", false, "b", false, "c", false, "d", false, "e", false), false, []string{"a", "b", "c", "d", "e"}},
		{"新しく閾値に届いた", []wiki.Row{row("a", 3)}, seen("a", false), true, []string{"a"}},
		{"届いたまま", []wiki.Row{row("a", 3)}, seen("a", true), false, []string{"a"}},
		{"候補でなくなった名前は外す", five[:1], seen("z", true, "a", false), false, []string{"a"}},
	} {
		show, record := decideLearning(c.rows, c.seen, wiki.DefaultSources, 5)
		if show != c.show || strings.Join(record.Keys(), ",") != strings.Join(c.keep, ",") {
			t.Errorf("%s: %v %v", c.name, show, record.Keys())
		}
	}
}

const statePathOracle = `
import json, sys
from ccnavi.infra import settings
from ccnavi.tickets.notice import learning_notice
c = json.load(sys.stdin)
conf, _ = settings.load(c["root"])
json.dump([learning_notice.state_path(conf, n).replace(c["root"], "<root>") for n in c["names"]], sys.stdout, ensure_ascii=False)
`

func TestLearningStatePath(t *testing.T) {
	root := t.TempDir()
	names := []string{"", "学習", "調査", "a b", "a_b", "a:b", "_self", "Foo", "foo", strings.Repeat("長い名前", 20)}
	want := testkit.Answers[[]any](t, "statepath", statePathOracle, map[string]any{"root": root, "names": names})
	conf := testkit.Settings(t, root)
	seen := map[string]bool{}
	var got []any
	for _, n := range names {
		p := learningStatePath(conf, n)
		got = append(got, testkit.Rel(root, p))
		seen[p] = true
	}
	testkit.SameJSON(t, map[string]any{"paths": got}, map[string]any{"paths": want})
	if len(seen) != len(names)-1 || learningStatePath(conf, "Foo") != learningStatePath(conf, "foo") {
		t.Errorf("名前は大文字小文字だけ違うものを除いて衝突しない: %v", got)
	}
}

// learningRepo は origin 付きのリポジトリ（統合先 main）を作る。
func learningRepo(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(path, 0o755); err != nil {
		t.Fatal(err)
	}
	testkit.Git(t, path, "init", "-q", "-b", "main")
	testkit.Put(t, filepath.Join(path, "README"), "x\n")
	testkit.Commit(t, path, "init")
	origin := path + ".origin.git"
	testkit.Git(t, path, "init", "-q", "--bare", origin)
	testkit.Git(t, path, "remote", "add", "origin", origin)
	testkit.Git(t, path, "push", "-q", "origin", "main")
	testkit.Git(t, path, "remote", "set-head", "origin", "main")
}

// publish はパターンを書いて統合先へ送る。sources は観測元の数。
func publish(t *testing.T, path string, sources map[string]int, target string) {
	t.Helper()
	for name, count := range sources {
		var ids []string
		for i := range count {
			ids = append(ids, fmt.Sprintf("s%d", i))
		}
		testkit.Put(t, filepath.Join(path, "docs", "wiki", "patterns", name+".md"), "---\ntype: learning-wiki\ntarget: "+target+
			"\nsources: ["+strings.Join(ids, ", ")+"]\ntitle: t\ndescription: d\ntags: [a]\nkeywords: [b]\n---\n\n本文\n")
	}
	testkit.Commit(t, path, "wiki")
	testkit.Git(t, path, "push", "-q", "origin", "main")
	testkit.Git(t, path, "fetch", "-q", "origin")
}

const learningOracle = `
import json, sys
from ccnavi.infra import settings
from ccnavi.tickets.notice import learning_notice
c = json.load(sys.stdin)
conf, _ = settings.load(c["root"])
text = learning_notice.notice(conf, c["root"])
states = []
for name in ("", "web"):
    try:
        with open(learning_notice.state_path(conf, name), encoding="utf-8") as f:
            states.append(f.read())
    except OSError:
        states.append(None)
json.dump({"text": text, "states": states}, sys.stdout, ensure_ascii=False)
`

// 同じ形のワークスペースを 2 つ作り、同じ順に変えながら、通知の文と通知済みの記録のバイト列が Python と同じことを
// 見る。リポジトリごとに別に判定し、決められないリポジトリは見送って記録を変えない。
func TestLearningMatchesPython(t *testing.T) {
	t.Setenv(env.Learning, "enable")
	t.Setenv(env.LearningSources, "")
	t.Setenv(env.LearningNoticeCount, "")
	build := func() string {
		root, err := filepath.EvalSymlinks(t.TempDir())
		if err != nil {
			t.Fatal(err)
		}
		learningRepo(t, root)
		learningRepo(t, filepath.Join(root, "projects", "web"))
		return root
	}
	pyRoot, goRoot := build(), build()
	both := func(change func(root string)) {
		change(pyRoot)
		change(goRoot)
	}
	five := map[string]int{"p0": 1, "p1": 1, "p2": 1, "p3": 1, "p4": 1}
	steps := []struct {
		name   string
		change func(root string)
	}{
		{"first", func(root string) {
			publish(t, root, five, "docs")
			publish(t, filepath.Join(root, "projects", "web"), map[string]int{"q0": 1, "q1": 1, "q2": 1, "q3": 1, "q4": 1}, "skill")
		}},
		{"again", func(string) {}},
		{"reached", func(root string) { publish(t, root, map[string]int{"p0": 3}, "docs") }},
		{"broken", func(root string) {
			testkit.Put(t, learningStatePath(testkit.Settings(t, root), ""), "{broken")
		}},
		{"undecidable", func(root string) {
			testkit.Git(t, filepath.Join(root, "projects", "web"), "remote", "remove", "origin")
		}},
	}
	for _, step := range steps {
		both(step.change)
		want := testkit.Answers[map[string]any](t, "learning-"+step.name, learningOracle, map[string]any{"root": pyRoot})
		conf := testkit.Settings(t, goRoot)
		text := Learning(conf, goRoot, time.Time{})
		var states []any
		for _, name := range []string{"", "web"} {
			data, err := os.ReadFile(learningStatePath(conf, name))
			if err != nil {
				states = append(states, nil)
				continue
			}
			states = append(states, string(data))
		}
		testkit.SameJSON(t, map[string]any{"text": text, "states": states}, want)
	}
	t.Setenv(env.Learning, "disable")
	if text := Learning(testkit.Settings(t, goRoot), goRoot, time.Time{}); text != "" {
		t.Errorf("学びの蓄積を止めていれば黙る: %s", text)
	}
}
