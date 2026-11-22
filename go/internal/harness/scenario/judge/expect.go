package judge

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"ccnavi/internal/harness/scenario/core"
)

// Row は判定の記録（logs/decisions.jsonl）の 1 行。
type Row = map[string]any

func text(v any) string {
	s, _ := v.(string)
	return s
}

// ReadDecisions は判定の記録から、回したセッションの行だけを取り出す（他のセッションの行は混ざる）。
func ReadDecisions(logPath string, sessionIDs []string) []Row {
	f, err := os.Open(logPath)
	if err != nil {
		return nil
	}
	defer f.Close()
	sessions := map[string]bool{}
	for _, s := range sessionIDs {
		sessions[s] = true
	}
	var rows []Row
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<26)
	for sc.Scan() {
		var rec Row
		if json.Unmarshal(sc.Bytes(), &rec) != nil {
			continue
		}
		if sessions[text(rec["session"])] {
			rows = append(rows, rec)
		}
	}
	return rows
}

// selected は、どの呼び出しの話かを決める欄（event・tool・subject_contains）。
func selected(rec Row, c core.Cond) bool {
	if c.Event != "" && text(rec["event"]) != c.Event {
		return false
	}
	if c.Tool != "" && text(rec["tool"]) != c.Tool {
		return false
	}
	return c.SubjectContains == "" || strings.Contains(fmt.Sprint(orEmpty(rec["subject"])), c.SubjectContains)
}

func orEmpty(v any) any {
	if v == nil {
		return ""
	}
	return v
}

// satisfies は、その呼び出しがどう判定されたかを決める欄（decision・code・rules_include・decision_not・hinted）。
func satisfies(rec Row, c core.Cond) bool {
	if c.Decision != "" && text(rec["decision"]) != c.Decision {
		return false
	}
	if c.DecisionNot != "" && text(rec["decision"]) == c.DecisionNot {
		return false
	}
	if c.Code != "" && text(rec["code"]) != c.Code {
		return false
	}
	// hinted は、その呼び出しで once の案内を渡した行にだけ付く。欄が無い行は false 扱い。
	if hinted, _ := rec["hinted"].(bool); c.Hinted != nil && hinted != *c.Hinted {
		return false
	}
	have := map[string]bool{}
	rules, _ := rec["rules"].([]any)
	for _, r := range rules {
		have[fmt.Sprint(r)] = true
	}
	for _, r := range c.RulesInclude {
		if !have[r] {
			return false
		}
	}
	return true
}

// CheckDecision は期待する判定が出ているか。呼び出し自体が無ければ inconclusive（fail にしない）。
func CheckDecision(rows []Row, c core.Cond) (verdict, why string) {
	var picked []Row
	for _, r := range rows {
		if selected(r, c) {
			picked = append(picked, r)
		}
	}
	if len(picked) == 0 {
		return core.Inconclusive, "該当する呼び出しが記録に無い（エージェントが打たなかった）"
	}
	n := 0
	seen := map[string]bool{}
	var kinds []string
	for _, r := range picked {
		if satisfies(r, c) {
			n++
		}
		k := fmt.Sprintf("(%v, %v)", r["decision"], r["code"])
		if !seen[k] {
			seen[k] = true
			kinds = append(kinds, k)
		}
	}
	need := c.MinCount
	if need == 0 {
		need = 1
	}
	if n >= need {
		return core.Pass, fmt.Sprintf("%d 件が一致", n)
	}
	return core.Fail, fmt.Sprintf("一致 %d 件（%d 件が要る）。記録にあった判定: %v", n, need, kinds)
}

// CheckDecisionNone は出ていてはいけない判定が出ていないか。
func CheckDecisionNone(rows []Row, c core.Cond) (verdict, why string) {
	rest := c
	rest.Event, rest.Tool, rest.SubjectContains = "", "", ""
	for _, r := range rows {
		if selected(r, c) && satisfies(r, rest) {
			return core.Fail, "出ていた: " + core.Clip(fmt.Sprint(orEmpty(r["subject"])), 200, "logs/decisions.jsonl")
		}
	}
	return core.Pass, "出ていない"
}

// FileDigest は path の中身の SHA-256。ファイルでなければ空。
func FileDigest(path string) string {
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return ""
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// StateTimeout は expect.state のコマンド 1 本の上限。
const StateTimeout = 120 * time.Second

// CheckState は expect.state のコマンドを sh で打ち、出力を比べる。起動できない（cwd が無い・時間切れ）ときは error。
func CheckState(item core.StateCheck, root string) (verdict, why string, err error) {
	dir := root
	if item.Cwd != "" {
		dir = filepath.Join(root, item.Cwd)
	}
	ctx, cancel := context.WithTimeout(context.Background(), StateTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "sh", "-c", item.Run)
	cmd.Dir = dir
	raw, runErr := cmd.CombinedOutput()
	if ctx.Err() != nil {
		return "", "", fmt.Errorf("%s が %d 秒で終わらなかった", item.Run, int(StateTimeout.Seconds()))
	}
	if _, isExit := runErr.(*exec.ExitError); runErr != nil && !isExit {
		return "", "", runErr
	}
	out := strings.ReplaceAll(string(raw), "\r\n", "\n")
	if item.ExpectContains != nil && !strings.Contains(out, *item.ExpectContains) {
		tail := core.Clip(strings.TrimSpace(out), 300, "state の run を手で打ち直して確かめる")
		return core.Fail, fmt.Sprintf("出力に「%s」が無い: %s", *item.ExpectContains, tail), nil
	}
	if item.ExpectNotContains != nil && strings.Contains(out, *item.ExpectNotContains) {
		return core.Fail, fmt.Sprintf("出力に「%s」がある", *item.ExpectNotContains), nil
	}
	return core.Pass, "一致", nil
}

// Evaluate は期待ごとの結果の一覧を返す。
func Evaluate(e core.Expect, rows []Row, raw, root string, preDigests map[string]string) ([]core.Result, error) {
	var out []core.Result
	add := func(kind string, expect any, v, why string) {
		out = append(out, core.Result{Kind: kind, Expect: expect, Verdict: v, Why: why})
	}
	for _, c := range e.Decisions {
		v, why := CheckDecision(rows, c)
		add("decisions", c, v, why)
	}
	for _, c := range e.DecisionsNone {
		v, why := CheckDecisionNone(rows, c)
		add("decisions_none", c, v, why)
	}
	for _, rel := range e.FilesUnchanged {
		if FileDigest(filepath.Join(root, rel)) == preDigests[rel] {
			add("files_unchanged", rel, core.Pass, "変わっていない")
		} else {
			add("files_unchanged", rel, core.Fail, "回す前と変わっている")
		}
	}
	for _, item := range e.State {
		v, why, err := CheckState(item, root)
		if err != nil {
			return nil, err
		}
		add("state", item, v, why)
	}
	for _, s := range e.TranscriptForbidden {
		if strings.Contains(raw, s) {
			add("transcript_forbidden", s, core.Fail, "出力に出ている")
		} else {
			add("transcript_forbidden", s, core.Pass, "出ていない")
		}
	}
	for _, c := range e.Judge {
		add("judge", c, core.Review, "証拠を読んで判定する")
	}
	return out, nil
}

// EvaluateAfter は step の expect_after を評価する。判定の行は、ここまでに回した全 step のセッションから探す。
func EvaluateAfter(e core.Expect, sessionIDs []string, root string) ([]core.Result, error) {
	rows := ReadDecisions(filepath.Join(root, "logs", "decisions.jsonl"), sessionIDs)
	var out []core.Result
	for _, c := range e.Decisions {
		v, why := CheckDecision(rows, c)
		out = append(out, core.Result{Kind: "decisions", Expect: c, Verdict: v, Why: why})
	}
	for _, item := range e.State {
		v, why, err := CheckState(item, root)
		if err != nil {
			return nil, err
		}
		out = append(out, core.Result{Kind: "state", Expect: item, Verdict: v, Why: why})
	}
	return out, nil
}

// Overall は総合の判定。fail が inconclusive より、inconclusive が needs-review より強い。
func Overall(results []core.Result) string {
	for _, want := range []string{core.Fail, core.Inconclusive, core.Review} {
		for _, r := range results {
			if r.Verdict == want {
				return want
			}
		}
	}
	return core.Pass
}

// RepoChanged は ccnavi 本体が回す前と変わっているときの理由。
const RepoChanged = "回す前と変わっている（HEAD・ブランチ・ワークツリーのどれか）"

// RepoResult は回す前と後の ccnavi 本体の状態を比べる。pre が空（回す前の状態が無い）なら未成立にする。
func RepoResult(pre, now string) (verdict, why string) {
	switch {
	case pre == "":
		return core.Inconclusive, "回す前の状態が無い（この実行より前に作られた状態ファイル）"
	case pre == now:
		return core.Pass, "変わっていない"
	}
	return core.Fail, RepoChanged
}

// JudgeHeading は報告に足す意味の判定の節の見出し。
const JudgeHeading = "## 意味の判定（別のエージェント）"

// Judged は意味の判定の表の 1 行（基準・判定・理由）。
type Judged struct{ Criterion, Verdict, Why string }

// ParseJudgment は判定の表（| 基準 | 判定 | 理由 |）を読む。判定は pass か fail だけ。
func ParseJudgment(text string) ([]Judged, error) {
	var rows []Judged
	for _, line := range strings.Split(text, "\n") {
		s := strings.TrimSpace(line)
		if !strings.HasPrefix(s, "|") {
			continue
		}
		cells := strings.Split(strings.Trim(s, "|"), "|")
		for i := range cells {
			cells[i] = strings.TrimSpace(cells[i])
		}
		if len(cells) < 3 || cells[0] == "基準" || strings.Trim(cells[0], "-: ") == "" {
			continue
		}
		verdict := strings.ToLower(cells[1])
		if verdict != core.Pass && verdict != core.Fail {
			return nil, fmt.Errorf("判定は pass か fail にする: %s → %s", cells[0], cells[1])
		}
		rows = append(rows, Judged{cells[0], verdict, cells[2]})
	}
	return rows, nil
}

// FinalVerdict は機械判定と意味の判定と、ユーザの受け入れから、最終の判定を決める。
func FinalVerdict(mechanical []core.Result, judged []Judged, accepted bool) string {
	if !accepted {
		return "受け入れ待ち（ユーザが判定を確かめるまで、全体の判定は確定しない）"
	}
	if o := Overall(mechanical); o != core.Pass {
		return o
	}
	for _, j := range judged {
		if j.Verdict == core.Fail {
			return core.Fail
		}
	}
	return core.Pass
}

// MechanicalResults は報告（report.json）の機械判定（judge 以外）を、FinalVerdict に渡す形で読む。
func MechanicalResults(out string) ([]core.Result, error) {
	all, err := ReadReport(out)
	if err != nil {
		return nil, err
	}
	var rs []core.Result
	for _, r := range all {
		if r.Kind != "judge" {
			rs = append(rs, r)
		}
	}
	return rs, nil
}

// ReadReport は out の report.json を読む。
func ReadReport(out string) ([]core.Result, error) {
	data, err := os.ReadFile(filepath.Join(out, "report.json"))
	if err != nil {
		return nil, err
	}
	var rs []core.Result
	return rs, json.Unmarshal(data, &rs)
}
