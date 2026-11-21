package git

import (
	"io/fs"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
)

// stageWS は ccnavi-stage.sh を置いた使い捨てのワークスペース。
type stageWS struct {
	t      *testing.T
	ws     string
	bin    string
	script string
}

func newStageWS(t *testing.T) *stageWS {
	t.Helper()
	tmp := base.TempDir(t)
	ws := filepath.Join(tmp, "ws")
	scripts := copyScripts(t, ws, "ccnavi-stage.sh")
	base.PutMessages(t, ws)
	return &stageWS{t: t, ws: ws, bin: exeWrapper(t, filepath.Join(tmp, "bin")), script: slash(filepath.Join(scripts, "ccnavi-stage.sh"))}
}

// stage は ccnavi-stage.sh を cwd（空なら ws）で打つ。本体は実行ファイル（ccnavi stage）。
func (s *stageWS) stage(cwd string, args ...string) run.Result {
	s.t.Helper()
	if cwd == "" {
		cwd = s.ws
	}
	return run.Sh(s.t, s.script, args, run.Opts{Dir: cwd, Env: map[string]string{"CCNAVI_ENTRY_PATH": s.bin}})
}

func (s *stageWS) path(rel string) string { return filepath.Join(s.ws, filepath.FromSlash(rel)) }

const (
	rulesRel = ".ccnavi/config/common/rules.yml"
	hookRel  = ".claude/hooks/lint-py.sh"
)

func newStageFixture(t *testing.T) *stageWS {
	s := newStageWS(t)
	write(t, s.path(rulesRel), "version: 1\n")
	write(t, s.path(hookRel), "echo lint\n")
	return s
}

// 元ファイルが wip/handoff/ の同じ階層へ cp される（保護された階層名は読み替える）。元は触らない。
func TestStageCopiesWithTheSameLayoutAndLeavesTheOriginal(t *testing.T) {
	t.Parallel()
	s := newStageFixture(t)
	r := s.stage("", rulesRel, hookRel)
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	for _, c := range []struct{ got, want string }{
		{read(t, s.path("wip/handoff/"+rulesRel)), "version: 1\n"},
		{read(t, s.path("wip/handoff/handoff-.claude/hooks/lint-py.sh")), "echo lint\n"},
		{read(t, s.path(rulesRel)), "version: 1\n"},
	} {
		if c.got != c.want {
			t.Errorf("got %q, want %q", c.got, c.want)
		}
	}
}

// cwd が .claude/worktrees/<名前> の中なら、下書きはそのワークツリーの wip/handoff/ に置き、取り込みの手順はそのワークツリーを --root に指す。
func TestStageInsideAWorktreeUsesThatWorktreesHandoff(t *testing.T) {
	t.Parallel()
	s := newStageFixture(t)
	wt := s.path(".claude/worktrees/scratch-1-x")
	write(t, filepath.Join(wt, "src", ".keep"), "")
	write(t, filepath.Join(wt, filepath.FromSlash(rulesRel)), "version: 1\n")
	r := s.stage(filepath.Join(wt, "src"), s.path(rulesRel))
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	draft := filepath.Join(wt, "wip", "handoff", filepath.FromSlash(rulesRel))
	if !isFile(draft) {
		t.Fatalf("下書きが無い: %s", r.Stdout)
	}
	if exists(s.path("wip/handoff/" + rulesRel)) {
		t.Error("ワークスペースの下書きの置き場にも作られた")
	}
	write(t, draft, "version: 2\n")
	r = s.stage(wt, "--commands", s.path(rulesRel))
	if r.Code != 0 {
		t.Fatalf("--commands code = %d: %s", r.Code, r.Stderr)
	}
	if strings.Contains(r.Stdout, "mv ") || !strings.Contains(slash(r.Stdout), "--root '"+slash(wt)+"' '"+rulesRel+"'") {
		t.Errorf("取り込みの手順が --root にワークツリーを指していない: %s", r.Stdout)
	}
	// 下書きの置き場の中は元として受けない
	r = s.stage(wt, draft)
	if r.Code != 2 || !strings.Contains(r.Stderr, "下書きの置き場の中") {
		t.Errorf("code = %d, stderr = %q", r.Code, r.Stderr)
	}
}

// 次の一手は、いま下書きを作ったパスを埋めて 1 行だけ出す。下書きが既にあるだけでも消えない。
func TestStageNextStepNamesTheStagedPaths(t *testing.T) {
	t.Parallel()
	s := newStageFixture(t)
	r := s.stage("", rulesRel, hookRel)
	var nexts []string
	for _, line := range strings.Split(r.Stdout, "\n") {
		if strings.HasPrefix(line, "次: ") {
			nexts = append(nexts, line)
		}
	}
	if len(nexts) != 1 {
		t.Fatalf("次: の行が %d 本: %s", len(nexts), r.Stdout)
	}
	if strings.Contains(nexts[0], "<パス>") || !strings.HasSuffix(nexts[0], "--commands "+s.path(rulesRel)+" "+s.path(hookRel)+" を打つ（取り込みの手順が出る）") {
		t.Errorf("次の一手 = %q", nexts[0])
	}
	if again := s.stage("", rulesRel); !strings.Contains(again.Stdout, "次: ") {
		t.Errorf("下書きが既にあるときに次の一手が消えた: %s", again.Stdout)
	}
}

// --commands は、元と違う下書きだけに取り込みの手順（defense を含む全種類）を出し、mv を出さない。
// 編集していない下書きが残るときは 1 で終わり、次の一手は出さない。スクリプトは差し替えない。
func TestStageCommandsPrintTheHandoffForEditedDraftsNotMv(t *testing.T) {
	t.Parallel()
	s := newStageFixture(t)
	s.stage("", rulesRel, hookRel)
	write(t, s.path("wip/handoff/handoff-.claude/hooks/lint-py.sh"), "echo lint2\n")
	r := s.stage("", "--commands", rulesRel, hookRel)
	handoff := "sh '" + slash(s.ws) + "/.ccnavi/scripts/ccnavi-user-handoff.sh'"
	for _, want := range []string{handoff + " '" + hookRel + "'\n", "--yes --expect <上のハッシュ>"} {
		if !strings.Contains(slash(r.Stdout), want) {
			t.Errorf("stdout に %q が無い: %s", want, r.Stdout)
		}
	}
	if strings.Contains(r.Stdout, "mv ") || strings.Contains(r.Stdout, "次: ") {
		t.Errorf("mv か次の一手が出ている: %s", r.Stdout)
	}
	if r.Code != 1 || !strings.Contains(r.Stderr, "まだ編集していない") {
		t.Errorf("code = %d, stderr = %q", r.Code, r.Stderr)
	}
	if got := read(t, s.path(hookRel)); got != "echo lint\n" {
		t.Errorf("元が書き換わった: %q", got)
	}
}

// draftOf は stage のあと wip/handoff/ の下にできた唯一のファイルの相対パス。
func draftOf(t *testing.T, ws string) string {
	t.Helper()
	root := filepath.Join(ws, "wip", "handoff")
	var found []string
	_ = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			rel, _ := filepath.Rel(root, path)
			found = append(found, filepath.ToSlash(rel))
		}
		return nil
	})
	if len(found) != 1 {
		t.Fatalf("下書きは 1 件のはず: %v", found)
	}
	return found[0]
}

// 階層名の読み替え（`.claude` → `handoff-.claude`）。見本は samples.NameSamples（表の読み方は Go の handoffnames の試験が同じ見本で見る）。
func TestStageConvertsEveryNameSample(t *testing.T) {
	t.Parallel()
	for _, sample := range samples.NameSamples {
		t.Run(sample.From, func(t *testing.T) {
			t.Parallel()
			s := newStageWS(t)
			write(t, s.path(sample.From), "x\n")
			r := s.stage("", sample.From)
			if r.Code != 0 {
				t.Fatalf("code = %d: %s", r.Code, r.Stderr)
			}
			if got := draftOf(t, s.ws); got != sample.To {
				t.Errorf("stage の変換 = %q, want %q", got, sample.To)
			}
		})
	}
}

// 元のパスに予約名（`handoff-.claude`）の階層があれば、stage が断り、何も作らない。
func TestStageRefusesAnOriginalPathWithTheReservedName(t *testing.T) {
	t.Parallel()
	for _, rel := range samples.ReservedNames {
		t.Run(rel, func(t *testing.T) {
			t.Parallel()
			s := newStageWS(t)
			write(t, s.path(rel), "x\n")
			r := s.stage("", rel)
			if r.Code != 2 || !strings.Contains(r.Stderr, "予約") {
				t.Errorf("code = %d, stderr = %q", r.Code, r.Stderr)
			}
			if exists(s.path("wip")) {
				t.Error("断ったのに wip を作った")
			}
		})
	}
}

// stage → 編集 → --commands で、取り込みの手順が、読み替えを戻した元のパスを指す。
func TestStageCommandsPointTheHandoffAtTheOriginalPathNotTheRenamedDraft(t *testing.T) {
	t.Parallel()
	s := newStageWS(t)
	write(t, s.path(".claude/settings.json"), "{\"a\": 1}\n")
	if r := s.stage("", ".claude/settings.json"); r.Code != 0 {
		t.Fatalf("stage code = %d: %s", r.Code, r.Stderr)
	}
	draft := s.path("wip/handoff/handoff-.claude/settings.json")
	if got := read(t, draft); got != "{\"a\": 1}\n" {
		t.Fatalf("下書き = %q", got)
	}
	write(t, draft, "{\"a\": 2}\n")
	r := s.stage("", "--commands", ".claude/settings.json")
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	if !strings.Contains(r.Stdout, "'.claude/settings.json' --yes --expect <上のハッシュ>") {
		t.Errorf("元のパスを指していない: %s", r.Stdout)
	}
	for _, bad := range []string{"mv ", "handoff-.claude", "wip/handoff/.claude"} {
		if strings.Contains(r.Stdout, bad) {
			t.Errorf("stdout に %q が出ている: %s", bad, r.Stdout)
		}
	}
}
