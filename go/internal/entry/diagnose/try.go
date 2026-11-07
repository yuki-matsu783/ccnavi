package diagnose

import (
	"bytes"
	"fmt"
	"io"
	"slices"
	"strings"
	"time"
	"unicode/utf8"

	"ccnavi/internal/hook/judge"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/audit"
)

// testVersion は `--test --json` と `--test-expectations --json` の形の版。読み手は VS Code 拡張のルール管理画面。
// 形を変えたら上げる。
const testVersion = 1

// Source はヒットしたルールの出どころ。値を増やすと 2 値で読む拡張（testmodel.ts）が読み違えるので、増やすなら
// testVersion も上げる。
type Source string

const (
	// FromFile はルールファイルから読んだルール。
	FromFile Source = "file"
	// Outside はチケットの範囲や組み込みの保護のように、ルールファイルの外から来た根拠。
	Outside Source = "outside"
)

// outsideFormLimit は外の根拠の式を見せる長さの上限（字）。組み込みの保護の式は 2 万字を超える。
const outsideFormLimit = 160

// ExpectationPlaceholder は期待する判定のパスに書く合言葉。走らせた場所（ワークスペースルート）に読み替える。
// 絶対パスで書かせると、判定が解いた先が走らせた場所によって変わる。
const ExpectationPlaceholder = "/repo"

// noneShown は payload の欄が無いことの表記。
const noneShown = "(無し)"

// Call は試験の payload のうち、打つ側が変えられる欄。どれも空なら hook に無い既定で試す。Cwd は呼び出しが来た
// ディレクトリ（空ならワークスペースルート）で、Bash の行き先のツリー・`cd` の読み・レビュー待ちと子からの push の止めが
// 変わる。PermissionMode は Claude Code の権限モード、AgentID はサブエージェントの中で走った呼び出しの印。
type Call struct {
	Cwd, PermissionMode, AgentID string
}

func (c Call) given() bool { return c.Cwd != "" || c.PermissionMode != "" || c.AgentID != "" }

// permissionModes は --permission-mode に渡せる名前。判定は知らない名前を ask にするが、試験で打ち間違えた名前を
// 黙って ask にすると、そのモードの答えとして読まれる。
func permissionModes() []string {
	return slices.Concat(judge.PermissionJudged, judge.PermissionNoJudge)
}

// problem は payload の欄が使えないならその理由。
func (c Call) problem() string {
	if c.PermissionMode != "" && !slices.Contains(permissionModes(), c.PermissionMode) {
		return fmt.Sprintf("--permission-mode %s は権限モードの名前ではない。渡せるのは %s",
			pyfmt.Repr(c.PermissionMode), strings.Join(permissionModes(), ", "))
	}
	if c.Cwd != "" && !ospath.IsDir(c.Cwd) {
		return fmt.Sprintf("--cwd %s はディレクトリではない（無いか、ファイル）", pyfmt.Repr(c.Cwd))
	}
	return ""
}

// payload は判定に渡した payload の欄（`--test` が出す `payload`）。
type payload struct {
	Cwd, PermissionMode, AgentID string
}

func (c Call) payload(root string) payload {
	cwd := root
	if c.Cwd != "" {
		cwd = ospath.Abspath(c.Cwd)
	}
	return payload{Cwd: cwd, PermissionMode: c.PermissionMode, AgentID: c.AgentID}
}

func (p payload) object() *pyjson.Object {
	return pyjson.NewObject("cwd", p.Cwd, "permission_mode", p.PermissionMode, "agent_id", p.AgentID)
}

// Hit はヒットしたルール 1 件。Section が空なのは、ルールそのものの無い外の根拠。
type Hit struct {
	ID      string
	Source  Source
	Section rules.Decision
	form
}

func (h Hit) object() *pyjson.Object {
	return h.put(pyjson.NewObject("id", h.ID, "source", string(h.Source), "section", string(h.Section)))
}

// Result は 1 件の試験の結果。Known が偽なら、そのツールは判定が対象を取り出せないもので、ほかは空。
// キーは docs/11_requirements/json-shapes.md「試験の JSON」。
type Result struct {
	Known                                                bool
	Tool, Subject                                        string
	Resolved, Verdict, Code, Reason, Degraded, Unwrapped string
	Fallback, Response                                   string
	Rules                                                []Hit
	// Quoted は引用の中から切り出したコマンドにだけヒットしたルールの id。Payload は渡した payload の欄。
	// どちらも、無ければ JSON にキーごと出さない（読み手の知っているキーを、この場合が無い呼び出しで変えない）。
	Quoted  []string
	Payload *payload
}

func hitObjects(hits []Hit) []any {
	out := make([]any, len(hits))
	for i, h := range hits {
		out[i] = h.object()
	}
	return out
}

func (r Result) put(o *pyjson.Object) *pyjson.Object {
	for _, kv := range [][2]any{
		{"known", r.Known}, {"tool", r.Tool}, {"subject", r.Subject}, {"resolved", r.Resolved},
		{"verdict", r.Verdict}, {"code", r.Code}, {"reason", r.Reason}, {"degraded", r.Degraded},
		{"unwrapped", r.Unwrapped}, {"fallback", r.Fallback}, {"rules", hitObjects(r.Rules)}, {"response", r.Response},
	} {
		o.Set(kv[0].(string), kv[1])
	}
	if len(r.Quoted) > 0 {
		o.Set("quoted", r.Quoted)
	}
	if r.Payload != nil {
		o.Set("payload", r.Payload.object())
	}
	return o
}

func known(tool string) bool { return tool == rules.StopMatch || rules.SubjectFieldOf(tool) != "" }

// TryOne は 1 件を判定して、結果とすべての理由を返す。文字と JSON の両方がここを読む（読み手ごとに判定を呼び直すと、
// ターミナルと画面で答えが違いうる）。call の欄を確かめるのは呼び手。error は判定の想定外の失敗。
func TryOne(stderr io.Writer, conf *config.Settings, root, tool, subject string, call Call) (Result, error) {
	// 試験は記録を持たない。「1 度だけ渡す文」を試しで消費すると、本番の最初の 1 回で届かなくなる。
	trial := *conf
	trial.State = ""
	out := Result{Known: known(tool), Tool: tool, Subject: subject, Rules: []Hit{}}
	if !out.Known {
		return out, nil
	}
	if tool == rules.StopMatch {
		return tryStop(&trial, root, out), nil
	}

	used := call.payload(root)
	in := hookio.Input{
		Event:          hookio.PreToolUse,
		ToolName:       tool,
		ToolInput:      pyjson.NewObject(rules.SubjectFieldOf(tool), subject),
		Cwd:            used.Cwd,
		PermissionMode: used.PermissionMode,
		AgentID:        used.AgentID,
	}
	record := &audit.Record{Mode: config.Enable, PermissionMode: in.PermissionMode, Event: string(in.Event), Tool: tool,
		Subject: rules.SubjectOf(in)}
	// 応答は拾ってそのまま見せる。ここで文を組み直すと、試験で読んだ文とエージェントに届く文が違うものになる。
	var captured bytes.Buffer
	if _, err := judge.Before(&captured, stderr, config.Enable, &trial, root, in, record,
		time.Now().Add(config.HookDeadline.Value())); err != nil {
		return out, err
	}

	out.Verdict = string(record.Decision)
	if out.Verdict == "" {
		out.Verdict = string(audit.Allow)
	}
	out.Code = record.Code
	// 解いた先を見せないと、ヒットしなかった理由が「パスの表記が違う」かどうかをユーザが言えない。
	if record.Subject != "" && record.Subject != subject {
		out.Resolved = record.Subject
	}
	out.Reason = string(record.Reason)
	out.Degraded = record.Degraded
	out.Unwrapped = record.Unwrapped
	out.Fallback = record.Fallback
	out.Rules = rulesHit(record)
	out.Response = responseText(captured.String())
	out.Quoted = record.Quoted
	if call.given() {
		out.Payload = &used
	}
	return out, nil
}

// tryStop はターンの終わりの試し。使われるのは共通レイヤーと自身のレイヤーの allow で、every が 2 以上のものだけ
// （ruleload.StopRules）。あれば allow、無ければ判定に入らない（skip）。数えは進めない。
func tryStop(conf *config.Settings, root string, out Result) Result {
	picked := ruleload.StopRules(conf, root)
	out.Verdict = string(audit.Skip)
	if len(picked) > 0 {
		out.Verdict = string(audit.Allow)
	}
	for _, r := range picked {
		out.Rules = append(out.Rules, Hit{ID: r.ID, Source: FromFile, Section: r.Decision, form: ruleForm(r)})
	}
	return out
}

// rulesHit は判定が適用したルールそのもの（record.Hits）を、タイプと翻訳後の式まで返す。glob は正規表現に変わるので、
// 翻訳後の式を見せないと、ヒットしなかった理由をユーザが辿れない。ルールファイルを読み直すと、判定が足した組み込みや
// 重複で外れた側の定義を引けない。Hits は Rules の末尾と同じ順なので、id ではなく位置で対応させる。
func rulesHit(record *audit.Record) []Hit {
	hits := []Hit{}
	offset := len(record.Rules) - len(record.Hits)
	applied := record.Hits
	if offset < 0 {
		applied, offset = nil, len(record.Rules)
	}
	for i, name := range record.Rules {
		var r *rules.Rule
		if i >= offset {
			r, _ = applied[i-offset].(*rules.Rule)
		}
		if r == nil || rules.RecordName(r) != name {
			hits = append(hits, Hit{ID: name, Source: Outside})
			continue
		}
		f := ruleForm(r)
		source := FromFile
		if !r.FromFile {
			// 組み込みの保護の式は 2 万字を超えるものがあり、そのまま出すと 1 件の試験が読めなくなる。
			f = form{Kind: f.Kind, Written: abbreviated(f.Written), Pattern: abbreviated(f.Pattern)}
			source = Outside
		}
		hits = append(hits, Hit{ID: name, Source: source, Section: r.Decision, form: f})
	}
	return hits
}

// abbreviated は外の根拠の式を頭から outsideFormLimit 字までに切る。切ったら全体の字数を添える。
func abbreviated(text string) string {
	n := utf8.RuneCountInString(text)
	if n <= outsideFormLimit {
		return text
	}
	return fmt.Sprintf("%s…（全 %d 字）", string([]rune(text)[:outsideFormLimit]), n)
}

// responseText は判定が返した文面を取り出す。理由と additionalContext は別のキーで、deny と ask では両方が届くので、
// 届く順に並べる。
func responseText(written string) string {
	if ospath.Strip(written) == "" {
		return ""
	}
	v, err := pyjson.Loads(written)
	top, _ := v.(*pyjson.Object)
	inner, ok := top.Value("hookSpecificOutput").(*pyjson.Object)
	if err != nil || !ok {
		return ospath.Strip(written)
	}
	return pyfmt.JoinNonEmpty("\n\n", inner.String("permissionDecisionReason"), inner.String("additionalContext"))
}

// Test は `--test`。1 件を判定して、結果とすべての理由を書く（asJSON なら JSON）。判定の内容を終了コードで表さない
// （deny を非ゼロにすると「拒否された」と「試験が失敗した」を見分けられない）。payload の欄の誤りだけ 1。
func Test(stdout, stderr io.Writer, conf *config.Settings, root, tool, subject string, call Call, asJSON bool) (int, error) {
	if problem := call.problem(); problem != "" {
		fmt.Fprintf(stderr, "ccnavi: %s\n", problem)
		return config.ExitError, nil
	}
	out, err := TryOne(stderr, conf, root, tool, subject, call)
	if err != nil {
		return 0, err
	}
	if asJSON {
		body := pyjson.NewObject("version", testVersion, "root", root, "rules_path", conf.Rules)
		return config.ExitOK, writeJSON(stdout, out.put(body))
	}
	writeTest(stdout, root, out, call)
	return config.ExitOK, nil
}

// writeJSON は json.dumps(body, ensure_ascii=True, indent=1) と改行。
func writeJSON(w io.Writer, body any) error {
	text, err := pyjson.Dumps(body, pyjson.Options{Indent: 1})
	if err != nil {
		return err
	}
	_, err = io.WriteString(w, text+"\n")
	return err
}

func writeTest(w io.Writer, root string, out Result, call Call) {
	p := func(format string, a ...any) { fmt.Fprintf(w, format, a...) }
	tool := out.Tool
	if tool == rules.StopMatch {
		p("verdict: %s\ntool: %s\n", out.Verdict, tool)
		p("note: ターンの終わり。使われるのは共通レイヤーと自身のレイヤーの allow で、every が 2 以上のものだけ。渡す回にだけ止める\n")
		if len(out.Rules) == 0 {
			p("rules: (使われるルールがありません。ターンの終わりには止めない)\n")
			return
		}
		p("rules:\n")
		for _, h := range out.Rules {
			p("  %s\n", h.ID)
		}
		return
	}
	if !out.Known {
		p("verdict: (判定に入らない)\ntool: %s\n", tool)
		p("note: %s は判定が対象を取り出せないツール。ルールを書いてもヒットせず、呼び出しはそのまま通る\n", tool)
		return
	}

	code := ""
	if out.Code != "" {
		code = " (" + out.Code + ")"
	}
	p("verdict: %s%s\n", out.Verdict, code)
	p("tool: %s\n", tool)
	p("subject: %s\n", out.Subject)
	if out.Resolved != "" {
		p("resolved: %s\n", out.Resolved)
	}
	// hook はこの 3 つを Claude Code から受け取り、判定がそれで変わる。
	used := call.payload(root)
	p("payload: cwd=%s  permission_mode=%s  agent_id=%s\n", used.Cwd, orNone(used.PermissionMode), orNone(used.AgentID))
	if out.Reason != "" {
		p("reason: %s\n", out.Reason)
	}
	if out.Degraded != "" {
		p("degraded: %s（生の文字列に適用した）\n", out.Degraded)
	}
	if out.Unwrapped != "" {
		for _, layer := range strings.Split(out.Unwrapped, "\x00") {
			shown := strings.Join(strings.FieldsFunc(strings.ReplaceAll(layer, "\x01", " "), ospath.IsSpace), " ")
			p("unwrapped: %s（中で実行されるコマンドに適用した）\n", shown)
		}
	}
	if out.Fallback != "" {
		p("fallback: %s（組み込みの既定で判定した）\n", out.Fallback)
	}

	if len(out.Rules) == 0 {
		p("rules: (どのルールにもヒットしなかった)\n")
	} else {
		p("rules:\n")
		for _, h := range out.Rules {
			if h.Source == Outside {
				// ルールファイルの中に無い根拠。ルールそのものがあれば、タイプと短く切った式を添える。
				if h.Section == "" {
					p("  %s（ルールファイルの外にある根拠）\n", h.ID)
					continue
				}
				p("  %s:%s（ルールファイルの外にある根拠）  %s %s\n", h.Section, h.ID, h.Kind, pyfmt.Repr(h.Written))
				continue
			}
			p("  %s:%s  %s %s\n", h.Section, h.ID, h.Kind, pyfmt.Repr(h.Written))
			pattern := h.Pattern
			if pattern == "" {
				pattern = "(組み立て失敗)"
			}
			p("    -> %s\n", pattern)
		}
	}
	if len(out.Quoted) > 0 {
		p("quoted: %s（引用の中から切り出したコマンドにだけヒットした）\n", strings.Join(out.Quoted, ", "))
	}

	if out.Response == "" {
		p("response: (何も返さない)\n")
		return
	}
	p("response:\n")
	for _, line := range ospath.Splitlines(out.Response) {
		p("  %s\n", line)
	}
}

func orNone(s string) string {
	if s == "" {
		return noneShown
	}
	return s
}
