package oplog

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/prune"
)

var testStages = []StageSpec{{"reply", StageReply}, {"issue", StageIssue}, {"note", StageComment}}

func stateDir(t *testing.T) string {
	t.Helper()
	return filepath.Join(t.TempDir(), "logs", "state")
}

func begin(t *testing.T, state string, o Options) string {
	t.Helper()
	if o.Stages == nil {
		o.Stages = testStages
	}
	id, err := Begin(state, KindDecide, o)
	if err != nil {
		t.Fatal(err)
	}
	return id
}

func load(t *testing.T, state, id string) *Record {
	t.Helper()
	r, err := Load(state, id)
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func stageSet(t *testing.T, state, id, stage string, to State, url, account string) {
	t.Helper()
	if err := StageSet(state, id, stage, to, url, account); err != nil {
		t.Fatal(err)
	}
}

func isOpError(err error) bool {
	var e *Error
	return errors.As(err, &e)
}

// 記録の形は sh（ops show を jq で読む）との契約。キーの順序と indent=1・ensure_ascii=False を見る。
func TestRecordFileShape(t *testing.T) {
	state := stateDir(t)
	id := begin(t, state, Options{
		Repo:   pyjson.NewObject("host", "github.com", "kind", "github", "path", "o/r"),
		Parent: "i0001", Phase: 1, MR: 7,
		Shown:   []any{pyjson.NewObject("key", "k", "body", "指摘")},
		Choices: pyjson.NewObject("k", "keep"),
	})
	if !validID(id) {
		t.Fatalf("ID の形: %q", id)
	}
	raw, err := os.ReadFile(recordPath(state, id))
	if err != nil {
		t.Fatal(err)
	}
	text := string(raw)
	r := load(t, state, id)
	want := fmt.Sprintf(`{
 "version": 1,
 "id": "%s",
 "kind": "decide",
 "repo": {
  "host": "github.com",
  "kind": "github",
  "path": "o/r"
 },
 "parent": "i0001",
 "phase": 1,
 "mr": 7,
 "request_head": "",
 "shown": [
  {
   "key": "k",
   "body": "指摘"
  }
 ],
 "choices": {
  "k": "keep"
 },
 "plan": {},
 "applied": {},
 "stages": [
  {
   "id": "reply",
   "kind": "reply",
   "state": "pending",
   "url": "",
   "account": "",
   "tries": 0,
   "at": ""
  },
`, id)
	if !strings.HasPrefix(text, want) {
		t.Errorf("記録の形:\n%s", text)
	}
	if !strings.Contains(text, `"created_at": "`+r.createdAt+`",`) || !strings.HasSuffix(text, "\"\n}\n") {
		t.Errorf("末尾の形:\n%s", text)
	}
	// 読んで書き戻すと同じバイト列になる（ops show の出力が記録と食い違わない）
	again, _ := pyjson.Dumps(r.Object(), pyjson.Options{RawUnicode: true, Indent: 1})
	if again+"\n" != text {
		t.Errorf("読み戻しが違う:\n%s", again)
	}
}

func TestBeginWithAnExistingIDAddsOnlyMissingStages(t *testing.T) {
	state := stateDir(t)
	fixed := IDFor("close-early", "p", "0", "7", "k")
	for _, stages := range [][]StageSpec{{{"issue", StageIssue}}, {{"issue", StageIssue}, {"comment", StageComment}}} {
		if _, err := Begin(state, kindCloseEarly, Options{Stages: stages, ID: fixed}); err != nil {
			t.Fatal(err)
		}
		if len(stages) == 1 {
			stageSet(t, state, fixed, "issue", StateSent, "u", "")
		}
	}
	r := load(t, state, fixed)
	if len(r.Stages) != 2 || r.Stages[0].ID != "issue" || r.Stages[1].ID != "comment" || r.Stages[0].State != StateSent {
		t.Errorf("段階: %+v", r.Stages)
	}
}

func TestBadIDOrKindIsRefused(t *testing.T) {
	state := stateDir(t)
	if _, err := Begin(state, Kind("unknown"), Options{}); !isOpError(err) {
		t.Errorf("知らない種類: %v", err)
	}
	if _, err := Begin(state, KindDecide, Options{ID: "../x"}); !isOpError(err) || err.Error() != "操作IDの形が不正: '../x'" {
		t.Errorf("ID の形: %v", err)
	}
	if _, err := Load(state, "../../etc/passwd"); !isOpError(err) {
		t.Errorf("Load の ID: %v", err)
	}
}

func TestStageSet(t *testing.T) {
	state := stateDir(t)
	id := begin(t, state, Options{})
	stageSet(t, state, id, "issue", stateUnknown, "", "")
	stageSet(t, state, id, "issue", StatePending, "", "")
	stageSet(t, state, id, "issue", StateSent, "https://x/1", "me")
	s := load(t, state, id).Stages[1]
	if s.State != StateSent || s.URL != "https://x/1" || s.account != "me" || s.tries != 2 || s.at == "" {
		t.Errorf("段階: %+v", s)
	}
	stageSet(t, state, id, "reply", StateSent, "", "")
	for _, c := range []struct {
		stage string
		to    State
	}{{"reply", StatePending}, {"nope", StateSent}, {"reply", "weird"}} {
		if err := StageSet(state, id, c.stage, c.to, "", ""); !isOpError(err) {
			t.Errorf("%s → %s: %v", c.stage, c.to, err)
		}
	}
}

func TestPendingAndCompleteIfDone(t *testing.T) {
	state := stateDir(t)
	done := begin(t, state, Options{})
	for _, s := range testStages {
		stageSet(t, state, done, s.ID, StateSent, "", "")
	}
	half := begin(t, state, Options{})
	stageSet(t, state, half, "reply", StateSent, "", "")
	if got := Pending(state); len(got) != 1 || got[0].ID != half {
		t.Fatalf("Pending: %+v", got)
	}
	if ok, err := CompleteIfDone(state, done); !ok || err != nil {
		t.Error("送り終えた記録が消えない")
	}
	if ok, _ := CompleteIfDone(state, half); ok {
		t.Error("未完了の記録が消えた")
	}
	if load(t, state, done) != nil || load(t, state, half) == nil {
		t.Error("消す・残すが違う")
	}
	if got := Summary(load(t, state, half)); got != fmt.Sprintf("decide（のフェーズ 0）%s: 未完了の段階 issue=pending, note=pending", half) {
		t.Errorf("Summary: %q", got)
	}
}

func TestBrokenRecordIsReportedNotHidden(t *testing.T) {
	state := stateDir(t)
	id := begin(t, state, Options{})
	if err := os.WriteFile(recordPath(state, id), []byte("{not json"), 0o644); err != nil {
		t.Fatal(err)
	}
	got := Pending(state)
	if len(got) != 1 || got[0].Broken == "" || !strings.Contains(Summary(got[0]), "壊れている") {
		t.Errorf("壊れた記録: %+v", got)
	}
}

func TestAppliedSetInParallelLosesNothing(t *testing.T) {
	state := stateDir(t)
	id := begin(t, state, Options{})
	var keys []string
	var wg sync.WaitGroup
	for i := range 12 {
		key := fmt.Sprintf("k%d", i)
		keys = append(keys, key)
		wg.Go(func() {
			if err := AppliedSet(state, id, key, true); err != nil {
				t.Error(err)
			}
		})
	}
	wg.Wait()
	got := load(t, state, id).Applied.Keys()
	slices.Sort(got)
	slices.Sort(keys)
	if !slices.Equal(got, keys) {
		t.Errorf("applied: %v", got)
	}
}

func TestStaleLockIsTakenOver(t *testing.T) {
	state := stateDir(t)
	id := begin(t, state, Options{})
	path := filepath.Join(dir(state), id+lockExt)
	if err := os.WriteFile(path, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-2 * lockStale)
	if err := os.Chtimes(path, old, old); err != nil {
		t.Fatal(err)
	}
	if err := AppliedSet(state, id, "local", true); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); !errors.Is(err, os.ErrNotExist) {
		t.Error("ロックが残った")
	}
}

func TestMarkSent(t *testing.T) {
	state := stateDir(t)
	id := IDFor("request", "p1", "0", "7", "k")
	if _, err := Begin(state, kindRequest, Options{Parent: "p1", MR: 7, Stages: []StageSpec{{"comment", StageComment}}, ID: id}); err != nil {
		t.Fatal(err)
	}
	stageSet(t, state, id, "comment", stateUnknown, "", "")
	got, err := MarkSent(state, kindRequest, "p1", "7", "k", "comment", "u")
	if err != nil || got != id {
		t.Fatalf("MarkSent = %q, %v", got, err)
	}
	if load(t, state, id).Stages[0].State != StateSent || len(Pending(state)) != 0 {
		t.Error("送り済みにならない")
	}
}

func TestRetireParentRemovesOnlyFinishedRecordsOfThatParent(t *testing.T) {
	state := stateDir(t)
	done := begin(t, state, Options{Parent: "p1"})
	for _, s := range testStages {
		stageSet(t, state, done, s.ID, StateSent, "", "")
	}
	open := begin(t, state, Options{Parent: "p1"})
	other := begin(t, state, Options{Parent: "p2", Stages: []StageSpec{}})
	if got := RetireParent(state, "p1"); !slices.Equal(got, []string{done}) {
		t.Errorf("消した: %v", got)
	}
	if load(t, state, done) != nil || load(t, state, open) == nil || load(t, state, other) == nil {
		t.Error("消す・残すが違う")
	}
	if got := RetireParent(state, ""); got != nil {
		t.Errorf("親が空で消した: %v", got)
	}
}

func TestDiscardUntouched(t *testing.T) {
	state := stateDir(t)
	o := Options{Parent: "i0001", Phase: 1}
	old, mine := begin(t, state, o), begin(t, state, o)
	if got := DiscardUntouched(state, KindDecide, "i0001", 1, mine); !slices.Equal(got, []string{old}) {
		t.Errorf("消した: %v", got)
	}
	sent, applied := begin(t, state, o), begin(t, state, o)
	other := begin(t, state, Options{Parent: "i0001", Phase: 2})
	stageSet(t, state, sent, "reply", StateSent, "", "")
	if err := AppliedSet(state, applied, "local", true); err != nil {
		t.Fatal(err)
	}
	if got := DiscardUntouched(state, KindDecide, "i0001", 1, mine); len(got) != 0 {
		t.Errorf("何か起きた記録を消した: %v", got)
	}
	for _, kept := range []string{mine, sent, applied, other} {
		if load(t, state, kept) == nil {
			t.Errorf("%s が消えた", kept)
		}
	}
}

func TestReconcile(t *testing.T) {
	state := stateDir(t)
	id := begin(t, state, Options{})
	fresh := load(t, state, id)
	stageSet(t, state, id, "issue", stateUnknown, "", "alice")
	tried := load(t, state, id)
	now := time.Now()
	cases := []struct {
		name    string
		r       *Record
		stage   string
		found   []string
		listing Listing
		account string
		at      time.Time
		want    Verdict
	}{
		{"1 件は送った", fresh, "issue", []string{"https://x/1"}, ListedAll, "me", now, verdictSent},
		{"複数は止める", fresh, "issue", []string{"a", "b"}, ListedAll, "me", now, verdictStop},
		{"最後まで読めた 0 件は未送信", fresh, "issue", nil, ListedAll, "me", now, verdictSend},
		{"打ち切った 0 件は不明", fresh, "issue", nil, ListedPart, "me", now, verdictUnknown},
		{"読めない一覧は止める", fresh, "issue", nil, ListFailed, "me", now, verdictStop},
		{"アカウント違いは不明", tried, "issue", nil, ListedAll, "bob", now, verdictUnknown},
		{"送った直後は不明", tried, "issue", nil, ListedAll, "alice", now, verdictUnknown},
		{"間が空けば未送信", tried, "issue", nil, ListedAll, "alice", now.Add(recentWindow + time.Minute), verdictSend},
		{"無い段階は止める", fresh, "nope", nil, ListedAll, "me", now, verdictStop},
	}
	for _, c := range cases {
		if got, why := Reconcile(c.r, c.stage, c.found, c.listing, c.account, c.at); got != c.want {
			t.Errorf("%s: %s（%s）", c.name, got, why)
		}
	}
}

// records/prune は操作記録の置き場と「送り終えた」の綴りを写して持つ（tickets を読めない）。写しが持ち主と
// ずれると、送り終えていない記録を数え損ねる（Python は tests/core/test_prune.py が見ていた）。
func TestPruneCountsUnsentRecords(t *testing.T) {
	state := stateDir(t)
	id := begin(t, state, Options{Stages: []StageSpec{{"note", StageComment}}})
	root := filepath.Dir(filepath.Dir(state))
	if got := prune.Run(root, "", state, "", prune.Options{DryRun: true}).KeptOps; got != 1 {
		t.Fatalf("送り終えていない記録を %d 件と数えた", got)
	}
	stageSet(t, state, id, "note", StateSent, "", "")
	if got := prune.Run(root, "", state, "", prune.Options{DryRun: true}).KeptOps; got != 0 {
		t.Fatalf("送り終えた記録を %d 件と数えた", got)
	}
}
