package turngate

import (
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/records/prune"
	"ccnavi/internal/records/turn"
	"ccnavi/internal/tickets/testkit"
)

// 宣言の表を直接渡して確かめる。組み込みの組のほかに、integrate と関係の無い架空の組を使い、部品が特定の組に
// 依存していないことを見る。

// copyGate は「copy-in <名前>」のあと、発言をはさむまで「copy-out <名前>」を打てない。
var copyGate = &Gate{
	id:         "copy-out",
	first:      cond{regex: `\bcopy-in\s+(?P<key>\S+)`},
	second:     cond{regex: `\bcopy-out\s+(?P<key>\S+)`},
	firstName:  "copy-in",
	secondName: "copy-out",
	message:    "DENY_NEEDS_USER_TURN__COPY",
	keyName:    "<名前>",
}

// deployGate はキーを持たない組（glob と but）。「deploy」のあと、発言をはさむまで「promote」を打てない。
var deployGate = &Gate{
	id:         "promote",
	first:      cond{glob: "*deploy*", but: []string{`deploy\s+--dry-run`}},
	second:     cond{glob: "*promote*"},
	firstName:  "deploy",
	secondName: "promote",
	message:    "DENY_NEEDS_USER_TURN__PROMOTE",
}

var gates = []*Gate{copyGate, deployGate}

const session = "s1"

type fixture struct {
	t     *testing.T
	state string
	clock time.Time
}

func newFixture(t *testing.T) *fixture {
	return &fixture{t: t, state: t.TempDir(), clock: time.Unix(1000, 0)}
}

func (f *fixture) tick() time.Time {
	f.clock = f.clock.Add(10 * time.Second)
	return f.clock
}

// ok は判定の error を落として結論だけを返す（組み立てられる宣言しか渡さない）。
func ok(t *testing.T) func(*Stop, error) *Stop {
	return func(s *Stop, err error) *Stop {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
		return s
	}
}

// a は A として打った（実行前に記録する）。止められたら Stop。
func (f *fixture) a(command string, opts ...string) *Stop {
	sess, call := session, "c1"
	if len(opts) > 0 {
		sess = opts[0]
	}
	if len(opts) > 1 {
		call = opts[1]
	}
	if s := ok(f.t)(Check(gates, []string{command}, f.state, sess, false)); s != nil {
		return s
	}
	return ok(f.t)(Note(gates, []string{command}, f.state, sess, call, f.tick()))
}

func (f *fixture) b(command string, sess ...string) *Stop {
	s := session
	if len(sess) > 0 {
		s = sess[0]
	}
	return ok(f.t)(Check(gates, []string{command}, f.state, s, false))
}

func (f *fixture) speak(sess ...string) {
	s := session
	if len(sess) > 0 {
		s = sess[0]
	}
	turn.NotePrompt(f.state, s, f.tick())
}

func (f *fixture) files() []string {
	names, _ := os.ReadDir(f.state)
	var out []string
	for _, n := range names {
		if strings.HasPrefix(n.Name(), prefix) {
			out = append(out, n.Name())
		}
	}
	return out
}

func TestFlow(t *testing.T) {
	t.Run("同じ発言の中で A のあとの B は止まり、発言を 1 回はさむと通る", func(t *testing.T) {
		f := newFixture(t)
		f.speak()
		if f.a("copy-in foo") != nil {
			t.Fatal("A が止まった")
		}
		got := f.b("copy-out foo")
		if got == nil || got.reason != noUserTurn || got.Gate.id != "copy-out" || got.key != "foo" {
			t.Fatalf("got %+v", got)
		}
		// 発言が無いまま何度打っても止まる。
		if f.b("copy-out foo") == nil {
			t.Fatal("2 回目が通った")
		}
		f.speak()
		if f.b("copy-out foo") != nil {
			t.Fatal("発言のあとに止まった")
		}
		// 通ったら記録が消え、次の A からまた数える。
		if len(f.files()) != 0 || f.b("copy-out foo") != nil {
			t.Fatal("記録が残った")
		}
		f.a("copy-in foo", session, "c2")
		if f.b("copy-out foo") == nil {
			t.Fatal("次の A のあとが通った")
		}
	})
	t.Run("A の前の発言は数えない", func(t *testing.T) {
		f := newFixture(t)
		f.speak()
		f.a("copy-in foo")
		if f.b("copy-out foo") == nil {
			t.Fatal("通った")
		}
	})
	t.Run("キーが違う組は対象外で、キーは大文字小文字を区別しない", func(t *testing.T) {
		f := newFixture(t)
		f.a("copy-in Foo")
		if f.b("copy-out bar") != nil || f.b("copy-out FOO") == nil {
			t.Fatal("キーの比べが違う")
		}
	})
	t.Run("A の記録が無ければ B は対象外", func(t *testing.T) {
		if newFixture(t).b("copy-out foo") != nil {
			t.Fatal("止まった")
		}
	})
	t.Run("B が通ってもほかの宣言の記録は消さない", func(t *testing.T) {
		f := newFixture(t)
		f.a("copy-in foo")
		f.a("deploy", session, "c2")
		f.speak()
		if f.b("copy-out foo") != nil {
			t.Fatal("止まった")
		}
		if files := f.files(); len(files) != 1 || !strings.Contains(files[0], "promote") {
			t.Fatalf("残った記録 %v", files)
		}
	})
	t.Run("キーの無い宣言（glob と but）も同じ部品で止まる", func(t *testing.T) {
		f := newFixture(t)
		f.a("run deploy --prod")
		got := f.b("run promote")
		if got == nil || got.Gate.id != "promote" || got.key != "" {
			t.Fatalf("got %+v", got)
		}
		f.speak()
		if f.b("run promote") != nil {
			t.Fatal("発言のあとに止まった")
		}
	})
	t.Run("but に当たった A は数えず、but は同じコマンドの中だけを見る", func(t *testing.T) {
		f := newFixture(t)
		f.a("run deploy --dry-run")
		if f.b("run promote") != nil {
			t.Fatal("but の A を数えた")
		}
		f.a("deploy --dry-run\x00deploy --prod", session, "c2")
		if f.b("promote") == nil {
			t.Fatal("別のコマンドの but で外れた")
		}
	})
	t.Run("別のセッションの記録は見ない（名前が前方一致しても）", func(t *testing.T) {
		f := newFixture(t)
		f.a("copy-in foo", "s1")
		f.a("copy-in foo", "s1-b")
		if f.b("copy-out foo", "s2") != nil {
			t.Fatal("s2 で止まった")
		}
		f.speak("s2")
		if f.b("copy-out foo", "s1") == nil {
			t.Fatal("s2 の発言で s1 が通った")
		}
		f.speak("s1")
		if f.b("copy-out foo", "s1") != nil {
			t.Fatal("s1-b の記録で s1 が止まった")
		}
	})
	t.Run("同じコマンドの中の A と B は止まる", func(t *testing.T) {
		f := newFixture(t)
		if got := f.b("copy-in foo && copy-out foo"); got == nil || got.reason != sameCommand {
			t.Fatalf("got %+v", got)
		}
		if f.b("copy-in foo && copy-out bar") != nil {
			t.Fatal("キーが違うのに止まった")
		}
		if got := f.b("deploy; promote"); got == nil || got.reason != sameCommand {
			t.Fatalf("キーなし got %+v", got)
		}
	})
	t.Run("実行役が中で実行するコマンドも見る", func(t *testing.T) {
		f := newFixture(t)
		ok(t)(Note(gates, []string{"sh -c x", "copy-in foo"}, f.state, session, "c1", f.tick()))
		if ok(t)(Check(gates, []string{"bash", "copy-out foo"}, f.state, session, false)) == nil {
			t.Fatal("通った")
		}
	})
	t.Run("解けないキーはどのキーとも一致する", func(t *testing.T) {
		f := newFixture(t)
		f.a("copy-in foo")
		if f.b("copy-out $name") == nil || f.b("copy-out 'foo'") == nil {
			t.Fatal("解けないキーが外れた")
		}
		f.speak()
		if f.b("copy-out $name") != nil {
			t.Fatal("発言のあとに止まった")
		}
		g := newFixture(t)
		g.a("copy-in $name")
		if g.b("copy-out whatever") == nil {
			t.Fatal("A の解けないキーが外れた")
		}
	})
	t.Run("呼び出しの記録を消すとその A は数えず、前の A は残る", func(t *testing.T) {
		f := newFixture(t)
		f.a("copy-in foo", session, "c1")
		ForgetCall(f.state, session, "c1")
		if f.b("copy-out foo") != nil {
			t.Fatal("消した A を数えた")
		}
		f.a("copy-in foo", session, "c1")
		f.speak()
		f.a("copy-in foo", session, "c2")
		ForgetCall(f.state, session, "c2")
		if f.b("copy-out foo") != nil {
			t.Fatal("前の A のあとの発言が数えられない")
		}
		// 識別子が空なら何も消さない。
		g := newFixture(t)
		g.a("copy-in foo", session, "")
		ForgetCall(g.state, session, "")
		if g.b("copy-out foo") == nil {
			t.Fatal("空の識別子で消えた")
		}
	})
	t.Run("Clear はセッションの記録を全部消し、ほかのセッションは残す", func(t *testing.T) {
		f := newFixture(t)
		f.a("copy-in foo", "s1")
		f.a("copy-in foo", "s2")
		Clear(f.state, "s1")
		if f.b("copy-out foo", "s1") != nil || f.b("copy-out foo", "s2") == nil {
			t.Fatal("Clear の範囲が違う")
		}
	})
	t.Run("置き場かセッションが空なら働かない", func(t *testing.T) {
		f := newFixture(t)
		for _, c := range [][2]string{{"", session}, {f.state, ""}} {
			if ok(t)(Note(gates, []string{"copy-in foo"}, c[0], c[1], "c1", time.Time{})) != nil ||
				ok(t)(Check(gates, []string{"copy-out foo"}, c[0], c[1], false)) != nil {
				t.Fatalf("%q %q で働いた", c[0], c[1])
			}
		}
	})
}

func TestBrokenRecords(t *testing.T) {
	t.Run("発言の記録が無い・壊れていれば止まり、次の発言で通る", func(t *testing.T) {
		for _, broken := range []bool{false, true} {
			f := newFixture(t)
			f.a("copy-in foo")
			if broken {
				testkit.Put(t, filepath.Join(f.state, turn.UserPromptPrefix+session+".json"), "{not json")
			}
			if got := f.b("copy-out foo"); got == nil || got.reason != noUserTurn {
				t.Fatalf("broken=%v got %+v", broken, got)
			}
			f.speak()
			if f.b("copy-out foo") != nil {
				t.Fatalf("broken=%v 発言のあとに止まった", broken)
			}
		}
	})
	t.Run("壊れた記録・形の違う記録はどの B も止め、次の発言で消える", func(t *testing.T) {
		for _, text := range []string{"{not json", `{"session": "s1", "gate": "copy-out", "key": "foo", "at": "x"}`} {
			f := newFixture(t)
			f.a("copy-in foo")
			path := filepath.Join(f.state, f.files()[0])
			testkit.Put(t, path, text)
			f.speak()
			got := f.b("copy-out bar")
			if got == nil || got.reason != Broken || got.detail != path {
				t.Fatalf("got %+v", got)
			}
			if got := f.b("promote"); got == nil || got.reason != Broken {
				t.Fatalf("promote got %+v", got)
			}
			f.speak()
			DropBroken(f.state, session)
			if len(f.files()) != 0 || f.b("copy-out bar") != nil {
				t.Fatal("壊れた記録が消えない")
			}
		}
	})
	t.Run("発言で消すのは壊れた記録だけ", func(t *testing.T) {
		f := newFixture(t)
		f.a("copy-in foo")
		DropBroken(f.state, session)
		if len(f.files()) != 1 || f.b("copy-out foo").reason != noUserTurn {
			t.Fatal("壊れていない記録が消えた")
		}
	})
	t.Run("置き場を読めなければ B を止め、まだ無ければ対象外", func(t *testing.T) {
		f := newFixture(t)
		blocker := testkit.Put(t, filepath.Join(f.state, "file"), "x")
		if got := ok(t)(Check(gates, []string{"copy-out foo"}, blocker, session, false)); got == nil || got.reason != unreadable {
			t.Fatalf("got %+v", got)
		}
		if ok(t)(Check(gates, []string{"copy-out foo"}, filepath.Join(f.state, "nowhere"), session, false)) != nil {
			t.Fatal("無い置き場で止まった")
		}
	})
	t.Run("記録を書けなければ A を止める。A に当たらなければ止めない", func(t *testing.T) {
		f := newFixture(t)
		blocker := testkit.Put(t, filepath.Join(f.state, "file"), "x")
		if got := ok(t)(Note(gates, []string{"copy-in foo"}, blocker, session, "c1", time.Time{})); got == nil || got.reason != unwritable {
			t.Fatalf("got %+v", got)
		}
		if ok(t)(Note(gates, []string{"ls"}, blocker, session, "c1", time.Time{})) != nil {
			t.Fatal("A でないのに止まった")
		}
	})
}

const extraMessages = `version: 1
messages:
  DENY_NEEDS_USER_TURN__COPY:
    kind: deny
    values: [first_name, second_name, where, key]
    what:
      same_command: "{first_name} と {second_name} をまとめて打とうとしました{where}。"
      no_user_turn: "{first_name} を打ったあと、発言が無いまま {second_name} を打とうとしました{where}。"
    why: コピーの中身をユーザが見るためです。
    next: ユーザに見せて返事を待ち、あとで {second_name} {key} を打ち直す
    no_bypass: 分けても同じゲートに当たります。
`

func messageRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	testkit.CopyMessages(t, root)
	testkit.Put(t, filepath.Join(root, ".ccnavi", "messages", "test-copy.yml"), extraMessages)
	return root
}

func TestMessage(t *testing.T) {
	root := messageRoot(t)
	f := newFixture(t)
	f.a("copy-in foo")
	lines := strings.Split(Message(f.b("copy-out foo"), root), "\n")
	if len(lines) != 4 || !strings.Contains(lines[0], "copy-in を打ったあと") || !strings.Contains(lines[0], "宣言 copy-out") ||
		!strings.Contains(lines[0], "キー foo") || !strings.HasPrefix(lines[1], "なぜ: ") ||
		!strings.HasPrefix(lines[2], "次: ") || !strings.Contains(lines[2], "copy-out foo を打ち直す") ||
		!strings.HasPrefix(lines[3], "迂回しない: ") {
		t.Fatalf("段が違う: %q", lines)
	}
	if got := Message(f.b("copy-out $name"), root); !strings.Contains(got, "copy-out <名前> を打ち直す") {
		t.Errorf("解けないキーの仮の値: %s", got)
	}
	for _, stop := range []*Stop{
		f.b("copy-out foo"), f.b("copy-in foo && copy-out foo"),
		{copyGate, unreadable, "foo", "permission denied"},
		{copyGate, unwritable, "foo", "disk full"},
		{copyGate, Broken, "foo", "/state/turngate-s1-x.json"},
	} {
		text := Message(stop, root)
		next := 0
		for _, line := range strings.Split(text, "\n") {
			if strings.HasPrefix(line, "次: ") {
				next++
			}
		}
		if next != 1 || strings.Contains(text, "{") || strings.Contains(strings.ToLower(text), "adr") {
			t.Errorf("%s: %s", stop.reason, text)
		}
	}
	unreadable := Message(&Stop{copyGate, unreadable, "foo", "permission denied"}, root)
	if !strings.Contains(unreadable, "permission denied") || !strings.Contains(unreadable, "次: ユーザに") {
		t.Errorf("読めないとき: %s", unreadable)
	}
	broken := Message(&Stop{copyGate, Broken, "foo", "/state/turngate-s1-x.json"}, root)
	if !strings.Contains(broken, "/state/turngate-s1-x.json") || !strings.Contains(broken, "次: ここでターンを終え") ||
		!strings.Contains(broken, "copy-out を打ち直す") {
		t.Errorf("壊れた記録: %s", broken)
	}
}

func TestValidate(t *testing.T) {
	for _, g := range append(slices.Clone(builtin), gates...) {
		if err := validate(g); err != nil {
			t.Errorf("%s: %v", g.id, err)
		}
	}
	bad := func(id string, first, second cond) *Gate {
		return &Gate{id: id, first: first, second: second, firstName: "a", secondName: "b"}
	}
	for name, g := range map[string]*Gate{
		"キーが片方だけ":      bad("bad", cond{regex: `a\s+(?P<key>\S+)`}, cond{regex: "b"}),
		"regex と glob": bad("bad", cond{regex: "a", glob: "*a*"}, cond{regex: "b"}),
		"条件が空":         bad("bad", cond{}, cond{regex: "b"}),
		"先読み":          bad("bad", cond{regex: `a(?=b)`}, cond{regex: "b"}),
		"否定の先読み":       bad("bad", cond{regex: `a(?!b)`}, cond{regex: "b"}),
		"閉じない括弧":       bad("bad", cond{regex: "a("}, cond{regex: "b"}),
		"後方参照":         bad("bad", cond{regex: `(a)\1`}, cond{regex: "b"}),
		"空の id":        bad("", cond{regex: "a"}, cond{regex: "b"}),
		"- で始まる id":    bad("-a", cond{regex: "a"}, cond{regex: "b"}),
		"空白の id":       bad("a b", cond{regex: "a"}, cond{regex: "b"}),
		"/ の id":       bad("a/b", cond{regex: "a"}, cond{regex: "b"}),
	} {
		if err := validate(g); !errors.Is(err, errBadGate) {
			t.Errorf("%s: %v", name, err)
		}
	}
	// 組めない宣言は判定にも error を返す（止めずに通すことはしない）。
	broken := bad("bad", cond{regex: "a("}, cond{regex: "a("})
	if _, err := Check([]*Gate{broken}, []string{"a"}, t.TempDir(), session, false); !errors.Is(err, errBadGate) {
		t.Errorf("Check: %v", err)
	}
}

func TestConditionAndNames(t *testing.T) {
	got, _ := hits(cond{regex: `copy-in\s+(?P<key>\S+)`}, "x", []string{"COPY-IN Foo"})
	if !slices.Equal(got, []hit{{"foo", "Foo"}}) {
		t.Errorf("hits = %v", got)
	}
	if RuleID(copyGate) != "builtin:turn-gate:copy-out" || Code != "DENY_NEEDS_USER_TURN" {
		t.Error("記録の名前が違う")
	}
}

func keys(t *testing.T, c cond, command string) []hit {
	t.Helper()
	got, err := hits(c, "x", []string{command})
	if err != nil {
		t.Fatal(err)
	}
	return got
}

func TestIntegrateForms(t *testing.T) {
	const git = "sh /w/.ccnavi/scripts/ccnavi-git.sh "
	foo := []hit{{"foo", "foo"}}
	for tail, want := range map[string][]hit{
		"integrate foo":            foo,
		"integrate docs-69-readme": {{"docs-69-readme", "docs-69-readme"}},
		"integrate --abort foo":    nil,
		"status":                   nil,
		"commit -m integrate foo":  nil,
		"log integrate-done foo":   nil,
	} {
		if got := keys(t, integrateGate.first, git+tail); !slices.Equal(got, want) {
			t.Errorf("first %q = %v, want %v", tail, got, want)
		}
	}
	msg := "a\x01b\x01\x01c"
	for tail, want := range map[string][]hit{
		"integrate-done foo":                       foo,
		"integrate-done foo -m " + msg:             foo,
		"integrate-done -m " + msg + " foo":        foo,
		"integrate-done --message " + msg + " foo": foo,
		"integrate-done --message=" + msg + " foo": foo,
		"integrate-done -m x -m y foo":             foo,
		// メッセージの語は名前と取らない。
		"integrate-done bar -m foo": {{"bar", "bar"}},
		"integrate-done -m foo bar": {{"bar", "bar"}},
		"integrate-done":            nil,
		"integrate-done -m x":       nil,
		"status":                    nil,
		"commit -m integrate foo":   nil,
		"log integrate-done foo":    nil,
	} {
		if got := keys(t, integrateGate.second, git+tail); !slices.Equal(got, want) {
			t.Errorf("second %q = %v, want %v", tail, got, want)
		}
	}
	if got := keys(t, integrateGate.second, "sh other.sh integrate-done foo"); got != nil {
		t.Errorf("別の sh: %v", got)
	}
	// 引用の中の文字列（shell が空白を目印に置き換えた echo の引数）は当たらない。
	if got := keys(t, integrateGate.first, "echo ccnavi-git.sh\x01integrate\x01foo"); got != nil {
		t.Errorf("引用: %v", got)
	}
	f := newFixture(t)
	ok(t)(Note(builtin, []string{git + "integrate Foo"}, f.state, session, "c1", time.Unix(1, 0)))
	got := ok(t)(Check(builtin, []string{git + "integrate-done foo -m x"}, f.state, session, false))
	if got == nil || got.Gate != integrateGate || got.key != "foo" {
		t.Errorf("got %+v", got)
	}
}

func TestReviewConfirmForms(t *testing.T) {
	const review = "sh .ccnavi/scripts/ccnavi-review.sh "
	const rr = "review-request --body-file req.md"
	for _, tail := range []string{rr + " --phase 2 --check", rr + " --check --phase 2"} {
		if got := keys(t, reviewConfirmGate.first, review+tail); got != nil {
			t.Errorf("first %q = %v", tail, got)
		}
	}
	for _, tail := range []string{"confirm --phase 2 --check", "confirm --check --phase 2"} {
		if got := keys(t, reviewConfirmGate.second, review+tail); got != nil {
			t.Errorf("second %q = %v", tail, got)
		}
	}
	for _, sub := range []string{"decide", "ready", "comment", "fetch", "origin", "merged", "mr-state"} {
		if keys(t, reviewConfirmGate.first, review+sub+" --phase 2") != nil || keys(t, reviewConfirmGate.second, review+sub+" --phase 2") != nil {
			t.Errorf("%s が当たった", sub)
		}
	}
	if keys(t, reviewConfirmGate.second, "sh other.sh confirm --phase 1") != nil {
		t.Error("別のスクリプトが当たった")
	}
	f := newFixture(t)
	ok(t)(Note(builtin, []string{review + rr + " --phase 1"}, f.state, session, "c1", time.Time{}))
	for _, tail := range []string{"confirm --phase 1", "confirm --phase=2", "confirm --phase 01", "confirm"} {
		if got := ok(t)(Check(builtin, []string{review + tail}, f.state, session, false)); got == nil || got.reason != noUserTurn {
			t.Errorf("%q got %+v", tail, got)
		}
	}
	both := review + rr + " --phase 1 && " + review + "confirm --phase 1"
	if got := ok(t)(Check(builtin, []string{both}, newFixture(t).state, session, false)); got == nil || got.reason != sameCommand {
		t.Errorf("同じコマンド got %+v", got)
	}
	root := t.TempDir()
	testkit.CopyMessages(t, root)
	g := newFixture(t)
	ok(t)(Note(builtin, []string{review + rr + " --phase 3"}, g.state, session, "c1", time.Time{}))
	stop := ok(t)(Check(builtin, []string{review + "confirm --phase 3"}, g.state, session, false))
	lines := strings.Split(Message(stop, root), "\n")
	if len(lines) != 4 || !strings.Contains(lines[0], "ccnavi-review.sh review-request を打ったあと") ||
		!strings.Contains(lines[2], "confirm --phase <N>") || !strings.Contains(lines[2], "ユーザのレビュー") {
		t.Errorf("文面: %q", lines)
	}
}

func TestAskIfAttended(t *testing.T) {
	const git = "sh .ccnavi/scripts/ccnavi-git.sh "
	waited, asked := SplitByAttendance(builtin, true)
	if !slices.Equal(waited, []*Gate{reviewConfirmGate}) || !slices.Equal(asked, []*Gate{integrateGate}) {
		t.Errorf("付き添いあり: %v %v", waited, asked)
	}
	if waited, asked := SplitByAttendance(builtin, false); !slices.Equal(waited, builtin) || asked != nil {
		t.Errorf("付き添いなし: %v %v", waited, asked)
	}
	if waited, asked := SplitByAttendance(gates, true); !slices.Equal(waited, gates) || asked != nil {
		t.Errorf("立てていない宣言: %v %v", waited, asked)
	}
	got := ok(t)(Asks(builtin, []string{git + "integrate-done foo -m x"}))
	if got == nil || got.Gate != integrateGate || got.key != "foo" || got.reason != ask {
		t.Fatalf("Asks got %+v", got)
	}
	if s := ok(t)(Asks(asked, []string{"sh .ccnavi/scripts/ccnavi-review.sh confirm --phase 1"})); s != nil {
		t.Error("レビューの確定を確認に出した")
	}
	for _, c := range []string{git + "integrate foo", "ls"} {
		if ok(t)(Asks(builtin, []string{c})) != nil {
			t.Errorf("%q が当たった", c)
		}
	}
	f := newFixture(t)
	both := []string{git + "integrate foo && " + git + "integrate-done foo -m x"}
	if s := ok(t)(Check(builtin, both, f.state, session, true)); s == nil || s.reason != sameCommand {
		t.Errorf("同じコマンド got %+v", s)
	}
	ok(t)(Note(builtin, []string{git + "integrate foo"}, f.state, session, "c1", time.Unix(1, 0)))
	if s := ok(t)(Check(builtin, []string{git + "integrate-done foo -m x"}, f.state, session, true)); s != nil {
		t.Errorf("記録を見た %+v", s)
	}
	root := t.TempDir()
	testkit.CopyMessages(t, root)
	text := AskMessage(got, root)
	for _, want := range []string{"ユーザの確認が要ります", "integrate-user-turn", "foo"} {
		if !strings.Contains(text, want) {
			t.Errorf("確認の文に %q が無い: %s", want, text)
		}
	}
}

// records/prune は持ち主（turngate）を読めないので、記録の名前の頭を写して持つ。写しが食い違うと、古い記録が
// 残り続ける。持ち主の名前で作った記録を、prune が古いセッションの記録として消すことで確かめる。
func TestPruneRemovesOldRecords(t *testing.T) {
	root := t.TempDir()
	state := filepath.Join(root, "state")
	f := &fixture{t: t, state: state, clock: time.Now()}
	f.a("copy-in foo", "old-session")
	names := f.files()
	if len(names) != 1 || names[0] != entryName("old-session", "copy-out", "foo", "c1") {
		t.Fatalf("記録の名前 %v", names)
	}
	old := time.Now().Add(-90 * 24 * time.Hour)
	if err := os.Chtimes(filepath.Join(state, names[0]), old, old); err != nil {
		t.Fatal(err)
	}
	prune.Run(root, "", state, "current", prune.Options{})
	if left := f.files(); len(left) != 0 {
		t.Errorf("古い記録が残った: %v", left)
	}
}
