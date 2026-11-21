package self

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// ---- バックアップを取って戻す ----

func TestARewrittenRuleFileIsRestoredToTheContentJustBefore(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("PreToolUse")
	write(t, s.rules, ruleBlob(t, payload{"version": 1, "deny": []any{}}))

	r := s.run("PostToolUse")

	s.rulesAre(selfRules())
	mustHave(t, r.Stdout, "rules.yml", "restored")
}

// ルール由来の保護は、保護領域をルールファイルから導く。deny を空にされるとその一覧ごと消えるので、実行後チェックは何も
// 検知しない。この仕組みはルールを読まずに対象を決めるので、そこで止まらない。
func TestEmptyingTheRulesDoesNotRemoveTheProtection(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("PreToolUse")
	write(t, s.rules, ruleBlob(t, payload{"version": 1, "deny": []any{}, "ask": []any{}, "allow": []any{}}))
	write(t, s.settings, "{}\n")

	s.run("PostToolUse")

	s.rulesAre(selfRules())
	s.settingsAre(selfSettings())
}

// git から戻すとコミット済みの内容まで巻き戻り、ユーザの書きかけが消える。バックアップから戻せば、戻る先はこのツール呼び出しの直前になる。
func TestRestoringToTheSnapshotJustBeforeKeepsUncommittedEdits(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	edited := withRules(payload{"ask": []payload{{"id": "x", "match": "Bash", "regex": "y", "message": "z"}}})
	write(t, s.rules, ruleBlob(t, edited))
	s.run("PreToolUse")
	write(t, s.rules, "{}")

	s.run("PostToolUse")

	s.rulesAre(edited)
}

// ---- 変わっていないバックアップは書き直さない ----

func TestAnUnchangedBackupIsNotRewritten(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("PreToolUse")
	files := s.backupFiles("s1")
	if len(files) == 0 {
		t.Fatal("バックアップが無い")
	}
	s.setAge(3600, files...)
	before := map[string]int64{}
	for _, f := range files {
		info, err := os.Stat(f)
		if err != nil {
			t.Fatal(err)
		}
		before[f] = info.ModTime().UnixNano()
	}

	s.run("PreToolUse")

	for _, f := range files {
		info, err := os.Stat(f)
		if err != nil {
			t.Fatal(err)
		}
		if info.ModTime().UnixNano() != before[f] {
			t.Errorf("%s の更新時刻が変わった", f)
		}
	}
}

func TestAChangedSettingIsBackedUpAgainBeforeTheNextRun(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("PreToolUse")
	edited := withRules(payload{"ask": []any{}})
	write(t, s.rules, ruleBlob(t, edited))
	s.run("PreToolUse")
	write(t, s.rules, "{}")

	s.run("PostToolUse")

	s.rulesAre(edited)
}

func TestAStaleBackupGetsANewTimestampWithoutChangingContent(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("PreToolUse")
	files := s.backupFiles("s1")
	contents := map[string]string{}
	for _, f := range files {
		contents[f] = read(t, f)
	}
	s.setAge(2*86400, files...)

	s.run("PreToolUse")

	for _, f := range files {
		if read(t, f) != contents[f] {
			t.Errorf("%s の中身が変わった", f)
		}
		if s.age(f) > time.Hour {
			t.Errorf("%s の更新時刻が新しくなっていない", f)
		}
	}
}

// ---- 消されたとき・置いていないファイル ----

// 実行前を通らずに消された場合。バックアップの置き場ごと消えた形も同じ。
func TestWithoutABackupARemovedRuleFileComesBackFromGit(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	if err := os.Remove(s.rules); err != nil {
		t.Fatal(err)
	}

	s.run("PreToolUse")

	s.rulesAre(selfRules())
}

// settings.local.json は置かないのが普通。無いことを毎回報告すると、呼び出しのたびに 1 行増えて、本当に言うべき 1 行が埋もれる。
// 現れたら言うが、ユーザが置くこともあるファイルなので消さない。
func TestAnAbsentSettingFileIsNotReportedAndAnAppearingOneIsNotRemoved(t *testing.T) {
	t.Parallel()
	s := newSG(t)

	r := s.run("PreToolUse")
	mustNot(t, r.Stdout, "settings.local.json")
	if r.Stderr != "" {
		t.Errorf("stderr = %q", r.Stderr)
	}

	local := write(t, s.at(".claude/settings.local.json"), `{"hooks": {}}`+"\n")
	r = s.run("PostToolUse")
	if !exists(local) {
		t.Error("ユーザが置くこともあるファイルを消してはいけない")
	}
	mustHave(t, r.Stdout, "settings.local.json")
}

func TestAnAbsentSettingMarkerGetsANewTimestampToo(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("PreToolUse")
	marker := filepath.Join(s.state, "selfguard", "s1", "settings-local.absent")
	if !exists(marker) {
		t.Fatal("置いていない印が無い")
	}
	s.setAge(2*86400, marker)

	s.run("PreToolUse")

	if s.age(marker) > time.Hour {
		t.Error("印の更新時刻が新しくなっていない")
	}
}

// ---- ワークツリー側の設定 ----

func TestRuleFilesInsideAWorktreeAreRestoredAndBackedUpPerWorktree(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	w1, w2 := s.worktree("w1"), s.worktree("w2")
	rulesIn := filepath.Join(w1, ".ccnavi", "config", "common", "rules.yml")
	one, two := filepath.Join(w1, ".claude", "settings.json"), filepath.Join(w2, ".claude", "settings.json")
	write(t, one, ruleBlob(t, payload{"env": payload{"A": "1"}}))
	write(t, two, ruleBlob(t, payload{"env": payload{"B": "2"}}))
	s.run("PreToolUse")
	write(t, rulesIn, ruleBlob(t, payload{"version": 1, "deny": []any{}}))
	write(t, one, "{}\n")
	write(t, two, "{}\n")

	s.run("PostToolUse")

	if got := read(t, rulesIn); !sameJSON(t, got, selfRules()) {
		t.Errorf("ワークツリーの中のルールファイルが戻っていない: %s", got)
	}
	// 取り違えると、片方のワークツリーの内容がもう片方に書き戻される。
	if got := read(t, one); !sameJSON(t, got, payload{"env": payload{"A": "1"}}) {
		t.Errorf("w1 の設定: %s", got)
	}
	if got := read(t, two); !sameJSON(t, got, payload{"env": payload{"B": "2"}}) {
		t.Errorf("w2 の設定: %s", got)
	}
}

// `.claude/worktrees/` の下に在るだけのディレクトリ。参考実装のコピーを置いた形がこれで、保護に行くとユーザのファイルを
// 勝手に戻すことになる。
func TestADirectoryThatIsNotAWorktreeIsNotGuarded(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	copyPath := write(t, s.at(".claude/worktrees/not-a-tree/.claude/settings.json"), "{}\n")
	s.run("PreToolUse")
	write(t, copyPath, `{"changed": true}`+"\n")

	s.run("PostToolUse")

	if got := read(t, copyPath); !sameJSON(t, got, payload{"changed": true}) {
		t.Errorf("ワークツリーでないディレクトリを戻した: %s", got)
	}
}

func TestARemovedWorktreeSettingComesBackBeforeTheRun(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	copyPath := filepath.Join(s.worktree("w1"), ".claude", "settings.json")
	s.run("PreToolUse")
	if err := os.Remove(copyPath); err != nil {
		t.Fatal(err)
	}

	s.run("PreToolUse")

	if got := read(t, copyPath); !sameJSON(t, got, selfSettings()) {
		t.Errorf("戻っていない: %s", got)
	}
}

// バックアップを取る前に書き換えられた回。main の git は .claude/worktrees/ を無視しているので、ワークツリー側の設定の
// コミット済みの内容を持っているのは、そのワークツリー自身の git のほうになる。
func TestWithoutABackupAWorktreeSettingComesBackFromTheWorktreesGit(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	copyPath := filepath.Join(s.worktree("w1"), ".claude", "settings.json")
	write(t, copyPath, "{}\n")

	s.run("PostToolUse")

	if got := read(t, copyPath); !sameJSON(t, got, selfSettings()) {
		t.Errorf("戻っていない: %s", got)
	}
}

// ---- ユーザの発言までの変更は受け入れる ----

func TestSessionStartDoesNotBackUpSettingFilesButTheFirstPromptDoes(t *testing.T) {
	t.Parallel()
	// 開始から最初の発言までは、エージェントが何も実行しない。その間の変更はユーザの手によるものなので、戻す根拠になる
	// バックアップを取らない。
	s := newSG(t)
	s.run("SessionStart")
	saved := filepath.Join(s.state, "selfguard", "s1", "rules")
	if exists(saved) {
		t.Error("セッション開始ではバックアップを取らないはず")
	}

	s.run("UserPromptSubmit", call{Prompt: "お願いします"})

	if read(t, saved) != read(t, s.rules) {
		t.Error("最初の発言でバックアップを取るはず")
	}
}

func TestARuleFileRemovedBetweenSessionStartAndTheFirstPromptIsNotRestored(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("SessionStart")
	if err := os.Remove(s.rules); err != nil {
		t.Fatal(err)
	}

	s.run("UserPromptSubmit", call{Prompt: "お願いします"})
	s.run("PreToolUse")
	s.run("PostToolUse")

	if exists(s.rules) {
		t.Error("ユーザが消したものを戻してはいけない")
	}
}

func TestARuleFileTheUserRemovedStaysRemovedAfterTheNextPrompt(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("SessionStart")
	s.run("UserPromptSubmit", call{Prompt: "一つ目"})
	s.run("PreToolUse")
	s.run("PostToolUse")
	if err := os.Remove(s.rules); err != nil {
		t.Fatal(err)
	}

	s.run("UserPromptSubmit", call{Prompt: "二つ目"})
	s.run("PreToolUse")
	s.run("PostToolUse")

	if exists(s.rules) {
		t.Error("ユーザが消したものを戻してはいけない")
	}
}

func TestARuleFileTheUserRemovedAndPlacedAgainIsGuarded(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("SessionStart")
	s.run("UserPromptSubmit", call{Prompt: "一つ目"})
	if err := os.Remove(s.rules); err != nil {
		t.Fatal(err)
	}
	s.run("UserPromptSubmit", call{Prompt: "二つ目"})
	edited := withRules(payload{"ask": []any{}})
	write(t, s.rules, ruleBlob(t, edited))
	s.run("PreToolUse")
	write(t, s.rules, "{}")

	s.run("PostToolUse")

	s.rulesAre(edited)
}

func TestARuleFileTheUserChangedIsNotRestoredAtTheNextToolCall(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("SessionStart")
	s.run("UserPromptSubmit", call{Prompt: "一つ目"})
	edited := withRules(payload{"ask": []any{}})
	write(t, s.rules, ruleBlob(t, edited))

	s.run("UserPromptSubmit", call{Prompt: "二つ目"})
	s.run("PostToolUse")

	s.rulesAre(edited)
}

// バックグラウンドのプロセスが消したものは、ユーザの削除ではない。完了通知は先頭でなくても通知として扱う。
func TestABackgroundNotificationDoesNotAcceptARemovedRuleFile(t *testing.T) {
	t.Parallel()
	for name, prompt := range map[string]string{
		"完了通知":      "<task-notification>\n<status>completed</status>\n</task-notification>",
		"先頭でない完了通知": "<system-reminder>x</system-reminder><task-notification>",
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			s := newSG(t)
			s.run("SessionStart")
			s.run("UserPromptSubmit", call{Prompt: "一つ目"})
			s.run("PreToolUse")
			if err := os.Remove(s.rules); err != nil {
				t.Fatal(err)
			}

			s.run("UserPromptSubmit", call{Prompt: prompt})
			s.run("PreToolUse")

			if !exists(s.rules) {
				t.Error("プロセスが消したものはユーザの削除ではない")
			}
		})
	}
}

// 失敗した呼び出しも、失敗する前に変えていることがある。実行後の照合を通さないと、次のユーザの発言で「ユーザが消した」ものとして
// 受け入れられる。
func TestARuleFileRemovedInAFailedCallComesBackRightAfterTheFailure(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("SessionStart")
	s.run("UserPromptSubmit", call{Prompt: "一つ目"})
	s.run("PreToolUse")
	if err := os.Remove(s.rules); err != nil {
		t.Fatal(err)
	}

	s.run("PostToolUseFailure")

	if !exists(s.rules) {
		t.Fatal("失敗の直後に戻るはず")
	}
	s.run("UserPromptSubmit", call{Prompt: "二つ目"})
	s.rulesAre(selfRules())
}

// バックグラウンドのコマンドや予約した繰り返しが、ターンの終わりのあとに消したかもしれない。動かしている作業が残ったターンの
// あとは発言でも受け入れず、作業が残っていない・終わった・再開したときは受け入れる。
func TestRemovedRuleFileAfterATurnWithRunningWork(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name     string
		stops    []payload
		resume   bool
		restored bool
	}{
		{"動かしている作業が残ったターンのあとは発言でも受け入れない", []payload{{"background_tasks": []payload{{"id": "b1", "status": "running"}}}}, false, true},
		{"作業が残っていないターンのあとは発言で受け入れる", []payload{{"background_tasks": []any{}, "session_crons": []any{}}}, false, false},
		{"作業が終わったあとのターンの終わりで印を消す", []payload{{"background_tasks": []payload{{"id": "b1"}}}, {"background_tasks": []any{}}}, false, false},
		{"再開で動かしている作業の印を消す", []payload{{"background_tasks": []payload{{"id": "b1"}}}}, true, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			s := newSG(t)
			s.run("SessionStart")
			s.run("UserPromptSubmit", call{Prompt: "一つ目"})
			s.run("PreToolUse")
			for _, extra := range c.stops {
				s.run("Stop", call{Extra: extra})
			}
			if c.resume {
				s.run("SessionStart", call{Extra: payload{"source": "resume"}})
			}
			if err := os.Remove(s.rules); err != nil {
				t.Fatal(err)
			}

			s.run("UserPromptSubmit", call{Prompt: "二つ目"})

			if c.restored {
				s.rulesAre(selfRules()) // 発言の時点で戻す
			} else if exists(s.rules) {
				t.Error("ユーザの削除として受け入れるはず")
			}
		})
	}
}

func TestAnAcceptedRemovalIsLeftInTheRecord(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("SessionStart")
	s.run("UserPromptSubmit", call{Prompt: "一つ目"})
	if err := os.Remove(s.rules); err != nil {
		t.Fatal(err)
	}

	s.run("UserPromptSubmit", call{Prompt: "二つ目"})

	found := false
	for _, item := range strs(s.lastRecord()["guarded"]) {
		found = found || len(item) > len(":accepted") && item[len(item)-len(":accepted"):] == ":accepted"
	}
	if !found {
		t.Errorf("受け入れで消えたことが記録に無い: %v", s.lastRecord()["guarded"])
	}
}

// ---- 設定で切る・記録 ----

func TestDisableDoesNotBackUpOrRestore(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("PreToolUse", call{Setting: "disable"})
	write(t, s.rules, "{}")

	r := s.run("PostToolUse", call{Setting: "disable"})

	if read(t, s.rules) != "{}" || r.Stdout != "" {
		t.Errorf("何もしないはず: rules=%q stdout=%q", read(t, s.rules), r.Stdout)
	}
}

// dry-run は「呼び出しにもワークツリーにも手を出さない」が約束。守る側だけがその外に出ると、試している最中に誰も頼んでいない
// ファイル操作が起きる。設定の dry-run もモードの dry-run も、戻すはずだったと言うだけで触らない。
func TestDryRunSaysItWouldRestoreButTouchesNothing(t *testing.T) {
	t.Parallel()
	for name, c := range map[string]call{
		"設定が dry-run":  {Setting: "dry-run"},
		"モードが dry-run": {Mode: "dry-run"},
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			s := newSG(t)
			s.run("PreToolUse", c)
			write(t, s.rules, "{}")

			r := s.run("PostToolUse", c)

			if read(t, s.rules) != "{}" {
				t.Error("言うだけで触らないはず")
			}
			mustHave(t, r.Stdout, "would-restore")
		})
	}
}

func TestTheRecordSaysWhatWasDone(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("PreToolUse")
	write(t, s.rules, "{}")
	s.run("PostToolUse")

	line := s.lastRecord()
	guarded, ok := line["guarded"]
	if !ok {
		t.Fatalf("guarded が無い: %v", line)
	}
	found := false
	for _, item := range strs(guarded) {
		found = found || item == "rules:restored"
	}
	if !found {
		t.Errorf("rules:restored が無い: %v", guarded)
	}
}

// ---- バックアップを溜めない ----

func TestOldBackupsAreRemovedAtSessionStart(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	s.run("SessionStart", call{Session: "old"})
	s.run("UserPromptSubmit", call{Session: "old", Prompt: "お願いします"})
	s.backdate(filepath.Join(s.state, "selfguard", "old"))

	s.run("SessionStart", call{Session: "s1"})

	for _, name := range s.sessions() {
		if name == "old" {
			t.Error("古いバックアップが残っている")
		}
	}
}
