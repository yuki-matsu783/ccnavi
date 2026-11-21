package setup

import (
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
	"ccnavi/internal/infra/env"
)

// ユーザだけが打つ sh（`ccnavi-user-*.sh`）の分け方の試験。
//
// ユーザだけが打つ操作（承認・承認の push・取り込み状態の削除・残った指摘の対応方針・早めに閉じる・残った子のブランチの削除）は、
// エージェントも使う sh から `ccnavi-user-*.sh` に分けてある。hook はその名前で止めるので、名前と置き場の決まりが崩れると止めが外れる。
// 見るのは次のとおり。
//
//  1. 書く処理（返信・解決・issue を作る・ユーザの判断を承認の push で送る）は `ccnavi-user-*.sh` の中だけにあり、エージェントも読む
//     共通部（`ccnavi-common*.sh`）は持たない。共通部に置くと `sh -c '. …common….sh; 関数'` の形で、ユーザ専用の sh の名前を出さずに呼べる
//  2. 実行ファイルを起こす `ccnavi_wap_exe` の定義は共通部の 1 か所（と、レビューの共通部の包み）だけ
//  3. 古い入口は、何も動かさずに終了コード 2 で新しい sh を案内する
//  4. どの `ccnavi-user-*.sh` も単体で起動できる（`-h`）。hook の名前の止めに当たることは go test
//     （go/internal/tickets/model/useronly/user_scripts_sh_test.go）が見る

// ユーザの判断をホストやリモートに書く関数。ユーザ専用の sh の中だけに置く。
var writers = []string{
	"accept_reply", "accept_resolve", "accept_threads", "accept_execute", "accept_cleanup",
	"accept_before_place", "create_issue", "post_decision", "carry_human",
}

var (
	definition  = regexp.MustCompile(`(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{`)
	startsUser  = regexp.MustCompile(`(?m)^\s*(?:exec\s+)?sh\s+\S*ccnavi-user-`)
	oldUserForm = regexp.MustCompile(`(?:^|[^\w-])--(?:agree|close-early|prune)\b|(?:^|[^\w-])--reviewed\s+"`)
)

func scriptsDir() string { return base.ScriptsDir() }

func shNames(t testing.TB) []string {
	t.Helper()
	var out []string
	for _, n := range shkit.Names(t, scriptsDir()) {
		if strings.HasSuffix(n, ".sh") {
			out = append(out, n)
		}
	}
	return out
}

// code はコメントの行を除いた本文。
func codeOf(text string) string {
	var out []string
	for _, line := range strings.Split(text, "\n") {
		if !strings.HasPrefix(strings.TrimLeft(line, " \t"), "#") {
			out = append(out, line)
		}
	}
	return strings.Join(out, "\n")
}

func shText(t testing.TB, name string) string {
	t.Helper()
	return codeOf(shkit.Read(t, filepath.Join(scriptsDir(), name)))
}

func defined(t testing.TB, name string) map[string]bool {
	t.Helper()
	out := map[string]bool{}
	for _, m := range definition.FindAllStringSubmatch(shText(t, name), -1) {
		out[m[1]] = true
	}
	return out
}

func userScripts(t testing.TB) []string {
	t.Helper()
	var out []string
	for _, n := range shNames(t) {
		if strings.HasPrefix(n, "ccnavi-user-") {
			out = append(out, n)
		}
	}
	return out
}

func commonScripts(t testing.TB) []string {
	t.Helper()
	var out []string
	for _, n := range base.CommonSh(t, scriptsDir()) {
		out = append(out, n)
	}
	return out
}

func TestUserScriptsExist(t *testing.T) {
	t.Parallel()
	have := userScripts(t)
	for _, want := range []string{
		"ccnavi-user-agree.sh", "ccnavi-user-push-approved.sh", "ccnavi-user-decide.sh", "ccnavi-user-close-early.sh",
		"ccnavi-user-clean-branch.sh", "ccnavi-user-handoff.sh", "ccnavi-user-mirror.sh", "ccnavi-user-prune.sh",
	} {
		if !contains(have, want) {
			t.Errorf("%s が無い", want)
		}
	}
}

func contains(list []string, want string) bool {
	for _, s := range list {
		if s == want {
			return true
		}
	}
	return false
}

// 書く処理は共通部に置かない（共通部に置くと、ユーザ専用の sh の名前を出さずに呼べる）。ユーザ専用の sh の中だけにある。
func TestWritersLiveOnlyInUserScripts(t *testing.T) {
	t.Parallel()
	for _, name := range commonScripts(t) {
		for _, fn := range writers {
			if defined(t, name)[fn] {
				t.Errorf("共通部 %s が %s を持つ", name, fn)
			}
		}
	}
	found := map[string][]string{}
	for _, name := range shNames(t) {
		for fn := range defined(t, name) {
			if contains(writers, fn) {
				found[fn] = append(found[fn], name)
			}
		}
	}
	for _, fn := range writers {
		if len(found[fn]) == 0 {
			t.Errorf("%s の定義が無い", fn)
		}
		for _, where := range found[fn] {
			if !strings.HasPrefix(where, "ccnavi-user-") {
				t.Errorf("%s が %s にある（ユーザ専用の sh の外）", fn, where)
			}
		}
	}
}

// 共通部と、エージェントも使う sh（ccnavi-user- で始まらない sh）は、ユーザ専用の sh を起動しない（案内の文面に名前を出すだけ）。
// エージェントが打てる sh の中から、ユーザ専用の sh を名前を出さずに動かせないようにする。
func TestAgentScriptsDoNotStartAUserScript(t *testing.T) {
	t.Parallel()
	for _, name := range shNames(t) {
		if strings.HasPrefix(name, "ccnavi-user-") {
			continue
		}
		if startsUser.MatchString(shText(t, name)) {
			t.Errorf("%s がユーザ専用の sh を起動している", name)
		}
	}
}

// `ccnavi_wap_exe` の定義は ccnavi-common-write-and-push.sh（起こし方）と ccnavi-common-review.sh（レビューの sh の --cwd つきの包み）
// だけ。各 sh にコピーしない。
func TestTheExecutableRunnerIsDefinedOnce(t *testing.T) {
	t.Parallel()
	var where []string
	for _, name := range shNames(t) {
		if defined(t, name)["ccnavi_wap_exe"] {
			where = append(where, name)
		}
	}
	sort.Strings(where)
	if strings.Join(where, " ") != "ccnavi-common-review.sh ccnavi-common-write-and-push.sh" {
		t.Errorf("ccnavi_wap_exe の定義 = %v", where)
	}
}

// ユーザ専用の操作は `user <操作>` の形で呼ぶ（`user` の外の形は実行ファイルが断る）。
func TestUserScriptsCallTheExecutableThroughTheUserEntry(t *testing.T) {
	t.Parallel()
	for _, name := range userScripts(t) {
		if m := oldUserForm.FindString(shText(t, name)); m != "" {
			t.Errorf("%s が user の外の形で呼ぶ: %q", name, m)
		}
	}
}

// 単体で起こす。実行ファイルはスタブで、呼ばれたら印を残す（古い入口は呼ばないこと）。
type userEntry struct {
	t                        *testing.T
	ws, scripts, called, bin string
}

func newUserEntry(t *testing.T) *userEntry {
	t.Helper()
	haveCommands(t, "sh")
	work := base.TempDir(t)
	u := &userEntry{t: t, ws: filepath.Join(work, "ws"), called: filepath.Join(work, "called")}
	u.scripts = filepath.Join(u.ws, ".ccnavi", "scripts")
	shkit.Mkdir(t, u.scripts)
	for _, name := range shNames(t) {
		copyFile(t, filepath.Join(scriptsDir(), name), filepath.Join(u.scripts, name), 0o644)
	}
	bin := filepath.Join(work, "bin", "ccnavi")
	shkit.WriteExec(t, bin, "#!/bin/sh\nprintf '%s\\n' \"$*\" >>'"+u.called+"'\nexit 0\n")
	u.bin = bin
	return u
}

func (u *userEntry) sh(name string, args ...string) run.Result {
	u.t.Helper()
	return run.Command(u.t, "sh", append([]string{filepath.Join(u.scripts, name)}, args...),
		run.Opts{Dir: u.ws, Env: map[string]string{"CCNAVI_WORKSPACE": u.ws, env.EntryPath: u.bin}})
}

func (u *userEntry) wasCalled() bool { return shkit.Exists(u.called) }

func TestEveryUserScriptStartsAndShowsItsUsage(t *testing.T) {
	t.Parallel()
	u := newUserEntry(t)
	for _, name := range userScripts(t) {
		if name == "ccnavi-user-lib-review.sh" {
			continue
		}
		r := u.sh(name, "-h")
		code(t, r, 0)
		in(t, r.Stdout, "sh .ccnavi/scripts/"+name)
	}
	if u.wasCalled() {
		t.Error("使い方を出すだけなのに実行ファイルを起こした")
	}
}

// 共有の部品は関数を決めるだけで、単体で打っても何もしない。
func TestTheUserLibraryDoesNothingByItself(t *testing.T) {
	t.Parallel()
	u := newUserEntry(t)
	r := u.sh("ccnavi-user-lib-review.sh")
	code(t, r, 0)
	eq(t, "stdout", r.Stdout, "")
	if u.wasCalled() {
		t.Error("部品が実行ファイルを起こした")
	}
}

// hook の止めが ask に下がっても、ccnavi-review.sh 自身が拒否する。実行側の拒否が唯一の守りになる。
func TestTheReviewScriptRefusesTheEntriesThatMovedToUserScripts(t *testing.T) {
	t.Parallel()
	u := newUserEntry(t)
	cases := []struct {
		args    []string
		message string
	}{
		{[]string{"close-early", "--reason", "r"}, "close-early は指定できません"},
		{[]string{"decide", "1", "--choices", "{}", "--digest", "d"}, "decide は --choices を受けない"},
	}
	for _, c := range cases {
		r := u.sh("ccnavi-review.sh", c.args...)
		code(t, r, 2)
		in(t, r.Stderr, c.message)
	}
	if u.wasCalled() {
		t.Error("古い入口が実行ファイルを起こした")
	}
}

func TestTheReviewUsageNamesTheUserScripts(t *testing.T) {
	t.Parallel()
	u := newUserEntry(t)
	r := u.sh("ccnavi-review.sh", "--help")
	code(t, r, 0)
	in(t, r.Stdout, "ccnavi-user-decide.sh", "ccnavi-user-close-early.sh")
	notIn(t, r.Stdout, "close-early  --reason")
}
