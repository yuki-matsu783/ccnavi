package gitlabprobe

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/infra/env"
)

// Config は 1 回の probe の入力。
type Config struct {
	// Root はリポジトリの根（sh と .ccnavi の元）。GitLab は接続先の根。
	Root, GitLab string
	// RootToken・ReviewerToken はエージェント役（root）と人間役のトークン。
	RootToken, ReviewerToken string
	// Exe は組み立て済みの ccnavi。Out は結果の置き場の親（空なら一時ディレクトリ）。Prefix はプロジェクト名の前置き。Keep なら作ったプロジェクトを消さない。
	Exe, Out, Prefix string
	Keep             bool
	// Environ は親の環境（`名前=値`）。
	Environ []string
	Stdout  io.Writer
	// Now は日時の刻み（nil なら time.Now）。
	Now func() time.Time
}

// Result は 1 段階の結果。State は PASS・SKIP・FAIL。
type Result struct{ Name, State, Detail string }

// ProjectName はプロジェクト名。他の作業と区別したいときは前置きを変える。
func ProjectName(prefix, stamp string) string { return prefix + "-" + stamp }

// SummaryLine は結果の数のまとめ。
func SummaryLine(results []Result) string {
	count := map[string]int{}
	for _, r := range results {
		count[r.State]++
	}
	return fmt.Sprintf("PASS %d / SKIP %d / FAIL %d", count["PASS"], count["SKIP"], count["FAIL"])
}

// ApprovedCopyPath は承認済みチケットの置き場（`.ccnavi/approved/<doing|done>/<チケット>.md`）。
// 取り消しは置き場でなく欄なので、取り消した子も done/ に在る。
func ApprovedCopyPath(tree, state, ticket string) string {
	return filepath.Join(tree, ".ccnavi", "approved", state, ticket+".md")
}

// approvalFields は承認のあとに ccnavi が書く欄。改版の提案では空にする（approval の検査が求める）。
var approvalFields = []string{"started_at", "completed_at", "base_sha", "merged_sha", "cancelled_at", "cancel_reason", "finished_to", "settled_by"}

// FeedbackRevision は承認済みの親の全文に `feedback: []` を足した改版の本文。ゼロから作り直さない（plan・phases・範囲は承認済みのまま）。
// 承認のあとに ccnavi が書いた欄は、提案には書かない決まりなので空にする。すでに feedback があればエラー。
func FeedbackRevision(approved string) (string, error) {
	lines := strings.Split(approved, "\n")
	for _, l := range lines {
		if strings.HasPrefix(l, "feedback:") {
			return "", fmt.Errorf("feedback が既にある")
		}
	}
	end := -1
	for i := 1; i < len(lines); i++ {
		if lines[i] == "---" {
			end = i
			break
		}
	}
	if end < 0 {
		return "", fmt.Errorf("frontmatter の終わりが無い")
	}
	at := -1
	for i := 1; i < end; i++ { // 本文の行は触らない
		for _, key := range approvalFields {
			if strings.HasPrefix(lines[i], key+":") {
				lines[i] = key + `: ""`
			}
		}
		if at < 0 && strings.HasPrefix(lines[i], "human_review:") {
			at = i
		}
	}
	if at < 0 {
		return "", fmt.Errorf("human_review: の行が無い")
	}
	out := append(append(append([]string{}, lines[:at]...), "feedback: []"), lines[at:]...)
	return strings.Join(out, "\n"), nil
}

var cancelledAt = regexp.MustCompile(`(?m)^cancelled_at:\s*"?[^\s"]`)

// IsCancelledCopy は承認済みのコピーの `cancelled_at` が空でない（取り消した印）。
func IsCancelledCopy(text string) bool { return cancelledAt.MatchString(text) }

// TicketOpts は TicketText の入力。Parent が空なら親。MR は親の MR の番号（0 なら 1）。
type TicketOpts struct {
	Parent string
	Phase  int
	Allow  []string
	Review bool
	Title  string
	Issue  int
	MR     int
}

// TicketText は提案の本文。親（parent が空）は MR の番号を持ち、計画（1 フェーズ）を足す。
func TicketText(name string, o TicketOpts) string {
	mr := o.MR
	if mr == 0 {
		mr = 1
	}
	lines := []string{"---", "version: 1", "ticket: " + name}
	if o.Parent != "" {
		lines = append(lines, "parent: "+o.Parent, fmt.Sprintf("phase: %d", o.Phase))
	} else {
		// 親は MR の番号を持つ（ccnavi の親チケットは issue と MR のセット）
		lines = append(lines, fmt.Sprintf("mr: %d", mr))
	}
	if o.Issue != 0 {
		lines = append(lines, fmt.Sprintf("issue: %d", o.Issue))
	}
	lines = append(lines, "human_review:", fmt.Sprintf("  required: %t", o.Review), "  reason: 実測",
		"title: "+o.Title, "rationale: |", "  実物の GitLab に適用する実測。")
	if len(o.Allow) > 0 {
		lines = append(lines, "allow:")
		for _, g := range o.Allow {
			lines = append(lines, "  - match: Write|Edit", fmt.Sprintf("    glob: %q", g))
		}
	}
	lines = append(lines, `started_at: ""`, `completed_at: ""`, `base_sha: ""`, "---", "", "本文（ユーザが読む説明）", "")
	// 親には計画（1 フェーズ）を足す。子は PlainParent が何もしない
	return samples.PlainParent(strings.Join(lines, "\n"), samples.Work(1))
}

const rulesJSON = `{"version": 1, "deny": [{"id": "guard-approved", "match": "Write|Edit|NotebookEdit", "glob": "*/.ccnavi/*", "message": "ガードの設定と承認済みチケットです。ユーザに依頼してください。"}]}`

// requestMarker は依頼の目印。製品の形のまま照合する（review_host.MARKER_REQUEST に key=<16 桁の 16 進> が続く）。
var requestMarker = regexp.MustCompile(`^<!-- ccnavi:request i0001:1 key=[0-9a-f]{16} -->$`)

// Usage は --help の本文。
func Usage() string {
	return strings.TrimSpace(`gitlabmanual probe: 実物の GitLab に sh 5 本と実行ファイルを適用して 1 周する。

用意するもの:
  - 動いている GitLab（既定 http://127.0.0.1:8929。CCNAVI_PROBE_GITLAB で変える。gitlabmanual up で立てる）
  - トークン 2 本: GITLAB_TOKEN（root、エージェント役）と CCNAVI_PROBE_REVIEWER_TOKEN（人間役）。
    up が書く 0600 のファイルから読む（set -a; . <status が出すパス>; set +a でも渡せる）
  - 組み立て済みの実行ファイル（dist/ccnavi/ccnavi。CCNAVI_ENTRY_PATH で差し替え）、jq と curl

環境変数:
  CCNAVI_PROBE_PREFIX  作るプロジェクト名の前置き（既定 ccnavi-probe。名前は <前置き>-<日時>）
  CCNAVI_PROBE_OUT     結果と生の JSON の置き場（既定は一時ディレクトリ）
  CCNAVI_PROBE_KEEP    空でなければ、作ったプロジェクトを消さない

まとめは PASS n / SKIP m / FAIL k。SKIP は確かめられなかった段階（CE の変更要求）。FAIL があれば終了コードは 1。`)
}

// ResolveExe は組み立て済みの実行ファイルを探す（CCNAVI_ENTRY_PATH が絶対パスならそれ、相対なら根から。無ければ dist/ccnavi/ccnavi）。
func ResolveExe(root, given string) (string, error) {
	if given != "" && filepath.IsAbs(given) {
		return given, nil
	}
	if given == "" {
		given = filepath.Join("dist", "ccnavi", "ccnavi")
	}
	path := filepath.Join(root, given)
	for _, c := range []string{path, path + ".exe"} {
		if info, err := os.Stat(c); err == nil && !info.IsDir() {
			return c, nil
		}
	}
	return "", fmt.Errorf("exe が無い (%s)。scripts/ccnavi-build.sh で組み立てる", path)
}

// Main は引数と環境から Config を作って回す。終了コードを返す。
func Main(args []string, root string, environ []string, stdout, stderr io.Writer) int {
	for _, a := range args {
		if a == "-h" || a == "--help" {
			fmt.Fprintln(stdout, Usage())
			return 0
		}
	}
	get := func(name string) string {
		for i := len(environ) - 1; i >= 0; i-- {
			if v, ok := strings.CutPrefix(environ[i], name+"="); ok {
				return v
			}
		}
		return ""
	}
	cfg := Config{
		Root: root, Environ: environ, Stdout: stdout,
		RootToken: get(env.GitLabToken), ReviewerToken: get(env.ProbeReviewerToken),
		Out: get(env.ProbeOut), Prefix: get(env.ProbePrefix), Keep: get(env.ProbeKeep) != "",
		GitLab: get(env.ProbeGitLab),
	}
	if cfg.Prefix == "" {
		cfg.Prefix = "ccnavi-probe"
	}
	if cfg.GitLab == "" {
		port := get(env.GitLabPort)
		if port == "" {
			port = "8929"
		}
		cfg.GitLab = "http://127.0.0.1:" + port
	}
	cfg.GitLab = strings.TrimRight(cfg.GitLab, "/")
	if cfg.RootToken == "" || cfg.ReviewerToken == "" {
		fmt.Fprintln(stderr, "GITLAB_TOKEN と CCNAVI_PROBE_REVIEWER_TOKEN が要る。tests/manual/gitlab/make_gitlab_tokens.rb で作る（gitlabmanual up が作って tokens.env に書く）")
		return 2
	}
	exe, err := ResolveExe(root, get(env.EntryPath))
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 2
	}
	cfg.Exe = exe
	return Run(cfg, stderr)
}

// jsonText は JSON を日本語のまま整形する。
func jsonText(v any) string {
	data, err := json.MarshalIndent(v, "", " ")
	if err != nil {
		return fmt.Sprint(v)
	}
	return string(data)
}
