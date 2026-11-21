package repo

import (
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// 場面（tests/fixtures/stop_review/scenarios/<名前>/）を最初から再生する道具。Stop ごとに hook の足跡を作り、
// 計画・材料・判定結果を `ccnavi stop-review` に通す。実機のモデルは呼ばない。場面の置き方は stopreview_scenarios_test.go の doc。

const (
	// session は場面を再生するセッションの ID。
	session = "s1"
	// todoDir は Write で書かれた提案の置き場（リポジトリの根から）。
	todoDir = "wip/proposals/todo"
	// resultDir は approval-request の結果ファイルを置く場所（state の下）。
	resultDir = "stopops-result"
)

// scenesRel は場面データの置き場（リポジトリの根から）。
const scenesRel = "tests/fixtures/stop_review/scenarios"

// planState は計画（stopreview-plan-<鍵の sha1>.json）の、場面の確かめに使う欄。
type planState struct {
	AttemptID   string          `json:"attempt_id"`
	Key         string          `json:"key"`
	IsCandidate bool            `json:"is_candidate"`
	Effective   string          `json:"effective"`
	Targets     []string        `json:"targets"`
	Refs        []string        `json:"refs"`
	Proposals   []proposalState `json:"proposals"`
	NoR2        []string        `json:"no_r2"`
	Rejudge     bool            `json:"rejudge"`
	Original    string          `json:"original"`
}

type proposalState struct {
	Key   string `json:"key"`
	Ident string `json:"ident"`
	Route string `json:"route"`
}

// attemptState は試行（stopreview-attempt-<試行ID>.json）の、場面の確かめに使う欄。
type attemptState struct {
	AttemptID string `json:"attempt_id"`
	Key       string `json:"key"`
	State     string `json:"state"`
	Final     string `json:"final"`
	Judge     string `json:"judge"`
	Rejudge   string `json:"rejudge"`
}

// reply は `stop-review record` の答え。
type reply struct {
	OK     bool   `json:"ok"`
	Reason string `json:"reason"`
}

// play は場面を 1 回再生する状態。木は使い捨ての置き場に組み、state はその下の logs/state。
type play struct {
	t          *testing.T
	expected   scene
	dir        string // 場面の直下
	root       string
	state      string
	transcript string
	env        map[string]string
	stops      []string // Stop ごとの場面の置き場（1 回の Stop なら場面の直下 1 つ）
	prompted   int      // 足跡に入れたユーザの発言の数
	calls      int      // approval-request の呼び出しの数
	repo       bool     // 木を git のリポジトリにしたか
	errors     string   // 直前の Stop の hook の標準エラー
	key        string   // 直前の Stop の計画の鍵
}

// newPlay は場面を再生する状態を作る。settings.json があれば stop_review の値を、無ければ enable を使う。
func newPlay(t *testing.T, name string) *play {
	t.Helper()
	dir := filepath.Join(sceneDir(t), name)
	expected := loadJSON[scene](t, filepath.Join(dir, "expected.json"))
	root := base.TempDir(t)
	declared := "enable"
	if place := filepath.Join(dir, "settings.json"); fileExists(place) {
		declared = loadJSON[map[string]string](t, place)["stop_review"]
	}
	p := &play{
		t:          t,
		expected:   expected,
		dir:        dir,
		root:       root,
		state:      filepath.Join(root, "logs", "state"),
		transcript: filepath.Join(root, "t.jsonl"),
		env: map[string]string{
			env.Learning:        "disable",
			env.Mode:            "enable",
			env.StopReviewAgent: "1",
			env.StopReview:      declared,
		},
	}
	if err := os.MkdirAll(p.state, 0o755); err != nil {
		t.Fatal(err)
	}
	p.stops = stopFolders(t, dir)
	return p
}

// stopFolders は場面の Stop ごとの置き場。stop-1 があれば stop-* の並び（名前の順）、無ければ場面の直下 1 つ。
func stopFolders(t *testing.T, dir string) []string {
	t.Helper()
	if !dirExists(filepath.Join(dir, "stop-1")) {
		return []string{dir}
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "stop-") {
			out = append(out, filepath.Join(dir, e.Name()))
		}
	}
	return out
}

// ccnavi は `ccnavi --root <root> <args…>` を、stdin を渡して起こす。終了コードは 0 のはず。
func (p *play) ccnavi(args []string, stdin string) run.Result {
	p.t.Helper()
	r := run.Ccnavi(p.t, append([]string{"--root", p.root}, args...), run.Opts{Input: stdin, Dir: p.root, Env: p.env})
	if r.Code != 0 {
		p.t.Fatalf("ccnavi %v: code = %d, stdout = %s, stderr = %s", args, r.Code, r.Stdout, r.Stderr)
	}
	return r
}

// hook は hook の入力（hook_event_name・session_id・cwd と、場合で足す欄）を ccnavi に渡す。
func (p *play) hook(event string, fields map[string]any) run.Result {
	p.t.Helper()
	body := map[string]any{"hook_event_name": event, "session_id": session, "cwd": p.root}
	for k, v := range fields {
		body[k] = v
	}
	b, err := json.Marshal(body)
	if err != nil {
		p.t.Fatal(err)
	}
	return p.ccnavi(nil, string(b))
}

// git は木の中で git を 1 回起こす。署名と作者は場面の試験で決まった値にする。
func (p *play) git(args ...string) string {
	p.t.Helper()
	full := append([]string{"-C", p.root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"}, args...)
	return gitIn(p.t, p.root, nil, full...)
}

// commit は wip/ を空のコミットに載せ、その commit の ID を返す（approval-request の結果に入る）。
func (p *play) commit() string {
	p.t.Helper()
	if !p.repo {
		p.git("init", "-q")
		p.repo = true
	}
	p.git("add", "-A", "wip")
	p.git("commit", "-q", "--allow-empty", "-m", "approval-request")
	return p.git("rev-parse", "HEAD")
}

var (
	blobRef     = regexp.MustCompile(`\{blob:([^}]+)\}`)
	proposalID  = regexp.MustCompile(`(?m)^id=(.+)$`)
	proposalRef = regexp.MustCompile(`\$PROPOSAL:([\w.-]+)`)
)

// expand は結果ファイルの置き換え（{now}・{commit}・{blob:<パス>}）をする。
func (p *play) expand(text, commit string) string {
	text = strings.ReplaceAll(text, "{now}", strconv.FormatInt(time.Now().Unix(), 10))
	text = strings.ReplaceAll(text, "{commit}", commit)
	return blobRef.ReplaceAllStringFunc(text, func(m string) string {
		return p.git("rev-parse", "HEAD:"+blobRef.FindStringSubmatch(m)[1])
	})
}

// stage は、このStopまでに起きたこと（ユーザの発言・提案の書き込み・approval-request の実行）を再現する。
func (p *play) stage(folder string) {
	p.t.Helper()
	copyFile(p.t, filepath.Join(folder, "transcript.jsonl"), p.transcript)
	prompts := userPrompts(p.t, p.transcript)
	for _, text := range prompts[p.prompted:] {
		p.hook("UserPromptSubmit", map[string]any{"prompt": text})
	}
	p.prompted = len(prompts)

	drafts := filepath.Join(folder, "proposals")
	for _, name := range listNames(p.t, drafts) {
		ident := strings.TrimSuffix(name, filepath.Ext(name))
		path := filepath.Join(p.root, filepath.FromSlash(todoDir), ident+".md")
		copyFile(p.t, filepath.Join(drafts, name), path)
		p.hook("PostToolUse", map[string]any{
			"tool_name":   "Write",
			"tool_input":  map[string]any{"file_path": path},
			"tool_use_id": fmt.Sprintf("w%d-%s", p.calls, ident),
		})
	}

	kv := filepath.Join(folder, "propose.kv")
	if !fileExists(kv) {
		return
	}
	commit := p.commit()
	p.calls++
	toolUseID := fmt.Sprintf("tu%d", p.calls)
	body := p.expand(readText(p.t, kv), commit)
	m := proposalID.FindStringSubmatch(body)
	if m == nil {
		p.t.Fatalf("結果ファイルに id= が無い: %s", kv)
	}
	call := map[string]any{
		"tool_name":   "Bash",
		"tool_input":  map[string]any{"command": "sh .ccnavi/scripts/ccnavi-ticket.sh approval-request " + m[1]},
		"tool_use_id": toolUseID,
	}
	p.hook("PreToolUse", call)
	dir := filepath.Join(p.state, resultDir)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		p.t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, toolUseID+".kv"), []byte(body), 0o644); err != nil {
		p.t.Fatal(err)
	}
	after := map[string]any{"tool_response": map[string]any{}}
	for k, v := range call {
		after[k] = v
	}
	p.hook("PostToolUse", after)
}

// plan は Stop の hook を起こし、その Stop の計画を読む（無ければ nil）。
func (p *play) plan(continued bool) *planState {
	p.t.Helper()
	r := p.hook("Stop", map[string]any{"transcript_path": p.transcript, "stop_hook_active": continued})
	p.errors = r.Stderr
	p.key = session + ":" + lastAssistantUUID(p.t, p.transcript)
	sum := sha1.Sum([]byte(p.key))
	digest := hex.EncodeToString(sum[:])[:16]
	return readState[planState](p.t, p.state, "stopreview-plan-"+digest+".json")
}

// material は `stop-review material` の標準出力（判定役に渡る材料）。
func (p *play) material() string {
	p.t.Helper()
	return p.ccnavi([]string{"stop-review", "material", session, p.transcript}, "").Stdout
}

// record は判定結果を `stop-review record` に渡し、答えを読む。
func (p *play) record(resultText string) reply {
	p.t.Helper()
	out := p.ccnavi([]string{"stop-review", "record", session, p.transcript}, resultText).Stdout
	var r reply
	if err := json.Unmarshal([]byte(out), &r); err != nil {
		p.t.Fatalf("record の答えを読めない: %v\n%s", err, out)
	}
	return r
}

// judgeText は判定結果の雛形に、試行 ID と提案の対象キーを入れる。
func (p *play) judgeText(plan *planState, text string) string {
	p.t.Helper()
	keys := map[string]string{}
	for _, prop := range plan.Proposals {
		keys[prop.Ident] = prop.Key
	}
	text = strings.ReplaceAll(text, "$ATTEMPT", plan.AttemptID)
	return proposalRef.ReplaceAllStringFunc(text, func(m string) string {
		ident := proposalRef.FindStringSubmatch(m)[1]
		key, ok := keys[ident]
		if !ok {
			p.t.Fatalf("提案 %s の対象キーが計画に無い", ident)
		}
		return key
	})
}

// attempt は計画の試行を読む（無ければ止める）。
func (p *play) attempt(plan *planState) attemptState {
	p.t.Helper()
	a := readState[attemptState](p.t, p.state, "stopreview-attempt-"+plan.AttemptID+".json")
	if a == nil {
		p.t.Fatalf("試行 %s が無い", plan.AttemptID)
	}
	return *a
}

// sentBackLeft は、同じセッションに次の Stop で消費される差し戻しが残っているか。
func sentBackLeft(t *testing.T, state string) bool {
	t.Helper()
	for _, name := range listNames(t, state) {
		if !strings.HasPrefix(name, "stopreview-attempt-") || !strings.HasSuffix(name, ".json") {
			continue
		}
		a := readState[attemptState](t, state, name)
		if a.State == sentBack && strings.HasPrefix(a.Key, session+":") {
			return true
		}
	}
	return false
}

// userPrompts は transcript のユーザの発言から、ツールの結果と通知を除いたものを返す（UserPromptSubmit の足跡に使う）。
func userPrompts(t *testing.T, path string) []string {
	t.Helper()
	var found []string
	for _, row := range transcriptRows(t, path) {
		if row.Type != "user" {
			continue
		}
		content := row.contentText()
		if content != "" && !strings.HasPrefix(strings.TrimLeft(content, " \t\r\n"), "<task-notification") {
			found = append(found, content)
		}
	}
	return found
}

// lastAssistantUUID は transcript の末尾にある、サイドチェーンでないアシスタント行の uuid（Stop の機会の鍵）。
func lastAssistantUUID(t *testing.T, path string) string {
	t.Helper()
	found := ""
	for _, row := range transcriptRows(t, path) {
		if row.Type == "assistant" && !row.IsSidechain {
			found = row.UUID
		}
	}
	return found
}

// transcriptRow は transcript の 1 行。ユーザの発言の本文（文字列か text の塊）と、アシスタントの鍵を読む。
type transcriptRow struct {
	Type        string `json:"type"`
	UUID        string `json:"uuid"`
	IsSidechain bool   `json:"isSidechain"`
	Message     struct {
		Content json.RawMessage `json:"content"`
	} `json:"message"`
}

// contentText は発言の本文。文字列ならそのまま、ブロックの並びなら text の塊を改行でつなぐ。
func (r transcriptRow) contentText() string {
	raw := strings.TrimSpace(string(r.Message.Content))
	if strings.HasPrefix(raw, "[") {
		var blocks []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		}
		if err := json.Unmarshal(r.Message.Content, &blocks); err != nil {
			return ""
		}
		var parts []string
		for _, b := range blocks {
			if b.Type == "text" {
				parts = append(parts, b.Text)
			}
		}
		return strings.Join(parts, "\n")
	}
	var s string
	if err := json.Unmarshal(r.Message.Content, &s); err != nil {
		return ""
	}
	return s
}

// transcriptRows は transcript の行を読む（空の行は飛ばす）。
func transcriptRows(t *testing.T, path string) []transcriptRow {
	t.Helper()
	var rows []transcriptRow
	for _, line := range strings.Split(readText(t, path), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var row transcriptRow
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatalf("%s の行を読めない: %v", path, err)
		}
		rows = append(rows, row)
	}
	return rows
}

// idealResult は正しい判定役が出すはずの結果。基準ごとの期待（criteria）から組む。
func idealResult(plan *planState, criteria map[string]map[string]string) string {
	keys := map[string]string{} // "proposal:<識別子>" → 提案の対象キー
	for _, p := range plan.Proposals {
		keys["proposal:"+p.Ident] = p.Key
	}
	var rows []map[string]any
	for _, criterion := range sortedKeys(criteria) {
		perTarget := criteria[criterion]
		for _, target := range sortedKeys(perTarget) {
			outcome := perTarget[target]
			row := map[string]any{"criterion": criterion, "target": target, "result": outcome}
			if k, ok := keys[target]; ok {
				row["target"] = k
			}
			if outcome == failResult {
				kind := "proposal"
				if target == stopTarget {
					kind = "final_answer"
				}
				row["locations"] = []map[string]any{{"kind": kind, "text": "場面データの期待"}}
				if fix, ok := fixes[criterion]; ok {
					row["fixes"] = []map[string]any{{"kind": fix, "text": "場面データの期待"}}
				}
			}
			rows = append(rows, row)
		}
	}
	out, err := json.Marshal(map[string]any{"attempt_id": plan.AttemptID, "classification": classTicketWork, "results": rows})
	if err != nil {
		panic(err)
	}
	return string(out)
}

// fileExists は通常のファイルがあるか。
func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

// dirExists はディレクトリがあるか。
func dirExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

// listNames は dir の直下の名前を並べて返す。dir が無ければ空。
func listNames(t *testing.T, dir string) []string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	return names
}

// copyFile は src を dst に写す（dst の置き場は作る）。
func copyFile(t *testing.T, src, dst string) {
	t.Helper()
	body, err := os.ReadFile(src)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(dst, body, 0o644); err != nil {
		t.Fatal(err)
	}
}

// readText は UTF-8 の本文を読む。改行は CRLF も LF に揃える（Windows の取り出しでも同じ答えにする）。
func readText(t *testing.T, path string) string {
	t.Helper()
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return strings.ReplaceAll(string(body), "\r\n", "\n")
}

// loadJSON は JSON のファイルを読む。
func loadJSON[T any](t *testing.T, path string) T {
	t.Helper()
	var v T
	if err := json.Unmarshal([]byte(readText(t, path)), &v); err != nil {
		t.Fatalf("%s を読めない: %v", path, err)
	}
	return v
}

// readState は状態のファイルを読む。無ければ nil。
func readState[T any](t *testing.T, state, name string) *T {
	t.Helper()
	path := filepath.Join(state, name)
	if !fileExists(path) {
		return nil
	}
	v := loadJSON[T](t, path)
	return &v
}

// sceneDir は場面データの置き場（リポジトリの根から）。
func sceneDir(t *testing.T) string {
	t.Helper()
	return filepath.Join(base.Root(), filepath.FromSlash(scenesRel))
}
