// claudrecord は、本物の `claude -p` に決まった手を打たせ、hook に渡る JSON と stream-json の形を録る。
//
// claude -p のスタブの契約のうち、本物から録る側（仕様は `.agents/docs/claude-contract.md`）。手動で回す。費用がかかる。
// 自動テストは呼ばない。
//
//	cd go && go run ./internal/harness/cmd/claudrecord --max-budget-usd 2
//	cd go && go run ./internal/harness/cmd/claudrecord --max-budget-usd 2 --dry-run
//
// --dry-run は recorded.json を置かず、標準出力へ出す。
//
// やること
//
//  1. 使い捨ての木（git init 済み）に、hook の入力を書き出すだけの hook を全イベントに登録した .claude/settings.json を置く
//     （fakeclaude.BuildTree。hook は teststub の claude-hook を起こす sh の包み。ccnavi は使わない）
//  2. `claude -p --model haiku --setting-sources project` を 2 回回す。1 回目は決まった手の列（fakeclaude.Prompt）、
//     2 回目は --resume でつなぎ、SessionStart の source を見る
//  3. hook が受けた JSON と stream-json を形へ正規化し、tests/contract/claude/recorded.json に置く。値は持たない
//  4. 振る舞い（止めたツールが動いたか など）を observations に置く
//  5. 生の記録（値を含む）は --raw-dir に残す。既定はリポジトリの根の scratchpad/claude-record/（追跡されない）
//
// 置く前に、記録に機密やモデルの名前・機械のパスが入っていないかを確かめ、当たれば置かない。
package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/harness/contractshape"
	"ccnavi/internal/harness/fakeclaude"
	"ccnavi/internal/infra/env"
)

// claudeTimeout は claude -p 1 回の上限（Python の timeout=900 の写し）。
const claudeTimeout = 15 * time.Minute

// recordTail は失敗したときに見せる標準エラーの末尾の長さ（バイト）。
const recordTail = 2000

// recIdentifier は、禁止する文字列のうち「REC の目印」（記録の振る舞いの文は目印を含んでよい）。
const recIdentifier = "REC の目印"

var versionRE = regexp.MustCompile(`\d+\.\d+\.\d+`)

// options は引数。
type options struct {
	budget float64
	rawDir string
	dryRun bool
}

// parseArgs は引数を読む。--max-budget-usd は要る。
func parseArgs(args []string, root string, stderr io.Writer) (options, error) {
	fs := flag.NewFlagSet("claudrecord", flag.ContinueOnError)
	fs.SetOutput(stderr)
	budget := fs.Float64("max-budget-usd", 0, "1 回の claude -p の上限（2 回回すので、合計はこの 2 倍まで）")
	rawDir := fs.String("raw-dir", filepath.Join(root, "scratchpad", "claude-record"), "生の記録の置き場")
	dryRun := fs.Bool("dry-run", false, "recorded.json を置かず、標準出力へ出す")
	if err := fs.Parse(args); err != nil {
		return options{}, err
	}
	set := false
	fs.Visit(func(f *flag.Flag) {
		if f.Name == "max-budget-usd" {
			set = true
		}
	})
	if !set {
		return options{}, errors.New("--max-budget-usd が要る")
	}
	return options{budget: *budget, rawDir: *rawDir, dryRun: *dryRun}, nil
}

// document は recorded.json の形（source・claude_version のほかは fakeclaude.Doc と同じ）。
type document struct {
	Source        string `json:"source"`
	ClaudeVersion string `json:"claude_version"`
	fakeclaude.Doc
}

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

func run(args []string, stdout, stderr io.Writer) int {
	root, err := findRoot()
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	opt, err := parseArgs(args, root, stderr)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 2
	}
	base, err := os.MkdirTemp("", "ccnavi-claude-record-")
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	defer os.RemoveAll(base)

	stub, err := buildTeststub(root, base)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	tree, err := fakeclaude.BuildTree(base, stub)
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	session := newSession()
	var streams []string
	doc, err := fakeclaude.Collect(tree, func(n int, prompt string) (string, error) {
		out, err := runClaude(tree, prompt, opt.budget, session, n > 0)
		streams = append(streams, out)
		return out, err
	})
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	if err := saveRaw(opt.rawDir, streams, filepath.Join(tree, ".rec", "log.jsonl")); err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	fmt.Fprintf(stderr, "費用の合計: %.4f USD\n", costOf(streams))
	fmt.Fprintf(stderr, "生の記録: %s\n", opt.rawDir)

	text, err := encode(document{Source: "claude", ClaudeVersion: claudeVersion(), Doc: doc})
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	if hits := leaks(doc, text); len(hits) > 0 {
		fmt.Fprintf(stderr, "記録に入れてはいけない文字列に当たった: %v。置かない\n", hits)
		fmt.Fprint(stdout, text)
		return 1
	}
	if opt.dryRun {
		fmt.Fprint(stdout, text)
		return 0
	}
	recorded := filepath.Join(root, filepath.FromSlash(fakeclaude.RecordedRel))
	if err := os.MkdirAll(filepath.Dir(recorded), 0o755); err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	if err := contractshape.WriteAtomic(recorded, text); err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	fmt.Fprintf(stderr, "置いた: %s\n", fakeclaude.RecordedRel)
	return 0
}

// findRoot は作業ディレクトリから上って go/go.mod を持つ所（リポジトリの根）を探す。
func findRoot() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go", "go.mod")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", errors.New("リポジトリの根（go/go.mod の親）が見つからない")
		}
		dir = parent
	}
}

// buildTeststub は teststub（hook の包みが起こす claude-hook）を base の下に組む。
func buildTeststub(root, base string) (string, error) {
	name := "teststub"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	out := filepath.Join(base, name)
	cmd := exec.Command("go", "build", "-o", out, "./internal/harness/cmd/teststub")
	cmd.Dir = filepath.Join(root, "go")
	cmd.Env = os.Environ()
	var log bytes.Buffer
	cmd.Stdout, cmd.Stderr = &log, &log
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("teststub を組めない: %v\n%s", err, log.String())
	}
	return out, nil
}

// newSession は claude --session-id に渡す UUID（version 4）。
func newSession() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	h := fmt.Sprintf("%x", b)
	return h[:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:]
}

// childEnv は子の環境。走らせた人の CCNAVI_* と CLAUDE_PROJECT_DIR を外す（Python の child_env）。
func childEnv(environ []string) []string {
	var out []string
	for _, kv := range environ {
		name, _, _ := strings.Cut(kv, "=")
		if strings.HasPrefix(name, env.ClaudeProjectDir) || strings.HasPrefix(name, env.Prefix) {
			continue
		}
		out = append(out, kv)
	}
	return out
}

// runClaude は claude -p を 1 回回し、標準出力（stream-json）を返す。resume なら --resume でつなぐ。
func runClaude(tree, prompt string, budget float64, session string, resume bool) (string, error) {
	sess := []string{"--session-id", session}
	if resume {
		sess = []string{"--resume", session}
	}
	args := append([]string{
		"-p", prompt,
		"--model", "haiku",
		"--output-format", "stream-json",
		"--verbose",
		"--include-hook-events",
		"--max-budget-usd", strconv.FormatFloat(budget, 'g', -1, 64),
		"--permission-mode", "auto",
		"--setting-sources", "project",
	}, sess...)
	ctx, cancel := context.WithTimeout(context.Background(), claudeTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "claude", args...)
	cmd.Dir = tree
	cmd.Env = childEnv(os.Environ())
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			return "", fmt.Errorf("claude が終了コード %d で終わった\n%s", exitErr.ExitCode(), tail(stderr.String(), recordTail))
		}
		return "", err
	}
	return stdout.String(), nil
}

// tail は文字列の末尾 n バイト（短ければ全部）。
func tail(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[len(s)-n:]
}

// claudeVersion は `claude --version` の版の番号。読めなければ unknown。
func claudeVersion() string {
	out, err := exec.Command("claude", "--version").Output()
	if err != nil {
		return "unknown"
	}
	return parseVersion(string(out))
}

func parseVersion(text string) string {
	if v := versionRE.FindString(text); v != "" {
		return v
	}
	return "unknown"
}

// costOf は stream-json の result の行の total_cost_usd の合計。
func costOf(streams []string) float64 {
	var sum float64
	for _, s := range streams {
		for _, line := range strings.Split(s, "\n") {
			line = strings.TrimSpace(line)
			if !strings.HasPrefix(line, "{") {
				continue
			}
			var e map[string]any
			if json.Unmarshal([]byte(line), &e) != nil || e["type"] != "result" {
				continue
			}
			if f, ok := e["total_cost_usd"].(float64); ok {
				sum += f
			}
		}
	}
	return sum
}

// saveRaw は生の記録（stream の行と hook のログ）を dir に置く。
func saveRaw(dir string, streams []string, hookLog string) error {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	for i, s := range streams {
		if err := os.WriteFile(filepath.Join(dir, fmt.Sprintf("stream-%d.jsonl", i+1)), []byte(s), 0o644); err != nil {
			return err
		}
	}
	data, err := os.ReadFile(hookLog)
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, "hooks.jsonl"), data, 0o644)
}

// encode は recorded.json の本文（字下げ 1、非 ASCII はそのまま、末尾に改行）。
func encode(doc document) (string, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", " ")
	if err := enc.Encode(doc); err != nil {
		return "", err
	}
	return buf.String(), nil
}

// forbidden は fakeclaude の禁止する文字列を契約の形の器の型にしたもの。
func forbidden() []contractshape.Forbidden {
	var out []contractshape.Forbidden
	for _, f := range fakeclaude.Forbidden {
		out = append(out, contractshape.Forbidden{Name: f.Name, Regex: f.Regex})
	}
	return out
}

// leaks は記録に入れてはいけない文字列の名前の一覧（重複は除く）。形（hooks・stream）は全部の禁止を見る。
// 本文（目印の REC を含む振る舞いの文）は、目印の REC 以外を見る。
func leaks(doc fakeclaude.Doc, text string) []string {
	shapes, _ := json.Marshal(map[string]any{"hooks": doc.Hooks, "stream": doc.Stream})
	hits := contractshape.ScanForbidden(string(shapes), forbidden())
	for _, h := range contractshape.ScanForbidden(text, forbidden()) {
		if h != recIdentifier {
			hits = append(hits, h)
		}
	}
	return unique(hits)
}

func unique(items []string) []string {
	set := map[string]bool{}
	var out []string
	for _, s := range items {
		if !set[s] {
			set[s] = true
			out = append(out, s)
		}
	}
	sort.Strings(out)
	return out
}
