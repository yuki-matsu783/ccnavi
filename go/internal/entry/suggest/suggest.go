package suggest

import (
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"

	"ccnavi/internal/entry/diagnose"
	"ccnavi/internal/hook/reasons"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/infra/yamljson"
	"ccnavi/internal/policy/rulelint"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/records/tally"
)

// suggestVersion は `--suggest --json` の形の版。読み手は VS Code 拡張のルール管理画面。形を変えたら上げる。
const suggestVersion = 1

// kind は候補の種類。
type kind string

const (
	kindRule    kind = "rule"
	kindMessage kind = "message"
)

const (
	// handoverMin はルールを足す候補にする、渡った回数の下限。数回ならルールを書くよりユーザが見たほうが早い。
	handoverMin = 5
	// candidateLimit は種類ごとに出す候補の上限、expectationLimit は候補 1 件につける期待する判定の上限。
	candidateLimit   = 10
	expectationLimit = 3
	// idPrefix は候補の id の前置き。ユーザが名前を付け直す前提の仮の名前であることを表記で言う。
	idPrefix = "suggest-"
	// yamlWidth は候補の下書きを折り返す幅。
	yamlWidth = 100
	// scratchPrefix は候補を試す一時ディレクトリの名前の頭。
	scratchPrefix = "ccnavi-suggest-"
)

// cut は記録の subject が上限で切られた目印。切れた文字列は期待する判定にできない。
var cut = pyre.MustCompile(`…\(\+\d+\)$`, 0)

// candidate は候補 1 件。rule は rules.yml に置く 1 件（書いた表記の形）、expectations は rule-expectations.yml の
// 1 件ずつ（subject のルートは diagnose.ExpectationPlaceholder）。
type candidate struct {
	kind         kind
	section      rules.Decision
	tool         string
	count        int
	summary      string
	rule         *pyjson.Object
	layer        string
	rulesPath    string
	expectations []any
}

func (c candidate) yamlText() (string, error) {
	body := pyjson.NewObject(
		"rules", pyjson.NewObject(string(c.section), []any{c.rule}),
		"expectations", pyjson.NewObject(string(c.section), c.expectations),
	)
	dumped, err := yaml.Dump(yamljson.FromJSON(body), yaml.DumpOptions{Width: yamlWidth})
	if err != nil {
		return "", err
	}
	return "# " + c.summary + "\n" + dumped, nil
}

func (c candidate) object() (*pyjson.Object, error) {
	text, err := c.yamlText()
	if err != nil {
		return nil, err
	}
	id, _ := c.rule.Get("id")
	if id == nil {
		id = ""
	}
	return pyjson.NewObject("kind", string(c.kind), "section", string(c.section), "id", id, "tool", c.tool,
		"count", c.count, "summary", c.summary, "layer", c.layer, "rules_path", c.rulesPath, "rule", c.rule,
		"expectations", c.expectations, "yaml", text), nil
}

// placeholder は期待する判定に書く文字列。ルートを合言葉にする。
func placeholder(subject, root string) string {
	spellings := []string{ospath.Realpath(root)}
	if root != spellings[0] {
		spellings = append(spellings, root)
	}
	for _, s := range spellings {
		if s != "" && s != "/" {
			subject = strings.ReplaceAll(subject, s, diagnose.ExpectationPlaceholder)
		}
	}
	return subject
}

func expectation(tool, subject, why string) *pyjson.Object {
	return pyjson.NewObject("tool", tool, "subject", subject, "why", why)
}

func textOf(rec *pyjson.Object, key string) string {
	if v := rec.Value(key); pyfmt.Truthy(v) {
		return pyfmt.Str(v)
	}
	return ""
}

// usable は候補の材料にできる実行前チェックの行か。切れた文字列と、読み切れなかった呼び出しは使わない。
func usable(rec *pyjson.Object) bool {
	subject, ok := rec.Value("subject").(string)
	return rec.Value("event") == string(hookio.PreToolUse) && ok && subject != "" && !cut.Search(subject) &&
		!pyfmt.Truthy(rec.Value("degraded"))
}

// group は同じ形で渡った呼び出しのまとまり。subjects は多い順（同数は先に来た順）。
type group struct {
	tool     string
	shape    shaped
	n        int
	subjects []string
}

// handoverGroups は、ルールが言及しなかった呼び出しを形ごとにまとめ、多い順に並べる。
func handoverGroups(records []*pyjson.Object, root string) []group {
	type key struct{ tool, spelled string }
	index := map[key]int{}
	var groups []group
	var counts []map[string]int
	for _, rec := range records {
		if !usable(rec) {
			continue
		}
		if rec.Value("decision") != string(audit.Handover) && rec.Value("code") != reasons.CodeUndeclared {
			continue
		}
		tool, subject := textOf(rec, "tool"), rec.String("subject")
		s, ok := shapeOf(tool, subject, root)
		if !ok {
			continue
		}
		k := key{tool, s.spelled}
		i, seen := index[k]
		if !seen {
			i = len(groups)
			index[k] = i
			groups = append(groups, group{tool: tool, shape: s})
			counts = append(counts, map[string]int{})
		}
		groups[i].n++
		if counts[i][subject] == 0 {
			groups[i].subjects = append(groups[i].subjects, subject)
		}
		counts[i][subject]++
	}
	for i := range groups {
		c := counts[i]
		sort.SliceStable(groups[i].subjects, func(a, b int) bool { return c[groups[i].subjects[a]] > c[groups[i].subjects[b]] })
	}
	sort.SliceStable(groups, func(a, b int) bool {
		x, y := groups[a], groups[b]
		if x.n != y.n {
			return x.n > y.n
		}
		if x.tool != y.tool {
			return x.tool < y.tool
		}
		return x.shape.spelled < y.shape.spelled
	})
	var out []group
	for _, g := range groups {
		if g.n >= handoverMin {
			out = append(out, g)
		}
	}
	return out
}

// repeated は同じルールが同じ呼び出しを n 回以上止めたもの（ルールごと）。
type repeated struct {
	ruleID, tool string
	count        int
	subjects     []string
}

func repeatedDenies(records []*pyjson.Object, n int) []repeated {
	type key struct{ rule, norm string }
	var order []key
	counts := map[key]int{}
	spelled := map[key]string{}
	tools := map[string]string{}
	for _, rec := range records {
		if !usable(rec) || rec.Value("decision") != string(audit.Deny) || !pyfmt.Truthy(rec.Value("enforced")) {
			continue
		}
		ids, _ := rec.Value("rules").([]any)
		if len(ids) == 0 {
			continue
		}
		first, ok := ids[0].(string)
		if !ok {
			continue
		}
		subject := rec.String("subject")
		k := key{first, tally.Normalize(subject)}
		if _, seen := counts[k]; !seen {
			order = append(order, k)
			spelled[k] = subject
		}
		counts[k]++
		if _, seen := tools[first]; !seen {
			tools[first] = textOf(rec, "tool")
		}
	}
	type hit struct {
		got     int
		subject string
	}
	var ruleOrder []string
	byRule := map[string][]hit{}
	for _, k := range order {
		if got := counts[k]; got >= n {
			if _, seen := byRule[k.rule]; !seen {
				ruleOrder = append(ruleOrder, k.rule)
			}
			byRule[k.rule] = append(byRule[k.rule], hit{got, spelled[k]})
		}
	}
	var out []repeated
	for _, id := range ruleOrder {
		hits := byRule[id]
		sort.SliceStable(hits, func(a, b int) bool {
			if hits[a].got != hits[b].got {
				return hits[a].got > hits[b].got
			}
			return hits[a].subject < hits[b].subject
		})
		r := repeated{ruleID: id, tool: tools[id]}
		for _, h := range hits {
			r.count += h.got
			r.subjects = append(r.subjects, h.subject)
		}
		out = append(out, r)
	}
	sort.SliceStable(out, func(a, b int) bool {
		if out[a].count != out[b].count {
			return out[a].count > out[b].count
		}
		return out[a].ruleID < out[b].ruleID
	})
	return out
}

// writtenRule はルール 1 件を、書いた表記の形に戻す。
func writtenRule(r *rules.Rule) *pyjson.Object {
	id := r.BareID
	if id == "" {
		id = r.ID
	}
	out := pyjson.NewObject("id", id, "match", r.Match)
	switch {
	case r.When != nil:
		out.Set(rules.WhenKey, r.When.Written())
	case r.Glob != "":
		out.Set(string(rules.WhenGlob), r.Glob)
	default:
		out.Set(string(rules.WhenRegex), r.Regex)
	}
	if len(r.Excepts) > 0 {
		excepts := make([]any, len(r.Excepts))
		for i, e := range r.Excepts {
			excepts[i] = e.Written()
		}
		out.Set(rules.ExceptKey, excepts)
	}
	for _, kv := range [][2]string{
		{"message", r.Message},
		{"additionalContext", r.AdditionalContext},
		{"additionalContextOnce", r.AdditionalContextOnce},
		{"additionalContextFile", r.AdditionalContextFile},
		{"additionalContextOnceFile", r.AdditionalContextOnceFile},
	} {
		if kv[1] != "" {
			out.Set(kv[0], kv[1])
		}
	}
	if r.EveryWritten != nil {
		out.Set("every", yamljson.ToJSON(r.EveryWritten))
	}
	return out
}

// lintErrors はそのルールに --lint の error が付くか。
func lintErrors(path, root, ruleID string, layer bool) bool {
	for _, p := range rulelint.FileProblems(path, root, rulelint.Options{Layer: layer}) {
		if p.Severity == rules.Error && p.Rule == ruleID {
			return true
		}
	}
	return false
}

// expectationsPass は期待がぜんぶ want になり、候補（記録に出る名前 recordName）にヒットするか（--test-expectations と同じ）。
func expectationsPass(conf *config.Settings, root string, expectations []any, want rules.Decision, recordName string) (bool, error) {
	for _, e := range expectations {
		o := e.(*pyjson.Object)
		subject := strings.ReplaceAll(o.String("subject"), diagnose.ExpectationPlaceholder, root)
		out, err := diagnose.TryOne(io.Discard, conf, root, o.String("tool"), subject, diagnose.Call{})
		if err != nil {
			return false, err
		}
		hit := false
		for _, h := range out.Rules {
			hit = hit || h.ID == recordName
		}
		if out.Verdict != string(want) || !hit {
			return false, nil
		}
	}
	return true, nil
}

// ruleCandidates は、足すと良い ask のルールの候補と、検証を通らずに捨てた数。共通レイヤーを読めなければ、足した
// コピーで試せないので候補を出さず、全部を捨てた数にする。
func ruleCandidates(conf *config.Settings, root string, records []*pyjson.Object) ([]candidate, int, error) {
	groups := handoverGroups(records, root)
	if len(groups) == 0 {
		return nil, 0, nil
	}
	text, err := fsio.ReadDiskText(conf.Rules, fsio.Strict)
	if err != nil {
		return nil, len(groups), nil
	}
	data, err := yaml.Load(text)
	base, ok := data.(*yaml.Map)
	if err != nil || !ok {
		return nil, len(groups), nil
	}
	taken := map[string]bool{}
	for _, name := range rules.Sections() {
		list, _ := base.Get(string(name)).([]any)
		for _, r := range list {
			if m, ok := r.(*yaml.Map); ok {
				taken[pyfmt.Str(m.Get("id"))] = true
			}
		}
	}
	scratch, err := os.MkdirTemp("", scratchPrefix)
	if err != nil {
		return nil, 0, err
	}
	defer os.RemoveAll(scratch)
	var found []candidate
	dropped := 0
	for _, g := range groups {
		if len(found) >= candidateLimit {
			break
		}
		id := idPrefix + slug(g.shape.name)
		for taken[id] {
			id += "-2"
		}
		taken[id] = true
		rule := pyjson.NewObject("id", id, "match", g.tool)
		for k, v := range g.shape.form.All() {
			rule.Set(k.(string), v)
		}
		var expectations []any
		for _, s := range g.subjects[:min(len(g.subjects), expectationLimit)] {
			expectations = append(expectations, expectation(g.tool, placeholder(s, root),
				"記録で、どのルールにもヒットせず権限モードへ渡った形（"+g.shape.spelled+"）"))
		}
		trialData := &yaml.Map{}
		for k, v := range base.All() {
			trialData.Put(k, v)
		}
		asks, _ := base.Get(string(rules.Ask)).([]any)
		trialData.Put(string(rules.Ask), append(append([]any{}, asks...), yamljson.FromJSON(rule)))
		dumped, err := yaml.Dump(trialData, yaml.DumpOptions{})
		if err != nil {
			return nil, 0, err
		}
		path := ospath.Join(scratch, id+".yml")
		if why := fsio.WriteText(path, dumped, fsio.NativeNewline, fsio.Plain); why != "" {
			return nil, 0, errors.New(why)
		}
		trial := *conf
		trial.Rules = path
		// 候補は試す共通レイヤー（path）に書いてあるので、記録名はその相対パスと書いた id。
		recordName := ruleload.RelPath(path, root) + rules.IDSeparator + id
		if lintErrors(path, root, id, false) {
			dropped++
			continue
		}
		pass, err := expectationsPass(&trial, root, expectations, rules.Ask, recordName)
		if err != nil {
			return nil, 0, err
		}
		if !pass {
			dropped++
			continue
		}
		found = append(found, candidate{kind: kindRule, section: rules.Ask, tool: g.tool, count: g.n,
			summary: fmt.Sprintf("ルールを追加する候補: %s の %s が %d 回、どのルールにもヒットせず権限モードへ渡った。"+
				"止めるべきなら deny に移して message を記載してください", g.tool, g.shape.spelled, g.n),
			rule: rule, layer: config.LayerCommon, rulesPath: conf.Rules, expectations: expectations})
	}
	return found, dropped, nil
}

// messageCandidates は、記録で同じ呼び出しを繰り返し止めた deny のルールについて、message の見直し候補を作る。ルール
// ファイルに見つからないもの（組み込みや、もう無いルール）は、文面を直す先が無いので捨てる。
func messageCandidates(conf *config.Settings, root string, records []*pyjson.Object, n int) ([]candidate, int, error) {
	list := repeatedDenies(records, n)
	if len(list) == 0 {
		return nil, 0, nil
	}
	var views []ruleload.LayerView
	for _, v := range ruleload.Survey(io.Discard, conf, root) {
		if v.Unreadable == "" {
			views = append(views, v)
		}
	}
	var found []candidate
	dropped := 0
	for _, r := range list {
		if len(found) >= candidateLimit {
			break
		}
		view, rule := findDeny(views, r.ruleID)
		if rule == nil {
			dropped++
			continue
		}
		written := writtenRule(rule)
		var expectations []any
		for _, s := range r.subjects[:min(len(r.subjects), expectationLimit)] {
			expectations = append(expectations, expectation(r.tool, placeholder(s, root),
				fmt.Sprintf("記録で、%s が同じ呼び出しを %d 回以上止めた形", r.ruleID, n)))
		}
		if lintErrors(view.Path, root, written.String("id"), view.Name != config.LayerCommon) {
			dropped++
			continue
		}
		pass, err := expectationsPass(conf, root, expectations, rules.Deny, r.ruleID)
		if err != nil {
			return nil, 0, err
		}
		if !pass {
			dropped++
			continue
		}
		found = append(found, candidate{kind: kindMessage, section: rules.Deny, tool: r.tool, count: r.count,
			summary: fmt.Sprintf("message を見直す候補: %s が同じ呼び出しを繰り返し止めた（%d 回）。"+
				"エージェントが次の行動を判断できていない可能性があります。拒否理由と代替手順の説明を見直してください。",
				r.ruleID, r.count),
			rule: written, layer: view.Name, rulesPath: view.Path, expectations: expectations})
	}
	return found, dropped, nil
}

func findDeny(views []ruleload.LayerView, recordName string) (ruleload.LayerView, *rules.Rule) {
	for _, v := range views {
		for _, r := range v.RuleSet.Deny {
			if rules.RecordName(r) == recordName {
				return v, r
			}
		}
	}
	return ruleload.LayerView{}, nil
}

// collected は候補を集めた結果。キーは docs/11_requirements/json-shapes.md「候補の JSON」。
type collected struct {
	obj        *pyjson.Object
	logs       []string
	records    int
	candidates []*pyjson.Object
	dropped    int
}

func collect(conf *config.Settings, root string) (*collected, error) {
	// 試験と同じく記録を持たない。候補の検証で「1 度だけ渡す文」を消費しない。
	c := *conf
	c.State = ""
	conf = &c
	paths := audit.LogFiles(conf.Log)
	records, _ := audit.ReadRecords(conf.Log)
	added, droppedRules, err := ruleCandidates(conf, root, records)
	if err != nil {
		return nil, err
	}
	review, droppedMessages, err := messageCandidates(conf, root, records, tally.DenyThreshold(conf.DenyRepeat))
	if err != nil {
		return nil, err
	}
	out := &collected{logs: paths, records: len(records), dropped: droppedRules + droppedMessages}
	items := []any{}
	for _, cand := range append(added, review...) {
		obj, err := cand.object()
		if err != nil {
			return nil, err
		}
		out.candidates = append(out.candidates, obj)
		items = append(items, obj)
	}
	logs := paths
	if logs == nil {
		logs = []string{}
	}
	out.obj = pyjson.NewObject("version", suggestVersion, "root", root, "rules_path", conf.Rules, "logs", logs,
		"records", len(records), "candidates", items, "dropped", out.dropped)
	return out, nil
}

// Report は `--suggest`。候補を YAML の下書きか JSON で出す。終了コードは候補が無くても 0。error は想定外の失敗。
func Report(stdout io.Writer, conf *config.Settings, root string, asJSON bool) (int, error) {
	body, err := collect(conf, root)
	if err != nil {
		return 0, err
	}
	if asJSON {
		if err := pyjson.Write(stdout, body.obj, pyjson.Options{Indent: 1}); err != nil {
			return 0, err
		}
		return config.ExitOK, nil
	}
	if len(body.logs) == 0 {
		where := conf.Log
		if where == "" {
			where = "記録先が空"
		}
		fmt.Fprintf(stdout, "ccnavi: 記録がありません（%s）。候補を作成できません。\n", where)
		return config.ExitOK, nil
	}
	fmt.Fprintf(stdout, "# 記録 %d 本・%d 行から作った候補 %d 件（検証を通らず除いたもの %d 件）。\n"+
		"# 候補はすべて下書きです。ユーザが /ccnavi-config の手順で確認してから、設定に反映してください。\n",
		len(body.logs), body.records, len(body.candidates), body.dropped)
	for _, c := range body.candidates {
		fmt.Fprintf(stdout, "---\n# 置き場: %s（rules）と rule-expectations.yml（expectations）\n", c.String("rules_path"))
		io.WriteString(stdout, c.String("yaml"))
	}
	return config.ExitOK, nil
}
