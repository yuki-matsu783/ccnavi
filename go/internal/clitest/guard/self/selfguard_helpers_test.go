package self

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// ccnavi 自身の設定ファイルを守る仕組みの受入テスト。
//
// 道具を外から動かす。実際の git リポジトリを使い捨ての置き場に作り、実行前の payload でバックアップを取らせ、
// 設定ファイルを壊してから実行後の payload を渡し、ファイルが実際にどうなったかを読む。
//
// 確かめたいのは 1 つに尽きる。ルールファイルを壊す経路と、壊されたことに気づく経路が、同じファイルに依存していないこと。
// ルール由来の保護は、ルールを空にされると保護領域ごと消える。この仕組みはそこを埋めるために在る。

// 振り分けの sh の名前。この名前なら、実体は隣ではなく `../bin/<os>-<arch>/` に在る。
const launcherName = "ccnavi-launcher.sh"

// 生の文字列の `>` の個数の上限。超えると保護を適用せずに止める。
const redirectLimit = 50

// 保護の識別子。
const (
	binaryRule  = "builtin:guard-binary"
	recordsRule = "builtin:guard-records"
)

func selfRules() payload {
	return payload{
		"version": 1,
		"deny": []payload{{
			"id": "push", "match": "Bash", "glob": "*git push*", "message": "git push is not run by the agent.",
		}},
		"allow": []payload{{"id": "anything-else", "match": "Bash|Read|Write|Edit", "regex": "."}},
	}
}

func selfSettings() payload {
	return payload{"hooks": payload{"PreToolUse": []any{}, "PostToolUse": []any{}}, "env": payload{"CCNAVI_MODE": "enable"}}
}

// withRules は selfRules に欄を足した（上書きした）もの。
func withRules(extra payload) payload {
	r := selfRules()
	for k, v := range extra {
		r[k] = v
	}
	return r
}

// sameJSON は text の JSON が want と同じ値か。
func sameJSON(t testing.TB, text string, want any) bool {
	t.Helper()
	var got, exp any
	if err := json.Unmarshal([]byte(text), &got); err != nil {
		return false
	}
	b, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &exp); err != nil {
		t.Fatal(err)
	}
	return reflect.DeepEqual(got, exp)
}

func mustHave(t testing.TB, text string, wants ...string) {
	t.Helper()
	for _, w := range wants {
		if !strings.Contains(text, w) {
			t.Errorf("%q が無い: %s", w, text)
		}
	}
}

func mustNot(t testing.TB, text string, nots ...string) {
	t.Helper()
	for _, n := range nots {
		if strings.Contains(text, n) {
			t.Errorf("%q が出てはいけない: %s", n, text)
		}
	}
}

// sg は設定ファイルとルールをコミットした git の木と、そこへ向けて hook を通す道具。
type sg struct {
	t                     *testing.T
	repo, settings, rules string
	state, log            string
}

func newSG(t *testing.T) *sg {
	t.Helper()
	repo := base.TempDir(t)
	initRepo(t, repo)
	s := &sg{t: t, repo: repo}
	s.settings = write(t, filepath.Join(repo, ".claude", "settings.json"), ruleBlob(t, selfSettings())+"\n")
	s.rules = write(t, base.CommonPath(repo, "rules"), ruleBlob(t, selfRules()))
	git(t, repo, "add", "-A")
	git(t, repo, "commit", "--quiet", "-m", "init")
	s.state = filepath.Join(repo, "logs", "state")
	s.log = filepath.Join(repo, "logs", "decisions.jsonl")
	return s
}

func (s *sg) at(rel string) string { return filepath.Join(s.repo, filepath.FromSlash(rel)) }

// call は 1 回の hook の呼び出し。空の欄は既定（mode・setting は enable、session は s1、tool は Bash）。
type call struct {
	Mode, Setting, Session, Tool string
	// Bin は CCNAVI_ENTRY_PATH（実行ファイルを指す設定）。
	Bin            string
	PermissionMode string
	Env            map[string]string
	Prompt         string
	// Extra は Stop の background_tasks など、イベントごとの欄。
	Extra   payload
	Command string
	// Path は Write などの file_path。
	Path string
}

// run は hook を 1 回通す。ルール由来の保護は切っておく（両方が同じファイルについて別々に口を出すと、どちらが戻したのかが
// テストから見えない）。
func (s *sg) run(event string, c ...call) run.Result {
	s.t.Helper()
	var o call
	if len(c) > 0 {
		o = c[0]
	}
	or := func(v, d string) string {
		if v == "" {
			return d
		}
		return v
	}
	input := map[string]any{"command": "ls"}
	switch {
	case o.Path != "":
		input = map[string]any{"file_path": o.Path}
	case o.Command != "":
		input = map[string]any{"command": o.Command}
	}
	body := payload{"hook_event_name": event, "tool_name": or(o.Tool, "Bash"), "tool_input": input, "session_id": or(o.Session, "s1")}
	for k, v := range o.Extra {
		body[k] = v
	}
	if o.Prompt != "" { // UserPromptSubmit だけが prompt を持つ。
		body["prompt"] = o.Prompt
	}
	if o.PermissionMode != "" {
		body["permission_mode"] = o.PermissionMode
	}
	vars := map[string]string{}
	if o.Bin != "" {
		vars[env.EntryPath] = o.Bin
	}
	for k, v := range o.Env {
		vars[k] = v
	}
	args := []string{"--mode", or(o.Mode, "enable"), "--guard-core-files", or(o.Setting, "enable"), "--restore-if-deny", "disable"}
	return hook(s.t, s.repo, args, body, vars)
}

func (s *sg) binary(text ...string) string {
	s.t.Helper()
	content := "MZ fake executable\n"
	if len(text) > 0 {
		content = text[0]
	}
	// 実行ファイルの代わりを置く。.gitignore の中に在る想定なのでコミットしない。git から戻せない対象がこれにあたる。
	return write(s.t, s.at("dist/ccnavi/ccnavi.exe"), content)
}

// worktree は実際のワークツリーを .claude/worktrees/<名前> に作る。守る側は .git ファイルと main の登録の相互参照が
// 両向きに揃ったものだけをワークツリーと呼ぶので、手でディレクトリを置いただけでは対象にならない。
func (s *sg) worktree(name string) string {
	s.t.Helper()
	path := s.at(".claude/worktrees/" + name)
	git(s.t, s.repo, "worktree", "add", "--quiet", "-b", name, path)
	return path
}

// store は実体の置き場に在るものの中身（セッションをまたいで共有する側）。
func (s *sg) store() []string {
	s.t.Helper()
	dir := filepath.Join(s.state, "selfguard", "store")
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var out []string
	for _, e := range entries {
		out = append(out, read(s.t, filepath.Join(dir, e.Name())))
	}
	sort.Strings(out)
	return out
}

// sessions はバックアップを持っているセッションの名前。
func (s *sg) sessions() []string {
	s.t.Helper()
	dir := filepath.Join(s.state, "selfguard")
	entries, err := os.ReadDir(dir)
	if err != nil {
		s.t.Fatal(err)
	}
	var out []string
	for _, e := range entries {
		if e.IsDir() {
			out = append(out, e.Name())
		}
	}
	return out
}

// backupFiles はセッションのバックアップのファイル（置いていない印は除く）。
func (s *sg) backupFiles(session string) []string {
	s.t.Helper()
	dir := filepath.Join(s.state, "selfguard", session)
	entries, err := os.ReadDir(dir)
	if err != nil {
		s.t.Fatal(err)
	}
	var out []string
	for _, e := range entries {
		if !e.IsDir() && !strings.HasSuffix(e.Name(), ".absent") {
			out = append(out, filepath.Join(dir, e.Name()))
		}
	}
	return out
}

func (s *sg) setAge(seconds int, paths ...string) {
	s.t.Helper()
	old := time.Now().Add(-time.Duration(seconds) * time.Second)
	for _, p := range paths {
		if err := os.Chtimes(p, old, old); err != nil {
			s.t.Fatal(err)
		}
	}
}

// backdate は置き場ごと更新時刻を古くする。掃除の日付をまたがせるため。
func (s *sg) backdate(dir string) {
	s.t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		s.t.Fatal(err)
	}
	for _, e := range entries {
		s.setAge(10*86400, filepath.Join(dir, e.Name()))
	}
	s.setAge(10*86400, dir)
}

func (s *sg) age(path string) time.Duration {
	s.t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		s.t.Fatal(err)
	}
	return time.Since(info.ModTime())
}

// reading は `--test` の JSON。読み切れなかったか（degraded）と、中で実行されるもの（unwrapped）。
func (s *sg) reading(command string) (degraded bool, unwrapped string) {
	s.t.Helper()
	r := run.Ccnavi(s.t, []string{"--root", s.repo, "--test", "Bash", command, "--json"}, run.Opts{Dir: base.Root()})
	if r.Code != 0 {
		s.t.Fatal(r.Stderr)
	}
	var out struct {
		Degraded  any    `json:"degraded"`
		Unwrapped string `json:"unwrapped"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		s.t.Fatalf("%v: %s", err, r.Stdout)
	}
	return out.Degraded != nil && out.Degraded != "" && out.Degraded != false, out.Unwrapped
}

// records は判定の記録（logs/decisions.jsonl）の行。
func (s *sg) records() []map[string]any {
	s.t.Helper()
	var out []map[string]any
	for _, line := range strings.Split(read(s.t, s.log), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var rec map[string]any
		if err := json.Unmarshal([]byte(line), &rec); err != nil {
			s.t.Fatalf("記録が JSON でない: %v: %s", err, line)
		}
		out = append(out, rec)
	}
	return out
}

func (s *sg) lastRecord() map[string]any {
	s.t.Helper()
	recs := s.records()
	if len(recs) == 0 {
		s.t.Fatal("記録が無い")
	}
	return recs[len(recs)-1]
}

// strs は記録の配列の欄を文字列の並びにする。
func strs(v any) []string {
	list, _ := v.([]any)
	out := make([]string, 0, len(list))
	for _, x := range list {
		out = append(out, x.(string))
	}
	return out
}

func (s *sg) rulesAre(want any) {
	s.t.Helper()
	if got := read(s.t, s.rules); !sameJSON(s.t, got, want) {
		s.t.Errorf("ルールファイルが戻っていない: %s", got)
	}
}

func (s *sg) settingsAre(want any) {
	s.t.Helper()
	if got := read(s.t, s.settings); !sameJSON(s.t, got, want) {
		s.t.Errorf("設定ファイルが戻っていない: %s", got)
	}
}

func contains(list []string, want string) bool {
	for _, x := range list {
		if x == want {
			return true
		}
	}
	return false
}
