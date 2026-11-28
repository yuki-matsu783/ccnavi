package core

import (
	"path/filepath"
	"strings"
	"testing"
)

// ルールの `when:`（組み合わせ条件）と `except:`（本体にヒットしても、除くものにヒットすればヒットしない扱い）を、
// hook の判定・`--explain`・`--test-expectations` で見る。どのルールも言及しない呼び出しが確認になることもここで見る。
// 読み込み・組み合わせの評価・lint・設定のマージ・提案が書き戻す形は go/internal/policy と go/internal/entry/suggest の go test が見る。
// 強さの順（deny > ask > allow）と読み切れないコマンドの扱いは go/internal/hook/judge/sections_test.go が見る。

// vaultRule は「*.crt か、vault/ の下で .md でないもの」。組み込みの認証情報の土台（builtin:credentials）にヒットしない名前にしてある
// （土台は設定で切れず、ヒットすると when の検査が土台の deny に紛れる）。
var vaultRule = rule{
	"id": "vault", "match": "Write|Edit", "message": "キーは書かない",
	"when": map[string]any{"any": []any{
		map[string]any{"glob": "*.crt"},
		map[string]any{"all": []any{map[string]any{"glob": "*/vault/*"}, map[string]any{"not": map[string]any{"glob": "*.md"}}}},
	}},
}

// workspaceRoot は workspace-root を except で書いた形。rules.yml の 1 文字ずつの場合分けを置き換える。{root} は実行ファイルが読み替える。
func workspaceRootRule() rule {
	const r = "{root}"
	return rule{
		"id": "workspace-root", "match": "Write|Edit|NotebookEdit", "regex": "^" + r + `[\\/]`, "message": "main では編集しない",
		"except": []rule{
			{"regex": "^" + r + `[\\/]wip[\\/]handoff(?:[\\/]|$)`},
			{"regex": "^" + r + `[\\/]scratchpad(?:[\\/]|$)`},
			{"regex": "^" + r + `[\\/]\.claude[\\/]worktrees(?:[\\/]|$)`},
		},
	}
}

// publishSays は Bash の判定（deny か ask か）と、publish ルールが止めたか。
func publishSays(t *testing.T, root, command string) (decision string, byPublish bool) {
	out := judgeTool(t, root, "Bash", command, "--guard-core-files", "disable")
	return str(out, "permissionDecision"), strings.Contains(str(out, "permissionDecisionReason"), "(rule: .ccnavi/config/common/rules.yml:publish)")
}

// publishCases は when・except のどちらで書いても同じ結末になる npm publish の呼び方。
// sudo の中で実行するコマンドにも同じにヒットし、Bash ではコマンド列の全体に適用する（lint が warn で言う形）。
func checkPublish(t *testing.T, root string, cases []struct {
	command  string
	decision string
	hit      bool
}) {
	t.Helper()
	for _, c := range cases {
		if d, hit := publishSays(t, root, c.command); d != c.decision || hit != c.hit {
			t.Errorf("%q: (%s, %v), want (%s, %v)", c.command, d, hit, c.decision, c.hit)
		}
	}
}

func TestWhenJudgesPathsAndCommands(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{
		"deny": []rule{vaultRule, {
			"id": "publish", "match": "Bash", "message": "公開しない",
			"when": map[string]any{"all": []any{
				map[string]any{"regex": `(^|\x00)npm publish\b`},
				map[string]any{"not": map[string]any{"regex": `--dry-run\b`}},
			}},
		}},
		"allow": []rule{{"id": "all", "match": "Read", "glob": "*"}},
	})
	if d := str(judgeTool(t, root, "Write", filepath.Join(root, "k.crt"), "--guard-core-files", "disable"), "permissionDecision"); d != "deny" {
		t.Errorf("k.crt = %s", d)
	}
	if d := str(judgeTool(t, root, "Write", filepath.Join(root, "vault", "a.md"), "--guard-core-files", "disable"), "permissionDecision"); d == "deny" {
		t.Errorf("vault の .md が止まった")
	}
	checkPublish(t, root, []struct {
		command  string
		decision string
		hit      bool
	}{
		{"npm publish", "deny", true},
		{"npm publish --dry-run", "ask", false},
		{"sudo npm publish", "deny", true},
		{"sudo npm publish --dry-run", "ask", false},
		{"npm publish; echo --dry-run", "ask", false}, // コマンド列の全体に適用する
	})
}

func TestExceptJudgesPathsAndCommands(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{
		"deny": []rule{workspaceRootRule(), {
			"id": "publish", "match": "Bash", "regex": `(^|\x00)npm publish\b`, "message": "公開しない",
			"except": []rule{{"regex": `(^|\x00)npm publish --dry-run\b`}},
		}},
		"allow": []rule{{"id": "all", "match": "Read", "glob": "*"}},
	})
	if d := str(judgeTool(t, root, "Write", filepath.Join(root, "README.md"), "--guard-core-files", "disable"), "permissionDecision"); d != "deny" {
		t.Errorf("README.md = %s", d)
	}
	if d := str(judgeTool(t, root, "Write", filepath.Join(root, "wip", "handoff", "a.md"), "--guard-core-files", "disable"), "permissionDecision"); d == "deny" {
		t.Errorf("except の下書きの置き場が止まった")
	}
	checkPublish(t, root, []struct {
		command  string
		decision string
		hit      bool
	}{
		{"npm publish", "deny", true},
		{"npm publish --dry-run", "ask", false},
		{"sudo npm publish", "deny", true}, // sudo が中で実行するコマンドにも、同じ except が効く
		{"sudo npm publish --dry-run", "ask", false},
		{"npm publish; npm publish --dry-run", "ask", false}, // Bash ではコマンド列の全体に適用する
	})
}

// `--explain` が組み合わせ条件を出し、`--test-expectations` が when のルールを確かめる。
func TestWhenCommandLine(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{"deny": []rule{vaultRule}, "allow": []rule{{"id": "all", "match": "Read", "glob": "*"}}})
	t.Run("explain", func(t *testing.T) {
		out := ccnavi(t, root, nil, "--explain").Stdout
		mustContain(t, "--explain", out, "when: any(glob '*.crt', all(glob '*/vault/*', not(glob '*.md')))", "        any\n          glob '*.crt'\n")
	})
	t.Run("expectations", func(t *testing.T) {
		expectations := writeFile(t, root, "expectations.yml", mustJSON(t, map[string]any{
			"deny": []rule{
				{"tool": "Write", "subject": "/repo/k.crt", "why": "キー"},
				{"tool": "Write", "subject": "/repo/vault/token", "why": "秘密"},
			},
			"ask": []rule{{"tool": "Write", "subject": "/repo/vault/README.md", "why": "説明"}},
		}))
		body := decode(t, ccnavi(t, root, nil, "--test-expectations", expectations, "--json").Stdout)
		hit := body["expectations"].([]any)[0].(map[string]any)["rules"].([]any)[0].(map[string]any)
		if body["mismatches"] != 0.0 || hit["id"] != ".ccnavi/config/common/rules.yml:vault" || hit["kind"] != "when" {
			t.Errorf("body = %v", body)
		}
	})
}

// どのルールも言及しなければ確認になり、危険の表明ではないと言う。既定が許可ではなく確認であること。
// allow を書き切るまで、言及されていない呼び出しはユーザが見る側になる。
func TestUndeclaredCallIsAnAskAndSaysItIsNotAWarning(t *testing.T) {
	t.Parallel()
	root := emptyRoot(t)
	putCommonRules(t, root, map[string]any{"deny": []rule{{"id": "push", "match": "Bash", "glob": "*git push*", "message": "文面"}}})
	out := judgeTool(t, root, "Bash", "ls -la")
	if str(out, "permissionDecision") != "ask" {
		t.Errorf("out = %v", out)
	}
	// ccnavi は判定を持たないからユーザに聞く。危険だと書くと、受け取った側は存在しない危険を探しに行く。
	// 繰り返しを止める先はルールの側。そこを言わないと同じ問いが出続ける。
	mustContain(t, "理由", str(out, "permissionDecisionReason"), "UNDECLARED", "not a warning about the call itself", "allow section")
}
