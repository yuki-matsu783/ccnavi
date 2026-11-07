package events

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/testkit"
)

// eventsOracle は Python の hook の経路（cli の設定の解き方と events.decide と記録）を、場面の手順の順に同じプロセスで
// 回す。手順ごとに、ファイルの書き換えと git を先に行い、環境変数を替え、Stop の AI 判定の記録の結び（プロセスに
// 1 つの大域）を空に戻す（hook は 1 回の起動が 1 プロセスなので）。答えは標準出力・終了コード・標準エラー・記録の
// 1 行・ワークスペースのファイル。
const eventsOracle = `
import io, json, os, subprocess, sys, time
from ccnavi.hook import events
from ccnavi.infra import hookio, modes, settings
from ccnavi.policy import selfguard, subjects
from ccnavi.records import audit, stopreview
from ccnavi.tickets.model import via
c = json.load(sys.stdin)
root = c["root"]
base = {k: v for k, v in os.environ.items() if not k.startswith(("CCNAVI_", "CLAUDE_"))}
git_env = dict(base, **c["git_env"])
skip = set(c["skip"])
def snapshot():
    out = {}
    for d, dirs, files in os.walk(root):
        rel = os.path.relpath(d, root).replace(os.sep, "/")
        dirs[:] = [x for x in dirs if (x if rel == "." else rel + "/" + x) not in skip and x != ".git"]
        for f in files:
            p = os.path.join(d, f)
            key = f if rel == "." else rel + "/" + f
            if f == ".git" or key in skip:
                continue
            with open(p, "rb") as h:
                out[key] = h.read().decode("utf-8", "replace")
    return out
out = []
for case in c["steps"]:
    for rel, text in (case.get("files") or {}).items():
        p = os.path.join(root, rel)
        if text is None:
            if os.path.exists(p):
                os.remove(p)
        else:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as h:
                h.write(text.replace("{NOW}", str(int(time.time()))))
    for g in case.get("git") or []:
        subprocess.run(["git"] + g["args"], cwd=os.path.join(root, g["dir"]), env=git_env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.environ.clear()
    os.environ.update(base)
    os.environ.update(case["env"])
    stopreview._LOG_STATE.clear()
    err, stdout = io.StringIO(), io.StringIO()
    answer = {"code": 0, "stdout": "", "stderr": "", "record": None, "error": ""}
    try:
        conf, _ = settings.load(root)
        conf.guard_ticket_approval = selfguard.resolve(err, "", conf.guard_ticket_approval,
            settings.GUARD_TICKET_APPROVAL_ENV, selfguard.GATE_SETTINGS)
        conf.ticket_control = selfguard.resolve(err, "", conf.ticket_control,
            settings.TICKET_CONTROL_ENV, selfguard.GATE_SETTINGS)
        mode = modes.resolve_mode(err, None, conf)
        conf.restore_if_deny = selfguard.resolve(err, "", conf.restore_if_deny, settings.RESTORE_IF_DENY_ENV)
        conf.guard_core_files = selfguard.resolve(err, "", conf.guard_core_files, settings.GUARD_CORE_FILES_ENV)
        conf.guard_unwatched = selfguard.resolve(err, "", conf.guard_unwatched,
            settings.GUARD_UNWATCHED_ENV, selfguard.GATE_SETTINGS)
        payload = hookio.decode(io.StringIO(case["payload"]))
        record = audit.Record(mode=mode, permission_mode=payload.permission_mode, event=payload.event,
            tool=payload.tool_name, session=payload.session_id, agent=payload.agent_id)
        record.subject = subjects.subject_of(payload)
        with via.session(via.VIA_HOOK):
            answer["code"] = events.decide(stdout, err, mode, conf, root, payload, record, time.monotonic() + 30)
        audit.Log(conf.log).write(record)
        with open(conf.log, encoding="utf-8") as f:
            answer["record"] = json.loads(f.read().splitlines()[-1])
    except Exception as exc:
        answer["error"] = type(exc).__name__ + ": " + str(exc)
    answer["stdout"], answer["stderr"] = stdout.getvalue(), err.getvalue()
    answer["files"] = snapshot()
    out.append(answer)
print(json.dumps(out, ensure_ascii=False))
`

// deadline は突き合わせで渡す判定の期限。git を何度も起こす手順があるので、期限で答えが分かれないよう長く取る。
const deadline = 30 * time.Second

// step は場面の 1 手順。Files は先に書くファイル（ワークスペースからの相対。nil の値は消す。中身の {NOW} は書く時の
// UNIX 秒）、Git は先に打つ git。
// Payload・Env・Files の値の {ROOT} は、回すワークスペースのルートに置き換える。
type step struct {
	Name    string             `json:"name"`
	Payload string             `json:"payload"`
	Env     map[string]string  `json:"env"`
	Files   map[string]*string `json:"files,omitempty"`
	Git     []gitCall          `json:"git,omitempty"`
}

type gitCall struct {
	Dir  string   `json:"dir"`
	Args []string `json:"args"`
}

type answer struct {
	Code   int               `json:"code"`
	Stdout string            `json:"stdout"`
	Stderr string            `json:"stderr"`
	Record map[string]any    `json:"record"`
	Error  string            `json:"error"`
	Files  map[string]string `json:"files"`
}

const rootMark = "{ROOT}"

// nowMark は手順の前に書くファイルの中で、書く時の UNIX 秒に置き換える印。
const nowMark = "{NOW}"

// skipped は写しを比べないもの（手順で動かない大きい置き場と、Go と Python で書く形が決まっていない診断ログ）。
var skipped = []string{".ccnavi/messages", ".ccnavi/scripts", "remote.git", "logs/diag", "logs/decisions.jsonl"}

func jsonInner(s string) string {
	b, _ := json.Marshal(s)
	return string(b[1 : len(b)-1])
}

func (s step) at(root string) step {
	envs := map[string]string{}
	for k, v := range s.Env {
		envs[k] = strings.ReplaceAll(v, rootMark, root)
	}
	files := map[string]*string{}
	for k, v := range s.Files {
		if v == nil {
			files[k] = nil
			continue
		}
		text := strings.ReplaceAll(*v, rootMark, root)
		files[k] = &text
	}
	return step{Name: s.Name, Payload: strings.ReplaceAll(s.Payload, rootMark, jsonInner(root)), Env: envs, Files: files, Git: s.Git}
}

func setEnv(values map[string]string) func() {
	saved := os.Environ()
	for _, kv := range saved {
		name, _, _ := strings.Cut(kv, "=")
		if strings.HasPrefix(name, "CCNAVI_") || strings.HasPrefix(name, "CLAUDE_") {
			os.Unsetenv(name)
		}
	}
	for k, v := range values {
		os.Setenv(k, v)
	}
	return func() {
		os.Clearenv()
		for _, kv := range saved {
			name, value, _ := strings.Cut(kv, "=")
			os.Setenv(name, value)
		}
	}
}

// prepare は手順の前のファイルの書き換えと git を行う。
func prepare(t *testing.T, root string, s step) {
	t.Helper()
	for rel, text := range s.Files {
		path := filepath.Join(root, filepath.FromSlash(rel))
		if text == nil {
			_ = os.Remove(path)
			continue
		}
		testkit.Put(t, path, strings.ReplaceAll(*text, nowMark, strconv.FormatInt(time.Now().Unix(), 10)))
	}
	for _, g := range s.Git {
		testkit.Git(t, filepath.Join(root, filepath.FromSlash(g.Dir)), g.Args...)
	}
}

// runGo は eventsOracle と同じ手順を Go で回す。
func runGo(t *testing.T, root string, s step) answer {
	t.Helper()
	prepare(t, root, s)
	restore := setEnv(s.Env)
	defer restore()
	var stdout, stderr bytes.Buffer
	got := answer{}
	conf, _ := config.Load(root)
	conf.GuardTicketApproval = string(selfguard.Resolve(&stderr, "", conf.GuardTicketApproval, env.GuardTicketApproval, selfguard.GateChoices))
	conf.TicketControl = string(selfguard.Resolve(&stderr, "", conf.TicketControl, env.TicketControl, selfguard.GateChoices))
	mode := config.ResolveMode(&stderr, "", conf)
	conf.RestoreIfDeny = string(selfguard.Resolve(&stderr, "", conf.RestoreIfDeny, env.RestoreIfDeny, selfguard.Choices))
	conf.GuardCoreFiles = string(selfguard.Resolve(&stderr, "", conf.GuardCoreFiles, env.GuardCoreFiles, selfguard.Choices))
	conf.GuardUnwatched = string(selfguard.Resolve(&stderr, "", conf.GuardUnwatched, env.GuardUnwatched, selfguard.GateChoices))
	in, err := hookio.Decode(strings.NewReader(s.Payload))
	if err != nil {
		got.Error = err.Error()
		return got
	}
	record := &audit.Record{Mode: mode, PermissionMode: in.PermissionMode, Event: string(in.Event), Tool: in.ToolName,
		Session: in.SessionID, Agent: in.AgentID}
	record.Subject = rules.SubjectOf(in)
	back := via.Session(via.Hook, "")
	code, err := Decide(&stdout, &stderr, mode, conf, root, in, record, time.Now().Add(deadline))
	back()
	got.Code = code
	if err != nil {
		got.Error = err.Error()
	} else if err := audit.NewLog(conf.Log).Write(record); err != nil {
		got.Error = err.Error()
	} else {
		got.Record = lastRecord(t, conf.Log)
	}
	got.Stdout, got.Stderr = stdout.String(), stderr.String()
	got.Files = snapshot(t, root)
	return got
}

func snapshot(t *testing.T, root string) map[string]string {
	t.Helper()
	out := map[string]string{}
	skip := map[string]bool{}
	for _, s := range skipped {
		skip[s] = true
	}
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(root, path)
		rel = filepath.ToSlash(rel)
		if d.IsDir() {
			if rel != "." && (d.Name() == ".git" || skip[rel]) {
				return filepath.SkipDir
			}
			return nil
		}
		if d.Name() == ".git" || skip[rel] {
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		out[rel] = strings.ToValidUTF8(string(data), "\uFFFD")
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func lastRecord(t *testing.T, path string) map[string]any {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var last string
	s := bufio.NewScanner(f)
	s.Buffer(make([]byte, 1<<20), 1<<26)
	for s.Scan() {
		last = s.Text()
	}
	var row map[string]any
	if err := json.Unmarshal([]byte(last), &row); err != nil {
		t.Fatal(err)
	}
	return row
}

// 時刻・日時・時刻から作る名前は、2 つのワークスペースで違うので印に置き換える。
var (
	unixTime = regexp.MustCompile(`\b1[789]\d{8}(\d{3})?(\.\d+)?\b`)
	isoTime  = regexp.MustCompile(`\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)?`)
	stamped  = regexp.MustCompile(`\d{8}[-T]?\d{6}(\d*)`)
)

func normalize(s, root string) string {
	s = strings.ReplaceAll(strings.ReplaceAll(s, jsonInner(root), rootMark), root, rootMark)
	s = isoTime.ReplaceAllString(s, "<time>")
	s = stamped.ReplaceAllString(s, "<stamp>")
	return unixTime.ReplaceAllString(s, "<t>")
}

// normalized はルートを {ROOT} に戻し、時刻と経過時間を落とした答え。
func (a answer) normalized(root string) answer {
	fix := func(s string) string { return normalize(s, root) }
	out := answer{Code: a.Code, Stdout: fix(a.Stdout), Stderr: fix(a.Stderr), Error: a.Error, Files: map[string]string{}}
	if a.Error != "" {
		out.Error = "error"
	}
	if a.Record != nil {
		data, _ := json.Marshal(a.Record)
		var row map[string]any
		_ = json.Unmarshal([]byte(fix(string(data))), &row)
		delete(row, "ts")
		delete(row, "ms")
		out.Record = row
	}
	for k, v := range a.Files {
		out.Files[fix(k)] = fix(v)
	}
	return out
}

func answerDiff(got, want answer) string {
	var b strings.Builder
	if got.Code != want.Code {
		fmt.Fprintf(&b, "code: Go %d, Python %d\n", got.Code, want.Code)
	}
	if got.Error != want.Error {
		fmt.Fprintf(&b, "error: Go %q, Python %q\n", got.Error, want.Error)
	}
	if got.Stdout != want.Stdout {
		fmt.Fprintf(&b, "stdout:\n Go:     %s\n Python: %s\n", got.Stdout, want.Stdout)
	}
	if got.Stderr != want.Stderr {
		fmt.Fprintf(&b, "stderr:\n Go:     %q\n Python: %q\n", got.Stderr, want.Stderr)
	}
	g, _ := json.Marshal(got.Record)
	w, _ := json.Marshal(want.Record)
	if !bytes.Equal(g, w) {
		fmt.Fprintf(&b, "record:\n Go:     %s\n Python: %s\n", g, w)
	}
	var names []string
	for k := range got.Files {
		names = append(names, k)
	}
	for k := range want.Files {
		if _, ok := got.Files[k]; !ok {
			names = append(names, k)
		}
	}
	sort.Strings(names)
	for _, k := range names {
		gv, gok := got.Files[k]
		wv, wok := want.Files[k]
		switch {
		case !gok:
			fmt.Fprintf(&b, "file %s: Python だけに在る\n %q\n", k, clip(wv))
		case !wok:
			fmt.Fprintf(&b, "file %s: Go だけに在る\n %q\n", k, clip(gv))
		case gv != wv:
			fmt.Fprintf(&b, "file %s:\n Go:     %q\n Python: %q\n", k, clip(gv), clip(wv))
		}
	}
	return b.String()
}

func clip(s string) string {
	if r := []rune(s); len(r) > 600 {
		return string(r[:600]) + "…"
	}
	return s
}

// compareScenario は同じ形のワークスペースを同じパスに 2 回作り、1 回目で Python、2 回目で Go に同じ手順を順に流して
// 手順ごとに突き合わせる（パスを同じにするのは、パスを含むコマンドの要約が記録の名前に入るため）。最初に食い違った
// 手順で止める（そのあとは state が分かれて食い違いが連なる）。
func compareScenario(t *testing.T, build func(t *testing.T, root string), steps []step) {
	t.Helper()
	base := testkit.GitRoot(t)
	root := filepath.Join(base, "ws")
	fresh := func() {
		if err := os.MkdirAll(root, 0o755); err != nil {
			t.Fatal(err)
		}
		build(t, root)
	}
	fresh()
	var pySteps []step
	for _, s := range steps {
		pySteps = append(pySteps, s.at(root))
	}
	gitEnv := map[string]string{}
	for _, kv := range testkit.GitEnv() {
		k, v, _ := strings.Cut(kv, "=")
		gitEnv[k] = v
	}
	py := testkit.Answers[[]answer](t, "", eventsOracle,
		map[string]any{"root": root, "steps": pySteps, "git_env": gitEnv, "skip": skipped})
	if len(py) != len(steps) {
		t.Fatalf("Python の答えが %d 件（%d 件を渡した）", len(py), len(steps))
	}
	if err := os.Rename(root, filepath.Join(base, "py")); err != nil {
		t.Fatal(err)
	}
	fresh()
	for i, s := range steps {
		want := py[i].normalized(root)
		got := runGo(t, root, s.at(root)).normalized(root)
		t.Logf("step %d %s: exit %d, %v %v", i, s.Name, got.Code, got.Record["decision"], got.Record["code"])
		if diff := answerDiff(got, want); diff != "" {
			t.Fatalf("step %d %s\npayload: %s\nenv: %v\n%s", i, s.Name, s.Payload, s.Env, diff)
		}
	}
}

// pythonReady は Python と突き合わせられるか（-short と Python の無い機械では飛ばす）。
func pythonReady(t *testing.T) {
	t.Helper()
	if testing.Short() {
		t.Skip("Python と突き合わせる場面は -short では回さない")
	}
	repo := testkit.RepoRoot(t)
	for _, rel := range []string{".venv/bin/python", ".venv/Scripts/python.exe"} {
		if _, err := os.Stat(filepath.Join(repo, rel)); err == nil {
			if _, err := exec.LookPath("git"); err != nil {
				t.Skip("git が無い")
			}
			return
		}
	}
	t.Skip("Python が無い")
}
