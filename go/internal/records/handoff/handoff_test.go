package handoff

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
)

const session = "sess-1"

// assistant は応答の行。tokens は会話の大きさ、short・long は 5 分側・1 時間側のキャッシュの書き込み。
func assistant(tokens, short, long int, sidechain bool) string {
	return fmt.Sprintf(`{"type": "assistant", "isSidechain": %t, "message": {"usage": {"input_tokens": 2, `+
		`"cache_read_input_tokens": %d, "cache_creation_input_tokens": %d, "cache_creation": `+
		`{"ephemeral_5m_input_tokens": %d, "ephemeral_1h_input_tokens": %d}}}}`,
		sidechain, tokens-2-short-long, short+long, short, long)
}

func transcript(t *testing.T, dir string, rows ...string) string {
	t.Helper()
	path := filepath.Join(dir, "t.jsonl")
	if err := os.WriteFile(path, []byte(strings.Join(rows, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestUsageOf(t *testing.T) {
	big := `{"type": "user", "message": {"content": "` + strings.Repeat("x", 1_100_000) + `"}}`
	cases := []struct {
		name   string
		rows   []string
		tokens int
		ttl    time.Duration
	}{
		{"最新の応答の入力の合計", []string{assistant(1000, 0, 10, false), assistant(2500, 0, 10, false)}, 2500, ttlLong},
		{"サブエージェントの行は数えない", []string{assistant(1000, 0, 10, false), assistant(9000, 0, 10, true)}, 1000, ttlLong},
		{"直近の書き込みが5分側", []string{assistant(1000, 0, 10, false), assistant(2000, 10, 0, false)}, 2000, ttlShort},
		{"直近が読み取りだけなら遡る", []string{assistant(1000, 10, 0, false), assistant(2000, 0, 0, false)}, 2000, ttlShort},
		{"末尾に巨大な行が続いても読む", []string{assistant(500_000, 0, 10, false), big}, 500_000, ttlLong},
		{"使用量が全部0の行は飛ばす", []string{assistant(300_000, 0, 10, false), `{"type": "assistant", "message": {"usage": {"input_tokens": 0}}}`}, 300_000, ttlLong},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			usage := UsageOf(transcript(t, t.TempDir(), c.rows...))
			if usage == nil || usage.Tokens != c.tokens || usage.TTL != c.ttl {
				t.Errorf("UsageOf = %+v, want %d %v", usage, c.tokens, c.ttl)
			}
		})
	}
	dir := t.TempDir()
	for _, path := range []string{filepath.Join(dir, "none.jsonl"), "", transcript(t, dir, "not json", "{}")} {
		if usage := UsageOf(path); usage != nil {
			t.Errorf("%q: UsageOf = %+v", path, usage)
		}
	}
}

func TestThreshold(t *testing.T) {
	for raw, want := range map[string]int{"": 400000, "123000": 123000, "0": 0, "-5": 0, "abc": 400000} {
		if got := Threshold(raw); got != want {
			t.Errorf("Threshold(%q) = %d, want %d", raw, got, want)
		}
	}
}

func TestWaitFor(t *testing.T) {
	cases := []struct {
		ttl      time.Duration
		override string
		want     time.Duration
	}{
		{ttlLong, "", 55 * time.Minute},
		{ttlShort, "", 4 * time.Minute},
		{ttlLong, "2", 2 * time.Second},
		{ttlLong, "x", 55 * time.Minute},
		{ttlLong, "²", 55 * time.Minute}, // 上付き数字で落ちない
		{ttlLong, "999999", maxWait},
	}
	for _, c := range cases {
		if got := waitFor(c.ttl, c.override); got != c.want {
			t.Errorf("waitFor(%v, %q) = %v, want %v", c.ttl, c.override, got, c.want)
		}
	}
}

func TestUnattended(t *testing.T) {
	cases := []struct {
		env  map[string]string
		want bool
	}{
		{map[string]string{env.ClaudeSessionAttended: "0"}, true},
		{map[string]string{env.ClaudeCodeEntrypoint: "sdk-cli"}, true},
		{map[string]string{env.ClaudeCodeEntrypoint: "cli"}, false},
		{map[string]string{}, false},
	}
	for _, c := range cases {
		if got := Unattended(func(k string) string { return c.env[k] }); got != c.want {
			t.Errorf("Unattended(%v) = %v", c.env, got)
		}
	}
}

var bigUsage = &Usage{Tokens: 500_000, TTL: ttlLong}

func usage(tokens int) *Usage { return &Usage{Tokens: tokens, TTL: ttlLong} }

func begin(t *testing.T, state string, u *Usage) (Plan, bool) {
	t.Helper()
	return Start(state, session, u, 400_000, "", "")
}

func TestStartAndCheck(t *testing.T) {
	state := t.TempDir()
	if _, ok := begin(t, state, usage(399_999)); ok {
		t.Error("閾値に届かないのに始めた")
	}
	plan, ok := begin(t, state, bigUsage)
	if !ok || plan.Wait != 55*time.Minute || plan.Token == "" {
		t.Fatalf("Start = %+v, %v", plan, ok)
	}
	text, ok := Check(state, session, plan.Token, plan.Wait, 0)
	if !ok || !strings.HasPrefix(text, hookio.IdleHandoffMarker) || !strings.Contains(text, "scratchpad/") || !strings.Contains(text, "55 分") {
		t.Errorf("Check = %q, %v", text, ok)
	}
}

func TestResetStopsOnlyForUserInput(t *testing.T) {
	cases := []struct {
		prompt     string
		background bool
		fires      bool
	}{
		{"続きをお願いします", false, false},
		{hookio.IdleHandoffMarker + " 書いてください", false, true},
		{"この差分を見て: [idle-handoff] を含む実装", false, false}, // 目印は先頭にあるときだけ
		{"<task-notification>", true, true},
	}
	for _, c := range cases {
		state := t.TempDir()
		plan, _ := begin(t, state, bigUsage)
		Reset(state, session, c.prompt, c.background)
		if _, ok := Check(state, session, plan.Token, plan.Wait, 0); ok != c.fires {
			t.Errorf("%q: 起こした = %v, want %v", c.prompt, ok, c.fires)
		}
	}
}

func TestShrunkConversationDoesNotFire(t *testing.T) {
	dir := t.TempDir()
	state := filepath.Join(dir, "state")
	path := transcript(t, dir, assistant(500_000, 0, 10, false))
	plan, ok := Start(state, session, UsageOf(path), 400_000, "", path)
	if !ok {
		t.Fatal("始めない")
	}
	transcript(t, dir, assistant(50_000, 0, 10, false)) // /compact のあと
	if _, ok := Check(state, session, plan.Token, plan.Wait, 400_000); ok {
		t.Error("小さくなった会話で起こした")
	}
}

func TestNewerWaitReplacesTheOlder(t *testing.T) {
	state := t.TempDir()
	older, _ := begin(t, state, bigUsage)
	newer, _ := begin(t, state, bigUsage)
	if _, ok := Check(state, session, older.Token, older.Wait, 0); ok {
		t.Error("置き換わった待機で起こした")
	}
	if _, ok := Check(state, session, newer.Token, newer.Wait, 0); !ok {
		t.Error("新しい待機で起こさない")
	}
}

func TestBaseAfterFiring(t *testing.T) {
	fire := func(t *testing.T) string {
		state := t.TempDir()
		plan, _ := begin(t, state, bigUsage)
		Check(state, session, plan.Token, plan.Wait, 0)
		return state
	}
	state := fire(t)
	if _, ok := begin(t, state, usage(900_000)); ok {
		t.Error("ユーザが打つ前に始めた")
	}
	Reset(state, session, "続けます", false)
	if _, ok := begin(t, state, usage(899_999)); ok {
		t.Error("閾値のぶん育つ前に始めた")
	}
	if _, ok := begin(t, state, usage(900_000)); !ok {
		t.Error("育ったのに始めない")
	}
	// 会話が基準（500,000）より小さくなれば基準は 0 に戻る。
	state = fire(t)
	Reset(state, session, "続けます", false)
	if _, ok := begin(t, state, usage(100_000)); ok {
		t.Error("小さい会話で始めた")
	}
	if _, ok := begin(t, state, usage(450_000)); !ok {
		t.Error("基準が戻っていない")
	}
}

func TestStartRefuses(t *testing.T) {
	state := t.TempDir()
	for _, s := range []string{"../x", ""} {
		if _, ok := Start(state, s, bigUsage, 400_000, "", ""); ok {
			t.Errorf("%q で始めた", s)
		}
	}
	if _, ok := Start(state, session, bigUsage, 0, "", ""); ok {
		t.Error("0 で始めた")
	}
	if _, ok := Start(state, session, nil, 400_000, "", ""); ok {
		t.Error("大きさが分からないのに始めた")
	}
}

func age(t *testing.T, path string) {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	old := info.ModTime().Add(-8 * 24 * time.Hour)
	if err := os.Chtimes(path, old, old); err != nil {
		t.Fatal(err)
	}
}

func TestPrune(t *testing.T) {
	state := t.TempDir()
	Start(state, session, bigUsage, 400_000, "", "")
	Notice(state, session, bigUsage, 400_000, "")
	dir := filepath.Join(state, folderName)
	age(t, filepath.Join(dir, session+noticeSuffix))
	Prune(state, time.Now())
	if _, err := os.Stat(filepath.Join(dir, session+noticeSuffix)); err != nil {
		t.Errorf("対の状態ファイルが新しいのに印を消した: %v", err)
	}
	age(t, filepath.Join(dir, session+fsio.JSONExt))
	Prune(state, time.Now())
	if entries, _ := os.ReadDir(dir); len(entries) != 0 {
		t.Errorf("残った: %v", entries)
	}
}

func TestNotice(t *testing.T) {
	state := t.TempDir()
	if _, ok := Notice(state, session, usage(1000), 400_000, ""); ok {
		t.Error("閾値に届かないのに知らせた")
	}
	first, ok := Notice(state, session, bigUsage, 400_000, "")
	if !ok {
		t.Fatal("知らせない")
	}
	for _, word := range []string{"キャッシュ", "料金", "引き継ぎ", "60 分", "scratchpad/", "500,000", env.HandoffSuggestTokens} {
		if !strings.Contains(first, word) {
			t.Errorf("知らせに %q が無い: %s", word, first)
		}
	}
	if _, ok := Notice(state, session, bigUsage, 400_000, ""); ok {
		t.Error("2 回知らせた")
	}
}

func TestNoticeAfterFiring(t *testing.T) {
	fire := func(state string, u *Usage) {
		plan, _ := Start(state, session, u, 400_000, "", "")
		Check(state, session, plan.Token, maxWait, 0)
	}
	state := t.TempDir()
	fire(state, bigUsage)
	// 会話の大きさを読めなくても、書かせたことは 1 回だけ知らせる。
	first, ok := Notice(state, session, nil, 400_000, "")
	if !ok || !strings.Contains(first, "55 分放置されたため") || !strings.Contains(first, "scratchpad/") {
		t.Errorf("Notice = %q, %v", first, ok)
	}
	if _, ok := Notice(state, session, bigUsage, 400_000, ""); ok {
		t.Error("2 回知らせた")
	}
	// 次に書かせたときも知らせる。
	Reset(state, session, "続けます", false)
	fire(state, usage(1_000_000))
	if _, ok := Notice(state, session, usage(1_000_000), 400_000, ""); !ok {
		t.Error("次に書かせたときに知らせない")
	}
	// ユーザが打った後は書かせた後の知らせを出さない。
	other := t.TempDir()
	fire(other, bigUsage)
	Reset(other, session, "続けます", false)
	if text, ok := Notice(other, session, bigUsage, 400_000, ""); ok {
		t.Errorf("打った後に知らせた: %q", text)
	}
}
