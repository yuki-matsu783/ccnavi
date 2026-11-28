package review

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// このファイルは、ユーザ専用の decide（ccnavi-user-decide.sh）の試験が共有する道具。sh を外から呼び、ホスト（GitLab）の
// 小さな HTTP サーバと実行ファイルの代わりの sh で 1 周させる。実行ファイルの代わりの sh は、`ops …` を本物の `ccnavi ops` に渡し、
// 受け入れの計画を本物の操作記録の形（samples.OprecordMain）で書く。

// acceptMarker は受け入れの返信の頭の印（実行ファイルの host.AcceptReply と同じ）。
const acceptMarker = "<!-- ccnavi:accept -->"

// post はホストが受けた書き込み 1 件。
type post struct {
	kind string
	data map[string]any
}

// gitlab は sh が呼ぶ経路だけを返す小さなホスト。書き込みは posted に積む。
type gitlab struct {
	mu  sync.Mutex
	srv *httptest.Server

	posted      []post
	threads     []*thread // スレッド（GET で返し、解決の PUT で解決済みにする）。順を保つ
	fetches     int       // スレッドの 1 ページ目を読まれた回数
	failReply   bool
	failResolve bool
	// failResolveIDs は、解決の PUT だけを 500 にするスレッドの id。
	failResolveIDs map[string]bool

	// 以下は issue・コメントの一覧と、送れなかった形の種類。
	issues, notes, issueNotes []map[string]any
	user                      string
	// lostResponse の種類は、保存してから 500 を返す（送れたのに応答だけ失敗した形）。reject の種類は、保存せずに 500 を返す。
	lostResponse, reject map[string]bool
}

type thread struct {
	id       string
	resolved bool
}

func newGitlab(t *testing.T) *gitlab {
	g := &gitlab{threads: []*thread{{id: "d1"}}, failResolveIDs: map[string]bool{}, user: "me", lostResponse: map[string]bool{}, reject: map[string]bool{}}
	g.srv = httptest.NewServer(g)
	t.Cleanup(g.srv.Close)
	return g
}

func (g *gitlab) url() string { return g.srv.URL }

func (g *gitlab) reply(w http.ResponseWriter, code int, payload any) {
	body, _ := json.Marshal(payload)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_, _ = w.Write(body)
}

func (g *gitlab) threadList() []any {
	out := []any{}
	for i, th := range g.threads {
		out = append(out, map[string]any{"id": th.id, "notes": []any{map[string]any{
			"id": 100 + i, "resolvable": true, "resolved": th.resolved, "body": "指摘", "created_at": "2026-09-23T00:00:00Z",
			"author": map[string]any{"id": 5}, "position": map[string]any{"new_path": "src/a.py", "new_line": 3},
		}}})
	}
	return out
}

func (g *gitlab) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	g.mu.Lock()
	defer g.mu.Unlock()
	path := r.URL.RequestURI()
	var data map[string]any
	if r.Method != http.MethodGet {
		_ = json.NewDecoder(r.Body).Decode(&data)
		if data == nil {
			data = map[string]any{}
		}
	}
	switch r.Method {
	case http.MethodGet:
		g.get(w, path)
	case http.MethodPut:
		g.put(w, path, data)
	case http.MethodPost:
		g.post(w, path, data)
	default:
		g.reply(w, 405, map[string]any{"message": "405"})
	}
}

func (g *gitlab) get(w http.ResponseWriter, path string) {
	if strings.HasSuffix(strings.SplitN(path, "?", 2)[0], "/user") {
		g.reply(w, 200, map[string]any{"username": g.user})
		return
	}
	page1 := strings.Contains(path, "page=1")
	pick := func(list []map[string]any) any {
		if page1 {
			return append([]map[string]any{}, list...)
		}
		return []any{}
	}
	if strings.Contains(path, "per_page") {
		switch {
		case strings.Contains(path, "/merge_requests/1/notes"):
			g.reply(w, 200, pick(g.notes))
			return
		case strings.Contains(path, "/issues/1/notes"):
			g.reply(w, 200, pick(g.issueNotes))
			return
		case strings.Contains(path, "/issues?"):
			g.reply(w, 200, pick(g.issues))
			return
		}
	}
	switch {
	case strings.Contains(path, "/discussions") && strings.Contains(path, "per_page"):
		if page1 {
			g.fetches++
			g.reply(w, 200, g.threadList())
		} else {
			g.reply(w, 200, []any{})
		}
	case strings.Contains(path, "/merge_requests?"):
		g.reply(w, 200, []any{map[string]any{"iid": 1, "web_url": g.mrURL(), "source_project_id": 1}})
	case !strings.Contains(path, "/merge_requests") && !strings.Contains(path, "per_page"):
		g.reply(w, 200, map[string]any{"id": 1})
	default:
		g.reply(w, 200, []any{})
	}
}

func (g *gitlab) mrURL() string { return g.srv.URL + "/demo/greeter/-/merge_requests/1" }

func (g *gitlab) put(w http.ResponseWriter, path string, data map[string]any) {
	if !strings.Contains(path, "/discussions/") {
		g.reply(w, 405, map[string]any{"message": "405"})
		return
	}
	id := path[strings.LastIndex(path, "/")+1:]
	if g.failResolve || g.failResolveIDs[id] {
		g.reply(w, 500, map[string]any{"message": "500 boom"})
		return
	}
	g.posted = append(g.posted, post{"resolve", merge(map[string]any{"path": path}, data)})
	for _, th := range g.threads {
		if th.id == id {
			th.resolved = true
		}
	}
	g.reply(w, 200, map[string]any{"id": id})
}

func merge(a, b map[string]any) map[string]any {
	for k, v := range b {
		a[k] = v
	}
	return a
}

func (g *gitlab) post(w http.ResponseWriter, path string, data map[string]any) {
	if strings.Contains(path, "/discussions/") { // 返信
		if !strings.HasSuffix(path, "/notes") {
			g.reply(w, 405, map[string]any{"message": "405"})
			return
		}
		if g.failReply {
			g.reply(w, 500, map[string]any{"message": "500 boom"})
			return
		}
		g.posted = append(g.posted, post{"reply", merge(map[string]any{"path": path}, data)})
		g.reply(w, 201, map[string]any{"id": 11})
		return
	}
	item := func(url string, id int) map[string]any {
		return map[string]any{"id": id, "iid": id, "web_url": url, "body": data["body"], "description": data["description"], "author": map[string]any{"username": g.user}}
	}
	var (
		kind  string
		store *[]map[string]any
		entry map[string]any
		reply map[string]any
	)
	switch {
	case strings.HasSuffix(path, "/issues"):
		kind, store = "issue", &g.issues
		url := g.srv.URL + "/demo/greeter/-/issues/1"
		entry, reply = item(url, 1), map[string]any{"iid": 1, "web_url": url}
	case strings.HasSuffix(path, "/issues/1/notes"):
		kind, store = "issue-note", &g.issueNotes
		entry = item(fmt.Sprintf("%s#note_%d", path, len(*store)+20), len(*store)+20)
		reply = map[string]any{"id": entry["id"]}
	case strings.HasSuffix(path, "/notes"):
		kind, store = "note", &g.notes
		entry = item(fmt.Sprintf("%s#note_%d", path, len(*store)+30), len(*store)+30)
		reply = map[string]any{"id": entry["id"], "created_at": "2026-09-23T00:00:00Z"}
	default:
		g.reply(w, 405, map[string]any{"message": "405"})
		return
	}
	if g.reject[kind] {
		g.reply(w, 500, map[string]any{"message": "500 boom"})
		return
	}
	*store = append(*store, entry)
	g.posted = append(g.posted, post{kind, data})
	if g.lostResponse[kind] {
		g.reply(w, 500, map[string]any{"message": "500 lost"})
		return
	}
	g.reply(w, 201, reply)
}

// 試験の側からの読み書き（サーバのゴルーチンと同じロックで守る）。

func (g *gitlab) do(f func()) { g.mu.Lock(); defer g.mu.Unlock(); f() }

// kinds は書き込みの種類を受けた順に。
func (g *gitlab) kinds() []string {
	g.mu.Lock()
	defer g.mu.Unlock()
	var out []string
	for _, p := range g.posted {
		out = append(out, p.kind)
	}
	return out
}

// postedOf は種類 kind の書き込みの本文（受けた順）。
func (g *gitlab) postedOf(kind string) []map[string]any {
	g.mu.Lock()
	defer g.mu.Unlock()
	var out []map[string]any
	for _, p := range g.posted {
		if p.kind == kind {
			out = append(out, p.data)
		}
	}
	return out
}

func (g *gitlab) count(kind string) int { return len(g.postedOf(kind)) }

func (g *gitlab) fetched() int { g.mu.Lock(); defer g.mu.Unlock(); return g.fetches }

func (g *gitlab) stored() (issues, notes []map[string]any) {
	g.mu.Lock()
	defer g.mu.Unlock()
	return append([]map[string]any{}, g.issues...), append([]map[string]any{}, g.notes...)
}

func (g *gitlab) resolved(id string) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	for _, th := range g.threads {
		if th.id == id {
			return th.resolved
		}
	}
	return false
}

// decideStub は実行ファイルの代わりの sh。`ops …`・計画の検べは本物に渡す。`--preview` は決まった一覧を返し、
// `STUB_PLAN` があればその計画を使う。`--accept-plan` は本物の操作記録に判断を保存して計画を書き、`--yes` は計画の操作ID に
// 「適用した」を記録する。`--check` は `STUB_FAIL_CHECK` があれば断る。ただし `--entry` の並びは、本物と同じ検べ方
// （teststub の userform）で先に検べる（`--entry sh --check user …` のような、本物が断る並びを通さない）。
// `STUB_MISMATCH` があれば、`--yes` は「ホストに書いたあとの照合が外れた」形で断る。
const decideStub = `#!/bin/sh
case " $* " in
*" --version "*) printf 'compat: @COMPAT@\n'; exit 0 ;;
*" write-and-push family "*) printf 'write-and-push 1\nfamily i0001\ntarget no\n'; exit 0 ;;
*" ops "* | *" host "*) exec "$STUB_EXE" "$@" ;;
*" accept plan "* | *" accept threads "* | *" accept decided "*) exec "$STUB_EXE" "$@" ;;
esac
printf '%s\n' "$*" >>"$STUB_LOG"
root="$2"
case " $* " in
*" --check "*)
  "$STUB_TEST" userform "$@" || exit $?
  [ -z "$STUB_FAIL_CHECK" ] || { echo "断った" >&2; exit 1; }; exit 0 ;;
*" --preview "*) printf '{"version":1,"threads":[{"key":"u1"}],"digest":"d0"}\n' ;;
*" --accept-plan "*)
  plan=""; prev=""
  for a in "$@"; do [ "$prev" = --accept-plan ] && plan="$a"; prev="$a"; done
  if [ -n "$STUB_PLAN" ]; then cp "$STUB_PLAN" "$plan"; else
    "$STUB_TEST" oprecord "$root/logs/state" "$plan" "$STUB_BEGIN"
  fi ;;
*" --yes "*)
  [ -z "$STUB_FAIL_YES" ] || { echo "置けない" >&2; exit 1; }
  done_plan=""; prev=""
  for a in "$@"; do [ "$prev" = --accept-done ] && done_plan="$a"; prev="$a"; done
  op=$(sed -n 's/.*"op": "\([0-9a-f]*\)".*/\1/p' "$done_plan")
  if [ -n "$STUB_MISMATCH" ]; then
    "$STUB_EXE" --root "$root" ops applied "$op" accepted_on_host u1
    printf '{"version":2,"ok":false,"mismatch":true,"changed":["u1"]}\n'
    echo "ccnavi: 見せた指摘と今の指摘が違う。何も置いていない" >&2
    exit 1
  fi
  [ -z "$op" ] || "$STUB_EXE" --root "$root" ops applied "$op" local true
  printf '{"version":2,"ok":true,"reviewed":true,"followup":""}\n' ;;
esac
`

// 実行ファイルの代わりの sh が `--accept-plan` で書く判断（操作記録）と計画。
func decideBegin(mrURL string) map[string]any {
	return beginSpec(map[string]any{
		"kind": "decide", "repo": map[string]any{"host": "gitlab", "mr_url": mrURL}, "parent": "i0001", "phase": 1, "mr": 1,
		"shown":   []any{map[string]any{"key": "u1", "id": "d1", "url": "u1", "path": "a.py", "line": 3, "version": "v1", "body": "指摘の本文\n```py\nx\n```"}},
		"choices": map[string]any{"u1": "issue"},
		"plan":    map[string]any{"digest": "d0"},
		"stages":  [][]string{{"issue", "issue"}, {"reply", "reply"}, {"note", "comment"}},
	}, planOf("d0", map[string]any{"id": "d1", "key": "u1", "reply": acceptMarker + "\nissue: {issue_url}\n"}))
}

// 子の PATH に並べる標準コマンド。PATH は偽の道具の bin とシェル本体の置き場だけで組む。sh が使うコマンドはここに足す。
// macOS は awk などが /bin にないが、/usr/bin を足すと gh と glab も見つかってしまう。
var standardTools = []string{
	"jq", "curl", "git", "sed", "cat", "rm", "dirname", "printf", "mkdir", "tr", "mktemp", "head", "date", "grep", "wc", "uname",
	"cp", "mv", "awk", "tail", "sleep", "rmdir", "basename", "env", "xargs",
	"find", // Windows の PATH では System32 の find.exe（文字列検索）に解けてしまうので、下で載せない
}

// putTool は found の道具を link の名前で PATH に置く。POSIX ではシンボリックリンク。Windows はリンクを張る権限が無いことが多く、
// コピーやハードリンクにすると mingw64 の git / curl が隣の DLL を見つけられず起動しない。だから `exec` で実物へ渡す sh を置く。
func putTool(t testing.TB, found, link string) {
	t.Helper()
	if runtime.GOOS != "windows" {
		if err := os.Symlink(found, link); err != nil {
			t.Fatal(err)
		}
		return
	}
	shkit.WriteExec(t, link, "#!/bin/sh\nexec "+shq(found)+" \"$@\"\n")
}

// decideSite は decide の試験の木。ws は git のリポジトリではなく、その下の tree が i0001 の作業ツリー（origin は偽のホスト）。
type decideSite struct {
	*site
	gl           *gitlab
	tree         string
	state        string
	stub, log    string
	begin        string // `--accept-plan` のスタブが読む判断の JSON
	tools        string
	shell        string
	mrURL        string
	teststubPath string
}

func newDecideSite(t *testing.T) *decideSite {
	t.Helper()
	s := newBareSite(t, reviewScripts...)
	shkit.Need(t, "curl")
	d := &decideSite{site: s, gl: newGitlab(t), shell: shellPath(t)}
	d.state = filepath.Join(s.ws, "logs", "state")
	if err := os.MkdirAll(d.state, 0o755); err != nil {
		t.Fatal(err)
	}
	d.tree = filepath.Join(s.ws, "tree")
	s.initRepo(d.tree, "i0001", d.gl.url()+"/demo/greeter.git")
	d.stub, d.log = filepath.Join(s.tmp, "stub-ccnavi"), filepath.Join(s.tmp, "stub.log")
	d.mrURL = d.gl.mrURL()
	d.begin = filepath.Join(s.tmp, "begin_op.json")
	data, err := json.Marshal(decideBegin(d.mrURL))
	if err != nil {
		t.Fatal(err)
	}
	shkit.Write(t, d.begin, string(data))
	shkit.WriteExec(t, d.stub, strings.ReplaceAll(decideStub, "@COMPAT@", exe.Compat(t)))
	d.tools = filepath.Join(s.tmp, "bin")
	d.teststubPath = exe.TeststubPath(t)
	return d
}

// putTools は標準コマンドを tools に置く（既にあるものは置かない）。
func (d *decideSite) putTools() {
	d.t.Helper()
	if err := os.MkdirAll(d.tools, 0o755); err != nil {
		d.t.Fatal(err)
	}
	for _, name := range standardTools {
		if name == "find" && runtime.GOOS == "windows" {
			continue
		}
		found, err := exec.LookPath(name)
		if err != nil {
			if name != "find" {
				d.t.Fatalf("標準の道具 %s が PATH に無い", name)
			}
			continue
		}
		if shkit.Exists(filepath.Join(d.tools, name)) {
			continue
		}
		putTool(d.t, found, filepath.Join(d.tools, name))
	}
}

// dOpts は decide の 1 回の起こし方の差し替え。
type dOpts struct {
	shell  string // 空なら sh
	script string // 空なら ccnavi-user-decide.sh。ccnavi-review.sh なら `decide` を足して起こす
	input  string
	cwd    string // 空なら tree
	env    map[string]string
}

// decide は ccnavi-user-decide.sh（o.script が ccnavi-review.sh なら ccnavi-review.sh decide）を打つ。
func (d *decideSite) decide(o dOpts, args ...string) run.Result {
	d.t.Helper()
	d.putTools()
	shell := o.shell
	if shell == "" {
		shell = d.shell
	}
	script := o.script
	if script == "" {
		script = userDecideSh
	}
	env := map[string]string{
		"CCNAVI_WORKSPACE": d.ws, "CCNAVI_ENTRY_PATH": d.stub, "GITLAB_TOKEN": "t0k",
		"STUB_EXE": exe.ShPath(d.t), "STUB_TEST": filepath.ToSlash(d.teststubPath), "STUB_BEGIN": d.begin, "STUB_LOG": d.log,
		"STUB_PLAN": "", "STUB_FAIL_YES": "", "STUB_FAIL_CHECK": "", "STUB_MISMATCH": "",
		"PATH": d.tools + string(os.PathListSeparator) + filepath.Dir(shell),
	}
	for k, v := range o.env {
		env[k] = v
	}
	cwd := o.cwd
	if cwd == "" {
		cwd = d.tree
	}
	cmd := []string{filepath.Join(d.scripts, script)}
	if script == reviewSh {
		cmd = append(cmd, "decide")
	}
	return run.Command(d.t, shell, append(cmd, args...), run.Opts{Dir: cwd, Env: env, Input: o.input})
}

// leftovers は state に残ったファイル（操作記録の置き場 ops・locks は数えない）。
func (d *decideSite) leftovers() []string {
	d.t.Helper()
	entries, _ := os.ReadDir(d.state)
	var out []string
	for _, e := range entries {
		if e.Name() != "ops" && e.Name() != "locks" {
			out = append(out, e.Name())
		}
	}
	return out
}

func (d *decideSite) allCalls() []string { return lines(readIf(d.log)) }

// calls は置く・計画の段階の呼び出し。事前の検査（`--check`）は allCalls にだけ入る。
func (d *decideSite) calls() []string {
	var out []string
	for _, c := range d.allCalls() {
		if !strings.Contains(" "+c+" ", " --check ") {
			out = append(out, c)
		}
	}
	return out
}

// planFile は、実行ファイルが `--accept-plan` に書くのと同じ形の計画。op が nil なら、実行ファイルと同じく本物の操作記録を
// 作って、その操作ID を計画に書く。形の書き手と読み手の往復は go test（host の TestAcceptPlanRoundTrip）が見る。
func (d *decideSite) planFile(op *string, ids ...string) string {
	d.t.Helper()
	made := filepath.Join(d.tmp, "plan-op.json")
	id := ""
	if op == nil {
		id = writePlan(d.t, d.state, made, decideBegin(d.mrURL))
	} else {
		id = *op
	}
	threads := []any{}
	for _, i := range ids {
		threads = append(threads, map[string]any{"id": i, "key": "u/" + i, "reply": acceptMarker + "\nテスト\n"})
	}
	body, err := json.Marshal(map[string]any{"version": 1, "digest": "d0", "op": id, "threads": threads})
	if err != nil {
		d.t.Fatal(err)
	}
	return shkit.Write(d.t, filepath.Join(d.tmp, "plan-in.json"), string(body)+"\n")
}

// answer は標準出力の最後の行（JSON の答え）。
func answerOf(t testing.TB, out string) map[string]any {
	t.Helper()
	ls := lines(strings.TrimSpace(out))
	if len(ls) == 0 {
		t.Fatal("標準出力が空")
	}
	// 一部だけ通ったときは、JSON の後ろに 通ったもの・残り・次 が続く。答えは最後の JSON の行。
	for i := len(ls) - 1; i >= 0; i-- {
		if strings.HasPrefix(ls[i], "{") {
			return decode(t, ls[i])
		}
	}
	return decode(t, ls[len(ls)-1])
}
