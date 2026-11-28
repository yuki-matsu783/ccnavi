package union

import (
	"ccnavi/internal/harness/configws"
	"fmt"
	"path"
	"path/filepath"
	"strings"
	"testing"
)

// 設定ファイルの保護の受入テスト。
//
// selfguard のコアに、各設定の `.ccnavi/config/project/` の 3 本と共通設定の phases / risks が入る。
// Write / Edit の拒否、シェルからの書き込みの拒否、バックアップと復元の 3 つとも、共通設定の rules.yml に掛けているものを
// そのまま掛ける。元リポジトリから切ったワークツリー側の設定も対象。
// ルールファイルは何でも通す 1 本にしてある。止まるなら、それはルールの外の組み込み。

// バックアップと復元。

func TestLayerFilesAreRestored(t *testing.T) {
	t.Parallel()
	// 守る対象は、プロジェクト・ワークスペースの設定の 3 本ずつと、共通設定の phases・risks。
	// どの対象が守られるかの組み立ては Go の selfguard の見本（backup_golden_test.go）が見る。ここは本物の hook で戻ることの通し。
	w := newWS(t, configws.KindGuard)
	configws.Write(t, configws.LayerPath(w.Root, "risk"), configws.CommonRiskText)
	var paths []string
	for _, root := range []string{w.Lib, w.Root} {
		for _, kind := range []string{"rules", "phases", "risk"} {
			paths = append(paths, configws.LayerPath(root, kind))
		}
	}
	paths = append(paths, w.Phases, w.Risk)
	result := w.BreakAllAndRestore(paths)
	if !strings.Contains(result.Stdout, "rules.yml") {
		t.Errorf("戻したファイルを言わない\n%s", configws.Said(result, ""))
	}
}

func TestCopiesInWorktreesAreRestored(t *testing.T) {
	t.Parallel()
	// ワークツリー側の設定も対象。lib から切った分・ワークスペースから切った自身の分・共通設定の 3 本
	// （既存の穴。直す前は allow `worktrees` にヒットして書けた）。
	w := newWS(t, configws.KindGuard)
	t1 := w.Worktree(w.Lib, "w1")
	t2 := w.Worktree(w.Root, "w2")
	t3 := w.Worktree(w.Root, "w3")
	paths := []string{configws.LayerPath(t1, "rules"), configws.LayerPath(t2, "phases")}
	for _, name := range []string{"rules.yml", "phases.yml", "risks.yml"} {
		paths = append(paths, p(t3, ".ccnavi", "config", "common", name))
	}
	for _, path := range paths {
		if !configws.Exists(path) {
			t.Fatalf("追跡されているのでワークツリー側の設定がある: %s", path)
		}
	}
	result := w.BreakAllAndRestore(paths)
	if !strings.Contains(result.Stdout, "統合すれば") {
		t.Errorf("統合の注意を言わない\n%s", configws.Said(result, ""))
	}
}

func TestADeletedLayerFileComesBackFromTheProjectGit(t *testing.T) {
	t.Parallel()
	// バックアップが無ければ、その設定の git（プロジェクト自身）から戻る。
	w := newWS(t, configws.KindGuard)
	path := configws.LayerPath(w.Lib, "rules")
	expected := configws.Read(t, path)
	if err := configws.RemoveFile(path); err != nil {
		t.Fatal(err)
	}

	result := w.RunHook("PreToolUse")
	if !configws.Exists(path) || configws.Read(t, path) != expected {
		t.Errorf("git から戻らない\n%s", configws.Said(result, ""))
	}
}

func TestMissingLayerFilesAreNotReported(t *testing.T) {
	t.Parallel()
	// 無いものは対象から外れる。app の設定が無いことは言わない。
	w := newWS(t, configws.KindGuard)
	result := w.RunHook("PreToolUse")
	if strings.Contains(result.Stdout, "app") || result.Stderr != "" {
		t.Errorf("無い設定を言っている\n%s", configws.Said(result, ""))
	}
}

func TestTheRecordNamesTheLayerThatWasRestored(t *testing.T) {
	t.Parallel()
	// 記録の `guarded` に、設定の名前付きのキーで何をしたかが残る。
	w := newWS(t, configws.KindGuard)
	path := configws.LayerPath(w.Lib, "phases")
	w.RunHook("PreToolUse")
	configws.Write(t, path, "version: 1\ndeny: []\n")
	w.RunHook("PostToolUse")

	guarded, _ := w.LastRecord()["guarded"].([]any)
	found := false
	for _, g := range guarded {
		s, _ := g.(string)
		found = found || (strings.Contains(s, "phases") && strings.Contains(s, "lib") && strings.Contains(s, "restored"))
	}
	if !found {
		t.Errorf("guarded に lib の phases の復元が無い: %v", guarded)
	}
}

// 共通設定のルールファイルが読めない間の書き込み。

func TestARepairOfAnUnreadableCommonRulesFileIsRestoredWithTheWay(t *testing.T) {
	t.Parallel()
	// 読めない共通設定のルールを名指しのツールで直した結果も、実行後に戻す。
	// 黙って戻されたように見せず、下書きとユーザの取り込みという直し方を添える。
	w := newWS(t, configws.KindGuard)
	fixed := configws.Body(configws.OpenRules)
	for _, tool := range []string{"Write", "Edit"} {
		t.Run(tool, func(t *testing.T) {
			w := w.as(t)
			configws.Write(t, w.Rules, configws.Broken)
			w.GuardedHook(tool, w.Root, configws.Obj{"file_path": w.Rules})
			configws.Write(t, w.Rules, fixed)
			result := w.HookAs("PostToolUse", "enable", tool, w.Root, configws.Obj{"file_path": w.Rules})
			if configws.Read(t, w.Rules) != configws.Broken {
				t.Errorf("戻らない\n%s", configws.Said(result, ""))
			}
			for _, want := range []string{"restored", "wip/handoff/.ccnavi/config/common/rules.yml", "ccnavi-user-handoff.sh"} {
				if !strings.Contains(result.Stdout, want) {
					t.Errorf("出力に %q が無い\n%s", want, configws.Said(result, ""))
				}
			}
		})
	}
}

func TestBreakingAReadableCommonRulesFileIsStillRestored(t *testing.T) {
	t.Parallel()
	// 実行前に読めていたなら、名指しのツールで壊した結果は戻す。実行後の中身で決めると、壊すことが戻されない経路になる。
	w := newWS(t, configws.KindGuard)
	before := configws.Read(t, w.Rules)
	w.GuardedHook("Edit", w.Root, configws.Obj{"file_path": w.Rules})
	configws.Write(t, w.Rules, configws.Broken)
	result := w.HookAs("PostToolUse", "enable", "Edit", w.Root, configws.Obj{"file_path": w.Rules})
	if configws.Read(t, w.Rules) != before || !strings.Contains(result.Stdout, "restored") {
		t.Errorf("戻らない\n%s", configws.Said(result, ""))
	}
}

func TestAShellWriteToAnUnreadableCommonRulesFileIsRestored(t *testing.T) {
	t.Parallel()
	// シェルからの書き込みも、読めない間でも戻す。
	w := newWS(t, configws.KindGuard)
	configws.Write(t, w.Rules, configws.Broken)
	w.RunHook("PreToolUse")
	configws.Write(t, w.Rules, configws.Body(configws.OpenRules))
	result := w.RunHook("PostToolUse")
	if configws.Read(t, w.Rules) != configws.Broken {
		t.Errorf("戻らない\n%s", configws.Said(result, ""))
	}
}

// `.ccnavi/` 全体への組み込みの deny。ルールに宣言が無くても止まる。

func TestARuleNamedLikeABuiltinDoesNotReplaceIt(t *testing.T) {
	t.Parallel()
	// 組み込みの名前（`builtin:guard-`）のルールは読み込まず、組み込みは常に足す。
	// 以前は何にもヒットしない 1 本をこの名前で書くだけで、共通設定の保護が気づかないうちに消えた。
	w := newWS(t, configws.KindGuard)
	decoy := configws.With(configws.OpenRules, "deny", []configws.Obj{
		configws.Rule("builtin:guard-common-layer", "Write|Edit|NotebookEdit", "regex", "never-matches-anything", "decoy"),
	})
	configws.Write(t, w.Rules, configws.Body(decoy))
	result := w.GuardedHook("Write", w.Root, configws.Obj{"file_path": w.Phases})
	w.AssertDenied(result, "builtin:guard-common-layer")
	if !strings.Contains(result.Stderr, "builtin:guard-") {
		t.Errorf("標準エラーが組み込みの名前を言わない\n%s", configws.Said(result, ""))
	}
}

func TestLintNamesARuleNamedLikeABuiltinInAnyLayer(t *testing.T) {
	t.Parallel()
	// プロジェクトの設定に書いても `--lint` が error で名指しする。
	w := newWS(t, configws.KindGuard)
	configws.WriteRules(t, w.Lib, configws.Ruleset([]configws.Obj{configws.Rule("builtin:guard-setting-files", "Bash", "regex", "never-matches-anything", "decoy")}, nil))
	w.MustHaveDetail(w.Problems("error", configws.ProjectWhere("lib")), "builtin:")
}

func TestNamedToolWritesIntoProjectHomeAreDenied(t *testing.T) {
	t.Parallel()
	// `*/.ccnavi/*` への Write / Edit は `builtin:guard-project-home` で止まる。
	w := newWS(t, configws.KindGuard)
	for _, target := range []string{
		configws.LayerPath(w.Lib, "rules"),
		p(w.Lib, configws.Home, "scripts", "count.sh"),
		configws.LayerPath(w.Root, "phases"),
		p(w.App, configws.Home, "config", "project", "rules.yml"),
	} {
		for _, tool := range []string{"Write", "Edit"} {
			rel, _ := filepath.Rel(w.Root, target)
			t.Run(tool+" "+filepath.ToSlash(rel), func(t *testing.T) {
				w := w.as(t)
				w.AssertDenied(w.GuardedHook(tool, w.Root, configws.Obj{"file_path": target}), "builtin:guard-project-home")
			})
		}
	}
}

func TestReadingProjectHomeIsAllowed(t *testing.T) {
	t.Parallel()
	// 読むだけなら通る。名前が出ただけでは止めない。
	w := newWS(t, configws.KindGuard)
	w.AssertNoneDenied([]string{
		"cat projects/lib/.ccnavi/config/project/rules.yml",
		"grep -n deny .ccnavi/config/project/rules.yml",
	})
	w.AssertNotDenied(w.GuardedHook("Read", w.Root, configws.Obj{"file_path": configws.LayerPath(w.Lib, "rules")}))
}

func TestDisableDoesNotAddTheDeny(t *testing.T) {
	t.Parallel()
	// 設定ファイルの保護を disable にすれば組み込みの deny も足さない。
	w := newWS(t, configws.KindGuard)
	result := w.Hook("Write", w.Root, configws.Obj{"file_path": configws.LayerPath(w.Lib, "rules")})
	if strings.Contains(w.Reason(result), "builtin:guard-project-home") {
		t.Error("disable でも guard-project-home を足した")
	}
	result = w.Hook("Write", w.Root, configws.Obj{"file_path": w.Phases})
	if strings.Contains(w.Reason(result), "builtin:guard-common-layer") {
		t.Error("disable でも guard-common-layer を足した")
	}
}

func TestNamedToolWritesIntoTheCommonLayerAreDenied(t *testing.T) {
	t.Parallel()
	// 共通設定の 3 本への Write / Edit は、ルールに宣言が無くても組み込みで止まる。
	// 土台の共通設定は既定の置き場（`.ccnavi/config/common/`）にある。ルールの 1 行に任せると、
	// 書き換えたルールファイルのもとでは通ってしまう（issue #14）。
	w := newWS(t, configws.KindGuard)
	for _, target := range []string{w.Rules, w.Phases, w.Risk} {
		for _, tool := range []string{"Write", "Edit"} {
			t.Run(tool+" "+filepath.Base(target), func(t *testing.T) {
				w := w.as(t)
				w.AssertDenied(w.GuardedHook(tool, w.Root, configws.Obj{"file_path": target}), "builtin:guard-common-layer")
			})
		}
	}
}

func TestCommonLayerCopiesInAWorktreeAreDenied(t *testing.T) {
	t.Parallel()
	// ワークスペースから切ったワークツリー側の共通設定も止まる。統合すればそのまま main の設定になる。
	w := newWS(t, configws.KindGuard)
	tree := w.Worktree(w.Root, "w5")
	w.AssertDenied(w.GuardedHook("Write", w.Root, configws.Obj{"file_path": p(tree, ".ccnavi", "config", "common", "phases.yml")}), "builtin:guard-common-layer")
}

func TestTheCommonLayerDenyNamesOnlyThoseFiles(t *testing.T) {
	t.Parallel()
	// 同じ名前の別のファイルは止めない。適用するのはワークスペースルートと、そこから切ったワークツリーの下の同じ相対だけ。
	w := newWS(t, configws.KindGuard)
	for _, target := range []string{p(w.Root, "src", "rules.yml"), p(w.App, "common", "rules.yml")} {
		rel, _ := filepath.Rel(w.Root, target)
		t.Run(filepath.ToSlash(rel), func(t *testing.T) {
			w := w.as(t)
			w.AssertNotDenied(w.GuardedHook("Write", w.Root, configws.Obj{"file_path": target}))
		})
	}
}

func TestShellWritesIntoTheCommonLayerAreDenied(t *testing.T) {
	t.Parallel()
	// 共通設定の 3 本へのシェルからの書き込みは組み込みで止まる。置き場は `.ccnavi/config/common/` に固定なので、
	// ccnavi ディレクトリを丸ごと拾う 1 本が適用する。動かした置き場は TestShellWritesIntoAMovedCommonLayerAreDenied が見る。
	w := newWS(t, configws.KindGuard)
	w.AssertAllGuarded([]string{
		"echo x > .ccnavi/config/common/rules.yml",
		"sed -i s/deny/allow/ .ccnavi/config/common/phases.yml",
		"cp /tmp/x .ccnavi/config/common/risks.yml",
	})
	// 名前の途中でヒットしたものは別のファイル。
	result := w.GuardedHook("Bash", w.Root, configws.Obj{"command": "echo x > myccnavi/common/rules.yml"})
	if strings.Contains(w.Reason(result), configws.SettingFilesGuard) {
		t.Error("名前の途中でヒットした")
	}
}

func TestCopyingOutOfTheCommonLayerIsAllowed(t *testing.T) {
	t.Parallel()
	// 共通設定の 3 本が cp / ln / install の元の側にだけ出る形は、読むだけなので通る。
	// コピーする側は行き先（最後の引数か `-t` の値）だけを見る。元の側でヒットすると、バックアップを取るだけの `cp` まで止まる
	// （rule-expectations.yml の期待する判定）。
	w := newWS(t, configws.KindGuard)
	absolute := filepath.ToSlash(filepath.Dir(w.Rules))
	var commands []string
	for _, name := range []string{"rules.yml", "phases.yml", "risks.yml"} {
		commands = append(commands,
			fmt.Sprintf("cp .ccnavi/config/common/%s /tmp/backup.yml", name),
			fmt.Sprintf("cp %s/%s /tmp/backup.yml", absolute, name),
			fmt.Sprintf("ln -s .ccnavi/config/common/%s /tmp/link.yml", name),
			fmt.Sprintf("install -m 644 .ccnavi/config/common/%s /tmp/x.yml", name),
			fmt.Sprintf("cp .ccnavi/config/common/%s /tmp/a.yml && cat /tmp/a.yml", name),
			fmt.Sprintf("cp .ccnavi/config/common/%s /tmp/a.yml 2>/dev/null", name),
			"cp -r .ccnavi/config/common /tmp/out",
		)
	}
	w.AssertNoneGuarded(commands)
}

// movedLayer は共通設定の 3 本を policy/ に動かした形（`--rules` などは診断でだけ有効。それでも動かせる以上、保護は動かした先を追う）。
func (w *ws) movedLayer() (flags []string, rel, abs map[string]string) {
	w.T.Helper()
	rel, abs = map[string]string{}, map[string]string{}
	for _, f := range []struct{ flag, source, name string }{
		{"--rules", w.Rules, "rules.yml"},
		{"--phases", w.Phases, "phases.yml"},
		{"--risk", w.Risk, "risks.yml"},
	} {
		moved := configws.Write(w.T, p(w.Root, "policy", f.name), configws.Read(w.T, f.source))
		flags = append(flags, f.flag, moved)
		rel[f.name] = "policy/" + f.name
		abs[f.name] = filepath.ToSlash(moved)
	}
	return flags, rel, abs
}

func TestShellWritesIntoAMovedCommonLayerAreDenied(t *testing.T) {
	t.Parallel()
	// 共通設定が既定の置き場の外にあっても、シェルからの書き込みは組み込みで止まる。名指しのツールは `common_layer_regex` が
	// 同じ先を追うので、こちらを外すと、同じファイルが `Write` では止まってシェルでは通る形になる。
	// 末尾の 2 本が境界を見る。前と後ろの両方が要る。`otherpolicy/rules.yml` は前の境界だけを、`policy/rules.yml.bak` は
	// 後ろの境界だけを外す。片方しか置かないと、置かなかったほうの境界を消す変異があってもテストが通る。
	w := newWS(t, configws.KindGuard)
	policy := configws.Write(t, p(w.Root, "policy", "rules.yml"), configws.Read(t, w.Rules))
	moved := []string{"--rules", policy}
	// 絶対パスは `/` で書く。bash は引用されない `\` を消すので、`\` の表記のままではそのコマンドは設定ファイルに書かない。
	w.AssertAllGuarded([]string{
		"echo x > policy/rules.yml",
		"sed -i s/deny/allow/ policy/rules.yml",
		"cp /tmp/x policy/rules.yml",
		"echo x > " + filepath.ToSlash(policy),
	}, moved...)
	w.AssertNoneGuarded([]string{
		"echo x > otherpolicy/rules.yml",
		"echo x > policy/rules.yml.bak",
	}, moved...)
}

func TestCopiesOfAMovedCommonLayerLookOnlyAtTheDestination(t *testing.T) {
	t.Parallel()
	// 動かした共通設定の 3 本も、cp / ln / install は行き先の側だけで適用する。動かした先の表記は書き込み用とコピー用に分けて足す。
	// 書き込み用（空白で閉じる）をそのままコピーする側に足すと、元の側に出ただけでヒットする。
	w := newWS(t, configws.KindGuard)
	flags, rels, abss := w.movedLayer()
	for _, name := range []string{"rules.yml", "phases.yml", "risks.yml"} {
		rel, abs := rels[name], abss[name]
		w.AssertNoneGuarded([]string{
			"cp " + rel + " /tmp/backup.yml",
			"cp " + abs + " /tmp/backup.yml",
			"ln -s " + rel + " /tmp/link.yml",
			"install " + rel + " /tmp/x.yml",
			"cp " + rel + " /tmp/a.yml && cat /tmp/a.yml",
			"cp -t /tmp/out " + rel,
			"cp --target-directory=/tmp/out " + abs,
			"cp -v " + rel + " /tmp/out -f",
		}, flags...)
		w.AssertAllGuarded([]string{
			"cp /tmp/x " + rel,
			"cp /tmp/x " + abs,
			"ln -sf /tmp/x " + rel,
			"install /tmp/x " + rel,
			"cp /tmp/x " + rel + " && echo done",
			"cp /tmp/x " + rel + "; echo done",
			"cp /tmp/x " + rel + " -f",
			"cp -S -t /tmp/x " + rel,
			"sudo cp /tmp/x " + abs,
			"echo go && cp /tmp/x " + rel,
			`cp "/tmp/a b" ` + rel,
			`cp /tmp/x "` + rel + `"`,
			"cd policy && cp /tmp/x " + path.Base(rel),
			"mv " + rel + " /tmp/x",
			"rm " + rel,
			"tee " + rel + " < /tmp/x",
			"sed -i s/deny/allow/ " + rel,
			"echo x > " + rel,
		}, flags...)
	}
	// 名前の途中や続きでヒットしたものは別のファイル。
	w.AssertNoneGuarded([]string{
		"cp /tmp/x otherpolicy/rules.yml",
		"cp /tmp/x policy/rules.yml.bak",
	}, flags...)
}

func TestADirectoryHoldingAMovedCommonLayerIsGuardedByName(t *testing.T) {
	t.Parallel()
	// 動かした共通設定の入っているディレクトリを行き先にした形（`_moved_holders`）。行き先の表記に共通設定の名前が出ないので、
	// 元の名前で見る。同じ名前を置く形と、名前の決まらない形は止め、別の名前を置くだけの形は止めない。
	w := newWS(t, configws.KindGuard)
	policy := configws.Write(t, p(w.Root, "policy", "rules.yml"), configws.Read(t, w.Rules))
	moved := []string{"--rules", policy}
	absolute := filepath.ToSlash(filepath.Dir(policy))
	w.AssertAllGuarded([]string{
		"cp /tmp/rules.yml policy/",
		"cp /tmp/rules.yml policy",
		"mv /tmp/rules.yml policy/",
		"cp -t policy /tmp/rules.yml",
		"cp /tmp/rules.yml " + absolute + "/",
		"cp /tmp/*.yml policy/",
		"cd policy && cp /tmp/rules.yml .",
		"cp -r /tmp/policy .",
	}, moved...)
	w.AssertNoneGuarded([]string{
		"cp /tmp/notes.txt policy/",
		"cp -t policy /tmp/notes.txt",
		"cp policy/rules.yml /tmp/",
		"cp /tmp/rules.yml otherpolicy/",
	}, moved...)
}

func TestLintWarnsAboutNewFilesInAWorktreeProjectHome(t *testing.T) {
	t.Parallel()
	// ワークツリーの `.ccnavi/` に元リポジトリに無いファイルがあれば --lint warn。
	w := newWS(t, configws.KindGuard)
	tree := w.Worktree(w.Lib, "w1")
	configws.Write(t, p(tree, configws.Home, "scripts", "new.sh"), "echo new\n")
	w.MustHaveDetail(w.Problems("warn", ""), "new.sh")
}
