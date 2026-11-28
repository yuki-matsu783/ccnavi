package core

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// 設定検証（--lint）の受入テスト。標準出力と終了コードだけを見る。
// 見るのは、error があれば非ゼロで終わること、error と warn が分かれていること、検証が判定と同じ読み込みを使っていること。
// 3 つ目が成り立たないと、検証が通ったのに実運用で失敗するという、検証があるぶんかえって危ない形になる。

type rule = map[string]any

// sound は不備の無いルール 1 件。ここに 1 つずつ壊した欄を足して試す。
var sound = rule{
	"id":      "git-push",
	"match":   "Bash",
	"glob":    "*git push*",
	"message": "git push is not run by the agent. Ask the user to push.",
}

// allowed は検証を通すのに要る最小の allow。無いと「allow が空」の warn が毎回 1 件増え、数を見るテストの主題と関係の無い 1 件を数える。
var allowed = rule{"id": "anything", "match": "Read", "regex": "."}

// with は rule の写しに欄を足す・上書きする。
func with(r rule, kv ...any) rule {
	out := rule{}
	for k, v := range r {
		out[k] = v
	}
	for i := 0; i+1 < len(kv); i += 2 {
		out[kv[i].(string)] = kv[i+1]
	}
	return out
}

// rulesFile は共通設定の既定の場所にルールファイルを 1 本置く。並べたルールは deny のタイプに入る。
func rulesFile(t testing.TB, dir string, rules ...rule) string {
	return rulesFileOpt(t, dir, 1, true, rules...)
}

func rulesFileOpt(t testing.TB, dir string, version int, withAllow bool, rules ...rule) string {
	t.Helper()
	body := map[string]any{"version": version, "deny": rules}
	if withAllow {
		body["allow"] = []rule{allowed}
	}
	return writeFile(t, dir, base.CommonRelpath("rules"), mustJSON(t, body))
}

// 提案 1 枚と承認済みチケット 1 枚の最小の中身。置き場の話だけを見るので、範囲は 1 項でよい。
const ticketText = `---
version: 1
ticket: %s
title: t
allow:
- {glob: "src/*", match: "Write|Edit"}
---
`

// copyText は承認済みチケット。`ccnavi_approved` が無いと承認済みチケットとして読まれない。
const copyText = `---
version: 1
ticket: %s
title: t
issue: 1
mr: 1
ccnavi_approved: {approved_at: "2026-01-01T00:00:00Z", source_tree: "", source_path: p}
allow:
- {glob: "src/*", match: "Write|Edit"}
---
`

func ticketOf(text, name string) string { return strings.ReplaceAll(text, "%s", name) }

func lint(t testing.TB, root, rulesPath string, extra ...string) run.Result {
	t.Helper()
	return ccnavi(t, root, nil, append([]string{"--lint", "--rules", rulesPath}, extra...)...)
}

// lintCase は 1 本の使い捨ての木（CCNAVI.md 入り）にルールを置いて検証する 1 回。
// wantCode・errors・warns は -1 なら見ない。
type lintCase struct {
	name     string
	rules    func(t *testing.T, root string) string // nil なら sound だけのルールファイル
	setup    func(t *testing.T, root string)
	wantCode int
	contains []string
	absent   []string
	errors   int
	warns    int
}

func TestLintReports(t *testing.T) {
	t.Parallel()
	approved := func(state string) string { return filepath.Join(".ccnavi", "approved", state) }
	cases := []lintCase{
		{
			name: "不備が無ければ何も指摘せずに0で終わる", wantCode: 0, errors: 0, warns: 0,
			// どのファイルを見た結果なのかを示さない報告は、別の設定についての報告と見分けが付かない。
			contains: []string{"rules.yml"},
		},
		{
			// block モードではこれが全ツール呼び出しの拒否になり、直すための呼び出しまで止まる。検証がいちばん先に見つけなければならない形。
			name: "読めないルールはerrorで非ゼロで終わる", wantCode: 1, errors: -1, warns: -1,
			rules: func(t *testing.T, root string) string {
				return writeFile(t, root, "rules.yml", "version: 2\ndeny: [\n  - id: x\n")
			},
			contains: []string{"error:", "ルールを読めない"},
		},
		{
			name: "無いルールファイルもerrorになる", wantCode: 1, errors: 1, warns: -1,
			rules: func(t *testing.T, root string) string { return filepath.Join(root, "どこにも無い.yml") },
		},
		{
			name: "版が違うルールはerrorになる", wantCode: 1, errors: -1, warns: -1,
			rules: func(t *testing.T, root string) string {
				return rulesFileOpt(t, root, 99, true, sound)
			},
			contains: []string{"版"},
		},
		{
			// 動かす途中で止まった形跡（コピーできたが消せなかった）。状態の操作は「複数の場所にある」で止まるので、CI が先に名指しする。
			// 作業中とレビュー待ちだけを横断して数えると、閉じた側との重複だけが通る。
			name: "同じ識別子がdoingとdoneの両方に在ればerrorになる", wantCode: 1, errors: -1, warns: -1,
			setup: func(t *testing.T, root string) {
				for _, state := range []string{"doing", "done"} {
					writeFile(t, root, filepath.Join(approved(state), "i0001.md"), ticketOf(copyText, "i0001"))
				}
			},
			contains: []string{"i0001 が複数の場所にある", "(ワークスペースルート):doing", "(ワークスペースルート):done"},
		},
		{
			// review/ は提案の走査に入る。doing と done の側を足したときに、数え方を変えたせいでこちらが何も言わなくならないことを固定する。
			name: "同じ識別子がdoingとreviewの両方に在ればerrorになる", wantCode: 1, errors: -1, warns: -1,
			setup: func(t *testing.T, root string) {
				writeFile(t, root, filepath.Join(approved("doing"), "i0001.md"), ticketOf(copyText, "i0001"))
				writeFile(t, root, "wip/proposals/review/i0001.md", ticketOf(copyText, "i0001"))
			},
			contains: []string{"i0001 が複数の場所にある", "(ワークスペースルート):review"},
		},
		{
			// 閉じた記録が 1 つ在るだけの、いちばん普通の形。
			name: "doneに1つだけ在るのは指摘しない", wantCode: 0, errors: -1, warns: -1,
			setup: func(t *testing.T, root string) {
				writeFile(t, root, filepath.Join(approved("done"), "i0001.md"), ticketOf(copyText, "i0001"))
			},
			absent: []string{"複数の場所にある"},
		},
		{
			// BOM は目に見えないので、`---` と書いたのに拒まれたように見える。文面が原因を言わないと、書いたユーザはエディタで見えているものを疑えない。
			name: "BOMの付いた提案はerrorでBOMを名指しする", wantCode: 1, errors: -1, warns: -1,
			setup: func(t *testing.T, root string) {
				writeFile(t, root, "wip/proposals/todo/i0001.md", "\ufeff"+ticketOf(ticketText, "i0001"))
			},
			contains: []string{"BOM (U+FEFF)", "i0001.md"},
		},
		{
			// 読めないファイルが置き場に残っていること自体は書いたユーザの思い違いで、承認済みチケットは親のブランチに乗って
			// 他の機械へそのまま届く。閉じた側の苦情を破棄すると、届いた先でも何も言われないままになる。
			name: "BOMの付いた承認済みチケットはdoneに在ってもerrorになる", wantCode: 1, errors: -1, warns: -1,
			setup: func(t *testing.T, root string) {
				writeFile(t, root, filepath.Join(approved("done"), "i0001.md"), "\ufeff"+ticketOf(copyText, "i0001"))
			},
			contains: []string{"を読めない", "BOM (U+FEFF)"},
		},
		{
			// 承認の正とするのは置き場。`.ccnavi/approved/` は組み込みの保護がエージェントの書き込みを止めるので、
			// `ccnavi_approved` が無くても承認済みとして読む。
			name: "承認の記録が無くても承認済みの置き場なら読む", wantCode: -1, errors: -1, warns: -1,
			setup: func(t *testing.T, root string) {
				writeFile(t, root, filepath.Join(approved("doing"), "i0001.md"), ticketOf(ticketText, "i0001"))
			},
			absent: []string{"ccnavi_approved", "を読めない"},
		},
		{
			// `wip/proposals/review/` はエージェントが書ける側にある。保護が組み込みの deny 1 枚しか無いので、
			// `finish` が書く `completed_at` を 2 枚目の保護として求める。
			name: "レビュー待ちの置き場ではfinishの欄を求める", wantCode: 1, errors: -1, warns: -1,
			setup: func(t *testing.T, root string) {
				writeFile(t, root, "wip/proposals/review/i0001.md", ticketOf(ticketText, "i0001"))
			},
			contains: []string{"`completed_at` がありません。"},
			absent:   []string{"BOM (U+FEFF)"},
		},
		{
			// 何も止めないガードは、入っていないガードと同じでありながら、入っているように見える。いちばん見つけにくい不具合なので error。
			name: "denyが1件も無いのはerrorになる", wantCode: 1, errors: -1, warns: -1,
			rules:    func(t *testing.T, root string) string { return rulesFile(t, root) },
			contains: []string{"`deny` が空"},
		},
		{
			// 判定は動いている。ただし、どのルールも言及しない呼び出しがすべて Claude Code の権限モードに従うので、確認が出続ける状態と区別が付かない。
			name: "allowが1件も無いのはwarnになる", wantCode: 0, errors: -1, warns: -1,
			rules:    func(t *testing.T, root string) string { return rulesFileOpt(t, root, 1, false, sound) },
			contains: []string{"`allow` が空"},
		},
		{
			// error と warn を分ける意味そのもの。ガードは動いているので CI を失敗させる必要は無いが、守っているつもりの穴は開いている。
			// 重複は 2 件目だけを報告する。1 件目は、他に同じ id が無ければそのままで正しいルールだから。
			name: "判定は動くが効かない記述はwarnで0のまま", wantCode: 0, errors: 0, warns: 2,
			rules: func(t *testing.T, root string) string {
				return rulesFile(t, root, with(sound, "id", "重複"), with(sound, "id", "重複", "glob", "*rm -rf *"),
					with(sound, "id", "ヒットしないツール", "match", "Mystery|Bash"))
			},
			contains: []string{"id が重複", "Mystery"},
		},
		{
			// 権限ルール `ToolName(指定子)` の括弧の中を除いた名前は、判定が対象を取り出せる。lint の probe がその欄を渡し損ねると正しいルールを報告する。
			// WebSearch は指定子を持たず、Mystery は Claude Code に無いツール。この 2 つだけを名前ごとに報告する。
			name: "権限ルールの名前で書いたmatchは指摘しない", wantCode: 0, errors: 0, warns: 2,
			rules: func(t *testing.T, root string) string {
				return rulesFile(t, root,
					with(sound, "id", "ps", "match", "PowerShell"),
					with(sound, "id", "search", "match", "Grep|Glob", "glob", "*/secrets/*"),
					with(sound, "id", "skill", "match", "Skill", "glob", "deploy*"),
					with(sound, "id", "fetch", "match", "WebFetch", "glob", "*://example.com/*"),
					with(sound, "id", "gone", "match", "WebSearch|Mystery", "glob", "*"))
			},
			contains: []string{"WebSearch", "Mystery"},
		},
		{
			// 監視される側が書けるファイルから監視を止める記述。判定の側にはユーザが渡した off と見分ける手段が無い。
			name: "ワークツリーの中に書かれたoffはerrorになる", wantCode: 1, errors: -1, warns: -1,
			setup: func(t *testing.T, root string) {
				writeFile(t, root, ".claude/settings.json", mustJSON(t, map[string]any{"env": map[string]string{"CCNAVI_MODE": "disable"}}))
			},
			contains: []string{"settings.json", "disable"},
		},
		{
			name: "実行後チェックが登録されていなければwarnになる", wantCode: 0, errors: 0, warns: 1,
			setup: func(t *testing.T, root string) {
				writeFile(t, root, ".claude/settings.json", `{"hooks": {"PreToolUse": [{"hooks": [{"command": "ccnavi"}]}]}}`)
			},
			contains: []string{"PostToolUse"},
		},
		{
			// 実行ファイルの位置を env から取る形。そこでは名前が大文字で書かれる。大文字小文字を区別すると、正しい設定に「登録されていない」と言う。
			name: "実行ファイルを環境変数で指した登録も見つける", wantCode: -1, errors: -1, warns: -1,
			setup: func(t *testing.T, root string) {
				writeFile(t, root, ".claude/settings.json",
					`{"hooks": {"PostToolUse": [{"hooks": [{"command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_ENTRY_PATH}\""}]}]}}`)
			},
			absent: []string{"登録されていない"},
		},
		{
			// 登録はされているのに見る先が無い状態。実行後チェックは git の差分で見るので、リポジトリでない場所では 1 件も検知しない。
			name: "gitのワークツリーでなければ実行後チェックが何も見ないとwarnになる", wantCode: 0, errors: 0, warns: 1,
			setup: func(t *testing.T, root string) {
				writeFile(t, root, ".claude/settings.json", `{"hooks": {"PostToolUse": [{"hooks": [{"command": "ccnavi"}]}]}}`)
			},
			contains: []string{"ワークツリーを読めない"},
		},
		{
			// hook はユーザごとの設定にも書ける。そちらはここから見えないので、見えないものを「無い」と報告すると正しい設定に苦情を出す。
			name: "設定ファイルが無ければ登録については何も言わない", wantCode: 0, errors: 0, warns: 0,
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			root := workspace(t)
			var rulesPath string
			if c.rules != nil {
				rulesPath = c.rules(t, root)
			} else {
				rulesPath = rulesFile(t, root, sound)
			}
			if c.setup != nil {
				c.setup(t, root)
			}
			r := lint(t, root, rulesPath)
			if c.wantCode >= 0 && r.Code != c.wantCode {
				t.Errorf("code = %d, want %d: %s%s", r.Code, c.wantCode, r.Stdout, r.Stderr)
			}
			if c.errors >= 0 || c.warns >= 0 {
				e, w := counts(t, r.Stdout)
				if c.errors >= 0 && e != c.errors || c.warns >= 0 && w != c.warns {
					t.Errorf("error %d 件 warn %d 件, want %d / %d: %s", e, w, c.errors, c.warns, r.Stdout)
				}
			}
			mustContain(t, "報告", r.Stdout, c.contains...)
			mustNotContain(t, "報告", r.Stdout, c.absent...)
		})
	}
}

// 不正なルールは 1 件ずつ id で名指しする。除かれたルールは何も言われずに消え、消えた穴は誰も気づかない。
// 名前にはタイプが付く。同じ id が別のタイプに居ることがあるので、どちらの話なのかを名前が言えないと直しに行く先が決まらない。
func TestLintNamesEachInvalidRuleByIDAndType(t *testing.T) {
	t.Parallel()
	root := workspace(t)
	names := []string{"文面無し", "match無し", "適用するもの無し", "二重指定", "組み立て不能", "先読み"}
	r := lint(t, root, rulesFile(t, root,
		with(sound, "id", names[0], "message", ""),
		with(sound, "id", names[1], "match", ""),
		with(sound, "id", names[2], "glob", "", "regex", ""),
		with(sound, "id", names[3], "regex", "git push"),
		with(sound, "id", names[4], "glob", "", "regex", "git push ("),
		with(sound, "id", names[5], "glob", "", "regex", "git (?=push)")))
	if r.Code != 1 {
		t.Errorf("code = %d", r.Code)
	}
	for _, name := range names {
		mustContain(t, name+" の報告", r.Stdout, "error: deny:"+name+":")
	}
}

// ask の文面はユーザの確認ダイアログにしか出ず、allow の文面はどこにも出ない。届かない欄を残さず error にする。
// ただしルールごと除くと、文面を書いただけで ask が外れて通るので、読み込みは通す（--explain に載る）。
func TestLintRejectsMessageOnAskAndAllowButKeepsTheRules(t *testing.T) {
	t.Parallel()
	root := workspace(t)
	body := map[string]any{
		"version": 1,
		"deny":    []rule{sound},
		"ask": []rule{
			{"id": "mig", "match": "Write", "glob": "*/migrations/*", "message": "ユーザが見る"},
			{"id": "quiet", "match": "Write", "glob": "*/quiet/*"},
		},
		"allow": []rule{with(allowed, "message", "通す")},
	}
	path := writeFile(t, root, "rules.yml", mustJSON(t, body))
	r := lint(t, root, path, "--mode", "enable")
	if r.Code != 1 {
		t.Errorf("code = %d", r.Code)
	}
	mustContain(t, "報告", r.Stdout, "error: mig: ask に message がある", "error: anything: allow に message がある")
	mustNotContain(t, "文面の無い ask は報告しない", r.Stdout, "quiet")
	shown := ccnavi(t, root, nil, "--explain", "--rules", path, "--mode", "enable").Stdout
	mustContain(t, "--explain", shown, "mig", "quiet")
}

// モードの値の扱い。止めないモードと読めない値は warn で、ガードは弱まらない。
func TestLintModeProblemsAreWarns(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, mode string
		warns      int
		contains   []string
	}{
		// 戻す働きの 2 つは、書かれた値が enable でもモードに合わせて dry-run になる。実効値で見るので、そのぶんも言う。
		{"止めないモードはwarnとして報告される", "dry-run", 3,
			[]string{"dry-run なので一般の保護は報告だけにする", "CCNAVI_MODE=dry-run なので実際は dry-run"}},
		// block になるのでガードは弱まらない。
		{"モードとして読めない値はwarnとして報告される", "blocking", 1, []string{"blocking"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			root := workspace(t)
			r := lint(t, root, rulesFile(t, root, sound), "--mode", c.mode)
			if r.Code != 0 {
				t.Errorf("code = %d: %s", r.Code, r.Stdout)
			}
			if e, w := counts(t, r.Stdout); e != 0 || w != c.warns {
				t.Errorf("error %d 件 warn %d 件, want 0 / %d: %s", e, w, c.warns, r.Stdout)
			}
			mustContain(t, "報告", r.Stdout, c.contains...)
		})
	}
}

// 切り替えの環境変数の誤りは error、切った状態は warn で言う。値ごとの文面は go/internal/entry/lint の見本（TestReportSwitches）が見る。
// ここは終了コードと `--json` の problems に出るところだけを通しで見る。
func TestLintSwitchMistakesAreErrorsAndSwitchedOffIsWarn(t *testing.T) {
	t.Parallel()
	root := workspace(t)
	rules := rulesFile(t, root, sound)
	// 承認の切り替えは enable か disable しか取らない。dry-run と書いたユーザは止まらないつもりでいるのに、実際は enable と同じに止める。
	// 実際の値も言わないと、止まっているのか通っているのかが分からない。
	wrong := ccnavi(t, root, nil, "--lint", "--rules", rules, "--mode", "enable",
		"--guard-ticket-approval", "dry-run", "--ticket-control", "off")
	if wrong.Code != 1 {
		t.Errorf("code = %d: %s%s", wrong.Code, wrong.Stdout, wrong.Stderr)
	}
	mustContain(t, "誤りの報告", wrong.Stdout, "CCNAVI_GUARD_TICKET_APPROVAL=dry-run", "CCNAVI_TICKET_CONTROL=off", "enable として動いている")

	// 切ってあること自体は設定として正しいので error にはしない。読み手は CI と VS Code の拡張なので、`--json` の problems にも載せる。
	switched := ccnavi(t, root, map[string]string{"CCNAVI_GUARD_CORE_FILES": "disable", "CCNAVI_RESTORE_IF_DENY": "dry-run"},
		"--lint", "--json", "--rules", rules, "--mode", "enable")
	if switched.Code != 0 {
		t.Fatalf("code = %d: %s%s", switched.Code, switched.Stdout, switched.Stderr)
	}
	var payload struct {
		Errors, Warns int
		Problems      []struct{ Detail string }
	}
	if err := json.Unmarshal([]byte(switched.Stdout), &payload); err != nil {
		t.Fatal(err)
	}
	var details []string
	for _, p := range payload.Problems {
		details = append(details, p.Detail)
	}
	all := strings.Join(details, "\n")
	if payload.Errors != 0 || payload.Warns != 2 {
		t.Errorf("errors = %d, warns = %d: %s", payload.Errors, payload.Warns, all)
	}
	mustContain(t, "problems", all, "CCNAVI_GUARD_CORE_FILES=disable", "CCNAVI_RESTORE_IF_DENY=dry-run")
}

// 別の読み方をすると、検証は通ったのに実運用で失敗する。同じ不正なルールについて、検証が名指しするものと、判定が走るときに苦情を言うものが
// 一致することで確かめる。
func TestLintUsesTheSameLoadingAsTheVerdict(t *testing.T) {
	t.Parallel()
	root := workspace(t)
	path := rulesFile(t, root, sound, with(sound, "id", "組み立て不能", "glob", "", "regex", "("))
	checked := lint(t, root, path)
	decided := run.Ccnavi(t, []string{"--root", root, "--mode", "enable"}, run.Opts{
		Dir:   base.Root(),
		Input: `{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git status"}}`,
	})
	mustContain(t, "検証", checked.Stdout, "組み立て不能")
	mustContain(t, "判定の stderr（判定と検証が別のことを言っている）", decided.Stderr, "組み立て不能")
}

// 振り分けの sh を指す env（設計 launcher-scripts 9 節）。hook は sh を直に起動するので、実行ビットが無いと 126 で起動しない。
// 判定そのものが動いていないので error。
func TestLintRefusesAnUnrunnableLauncher(t *testing.T) {
	t.Parallel()
	if runtime.GOOS == "windows" {
		t.Skip("実行ビットは POSIX でだけ見る")
	}
	root := workspace(t)
	launcher := writeFile(t, root, ".ccnavi/scripts/ccnavi-launcher.sh", "")
	rules := rulesFile(t, root, sound)
	writeFile(t, root, ".claude/settings.json",
		mustJSON(t, map[string]any{"env": map[string]string{"CCNAVI_ENTRY_PATH": ".ccnavi/scripts/ccnavi-launcher.sh"}}))

	if err := os.Chmod(launcher, 0o755); err != nil {
		t.Fatal(err)
	}
	runnable := lint(t, root, rules)
	if err := os.Chmod(launcher, 0o644); err != nil {
		t.Fatal(err)
	}
	r := lint(t, root, rules)

	if e, _ := counts(t, runnable.Stdout); e != 0 {
		t.Errorf("実行できる sh に error: %s", runnable.Stdout)
	}
	if e, _ := counts(t, r.Stdout); r.Code != 1 || e != 1 {
		t.Errorf("code = %d, errors = %d: %s", r.Code, e, r.Stdout)
	}
	named := false
	for _, line := range strings.Split(r.Stdout, "\n") {
		named = named || strings.HasPrefix(line, "error:") && strings.Contains(line, "ccnavi-launcher.sh")
	}
	if !named {
		t.Errorf("実行できない sh を error で名指ししていない: %s", r.Stdout)
	}
}

// VS Code 拡張が読む形（docs/11_requirements/json-shapes.md「lint の JSON」）。文面の版と同じ判定を同じ深刻度で返す。
// error があれば終了コードも同じく非ゼロ。
func TestLintJSONReturnsTheSameComplaintsWithTheSameExitCode(t *testing.T) {
	t.Parallel()
	root := workspace(t)
	path := writeFile(t, root, "rules.yml", "version: 2\ndeny: [\n  - id: x\n")
	r := lint(t, root, path, "--json", "--mode", "dry-run")
	if r.Code != 1 {
		t.Errorf("code = %d: %s", r.Code, r.Stdout)
	}
	var body struct {
		Version  int
		Rules    string
		Mode     string
		Projects []any
		Errors   int
		Warns    int
		Problems []map[string]string
	}
	if err := json.Unmarshal([]byte(r.Stdout), &body); err != nil {
		t.Fatal(err)
	}
	if body.Version != 1 || body.Rules != path || body.Mode != "dry-run" || len(body.Projects) != 0 || body.Errors != 1 || body.Warns < 1 {
		t.Errorf("body = %+v", body)
	}
	if body.Errors+body.Warns != len(body.Problems) {
		t.Errorf("件数 %d + %d と problems %d が合わない", body.Errors, body.Warns, len(body.Problems))
	}
	severities := map[string]bool{}
	var firstError string
	for _, p := range body.Problems {
		severities[p["severity"]] = true
		if p["severity"] == "error" && firstError == "" {
			firstError = p["detail"]
		}
	}
	if len(severities) != 2 || !severities["error"] || !severities["warn"] {
		t.Errorf("severities = %v", severities)
	}
	mustContain(t, "最初の error", firstError, "ルールを読めない")
	for k := range body.Problems[0] {
		if k != "severity" && k != "where" && k != "detail" {
			t.Errorf("problems の欄に余分な %q", k)
		}
	}
}
