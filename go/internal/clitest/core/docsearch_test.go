package core

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/run"
)

// md の frontmatter の索引と `ccnavi --docs` の受入テスト。実行ファイルを起こして出力だけを見る。
//
//  1. CLI: `--docs` の外の絞り込みは理由を言って失敗にする。誤った値は 1、0 件は 0。どこから打ってもワークスペースを引く。
//  2. プロジェクト: 置き場の直下の各 git も引く。パスはワークスペースルートから。ccnavi-index.jsonl を無視していなければ対象外
//  3. SessionStart: 索引を新しくして案内をつける。対象外は名指し。サブエージェントと git の外では案内を出さない
//  4. PostToolUse: frontmatter の無い md を Write で作ったら、判定を変えずに案内を渡す
//
// 索引の作り方・差分・frontmatter の読み方・引き方・出力の形・敵対的な入力と git の失敗は go/internal/catalog の go test が見る。

const (
	conventionDoc           = ".ccnavi/docs/frontmatter.md"
	unignored               = "は .gitignore に `**/ccnavi-index.jsonl` が無く、足せなかったので索引の対象外"
	ignoreAdded             = "に `**/ccnavi-index.jsonl` を追加しました（未コミットの差分が残る）"
	codeMissingFrontmatter  = "NOTICE_DOC_FRONTMATTER"
	ignoreLine              = "**/ccnavi-index.jsonl\n"
	launcherEntryPathForDoc = ".ccnavi/scripts/ccnavi-launcher.sh"
)

// 2026-08-05 12:00:00 のローカル時刻。mtime の文字列はローカル時刻で書くので、そこから作る。
var noon = time.Date(2026, 8, 5, 12, 0, 0, 0, time.Local)

func doc(front ...string) string { // doc("type", "guide", "title", "甲")
	lines := []string{"---"}
	for i := 0; i+1 < len(front); i += 2 {
		lines = append(lines, front[i]+": "+front[i+1])
	}
	lines = append(lines, "---")
	return strings.Join(lines, "\n") + "\n\n# 本文\n\nbody text\n"
}

func readFile(t testing.TB, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// docsRepo は git の木（.gitignore に索引の無視を書いたもの）。
type docsRepo struct {
	t    *testing.T
	root string
}

func newDocsRepo(t *testing.T) *docsRepo {
	t.Helper()
	root := emptyRoot(t)
	run.Git(t, root, "init", "-q")
	writeFile(t, root, ".gitignore", ignoreLine)
	return &docsRepo{t, root}
}

func (r *docsRepo) put(rel, text string) string {
	r.t.Helper()
	path := writeFile(r.t, r.root, rel, text)
	if err := os.Chtimes(path, noon, noon); err != nil {
		r.t.Fatal(err)
	}
	return path
}

func (r *docsRepo) indexOf(relDir string) string {
	return filepath.Join(r.root, filepath.FromSlash(relDir), "ccnavi-index.jsonl")
}

// ccnavi は --root を r.root にして起こす。cwd が空なら r.root。
func (r *docsRepo) ccnavi(cwd string, args ...string) run.Result {
	r.t.Helper()
	if cwd == "" {
		cwd = r.root
	}
	return run.Ccnavi(r.t, append([]string{"--root", r.root}, args...), run.Opts{Dir: cwd})
}

// context は SessionStart の additionalContext。
func (r *docsRepo) context(root string, extra map[string]any) string {
	r.t.Helper()
	payload := map[string]any{"hook_event_name": "SessionStart", "session_id": "s1", "cwd": root}
	for k, v := range extra {
		payload[k] = v
	}
	done := run.Ccnavi(r.t, []string{"--root", root, "--mode", "enable"}, run.Opts{
		Input: mustJSON(r.t, payload), Dir: root,
		Env: map[string]string{"CCNAVI_ENTRY_PATH": launcherEntryPathForDoc},
	})
	if done.Code != 0 {
		r.t.Fatalf("code = %d: %s", done.Code, done.Stderr)
	}
	if strings.TrimSpace(done.Stdout) == "" {
		return ""
	}
	var out struct {
		HookSpecificOutput struct{ AdditionalContext string }
	}
	if err := json.Unmarshal([]byte(done.Stdout), &out); err != nil {
		r.t.Fatal(err)
	}
	return out.HookSpecificOutput.AdditionalContext
}

func TestDocsCLISearchesAndWritesTheIndex(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	r.put("docs/a.md", doc("type", "guide", "title", "甲"))
	r.put("b.md", doc("type", "adr"))
	done := r.ccnavi("", "--docs", "--type", "GUIDE")
	if done.Code != 0 || done.Stdout != "guide  docs/a  甲\n" || !strings.Contains(done.Stderr, "matched=1 total=2") {
		t.Errorf("code = %d, stdout = %q, stderr = %q", done.Code, done.Stdout, done.Stderr)
	}
	if !exists(r.indexOf("docs")) {
		t.Error("docs の索引が書かれていない")
	}
}

func TestDocsCLIOutputsAndScopes(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	r.put("a.md", doc("type", "guide"))
	t.Run("--json は --format json", func(t *testing.T) {
		done := r.ccnavi("", "--docs", "--json")
		var rows []struct {
			Concept_id string
		}
		if err := json.Unmarshal([]byte(done.Stdout), &rows); done.Code != 0 || err != nil || rows[0].Concept_id != "a" {
			t.Errorf("code = %d, err = %v, stdout = %q, stderr = %q", done.Code, err, done.Stdout, done.Stderr)
		}
	})
	t.Run("どこから打ってもワークスペースを引く", func(t *testing.T) {
		done := r.ccnavi(emptyRoot(t), "--docs", "--format", "path")
		if done.Code != 0 || done.Stdout != "a\n" {
			t.Errorf("code = %d, stdout = %q, stderr = %q", done.Code, done.Stdout, done.Stderr)
		}
	})
	t.Run("0 件でも 0", func(t *testing.T) {
		done := r.ccnavi("", "--docs", "--type", "nothing", "--format", "count")
		if done.Code != 0 || done.Stdout != "matched=0 total=1\n" {
			t.Errorf("code = %d, stdout = %q", done.Code, done.Stdout)
		}
	})
	t.Run("git の外のワークスペースは何も無く 0", func(t *testing.T) {
		bare := emptyRoot(t)
		writeFile(t, bare, "a.md", doc("type", "guide"))
		done := run.Ccnavi(t, []string{"--root", bare, "--docs", "--format", "count"})
		if done.Code != 0 || done.Stdout != "matched=0 total=0\n" {
			t.Errorf("code = %d, stdout = %q", done.Code, done.Stdout)
		}
	})
	t.Run("--root と log・state は受ける", func(t *testing.T) {
		done := r.ccnavi("", "--docs", "--format", "path")
		if done.Code != 0 || done.Stdout != "a\n" {
			t.Errorf("code = %d, stdout = %q, stderr = %q", done.Code, done.Stdout, done.Stderr)
		}
	})
}

// 誤った値は理由を言って 1 で止める。
func TestDocsCLIBadValuesAreOne(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	r.put("a.md", doc("type", "guide"))
	for _, args := range [][]string{
		{"--sort", "size"}, {"--format", "xml"}, {"--until", "tomorrow"}, {"--json", "--format", "table"}, {"--lint"}, {"stray"},
		// 存在しない日付
		{"--since", "2026-13-45"}, {"--since", "2026-02-30"}, {"--since", "2026-08-05T25"}, {"--since", "2026-08-05T10:61"},
	} {
		done := r.ccnavi("", append([]string{"--docs"}, args...)...)
		if done.Code != 1 || !strings.Contains(done.Stderr, "ccnavi:") {
			t.Errorf("%v: code = %d, stdout = %q, stderr = %q", args, done.Code, done.Stdout, done.Stderr)
		}
	}
	if done := r.ccnavi("", "--docs", "--until", "2026-08-05T10:59"); done.Code != 0 {
		t.Errorf("正しい日時が断られた: %s", done.Stderr)
	}
}

// --docs の外の絞り込みは理由を言って失敗にする。別の副命令に --limit を付ける打ち間違いも進めない。
func TestDocsFiltersOutsideDocsStopTheRun(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	done := r.ccnavi("", "--lint", "--type", "adr", "--limit", "3")
	if done.Code != 1 {
		t.Errorf("code = %d", done.Code)
	}
	mustContain(t, "stderr", done.Stderr, "ccnavi: --type は --docs でだけ指定できる", "ccnavi: --limit は --docs でだけ指定できる")
	done = r.ccnavi("", "ticket", "start", "i0001", "--limit", "3")
	if done.Code != 1 {
		t.Errorf("code = %d", done.Code)
	}
	mustContain(t, "stderr", done.Stderr, "--limit は --docs でだけ指定できる")
	mustNotContain(t, "stdout", done.Stdout, "チケット")
}

// 他の実行の旗・設定や sh のパスを差し替える旗は、何も言わずに無視せず --docs を止める。
// 止める文は「診断でだけ効く」の設定の文で先へ進まない。
func TestDocsIsStoppedByTheFlagsOfOtherRuns(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	r.put("a.md", doc("type", "guide"))
	for _, args := range [][]string{
		{"--preview"}, {"--verify"}, {"--result", "{}"}, {"--tickets", "wip"}, {"--reason", "x"}, {"--flow", "f.yml"},
		{"--cwd", r.root}, {"--approved", "x"}, {"--approved", ""}, {"--mode", "enable"}, {"--projects", "elsewhere"}, {"--projects", ""},
		{"--project-home", ".other"}, {"--rules", "r.yml"}, {"--phases", "p.yml"}, {"--risk", "k.yml"}, {"--ticket-control", "disable"},
		{"--guard-core-files", "disable"}, {"--guard-ticket-approval", "enable"}, {"--restore-if-deny", "x"}, {"--choose-out", "x"},
	} {
		done := r.ccnavi("", append([]string{"--docs"}, args...)...)
		if done.Code != 1 || !strings.Contains(done.Stderr, "--docs は "+args[0]+" と同時に指定できない") || done.Stdout != "" {
			t.Errorf("%v: code = %d, stdout = %q, stderr = %q", args, done.Code, done.Stdout, done.Stderr)
		}
		mustNotContain(t, fmt.Sprint(args), done.Stderr, "診断")
	}
}

// `user` の操作にも `--docs` は添えられない。`user` の外の `--yes` は先に断る。
func TestDocsDoesNotGoWithUserOperations(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	r.put("a.md", doc("type", "guide"))
	for _, c := range []struct {
		args []string
		flag string
	}{
		{[]string{"user", "reviewed", "0", "--docs"}, "--reviewed"},
		{[]string{"user", "agree", "--yes", "i0001", "--docs"}, "--agree"},
		{[]string{"user", "prune", "--docs"}, "--prune"},
	} {
		done := r.ccnavi("", c.args...)
		if done.Code != 1 || !strings.Contains(done.Stderr, "--docs は "+c.flag+" と同時に指定できない") {
			t.Errorf("%v: code = %d, stderr = %q", c.args, done.Code, done.Stderr)
		}
	}
	refused := r.ccnavi("", "--docs", "--yes", "i0001")
	if refused.Code != 2 || !strings.Contains(refused.Stderr, "ユーザ専用") {
		t.Errorf("code = %d, stderr = %q", refused.Code, refused.Stderr)
	}
}

// プロジェクトの置き場（projects/）の直下の各 git も索引に入れる。
type projectsRepo struct {
	*docsRepo
	lib, bare string
}

func newProjectsRepo(t *testing.T) *projectsRepo {
	r := newDocsRepo(t)
	writeFile(t, r.root, ".gitignore", ignoreLine+"/projects/\n")
	p := &projectsRepo{docsRepo: r}
	p.lib = p.project("lib", ignoreLine)
	// .gitignore に足せない（ふつうのファイルでない）プロジェクトは、対象外のまま。
	p.bare = p.project("bare", "")
	r.put("docs/w.md", doc("type", "guide", "title", "ワークスペース"))
	r.put("projects/lib/docs/x.md", doc("type", "adr", "title", "lib の決定"))
	r.put("projects/bare/y.md", doc("type", "guide"))
	return p
}

// project は projects/<name> に git を作る。ignore が空なら .gitignore をディレクトリにして、無視の行を足せなくする。
func (r *docsRepo) project(name, ignore string) string {
	home := filepath.Join(r.root, "projects", name)
	if err := os.MkdirAll(home, 0o755); err != nil {
		r.t.Fatal(err)
	}
	run.Git(r.t, home, "init", "-q")
	if ignore == "" {
		if err := os.MkdirAll(filepath.Join(home, ".gitignore"), 0o755); err != nil {
			r.t.Fatal(err)
		}
	} else {
		writeFile(r.t, home, ".gitignore", ignore)
	}
	return home
}

func TestDocsProjectsAreSearchedWithPathsFromTheWorkspaceRoot(t *testing.T) {
	t.Parallel()
	p := newProjectsRepo(t)
	done := p.ccnavi("", "--docs", "--format", "jsonl")
	if done.Code != 0 {
		t.Fatalf("code = %d: %s", done.Code, done.Stderr)
	}
	var got []string
	for _, line := range strings.Split(strings.TrimSpace(done.Stdout), "\n") {
		var row struct{ Concept_id, Directory string }
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatal(err)
		}
		got = append(got, row.Concept_id+"|"+row.Directory)
	}
	sort.Strings(got)
	if strings.Join(got, ",") != "docs/w|docs,projects/lib/docs/x|projects/lib/docs" {
		t.Errorf("rows = %v", got)
	}
	// 書く行はそのプロジェクトのルートからの相対。
	first, _, _ := strings.Cut(readFile(t, filepath.Join(p.lib, "docs", "ccnavi-index.jsonl")), "\n")
	var row struct{ Concept_id string }
	if err := json.Unmarshal([]byte(first), &row); err != nil || row.Concept_id != "docs/x" {
		t.Errorf("プロジェクトの索引の行 = %q (%v)", first, err)
	}
}

func TestDocsPathNarrowsToOneProject(t *testing.T) {
	t.Parallel()
	p := newProjectsRepo(t)
	if done := p.ccnavi("", "--docs", "--path", "projects/lib", "--format", "path"); done.Stdout != "projects/lib/docs/x\n" {
		t.Errorf("stdout = %q", done.Stdout)
	}
}

func TestDocsAProjectThatIgnoresNoIndexIsLeftOutAndNamed(t *testing.T) {
	t.Parallel()
	p := newProjectsRepo(t)
	done := p.ccnavi("", "--docs", "--format", "count")
	if done.Stdout != "matched=2 total=2\n" {
		t.Errorf("stdout = %q", done.Stdout)
	}
	mustContain(t, "stderr", done.Stderr, "bare "+unignored)
	if exists(filepath.Join(p.bare, "ccnavi-index.jsonl")) {
		t.Error("対象外のプロジェクトに索引が書かれた")
	}
	if info, err := os.Stat(filepath.Join(p.bare, ".gitignore")); err != nil || !info.IsDir() {
		t.Error(".gitignore が壊された")
	}
}

func TestDocsAProjectWithoutTheIgnoreLineGetsItAndIsIndexed(t *testing.T) {
	t.Parallel()
	p := newProjectsRepo(t)
	plain := p.project("plain", "node_modules/\n")
	p.put("projects/plain/z.md", doc("type", "guide", "title", "plain の文書"))
	done := p.ccnavi("", "--docs", "--format", "path")
	if done.Code != 0 {
		t.Fatalf("code = %d: %s", done.Code, done.Stderr)
	}
	found := false
	for _, line := range strings.Split(done.Stdout, "\n") {
		found = found || line == "projects/plain/z"
	}
	if !found {
		t.Errorf("plain の文書が引けない: %q", done.Stdout)
	}
	mustContain(t, "stderr", done.Stderr, "projects/plain/.gitignore に")
	if got := readFile(t, filepath.Join(plain, ".gitignore")); got != "node_modules/\n"+ignoreLine {
		t.Errorf(".gitignore = %q", got)
	}
	if !exists(filepath.Join(plain, "ccnavi-index.jsonl")) {
		t.Error("plain の索引が書かれていない")
	}
	again := p.ccnavi("", "--docs", "--format", "path") // 2 回目は足さない。
	mustNotContain(t, "2 回目の stderr", again.Stderr, "を追加した")
	if got := readFile(t, filepath.Join(plain, ".gitignore")); got != "node_modules/\n"+ignoreLine {
		t.Errorf("2 回目で .gitignore が変わった: %q", got)
	}
}

func TestDocsSessionStartRefreshesTheIndexAndTellsHowToSearch(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	r.put("docs/a.md", doc("type", "guide"))
	said := r.context(r.root, nil)
	if !exists(r.indexOf("docs")) {
		t.Error("索引を新しくしていない")
	}
	mustContain(t, "案内", said, "sh "+filepath.ToSlash(r.root)+"/.ccnavi/scripts/ccnavi-launcher.sh --docs", "grep・Glob より先に")
	mustNotContain(t, "案内", said, "--keyword frontmatter", unignored)
}

func TestDocsSessionStartPointsToTheConventionDocumentWhenPresent(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	r.put("docs/a.md", doc("type", "guide"))
	r.put(conventionDoc, doc("type", "guide"))
	mustContain(t, "案内", r.context(r.root, nil), "--docs --keyword frontmatter")
}

func TestDocsSessionStartNamesTheWorkspaceThatGotTheLine(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	writeFile(t, r.root, ".gitignore", "")
	r.put("docs/a.md", doc("type", "guide"))
	mustContain(t, "案内", r.context(r.root, nil), ".gitignore "+ignoreAdded, "grep・Glob より先に")
}

// サブエージェント・md が無い木・git の外では案内を出さない。
func TestDocsSessionStartIsSilentForSubagentWithoutMdAndOutsideGit(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	r.put("docs/a.md", doc("type", "guide"))
	mustNotContain(t, "サブエージェントの案内", r.context(r.root, map[string]any{"agent_id": "a1"}), "--docs")
	if exists(r.indexOf("docs")) {
		t.Error("サブエージェントが索引を書いた")
	}
	if err := os.Remove(filepath.Join(r.root, "docs", "a.md")); err != nil {
		t.Fatal(err)
	}
	mustNotContain(t, "md が無い木の案内", r.context(r.root, nil), "--docs")
	bare := emptyRoot(t)
	writeFile(t, bare, "a.md", doc("type", "guide"))
	mustNotContain(t, "git の外の案内", r.context(bare, nil), "--docs")
}

// Write で新しく作った md に frontmatter か type が無ければ、PostToolUse で 1 度だけ案内する（判定は変えない）。
// 既にある md の Edit・上書きでは出さない。
func TestDocsPostToolUseDeliversTheFrontmatterHintWithoutChangingTheDecision(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	path := r.put("docs/new.md", "# 見出し\n")
	payload := map[string]any{
		"hook_event_name": "PostToolUse", "session_id": "s1", "cwd": r.root, "tool_name": "Write",
		"tool_input":    map[string]any{"file_path": path, "content": "# 見出し\n"},
		"tool_response": map[string]any{"type": "create", "filePath": path},
	}
	done := run.Ccnavi(t, []string{"--root", r.root, "--mode", "enable"}, run.Opts{Input: mustJSON(t, payload), Dir: r.root})
	if done.Code != 0 {
		t.Fatalf("code = %d: %s", done.Code, done.Stderr)
	}
	var out struct {
		HookSpecificOutput struct{ AdditionalContext string }
	}
	if err := json.Unmarshal([]byte(done.Stdout), &out); err != nil {
		t.Fatal(err)
	}
	mustContain(t, "案内", out.HookSpecificOutput.AdditionalContext, codeMissingFrontmatter)
	mustNotContain(t, "stdout", done.Stdout, "permissionDecision")
}

// ccnavi の形でない ccnavi-index.jsonl は上書きも削除もしない。CLI と SessionStart が名指しする。
func TestDocsAForeignIndexIsNamedAndKept(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	r.put("a.md", doc("type", "guide"))
	writeFile(t, r.root, "ccnavi-index.jsonl", "x\n")
	done := r.ccnavi("", "--docs", "--format", "count")
	mustContain(t, "stderr", done.Stderr, "ccnavi-index.jsonl は ccnavi の索引ではないので書き換えない")
	mustContain(t, "案内", r.context(r.root, nil), "書き換えなかった: ccnavi-index.jsonl")
}

// 字下げの JSON に書けない深さの行は ccnavi の行とみなさない。md は読み直し、異常終了しない。
func TestDocsOddRowsDoNotCrashTheOutputs(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	r.put("a.md", doc("type", "guide"))
	deep := `"deep"`
	for i := 0; i < 500; i++ {
		deep = "[" + deep + "]"
	}
	writeFile(t, r.root, "ccnavi-index.jsonl", `{"concept_id":"a","directory":".","frontmatter":{"n":`+deep+`},"mtime":"x"}`+"\n")
	for format, want := range map[string]string{"table": "guide", "json": `"a"`} {
		done := r.ccnavi("", "--docs", "--no-refresh", "--format", format)
		if done.Code != 0 || !strings.Contains(done.Stderr, "ccnavi の索引ではない") || !strings.Contains(done.Stdout, want) {
			t.Errorf("%s: code = %d, stdout = %q, stderr = %q", format, done.Code, done.Stdout, done.Stderr)
		}
	}
}

// 同じ concept_id は 1 本に。`.git` の無いプロジェクトは CLI で名指しする（SessionStart では言わない）。
func TestDocsAProjectWithoutGitIsNamedByTheCLI(t *testing.T) {
	t.Parallel()
	r := newDocsRepo(t)
	r.put("a.md", doc("type", "guide"))
	r.put("projects/plain/x.md", doc("type", "guide"))
	done := run.Ccnavi(t, []string{"--root", r.root, "--docs"})
	mustContain(t, "stderr", done.Stderr, "プロジェクト plain は .git を持たないので引かない")
	mustNotContain(t, "SessionStart の案内", r.context(r.root, nil), "plain")
}

// どの出力の形でも異常終了せず、正しい JSON を出す。深い frontmatter は null、NaN・Infinity を含む行は ccnavi の行とみなさない。
func TestDocsJSONOutputSurvivesDeepFrontmatterAndNonFiniteRows(t *testing.T) {
	t.Parallel()
	t.Run("深い frontmatter は null", func(t *testing.T) {
		r := newDocsRepo(t)
		r.put("a.md", "---\ntype: guide\nn: "+strings.Repeat("[", 40)+strings.Repeat("]", 40)+"\n---\n")
		done := run.Ccnavi(t, []string{"--root", r.root, "--docs", "--format", "json"})
		var rows []struct{ Frontmatter any }
		if err := json.Unmarshal([]byte(done.Stdout), &rows); done.Code != 0 || err != nil || rows[0].Frontmatter != nil {
			t.Errorf("code = %d, err = %v, stdout = %q, stderr = %q", done.Code, err, done.Stdout, done.Stderr)
		}
	})
	t.Run("NaN と Infinity の行", func(t *testing.T) {
		r := newDocsRepo(t)
		r.put("a.md", doc("type", "guide"))
		for _, bad := range []string{"NaN", "Infinity", "-Infinity"} {
			writeFile(t, r.root, "ccnavi-index.jsonl", `{"concept_id":"a","directory":".","frontmatter":{"n":`+bad+`},"mtime":"x"}`+"\n")
			done := run.Ccnavi(t, []string{"--root", r.root, "--docs", "--no-refresh", "--format", "json"})
			if done.Code != 0 {
				t.Errorf("%s: code = %d: %s", bad, done.Code, done.Stderr)
			}
		}
	})
}
