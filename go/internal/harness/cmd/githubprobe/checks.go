package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"ccnavi/internal/infra/env"
)

// 実験場の既定。ここ以外に書くには CCNAVI_PROBE_REPO_OK を置く。
const defaultRepo = "yuki-matsu783/test-repo"

var (
	repoRE   = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)
	originRE = regexp.MustCompile(`github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$`)
)

// probeSettings は実測の子の環境に足す設定（認証は別に足す）。ccnavi の判定を実物の既定に寄せる。
var probeSettings = map[string]string{
	env.GuardTicketApproval: "disable",
	env.Mode:                "enable",
	env.GuardCoreFiles:      "disable",
	env.RestoreIfDeny:       "disable",
	"GIT_TERMINAL_PROMPT":   "0",
	"GCM_INTERACTIVE":       "never",
	"GIT_AUTHOR_NAME":       "probe",
	"GIT_AUTHOR_EMAIL":      "probe@example.com",
	"GIT_COMMITTER_NAME":    "probe",
	"GIT_COMMITTER_EMAIL":   "probe@example.com",
}

// ownRepo は origin の URL から owner/name を小文字で読む。読めなければ空。
func ownRepo(originURL string) string {
	m := originRE.FindStringSubmatch(strings.TrimSpace(originURL))
	if m == nil {
		return ""
	}
	return strings.ToLower(m[1])
}

// checkTarget は実験場の決まり。ccnavi 本体（own）には何があっても書かない。
func checkTarget(repo, own string, optedIn bool) error {
	switch {
	case !repoRE.MatchString(repo):
		return fmt.Errorf("CCNAVI_PROBE_REPO は owner/name の形で (%s)", repo)
	case strings.ToLower(repo) == own:
		return fmt.Errorf("%s は ccnavi 本体のリポジトリ。ここには書かない", repo)
	case repo != defaultRepo && !optedIn:
		return errors.New("実験場は yuki-matsu783/test-repo だけ。ほかを使うなら CCNAVI_PROBE_REPO_OK=1 を置く")
	case own == "":
		return errors.New("このチェックアウトの origin を読めず、本体への書き込みを避けられるか分からない。止めた")
	}
	return nil
}

// pickToken は GITHUB_TOKEN、無ければ gh auth token の値。どちらも無ければ空。
func pickToken(given string, ghToken func() string) string {
	if given != "" {
		return given
	}
	return strings.TrimSpace(ghToken())
}

// exePath は確かめる実行ファイルのパス。given が絶対パスならそれ、相対なら根からの相対（既定は dist/ccnavi/ccnavi）。
// 拡張子 .exe の付いた物も探す。
func exePath(given, root string, isFile func(string) bool) (string, error) {
	if given != "" && filepath.IsAbs(given) {
		return given, nil
	}
	rel := given
	if rel == "" {
		rel = filepath.Join("dist", "ccnavi", "ccnavi")
	}
	base := filepath.Join(root, rel)
	for _, c := range []string{base, base + ".exe"} {
		if isFile(c) {
			return c, nil
		}
	}
	return "", fmt.Errorf("exe が無い (%s)。CCNAVI_ENTRY_PATH に絶対パスを渡すか、scripts/ccnavi-build.sh で組み立てる", base)
}

// staleCheck は exe の版（--version の出力）と、このチェックアウトの HEAD を並べて出す文と、止めるかどうか。
// 古い exe は仕様が違うことがある。strict なら HEAD と合わないときに止める。
func staleCheck(shown, sha string, strict bool) ([]string, error) {
	if shown == "" {
		shown = "(読めない)"
	}
	notes := []string{fmt.Sprintf("exe の版: %s / このチェックアウトの HEAD: %s", shown, sha)}
	matches := sha != "" && strings.Contains(shown, sha)
	if strict && !matches {
		return notes, errors.New("exe がこのチェックアウトから組み立てたものと確認できない。scripts/ccnavi-build.sh で組み直す")
	}
	if !matches {
		notes = append(notes, "警告: exe が古いかもしれない（CCNAVI_PROBE_STRICT_EXE=1 で止める）")
	}
	return notes, nil
}

// probeEnv は実測の子の環境。走らせた人の CCNAVI_* と CLAUDE_PROJECT_DIR を外し、トークンと実測の設定を足す。
func probeEnv(base map[string]string, token, exe string) map[string]string {
	out := map[string]string{}
	for k, v := range base {
		if strings.HasPrefix(k, env.Prefix) || k == env.ClaudeProjectDir {
			continue
		}
		out[k] = v
	}
	out[env.GitHubToken] = token
	out["GH_TOKEN"] = token
	out[env.EntryPath] = exe
	for k, v := range probeSettings {
		out[k] = v
	}
	return out
}

// pathKey は環境の PATH の名前（Windows は Path と書かれることがある）。
func pathKey(env map[string]string) string {
	for k := range env {
		if strings.EqualFold(k, "PATH") {
			return k
		}
	}
	return "PATH"
}

// withoutGh は gh を PATH から外した環境。gh と同じ場所の道具（jq・curl・git）は shim に見せる。
// sh が curl とトークンの経路に切り替わるのを見るため。
func withoutGh(env map[string]string, ghPath, shim string) (map[string]string, error) {
	out := map[string]string{}
	for k, v := range env {
		out[k] = v
	}
	ghDir := filepath.Dir(ghPath)
	entries, err := os.ReadDir(ghDir)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(shim, 0o755); err != nil {
		return nil, err
	}
	for _, e := range entries {
		name := e.Name()
		if strings.ToLower(strings.TrimSuffix(name, filepath.Ext(name))) == "gh" {
			continue
		}
		link := filepath.Join(shim, name)
		if _, err := os.Lstat(link); err == nil {
			continue
		}
		src := filepath.Join(ghDir, name)
		if err := os.Symlink(src, link); err != nil {
			_ = copyFile(src, link) // Windows など。コピーで代える（失敗は無視）
		}
	}
	key := pathKey(out)
	parts := []string{shim}
	for _, p := range filepath.SplitList(out[key]) {
		if p != ghDir {
			parts = append(parts, p)
		}
	}
	out[key] = strings.Join(parts, string(os.PathListSeparator))
	return out, nil
}

func copyFile(src, dst string) error {
	data, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	return os.WriteFile(dst, data, 0o755)
}

// ticket は実測で使う提案の中身。Parent が空なら親、あれば子。
type ticket struct {
	Name   string
	Parent string
	Phase  int
	Allow  []string
	Review bool
	Title  string
	Issue  int
}

// ticketText は提案の本文（ccnavi が読む見出しと、ユーザが読む本文）。
func ticketText(t ticket) string {
	lines := []string{"---", "version: 1", "ticket: " + t.Name}
	if t.Parent != "" {
		lines = append(lines, "parent: "+t.Parent, fmt.Sprintf("phase: %d", t.Phase))
	}
	if t.Issue != 0 {
		lines = append(lines, fmt.Sprintf("issue: %d", t.Issue))
	}
	required := "false"
	if t.Review {
		required = "true"
	}
	lines = append(lines,
		"human_review:",
		"  required: "+required,
		"  reason: 実測",
		"title: "+t.Title,
		"rationale: |",
		"  実物の GitHub に適用する実測。",
	)
	if len(t.Allow) > 0 {
		lines = append(lines, "allow:")
		for _, g := range t.Allow {
			lines = append(lines, "  - match: Write|Edit", "    glob: \""+g+"\"")
		}
	}
	lines = append(lines, `started_at: ""`, `completed_at: ""`, `base_sha: ""`, "---", "", "本文（ユーザが読む説明）", "")
	return strings.Join(lines, "\n")
}

// rule と rulesFile は実測の木に置く規則（deny の 1 件）。
type rule struct {
	ID      string `json:"id"`
	Match   string `json:"match"`
	Glob    string `json:"glob"`
	Message string `json:"message"`
}

type rulesFile struct {
	Version int    `json:"version"`
	Deny    []rule `json:"deny"`
}

// rulesText は規則の本文（YAML の部分集合の JSON）。
func rulesText() (string, error) {
	data, err := json.Marshal(rulesFile{Version: 1, Deny: []rule{{
		ID:      "guard-approved",
		Match:   "Write|Edit|NotebookEdit",
		Glob:    "*/.ccnavi/*",
		Message: "ガードの設定と承認済みチケットです。ユーザに依頼してください。",
	}}})
	return string(data), err
}

// redact は、トークンを <token> に置き換える。空のトークンは無視する。
func redact(text string, secrets ...string) string {
	for _, s := range secrets {
		if s != "" {
			text = strings.ReplaceAll(text, s, "<token>")
		}
	}
	return text
}

// hasMarker は、コメントの本文がマーカーで始まるものが 1 件でもあるか。
func hasMarker(comments []map[string]any, marker string) bool {
	for _, c := range comments {
		if strings.HasPrefix(str(c, "body"), marker) {
			return true
		}
	}
	return false
}

// head は文字列の先頭 n 文字（文字単位。日本語を途中で切らない）。
func head(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}

// tail は文字列の末尾 n 文字。
func tailRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[len(r)-n:])
}

// 読み取り（JSON の値を型の無い map から読む。足りなければ零値）。

func obj(v any) map[string]any {
	m, _ := v.(map[string]any)
	return m
}

func list(v any) []any {
	l, _ := v.([]any)
	return l
}

func str(m map[string]any, k string) string {
	s, _ := m[k].(string)
	return s
}

func flagOf(m map[string]any, k string) bool {
	b, _ := m[k].(bool)
	return b
}

func intOf(m map[string]any, k string) int {
	f, _ := m[k].(float64)
	return int(f)
}

// dig は入れ子の辞書を keys の順にたどる。途中が無ければ nil。
func dig(v any, keys ...string) any {
	for _, k := range keys {
		v = obj(v)[k]
	}
	return v
}

// writeFile は本文を書く（親が無ければ作る）。
func writeFile(path, text string) (string, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return "", err
	}
	return path, os.WriteFile(path, []byte(text), 0o644)
}

// readerOf は標準入力の文字列。
func readerOf(s string) io.Reader { return strings.NewReader(s) }
