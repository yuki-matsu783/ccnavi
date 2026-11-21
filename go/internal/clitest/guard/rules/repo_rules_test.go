package rules

import (
	"bytes"
	"encoding/json"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// このリポジトリの実際のルール（.ccnavi/config/common/rules.yml）で判定する受入テスト。
// tests/fixtures のルールではなく、運用に使っている rules.yml をそのまま `--test` に渡す。
//
// 判定とヒットしたルールの名前だけを見る表は hook/judge の TestRepoRulesTable（testdata/repo_rules.json）が持つ。
// ここに残すのは、文面・縮退の理由・unwrapped・quoted・環境の切り替えを見るもの。
// `--test <tool> <subject> --json` の `verdict` と `rules[].id` を読む（形は docs/11_requirements/json-shapes.md「試験の JSON」）。

const (
	placeholder  = "/repo" // 期待する判定のパスに書く合言葉。`--root` に渡したルートに読み替える
	launcher     = ".ccnavi/scripts/ccnavi-launcher.sh"
	settingFiles = "builtin:guard-setting-files"
	nameRule     = "builtin:command-name-expansion"
	backquote    = "builtin:backquote"

	reasonUnterminated      = "unterminated-quote"
	reasonUnterminatedSubst = "unterminated-substitution"
	reasonTakenAsCode       = "command-taken-as-code"
)

// verdictBody は試験の JSON。
type verdictBody map[string]any

func (b verdictBody) s(key string) string { s, _ := b[key].(string); return s }
func (b verdictBody) known() bool         { k, _ := b["known"].(bool); return k }

// response は応答の文面。文字列でなければ JSON に直した文字（非 ASCII はそのまま）。
func (b verdictBody) response() string {
	if s, ok := b["response"].(string); ok {
		return s
	}
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(b["response"])
	return buf.String()
}

// hit はヒットしたルールの、書いた id（組み込みの名前はそのまま）。
func (b verdictBody) hit() []string {
	var out []string
	list, _ := b["rules"].([]any)
	for _, r := range list {
		m, _ := r.(map[string]any)
		id, _ := m["id"].(string)
		if !strings.HasPrefix(id, "builtin:") {
			id = id[strings.LastIndex(id, ":")+1:]
		}
		out = append(out, id)
	}
	return out
}

func (b verdictBody) hits(id string) bool {
	for _, h := range b.hit() {
		if h == id {
			return true
		}
	}
	return false
}

func (b verdictBody) quoted() []string {
	var out []string
	list, _ := b["quoted"].([]any)
	for _, v := range list {
		s, _ := v.(string)
		out = append(out, s)
	}
	return out
}

func rulesFile() string {
	return filepath.Join(base.Root(), ".ccnavi", "config", "common", "rules.yml")
}

func subject(s string) string { return strings.ReplaceAll(s, placeholder, slash(base.Root())) }

// judge は 1 件を実際のルールで判定して試験の JSON を返す。承認済みチケットと state は外し、記録も残さない。
// 組み込みの selfguard は既定のまま有効にする。env は呼び手が足す環境（承認のルールは CCNAVI_ENTRY_PATH から適用する形を作る）。
func judge(t testing.TB, tool, subj string, env map[string]string) verdictBody {
	t.Helper()
	r := run.Ccnavi(t, []string{"--root", base.Root(), "--rules", rulesFile(), "--test", tool, subject(subj), "--json"},
		run.Opts{Env: env, Dir: base.Root()})
	if r.Code != 0 {
		t.Fatalf("--test が失敗した: %s", r.Stderr)
	}
	var body verdictBody
	if err := json.Unmarshal([]byte(r.Stdout), &body); err != nil {
		t.Fatalf("--test の出力が JSON でない: %v\n%s", err, r.Stdout)
	}
	return body
}

func judgeBash(t testing.TB, subj string) verdictBody { return judge(t, "Bash", subj, nil) }

// judgeMany は何件もを 1 回の起動で判定する（`--test-expectations`。判定は `--test` と同じ関数を通る）。
// 文面（response）・degraded・unwrapped・quoted は持たないので、それを見るテストは judge で 1 件ずつ見る。
func judgeMany(t testing.TB, tool string, subjects []string, env map[string]string) []verdictBody {
	t.Helper()
	var items []map[string]string
	for _, s := range subjects {
		items = append(items, map[string]string{"tool": tool, "subject": subject(s)})
	}
	// 期待のタイプは判定に効かない。JSON は YAML としても読める。
	path := write(t, filepath.Join(base.TempDir(t), "expectations.yml"), jsonText(t, map[string]any{"ask": items}))
	r := run.Ccnavi(t, []string{"--root", base.Root(), "--rules", rulesFile(), "--test-expectations", path, "--json"},
		run.Opts{Env: env, Dir: base.Root()})
	if r.Code != 0 {
		t.Fatalf("--test-expectations が失敗した: %s", r.Stderr)
	}
	var out struct {
		Expectations []verdictBody `json:"expectations"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		t.Fatalf("出力が JSON でない: %v\n%s", err, r.Stdout)
	}
	if len(out.Expectations) != len(subjects) {
		t.Fatalf("%d 件を渡して %d 件が返った", len(subjects), len(out.Expectations))
	}
	return out.Expectations
}

func assertVerdict(t testing.TB, b verdictBody, want, ruleID string) {
	t.Helper()
	if !b.known() {
		t.Errorf("known でない: %v", b)
	}
	if b.s("verdict") != want {
		t.Errorf("verdict = %q, want %q: %s", b.s("verdict"), want, b.response())
	}
	if ruleID != "" && !b.hits(ruleID) {
		t.Errorf("ルール %q にヒットしていない: %v", ruleID, b.hit())
	}
	if ruleID == "" && len(b.hit()) != 0 {
		t.Errorf("どのルールにもヒットしないはず: %v", b.hit())
	}
}

func TestTheFixedConfirmationTextAppearsOnlyWhenUnattended(t *testing.T) {
	t.Parallel()
	script := "sh /repo/.ccnavi/scripts/ccnavi-worktree.sh"
	for _, c := range []struct{ subject, rule, extra string }{
		{script + " x --base feature/x", "direct-worktree-confirm", ""},
		{"curl -s https://example.com/a", "prefer-webfetch", "まとめてから実行する"},
	} {
		for _, a := range []struct {
			name, value string
			shown       bool
		}{{"unattended", "0", true}, {"attended", "1", false}, {"unset", "", false}} {
			t.Run(c.rule+"/"+a.name, func(t *testing.T) {
				t.Parallel()
				e := map[string]string{}
				if a.value != "" {
					e[env.ClaudeSessionAttended] = a.value
				}
				body := judge(t, "Bash", c.subject, e)
				assertVerdict(t, body, "ask", c.rule)
				said := body.response()
				if strings.Contains(said, "確認に答えられません") != a.shown {
					t.Errorf("shown=%v のはず: %s", a.shown, said)
				}
				if c.extra != "" && strings.Contains(said, c.extra) != a.shown {
					t.Errorf("%q は shown=%v のはず: %s", c.extra, a.shown, said)
				}
			})
		}
	}
}

func TestTheRedirectCapDenialAddsNoConfirmationText(t *testing.T) {
	t.Parallel()
	// ask のルールにもヒットするが、返すのは deny。打ち直しても通らないので、了承を頼ませない。
	// 閉じない引用で縮退させ、生の文字列の `>` を上限（50）より多く並べる。
	subj := `curl -s https://example.com/a "` + strings.Repeat(">a", 51)
	body := judge(t, "Bash", subj, map[string]string{env.ClaudeSessionAttended: "0"})
	if body.s("verdict") != "deny" || !body.hits("prefer-webfetch") {
		t.Errorf("deny で prefer-webfetch にヒットするはず: %s %v", body.response(), body.hit())
	}
	mustNotHave(t, body.response(), "確認に答えられません")
}

func TestWorktreeAddPassesWithAFetchNote(t *testing.T) {
	t.Parallel()
	// 止めない。遅れの判定で止めるのは ccnavi-git.sh の中（hook はネットワークに出ない）。
	body := judgeBash(t, "sh /repo/.ccnavi/scripts/ccnavi-git.sh worktree add .claude/worktrees/y -b y main")
	assertVerdict(t, body, "allow", "ccnavi-git-worktree-add")
	resp := body.response()
	mustHave(t, resp, "ccnavi-worktree.sh", "--adopt --base", "integrate できません")
	mustNotHave(t, resp, "--no-track", "ff-only") // 手の worktree add に誘わない
	// ほかのサブコマンドには添えない。
	if judgeBash(t, "sh /repo/.ccnavi/scripts/ccnavi-git.sh worktree list").hits("ccnavi-git-worktree-add") {
		t.Error("worktree list に添えている")
	}
}

func TestIntegratePassesWithAReportFirstNote(t *testing.T) {
	t.Parallel()
	script := "sh /repo/.ccnavi/scripts/ccnavi-git.sh"
	for _, subj := range []string{
		script + " integrate foo",
		"cd /repo/.claude/worktrees/foo && " + script + " integrate foo",
		"cd /repo && " + script + " integrate foo-1",
	} {
		body := judgeBash(t, subj)
		assertVerdict(t, body, "allow", "integrate-report-first")
		mustHave(t, body.response(), "integrate-done の前", "DENY_NEEDS_USER_TURN")
	}
	// 確定・取りやめ・名前なしには添えない。
	others := []string{script + " integrate-done foo -m x", script + " integrate --abort foo", script + " integrate", script + " status"}
	for i, body := range judgeMany(t, "Bash", others, nil) {
		if body.hits("integrate-report-first") {
			t.Errorf("%q に添えている", others[i])
		}
	}
}

func TestAQuotedChevronPairStaysUnreadAndStopped(t *testing.T) {
	t.Parallel()
	// 許容した誤検知。生の文字列に heredoc がヒットし、読めなかったことを示す。
	body := judgeBash(t, `grep -n "<<" README.md`)
	if body.s("verdict") != "deny" || body.s("code") != "PARSE_UNCERTAIN" || body.s("degraded") != reasonUnterminated {
		t.Errorf("deny / PARSE_UNCERTAIN / %s のはず: %v", reasonUnterminated, body)
	}
	mustHave(t, body.response(), "生の文字列")
}

// `CCNAVI_ENTRY_PATH` が振り分けの sh を指すときの判定。
func judgeAsLauncher(t testing.TB, subj string) verdictBody {
	t.Helper()
	body := judge(t, "Bash", subj, map[string]string{env.EntryPath: launcher})
	if !body.known() {
		t.Fatalf("known でない: %v", body)
	}
	return body
}

func assertDeniedBy(t testing.TB, subj string, body verdictBody, rule string) {
	t.Helper()
	if body.s("verdict") != "deny" || !body.hits(rule) {
		t.Errorf("%q は %s で deny のはず: %s %v %s", subj, rule, body.s("code"), body.hit(), body.response())
	}
}

func TestARunnerIsNamedWhenItsInnerCommandHits(t *testing.T) {
	t.Parallel()
	// 元の形のどこが問題なのかを、読み手が辿れるように。
	body := judgeAsLauncher(t, "env rm -f .ccnavi/config/common/rules.yml")
	if body.s("verdict") != "deny" {
		t.Fatalf("deny のはず: %s", body.response())
	}
	if _, has := body["unwrapped"]; !has || body.s("unwrapped") != "rm -f .ccnavi/config/common/rules.yml" {
		t.Errorf("unwrapped = %v", body["unwrapped"])
	}
	// 名前を括る記号は問わない。
	line := regexp.MustCompile("[`'\"]?env[`'\"]? が実行する [`'\"]?rm -f \\.ccnavi/config/common/rules\\.yml[`'\"]? にヒットしました")
	if !line.MatchString(body.response()) {
		t.Errorf("実行役の名前を言う文が無い: %s", body.response())
	}
}

func TestADirectHitHasNoUnwrappedField(t *testing.T) {
	t.Parallel()
	body := judgeAsLauncher(t, "rm -f .ccnavi/config/common/rules.yml")
	if body.s("verdict") != "deny" {
		t.Fatalf("deny のはず: %s", body.response())
	}
	if v, has := body["unwrapped"]; !has || v != "" {
		t.Errorf("unwrapped は空の欄で在るはず: %v", body["unwrapped"])
	}
	mustNotHave(t, body.response(), "が実行する")
}

// `cd` で移った先から見たパスに、止める側のルールを適用する（issue #61）。

func TestTheTextShowsThePathSeenFromWhereItMoved(t *testing.T) {
	t.Parallel()
	body := judgeAsLauncher(t, "cd .claude && echo x > settings.json")
	mustHave(t, body.response(), "`cd` で移った先から見ると", ".claude/settings.json")
}

func TestACdWithAnUnreadableDestinationKeepsTheReading(t *testing.T) {
	t.Parallel()
	// 縮退させない。縮退すると生の文字列で見るので、コマンドの頭に固定して書かれた保護がヒットしなくなる。
	for _, subj := range []string{
		`cd "$(pwd)" && rm -f /repo/.ccnavi/config/common/rules.yml`,
		"cd - && cp /tmp/x .ccnavi/config/common/rules.yml",
		"pushd .claude && mv .ccnavi/config/common/rules.yml /tmp/x",
	} {
		t.Run(subj, func(t *testing.T) {
			t.Parallel()
			body := judgeAsLauncher(t, subj)
			assertDeniedBy(t, subj, body, settingFiles)
			if body.s("degraded") != "" {
				t.Errorf("縮退している: %s", body.response())
			}
		})
	}
	// 読み自体は変わらない。読み切れなかった扱い（PARSE_UNCERTAIN）にはしない。
	body := judgeAsLauncher(t, "cd - && cat README.md")
	if body.s("degraded") != "" || body.s("code") != "UNDECLARED" {
		t.Errorf("縮退せず UNDECLARED のはず: %v", body)
	}
}

// コマンド置換・改行・プロセス置換を読んだあとの判定。

// substCase は (subject, 判定, ヒットするルールの id, 根拠コード)。id が空ならどのルールにもヒットしないこと、コードが空なら見ない。
type substCase struct{ subject, want, rule, code string }

func checkSubst(t *testing.T, cases []substCase) {
	t.Helper()
	subjects := make([]string, len(cases))
	for i, c := range cases {
		subjects[i] = c.subject
	}
	for i, body := range judgeMany(t, "Bash", subjects, nil) {
		c := cases[i]
		if !body.known() || body.s("verdict") != c.want {
			t.Errorf("%q: verdict = %q, want %q", c.subject, body.s("verdict"), c.want)
		}
		if c.rule != "" && !body.hits(c.rule) {
			t.Errorf("%q: %s にヒットしていない: %v", c.subject, c.rule, body.hit())
		}
		if c.rule == "" && c.want != "skip" && len(body.hit()) != 0 {
			t.Errorf("%q: どのルールにもヒットしないはず: %v", c.subject, body.hit())
		}
		if c.code != "" && body.s("code") != c.code {
			t.Errorf("%q: code = %q, want %q", c.subject, body.s("code"), c.code)
		}
	}
}

func TestOnlyACommandCutOutOfAQuoteGetsTheNote(t *testing.T) {
	t.Parallel()
	quoted := judgeBash(t, `gh issue create --title t --body "use $(git push) here"`)
	mustHave(t, quoted.response(), "二重引用符の中", "ccnavi-git.sh commit -F <file>") // ラッパーのある木では文字として渡す道もラッパーで案内する
	mustNotHave(t, quoted.response(), "(git commit -F")
	// 引用の中の git push は、組み込みの push と生の git の両方にヒットする。
	found := false
	for _, q := range quoted.quoted() {
		found = found || q == "builtin:guard-raw-git"
	}
	if !found {
		t.Errorf("quoted に builtin:guard-raw-git が無い: %v", quoted.quoted())
	}

	bare := judgeBash(t, "echo $(git push origin main)")
	mustNotHave(t, bare.response(), "二重引用符の中")
	if len(bare.quoted()) != 0 {
		t.Errorf("引用の外の置換に quoted が付いている: %v", bare.quoted())
	}
}

func TestEachDegradedReasonHasItsOwnNote(t *testing.T) {
	t.Parallel()
	seen := map[string]bool{}
	for _, c := range []struct{ subject, reason, phrase string }{
		{`echo "git push`, reasonUnterminated, "引用符かヒアドキュメントが閉じていない"},
		{"echo $(git push", reasonUnterminatedSubst, "$( ) が閉じていない"},
		{"bash -c x; git push origin main", reasonTakenAsCode, "コードとして実行するものに渡している"},
	} {
		body := judgeBash(t, c.subject)
		if body.s("degraded") != c.reason {
			t.Errorf("%q: degraded = %q, want %q", c.subject, body.s("degraded"), c.reason)
		}
		mustHave(t, body.response(), c.phrase)
		seen[body.response()] = true
	}
	if len(seen) != 3 {
		t.Error("理由の違う縮退に同じ文面を返した")
	}
}

func TestABackquoteIsAlwaysStopped(t *testing.T) {
	t.Parallel()
	// 二重引用の中でも、区切りを引用しないヒアドキュメントの中でも実行される。
	const code = "DENY_BACKQUOTE"
	checkSubst(t, []substCase{
		{"echo `find . -delete`", "deny", backquote, code},
		{"grep -n \"`rm -rf /tmp/x`\" f", "deny", backquote, code},
		{"cat <<EOF\nuse `rm -rf /tmp/x`\nEOF", "deny", backquote, code},
		{"gh issue create --title t --body \"use `git push` here\"", "deny", backquote, code},
		{"gh issue create --title t --body \"odd ` backtick\"", "deny", backquote, code},
		{"echo \"$(echo `id`)\"", "deny", backquote, code},
		// 文字として渡す表記は止まらない。
		{"grep -n \"\\`git push\\`\" f", "allow", "prefer-read-grep", ""},
		{"grep -n '`git push`' f", "allow", "prefer-read-grep", ""},
	})
	body := judgeBash(t, "gh issue create --title t --body \"use `x` here\"")
	mustHave(t, body.response(), "$( )", "--body-file")
}

func TestACommandNameDecidedAtRunTimeIsAlwaysStopped(t *testing.T) {
	t.Parallel()
	// どれもどのルールにもヒットせず、auto では権限モードに渡っていた。
	const code = "DENY_COMMAND_NAME_EXPANSION"
	checkSubst(t, []substCase{
		{"c=git; $c push origin main", "deny", nameRule, code},
		{"$(echo git) push origin main", "deny", nameRule, code},
		{"/usr/bin/gi? push origin main", "deny", nameRule, code},
		{"git${IFS}push origin main", "deny", nameRule, code},
		{"/bin/r? -rf /tmp/x", "deny", nameRule, code},
		{"FOO=1 $c status", "deny", nameRule, code},
		{">/dev/null $c status", "deny", nameRule, code},
		{"2>&1 $c status", "deny", nameRule, code},
		{"env $c status", "deny", nameRule, code},
		{">/dev/null env $c status", "deny", nameRule, code},
		{"sudo -u me $c status", "deny", nameRule, code},
		{"sh $G status", "deny", nameRule, code},
		{"if $c status; then :; fi", "deny", nameRule, code},
		{"{fd}>/dev/null $c status", "deny", nameRule, code},
		{`FOO=1 eval "$c status"`, "deny", nameRule, code},
		// 外側が縮退する `sh -c` と `eval` の文字列の中は、止めずに確認にする。
		{`sh -c "$c status"`, "ask", "", "PARSE_UNCERTAIN"},
		{`eval "$(pyenv init -)"`, "ask", "", "PARSE_UNCERTAIN"},
		{`"$(git rev-parse --show-toplevel)/x.sh"`, "deny", nameRule, code},
	})
	body := judgeBash(t, "c=git; $c status")
	mustHave(t, body.response(), "`$c`", "`c=ls; $c -la` は `ls -la`") // 書き直しの例に、止まる形（生の git）を使わない
	mustNotHave(t, body.response(), "`git status`")
}

func TestABraceExpansionDenialSaysHowToRewrite(t *testing.T) {
	t.Parallel()
	body := judgeBash(t, "grep -rn x --exclude-dir={node_modules,.git} .")
	mustHave(t, body.response(), "`{node_modules,.git}`", "--exclude-dir=a --exclude-dir=b")
	// 読めなかったのではない。読めなかった断りを付けない。
	mustNotHave(t, body.response(), "PARSE_UNCERTAIN", "生の文字列")
}
