package cli

import (
	"fmt"
	"io"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/proposal/templates"
	"ccnavi/internal/tickets/sync/drafts"
)

// ticketNew は `ticket new` か（`--template` と `--layer` を読む経路）。
func ticketNew(a opts) bool { return a.head("ticket", "new") }

// templatesAsked は `--templates` に関わるフラグが 1 つでも渡されたか（組み合わせの誤りを見る入口）。
func templatesAsked(a opts) bool {
	return a.Truthy("templates") || a.Str("template") != "" || a.Truthy("with_feedback") || !a.IsNone("show") ||
		a.Str("layer") != "" || !a.IsNone("draft") || !a.IsNone("check_file") || a.Str("name") != "" || a.Str("to") != ""
}

// flagGiven はフラグと、それが渡されたか。
type flagGiven struct {
	flag  string
	given bool
}

// firstGiven は並びのうち、渡された最初のフラグ。
func firstGiven(flags []flagGiven) string {
	for _, f := range flags {
		if f.given {
			return f.flag
		}
	}
	return ""
}

// draftUsage は下書きを作る側（`--draft`）の組み合わせの誤り。
func draftUsage(a opts) string {
	expect, yes := !a.IsNone("expect"), a.Truthy("yes")
	if a.IsNone("draft") {
		if a.Str("name") != "" {
			return "--name は --draft でだけ指定できる"
		}
		if a.Str("to") != "" {
			return "--to は --draft でだけ指定できる"
		}
		if flag := firstGiven([]flagGiven{{"--expect", expect}, {"--yes", yes}}); flag != "" {
			return flag + " は --templates と同時に指定できない（取り込みは ccnavi-user-handoff.sh）"
		}
		return ""
	}
	if flag := firstGiven([]flagGiven{{"--expect", expect}, {"--yes", yes}}); flag != "" {
		return flag + " は --draft と同時に指定できない（取り込みは ccnavi-user-handoff.sh）"
	}
	if !a.IsNone("show") || len(a.Strings("keyword")) > 0 {
		return "--draft は --show・--keyword と同時に指定できない"
	}
	if ospath.Strip(a.Str("draft")) == "" {
		return "--draft には完了した親の識別子を渡す"
	}
	to := a.Str("to")
	if to != "" && !slices.Contains(drafts.Targets(), drafts.Target(to)) {
		shown := make([]string, len(drafts.Targets()))
		for i, t := range drafts.Targets() {
			shown[i] = string(t)
		}
		return "--to は " + strings.Join(shown, " か ")
	}
	if a.Str("layer") != "" && to == string(drafts.ToCommon) {
		return "--layer は --to project のときだけ使う"
	}
	return ""
}

// templatesUsage は `--templates` の組み合わせの誤り。設定を読む前に見る。
func templatesUsage(a opts) string {
	templatesOn := a.Truthy("templates")
	if a.Str("template") != "" && (templatesOn || !ticketNew(a)) {
		return "--template は ticket new <識別子> --title <タイトル> --template <名前> でだけ使う"
	}
	if a.Truthy("with_feedback") && (templatesOn || !ticketNew(a) || a.Str("template") == "") {
		return "--with-feedback は ticket new <識別子> --title <タイトル> --template <名前> でだけ使う"
	}
	if !templatesOn {
		// `ticket new --template` の `--layer` は、テンプレートを引くレイヤー。
		if flag := firstGiven([]flagGiven{{"--check-file", !a.IsNone("check_file")}, {"--show", !a.IsNone("show")},
			{"--layer", a.Str("layer") != "" && !ticketNew(a)}, {"--draft", !a.IsNone("draft")},
			{"--name", a.Str("name") != ""}, {"--to", a.Str("to") != ""}}); flag != "" {
			return flag + " は --templates と一緒に使う"
		}
		return ""
	}
	if a.Truthy("docs") {
		return "--templates は --docs と同時に指定できない"
	}
	if refused := notWithDocsGiven(a, ""); refused != "" {
		return "--templates は " + refused + " と同時に指定できない"
	}
	// 絞り込みのフラグは `--docs` の物。`--keyword` だけは同じ語を使う。
	for _, flag := range docsFlags {
		if flag != "--keyword" && a.given(flag) {
			return flag + " は --docs でだけ指定できる"
		}
	}
	if cmd := a.command(); len(cmd) > 0 {
		return "--templates にはフラグ以外の語を付けられない（" + strings.Join(cmd, " ") + "）"
	}
	if !a.IsNone("check_file") {
		if ospath.Strip(a.Str("check_file")) == "" {
			return "--check-file にはパスを渡す"
		}
		if flag := firstGiven([]flagGiven{{"--show", !a.IsNone("show")}, {"--draft", !a.IsNone("draft")},
			{"--keyword", len(a.Strings("keyword")) > 0}, {"--name", a.Str("name") != ""}, {"--to", a.Str("to") != ""},
			{"--expect", !a.IsNone("expect")}, {"--yes", a.Truthy("yes")}}); flag != "" {
			return flag + " は --check-file と同時に指定できない"
		}
		return ""
	}
	if storing := draftUsage(a); storing != "" {
		return storing
	}
	if !a.IsNone("show") && len(a.Strings("keyword")) > 0 {
		return "--show は --keyword と同時に指定できない"
	}
	if !a.IsNone("show") && ospath.Strip(a.Str("show")) == "" {
		return "--show には名前を渡す"
	}
	return ""
}

// runTemplates は `--templates` の一覧・検索・表示と、下書きの作成。`--layer` を省けば、一覧と表示はワークスペース
// 自身のレイヤー（共通 + self）。
func runTemplates(stdout, stderr io.Writer, conf *config.Settings, root string, a opts) int {
	asJSON := a.Truthy("json")
	if !a.IsNone("draft") {
		return templateDraft(stdout, stderr, conf, root, a)
	}
	layer := ospath.Strip(cmpOr(a.Str("layer"), config.LayerSelf))
	known := layerNames(conf, root)
	if !a.IsNone("check_file") {
		if !slices.Contains(known, layer) && layer != config.LayerCommon {
			return failAnswer(stdout, stderr, asJSON, templates.JSONVersion, unknownLayer(layer, known))
		}
		return checkTemplateFile(stdout, conf, root, ospath.Strip(a.Str("check_file")), layer, asJSON)
	}
	if !slices.Contains(known, layer) {
		return failAnswer(stdout, stderr, asJSON, templates.JSONVersion, unknownLayer(layer, known))
	}
	scanned := templates.Load(conf, root)
	if !a.IsNone("show") {
		return showTemplate(stdout, stderr, conf, root, scanned, layer, ospath.Strip(a.Str("show")), asJSON)
	}
	var keywords []string
	for _, k := range a.Strings("keyword") {
		if ospath.Strip(k) != "" {
			keywords = append(keywords, k)
		}
	}
	found := drafts.Scan(conf, root, &scanned.Catalog).Drafts
	rows := []any{}
	for _, d := range found {
		if templates.Matches(d.Entry, keywords) {
			rows = append(rows, drafts.DraftJSON(d, scanned))
		}
	}
	answer := templates.AsJSON(conf, root, scanned, layer, keywords)
	if asJSON {
		answer.Set("drafts", rows)
		writeAnswer(stdout, answer)
		return config.ExitOK
	}
	listTemplates(stdout, answer, layer, len(keywords) > 0, len(rows))
	return config.ExitOK
}

func cmpOr(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

func layerNames(conf *config.Settings, root string) []string {
	var names []string
	for _, l := range ruleload.Layers(conf, root) {
		names = append(names, l.Name)
	}
	return names
}

func unknownLayer(layer string, known []string) string {
	return "レイヤー " + pyfmt.Repr(layer) + " はありません（" + strings.Join(known, " / ") + "）"
}

// label は深刻度の欄（幅をそろえる）。
func label(severity string) string {
	switch rules.Severity(severity) {
	case rules.Error:
		return "error"
	case rules.Warn:
		return "warn "
	case rules.Info:
		return "info "
	}
	return severity
}

// JSON の答えの欄を読む小さな道具（答えは templates の JSON をそのまま描く）。
func jstr(o *pyjson.Object, key string) string { return pyfmt.Str(o.Value(key)) }
func jtrue(o *pyjson.Object, key string) bool  { return pyfmt.Truthy(o.Value(key)) }
func jobj(o *pyjson.Object, key string) *pyjson.Object {
	v, _ := o.Value(key).(*pyjson.Object)
	return v
}
func jlist(o *pyjson.Object, key string) []*pyjson.Object {
	list, _ := o.Value(key).([]any)
	out := make([]*pyjson.Object, 0, len(list))
	for _, v := range list {
		if obj, ok := v.(*pyjson.Object); ok {
			out = append(out, obj)
		}
	}
	return out
}

func writeProblems(w io.Writer, prefix string, o *pyjson.Object) {
	for _, said := range jlist(o, "problems") {
		fmt.Fprintf(w, "  %s %s%s\n", label(jstr(said, "severity")), prefix, jstr(said, "detail"))
	}
}

// checkTemplateFile は任意のパスのテンプレートを検査する（`--check-file`）。書かない。
func checkTemplateFile(stdout io.Writer, conf *config.Settings, root, path, layer string, asJSON bool) int {
	answer := templates.CheckFile(conf, root, path, layer)
	if asJSON {
		writeAnswer(stdout, answer)
		return config.ExitOK
	}
	fmt.Fprintf(stdout, "ccnavi: テンプレートの検査: %s（%s）\n", path, layer)
	valid := "いいえ"
	if jtrue(answer, "valid") {
		valid = "はい"
	}
	fmt.Fprintf(stdout, "  名前: %s  読めた: %s\n", jstr(answer, "name"), valid)
	writeProblems(stdout, "", answer)
	return config.ExitOK
}

func listTemplates(w io.Writer, answer *pyjson.Object, layer string, filtered bool, drafted int) {
	rows := jlist(answer, "templates")
	fmt.Fprintf(w, "ccnavi: テンプレート（共通 + %s）\n", layer)
	if len(rows) == 0 {
		if filtered {
			fmt.Fprintln(w, "  該当するテンプレートはありません。")
		} else {
			fmt.Fprintln(w, "  テンプレートはありません。")
		}
	}
	for _, row := range rows {
		mark := ""
		dropped := jobj(row, "dropped")
		switch {
		case dropped != nil:
			mark = "（使わない: " + jstr(dropped, "reason") + "）"
		case !jtrue(row, "usable"):
			mark = "（error あり）"
		}
		summary := jstr(row, "description")
		if !jtrue(row, "valid") && dropped != nil {
			summary = jstr(dropped, "detail")
		}
		fmt.Fprintf(w, "  %s  [%s]%s  %s\n", jstr(row, "name"), jstr(row, "layer"), mark, summary)
		if when := jstr(row, "when"); when != "" {
			fmt.Fprintf(w, "      いつ: %s\n", when)
		}
	}
	// 置き場の苦情と、読めなかったものは、一覧に載せない理由として最後に言う。
	for _, view := range jlist(answer, "layers") {
		name := jstr(view, "name")
		if name != config.LayerCommon && name != layer {
			continue
		}
		writeProblems(w, name+": ", view)
	}
	if drafted > 0 {
		fmt.Fprintf(w, "下書き: %d 件（%s/）\n", drafted, place.Handoff)
	}
}

// templateDraft は完了した親から下書きを作る（`--draft`）。上書きはしない。効力は無いのでエージェントが打ってよい。
func templateDraft(stdout, stderr io.Writer, conf *config.Settings, root string, a opts) int {
	parent := ospath.Strip(a.Str("draft"))
	made, why := drafts.Make(conf, root, drafts.Request{Parent: parent, Name: ospath.Strip(a.Str("name")),
		To: drafts.Target(cmpOr(a.Str("to"), string(drafts.ToProject))), Layer: ospath.Strip(a.Str("layer"))})
	if a.Truthy("json") {
		shown := made
		if shown == nil {
			shown = &drafts.Made{}
		}
		notes := make([]any, len(shown.Notes))
		for i, n := range shown.Notes {
			notes[i] = n
		}
		problems := []any{}
		for _, p := range shown.Problems {
			problems = append(problems, pyjson.NewObject("severity", string(p.Severity), "detail", p.Detail))
		}
		writeAnswer(stdout, pyjson.NewObject("version", templates.JSONVersion, "ok", made != nil, "error", why,
			"name", shown.Name, "path", shown.Rel, "target", shown.Target, "layer", shown.Layer, "sha", shown.SHA,
			"tree", shown.Tree, "notes", notes, "problems", problems))
		return okIf(made != nil)
	}
	if made == nil {
		fmt.Fprintf(stderr, "ccnavi: %s\n", why)
		return config.ExitError
	}
	t := made.Template
	fmt.Fprintf(stdout, "ccnavi: 下書きを書いた: %s\n", made.Rel)
	fmt.Fprintf(stdout, "  取り込み先: %s（%s）\n", made.Target, made.Layer)
	fmt.Fprintf(stdout, "  親: %s  全体計画 %d ステップ / フィードバック計画 %d ステップ / フロー %d 本\n", parent,
		len(t.Plan), len(t.Feedback), len(t.Flows))
	fmt.Fprintf(stdout, "  ハッシュ: %s\n", made.SHA)
	for _, note := range made.Notes {
		fmt.Fprintf(stdout, "  info  %s\n", note)
	}
	for _, p := range made.Problems {
		fmt.Fprintf(stdout, "  %s %s\n", label(string(p.Severity)), p.Detail)
	}
	fmt.Fprintln(stdout, texts.Say(root, "NEXT_TEMPLATE_DRAFT", texts.Values{"target": made.Target,
		"tree_root": made.TreeRoot}, nil))
	return config.ExitOK
}

// showTemplate はテンプレート 1 つの中身を表示する（`--show`）。読むだけ。共通のテンプレートは、いまのレイヤーの
// 種類で検査し直す。
func showTemplate(stdout, stderr io.Writer, conf *config.Settings, root string, scanned *templates.Scan, layer, name string,
	asJSON bool) int {
	entry := scanned.Find(layer, name)
	if entry == nil {
		return failAnswer(stdout, stderr, asJSON, templates.JSONVersion,
			"テンプレート "+pyfmt.Repr(name)+" はありません（共通 + "+layer+"）")
	}
	if entry.Layer == config.LayerCommon {
		entry = templates.Recheck(entry, templates.TypesFor(conf, root, layer), &scanned.Catalog)
	}
	row := templates.EntryJSON(entry)
	text, _ := fsio.DecodeBytes(yaml.TrimBOM(entry.Raw), fsio.Replace)
	if asJSON {
		writeAnswer(stdout, pyjson.NewObject("version", templates.JSONVersion, "root", root, "layer", layer,
			"template", row.With("text", text)))
		return config.ExitOK
	}
	w := stdout
	fmt.Fprintf(w, "テンプレート: %s（%s）\n", jstr(row, "name"), jstr(row, "layer"))
	fmt.Fprintf(w, "  置き場: %s\n", jstr(row, "path"))
	if sha := jstr(row, "sha"); sha != "" {
		fmt.Fprintf(w, "  ハッシュ: %s\n", sha)
	}
	if dropped := jobj(row, "dropped"); dropped != nil {
		fmt.Fprintf(w, "  使わない: %s\n", jstr(dropped, "detail"))
	}
	if d := jstr(row, "description"); d != "" {
		fmt.Fprintf(w, "  説明: %s\n", d)
	}
	if when := jstr(row, "when"); when != "" {
		fmt.Fprintf(w, "  いつ: %s\n", when)
	}
	for _, part := range []struct{ title, key string }{{"全体計画", "plan"}, {"フィードバック計画", "feedback"}} {
		items := jlist(row, part.key)
		if len(items) == 0 {
			continue
		}
		fmt.Fprintf(w, "  %s:\n", part.title)
		for _, item := range items {
			var extra []string
			if after, _ := item.Value("after").([]any); len(after) > 0 {
				nums := make([]string, len(after))
				for i, n := range after {
					nums[i] = pyfmt.Str(n)
				}
				extra = append(extra, "after "+strings.Join(nums, ", "))
			}
			if jtrue(item, "review") {
				extra = append(extra, "review true")
			}
			if jtrue(item, "deferred") {
				extra = append(extra, "deferred true")
			}
			tail := ""
			if len(extra) > 0 {
				tail = "（" + strings.Join(extra, "; ") + "）"
			}
			fmt.Fprintf(w, "    %s. %s%s\n", jstr(item, "index"), jstr(item, "type"), tail)
		}
	}
	if flows := jlist(row, "flows"); len(flows) > 0 {
		fmt.Fprintln(w, "  フロー:")
		for _, f := range flows {
			named := ""
			if n := jstr(f, "name"); n != "" {
				named = " " + n
			}
			fmt.Fprintf(w, "    全体計画の %s 番目:%s（ノード %s、線 %s）\n", jstr(f, "index"), named, jstr(f, "nodes"),
				jstr(f, "connections"))
		}
	}
	writeProblems(w, "", row)
	fmt.Fprintln(w, "--- ファイルの中身 ---")
	text = templates.Reveal(text)
	if text != "" && !strings.HasSuffix(text, "\n") {
		text += "\n"
	}
	io.WriteString(w, text)
	return config.ExitOK
}
