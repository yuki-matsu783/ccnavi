package judge

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/infra/pathcase"
)

const (
	// EvidenceLimit は拒否・案内などを証拠に出すときの文字数の上限。
	EvidenceLimit = 1000
	// ContextLimit は渡った文脈の 1 件を出すときの上限。
	ContextLimit = 2000
	// RepeatMin は反復拒否と数える連続の回数。
	RepeatMin = 3
	// EmptyReason は理由文が空の拒否に入れる文。
	EmptyReason = "(理由文が空)"
	// cutMark は decisions.jsonl の subject が上限で切られた目印（go/internal/records/audit の limited）。
	cutMark = "…(+"
	// agentTool は Agent の呼び出しのツール名。
	agentTool = "Agent"
)

var (
	contextEvents = []string{"SessionStart", "UserPromptSubmit", "PreToolUse"}
	guideHeads    = []string{"次:", "案内:", "必須:"}
	writeTools    = []string{"Edit", "Write"}
	// 次の手を示す語。hook の拒否文は、次の手をマーカーのない行頭の文で書くことが多いので、マーカーの行に加えて、
	// 依頼・指示の言い回しも見る。「使う」「確かめ」のように、禁止の説明にも出る語は入れない。
	guidePhrases = []string{"ください", "頼んで", "依頼", "使って", "使います", "従って", "してから", "書き直", "打ち直", "伝えて", "報告して"}
	guideCommand = regexp.MustCompile("`[^`\n]+`") // 打つ形をコード片で示す文
)

type obj = map[string]any

func asObj(v any) obj { m, _ := v.(obj); return m }

func hookPayload(ev obj) obj {
	for _, key := range []string{"stdout", "output"} {
		raw, ok := ev[key].(string)
		if !ok || !strings.HasPrefix(strings.TrimLeft(raw, " \t\r\n"), "{") {
			continue
		}
		var data obj
		if json.Unmarshal([]byte(raw), &data) == nil && data != nil {
			return data
		}
	}
	return obj{}
}

func hookName(ev obj) string {
	for _, k := range []string{"hook_event", "hook_event_name"} {
		if s := text(ev[k]); s != "" {
			return s
		}
	}
	return "?"
}

func labelOf(ev obj) string {
	if s := text(ev["hook_name"]); s != "" {
		return s
	}
	return hookName(ev)
}

// decisionOf は hook の応答の判定（deny・ask・block）と理由。
func decisionOf(data obj) (decision, reason string) {
	spec := asObj(data["hookSpecificOutput"])
	decision = text(spec["permissionDecision"])
	if decision == "" {
		decision = text(data["decision"])
	}
	reason = text(spec["permissionDecisionReason"])
	if reason == "" {
		reason = text(data["reason"])
	}
	return decision, reason
}

func isHookResponse(ev obj) bool { return ev["subtype"] == "hook_response" }

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

func dumps(v any) string {
	var b strings.Builder
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	return strings.TrimRight(b.String(), "\n")
}

// SummarizeHooks は hook の応答を、判定に要るものの行と、省いたものの件数に分ける。
// 残すのは、拒否・確認、案内文（SessionStart を除く）、中身のある Stop の応答、終了コードが 0 でない応答、停止、
// systemMessage（SessionStart を除く）。SessionStart と出力が空の応答は件数だけにする。
func SummarizeHooks(events []map[string]any, where string) (lines []string, omitted map[string]int) {
	omitted = map[string]int{}
	for _, ev := range events {
		if !isHookResponse(ev) {
			continue
		}
		name := hookName(ev)
		data := hookPayload(ev)
		decision, reason := decisionOf(data)
		spec := asObj(data["hookSpecificOutput"])
		ctx := text(spec["additionalContext"])
		label := strings.TrimSpace(name + " " + text(ev["hook_name"]))
		exitCode, hasExit := ev["exit_code"].(float64)
		switch {
		case decision == "deny" || decision == "ask" || decision == "block":
			lines = append(lines, fmt.Sprintf("- [%s] %s: %s", label, decision, reason)) // 拒否の文面は切らない
		case hasExit && exitCode != 0:
			body := strings.TrimSpace(text(ev["stderr"]))
			if body == "" {
				body = strings.TrimSpace(text(ev["stdout"]))
			}
			if body == "" {
				body = text(ev["output"])
			}
			lines = append(lines, fmt.Sprintf("- [%s] 終了コード %d: %s", label, int(exitCode), body))
		case data["continue"] == false || text(data["stopReason"]) != "":
			lines = append(lines, fmt.Sprintf("- [%s] 停止: %s", label, text(data["stopReason"])))
		case name == "Stop" && (len(data) > 0 || text(ev["stdout"]) != "" || text(ev["stderr"]) != ""):
			body := text(ev["stdout"])
			if len(data) > 0 {
				body = dumps(data)
			} else if body == "" {
				body = text(ev["stderr"])
			}
			lines = append(lines, fmt.Sprintf("- [%s] %s", label, core.Clip(body, EvidenceLimit, where)))
		case ctx != "" && name != "SessionStart":
			lines = append(lines, fmt.Sprintf("- [%s] 案内: %s", label, core.Clip(ctx, ContextLimit, where)))
		case text(data["systemMessage"]) != "" && name != "SessionStart":
			lines = append(lines, fmt.Sprintf("- [%s] 通知: %s", label, core.Clip(text(data["systemMessage"]), ContextLimit, where)))
		default:
			omitted[name]++
		}
	}
	return lines, omitted
}

// HookDenialTexts は拒否・確認・ブロックの hook 応答にある理由の文面を集める。ツール結果のエラーとの重複判定に使う。
func HookDenialTexts(events []map[string]any) []string {
	var texts []string
	for _, ev := range events {
		if !isHookResponse(ev) {
			continue
		}
		decision, reason := decisionOf(hookPayload(ev))
		reason = strings.TrimSpace(reason)
		if (decision == "deny" || decision == "ask" || decision == "block") && reason != "" {
			texts = append(texts, reason)
		}
	}
	return texts
}

// DuplicatesHook はエラーの文面が、hook の拒否理由と（前後の空白を除いて）まったく同じなら true。
func DuplicatesHook(t string, denials []string) bool {
	t = strings.TrimSpace(t)
	return t != "" && contains(denials, t)
}

// Item は hook の応答からエージェントかユーザに渡る文面。Kind は 拒否（deny・block）・確認（ask）・案内（additionalContext）。
type Item struct{ Event, Label, Kind, Text string }

// HookItems は hook の応答から、エージェントかユーザに渡る文面を取り出す。1 つの応答が理由と案内の両方を持てば 2 件になる。
func HookItems(events []map[string]any) []Item {
	var items []Item
	for _, ev := range events {
		if !isHookResponse(ev) {
			continue
		}
		data := hookPayload(ev)
		decision, reason := decisionOf(data)
		base := Item{Event: hookName(ev), Label: labelOf(ev)}
		if (decision == "deny" || decision == "ask" || decision == "block") && reason != "" {
			it := base
			it.Kind, it.Text = "拒否", strings.TrimSpace(reason)
			if decision == "ask" {
				it.Kind = "確認"
			}
			items = append(items, it)
		}
		if ctx := text(asObj(data["hookSpecificOutput"])["additionalContext"]); ctx != "" {
			it := base
			it.Kind, it.Text = "案内", strings.TrimSpace(ctx)
			items = append(items, it)
		}
	}
	return items
}

// Fence は t を囲むコードフェンスの行。t の中のバッククォートの列より長くする。
func Fence(t string) []string {
	n := 2
	for _, m := range regexp.MustCompile("`+").FindAllString(t, -1) {
		if len(m) > n {
			n = len(m)
		}
	}
	bar := strings.Repeat("`", n+1)
	return []string{bar + "text", t, bar}
}

// StepStreams は出力ディレクトリの step-N.jsonl を ParseStream して {N: 結果} にする。Nums は N の昇順。
type StepStreams struct {
	Nums    []int
	Streams map[int]*core.Stream
}

// ReadStepStreams は out の step-*.jsonl を読む。
func ReadStepStreams(out string) StepStreams {
	paths, _ := filepath.Glob(filepath.Join(out, "step-*.jsonl"))
	ss := StepStreams{Streams: map[int]*core.Stream{}}
	for _, p := range paths {
		n, err := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(filepath.Base(p), "step-"), ".jsonl"))
		if err != nil {
			continue
		}
		data, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		ss.Nums = append(ss.Nums, n)
		ss.Streams[n] = core.ParseStream(string(data))
	}
	sort.Ints(ss.Nums)
	return ss
}

// StepSessions は {step 番号: [session id]}。
func (ss StepStreams) StepSessions() map[int][]string {
	m := map[int][]string{}
	for n, p := range ss.Streams {
		m[n] = p.SessionIDs
	}
	return m
}

// ContextLines は step に渡った文脈の節（evidence.md）の行を返し、全文を context-<num>.md に書く。
// 親とサブエージェントは分ける。何も無ければ nil を返し、ファイルも書かない。
func ContextLines(parsed *core.Stream, num int, out string) ([]string, error) {
	where := fmt.Sprintf("context-%d.md", num)
	items := HookItems(parsed.HookEvents)
	var main, sub []Item
	for _, i := range items {
		if i.Kind != "案内" || contains(contextEvents, i.Event) {
			main = append(main, i)
		}
		if i.Kind == "案内" && i.Event == "SubagentStart" {
			sub = append(sub, i)
		}
	}
	calls := parsed.ToolCalls
	var agents []core.ToolCall
	for _, c := range calls {
		if c.Name == agentTool && c.ID != "" {
			agents = append(agents, c)
		}
	}
	if len(main) == 0 && len(agents) == 0 && len(sub) == 0 {
		return nil, nil
	}
	lines := []string{"", fmt.Sprintf("### 渡った文脈（全文は %s）", where),
		"SessionStart・UserPromptSubmit・PreToolUse の案内（additionalContext）と、拒否・確認の理由。先頭だけを出す。"}
	full := []string{fmt.Sprintf("# step %d に渡った文脈（全文）", num)}
	add := func(heading, t string, limit int) {
		n := len([]rune(t))
		lines = append(lines, fmt.Sprintf("- %s（%d 文字）", heading, n))
		lines = append(lines, Fence(core.Clip(t, limit, where))...)
		full = append(full, "", fmt.Sprintf("## %s（%d 文字）", heading, n))
		full = append(full, Fence(t)...)
	}
	for _, i := range main {
		limit := EvidenceLimit
		if i.Kind == "案内" {
			limit = ContextLimit
		}
		add(fmt.Sprintf("%s [%s] %s", i.Event, i.Label, i.Kind), i.Text, limit)
	}
	if len(main) == 0 {
		lines = append(lines, "- なし")
	}
	if len(agents) > 0 || len(sub) > 0 {
		lines = append(lines, "", "#### サブエージェント（親の呼び出しと分けて出す）")
		full = append(full, "", "# サブエージェント")
	}
	for _, a := range agents {
		spec := asObj(a.Input)
		st := text(spec["subagent_type"])
		if st == "" {
			st = "?"
		}
		add(fmt.Sprintf("Agent 呼び出し %s（subagent_type: %s）に親が渡した prompt", a.ID, st), text(spec["prompt"]), EvidenceLimit)
		var kids []core.ToolCall
		for _, c := range calls {
			if c.Parent == a.ID {
				kids = append(kids, c)
			}
		}
		lines = append(lines, fmt.Sprintf("- この Agent 呼び出しの中のツール呼び出し: %d 件", len(kids)))
		full = append(full, "", fmt.Sprintf("## Agent 呼び出し %s の中のツール呼び出し（%d 件）", a.ID, len(kids)))
		for _, c := range kids {
			lines = append(lines, fmt.Sprintf("  - %s: %s", c.Name, core.Clip(dumps(c.Input), 500, where)))
			full = append(full, fmt.Sprintf("- %s: %s", c.Name, dumps(c.Input)))
		}
	}
	for _, i := range sub {
		add(fmt.Sprintf("SubagentStart [%s] 案内（どの Agent 呼び出しのものかは取れない）", i.Label), i.Text, ContextLimit)
	}
	if len(agents) > 0 || len(sub) > 0 {
		lines = append(lines, "- 取れない: サブエージェントが受け取った SessionStart の文脈・スキル・CLAUDE.md。"+
			"stream-json に出るのは上の分だけで、渡っていないものは推測しない")
	}
	err := os.WriteFile(filepath.Join(out, where), []byte(strings.Join(full, "\n")+"\n"), 0o644)
	return lines, err
}

func stepLabel(row Row, stepSessions map[int][]string) string {
	var steps []int
	for n, sids := range stepSessions {
		if contains(sids, text(row["session"])) {
			steps = append(steps, n)
		}
	}
	sort.Ints(steps)
	parts := make([]string, len(steps))
	for i, n := range steps {
		parts[i] = strconv.Itoa(n)
	}
	if len(parts) == 0 {
		return "?"
	}
	return strings.Join(parts, ",")
}

// StepLabel はその行がどの step のものか（"1" や "1,2"。分からなければ "?"）。
func StepLabel(row Row, stepSessions map[int][]string) string { return stepLabel(row, stepSessions) }

// Repeat は反復拒否 1 か所。
type Repeat struct {
	Step     string
	Code     string
	Count    int
	Subjects []string
}

// RepeatedDenials は同じ step で、同じ code の deny が、間に allow を挟まず RepeatMin 回以上続いた箇所。
// 間にある deny 以外の行（skip・handover など）は数えず、途切れにもしない。
func RepeatedDenials(rows []Row, stepSessions map[int][]string) []Repeat {
	var found []Repeat
	cur := map[string]*Repeat{}
	var order []string
	flush := func(step string) {
		if run := cur[step]; run != nil && run.Count >= RepeatMin {
			found = append(found, *run)
		}
		delete(cur, step)
	}
	for _, r := range rows {
		step := stepLabel(r, stepSessions)
		switch text(r["decision"]) {
		case "allow":
			flush(step)
		case "deny":
			code := text(r["code"])
			subject := text(r["subject"])
			if run := cur[step]; run != nil && run.Code == code {
				run.Count++
				run.Subjects = append(run.Subjects, subject)
			} else {
				flush(step)
				cur[step] = &Repeat{Step: step, Code: code, Count: 1, Subjects: []string{subject}}
				order = append(order, step)
			}
		}
	}
	for _, step := range order {
		if _, ok := cur[step]; ok {
			flush(step)
		}
	}
	return found
}

func writePath(c core.ToolCall) string {
	spec := asObj(c.Input)
	p, _ := spec["file_path"].(string)
	if contains(writeTools, c.Name) && p != "" {
		return p
	}
	return ""
}

// Bypass は迂回の疑い 1 か所。
type Bypass struct {
	Step          string
	Path          string
	Denied, Calls int
}

// BypassSuspects は Edit / Write が deny されたあと、同じ step で同じパス（fold で比べる）への Edit / Write がまだ
// 打たれている箇所。deny の数より呼び出しの数が多いものを疑う。Bash の中身からはパスを取らない。切れた subject は比べない。
func BypassSuspects(rows []Row, streams map[int]*core.Stream, stepSessions map[int][]string) []Bypass {
	type key struct{ step, folded string }
	denied := map[key][]string{}
	var order []key
	for _, r := range rows {
		subject := text(r["subject"])
		if text(r["decision"]) == "deny" && text(r["event"]) == "PreToolUse" && contains(writeTools, text(r["tool"])) &&
			subject != "" && !strings.Contains(subject, cutMark) {
			k := key{stepLabel(r, stepSessions), pathcase.Fold(subject)}
			if _, ok := denied[k]; !ok {
				order = append(order, k)
			}
			denied[k] = append(denied[k], subject)
		}
	}
	var found []Bypass
	for _, k := range order {
		var paths []string
		for _, x := range strings.Split(k.step, ",") {
			n, err := strconv.Atoi(x)
			if err != nil || streams[n] == nil {
				continue
			}
			for _, c := range streams[n].ToolCalls {
				if p := writePath(c); p != "" {
					paths = append(paths, p)
				}
			}
		}
		later := 0
		for _, p := range paths {
			if pathcase.Fold(p) == k.folded {
				later++
			}
		}
		if subjects := denied[k]; later > len(subjects) {
			found = append(found, Bypass{k.step, subjects[0], len(subjects), later})
		}
	}
	return found
}

// HasGuide は理由文が次の手を示しているか。行頭（前の空白を除く）が `次:`・`案内:`・`必須:`、依頼・指示の語がある、
// 打つ形をバッククォートで囲んだコード片で示している、のどれか。語の有無で見るので、誤検知と見逃しがある。
func HasGuide(t string) bool {
	for _, line := range strings.Split(t, "\n") {
		line = strings.TrimSpace(line)
		for _, h := range guideHeads {
			if strings.HasPrefix(line, h) {
				return true
			}
		}
	}
	for _, p := range guidePhrases {
		if strings.Contains(t, p) {
			return true
		}
	}
	return guideCommand.MatchString(t)
}

// Unguided は次の手を示していない拒否 1 件。
type Unguided struct {
	Step        int
	Label, Text string
}

// UnguidedDenials は次の手を示していない拒否の理由文（HasGuide が偽、または理由文が空）。
// 理由文が空のものは EmptyReason を入れる。
func UnguidedDenials(ss StepStreams) []Unguided {
	var found []Unguided
	for _, num := range ss.Nums {
		parsed := ss.Streams[num]
		for _, i := range HookItems(parsed.HookEvents) {
			if i.Kind == "拒否" && !HasGuide(i.Text) {
				found = append(found, Unguided{num, i.Label, i.Text})
			}
		}
		for _, ev := range parsed.HookEvents { // 理由文が空の拒否（HookItems は文面があるものだけなので別に拾う）
			if !isHookResponse(ev) {
				continue
			}
			decision, reason := decisionOf(hookPayload(ev))
			if (decision == "deny" || decision == "block") && strings.TrimSpace(reason) == "" {
				found = append(found, Unguided{num, labelOf(ev), EmptyReason})
			}
		}
	}
	return found
}

var (
	reSHA  = regexp.MustCompile(`\b[0-9a-f]{7,40}\b`)
	rePath = regexp.MustCompile(`(?:[A-Za-z0-9_.\-~:@%+=/\\]|<SHA>)*[/\\](?:[A-Za-z0-9_.\-~:@%+=/\\]|<SHA>)*`)
	reNum  = regexp.MustCompile(`\d+`)
)

// NormalizeMessage は文面を、パスと数字の違いを除いた形にする。置き換えの順は、コミットの ID → パス → 数字
// （数字を先にすると ID が壊れる）。
func NormalizeMessage(t string) string {
	t = reSHA.ReplaceAllString(t, "<SHA>")
	t = rePath.ReplaceAllString(t, "<PATH>")
	return reNum.ReplaceAllString(t, "<N>")
}

// Message は重複を除いた文面 1 件。
type Message struct {
	Kind   string
	Text   string
	Count  int
	Events []string
	Steps  []int
	Src    []string
}

// sources は hook の応答に出どころ（code・rules）を付ける。応答には無いので、decisions.jsonl の行と順に突き合わせる。
// 数が合わなければ付けない。キーは items の添字。
func sources(items []stepItem, rows []Row) map[int]string {
	rowSets := map[string][]Row{}
	for _, r := range rows {
		if text(r["event"]) != "PreToolUse" {
			continue
		}
		if d := text(r["decision"]); (d == "deny" || d == "ask") && r["enforced"] != false {
			rowSets["reason"] = append(rowSets["reason"], r)
		}
		if h, _ := r["hinted"].(bool); h {
			rowSets["hint"] = append(rowSets["hint"], r)
		}
	}
	itemSets := map[string][]int{}
	for idx, x := range items {
		if x.Event != "PreToolUse" {
			continue
		}
		if x.Kind != "案内" {
			itemSets["reason"] = append(itemSets["reason"], idx)
		} else {
			itemSets["hint"] = append(itemSets["hint"], idx)
		}
	}
	found := map[int]string{}
	for key, its := range itemSets {
		if len(its) != len(rowSets[key]) {
			continue
		}
		for n, idx := range its {
			row := rowSets[key][n]
			var rules string
			if list, ok := row["rules"].([]any); ok {
				parts := make([]string, len(list))
				for i, r := range list {
					parts[i] = fmt.Sprint(r)
				}
				rules = strings.Join(parts, ",")
			}
			code := text(row["code"])
			if code == "" {
				code = "-"
			}
			if rules == "" {
				rules = "-"
			}
			found[idx] = code + " / " + rules
		}
	}
	return found
}

type stepItem struct {
	Item
	Step int
}

// CollectMessages は hook の応答（拒否・確認・案内）の文面を、正規化した文面で重複を除いて集める。
// 案内は SessionStart のものを除く（CCNAVI.md の全文なので、文脈の節で見る）。
func CollectMessages(ss StepStreams, rows []Row) []Message {
	var items []stepItem
	for _, num := range ss.Nums {
		for _, i := range HookItems(ss.Streams[num].HookEvents) {
			if i.Kind != "案内" || i.Event != "SessionStart" {
				items = append(items, stepItem{i, num})
			}
		}
	}
	src := sources(items, rows)
	var merged []*Message
	index := map[string]*Message{}
	for idx, x := range items {
		key := x.Kind + "\x00" + NormalizeMessage(x.Text)
		m := index[key]
		if m == nil {
			m = &Message{Kind: x.Kind, Text: x.Text}
			index[key] = m
			merged = append(merged, m)
		}
		m.Count++
		if !contains(m.Events, x.Event) {
			m.Events = append(m.Events, x.Event)
		}
		seen := false
		for _, s := range m.Steps {
			seen = seen || s == x.Step
		}
		if !seen {
			m.Steps = append(m.Steps, x.Step)
		}
		if s, ok := src[idx]; ok && !contains(m.Src, s) {
			m.Src = append(m.Src, s)
		}
	}
	out := make([]Message, len(merged))
	for i, m := range merged {
		out[i] = *m
	}
	return out
}
