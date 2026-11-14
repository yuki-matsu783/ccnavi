package diagnose

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/audit"
)

// errUnreadable は期待する判定のファイルを読めない・形が違う。文はそのまま見せる。
// YAML として読めないものは包まずに返す。
var errUnreadable = errors.New("期待する判定を読めない")

type unreadable struct{ text string }

func (e unreadable) Error() string        { return e.text }
func (e unreadable) Is(target error) bool { return target == errUnreadable }

// expectation は期待する判定の 1 件。expected はそれを置いたタイプ（deny なら止まるはず）。
type expectation struct {
	expected        rules.Decision
	tool, subject   string
	resolvedSubject string
	why             string
}

// loadExpectations は期待する判定を読み、タイプの順に平らに並べる。subject の合言葉は走らせた場所に読み替える。
func loadExpectations(path, root string) ([]expectation, error) {
	text, err := fsio.ReadDiskText(path, fsio.Strict)
	if err != nil {
		return nil, unreadable{err.Error()}
	}
	raw, err := yaml.Load(text)
	if err != nil {
		return nil, err
	}
	top, ok := raw.(*yaml.Map)
	if !ok {
		return nil, unreadable{"期待する判定の形が違う。最上位はタイプの対応表のはず: " + path}
	}
	var out []expectation
	for _, want := range rules.Sections() {
		value := top.Get(string(want))
		if !pyfmt.Truthy(value) {
			continue
		}
		// 列でない値は、要素が対応表でないときと同じ苦情にする。
		cases, _ := value.([]any)
		if cases == nil {
			cases = []any{nil}
		}
		for _, c := range cases {
			m, ok := c.(*yaml.Map)
			if !ok {
				return nil, unreadable{fmt.Sprintf("期待する判定の形が違う。タイプ %s の 1 件が対応表ではない: %s", want, path)}
			}
			subject := text0(m, "subject")
			out = append(out, expectation{expected: want, tool: text0(m, "tool"), subject: subject,
				resolvedSubject: strings.ReplaceAll(subject, ExpectationPlaceholder, root), why: text0(m, "why")})
		}
	}
	return out, nil
}

// text0 は m の key の値を文字列で返す。欄が無い・空・偽の値なら空。
func text0(m *yaml.Map, key string) string {
	if v := m.Get(key); pyfmt.Truthy(v) {
		return pyfmt.Str(v)
	}
	return ""
}

// expectationResult は期待 1 件を判定に掛けた結果。skipped は allow の期待が判定に入らずに通ったもの（呼び出しは
// 通るので期待は満たすが、ルールを書いてもヒットしない場所なので、食い違いとは別に数えて必ず見せる）。
type expectationResult struct {
	expectation
	Result
	verdict     string
	ok, skipped bool
}

// object は `--test-expectations --json` の expectations の 1 件。
func (r expectationResult) object() *pyjson.Object {
	return pyjson.NewObject("expected", string(r.expected), "tool", r.expectation.tool,
		"subject", r.expectation.subject, "resolved_subject", r.resolvedSubject, "why", r.why,
		"known", r.known, "verdict", r.verdict, "code", r.code, "reason", r.reason, "rules", hitObjects(r.Rules),
		"ok", r.ok, "skipped", r.skipped)
}

// expectations は期待する判定を全部回した結果。
type expectations struct {
	root, rulesPath, path string
	results               []expectationResult
}

// runExpectations は期待する判定をすべて判定（TryOne）に掛ける。判定を作り直さずに本番と同じ経路を通す。
// 別の経路で確かめると、期待は通るのに実運用では失敗することがある。
func runExpectations(stderr io.Writer, conf *config.Settings, root, path string) (*expectations, error) {
	list, err := loadExpectations(path, root)
	if err != nil {
		return nil, err
	}
	body := &expectations{root: root, rulesPath: conf.Rules, path: path}
	// 期待ごとに判定するので、共通レイヤーの苦情は期待の数だけ出る。行き先で読むレイヤーは期待ごとに違うので、
	// まとめて捨てずに、まだ書いていない行だけを書く。
	said := map[string]bool{}
	for _, e := range list {
		var heard bytes.Buffer
		out, err := TryOne(&heard, conf, root, e.tool, e.resolvedSubject, Call{})
		if err != nil {
			return nil, err
		}
		for _, line := range ospath.SplitlinesKeep(heard.String()) {
			if !said[line] {
				said[line] = true
				io.WriteString(stderr, line)
			}
		}
		// 判定が対象を取り出せないツールは、判定に入らない（skip）。allow の期待だけは、通るので満たしたと数える。
		got := string(audit.Skip)
		if out.known {
			got = out.Verdict
		}
		skipped := e.expected == rules.Allow && got == string(audit.Skip)
		body.results = append(body.results, expectationResult{expectation: e, Result: out, verdict: got,
			ok: got == string(e.expected) || skipped, skipped: skipped})
	}
	return body, nil
}

// count は want の期待のうち、満たしたものの数と全体の数。
func (b *expectations) count(want rules.Decision) (ok, total int) {
	for _, r := range b.results {
		if r.expected == want {
			total++
			if r.ok {
				ok++
			}
		}
	}
	return ok, total
}

// tally は食い違い（満たさなかった期待）の数と、判定に入らずに通った期待の数。
func (b *expectations) tally() (mismatches, skipped int) {
	for _, r := range b.results {
		if !r.ok {
			mismatches++
		}
		if r.skipped {
			skipped++
		}
	}
	return mismatches, skipped
}

// object は `--test-expectations --json` の本文。
func (b *expectations) object() *pyjson.Object {
	counts := pyjson.NewObject()
	for _, want := range rules.Sections() {
		ok, total := b.count(want)
		counts.Set(string(want), pyjson.NewObject("ok", ok, "total", total))
	}
	mismatches, skipped := b.tally()
	results := make([]any, len(b.results))
	for i, r := range b.results {
		results[i] = r.object()
	}
	return pyjson.NewObject("version", testVersion, "root", b.root, "rules_path", b.rulesPath,
		"expectations_path", b.path, "counts", counts, "mismatches", mismatches, "skipped", skipped,
		"expectations", results)
}

// TestExpectations は `--test-expectations`。期待する判定をぜんぶ回して、食い違いを並べる。文字で出すときの終了コードは
// 食い違いが 1 件でもあれば 1（`/ccnavi-config` スキルが見る）。JSON は常に 0 で、数は本文の
// mismatches にある（読み手が「食い違った」と「試験が失敗した」を終了コードで分けられるように）。読めなければ 1。
func TestExpectations(stdout, stderr io.Writer, conf *config.Settings, root, path string, asJSON bool) (int, error) {
	body, err := runExpectations(stderr, conf, root, path)
	if errors.Is(err, errUnreadable) {
		fmt.Fprintf(stderr, "ccnavi: 期待する判定を読めない: %s\n", err)
		return config.ExitError, nil
	}
	if err != nil {
		return 0, err
	}
	if asJSON {
		return config.ExitOK, writeJSON(stdout, body.object())
	}

	// 文字の出力。食い違いを先に、判定に入らずに通ったものを次に、タイプごとの件数を最後に出す。
	p := func(format string, a ...any) { fmt.Fprintf(stdout, format, a...) }
	for _, r := range body.results {
		if r.ok {
			continue
		}
		p("食い違い: %s のはずが %s\n", r.expected, r.verdict)
		p("  %s  %s\n", r.expectation.tool, r.expectation.subject)
		p("  なぜ deny/ask/allow に置いたか: %s\n", r.why)
		var hit []string
		for _, h := range r.Rules {
			if h.source == fromFile {
				hit = append(hit, string(h.section)+":"+h.ID)
			}
		}
		if len(hit) > 0 {
			p("  ヒットしたルール: %s\n", strings.Join(hit, ", "))
		}
		p("\n")
	}
	for _, r := range body.results {
		if r.skipped {
			p("判定に入らずに通った（allow ではない）: %s  %s\n", r.expectation.tool, r.expectation.subject)
			p("  %s\n\n", r.why)
		}
	}
	for _, want := range rules.Sections() {
		ok, total := body.count(want)
		p("%s: %d/%d\n", want, ok, total)
	}
	mismatches, skipped := body.tally()
	p("食い違い %d 件、判定に入らなかったもの %d 件\n", mismatches, skipped)
	if mismatches > 0 {
		return config.ExitError, nil
	}
	return config.ExitOK, nil
}
