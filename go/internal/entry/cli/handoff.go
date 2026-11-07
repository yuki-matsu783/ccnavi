package cli

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/infra/argparse"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/useronly"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/sync/handoff"
)

// writeJSON は答えの JSON を 1 つ書く。asciiOnly は ensure_ascii=True。indent は indent=N（0 は 1 行）。
func writeJSON(stdout io.Writer, v any, asciiOnly bool, indent int) error {
	text, err := pyjson.Dumps(v, pyjson.Options{RawUnicode: !asciiOnly, Indent: indent})
	if err != nil {
		return err
	}
	_, err = io.WriteString(stdout, text+"\n")
	return err
}

// writeAnswer は拡張が読む答え（ensure_ascii=True・indent=1）を書く。
func writeAnswer(stdout io.Writer, v any) { writeJSON(stdout, v, true, 1) }

// sidewaysOptions は `--handoff`・`--mirror` と一緒に読むオプション（dest）。ほかが既定でなければ断る。先頭の 4 つは
// ラッパーが付けるもの、`mode` から後ろは設定に重ねる切り替え。
var sidewaysOptions = map[string]bool{
	"root": true, "cwd": true, "record_writes": true, "record_tree": true, "mode": true, "restore_if_deny": true,
	"guard_core_files": true, "guard_ticket_approval": true, "ticket_control": true, "command": true, "yes": true,
	"expect": true, "via": true, "json": true,
}

// sidewaysProblem は、`--handoff`（own）・`--mirror` と同時に渡せないオプションのうち最初のもの。
func sidewaysProblem(parser *argparse.Parser, a opts, own, verb string) string {
	for _, action := range parser.Actions() {
		if action.Dest == own || sidewaysOptions[action.Dest] || len(action.OptionStrings) == 0 {
			continue
		}
		if a.Changed(action) {
			return action.OptionStrings[len(action.OptionStrings)-1] + " は " + verb + " と同時には指定できません"
		}
	}
	return ""
}

// wanted は位置引数で選んだもの（無ければ nil で全部）。
func wanted(a opts) []string {
	if cmd := a.command(); len(cmd) > 0 {
		return cmd
	}
	return nil
}

// sidewaysRoute は `--yes` で書くときの経路を決める。ボードの経路（`--via board`）はターミナルを求めず、ターミナルの
// 経路はターミナルの前のユーザであることを確かめる。
func sidewaysRoute(in *input, conf *config.Settings, stderr io.Writer, a opts, op useronly.Op) (board, ok bool) {
	if a.Str("via") == string(via.Board) {
		via.SetRoute(via.Board)
		return true, true
	}
	if !fromTerminal(in, conf, stderr, op) {
		return false, false
	}
	via.SetRoute(via.Terminal)
	return false, true
}

// failAnswer は失敗の答え。`--json` なら標準出力に JSON、でなければ標準エラーに 1 行。
func failAnswer(stdout, stderr io.Writer, asJSON bool, version int, text string) int {
	if asJSON {
		writeAnswer(stdout, pyjson.NewObject("version", version, "ok", false, "error", text))
	} else {
		fmt.Fprintf(stderr, "ccnavi: %s\n", text)
	}
	return config.ExitError
}

// handoffUsage は `--handoff` の組み合わせの誤り。
func handoffUsage(parser *argparse.Parser, a opts) string {
	if refused := sidewaysProblem(parser, a, "handoff", "user handoff"); refused != "" {
		return refused
	}
	yes := a.Str("yes")
	route := a.Str("via")
	switch {
	case yes != "" && yes != bareYes:
		return "--yes には値を付けられません（user handoff <パス>… --yes --expect <ハッシュ>）"
	case !a.IsNone("expect") && yes == "":
		return "--expect は --yes と一緒に指定してください"
	case route != "" && route != string(via.Board):
		return "--via に渡せるのは board だけです"
	case route != "" && (yes == "" || a.IsNone("expect") || !a.Truthy("json")):
		return "--via board は --yes --expect <ハッシュ> --json と一緒に指定してください"
	}
	return ""
}

// runHandoff は `ccnavi user handoff`。保護対象の下書き（wip/handoff/）を、見るだけか、`--yes` で元の場所へ取り込む。
// 見るだけの形を含めてユーザ専用（hook が止める）。
func runHandoff(in *input, stdout, stderr io.Writer, parser *argparse.Parser, conf *config.Settings, root string, a opts) int {
	asJSON := a.Truthy("json")
	if refused := handoffUsage(parser, a); refused != "" {
		return failAnswer(stdout, stderr, asJSON, handoff.JSONVersion, refused)
	}
	if a.Str("yes") != "" {
		board, ok := sidewaysRoute(in, conf, stderr, a, useronly.OpHandoff)
		if !ok {
			return config.ExitError
		}
		done := handoff.Apply(handoff.Prepare(conf, root, wanted(a)), a.Str("expect"), board)
		if asJSON {
			writeAnswer(stdout, done.JSON())
			return okIf(done.OK)
		}
		if !done.OK {
			fmt.Fprintf(stderr, "ccnavi: %s\n", done.Error)
			return config.ExitError
		}
		handoffApplied(stdout, done)
		return config.ExitOK
	}
	plan := handoff.Prepare(conf, root, wanted(a))
	if plan.Error != "" {
		return failAnswer(stdout, stderr, asJSON, handoff.JSONVersion, plan.Error)
	}
	if asJSON {
		writeAnswer(stdout, plan.JSON())
		return config.ExitOK
	}
	handoffPlan(stdout, plan, wanted(a) != nil, root)
	return config.ExitOK
}

func okIf(ok bool) int {
	if ok {
		return config.ExitOK
	}
	return config.ExitError
}

// handoffPlan は取り込みの計画を人が読む形で書く。何も反映せず、取り込めるときだけ次に打つコマンドを添える。
func handoffPlan(w io.Writer, plan *handoff.Plan, named bool, root string) {
	fmt.Fprintf(w, "ccnavi: 保護対象の下書き（%s の %s/）\n", plan.Tree, place.Handoff)
	if len(plan.Entries) == 0 {
		fmt.Fprintln(w, "  取り込める下書きはありません。")
	}
	for _, e := range plan.Entries {
		mark := " "
		if e.Selected {
			mark = "*"
		}
		fmt.Fprintf(w, "%s [%s] %s（%s）\n", mark, e.State(), e.Rel, e.Kind.ID)
		for _, line := range e.Errors {
			fmt.Fprintf(w, "    error %s\n", line)
		}
		for _, line := range e.Warnings {
			fmt.Fprintf(w, "    warn  %s\n", line)
		}
		if e.Defense != "" {
			fmt.Fprintf(w, "    ターミナルのみ %s。ボードからは取り込めません\n", e.Defense)
		}
		if e.Selected && e.Diff != "" {
			for _, line := range strings.Split(e.Diff, "\n") {
				fmt.Fprintf(w, "    %s\n", line)
			}
		}
	}
	if len(plan.Ignored) > 0 {
		fmt.Fprintf(w, "対象外: %d 件（取り込める種類の表にヒットしない）\n", len(plan.Ignored))
	}
	chosen := plan.Selected()
	if len(chosen) == 0 {
		fmt.Fprintln(w, "選んだ対象はありません。何も反映していません。")
		return
	}
	if !plan.OK() {
		fmt.Fprintln(w, "選んだ対象に error があるため、取り込めません。直すか、パスを選び直してください。何も反映していません。")
		return
	}
	command := config.ScriptCommand(root, useronly.UserScriptPrefix+"handoff.sh") + " "
	if named {
		rels := make([]string, len(chosen))
		for i, e := range chosen {
			rels[i] = e.Rel
		}
		command += strings.Join(rels, " ") + " "
	}
	fmt.Fprintln(w, "何も反映していません。この内容で取り込むなら、次を実行します。")
	fmt.Fprintf(w, "  %s--yes --expect %s\n", command, plan.Expect())
	for _, e := range chosen {
		if !e.Board() {
			fmt.Fprintln(w, "  （defense の種類か、防御の記述を動かす rules.yml を含むため、ボードからは取り込めません。"+
				"ターミナルで実行してください）")
			break
		}
	}
}

func handoffApplied(w io.Writer, done *handoff.Applied) {
	for _, e := range done.Applied {
		fmt.Fprintf(w, "取り込んだ: %s（%s）\n", e.Rel, e.Kind.ID)
	}
	for _, path := range done.RemovedDrafts {
		fmt.Fprintf(w, "下書きを削除しました: %s\n", path)
	}
	for _, k := range done.KeptDrafts {
		fmt.Fprintf(w, "下書きを残しました: %s（%s）\n", k.Path, k.Reason)
	}
}
