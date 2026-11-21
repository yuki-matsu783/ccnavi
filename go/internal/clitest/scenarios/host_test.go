package scenarios

import (
	"encoding/json"
	"errors"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/hoststub"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/runner"
)

func (g *rig) places() map[string]bool {
	m, _ := filepath.Glob(filepath.Join(g.scratch, "ccnavi-host-*"))
	set := map[string]bool{}
	for _, p := range m {
		set[p] = true
	}
	return set
}

func (g *rig) newPlaces(before map[string]bool) int {
	n := 0
	for p := range g.places() {
		if !before[p] {
			n++
		}
	}
	return n
}

func hostScenario(t *testing.T, w ws, kind string, steps []map[string]any, extra map[string]any) string {
	setup := map[string]any{"host": kind, "reset": resetBase(w.base)}
	for k, v := range extra {
		setup[k] = v
	}
	return w.scenario(t, setup, steps)
}

func canConnect(port int) bool {
	c, err := net.Dial("tcp", net.JoinHostPort("127.0.0.1", itoa(port)))
	if err != nil {
		return false
	}
	_ = c.Close()
	return true
}

func itoa(n int) string { return strconv.Itoa(n) }

func seeded() map[string]any {
	note := map[string]any{"id": 101, "body": "ここを直してください", "resolvable": true, "resolved": false, "created_at": "2026-10-25T00:00:00Z",
		"author": map[string]any{"id": 2, "username": "reviewer"}, "position": map[string]any{"new_path": "a.txt", "new_line": 3}}
	mr := map[string]any{"iid": 1, "state": "opened", "source_branch": "i0001", "target_branch": "main", "title": "i0001",
		"web_url": "http://127.0.0.1/ns/proj/-/merge_requests/1", "reviewers": []any{}, "notes": []any{}, "created_at": "2026-10-25T00:00:00Z",
		"discussions": []any{map[string]any{"id": "d101", "individual_note": false, "notes": []any{note}}}}
	return map[string]any{"project": "ns/proj", "seq": 100, "issues": []any{}, "mrs": []any{mr}}
}

func editState(t *testing.T, path string, f func(map[string]any)) {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(read(t, path)), &m); err != nil {
		t.Fatal(err)
	}
	f(m)
	data, _ := json.Marshal(m)
	write(t, path, string(data))
}

func TestHostStepsGetTheStubEnvAndEverythingIsGoneAfterFinish(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g := newRig(t, w)
	before := g.places()
	var env map[string]string
	var existed bool
	g.fn = func(in runner.StepInput) (*core.Stream, error) {
		env = in.Env
		existed = exists(filepath.Dir(strings.Split(in.Env["PATH"], string(os.PathListSeparator))[0]))
		return stepOut, nil
	}
	rc := g.r.Run(w.runOpts(hostScenario(t, w, "bare", nil, nil)))
	if rc != 0 && rc != 2 || !existed || env["FAKE_ORIGIN_URL"] != hoststub.OriginURL || g.newPlaces(before) != 0 {
		t.Fatalf("rc = %d・env = %v\n%s", rc, env, g.text())
	}
	md := read(t, filepath.Join(w.out, "report.md"))
	if !contains(md, "相手（setup.host）: bare", "模擬のずれで見逃した問題がありうる") {
		t.Error("report に相手が書かれていない")
	}
	rs, _ := judgeReport(w.out)
	for _, r := range rs {
		if r.Kind == "host" && (r.Expect != "setup.host" || !contains(r.Why, "bare", "模擬のずれ")) {
			t.Errorf("host の行 = %+v", r)
		}
	}
}

func TestRealHostLeavesTheRunUnchanged(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g := newRig(t, w)
	before := g.places()
	var env map[string]string = map[string]string{"x": "y"}
	g.fn = func(in runner.StepInput) (*core.Stream, error) { env = in.Env; return stepOut, nil }
	g.r.Run(w.runOpts(hostScenario(t, w, "real", nil, nil)))
	md := read(t, filepath.Join(w.out, "report.md"))
	if env != nil || g.newPlaces(before) != 0 || !strings.Contains(md, "相手（setup.host）: real: 本物") || strings.Contains(md, "模擬のずれ") {
		t.Errorf("env = %v\n%s", env, md)
	}
}

func TestFakeGitlabLeavesTheOtherSideStateAtTheEndOfEachStep(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g := newRig(t, w)
	n := 0
	g.fn = func(in runner.StepInput) (*core.Stream, error) {
		if n++; n == 1 { // 1 つ目の step の間に、相手側で MR が作られた状況にする
			editState(t, w.state(t).Host.StatePath, func(m map[string]any) { m["mrs"] = seeded()["mrs"] })
		}
		return stepOut, nil
	}
	steps := []map[string]any{{"prompt": "a"}, {"prompt": "b"}}
	if rc := g.r.Run(w.runOpts(hostScenario(t, w, "fake-gitlab", steps, nil))); rc != 0 && rc != 2 {
		t.Fatalf("rc = %d\n%s", rc, g.text())
	}
	var snap struct {
		Step    int
		Summary map[string]any
		MRs     []struct {
			Approvals map[string]any
		}
	}
	if err := json.Unmarshal([]byte(read(t, filepath.Join(w.out, "host-1.json"))), &snap); err != nil {
		t.Fatal(err)
	}
	if snap.Step != 1 || snap.Summary["mrs"] != float64(1) || snap.Summary["threads_unresolved"] != float64(1) || snap.MRs[0].Approvals["taken"] != true || snap.MRs[0].Approvals["approved"] != false {
		t.Errorf("host-1.json = %+v", snap)
	}
	if !exists(filepath.Join(w.out, "host-2.json")) {
		t.Error("step ごとに残す")
	}
	st := w.state(t)
	if len(st.HostSnapshots) != 2 || st.HostSnapshots[0].File != "host-1.json" || !st.HostSnapshots[0].OK || !st.HostSnapshots[1].OK {
		t.Errorf("host_snapshots = %+v", st.HostSnapshots)
	}
	if md := read(t, filepath.Join(w.out, "report.md")); !contains(md, "host-N.json", "承認は MR ごとに読んだ") {
		t.Error("report に記録の要約が無い")
	}
}

func TestASnapshotThatCannotBeWrittenStopsTheRunAndCleansUp(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g := newRig(t, w)
	before := g.places()
	g.fn = func(in runner.StepInput) (*core.Stream, error) {
		if err := os.Mkdir(filepath.Join(in.Out, "host-1.json"), 0o755); err != nil { // 記録を書けなくする
			t.Fatal(err)
		}
		return stepOut, nil
	}
	steps := []map[string]any{{"prompt": "a"}, {"prompt": "b"}}
	rc := g.r.Run(w.runOpts(hostScenario(t, w, "fake-gitlab", steps, nil)))
	h := w.state(t).Halt
	if rc != 2 || g.ran() != 1 || h == nil || h.Kind != "host" || g.newPlaces(before) != 0 || !w.restored(t) {
		t.Errorf("記録の失敗は step を止めて後片付けする: rc = %d・halt = %+v\n%s", rc, h, g.text())
	}
}

func TestBareAndRealLeaveNoHostState(t *testing.T) {
	t.Parallel()
	for _, kind := range []string{"bare", "real"} {
		t.Run(kind, func(t *testing.T) {
			t.Parallel()
			w := hostWs(t)
			g := newRig(t, w)
			g.r.Run(w.runOpts(hostScenario(t, w, kind, nil, nil)))
			if m, _ := filepath.Glob(filepath.Join(w.out, "host-*.json")); len(m) != 0 || len(w.state(t).HostSnapshots) != 0 || strings.Contains(read(t, filepath.Join(w.out, "report.md")), "host-N.json") {
				t.Error("bare と real は相手側の状態を残さない")
			}
		})
	}
}

func TestAPanicStopsTheServerAndLeavesNoPlace(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g := newRig(t, w)
	before := g.places()
	port := 0
	g.fn = func(runner.StepInput) (*core.Stream, error) {
		port = w.state(t).Host.Port
		panic("割り込み")
	}
	if p := panics(func() { g.r.Run(w.runOpts(hostScenario(t, w, "fake-gitlab", nil, nil))) }); p != "割り込み" {
		t.Fatalf("panic = %v", p)
	}
	if canConnect(port) || g.newPlaces(before) != 0 {
		t.Error("模擬が動いたまま、または置き場が残っている")
	}
}

func TestSetupGitPushReachesTheBareAndNotTheRealOrigin(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g := newRig(t, w)
	var pushed string
	g.fn = func(runner.StepInput) (*core.Stream, error) {
		pushed = run.Git(t, w.state(t).Host.Bare, "rev-parse", "--verify", "-q", "refs/heads/feat")
		return stepOut, nil
	}
	extra := map[string]any{"git": [][]string{{"git", "push", "-q", "origin", "main:refs/heads/feat"}}}
	g.r.Run(w.runOpts(hostScenario(t, w, "bare", nil, extra)))
	if pushed == "" {
		t.Errorf("bare に届いていない\n%s", g.text())
	}
}

func TestFailuresAfterTheHostIsMadeLeaveNoPlace(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g := newRig(t, w)
	before := g.places()
	g.fn = func(runner.StepInput) (*core.Stream, error) { return nil, errors.New("起動できない") }
	if rc := g.r.Run(w.runOpts(hostScenario(t, w, "fake-gitlab", nil, nil))); rc != 2 || g.newPlaces(before) != 0 {
		t.Errorf("失敗した step: rc = %d", rc)
	}
	// 相手を作ったあとの setup の失敗（作れないファイル）でも置き場を残さない
	w2 := hostWs(t)
	g2 := newRig(t, w2)
	before2 := g2.places()
	extra := map[string]any{"files": map[string]any{"projects/test-repo/README.md/x": "y"}}
	path := hostScenario(t, w2, "fake-gitlab", nil, extra)
	scn, err := core.LoadFile(path, core.AgentClaude)
	if err != nil {
		t.Fatal(err)
	}
	top := w2.target
	if _, err := g2.r.StartScenario(scn, path, w2.root, top, w2.out); err == nil {
		t.Error("setup の失敗はエラーで返す")
	}
	if g2.newPlaces(before2) != 0 || !w2.restored(t) {
		t.Error("置き場か対象が残っている")
	}
}

func TestATargetWithoutOriginIsRefusedWithNext(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	run.Git(t, w.target, "remote", "remove", "origin")
	g := newRig(t, w)
	before := g.places()
	rc := g.r.Run(w.runOpts(hostScenario(t, w, "bare", nil, nil)))
	if rc != 3 || !contains(g.text(), "origin が無い", "次: ") || g.ran() != 0 || g.newPlaces(before) != 0 {
		t.Errorf("rc = %d\n%s", rc, g.text())
	}
}

func TestATargetThatIsCcnaviItselfIsRefusedBeforeAnythingIsMade(t *testing.T) {
	t.Parallel()
	w := newWs(t, realOrigin, realOrigin)
	g := newRig(t, w)
	before := g.places()
	rc := g.r.Run(w.runOpts(hostScenario(t, w, "bare", nil, nil)))
	if rc != 3 || !contains(g.text(), "origin が ccnavi 本体の origin と同じ", "次: ") || g.ran() != 0 || g.newPlaces(before) != 0 {
		t.Errorf("rc = %d\n%s", rc, g.text())
	}
}

func TestAHumanStepStopsTheServerAndKeepsThePlaceUntilNextEnds(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g := newRig(t, w)
	before := g.places()
	steps := []map[string]any{{"prompt": "a"}, {"human": true, "instruction": "打つ"}, {"prompt": "b"}}
	var port int
	var statePath string
	g.fn = func(runner.StepInput) (*core.Stream, error) {
		h := w.state(t).Host
		port, statePath = h.Port, h.StatePath
		editState(t, statePath, func(m map[string]any) { m["seq"] = 555 }) // この step で模擬の状態が動いたものとして扱う
		return stepOut, nil
	}
	if rc := g.r.Run(w.runOpts(hostScenario(t, w, "fake-gitlab", steps, nil))); rc != 10 {
		t.Fatalf("rc = %d\n%s", rc, g.text())
	}
	if !exists(w.state(t).Host.Dir) || canConnect(port) || g.newPlaces(before) != 1 {
		t.Error("置き場は残し、模擬は止める")
	}
	var seq any
	var url string
	var live bool
	g.fn = func(in runner.StepInput) (*core.Stream, error) {
		var m map[string]any
		_ = json.Unmarshal([]byte(read(t, statePath)), &m)
		seq = m["seq"]
		p := w.state(t).Host.Port
		live = canConnect(p)
		url = in.Env["FAKE_ORIGIN_URL"]
		return stepOut, nil
	}
	if rc := g.r.Next(w.out); rc != 0 && rc != 2 {
		t.Fatalf("next: rc = %d\n%s", rc, g.text())
	}
	if seq != float64(555) || !live || !strings.HasPrefix(url, "http://127.0.0.1:") || !strings.HasSuffix(url, "/ns/proj.git") {
		t.Errorf("next は状態を初期値で上書きしない: seq = %v・live = %v・url = %s", seq, live, url)
	}
	if g.newPlaces(before) != 0 || canConnect(port) {
		t.Error("next が終わったら置き場も模擬も消える")
	}
}

func TestResumeStopsWhenThePortIsTaken(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g := newRig(t, w)
	steps := []map[string]any{{"prompt": "a"}, {"human": true, "instruction": "打つ"}}
	var port int
	g.fn = func(runner.StepInput) (*core.Stream, error) { port = w.state(t).Host.Port; return stepOut, nil }
	if rc := g.r.Run(w.runOpts(hostScenario(t, w, "fake-gitlab", steps, nil))); rc != 10 {
		t.Fatalf("rc = %d", rc)
	}
	l, err := net.Listen("tcp", net.JoinHostPort("127.0.0.1", itoa(port)))
	if err != nil {
		t.Skip("ポートを取り直せない")
	}
	defer l.Close()
	if rc := g.r.Next(w.out); rc != 3 || !contains(g.text(), "使われている", "次: ") {
		t.Errorf("同じポートが取れなければ止まる: rc = %d\n%s", rc, g.text())
	}
}

func TestNextAfterAHaltOrARestoreRemovesThePlaceAndDoesNotRunTheSteps(t *testing.T) {
	t.Parallel()
	w := hostWs(t)
	g := newRig(t, w)
	before := g.places()
	steps := []map[string]any{{"prompt": "a"}, {"human": true, "instruction": "打つ"}, {"prompt": "b"}}
	if rc := g.r.Run(w.runOpts(hostScenario(t, w, "bare", steps, nil))); rc != 10 {
		t.Fatalf("rc = %d", rc)
	}
	if g.newPlaces(before) != 1 {
		t.Fatal("人間の step では置き場を残す")
	}
	if rc := g.r.Restore(w.out); rc != 0 || g.newPlaces(before) != 0 {
		t.Errorf("restore で置き場を消す: rc = %d", rc)
	}
	if rc := g.r.Next(w.out); rc != 2 || g.ran() != 1 {
		t.Errorf("restore のあとの next は step を回さない: rc = %d・%d 回", rc, g.ran())
	}
	// finish に届かないまま halt だけ残った回
	w2 := hostWs(t)
	g2 := newRig(t, w2)
	before2 := g2.places()
	if rc := g2.r.Run(w2.runOpts(hostScenario(t, w2, "bare", steps, nil))); rc != 10 {
		t.Fatalf("rc = %d", rc)
	}
	st := w2.state(t)
	st.Halt = &core.Halt{Step: 1, Kind: "run", Reasons: []string{"x"}}
	if err := core.Save(w2.out, st); err != nil {
		t.Fatal(err)
	}
	if rc := g2.r.Next(w2.out); rc != 2 || g2.newPlaces(before2) != 0 || !w2.restored(t) {
		t.Errorf("halt だけ残った回の next は置き場も対象も片付ける: rc = %d", rc)
	}
	if h := w2.state(t).Host; h == nil || !h.Down {
		t.Errorf("置き場が無いことを記録する: %+v", h)
	}
}
