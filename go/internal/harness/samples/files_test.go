package samples

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/oplog"
)

func TestOprecordMainWritesARecordTheExecutableTypeReadsAndAPlanWithItsID(t *testing.T) {
	t.Parallel()
	dir := base.TempDir(t)
	state, plan, spec := filepath.Join(dir, "state"), filepath.Join(dir, "plan.json"), filepath.Join(dir, "spec.json")
	body := `{"begin": {"kind": "decide", "repo": {"host": "gitlab", "mr_url": "u"}, "parent": "i0001", "phase": 1, "mr": 1,
		"shown": [{"key": "u1", "body": "x"}], "choices": {"u1": "issue"}, "plan": {"digest": "d0"},
		"stages": [["reply", "reply"], ["issue", "issue"], ["note", "comment"]]},
		"plan": {"version": 1, "digest": "d0"}}`
	if err := os.WriteFile(spec, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	var stderr strings.Builder
	if code := OprecordMain([]string{state, plan, spec}, &stderr); code != 0 {
		t.Fatalf("code = %d: %s", code, stderr.String())
	}
	ids := RecordIDs(state)
	if len(ids) != 1 {
		t.Fatalf("記録は 1 つのはず: %v", ids)
	}
	r, err := oplog.Load(state, ids[0])
	if err != nil || r == nil {
		t.Fatalf("Load: %v, %v", r, err)
	}
	if r.Parent != "i0001" || r.Phase != 1 || r.MR != 1 || len(r.Stages) != 3 || r.Stages[2].ID != "note" || r.Stages[0].State != oplog.StatePending {
		t.Errorf("記録の中身が違う: %+v", r)
	}
	raw, err := os.ReadFile(plan)
	if err != nil {
		t.Fatal(err)
	}
	data, err := pyjson.Loads(string(raw))
	if err != nil {
		t.Fatal(err)
	}
	if op := data.(*pyjson.Object).String("op"); op != ids[0] {
		t.Errorf("計画の op = %q, 記録 = %q", op, ids[0])
	}
	if string(raw) != `{"version": 1, "digest": "d0", "op": "`+ids[0]+`"}` {
		t.Errorf("計画は Python の json.dump の形: %s", raw)
	}
	var again strings.Builder
	if code := OprecordMain([]string{state}, &again); code != 1 || again.Len() == 0 {
		t.Errorf("引数が足りなければ 1 と文: %d %q", code, again.String())
	}
}

func TestRecordIDsAreInWriteOrder(t *testing.T) {
	t.Parallel()
	state := base.TempDir(t)
	var want []string
	for i := range 3 {
		id := BeginRecord(t, state, oplog.KindDecide, oplog.Options{Parent: "p", Phase: i})
		want = append(want, id)
		// 更新時刻の粒度が粗い機械でも順が付くよう、記録ごとに時刻をずらす
		stamp := mtimeAfter(i)
		if err := os.Chtimes(filepath.Join(state, "ops", id+".json"), stamp, stamp); err != nil {
			t.Fatal(err)
		}
	}
	if got := RecordIDs(state); !slices.Equal(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
	if RecordIDs(filepath.Join(state, "none")) != nil {
		t.Error("置き場が無ければ空")
	}
}

func TestAsWriteAndPushMarksStateOperationsOfARootWithAnOrigin(t *testing.T) {
	t.Parallel()
	root, state := base.TempDir(t), base.TempDir(t)
	if err := os.MkdirAll(filepath.Join(root, ".git"), 0o755); err != nil {
		t.Fatal(err)
	}
	config := func(text string) {
		if err := os.WriteFile(filepath.Join(root, ".git", "config"), []byte(text), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	args := func(a ...string) []string { return a }
	config("[core]\n\tbare = false\n")
	if got := AsWriteAndPush(root, state, "T", args("ticket", "start", "c1")); len(got) != 3 {
		t.Errorf("origin が無ければそのまま: %v", got)
	}
	config("[remote \"origin\"]\n\turl = /somewhere\n")
	got := AsWriteAndPush(root, state, "T", args("ticket", "start", "c1"))
	if len(got) != 7 || got[0] != "--record-writes" || got[2] != "--record-tree" || got[3] != "T" || got[4] != "ticket" {
		t.Fatalf("印が付かない: %v", got)
	}
	if dir := filepath.Dir(got[1]); !strings.HasSuffix(filepath.ToSlash(dir), "/write-and-push/self") {
		t.Errorf("書き込み先の置き場: %s", got[1])
	}
	for _, op := range [][2]string{{"ticket", "start"}, {"ticket", "finish"}, {"ticket", "cancel"}, {"review", "requested"}, {"review", "confirm"}, {"review", "ready"}} {
		if r := AsWriteAndPush(root, state, "T", args(op[0], op[1], "x")); r[0] != "--record-writes" {
			t.Errorf("%v は状態の操作: %v", op, r)
		}
	}
	if other := AsWriteAndPush(root, state, "T", args("ticket", "start", "c2")); other[1] == got[1] {
		t.Error("呼ぶたびに別のファイル")
	}
	for name, a := range map[string][]string{
		"not a state operation": args("ticket", "status"),
		"already marked":        args("--record-tree", "X", "ticket", "finish", "c1"),
		"reviewed preview":      args("user", "reviewed", "1", "--accept-unresolved", "--preview"),
		"reviewed without flag": args("user", "reviewed", "1"),
	} {
		if r := AsWriteAndPush(root, state, "T", a); !slices.Equal(r, a) {
			t.Errorf("%s: そのまま返すはず: %v", name, r)
		}
	}
	if r := AsWriteAndPush(root, state, "T", args("user", "reviewed", "1", "--accept-unresolved")); r[0] != "--record-writes" {
		t.Errorf("reviewed の受け入れは印を付ける: %v", r)
	}
}

func TestRecordMergeWritesTheMergedHeadIntoTheChildTicketAndCommits(t *testing.T) {
	t.Parallel()
	tree := base.TempDir(t)
	run := func(args ...string) string { return git(t, tree, args...) }
	run("init", "--quiet", "-b", "main")
	ticket := filepath.Join(tree, ".ccnavi", "approved", "doing", "c1.md")
	if err := os.MkdirAll(filepath.Dir(ticket), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(ticket, []byte("---\nticket: c1\nmerged_sha: \"\"\n---\n\n本文\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	run("add", "-A")
	run("commit", "--quiet", "-m", "init")
	run("branch", "c1")
	head := run("rev-parse", "c1")
	RecordMerge(t, tree, "c1")
	raw, _ := os.ReadFile(ticket)
	if !strings.Contains(string(raw), `merged_sha: "`+head+`"`) {
		t.Errorf("merged_sha が書かれない:\n%s", raw)
	}
	if run("status", "--porcelain") != "" || !strings.Contains(run("log", "-1", "--format=%s"), "merged_sha c1") {
		t.Error("書いたあとコミットされる")
	}
	// 子のチケットが無ければ何もしない
	run("branch", "c2")
	RecordMerge(t, tree, "c2")
	if run("status", "--porcelain") != "" {
		t.Error("子のチケットが無ければ何も書かない")
	}
}

func TestChromePlacementAnswersTheNamesTheExtensionUses(t *testing.T) {
	t.Parallel()
	placement := ChromePlacement(t, exe.ChromePath(t))
	if len(placement) == 0 {
		t.Fatal("置き場の答えが空")
	}
	text := ""
	for _, v := range placement {
		text += " " + strings.ToLower(toString(v))
	}
	for _, name := range []string{CompatFile, SettingsFile, CommonLayer} {
		if !strings.Contains(text, strings.ToLower(name)) {
			t.Errorf("置き場の答えに %s が無い: %v", name, placement)
		}
	}
}

func toString(v any) string {
	out, err := pyjson.Dumps(v, pyjson.Options{RawUnicode: true})
	if err != nil {
		return ""
	}
	return out
}

func mtimeAfter(i int) time.Time { return time.Unix(1_700_000_000+int64(i)*10, 0) }
