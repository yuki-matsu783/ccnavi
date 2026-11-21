package setup

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/shkit"
	"ccnavi/internal/infra/env"
)

// 設定ファイル（.claude/settings.json）の書き方。

var (
	guardEnv = []string{"CCNAVI_RESTORE_IF_DENY", "CCNAVI_GUARD_CORE_FILES"} // 戻す働きの 2 つ。モードと同じ強さで並ぶ
)

const (
	ticketApprovalEnv = "CCNAVI_GUARD_TICKET_APPROVAL"
	unwatchedEnv      = "CCNAVI_GUARD_UNWATCHED"
)

// 設定ファイルが無いところに、引数なしで 1 回入れて、env と hook の値そのものを見る。「ccnavi という字が入っている」だけでは
// 取り違えを見つけられないので、hook に書かれる 1 行・env の値（導入先の既定 dry-run）・戻す働きと承認の切り替えの強さまで見る。
func TestSetupWritesEnvAndAllHooks(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup()
	code(t, r, 0)
	e := w.envOf()
	for _, name := range []string{"CCNAVI_MODE", "CCNAVI_ENTRY_PATH"} {
		if _, ok := e[name]; !ok {
			t.Errorf("env に %s が無い", name)
		}
	}
	// 指すのは .ccnavi/scripts/ の振り分けの sh で、どの環境でも 1 行のまま。
	eq(t, "CCNAVI_ENTRY_PATH", e["CCNAVI_ENTRY_PATH"], any(binPath))
	// 戻す働きは CCNAVI_MODE と同じ強さで並ぶ。dry-run の導入先で勝手にファイルが戻らない。
	for _, name := range guardEnv {
		eq(t, name, e[name], any("dry-run"))
	}
	// この 3 つは enable か disable しか取らないので、dry-run で導入しても enable で書く。
	for _, name := range []string{ticketApprovalEnv, unwatchedEnv, ticketControl} {
		eq(t, name, e[name], any("enable"))
	}
	data := w.settings()
	for _, event := range events {
		others := append([]string{fetchCommand}, idleCommands[event]...)
		var main []jsonObj
		for _, h := range hooksOf(data, event) {
			if !containsString(others, h["command"].(string)) {
				main = append(main, h)
			}
		}
		if len(main) != 1 {
			t.Errorf("%s の hook が 1 つでない: %v", event, main)
			continue
		}
		eq(t, event+" の command", main[0]["command"], any(hookCommand))
		eq(t, event+" の type", main[0]["type"], any("command"))
		eq(t, event+" の timeout", main[0]["timeout"], any(float64(10)))
	}
}

func containsString(list []string, want string) bool {
	for _, s := range list {
		if s == want {
			return true
		}
	}
	return false
}

func TestSetupEnableModeIsFollowedByThePathsAndTheGuards(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.setup("--mode", "enable")
	e := w.envOf()
	eq(t, "CCNAVI_MODE", e["CCNAVI_MODE"], any("enable"))
	eq(t, "CCNAVI_ENTRY_PATH", e["CCNAVI_ENTRY_PATH"], any(binPath))
	// 戻す働きの 2 つは片方だけが残らない。
	for _, name := range guardEnv {
		eq(t, name, e[name], any("enable"))
	}
	eq(t, ticketApprovalEnv, e[ticketApprovalEnv], any("enable"))
}

// 取り込みは SessionStart に 1 つだけ。他のイベントには載せない。打ち直しても増えない。
func TestSetupRegistersTheFetchAtSessionStartOnly(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.setup()
	data := w.settings()
	for _, event := range events {
		n := 0
		for _, c := range commandsOf(data, event) {
			if c == fetchCommand {
				n++
			}
		}
		want := 0
		if event == "SessionStart" {
			want = 1
		}
		if n != want {
			t.Errorf("%s の取り込み = %d, want %d", event, n, want)
		}
	}
	for _, h := range hooksOf(data, "SessionStart") {
		if h["command"] == fetchCommand {
			eq(t, "取り込みの timeout", h["timeout"], any(float64(60)))
		}
	}
	w.setup()
	if n := len(hooksOf(w.settings(), "SessionStart")); n != 2 {
		t.Errorf("打ち直したあとの SessionStart の hook = %d, want 2", n)
	}
}

func TestSetupNoFetchLeavesTheFetchOutAndCheckAgrees(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.setup("--no-fetch")
	if got := commandsOf(w.settings(), "SessionStart"); !reflect.DeepEqual(got, []string{hookCommand}) {
		t.Errorf("SessionStart = %v", got)
	}
	notIn(t, w.setup("--no-fetch", "--check").Stdout, "取り込み")
}

// 取り込みだけが先に在っても、本体の SessionStart を「別の表記」と取り違えない。
func TestSetupAFetchRegisteredFirstDoesNotHideTheMainHook(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.writeSettings(jsonObj{"hooks": jsonObj{"SessionStart": []any{
		jsonObj{"matcher": "", "hooks": []any{jsonObj{"type": "command", "command": fetchCommand}}},
	}}})
	w.setup()
	if got := commandsOf(w.settings(), "SessionStart"); !reflect.DeepEqual(got, []string{fetchCommand, hookCommand}) {
		t.Errorf("SessionStart = %v", got)
	}
}

// 共通レイヤーの 3 本は `.ccnavi/config/common/` 固定なので、env には書かない（`--all` の一覧にも）。読まれない語が設定に残ると、
// そこを直せば置き場が動くと読める。
func TestSetupDoesNotWriteTheCommonLayerPaths(t *testing.T) {
	t.Parallel()
	for _, args := range [][]string{{"--mode", "enable"}, {"--mode", "enable", "--all"}} {
		w := newTarget(t)
		w.setup(args...)
		for _, name := range []string{"CCNAVI_RULES", "CCNAVI_PHASES", "CCNAVI_RISK"} {
			if _, ok := w.envOf()[name]; ok {
				t.Errorf("%v: env に %s がある", args, name)
			}
		}
	}
}

// 全体ルールだけで足りるプロジェクトは、導入のときに disable を選ぶ。知らない値は 2 で断り、何も書かない。
// 置き換えるのは、この実行で名指しした値だけ。
func TestSetupTicketControl(t *testing.T) {
	t.Parallel()
	t.Run("導入のときに disable を選べる", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		w.setup("--ticket-control", "disable")
		eq(t, ticketControl, w.envOf()[ticketControl], any("disable"))
	})
	t.Run("知らない値は断る", func(t *testing.T) {
		t.Parallel()
		for _, value := range []string{"off", "dry-run", "Enable"} {
			w := newTarget(t)
			code(t, w.setup("--ticket-control", value), 2)
			if shkit.Exists(w.settingsPath()) {
				t.Errorf("%s: 設定を書いた", value)
			}
		}
	})
	t.Run("--force は名指ししたときだけ置き換える", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		w.writeSettings(jsonObj{"env": jsonObj{ticketControl: "disable"}})
		w.setup("--force", "--mode", "enable")
		eq(t, ticketControl, w.envOf()[ticketControl], any("disable"))
		w.setup("--force", "--ticket-control", "enable")
		eq(t, ticketControl, w.envOf()[ticketControl], any("enable"))
	})
}

// 登録しただけでは動かないので、ユーザが置くものを挙げる。振り分けの sh は代わりに通る sh と同じ一覧に出る。
func TestSetupSaysWhatIsStillMissing(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup()
	const heading = "まだ無いもの"
	wantListed(t, r.Stdout, heading, binPath)
	wantListed(t, r.Stdout, heading, ".ccnavi/bin/"+thisMachine()+"/ccnavi")
	wantListed(t, r.Stdout, heading, ".ccnavi/config/common/rules.yml")
	for _, name := range deployScripts {
		wantListed(t, r.Stdout, heading, ".ccnavi/scripts/"+name)
	}
}

// PATH の先頭に ccnavi を置いた環境。bin/ に実行ファイルが無くても、振り分けの sh が PATH の ccnavi を起動するので「まだ無いもの」に
// 出さない。ccnavi という名前で張った振り分けの sh へのリンクは、起動すると自分に戻るので無いものとして数える。
func TestSetupACcnaviOnPathCountsAsARunnableBuildUnlessItIsTheLauncher(t *testing.T) {
	t.Parallel()
	notWindows(t)
	machineBuild := ".ccnavi/bin/" + thisMachine() + "/ccnavi"
	t.Run("PATH の ccnavi は実行できる実体", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		place := base.TempDir(t)
		shkit.WriteExec(t, filepath.Join(place, "ccnavi"), "#!/bin/sh\n")
		r := w.setupEnv(map[string]string{env.Path: pathWith(place)})
		wantNotListed(t, r.Stdout, "まだ無いもの", machineBuild)
	})
	t.Run("launcher へのリンクは実体ではない", func(t *testing.T) {
		t.Parallel()
		if !base.CanSymlink() {
			t.Skip("シンボリックリンクを作れない")
		}
		w := newTarget(t)
		launcher := shkit.WriteExec(t, w.path(strings.Split(binPath, "/")...), "#!/bin/sh\n")
		place := base.TempDir(t)
		symlink(t, launcher, filepath.Join(place, "ccnavi"))
		r := w.setupEnv(map[string]string{env.Path: pathWith(place)})
		wantListed(t, r.Stdout, "まだ無いもの", machineBuild)
	})
}

// 2 回目は同じ形に落ち着き、hook が二重にならない。
func TestSetupRunningTwiceChangesNothing(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.setup()
	first := w.settings()
	r := w.setup()
	code(t, r, 0)
	in(t, r.Stdout, "書き足すものはありません")
	if !reflect.DeepEqual(w.settings(), first) {
		t.Error("2 回目で設定が変わった")
	}
	if n := len(commandsOf(first, "PreToolUse")); n != 1 {
		t.Errorf("PreToolUse の hook = %d, want 1", n)
	}
}

// ccnavi と関係のない hook・env・権限を消さない。導入は既にあるプロジェクトに対して打つものなので、消してしまうと入れた時点で、
// そのプロジェクトの他の道具が止まる。
func TestSetupKeepsSettingsThatHaveNothingToDoWithCcnavi(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.writeSettings(jsonObj{
		"env":         jsonObj{"PYTHONUTF8": "1"},
		"permissions": jsonObj{"deny": []any{"Read(**/.env*)"}},
		"hooks": jsonObj{"PostToolUse": []any{
			jsonObj{"matcher": "Write", "hooks": []any{jsonObj{"type": "command", "command": "sh lint.sh"}}},
		}},
	})
	w.setup()
	data := w.settings()
	eq(t, "PYTHONUTF8", w.envOf()["PYTHONUTF8"], any("1"))
	if !reflect.DeepEqual(data["permissions"], any(map[string]any{"deny": []any{"Read(**/.env*)"}})) {
		t.Errorf("permissions = %v", data["permissions"])
	}
	cmds := commandsOf(data, "PostToolUse")
	if !containsString(cmds, "sh lint.sh") || len(cmds) != 2 {
		t.Errorf("PostToolUse = %v", cmds)
	}
}

// バックアップは最初の 1 回だけ取る。毎回取り直すと、戻れるのは 1 手前までになり、入れる前の状態へ戻す手立てが消える。
func TestSetupKeepsTheCopyItTookBeforeTheFirstChange(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.writeSettings(jsonObj{"env": jsonObj{"MY_IMPORTANT": "keep"}})
	w.setup("--mode", "enable")
	w.setup("--mode", "enable", "--all")
	saved := parseJSON(t, shkit.Read(t, w.settingsPath()+".bak"))
	if !reflect.DeepEqual(saved, jsonObj{"env": map[string]any{"MY_IMPORTANT": "keep"}}) {
		t.Errorf("バックアップ = %v", saved)
	}
}

// `disable` は書かない。監視される側が書けるファイルから監視を止める形になる。
func TestSetupRefusesDisable(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--mode", "disable")
	code(t, r, 2)
	in(t, r.Stderr, "disable")
	if shkit.Exists(w.settingsPath()) {
		t.Error("設定を書いた")
	}
}

// 揃っているように見えて防御が消えている形を、何も言わずに見逃さない。値が違うだけのときも、--check は「揃っていない」と言う。
func TestSetupReportsAValueItWillNotReplace(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.setup("--mode", "dry-run")
	r := w.setup("--mode", "enable")
	code(t, r, 0)
	in(t, r.Stdout, "CCNAVI_MODE", "--force")
	eq(t, "CCNAVI_MODE", w.envOf()["CCNAVI_MODE"], any("dry-run"))
	checked := w.setup("--mode", "enable", "--check")
	code(t, checked, 1)
	in(t, checked.Stdout, "CCNAVI_MODE")
}

// 置き換えるのは、この実行で名指しした値だけ。--force が既定値まで書き込むと、`--all` を足しに来た打ち直しが、
// 指定していない CCNAVI_MODE を既定の dry-run に戻す。
func TestSetupKeepsAnExistingValueUnlessTheOptionIsNamed(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.setup("--mode", "enable")
	w.setup("--all", "--force")
	eq(t, "CCNAVI_MODE", w.envOf()["CCNAVI_MODE"], any("enable"))
	w.setup("--mode", "dry-run", "--force")
	eq(t, "CCNAVI_MODE", w.envOf()["CCNAVI_MODE"], any("dry-run"))
}

// --check は書かない。揃っていなければ終了コードで言う。
func TestSetupCheckReportsWithoutWriting(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--check")
	code(t, r, 1)
	in(t, r.Stdout, "CCNAVI_ENTRY_PATH", "SubagentStop")
	if shkit.Exists(w.settingsPath()) {
		t.Error("--check が設定を書いた")
	}
	w.setup()
	code(t, w.setup("--check"), 0)
}

// すでに ccnavi が登録されているイベントには足さない（表記は決め打ちにできないので、名前が入っているかどうかで見る）。
// 別の表記で登録されているイベントは足さずにユーザへ見せる。部分一致が無関係な hook にたまたま入っているだけなら登録する
// （部分一致だけで見ていると、その 1 件でイベントが丸ごと外れ、設定 lint も PreToolUse の登録を検査していない）。
func TestSetupReadsTheRegistrationCarefully(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, event, command string
		check                func(t *testing.T, w *target, r string)
	}{
		{"同じ名前で登録済みなら 2 本目を足さない", "PreToolUse", "bin/CCNAVI", func(t *testing.T, w *target, r string) {
			if got := commandsOf(w.settings(), "PreToolUse"); !reflect.DeepEqual(got, []string{"bin/CCNAVI"}) {
				t.Errorf("PreToolUse = %v", got)
			}
		}},
		{"別の表記は足さずにユーザへ見せる", "PreToolUse", "sh /src/ccnavi-app/lint.sh", func(t *testing.T, w *target, r string) {
			in(t, r, "PreToolUse", "別の表記")
			if n := len(commandsOf(w.settings(), "PreToolUse")); n != 1 {
				t.Errorf("PreToolUse の hook = %d, want 1", n)
			}
		}},
		{"名前が部分一致するだけなら登録する", "Stop", "echo my-ccnavigation-tool", func(t *testing.T, w *target, r string) {
			if !containsString(commandsOf(w.settings(), "Stop"), hookCommand) {
				t.Error("Stop に本体の hook が登録されていない")
			}
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w := newTarget(t)
			w.writeSettings(jsonObj{"hooks": jsonObj{c.event: []any{
				jsonObj{"matcher": "", "hooks": []any{jsonObj{"type": "command", "command": c.command}}},
			}}})
			r := w.setup()
			c.check(t, w, r.Stdout)
		})
	}
}

// 読めない設定ファイル・想定と違う型には書かず、jq の生のエラーではなく 2 で断る（失敗した先の終了コード 5 は宣言していない値）。
// 不正な JSON を空のオブジェクトとして扱うと、書き損じたカンマ 1 つでそのプロジェクトの設定が丸ごと消える。
func TestSetupRefusesASettingsFileItCannotHandle(t *testing.T) {
	t.Parallel()
	raws := []string{
		`{"env": {,}`,
		`{"env": ["A=1"]}`, `{"env": "nope"}`, `{"hooks": ["PreToolUse"]}`, `{"hooks": {"PreToolUse": "run-me"}}`,
		`{"hooks": {"Stop": ["oops"]}}`, `{"hooks": {"Stop": [{"hooks": "x"}]}}`, `{"hooks": {"Stop": [{"hooks": [{"command": 42}]}]}}`,
		"null", "[]", `"just a string"`,
	}
	for _, raw := range raws {
		w := newTarget(t)
		w.writeSettings(raw)
		r := w.setup()
		code(t, r, 2)
		in(t, r.Stderr, "ccnavi-setup:")
		eq(t, "設定の中身", shkit.Read(t, w.settingsPath()), raw)
	}
}

// `hooks` を持たないエントリは、不正ではない。そのまま扱う。
func TestSetupAcceptsAnEntryWithoutAHooksKey(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.writeSettings(jsonObj{"hooks": jsonObj{"PreToolUse": []any{jsonObj{"matcher": "Bash"}}}})
	code(t, w.setup(), 0)
	if !containsString(commandsOf(w.settings(), "PreToolUse"), hookCommand) {
		t.Error("PreToolUse に登録されていない")
	}
}

// `.claude` が普通のファイルなら、jq を回す前に断る（あとで mkdir が失敗すると、終了コード 1（--check の「揃っていない」）と衝突する）。
func TestSetupRefusesWhenTheClaudeDirectoryIsAFile(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	shkit.Write(t, w.path(".claude"), "not a directory\n")
	r := w.setup()
	code(t, r, 2)
	in(t, r.Stderr, "ccnavi-setup:")
}

func TestSetupReadsItsArguments(t *testing.T) {
	t.Parallel()
	t.Run("--help は 0", func(t *testing.T) {
		t.Parallel()
		r := newTarget(t).raw("--help")
		code(t, r, 0)
		in(t, r.Stdout, "--mode")
	})
	t.Run("知らないオプション・値の無いオプションは 2", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		code(t, w.setup("--nope"), 2)
		code(t, w.raw("--mode"), 2)
	})
	t.Run("知らないモードは 2 で、何も書かない", func(t *testing.T) {
		t.Parallel()
		for _, mode := range []string{"off", "Enable", "yes"} {
			w := newTarget(t)
			code(t, w.setup("--mode", mode), 2)
			if shkit.Exists(w.settingsPath()) {
				t.Errorf("%s: 設定を書いた", mode)
			}
		}
	})
	t.Run("対象のディレクトリは 1 つだけ（空文字を「まだ受け取っていない」と読まない）", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		code(t, w.raw("", w.dir, "--check"), 2)
		code(t, w.raw(w.dir, w.dir), 2)
	})
	t.Run("無いディレクトリは 2", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		code(t, w.raw(w.path("nope")), 2)
	})
	t.Run("-- で終わりにすれば - で始まる名前のディレクトリも対象にできる", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		odd := shkit.Mkdir(t, w.path("-weird"))
		r := w.raw("--", odd)
		code(t, r, 0)
		if !shkit.Exists(filepath.Join(odd, ".claude", "settings.json")) {
			t.Error("設定を書いていない")
		}
	})
}

// 設定ファイルの隣に残骸を置かない（設定なのか残骸なのかをユーザが判断できない）。
func TestSetupLeavesNoTemporaryFile(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.setup()
	if got := shkit.Names(t, w.path(".claude")); !reflect.DeepEqual(got, []string{"settings.json"}) {
		t.Errorf(".claude の中 = %v", got)
	}
}

// 設定を 1 か所で持って張っている置き方を壊さない。`mv` はディレクトリエントリを差し替えるので、リンクが普通のファイルになって
// 実体には何も届かない。
func TestSetupKeepsALinkInsteadOfReplacingIt(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	shared := w.path("real.json")
	writeJSON(t, shared, jsonObj{"env": jsonObj{"KEEP": "yes"}})
	shkit.Mkdir(t, w.path(".claude"))
	if err := os.Link(shared, w.settingsPath()); err != nil {
		t.Skipf("ハードリンクを作れない: %v", err)
	}
	code(t, w.setup("--mode", "enable"), 0)
	landed := parseJSON(t, shkit.Read(t, shared))["env"].(map[string]any)
	eq(t, "CCNAVI_MODE", landed["CCNAVI_MODE"], any("enable"))
	eq(t, "KEEP", landed["KEEP"], any("yes"))
}
