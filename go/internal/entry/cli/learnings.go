package cli

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/wiki"
)

// learningsVersion は `--learnings --json` の形の版。欄を足すだけなら上げない。
const learningsVersion = 1

// targetWidth は一覧の target の欄の幅。
const targetWidth = 9

// learningsUsage は `--learnings` の組み合わせの誤り。設定を読む前に見る。
func learningsUsage(a opts) string {
	if refused := notWithDocsGiven(a, "--learnings"); refused != "" {
		return "--learnings は " + refused + " と同時に指定できない"
	}
	if a.Truthy("docs") {
		return "--learnings は --docs と同時に指定できない"
	}
	if a.Truthy("templates") {
		return "--learnings は --templates と同時に指定できない"
	}
	if stray := docsFlagsGiven(a); len(stray) > 0 {
		return stray[0] + " は --docs でだけ指定できる"
	}
	if cmd := a.command(); len(cmd) > 0 {
		return "--learnings にはフラグ以外の語を付けられない（" + strings.Join(cmd, " ") + "）"
	}
	return ""
}

// learningsSummary は一覧の中身（`--json` の形）。ファイルも時計も見ない。
func learningsSummary(found *wiki.Wiki, threshold int) *pyjson.Object {
	table := wiki.Rows(found)
	var candidates []wiki.Row
	for _, r := range table {
		if r.Status == wiki.Candidate {
			candidates = append(candidates, r)
		}
	}
	reached := func(rows []wiki.Row) int {
		n := 0
		for _, r := range rows {
			if r.Reached(threshold) {
				n++
			}
		}
		return n
	}
	byTarget := pyjson.NewObject()
	for _, target := range wiki.Targets {
		var mine []wiki.Row
		for _, r := range candidates {
			if r.Target == target {
				mine = append(mine, r)
			}
		}
		patterns := 0
		for _, r := range table {
			if r.Target == target {
				patterns++
			}
		}
		byTarget.Set(string(target), pyjson.NewObject("patterns", patterns, "candidates", len(mine), "reached", reached(mine)))
	}
	rows := []any{}
	for _, r := range table {
		rows = append(rows, pyjson.NewObject("name", r.Name, "target", string(r.Target), "title", r.Title,
			"status", string(r.Status), "sources", r.Count, "reached", r.Reached(threshold)))
	}
	pending := []any{}
	for _, i := range wiki.PendingImpacts(found) {
		files := make([]any, len(i.Files))
		for k, f := range i.Files {
			files[k] = f
		}
		counts := pyjson.NewObject()
		for _, p := range i.Patterns {
			counts.Set(p.Name, p.Count)
		}
		pending = append(pending, pyjson.NewObject("proposal", i.Proposal, "target", string(i.Target), "files", files,
			"patterns", counts))
	}
	broken := []any{}
	for _, b := range found.Broken {
		broken = append(broken, pyjson.NewObject("file", b.File, "kind", string(b.Kind), "problem", b.Problem))
	}
	return pyjson.NewObject("version", learningsVersion, "wiki", found.Present, "threshold", threshold,
		"candidates", len(candidates), "reached", reached(candidates), "by_target", byTarget, "patterns", rows,
		"pending", pending, "broken", broken)
}

// renderLearnings は一覧を人が読む形で書く。
func renderLearnings(w io.Writer, root string, data *pyjson.Object) {
	fmt.Fprintln(w, "ccnavi: 学びの蓄積の一覧")
	fmt.Fprintf(w, "  ツリー: %s\n", root)
	broken := jlist(data, "broken")
	if !jtrue(data, "wiki") && len(broken) == 0 {
		fmt.Fprintf(w, "  %s/ も %s/ も無い\n", wiki.PatternsDir, wiki.ImpactDir)
		return
	}
	fmt.Fprintf(w, "  観測元の閾値: %s（%s。以上で提案に足りる）\n", jstr(data, "threshold"), env.LearningSources)
	fmt.Fprintf(w, "  候補: %s 件（観測元が閾値以上 %s 件）\n", jstr(data, "candidates"), jstr(data, "reached"))
	fmt.Fprintln(w, "  target ごとの候補:")
	byTarget := jobj(data, "by_target")
	for _, target := range byTarget.Keys() {
		row := jobj(byTarget, target)
		fmt.Fprintf(w, "    %s 候補 %s 件（閾値以上 %s 件）  パターン %s 本\n", padRight(target, targetWidth),
			jstr(row, "candidates"), jstr(row, "reached"), jstr(row, "patterns"))
	}
	fmt.Fprintln(w, "  パターン:")
	patterns := jlist(data, "patterns")
	for _, row := range patterns {
		mark := ""
		if jtrue(row, "reached") {
			mark = "  閾値以上"
		}
		fmt.Fprintf(w, "    %s  %s  %s  観測元 %s%s\n", jstr(row, "name"), jstr(row, "target"), jstr(row, "status"),
			jstr(row, "sources"), mark)
	}
	if len(patterns) == 0 {
		fmt.Fprintln(w, "    （なし）")
	}
	fmt.Fprintln(w, "  承認待ちの提案:")
	pending := jlist(data, "pending")
	for _, item := range pending {
		counts := jobj(item, "patterns")
		var names []string
		for _, n := range counts.Keys() {
			names = append(names, n+"("+jstr(counts, n)+")")
		}
		shown := strings.Join(names, "、")
		if shown == "" {
			shown = "（なし）"
		}
		fmt.Fprintf(w, "    %s  %s  動機: %s\n", jstr(item, "proposal"), jstr(item, "target"), shown)
	}
	if len(pending) == 0 {
		fmt.Fprintln(w, "    （なし）")
	}
	if len(broken) > 0 {
		fmt.Fprintln(w, "  frontmatter が壊れたファイル（数えていない）:")
		for _, item := range broken {
			fmt.Fprintf(w, "    %s: %s\n", jstr(item, "file"), jstr(item, "problem"))
		}
	}
}

// runLearnings は `--learnings`。ツリー（`--root`）の wiki を読んで一覧を書く。何も書かない。
func runLearnings(stdout, stderr io.Writer, conf *config.Settings, root string, a opts) int {
	if !ospath.IsDir(root) {
		fmt.Fprintf(stderr, "ccnavi: --root %s はディレクトリではない\n", root)
		return config.ExitError
	}
	data := learningsSummary(wiki.LoadTree(root), wiki.SourcesThreshold(conf.LearningSources))
	if a.Truthy("json") {
		writeJSON(stdout, pyjson.NewObject("root", root).With(kvOf(data)...), false, 1)
		return config.ExitOK
	}
	renderLearnings(stdout, root, data)
	return config.ExitOK
}

// kvOf はオブジェクトの欄を、入れた順の (キー, 値) の並びにする。
func kvOf(o *pyjson.Object) []any {
	var kv []any
	for _, k := range o.Keys() {
		kv = append(kv, k, o.Value(k))
	}
	return kv
}
