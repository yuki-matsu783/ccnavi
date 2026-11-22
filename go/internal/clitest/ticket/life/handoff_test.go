package life

import (
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 保護対象の下書きの取り込み（`ccnavi user handoff`。本体の tickets/sync/handoff）。取り込みの判断（取り込み先の表・断る形・--expect のハッシュ・
// 差分の見え方・テンプレートと defense の検査・防御の記述を動かす変更・読めない rules.yml の修復・同じ回に取り込む対象の検査し直し）は、本体の
// handoff の見本と表が見る。ここで見るのは、入口から起こす通しの次の 2 つ。
//  1. 入口: 組み合わせの拒否と終了コード、見る JSON の形（拡張と共有）、テキストの案内、書いたら下書きを消し、取り込み先の綴りを保つ
//  2. 経路: 書くにはターミナルか `--via board`。defense はボードから断る
// 下書きの置き場での階層名の読み替え（ccnavi-handoff-names.txt の表）は、stage → 編集 → user handoff で .claude/settings.json が元の場所に
// 戻ることを見る（変換・予約名・表の形は本体の handoffnames・handoff のテストが見る）。

const (
	handoffDocs     = ".ccnavi/docs/guide.md"
	handoffScript   = ".ccnavi/scripts/ccnavi-x.sh"
	handoffSettings = ".claude/settings.json"
	handoffTemplate = "name: feature\ndescription: 機能を設計から実装まで進める\nwhen: 新しい機能を足すとき\nplan:\n  - design\n  - {type: implement, after: [1]}\n"
	draftDirName    = "handoff-.claude"
)

// hoWS はワークスペースルートの置き場に下書きを置いて、取り込みを動かす道具。
type hoWS struct {
	t    *testing.T
	root string
}

func newHoWS(t *testing.T) *hoWS {
	t.Helper()
	w := &hoWS{t: t, root: base.TempDir(t)}
	ticketkit.Write(t, base.CommonPath(w.root, "rules"), samples.Rules)
	ticketkit.Write(t, base.CommonPath(w.root, "phases"), samples.Phases)
	ticketkit.Write(t, w.target(handoffDocs), "# 手引き\n\n古い文\n")
	ticketkit.Write(t, w.target(handoffScript), "#!/bin/sh\necho old\n")
	ticketkit.Write(t, w.target(handoffSettings), `{"hooks": {}}`+"\n")
	return w
}

func (w *hoWS) target(rel string) string { return filepath.Join(w.root, filepath.FromSlash(rel)) }

// toDraft は取り込み先の相対パスを、下書きの置き場での綴りにする（先頭の階層だけを読み替える）。
func toDraft(rel string) string {
	head, rest, _ := strings.Cut(rel, "/")
	if head == ".claude" {
		head = draftDirName
	}
	if rest == "" {
		return head
	}
	return head + "/" + rest
}

func (w *hoWS) draftPath(rel string) string {
	return filepath.Join(w.root, "wip", "handoff", filepath.FromSlash(toDraft(rel)))
}

// stage は ccnavi-stage.sh と同じく、元を下書きにコピーし、下書きを data にする（nil なら元のまま）。
func (w *hoWS) stage(rel string, data []byte) string {
	w.t.Helper()
	if data == nil {
		data, _ = os.ReadFile(w.target(rel))
	}
	return ticketkit.Write(w.t, w.draftPath(rel), string(data))
}

func (w *hoWS) handoff(guard string, args ...string) run.Result {
	w.t.Helper()
	if guard == "" {
		guard = "disable"
	}
	// ボードの経路（--via board）は拡張の子プロセスが呼ぶ形なので、印も board にする。
	entry := "sh"
	if slices.Contains(args, "board") {
		entry = "board"
	}
	return run.Ccnavi(w.t, append([]string{"--root", w.root, "--entry", entry, "user", "handoff"}, args...), run.Opts{Dir: base.Root(), Env: map[string]string{"CCNAVI_GUARD_TICKET_APPROVAL": guard}})
}

func (w *hoWS) view(paths ...string) map[string]any {
	w.t.Helper()
	r := ticketkit.OK(w.t, w.handoff("", append(paths, "--json")...))
	return ticketkit.ParseJSON(w.t, r.Stdout)
}

func (w *hoWS) take(paths ...string) run.Result {
	w.t.Helper()
	expect := w.view(paths...)["expect"].(string)
	return w.handoff("", append(paths, "--yes", "--expect", expect, "--json")...)
}

func (w *hoWS) board(expect string, paths ...string) run.Result {
	w.t.Helper()
	return w.handoff("enable", append(paths, "--yes", "--expect", expect, "--via", "board", "--json")...)
}

func entryOf(t *testing.T, answer map[string]any, rel string) map[string]any {
	t.Helper()
	var found []map[string]any
	for _, e := range answer["entries"].([]any) {
		if m := e.(map[string]any); m["path"] == rel {
			found = append(found, m)
		}
	}
	if len(found) != 1 {
		t.Fatalf("%s の項目が %d 件: %v", rel, len(found), answer)
	}
	return found[0]
}

func slurp(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func keysOf(m map[string]any) []string {
	var keys []string
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}

func TestHandoffExitCodesAndRefusedCombinations(t *testing.T) {
	t.Parallel()
	w := newHoWS(t)
	w.stage(handoffDocs, []byte("new\n"))
	ticketkit.Write(t, w.draftPath(handoffScript), "a\x00\n")
	// 見るだけは、下書きに error があっても 0。
	r := ticketkit.OK(t, w.handoff("", "--json"))
	if ticketkit.ParseJSON(t, r.Stdout)["ok"] != false {
		t.Error("error のある下書きで ok が真")
	}
	for _, args := range [][]string{
		{w.target(handoffDocs), "--json"}, {"../x.md", "--json"}, {"--yes", "--json"}, {"--yes", "--expect", "x", "--via", "terminal", "--json"},
		{"--lint"}, {"--docs"}, {"--yes", "i1"}, {"--templates"},
	} {
		if got := w.handoff("", args...); got.Code != 1 {
			t.Errorf("%v: code %d\n%s%s", args, got.Code, got.Stdout, got.Stderr)
		}
	}
	for _, args := range [][]string{
		{"--yes", "--via", "board", "--json"}, {"--yes", "--expect", "x", "--via", "board"}, {"--expect", "x", "--via", "board", "--json"},
	} {
		if got := w.handoff("enable", args...); got.Code != 1 {
			t.Errorf("%v: code %d\n%s%s", args, got.Code, got.Stdout, got.Stderr)
		}
	}
	if got := slurp(t, w.target(handoffDocs)); got != "# 手引き\n\n古い文\n" {
		t.Errorf("取り込み先が変わった: %q", got)
	}
	outside := run.Ccnavi(t, []string{"--root", w.root, "--handoff"}, run.Opts{Dir: base.Root()})
	if outside.Code != 2 {
		t.Errorf("code %d", outside.Code)
	}
	ticketkit.Has(t, outside.Stderr, "ccnavi-user-handoff.sh")
}

func TestTheHandoffViewJSONHasTheAgreedShape(t *testing.T) {
	t.Parallel()
	w := newHoWS(t)
	w.stage(handoffDocs, []byte("new\n"))
	answer := w.view()
	if want := []string{"entries", "expect", "ignored", "ignored_paths", "ok", "tree", "version"}; !slices.Equal(keysOf(answer), want) {
		t.Errorf("キー = %v", keysOf(answer))
	}
	if answer["version"] != float64(1) || answer["tree"] != w.root {
		t.Errorf("version = %v, tree = %v", answer["version"], answer["tree"])
	}
	want := []string{"board", "diff", "draft", "entry_sha", "errors", "kind", "path", "selected", "state", "warnings"}
	if got := keysOf(answer["entries"].([]any)[0].(map[string]any)); !slices.Equal(got, want) {
		t.Errorf("項目のキー = %v", got)
	}
}

func TestTheHandoffTextAnswerShowsTheDiffAndTheCommand(t *testing.T) {
	t.Parallel()
	w := newHoWS(t)
	w.stage(handoffDocs, []byte("# 手引き\n\n新しい文\n"))
	r := ticketkit.OK(t, w.handoff(""))
	ticketkit.Has(t, r.Stdout, "-古い文")
	ticketkit.Has(t, r.Stdout, "+新しい文")
	ticketkit.Has(t, r.Stdout, "ccnavi-user-handoff.sh --yes --expect "+w.view()["expect"].(string))
	ticketkit.Has(t, r.Stdout, "何も反映していません")
	ticketkit.Lacks(t, r.Stdout, "次:")
	// 名指しした下書きのパスは引用して案内する。空白を含むパスを貼っても、同じパスを指す。
	rel := ".ccnavi/docs/my guide.md"
	ticketkit.Write(t, w.target(rel), "# 手引き\n")
	w.stage(rel, []byte("# 手引き\n\n足した\n"))
	r = ticketkit.OK(t, w.handoff("", rel))
	ticketkit.Has(t, r.Stdout, "ccnavi-user-handoff.sh '"+rel+"' --yes --expect "+w.view(rel)["expect"].(string))
}

func TestHandoffYesWritesCleansTheDraftAndANewTemplateIsListed(t *testing.T) {
	t.Parallel()
	w := newHoWS(t)
	draft := w.stage(handoffDocs, []byte("new\n"))
	rel := ".ccnavi/config/project/templates/feature.yml"
	ticketkit.Write(t, w.draftPath(rel), handoffTemplate)
	r := ticketkit.OK(t, w.take())
	var got map[string]any
	jsonOf(t, r.Stdout, &got)
	want := map[string]any{
		"version": float64(1), "ok": true,
		"applied":        []any{map[string]any{"path": rel, "kind": "template"}, map[string]any{"path": handoffDocs, "kind": "docs"}},
		"removed_drafts": []any{"wip/handoff/" + rel, "wip/handoff/" + handoffDocs},
		"kept_drafts":    []any{},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("結果 = %v", got)
	}
	if slurp(t, w.target(handoffDocs)) != "new\n" || slurp(t, w.target(rel)) != handoffTemplate {
		t.Error("取り込み先の中身が違う")
	}
	if ticketkit.Exists(draft) {
		t.Error("下書きが残った")
	}
	listed := run.Ccnavi(t, []string{"--root", w.root, "--templates", "--json"}, run.Opts{Dir: base.Root()})
	var templates struct {
		Templates []struct{ Name string } `json:"templates"`
	}
	jsonOf(t, listed.Stdout, &templates)
	if len(templates.Templates) != 1 || templates.Templates[0].Name != "feature" {
		t.Errorf("テンプレート = %v", templates.Templates)
	}
}

func TestTheHandoffTargetKeepsItsSpelling(t *testing.T) {
	t.Parallel()
	if !base.CanCaseSensitive() {
		t.Skip("大文字小文字を区別しないファイルシステムでは同じ名前になる")
	}
	w := newHoWS(t)
	ticketkit.Write(t, w.draftPath(".ccnavi/docs/GUIDE.md"), "new\n")
	ticketkit.OK(t, w.take())
	if slurp(t, w.target(handoffDocs)) != "new\n" || ticketkit.Exists(w.target(".ccnavi/docs/GUIDE.md")) {
		t.Error("取り込み先の綴りが変わった")
	}
}

func TestTheTerminalIsNeededToWriteAndTheBoardRouteSkipsIt(t *testing.T) {
	t.Parallel()
	w := newHoWS(t)
	w.stage(handoffDocs, []byte("new\n"))
	w.stage(handoffScript, []byte("#!/bin/sh\necho new\n"))
	both := w.view()["expect"].(string)
	r := w.handoff("enable", "--yes", "--expect", both)
	if r.Code != 1 {
		t.Errorf("code %d", r.Code)
	}
	ticketkit.Has(t, r.Stderr, "ターミナル")
	ticketkit.OK(t, w.handoff("enable", "--json"))
	// defense はボードから断る。
	r = w.board(both)
	if r.Code != 1 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	answer := ticketkit.ParseJSON(t, r.Stdout)
	if answer["ok"] != false {
		t.Error("ok が真")
	}
	msg, _ := answer["error"].(string)
	ticketkit.Has(t, msg, "defense")
	ticketkit.Has(t, msg, "ターミナル")
	if slurp(t, w.target(handoffScript)) != "#!/bin/sh\necho old\n" || slurp(t, w.target(handoffDocs)) != "# 手引き\n\n古い文\n" {
		t.Error("断ったのに書いた")
	}
	// defense を外して選べば、ボードの経路でターミナル無しに書ける。
	ticketkit.OK(t, w.board(w.view(handoffDocs)["expect"].(string), handoffDocs))
	if slurp(t, w.target(handoffDocs)) != "new\n" || slurp(t, w.target(handoffScript)) != "#!/bin/sh\necho old\n" {
		t.Error("ボードの経路の書き方が違う")
	}
}

// ---- 下書きの置き場での階層名の読み替え

const settingsNew = `{"hooks": {"SessionStart": [{"timeout": 30}]}}` + "\n"

// stage → 編集 → 取り込みで、元の場所に戻る。
func TestAStagedSettingsDraftGoesBackToTheOriginal(t *testing.T) {
	t.Parallel()
	w := newHoWS(t)
	draft := w.stage(handoffSettings, []byte(settingsNew))
	if !strings.HasSuffix(draft, filepath.Join(draftDirName, "settings.json")) {
		t.Errorf("下書き = %s", draft)
	}
	if ticketkit.Exists(filepath.Join(w.root, "wip", "handoff", ".claude")) {
		t.Error("読み替えない階層名の置き場ができた")
	}
	answer := w.view()
	row := entryOf(t, answer, handoffSettings)
	if row["state"] != "changed" || row["draft"] != "wip/handoff/handoff-.claude/settings.json" || answer["ok"] != true {
		t.Errorf("項目 = %v, ok = %v", row, answer["ok"])
	}
	ticketkit.OK(t, w.take())
	if slurp(t, w.target(handoffSettings)) != settingsNew || ticketkit.Exists(draft) {
		t.Error("元の場所に戻っていない")
	}
}

func TestEitherPathFormSelectsTheDraft(t *testing.T) {
	t.Parallel()
	w := newHoWS(t)
	w.stage(handoffSettings, []byte(settingsNew))
	for _, arg := range []string{handoffSettings, "wip/handoff/handoff-.claude/settings.json", ".CLAUDE/settings.json"} {
		answer := w.view(arg)
		var selected []string
		for _, e := range answer["entries"].([]any) {
			if m := e.(map[string]any); m["selected"] == true {
				selected = append(selected, m["path"].(string))
			}
		}
		if answer["ok"] != true || !slices.Equal(selected, []string{handoffSettings}) {
			t.Errorf("%s: ok = %v, 選ばれた = %v", arg, answer["ok"], selected)
		}
	}
}

func TestHooksUnderTheHiddenDirectoryAreTakenToo(t *testing.T) {
	t.Parallel()
	w := newHoWS(t)
	hook := ".claude/hooks/lint-go.sh"
	ticketkit.Write(t, w.target(hook), "#!/bin/sh\necho old\n")
	w.stage(hook, []byte("#!/bin/sh\necho new\n"))
	ticketkit.OK(t, w.take())
	if got := slurp(t, w.target(hook)); got != "#!/bin/sh\necho new\n" {
		t.Errorf("中身 = %q", got)
	}
}

func TestSpellingVariantsOfTheDraftDirectoryAreOnePlace(t *testing.T) {
	t.Parallel()
	w := newHoWS(t)
	ticketkit.Write(t, filepath.Join(w.root, "wip", "handoff", "Handoff-.CLAUDE", "settings.json"), settingsNew)
	answer := w.view()
	var paths []string
	for _, e := range answer["entries"].([]any) {
		paths = append(paths, e.(map[string]any)["path"].(string))
	}
	if !slices.Equal(paths, []string{handoffSettings}) || answer["ok"] != true {
		t.Errorf("項目 = %v, ok = %v", paths, answer["ok"])
	}
}
