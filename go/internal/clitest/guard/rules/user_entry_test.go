package rules

import (
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// ユーザ専用の入口（`ccnavi user <操作>`）を確かめる（本体は tickets/model/useronly）。
//
//  1. 実行ファイル: `user <操作>` を内部の形に読み替える。`user` の外のユーザ専用のオプションは何も動かさずに断り、見るだけの形は断らない
//  2. hook: `user` の入口と `ccnavi-user-*.sh` を、呼び方に依らず止める。ターミナルを求めない経路（`--yes`）は、名前を変えたコピーでも止める
//
// 表（`user` の操作と、`user` の外で断るオプション）が食い違わないことと、読み替えの細部は useronly の単体テストが確かめる。

const (
	exitBlock          = 2 // 止めたときの終了コード
	exitError          = 1 // 使い方の誤りの終了コード
	codeTicketApproval = "DENY_TICKET_APPROVAL_CLI"
	codeRecordsPrune   = "DENY_RECORDS_PRUNE"
	notTyped           = "直に打つ入口ではありません"
)

// entry は ccnavi を引数だけで 1 回起こす。ルートは決めない（何も無い一時の置き場から起こす）。
func entry(t testing.TB, argv ...string) run.Result {
	t.Helper()
	return run.Ccnavi(t, argv, run.Opts{Dir: base.TempDir(t)})
}

func TestUserOnlyOptionsOutsideTheEntryAreRefused(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		argv  []string
		shown string
	}{
		{[]string{"--agree"}, "ccnavi-user-agree.sh"},
		{[]string{"--agree", "--yes", "i1"}, "ccnavi-user-agree.sh"},
		{[]string{"--reviewed", "0"}, "ccnavi-user-decide.sh"},
		{[]string{"--close-early"}, "ccnavi-user-close-early.sh"},
		{[]string{"--prune"}, "ccnavi-user-prune.sh"},
		{[]string{"--handoff"}, "ccnavi-user-handoff.sh"},
		{[]string{"--handoff", "a.md", "--yes", "--expect", "x"}, "ccnavi-user-handoff.sh"},
		{[]string{"--mirror"}, "ccnavi-user-mirror.sh"},
		{[]string{"--mirror", "lib", "--yes", "--expect", "x"}, "ccnavi-user-mirror.sh"},
		{[]string{"--yes", "i1"}, "ユーザ専用"},
	} {
		t.Run(strings.Join(c.argv, " "), func(t *testing.T) {
			t.Parallel()
			r := entry(t, c.argv...)
			if r.Code != exitBlock {
				t.Errorf("終了コード = %d, %d のはず: %s", r.Code, exitBlock, r.Stderr)
			}
			mustHave(t, r.Stderr, c.shown)
		})
	}
}

func TestReadOnlyFormsAreNotRefused(t *testing.T) {
	t.Parallel()
	for _, argv := range [][]string{
		{"--agree", "--preview", "--json"},
		{"--agree", "--preview", "--verify"},
		{"--prune", "--preview"},
		{"--templates", "--json"},
		{"--docs", "--keyword=--agree"}, // 別のオプションの値は、そのオプションとして読まない
	} {
		t.Run(strings.Join(argv, " "), func(t *testing.T) {
			t.Parallel()
			mustNotHave(t, entry(t, argv...).Stderr, "ユーザ専用")
		})
	}
}

func TestUserWithAnUnknownOperationIsRefused(t *testing.T) {
	t.Parallel()
	for _, argv := range [][]string{{"user", "bogus"}, {"user", "reviewed"}} {
		t.Run(strings.Join(argv, " "), func(t *testing.T) {
			t.Parallel()
			r := entry(t, argv...)
			if r.Code != exitBlock {
				t.Errorf("終了コード = %d: %s", r.Code, r.Stderr)
			}
			mustHave(t, r.Stderr, "user")
		})
	}
}

// 操作の無い `user` は、入口の一覧（名前・1 行の説明・打つ例）を出して終わる。何も動かさない。
func TestUserAloneListsTheEntries(t *testing.T) {
	t.Parallel()
	r := entry(t, "user")
	if r.Code != 0 {
		t.Fatalf("終了コード = %d: %s", r.Code, r.Stderr)
	}
	for _, name := range []string{"agree", "decide", "close-early", "push-approved", "handoff", "mirror", "prune", "clean-branch"} {
		script := "ccnavi-user-" + name + ".sh"
		mustHave(t, r.Stdout, script)
		mustHave(t, r.Stdout, "例: sh .ccnavi/scripts/"+script)
	}
}

func TestANonAsciiSpellingOfTheEntryIsRefused(t *testing.T) {
	t.Parallel()
	for _, argv := range [][]string{{"uſer", "handoff"}, {"user", "handoﬀ"}} {
		t.Run(strings.Join(argv, " "), func(t *testing.T) {
			t.Parallel()
			r := entry(t, argv...)
			if r.Code != exitBlock {
				t.Errorf("終了コード = %d: %s", r.Code, r.Stderr)
			}
			mustHave(t, r.Stderr, "ASCII")
		})
	}
}

func TestWritingThroughTheEntryNeedsATerminal(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		argv []string
		want string
	}{
		{[]string{"--entry", "sh", "user", "handoff", "--yes", "--expect", "x"}, "ccnavi-user-handoff.sh はユーザがターミナルから実行する必要があります"},
		{[]string{"--entry", "sh", "user", "mirror", "--yes", "--expect", "x"}, "ccnavi-user-mirror.sh はユーザがターミナルから実行する必要があります"},
		{[]string{"--entry", "sh", "user", "prune"}, "ccnavi-user-prune.sh はユーザがターミナルから実行する必要があります"},
	} {
		t.Run(c.argv[3], func(t *testing.T) {
			t.Parallel()
			r := entry(t, c.argv...)
			if r.Code == 0 {
				t.Error("成功してはいけない")
			}
			mustHave(t, r.Stderr, c.want)
		})
	}
}

// 入口の印（`--entry sh|board`）。印の無い `user <操作>` は、直に打った形として断る。

func TestAMarkInAnotherPlaceIsLeftForTheArgumentParser(t *testing.T) {
	t.Parallel()
	if r := entry(t, "--entry", "sh", "user", "prune", "--entry", "sh"); r.Code != exitError {
		t.Errorf("終了コード = %d, %d のはず: %s", r.Code, exitError, r.Stderr)
	}
}

func TestTheDecideCheckFormKeepsTheMarkNextToUser(t *testing.T) {
	t.Parallel()
	// decide の sh が打つ事前検査の並び。`--check` は `user reviewed N` の後ろに置く。
	good := entry(t, "--entry", "sh", "user", "reviewed", "1", "--check", "--accept-unresolved")
	mustNotHave(t, good.Stderr, "入口と一緒に")
	if bad := entry(t, "--entry", "sh", "--check", "user", "reviewed", "1", "--accept-unresolved"); bad.Code != exitBlock {
		t.Errorf("終了コード = %d: %s", bad.Code, bad.Stderr)
	}
}

func TestABadMarkIsAnError(t *testing.T) {
	t.Parallel()
	for _, argv := range [][]string{
		{"--entry", "terminal", "user", "agree"},
		{"--entry"},
		{"--entry", "sh"},
		{"--entry", "sh", "agree"},
		{"--entry", "sh", "--entry", "board", "user", "agree"},
	} {
		t.Run(strings.Join(argv, " "), func(t *testing.T) {
			t.Parallel()
			if r := entry(t, argv...); r.Code != exitBlock {
				t.Errorf("終了コード = %d: %s", r.Code, r.Stderr)
			}
		})
	}
}

func TestEveryOperationWithoutAMarkNamesItsScript(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		words  []string
		script string
	}{
		{[]string{"agree"}, "ccnavi-user-agree.sh"},
		{[]string{"reviewed", "3"}, "ccnavi-user-decide.sh"},
		{[]string{"close-early"}, "ccnavi-user-close-early.sh"},
		{[]string{"prune"}, "ccnavi-user-prune.sh"},
		{[]string{"handoff"}, "ccnavi-user-handoff.sh"},
		{[]string{"mirror"}, "ccnavi-user-mirror.sh"},
		{[]string{"handoff", "--json"}, "ccnavi-user-handoff.sh"},
		{[]string{"agree", "--yes", "i1", "--digest", "x", "--json"}, "ccnavi-user-agree.sh"},
	} {
		t.Run(strings.Join(c.words, " "), func(t *testing.T) {
			t.Parallel()
			r := entry(t, append([]string{"user"}, c.words...)...)
			if r.Code != exitBlock {
				t.Errorf("終了コード = %d: %s", r.Code, r.Stderr)
			}
			mustHave(t, r.Stderr, c.script, notTyped)
		})
	}
}

func TestAMarkedEntryGoesOn(t *testing.T) {
	t.Parallel()
	for _, mark := range []string{"sh", "board"} {
		t.Run(mark, func(t *testing.T) {
			t.Parallel()
			r := entry(t, "--entry", mark, "user", "prune")
			mustNotHave(t, r.Stderr, notTyped)
			mustHave(t, r.Stderr, "ターミナル")
		})
	}
}

func TestTheGuardSwitchLetsTheTestsTypeTheEntryDirectly(t *testing.T) {
	t.Parallel()
	r := run.Ccnavi(t, []string{"user", "prune", "--preview"}, run.Opts{Dir: base.TempDir(t), Env: map[string]string{env.GuardTicketApproval: "disable"}})
	mustNotHave(t, r.Stderr, notTyped)
	if r.Code != 0 {
		t.Errorf("終了コード = %d: %s", r.Code, r.Stderr)
	}
}

// hook: 止めるか。

// userHook は rules が空（{"version": 1}）の作業場所に Bash の PreToolUse を渡す。作業場所は呼び出しごとに別にする。
func userHook(t testing.TB, command, agent string) reply {
	t.Helper()
	root := withRuleText(t, `{"version": 1}`)
	mkdirs(t, root+"/.claude")
	body := preBash(command)
	body["cwd"], body["session_id"] = root, "s1"
	if agent != "" {
		body["agent_id"] = agent
	}
	return parse(t, fire(t, root, []string{"--mode", "enable"}, body, nil))
}

// stoppedByApproval は、承認の拒否コードで止めたか。
func stoppedByApproval(r reply) bool {
	return r.Decision == "deny" && strings.Contains(r.Reason, codeTicketApproval)
}

func TestTheEntryIsStoppedWhateverTheForm(t *testing.T) {
	t.Parallel()
	for _, command := range []string{
		"ccnavi user agree",
		"ccnavi user agree i0001-01-01",
		"ccnavi --root /x --cwd /y user reviewed 3 --accept-unresolved",
		"ccnavi --root=/x user close-early --reason r --result /t.json",
		"ccnavi --root '/a b' user handoff",
		"ccnavi.exe USER handoff --json",
		"python -m ccnavi user handoff .ccnavi/docs/a.md --yes --expect x",
		"ccnavi --cwd /y user handoff .claude/settings.json",
		"ccnavi --handoff --json",
		"ccnavi user mirror",
		"ccnavi --root /x user mirror lib --json",
		"ccnavi --mirror --json",
		"uv run python -m ccnavi user close-early",
		".ccnavi/bin/ccnavi -- user agree",
		"sh .ccnavi/scripts/ccnavi-launcher.sh user agree",
		"echo ok && ccnavi us''er agree",
		`ccnavi "user" agree`,
		// 入口の印を付けても、エージェントが打つ形は止まる（印は案内のための印で、守りではない）。
		"ccnavi --entry sh user agree",
		"ccnavi --root /x --entry board user mirror --json",
		"ccnavi --entry=sh user handoff .ccnavi/docs/a.md --yes --expect x",
		"ccnavi --entry sh user close-early --reason r --result /t.json",
		// 値を空の引用符にしたラッパーのオプション（hook が読む前に引用符ごと消えて、`user` が値に見える）。
		"ccnavi --entry sh --root '' user agree",
		`ccnavi --root "" --entry board user mirror`,
		"ccnavi --cwd '' user agree",
		"ccnavi --root '' user handoff --json",
		// 実行するときに決まる語が入口になりうる形。
		"ccnavi $X agree",
		"ccnavi --root /x u${X}ser agree",
		"ccnavi us*r agree",
	} {
		for _, agent := range []string{"", "a1"} {
			t.Run(command+"/agent="+agent, func(t *testing.T) {
				t.Parallel()
				if r := userHook(t, command, agent); r.Decision != "deny" {
					t.Errorf("止めるはず: %s", r.Reason)
				}
			})
		}
	}
}

func TestTheEntryIsStoppedByTheApprovalRule(t *testing.T) {
	t.Parallel()
	r := userHook(t, "ccnavi user agree", "")
	if !stoppedByApproval(r) {
		t.Fatalf("承認の規則で止めるはず: %+v", r)
	}
	mustHave(t, r.Reason, "ccnavi user")
	// prune は自己防衛（コアファイル）の規則に従う。
	if p := userHook(t, "ccnavi user prune", ""); p.Decision != "deny" || !strings.Contains(p.Reason, codeRecordsPrune) {
		t.Errorf("prune は %s で止めるはず: %+v", codeRecordsPrune, p)
	}
}

func TestUserScriptsAreStoppedByTheirNames(t *testing.T) {
	t.Parallel()
	for _, command := range []string{
		"sh .ccnavi/scripts/ccnavi-user-agree.sh",
		"bash -x .ccnavi/scripts/ccnavi-user-forget.sh P",
		"flock /tmp/l sh .ccnavi/scripts/ccnavi-user-close-early.sh --reason r",
		"cd /x && sh /tmp/copy/CCNAVI-USER-DECIDE.SH 3",
		"setsid sh .ccnavi/scripts/ccnavi-user-clean-branch.sh i0001-01-01",
	} {
		for _, agent := range []string{"", "a1"} {
			t.Run(command+"/agent="+agent, func(t *testing.T) {
				t.Parallel()
				if r := userHook(t, command, agent); !stoppedByApproval(r) {
					t.Errorf("止めるはず: %+v", r)
				}
			})
		}
	}
}

// ターミナルを求めない経路は hook の正規表現が唯一の防御なので、名前を変えたコピー・インタプリタ・実行役・展開を含む表記でも止める。
func TestTheBoardPathIsStoppedEvenUnderAnotherName(t *testing.T) {
	t.Parallel()
	for _, command := range []string{
		"/tmp/x user agree --yes i1 --digest d",
		"uv run python main.py user agree --yes i1 --digest d",
		"flock /tmp/l ccnavi user reviewed 3 --accept-unresolved --yes 1",
		`python3 -c "import subprocess;subprocess.run(['c','user','agree','--yes','i1'])"`,
		"/tmp/x user handoff a.md --yes --expect h --via board --json",
		"/tmp/x user mirror lib --yes --expect h --via board --json",
		`python3 -c "import subprocess;subprocess.run(['c','user','mirror','--yes'])"`,
		`python3 -c "import subprocess;subprocess.run(['c','user','handoff','--yes'])"`,
		"/tmp/x --agree --yes i1 --digest d",
		// 入口の表記が読めなくても（名前を変えたコピー）、`user` と展開を含む `--` の語が並べば止める。
		"/tmp/x user agree --y${X}es i1",
		"/tmp/x user agree --y$(echo es) i1",
		"/tmp/x user agree --y`echo es` i1",
		`/tmp/x user handoff a.md "--y$X" --expect h`,
		"/tmp/x --root /w user agree --${Y}",
		// 板の印だけでも止める。
		"/tmp/x foo --via board",
		"/tmp/x --handoff a.md --via=board",
		"/tmp/x --via board --json",
		"/tmp/x user handoff a.md --y$X --expect h --via board",
		// インタプリタの選択肢・モジュールの表記・実行役を前に置いた形。
		"python3 -u -m ccnavi user agree",
		"python3 -W ignore -m ccnavi user agree",
		"python3 -Xutf8 -m ccnavi user agree",
		"python3 -Im ccnavi user agree",
		"python3 -m ccnavi.__main__ user agree",
		"/usr/bin/python3.12 -B -m ccnavi user reviewed 3",
		"python3 main.py user handoff",
		"py -3 -m ccnavi user agree",
		"python3 -u -m ccnavi ticket finish i0001",
		"uv run --with x --project /p python3 -u -m ccnavi user agree",
		"uv run main.py user close-early",
		"poetry run ccnavi user agree",
		"poetry run ccnavi $X agree",
		"python3 -I -m ccnavi user agree",
		"python3 -OO -m ccnavi user agree",
		"python3 -Wignore -m ccnavi user agree",
		"python3 -X utf8 -m ccnavi user agree",
		"python3 -mccnavi user agree",
		"py -m ccnavi user agree",
		"python3 /repo/main.py user handoff",
		"python3 -u -m ccnavi $X agree",
		"uv run -m ccnavi user agree",
		"uv run --frozen python -m ccnavi user agree",
		"poetry run python -m ccnavi user agree",
		"pipenv run python -m ccnavi user agree",
	} {
		t.Run(command, func(t *testing.T) {
			t.Parallel()
			if r := userHook(t, command, ""); !stoppedByApproval(r) {
				t.Errorf("止めるはず: %+v", r)
			}
		})
	}
}

func TestAnExpandedGuardVariableNameIsStopped(t *testing.T) {
	t.Parallel()
	for _, command := range []string{
		"env CCNAVI_GUARD_TICKET_${X}APPROVAL=disable /tmp/x user agree",
		"env CCNAVI_GUARD_${X}=disable /tmp/x user agree --yes i1",
		"export CCNAVI_GUARD_TICKET_$(printf APPROVAL)=disable",
		"env CCNAVI_GUARD_TICKET_`echo APPROVAL`=disable /tmp/x",
	} {
		t.Run(command, func(t *testing.T) {
			t.Parallel()
			if r := userHook(t, command, ""); !stoppedByApproval(r) {
				t.Errorf("止めるはず: %+v", r)
			}
		})
	}
}

func TestReadingIsNotStopped(t *testing.T) {
	t.Parallel()
	for _, command := range []string{
		"ccnavi --docs --keyword user",
		"ccnavi --keyword user --docs",
		"ccnavi --agree --preview --json",
		"ccnavi --agree --preview --verify",
		"ccnavi --prune --preview",
		"grep -rn 'user agree' docs",
		"grep -rn 'user handoff' docs",
		"cat docs/30_adr/rules/0143-user-only-entries-are-separate.md",
		"ccnavi --lint",
		"ccnavi --templates --json",
		"ccnavi --templates --draft i0001",
		"python3 -u -m ccnavi --agree --preview --json",
		"python3 -m ccnavi --prune --preview",
		"uv run python -m ccnavi --docs --keyword user",
		"python3 -m unittest discover -s tests/guard",
		"uv run --frozen python -m pytest -q",
		"py -m pip list",
		"python3 build.py",
		"echo user --x=$Y",
		"echo $CCNAVI_ROOT$X",
		"ls ${CCNAVI_HOME}",
		"sh .ccnavi/scripts/ccnavi-ticket.sh status",
		"sh .ccnavi/scripts/ccnavi-review.sh decide 3 --preview",
	} {
		t.Run(command, func(t *testing.T) {
			t.Parallel()
			r := userHook(t, command, "")
			mustNotHave(t, r.Reason, codeTicketApproval, codeRecordsPrune)
		})
	}
}
