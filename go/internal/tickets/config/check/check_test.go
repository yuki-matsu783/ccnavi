package check

import (
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/policy/rules"

	"ccnavi/internal/tickets/testkit"
)

const phasesYML = `version: 1
phases:
  implement: {kind: work, title: 実装とテスト, review: true, scope: ["src/*", "tests/*"]}
  research: {kind: work, title: 調査, review: false, scope: ["wip/research/*"]}
`

func workspace(t *testing.T) (string, *config.Settings) {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q")
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "phases.yml"), phasesYML)
	conf, _ := config.Load(root)
	return root, conf
}

func TestUnmatchedScopes(t *testing.T) {
	root, conf := workspace(t)
	// textkit/ の配置では src/* だけが当たらない。wip/ の下は空でも言わない。
	testkit.Put(t, filepath.Join(root, "textkit", "a.py"), "")
	testkit.Put(t, filepath.Join(root, "tests", "test_a.py"), "")
	found := UnmatchedScopes(conf, root)
	if len(found) != 1 || found[0].Severity != rules.Warn || found[0].Rule != "(self) (phases) implement" ||
		!strings.HasPrefix(found[0].Detail, "`scope` の `src/*` がどのファイルにも当たりません。") {
		t.Errorf("%v", found)
	}
	testkit.Put(t, filepath.Join(root, "src", "a.py"), "")
	if found := UnmatchedScopes(conf, root); len(found) != 0 {
		t.Errorf("当たるのに %v", found)
	}
	// git が使えないレイヤーは何も言わない。
	if err := os.RemoveAll(filepath.Join(root, ".git")); err != nil {
		t.Fatal(err)
	}
	if found := UnmatchedScopes(conf, root); len(found) != 0 {
		t.Errorf("git が無いのに %v", found)
	}
}

func TestFileProblems(t *testing.T) {
	root, conf := workspace(t)
	if found := FileProblems(conf, root, config.KindPhases, config.LayerCommon); len(found) != 0 {
		t.Errorf("読めるのに %v", found)
	}
	testkit.Put(t, conf.Phases, "version: 1\nphases: {a:b: {}}\n")
	found := FileProblems(conf, root, config.KindPhases, config.LayerCommon)
	if len(found) != 1 || found[0].Rule != "(phases)" || !strings.HasPrefix(found[0].Detail, "a:b: 識別子に `:` は書けない") {
		t.Errorf("共通の phases: %v", found)
	}
	// 共通レイヤーの配点は script: の指す先も見る。
	testkit.Put(t, conf.Risk, "version: 1\nfactors:\n  - {id: s, points: 1, script: .ccnavi/config/common/scripts/none.sh}\n")
	found = FileProblems(conf, root, config.KindRisk, config.LayerCommon)
	if len(found) != 1 || found[0].Rule != "(risk)" || !strings.HasPrefix(found[0].Detail, "s: `script` の `.ccnavi/config/common/scripts/none.sh` が "+root+" にありません。") {
		t.Errorf("共通の risk: %v", found)
	}
	// レイヤーの phases は共通レイヤーと合成した結果を見る。
	testkit.Put(t, conf.Phases, phasesYML)
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "project", "phases.yml"), "version: 1\nphases:\n  implement: {title: 別}\n")
	found = FileProblems(conf, root, config.KindPhases, config.LayerSelf)
	if len(found) != 1 || found[0].Severity != rules.Error || found[0].Rule != "(self) (phases) implement" {
		t.Errorf("レイヤーの phases: %v", found)
	}
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "project", "risks.yml"), "version: 1\nfactors: x\n")
	found = LayerConfigs(conf, root, "", config.KindRisk)
	if len(found) != 1 || found[0].Rule != "(self) (risk) "+conf.LayerPath(root, config.KindRisk, config.LayerSelf) {
		t.Errorf("レイヤーの risk: %v", found)
	}
	// 無いレイヤーのルールは言わない。
	if found := LayerRules(io.Discard, conf, root, ""); len(found) != 0 {
		t.Errorf("ルール: %v", found)
	}
	if found := CommonRules(conf, root); len(found) != 0 {
		t.Errorf("無い共通のルール: %v", found)
	}
}

func TestLayerWhere(t *testing.T) {
	for name, want := range map[string]string{config.LayerCommon: "(rules)", config.LayerSelf: "(self)", "web": "(projects/web)"} {
		if got := LayerWhere(name); got != want {
			t.Errorf("%s: %q", name, got)
		}
	}
}
