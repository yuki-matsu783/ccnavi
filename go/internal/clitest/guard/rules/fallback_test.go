package rules

import (
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/infra/env"
)

// ルールファイルを読めないときの受入テスト。「ガードが機能しなくなったときの振る舞い」という独立した関心。
//
// ルールは `--rules` では渡さない（診断でだけ有効で、hook の判定には届かない）。読めないルールは `--root` の下の
// 共通レイヤーに置く。コアファイルのバックアップと復元は切る（一時の木なのでリポジトリは汚さないが、見たいものは
// 組み込みの既定で、設定に依らない）。

// YAML として不正である。閉じていないリスト 1 つ。書き損じの典型。
const brokenRules = "version: 2\ndeny: [\n  - id: x\n"

// brokenRoot は共通レイヤーのルールが読めないワークスペースと、そのルールのパス。
func brokenRoot(t testing.TB) (root, broken string) {
	t.Helper()
	root = base.TempDir(t)
	broken = base.CommonPath(root, "rules")
	write(t, broken, brokenRules)
	return root, broken
}

func inRoot(t testing.TB, root string, body any, extra map[string]string) reply {
	t.Helper()
	e := map[string]string{env.GuardCoreFiles: "disable"}
	for k, v := range extra {
		e[k] = v
	}
	return parse(t, fire(t, root, []string{"--mode", "enable"}, body, e))
}

func TestABrokenOrMissingRuleFileKeepsTheSessionRunning(t *testing.T) {
	t.Parallel()
	// ここが要件の中心。拒否にすると、不正なファイルを直すための呼び出しまで止まって回復できなくなる。
	// 既定にはプロジェクトの allow が無いので無害な呼び出しも権限モードへの委譲になる。進めなくなるのは deny だけ。
	broken, _ := brokenRoot(t)
	for name, root := range map[string]string{"broken": broken, "missing": base.TempDir(t)} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			r := fire(t, root, []string{"--mode", "enable"}, preBash("cat README.md"), guardOff)
			if r.Code != 0 {
				t.Errorf("無害な呼び出しを止めた: %q", r.Stderr)
			}
			if got := parse(t, r).Decision; got == "deny" {
				t.Error("無害な呼び出しを拒否した")
			}
		})
	}
}

func TestFallingBackToTheDefaultsIsToldOnEveryCall(t *testing.T) {
	t.Parallel()
	// 何も言わずに既定に戻ると、ガードが働いているように見えて実際は何も見ていない状態が続く。
	root, broken := brokenRoot(t)
	out := inRoot(t, root, preBash("cat README.md"), nil)
	mustHave(t, out.text(), "built-in defaults", broken) // どのファイルが読めなかったのかも言う
}

func TestTheDefaultsLetReadsThrough(t *testing.T) {
	t.Parallel()
	// いちばん数が多く、ワークツリーを変えようがない。ここまで確認を出すと、本当に見てほしい 1 件が埋もれる。
	root, _ := brokenRoot(t)
	if got := inRoot(t, root, pre("Read", map[string]any{"file_path": "README.md"}), nil).Decision; got != "" {
		t.Errorf("読み取りに判定を返している: %q", got)
	}
}

func TestTheDefaultsStillStopIrreversibleOperations(t *testing.T) {
	t.Parallel()
	// 戻った先が何でも通す設定では、壊すだけでガードを外せることになる。
	root, _ := brokenRoot(t)
	for _, command := range []string{"rm -rf /tmp/x", "git push origin main", "git reset --hard HEAD~1"} {
		t.Run(command, func(t *testing.T) {
			t.Parallel()
			if got := inRoot(t, root, preBash(command), nil).Decision; got != "deny" {
				t.Errorf("通した: %q", got)
			}
		})
	}
}

func TestRepairIsLimitedToADraftAndTheUsersImport(t *testing.T) {
	t.Parallel()
	// 直接 Write / Edit で直す道は通さない（builtin:guard-ccnavi-config は読めない間も外れない）。
	// 残る道は 1 つで、wip/handoff/ に下書きを置き、ユーザが ccnavi user handoff で取り込む。
	root, _ := brokenRoot(t)
	rulesFile := root + "/.ccnavi/config/common/rules.yml"
	draft := root + "/wip/handoff/.ccnavi/config/common/rules.yml"
	// 自己防衛を切ると、名前で書くツールの 4 本は足されない。ここでは有効にして見る。
	guard := map[string]string{env.GuardCoreFiles: "enable"}
	if got := inRoot(t, root, pre("Read", map[string]any{"file_path": rulesFile}), guard).Decision; got == "deny" {
		t.Error("読めない")
	}
	for _, tool := range []string{"Write", "Edit"} {
		out := inRoot(t, root, pre(tool, map[string]any{"file_path": rulesFile}), guard)
		if out.Decision != "deny" {
			t.Errorf("%s で直せてしまう", tool)
		}
		mustHave(t, out.Reason, "builtin:guard-ccnavi-config", "wip/handoff/.ccnavi/config/common/rules.yml", "ccnavi-user-handoff.sh")
		if got := inRoot(t, root, pre(tool, map[string]any{"file_path": draft}), guard).Decision; got == "deny" {
			t.Errorf("%s で下書きを置けない", tool)
		}
	}
}

func TestTheDefaultsKeepTheShellFromRewritingTheConfig(t *testing.T) {
	t.Parallel()
	// 上と対になっている。ここを通すと、シェルでルールを壊し、緩んだ既定に戻る、という順路ができる。
	root, _ := brokenRoot(t)
	out := inRoot(t, root, preBash("echo x > .ccnavi/config/common/rules.yml"), nil)
	if out.Decision != "deny" {
		t.Errorf("deny のはず: %+v", out)
	}
	// 止めた先に別の方法が無いと、拒否されたまま進めなくなる。
	mustHave(t, out.Reason, "wip/handoff/.ccnavi/config/common/rules.yml", "ccnavi-user-handoff.sh")
	mustNotHave(t, out.Reason, "Write か Edit のツールで書いてください")
}

func TestTheDefaultsStopShellWritesInAnySpelling(t *testing.T) {
	t.Parallel()
	root, _ := brokenRoot(t)
	for _, command := range []string{
		"echo x >> .claude/hooks/lint-go.sh",
		"sed -i s/deny/allow/ .ccnavi/config/common/rules.yml",
		"cp /tmp/x .ccnavi/scripts/ccnavi-git.sh",
		"echo {} > .claude/settings.json",
		"cd .claude/worktrees/w && echo x > ../../scripts/ccnavi-git.sh",
		// `cd` で入ってから書く形（issue #61）。行き先のパスから場所の名前が消えるので、移った先から見たパスにも適用しないと通る。
		"cd .ccnavi/config/common && echo x > rules.yml",
		"cd .claude && echo x > settings.json",
		"cd .claude/hooks && echo x > lint-go.sh",
		"cd .claude && rm -rf hooks",
	} {
		t.Run(command, func(t *testing.T) {
			t.Parallel()
			if got := inRoot(t, root, preBash(command), nil).Decision; got != "deny" {
				t.Errorf("通した: %q", got)
			}
		})
	}
}

func TestTheDefaultShellProtectionHitsAMovedPlace(t *testing.T) {
	t.Parallel()
	// 実行ファイルは設定で動く（ccnavi ディレクトリと共通レイヤーは固定）。既定の側だけ空の設定で組んでいると、動かした
	// ワークスペースではルールファイルが不正になったときにだけそこへの書き込みが止まらない（issue #14）。
	// 絶対パスは `/` で書いて引用する。bash は引用されない `\` を取り除くので、`\` の表記のまま埋め込むと行き先が変わる。
	root, broken := brokenRoot(t)
	for _, c := range []struct {
		env     map[string]string
		command string
	}{
		{map[string]string{env.EntryPath: "tools/guard/ccnavi"}, "cp /tmp/x tools/guard/ccnavi"},
		{nil, "echo x > '" + slash(broken) + "'"},
	} {
		t.Run(c.command, func(t *testing.T) {
			t.Parallel()
			out := inRoot(t, root, preBash(c.command), c.env)
			if out.Decision != "deny" {
				t.Errorf("通した: %+v", out)
			}
			mustHave(t, out.Reason, "builtin:guard-config-via-bash")
		})
	}
}

func TestTheDefaultsDoNotBlockResolvingAMerge(t *testing.T) {
	t.Parallel()
	// 衝突マーカーの入ったルールファイルは YAML として読めないので、衝突を解いている最中は必ず既定を使っている。
	// そこで解決の手が止まると、ガードが機能しない状態から出られない。どれもファイルに新しい文面を書かない。
	root, _ := brokenRoot(t)
	for _, command := range []string{
		"sh .ccnavi/scripts/ccnavi-git.sh restore --ours -- .ccnavi/config/common/rules.yml",
		"sh .ccnavi/scripts/ccnavi-git.sh add -- .ccnavi/config/common/rules.yml",
		"cat .ccnavi/config/common/rules.yml",
		"grep -n conflict .ccnavi/config/common/rules.yml",
	} {
		t.Run(command, func(t *testing.T) {
			t.Parallel()
			if got := inRoot(t, root, preBash(command), nil).Decision; got == "deny" {
				t.Errorf("止めた: %q", command)
			}
		})
	}
}

func TestFallingBackToTheDefaultsIsRecorded(t *testing.T) {
	t.Parallel()
	// ガードが機能しないまま何回動いたかは、これでしか数えられない。
	root, broken := brokenRoot(t)
	inRoot(t, root, preBash("cat README.md"), nil)
	rows := records(t, root)
	if len(rows) != 1 {
		t.Fatalf("1 行のはず: %v", rows)
	}
	row := rows[0]
	if str(row, "fallback") != "builtin:rules" || str(row, "detail") != broken {
		t.Errorf("fallback / detail: %v", row)
	}
	// 判定には達しているので skip ではない。既定にプロジェクトの allow が無いので、権限モードに従う。
	if str(row, "decision") != "ask" || str(row, "code") != "UNDECLARED" {
		t.Errorf("ask / UNDECLARED のはず: %v", row)
	}
}

func TestTheUndeclaredTextOfAFallbackDoesNotCiteTheUnreadFile(t *testing.T) {
	t.Parallel()
	// 読めていないファイルを出所に名指しし、そこへ allow を足せと言うと、同じ応答の退避の文（先にそのファイルを直す）と食い違う。
	// 権限モードが無い payload は ask、確認できる者が居ないモードは deny になる。
	root, broken := brokenRoot(t)
	for _, c := range []struct{ mode, decision string }{{"", "ask"}, {"bypassPermissions", "deny"}} {
		t.Run(c.mode, func(t *testing.T) {
			t.Parallel()
			body := preBash("frobnicate x")
			body["permission_mode"] = c.mode
			out := inRoot(t, root, body, nil)
			if out.Decision != c.decision {
				t.Fatalf("decision = %q, want %q", out.Decision, c.decision)
			}
			i := strings.Index(out.Reason, "[ccnavi] UNDECLARED")
			if i < 0 {
				t.Fatalf("UNDECLARED の件が無い: %s", out.Reason)
			}
			undeclared := out.Reason[i:]
			mustHave(t, undeclared, "(source: (ccnavi built-in defaults))", "see the note above")
			mustNotHave(t, undeclared, broken, "allow section of")
			// 退避の文は 1 度だけ。同じ直し方を 2 度並べない。
			if n := strings.Count(out.Reason, "could not be read, so ccnavi is judging"); n != 1 {
				t.Errorf("退避の文が %d 回ある", n)
			}
		})
	}
}
