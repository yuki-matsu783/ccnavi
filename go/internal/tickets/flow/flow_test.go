package flow

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

const child = "i0001-01-01"

func childTicket(t *testing.T, started bool) *ticket.Ticket {
	t.Helper()
	text := strings.Join([]string{"---", "version: 1", "ticket: " + child, "parent: i0001", "phase: 1",
		"title: 子", "rationale: r", "allow:", "  - match: Write|Edit", `    glob: "wip/research/*"`,
		`started_at: ""`, `completed_at: ""`, `base_sha: ""`, "---", "", "本文", ""}, "\n")
	c, problems := ticket.Parse(text)
	if c == nil {
		t.Fatal(problems)
	}
	if started {
		c.StartedAt = "2026-09-25T00:00:00Z"
	}
	return c
}

func TestPlaceIsNormalized(t *testing.T) {
	root := t.TempDir()
	for _, approved := range []string{".ccnavi/approved", "./.ccnavi/approved", ".ccnavi//approved",
		".ccnavi/approved/.", "tickets/../.ccnavi/approved", ".ccnavi/approved/", ""} {
		conf := &config.Settings{Approved: approved}
		if got := FlowRel(conf, child); got != ".ccnavi/approved/flows/"+child+".yml" {
			t.Errorf("%q: FlowRel = %q", approved, got)
		}
		file := FlowFile(conf, root, child)
		if file != filepath.Join(root, ".ccnavi", "approved", "flows", child+".yml") {
			t.Errorf("%q: FlowFile = %q", approved, file)
		}
		if at, ok := Locate(conf, root, file); !ok || at != (Location{child + ".yml", "", true}) {
			t.Errorf("%q: Locate = %#v %v", approved, at, ok)
		}
	}
	conf := &config.Settings{Tickets: "./work/props/"}
	if got := DraftRel(conf, child); got != "work/props/flows/"+child+".yml" {
		t.Errorf("DraftRel = %q", got)
	}
}

func TestLocateFoldsSpellings(t *testing.T) {
	root := t.TempDir()
	conf := &config.Settings{}
	base := filepath.Join(root, ".ccnavi", "approved", "flows")
	for _, name := range []string{child + ".yml", "I0001-01-01.YML", child + ".yml.", child + ".yml ",
		child + ".yml::$DATA", child + ".yml:x"} {
		at, ok := Locate(conf, root, filepath.Join(base, name))
		if !ok || at != (Location{child + ".yml", "", true}) {
			t.Errorf("%q: %#v %v", name, at, ok)
		}
	}
	upper := filepath.Join(root, ".CCNAVI", "Approved", "FLOWS", "I0001-01-01.YML")
	if at, ok := Locate(conf, root, upper); !ok || at.name != child+".yml" || !at.known {
		t.Errorf("upper: %#v %v", at, ok)
	}
	for _, miss := range []string{filepath.Join(root, "flows", child+".yml"),
		filepath.Join(root, ".ccnavi", "approved", "doing", "x.md"), ""} {
		if at, ok := Locate(conf, root, miss); ok {
			t.Errorf("%q: %#v", miss, at)
		}
	}
	// ワークスペースの外は、どのプロジェクトのものでもない。
	if at, ok := Locate(conf, root, "/elsewhere/.ccnavi/approved/flows/"+child+".yml"); !ok || at.known {
		t.Errorf("outside: %#v %v", at, ok)
	}
	// ツリーの外の置き場は、どのプロジェクトの子にも当てる。
	shared := &config.Settings{Approved: "../shared/approved"}
	if at, _ := Locate(shared, "/w", "/elsewhere/shared/approved/flows/"+child+".yml"); at != (Location{child + ".yml", anyProject, true}) {
		t.Errorf("shared: %#v", at)
	}
	// 小文字にすると長さの変わる字（İ）が途中にあっても、位置が食い違わない。
	running := childTicket(t, true)
	for _, p := range []string{"/home/İsmail/ws/.ccnavi/approved/flows/I0001-01-01.YML",
		"/home/İsmail/ws/.ccnavi/Approved/flows/i0001-01-01.yml",
		"/home/ismail/ws/.CCNAVI/APPROVED/FLOWS/I0001-01-01.YML"} {
		at, ok := Locate(conf, "/home/ismail/ws", p)
		if !ok || at.name != child+".yml" || LockHit([]*ticket.Ticket{running}, Location{at.name, anyProject, true}) != running {
			t.Errorf("%q: %#v %v", p, at, ok)
		}
	}
	if n := len(fold("İx")); n != 2 {
		t.Errorf("fold の長さ %d", n)
	}
}

func TestLockHit(t *testing.T) {
	idle, running := childTicket(t, false), childTicket(t, true)
	at := Location{child + ".yml", "", true}
	cases := []struct {
		copies []*ticket.Ticket
		at     Location
		want   *ticket.Ticket
	}{
		{[]*ticket.Ticket{idle, running}, at, running}, // 識別子でまとめる前の 1 本でも着手中なら止める
		{[]*ticket.Ticket{idle}, at, nil},
		{[]*ticket.Ticket{running}, Location{at.name, "", false}, nil},
		{[]*ticket.Ticket{running}, Location{"i0001-01-02.yml", "", true}, nil},
		{[]*ticket.Ticket{running}, Location{at.name, anyProject, true}, running},
		{[]*ticket.Ticket{running}, Location{"I0001-01-01.YML", "", true}, running},
		{[]*ticket.Ticket{running}, Location{at.name, "web", true}, nil},
	}
	for i, c := range cases {
		if got := LockHit(c.copies, c.at); got != c.want {
			t.Errorf("%d: %v", i, got)
		}
	}
}

func TestReadBytesRefusesOddFiles(t *testing.T) {
	root := t.TempDir()
	file := testkit.Put(t, filepath.Join(root, "flows", "a.yml"), sampleFlow)
	if data, why := Load(file, root); data == nil {
		t.Fatal(why)
	}
	// 先頭の BOM は外して読む。
	if data, why := Load(testkit.Put(t, filepath.Join(root, "bom.yml"), "\ufeff"+sampleFlow), root); data == nil {
		t.Fatal(why)
	}
	if err := os.Link(file, filepath.Join(root, "alias.yml")); err == nil {
		if _, why := Load(file, root); why != hardLinked {
			t.Errorf("ハードリンク: %q", why)
		}
		if !HardLinked(file) || HardLinked(filepath.Join(root, "missing.yml")) {
			t.Error("HardLinked")
		}
	}
	dir := filepath.Join(root, "flows", "d.yml")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if _, why := Load(dir, root); why != notRegular {
		t.Errorf("ディレクトリ: %q", why)
	}
	big := testkit.Put(t, filepath.Join(root, "big.yml"), "nodes: []\npad: "+strings.Repeat("x", fileLimit+10)+"\n")
	if _, why := Load(big, root); !strings.Contains(why, "大きすぎる") {
		t.Errorf("大きい: %q", why)
	}
	if _, why := Load(filepath.Join(root, "missing.yml"), ""); why != "読めない (No such file or directory)" {
		t.Errorf("無い: %q", why)
	}
}

func TestReadBytesRefusesLinks(t *testing.T) {
	root := t.TempDir()
	real := testkit.Put(t, filepath.Join(root, "wip", "real.yml"), sampleFlow)
	flows := filepath.Join(root, ".ccnavi", "approved", "flows")
	if err := os.MkdirAll(flows, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(real, filepath.Join(flows, "a.yml")); err != nil {
		t.Skip("リンクを作れない")
	}
	for _, base := range []string{root, ""} {
		if _, why := Load(filepath.Join(flows, "a.yml"), base); why != linked {
			t.Errorf("%q: %q", base, why)
		}
	}
	testkit.Put(t, filepath.Join(root, "elsewhere", "b.yml"), sampleFlow)
	if err := os.Symlink(filepath.Join(root, "elsewhere"), filepath.Join(root, ".ccnavi", "approved", "x")); err != nil {
		t.Fatal(err)
	}
	if _, why := Load(filepath.Join(root, ".ccnavi", "approved", "x", "b.yml"), root); why != linked {
		t.Errorf("途中のリンク: %q", why)
	}
}

func TestInfo(t *testing.T) {
	root := t.TempDir()
	conf := &config.Settings{}
	with := func(state place.State, started bool) *ticket.Ticket {
		c := childTicket(t, started)
		c.TreeRoot, c.State = root, state
		return c
	}
	// 閉じた子でフローが無ければ欄を出さない（「フローを作る」を出させない）。
	for _, s := range []place.State{place.Done, place.Cancelled} {
		if FlowInfo(conf, root, with(s, true)) != nil {
			t.Errorf("%s: 欄がある", s)
		}
	}
	for _, c := range []struct {
		state   place.State
		started bool
		lock    place.State
	}{{place.Doing, false, ""}, {place.Doing, true, place.Doing}, {place.Todo, false, ""},
		{place.Review, false, place.Review}, {place.Review, true, place.Review}} {
		shown := FlowInfo(conf, root, with(c.state, c.started))
		if shown == nil || shown.Exists || shown.LockKind != c.lock || shown.Locked() != (c.lock != "") {
			t.Errorf("%s %v: %#v", c.state, c.started, shown)
		}
	}
	shown := FlowInfo(conf, root, with(place.Todo, false))
	if shown.Draft.Rel != "wip/proposals/flows/"+child+".yml" ||
		shown.Draft.Path != filepath.Join(root, "wip", "proposals", "flows", child+".yml") || shown.Draft.Exists {
		t.Errorf("下書き: %#v", shown.Draft)
	}
	testkit.Put(t, shown.Draft.Path, sampleFlow)
	testkit.Put(t, FlowFile(conf, root, child), sampleFlow)
	for _, s := range []place.State{place.Done, place.Cancelled, place.Todo} {
		shown := FlowInfo(conf, root, with(s, true))
		if shown == nil || !shown.Exists || shown.Linked || !shown.Draft.Exists || shown.Rel != ".ccnavi/approved/flows/"+child+".yml" {
			t.Errorf("%s: %#v", s, shown)
		}
	}
	if FlowInfo(conf, root, &ticket.Ticket{ID: "i0001"}) != nil {
		t.Error("親に欄がある")
	}
}

func TestBriefing(t *testing.T) {
	repo := testkit.RepoRoot(t)
	root := t.TempDir()
	conf := &config.Settings{}
	running := childTicket(t, true)
	running.TreeRoot = root
	if got := Briefing(conf, repo, running, TotalTextLimit, true); got != nil {
		t.Errorf("フローが無いのに %q", got)
	}
	file := testkit.Put(t, FlowFile(conf, root, child), sampleFlow)
	text := strings.Join(Briefing(conf, repo, running, TotalTextLimit, true), "\n")
	for _, want := range []string{"フロー: " + file, "合意済みの手順。編集しない", fenceOpen, fenceClose, "1. [start] 開始 → 2"} {
		if !strings.Contains(text, want) {
			t.Errorf("%q が無い:\n%s", want, text)
		}
	}
	if strings.Contains(text, "ロック") {
		t.Errorf("ロックに触れている:\n%s", text)
	}
	if got := Briefing(conf, repo, running, TotalTextLimit, false); len(got) != 1 || got[0] != "    フロー: "+file {
		t.Errorf("full でない: %q", got)
	}
	if text := strings.Join(Briefing(conf, repo, running, 0, true), "\n"); strings.Contains(text, fenceOpen) || !strings.Contains(text, "上限") {
		t.Errorf("上限:\n%s", text)
	}
	testkit.Put(t, file, "nodes:\n  - {id: s, type: start}\n  - {id: l, type: loop, data: {condition: c, maxIterations: 2}}\n")
	if text := strings.Join(Briefing(conf, repo, running, TotalTextLimit, true), "\n"); !strings.Contains(text, "loop のノード") {
		t.Errorf("loop の扱いが無い:\n%s", text)
	}
	testkit.Put(t, file, "nodes: [\n")
	if text := strings.Join(Briefing(conf, repo, running, TotalTextLimit, true), "\n"); !strings.Contains(text, "フローを読めない: YAML として読めない") {
		t.Errorf("読めない:\n%s", text)
	}
}

func TestDigestRecordAndNotice(t *testing.T) {
	root := t.TempDir()
	conf := &config.Settings{}
	running := childTicket(t, true)
	running.TreeRoot = root
	file := FlowFile(conf, root, child)
	if got := ChangedNotice(conf, root, running); got != "" {
		t.Errorf("記録が無いのに %q", got)
	}
	// フローが無くても記録する（着手のあとに現れたことも知らせる）。
	target, failed := RecordDigest(conf, root, running)
	if failed != "" || target != filepath.Join(root, ".ccnavi", "approved", "phases", "i0001", child+".flow.json") {
		t.Fatalf("%q %q", target, failed)
	}
	raw, _ := os.ReadFile(target)
	if want := "{\n \"fingerprint\": \"absent\",\n \"path\": \".ccnavi/approved/flows/" + child + ".yml\",\n \"at\": \"2026-09-25T00:00:00Z\"\n}\n"; string(raw) != want {
		t.Errorf("記録 %q", raw)
	}
	testkit.Put(t, file, sampleFlow)
	if got := ChangedNotice(conf, root, running); !strings.Contains(got, "着手後にフローが書き換わった") ||
		!strings.Contains(got, "着手のとき（無い）と違う（いま sha256:") {
		t.Errorf("現れたのに %q", got)
	}
	RecordDigest(conf, root, running)
	if got := ChangedNotice(conf, root, running); got != "" {
		t.Errorf("変わっていないのに %q", got)
	}
	if !strings.HasPrefix(fingerprint(file, root), markSHA256) {
		t.Error("ハッシュ")
	}
	testkit.Put(t, file, "nodes: []\n")
	if got := ChangedNotice(conf, root, running); !strings.Contains(got, codeChanged) || !strings.Contains(got, child) {
		t.Errorf("書き換えたのに %q", got)
	}
	idle := childTicket(t, false)
	idle.TreeRoot = root
	if got := ChangedNotice(conf, root, idle); got != "" {
		t.Errorf("着手していないのに %q", got)
	}
}

func TestInodeHit(t *testing.T) {
	root := t.TempDir()
	conf := &config.Settings{}
	running := childTicket(t, true)
	running.TreeRoot = root
	file := testkit.Put(t, FlowFile(conf, root, child), sampleFlow)
	alias := filepath.Join(root, "alias.yml")
	if err := os.Link(file, alias); err != nil {
		t.Skip(err)
	}
	if InodeHit(conf, root, []*ticket.Ticket{running}, alias) != running {
		t.Error("ハードリンクの別名を止めない")
	}
	if InodeHit(conf, root, []*ticket.Ticket{childTicket(t, false)}, alias) != nil {
		t.Error("着手前なのに止める")
	}
	other := testkit.Put(t, filepath.Join(root, "other.yml"), "x")
	if InodeHit(conf, root, []*ticket.Ticket{running}, other) != nil {
		t.Error("別のファイルを止める")
	}
}

func TestCatalog(t *testing.T) {
	root := t.TempDir()
	testkit.Put(t, filepath.Join(root, ".claude", "agents", "reviewer.md"), "---\nname: >-\n  code\n  reviewer # x\n---\n")
	testkit.Put(t, filepath.Join(root, ".claude", "agents", "plain.MD"), "no front\n")
	testkit.Put(t, filepath.Join(root, ".claude", "agents", "aliased.md"), "---\nname: &a x\nother: *a\n---\n")
	testkit.Put(t, filepath.Join(root, ".claude", "agents", "notes.txt"), "")
	testkit.Put(t, filepath.Join(root, ".claude", "agents", "dup.md"), "---\nname: Explore\n---\n")
	testkit.Put(t, filepath.Join(root, ".claude", "skills", "process-debug", "SKILL.md"), "\ufeff---\nname: debug\n---\n")
	testkit.Put(t, filepath.Join(root, ".claude", "skills", "bare", "SKILL.md"), "")
	testkit.Put(t, filepath.Join(root, ".claude", "skills", "empty", "README.md"), "")
	got := LoadCatalog(root)
	wantAgents := candidates{{"general-purpose", sourceBuiltin}, {"Explore", sourceBuiltin}, {"Plan", sourceBuiltin},
		{"aliased", sourceProject}, {"plain", sourceProject}, {"code reviewer # x", sourceProject}}
	wantSkills := candidates{{"bare", sourceProject}, {"debug", sourceProject}}
	if !equalCandidates(got.agents, wantAgents) || !equalCandidates(got.skills, wantSkills) {
		t.Errorf("%#v", got)
	}
	if got := LoadCatalog(""); len(got.agents) != 3 || len(got.skills) != 0 {
		t.Errorf("root なし: %#v", got)
	}
}

func equalCandidates(a, b candidates) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func TestTextReadsNumbersLikeTheBoard(t *testing.T) {
	for _, c := range []struct {
		in   any
		want string
	}{{1.0, "1"}, {2, "2"}, {1.5, "1.5"}, {true, ""}, {-0.0, "0"}, {1e20, "100000000000000000000"}, {1e21, "1e+21"}, {[]any{1}, ""}} {
		if got := textOf(c.in); got != c.want {
			t.Errorf("textOf(%v) = %q, want %q", c.in, got, c.want)
		}
	}
}
