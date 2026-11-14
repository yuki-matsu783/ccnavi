package marks

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyjson"
)

// 書く形（キーの順・indent・時刻の足し方）は、見本と突き合わせる approval のテストが見る。
// ここは消す順と溜める段階、台帳の読めない形を見る。

func TestClearTakesEveryKindInOrder(t *testing.T) {
	dir := t.TempDir()
	for _, k := range []Kind{Pending, Reviewed, Requested} {
		if failed := WriteMark(dir, "p", 1, k, nil); failed != "" {
			t.Fatal(failed)
		}
	}
	got := Clear(dir, "p", 1, nil)
	if !reflect.DeepEqual(got.Kinds, []Kind{Requested, Reviewed, Pending}) || got.Failed != "" || got.Warnings != nil {
		t.Errorf("%+v", got)
	}
	if len(Marks(dir, "p", 1)) != 0 {
		t.Error("マーカーが残った")
	}
	if got := Clear(dir, "p", 1, nil); got.Kinds != nil {
		t.Errorf("無いものを消した: %+v", got)
	}
}

func TestClearInTheStageAnnouncesWhatWasRemoved(t *testing.T) {
	dir := t.TempDir()
	for _, k := range []Kind{Skipped, Reviewed} {
		if failed := WriteMark(dir, "p", 2, k, nil); failed != "" {
			t.Fatal(failed)
		}
	}
	stage, stop, err := fsio.Staging()
	if err != nil {
		t.Fatal(err)
	}
	announce := func(k []Kind) []string {
		return []string{"消した: " + string(k[0]) + " ほか" + string(rune('0'+len(k)))}
	}
	got := Clear(dir, "p", 2, announce)
	stop()
	if !reflect.DeepEqual(got.Kinds, []Kind{Reviewed, Skipped}) {
		t.Errorf("%+v", got)
	}
	if len(Marks(dir, "p", 2)) != 2 {
		t.Error("溜める段階の中でディスクから消した")
	}
	var call *fsio.Call
	var ops []*fsio.Op
	for _, item := range stage.Items {
		switch x := item.(type) {
		case fsio.Call:
			call = &x
		case *fsio.Op:
			ops = append(ops, x)
		}
	}
	if call == nil || len(ops) != 2 || !reflect.DeepEqual(call.Lines, []string{"消した: reviewed ほか2"}) {
		t.Fatalf("call=%+v ops=%d", call, len(ops))
	}
	// reviewed を先に消す（消せなければ止めるので、ほかに手を付ける前に決める）。
	if ops[0].Policy.Tag != string(Reviewed) || ops[0].Policy.OnFail != fsio.FailStop || ops[1].Policy.OnFail != fsio.FailWarn {
		t.Errorf("扱い: %+v / %+v", ops[0].Policy, ops[1].Policy)
	}
	// Writer(FS) が skipped だけを消せたなら、行は skipped だけで作る。
	if lines := call.Run(map[string]bool{string(Skipped): true}); !reflect.DeepEqual(lines, []string{"消した: skipped ほか1"}) {
		t.Errorf("%v", lines)
	}
	if lines := call.Run(map[string]bool{}); lines != nil {
		t.Errorf("何も消せなかったのに行を出した: %v", lines)
	}
}

func TestLedger(t *testing.T) {
	dir := t.TempDir()
	if entries, problem := ReadLedger(dir, "p"); entries != nil || problem != "" {
		t.Errorf("無い台帳: %v %q", entries, problem)
	}
	path := parentPath(dir, "p", Ledger)
	for _, text := range []string{`{"version": 1}`, `{"entries": [1]}`, `[]`, `{`} {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
		if _, problem := ReadLedger(dir, "p"); !strings.Contains(problem, "受け入れ台帳") {
			t.Errorf("%s を読めた", text)
		}
		// 読めない台帳には書かない（読めなかった項目が消える）。
		if failed := AddLedger(dir, "p", []*pyjson.Object{pyjson.NewObject("key", "k")}); failed == "" {
			t.Errorf("%s に書いた", text)
		}
		if data, _ := os.ReadFile(path); string(data) != text {
			t.Errorf("書き換えた: %s", data)
		}
	}
}
