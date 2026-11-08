package core

import (
	"bytes"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/model/host"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/agree"

	"ccnavi/internal/tickets/testkit"
)

// staged は fn が溜めた書き込みの並び。
func staged(t *testing.T, fn func(s *fsio.Stage)) *fsio.Stage {
	t.Helper()
	s, done, err := fsio.Staging()
	if err != nil {
		t.Fatal(err)
	}
	defer done()
	fn(s)
	return s
}

func withPolicy(change func(*fsio.Policy), write func()) {
	restore := fsio.SetPolicy(change)
	defer restore()
	write()
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

// 書き手の失敗の仕方（失敗の枝ごとに 1 つずつ）。溜める段では書き込みが失敗しないので、書くときに当てる。
// 行き先のディレクトリをファイルで塞いで書けなくする。
func TestWriteFailureModes(t *testing.T) {
	dir := t.TempDir()
	blocked := filepath.Join(dir, "blocked")
	testkit.Put(t, blocked, "x")
	fail := filepath.Join(blocked, "x.md")
	first := filepath.Join(dir, "first.md")
	undone := filepath.Join(dir, "undone.md")
	after := filepath.Join(dir, "after.md")
	s := staged(t, func(s *fsio.Stage) {
		// FailLine は行を出し、同じ組の残り（行と書き込み）を飛ばす。
		line := s.NewGroup()
		withPolicy(func(p *fsio.Policy) { p.OnFail, p.Group, p.Message = fsio.FailLine, line, "行の組 ({reason})" }, func() {
			fsio.WriteFile(fail, []byte("a"), fsio.Plain)
		})
		s.AddLine("飛ばす行", line, fsio.StreamOut)
		// FailWarn は識別子つきで言って続ける。
		withPolicy(func(p *fsio.Policy) { p.OnFail, p.Ticket, p.Message = fsio.FailWarn, "t1", "続ける ({reason})" }, func() {
			fsio.WriteFile(fail, []byte("b"), fsio.Plain)
		})
		// FailQuiet は何も言わない。
		withPolicy(func(p *fsio.Policy) { p.OnFail = fsio.FailQuiet }, func() { fsio.WriteFile(fail, []byte("q"), fsio.Plain) })
		// 書けたものは Places を数え、同じ組の Call に Tag を渡す。
		marks := s.NewGroup()
		withPolicy(func(p *fsio.Policy) { p.Places, p.Group, p.Tag = "t2", marks, "requested" }, func() {
			fsio.WriteFile(first, []byte("c"), fsio.Plain)
		})
		withPolicy(func(p *fsio.Policy) { p.OnFail, p.Group, p.Tag = fsio.FailQuiet, marks, "reviewed" }, func() {
			fsio.WriteFile(fail, []byte("d"), fsio.Plain)
		})
		s.Items = append(s.Items, fsio.Call{Group: marks, Run: func(done map[string]bool) []string {
			var got []string
			for k := range done {
				got = append(got, k)
			}
			slices.Sort(got)
			return got
		}})
		s.AddLine("エラーの行", 0, fsio.StreamErr)
		fsio.WriteFile(undone, []byte("e"), fsio.Plain)
		// FailStop は Undo を消して止める。後ろは書かない。
		withPolicy(func(p *fsio.Policy) { p.Ticket, p.Undo = "t3", []string{undone} }, func() {
			fsio.WriteFile(fail, []byte("f"), fsio.Plain)
		})
		fsio.WriteFile(after, []byte("g"), fsio.Plain)
	})
	var out, errs bytes.Buffer
	applied := write(&out, &errs, &agree.Planned{Stage: s})
	if applied.Code != 1 || applied.StoppedAt != "t3" || !reflect.DeepEqual(applied.Placed, []string{"t2"}) {
		t.Errorf("%+v", applied)
	}
	reason := fsio.WriteFile(fail, []byte("x"), fsio.Plain)
	if applied.Reason != reason {
		t.Errorf("理由 %q, 書いた答え %q", applied.Reason, reason)
	}
	if want := "  行の組 (" + reason + ")\nrequested\n"; out.String() != want {
		t.Errorf("標準出力 %q", out.String())
	}
	if want := "ccnavi: t1: 続ける (" + reason + ")\nエラーの行\nccnavi: t3: " + reason + "\n"; errs.String() != want {
		t.Errorf("標準エラー %q", errs.String())
	}
	if !exists(first) || exists(undone) || exists(after) {
		t.Errorf("first %v undone %v after %v", exists(first), exists(undone), exists(after))
	}
}

// 並べる段で止まっていたら、並べた分を書いてから止まった理由を言う。
func TestWriteStoppedWhilePlanning(t *testing.T) {
	path := filepath.Join(t.TempDir(), "a.md")
	s := staged(t, func(*fsio.Stage) { fsio.WriteFile(path, []byte("a"), fsio.Plain) })
	var out, errs bytes.Buffer
	applied := write(&out, &errs, &agree.Planned{Stage: s, Stopped: &agree.Stop{Reason: "止めた"}})
	if applied.Code != 1 || applied.StoppedAt != "" || errs.String() != "ccnavi: 止めた\n" || !exists(path) {
		t.Errorf("%+v %q %v", applied, errs.String(), exists(path))
	}
}

// 並べた後に行き先が置かれていたら、動かさずに止める（上書きしない）。
func TestWriteMoveTargetAppeared(t *testing.T) {
	dir := t.TempDir()
	source, target := filepath.Join(dir, "todo.md"), filepath.Join(dir, "done.md")
	testkit.Put(t, source, "a")
	s := staged(t, func(*fsio.Stage) { fsio.Move(source, target) })
	testkit.Put(t, target, "other")
	var out, errs bytes.Buffer
	if applied := write(&out, &errs, &agree.Planned{Stage: s}); applied.Code != 1 || errs.String() != "ccnavi: 移動先に既にある\n" {
		t.Errorf("%+v %q", applied, errs.String())
	}
	if data, _ := os.ReadFile(target); string(data) != "other" {
		t.Errorf("%q", data)
	}
}

func TestWrittenPathsKeepOnlyInsideTheRoot(t *testing.T) {
	root := t.TempDir()
	got := writtenPaths(root, []string{filepath.Join(root, "a", "b.md"), filepath.Join(filepath.Dir(root), "out.md"), root})
	if want := []string{"a/b.md", "."}; !reflect.DeepEqual(got, want) {
		t.Errorf("%q", got)
	}
}

// 次の手が複数あっても `次:` は 1 行（最初の手）。後の手は `案内:`。
func TestFollowHasOneNext(t *testing.T) {
	got := follow([]string{"一手目", "二手目", "三手目"})
	if want := []string{"次: 一手目", "案内: 二手目", "案内: 三手目"}; !reflect.DeepEqual(got, want) {
		t.Errorf("%q", got)
	}
}

func TestTaggedPutsTheCodeAfterThePrefix(t *testing.T) {
	cases := map[string]string{
		"ccnavi: 理由": "ccnavi: [CORE_APPROVE_NOTHING] 理由",
		"  補足":       "[CORE_APPROVE_NOTHING]   補足",
	}
	for in, want := range cases {
		if got := tagged(codeApproveNothing, in); got != want {
			t.Errorf("%q → %q", in, got)
		}
	}
	if got := refused(codeConfirmMoved, nil); !reflect.DeepEqual(got, []string{"ccnavi: [CORE_CONFIRM_MOVED] CORE_CONFIRM_MOVED"}) {
		t.Errorf("%q", got)
	}
	if got := refused(codeConfirmMoved, []string{"ccnavi: a", "b"}); !reflect.DeepEqual(got, []string{"ccnavi: [CORE_CONFIRM_MOVED] a", "b"}) {
		t.Errorf("%q", got)
	}
}

// 手で動かした移動の確定は、移動より余裕幅（10 分）以上前に作られ、最後のコメントもその前のスレッドだけを受け入れる。
func TestLateThreads(t *testing.T) {
	moved := host.Epoch("2026-01-05T00:00:00Z")
	cases := []struct {
		name          string
		created, last string
		late          bool
	}{
		{"余裕幅より前", "2026-01-04T23:49:00Z", "", false},
		{"余裕幅の中", "2026-01-04T23:55:00Z", "", true},
		{"移動の後の返信", "2026-01-01T00:00:00Z", "2026-01-05T01:00:00Z", true},
		{"時刻を読めない", "", "", true},
	}
	for _, c := range cases {
		got := lateThreads([]host.Thread{{URL: c.name, CreatedAt: c.created, LastCommentAt: c.last}}, moved)
		if (len(got) == 1) != c.late {
			t.Errorf("%s: %v", c.name, got)
		}
	}
}

// 親のワークツリーが無いときは黙って数えない（承認は止めない）。
func TestDirtyParentsWithoutTheWorktree(t *testing.T) {
	root := workspace(t)
	if ospath.IsDir(testkit.Worktree(root, "i9999")) {
		t.Fatal("ワークツリーが在る")
	}
	conf, _ := config.Load(root)
	if got := dirtyParents(conf, root, []*ticket.Ticket{{ID: "i9999"}}); len(got) != 0 {
		t.Errorf("%v", got)
	}
}
