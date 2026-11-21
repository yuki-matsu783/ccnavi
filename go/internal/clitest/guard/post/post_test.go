package post

// 実行後チェック（PostToolUse・PostToolUseFailure・Stop）の受入テスト。保護領域を持つリポジトリを組み、汚してから
// payload を渡し、返ってきた文と終了コードと記録だけを読む。ワークツリーの実物を見るのがこのチェックの要点なので、
// git は本物を使う。

import (
	"fmt"
	"io/fs"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// postRules は保護領域の宣言。`ask` は報告はされるが戻す対象ではない。allow は実行前チェックで確認を出させないため
// （見たいのは実行後チェックが何を言ったか）。
const postRules = `{
  "version": 1,
  "deny": [
    {"id": "protected", "match": "Write|Edit", "glob": "*/protected/*",
     "message": "protected/ is declared write-deny. Ask the user before changing it."},
    {"id": "push", "match": "Bash", "glob": "*git push*", "message": "git push is not run by the agent."},
    {"id": "approved", "match": "Write|Edit", "glob": "*/.ccnavi/approved/*", "message": "approved tickets are moved by the user."},
    {"id": "proposals", "match": "Write|Edit", "glob": "*/wip/proposals/*", "message": "proposals are moved by the scripts."}
  ],
  "ask": [{"id": "watched", "match": "Write|Edit", "glob": "*/watched/*"}],
  "allow": [{"id": "anything-else", "match": "Bash|Read|Write|Edit|NotebookEdit", "regex": "."}]
}`

const restoreHint = "ccnavi-git.sh restore --staged --worktree -- protected/keep.txt"

// repo は 1 つの作業場所。
type repo struct{ root string }

func (r *repo) path(rel string) string { return filepath.Join(r.root, filepath.FromSlash(rel)) }

// hook は payload を 1 回渡す。モードは環境でなくフラグで固定する（走らせたセッションのモードを読まないため）。
// 自分自身の設定ファイルを守る側は対象も経路も別なので切る（あちらは guard/self が見る）。
func (r *repo) hook(t testing.TB, c call) run.Result {
	t.Helper()
	switch c.Mode {
	case "":
		c.Mode = "enable"
	case "disable":
		// disable だけはフラグから言えない（ワークツリーの中から来た disable は名指しで無視される）。環境から渡す。
		c.EnvMode, c.Mode = "disable", ""
	}
	if c.Restore == "" {
		c.Restore = "disable"
	}
	c.Guard = "disable"
	return fire(t, r.root, c)
}

func (r *repo) bash(t testing.TB, command string) run.Result {
	t.Helper()
	return r.hook(t, call{Input: map[string]any{"command": command}})
}

func (r *repo) dirty(t testing.TB, text ...string) {
	t.Helper()
	body := "changed by a build\n"
	if len(text) > 0 {
		body = text[0]
	}
	write(t, r.path("protected/keep.txt"), body)
}

func (r *repo) keep(t testing.TB) string { return read(t, r.path("protected/keep.txt")) }

// newRepo は保護領域を持つリポジトリ。戻す手順はラッパーの打ち方で書かれる（ラッパーが無いときの文は別に見る）。
func newRepo(t testing.TB) *repo {
	t.Helper()
	root := copyOf(t, "post", func(dir string) {
		git(t, dir, "init", "--quiet")
		write(t, filepath.Join(dir, "protected", "keep.txt"), "committed\n")
		write(t, filepath.Join(dir, "watched", "deps.txt"), "committed\n")
		write(t, filepath.Join(dir, "src", "app.py"), "print(1)\n")
		git(t, dir, "add", "-A")
		git(t, dir, "commit", "--quiet", "-m", "init")
	})
	write(t, base.CommonPath(root, "rules"), postRules)
	write(t, filepath.Join(root, ".ccnavi", "scripts", "ccnavi-git.sh"), "exit 0\n")
	return &repo{root}
}

func TestNothingIsSaidWhenNothingProtectedChanged(t *testing.T) {
	t.Parallel()
	t.Run("unchanged", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		got := r.bash(t, "ls")
		if got.Code != 0 || got.Stdout != "" || str(last(t, r.root), "decision") != "allow" {
			t.Errorf("code=%d stdout=%q last=%v", got.Code, got.Stdout, last(t, r.root))
		}
	})
	t.Run("a change outside the declared places", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.bash(t, "ls")
		write(t, r.path("src/app.py"), "print(2)\n")
		if got := r.bash(t, "python build.py"); got.Code != 0 || got.Stderr != "" {
			t.Errorf("守ると宣言していない場所の変更は対象外: code=%d stderr=%q", got.Code, got.Stderr)
		}
	})
	t.Run("a tool that cannot write", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.bash(t, "ls")
		r.dirty(t)
		got := r.hook(t, call{Tool: "Read", Input: map[string]any{"file_path": "src/app.py"}})
		if got.Code != 0 || str(last(t, r.root), "reason") != "tool-cannot-write" {
			t.Errorf("code=%d last=%v", got.Code, last(t, r.root))
		}
	})
	t.Run("mode disable", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.dirty(t)
		got := r.hook(t, call{Mode: "disable", Input: map[string]any{"command": "python build.py"}})
		if got.Code != 0 || got.Stderr != "" || str(last(t, r.root), "reason") != "mode-disabled" {
			t.Errorf("code=%d stderr=%q last=%v", got.Code, got.Stderr, last(t, r.root))
		}
	})
	t.Run("the records the checker writes itself", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		write(t, r.path(".ccnavi/config/common/rules.yml"), strings.Replace(postRules, `"deny": [`,
			`"deny": [{"id": "records", "match": "Write|Edit", "glob": "*/logs/*", "message": "records are written by ccnavi."},`, 1))
		r.bash(t, "ls")
		if got := r.bash(t, "ls -la"); got.Code != 0 || got.Stderr != "" {
			t.Errorf("自分が書く場所は自分の違反にしない: code=%d stderr=%q", got.Code, got.Stderr)
		}
	})
	t.Run("not a git worktree", func(t *testing.T) {
		t.Parallel()
		plain := &repo{base.TempDir(t)}
		write(t, base.CommonPath(plain.root, "rules"), postRules)
		if got := plain.bash(t, "ls"); got.Code != 0 {
			t.Errorf("見えないことでツールを止めない: code=%d", got.Code)
		}
		// 見えなかったことは記録に残す。残さないと、違反が無かったのか見ていなかったのかが同じ見た目になる。
		row := last(t, plain.root)
		if str(row, "reason") != "worktree-unreadable" || str(row, "detail") != "not-a-git-worktree" {
			t.Errorf("record = %v", row)
		}
	})
}

// 変更を見つけたら、このイベントではツールを取り消せないので、差し戻しの文（exit 2 と標準エラー）だけを返す。
func TestAViolationIsReportedOnceAndAgainWhenItChangesShape(t *testing.T) {
	t.Parallel()
	r := newRepo(t)
	r.bash(t, "ls")
	r.dirty(t)

	got := r.bash(t, "python build.py")

	if got.Code != 2 {
		t.Fatalf("exit 2 と標準エラーがモデルに届く経路: code=%d stderr=%s", got.Code, got.Stderr)
	}
	mustHave(t, got.Stderr,
		"POST_VIOLATION", "protected/keep.txt",
		"rule: .ccnavi/config/common/rules.yml:protected", // どの設定が言っているか
		"Bash(python build.py)",                           // 原因となった直前の実行
		restoreHint,                                       // 戻す手順
		"Ask the user")                                    // ルールが持つ文面。止めるだけでは足りないという道具の目的
	row := last(t, r.root)
	if str(row, "event") != "PostToolUse" || str(row, "decision") != "deny" || row["enforced"] != true ||
		str(row, "code") != "POST_VIOLATION" || strings.Join(strs(row, "paths"), "|") != "changed protected/keep.txt" ||
		strings.Join(strs(row, "rules"), "|") != ".ccnavi/config/common/rules.yml:protected" {
		t.Errorf("record = %v", row)
	}

	if again := r.bash(t, "ls -la"); again.Code != 0 || again.Stderr != "" {
		t.Errorf("1 度伝えた変更を呼び出しのたびに繰り返さない: code=%d stderr=%q", again.Code, again.Stderr)
	}

	remove(t, r.path("protected/keep.txt"))
	gone := r.bash(t, "python clean.py")
	if gone.Code != 2 || !strings.Contains(gone.Stderr, "gone") {
		t.Errorf("中身が変わったことと消えたことは別の出来事: code=%d stderr=%s", gone.Code, gone.Stderr)
	}
}

// セッションが始まる前から在った変更は、直前の実行の結果ではない。原因を付けずに言い、戻さない（他人の書きかけ）。
func TestChangesThatWereThereBeforeAreSaidOnceAndNeverReverted(t *testing.T) {
	t.Parallel()
	t.Run("before the first call, even with restore enabled", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.dirty(t)
		got := r.hook(t, call{Restore: "enable", Input: map[string]any{"command": "ls"}})
		if got.Code != 0 {
			t.Fatalf("直前の実行の結果ではないので差し戻さない: code=%d %s", got.Code, got.Stderr)
		}
		text := additional(t, got)
		mustHave(t, text, "POST_PREEXISTING", "戻さず、これを土台にもせず", "stash push -u -- <頼まれたパス>")
		mustNotHave(t, text, "POST_VIOLATION")
		if row := last(t, r.root); str(row, "decision") != "allow" || str(row, "code") != "POST_PREEXISTING" {
			t.Errorf("判定は allow のまま、文面で知らせた根拠を記録に残す: %v", row)
		}
		r.bash(t, "ls")
		if _, has := last(t, r.root)["code"]; has {
			t.Error("次の回は何も知らせないので code も無い")
		}
		if r.keep(t) != "changed by a build\n" {
			t.Error("他人の書きかけを戻してはいけない")
		}
	})
	t.Run("at the session start", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.dirty(t)
		r.hook(t, call{Event: "SessionStart", Source: "startup"})
		first := r.hook(t, call{Restore: "enable", Input: map[string]any{"command": "ls"}})
		second := r.hook(t, call{Restore: "enable", Input: map[string]any{"command": "ls -la"}})
		if first.Code != 0 || !strings.Contains(additional(t, first), "POST_PREEXISTING") || second.Stdout != "" {
			t.Errorf("1 度だけ伝える: first=%d %q second=%q", first.Code, first.Stdout, second.Stdout)
		}
		if r.keep(t) != "changed by a build\n" {
			t.Error("他人の書きかけを戻してはいけない")
		}
	})
	t.Run("a change that appears after the start is reverted whatever the baseline", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.hook(t, call{Event: "SessionStart", Source: "startup"})
		r.dirty(t)
		got := r.bash(t, "python build.py")
		if got.Code != 2 {
			t.Errorf("code=%d", got.Code)
		}
		mustHave(t, got.Stderr, "POST_VIOLATION", "protected/keep.txt")
	})
	t.Run("more changes than the baseline limit", func(t *testing.T) {
		t.Parallel()
		// 基準を件数で切ると、切った先の在る汚れが「新しい変更」になり、自動復元で消される。
		r := newRepo(t)
		var names []string
		for i := range 520 {
			name := r.path(fmt.Sprintf("protected/gen%03d.txt", i))
			write(t, name, "generated\n")
			names = append(names, name)
		}
		r.hook(t, call{Event: "SessionStart", Source: "startup"})
		r.hook(t, call{Restore: "enable", Input: map[string]any{"command": "ls"}})
		for _, name := range names {
			if !exists(name) {
				t.Fatalf("他人の書きかけを消した: %s", name)
			}
		}
	})
}

// 起動のあとのユーザの発言は、そのとき在る変更をユーザのものとして受け入れる。バックグラウンドの完了通知は受け入れない
// （その間に動いたプロセスの変更はユーザの変更と区別できない）。
func TestTheUsersEditsAreAcceptedAfterAPromptButTheAgentsAreNot(t *testing.T) {
	t.Parallel()
	prompt := func(r *repo, text string) {
		r.hook(t, call{Event: "UserPromptSubmit", Prompt: text})
	}
	start := func(r *repo) { r.hook(t, call{Event: "SessionStart", Source: "startup"}) }
	enforce := func(r *repo, command string) run.Result {
		return r.hook(t, call{Restore: "enable", Input: map[string]any{"command": command}})
	}
	t.Run("edited between turns is not reverted", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		start(r)
		r.dirty(t, "edited by the user\n")
		prompt(r, "続きをお願いします")
		if got := enforce(r, "ls"); got.Code != 0 || r.keep(t) != "edited by the user\n" {
			t.Errorf("ユーザの編集を戻してはいけない: code=%d keep=%q", got.Code, r.keep(t))
		}
	})
	t.Run("a call after accepting reverts only its own change", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		start(r)
		r.dirty(t, "edited by the user\n")
		prompt(r, "続きをお願いします")
		write(t, r.path("protected/new.txt"), "built\n")
		got := enforce(r, "python build.py")
		if !strings.Contains(got.Stderr, "protected/new.txt") || exists(r.path("protected/new.txt")) || r.keep(t) != "edited by the user\n" {
			t.Errorf("stderr=%q keep=%q", got.Stderr, r.keep(t))
		}
	})
	t.Run("a background completion notice is not the user", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		start(r)
		r.dirty(t, "written by a background process\n")
		prompt(r, "<task-notification>\n<status>completed</status>\n</task-notification>")
		if got := enforce(r, "ls"); got.Code != 2 || r.keep(t) != "committed\n" {
			t.Errorf("code=%d keep=%q", got.Code, r.keep(t))
		}
	})
	t.Run("the agent writing over an accepted edit is told, not reverted", func(t *testing.T) {
		t.Parallel()
		// 戻す先のコミット済みの内容はユーザの編集より古いので、戻すとその編集まで消える。だから言うだけにする。
		r := newRepo(t)
		start(r)
		r.dirty(t, "edited by the user\n")
		prompt(r, "続きをお願いします")
		r.dirty(t, "overwritten by the agent\n")
		got := enforce(r, "python build.py")
		if got.Code != 2 {
			t.Errorf("code=%d", got.Code)
		}
		mustHave(t, got.Stderr, "protected/keep.txt", "既にユーザの変更があり")
		if r.keep(t) != "overwritten by the agent\n" {
			t.Error("ユーザの編集まで消さない")
		}
		if again := enforce(r, "ls"); again.Code != 0 || again.Stdout != "" {
			t.Errorf("1 度言えば繰り返さない: code=%d stdout=%q", again.Code, again.Stdout)
		}
	})
}

// 戻すかどうかは `--restore-if-deny`（enable・dry-run・disable）と全体のモードで決まる。
func TestRestoreSettings(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name          string
		mode, restore string
		wantKeep      string // 呼んだあとの protected/keep.txt
		wantCode      int
		have, not     []string // 標準出力と標準エラーに有る・無い文
		wantDetail    string   // 記録の detail に有る文
	}{
		{"enable restores and says so", "", "enable", "committed\n", 2, []string{"restored:"}, nil, ""},
		{"a dry-run restore tells what would be restored", "", "dry-run", "changed by a build\n", 2,
			[]string{"would-restore:", "put it back to its committed content", restoreHint}, nil, "would-restore 1"},
		{"a disabled restore does not pretend to be a dry-run", "", "disable", "changed by a build\n", 2,
			[]string{"POST_VIOLATION"}, []string{"would-restore"}, ""},
		// 書き損じた 1 語で保護が消えるより残るほうがよい。戻す先はコミット済みの内容なので、失われるのは未コミットの変更だけ。
		{"an unreadable value protects", "", "yes", "committed\n", 2, []string{"は設定ではありません"}, nil, ""},
		{"a global dry-run reports only, even when restore is enable", "dry-run", "enable", "changed by a build\n", 0,
			[]string{"POST_VIOLATION", "would-restore:", "protected/keep.txt"}, nil, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			r := newRepo(t)
			r.hook(t, call{Mode: c.mode, Input: map[string]any{"command": "ls"}})
			r.dirty(t)

			got := r.hook(t, call{Mode: c.mode, Restore: c.restore, Input: map[string]any{"command": "python build.py"}})

			if got.Code != c.wantCode || r.keep(t) != c.wantKeep {
				t.Errorf("code=%d keep=%q\n%s", got.Code, r.keep(t), said(got))
			}
			mustHave(t, said(got), c.have...)
			mustNotHave(t, said(got), c.not...)
			detail := str(last(t, r.root), "detail")
			if c.wantDetail != "" {
				mustHave(t, detail, c.wantDetail)
			}
			if len(c.not) > 0 {
				mustNotHave(t, detail, c.not...)
			}
			if c.mode == "dry-run" && last(t, r.root)["enforced"] != false {
				t.Errorf("dry-run は enforced でない: %v", last(t, r.root))
			}
		})
	}
}

// 現れたファイルは消さずに退避する（要るものだったなら拾い直せる）。予行は触らない。
func TestAFileThatAppearedIsMovedAsideNotDeleted(t *testing.T) {
	t.Parallel()
	t.Run("restore enable", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.bash(t, "ls")
		write(t, r.path("protected/generated.env"), "SECRET=1\n")

		got := r.hook(t, call{Restore: "enable", Input: map[string]any{"command": "npm run build"}})

		if exists(r.path("protected/generated.env")) {
			t.Error("退避されていない")
		}
		mustHave(t, got.Stderr, "moved this file to")
		var found []string
		_ = filepath.WalkDir(filepath.Join(r.root, "logs", "state", "aside"), func(p string, d fs.DirEntry, err error) error {
			if err == nil && !d.IsDir() {
				found = append(found, filepath.Base(p))
			}
			return nil
		})
		if len(found) != 1 || found[0] != "generated.env" {
			t.Errorf("退避先に残る = %v", found)
		}
	})
	t.Run("restore dry-run", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.bash(t, "ls")
		write(t, r.path("protected/generated.env"), "SECRET=1\n")
		got := r.hook(t, call{Restore: "dry-run", Input: map[string]any{"command": "npm run build"}})
		if !exists(r.path("protected/generated.env")) {
			t.Error("予行はファイルに触らない")
		}
		mustHave(t, got.Stderr, "would have moved this file aside")
	})
}

// `ask` は「ユーザが 1 度見る場所」の宣言で、「書くな」ではない。戻すと、ユーザが確認に「はい」と答えた編集を
// あとから無かったことにする。報告はする。deny と同じ回に出たら deny だけ戻る。
func TestAnAskPlaceIsReportedButNeverReverted(t *testing.T) {
	t.Parallel()
	watched := func(r *repo) string { return r.path("watched/deps.txt") }
	t.Run("restore enable", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.bash(t, "ls")
		write(t, watched(r), "changed by a build\n")
		got := r.hook(t, call{Restore: "enable", Input: map[string]any{"command": "python build.py"}})
		if read(t, watched(r)) != "changed by a build\n" {
			t.Error("ask の場所は戻さない")
		}
		mustHave(t, got.Stderr, "POST_VIOLATION", "watched/deps.txt",
			"ccnavi-git.sh restore --staged --worktree -- watched/deps.txt", // 戻していないので、戻す手順は載せたままにする
			"not-restored:", "`deny` ではなく `ask` と宣言") // 戻さなかった理由
		mustNotHave(t, got.Stderr, "restored: ccnavi")
	})
	t.Run("restore dry-run does not claim it would restore", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.bash(t, "ls")
		write(t, watched(r), "changed by a build\n")
		got := r.hook(t, call{Restore: "dry-run", Input: map[string]any{"command": "python build.py"}})
		mustHave(t, got.Stderr, "POST_VIOLATION")
		mustNotHave(t, got.Stderr, "would-restore")
		mustNotHave(t, str(last(t, r.root), "detail"), "would-restore")
	})
	t.Run("deny and ask in the same call", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.bash(t, "ls")
		r.dirty(t)
		write(t, watched(r), "changed by a build\n")
		got := r.hook(t, call{Restore: "enable", Input: map[string]any{"command": "python build.py"}})
		if r.keep(t) != "committed\n" || read(t, watched(r)) != "changed by a build\n" {
			t.Errorf("deny の場所は戻り、ask の場所は残る: keep=%q", r.keep(t))
		}
		mustHave(t, got.Stderr, "protected/keep.txt", "watched/deps.txt")
		mustHave(t, str(last(t, r.root), "detail"), "restored 1")
	})
	t.Run("an unreverted change is said once per session and again at the turn end", func(t *testing.T) {
		t.Parallel()
		// 戻していないのでファイルは汚れたまま。呼び出しごとに言えば同じ文が積まれるので、報告はセッションで 1 度きり。
		r := newRepo(t)
		r.bash(t, "ls")
		r.hook(t, call{Event: "UserPromptSubmit"})
		write(t, watched(r), "first\n")
		first := r.hook(t, call{Restore: "enable", Input: map[string]any{"command": "python build.py"}})
		mustHave(t, first.Stderr, "watched/deps.txt")

		write(t, watched(r), "second and different\n")
		again := r.hook(t, call{Restore: "enable", Input: map[string]any{"command": "python build.py"}})

		if again.Stderr != "" {
			t.Errorf("同じ汚れを呼び出しごとには言わない: %q", again.Stderr)
		}
		mustHave(t, str(last(t, r.root), "detail"), "known")
		stop := r.hook(t, call{Event: "Stop", Restore: "enable"})
		mustHave(t, systemMessage(t, stop), "watched/deps.txt")
	})
}

// ターンの終わり（Stop）は、ユーザへ報告するだけで、ターンを続けさせず、戻さない。基準はターンの始まり。
func TestTheTurnEndReportsWhatHappenedDuringTheTurn(t *testing.T) {
	t.Parallel()
	prompt := func(r *repo, mode string) { r.hook(t, call{Event: "UserPromptSubmit", Mode: mode}) }
	cases := []struct {
		name string
		do   func(r *repo)
		mode string
		want []string // 空ならターンの終わりは何も言わない
		why  string   // 何も言わないときの記録の reason
	}{
		{"a change in a protected place", func(r *repo) { prompt(r, ""); r.dirty(t) }, "",
			[]string{"protected/keep.txt", "protected", "ccnavi-git.sh restore"}, ""},
		{"nothing changed", func(r *repo) { prompt(r, "") }, "", nil, ""},
		// ユーザの書きかけ、他のセッションが置いたもの、前のターンで片付けなかったもの。全部並べると、次から読まれなくなる。
		{"changes from before the turn began", func(r *repo) { r.dirty(t); prompt(r, "") }, "", nil, ""},
		// 登録されていない、あるいは基準を読めなかった。見えていない期間を見えたことにしない。
		{"a turn whose start was never seen", func(r *repo) { r.dirty(t) }, "", nil, "no-turn-baseline"},
		// 呼び出しごとの報告は記録を見て繰り返さないが、ユーザはまだ 1 度も見ていないことがある。宛先が違うので記録を共有しない。
		{"a change that was already said per call", func(r *repo) {
			prompt(r, "")
			r.bash(t, "ls")
			r.dirty(t)
			r.bash(t, "python build.py")
		}, "", []string{"protected/keep.txt"}, ""},
		{"a global dry-run still reports", func(r *repo) { prompt(r, "dry-run"); r.dirty(t) }, "dry-run", []string{"protected/keep.txt"}, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			r := newRepo(t)
			c.do(r)

			got := r.hook(t, call{Event: "Stop", Mode: c.mode})

			if got.Code != 0 {
				t.Fatalf("報告のためにターンを続けさせない: code=%d", got.Code)
			}
			if len(c.want) == 0 {
				if got.Stdout != "" {
					t.Errorf("stdout = %q", got.Stdout)
				}
				if c.why != "" && str(last(t, r.root), "reason") != c.why {
					t.Errorf("reason = %v", last(t, r.root))
				}
				return
			}
			mustHave(t, systemMessage(t, got), c.want...)
		})
	}
	t.Run("the record names the event and the code", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		prompt(r, "")
		r.dirty(t)
		r.hook(t, call{Event: "Stop"})
		row := last(t, r.root)
		if str(row, "event") != "Stop" || str(row, "decision") != "deny" || str(row, "code") != "POST_VIOLATION" {
			t.Errorf("record = %v", row)
		}
	})
}

// `git status` はコミットを見せない。ここを足さないと、保護領域を汚してからコミットした回が「何も起きなかった」と
// 同じ見た目になる。数えるのは、ターンの始まりに記録した HEAD から、このツリーが積んだコミットだけ。
func TestTheTurnEndAlsoReportsCommittedChanges(t *testing.T) {
	t.Parallel()
	commit := func(r *repo, rel, text, message string) {
		write(t, r.path(rel), text)
		git(t, r.root, "add", "--", rel)
		git(t, r.root, "commit", "--quiet", "-m", message)
	}
	prompt := func(r *repo) { r.hook(t, call{Event: "UserPromptSubmit"}) }
	stop := func(r *repo) run.Result { return r.hook(t, call{Event: "Stop"}) }

	t.Run("a commit made in the turn", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		prompt(r)
		commit(r, "protected/keep.txt", "changed by a build\n", "汚してからコミットする")
		message := systemMessage(t, stop(r))
		mustHave(t, message, "protected/keep.txt", "コミット済み", "はすでにコミット済みなので", "不要な変更なら取り消してください")
		mustNotHave(t, message, "git restore") // 履歴は書き換えないので、案内できる 1 つが無い
	})
	t.Run("a commit from before the turn is not listed", func(t *testing.T) {
		t.Parallel()
		// 「何も出ない」だけを見ると、コミットを一切見ない実装でも通る。同じターンで 1 件だけ積み、そちらは出ることも見る。
		r := newRepo(t)
		commit(r, "protected/keep.txt", "changed by a build\n", "前のターンのコミット")
		prompt(r)
		commit(r, "protected/later.txt", "このターンのコミット\n", "このターンのコミット")
		message := systemMessage(t, stop(r))
		mustHave(t, message, "protected/later.txt")
		mustNotHave(t, message, "protected/keep.txt")
	})
	t.Run("an approval commit is left out but the rest of the same commit is not", func(t *testing.T) {
		t.Parallel()
		// 承認はユーザが提案を .ccnavi/approved/ へ動かしてコミットする進め方。そこは deny でもあるので、外さないと毎回並ぶ。
		r := newRepo(t)
		prompt(r)
		write(t, r.path(".ccnavi/approved/doing/i0001.md"), "x\n")
		r.dirty(t)
		git(t, r.root, "add", "--", ".ccnavi/approved/doing/i0001.md", "protected/keep.txt")
		git(t, r.root, "commit", "--quiet", "-m", "承認済みチケットと、保護領域の変更")
		message := systemMessage(t, stop(r))
		mustHave(t, message, "protected/keep.txt")
		mustNotHave(t, message, ".ccnavi/approved")
	})
	t.Run("what a merge of the integration branch brought in is not listed", func(t *testing.T) {
		t.Parallel()
		// 二点の差分で数えると、ユーザが統合先で直した保護領域が `merge <統合先>` のたびに並ぶ。
		r := newRepo(t)
		git(t, r.root, "checkout", "--quiet", "-b", "feature")
		prompt(r)
		commit(r, "src/app.py", "print(2)\n", "自分の作業")
		git(t, r.root, "checkout", "--quiet", "master")
		commit(r, "protected/keep.txt", "ユーザが統合先で直した\n", "統合先の変更")
		git(t, r.root, "checkout", "--quiet", "feature")
		git(t, r.root, "merge", "--quiet", "--no-edit", "master")
		if got := stop(r); got.Stdout != "" {
			t.Errorf("取り込んだぶんを自分のターンの成果にしない: %q", got.Stdout)
		}
	})
}

// integrate と integrate-done の後の形（squash のコミット、片付け、記録のコピー）を作る。
func integrateAndFinish(t *testing.T, r *repo) {
	t.Helper()
	home := strings.TrimSpace(git(t, r.root, "rev-parse", "--abbrev-ref", "HEAD"))
	git(t, r.root, "checkout", "--quiet", "-b", "feat")
	r.dirty(t, "ワークツリーで直した\n")
	git(t, r.root, "add", "--", "protected/keep.txt")
	git(t, r.root, "commit", "--quiet", "-m", "ワークツリーの作業")
	tip := strings.TrimSpace(git(t, r.root, "rev-parse", "HEAD"))
	git(t, r.root, "checkout", "--quiet", home)
	baseSha := strings.TrimSpace(git(t, r.root, "rev-parse", "HEAD"))
	git(t, r.root, "merge", "--quiet", "--squash", "feat")
	tree := strings.TrimSpace(git(t, r.root, "write-tree"))
	git(t, r.root, "commit", "--quiet", "-m", "取り込み")
	git(t, r.root, "branch", "--quiet", "-D", "feat")
	write(t, filepath.Join(r.root, ".git", "ccnavi-integrate-done", "feat"),
		"state staged\nbranch feat\nbranch_sha "+tip+"\nbase "+baseSha+"\ntree "+tree+"\n")
}

// 正規の取り込み（squash と git で確かめられたコミット）は並べない。記録が無ければ確かめられないので並べる。
func TestAnIntegrationIsLeftOutOfTheTurnReportOnlyWhenItCanBeVerified(t *testing.T) {
	t.Parallel()
	t.Run("a verified squash is left out", func(t *testing.T) {
		t.Parallel()
		if !mergeTree() {
			t.Skip("git merge-tree --write-tree (git 2.38+) is needed")
		}
		r := newRepo(t)
		r.hook(t, call{Event: "UserPromptSubmit"})
		integrateAndFinish(t, r)
		if got := r.hook(t, call{Event: "Stop"}); got.Stdout != "" {
			t.Errorf("正規の取り込みを違反として並べない: %q", got.Stdout)
		}
	})
	t.Run("a direct commit in the same turn is listed", func(t *testing.T) {
		t.Parallel()
		if !mergeTree() {
			t.Skip("git merge-tree --write-tree (git 2.38+) is needed")
		}
		r := newRepo(t)
		r.hook(t, call{Event: "UserPromptSubmit"})
		integrateAndFinish(t, r)
		write(t, r.path("protected/later.txt"), "直接書いた\n")
		git(t, r.root, "add", "--", "protected/later.txt")
		git(t, r.root, "commit", "--quiet", "-m", "直接のコミット")
		message := systemMessage(t, r.hook(t, call{Event: "Stop"}))
		mustHave(t, message, "protected/later.txt")
		mustNotHave(t, message, "protected/keep.txt")
	})
	t.Run("without the saved record it is listed", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.hook(t, call{Event: "UserPromptSubmit"})
		integrateAndFinish(t, r)
		remove(t, filepath.Join(r.root, ".git", "ccnavi-integrate-done", "feat"))
		mustHave(t, systemMessage(t, r.hook(t, call{Event: "Stop"})), "protected/keep.txt")
	})
}

// ワークツリーを切ってから手を付けるのがこのリポジトリの手順。切ったターンがまるごと数えられないと、その手順を踏むほど
// 穴が大きくなる。基準を持てなかったツリーは、何も言わずに飛ばさず「数えていない」と言う。
func TestAWorktreeCutDuringTheTurnIsCountedOrSaidToBeUncounted(t *testing.T) {
	t.Parallel()
	const uncounted = "今回のターンでこの確認ができませんでした"
	cut := func(t *testing.T, r *repo, extra ...string) string {
		later := r.path(".claude/worktrees/wt1")
		args := append([]string{"worktree", "add", "--quiet"}, extra...)
		if err := gitTry(r.root, append(args, "-b", "wt1", later)...); err != nil {
			t.Skipf("この git ではワークツリーを切れない: %v", err)
		}
		return later
	}
	t.Run("counted from the moment it was touched", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.hook(t, call{Event: "UserPromptSubmit"})
		later := cut(t, r)
		// 切ったあとの 1 回で基準が付く（この呼び出しの行き先がそのツリー）。
		r.hook(t, call{Tool: "Write", Input: map[string]any{"file_path": filepath.Join(later, "src", "app.py")}})
		write(t, filepath.Join(later, "protected", "keep.txt"), "切ったあとに汚してコミット\n")
		git(t, later, "add", "--", "protected/keep.txt")
		git(t, later, "commit", "--quiet", "-m", "切ったあとのコミット")
		message := systemMessage(t, r.hook(t, call{Event: "Stop"}))
		mustHave(t, message, "protected/keep.txt", "wt1")
		mustNotHave(t, message, uncounted)
	})
	t.Run("counted from the branch point when never touched", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.hook(t, call{Event: "UserPromptSubmit"})
		later := cut(t, r)
		write(t, filepath.Join(later, "protected", "keep.txt"), "切ったあとに汚してコミット\n")
		git(t, later, "add", "--", "protected/keep.txt")
		git(t, later, "commit", "--quiet", "-m", "切ったあとのコミット")
		message := systemMessage(t, r.hook(t, call{Event: "Stop"}))
		mustHave(t, message, "protected/keep.txt")
		mustNotHave(t, message, uncounted)
	})
	t.Run("a tree without a baseline is said to be uncounted, along with a violation", func(t *testing.T) {
		t.Parallel()
		r := newRepo(t)
		r.hook(t, call{Event: "UserPromptSubmit"})
		cut(t, r, "--orphan")

		message := systemMessage(t, r.hook(t, call{Event: "Stop"}))
		mustHave(t, message, "ワークツリー wt1では、"+uncounted)
		mustHave(t, str(last(t, r.root), "detail"), "uncounted")

		// detail を上書きすると、先に入った断りが消える。足していく。
		r.dirty(t)
		r.hook(t, call{Event: "Stop"})
		row := last(t, r.root)
		if str(row, "code") != "POST_VIOLATION" || !strings.Contains(str(row, "detail"), "uncounted 1") {
			t.Errorf("record = %v", row)
		}
	})
}

const (
	ticket = "---\nid: i0001\ntitle: \"something\"\nallow:\n  - match: Write|Edit\n    glob: \"src/*\"\n---\nbody\n"
	doing  = ".ccnavi/approved/doing/i0001.md"
)

// チケットの置き場は ccnavi のサブコマンドが書く場所で、同時にプロジェクトが `deny` と宣言した場所でもある。誰が書いたかでなく
// 何が変わったかで見分ける。外さないと自分の手順を自分で違反と報告し、戻す設定では自分で戻して手順が進まなくなる。
// 外しすぎると、承認済みチケットが宣言する範囲を、引数に現れない書き込みで広げる経路ができる。
func TestTicketPlaceChangesAreToldApartByWhatChanged(t *testing.T) {
	t.Parallel()
	newTicketRepo := func(t *testing.T) *repo {
		root := copyOf(t, "post-ticket", func(dir string) {
			git(t, dir, "init", "--quiet")
			write(t, filepath.Join(dir, filepath.FromSlash(doing)), ticket)
			write(t, filepath.Join(dir, "src", "app.py"), "print(1)\n")
			git(t, dir, "add", "-A")
			git(t, dir, "commit", "--quiet", "-m", "init")
		})
		write(t, base.CommonPath(root, "rules"), postRules)
		r := &repo{root}
		r.bash(t, "ls") // 基準を取る。以降に現れたものが、この呼び出しの結果として見られる。
		return r
	}
	script := func(r *repo, restore string) run.Result {
		return r.hook(t, call{Restore: restore, Input: map[string]any{"command": "sh .ccnavi/scripts/ccnavi-ticket.sh start i0001"}})
	}
	t.Run("a field only the script writes is neither said nor reverted", func(t *testing.T) {
		t.Parallel()
		r := newTicketRepo(t)
		write(t, r.path(doing), strings.Replace(ticket, "---\nbody", "started_at: \"2026-09-22T00:00:00Z\"\n---\nbody", 1))
		got := script(r, "enable")
		if got.Code != 0 || said(got) != "" || !strings.Contains(read(t, r.path(doing)), "started_at") {
			t.Errorf("戻されると着手の時刻が消え、手順が進まない: code=%d %q", got.Code, said(got))
		}
	})
	t.Run("a move to the review place is not said", func(t *testing.T) {
		t.Parallel()
		// `ticket finish`。`doing/` から消えて、正規化した内容が同じものが `review/` に現れる。
		r := newTicketRepo(t)
		remove(t, r.path(doing))
		write(t, r.path("wip/proposals/review/i0001.md"), strings.Replace(ticket, "---\nbody", "completed_at: \"2026-09-22T01:00:00Z\"\n---\nbody", 1))
		if got := script(r, ""); got.Code != 0 {
			t.Errorf("code=%d %s", got.Code, said(got))
		}
	})
	t.Run("widening the scope is said, with the note that ccnavi itself may have written it", func(t *testing.T) {
		t.Parallel()
		r := newTicketRepo(t)
		write(t, r.path(doing), strings.Replace(ticket, `glob: "src/*"`, `glob: "*"`, 1))
		got := script(r, "")
		if got.Code != 2 {
			t.Fatalf("code=%d %s", got.Code, said(got))
		}
		mustHave(t, slash(got.Stderr), "POST_VIOLATION", doing, "書いたのはおそらく ccnavi 自身", "undo: は打たないでください", "undo:")
	})
}
