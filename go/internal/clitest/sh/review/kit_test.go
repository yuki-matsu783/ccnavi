package review

import (
	"encoding/json"
	"flag"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
)

// このファイルは、このパッケージの試験が共有する道具。

const (
	reviewSh     = "ccnavi-review.sh"
	userDecideSh = "ccnavi-user-decide.sh"
	userLibSh    = "ccnavi-user-lib-review.sh"
	commonRevSh  = "ccnavi-common-review.sh"
)

// reviewScripts は review の入口が使う sh（共通部は base.CommonSh が足す）。
var reviewScripts = []string{reviewSh, userDecideSh, userLibSh}

var updateHostFixture = flag.Bool("update-host-fixture", false, "ホストの応答の見本の expected.json を、sh が組んだ結果で書き直す")

// need は、無い道具があればテストを飛ばす。
func need(t testing.TB, tools ...string) {
	t.Helper()
	for _, tool := range tools {
		if _, err := exec.LookPath(tool); err != nil {
			t.Skipf("%s が無い", tool)
		}
	}
}

// write は親ディレクトリごと作って、LF のまま書く。
func write(t testing.TB, path, text string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

// writeExec は write して実行できるようにする。
func writeExec(t testing.TB, path, text string) string {
	t.Helper()
	write(t, path, text)
	if err := os.Chmod(path, 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

func read(t testing.TB, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

// readIf は、無いファイルを空として読む。
func readIf(path string) string {
	data, _ := os.ReadFile(path)
	return string(data)
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

func remove(t testing.TB, path string) {
	t.Helper()
	if err := os.RemoveAll(path); err != nil {
		t.Fatal(err)
	}
}

// shq は sh の単引用符で包んだパス（区切りは `/` にそろえる。Git Bash は `\` を食う）。
func shq(path string) string {
	return "'" + strings.ReplaceAll(filepath.ToSlash(path), "'", `'\''`) + "'"
}

// lines は標準出力などを行に切る（末尾の改行 1 つは区切りにしない。空は nil）。
func lines(s string) []string {
	s = strings.TrimSuffix(s, "\n")
	if s == "" {
		return nil
	}
	return strings.Split(s, "\n")
}

func contains(list []string, want string) bool {
	for _, s := range list {
		if s == want {
			return true
		}
	}
	return false
}

func anyHasPrefix(list []string, prefixes ...string) bool {
	for _, s := range list {
		for _, p := range prefixes {
			if strings.HasPrefix(s, p) {
				return true
			}
		}
	}
	return false
}

// decode は JSON のテキストを map にする（数は json.Number）。
func decode(t testing.TB, text string) map[string]any {
	t.Helper()
	dec := json.NewDecoder(strings.NewReader(text))
	dec.UseNumber()
	var out map[string]any
	if err := dec.Decode(&out); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, text)
	}
	return out
}

// shellPath は sh（無ければ bash）の絶対パス。
func shellPath(t testing.TB) string {
	t.Helper()
	for _, name := range []string{"sh", "bash"} {
		if path, err := exec.LookPath(name); err == nil {
			return path
		}
	}
	t.Skip("sh が無い")
	return ""
}

// site は使い捨てのワークスペース。ws は git のリポジトリ（ブランチ i0001・空のコミット 1 つ）で、
// `.ccnavi/scripts/` に試す sh と共通部のコピーを置く。
type site struct {
	t       *testing.T
	tmp, ws string
	scripts string
}

// newBareSite は、試す sh と共通部のコピーだけを置いた木（git のリポジトリにはしない）。
func newBareSite(t *testing.T, names ...string) *site {
	t.Helper()
	need(t, "sh", "git", "jq")
	tmp := base.TempDir(t)
	s := &site{t: t, tmp: tmp, ws: filepath.Join(tmp, "ws"), scripts: filepath.Join(tmp, "ws", ".ccnavi", "scripts")}
	src := base.ScriptsDir()
	for _, name := range append(append([]string{}, names...), base.CommonSh(t, src)...) {
		writeExec(t, filepath.Join(s.scripts, name), read(t, filepath.Join(src, name)))
	}
	return s
}

// newSite は newBareSite に、ブランチ i0001・空のコミット 1 つの git のリポジトリを足す。origin が空でなければ remote も足す。
func newSite(t *testing.T, origin string, names ...string) *site {
	t.Helper()
	s := newBareSite(t, names...)
	s.initRepo(s.ws, "i0001", origin)
	return s
}

// initRepo は dir に、空のコミット 1 つのリポジトリを作る。
func (s *site) initRepo(dir, branch, origin string) {
	s.t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		s.t.Fatal(err)
	}
	run.Git(s.t, dir, "init", "-q", "-b", branch)
	run.Git(s.t, dir, "commit", "-q", "--allow-empty", "-m", "seed")
	if origin != "" {
		run.Git(s.t, dir, "remote", "add", "origin", origin)
	}
}

func (s *site) git(args ...string) string { s.t.Helper(); return run.Git(s.t, s.ws, args...) }

// setOriginHead は origin/<name> と origin/HEAD を、今の HEAD に置く（統合先を決める）。
func setOriginHead(t testing.TB, repo, name string) {
	t.Helper()
	run.Git(t, repo, "update-ref", "refs/remotes/origin/"+name, "HEAD")
	run.Git(t, repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/"+name)
}

// newMarkerSite は共通部の目印（空の ccnavi-common.sh）だけを持つ木。sh は目印を cwd から上へ探して根を決めるので、
// 配る sh そのものを起こす試験（コピーしない）は、これだけでホストへ繋ぐ前の判定まで届く。
func newMarkerSite(t *testing.T, branch string) *site {
	t.Helper()
	need(t, "sh", "git", "jq", "curl")
	tmp := base.TempDir(t)
	s := &site{t: t, tmp: tmp, ws: tmp, scripts: filepath.Join(tmp, ".ccnavi", "scripts")}
	write(t, filepath.Join(s.scripts, "ccnavi-common.sh"), "")
	s.initRepo(tmp, branch, "")
	return s
}

// sh は ws の中の sh を 1 回起こす（作業ディレクトリは ws。o.Dir で替える）。
func (s *site) sh(script string, o run.Opts, args ...string) run.Result {
	s.t.Helper()
	if o.Dir == "" {
		o.Dir = s.ws
	}
	return run.Sh(s.t, filepath.Join(s.scripts, script), args, o)
}

// stubDir は実行ファイルの代わりの sh の置き場。
type stubDir struct {
	Path   string // 実行ファイルの代わりの sh（CCNAVI_ENTRY_PATH に渡す）
	Args   string // 受けた引数を 1 行 1 つで溜める
	Result string // `--result` に渡された JSON の写し
	Out    string // `prepare` が書く依頼の本文（body.md）と下書き（draft.md）の置き場
}

// stubHead は実行ファイルの代わりの sh の先頭。版の問いに答え、実行ファイル本物に任せる副命令は渡す。
const stubHead = `#!/bin/sh
case " $* " in
*" --version --json "*)
  printf '{"compat": @COMPAT@}\n'
  exit 0 ;;
*" --version "*) printf 'compat: @COMPAT@\n'; exit 0 ;;
*" write-and-push family "*) printf 'write-and-push 1\nfamily i0001\ntarget no\n'; exit 0 ;;
*" ops "* | *" git integration "* | *" path under "* | *" host "*) exec @EXE@ "$@" ;;
*" accept plan "* | *" accept threads "* | *" accept decided "*) exec @EXE@ "$@" ;;
esac
`

// writeStub は dir/exe/ccnavi に、実行ファイルの代わりの sh を置く。受けた引数は Args に 1 行 1 つで溜める。
// 計画（`--accept-plan`）は環境変数 STUB_PLAN の写し（無ければ空の計画）、`--result` の JSON は Result に写す。
// `prepare` は依頼の本文と下書きを Out に書いてそのパスを答える。それ以外は何もせず 0 で終わる。
func writeStub(t testing.TB, dir string) stubDir {
	t.Helper()
	d := filepath.Join(dir, "exe")
	s := stubDir{Path: filepath.Join(d, "ccnavi"), Args: filepath.Join(d, "args.txt"), Result: filepath.Join(d, "result.json"), Out: d}
	head := strings.NewReplacer("@COMPAT@", exe.Compat(t), "@EXE@", shq(exe.ShPath(t))).Replace(stubHead)
	tail := `for a in "$@"; do printf '%s\n' "$a"; done >>` + shq(s.Args) + `
case " $* " in *" prepare "*)
  printf '<!-- ccnavi:request i0001:1 key=k -->\n依頼\n' >` + shq(filepath.Join(d, "body.md")) + `
  printf 'Draft: t\n\nb\n' >` + shq(filepath.Join(d, "draft.md")) + `
  printf '%s\n%s\n' ` + shq(filepath.Join(d, "body.md")) + ` ` + shq(filepath.Join(d, "draft.md")) + `; exit 0 ;;
esac
prev=""
for a in "$@"; do
  case "$prev" in
  --choose-out) printf 'choices {}\ndigest d\n' >"$a" ;;
  --accept-plan)
    if [ -n "${STUB_PLAN:-}" ]; then cp "$STUB_PLAN" "$a"
    else printf '{"version": 1, "threads": []}\n' >"$a"; fi ;;
  --result) cp "$a" ` + shq(s.Result) + ` ;;
  esac
  prev="$a"
done
exit 0
`
	writeExec(t, s.Path, head+tail)
	return s
}

// passed は stub が受けた引数（1 行 1 つ）。
func (s stubDir) passed() []string { return lines(readIf(s.Args)) }

// reset は溜めた引数を消す。
func (s stubDir) reset(t testing.TB) { t.Helper(); remove(t, s.Args) }

// pathWith は、先頭に dirs を足した PATH。
func pathWith(dirs ...string) string {
	return strings.Join(append(dirs, os.Getenv("PATH")), string(os.PathListSeparator))
}

// pathWithout は、PATH にある道具のうち names 以外を並べた置き場を dir に作って返す（names が見つからない PATH を作る）。
func pathWithout(t testing.TB, dir string, names ...string) string {
	t.Helper()
	if !base.CanSymlink() {
		t.Skip("シンボリックリンクを張れない")
	}
	out := filepath.Join(dir, "tools")
	if err := os.MkdirAll(out, 0o755); err != nil {
		t.Fatal(err)
	}
	skip := map[string]bool{}
	for _, n := range names {
		skip[n] = true
	}
	for _, d := range filepath.SplitList(os.Getenv("PATH")) {
		entries, err := os.ReadDir(d)
		if err != nil {
			continue
		}
		for _, e := range entries {
			name := e.Name()
			if skip[name] {
				continue
			}
			full := filepath.Join(d, name)
			if info, err := os.Stat(full); err != nil || info.IsDir() || info.Mode()&0o111 == 0 {
				continue
			}
			skip[name] = true // 先に見つかったものだけ
			_ = os.Symlink(full, filepath.Join(out, name))
		}
	}
	return out
}

// real は、配る sh そのもの（コピーしない。base.ScriptsDir の下）を 1 回起こす。作業ディレクトリは ws。
func (s *site) real(script string, o run.Opts, args ...string) run.Result {
	s.t.Helper()
	if o.Dir == "" {
		o.Dir = s.ws
	}
	return run.Sh(s.t, script, args, o)
}

// planOf は、実行ファイルが `--accept-plan` に書くのと同じ形の計画の欄（スタブの teststub oprecord と、plan の書き手が共有する）。
func planOf(digest string, threads ...map[string]any) map[string]any {
	list := []any{}
	for _, th := range threads {
		list = append(list, th)
	}
	return map[string]any{"version": 1, "digest": digest, "threads": list}
}

// beginSpec は操作記録を始める欄と、その操作ID を足す計画の欄（`teststub oprecord` が読む JSON の形）。
func beginSpec(begin, plan map[string]any) map[string]any {
	return map[string]any{"begin": begin, "plan": plan}
}

// writePlan は spec の記録を state に作り、その操作ID を入れた計画を planPath に書く。操作ID を返す。
// 実行ファイルと同じく、本物の操作記録を作って、その ID を計画に書く（形は tickets/model/oplog と同じ型で書く）。
func writePlan(t testing.TB, state, planPath string, spec map[string]any) string {
	t.Helper()
	data, err := json.Marshal(spec)
	if err != nil {
		t.Fatal(err)
	}
	specPath := write(t, planPath+".spec", string(data))
	var stderr strings.Builder
	if code := samples.OprecordMain([]string{state, planPath, specPath}, &stderr); code != 0 {
		t.Fatalf("操作記録を作れない: %s", stderr.String())
	}
	return decode(t, read(t, planPath))["op"].(string)
}

// keepPlan は、指摘 nodes を全部「そのまま受け入れる」計画を書く（キーは `u/<id>`）。返信の本文は受け入れの目印で始まる。
func keepPlan(t testing.TB, tmp, state, host, mrURL string, mr int, nodes ...string) string {
	t.Helper()
	var shown, threads []any
	choices := map[string]any{}
	for _, node := range nodes {
		key := "u/" + node
		shown = append(shown, map[string]any{"key": key, "id": node, "url": key, "path": "a.py", "line": 3, "version": "v1", "body": "指摘の本文"})
		choices[key] = "keep"
		threads = append(threads, map[string]any{"id": node, "key": key, "reply": "<!-- ccnavi:accept -->\nテスト\n"})
	}
	begin := map[string]any{
		"kind": "decide", "repo": map[string]any{"host": host, "mr_url": mrURL}, "parent": "i0001", "phase": 1, "mr": mr,
		"shown": shown, "choices": choices, "plan": map[string]any{"digest": "d"}, "stages": [][]string{{"reply", "reply"}},
	}
	plan := planOf("d")
	plan["threads"] = threads
	path := filepath.Join(tmp, "plan.json")
	writePlan(t, state, path, beginSpec(begin, plan))
	return path
}
