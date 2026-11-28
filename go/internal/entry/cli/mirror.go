package cli

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/tickets/model/useronly"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/sync/mirror"
)

var mirrorActionText = map[mirror.Action]string{
	mirror.ActionCreate: "追加",
	mirror.ActionUpdate: "更新",
	mirror.ActionDelete: "削除",
}

// mirrorUsage は `--mirror` の組み合わせの誤り。
func mirrorUsage(parser *argReader, a opts) string {
	if refused := sidewaysProblem(parser, a, "mirror", "user mirror"); refused != "" {
		return refused
	}
	yes := a.Str("yes")
	route := a.Str("via")
	switch {
	case yes != "" && yes != bareYes:
		return "--yes には値を付けられません（user mirror [<名前>…] --yes --expect <値>）"
	case !a.IsNone("expect") && yes == "":
		return "--expect は --yes と一緒に指定してください"
	case yes != "" && a.IsNone("expect"):
		return "--yes には --expect が必要です（user mirror を --yes なしで実行すると、値が出ます）"
	case route != "" && route != string(via.Board):
		return "--via に渡せるのは board だけです"
	case route != "" && (yes == "" || a.IsNone("expect") || !a.Truthy("json")):
		return "--via board は --yes --expect <値> --json と一緒に指定してください"
	case a.Truthy("preview") && yes != "":
		return "--preview と --yes は同時に指定できません"
	case a.Truthy("json") && yes == "" && !a.Truthy("preview"):
		return "--json は --preview か --yes と一緒に指定してください"
	}
	return ""
}

// runMirror は `ccnavi user mirror`。プロジェクトのミラーを、ワークスペースの共通設定に揃える。見るだけの形を
// 含めてユーザ専用。書くのはワークツリーだけで、コミットはしない。
func runMirror(in *input, stdout, stderr io.Writer, parser *argReader, conf *config.Settings, root string, a opts) int {
	// 1. 引数の組み合わせを確かめる。
	asJSON := a.Truthy("json")
	if refused := mirrorUsage(parser, a); refused != "" {
		return failAnswer(stdout, stderr, asJSON, mirror.JSONVersion, refused)
	}
	// 2. 書く形（--yes）。経路（ターミナルかボード）を確かめてから、--expect の値が計画と合うときだけ揃える。
	if a.Str("yes") != "" {
		if _, ok := sidewaysRoute(in, conf, stderr, a, useronly.OpMirror); !ok {
			return config.ExitError
		}
		done := mirror.Apply(conf, mirror.Prepare(conf, root, wanted(a)), a.Str("expect"))
		if asJSON {
			writeAnswer(stdout, mirror.AppliedJSON(done))
			return okIf(done.OK)
		}
		if !done.OK {
			fmt.Fprintf(stderr, "ccnavi: %s\n", done.Error)
			mirrorApplied(stdout, done)
			mirrorLeft(stdout, done, root)
			return config.ExitError
		}
		mirrorApplied(stdout, done)
		return config.ExitOK
	}
	// 3. 見るだけの形（--preview）。計画を出して、何も書かない。
	plan := mirror.Prepare(conf, root, wanted(a))
	if plan.Error != "" {
		return failAnswer(stdout, stderr, asJSON, mirror.JSONVersion, plan.Error)
	}
	if asJSON {
		writeAnswer(stdout, mirror.PlanJSON(plan))
		return config.ExitOK
	}
	preview := a.Truthy("preview")
	if !preview && !fromTerminal(in, conf, stderr, useronly.OpMirror) {
		return config.ExitError
	}
	mirrorPlan(stdout, plan, wanted(a) != nil, root, preview)
	if preview {
		return config.ExitOK
	}
	// 4. ターミナル。計画を見せて y/N を聞き、y のときだけ、見せた計画の値で揃える。
	if len(plan.Chosen()) == 0 {
		return config.ExitOK
	}
	fmt.Fprint(stdout, "この内容で揃える場合は y、やめる場合はそれ以外: ")
	if !fsio.ReadYes(in.buf) {
		fmt.Fprintln(stderr, "ccnavi: 揃えなかった")
		return config.ExitError
	}
	via.SetRoute(via.Terminal)
	done := mirror.Apply(conf, mirror.Prepare(conf, root, wanted(a)), plan.Expect())
	mirrorApplied(stdout, done)
	if !done.OK {
		fmt.Fprintf(stderr, "ccnavi: %s\n", done.Error)
		mirrorLeft(stdout, done, root)
		return config.ExitError
	}
	return config.ExitOK
}

// mirrorLeft は一部のプロジェクトだけが揃ったとき、通ったもの・残り・打ち直す 1 行を出す
// （決まりは .agents/docs/script-output.md の「ユーザ専用 sh の作法」）。揃わなかった分だけがずれとして残るので、
// 打ち直しは同じ入口でよい。
func mirrorLeft(w io.Writer, done *mirror.Applied, root string) {
	if len(done.Synced) == 0 {
		return
	}
	names := make([]string, len(done.Synced))
	for i, s := range done.Synced {
		names[i] = s.Name
	}
	fmt.Fprintf(w, "通ったもの: %s\n残り: %s\n次: %s\n", strings.Join(names, ", "), done.Error,
		config.ScriptCommand(root, useronly.UserScriptPrefix+"mirror.sh"))
}

// mirrorPlan はミラーの状態を人が読む形で書く。何も反映しない。preview のときは、揃えるときに実行するコマンドを添える
// （ターミナルの y/N の前は、コマンドを添えず注意だけを出す）。
func mirrorPlan(w io.Writer, plan *mirror.Plan, named bool, root string, preview bool) {
	fmt.Fprintln(w, "ccnavi: 共通設定のミラー（各プロジェクトの .ccnavi/config/common/）と工程スキル（skills/）")
	if len(plan.Projects) == 0 {
		fmt.Fprintln(w, "  プロジェクトはありません。")
		return
	}
	if plan.CommonEmpty {
		fmt.Fprintln(w, "  共通設定にも工程スキルにもコピーするものがありません。ミラーは消さず、何も揃えません。")
	}
	for _, p := range plan.Projects {
		if !p.Selected {
			continue
		}
		switch p.State {
		case mirror.StateSynced:
			fmt.Fprintf(w, "  [同期済み] %s\n", p.Name)
		case mirror.StateError:
			fmt.Fprintf(w, "  [調べられない] %s: %s\n", p.Name, p.Error)
		default:
			fmt.Fprintf(w, "  [ずれ %d 件] %s\n", len(p.Changes), p.Name)
			// ずれの内訳と、見比べる方法。skills/ の元は .claude/skills/ にあり、それ以外は .ccnavi/ のミラーなので、案内が分かれる。
			skills, others := false, false
			for _, c := range p.Changes {
				fmt.Fprintf(w, "      %s %s\n", mirrorActionText[c.Action()], c.Rel)
				if c.IsSkill() {
					skills = true
				} else {
					others = true
				}
			}
			if p.MirrorPath != "" && plan.CommonPath != "" {
				if others {
					fmt.Fprintf(w, "      .ccnavi/ の中身の差を見るには: diff -ru %s %s\n", shell.Quote(p.MirrorPath),
						shell.Quote(plan.CommonPath))
				}
				if skills {
					fmt.Fprintf(w, "      skills/ の元は、ワークスペースの .claude/skills/<工程>/ の SKILL.md と references/common/。"+
						"%s と見比べる\n", shell.Quote(ospath.Join(p.Root, config.SkillsDir)))
				}
			}
		}
	}
	chosen := plan.Chosen()
	if len(chosen) == 0 {
		fmt.Fprintln(w, "揃えるミラーはありません。")
		return
	}
	if preview {
		command := config.ScriptCommand(root, useronly.UserScriptPrefix+"mirror.sh") + " "
		if named {
			names := make([]string, len(chosen))
			for i, p := range chosen {
				names[i] = shell.Quote(p.Name)
			}
			command += strings.Join(names, " ") + " "
		}
		fmt.Fprint(w, "何も反映していません。この内容で揃えるなら、次を実行します。ワークツリーに書くだけで、")
		fmt.Fprintln(w, "コミットはしません。")
		fmt.Fprintf(w, "  %s--yes --expect %s\n", command, plan.Expect())
	} else {
		fmt.Fprintln(w, "ワークツリーに書くだけで、コミットはしません。")
	}
	// 新規の作成だけなら注意は要らない。更新と削除があれば、ミラーの手入れを失うので、確かめてから実行するよう添える。
	for _, p := range chosen {
		for _, c := range p.Changes {
			if c.Action() != mirror.ActionCreate {
				fmt.Fprintln(w, "更新と削除は、ミラーの今の中身を共通設定の中身で上書き・削除します。"+
					"ミラーや skills/ に手を入れていたなら、上の案内で確かめてから実行します。")
				return
			}
		}
	}
}

// mirrorApplied は揃えたプロジェクトごとに、ファイルの一覧を書く。
func mirrorApplied(w io.Writer, done *mirror.Applied) {
	for _, s := range done.Synced {
		fmt.Fprintf(w, "揃えた: %s（%d 件。コミットはしていません）\n", s.Name, len(s.Files))
		for _, rel := range s.Files {
			fmt.Fprintf(w, "  %s\n", rel)
		}
	}
}
