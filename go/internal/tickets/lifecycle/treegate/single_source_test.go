package treegate

import (
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// ゲートの条件を調べる git の呼び出しは treegate の 1 か所にある。
// 親のワークツリーの未コミットの変更（未追跡を挙げない status）と、親のブランチの未 push
// （rev-list origin/<ブランチ>..HEAD）を数える実装を、操作ごとに書き直さない（.agents/docs/gate-conditions.md）。
// 見るのは _test.go でない Go のソースの行で、dirty は git.UntrackedNo、unpushed は "rev-list" と "origin/" で始まる
// 文字列を同じ行に持つもの。別の目的で同じ git を使うものは、理由を付けて exceptions に挙げる。
func TestOnlyTreegateReadsDirtyAndUnpushed(t *testing.T) {
	internal := filepath.Join(testkit.RepoRoot(t), "go", "internal")
	own := filepath.ToSlash(filepath.Join("tickets", "lifecycle", "treegate", "treegate.go"))
	// (ファイル, 種類) -> ゲートの条件でない理由。
	exceptions := map[[2]string]string{
		{"tickets/lifecycle/start/route.go", "unpushed"}: "開始の候補のブランチが統合先より何コミット進んでいるか（取り込み済みの古いブランチか、" +
			"初期コミットだけの作りかけか）を数える。親のブランチの未 push ではない",
		{"tickets/lifecycle/start/prepare.go", "unpushed"}: "新しく切ったブランチが統合先より進んでいるか（空の初期コミットを積むか）を数える。" +
			"親のブランチの未 push ではない",
	}
	hits := map[[2]string]bool{}
	err := filepath.WalkDir(internal, func(path string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return err
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(internal, path)
		rel = filepath.ToSlash(rel)
		for _, line := range strings.Split(string(data), "\n") {
			if strings.Contains(line, "git.UntrackedNo") || strings.Contains(line, "Untracked: UntrackedNo") {
				hits[[2]string{rel, "dirty"}] = true
			}
			if strings.Contains(line, `"rev-list"`) && strings.Contains(line, `"origin/"`) {
				hits[[2]string{rel, "unpushed"}] = true
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	var outside []string
	for hit := range hits {
		if _, ok := exceptions[hit]; hit[0] != own && !ok {
			outside = append(outside, hit[0]+" "+hit[1])
		}
	}
	slices.Sort(outside)
	if len(outside) > 0 {
		t.Errorf("ゲートの条件は treegate に置く: %v", outside)
	}
	for _, kind := range []string{"dirty", "unpushed"} {
		if !hits[[2]string{own, kind}] {
			t.Errorf("treegate.go に %s の読みが見つからない（数え方を直す）", kind)
		}
	}
	for hit := range exceptions {
		if !hits[hit] {
			t.Errorf("要らなくなった例外: %v", hit)
		}
	}
}
