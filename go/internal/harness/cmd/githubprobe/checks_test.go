package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// 本物の GitHub には繋がない。実験場の決まり・トークンと exe の選び方・環境・提案の本文・読みの判定だけを見る。

func TestOwnRepoReadsTheOriginURL(t *testing.T) {
	t.Parallel()
	cases := map[string]string{
		"https://github.com/Yuki/Repo.git": "yuki/repo",
		"git@github.com:yuki/repo.git":     "yuki/repo",
		"https://github.com/yuki/repo/":    "yuki/repo",
		"https://example.com/x/y":          "",
		"":                                 "",
	}
	for in, want := range cases {
		if got := ownRepo(in); got != want {
			t.Errorf("ownRepo(%q) = %q, want %q", in, got, want)
		}
	}
}

// 実験場の決まり: 形・本体（書かない）・既定以外（明示が要る）・origin が読めない（止める）。
func TestCheckTargetRefusesEachWay(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, repo, own string
		optedIn         bool
		wantErr         string
	}{
		{"bad form", "bad", "ccnavi/ccnavi", false, "owner/name"},
		{"ccnavi itself", defaultRepo, defaultRepo, false, "ccnavi 本体のリポジトリ"},
		{"ccnavi itself in other case", "Yuki-Matsu783/test-repo", "yuki-matsu783/test-repo", false, "ccnavi 本体"},
		{"other repo without opt-in", "other/x", "ccnavi/ccnavi", false, "CCNAVI_PROBE_REPO_OK"},
		{"origin unreadable", defaultRepo, "", false, "origin を読めず"},
	}
	for _, c := range cases {
		err := checkTarget(c.repo, c.own, c.optedIn)
		if err == nil || !strings.Contains(err.Error(), c.wantErr) {
			t.Errorf("%s: err = %v, want containing %q", c.name, err, c.wantErr)
		}
	}
	if err := checkTarget(defaultRepo, "ccnavi/ccnavi", false); err != nil {
		t.Errorf("既定の実験場は通るはず: %v", err)
	}
	if err := checkTarget("other/x", "ccnavi/ccnavi", true); err != nil {
		t.Errorf("明示した実験場は通るはず: %v", err)
	}
}

func TestPickTokenPrefersGithubToken(t *testing.T) {
	t.Parallel()
	calls := 0
	gh := func() string { calls++; return " from-gh \n" }
	if got := pickToken("given", gh); got != "given" || calls != 0 {
		t.Errorf("given: %q (gh を %d 回呼んだ)", got, calls)
	}
	if got := pickToken("", gh); got != "from-gh" {
		t.Errorf("gh の値は前後の空白を落とす: %q", got)
	}
	if got := pickToken("", func() string { return "" }); got != "" {
		t.Errorf("どちらも無ければ空: %q", got)
	}
}

func TestExePathChoosesTheCandidate(t *testing.T) {
	t.Parallel()
	root := filepath.Join("r", "root")
	files := map[string]bool{
		filepath.Join(root, "dist", "ccnavi", "ccnavi.exe"): true,
		filepath.Join(root, "custom", "tool"):               true,
		"/abs/ccnavi":                                       true,
	}
	isFile := func(p string) bool { return files[p] }
	cases := []struct {
		name, given, want string
		err               bool
	}{
		{"absolute path is used as is", "/abs/ccnavi", "/abs/ccnavi", false},
		{"default with .exe", "", filepath.Join(root, "dist", "ccnavi", "ccnavi.exe"), false},
		{"relative path under the root", "custom/tool", filepath.Join(root, "custom", "tool"), false},
		{"missing", "nope", "", true},
	}
	for _, c := range cases {
		got, err := exePath(c.given, root, isFile)
		if (err != nil) != c.err || got != c.want {
			t.Errorf("%s: got %q (%v), want %q", c.name, got, err, c.want)
		}
	}
}

// 版の確かめ: HEAD と合えば 1 行。合わないと警告。strict なら止める。読めなかったら (読めない)。
func TestStaleCheckWarnsAndStrictStops(t *testing.T) {
	t.Parallel()
	notes, err := staleCheck("ccnavi 1.0 (abc1234)", "abc1234", false)
	if err != nil || len(notes) != 1 {
		t.Errorf("一致: notes=%q err=%v", notes, err)
	}
	notes, err = staleCheck("ccnavi 1.0 (old)", "abc1234", false)
	if err != nil || len(notes) != 2 || !strings.Contains(notes[1], "警告") {
		t.Errorf("不一致は警告: notes=%q err=%v", notes, err)
	}
	if _, err := staleCheck("ccnavi 1.0 (old)", "abc1234", true); err == nil {
		t.Error("strict で不一致は止まるはず")
	}
	if _, err := staleCheck("", "", true); err == nil {
		t.Error("strict で HEAD が読めなければ止まるはず")
	}
	notes, _ = staleCheck("", "abc", false)
	if !strings.Contains(notes[0], "(読めない)") {
		t.Errorf("版が読めないとき: %q", notes[0])
	}
}

// 子の環境: 走らせた人の CCNAVI_* と CLAUDE_PROJECT_DIR を外し、トークンと実測の設定を足す。
func TestProbeEnvDropsTheCallersCcnaviSettings(t *testing.T) {
	t.Parallel()
	base := map[string]string{"CCNAVI_MODE": "x", "CLAUDE_PROJECT_DIR": "/c", "PATH": "/bin", "HOME": "/h"}
	env := probeEnv(base, "tok", "/exe")
	if env["CCNAVI_MODE"] != "enable" || env["CCNAVI_ENTRY_PATH"] != "/exe" {
		t.Errorf("CCNAVI_MODE = %q, CCNAVI_ENTRY_PATH = %q", env["CCNAVI_MODE"], env["CCNAVI_ENTRY_PATH"])
	}
	if _, ok := env["CLAUDE_PROJECT_DIR"]; ok {
		t.Error("CLAUDE_PROJECT_DIR が残っている")
	}
	if env["PATH"] != "/bin" || env["HOME"] != "/h" || env["GITHUB_TOKEN"] != "tok" || env["GH_TOKEN"] != "tok" {
		t.Errorf("env = %v", env)
	}
	if _, ok := env["PYTHONUTF8"]; ok {
		t.Error("PYTHONUTF8 は Go の実測では要らない")
	}
}

// gh を PATH から外すとき、gh だけが見えず、同じ場所のほかの道具は shim から見える。
func TestWithoutGhHidesOnlyGh(t *testing.T) {
	t.Parallel()
	dir := t.TempDir()
	ghDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(ghDir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"gh", "jq", "curl"} {
		if err := os.WriteFile(filepath.Join(ghDir, name), []byte("#!/bin/sh\n"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	shim := filepath.Join(dir, "shim")
	env := map[string]string{"PATH": ghDir + string(os.PathListSeparator) + "/usr/bin"}
	out, err := withoutGh(env, filepath.Join(ghDir, "gh"), shim)
	if err != nil {
		t.Fatal(err)
	}
	path := out["PATH"]
	if !strings.HasPrefix(path, shim+string(os.PathListSeparator)) || strings.Contains(path, ghDir) {
		t.Errorf("PATH = %q", path)
	}
	if _, err := os.Stat(filepath.Join(shim, "jq")); err != nil {
		t.Errorf("jq が shim に無い: %v", err)
	}
	if _, err := os.Lstat(filepath.Join(shim, "gh")); err == nil {
		t.Error("gh が shim に見えている")
	}
}

// 提案の本文の形（親と子）。ccnavi が読む見出しの並びと、allow の字下げ。
func TestTicketTextKeepsTheHeadingOrder(t *testing.T) {
	t.Parallel()
	got := ticketText(ticket{Name: "i0001-01-01", Parent: "i0001", Phase: 1, Allow: []string{"src/a/*"}, Review: true, Title: "a を書く"})
	want := strings.Join([]string{
		"---", "version: 1", "ticket: i0001-01-01", "parent: i0001", "phase: 1",
		"human_review:", "  required: true", "  reason: 実測", "title: a を書く",
		"rationale: |", "  実物の GitHub に適用する実測。",
		"allow:", "  - match: Write|Edit", `    glob: "src/a/*"`,
		`started_at: ""`, `completed_at: ""`, `base_sha: ""`, "---", "", "本文（ユーザが読む説明）", "",
	}, "\n")
	if got != want {
		t.Errorf("ticketText =\n%s\nwant\n%s", got, want)
	}
	parent := ticketText(ticket{Name: "i0001", Allow: []string{"src/*"}, Review: true, Title: "挨拶を足す", Issue: 7})
	if !strings.Contains(parent, "issue: 7\nhuman_review:") || strings.Contains(parent, "parent:") {
		t.Errorf("親の本文 = %q", parent)
	}
}

func TestRulesTextIsTheGuardRule(t *testing.T) {
	t.Parallel()
	text, err := rulesText()
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Version int `json:"version"`
		Deny    []struct {
			ID   string `json:"id"`
			Glob string `json:"glob"`
		} `json:"deny"`
	}
	if err := json.Unmarshal([]byte(text), &doc); err != nil {
		t.Fatal(err)
	}
	if doc.Version != 1 || len(doc.Deny) != 1 || doc.Deny[0].ID != "guard-approved" || doc.Deny[0].Glob != "*/.ccnavi/*" {
		t.Errorf("rules = %+v", doc)
	}
}

func TestRedactHidesSecretsAndIgnoresEmpty(t *testing.T) {
	t.Parallel()
	if got := redact("a tok b rev", "tok", "rev"); got != "a <token> b <token>" {
		t.Errorf("redact = %q", got)
	}
	if got := redact("x", ""); got != "x" {
		t.Errorf("空のトークンは無視: %q", got)
	}
}

func TestMarkerIsAPrefixOfABody(t *testing.T) {
	t.Parallel()
	comments := []map[string]any{{"body": "<!-- ccnavi:ready -->\n本文"}, {"body": "ふつうのコメント"}}
	if !hasMarker(comments, "<!-- ccnavi:ready -->") || hasMarker(comments, "<!-- ccnavi:decide -->") {
		t.Error("マーカーは本文の先頭で見る")
	}
}

// 2 本の未解決のスレッドを path・line・URL・時刻つきで読めたか。1 本でも解決済みや外れた所があれば偽。
func TestThreadsReadNeedsBothOpenThreads(t *testing.T) {
	t.Parallel()
	ok := []fetchThread{
		{Path: "src/1/work.py", Line: 1, URL: "https://github.com/a/b/pull/1#discussion_r1", CreatedAt: "2026-01-01T00:00:00Z"},
		{Path: "src/2/work.py", Line: 2, URL: "https://github.com/a/b/pull/1#discussion_r2", CreatedAt: "2026-01-01T00:00:00Z"},
	}
	if !threadsRead(ok) {
		t.Error("2 本とも正しいのに偽")
	}
	resolved := append([]fetchThread{}, ok...)
	resolved[0].Resolved = true
	if threadsRead(resolved) {
		t.Error("解決済みが混じっているのに真")
	}
	wrongPath := append([]fetchThread{}, ok...)
	wrongPath[1].Path = "src/3/work.py"
	if threadsRead(wrongPath) {
		t.Error("path が違うのに真")
	}
	if threadsRead(ok[:1]) {
		t.Error("1 本しか無いのに真")
	}
}

func TestFetchedDigitsAndSameFetch(t *testing.T) {
	t.Parallel()
	if !digits("12345") || digits("12a") || digits("") || digits(float64(7)) {
		t.Error("author は数字だけの文字列")
	}
	a := fetchOut{Host: "github", Threads: []fetchThread{{Path: "p", Line: 1}}}
	b := a
	b.Threads = []fetchThread{{Path: "p", Line: 1}}
	if !sameFetch(a, b) {
		t.Error("同じ読みが違うと言った")
	}
	b.Threads[0].Line = 2
	if sameFetch(a, b) {
		t.Error("違う読みを同じと言った")
	}
	if got := fetched(result{Stdout: "not json"}); got.Host != "" {
		t.Errorf("読めなければ零値: %+v", got)
	}
}

func TestHeadCountsRunesNotBytes(t *testing.T) {
	t.Parallel()
	if got := head("日本語テキスト", 3); got != "日本語" {
		t.Errorf("head = %q", got)
	}
	if got := tailRunes("日本語テキスト", 2); got != "スト" {
		t.Errorf("tailRunes = %q", got)
	}
}
