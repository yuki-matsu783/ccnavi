package rules

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// 横で作業する指定が有効な間の実行後チェックの受入テスト。
//
// ワークスペース 1 つとプロジェクト 2 つ（p と q）を作り、p の作業ワークツリーと直接作業の記録（`ccnavi-direct/<名前>`）を置く。
//   - 指定が有効なら、p のユーザのチェックアウトの deny の場所の変更は戻さず、出所不明として報告する
//   - エージェントが p の作業ワークツリーで動いていても、p は監視される
//   - ワークスペースと q は従来どおり戻る
//   - 指定が無い・ワークツリーを消した・共通ディレクトリが違う記録は無効で、従来どおり戻る

const sbsName = "fix-1-p-x"

func schemaRules(id string) payload {
	return payload{
		"version": 1,
		"deny":    []payload{rule(id, "Write|Edit", "*/schema/*", "schema is not edited by hand.")},
		"allow":   []payload{{"id": "anything", "match": "Bash|Read|Write|Edit", "regex": "."}},
	}
}

type sbs struct {
	t        *testing.T
	ws, p, q string
	wt       string
}

func newSBS(t *testing.T) *sbs {
	t.Helper()
	ws := base.TempDir(t)
	run.Git(t, ws, "init", "--quiet", "-b", "main")
	write(t, filepath.Join(ws, ".gitignore"), "/projects/\n/.claude/\n/logs/\n")
	write(t, filepath.Join(ws, "schema", "keep.sql"), "-- keep\n")
	write(t, base.CommonPath(ws, "rules"), jsonText(t, schemaRules("ws-schema")))
	run.Git(t, ws, "add", "-A")
	run.Git(t, ws, "commit", "--quiet", "-m", "init")
	s := &sbs{t: t, ws: ws}
	s.p, s.q = s.project("p"), s.project("q")
	s.wt = filepath.Join(ws, ".claude", "worktrees", sbsName)
	run.Git(t, s.p, "worktree", "add", "--quiet", s.wt, "-b", sbsName)
	return s
}

func (s *sbs) project(name string) string {
	root := filepath.Join(s.ws, "projects", name)
	mkdirs(s.t, root)
	run.Git(s.t, root, "init", "--quiet", "-b", "main")
	write(s.t, base.ConfigPath(root, "rules"), jsonText(s.t, schemaRules("schema")))
	write(s.t, filepath.Join(root, "schema", "keep.sql"), "-- keep\n")
	run.Git(s.t, root, "add", "-A")
	run.Git(s.t, root, "commit", "--quiet", "-m", "init")
	return root
}

// record は p の直接作業の記録を置く。side は横で作業する指定（yes・no）。
func (s *sbs) record(side string) {
	s.t.Helper()
	write(s.t, filepath.Join(s.p, ".git", "ccnavi-direct", sbsName),
		"version 4\nbase_branch main\nside_by_side "+side+"\nmain_tree "+s.p+"\ncommon_dir "+filepath.Join(s.p, ".git")+"\n")
}

type sbsOpts struct{ restore, event, guard string }

func (s *sbs) hook(cwd string, o ...sbsOpts) run.Result {
	s.t.Helper()
	opt := sbsOpts{"enable", "PostToolUse", "disable"}
	if len(o) > 0 {
		opt = o[0]
		if opt.restore == "" {
			opt.restore = "enable"
		}
		if opt.event == "" {
			opt.event = "PostToolUse"
		}
		if opt.guard == "" {
			opt.guard = "disable"
		}
	}
	body := payload{"hook_event_name": opt.event, "tool_name": "Bash", "tool_input": payload{"command": "make"}, "session_id": "s1", "cwd": cwd}
	return fire(s.t, s.ws, []string{"--mode", "enable", "--restore-if-deny", opt.restore, "--guard-core-files", opt.guard, "--guard-ticket-approval", "disable"}, body, nil)
}

func (s *sbs) dirty(root string) string {
	return write(s.t, filepath.Join(root, "schema", "keep.sql"), "-- changed\n")
}

const unknownOrigin = "出所不明の変更"

func TestWithTheDesignationOnTheUsersCheckoutIsReportedNotRestored(t *testing.T) {
	t.Parallel()
	s := newSBS(t)
	s.record("yes")
	s.hook(s.wt)
	path := s.dirty(s.p)
	r := s.hook(s.wt)
	if read(t, path) != "-- changed\n" {
		t.Error("戻している")
	}
	mustHave(t, r.Stderr, unknownOrigin, "schema/keep.sql", "自分で戻さず")
	mustNotHave(t, r.Stderr, "restored:", "undo:") // 報告の文は戻す手順を並べない
}

func TestTheUsersCheckoutIsWatchedEvenWhenTheAgentRunsInTheWorkWorktree(t *testing.T) {
	t.Parallel()
	s := newSBS(t)
	s.record("yes")
	s.hook(s.wt)
	s.dirty(s.p)
	r := s.hook(s.wt, sbsOpts{restore: "disable"})
	if r.Code != 2 {
		t.Errorf("終了コード = %d, 2 のはず: %s", r.Code, r.Stderr)
	}
	mustHave(t, r.Stderr, "schema/keep.sql")
}

func TestTheWorkspaceItselfAndQAreRestoredAsBefore(t *testing.T) {
	t.Parallel()
	s := newSBS(t)
	s.record("yes")
	s.hook(s.wt)
	wsPath, qPath := s.dirty(s.ws), s.dirty(s.q)
	r := s.hook(s.ws)
	if read(t, wsPath) != "-- keep\n" {
		t.Error("ワークスペースは戻るはず")
	}
	if read(t, qPath) != "-- changed\n" {
		t.Error("q は触ったツリーではないので見ない")
	}
	mustNotHave(t, r.Stderr, unknownOrigin)

	s.hook(s.q)
	qPath = s.dirty(s.q)
	s.hook(s.q)
	if read(t, qPath) != "-- keep\n" {
		t.Error("q は q で動けば戻るはず")
	}
}

func TestWithoutTheDesignationItIsRestoredAsBefore(t *testing.T) {
	t.Parallel()
	s := newSBS(t)
	s.record("no")
	s.hook(s.p)
	path := s.dirty(s.p)
	r := s.hook(s.p)
	if read(t, path) != "-- keep\n" {
		t.Error("戻るはず")
	}
	mustNotHave(t, r.Stderr, unknownOrigin)
}

func TestARemovedWorktreeLeavesTheRecordInvalid(t *testing.T) {
	t.Parallel()
	s := newSBS(t)
	s.record("yes")
	run.Git(t, s.p, "worktree", "remove", "--force", s.wt)
	s.hook(s.p)
	path := s.dirty(s.p)
	s.hook(s.p)
	if read(t, path) != "-- keep\n" {
		t.Error("戻す側に戻るはず")
	}
}

func TestTheDecisionRecordKeepsTheSideBySideCount(t *testing.T) {
	t.Parallel()
	s := newSBS(t)
	s.record("yes")
	s.hook(s.wt)
	s.dirty(s.p)
	s.hook(s.wt)
	mustHave(t, str(lastRecord(t, s.ws), "detail"), "side-by-side 1")
}

func TestATwinNamedTreeDoesNotSplitTheReportFromTheRestoreDecision(t *testing.T) {
	t.Parallel()
	// q の下に p と同じ名前のワークツリーを切る。名前で突き合わせると、戻していない p のユーザのチェックアウトを戻したと言う。
	s := newSBS(t)
	s.record("yes")
	twin := filepath.Join(s.ws, ".claude", "worktrees", "p")
	run.Git(t, s.q, "worktree", "add", "--quiet", twin, "-b", "p")
	s.hook(s.wt)
	pPath := s.dirty(s.p)
	s.dirty(twin)
	r := s.hook(twin)
	if read(t, pPath) != "-- changed\n" {
		t.Error("p のユーザのチェックアウトは戻さない")
	}
	mustHave(t, r.Stderr, unknownOrigin)
}

func TestAChangedContentAfterTheReportIsToldAgain(t *testing.T) {
	t.Parallel()
	s := newSBS(t)
	s.record("yes")
	s.hook(s.wt)
	path := s.dirty(s.p)
	first := s.hook(s.wt)
	again := s.hook(s.wt)
	write(t, path, "-- changed again\n")
	changed := s.hook(s.wt)
	if first.Code != 2 || again.Code != 0 || changed.Code != 2 {
		t.Errorf("終了コード = %d, %d, %d（2, 0, 2 のはず）", first.Code, again.Code, changed.Code)
	}
	mustHave(t, changed.Stderr, unknownOrigin)
}

func TestDryRunKeepsTheSideBySideChangeOutOfTheRestoreSet(t *testing.T) {
	t.Parallel()
	s := newSBS(t)
	s.record("yes")
	s.hook(s.wt, sbsOpts{restore: "dry-run"})
	s.dirty(s.p)
	r := s.hook(s.wt, sbsOpts{restore: "dry-run"})
	mustHave(t, r.Stderr, unknownOrigin)
	mustNotHave(t, r.Stderr, "would-restore")
}

func TestTheEndOfTurnReportAlsoSaysUnknownOrigin(t *testing.T) {
	t.Parallel()
	s := newSBS(t)
	s.record("yes")
	s.hook(s.wt, sbsOpts{event: "UserPromptSubmit"})
	s.dirty(s.p)
	r := s.hook(s.wt, sbsOpts{event: "Stop"})
	mustHave(t, r.Stderr+r.Stdout, unknownOrigin, "schema/keep.sql")
}

func TestSelfProtectedFilesAreRestoredEvenWhileWorkingSideBySide(t *testing.T) {
	t.Parallel()
	s := newSBS(t)
	s.record("yes")
	s.hook(s.wt, sbsOpts{guard: "enable"})
	rulesPath := base.ConfigPath(s.p, "rules")
	before := read(t, rulesPath)
	write(t, rulesPath, jsonText(t, payload{"version": 1, "deny": []any{}, "allow": []any{}}))
	s.hook(s.wt, sbsOpts{guard: "enable"})
	if read(t, rulesPath) != before {
		t.Error("自己防衛の対象は戻るはず")
	}
}
