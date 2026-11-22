package handoff

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
)

// 見本（handoff.json）が持たない場面。レイヤーの設定（rules.yml・phases.yml）とテンプレートの取り込みで、ボードの経路に
// 載せてよいかと、同じ回に取り込む対象を取り込んだあとの状態で検査し直すこと。

func sceneRoot(t *testing.T, files map[string]string) (string, *config.Settings) {
	t.Helper()
	clearEnv(t)
	root := t.TempDir()
	build(t, root, merged(files))
	conf, _ := config.Load(root)
	return root, conf
}

func slurp(t *testing.T, root, rel string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(rel)))
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func find(t *testing.T, plan *Plan, rel string) *Entry {
	t.Helper()
	for _, e := range plan.Entries {
		if e.Rel == rel {
			return e
		}
	}
	t.Fatalf("%s が答えに無い", rel)
	return nil
}

// 共通の rules.yml が読めない間の修復は、今の中身を確かめられないのでターミナルに限る。
func TestRepairOfUnreadableRulesIsTerminalOnly(t *testing.T) {
	broken := "deny:\n  - id: [unclosed\n<<<<<<< HEAD\n"
	root, conf := sceneRoot(t, map[string]string{rulesRel: broken, draftRoot + rulesRel: commonRules})
	plan := Prepare(conf, root, nil)
	row := find(t, plan, rulesRel)
	if string(row.Kind.ID) != "layer-config" || row.Board() {
		t.Errorf("kind=%s board=%v", row.Kind.ID, row.Board())
	}
	if done := Apply(plan, plan.Expect(), true); done.OK || !strings.Contains(done.Error, "ターミナル") {
		t.Errorf("ボードから通った: %+v", done)
	}
	if got := slurp(t, root, rulesRel); got != broken {
		t.Errorf("ボードの断りで書き換わった: %q", got)
	}
	if done := Apply(plan, plan.Expect(), false); !done.OK {
		t.Fatalf("ターミナルから取り込めない: %s", done.Error)
	}
	if got := slurp(t, root, rulesRel); got != commonRules {
		t.Errorf("取り込んだ中身: %q", got)
	}
	if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(draftRoot+rulesRel))); err == nil {
		t.Error("取り込んだ下書きが残った")
	}
}

// 今ある deny を減らす rules.yml は防御の記述を動かすので、ボードの経路では断り、ターミナルなら取り込める。
func TestRemovingADenyIsTerminalOnly(t *testing.T) {
	two := strings.Replace(commonRules, `"message": "ガードの設定と承認済みチケットです。ユーザに依頼してください。"}]}`,
		`"message": "m"}, {"id": "keep-out", "match": "Write", "glob": "*/x/*", "message": "x"}]}`, 1)
	if two == commonRules {
		t.Fatal("見本の組み立てが合わない")
	}
	root, conf := sceneRoot(t, map[string]string{rulesRel: two, draftRoot + rulesRel: commonRules})
	plan := Prepare(conf, root, nil)
	row := find(t, plan, rulesRel)
	if row.Board() || string(row.Kind.ID) != "layer-config" || !strings.Contains(row.Defense, "keep-out") {
		t.Errorf("board=%v kind=%s defense=%q", row.Board(), row.Kind.ID, row.Defense)
	}
	done := Apply(plan, plan.Expect(), true)
	if done.OK || !strings.Contains(done.Error, "防御の記述") || !strings.Contains(done.Error, "ターミナル") {
		t.Errorf("%+v", done)
	}
	if got := slurp(t, root, rulesRel); got != two {
		t.Errorf("ボードの断りで書き換わった: %q", got)
	}
	if done := Apply(plan, plan.Expect(), false); !done.OK {
		t.Errorf("ターミナルから取り込めない: %s", done.Error)
	}
}

// 読めないレイヤーの設定は、取り込む前に error で断る。
func TestBrokenLayerFilesAreRejected(t *testing.T) {
	projPhases := ".ccnavi/config/project/phases.yml"
	root, conf := sceneRoot(t, map[string]string{
		projPhases:             "version: 1\nphases: {}\n",
		draftRoot + rulesRel:   "version: 1\ndeny: [\n",
		draftRoot + projPhases: "version: 1\nphases:\n  design:\n    kind: nope\n",
	})
	plan := Prepare(conf, root, nil)
	row := find(t, plan, rulesRel)
	if string(row.Kind.ID) != "layer-config" || row.State() != stateRejected || !strings.Contains(strings.Join(row.Errors, " "), "ルールを読めない") {
		t.Errorf("rules.yml: kind=%s state=%s %v", row.Kind.ID, row.State(), row.Errors)
	}
	if row := find(t, plan, projPhases); row.State() != stateRejected {
		t.Errorf("phases.yml: %s %v", row.State(), row.Errors)
	}
}

// テンプレートは、同じ回に取り込む phases.yml の型で検査し直す。共通の同名テンプレートも同じ回に直せば衝突しない。
func TestAGroupTakenTogetherIsCheckedAfterTheImport(t *testing.T) {
	const tmpl = ".ccnavi/config/project/templates/feature.yml"
	text := strings.Replace(templateText, "{type: implement, after: [1]}", "{type: brand-new, after: [1]}", 1)
	newPhases := phasesText + "  brand-new:\n    kind: work\n    title: 新しい型\n    review: true\n    scope: [\"src/*\"]\n"
	root, conf := sceneRoot(t, map[string]string{draftRoot + tmpl: text, draftRoot + phasesRel: newPhases})

	alone := find(t, Prepare(conf, root, []string{tmpl}), tmpl)
	if !strings.Contains(strings.Join(alone.Warnings, " "), "brand-new") {
		t.Errorf("単独では型が無いと言う: %v", alone.Warnings)
	}
	together := Prepare(conf, root, []string{tmpl, phasesRel})
	if strings.Contains(strings.Join(find(t, together, tmpl).Warnings, " "), "brand-new") || !together.OK() {
		t.Errorf("一緒に取り込んでも言う: %v ok=%v", find(t, together, tmpl).Warnings, together.OK())
	}
}

func TestTemplateCollisionIsJudgedAfterTheImport(t *testing.T) {
	const own = ".ccnavi/config/project/templates/feature.yml"
	const common = ".ccnavi/config/common/templates/feature.yml"
	root, conf := sceneRoot(t, map[string]string{
		common:          strings.Replace(templateText, "機能を", "別の", 1),
		draftRoot + own: templateText,
	})
	row := find(t, Prepare(conf, root, nil), own)
	if !strings.Contains(strings.Join(row.Errors, " "), "共通に同名で中身の違う") {
		t.Errorf("衝突を言わない: %v", row.Errors)
	}
	// 共通の同名も同じ回に同じ中身へ直すなら、衝突しない。
	build(t, root, map[string]string{draftRoot + common: templateText})
	if plan := Prepare(conf, root, nil); !plan.OK() {
		t.Errorf("同じ回に直しても衝突する: %v", find(t, plan, own).Errors)
	}
}
