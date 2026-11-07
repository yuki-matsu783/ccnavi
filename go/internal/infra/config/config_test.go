package config

import (
	"math"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/env"
)

func TestTunableValue(t *testing.T) {
	tunable := Tunable{"CCNAVI_TEST_TUNABLE_SECONDS", 3 * time.Second}
	cases := []struct {
		written string
		set     bool
		want    time.Duration
	}{
		{"", false, 3 * time.Second},
		{"", true, 3 * time.Second},
		{"abc", true, 3 * time.Second},
		{"0", true, 3 * time.Second},
		{"-1", true, 3 * time.Second},
		{"nan", true, 3 * time.Second},
		{"inf", true, 3 * time.Second},
		{"-inf", true, 3 * time.Second},
		{"1e999", true, 3 * time.Second},
		{"0x10", true, 3 * time.Second}, // Python の float は 16 進を読まない
		{"1__0", true, 3 * time.Second},
		{"10", true, 10 * time.Second},
		{" 0.5 ", true, 500 * time.Millisecond},
		{"1_0", true, 10 * time.Second}, // Python の float は数字の間の _ を読む
	}
	for _, c := range cases {
		if c.set {
			t.Setenv(tunable.env, c.written)
		} else {
			os.Unsetenv(tunable.env)
		}
		if got := tunable.Value(); got != c.want {
			t.Errorf("Value(%q) = %v, want %v", c.written, got, c.want)
		}
	}
}

func TestTunableDefaults(t *testing.T) {
	all := map[Tunable]time.Duration{
		HookDeadline:       3 * time.Second,
		IntegratePending:   2 * time.Second,
		SubagentRunningMax: 4 * time.Hour,
		TurnStale:          30 * time.Minute,
		StopReviewTTL:      600 * time.Second,
		AskContextWait:     2500 * time.Millisecond,
	}
	seen := map[string]bool{}
	for tunable, want := range all {
		if tunable.Default != want {
			t.Errorf("%s の既定 = %v, want %v", tunable.env, tunable.Default, want)
		}
		if seen[tunable.env] {
			t.Errorf("%s が重なっている", tunable.env)
		}
		seen[tunable.env] = true
	}
}

func TestToolGroups(t *testing.T) {
	if !reflect.DeepEqual(writeTools, []string{"Write", "Edit", "MultiEdit", "NotebookEdit"}) ||
		!reflect.DeepEqual(agentTools, []string{"Agent", "Task"}) ||
		!reflect.DeepEqual(shellTools, []string{"Bash", "PowerShell"}) || WriteMatch != "Write|Edit|MultiEdit|NotebookEdit" {
		t.Fatal("ツール名の組の中身が変わった")
	}
	subset := func(a, b []string) bool {
		for _, x := range a {
			if !slices.Contains(b, x) {
				return false
			}
		}
		return true
	}
	if !subset(writeTools, strictTools) || !subset(agentTools, strictTools) || !subset(readTools, readOnlyTools) {
		t.Error("組の包含が崩れた")
	}
	for _, w := range writeTools {
		if slices.Contains(readOnlyTools, w) {
			t.Errorf("書き込みの %s が読むだけの組にある", w)
		}
	}
}

func TestFailClosed(t *testing.T) {
	cases := []struct {
		mode Mode
		tool string
		safe bool
		want int
	}{
		{Enable, "SubagentHandback", false, ExitOK},
		{DryRun, "StructuredOutput", false, ExitOK},
		{Enable, "", false, ExitBlock},
		{DryRun, "Write", true, ExitBlock},
		{DryRun, "Agent", false, ExitBlock},
		{Enable, "Read", true, ExitOK},
		{DryRun, "Bash", false, ExitBlock},
		{Enable, "UnknownTool", false, ExitBlock},
		{DryRun, "UnknownTool", false, ExitOK},
	}
	for _, c := range cases {
		if got := FailClosed(c.mode, c.tool, c.safe); got != c.want {
			t.Errorf("FailClosed(%s, %q, %v) = %d, want %d", c.mode, c.tool, c.safe, got, c.want)
		}
	}
	if FailClosedEvent(Enable) != ExitBlock || FailClosedEvent(DryRun) != ExitOK {
		t.Error("ツールを持たないイベントの終わり方が違う")
	}
}

func TestResolveMode(t *testing.T) {
	cases := []struct {
		name              string
		flag, value       string
		fromFile, fromEnv string
		want              Mode
		said              string
	}{
		{"値が無ければ enable", "", "", "", "", Enable, ""},
		{"大文字も読む", "", "DRY-RUN", "", "", DryRun, ""},
		{"環境の disable は効く", "", "disable", "", "disable", Disable, ""},
		{"設定ファイルの disable は効かない", "", "disable", "disable", "disable", Enable,
			"ccnavi: CCNAVI_MODE=disable is ignored because it comes from inside the project; " +
				"start the session with CCNAVI_MODE=disable in the environment instead\n"},
		{"フラグの disable は効かない", "disable", "", "", "disable", Enable,
			"ccnavi: --mode=disable is ignored because it comes from inside the project; " +
				"start the session with CCNAVI_MODE=disable in the environment instead\n"},
		{"読めない値は言って enable", "", "off", "", "off", Enable,
			"ccnavi: CCNAVI_MODE='off' is not a mode; using enable. Valid modes are disable, dry-run and enable\n"},
	}
	for _, c := range cases {
		var stderr strings.Builder
		conf := &Settings{Mode: c.value, ModeDeclaredInFile: c.fromFile, ModeFromEnvironment: c.fromEnv}
		if got := ResolveMode(&stderr, c.flag, conf); got != c.want || stderr.String() != c.said {
			t.Errorf("%s: = %s %q, want %s %q", c.name, got, stderr.String(), c.want, c.said)
		}
	}
}

func TestEffectiveSetting(t *testing.T) {
	cases := []struct{ mode, declared, want Mode }{
		{Enable, Enable, Enable},
		{DryRun, Enable, DryRun},
		{Enable, Disable, Disable},
		{DryRun, Disable, Disable},
	}
	for _, c := range cases {
		if got := EffectiveSetting(c.mode, c.declared); got != c.want {
			t.Errorf("EffectiveSetting(%s, %s) = %s, want %s", c.mode, c.declared, got, c.want)
		}
	}
}

func TestScriptCommand(t *testing.T) {
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	spaced := filepath.Join(root, "My Projects", "ws")
	if err := os.MkdirAll(spaced, 0o755); err != nil {
		t.Fatal(err)
	}
	slashed := filepath.ToSlash(root)
	cases := []struct{ root, want string }{
		{root, "sh " + slashed + "/.ccnavi/scripts/ccnavi-review.sh"},
		// 末尾の区切りは重ねない。
		{root + string(filepath.Separator), "sh " + slashed + "/.ccnavi/scripts/ccnavi-review.sh"},
		// 空白を含むルートは "..." で引用する（引用しないと sh が単語に分ける）。
		{spaced, `sh "` + filepath.ToSlash(spaced) + `/.ccnavi/scripts/ccnavi-review.sh"`},
		// "..." の中でも意味を持つ文字があれば単引用符。
		{"/tmp/a $b", "sh '/tmp/a $b/.ccnavi/scripts/ccnavi-review.sh'"},
	}
	for _, c := range cases {
		if got := ScriptCommand(c.root, "ccnavi-review.sh"); got != c.want {
			t.Errorf("ScriptCommand(%q) = %q, want %q", c.root, got, c.want)
		}
	}
	// Windows の "\" は "/" にそろえる（Git Bash は C:/... を読める）。
	got := ScriptCommand(`C:\Users\me\ws`, "ccnavi-review.sh")
	if strings.Contains(got, `\`) || !strings.HasSuffix(got, "/ws/.ccnavi/scripts/ccnavi-review.sh") {
		t.Errorf("ScriptCommand(Windows のパス) = %q", got)
	}
}

func TestBinCommand(t *testing.T) {
	cases := map[string]string{
		"":                             "ccnavi",
		"/opt/ccnavi/ccnavi":           "/opt/ccnavi/ccnavi",
		`C:\ws\.ccnavi\LAUNCHER.SH`:    "sh C:/ws/.ccnavi/LAUNCHER.SH",
		"/My Tools/ccnavi-launcher.sh": `sh "/My Tools/ccnavi-launcher.sh"`,
	}
	for in, want := range cases {
		if got := BinCommand(in); got != want {
			t.Errorf("BinCommand(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestBranchPrefixes(t *testing.T) {
	cases := []struct {
		text            string
		words, rejected []string
	}{
		{"feature, spike feature", []string{"feature", "spike"}, nil},
		{"feature,release,Bad,a-b", []string{"feature"}, []string{"release", "Bad", "a-b"}},
		{"", defaultBranchPrefixes, nil},
	}
	for _, c := range cases {
		words, rejected := parseBranchPrefixes(c.text)
		if !reflect.DeepEqual(words, c.words) || !reflect.DeepEqual(rejected, c.rejected) {
			t.Errorf("parseBranchPrefixes(%q) = %q %q", c.text, words, rejected)
		}
	}
	if slices.Contains(defaultBranchPrefixes, "release") {
		t.Error("既定に release がある")
	}

	// 環境変数 → 個人の設定 → 共有の設定の順。
	ws := t.TempDir()
	t.Setenv(env.BranchPrefixes, "")
	os.Unsetenv(env.BranchPrefixes)
	write := func(rel, value string) {
		path := filepath.Join(ws, rel)
		os.MkdirAll(filepath.Dir(path), 0o755)
		os.WriteFile(path, []byte(`{"env": {"`+env.BranchPrefixes+`": "`+value+`"}}`), 0o644)
	}
	check := func(want ...string) {
		t.Helper()
		if got, _ := branchPrefixes(ws); !reflect.DeepEqual(got, want) {
			t.Errorf("branchPrefixes = %q, want %q", got, want)
		}
	}
	check(defaultBranchPrefixes...)
	write(SharedClaudeSettings, "feature,hotfix")
	check("feature", "hotfix")
	write(LocalClaudeSettings, "spike")
	check("spike")
	t.Setenv(env.BranchPrefixes, "chore")
	check("chore")
}

func TestIsReservedLayerName(t *testing.T) {
	for name, want := range map[string]bool{"builtin": true, "Builtin": true, "SELF": true, "common": true, "lib": false, "": false} {
		if got := IsReservedLayerName(name); got != want {
			t.Errorf("IsReservedLayerName(%q) = %v, want %v", name, got, want)
		}
	}
}

func TestLoadReadsLocalFileOnlyInOwnSourceTree(t *testing.T) {
	root := t.TempDir()
	os.Unsetenv(env.Mode)
	os.WriteFile(filepath.Join(root, localFile), []byte(`{"mode": "dry-run"}`), 0o644)
	if conf, _ := Load(root); conf.Mode != "" {
		t.Errorf("ccnavi のツリーでないのに上書き設定を読んだ: %q", conf.Mode)
	}
	os.WriteFile(filepath.Join(root, "pyproject.toml"), []byte("[project]\nname = \"ccnavi\"\n"), 0o644)
	conf, problems := Load(root)
	if conf.Mode != "dry-run" || conf.ModeDeclaredInFile != "dry-run" || len(problems) != 0 {
		t.Errorf("Load = %q %q %q", conf.Mode, conf.ModeDeclaredInFile, problems)
	}
	// 書き損じは報告して読み飛ばす（ガードを消さない）。
	os.WriteFile(filepath.Join(root, localFile), []byte(`{"mode": `), 0o644)
	if _, problems := Load(root); len(problems) != 1 ||
		problems[0] != "ccnavi.settings.local.json が JSON として読めないので無視した (Expecting value: line 1 column 10 (char 9))" {
		t.Errorf("problems = %q", problems)
	}
}

func TestLauncher(t *testing.T) {
	if want := "sh .ccnavi/scripts/ccnavi-launcher.sh"; Launcher != want {
		t.Errorf("Launcher = %q, want %q", Launcher, want)
	}
}

func TestIntAndFinite(t *testing.T) {
	cases := []struct {
		written         string
		fallback, least int
		want            int
	}{
		{" 5 ", 3, 2, 5}, {"1", 3, 2, 3}, {"x", 3, 2, 3}, {"", 3, 0, 3}, {"0", 3, 0, 0},
		// int に収まらない値は端に寄せてから下限と比べる（Python は巨大な値のまま使う）。
		{"99999999999999999999", 3, 2, math.MaxInt}, {"-99999999999999999999", 3, 2, 3},
		{"-99999999999999999999", 3, math.MinInt, math.MinInt},
	}
	for _, c := range cases {
		if got := Int(c.written, c.fallback, c.least); got != c.want {
			t.Errorf("Int(%q, %d, %d) = %d", c.written, c.fallback, c.least, got)
		}
	}
	for written, ok := range map[string]bool{"1.5": true, " 0 ": true, "inf": false, "nan": false, "x": false, "": false} {
		if _, got := Finite(written); got != ok {
			t.Errorf("Finite(%q) = %v", written, got)
		}
	}
}

// 診断の差し替え（--project-rules-file など）は、レイヤーの名前を大文字小文字を区別せずに引く。
func TestLayerPathSwapsIgnoreCase(t *testing.T) {
	s := &Settings{ProjectRulesFiles: map[string]string{"Alpha": "/swap/rules.yml"}}
	home := filepath.Join(string(filepath.Separator)+"ws", "projects", "alpha")
	for _, layer := range []string{"Alpha", "alpha", "ALPHA", ""} {
		if got := s.LayerPath(home, KindRules, layer); got != "/swap/rules.yml" {
			t.Errorf("LayerPath(%q) = %q", layer, got)
		}
	}
	if got := s.LayerPath(home, KindRules, "beta"); got == "/swap/rules.yml" {
		t.Errorf("別の名前に差し替えが当たった: %q", got)
	}
	if got := s.LayerPath(home, KindPhases, "alpha"); got == "/swap/rules.yml" {
		t.Errorf("別の種類に差し替えが当たった: %q", got)
	}
}
