package layers

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/configws"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
)

// ユーザの発言のたびに、共通設定をプロジェクトの `.ccnavi/config/common/` へミラーする。
//
// 共通設定（ワークスペースルートの `.ccnavi/config/common/`）はワークスペースの git にあり、プロジェクトだけを clone したユーザからは
// 見えない。ユーザの発言（UserPromptSubmit）で、共通設定の中身を、着手済みの親のワークツリーにあるプロジェクトの
// `.ccnavi/config/common/` へコピーする。プロジェクトの `.ccnavi/config/project/` には触れない。`ticket start` ではコピーしない。
// 土台は configws の雛形（共通設定は rules / risks の 2 本を持ち、lib の config は 3 本とも別の中身を持つ。app は `.ccnavi/` を持たない）。
//
// 見るのは 5 つ。
//  1. 発言が、共通設定の中身を着手済みの親のワークツリーのミラーへコピーし、config には触れない。共通設定から無くなったファイルは
//     ミラーからも消す。`script:` の値は書き換えない
//  2. 子とワークスペース自身の作業、未着手の親にはコピーしない。`ticket start` 自身はコピーしない
//  3. コピーできない形（読めない・書き込みの失敗）では何もコピーせず、発言と着手は止めずに理由を言う。上書きの記録・知らせ・閉じるのを止める処理は無い
//  4. コピーした分は発言の受け入れに入るので、実行後チェックとバックアップと復元が戻さず、報告もしない。ツール呼び出しの中でミラーが書き換わったら、今までどおり戻す
//  5. 差分が無いときは何も読み書きしない（指紋が前と同じ）。元か先が動けば、検証つきで比べ直す
//
// 写す一覧の組み方（同じスクリプトは 1 回、読む前に消えたものは止める）と、書けなかったときに戻すことは、
// Go のテスト（go/internal/tickets/sync/mirror）が確かめる。

const (
	countSh         = "printf '{\"points\": 1}'\n"
	featureTemplate = "name: feature\ndescription: 機能を設計する\nwhen: 新しい機能を足すとき\nplan: [design]\n"
	expectations    = "version: 1\nexpectations: []\n"
	startCommand    = "sh .ccnavi/scripts/ccnavi-ticket.sh start i0001"
)

var commonScriptRisk = configws.CommonRiskText +
	"  - {id: counted, points: 5, script: .ccnavi/config/common/scripts/count.sh, message: 数えた}\n"

func configOf(tree, kind string) string {
	return configws.LayerPath(tree, kind)
}

type syncWS struct{ *configws.WS }

func newSyncWS(t *testing.T) *syncWS { return &syncWS{configws.NewWS(t, configws.KindBase)} }

func ticketFor(name, project, plan string) string {
	return configws.TicketText(name, configws.TicketOpts{Project: project, Plan: []string{plan}, Allow: []string{"src/*"}})
}

// approveLib は lib 向けの親 i0001 を承認し、ワークツリーを切る（着手はしない）。
func (w *syncWS) approveLib() string {
	w.T.Helper()
	w.Propose("i0001", ticketFor("i0001", "lib", "build"), "lib")
	if done := w.Approve(); done.Code != 0 {
		w.T.Fatalf("approve: %s%s", done.Stdout, done.Stderr)
	}
	return w.Worktree(w.Lib, "i0001")
}

// startParent は project 向けの親を承認し、そのプロジェクトからワークツリーを切って着手する。
func (w *syncWS) startParent(project string) (tree, out string) {
	w.T.Helper()
	owner := w.Root
	if project != "" {
		owner = filepath.Join(w.Projects, project)
	}
	// 計画の無い親は着手できない。設定に定義のあるプロジェクトはその定義を使い、定義の無いプロジェクト（app）には最小の定義を置く。
	plan := map[string]string{"lib": "build", "": "docs"}[project]
	if plan == "" {
		plan = "work"
	}
	if !configws.Exists(configws.LayerPath(owner, "phases")) {
		configws.Write(w.T, configws.LayerPath(owner, "phases"), samples.PlainPhases)
	}
	w.Propose("i0001", ticketFor("i0001", project, plan), project)
	if done := w.Approve(); done.Code != 0 {
		w.T.Fatalf("approve: %s%s", done.Stdout, done.Stderr)
	}
	tree = w.Worktree(owner, "i0001")
	code, out := w.StartTicket("i0001")
	if code != 0 {
		w.T.Fatalf("ticket start: %d\n%s", code, out)
	}
	return tree, out
}

func (w *syncWS) addScript() {
	configws.Write(w.T, w.Risk, commonScriptRisk)
	configws.Write(w.T, filepath.Join(w.Root, ".ccnavi", "config", "common", "scripts", "count.sh"), countSh)
}

func (w *syncWS) post(tree, command string, guard string) run.Result {
	return w.HookAs("PostToolUse", guard, "Bash", tree, configws.Obj{"command": command})
}

func (w *syncWS) pre(tree, command, guard string) run.Result {
	return w.HookAs("PreToolUse", guard, "Bash", tree, configws.Obj{"command": command})
}

func commonRulesJSON() string { return configws.Body(configws.CommonRules) }

func jsonEqual(t testing.TB, got string, want any) {
	t.Helper()
	var a, b any
	wantRaw, _ := json.Marshal(want)
	if err := json.Unmarshal([]byte(got), &a); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, got)
	}
	_ = json.Unmarshal(wantRaw, &b)
	ga, _ := json.Marshal(a)
	gb, _ := json.Marshal(b)
	if string(ga) != string(gb) {
		t.Errorf("rules.yml = %s, want %s", ga, gb)
	}
}

func assertGone(t testing.TB, paths ...string) {
	t.Helper()
	for _, p := range paths {
		if configws.Exists(p) {
			t.Errorf("%s がある", p)
		}
	}
}

func assertRead(t testing.TB, path, want string) {
	t.Helper()
	if got := configws.Read(t, path); got != want {
		t.Errorf("%s = %q, want %q", path, got, want)
	}
}

func assertHas(t testing.TB, text string, subs ...string) {
	t.Helper()
	for _, s := range subs {
		if !strings.Contains(text, s) {
			t.Errorf("%q が無い:\n%s", s, text)
		}
	}
}

// 1: 共通設定の中身を親のワークツリーの `.ccnavi/config/common/` へコピーする。config は触らない。
func TestParentStartMirrorsTheCommonLayerAndLeavesConfigAlone(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	configws.Write(t, filepath.Join(w.Root, ".ccnavi", "config", "common", "rule-expectations.yml"), expectations)
	before := map[string]string{}
	for _, k := range []string{"rules", "phases", "risk"} {
		before[k] = configws.Read(t, configOf(w.Lib, k))
	}

	tree, out := w.startParent("lib")

	jsonEqual(t, configws.Read(t, mirrorOf(tree, "rules.yml")), configws.CommonRules)
	assertRead(t, mirrorOf(tree, "risks.yml"), configws.CommonRiskText)
	assertRead(t, mirrorOf(tree, "rule-expectations.yml"), expectations)
	for kind, text := range before {
		assertRead(t, configOf(tree, kind), text)
		assertRead(t, configOf(w.Lib, kind), text)
	}
	// 元リポジトリのミラーは触らない（親のブランチに乗って届く）。
	assertGone(t, mirrorOf(w.Lib, "rules.yml"))
	assertHas(t, out, "ミラー", ".ccnavi/config/common/rules.yml")
	// 上書きの記録は置かない。
	assertGone(t, w.ApprovedPath("phases", "i0001", "config-sync.json"))
}

// 2: `ticket start` 自身はミラーを書かない。書くのは次のユーザの発言。
func TestTheStartDoesNotWriteTheMirror(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	w.addScript()
	tree := w.Worktree(w.Lib, "i0001")
	configws.Write(t, filepath.Join(tree, "wip", "proposals", "todo", "i0001.md"), configws.InsertPhases(ticketFor("i0001", "lib", "build"), configws.LibPhasesText))
	if done := w.Approve(); done.Code != 0 {
		t.Fatalf("approve: %s%s", done.Stdout, done.Stderr)
	}
	target := filepath.Join(w.Root, "logs", "state", "write-and-push", "lib", "i0001.t.writes")

	started := w.Ccnavi("--record-writes", target, "--record-tree", tree, "ticket", "start", "i0001")

	if started.Code != 0 {
		t.Fatalf("ticket start: %s%s", started.Stdout, started.Stderr)
	}
	if configws.Exists(target) && strings.Contains(configws.Read(t, target), ".ccnavi/config/common") {
		t.Error("ticket start の書き込みの記録にミラーがある")
	}
	assertGone(t, mirrorOf(tree, "rules.yml"))
	assertHas(t, started.Stdout, "次のユーザの発言")
	w.Prompt("s1", "disable")
	if !configws.Exists(mirrorOf(tree, "rules.yml")) {
		t.Error("次の発言でコピーされない")
	}
}

// 1: 単体 clone では、ミラー（`config/common/`）が共通として `--templates` に読まれる。
func TestTheMirroredTemplateIsReadAsTheCommonOneInASingleClone(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	configws.Write(t, filepath.Join(w.Root, ".ccnavi", "config", "common", "templates", "feature.yml"), featureTemplate)
	tree, _ := w.startParent("lib")

	shown := run.Ccnavi(t, []string{"--root", tree, "--templates", "--json"}, run.Opts{Dir: base.Root()})

	if shown.Code != 0 {
		t.Fatalf("%s%s", shown.Stdout, shown.Stderr)
	}
	var answer struct {
		Layers []struct {
			Name      string `json:"name"`
			Templates []struct{ Name, Path string }
		}
		Templates []struct {
			Name      string
			Effective bool
		}
	}
	if err := json.Unmarshal([]byte(shown.Stdout), &answer); err != nil {
		t.Fatal(err)
	}
	var common []string
	var paths []string
	for _, l := range answer.Layers {
		if l.Name == "common" {
			for _, tpl := range l.Templates {
				common = append(common, tpl.Name)
				paths = append(paths, tpl.Path)
			}
		}
	}
	if !slices.Equal(common, []string{"feature"}) || !slices.Equal(paths, []string{".ccnavi/config/common/templates/feature.yml"}) {
		t.Errorf("common = %v, paths = %v", common, paths)
	}
	if len(answer.Templates) != 1 || answer.Templates[0].Name != "feature" || !answer.Templates[0].Effective {
		t.Errorf("templates = %+v", answer.Templates)
	}
}

// 1: 共通のテンプレートが無くなれば、ミラーからも消す。空になった置き場も片付ける。
func TestATemplateGoneFromTheCommonLayerIsRemovedFromTheMirror(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	configws.Write(t, mirrorOf(w.Lib, "templates/old.yml"), strings.ReplaceAll(featureTemplate, "feature", "old"))
	run.Git(t, w.Lib, "add", "-A")
	run.Git(t, w.Lib, "commit", "--quiet", "-m", "old mirror")

	tree, out := w.startParent("lib")

	assertGone(t, mirrorOf(tree, "templates/old.yml"), mirrorOf(tree, "templates"))
	assertHas(t, out, "削除した")
}

// 1: `.ccnavi/` を持たないプロジェクトにも新しく置く。
func TestAProjectWithoutALayerGetsTheMirror(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	tree, out := w.startParent("app")
	assertRead(t, mirrorOf(tree, "risks.yml"), configws.CommonRiskText)
	assertHas(t, out, "新しく置いた")
	assertGone(t, configOf(tree, "rules"))
}

// 2: ワークスペース自身の作業は、共通設定と同じリポジトリにあるのでコピーしない。
func TestWorkspaceTicketsAreNotMirrored(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	tree, out := w.startParent("")
	if strings.Contains(out, "ミラー") {
		t.Errorf("ワークスペースの作業でミラーした:\n%s", out)
	}
	names := func(root string) []string {
		entries, err := os.ReadDir(filepath.Join(root, ".ccnavi", "config", "common"))
		if err != nil {
			t.Fatal(err)
		}
		var out []string
		for _, e := range entries {
			out = append(out, e.Name())
		}
		return out
	}
	if got, want := names(tree), names(w.Root); !slices.Equal(got, want) {
		t.Errorf("tree = %v, workspace = %v", got, want)
	}
}

// 2: 子は親のブランチに乗るので、子のワークツリーにはコピーしない。親のワークツリーには、発言でコピーされる。
func TestChildStartDoesNotMirror(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	w.Propose("i0001", ticketFor("i0001", "lib", "build"), "lib")
	w.Propose("i0001-01-01", configws.TicketText("i0001-01-01", configws.TicketOpts{Project: "lib", Parent: "i0001", Phase: 1, Allow: []string{"src/*"}}), "lib")
	if done := w.Approve(); done.Code != 0 {
		t.Fatalf("approve: %s%s", done.Stdout, done.Stderr)
	}
	parent := w.Worktree(w.Lib, "i0001")
	if w.Ccnavi("ticket", "start", "i0001").Code != 0 {
		t.Fatal("親の着手に失敗")
	}
	child := w.Worktree(w.Lib, "i0001-01-01")

	started := w.Ccnavi("ticket", "start", "i0001-01-01")
	w.Prompt("s1", "disable")

	if started.Code != 0 {
		t.Fatalf("%s%s", started.Stdout, started.Stderr)
	}
	assertGone(t, mirrorOf(child, "rules.yml"))
	if !configws.Exists(mirrorOf(parent, "rules.yml")) {
		t.Error("親のワークツリーにコピーされない")
	}
}

// 2: 承認済みの親の無いワークツリーにはコピーしない。
func TestAWorktreeWithoutAnApprovedParentIsNotMirrored(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	tree := w.Worktree(w.Lib, "scratchy")
	w.Prompt("s1", "disable")
	assertGone(t, mirrorOf(tree, "rules.yml"))
}

// 3: ミラーは誰も編集しないので、未コミットの変更があっても止めない。
func TestUncommittedEditsInTheMirrorAreOverwritten(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	tree := w.approveLib()
	configws.Write(t, mirrorOf(tree, "rules.yml"), "# 書きかけ\n")

	code, out := w.StartTicket("i0001")

	if code != 0 {
		t.Fatalf("%s", out)
	}
	jsonEqual(t, configws.Read(t, mirrorOf(tree, "rules.yml")), configws.CommonRules)
}

// 3: 上書きの知らせも、閉じるのを止める処理も無い。計画のある親は `finish` が全フェーズの終了を求めるので、閉じられはしない。
// 止める理由に、ミラーの上書きは出ない（計画の理由だけ）。
func TestNoNoticeAndNoGateAtClose(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	w.startParent("lib")

	closed := w.Ccnavi("ticket", "finish", "i0001")

	if closed.Code != 1 {
		t.Errorf("code = %d\n%s", closed.Code, closed.Stdout)
	}
	assertHas(t, closed.Stderr, "終わっていないフェーズがある")
	for _, word := range []string{"ミラー", "上書き", "config-synced"} {
		if strings.Contains(closed.Stdout+closed.Stderr, word) {
			t.Errorf("%q が出た:\n%s%s", word, closed.Stdout, closed.Stderr)
		}
	}
	if w.Exec("disable", "y\n", "--config-synced", "i0001").Code == 0 {
		t.Error("内部の旗 --config-synced を通した")
	}
}

// 2: `{root}` を使う共通ルールがあっても、親の着手が通りミラーされる。
func TestACommonRuleUsingRootIsMirrored(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	rooted := configws.Obj{"id": "workspace-root", "match": "Write|Edit", "regex": `^{root}[\\/]`}
	rules := configws.With(configws.CommonRules, "allow", []configws.Obj{rooted})
	configws.Write(t, w.Rules, configws.Body(rules))

	tree, _ := w.startParent("lib")

	jsonEqual(t, configws.Read(t, mirrorOf(tree, "rules.yml")), rules)
}

// 3: ミラーとして読めない共通設定・配るのに足りないもの（指すスクリプトが無い）は、何もコピーせず、理由を言う（着手は止めない）。
func TestACommonLayerTheMirrorCannotHoldIsReportedAndTheStartGoesOn(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name    string
		setup   func(*syncWS)
		mention string
	}{
		{"broken risk", func(w *syncWS) { configws.Write(w.T, w.Risk, "version: 1\nfactors: [\n") }, "ミラーとして読めない"},
		{"broken phases", func(w *syncWS) { configws.Write(w.T, w.Phases, "version: 1\nphases: [\n") }, "phases.yml"},
		{"missing script", func(w *syncWS) { configws.Write(w.T, w.Risk, commonScriptRisk) }, "count.sh"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			w := newSyncWS(t)
			tc.setup(w)
			tree := w.approveLib()

			code, out := w.StartTicket("i0001")

			if code != 0 {
				t.Fatalf("着手が止まった: %s", out)
			}
			assertHas(t, out, tc.mention)
			assertGone(t, mirrorOf(tree, "rules.yml"))
		})
	}
}

// 1: 共通設定に phases.yml が無く config にだけあるとき、ミラーに phases.yml は置かず、着手は通る。
// 前に配った phases.yml がミラーに残っていれば消す。config の phases.yml には触れない。
func TestWithoutACommonPhasesFileTheMirrorHasNone(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	tree := w.approveLib()
	configws.Write(t, mirrorOf(tree, "phases.yml"), configws.CommonPhasesText)

	code, out := w.StartTicket("i0001")

	if code != 0 {
		t.Fatalf("%s", out)
	}
	if !configws.Exists(mirrorOf(tree, "rules.yml")) {
		t.Error("rules.yml をコピーしない")
	}
	assertGone(t, mirrorOf(tree, "phases.yml"))
	assertRead(t, configOf(tree, "phases"), configws.LibPhasesText)
}

// 1: 共通設定の phases.yml もミラーへコピーする。config の phases.yml には触れない。
func TestACommonPhasesFileIsMirrored(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	configws.Write(t, w.Phases, configws.CommonPhasesText)
	tree := w.approveLib()

	code, out := w.StartTicket("i0001")

	if code != 0 {
		t.Fatalf("%s", out)
	}
	assertRead(t, mirrorOf(tree, "phases.yml"), configws.CommonPhasesText)
	assertRead(t, configOf(tree, "phases"), configws.LibPhasesText)
}

// 4: 発言でコピーした分は受け入れの対象になり、繰り返し報告しない。セッションの最初の実行後チェックは、基準を取ったあとにあった変更を
// 1 度だけ伝える（ユーザが発言の前に直した保護領域と同じ扱い）。2 回目からは伝えない。
func TestPostMonitorDoesNotReportTheMirror(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	tree, _ := w.startParent("lib")
	w.post(tree, "ls", "disable")

	said := w.post(tree, "ls", "disable")

	assertNoText(t, said.Stdout+said.Stderr, ".ccnavi/config/common")
}

func assertNoText(t testing.TB, text string, subs ...string) {
	t.Helper()
	for _, s := range subs {
		if strings.Contains(text, s) {
			t.Errorf("%q が出た:\n%s", s, text)
		}
	}
}

// 5: ミラーの後に手を入れて共通設定と違う中身にしたものは報告する。config の書き込みは、ミラーと中身が同じでも外さない。
func TestPostMonitorReportsEditsAfterTheMirror(t *testing.T) {
	t.Parallel()
	for _, tc := range []struct {
		name, file string
		edit       func(w *syncWS, tree string)
	}{
		{"the mirror", "risks.yml", func(w *syncWS, tree string) {
			configws.Write(w.T, mirrorOf(tree, "risks.yml"), configws.CommonRiskText+"# edited\n")
		}},
		{"the config", "rules.yml", func(w *syncWS, tree string) {
			configws.Write(w.T, configOf(tree, "rules"), configws.Read(w.T, w.Rules))
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			w := newSyncWS(t)
			tree, _ := w.startParent("lib")
			tc.edit(w, tree)

			said := w.post(tree, "ls", "disable")

			assertHas(t, said.Stdout+said.Stderr, tc.file)
		})
	}
}

// 4: 発言でコピーした分は戻さない。そのあと、ツール呼び出しの中で書き換わったら戻す。
func TestCoreFileGuardKeepsTheMirror(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	tree := w.approveLib()
	run.Git(t, tree, "add", "-A")

	w.pre(tree, startCommand, "enable")
	if started := w.Ccnavi("ticket", "start", "i0001"); started.Code != 0 {
		t.Fatalf("%s%s", started.Stdout, started.Stderr)
	}
	w.post(tree, startCommand, "enable")
	assertGone(t, mirrorOf(tree, "risks.yml"))

	w.Prompt("s1", "enable")
	assertRead(t, mirrorOf(tree, "risks.yml"), configws.CommonRiskText)
	w.pre(tree, "echo y", "enable")
	w.post(tree, "echo y", "enable")
	assertRead(t, mirrorOf(tree, "risks.yml"), configws.CommonRiskText)

	w.pre(tree, "echo x", "enable")
	configws.Write(t, mirrorOf(tree, "risks.yml"), "version: 1\n")
	w.post(tree, "echo x", "enable")
	assertRead(t, mirrorOf(tree, "risks.yml"), configws.CommonRiskText)
}

// 4: 共通設定から無くなって発言で消したミラーも、戻さない。
func TestCoreFileGuardKeepsARemovedMirrorFile(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	configws.Write(t, mirrorOf(w.Lib, "rules.yml"), commonRulesJSON())
	configws.Write(t, mirrorOf(w.Lib, "phases.yml"), configws.CommonPhasesText)
	run.Git(t, w.Lib, "add", "-A")
	run.Git(t, w.Lib, "commit", "--quiet", "-m", "old mirror")
	tree := w.approveLib()

	w.pre(tree, startCommand, "enable")
	if started := w.Ccnavi("ticket", "start", "i0001"); started.Code != 0 {
		t.Fatalf("%s%s", started.Stdout, started.Stderr)
	}
	w.post(tree, startCommand, "enable")
	w.Prompt("s1", "enable")
	assertGone(t, mirrorOf(tree, "phases.yml"))

	w.pre(tree, "echo x", "enable")
	w.post(tree, "echo x", "enable")

	assertGone(t, mirrorOf(tree, "phases.yml"))
}

// ミラーが統合先に入っても、共通設定の同じ項目と 2 回数えない。ワークスペースの中ではミラーを読まない（共通設定は 1 本）ので、
// そもそも数が増えない。
func TestAMirroredScriptFactorIsNotCountedTwice(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	w.addScript()
	tree, _ := w.startParent("lib")
	for _, rel := range []string{".ccnavi/config/common/risks.yml", ".ccnavi/config/common/scripts/count.sh"} {
		configws.Write(t, filepath.Join(w.Lib, filepath.FromSlash(rel)), configws.Read(t, filepath.Join(tree, filepath.FromSlash(rel))))
	}

	var out struct {
		Sums []struct {
			Name string
			Risk struct {
				Factors  []struct{ ID string }
				Problems []any
			}
		}
	}
	if err := json.Unmarshal([]byte(w.Ccnavi("--explain", "--json").Stdout), &out); err != nil {
		t.Fatal(err)
	}
	for _, s := range out.Sums {
		if s.Name != "lib" {
			continue
		}
		count := 0
		for _, f := range s.Risk.Factors {
			if f.ID == "counted" {
				count++
			}
		}
		if count != 1 || len(s.Risk.Problems) != 0 {
			t.Errorf("counted = %d, problems = %v", count, s.Risk.Problems)
		}
		return
	}
	t.Error("lib の和が無い")
}

// ---- 発言ごとの同期（差分が無ければ読み書きしない）

// 5: ターンの間にユーザが共通設定を直すと、次の発言でコピーされ、戻されない。
func TestAChangeInTheCommonLayerReachesTheMirrorAtTheNextPrompt(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	tree, _ := w.startParent("lib")
	w.Prompt("s1", "enable")
	edited := configws.CommonRiskText + "# 直した\n"
	configws.Write(t, w.Risk, edited)

	said := w.Prompt("s1", "enable")

	assertRead(t, mirrorOf(tree, "risks.yml"), edited)
	assertHas(t, said, "risks.yml")
	w.pre(tree, "echo x", "enable")
	w.post(tree, "echo x", "enable")
	assertRead(t, mirrorOf(tree, "risks.yml"), edited)
}

// 5: ターンの間にミラーが手で直されていれば、元が動いていなくても比べ直して揃える。
func TestAnEditOfTheMirrorBetweenTurnsIsOverwrittenAtTheNextPrompt(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	tree, _ := w.startParent("lib")
	configws.Write(t, mirrorOf(tree, "risks.yml"), "version: 1\n")

	w.Prompt("s1", "disable")

	assertRead(t, mirrorOf(tree, "risks.yml"), configws.CommonRiskText)
}

// 5: 同じ大きさで書き換え、更新時刻を元に戻しても、指紋が変わって比べ直す。
func TestARewriteWithTheSameSizeAndTimeIsStillNoticed(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	tree, _ := w.startParent("lib")
	info, err := os.Stat(w.Risk)
	if err != nil {
		t.Fatal(err)
	}
	text := configws.Read(t, w.Risk)
	configws.Write(t, w.Risk, strings.Replace(text, "points: 25", "points: 26", 1))
	if err := os.Chtimes(w.Risk, time.Now(), info.ModTime()); err != nil {
		t.Fatal(err)
	}
	if after, _ := os.Stat(w.Risk); after.Size() != info.Size() {
		t.Fatalf("大きさが変わった: %d → %d", info.Size(), after.Size())
	}

	w.Prompt("s1", "disable")

	got := configws.Read(t, mirrorOf(tree, "risks.yml"))
	if got == text || got != configws.Read(t, w.Risk) {
		t.Errorf("ミラー = %q", got)
	}
}

// 5: 並行するセッションの発言は、同じ内容を書くだけで、互いの結果を壊さない。
func TestParallelSessionsDoNotDisturbEachOther(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	tree, _ := w.startParent("lib")
	edited := configws.CommonRiskText + "# 直した\n"
	configws.Write(t, w.Risk, edited)

	w.Prompt("s2", "disable")
	again := w.Prompt("s1", "disable")

	assertRead(t, mirrorOf(tree, "risks.yml"), edited)
	assertNoText(t, again, "risks.yml")
}

// ミラーはワークスペースの中では読まれないので、「ワークツリーにしかない」とは言わない。
func TestLintDoesNotNameTheMirrorAsAWorktreeOnlyFile(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	w.startParent("lib")
	for _, p := range w.Problems("warn", "") {
		if strings.Contains(p.Detail, ".ccnavi/config/common") {
			t.Errorf("warn: %v", p)
		}
	}
}

// 元のプロジェクトの `.ccnavi/config/common/` のミラーも、守る対象（書き換えは戻す）。
func TestTheMirrorInTheOriginalProjectIsRestored(t *testing.T) {
	t.Parallel()
	w := newSyncWS(t)
	mirror := mirrorOf(w.Lib, "rules.yml")
	configws.Write(t, mirror, commonRulesJSON())
	run.Git(t, w.Lib, "add", "-A")
	run.Git(t, w.Lib, "commit", "--quiet", "-m", "mirror")

	w.pre(w.Root, "echo x", "enable")
	configws.Write(t, mirror, "version: 1\n")
	w.post(w.Root, "echo x", "enable")

	jsonEqual(t, configws.Read(t, mirror), configws.CommonRules)
}
