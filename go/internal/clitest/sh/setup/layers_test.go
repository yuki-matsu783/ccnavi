package setup

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/shkit"
)

// 導入スクリプト（scripts/ccnavi-setup.sh）が配る設定のひな形の置き場。rules・phases・risk のひな形は、どれも共通設定
// （.ccnavi/config/common/）へ配る。ワークスペースの設定（.ccnavi/config/project/）の phases.yml は配らない（レイアウトに依存する定義は
// 配布先のユーザが書く。既にあれば触らない）。配らないときは、rules と risk と、phases が共通にもワークスペースの設定にも無いことが
// 「まだ無いもの」に名前が出る。

const (
	layerRisk   = "version: 1\nlevels: {medium: 20, high: 40, critical: 70}\nfactors: {}\n"
	layerPhases = "version: 1\nphases:\n  implement:\n    kind: work\n    title: 実装とテスト\n    review: true\n    scope: [\"src/*\", \"tests/*\"]\n"
	ownPhases   = "version: 1\nphases:\n  docs:\n    kind: work\n    title: 文書\n    review: true\n    scope: [\"docs/*\"]\n"
	minePhases  = "version: 1\nphases:\n  mine:\n    kind: work\n    title: 自分の\n"
)

// layersSource は配布元のふり。実行ファイルと、共通設定の rules / phases / risk と、ワークスペースの設定の phases。
// 代わりに通る sh は起動して最初に共通部を読むので、配布元にもそれを置く（無いと「配布元に無くて配れないもの」として名指しされる）。
func layersSource(t *testing.T) string {
	t.Helper()
	root := makeSource(t, srcOpts{NoParts: true})
	shkit.Write(t, filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), "deny: []\n")
	shkit.Write(t, filepath.Join(root, ".ccnavi", "config", "common", "risks.yml"), layerRisk)
	shkit.Write(t, filepath.Join(root, ".ccnavi", "config", "common", "phases.yml"), layerPhases)
	shkit.Write(t, filepath.Join(root, ".ccnavi", "config", "common", "ticket-sections.yml"), "version: 1\nparent: []\n")
	shkit.Write(t, filepath.Join(root, ".ccnavi", "config", "project", "phases.yml"), ownPhases)
	shkit.Write(t, filepath.Join(root, ".ccnavi", "CCNAVI.md"), "# ccnavi の作業の決まり\n")
	shkit.Write(t, filepath.Join(root, ".ccnavi", "docs", "worktree.md"), "# ワークツリー\n")
	copyFile(t, launcherPath(), filepath.Join(root, ".ccnavi", "scripts", launcherName), 0o644)
	for _, name := range deployScripts {
		shkit.Write(t, filepath.Join(root, ".ccnavi", "scripts", name), "# "+name+"\n")
	}
	return root
}

// 共通設定に rules.yml・risks.yml・phases.yml・ticket-sections.yml のひな形を中身を変えずに配る。ワークスペースの設定の phases.yml は、配布元にあっても配らず、
// 配布先に既にあれば --force でも触らない。
func TestDeployCopiesTheCommonLayerTemplatesAndNotTheOwnPhases(t *testing.T) {
	t.Parallel()
	t.Run("配布先が空", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		r := w.setup("--mode", "enable", "--deploy", layersSource(t))
		code(t, r, 0)
		for _, kind := range []string{"rules.yml", "risks.yml", "phases.yml", "ticket-sections.yml"} {
			if !shkit.Exists(w.path(".ccnavi", "config", "common", kind)) {
				t.Errorf("共通設定の %s が配られていない", kind)
			}
		}
		if shkit.Exists(w.path(".ccnavi", "config", "project", "phases.yml")) {
			t.Error("ワークスペースの設定の phases.yml を配った")
		}
		notIn(t, r.Stdout, ".ccnavi/config/project/phases.yml", "まだ無いもの")
	})
	t.Run("--force でも配布先のワークスペースの設定に触らない", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		own := shkit.Write(t, w.path(".ccnavi", "config", "project", "phases.yml"), minePhases)
		r := w.setup("--mode", "enable", "--deploy", layersSource(t), "--force")
		code(t, r, 0)
		eq(t, "共通の phases.yml", shkit.Read(t, w.path(".ccnavi", "config", "common", "phases.yml")), layerPhases)
		eq(t, "ワークスペースの設定の phases.yml", shkit.Read(t, own), minePhases)
		in(t, r.Stdout, ".ccnavi/config/common/phases.yml")
		notIn(t, r.Stdout, ".ccnavi/config/project/phases.yml")
	})
}

// 配布先に共通設定の phases.yml があれば、--force の無い配布では入れ替えない。
func TestExistingCommonPhasesIsKeptWithoutForce(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	common := shkit.Write(t, w.path(".ccnavi", "config", "common", "phases.yml"), minePhases)
	r := w.setup("--mode", "enable", "--deploy", layersSource(t))
	code(t, r, 0)
	eq(t, "共通の phases.yml", shkit.Read(t, common), minePhases)
}

// フェーズ定義が共通設定にだけあれば、ワークスペースの設定の phases.yml が無くても「まだ無いもの」に出さない。
func TestPhasesOnlyInCommonIsNotMissing(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	shkit.Write(t, w.path(".ccnavi", "config", "common", "phases.yml"), layerPhases)
	r := w.setup("--mode", "enable", "--no-deploy")
	code(t, r, 0)
	notIn(t, r.Stdout, ".ccnavi/config/project/phases.yml", ".ccnavi/config/common/phases.yml")
}

// rules と risk と、どこにも無いフェーズ定義を「まだ無いもの」の点検に数える。
func TestMissingTemplatesAreNamed(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--mode", "enable", "--no-deploy")
	code(t, r, 0)
	in(t, r.Stdout, "まだ無いもの", ".ccnavi/config/common/rules.yml", ".ccnavi/config/common/risks.yml", ".ccnavi/config/common/phases.yml", ".ccnavi/config/common/ticket-sections.yml")
}
