package prune

import (
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/env"
)

var now = time.Now()

const (
	sOld  = "11111111-aaaa-bbbb-cccc-000000000001"
	sLive = "22222222-aaaa-bbbb-cccc-000000000002"
	sMe   = "33333333-aaaa-bbbb-cccc-000000000003"
)

// big はローテートの下限（1 MB）を超える記録。
var big = strings.Repeat("a", 1024*1024+100)

func clearLimits(t *testing.T) {
	t.Helper()
	for _, name := range []string{env.LogRotateMB, env.LogKeepDays, env.StateKeepDays, env.StopReviewKeepDays} {
		t.Setenv(name, "")
	}
}

func put(t *testing.T, path, text string, ageDays float64) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	age(t, path, ageDays)
	return path
}

func age(t *testing.T, path string, ageDays float64) {
	t.Helper()
	when := now.Add(-time.Duration(ageDays * float64(day)))
	if err := os.Chtimes(path, when, when); err != nil {
		t.Fatal(err)
	}
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

type place struct{ root, logs, log, state string }

func newPlace(t *testing.T) place {
	t.Helper()
	clearLimits(t)
	root := t.TempDir()
	p := place{root: root, logs: filepath.Join(root, "logs")}
	p.log = filepath.Join(p.logs, "decisions.jsonl")
	p.state = filepath.Join(p.logs, "state")
	os.MkdirAll(p.state, 0o777)
	return p
}

func (p place) run(session string, dry bool) *Report {
	return Run(p.root, p.log, p.state, session, Options{DryRun: dry, Now: now})
}

func TestRotate(t *testing.T) {
	p := newPlace(t)
	put(t, p.log, "{}\n", 0)
	if r := p.run("", false); len(r.Rotated) != 0 || !exists(p.log) {
		t.Errorf("小さい記録を動かした: %+v", r)
	}

	t.Setenv(env.LogRotateMB, "1")
	body := strings.Repeat(`{"decision":"allow"}`+"\n", 60000)
	put(t, p.log, body, 0)
	r := p.run("", false)
	if len(r.Rotated) != 1 || r.Rotated[0].From != "logs/decisions.jsonl" ||
		!regexp.MustCompile(`^logs/decisions\.\d{8}-\d{6}\.jsonl$`).MatchString(r.Rotated[0].To) || exists(p.log) {
		t.Fatalf("rotated = %+v", r.Rotated)
	}
	if data, _ := os.ReadFile(filepath.Join(p.root, r.Rotated[0].To)); string(data) != body {
		t.Error("中身が変わった")
	}

	// 同じ秒の 2 回目は上書きせず -2 にする。行き先を排他で押さえるので、同じ秒に始まった別のセッションも
	// 先にローテートした記録を上書きしない。
	put(t, p.log, strings.Repeat("b", len(body)), 0)
	r = p.run("", false)
	if len(r.Rotated) != 1 || !strings.HasSuffix(r.Rotated[0].To, "-2.jsonl") {
		t.Errorf("rotated = %+v", r.Rotated)
	}
	if data, _ := os.ReadFile(filepath.Join(p.root, strings.Replace(r.Rotated[0].To, "-2.jsonl", ".jsonl", 1))); string(data) != body {
		t.Error("先にローテートした記録を上書きした")
	}

	// dry-run は動かさずに並べる。
	put(t, p.log, big, 0)
	if r := p.run("", true); len(r.Rotated) != 1 || !strings.HasSuffix(r.Rotated[0].To, "-3.jsonl") || !exists(p.log) {
		t.Errorf("dry-run = %+v", r.Rotated)
	}

	// 0 はその段を止める。
	t.Setenv(env.LogRotateMB, "0")
	if r := p.run("", false); len(r.Rotated) != 0 {
		t.Errorf("0 で動かした: %+v", r.Rotated)
	}
}

func TestLimits(t *testing.T) {
	limits := []limit{rotateMB, logKeepDays, stateKeepDays}
	clearLimits(t)
	for _, l := range limits {
		if v, problem := l.read(); v != l.fallback || problem != "" {
			t.Errorf("%s: %v %q", l.env, v, problem)
		}
	}
	if rotateMB.fallback != 10 || logKeepDays.fallback != 14 || stateKeepDays.fallback != 14 {
		t.Error("既定が変わった")
	}
	// 0 に近づけるだけで保持日数のうちの記録が消えないよう、0 のほかは下限を置く。有限でない値も既定。
	for _, l := range limits {
		for _, raw := range []string{"0.001", "0.5", "inf", "-inf", "nan", "1e400", "many", "-1", "0x10"} {
			t.Setenv(l.env, raw)
			if v, problem := l.read(); v != l.fallback || !strings.Contains(problem, l.env) {
				t.Errorf("%s=%s: %v %q", l.env, raw, v, problem)
			}
		}
		for raw, want := range map[string]float64{"0": 0, "1": 1, "2.5": 2.5, " 1_0 ": 10, "1e-400": 0} {
			t.Setenv(l.env, raw)
			if v, problem := l.read(); v != want || problem != "" {
				t.Errorf("%s=%q: %v %q", l.env, raw, v, problem)
			}
		}
		t.Setenv(l.env, "")
	}
	t.Setenv(env.LogKeepDays, "many")
	if _, problem := logKeepDays.read(); problem != "CCNAVI_LOG_KEEP_DAYS='many' は 0 以上の数ではないので既定の 14 で動く" {
		t.Errorf("problem = %q", problem)
	}
	t.Setenv(env.LogKeepDays, "0.5")
	if _, problem := logKeepDays.read(); problem != "CCNAVI_LOG_KEEP_DAYS='0.5' は下限の 1 より小さいので既定の 14 で動く（0 はその段を止める）" {
		t.Errorf("problem = %q", problem)
	}
}

func TestInfiniteRotateIsReported(t *testing.T) {
	p := newPlace(t)
	t.Setenv(env.LogRotateMB, "inf")
	put(t, p.log, "{}\n", 0)
	if r := p.run("", false); len(r.Rotated) != 0 || len(r.Problems) == 0 || !strings.Contains(r.Problems[0], env.LogRotateMB) {
		t.Errorf("report = %+v", r)
	}
}

func TestOnlyOldRotatedLogsAreRemoved(t *testing.T) {
	p := newPlace(t)
	put(t, p.log, "{}\n", 100)
	old := put(t, filepath.Join(p.logs, "decisions.20260101-000000.jsonl"), "x", 15)
	old2 := put(t, filepath.Join(p.logs, "decisions.20260101-000000-2.jsonl"), "x", 15)
	fresh := put(t, filepath.Join(p.logs, "decisions.20260920-000000.jsonl"), "x", 13)
	other := put(t, filepath.Join(p.logs, "git-20260101-000000-1.log"), "x", 100)
	stray := put(t, filepath.Join(p.logs, "decisions.backup.jsonl"), "x", 100)
	r := p.run("", false)
	if strings.Join(r.Logs, ",") != "logs/decisions.20260101-000000-2.jsonl,logs/decisions.20260101-000000.jsonl" {
		t.Errorf("logs = %v", r.Logs)
	}
	for _, path := range []string{old, old2} {
		if exists(path) {
			t.Errorf("残った: %s", path)
		}
	}
	for _, path := range []string{p.log, fresh, other, stray} {
		if !exists(path) {
			t.Errorf("消えた: %s", path)
		}
	}
	// 保持日数は動かせる。記録の設定が空なら触らない。
	t.Setenv(env.LogKeepDays, "2")
	fresh2 := put(t, filepath.Join(p.logs, "decisions.20260921-000000.jsonl"), "x", 3)
	Run(p.root, "", p.state, "", Options{Now: now})
	if !exists(fresh2) {
		t.Error("記録の設定が空なのに消した")
	}
	p.run("", false)
	if exists(fresh2) {
		t.Error("保持日数を動かしても残った")
	}
}

// sessionFiles は 1 つのセッションが置く記録を全部置く。
func sessionFiles(t *testing.T, state, s string, ageDays float64) []string {
	t.Helper()
	var paths []string
	for _, name := range []string{
		s + ".reported.json", s + ".turn.json", "nudged-" + s + ".json", "denied-" + s + ".json",
		"direct-" + s + ".json", "userprompt-" + s + ".json",
		"turngate-" + s + "-integrate-user-turn-0123456789-t1.json",
		"once-" + s + "-main.json", "once-" + s + "-peers-agent-1.json", "failstreak-" + s + "-main.json",
		"subagent-" + s + "-guard-records.bounced", "subagent-" + s + "-agent-1.running",
		"subagent-" + s + "-agent-1.outcome",
	} {
		paths = append(paths, put(t, filepath.Join(state, name), "x", ageDays))
	}
	guard := filepath.Join(state, "selfguard", s)
	put(t, filepath.Join(guard, "rules_common"), "x", ageDays)
	age(t, guard, ageDays)
	return append(paths, guard)
}

func allExist(t *testing.T, paths []string, want bool) {
	t.Helper()
	for _, path := range paths {
		if exists(path) != want {
			t.Errorf("%s: exists = %v", path, !want)
		}
	}
}

func TestStatePrune(t *testing.T) {
	t.Run("終わったセッションはまとめて消す", func(t *testing.T) {
		p := newPlace(t)
		doomed := sessionFiles(t, p.state, sOld, 20)
		r := p.run("", false)
		allExist(t, doomed, false)
		if len(r.State) != len(doomed) || !slices.Contains(r.State, "logs/state/selfguard/"+sOld) {
			t.Errorf("state = %v", r.State)
		}
	})
	t.Run("新しい記録が 1 つあれば全部残す", func(t *testing.T) {
		p := newPlace(t)
		kept := sessionFiles(t, p.state, sLive, 20)
		age(t, filepath.Join(p.state, sLive+".turn.json"), 0.1)
		p.run("", false)
		allExist(t, kept, true)
	})
	t.Run("自己防衛のバックアップも生きている印", func(t *testing.T) {
		p := newPlace(t)
		kept := sessionFiles(t, p.state, sLive, 20)
		put(t, filepath.Join(p.state, "selfguard", sLive, "settings"), "x", 0.1)
		p.run("", false)
		allExist(t, kept, true)
	})
	t.Run("いま始まったセッションは消さない", func(t *testing.T) {
		p := newPlace(t)
		kept := sessionFiles(t, p.state, sMe, 20)
		p.run(sMe, false)
		allExist(t, kept, true)
	})
	t.Run("書きかけの一時ファイル", func(t *testing.T) {
		p := newPlace(t)
		doomed := sessionFiles(t, p.state, sOld, 20)
		doomed = append(doomed,
			put(t, filepath.Join(p.state, ".once-"+sOld+"-main.abc12345.part.json"), "x", 20),
			put(t, filepath.Join(p.state, ".nudged-"+sOld+".abc12345.part.json"), "x", 20)) // それ自身の日付で
		kept := sessionFiles(t, p.state, sLive, 20)
		age(t, filepath.Join(p.state, sLive+".turn.json"), 0.1)
		kept = append(kept,
			put(t, filepath.Join(p.state, ".once-"+sLive+"-main.abc12345.part.json"), "x", 20),
			put(t, filepath.Join(p.state, ".denied-"+sOld+".zz998877.part.json"), "x", 0),
			// 報告済みと turn の一時ファイルはどの形にも当たらず残る。
			put(t, filepath.Join(p.state, "."+sOld+".abc12345.part.json"), "x", 20),
			put(t, filepath.Join(p.state, "."+sOld+".turn.abc12345.part.json"), "x", 20))
		p.run("", false)
		allExist(t, doomed, false)
		allExist(t, kept, true)
	})
	t.Run("セッションを名前に持たないものと知らない名前は残す", func(t *testing.T) {
		p := newPlace(t)
		var kept []string
		for _, name := range []string{
			"review-request-i0001-1.md", "risk-judge-i0001-01-01.md", "aside/20260101-000000/new.txt",
			"selfguard/store/abc", "package.json", "tsconfig.json", "review-result-123.json", "notes.json",
		} {
			kept = append(kept, put(t, filepath.Join(p.state, filepath.FromSlash(name)), "x", 90))
		}
		if r := p.run("", false); len(r.State) != 0 {
			t.Errorf("state = %v", r.State)
		}
		allExist(t, kept, true)
	})
	t.Run("切れ目が決まらないものと日付で消すもの", func(t *testing.T) {
		p := newPlace(t)
		gone := []string{
			put(t, filepath.Join(p.state, "subagent-"+sOld+"-x.bounced"), "x", 20),
			put(t, filepath.Join(p.state, "turn-"+sOld+".json"), "x", 20),
			put(t, filepath.Join(p.state, "mirror-i0001.json"), "x", 20),
			put(t, filepath.Join(p.state, stopOpsResult, "1-2.kv"), "x", 20),
		}
		kept := []string{
			put(t, filepath.Join(p.state, "subagent-"+sLive+"-x.bounced"), "x", 1),
			put(t, filepath.Join(p.state, "turn-"+sLive+".json"), "x", 1),
			put(t, filepath.Join(p.state, "mirror-i0002.json"), "x", 1),
		}
		p.run("", false)
		allExist(t, gone, false)
		allExist(t, kept, true)
	})
	t.Run("dry-run は並べるだけ", func(t *testing.T) {
		p := newPlace(t)
		doomed := sessionFiles(t, p.state, sOld, 20)
		if r := p.run("", true); len(r.State) != len(doomed) {
			t.Errorf("state = %v", r.State)
		}
		allExist(t, doomed, true)
	})
	t.Run("保持日数は動かせる", func(t *testing.T) {
		p := newPlace(t)
		doomed := sessionFiles(t, p.state, sOld, 3)
		p.run("", false)
		allExist(t, doomed, true)
		t.Setenv(env.StateKeepDays, "2")
		p.run("", false)
		allExist(t, doomed, false)
	})
}

func link(t *testing.T, target, path string) {
	t.Helper()
	if err := os.Symlink(target, path); err != nil {
		t.Skipf("リンクを作れない: %v", err)
	}
}

func hasLinkProblem(r *Report) bool {
	for _, p := range r.Problems {
		if strings.Contains(p, "リンク") {
			return true
		}
	}
	return false
}

// リンクの先は ccnavi の置き場とは限らない。辿って消すと置き場の外のファイルを消す。
func TestLinksAreNotFollowed(t *testing.T) {
	t.Run("state の置き場", func(t *testing.T) {
		p := newPlace(t)
		elsewhere := t.TempDir()
		victim := put(t, filepath.Join(elsewhere, sOld+".turn.json"), "x", 90)
		result := put(t, filepath.Join(elsewhere, stopOpsResult, "x.kv"), "x", 90)
		linked := filepath.Join(p.logs, "state-link")
		link(t, elsewhere, linked)
		r := Run(p.root, p.log, linked, "", Options{Now: now})
		allExist(t, []string{victim, result}, true)
		if len(r.State) != 0 || !hasLinkProblem(r) {
			t.Errorf("report = %+v", r)
		}
	})
	t.Run("自己防衛の置き場", func(t *testing.T) {
		p := newPlace(t)
		elsewhere := t.TempDir()
		victim := put(t, filepath.Join(elsewhere, sOld, "settings"), "x", 90)
		age(t, filepath.Dir(victim), 90)
		link(t, elsewhere, filepath.Join(p.state, "selfguard"))
		if r := p.run("", false); !exists(victim) || !hasLinkProblem(r) {
			t.Errorf("report = %+v", r)
		}
	})
	t.Run("停止確認の材料", func(t *testing.T) {
		p := newPlace(t)
		outside := t.TempDir()
		victim := put(t, filepath.Join(outside, "material", "a.json"), "x", 200)
		link(t, outside, filepath.Join(p.logs, "stop-review"))
		p.run("", false)
		allExist(t, []string{victim}, true)
	})
}

func TestStopReview(t *testing.T) {
	p := newPlace(t)
	keep := put(t, filepath.Join(p.logs, "stop-review", "material", "keep.json"), "x", 40)
	old := put(t, filepath.Join(p.state, "stopreview-plan-x.json"), "x", 40)
	p.run("other", false)
	if !exists(keep) || exists(old) {
		t.Error("材料は別の保持日数（既定 90 日）、計画は state の保持日数")
	}
	Run(p.root, p.log, p.state, "", Options{Now: now.Add(60 * day)})
	if exists(keep) {
		t.Error("材料が保持日数を過ぎても残った")
	}
	// 試行のイベントのローテートは保持日数と独立。
	t.Setenv(env.StopReviewKeepDays, "0")
	t.Setenv(env.LogRotateMB, "1")
	events := put(t, filepath.Join(p.logs, "stop-review", "events.jsonl"), big, 0)
	p.run("", false)
	if exists(events) {
		t.Error("イベントをローテートしていない")
	}
}

func TestKeptOps(t *testing.T) {
	p := newPlace(t)
	ops := filepath.Join(p.state, opsDir)
	put(t, filepath.Join(ops, "a.json"), `{"stages": [{"state": "pending"}]}`, 400)
	put(t, filepath.Join(ops, "b.json"), `{"stages": [{"state": "sent"}]}`, 400)
	put(t, filepath.Join(ops, "c.json"), `broken`, 400)
	r := p.run("own", false)
	if r.KeptOps != 2 || !exists(filepath.Join(ops, "a.json")) {
		t.Errorf("kept = %d", r.KeptOps)
	}
	lines := Lines(r, true)
	if !strings.Contains(lines[len(lines)-1], "消さない") {
		t.Errorf("lines = %q", lines)
	}
}

func TestSummaryAndLines(t *testing.T) {
	empty := &Report{}
	if Summary(empty) != "" || strings.Join(Lines(empty, false), "") != "動かすものはありません。" {
		t.Error("空の報告")
	}
	r := &Report{Rotated: []Rotation{{"a", "b"}}, Logs: []string{"c"}, State: []string{"d", "e"}}
	if got := Summary(r); got != "pruned rotated=1 logs=1 state=2" {
		t.Errorf("summary = %q", got)
	}
	if got := strings.Join(Lines(r, false), "|"); got != "ローテートした  a → b|削除した  c|削除した  d|削除した  e" {
		t.Errorf("lines = %q", got)
	}
}

func TestDiagPrune(t *testing.T) {
	setup := func(t *testing.T) (place, string) {
		p := newPlace(t)
		diag := filepath.Join(p.root, "logs", "diag")
		os.MkdirAll(diag, 0o777)
		return p, diag
	}
	t.Run("古いものを消し新しいものは残す", func(t *testing.T) {
		p, diag := setup(t)
		old := put(t, filepath.Join(diag, "ccnavi-git.log"), "a", 30)
		fresh := put(t, filepath.Join(diag, "ccnavi.log"), "a", 1)
		other := put(t, filepath.Join(diag, "notes.txt"), "a", 30)
		preview := Run(p.root, "", "", "", Options{DryRun: true, Now: now})
		r := Run(p.root, "", "", "", Options{Now: now})
		if exists(old) || !exists(fresh) || !exists(other) || strings.Join(r.Logs, ",") != "logs/diag/ccnavi-git.log" ||
			strings.Join(preview.Logs, ",") != "logs/diag/ccnavi-git.log" {
			t.Errorf("logs = %v", r.Logs)
		}
	})
	t.Run("保持日数 0 は残す", func(t *testing.T) {
		p, diag := setup(t)
		t.Setenv(env.LogKeepDays, "0")
		old := put(t, filepath.Join(diag, "ccnavi-git.log"), "a", 30)
		Run(p.root, "", "", "", Options{Now: now})
		if !exists(old) {
			t.Error("消した")
		}
	})
	t.Run("大きいものはローテートし、その回には消さない", func(t *testing.T) {
		p, diag := setup(t)
		t.Setenv(env.LogRotateMB, "1")
		put(t, filepath.Join(diag, "ccnavi.log"), big, 30)
		put(t, filepath.Join(diag, "ccnavi.20260901-000000.log"), big, 0) // ローテートした分はもう回さない
		preview := Run(p.root, "", "", "", Options{DryRun: true, Now: now})
		r := Run(p.root, "", "", "", Options{Now: now})
		if len(r.Rotated) != 1 || len(r.Logs) != 0 || len(preview.Rotated) != 1 || len(preview.Logs) != 0 {
			t.Errorf("real = %+v preview = %+v", r, preview)
		}
		if !regexp.MustCompile(`/ccnavi\.\d{8}-\d{6}\.log$`).MatchString(r.Rotated[0].To) {
			t.Errorf("to = %q", r.Rotated[0].To)
		}
	})
	t.Run("リンクの置き場は辿らない", func(t *testing.T) {
		p, diag := setup(t)
		outside := t.TempDir()
		victim := put(t, filepath.Join(outside, "x.log"), "x", 100)
		os.RemoveAll(diag)
		link(t, outside, diag)
		if r := Run(p.root, "", "", "", Options{Now: now}); !exists(victim) || !hasLinkProblem(r) {
			t.Errorf("report = %+v", r)
		}
	})
}

func TestLinkless(t *testing.T) {
	base := t.TempDir()
	root := filepath.Join(base, "root")
	out := filepath.Join(base, "out")
	os.MkdirAll(filepath.Join(root, "a", "b"), 0o777)
	os.MkdirAll(out, 0o777)
	linked := filepath.Join(root, "m")
	link(t, out, linked)
	r := &run{root: root}
	if r.linkless(linked) || !r.linkless(filepath.Join(root, "a", "b")) || r.linkless(out) {
		t.Error("linkless")
	}
	// root が別綴り（リンク経由）で、path が実体の表記なら範囲外。
	alias := filepath.Join(base, "alias")
	link(t, root, alias)
	if (&run{root: alias}).linkless(filepath.Join(root, "a")) || !(&run{root: alias}).linkless(filepath.Join(alias, "a")) {
		t.Error("別綴りの root")
	}
	if (&run{root: "relative-root"}).linkless(root) {
		t.Error("相対の root")
	}
}
