package post

// この置き場のテストが共有する道具。実際の git リポジトリを使い捨ての置き場に組み、汚してから hook の payload を
// 1 回ずつ渡し、返ってきた文・終了コード・記録だけを読む。git を差し替えると、テストが通ることと検査が動くことが
// 別の話になるので、本物の git を使う。

import (
	"encoding/json"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// git は repo で git を打ち、標準出力を返す。失敗したら落とす。
func git(t testing.TB, repo string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-c", "user.email=t@example.com", "-c", "user.name=t"}, args...)...)
	cmd.Dir = repo
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return string(out)
}

func write(t testing.TB, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func read(t testing.TB, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

func remove(t testing.TB, path string) {
	t.Helper()
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
}

func slash(s string) string { return strings.ReplaceAll(s, `\`, "/") }

// templates は鍵ごとに 1 度だけ組んだリポジトリの置き場（プロセスの終わりに消える）。
var (
	templatesMu sync.Mutex
	templates   = map[string]*template{}
	templateDir string
)

type template struct {
	once sync.Once
	dir  string
}

// copyOf は build で組んだリポジトリを、鍵ごとにこのプロセスで 1 回だけ作り、使い捨ての置き場へ写して返す。
// 組んだ木は git のワークツリーと入れ子のリポジトリを含む。git を起こさずに済ませるため、ワークツリーの `.git` と
// 登録（`.git/worktrees/*/gitdir`）に書かれた絶対パスだけを、写した先に書き換える。
func copyOf(t testing.TB, key string, build func(dir string)) string {
	t.Helper()
	templatesMu.Lock()
	if templateDir == "" {
		dir, err := os.MkdirTemp(base.TempBase(), "cc-tpl-")
		if err != nil {
			templatesMu.Unlock()
			t.Fatal(err)
		}
		templateDir, _ = filepath.EvalSymlinks(dir)
		base.AtExit(templateDir)
	}
	tpl := templates[key]
	if tpl == nil {
		tpl = &template{}
		templates[key] = tpl
	}
	parent := templateDir
	templatesMu.Unlock()

	tpl.once.Do(func() {
		dir := filepath.Join(parent, strings.NewReplacer("/", "_", " ", "_").Replace(key))
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		build(dir)
		tpl.dir = dir
	})
	if tpl.dir == "" {
		t.Fatalf("リポジトリの見本 %q を組めなかった", key)
	}
	dest := base.TempDir(t)
	copyTree(t, tpl.dir, dest)
	return dest
}

func copyTree(t testing.TB, from, to string) {
	t.Helper()
	err := filepath.WalkDir(from, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(from, path)
		target := filepath.Join(to, rel)
		info, err := d.Info()
		if err != nil {
			return err
		}
		switch {
		case d.IsDir():
			return os.MkdirAll(target, 0o755)
		case info.Mode()&fs.ModeSymlink != 0:
			link, err := os.Readlink(path)
			if err != nil {
				return err
			}
			return os.Symlink(link, target)
		default:
			b, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			if isGitLink(path) {
				b = []byte(strings.NewReplacer(from, to, slash(from), slash(to)).Replace(string(b)))
			}
			return os.WriteFile(target, b, info.Mode().Perm())
		}
	})
	if err != nil {
		t.Fatal(err)
	}
}

// isGitLink は、絶対パスを書いた `.git`（ワークツリーの印）と `.git/worktrees/*/gitdir`。
func isGitLink(path string) bool {
	name := filepath.Base(path)
	if name == ".git" {
		return true
	}
	return name == "gitdir" && filepath.Base(filepath.Dir(filepath.Dir(path))) == "worktrees"
}

// call は hook の payload 1 回分。空の欄は渡さない。
type call struct {
	Event, Tool, Session, Source, Prompt, Cwd string
	Input                                     map[string]any
	Extra                                     map[string]any // payload に足す欄（error・agent_id・tool_use_id など）
	Mode, Restore, Guard                      string         // フラグ
	EnvMode                                   string         // 環境の CCNAVI_MODE（`--mode disable` はフラグから言えないので環境で渡す）
	Env                                       map[string]string
}

// fire は root を使う作業場所に call を 1 回渡す。作業ディレクトリはこのリポジトリ（走った機械の影響を受けない固定の場所）。
func fire(t testing.TB, root string, c call) run.Result {
	t.Helper()
	if c.Event == "" {
		c.Event = "PostToolUse"
	}
	if c.Tool == "" {
		c.Tool = "Bash"
	}
	if c.Session == "" {
		c.Session = "s1"
	}
	body := map[string]any{
		"hook_event_name": c.Event,
		"tool_name":       c.Tool,
		"tool_input":      map[string]any{},
		"session_id":      c.Session,
	}
	if c.Input != nil {
		body["tool_input"] = c.Input
	}
	if c.Cwd != "" {
		body["cwd"] = c.Cwd
	}
	if c.Prompt != "" {
		body["prompt"] = c.Prompt
	}
	if c.Source != "" {
		body["source"] = c.Source
	}
	for k, v := range c.Extra {
		body[k] = v
	}
	payload, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	args := []string{"--root", root}
	if c.Mode != "" {
		args = append(args, "--mode", c.Mode)
	}
	if c.Restore != "" {
		args = append(args, "--restore-if-deny", c.Restore)
	}
	if c.Guard != "" {
		args = append(args, "--guard-core-files", c.Guard)
	}
	environ := map[string]string{}
	for k, v := range c.Env {
		environ[k] = v
	}
	if c.EnvMode != "" {
		environ[env.Mode] = c.EnvMode
	}
	return run.Ccnavi(t, args, run.Opts{Input: string(payload), Dir: base.Root(), Env: environ})
}

// records は logs/decisions.jsonl の行。
func records(t testing.TB, root string) []map[string]any {
	t.Helper()
	path := filepath.Join(root, "logs", "decisions.jsonl")
	if !exists(path) {
		return nil
	}
	var out []map[string]any
	for _, line := range strings.Split(read(t, path), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		row := map[string]any{}
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatalf("記録の行が JSON でない: %q", line)
		}
		out = append(out, row)
	}
	return out
}

// last は最後の記録。無ければ落とす。
func last(t testing.TB, root string) map[string]any {
	t.Helper()
	rows := records(t, root)
	if len(rows) == 0 {
		t.Fatal("決定の記録が 1 行も無い")
	}
	return rows[len(rows)-1]
}

// str は記録の文字列の欄（無ければ空）。
func str(row map[string]any, key string) string {
	s, _ := row[key].(string)
	return s
}

func strs(row map[string]any, key string) []string {
	var out []string
	if list, ok := row[key].([]any); ok {
		for _, v := range list {
			s, _ := v.(string)
			out = append(out, s)
		}
	}
	return out
}

// output は応答の標準出力 JSON の入れ子の欄。
func hookOutput(t testing.TB, r run.Result) map[string]any {
	t.Helper()
	out := map[string]any{}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		t.Fatalf("標準出力が期待した JSON ではない: %v\nstdout: %q", err, r.Stdout)
	}
	return out
}

// additional は PostToolUse 系の応答の additionalContext。
func additional(t testing.TB, r run.Result) string {
	t.Helper()
	specific, _ := hookOutput(t, r)["hookSpecificOutput"].(map[string]any)
	return str(specific, "additionalContext")
}

// systemMessage は Stop の応答の systemMessage。
func systemMessage(t testing.TB, r run.Result) string {
	t.Helper()
	return str(hookOutput(t, r), "systemMessage")
}

// said は標準出力と標準エラーをつなげたもの。
func said(r run.Result) string { return r.Stdout + r.Stderr }

func mustHave(t testing.TB, got string, wants ...string) {
	t.Helper()
	for _, w := range wants {
		if !strings.Contains(got, w) {
			t.Errorf("%q が無い:\n%s", w, got)
		}
	}
}

func mustNotHave(t testing.TB, got string, unwanted ...string) {
	t.Helper()
	for _, w := range unwanted {
		if strings.Contains(got, w) {
			t.Errorf("%q が有る:\n%s", w, got)
		}
	}
}

// canHardLink はこの機械でハードリンクを作れるか（作れない機械は飛ばす）。
func canHardLink(t testing.TB) bool {
	t.Helper()
	dir := base.TempDir(t)
	a := filepath.Join(dir, "a")
	write(t, a, "x")
	return os.Link(a, filepath.Join(dir, "b")) == nil
}

// mergeTree は git が `merge-tree --write-tree`（2.38 以上）を持つか。squash の取り込みを確かめるテストが使う。
var mergeTree = sync.OnceValue(func() bool {
	return exec.Command("git", "merge-tree", "--write-tree", "HEAD", "HEAD").Run() == nil
})

// gitTry は git を打ち、失敗を返す（git の版で通らない手順を飛ばすときに使う）。
func gitTry(repo string, args ...string) error {
	cmd := exec.Command("git", append([]string{"-c", "user.email=t@example.com", "-c", "user.name=t"}, args...)...)
	cmd.Dir = repo
	return cmd.Run()
}

// freshWorkspace は tests/fixtures の見本のルールを共通レイヤーに据えた、テストごとの作業場所。
// base.FixtureWorkspace は読むだけの共有の木だが、記録や state を書く検査は、並行するテストと取り合わないよう別に持つ。
func freshWorkspace(t testing.TB, fixture string) string {
	t.Helper()
	root := base.TempDir(t)
	write(t, base.CommonPath(root, "rules"), read(t, filepath.Join(base.Root(), "tests", "fixtures", fixture)))
	return root
}
