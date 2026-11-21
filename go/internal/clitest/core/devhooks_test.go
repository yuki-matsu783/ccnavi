package core

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// 開発用の hook（`.claude/hooks/`）のうち、ターンの終わりのテスト（test-go.sh・test-ext.sh）と、
// 編集ごとの書き残し・整形（lint-go.sh・mark-ext.sh）の振る舞い。check-adr.sh は checkadrhook_test.go。
// テストそのものは回さない。go・gofmt・node の入口を代わりのものに差し替え、呼ばれた居場所と引数を書き出させる。
// 保護対象なので、base.Final で下書きがあれば下書きを読む。

// devHookEnv は hook を起こす一時の置き場。ws が CLAUDE_PROJECT_DIR（記録 logs/session/ を置く）。
type devHookEnv struct {
	t     *testing.T
	base  string
	ws    string
	bin   string
	calls string
}

func newDevHookEnv(t *testing.T) *devHookEnv {
	t.Helper()
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("sh が無い")
	}
	b := base.TempDir(t)
	e := &devHookEnv{t: t, base: b, ws: filepath.Join(b, "ws"), bin: mkdir(t, filepath.Join(b, "bin")), calls: filepath.Join(b, "calls.log")}
	mkdir(t, filepath.Join(e.ws, "logs", "session"))
	return e
}

func (e *devHookEnv) stub(name, body string) {
	e.t.Helper()
	path := writeFile(e.t, e.bin, name, body)
	if err := os.Chmod(path, 0o755); err != nil {
		e.t.Fatal(err)
	}
}

// goStubs は go と gofmt の代わり。居場所・GOOS・名前・引数を 1 行ずつ calls に足す。
// STUB_FAIL に挙げた「名前 最初の引数」なら失敗し、STUB_GOFMT があれば gofmt はそれを出力する。`go list` は 2 つのパッケージを出す。
func (e *devHookEnv) goStubs() {
	const body = `#!/bin/sh
printf '%s|%s|%s %s\n' "$(pwd)" "${GOOS:-host}" "$(basename "$0")" "$*" >>"$STUB_CALLS"
case "$(basename "$0") $1" in
"go list") printf 'ccnavi/internal/a\nccnavi/internal/clitest/core\n'; exit 0 ;;
esac
[ "$(basename "$0")" = gofmt ] && [ -n "$STUB_GOFMT" ] && { echo "$STUB_GOFMT"; exit 0; }
case "$(basename "$0") $1" in
"$STUB_FAIL") echo "FAIL: stub"; exit 1 ;;
esac
exit 0
`
	e.stub("go", body)
	e.stub("gofmt", body)
}

func (e *devHookEnv) hook(name, payload string, env map[string]string) run.Result {
	e.t.Helper()
	all := map[string]string{
		"CLAUDE_PROJECT_DIR": e.ws,
		"PATH":               e.bin + string(os.PathListSeparator) + os.Getenv("PATH"),
		"STUB_CALLS":         e.calls,
	}
	for k, v := range env {
		all[k] = v
	}
	return run.Sh(e.t, base.Final(".claude/hooks/"+name), nil, run.Opts{Input: payload, Dir: e.ws, Env: all})
}

func (e *devHookEnv) session(name string) string {
	return filepath.Join(e.ws, "logs", "session", name)
}

func (e *devHookEnv) lines(path string) []string {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var out []string
	for _, l := range strings.Split(string(data), "\n") {
		if strings.TrimSpace(l) != "" {
			out = append(out, strings.TrimSpace(l))
		}
	}
	return out
}

func stopPayload(session string, active bool) string {
	flag := "false"
	if active {
		flag = "true"
	}
	return `{"session_id":"` + session + `","hook_event_name":"Stop","stop_hook_active":` + flag + `}`
}

// goTree は go/go.mod を持つ木を作る。
func (e *devHookEnv) goTree(name string) string {
	tree := filepath.Join(e.base, name)
	writeFile(e.t, tree, "go/go.mod", "module x\n")
	return tree
}

func TestGoTestHook(t *testing.T) {
	t.Parallel()
	record := func(e *devHookEnv, session string, trees ...string) {
		writeFile(t, filepath.Dir(e.session("x")), session+".go-trees", strings.Join(trees, "\n")+"\n")
	}
	t.Run("記録した木ごとに 1 回、go/ で書式・vet・テストを回す", func(t *testing.T) {
		t.Parallel()
		e := newDevHookEnv(t)
		e.goStubs()
		a, b := e.goTree("a"), e.goTree("my dir")
		record(e, "s1", a, b, a)
		r := e.hook("test-go.sh", stopPayload("s1", false), nil)
		if r.Code != 0 {
			t.Fatalf("code = %d: %s", r.Code, r.Stderr)
		}
		calls := strings.Join(e.lines(e.calls), "\n")
		for _, tree := range []string{a, b} {
			dir := filepath.Join(tree, "go")
			for _, want := range []string{
				dir + "|host|gofmt -l .",
				dir + "|host|go vet ./...",
				dir + "|windows|go vet ./...",
				dir + "|host|go test ccnavi/internal/a\n",
				dir + "|host|go test ./internal/clitest/repo/",
			} {
				if !strings.Contains(calls+"\n", want) {
					t.Errorf("呼ばれていない: %q\n%s", want, calls)
				}
			}
		}
		if n := strings.Count(calls, "|host|go test ./internal/clitest/repo/"); n != 2 {
			t.Errorf("clitest/repo の回数 = %d（木ごとに 1 回）", n)
		}
		mustNotContain(t, "calls", calls, "clitest/core", "-count")
		if len(e.lines(e.session("s1.go-trees"))) != 0 {
			t.Error("通ったら記録を消す")
		}
	})
	t.Run("何も触っていないターンは何も回さない", func(t *testing.T) {
		t.Parallel()
		e := newDevHookEnv(t)
		e.goStubs()
		r := e.hook("test-go.sh", stopPayload("s1", false), nil)
		if r.Code != 0 || r.Stderr != "" || len(e.lines(e.calls)) != 0 {
			t.Errorf("code=%d stderr=%q calls=%v", r.Code, r.Stderr, e.lines(e.calls))
		}
	})
	t.Run("失敗は差し戻し、最初に落ちた段で止まる。書式の指摘も同じ", func(t *testing.T) {
		t.Parallel()
		for name, env := range map[string]map[string]string{
			"vet":   {"STUB_FAIL": "go vet"},
			"test":  {"STUB_FAIL": "go test"},
			"gofmt": {"STUB_GOFMT": "x.go"},
		} {
			e := newDevHookEnv(t)
			e.goStubs()
			record(e, "s1", e.goTree("a"))
			r := e.hook("test-go.sh", stopPayload("s1", false), env)
			if r.Code != 2 {
				t.Errorf("%s: code = %d", name, r.Code)
			}
			mustContain(t, name+" stderr", r.Stderr, "失敗したテストを直して")
			if got := e.lines(e.session("s1.retries")); len(got) != 1 || got[0] != "1" {
				t.Errorf("%s: retries = %v", name, got)
			}
			if name == "vet" && strings.Contains(strings.Join(e.lines(e.calls), "\n"), "go test") {
				t.Error("vet で落ちたあとにテストを回した")
			}
		}
	})
	t.Run("差し戻しは 3 回まで", func(t *testing.T) {
		t.Parallel()
		e := newDevHookEnv(t)
		e.goStubs()
		record(e, "s1", e.goTree("a"))
		env := map[string]string{"STUB_FAIL": "go test"}
		for _, want := range []string{"1", "2", "3"} {
			if r := e.hook("test-go.sh", stopPayload("s1", true), env); r.Code != 2 {
				t.Fatalf("code = %d", r.Code)
			}
			if got := e.lines(e.session("s1.retries")); len(got) != 1 || got[0] != want {
				t.Fatalf("retries = %v, want %s", got, want)
			}
		}
		r := e.hook("test-go.sh", stopPayload("s1", true), env)
		mustContain(t, "stderr", r.Stderr, "これ以上は止めません")
		if r.Code != 0 || len(e.lines(e.session("s1.retries"))) != 0 {
			t.Errorf("code = %d", r.Code)
		}
	})
	t.Run("1 時間を超えた自分の記録は読み、記録の名前は置き場の外を指さない", func(t *testing.T) {
		t.Parallel()
		e := newDevHookEnv(t)
		e.goStubs()
		record(e, "s1", e.goTree("a"))
		old := time.Now().Add(-2 * time.Hour)
		if err := os.Chtimes(e.session("s1.go-trees"), old, old); err != nil {
			t.Fatal(err)
		}
		if r := e.hook("test-go.sh", stopPayload("s1", false), nil); r.Code != 0 || len(e.lines(e.calls)) == 0 {
			t.Errorf("古い自分の記録を読まなかった: code=%d", r.Code)
		}
		// 区切りの入った session_id は unknown に落ちる
		record(e, "unknown", e.goTree("b"))
		e.hook("test-go.sh", stopPayload("../evil", false), map[string]string{"STUB_FAIL": "go test"})
		if _, err := os.Stat(e.session("unknown.retries")); err != nil {
			t.Errorf("unknown に落ちていない: %v", err)
		}
	})
}

func TestLintGoHook(t *testing.T) {
	t.Parallel()
	edit := func(e *devHookEnv, file string, env map[string]string) run.Result {
		return e.hook("lint-go.sh", `{"session_id":"s1","hook_event_name":"PostToolUse","tool_input":{"file_path":"`+file+`"}}`, env)
	}
	t.Run("整っている go ファイルは通り、触った木を書き残す", func(t *testing.T) {
		t.Parallel()
		e := newDevHookEnv(t)
		e.goStubs()
		tree := e.goTree("a")
		if r := edit(e, filepath.Join(tree, "go", "x", "y.go"), nil); r.Code != 0 {
			t.Fatalf("code = %d: %s", r.Code, r.Stderr)
		}
		if got := e.lines(e.session("s1.go-trees")); len(got) != 1 || got[0] != tree {
			t.Errorf("go-trees = %v", got)
		}
	})
	t.Run("整形されていない go ファイルは止める", func(t *testing.T) {
		t.Parallel()
		e := newDevHookEnv(t)
		e.goStubs()
		file := filepath.Join(e.goTree("a"), "go", "y.go")
		r := edit(e, file, map[string]string{"STUB_GOFMT": file})
		if r.Code != 2 {
			t.Errorf("code = %d", r.Code)
		}
		mustContain(t, "stderr", r.Stderr, "gofmt -w", file)
	})
	t.Run(".ccnavi と docs の編集は書き残すだけで整形は見ない。それ以外と go.mod の無い所は何もしない", func(t *testing.T) {
		t.Parallel()
		e := newDevHookEnv(t)
		e.goStubs()
		tree := e.goTree("a")
		for _, rel := range []string{".ccnavi/docs/x.md", "docs/y.md"} {
			if r := edit(e, filepath.Join(tree, filepath.FromSlash(rel)), map[string]string{"STUB_GOFMT": "bad"}); r.Code != 0 {
				t.Errorf("%s: code = %d", rel, r.Code)
			}
		}
		if got := e.lines(e.session("s1.go-trees")); len(got) != 2 {
			t.Errorf("go-trees = %v", got)
		}
		before := len(e.lines(e.session("s1.go-trees")))
		edit(e, filepath.Join(tree, "README.md"), nil)
		edit(e, filepath.Join(e.base, "elsewhere", "z.go"), nil)
		edit(e, "", nil)
		if after := len(e.lines(e.session("s1.go-trees"))); after != before {
			t.Errorf("書き残しが増えた: %d -> %d", before, after)
		}
	})
}

func TestMarkExtHook(t *testing.T) {
	t.Parallel()
	e := newDevHookEnv(t)
	mark := func(path string) []string {
		t.Helper()
		r := e.hook("mark-ext.sh", `{"session_id":"s1","hook_event_name":"PostToolUse","tool_input":{"file_path":"`+path+`"}}`, nil)
		if r.Code != 0 {
			t.Fatalf("code = %d: %s", r.Code, r.Stderr)
		}
		return e.lines(e.session("s1.ext-files"))
	}
	vscode := "/repo/extensions/vscode/ccnavi-board/src/core/board.ts"
	chrome := "/repo/extensions/chrome/ccnavi-approval/src/board.ts"
	want := []string{}
	for _, c := range []struct{ given, wrote string }{
		{vscode, vscode},
		{"/repo/.claude/worktrees/w1/extensions/vscode/ccnavi-board/test/b.test.ts", "/repo/.claude/worktrees/w1/extensions/vscode/ccnavi-board/test/b.test.ts"},
		// JSON の中でバックスラッシュは 2 個に増えている
		{`C:\\repo\\extensions\\vscode\\ccnavi-board\\src\\b.ts`, "C:/repo/extensions/vscode/ccnavi-board/src/b.ts"},
		{"extensions/vscode/ccnavi-board/src/core/board.ts", "extensions/vscode/ccnavi-board/src/core/board.ts"},
		{chrome, chrome},
	} {
		want = append(want, c.wrote)
		if got := mark(c.given); strings.Join(got, "\n") != strings.Join(want, "\n") {
			t.Fatalf("%s を渡した後 = %v, want %v", c.given, got, want)
		}
	}
	for _, outside := range []string{"/repo/go/cmd/ccnavi/main.go", "/repo/docs/30_adr/x/0001-a.md"} {
		if got := mark(outside); len(got) != len(want) {
			t.Errorf("拡張の外 %s を書き残した: %v", outside, got)
		}
	}
}

func TestTestExtHook(t *testing.T) {
	t.Parallel()
	if _, err := exec.LookPath("node"); err != nil {
		t.Skip("node が無い")
	}
	// 入口（scripts/test-groups.js・scripts/test.js）は代わりのもの。渡された引数と居場所を書き出し、STUB_RC で終わる。
	const stub = "const fs=require('node:fs');fs.writeFileSync(process.env.STUB_LOG," +
		"[process.cwd(),...process.argv.slice(2)].join('\\n'));process.exit(Number(process.env.STUB_RC||0));\n"
	type setup struct {
		e      *devHookEnv
		root   string
		touch  string
		marker string
		log    string
	}
	vscodeSetup := func(t *testing.T, tree string) setup {
		e := newDevHookEnv(t)
		root := filepath.Join(e.base, tree, "extensions", "vscode", "ccnavi-board")
		writeFile(t, root, "scripts/test-groups.js", stub)
		touch := filepath.ToSlash(filepath.Join(root, "src", "core", "board.ts"))
		marker := writeFile(t, filepath.Dir(e.session("x")), "s1.ext-files", touch+"\n")
		return setup{e, root, touch, marker, filepath.Join(e.base, "stub.log")}
	}
	chromeSetup := func(t *testing.T, modules bool, touched ...string) setup {
		e := newDevHookEnv(t)
		root := filepath.Join(e.base, "tree", "extensions", "chrome", "ccnavi-approval")
		writeFile(t, root, "scripts/test.js", stub)
		if modules {
			mkdir(t, filepath.Join(root, "node_modules"))
		}
		var text string
		for _, rel := range touched {
			text += filepath.ToSlash(filepath.Join(root, rel)) + "\n"
		}
		marker := writeFile(t, filepath.Dir(e.session("x")), "s1.ext-files", text)
		return setup{e, root, "", marker, filepath.Join(e.base, "stub.log")}
	}
	stop := func(s setup, rc string, active bool) run.Result {
		return s.e.hook("test-ext.sh", stopPayload("s1", active), map[string]string{"STUB_LOG": s.log, "STUB_RC": rc})
	}
	exists := func(path string) bool { _, err := os.Stat(path); return err == nil }

	t.Run("触ったファイルを --for で渡す。空白や記号の入ったパスも 1 つの引数", func(t *testing.T) {
		t.Parallel()
		for _, tree := range []string{"tree", "my dir", "a#b[c]"} {
			s := vscodeSetup(t, tree)
			if r := stop(s, "0", false); r.Code != 0 {
				t.Fatalf("%s: code = %d: %s", tree, r.Code, r.Stderr)
			}
			got := s.e.lines(s.log)
			if len(got) != 3 || got[1] != "--for" || got[2] != s.touch {
				t.Errorf("%s: 引数 = %v", tree, got)
			}
			if exists(s.marker) {
				t.Errorf("%s: 通ったらマーカーを消す", tree)
			}
		}
	})
	t.Run("相対パスのマーカーはワークスペースルートの下の木を回す", func(t *testing.T) {
		t.Parallel()
		s := vscodeSetup(t, "tree")
		root := filepath.Join(s.e.ws, "extensions", "vscode", "ccnavi-board")
		writeFile(t, root, "scripts/test-groups.js", stub)
		rel := "extensions/vscode/ccnavi-board/src/core/board.ts"
		writeFile(t, filepath.Dir(s.marker), "s1.ext-files", rel+"\n")
		if r := stop(s, "0", false); r.Code != 0 {
			t.Fatalf("code = %d: %s", r.Code, r.Stderr)
		}
		if got := s.e.lines(s.log); len(got) != 3 || got[2] != rel {
			t.Errorf("引数 = %v", got)
		}
	})
	t.Run("失敗は差し戻して数え、3 回で止める。環境が足りない側（3）は数えない", func(t *testing.T) {
		t.Parallel()
		s := vscodeSetup(t, "tree")
		retries := s.e.session("s1.ext-retries")
		for _, want := range []string{"1", "2", "3"} {
			r := stop(s, "1", true)
			if r.Code != 2 {
				t.Fatalf("code = %d", r.Code)
			}
			mustContain(t, "stderr", r.Stderr, "失敗したテストを直して")
			if got := s.e.lines(retries); len(got) != 1 || got[0] != want {
				t.Fatalf("retries = %v, want %s", got, want)
			}
			if !exists(s.marker) {
				t.Fatal("差し戻したらマーカーは残す")
			}
		}
		r := stop(s, "1", true)
		mustContain(t, "stderr", r.Stderr, "これ以上は止めません")
		if r.Code != 0 || exists(retries) {
			t.Errorf("code = %d", r.Code)
		}
		s2 := vscodeSetup(t, "tree")
		if r := stop(s2, "3", false); r.Code != 0 || exists(s2.e.session("s1.ext-retries")) {
			t.Errorf("環境不足を差し戻した: code = %d", r.Code)
		}
	})
	t.Run("触っていないターンは何も回さない。1 時間を超えた自分のマーカーは読む。入口の無いマーカーは黙って通さない", func(t *testing.T) {
		t.Parallel()
		s := vscodeSetup(t, "tree")
		old := time.Now().Add(-2 * time.Hour)
		if err := os.Chtimes(s.marker, old, old); err != nil {
			t.Fatal(err)
		}
		if r := stop(s, "0", false); r.Code != 0 || !exists(s.log) {
			t.Errorf("古い自分のマーカーを読まなかった: code = %d", r.Code)
		}
		idle := vscodeSetup(t, "tree")
		if err := os.Remove(idle.marker); err != nil {
			t.Fatal(err)
		}
		if r := stop(idle, "0", false); r.Code != 0 || r.Stderr != "" || exists(idle.log) {
			t.Errorf("何も触っていないのに回った: %q", r.Stderr)
		}
		dead := vscodeSetup(t, "tree")
		if err := os.Remove(filepath.Join(dead.root, "scripts", "test-groups.js")); err != nil {
			t.Fatal(err)
		}
		r := stop(dead, "0", false)
		mustContain(t, "stderr", r.Stderr, "テストの入口がない")
		if r.Code != 0 || exists(dead.log) {
			t.Errorf("code = %d", r.Code)
		}
	})
	t.Run("Chrome の拡張は引数なしで、拡張のディレクトリで 1 回回す", func(t *testing.T) {
		t.Parallel()
		s := chromeSetup(t, true, "src/board.ts")
		if r := stop(s, "0", false); r.Code != 0 {
			t.Fatalf("code = %d: %s", r.Code, r.Stderr)
		}
		if got := s.e.lines(s.log); len(got) != 1 || !run.SamePath(got[0], s.root) {
			t.Errorf("居場所・引数 = %v, want %s のみ", got, s.root)
		}
		if exists(s.marker) {
			t.Error("通ったらマーカーを消す")
		}
		bad := chromeSetup(t, true, "src/board.ts")
		r := stop(bad, "1", false)
		mustContain(t, "stderr", r.Stderr, "失敗したテストを直して")
		if r.Code != 2 || !exists(bad.marker) {
			t.Errorf("code = %d", r.Code)
		}
	})
	t.Run("Chrome の文書だけは回さない。node_modules が無ければ差し戻さず案内する", func(t *testing.T) {
		t.Parallel()
		docs := chromeSetup(t, true, "README.md", "docs/20_design.md")
		if r := stop(docs, "0", false); r.Code != 0 || r.Stderr != "" || exists(docs.log) {
			t.Errorf("文書だけで回った: code = %d %q", r.Code, r.Stderr)
		}
		bare := chromeSetup(t, false, "src/board.ts")
		r := stop(bare, "0", false)
		mustContain(t, "stderr", r.Stderr, "pnpm install")
		if r.Code != 0 || exists(bare.log) || exists(bare.e.session("s1.ext-retries")) {
			t.Errorf("code = %d", r.Code)
		}
	})
}
