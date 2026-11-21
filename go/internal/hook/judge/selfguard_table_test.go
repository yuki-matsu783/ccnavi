package judge

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// 守る場所（設定ファイル・記録と state・実行ファイル）への書き込みを、hook の判定の本体（Before）で止める・通すの表。
// 受入テスト（tests/guard/test_selfguard.py）が hook を 1 件ずつ起こして見ていた命令を、ここで 1 行ずつ見る。

const (
	settingFiles = "builtin:guard-setting-files"
	guardRecords = "builtin:guard-records"
	limitCode    = "DENY_REDIRECT_LIMIT"
	// 生の文字列の `>` の個数の上限（selfguard.RedirectLimit）。
	redirectLimit = 50
)

type guardRow struct {
	tool, subject string
	// stop は rule で止まるはずか。偽なら rule にヒットしないはず。
	stop bool
	rule string
}

func bashRows(stop bool, rule string, commands ...string) []guardRow {
	var out []guardRow
	for _, c := range commands {
		out = append(out, guardRow{config.ToolBash, c, stop, rule})
	}
	return out
}

func checkGuardRows(t *testing.T, root string, env map[string]string, pm string, rows []guardRow) {
	t.Helper()
	for _, r := range rows {
		field := "command"
		switch r.tool {
		case config.ToolNotebookEdit:
			field = "notebook_path"
		case config.ToolWrite, config.ToolEdit:
			field = "file_path"
		}
		a := runGo(t, root, judgeCase{Payload: call(r.tool, root, map[string]any{field: r.subject}, "permission_mode", pm), Env: env})
		hit := strings.Contains(a.Stdout, r.rule)
		if r.stop && (a.Record["decision"] != "deny" || !hit) {
			t.Errorf("%s %q (%s): 止まらない: %v %.300s", r.tool, r.subject, pm, a.Record["decision"], a.Stdout)
		}
		if !r.stop && hit {
			t.Errorf("%s %q (%s): %s にヒットした: %.300s", r.tool, r.subject, pm, r.rule, a.Stdout)
		}
	}
}

func TestSelfGuardShellWrites(t *testing.T) {
	root := fixtureWorkspace(t, "")
	rules := filepath.ToSlash(filepath.Join(root, config.DefaultRules))
	var rows []guardRow
	// sed の書き換えの表記。緩めたのは語の途中の `-i` だけ。
	for _, opt := range []string{"-i", "-i.bak", "-i''", "-iE", "-Ei", "-ni", "--in-place", "--in-place=.bak", "--in", "--i",
		"--in-pl=.bak", "'-i'", `"-i"`} {
		rows = append(rows, bashRows(true, settingFiles,
			"sed "+opt+" s/a/b/ "+rules, "sed -e s/a/b/ "+opt+" "+rules, "sed "+opt+" -e s/a/b/ "+rules)...)
	}
	rows = append(rows, bashRows(false, settingFiles,
		"sed -n 1,20p "+rules,
		"cd .ccnavi/config/common && cat rules.yml",
		"sed -n 1,20p /x/.claude/worktrees/feature-id/.ccnavi/config/common/rules.yml",
		"sed -n 1,20p /x/.claude/worktrees/feature-12-improve/.ccnavi/config/common/rules.yml",
		"sed -E -e s/a-i/b/ "+rules, "sed -ne 's/a-i/b/p' "+rules)...)
	// 空白を含む行き先と、そのほかの書き方。
	rows = append(rows, bashRows(true, settingFiles,
		`echo x > "projects/has space/.ccnavi/config/project/rules.yml"`, `echo x > "a b/.ccnavi/config/common/rules.yml"`,
		`echo x >> "a b/.ccnavi/config/common/rules.yml"`, `echo x >"a b/.ccnavi/config/common/rules.yml"`,
		`echo x > "a(1)/.ccnavi/config/common/rules.yml"`, `echo x > "R&D/.ccnavi/config/common/rules.yml"`,
		`echo x > "a b /.ccnavi/config/common/rules.yml"`, `echo x > "C:\my dir\.ccnavi\config\common\rules.yml"`,
		`echo x >| "a b/.ccnavi/config/common/rules.yml"`, `echo x &> "a b/.ccnavi/config/common/rules.yml"`,
		`echo x 2> "a b/.ccnavi/config/common/rules.yml"`, `echo x >& "a b/.ccnavi/config/common/rules.yml"`,
		`echo x > a\ b/.ccnavi/config/common/rules.yml`, "echo x > \"a\tb/.ccnavi/config/common/rules.yml\"",
		"cat <<EOF > \"a b/.ccnavi/config/common/rules.yml\"\nx\nEOF")...)
	// 引用の中の大なりは、目印が続いても書き込みではない。
	rows = append(rows, bashRows(false, settingFiles,
		`echo "a >b c/.ccnavi/config/common/rules.yml"`, `echo "a  >  b/.ccnavi/config/common/rules.yml"`,
		`echo "1>2" .ccnavi/config/common/rules.yml`, `grep -n "regex: (>" .ccnavi/config/common/rules.yml`,
		`echo x > "a b" .ccnavi/scripts/count.sh`, `echo x > "a .ccnavi/scripts/count.sh"`, `echo x > "a .claude/settings.json"`)...)
	// 記録と state（logs/ の下）と、cd で入ってから書く形。
	rows = append(rows, bashRows(true, settingFiles,
		"rm logs/decisions.jsonl", "rm -rf logs/state", "mv logs/state /tmp/x", "rm -rf logs/archive",
		"cp /tmp/x.md logs/archive/self/done/i0001.md", "cp -r /tmp/archive logs/", "mv /tmp/x/archive logs/", "cp -t logs /tmp/archive",
		"cd .ccnavi/config/common && echo x > rules.yml", "cd .ccnavi && echo x > common/rules.yml",
		"cd .claude && echo x > settings.json", "cd .claude/hooks && echo x > lint-py.sh", "cd logs && rm decisions.jsonl")...)
	checkGuardRows(t, root, baseEnv, "default", rows)
}

func TestSelfGuardRecordsByNamedTools(t *testing.T) {
	root := fixtureWorkspace(t, "")
	at := func(parts ...string) string { return filepath.Join(append([]string{root}, parts...)...) }
	var rows []guardRow
	for _, tool := range []string{config.ToolWrite, config.ToolEdit, config.ToolNotebookEdit} {
		for _, path := range []string{
			at("logs", "state", "denied-x.json"), at("logs", "state", "selfguard", "s1", "settings"), at("logs", "decisions.jsonl"),
			at("logs", "decisions.20260927-120000.jsonl"), at("Logs", "State", "x.json"),
			at("logs", "archive", "self", "done", "i0001.md"), at("logs", "decisions.20260927-120000-2.jsonl"),
		} {
			rows = append(rows, guardRow{tool, path, true, guardRecords})
		}
	}
	for _, path := range []string{
		at("logs", "git-20260913-000000-1.log"), at("logs", "notes.md"), at("logstate", "x.json"), at("logs", "archived-notes.md"),
		at("src", "logs", "catalog.jsonl"), at("state-notes", "x.md"),
	} {
		rows = append(rows, guardRow{config.ToolWrite, path, false, guardRecords})
	}
	checkGuardRows(t, root, baseEnv, "default", rows)
}

// 実行ファイル（CCNAVI_ENTRY_PATH）が行き先なら、コピーする形でも、入っているディレクトリを行き先にしても止める。
// 元の側に出ただけなら止めない。
func TestSelfGuardBinaryDestination(t *testing.T) {
	root := fixtureWorkspace(t, "")
	bin := filepath.Join(root, "dist", "ccnavi", "ccnavi.exe")
	testkit.Put(t, bin, "MZ fake executable\n")
	var rows []guardRow
	rows = append(rows, bashRows(true, settingFiles,
		"cp /tmp/other.exe dist/ccnavi/ccnavi.exe", "ln -sf /tmp/other.exe dist/ccnavi/ccnavi.exe",
		"install /tmp/other.exe dist/ccnavi/ccnavi.exe", "cp /tmp/other.exe dist/ccnavi/ccnavi.exe && echo done",
		"cp /tmp/other.exe dist/ccnavi/ccnavi.exe >/dev/null", "echo go; cp /tmp/other.exe dist/ccnavi/ccnavi.exe",
		`cp "/tmp/a b.exe" dist/ccnavi/ccnavi.exe`, `cp /tmp/other.exe "dist/ccnavi/ccnavi.exe"`,
		"cp /tmp/other.exe dist/ccnavi/ccnavi.exe -f", "sudo cp /tmp/other.exe dist/ccnavi/ccnavi.exe",
		"cp -S -t /tmp/other.exe dist/ccnavi/ccnavi.exe", "mv dist/ccnavi/ccnavi.exe /tmp/keep.exe", "rm dist/ccnavi/ccnavi.exe",
		"tee dist/ccnavi/ccnavi.exe < /tmp/other.exe", "echo x > dist/ccnavi/ccnavi.exe",
		"cp /tmp/x/ccnavi.exe dist/ccnavi/", "cp /tmp/x/ccnavi.exe dist/ccnavi", "mv /tmp/x/ccnavi.exe dist/ccnavi/",
		"cp -t dist/ccnavi /tmp/x/ccnavi.exe", "cp /tmp/x/* dist/ccnavi/", "cd dist/ccnavi && cp /tmp/x/ccnavi.exe .")...)
	rows = append(rows, bashRows(false, settingFiles,
		"cp /tmp/notes.txt dist/ccnavi/", "cp -t dist/ccnavi /tmp/notes.txt",
		"cp dist/ccnavi/ccnavi.exe /tmp/keep.exe", "ln -s dist/ccnavi/ccnavi.exe /tmp/link",
		"install dist/ccnavi/ccnavi.exe /tmp/keep.exe", "cp dist/ccnavi/ccnavi.exe /tmp/keep.exe && ls /tmp",
		"cp -t /tmp dist/ccnavi/ccnavi.exe", "cp dist/ccnavi/ccnavi.exe /tmp/keep.exe -f")...)
	checkGuardRows(t, root, with("CCNAVI_ENTRY_PATH", bin), "default", rows)
}

// 生の文字列の `>` が上限を超えたら、権限モードに依らず保護を当てずに止める（確認には回さない）。上限ちょうどなら
// 生の文字列にも保護を当てる。
func TestSelfGuardRedirectLimitInEveryMode(t *testing.T) {
	root := fixtureWorkspace(t, "")
	over := strings.Repeat(">a", redirectLimit+1)
	protected := "echo " + over + `; echo x > .ccnavi/config/common/rules.yml; echo "`
	unrelated := `echo "` + over
	exact := "echo x > .ccnavi/config/common/rules.yml; echo " + strings.Repeat(">a", redirectLimit-1) + ` "`
	exactUnrelated := `echo "` + strings.Repeat(">a", redirectLimit)
	if strings.Count(exact, ">") != redirectLimit || strings.Count(exactUnrelated, ">") != redirectLimit {
		t.Fatal("上限ちょうどの組み方が違う")
	}
	for _, pm := range []string{"default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"} {
		for _, command := range []string{protected, unrelated} {
			a := runGo(t, root, judgeCase{Payload: call(config.ToolBash, root, map[string]any{"command": command}, "permission_mode", pm), Env: baseEnv})
			if a.Record["decision"] != "deny" || a.Record["code"] != limitCode {
				t.Errorf("%s %.30q: %v %v", pm, command, a.Record["decision"], a.Record["code"])
			}
		}
		checkGuardRows(t, root, baseEnv, pm, bashRows(true, settingFiles, exact))
		checkGuardRows(t, root, baseEnv, pm, bashRows(false, limitCode, exact, exactUnrelated))
	}
}

// bin/ に実体が無く、PATH の ccnavi が起動される形。振り分けの sh だけを置き、実体は PATH の置き場（ワークスペースの外）に置く。
// 守る対象は PATH の実体になる。別の bin/ccnavi は守らず、PATH に ccnavi が無ければ守る対象も増えない。
func TestSelfGuardPathExecutable(t *testing.T) {
	root := fixtureWorkspace(t, "")
	launcher := filepath.Join(root, ".ccnavi", "scripts", "ccnavi-launcher.sh")
	testkit.Put(t, launcher, "#!/bin/sh\n")
	if err := os.Chmod(launcher, 0o755); err != nil {
		t.Fatal(err)
	}
	exe := filepath.Join(testkit.TempRoot(t), "local", "bin", "ccnavi")
	testkit.Put(t, exe, "ELF fake on path\n")
	if err := os.Chmod(exe, 0o755); err != nil {
		t.Fatal(err)
	}
	onPath := with("CCNAVI_ENTRY_PATH", launcher, "PATH", filepath.Dir(exe)+string(os.PathListSeparator)+os.Getenv("PATH"))
	exeDir := filepath.ToSlash(filepath.Dir(exe))
	checkGuardRows(t, root, onPath, "default", []guardRow{
		// 名指しのツールは、どの組み込みが当たったかではなく止まることを見る。
		{config.ToolWrite, exe, true, `"permissionDecision": "deny"`},
		{config.ToolEdit, exe, true, `"permissionDecision": "deny"`},
		{config.ToolBash, "cp /tmp/x " + exeDir + "/ccnavi", true, settingFiles},
		{config.ToolBash, "cp /tmp/x ./bin/ccnavi", false, settingFiles},
	})
	// PATH に ccnavi が無ければ、守る対象も増えない。
	noPath := with("CCNAVI_ENTRY_PATH", launcher, "PATH", "/nonexistent/path")
	checkGuardRows(t, root, noPath, "default", []guardRow{
		{config.ToolBash, "cp /tmp/x ./bin/ccnavi", false, settingFiles},
		{config.ToolBash, "cp /tmp/x " + exeDir + "/ccnavi", false, settingFiles},
	})
}
