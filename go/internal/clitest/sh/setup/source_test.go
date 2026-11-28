package setup

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// 導入スクリプト（scripts/ccnavi-setup.sh）の受入テストの道具。外からスクリプトを動かし、読み返すのは、書かれた
// `.claude/settings.json` と標準出力・終了コード・配った物だけ。使い捨てのディレクトリを毎回作る（このリポジトリ自身に打つと、
// テストが「コードのこと」ではなく「走った機械の設定ファイルのこと」を報告する）。

const (
	binPath       = ".ccnavi/scripts/ccnavi-launcher.sh" // CCNAVI_ENTRY_PATH に書くパス。固定
	launcherName  = "ccnavi-launcher.sh"
	hookCommand   = `"${CLAUDE_PROJECT_DIR}/${CCNAVI_ENTRY_PATH}"`
	fetchCommand  = `sh "${CLAUDE_PROJECT_DIR}/.ccnavi/scripts/ccnavi-session-start.sh"`
	idlePrefix    = `sh "${CLAUDE_PROJECT_DIR}/.ccnavi/scripts/ccnavi-idle.sh"`
	ticketControl = "CCNAVI_TICKET_CONTROL"
)

// 登録されていることを確かめる 8 つ（docs/50_guide/settings.md「hook の 8 つのイベント」と対）。
var events = []string{
	"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SubagentStart", "SubagentStop",
}

// idleCommands は放置したセッションの引き継ぎ促し（ccnavi-idle.sh）の 3 行。本体の 1 行とは別に登録される。
var idleCommands = map[string][]string{
	"Stop":             {idlePrefix + " notice", idlePrefix + " start"},
	"UserPromptSubmit": {idlePrefix + " reset"},
}

// gateScripts は --deploy が配る、代わりに通る sh。拒否の文面が案内する形で、無いと止められた側にほかに取れる方法がない。
var gateScripts = []string{"ccnavi-ticket.sh", "ccnavi-review.sh", "ccnavi-git.sh"}

// deployScripts は実際に配る sh。3 本が起動して最初に読む共通部（入口と部品）も要る。承認済みチケットをコミットして push する sh
// （ccnavi-user-push-approved.sh）も配る（ボードは承認のあとこれをターミナルに送る）。ユーザだけが打つ sh は ccnavi-user-*.sh。
var deployScripts = append(append([]string{}, gateScripts...),
	"ccnavi-common-state.sh", "ccnavi-common-lock.sh", "ccnavi-common-write-and-push.sh", "ccnavi-common-host.sh",
	"ccnavi-common-log.sh", "ccnavi-common-review.sh", "ccnavi-common.sh", "ccnavi-user-lib-review.sh",
	"ccnavi-user-push-approved.sh", "ccnavi-user-agree.sh", "ccnavi-user-decide.sh", "ccnavi-user-close-early.sh",
	"ccnavi-user-clean-branch.sh", "ccnavi-user-handoff.sh", "ccnavi-user-mirror.sh", "ccnavi-user-prune.sh",
	"ccnavi-fetch.sh", "ccnavi-dirs.sh", "ccnavi-session-start.sh", "ccnavi-sync.sh", "ccnavi-clean.sh",
	// 依頼文の issue・MR の指定に応じて hook が案内する sh
	"ccnavi-branches.sh", "ccnavi-start.sh", "ccnavi-stage.sh",
	// ccnavi-stage.sh が読む、下書きの置き場で読み替える階層名の表
	"ccnavi-handoff-names.txt", "ccnavi-worktree.sh", "ccnavi-idle.sh")

var deployDocs = []string{"worktree.md", "frontmatter.md"}

// quietDeploy は配布元を名指ししていない呼び出しから、配布を外す。配布は既定で走るので、設定の話をするテストでそれを許すと、
// テストが「コードのこと」ではなく「走った機械に dist/ が組み立ててあるかどうか」を報告する。
func quietDeploy(args []string) []string {
	for _, a := range args {
		if a == "--deploy" || a == "--no-deploy" {
			return args
		}
	}
	return append([]string{"--no-deploy"}, args...)
}

// target は配布先（導入スクリプトを打つ対象のディレクトリ）。
type target struct {
	t   *testing.T
	dir string
}

func newTarget(t *testing.T) *target {
	t.Helper()
	haveCommands(t, "sh", "jq")
	return &target{t: t, dir: base.TempDir(t)}
}

func (w *target) path(parts ...string) string {
	return filepath.Join(append([]string{w.dir}, parts...)...)
}

// setup は配布先に導入スクリプトを打つ。配布元を名指ししていなければ配布は外す。
func (w *target) setup(args ...string) run.Result {
	w.t.Helper()
	return w.setupEnv(nil, args...)
}

func (w *target) setupEnv(extra map[string]string, args ...string) run.Result {
	w.t.Helper()
	return run.Command(w.t, "sh", append([]string{scriptPath("ccnavi-setup.sh"), w.dir}, quietDeploy(args)...), run.Opts{Env: extra})
}

// raw は対象ディレクトリを自分で並べる形。引数の扱いを試すときに使う。
func (w *target) raw(args ...string) run.Result {
	w.t.Helper()
	return run.Command(w.t, "sh", append([]string{scriptPath("ccnavi-setup.sh")}, quietDeploy(args)...), run.Opts{})
}

// copied は配布を既定のまま走らせる（quietDeploy を通さない）。
func (w *target) copied(script string, args ...string) run.Result {
	w.t.Helper()
	return run.Command(w.t, "sh", append([]string{script, w.dir}, args...), run.Opts{})
}

func (w *target) settingsPath() string { return w.path(".claude", "settings.json") }

func (w *target) writeSettings(data any) {
	w.t.Helper()
	writeJSON(w.t, w.settingsPath(), data)
}

func writeJSON(t testing.TB, path string, data any) {
	t.Helper()
	if s, ok := data.(string); ok {
		shkit.Write(t, path, s)
		return
	}
	b, err := json.Marshal(data)
	if err != nil {
		t.Fatal(err)
	}
	shkit.Write(t, path, string(b))
}

type jsonObj = map[string]any

func parseJSON(t testing.TB, text string) jsonObj {
	t.Helper()
	var out jsonObj
	if err := json.Unmarshal([]byte(text), &out); err != nil {
		t.Fatalf("JSON が読めない: %v\n%s", err, text)
	}
	return out
}

func (w *target) settings() jsonObj {
	w.t.Helper()
	return parseJSON(w.t, shkit.Read(w.t, w.settingsPath()))
}

func (w *target) envOf() jsonObj {
	w.t.Helper()
	e, _ := w.settings()["env"].(map[string]any)
	return e
}

func (w *target) vscodePath() string { return w.path(".vscode", "settings.json") }

func (w *target) vscode() jsonObj { return parseJSON(w.t, shkit.Read(w.t, w.vscodePath())) }

// hooksOf は event に登録された hook（command・type・timeout の入れ物）。
func hooksOf(data jsonObj, event string) []jsonObj {
	var out []jsonObj
	hooks, _ := data["hooks"].(map[string]any)
	entries, _ := hooks[event].([]any)
	for _, e := range entries {
		entry, _ := e.(map[string]any)
		inner, _ := entry["hooks"].([]any)
		for _, h := range inner {
			if m, ok := h.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func commandsOf(data jsonObj, event string) []string {
	out := []string{}
	for _, h := range hooksOf(data, event) {
		c, _ := h["command"].(string)
		out = append(out, c)
	}
	return out
}

// section は見出しで始まる塊の、字下げされた行を並べる。文面の中にパスが 1 回出るだけを見ると、別の塊に出たパスでも通ってしまう。
// どの塊に並んだかまで見る。
func section(stdout, heading string) []string {
	var out []string
	inside := false
	for _, line := range strings.Split(stdout, "\n") {
		if inside && strings.HasPrefix(line, "  ") {
			out = append(out, strings.TrimSpace(line))
			continue
		}
		inside = strings.HasPrefix(line, heading)
	}
	return out
}

// listed は section の行のどれかが、そのパスそのもので始まるか。長いパスの頭と取り違えない。
func listed(lines []string, path string) bool {
	re := regexp.MustCompile("^" + regexp.QuoteMeta(path) + `($|[^\w./-])`)
	for _, line := range lines {
		if re.MatchString(line) {
			return true
		}
	}
	return false
}

func wantListed(t testing.TB, stdout, heading, path string) {
	t.Helper()
	if !listed(section(stdout, heading), path) {
		t.Errorf("%q の塊に %s が無い:\n%s", heading, path, stdout)
	}
}

func wantNotListed(t testing.TB, stdout, heading, path string) {
	t.Helper()
	if listed(section(stdout, heading), path) {
		t.Errorf("%q の塊に %s がある:\n%s", heading, path, stdout)
	}
}

// ---- 偽の配布元

type srcOpts struct {
	Unbuilt    bool              // dist/ccnavi/ を作らない
	NoParts    bool              // 配るもの（rules・sh・docs など）を置かない
	NoLauncher bool              // .ccnavi/scripts/ccnavi-launcher.sh だけを置かない
	NoDocs     bool              // 使い方の文書の置き場（.ccnavi/docs/）を置かない
	Target     string            // dist/ccnavi.target に書く目印（空なら、この機械）
	NoTarget   bool              // 目印を書かない
	Templates  map[string]string // nil でなければ共通のテンプレートの置き場に置く（空なら置き場だけ作る）
	Skills     map[string]string // 配布元の .claude/skills/ からの相対パスと中身
}

// makeSource は配布元のふりをするディレクトリを作る。実物を組み立てない（見たいのは「どこから何を配るか」で、実行ファイルの中身ではない）。
func makeSource(t testing.TB, o srcOpts) string {
	t.Helper()
	src := base.TempDir(t)
	machine := o.Target
	if machine == "" {
		machine = thisMachine()
	}
	if !o.Unbuilt {
		if !o.NoTarget {
			shkit.Write(t, filepath.Join(src, "dist", "ccnavi.target"), machine+"\n")
		}
		shkit.WriteExec(t, filepath.Join(src, "dist", "ccnavi", "ccnavi"), "#!/bin/sh\nexit 0\n")
	}
	if o.NoParts {
		return src
	}
	// 設定 4 本のひな形は共通設定。ワークスペースの設定の phases.yml は配布元にあっても配らない。
	shkit.Write(t, filepath.Join(src, ".ccnavi", "config", "common", "rules.yml"), "deny: []\n")
	shkit.Write(t, filepath.Join(src, ".ccnavi", "config", "common", "risks.yml"), "version: 1\nlevels: {}\nfactors: []\n")
	shkit.Write(t, filepath.Join(src, ".ccnavi", "config", "common", "phases.yml"), "version: 1\nphases: {}\n")
	shkit.Write(t, filepath.Join(src, ".ccnavi", "config", "common", "ticket-sections.yml"), "version: 1\nparent: []\nchild: []\n")
	shkit.Write(t, filepath.Join(src, ".ccnavi", "config", "project", "phases.yml"), "version: 1\nphases: {}\n")
	for _, name := range deployScripts {
		shkit.Write(t, filepath.Join(src, ".ccnavi", "scripts", name), "# "+name+"\n")
	}
	shkit.Write(t, filepath.Join(src, ".ccnavi", "CCNAVI.md"), "# ccnavi の作業の決まり\n")
	if !o.NoDocs {
		for _, name := range deployDocs {
			shkit.Write(t, filepath.Join(src, ".ccnavi", "docs", name), "# "+name+"\n")
		}
	}
	if o.Templates != nil {
		shkit.Mkdir(t, filepath.Join(src, ".ccnavi", "config", "common", "templates"))
		for name, text := range o.Templates {
			shkit.Write(t, filepath.Join(src, ".ccnavi", "config", "common", "templates", name), text)
		}
	}
	for rel, text := range o.Skills {
		shkit.Write(t, filepath.Join(src, ".claude", "skills", filepath.FromSlash(rel)), text)
	}
	if !o.NoLauncher {
		// 振り分けの sh は実物をコピーし、代わりに通る sh と同じ置き場に置く（配布先で中身が同じであることを見るため）。
		// モードは外して置く（配布元の置き方に依らず、配った先で実行ビットが付くことを見るため）。
		copyFile(t, launcherPath(), filepath.Join(src, ".ccnavi", "scripts", launcherName), 0o644)
	}
	return src
}

// installScript は配布元のふりをするディレクトリに、このスクリプト自身を置く。既定の配布元は「打ったスクリプトの置き場の 1 つ上」。
// 実際の ccnavi の根をそのまま配布元にすると、テストが走った機械に dist/ が組み立ててあるかどうかで結果が変わる。
func installScript(t testing.TB, src string) string {
	t.Helper()
	copied := filepath.Join(src, "scripts", "ccnavi-setup.sh")
	copyFile(t, scriptPath("ccnavi-setup.sh"), copied, 0o644)
	return copied
}

// built は配った組み立ての置き場（rest を足すとその下）。
func (w *target) built(machine string, rest ...string) string {
	return w.path(append([]string{".ccnavi", "bin", machine}, rest...)...)
}

func (w *target) launcher() string { return w.path(".ccnavi", "scripts", launcherName) }

func (w *target) rules() string { return w.path(".ccnavi", "config", "common", "rules.yml") }

// makeGit は配布先を git のリポジトリに見せる。.gitignore を書くのはここだけ。
func (w *target) makeGit() { shkit.Mkdir(w.t, w.path(".git")) }

func (w *target) gitignore() (string, bool) {
	data, err := os.ReadFile(w.path(".gitignore"))
	return string(data), err == nil
}

func (w *target) writeGitignore(text string) { shkit.Write(w.t, w.path(".gitignore"), text) }

func (w *target) readRaw() string { return shkit.Read(w.t, w.path(".gitignore")) }

// changeSourceBuild は配布元の組み立てを変える（組み立て直したことにする）。
func changeSourceBuild(t testing.TB, src, text string) {
	t.Helper()
	shkit.Write(t, filepath.Join(src, "dist", "ccnavi", "ccnavi"), text)
}
