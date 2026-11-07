package lint

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/stopreview"
	"ccnavi/internal/tickets/testkit"
)

func details(problems []rules.Problem) []string {
	var out []string
	for _, p := range problems {
		out = append(out, p.Detail)
	}
	return out
}

func anyContains(problems []rules.Problem, needle string) bool {
	for _, p := range problems {
		if strings.Contains(p.Detail, needle) {
			return true
		}
	}
	return false
}

// Stop の AI 判定の設定と agent hook の登録の食い違い（tests/core/test_stopreview_more.py の LintTest）。
func TestStopReviewProblems(t *testing.T) {
	mark := stopreview.HookMark
	allow := `["` + strings.Join(stopreview.JudgeAllowRules(), `", "`) + `"]`
	hooks := func(list, allowed string) string {
		return `{"permissions": {"allow": ` + allowed + `}, "hooks": {"Stop": [{"hooks": [` + list + `]}]}}`
	}
	ours := `{"type": "agent", "prompt": "` + mark + `判定"}`
	cases := []struct {
		name     string
		settings string // 空なら書かない
		agent    bool
		review   string
		want     []string // 空なら何も言わない
	}{
		{name: "使っていなければ黙る"},
		{name: "値の書き誤りを名指しする", review: "enabled", want: []string{"enabled"}},
		{name: "印だけで hook が無い", agent: true, want: []string{"見つからない"}},
		{name: "印と hook が揃う", agent: true, settings: hooks(ours, allow)},
		{name: "hook だけで印が無い", settings: hooks(ours, allow), want: []string{"計画が作られず"}},
		{name: "hook の重複", agent: true, settings: hooks(ours+","+ours, allow), want: []string{"2 件"}},
		{name: "ユーザ自身の agent 型 hook は数えない", settings: hooks(`{"type": "agent", "prompt": "user own"}`, allow)},
		{name: "settings.json の env の印も登録に数える",
			settings: `{"env": {"CCNAVI_STOP_REVIEW_AGENT": "1"}, "permissions": {"allow": ` + allow + `}, "hooks": {"Stop": [{"hooks": [` + ours + `]}]}}`},
		{name: "command 型の hook は数えない", agent: true, settings: hooks(`{"type": "command", "command": "x"}`, allow), want: []string{"見つからない"}},
		{name: "判定役の Bash の権限が無い", agent: true, settings: hooks(ours, "[]"), want: []string{"permissions.allow"}},
		{name: "判定役の Bash の権限が片方だけ", agent: true, settings: hooks(ours, `["`+stopreview.JudgeAllowRules()[0]+`"]`), want: []string{"permissions.allow"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			root := testkit.TempRoot(t)
			if c.settings != "" {
				testkit.Put(t, filepath.Join(root, ".claude", "settings.json"), c.settings)
			}
			got := stopReviewProblems(root, &config.Settings{StopReviewAgent: c.agent, StopReview: c.review})
			if len(c.want) == 0 && len(got) > 0 {
				t.Fatalf("言わないはずが %v", details(got))
			}
			for _, w := range c.want {
				if !anyContains(got, w) {
					t.Errorf("%q を含む苦情が無い: %v", w, details(got))
				}
			}
		})
	}
}

// 壊れた形の設定でも診断は落ちない。
func TestStopReviewMalformedSettings(t *testing.T) {
	for _, data := range []string{`{"hooks": "x"}`, `{"hooks": {"Stop": ["x"]}}`, `{"hooks": [1]}`,
		`{"hooks": {"Stop": [{"hooks": "abc"}]}}`, `{"env": "x"}`, `[1]`, `{`} {
		root := testkit.TempRoot(t)
		testkit.Put(t, filepath.Join(root, ".claude", "settings.json"), data)
		stopReviewProblems(root, &config.Settings{StopReviewAgent: true})
	}
}

// 案内のコマンドに載せるパスの綴り。導入スクリプトの sh_word と同じ綴りにする（TestShWordAgreesWithTheSetupSh が比べる）。
func TestShWord(t *testing.T) {
	for path, want := range map[string]string{
		"projects/lib":        "projects/lib",
		"projects/my-lib_2.x": "projects/my-lib_2.x",
		"projects/日本語":        "projects/日本語",
		"projects/my lib":     "'projects/my lib'",
		"projects/a$b":        "'projects/a$b'",
		"projects/it's":       `'projects/it'\''s'`,
	} {
		if got := shWord(path); got != want {
			t.Errorf("shWord(%q) = %q, want %q", path, got, want)
		}
	}
}

// 導入スクリプトの sh_word と shWord は、どの字でも同じ表記を返す。2 か所に書いた字の集合のずれを捕まえるため、
// scripts/ccnavi-setup.sh から関数を切り出して sh で走らせ、shSpecial の各字と割れない字の両方で比べる。
// 改行は比べない（sh_word が受けるのは `git ls-files` の 1 行で、改行を含むパスは git が引用して渡す）。
func TestShWordAgreesWithTheSetupSh(t *testing.T) {
	sh, err := exec.LookPath("sh")
	if err != nil {
		t.Skip("sh が無い")
	}
	data, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "scripts", "ccnavi-setup.sh"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(data)
	start := strings.Index(text, "\nsh_word() {\n")
	end := strings.Index(text[start+1:], "\n}\n")
	if start < 0 || end < 0 {
		t.Fatal("ccnavi-setup.sh に sh_word が見つからない")
	}
	function := text[start+1 : start+1+end+2]
	var chars []string
	for _, c := range shSpecial {
		if c != '\n' {
			chars = append(chars, string(c))
		}
	}
	chars = append(chars, "-", ".", "_", "/", "=", "%", "@", ":", ",", "+", "^", "日")
	for _, c := range chars {
		word := "projects/a" + c + "b"
		out, err := exec.Command(sh, "-c", "tab=$(printf '\\t'); "+function+"\nsh_word \"$1\"", "sh", word).Output()
		if err != nil {
			t.Fatalf("%q: %v", word, err)
		}
		if got, want := string(out), shWord(word); got != want {
			t.Errorf("%q: sh_word = %q, shWord = %q", word, got, want)
		}
	}
}

// 直接作業の記録（突き合わせの見本に無い 2 つ）。
func TestDirectRecordsQuietAndSideBySide(t *testing.T) {
	root := testkit.Workspace(t, "w1")
	common := filepath.Join(root, ".git")
	record := func(sideBySide string) {
		testkit.Put(t, filepath.Join(common, "ccnavi-direct", "w1"), "version 4\nbase_branch main\nbase_sha x\nsnap_sha x\n"+
			"side_by_side "+sideBySide+"\nmain_tree "+root+"\ncommon_dir "+common+"\n")
	}
	conf := &config.Settings{}
	about := func() []rules.Problem {
		var out []rules.Problem
		for _, p := range directProblems(conf, root) {
			if strings.Contains(p.Rule, "/w1)") {
				out = append(out, p)
			}
		}
		return out
	}
	record("no")
	if got := about(); len(got) > 0 {
		t.Fatalf("記録のあるワークツリーは言わない: %v", details(got))
	}
	record("yes")
	if got := about(); len(got) != 1 || got[0].Severity != rules.Info || !strings.Contains(got[0].Detail, "横で作業する指定が有効") {
		t.Fatalf("横で作業する指定は info 1 件: %v", details(got))
	}
}

// レイヤーのずれ（tests/core/test_lint_layer_drift.py）。手元のレイヤーを、取得済みの origin/<統合先> と比べる。
const (
	commonLayer = ".ccnavi/config/common/rules.yml"
	selfLayer   = ".ccnavi/config/project/rules.yml"
)

// driftRepo は git のリポジトリと、そこから push した origin（origin/<branch> が手元に取得済みになる）。
func driftRepo(t *testing.T, path string) {
	t.Helper()
	if err := os.MkdirAll(path, 0o755); err != nil {
		t.Fatal(err)
	}
	testkit.Git(t, path, "init", "-q", "-b", "main")
	testkit.Put(t, filepath.Join(path, commonLayer), "version: 1\nrules: []\n")
	testkit.Put(t, filepath.Join(path, selfLayer), "version: 1\nrules: []\n")
	testkit.Commit(t, path, "init")
	bare := path + ".origin.git"
	testkit.Git(t, filepath.Dir(path), "init", "-q", "--bare", bare)
	testkit.Git(t, path, "remote", "add", "origin", bare)
	testkit.Git(t, path, "push", "-q", "origin", "main")
}

func driftRoot(t *testing.T) string {
	t.Helper()
	root := filepath.Join(testkit.GitRoot(t), "ws")
	driftRepo(t, root)
	return root
}

func layerConf(t *testing.T, root string) *config.Settings {
	t.Helper()
	conf, _ := config.Load(root)
	return conf
}

func TestLayerDrift(t *testing.T) {
	write := func(t *testing.T, root, rel, text string) {
		testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), text)
	}
	t.Run("同じなら黙る", func(t *testing.T) {
		root := driftRoot(t)
		if got := layerDrift(layerConf(t, root), root, ""); len(got) > 0 {
			t.Fatal(details(got))
		}
	})
	t.Run("違えばパスと統合先を名指しする warn", func(t *testing.T) {
		root := driftRoot(t)
		write(t, root, selfLayer, "version: 1\nrules: [x]\n")
		got := layerDrift(layerConf(t, root), root, "")
		if len(got) != 1 || got[0].Severity != rules.Warn || got[0].Rule != "(self)" {
			t.Fatal(got)
		}
		for _, w := range []string{selfLayer, "origin/main", "中身が違う", "手元の origin/main が古いだけなら、先に取得（fetch）してから見直す"} {
			if !strings.Contains(got[0].Detail, w) {
				t.Errorf("%q が無い: %s", w, got[0].Detail)
			}
		}
	})
	t.Run("ワークスペース自身は共通レイヤーも比べる", func(t *testing.T) {
		root := driftRoot(t)
		write(t, root, commonLayer, "version: 1\nrules: [x]\n")
		if got := layerDrift(layerConf(t, root), root, ""); len(got) != 1 || !strings.Contains(got[0].Detail, commonLayer) {
			t.Fatal(details(got))
		}
	})
	t.Run("片方にしか無いことをどちらか言う", func(t *testing.T) {
		root := driftRoot(t)
		if err := os.Remove(filepath.Join(root, filepath.FromSlash(selfLayer))); err != nil {
			t.Fatal(err)
		}
		if got := layerDrift(layerConf(t, root), root, ""); len(got) != 1 || !strings.Contains(got[0].Detail, "ワークツリーにありません") {
			t.Fatal(details(got))
		}
		write(t, root, selfLayer, "version: 1\n")
		testkit.Git(t, root, "rm", "-q", "--cached", selfLayer)
		testkit.Git(t, root, "commit", "-q", "-m", "drop")
		testkit.Git(t, root, "push", "-q", "origin", "main")
		if got := layerDrift(layerConf(t, root), root, ""); len(got) != 1 || !strings.Contains(got[0].Detail, "統合先にありません") {
			t.Fatal(details(got))
		}
	})
	t.Run("改行の違いだけなら違いとしない", func(t *testing.T) {
		root := driftRoot(t)
		write(t, root, selfLayer, "version: 1\r\nrules: []\r\n")
		if got := layerDrift(layerConf(t, root), root, ""); len(got) > 0 {
			t.Fatal(details(got))
		}
	})
	t.Run("統合先は origin/HEAD に従う", func(t *testing.T) {
		root := driftRoot(t)
		testkit.Git(t, root, "branch", "develop")
		write(t, root, selfLayer, "version: 1\nrules: [dev]\n")
		testkit.Git(t, root, "checkout", "-q", "develop")
		testkit.Git(t, root, "commit", "-qam", "dev")
		testkit.Git(t, root, "push", "-q", "origin", "develop")
		testkit.Git(t, root, "checkout", "-q", "main")
		write(t, root, selfLayer, "version: 1\nrules: [dev]\n")
		if got := layerDrift(layerConf(t, root), root, ""); len(got) != 1 || !strings.Contains(got[0].Detail, "origin/main") {
			t.Fatal(details(got))
		}
		testkit.Git(t, root, "remote", "set-head", "origin", "develop")
		if got := layerDrift(layerConf(t, root), root, ""); len(got) > 0 {
			t.Fatal(details(got))
		}
	})
	t.Run("origin/<統合先> が無ければ黙る", func(t *testing.T) {
		root := driftRoot(t)
		testkit.Git(t, root, "update-ref", "-d", "refs/remotes/origin/main")
		write(t, root, selfLayer, "version: 1\nrules: [x]\n")
		if got := layerDrift(layerConf(t, root), root, ""); len(got) > 0 {
			t.Fatal(details(got))
		}
	})
	t.Run("プロジェクトは自分の origin と比べ、共通レイヤーは比べない", func(t *testing.T) {
		root := driftRoot(t)
		app := filepath.Join(root, "projects", "app")
		driftRepo(t, app)
		conf := layerConf(t, root)
		write(t, app, selfLayer, "version: 1\nrules: [x]\n")
		if got := layerDrift(conf, root, ""); len(got) > 0 {
			t.Fatal(details(got))
		}
		if got := layerDrift(conf, root, "app"); len(got) != 1 || got[0].Rule != "(projects/app)" {
			t.Fatal(got)
		}
		write(t, app, commonLayer, "version: 1\nrules: [x]\n")
		if got := layerDrift(conf, root, "app"); len(got) != 1 {
			t.Fatal(details(got))
		}
	})
	t.Run("--lint は比べられなかったリポジトリを info 1 行で言う", func(t *testing.T) {
		root := driftRoot(t)
		driftRepo(t, filepath.Join(root, "projects", "app"))
		testkit.Git(t, root, "update-ref", "-d", "refs/remotes/origin/main")
		var infos []rules.Problem
		for _, p := range syncProblems(layerConf(t, root), root) {
			if p.Severity == rules.Info {
				infos = append(infos, p)
			}
		}
		if len(infos) != 1 || !strings.Contains(infos[0].Detail, "self") || strings.Contains(infos[0].Detail, "app") {
			t.Fatal(details(infos))
		}
	})
	t.Run("origin を持たないリポジトリには取得を勧めない", func(t *testing.T) {
		root := driftRoot(t)
		testkit.Git(t, root, "remote", "remove", "origin")
		if got := syncProblems(layerConf(t, root), root); len(got) > 0 {
			t.Fatal(details(got))
		}
	})
}

// プロジェクトの置き場がワークスペースの中かは、大文字小文字を畳んで見る。".." で始まる名前の置き場は中。
func TestProjectsRelFoldsCase(t *testing.T) {
	root := filepath.Join(string(filepath.Separator)+"ws", "Root")
	for projects, want := range map[string]string{
		filepath.Join(root, "projects"):                                  "projects",
		filepath.Join(string(filepath.Separator)+"WS", "root", "p", "q"): "p/q",
		filepath.Join(root, "..projects"):                                "..projects",
		root:                                                             "",
		filepath.Join(root, "..", "x"):                                   "",
		"":                                                               "",
	} {
		if got := projectsRel(&config.Settings{Projects: projects}, root); got != want {
			t.Errorf("%q: %q", projects, got)
		}
	}
}

// 置き場の苦情は、`--lint --json` の kind で見分けられる（VS Code 拡張は文面で比べない）。
func TestProjectsDirProblemsCarryKind(t *testing.T) {
	root := testkit.Workspace(t)
	dir := filepath.Join(root, "projects", "app")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	testkit.Git(t, dir, "init", "-q", "-b", "main")
	kinds := func() []string {
		conf, _ := config.Load(root)
		var out bytes.Buffer
		Report(&out, Options{Root: root, Conf: conf, JSON: true})
		var body struct {
			Problems []struct{ Kind string } `json:"problems"`
		}
		if err := json.Unmarshal(out.Bytes(), &body); err != nil {
			t.Fatal(err, out.String())
		}
		var got []string
		for _, p := range body.Problems {
			if p.Kind != "" {
				got = append(got, p.Kind)
			}
		}
		return got
	}
	if got := kinds(); strings.Join(got, ",") != kindProjectsUnignored {
		t.Fatalf("無視されていない置き場の kind: %v", got)
	}
	testkit.Put(t, filepath.Join(root, "projects", "README.md"), "x\n")
	testkit.Git(t, root, "add", "projects/README.md")
	if got := kinds(); strings.Join(got, ",") != kindProjectsTracked {
		t.Fatalf("索引に載った置き場の kind: %v", got)
	}
}

// 承認済みの置き場が ccnavi ディレクトリの下かは、大文字小文字を畳んで見る（綴り違いを外と言わない）。
func TestApprovedGuardedFoldsCase(t *testing.T) {
	for approved, want := range map[string]int{".CCNAVI/approved": 0, ".ccnavi\\Approved": 0, "wip/approved": 1} {
		if got := approvedGuarded(&config.Settings{ProjectHome: ".ccnavi", Approved: approved}); len(got) != want {
			t.Errorf("%q: %v", approved, details(got))
		}
	}
}

// 登録の無いワークツリーの見分けは、ワークツリーの名前の大文字小文字を畳んで見る。
func TestUnregisteredWorktreesFoldsCase(t *testing.T) {
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q")
	if err := os.MkdirAll(filepath.Join(root, ".claude", "worktrees", "fix-a"), 0o755); err != nil {
		t.Fatal(err)
	}
	if got := unregisteredWorktrees(root, []tree.Tree{{Name: "Fix-A"}}); len(got) != 0 {
		t.Errorf("綴り違いの名前のワークツリーを未登録と言った: %v", details(got))
	}
	if got := unregisteredWorktrees(root, nil); len(got) != 1 {
		t.Errorf("未登録のディレクトリを言わない: %v", details(got))
	}
}
