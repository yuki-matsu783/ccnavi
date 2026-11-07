package events

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/testkit"
)

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

// skipped は見本に入れないもの（手順で動かない大きい置き場・リポジトリから写した設定と CCNAVI.md・書く形が
// 決まっていない診断ログ）。リポジトリから写したものを入れると、そちらを直すたびに見本が落ちる。
var skipped = []string{".ccnavi/messages", ".ccnavi/scripts", ".ccnavi/config/common", ".ccnavi/CCNAVI.md", "remote.git",
	"logs/diag", "logs/decisions.jsonl"}

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
	conf.GuardTicketApproval = string(selfguard.Resolve(&stderr, "", conf.GuardTicketApproval, env.GuardTicketApproval, selfguard.GateChoices()))
	conf.TicketControl = string(selfguard.Resolve(&stderr, "", conf.TicketControl, env.TicketControl, selfguard.GateChoices()))
	mode := config.ResolveMode(&stderr, "", conf)
	conf.RestoreIfDeny = string(selfguard.Resolve(&stderr, "", conf.RestoreIfDeny, env.RestoreIfDeny, selfguard.Choices()))
	conf.GuardCoreFiles = string(selfguard.Resolve(&stderr, "", conf.GuardCoreFiles, env.GuardCoreFiles, selfguard.Choices()))
	conf.GuardUnwatched = string(selfguard.Resolve(&stderr, "", conf.GuardUnwatched, env.GuardUnwatched, selfguard.GateChoices()))
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

// 時刻・日時・時刻から作る名前は、回すたびに違うので印に置き換える。
var (
	unixTime = regexp.MustCompile(`\b1[789]\d{8}(\d{3})?(\.\d+)?\b`)
	isoTime  = regexp.MustCompile(`\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)?`)
	stamped  = regexp.MustCompile(`\d{8}[-T]?\d{6}(\d*)`)
	// 止めた回の鍵（ルール id と対象の要約）。対象はルートを含むので、要約は回すたびに変わる。
	deniedKey = regexp.MustCompile(`(\S) [0-9a-f]{16}\b`)
)

func normalize(s, root string) string {
	s = strings.ReplaceAll(strings.ReplaceAll(s, jsonInner(root), rootMark), root, rootMark)
	// 記録の鍵はツリーのルートを畳んで持つ。
	folded := pathcase.Fold(root)
	s = strings.ReplaceAll(strings.ReplaceAll(s, jsonInner(folded), rootMark), folded, rootMark)
	s = isoTime.ReplaceAllString(s, "<time>")
	s = stamped.ReplaceAllString(s, "<stamp>")
	s = deniedKey.ReplaceAllString(s, "$1 <digest>")
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

// compareScenario はワークスペースを作って手順を順に流し、手順ごとの答え（ルートと時刻を置き換えたもの）を、Go の答えの
// 見本（答えが確かめ済みだった Go の出力から取った）と比べる。
func compareScenario(t *testing.T, build func(t *testing.T, root string), steps []step) {
	t.Helper()
	root := filepath.Join(testkit.GitRoot(t), "ws")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	build(t, root)
	var seen []answer
	for _, s := range steps {
		seen = append(seen, runGo(t, root, s.at(root)).normalized(root))
	}
	golden.Check(t, strings.TrimPrefix(t.Name(), "Test"), seen)
}

// heavyScenario は git を何度も起こす場面の前置き（-short と git の無い機械では飛ばす）。
func heavyScenario(t *testing.T) {
	t.Helper()
	if testing.Short() {
		t.Skip("git を何度も起こす場面は -short では回さない")
	}
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
}
