package core

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"ccnavi/internal/harness/scenario/host"
)

// 判定の値。
const (
	Pass         = "pass"
	Fail         = "fail"
	Inconclusive = "inconclusive"
	Review       = "needs-review"
)

const (
	// DisposableRel は setup.git・setup.reset・setup.files の追記が触れてよい唯一のリポジトリ（ワークスペースルートからの相対）。
	DisposableRel = "projects/test-repo"
	// DisposableProject は DisposableRel の名前（human_run が承認してよい提案のプロジェクト）。
	DisposableProject = "test-repo"
	// Runner は実行器の入口の打ち方（go/ で打つ）。案内の文に出す。
	Runner = "go run ./internal/harness/cmd/scenarios"
	// LogName は診断ログの出どころ。
	LogName = host.LogName
)

// Refusal は回す前の拒否・停止。Error() は、理由の行・`案内:` の行・最後に `次:` の 1 行。
type Refusal struct {
	What  string
	Next  string
	Notes []string
}

func (r *Refusal) Error() string {
	lines := []string{r.What}
	for _, n := range r.Notes {
		lines = append(lines, "案内: "+n)
	}
	return strings.Join(append(lines, "次: "+r.Next), "\n")
}

// Refuse は回す前の拒否・停止の文面を out に出して、終了コード 3 を返す。
func Refuse(out func(string), err error) int {
	out(err.Error())
	return 3
}

// Clip は limit 字を超えたら切り、切ったことと全量の在りかを末尾に書く。
func Clip(text string, limit int, where string) string {
	r := []rune(text)
	if len(r) <= limit {
		return text
	}
	return fmt.Sprintf("%s…（全 %d 文字のうち先頭 %d 文字。全量は %s）", string(r[:limit]), len(r), limit, where)
}

// Result は期待ごとの結果。Expect は期待の内容（条件か文字列）。
type Result struct {
	Kind    string `json:"kind"`
	Expect  any    `json:"expect"`
	Verdict string `json:"verdict"`
	Why     string `json:"why"`
	// CostUSD は種類 cost の行だけが持つ。
	CostUSD *float64 `json:"cost_usd,omitempty"`
}

// Proposal は承認待ちの提案（チケットの識別子, プロジェクト）。
type Proposal [2]string

// ThreadRef は人間役が立てたスレッドの参照。
type ThreadRef struct {
	ID  string `json:"id"`
	IID int    `json:"iid"`
}

// HumanOp は human_api の操作の結果（state.json の human_ops の 1 件）。
type HumanOp struct {
	Step         int    `json:"step"`
	Op           string `json:"op"`
	As           string `json:"as,omitempty"`
	Thread       string `json:"thread,omitempty"`
	DiscussionID string `json:"discussion_id,omitempty"`
	IID          int    `json:"iid,omitempty"`
	Status       int    `json:"status"`
	Error        string `json:"error,omitempty"`
}

// HumanRun は human_run の 1 回の記録。
type HumanRun struct {
	Step    int      `json:"step"`
	Script  string   `json:"script"`
	Tickets []string `json:"tickets"`
	Status  string   `json:"status"`
	Exit    *int     `json:"exit,omitempty"`
	Log     string   `json:"log,omitempty"`
}

// Halt は止めた理由。Kind は結果の種類（run・expect_after・human_api・human_run）。
type Halt struct {
	Step    int      `json:"step"`
	Kind    string   `json:"kind"`
	Reasons []string `json:"reasons"`
}

// Target は使い捨てのリポジトリ（実体のパス・戻す先・ロック）。
type Target struct {
	Path string `json:"path"`
	Base string `json:"base,omitempty"`
	Lock string `json:"lock,omitempty"`
}

// State は回の状態（state.json）。
type State struct {
	Scenario   string            `json:"scenario"`
	Agent      string            `json:"agent"`
	Root       string            `json:"root"`
	Out        string            `json:"out"`
	NextStep   int               `json:"next_step"`
	SessionIDs []string          `json:"session_ids"`
	CostUSD    float64           `json:"cost_usd"`
	PreDigests map[string]string `json:"pre_digests"`
	// PreRepo は回す前の ccnavi 本体の状態。空は「回す前の状態が無い」。
	PreRepo   string   `json:"pre_repo"`
	SetupMade []string `json:"setup_made"`
	SetupDirs []string `json:"setup_dirs"`
	IssueNo   int      `json:"issue_no,omitempty"`
	Target    *Target  `json:"target,omitempty"`
	Released  bool     `json:"released,omitempty"`
	Held      bool     `json:"held,omitempty"`

	Halt            *Halt                `json:"halt,omitempty"`
	Host            *host.Host           `json:"host,omitempty"`
	HostSnapshots   []host.SnapResult    `json:"host_snapshots,omitempty"`
	HumanOps        []HumanOp            `json:"human_ops,omitempty"`
	Threads         map[string]ThreadRef `json:"threads,omitempty"`
	HumanRuns       []HumanRun           `json:"human_runs,omitempty"`
	ProposalsBefore []Proposal           `json:"proposals_before"`
}

// Save は state.json を out に書く。
func Save(out string, st *State) error {
	data, err := json.MarshalIndent(st, "", " ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(out, "state.json"), data, 0o644)
}

// LoadState は out の state.json を読む。
func LoadState(out string) (*State, error) {
	data, err := os.ReadFile(filepath.Join(out, "state.json"))
	if err != nil {
		return nil, err
	}
	st := &State{}
	return st, json.Unmarshal(data, st)
}
