package layers

import (
	"encoding/json"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/configws"
	"ccnavi/internal/harness/run"
)

// 共通設定と config が無いとき・不正なときの受入テスト。
//
// ファイルが無いのは「設定が無い」正常で、ルール・リスクの配点・フェーズ定義のすべてで空として扱う。記録の `fallback` にも `--lint` にも出さない。
// 不正なときだけ `fallback` を残す。組み込みの deny は、共通設定と config の有無・状態によらず常にヒットする。
// 土台は configws の雛形。配点の設定の和の細部、組み込みの文面、照合の速さは Go のテスト
// （go/internal/tickets/config/risk・go/internal/policy/builtin・go/internal/policy/rules）が確かめる。

const (
	rmRf          = "rm -rf /tmp/x"
	gitCmd        = "git"
	rawGitBuiltin = "builtin:guard-raw-git"
)

// absentWS は雛形のワークスペースに、共通設定などを取り除く道具を足したもの。
type absentWS struct{ *configws.WS }

func newAbsentWS(t testing.TB) absentWS { return absentWS{configws.NewWS(t, configws.KindBase)} }

func (w absentWS) removeCommonRules() {
	w.T.Helper()
	if err := os.Remove(w.Rules); err != nil {
		w.T.Fatal(err)
	}
}

func (w absentWS) removeOwnLayer() {
	w.T.Helper()
	for _, kind := range []string{"rules", "phases", "risk"} {
		if path := configws.LayerPath(w.Root, kind); configws.Exists(path) {
			if err := os.Remove(path); err != nil {
				w.T.Fatal(err)
			}
		}
	}
}

// recordHas は直近の決定ログの rules に、その名前（組み込みは `builtin:…`）があるか。
func (w absentWS) recordHas(name string) bool {
	return slices.Contains(w.LastRecord().Names(), name)
}

func (w absentWS) fallback() string { return w.LastRecord().Str("fallback") }

func TestAMissingCommonLayerIsEmptyAndIsNotAFallback(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	w.removeCommonRules()

	denied := w.Hook("Write", w.Root, configws.Obj{"file_path": filepath.Join(w.Root, "generated", "x.py")})

	w.AssertDenied(denied, "self:generated")
	if _, ok := w.LastRecord()["fallback"]; ok {
		t.Error("共通設定が無いだけで fallback を残した")
	}
	if strings.Contains(w.Reason(denied), "built-in defaults") {
		t.Error("理由が組み込みの既定の話になった")
	}
}

func TestBuiltinDenyStaysOnWithoutTheCommonLayer(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name, tool, field, value string
	}{
		{"recursive delete", "Bash", "command", rmRf},
		{"git push", "Bash", "command", "git push origin main"},
		{"git reset hard", "Bash", "command", "git reset --hard HEAD~1"},
		{"env file", "Write", "file_path", ".env"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			w := newAbsentWS(t)
			w.removeCommonRules()
			value := tc.value
			if tc.field == "file_path" {
				value = filepath.Join(w.Root, value)
			}
			w.AssertDenied(w.Hook(tc.tool, w.Root, configws.Obj{tc.field: value}))
			rules := w.LastRecord().Names()
			if !slices.ContainsFunc(rules, func(n string) bool { return strings.HasPrefix(n, "builtin:") }) {
				t.Errorf("組み込みの deny にヒットしない: %v", rules)
			}
			if _, ok := w.LastRecord()["fallback"]; ok {
				t.Error("fallback を残した")
			}
		})
	}
}

// 土台の認証情報の止めは、実際の `.env`・`.ssh/` を止め、フィールド参照は巻き込まない。
func TestTheBuiltinCredentialsDenyHasALeadingBoundary(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	w.removeCommonRules()
	for _, c := range []struct{ tool, field, value string }{
		{"Bash", "command", "cat .env"},
		{"Bash", "command", "cat .envrc"},
		{"Bash", "command", "cat x/.env.local"},
		{"Bash", "command", "cat ~/.ssh/id_rsa"},
		{"Read", "file_path", filepath.Join(w.Root, ".env")},
		{"Write", "file_path", filepath.Join(w.Root, "x", ".npmrc")},
	} {
		w.AssertDenied(w.Hook(c.tool, w.Root, configws.Obj{c.field: c.value}), "builtin:credentials")
	}
	for _, command := range []string{"jq -r '.hooks[0],.env.X' a.json", "echo process.env.HOME"} {
		w.Hook("Bash", w.Root, configws.Obj{"command": command})
		if w.recordHas("builtin:credentials") {
			t.Errorf("フィールド参照を認証情報として止めた: %s", command)
		}
	}
}

// 共通設定が在っても、組み込みの deny は土台としてヒットする。
func TestBuiltinDenyStaysOnUnderTheCommonLayer(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	w.AssertDenied(w.Hook("Bash", w.Root, configws.Obj{"command": "git reset --hard HEAD~1"}), "builtin:git-reset-hard")
}

// 共通設定も config も無ければ、組み込みの既定だけで判定する（`fallback` は残さない）。既定は `Read` を通す（allow）。
// その allow は組み込みの既定だけのとき限りで、config が 1 本でも在れば足さない。
func TestTheBuiltinDefaultsDecideWhenNoLayerIsPlaced(t *testing.T) {
	t.Parallel()
	t.Run("without both layers", func(t *testing.T) {
		t.Parallel()
		w := newAbsentWS(t)
		w.removeCommonRules()
		w.removeOwnLayer()

		read := w.Hook("Read", w.Root, configws.Obj{"file_path": filepath.Join(w.Root, "README.md")})
		rec := w.LastRecord()
		if rec.Str("decision") != "allow" || !slices.Equal(rec.Names(), []string{"builtin:read-anything"}) {
			t.Errorf("record = %v\n%s", rec, read.Stdout)
		}
		if _, ok := rec["fallback"]; ok {
			t.Error("fallback を残した")
		}
		w.AssertDenied(w.Hook("Bash", w.Root, configws.Obj{"command": rmRf}), "builtin:recursive-delete")
	})
	t.Run("the read allow is not added when a layer exists", func(t *testing.T) {
		t.Parallel()
		w := newAbsentWS(t)
		w.removeCommonRules()
		configws.Write(t, configws.LayerPath(w.Root, "rules"), configws.Body(configws.Obj{"version": 1, "deny": []configws.Obj{}}))

		w.Hook("Read", w.Root, configws.Obj{"file_path": filepath.Join(w.Root, "README.md")})

		if w.recordHas("builtin:read-anything") {
			t.Error("config が在るのに Read を通す既定を足した")
		}
	})
}

// 共通設定のルールが不正なら、組み込みの既定だけで判定する。`fallback` を残し、`--lint` は error で言う。
func TestABrokenCommonLayerFallsBackToTheBuiltinDefaultsOnly(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	configws.Write(t, w.Rules, configws.Broken)

	generated := w.Hook("Write", w.Root, configws.Obj{"file_path": filepath.Join(w.Root, "generated", "x.py")})

	if w.Decision(generated) == "deny" {
		t.Errorf("組み込みの既定だけのはずが止めた: %s", generated.Stdout)
	}
	if got := w.fallback(); got != "builtin:rules" {
		t.Errorf("fallback = %q", got)
	}
	w.Hook("Read", w.Root, configws.Obj{"file_path": filepath.Join(w.Root, "README.md")})
	if got := w.LastRecord().Str("decision"); got != "allow" {
		t.Errorf("Read の決定 = %q", got)
	}
	w.AssertDenied(w.Hook("Bash", w.Root, configws.Obj{"command": rmRf}))
	if len(w.Problems("error", "(rules)")) == 0 {
		t.Error("不正な共通設定を --lint が error で言わない")
	}
}

// config の設定が不正なら空。共通設定は効き、`fallback` に名前を残す。
func TestABrokenLayerIsEmptyAndIsNamedInTheFallback(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	configws.Write(t, configws.LayerPath(w.Lib, "rules"), configws.Broken)

	denied := w.Hook("Write", w.Lib, configws.Obj{"file_path": filepath.Join(w.Lib, ".env")})

	w.AssertDenied(denied, "credentials")
	if got := w.fallback(); got != "lib" {
		t.Errorf("fallback = %q", got)
	}
}

// `--lint` は、共通設定と config の 3 本が無いことを不備として言わない。
func TestLintSaysNothingAboutMissingFiles(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	w.removeCommonRules()
	if err := os.Remove(w.Risk); err != nil {
		t.Fatal(err)
	}
	w.removeOwnLayer()
	for _, kind := range []string{"rules", "phases", "risk"} {
		if err := os.Remove(configws.LayerPath(w.Lib, kind)); err != nil {
			t.Fatal(err)
		}
	}

	if errs := w.Problems("error", ""); len(errs) != 0 {
		t.Errorf("error: %v", errs)
	}
	for _, p := range w.Problems("warn", "") {
		if slices.Contains([]string{"(rules)", "(phases)", "(risk)"}, p.Where) {
			t.Errorf("warn: %v", p)
		}
	}
}

// `--explain` は、置いていない設定を「置いていない（無い = 空）」と言う。
func TestExplainShowsAMissingLayerAsNotPlaced(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	w.removeCommonRules()

	out := w.Ccnavi("--explain").Stdout

	if !strings.Contains(out, "この設定のファイルはありません") {
		t.Errorf("置いていないと言わない:\n%s", out)
	}
	before, _, _ := strings.Cut(out, "■ rules ワークスペースの設定")
	if strings.Contains(before, "読めない: ") {
		t.Errorf("置いていない設定を読めないと言った:\n%s", before)
	}
}

// ---- 組み込みの deny の表記違い

// 組み込みの deny が表記違いでも止める形と、止めない形。
var builtinDenied = [][2]string{
	{gitCmd + " -C . push", "builtin:git-push"},
	{gitCmd + " --no-pager push", "builtin:git-push"},
	{gitCmd + " --git-dir=.git push", "builtin:git-push"},
	{gitCmd + " --git-dir .git push origin main", "builtin:git-push"},
	{gitCmd + " -c x=y reset --hard", "builtin:git-reset-hard"},
	{gitCmd + " reset -q --hard HEAD~1", "builtin:git-reset-hard"},
	{"rm -fr x", "builtin:recursive-delete"},
	{"rm -r -f x", "builtin:recursive-delete"},
	{"rm -f -r x", "builtin:recursive-delete"},
	{"rm --recursive --force x", "builtin:recursive-delete"},
	{"rm -Rfv x", "builtin:recursive-delete"},
	{"rm -r x -f", "builtin:recursive-delete"},
	// 後ろに別の rm を引数として挟んでも、最初の rm の選択肢として読む。
	{"rm -r rm -f", "builtin:recursive-delete"},
	{"ls; rm a rm -rf b", "builtin:recursive-delete"},
	// 値を取るオプションの値まで読んだ後のサブコマンド。
	{gitCmd + " -c push.default=current push", "builtin:git-push"},
	{gitCmd + " -C push push", "builtin:git-push"},
	{"/usr/bin/" + gitCmd + " push", "builtin:git-push"},
	{gitCmd + ".exe push", "builtin:git-push"},
	{gitCmd + " push;", "builtin:git-push"},
	// サブコマンドを直接起こす形（パス付き・.exe 付き）。
	{"/usr/lib/" + gitCmd + "-core/" + gitCmd + "-push origin HEAD", "builtin:git-push"},
	{gitCmd + "-push.exe origin", "builtin:git-push"},
	{"/usr/lib/" + gitCmd + "-core/" + gitCmd + "-reset --hard", "builtin:git-reset-hard"},
	{gitCmd + "-reset.exe -q --hard HEAD", "builtin:git-reset-hard"},
	{gitCmd + " -C x reset --hard", "builtin:git-reset-hard"},
	{gitCmd + " status; " + gitCmd + " reset --hard", "builtin:git-reset-hard"},
	// 値が g・gi・git… で始まっても、値として最後まで読む。
	{gitCmd + " -C gitx push", "builtin:git-push"},
	{gitCmd + " -c remote.origin.url=github.com push", "builtin:git-push"},
	{gitCmd + " -C gitlab reset --hard", "builtin:git-reset-hard"},
	{gitCmd + " -C gi push", "builtin:git-push"},
	// `--` の後ろはオペランド。その前に揃っていればヒットする。
	{"rm -rf -- x", "builtin:recursive-delete"},
	{"rm -r -f -- x", "builtin:recursive-delete"},
	{gitCmd + " reset --hard -- x", "builtin:git-reset-hard"},
	// 空の値（読みでは空白が 2 字続く）の後ろのサブコマンド。
	{gitCmd + " -C '' push", "builtin:git-push"},
	{gitCmd + ` -C "" reset --hard`, "builtin:git-reset-hard"},
	{gitCmd + " --work-tree '' push", "builtin:git-push"},
	{gitCmd + " -C '' -c '' push", "builtin:git-push"},
	// `\` の無い `$'…'` は単一引用と同じに読む。
	{gitCmd + " $'push'", "builtin:git-push"},
	{"rm $'-rf' x", "builtin:recursive-delete"},
}

var builtinPassed = []string{
	"legit push",
	"rm -r x",
	"rm -f x",
	"rm -f file-r",
	gitCmd + " commit -m push",
	gitCmd + " reset --soft HEAD~1",
	// `-c` の値の中の push・reset はサブコマンドではない。
	gitCmd + " -c push.default=current status",
	gitCmd + " -C push status",
	gitCmd + " -c reset.x=1 status --hard",
	gitCmd + " pushx",
	gitCmd + "-pushx origin",
	// `--` の後ろはオペランドで、選択肢には読まない。
	"rm -f -- -r",
	"rm -r -- -f x",
	gitCmd + " reset -- --hard",
	gitCmd + " -c x=push status",
}

// 組み込みの deny（土台）は、glob の文字列一致で外れていた表記違いも止める。git のグローバルオプションを挟んだ形、
// rm の再帰と強制の組み合わせ・順序・長い形。片方だけの rm や、`git` を含む別の語は止めない。共通設定が無いときと、
// 壊れて組み込みの既定に戻ったときの両方で見る（hook の実行前と同じ判定の本体を、表ごとに 1 回の起動で通す）。
func TestBuiltinFormsAreDeniedWhateverTheStateOfTheCommonLayer(t *testing.T) {
	t.Parallel()
	for name, setup := range map[string]func(absentWS){
		"without the common layer":   func(w absentWS) { w.removeCommonRules() },
		"with a broken common layer": func(w absentWS) { configws.Write(w.T, w.Rules, configws.Broken) },
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			w := newAbsentWS(t)
			setup(w)
			var calls [][2]string
			for _, d := range builtinDenied {
				calls = append(calls, [2]string{"Bash", d[0]})
			}
			for _, p := range builtinPassed {
				calls = append(calls, [2]string{"Bash", p})
			}
			answers := w.Judge("disable", calls)
			for i, d := range builtinDenied {
				if answers[i].Verdict != "deny" || !slices.Contains(answers[i].IDs, d[1]) {
					t.Errorf("止まらない: %q → %s %v (want %s)", d[0], answers[i].Verdict, answers[i].IDs, d[1])
				}
			}
			for i, p := range builtinPassed {
				a := answers[len(builtinDenied)+i]
				// 生の git は組み込みで常に止まる。ほかの土台の deny にヒットしていないことを見る。
				if a.Verdict == "deny" && !slices.Contains(a.IDs, rawGitBuiltin) {
					t.Errorf("止めた: %q → %v", p, a.IDs)
				}
			}
		})
	}
}

// 次の一手に挙げる git の操作は、ラッパーがあればラッパーの実パスで書く。生の git は組み込みで常に止まるので、止まる形（`git`）は案内しない。
// ラッパーが無いときは git の操作をユーザに頼む文にする。
func TestTheNextStepNamesTheGitWrapper(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	w.removeCommonRules()
	cases := [][2]string{{gitCmd + " reset --hard", "builtin:git-reset-hard"}, {"rm -rf x", "builtin:recursive-delete"}}
	for _, c := range cases {
		denied := w.Hook("Bash", w.Root, configws.Obj{"command": c[0]})
		w.AssertDenied(denied, c[1])
		if reason := w.Reason(denied); strings.Contains(reason, "ccnavi-git.sh") || strings.Contains(reason, "'git ") {
			t.Errorf("ラッパーが無いのに案内した: %s", reason)
		}
	}
	configws.Write(t, filepath.Join(w.Root, ".ccnavi", "scripts", "ccnavi-git.sh"), "")
	wrapper := base.ScriptCommand(w.Root, "ccnavi-git.sh")
	for _, c := range cases {
		denied := w.Hook("Bash", w.Root, configws.Obj{"command": c[0]})
		w.AssertDenied(denied, c[1])
		if reason := w.Reason(denied); !strings.Contains(reason, wrapper) || strings.Contains(reason, "'git ") {
			t.Errorf("ラッパーの実パスで案内しない（%s）: %s", wrapper, reason)
		}
	}
}

// ---- 生の git

// 生の git は組み込みで常に止める。ラッパーがあればラッパーへ誘導し、無ければユーザに頼む。
// 共通設定の有無、壊れて組み込みの既定に戻ったとき、チケット制御の可否、ラッパーの有無に依らない。
func rawGitWS(t *testing.T) (absentWS, string) {
	w := newAbsentWS(t)
	script := configws.Write(t, filepath.Join(w.Root, ".ccnavi", "scripts", "ccnavi-git.sh"), "exit 0\n")
	return w, script
}

func (w absentWS) assertRawGitDenied(script string) {
	w.T.Helper()
	result := w.Hook("Bash", w.Root, configws.Obj{"command": gitCmd + " status"})
	w.AssertDenied(result, rawGitBuiltin)
	reason := w.Reason(result)
	if !strings.Contains(reason, ".ccnavi/scripts/ccnavi-git.sh") || !strings.Contains(reason, "<サブコマンド>") {
		w.T.Errorf("ラッパーへ誘導しない: %s", reason)
	}
	// 誘導先のラッパーは止めない。
	wrapper := w.Hook("Bash", w.Root, configws.Obj{"command": "sh " + script + " status"})
	if w.recordHas(rawGitBuiltin) {
		w.T.Errorf("ラッパーを止めた: %s", wrapper.Stdout)
	}
	if legit := w.Hook("Bash", w.Root, configws.Obj{"command": "legit push"}); w.Decision(legit) == "deny" {
		w.T.Errorf("git を含む別の語を止めた: %s", legit.Stdout)
	}
}

func TestRawGitIsDeniedWhateverTheStateOfTheCommonLayer(t *testing.T) {
	t.Parallel()
	t.Run("without the common layer", func(t *testing.T) {
		t.Parallel()
		w, script := rawGitWS(t)
		w.removeCommonRules()
		w.assertRawGitDenied(script)
	})
	// ルールファイルが読めない間も止める。直す道はラッパーの add / restore --ours が残す。
	t.Run("with a broken common layer", func(t *testing.T) {
		t.Parallel()
		w, script := rawGitWS(t)
		configws.Write(t, w.Rules, configws.Broken)
		w.assertRawGitDenied(script)
		if got := w.fallback(); got != "builtin:rules" {
			t.Errorf("fallback = %q", got)
		}
	})
	t.Run("when ticket control is disabled", func(t *testing.T) {
		t.Parallel()
		w, _ := rawGitWS(t)
		w.removeCommonRules()
		w.Env = map[string]string{"CCNAVI_TICKET_CONTROL": "disable"}
		w.AssertDenied(w.Hook("Bash", w.Root, configws.Obj{"command": gitCmd + " status"}), rawGitBuiltin)
	})
	// ラッパーが無いときも止める。誘導先が無いので、スクリプトを名指しせず、ユーザに頼む文にする。
	t.Run("without the wrapper", func(t *testing.T) {
		t.Parallel()
		w, script := rawGitWS(t)
		w.removeCommonRules()
		if err := os.Remove(script); err != nil {
			t.Fatal(err)
		}
		result := w.Hook("Bash", w.Root, configws.Obj{"command": gitCmd + " status"})
		w.AssertDenied(result, rawGitBuiltin)
		if reason := w.Reason(result); strings.Contains(reason, "ccnavi-git.sh") || !strings.Contains(reason, "ユーザ") {
			t.Errorf("スクリプトを名指しした・ユーザに頼まない: %s", reason)
		}
	})
}

// `git` がコマンドの位置にある形だけを止める。引数の語の `git` は止めない。
func TestOnlyGitAtTheCommandPositionIsDenied(t *testing.T) {
	t.Parallel()
	w, _ := rawGitWS(t)
	w.removeCommonRules()
	for _, command := range []string{
		"ls && " + gitCmd + " log",
		"sudo " + gitCmd + " status",
		"env A=1 " + gitCmd + " status",
		"command " + gitCmd + " status",
		"ls | xargs " + gitCmd + " status",
		"nohup " + gitCmd + " status",
		"time " + gitCmd + " status",
		"/usr/bin/" + gitCmd + " status",
		gitCmd + ".exe status",
		"echo $(" + gitCmd + " status)",
		"if true; then " + gitCmd + " status; fi",
		`C:\Git\` + gitCmd + `.exe status`,
		`"C:\Git\` + gitCmd + `.exe" status`,
	} {
		w.AssertDenied(w.Hook("Bash", w.Root, configws.Obj{"command": command}), rawGitBuiltin)
	}
	for _, command := range []string{"grep " + gitCmd + " README.md", "echo hello " + gitCmd + " world", "echo to do " + gitCmd + " stuff"} {
		w.Hook("Bash", w.Root, configws.Obj{"command": command})
		if w.recordHas(rawGitBuiltin) {
			t.Errorf("引数の語を止めた: %s", command)
		}
	}
}

// ---- `.git` の中への書き込み

const (
	gitDirBuiltin      = "builtin:guard-git-dir"
	gitDirShellBuiltin = "builtin:guard-git-dir-via-bash"
)

// `.git` の中へシェルから書き込む形と、読むだけの形。
var gitDirShellDenied = []string{
	"echo x > .git/config",
	"echo x >> /abs/repo/.git/hooks/pre-commit",
	"cp /tmp/x .git/config",
	"cp /tmp/x .git",
	"cp -t .git/hooks /tmp/pre-commit",
	"mv /tmp/x .git/HEAD",
	"mv .git /tmp/old",
	"rm -f .git/index.lock",
	"tee .git/config < /tmp/x",
	"sed -i 's/a/b/' .git/config",
	"cp -r /tmp/x/.git .",
	"echo x > .GIT/config",
	"echo x > .git./config",
	"dd if=/tmp/x of=.git/HEAD",
	"rm .git",
	"sed -i s/a/b/ /abs/.git/config",
	"sed -i s/a/b/ x .git/config",
	"sed -e s/a/b/ -i .git/config",
	"find .git -name '*.lock' -delete",
	"find .git -type f -exec rm {} +",
	"cd .git && find . -delete",
	"cd .git && echo x > config",
	`printf '.git/config\n' | xargs rm`,
}

var gitDirShellPassed = []string{
	"cat .git/config",
	"cp .git/config /tmp/x",
	"sed -n 1p .git/config",
	"echo x > .gitignore",
	"echo x > .github/w.yml",
	"sed -i 's/old.git/new.git/' README.md",
	`echo "a > .git/x"`,
	"echo x > repo.git/config",
	"cp ../foo/*.py .",
	"dd if=.git/index of=/tmp/x",
	"tee /tmp/o < .git/config",
	"patch -p1 < .git/x",
	"sed -i 's/foo/.git/' f",
	`sed -i 's/\.git\//x/' f`,
	"find . -path ./.git -prune -o -type f -delete",
	"printf a | xargs rm",
	"ls .gitignore | xargs rm",
}

// `.git` の中への書き込みは、共通設定と config の有無・状態によらず土台で止める。名指しのツール（Write / Edit / NotebookEdit）は
// `.git/` の下と `.git` そのもの、シェルは守る場所と同じ書き込みの形で見る。読むだけと、`.gitignore`・`.github/` は止めない。
// git のラッパーの呼び出しは、`.git` がコマンド文字列に出ないのでヒットしない。表は状態ごとに 1 回の起動でまとめて判定する。
func TestGitDirWritesAreDeniedWhateverTheStateOfTheCommonLayer(t *testing.T) {
	t.Parallel()
	for name, setup := range map[string]func(absentWS){
		"with the common layer":      func(absentWS) {},
		"without the common layer":   func(w absentWS) { w.removeCommonRules() },
		"with a broken common layer": func(w absentWS) { configws.Write(w.T, w.Rules, configws.Broken) },
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			w := newAbsentWS(t)
			setup(w)
			type gitCase struct {
				tool, subject, rule string
				stops               bool
			}
			var cases []gitCase
			for _, tool := range []string{"Write", "Edit", "NotebookEdit"} {
				for _, p := range []string{filepath.Join(".git", "config"), filepath.Join(".git", "hooks", "pre-commit"), filepath.Join("wt", ".git")} {
					cases = append(cases, gitCase{tool, filepath.Join(w.Root, p), gitDirBuiltin, true})
				}
			}
			for _, n := range []string{".gitignore", filepath.Join(".github", "w.yml"), "repo.git"} {
				cases = append(cases, gitCase{"Write", filepath.Join(w.Root, n), gitDirBuiltin, false})
			}
			for _, c := range gitDirShellDenied {
				cases = append(cases, gitCase{"Bash", c, gitDirShellBuiltin, true})
			}
			wrapper := filepath.Join(w.Root, ".ccnavi", "scripts", "ccnavi-git.sh")
			passed := append(slices.Clone(gitDirShellPassed), "sh "+wrapper+" integrate x", "sh "+wrapper+" -C "+w.Root+" status")
			for _, c := range passed {
				cases = append(cases, gitCase{"Bash", c, gitDirShellBuiltin, false})
			}
			cases = append(cases, gitCase{"Read", filepath.Join(w.Root, ".git", "config"), gitDirBuiltin, false})
			var calls [][2]string
			for _, c := range cases {
				calls = append(calls, [2]string{c.tool, c.subject})
			}
			for i, a := range w.Judge("disable", calls) {
				c := cases[i]
				switch {
				case c.stops && (a.Verdict != "deny" || !slices.Contains(a.IDs, c.rule)):
					t.Errorf("止まらない: %s %q → %s %v", c.tool, c.subject, a.Verdict, a.IDs)
				case !c.stops && slices.Contains(a.IDs, c.rule):
					t.Errorf("止めた: %s %q → %v", c.tool, c.subject, a.IDs)
				}
			}
		})
	}
}

func TestTheGitDirMessageNamesTheGitWrapper(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	w.removeCommonRules()
	path := filepath.Join(w.Root, ".git", "config")
	denied := w.Hook("Write", w.Root, configws.Obj{"file_path": path})
	if strings.Contains(w.Reason(denied), "git <サブコマンド>") {
		t.Errorf("ラッパーが無いのに案内した: %s", w.Reason(denied))
	}
	configws.Write(t, filepath.Join(w.Root, ".ccnavi", "scripts", "ccnavi-git.sh"), "")
	wrapper := base.ScriptCommand(w.Root, "ccnavi-git.sh")
	for tool, input := range map[string]configws.Obj{"Write": {"file_path": path}, "Bash": {"command": "echo x > .git/config"}} {
		reason := w.Reason(w.Hook(tool, w.Root, input))
		if !strings.Contains(reason, wrapper) || strings.Contains(reason, "'git ") {
			t.Errorf("%s: ラッパーの実パスで案内しない（%s）: %s", tool, wrapper, reason)
		}
	}
}

// ---- ミラー・単体の clone・和

// sumsOf は `--explain --json` の sums[]（拡張が統合結果を自分で組まずに済むための合成結果）。
type sumView struct {
	Layers []string `json:"layers"`
	Rules  struct {
		Deny       []struct{ ID string }
		Unreadable any
		Missing    bool
	} `json:"rules"`
	Risk struct {
		Levels   map[string]int
		Factors  []struct{ ID, Source string }
		Fallback string
	} `json:"risk"`
	Phases map[string]any `json:"phases"`
}

func (s sumView) denyIDs() []string {
	var ids []string
	for _, r := range s.Rules.Deny {
		ids = append(ids, r.ID)
	}
	return ids
}

func (s sumView) factorIDs() []string {
	var ids []string
	for _, f := range s.Risk.Factors {
		ids = append(ids, f.ID)
	}
	return ids
}

func (s sumView) phaseIDs() []string {
	var ids []string
	types, _ := s.Phases["types"].([]any)
	for _, ty := range types {
		ids = append(ids, ty.(map[string]any)["id"].(string))
	}
	return ids
}

func truthy(v any) bool {
	switch x := v.(type) {
	case nil:
		return false
	case bool:
		return x
	case string:
		return x != ""
	}
	return true
}

func (w absentWS) explain() (sums map[string]sumView, raw map[string]any) {
	w.T.Helper()
	r := w.Ccnavi("--explain", "--json")
	var out struct {
		Sums []struct {
			sumView
			Name string `json:"name"`
		} `json:"sums"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		w.T.Fatalf("--explain --json が読めない: %v\n%s\n%s", err, r.Stdout, r.Stderr)
	}
	if err := json.Unmarshal([]byte(r.Stdout), &raw); err != nil {
		w.T.Fatal(err)
	}
	sums = map[string]sumView{}
	for _, s := range out.Sums {
		sums[s.Name] = s.sumView
	}
	return sums, raw
}

// ワークスペースの中では、プロジェクトのミラー（`.ccnavi/config/common/`）を共通設定として読まない。
// ミラーのルール・配点・フェーズ定義は、判定にも和にも入らない（不正なミラーの phases.yml があっても何も言わない）。
func TestTheMirrorIsNotReadInTheWorkspace(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	mirror := filepath.Join(w.Lib, ".ccnavi", "config", "common")
	configws.Write(t, filepath.Join(mirror, "rules.yml"), configws.Body(configws.Ruleset(
		[]configws.Obj{configws.Rule("mirror-only", "Write|Edit", "glob", "*/mirror-target/*", "from the mirror.")}, nil)))
	configws.Write(t, filepath.Join(mirror, "risks.yml"), configws.CommonRiskText+"  - {id: m, points: 1, lines_over: 0}\n")
	configws.Write(t, filepath.Join(mirror, "phases.yml"), "version: 1\nphases: [\n")

	result := w.Hook("Write", w.Lib, configws.Obj{"file_path": filepath.Join(w.Lib, "mirror-target", "x.py")})
	if w.Decision(result) == "deny" || strings.Contains(result.Stdout, "mirror-only") {
		t.Errorf("ミラーのルールが判定に入った: %s", result.Stdout)
	}
	sums, raw := w.explain()
	for _, layer := range raw["layers"].([]any) {
		deny, _ := layer.(map[string]any)["rules"].(map[string]any)["deny"].([]any)
		for _, r := range deny {
			if r.(map[string]any)["id"] == "mirror-only" {
				t.Error("ミラーのルールが --explain の layers に出た")
			}
		}
	}
	if got := sums["lib"].factorIDs(); !slices.Equal(got, []string{"big-diff", "schema"}) {
		t.Errorf("lib の配点 = %v", got)
	}
	for _, p := range w.Problems("error", "") {
		if strings.Contains(p.Detail, "mirror") || strings.Contains(p.Detail, "lib/.ccnavi/config/common") {
			t.Errorf("ミラーを読んで error: %v", p)
		}
	}
}

// 単体の clone。そのプロジェクトがルートになる。`.ccnavi/config/common/`（ミラー）が共通設定、`.ccnavi/config/project/` が
// ワークスペースの設定として読まれ、ワークスペースの中と同じ「共通 + 1 つの設定」の和で判定される。
func newStandalone(t *testing.T) (absentWS, string) {
	w := newAbsentWS(t)
	// lib を単体の clone に見立てる。ミラーは共通設定のコピー。
	mirror := filepath.Join(w.Lib, ".ccnavi", "config", "common")
	configws.Write(t, filepath.Join(mirror, "rules.yml"), configws.Body(configws.CommonRules))
	configws.Write(t, filepath.Join(mirror, "risks.yml"), configws.CommonRiskText)
	w.Root = w.Lib
	w.Log = filepath.Join(w.Lib, "logs", "decisions.jsonl")
	return w, mirror
}

func TestStandaloneCloneTheMirrorIsTheCommonLayerAndTheConfigIsTheOwnLayer(t *testing.T) {
	t.Parallel()
	w, _ := newStandalone(t)
	// ミラーのルールは裸の id、config のルールは `self:` の id でヒットする。
	w.AssertDenied(w.Hook("Write", w.Lib, configws.Obj{"file_path": filepath.Join(w.Lib, ".env")}), "credentials")
	if got := w.HitRules(); !slices.Equal(got, []string{"credentials"}) || w.LastRecord().Str("source") != "common" {
		t.Errorf("hits = %v, record = %v", got, w.LastRecord())
	}
	w.AssertDenied(w.Hook("Write", w.Lib, configws.Obj{"file_path": filepath.Join(w.Lib, "schema", "x.sql")}), "self:schema")
	if got := w.HitRules(); !slices.Equal(got, []string{"self:schema"}) || w.LastRecord().Str("source") != "self" {
		t.Errorf("hits = %v, record = %v", got, w.LastRecord())
	}
	// Bash も同じ和（共通 + ワークスペースの設定）。プロジェクトの設定は無い。
	w.AssertDenied(w.Hook("Bash", w.Lib, configws.Obj{"command": "terraform apply"}), "terraform")
	w.AssertDenied(w.Hook("Bash", w.Lib, configws.Obj{"command": "psql -c x"}), "self:raw-psql")
}

func TestStandaloneCloneSumsTheCommonLayerAndTheOwnLayer(t *testing.T) {
	t.Parallel()
	w, _ := newStandalone(t)
	sums, _ := w.explain()
	// 配点も共通 + ワークスペースの設定の和。閾値は小さいほう。
	risk := sums["self"].Risk
	if got := sums["self"].factorIDs(); !slices.Equal(got, []string{"big-diff", "schema"}) || risk.Levels["critical"] != 50 || risk.Fallback != "" {
		t.Errorf("risk = %+v", risk)
	}
	// フェーズ定義は config の 1 本（`.ccnavi/config/project/phases.yml`）。
	if len(sums) != 1 || !slices.Equal(sums["self"].phaseIDs(), []string{"design", "build", "release"}) {
		t.Errorf("sums = %v", sums)
	}
	if got, _ := sums["self"].Phases["path"].(string); !run.SamePath(got, filepath.Join(w.Lib, ".ccnavi", "config", "project", "phases.yml")) {
		t.Errorf("phases path = %q", got)
	}
}

func TestStandaloneCloneMirrorScriptResolvesFromTheCloneRoot(t *testing.T) {
	t.Parallel()
	w, mirror := newStandalone(t)
	// ミラーの `script:` は `.ccnavi/config/common/scripts/` を指し、その clone のルートから解く。
	configws.Write(t, filepath.Join(mirror, "risks.yml"),
		configws.CommonRiskText+"  - {id: counted, points: 5, script: .ccnavi/config/common/scripts/count.sh, message: m}\n")
	script := configws.Write(t, filepath.Join(mirror, "scripts", "count.sh"), "printf 1\n")
	said := func() bool {
		return configws.AnyDetail(w.Problems("error", ""), "count.sh") || configws.AnyDetail(w.Problems("warn", ""), "count.sh")
	}
	if said() {
		t.Error("置いたスクリプトを言った")
	}
	// 置き場に無ければ `--lint` が名指しする（解いた先を見ている）。
	if err := os.Remove(script); err != nil {
		t.Fatal(err)
	}
	if !said() {
		t.Error("置き場に無いスクリプトを --lint が名指ししない")
	}
}

// `--lint` もミラーを共通設定として読む。ミラーが不正なら error で名指しする。
func TestStandaloneCloneLintReadsTheMirrorAsTheCommonLayer(t *testing.T) {
	t.Parallel()
	w, mirror := newStandalone(t)
	configws.Write(t, filepath.Join(mirror, "rules.yml"), configws.Broken)
	if len(w.Problems("error", "(rules)")) == 0 {
		t.Error("不正なミラーを error で言わない")
	}
}

func TestEveryExplainSumIsTheCommonLayerPlusOneLayer(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	sums, _ := w.explain()

	if got := configws.SortedKeys(sums); !slices.Equal(got, []string{"app", "lib", "self"}) {
		t.Fatalf("sums = %v", got)
	}
	if !slices.Equal(sums["lib"].Layers, []string{"common", "lib"}) {
		t.Errorf("lib の layers = %v", sums["lib"].Layers)
	}
	common := []string{"credentials", "terraform", "guard-approved"}
	for name, want := range map[string][]string{
		"self": append(slices.Clone(common), "self:generated", "self:dropdb"),
		"lib":  append(slices.Clone(common), "lib:schema", "lib:raw-psql"),
		"app":  common,
	} {
		if got := sums[name].denyIDs(); !slices.Equal(got, want) {
			t.Errorf("%s の deny = %v, want %v", name, got, want)
		}
	}
}

// `layers[]` は合わせる途中経過なので、先の設定と全欄が同じ定義は後ろから外れる。
// `sums[]` は「共通 + その設定」だけで合わせるので、外れない。
func TestASumKeepsWhatTheLayersOverviewDrops(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	same := configws.Obj{"version": 1, "deny": []configws.Obj{configws.DenyOf(configws.CommonRules)[0]}}
	configws.Write(t, configws.LayerPath(w.Root, "rules"), configws.Body(same))
	configws.Write(t, configws.LayerPath(w.Lib, "rules"), configws.Body(same))

	sums, _ := w.explain()

	// どちらの和も共通設定の定義だけ（全欄一致の重複は後ろを破棄する）。
	var want []string
	for _, r := range configws.DenyOf(configws.CommonRules) {
		want = append(want, r["id"].(string))
	}
	if got := sums["lib"].denyIDs(); !slices.Equal(got, want) {
		t.Errorf("lib の deny = %v, want %v", got, want)
	}
	// lib の設定自身の宣言は、他の設定の定義に引かれない。
	if sums["lib"].denyIDs()[0] != sums["self"].denyIDs()[0] {
		t.Errorf("lib = %v, self = %v", sums["lib"].denyIDs(), sums["self"].denyIDs())
	}
}

func TestTheRiskSumCarriesTheEffectiveLevelsAndFactors(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	sums, _ := w.explain()
	lib := sums["lib"].Risk
	if want := map[string]int{"medium": 20, "high": 40, "critical": 50}; !maps.Equal(lib.Levels, want) {
		t.Errorf("levels = %v, want %v", lib.Levels, want)
	}
	if got := sums["lib"].factorIDs(); !slices.Equal(got, []string{"big-diff", "schema"}) {
		t.Errorf("lib の配点 = %v", got)
	}
	var sources []string
	for _, f := range lib.Factors {
		sources = append(sources, f.Source)
	}
	if !slices.Equal(sources, []string{"common", "lib"}) {
		t.Errorf("source = %v", sources)
	}
	if got := sums["app"].factorIDs(); !slices.Equal(got, []string{"big-diff"}) {
		t.Errorf("app の配点 = %v", got)
	}
}

func TestThePhasesAreTheOneFileThatTheLayerNames(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	sums, _ := w.explain()
	if got := sums["self"].phaseIDs(); !slices.Equal(got, []string{"docs"}) {
		t.Errorf("self = %v", got)
	}
	if got := sums["lib"].phaseIDs(); !slices.Equal(got, []string{"design", "build", "release"}) {
		t.Errorf("lib = %v", got)
	}
	if got := sums["app"].phaseIDs(); len(got) != 0 || truthy(sums["app"].Phases["unreadable"]) {
		t.Errorf("app = %v, unreadable = %v", got, sums["app"].Phases["unreadable"])
	}
	// 定義は順序を持たない。先行関係は計画のステップの `after` から読むので、先行関係の欄は渡さない。
	if _, ok := sums["lib"].Phases["order"]; ok {
		t.Error("phases に order の欄がある")
	}
}

func TestAMissingOrBrokenLayerIsSaidInItsOwnSum(t *testing.T) {
	t.Parallel()
	w := newAbsentWS(t)
	configws.Write(t, configws.LayerPath(w.Lib, "rules"), configws.Broken)

	sums, _ := w.explain()

	if !truthy(sums["lib"].Rules.Unreadable) {
		t.Error("lib の不正を言わない")
	}
	var want []string
	for _, r := range configws.DenyOf(configws.CommonRules) {
		want = append(want, r["id"].(string))
	}
	if got := sums["lib"].denyIDs(); !slices.Equal(got, want) {
		t.Errorf("lib の deny = %v, want %v", got, want)
	}
	if !sums["app"].Rules.Missing || sums["self"].Rules.Missing {
		t.Errorf("missing: app = %v, self = %v", sums["app"].Rules.Missing, sums["self"].Rules.Missing)
	}
}
