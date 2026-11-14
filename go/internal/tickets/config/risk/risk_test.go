package risk

import (
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/shell"

	"ccnavi/internal/tickets/testkit"
)

func factorIDs(d *Definition) []string {
	var out []string
	for _, f := range d.Factors {
		out = append(out, f.ID+"@"+f.Source)
	}
	return out
}

func TestLoadFallsBackToTheBuiltin(t *testing.T) {
	d, problems := Load(filepath.Join(t.TempDir(), "no", "risks.yml"))
	if len(problems) != 0 || d.Source != Builtin || len(d.Factors) != 4 || d.Fallback != "" {
		t.Errorf("無いファイル: %#v %v", d, problems)
	}
	// UTF-8 として読めない定義は、共通レイヤーなら組み込みへ、レイヤーなら空にして苦情を返す。
	path := testkit.Put(t, filepath.Join(t.TempDir(), "risks.yml"), "version: 1\nfactors: [{id: a, message: \xff\xfe\x80}]\n")
	d, problems = Load(path)
	if d.Source != Builtin || !strings.Contains(d.Fallback, "を読めない ('utf-8' codec can't decode byte 0xff") || len(problems) != 1 {
		t.Errorf("読めない: %#v %v", d, problems)
	}
	if layer, problems := LoadLayer(path, scriptHomes); layer != nil || len(problems) != 1 {
		t.Errorf("読めないレイヤー: %v %v", layer, problems)
	}
	broken := testkit.Put(t, filepath.Join(t.TempDir(), "risks.yml"), "version: 2\n")
	if d, _ := Load(broken); d.Source != Builtin || d.Fallback != broken+" が不正。組み込みの配点で数える" {
		t.Errorf("不正: %#v", d)
	}
	if d, problems := LoadLayer("", scriptHomes); d != nil || problems != nil {
		t.Error("空のパス")
	}
}

func TestEscalates(t *testing.T) {
	for level, want := range map[Level]bool{"HIGH": true, "CRITICAL": true, "MEDIUM": false, "LOW": false, "high": false, "": false} {
		if Escalates(level) != want {
			t.Errorf("%q", level)
		}
	}
}

func TestLayerDefinition(t *testing.T) {
	root := t.TempDir()
	conf := &config.Settings{Risk: filepath.Join(root, ".ccnavi", "config", "common", "risks.yml")}
	layer := filepath.Join(root, ".ccnavi", "config", "project", "risks.yml")
	// どちらにも無ければ組み込み。
	if d, notes := LayerDefinition(conf, root, ""); d.Source != Builtin || len(d.Factors) != 4 || notes != nil {
		t.Errorf("組み込み: %v %v", factorIDs(d), notes)
	}
	// 共通レイヤーだけが無ければ、共通レイヤーは空として、レイヤーの項目だけを数える。
	testkit.Put(t, layer, factors("  - {id: own, points: 5, files_over: 1}"))
	d, _ := LayerDefinition(conf, root, "")
	if got := factorIDs(d); !reflect.DeepEqual(got, []string{"own@self"}) || d.Factors[0].home != root {
		t.Errorf("レイヤーだけ: %v", got)
	}
	testkit.Put(t, conf.Risk, sampleRisk)
	d, notes := LayerDefinition(conf, root, "")
	if got := factorIDs(d); len(got) != 7 || got[6] != "own@self" || len(notes) != 0 {
		t.Errorf("和: %v %v", got, notes)
	}
	// レイヤーが壊れていれば空として数え、そのことを fallback に言う。
	testkit.Put(t, layer, "version: 1\nfactors: x\n")
	d, notes = LayerDefinition(conf, root, "")
	if len(d.Factors) != 6 || d.Fallback != "self の配点が不正。このレイヤーは空として数える" ||
		!reflect.DeepEqual(d.Dropped, []string{"self"}) || len(notes) != 1 {
		t.Errorf("壊れたレイヤー: %v %q %v", factorIDs(d), d.Fallback, notes)
	}
	// 合成で閾値が逆転すれば、レイヤーを空として数える。
	testkit.Put(t, layer, "version: 1\nlevels: {high: 39, critical: 39}\n")
	testkit.Put(t, conf.Risk, "version: 1\nlevels: {medium: 40, high: 60}\n")
	d, notes = LayerDefinition(conf, root, "")
	if d.Fallback != "self の配点の閾値が合成で逆転している。このレイヤーは空として数える" || len(notes) != 1 || notes[0].Rule != levelsWhere {
		t.Errorf("逆転: %q %v", d.Fallback, notes)
	}
	// 共通レイヤーが壊れていれば組み込みに戻り、レイヤーを足さない。
	testkit.Put(t, conf.Risk, "version: 9\n")
	if d, notes := LayerDefinition(conf, root, ""); d.Source != Builtin || d.Fallback == "" || notes != nil {
		t.Errorf("壊れた共通: %#v %v", d, notes)
	}
	// 予約名のプロジェクトはレイヤーとして数えない（配点を書ける側がレイヤーを選べないように）。
	testkit.Put(t, conf.Risk, sampleRisk)
	if d := LoadDefinition(conf, root, config.LayerSelf); len(d.Factors) != 6 {
		t.Errorf("予約名: %v", factorIDs(d))
	}
}

func TestScriptKeyComparesContentNotPlace(t *testing.T) {
	root := t.TempDir()
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "scripts", "c.sh"), "echo 1\r\n")
	testkit.Put(t, filepath.Join(root, ".ccnavi", "scripts", "c.sh"), "echo 1\n")
	testkit.Put(t, filepath.Join(root, ".ccnavi", "scripts", "d.sh"), "echo 2\n")
	common, _ := Parse(factors("  - {id: c, points: 1, script: .ccnavi/config/common/scripts/c.sh}"), where, scriptHomes)
	common.markLayer(config.LayerCommon, root)
	homes := []string{".ccnavi/scripts/"}
	same, _ := Parse(factors("  - {id: c, points: 1, script: .ccnavi/scripts/c.sh}"), where, homes)
	same.markLayer("self", root)
	other, _ := Parse(factors("  - {id: c, points: 1, script: .ccnavi/scripts/d.sh}"), where, homes)
	other.markLayer("self", root)
	if merged, _ := merge(common, same, "self"); len(merged.Factors) != 1 {
		t.Errorf("同じ中身を 2 回数える: %v", factorIDs(merged))
	}
	if merged, _ := merge(common, other, "self"); len(merged.Factors) != 2 || merged.Factors[1].ID != "self:c" {
		t.Errorf("違う中身を捨てる: %v", factorIDs(merged))
	}
	if problems := ScriptProblems(other, "self"); len(problems) != 0 {
		t.Errorf("在るのに %v", problems)
	}
	missing, _ := Parse(factors("  - {id: m, points: 1, script: .ccnavi/scripts/none.sh}"), where, homes)
	missing.markLayer("self", root)
	if problems := ScriptProblems(missing, "self"); len(problems) != 1 || ScriptProblems(missing, "web") != nil {
		t.Errorf("無いのに %v", problems)
	}
}

func TestRunScript(t *testing.T) {
	if _, err := exec.LookPath(shell.Program); err != nil {
		t.Skip("sh が無い")
	}
	root := t.TempDir()
	dir := ".ccnavi/config/common/scripts/"
	scripts := map[string]string{
		"json.sh":    `printf '{"points": 7, "message": "複雑度 %s"}' "$RISK_TEST_VALUE"`,
		"int.sh":     "echo 3",
		"zero.sh":    "echo 0",
		"fail.sh":    "echo first >&2; echo last >&2; exit 4",
		"failout.sh": "echo said; exit 2",
		"empty.sh":   "true",
		"text.sh":    "echo " + strings.Repeat("x", 70),
		"nopts.sh":   `echo '{"points": -1}'`,
		"float.sh":   "echo 1.5",
		"cwd.sh":     `[ "$(basename "$PWD")" = tree ] && echo 9`,
	}
	for name, body := range scripts {
		testkit.Put(t, filepath.Join(root, filepath.FromSlash(dir), name), body+"\n")
	}
	worktree := filepath.Join(root, "tree")
	if err := os.MkdirAll(worktree, 0o755); err != nil {
		t.Fatal(err)
	}
	env := map[string]string{"RISK_TEST_VALUE": "高"}
	cases := []struct {
		name   string
		points int
		note   string
		ok     bool
	}{
		{"json.sh", 7, "複雑度 高", true},
		{"int.sh", 3, "", true},
		{"zero.sh", 0, "", true},
		{"cwd.sh", 9, "", true},
		{"fail.sh", 0, dir + "fail.sh が 4 で終わった: last", false},
		{"failout.sh", 0, dir + "failout.sh が 2 で終わった: said", false},
		{"empty.sh", 0, dir + "empty.sh が何も出さなかった", false},
		{"text.sh", 0, dir + "text.sh の出力を点として読めない: " + strings.Repeat("x", 60), false},
		{"nopts.sh", 0, dir + "nopts.sh の JSON に `points`（0 以上の整数）がありません。", false},
		{"float.sh", 0, dir + "float.sh の出力を点として読めない: 1.5", false},
		{"none.sh", 0, dir + "none.sh がありません。", false},
	}
	for _, c := range cases {
		points, note, ok := runScript(root, dir+c.name, worktree, env)
		if points != c.points || note != c.note || ok != c.ok {
			t.Errorf("%s: %d %q %v", c.name, points, note, ok)
		}
	}
	// 測れなかった項目は重いほうとして配点の全部を加点する。
	d, _ := Parse(factors("  - {id: n, points: 1, script: "+dir+"int.sh, message: 数}",
		"  - {id: z, points: 5, script: "+dir+"zero.sh}", "  - {id: b, points: 45, script: "+dir+"none.sh, message: 無い}"), where, scriptHomes)
	d.markLayer(config.LayerCommon, "")
	s := Evaluate(d, &Diff{}, root, worktree, env, nil)
	if s.points != 48 || s.Level != High || !reflect.DeepEqual(s.unmeasured, []string{"b: " + dir + "none.sh がありません。"}) ||
		s.hits[0].detail != "数（スクリプト）" || s.hits[1].detail != "無い（測れなかった: "+dir+"none.sh がありません。）" {
		t.Errorf("%#v", s)
	}
}

func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	return strings.TrimSpace(testkit.Git(t, dir, args...))
}

func TestMeasure(t *testing.T) {
	repo := testkit.GitRoot(t)
	runGit(t, repo, "init", "-q", "-b", "main")
	testkit.Put(t, filepath.Join(repo, "keep.txt"), "a\nb\nc\n")
	testkit.Put(t, filepath.Join(repo, "gone.txt"), "x\n")
	testkit.Put(t, filepath.Join(repo, "old name.txt"), strings.Repeat("same line\n", 20))
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-q", "-m", "base")
	base := runGit(t, repo, "rev-parse", "HEAD")
	testkit.Put(t, filepath.Join(repo, "keep.txt"), "a\nB\nc\nd\n")
	testkit.Put(t, filepath.Join(repo, "新しい.bin"), "\x00\x01\x02")
	if err := os.Remove(filepath.Join(repo, "gone.txt")); err != nil {
		t.Fatal(err)
	}
	runGit(t, repo, "mv", "old name.txt", "new name.txt")
	runGit(t, repo, "add", "-A")
	runGit(t, repo, "commit", "-q", "-m", "work")
	head := runGit(t, repo, "rev-parse", "HEAD")
	diff, why := Measure(repo, base, "HEAD")
	if diff == nil {
		t.Fatal(why)
	}
	got := map[string]Change{}
	for _, c := range diff.changes {
		got[c.path] = c
	}
	want := map[string]Change{
		"keep.txt":     {path: "keep.txt", added: 2, deleted: 1, status: "M"},
		"新しい.bin":      {path: "新しい.bin", status: "A"},
		"gone.txt":     {path: "gone.txt", deleted: 1, status: "D"},
		"new name.txt": {path: "new name.txt", status: "R", old: "old name.txt"},
	}
	if !reflect.DeepEqual(got, want) || diff.Head != head || diff.Base != base {
		t.Errorf("%#v", diff)
	}
	if diff.Summary() != "4 ファイル、+2 -2 行、削除したファイル 1" {
		t.Errorf("%q", diff.Summary())
	}
	// 子の仕事の始まりが無い・読めないなら止める（0 件と数えると軽い側へ倒れる）。
	if d, why := Measure(repo, "", "HEAD"); d != nil || why != "子の仕事の始まり（親との共通の祖先）がありません。" {
		t.Errorf("%q", why)
	}
	if d, why := Measure(repo, "nope", "HEAD"); d != nil || why != "子の仕事の始まりからの差分を読めない" {
		t.Errorf("%q", why)
	}
}

func TestIncludeExcludeNarrowWhatIsCounted(t *testing.T) {
	// rename は旧パスが include に当たれば数え、exclude は新旧の両方に当たったときだけ外す。
	d, _ := Parse(factors("  - {id: inc, points: 1, lines_over: 0, include: ['src/**']}",
		"  - {id: exc, points: 2, lines_over: 0, exclude: ['vendor/**']}",
		"  - {id: both, points: 4, lines_over: 0, exclude: ['vendor/**', 'src/**']}"), where, scriptHomes)
	moved := Change{path: "vendor/core.py", added: 60, deleted: 60, status: "R", old: "src/core.py"}
	s := Evaluate(d, &Diff{changes: []Change{moved}}, "", "", nil, nil)
	got := map[string]int{}
	for _, h := range s.hits {
		got[h.id] = h.points
	}
	if !reflect.DeepEqual(got, map[string]int{"inc": 1, "exc": 2}) {
		t.Errorf("%v", got)
	}
}
