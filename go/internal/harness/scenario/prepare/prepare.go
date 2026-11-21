package prepare

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"ccnavi/internal/harness/scenario/host"
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

// Halt は止めた理由。
type Halt struct {
	Step    int      `json:"step"`
	Kind    string   `json:"kind"`
	Reasons []string `json:"reasons"`
}

// State は state.json のうち、準備・相手・人間役の操作が触る欄。
type State struct {
	NextStep        int                  `json:"next_step"`
	Halt            *Halt                `json:"halt,omitempty"`
	Host            *host.Host           `json:"host,omitempty"`
	HostSnapshots   []host.SnapResult    `json:"host_snapshots,omitempty"`
	HumanOps        []HumanOp            `json:"human_ops,omitempty"`
	Threads         map[string]ThreadRef `json:"threads,omitempty"`
	HumanRuns       []HumanRun           `json:"human_runs,omitempty"`
	ProposalsBefore []Proposal           `json:"proposals_before"`
}

// File は setup.files の 1 件。Append は、対象にある管理下のファイルへの追記（作って消す形ではない）。
type File struct {
	Rel    string
	Body   string
	Append bool
}

// Hooks は実行器の芯が持つ処理の差し込み口。
type Hooks struct {
	// ResolveAppend は追記の対象（ワークスペースルートからの相対）を実体にする。断るときは *Refusal。
	ResolveAppend func(rel, root, top string) (string, error)
	// CommitExists は対象に base がコミットとしてあるか。
	CommitExists func(top, base string) bool
}

// CheckBeforeRun は回す前（確認文より前）に確かめられる setup の誤りを返す。何も変えない。
// 追記の対象は reset のあとにもう一度確かめる（base の中身で変わりうるため）。
func CheckBeforeRun(h Hooks, files []File, resetBase string, kind host.Kind, m *host.Manager, root, top string) error {
	for _, f := range files {
		if f.Append {
			if _, err := h.ResolveAppend(f.Rel, root, top); err != nil {
				return err
			}
		}
	}
	if resetBase != "" && !h.CommitExists(top, resetBase) {
		return &Refusal{
			What:  fmt.Sprintf("setup.reset.base %s が対象 %s にコミットとして無い", resetBase, top),
			Next:  "base を、対象にあるコミットの 40 桁の SHA に直して打ち直す",
			Notes: []string{"対象には何もしていない"},
		}
	}
	if kind == host.DockerGitLab {
		if _, err := m.CheckDocker(); err != nil {
			return &Refusal{
				What:  fmt.Sprintf("docker-gitlab の GitLab を使えない: %v", err),
				Next:  "GitLab を立て（動いていれば status で確かめる。.agents/docs/gitlab-manual.md）、打ち直す",
				Notes: []string{"対象にも GitLab にも何もしていない"},
			}
		}
	}
	return nil
}

// Clashes は setup.files のパスのうち、回せない原因になるもの。作る形は、すでにあるもの（後片付けで消してしまうため）。
// 追記の形は、まだ無いもの（追記は対象にある管理下のファイルにだけ使う）。
func Clashes(files []File, root string) []string {
	var out []string
	for _, f := range files {
		if _, err := os.Lstat(filepath.Join(root, f.Rel)); (err == nil) != f.Append {
			out = append(out, f.Rel)
		}
	}
	return out
}

// MakeFiles は setup のファイルを作り、追記する。(作ったファイル, 作ったディレクトリ) を返す。
// 作る形は、既にあるパスには触らない（Clashes で先に確かめてある）。ディレクトリは、新しく作ったものだけ。
// 追記は作ったものに数えない（戻すのは setup.reset。消すと管理下のファイルを削除してしまう）。
// 追記の対象は、先に全部確かめてから（top は判定を通した対象）、書き始める。
func MakeFiles(h Hooks, files []File, root, top string) (made, dirs []string, err error) {
	appends := map[string]string{}
	var order []string
	for _, f := range files {
		if !f.Append {
			continue
		}
		if top == "" {
			return nil, nil, &Refusal{What: "追記の対象を決められない（対象の判定を通っていない）", Next: "setup を直す"}
		}
		real, err := h.ResolveAppend(f.Rel, root, top)
		if err != nil {
			return nil, nil, err
		}
		if _, ok := appends[real]; !ok {
			order = append(order, real)
		}
		appends[real] += f.Body
	}
	fail := func(err error) ([]string, []string, error) {
		RemoveFiles(made, dirs, root) // 途中で失敗したとき、ここまでに作ったものを残さない
		return nil, nil, err
	}
	for _, f := range files {
		if f.Append {
			continue
		}
		p := filepath.Join(root, f.Rel)
		var missing []string
		for d := filepath.Dir(p); d != filepath.Clean(root); d = filepath.Dir(d) {
			if _, err := os.Stat(d); err == nil {
				break
			}
			missing = append(missing, d)
		}
		for i := len(missing) - 1; i >= 0; i-- {
			if err := os.Mkdir(missing[i], 0o755); err != nil {
				return fail(err)
			}
			rel, _ := filepath.Rel(root, missing[i])
			dirs = append(dirs, rel)
		}
		if err := os.WriteFile(p, []byte(f.Body), 0o644); err != nil {
			return fail(err)
		}
		made = append(made, f.Rel)
	}
	for _, real := range order {
		fh, err := os.OpenFile(real, os.O_APPEND|os.O_WRONLY, 0)
		if err != nil {
			return fail(err)
		}
		_, err = fh.WriteString(appends[real])
		if cerr := fh.Close(); err == nil {
			err = cerr
		}
		if err != nil {
			return fail(err)
		}
	}
	return made, dirs, nil
}

// RemoveFiles は MakeFiles が作ったものを消す。ディレクトリは深いものから、空のときだけ。
func RemoveFiles(made, dirs []string, root string) {
	for _, rel := range made {
		if p := filepath.Join(root, rel); isFile(p) {
			_ = os.Remove(p)
		}
	}
	sort.Slice(dirs, func(i, j int) bool { return len(dirs[i]) > len(dirs[j]) })
	for _, rel := range dirs {
		_ = os.Remove(filepath.Join(root, rel)) // 空でなければ消えない
	}
}

func isFile(p string) bool {
	info, err := os.Lstat(p)
	return err == nil && info.Mode().IsRegular()
}

// HostEnv は claude に渡す環境。setup.host が real 以外のときだけ作る（real は nil）。
func HostEnv(m *host.Manager, st *State, parent []string) (map[string]string, error) {
	if st.Host == nil || st.Host.Down {
		return nil, nil
	}
	return m.StepEnv(st.Host, parent)
}

// TeardownHost は模擬を止め、スタブの置き場を消す（何度呼んでもよい）。docker-gitlab のプロジェクトを消せなかったとき
// （手作業が要る）は true。
func TeardownHost(m *host.Manager, st *State, root string) bool {
	return st.Host != nil && m.Teardown(root, st.Host)
}

// StartHost は setup.host が bare / fake-gitlab / docker-gitlab なら、相手を用意して state に残す。save は state.json を書く。
func StartHost(m *host.Manager, st *State, setup host.Setup, scenarioID, root, top string, save func() error) error {
	if !host.UsesStubGit(setup.Kind) {
		return nil
	}
	h, err := m.Prepare(root, top, setup, scenarioID)
	if err != nil {
		return &Refusal{
			What:  fmt.Sprintf("setup.host の相手を用意できなかった: %v", err),
			Next:  fmt.Sprintf("対象 %s の origin を確かめて打ち直す", top),
			Notes: []string{"対象は後始末で戻す"},
		}
	}
	st.Host = h
	return save()
}

// SnapshotHost は setup.host が fake-gitlab / docker-gitlab のとき、step（idx は 0 始まり）の終わりの相手側の状態を
// host-<step>.json に残す。API が個々に失敗したことは記録に残して続ける。記録できなかったとき（書けないなど）はエラーを
// 返すので、呼び手は step を止めて後片付けする。
func SnapshotHost(m *host.Manager, st *State, idx int, root, out string, save func() error) error {
	h := st.Host
	if h == nil || h.Down {
		return nil
	}
	snapshot := false
	for _, k := range host.SnapshotKinds() {
		snapshot = snapshot || h.Kind == k
	}
	if !snapshot {
		return nil
	}
	result, err := m.Take(root, h, idx+1, out)
	if err != nil {
		return err
	}
	st.HostSnapshots = append(st.HostSnapshots, result)
	return save()
}
