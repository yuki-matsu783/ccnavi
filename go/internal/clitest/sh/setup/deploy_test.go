package setup

import (
	"path/filepath"
	"reflect"
	"sort"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/shkit"
)

// `--deploy` が、設定だけでは動かないものを配布元から配る。`dist/` は .gitignore に入っていて git では渡らず、`CCNAVI_ENTRY_PATH` は
// 相対でしか書けない。よそで組んだ実行ファイルを指すこともできないので、対象プロジェクトの中に実体を置く経路がこのスクリプトに要る。

// 1 回で、設定を書き、実行ファイル・ルール・sh・作業の決まり・使い方の文書を配る。中身は変えない。
func TestDeployWritesTheSettingsAndCopiesThePartsInOneRun(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := makeSource(t, srcOpts{})
	r := w.setup("--mode", "enable", "--deploy", src)
	code(t, r, 0)
	eq(t, "CCNAVI_MODE", w.envOf()["CCNAVI_MODE"], any("enable"))
	if !shkit.Exists(w.built(thisMachine(), "ccnavi")) {
		t.Error("実行ファイルを配っていない")
	}
	eq(t, "振り分けの sh", shkit.Read(t, w.launcher()), shkit.Read(t, launcherPath()))
	for _, rel := range []string{"config/common/rules.yml", "config/common/risks.yml", "config/common/phases.yml", "config/common/ticket-sections.yml"} {
		if !shkit.Exists(w.path(".ccnavi", filepath.FromSlash(rel))) {
			t.Errorf("%s を配っていない", rel)
		}
	}
	// 自身のレイヤーの phases.yml は配らない（レイアウトに依存する定義は配布先のユーザが書く）。
	if shkit.Exists(w.path(".ccnavi", "config", "project", "phases.yml")) {
		t.Error("自身のレイヤーの phases.yml を配った")
	}
	for _, name := range deployScripts {
		if !shkit.Exists(w.path(".ccnavi", "scripts", name)) {
			t.Errorf("%s を配っていない", name)
		}
	}
	// セッションの開始で渡す作業の決まりと、使い方の文書を、中身を変えずに配る。
	eq(t, "CCNAVI.md", shkit.Read(t, w.path(".ccnavi", "CCNAVI.md")), "# ccnavi の作業の決まり\n")
	wantListed(t, r.Stdout, "配った", ".ccnavi/CCNAVI.md")
	for _, name := range deployDocs {
		eq(t, name, shkit.Read(t, w.path(".ccnavi", "docs", name)), shkit.Read(t, filepath.Join(src, ".ccnavi", "docs", name)))
	}
	wantListed(t, r.Stdout, "配った", ".ccnavi/docs/worktree.md")
}

// 実際の .ccnavi/docs を載せた配布元で、提案の書式の文書が配布先に出る。
func TestDeployDeploysTheProposalFormatDocFromTheRealSourceTree(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := makeSource(t, srcOpts{NoDocs: true})
	docs := filepath.Join(base.Root(), ".ccnavi", "docs")
	shkit.CopyTree(t, docs, filepath.Join(src, ".ccnavi", "docs"))
	r := w.setup("--mode", "enable", "--deploy", src)
	code(t, r, 0)
	eq(t, "proposal-format.md", shkit.Read(t, w.path(".ccnavi", "docs", "proposal-format.md")), shkit.Read(t, filepath.Join(docs, "proposal-format.md")))
	wantListed(t, r.Stdout, "配った", ".ccnavi/docs/proposal-format.md")
}

// 配布先に既にあるものは入れ替えない。入れ替えるのは --force のときだけ（使い方の文書・共通のテンプレート・工程のスキル）。
func TestDeployKeepsWhatTheProjectAlreadyHasUnlessForced(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name   string
		opts   srcOpts
		path   []string
		mine   string
		forced string
	}{
		{"使い方の文書", srcOpts{}, []string{".ccnavi", "docs", "worktree.md"}, "# このプロジェクトで直した\n", "# worktree.md\n"},
		{"共通のテンプレート", srcOpts{Templates: map[string]string{"feature.yml": "name: feature\n"}},
			[]string{".ccnavi", "config", "common", "templates", "feature.yml"}, "name: feature\ndescription: このプロジェクトで直した\n", "name: feature\n"},
		{"工程のスキル", srcOpts{Skills: processSkillFiles()}, []string{".claude", "skills", "process-test", "SKILL.md"}, "このプロジェクトで直した\n", processSkill},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w := newTarget(t)
			src := makeSource(t, c.opts)
			shkit.Write(t, w.path(c.path...), c.mine)
			w.setup("--deploy", src)
			eq(t, "既にあるもの", shkit.Read(t, w.path(c.path...)), c.mine)
			w.setup("--deploy", src, "--force")
			eq(t, "--force のあと", shkit.Read(t, w.path(c.path...)), c.forced)
		})
	}
}

// 配布元に .ccnavi/docs/ が無ければ、配れないものに挙げる。
func TestDeployNamesTheDocsDirectoryTheSourceDoesNotHave(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--deploy", makeSource(t, srcOpts{NoDocs: true}))
	code(t, r, 0)
	wantListed(t, r.Stdout, "配布元に無くて配れないもの", ".ccnavi/docs/")
}

// ---- 共通の作業・レビュー計画のテンプレート

// 配布元の共通のテンプレートを、配布先の同じ場所へ中身を変えずに配る。`.yml` でないもの、`.` で始まるものは配らない（読まれないため）。
func TestDeployCopiesOnlyTheYmlTemplatesWithoutChangingThem(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	templates := map[string]string{"feature.yml": "name: feature\nplan: [design]\n", "fix.yml": "name: fix\n", "notes.txt": "x\n", ".hidden.yml": "x\n"}
	r := w.setup("--mode", "enable", "--deploy", makeSource(t, srcOpts{Templates: templates}))
	code(t, r, 0)
	place := w.path(".ccnavi", "config", "common", "templates")
	if got := shkit.Names(t, place); !reflect.DeepEqual(got, []string{"feature.yml", "fix.yml"}) {
		t.Errorf("配ったテンプレート = %v", got)
	}
	for _, name := range []string{"feature.yml", "fix.yml"} {
		eq(t, name, shkit.Read(t, filepath.Join(place, name)), templates[name])
		wantListed(t, r.Stdout, "配った", ".ccnavi/config/common/templates/"+name)
	}
	notIn(t, r.Stdout, "まだ無いもの")
}

// 空白や glob の文字を含む名前は配らず、そのことを言う。ほかのファイルは配る（単語分割で cp が失敗すると、配布が半端なところで止まる）。
func TestDeployATemplateNameWithASpaceIsSkippedAndTheRestIsDeployed(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := makeSource(t, srcOpts{Templates: map[string]string{
		"feature.yml": "name: feature\n", "my plan.yml": "name: x\n", "star*.yml": "name: y\n", "-dash.yml": "name: z\n",
	}})
	r := w.setup("--mode", "enable", "--deploy", src)
	code(t, r, 0)
	if got := shkit.Names(t, w.path(".ccnavi", "config", "common", "templates")); !reflect.DeepEqual(got, []string{"feature.yml"}) {
		t.Errorf("配ったテンプレート = %v", got)
	}
	if !shkit.Exists(w.launcher()) || !shkit.Exists(w.rules()) {
		t.Error("ほかのファイルが配られていない")
	}
	in(t, r.Stdout, "名前に使えない文字があるので配っていないテンプレート")
	for _, name := range []string{"my plan.yml", "star*.yml", "-dash.yml"} {
		in(t, r.Stdout, "  "+name+"\n")
	}
	notIn(t, r.Stdout, "まだ無いもの")
}

// 配布元に共通のテンプレートが無ければ（置き場が無い・空）、何も足さず何も言わない。標準エラーも空のまま。
func TestDeployASourceWithoutTemplatesAddsNothingAndSaysNothing(t *testing.T) {
	t.Parallel()
	for name, templates := range map[string]map[string]string{"置き場が無い": nil, "置き場が空": {}} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			w := newTarget(t)
			r := w.setup("--mode", "enable", "--deploy", makeSource(t, srcOpts{Templates: templates}))
			code(t, r, 0)
			eq(t, "stderr", r.Stderr, "")
			if shkit.Exists(w.path(".ccnavi", "config", "common", "templates")) {
				t.Error("テンプレートの置き場を作った")
			}
			notIn(t, r.Stdout, "テンプレート", "templates")
		})
	}
}

// ---- 工程のスキル

const processSkill = "---\nname: process-test\nprocess: true\ndescription: 入口\n---\n# process-test\n"

func processSkillFiles() map[string]string {
	return map[string]string{
		"process-test/SKILL.md":                      processSkill,
		"process-test/references/common/purpose.md":  "共通\n",
		"process-test/references/common/sub/deep.md": "深い共通\n",
		"process-test/references/project/manual.md":  "配布元固有\n",
		"commit/SKILL.md":                            "---\nname: commit\ndescription: 配布元固有\n---\n",
		"flag/SKILL.md":                              "---\nname: flag\nprocess: \"true\"\n---\n",
		"late/SKILL.md":                              "---\nname: late\n---\nprocess: true\n",
	}
}

func filesUnder(t testing.TB, dir string) []string {
	t.Helper()
	if !shkit.Exists(dir) {
		return []string{}
	}
	return listing(t, dir)
}

// `process: true` のスキルの SKILL.md と references/common/ だけを、中身を変えずに配る。references/project/ は配布元固有、
// process が無い・文字列・本文にあるだけのスキルは対象外。
func TestDeployDeploysOnlyTheCommonPartOfTheProcessSkills(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--mode", "enable", "--deploy", makeSource(t, srcOpts{Skills: processSkillFiles()}))
	code(t, r, 0)
	skills := w.path(".claude", "skills")
	want := []string{"process-test/SKILL.md", "process-test/references/common/purpose.md", "process-test/references/common/sub/deep.md"}
	if got := filesUnder(t, skills); !reflect.DeepEqual(got, want) {
		t.Errorf("配ったスキル = %v", got)
	}
	eq(t, "SKILL.md", shkit.Read(t, filepath.Join(skills, "process-test", "SKILL.md")), processSkill)
	wantListed(t, r.Stdout, "配った", ".claude/skills/process-test/references/common/purpose.md")
	notIn(t, r.Stdout, "まだ無いもの")
}

// `distribute: true` のスキルも、SKILL.md と references/common/ の下の .md だけを配る（references 直下は配らない）。
// 生成物（ccnavi-index.jsonl・.DS_Store・.md でないもの）は配らない（着手がコピーするのも .md だけ）。
func TestDeployDeploysOnlyMarkdownUnderReferencesCommon(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := makeSource(t, srcOpts{Skills: map[string]string{
		"tool/SKILL.md":                             "---\nname: tool\ndistribute: true\ndescription: 配るだけ\n---\n# tool\n",
		"tool/references/common/add.md":             "足す\n",
		"tool/references/common/ccnavi-index.jsonl": "{}\n",
		"tool/references/common/.DS_Store":          "x",
		"tool/references/common/blob.bin":           "x",
		"tool/references/direct.md":                 "直下は配らない\n",
		"tool/references/project/own.md":            "配布元固有\n",
	}})
	code(t, w.setup("--mode", "enable", "--deploy", src), 0)
	if got := filesUnder(t, w.path(".claude", "skills", "tool")); !reflect.DeepEqual(got, []string{"SKILL.md", "references/common/add.md"}) {
		t.Errorf("配ったスキル = %v", got)
	}
}

// 配る判定は、親チケットの着手がコピーする判定（process）と同じ答えを返す。食い違うと、着手がコピーする元の SKILL.md が配布先に届かないまま、
// 何の知らせも出ない。特に Windows の Git Bash は SKILL.md を CRLF で取り出すことがある。着手の側の判定は go test
// （go/internal/tickets/sync/mirror/setup_sh_test.go）が同じ表で見る。distribute は同じ読み方で、配るだけのスキルを工程として数えない。
func TestDeployReadsTheFlagsLikeTheTicketStartDoes(t *testing.T) {
	t.Parallel()
	type flagCase struct {
		text   string
		deploy bool
	}
	for _, flag := range []string{"process", "distribute"} {
		cases := map[string]flagCase{
			"lf":                    {"---\nname: x\n" + flag + ": true\n---\n", true},
			"crlf":                  {"---\r\nname: x\r\n" + flag + ": true\r\n---\r\n", true},
			"trailing comment":      {"---\n" + flag + ": true # 配る\n---\n", true},
			"bom":                   {"\ufeff---\n" + flag + ": true\n---\n", true},
			"no closing fence":      {"---\n" + flag + ": true\n", false},
			"string":                {"---\n" + flag + ": \"true\"\n---\n", false},
			"false":                 {"---\n" + flag + ": false\n---\n", false},
			"in the body":           {"---\nname: x\n---\n" + flag + ": true\n", false},
			"commented out":         {"---\n# " + flag + ": true\n---\n", false},
			"flag before the fence": {flag + ": true\n---\nname: x\n---\n", false},
		}
		if flag == "process" {
			cases["padded fences"] = flagCase{"--- \nprocess: true\n---  \n", true}
		}
		// スキルごとに判定するので、1 つの配布元に並べて 1 回で配っても答えは変わらない。
		var labels []string
		for label := range cases {
			labels = append(labels, label)
		}
		sort.Strings(labels)
		skills := map[string]string{}
		for i, label := range labels {
			skills["probe-"+string(rune('a'+i))+"/SKILL.md"] = cases[label].text
		}
		w := newTarget(t)
		code(t, w.setup("--mode", "enable", "--deploy", makeSource(t, srcOpts{Skills: skills})), 0)
		for i, label := range labels {
			got := shkit.Exists(w.path(".claude", "skills", "probe-"+string(rune('a'+i)), "SKILL.md"))
			if got != cases[label].deploy {
				t.Errorf("%s: %s を配ったか = %v, want %v", flag, label, got, cases[label].deploy)
			}
		}
	}
}

// 着手のコピーは SKILL.md のリンクを弾く。配る側も、リンク先の中身を配らない。
func TestDeployDoesNotDeployASkillMdThatIsASymlink(t *testing.T) {
	t.Parallel()
	if !base.CanSymlink() {
		t.Skip("シンボリックリンクを作れない")
	}
	w := newTarget(t)
	src := makeSource(t, srcOpts{Skills: processSkillFiles()})
	skillMd := filepath.Join(src, ".claude", "skills", "process-test", "SKILL.md")
	shkit.Remove(t, skillMd)
	outside := shkit.Write(t, filepath.Join(src, "outside.md"), processSkill)
	symlink(t, outside, skillMd)
	code(t, w.setup("--mode", "enable", "--deploy", src), 0)
	if shkit.Exists(w.path(".claude", "skills", "process-test")) {
		t.Error("リンクの SKILL.md を配った")
	}
}

// 配布先の道にファイルがあると mkdir が失敗して配布が半端に止まる。名指しして続ける。
func TestDeployNamesABlockedPlaceAndGoesOnDeploying(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	src := makeSource(t, srcOpts{Skills: processSkillFiles()})
	shkit.Write(t, w.path(".claude", "skills", "process-test"), "ユーザのファイル\n")
	r := w.setup("--mode", "enable", "--deploy", src)
	code(t, r, 0)
	wantListed(t, r.Stdout, "配布先の道にディレクトリでないものがある", ".claude/skills/process-test/SKILL.md")
	eq(t, "ユーザのファイル", shkit.Read(t, w.path(".claude", "skills", "process-test")), "ユーザのファイル\n")
	if !shkit.Exists(w.launcher()) {
		t.Error("後ろの配布物が届いていない")
	}
}

func TestDeployCheckListsTheProcessSkillFilesItWouldDeploy(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--deploy", makeSource(t, srcOpts{Skills: processSkillFiles()}), "--check")
	code(t, r, 1)
	wantListed(t, r.Stdout, "配る", ".claude/skills/process-test/SKILL.md")
	if shkit.Exists(w.path(".claude", "skills")) {
		t.Error("--check が書いた")
	}
}

func TestDeployASourceWithoutProcessSkillsDeploysNothingOfThem(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--mode", "enable", "--deploy", makeSource(t, srcOpts{Skills: map[string]string{"commit/SKILL.md": "---\nname: commit\n---\n"}}))
	code(t, r, 0)
	if shkit.Exists(w.path(".claude", "skills")) {
		t.Error("スキルを配った")
	}
	notIn(t, r.Stdout, "skills")
}

// 配ったあとは「まだ無いもの」が出ない（ここが出たままだと、配ったのか配れなかったのかをユーザが読み取れない）。
func TestDeploySaysNothingIsMissingAfterItCopied(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--mode", "enable", "--deploy", makeSource(t, srcOpts{}))
	notIn(t, r.Stdout, "まだ無いもの")
	in(t, r.Stdout, "配った")
}
