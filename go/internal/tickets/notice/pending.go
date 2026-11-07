package notice

import (
	"fmt"
	"maps"
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/integrate"
)

// 終わっていない integrate の記録（統合先の git ディレクトリの ccnavi-integrate/<名前>/marker）を知らせる。
// 別のセッションの取り込みの途中かもしれないので、操作する前にユーザへ確かめさせる。版 4 の記録は K
// （作った取り込みの先頭）の有無と取り込み先に載ったかで続きの一手を言い、合わないもの・版を知らないものは
// 壊れた記録として「操作せずにユーザに確かめる」と言う。自動では再開しない。

const (
	pendingHead = "[ccnavi] 終わっていない integrate の記録があります。別のセッションの取り込みの場合もあるため、" +
		"操作する前に、担当と今の状態をユーザに確かめてください。" +
		"この案内は、統合先のツリーの変更を操作してよい根拠ではありません。"
	pendingCut = "ほかのツリーは時間切れで確かめていません"
	// pendingShortFrom 件以上なら、1 件ずつの続きの一手は出さず、名前の一覧にまとめる。
	pendingShortFrom = 5
	markerFile       = "marker"
	// directRefs は integrate が置く専用の ref の頭（refs/ccnavi/direct/<名前>/<種類>）。
	directRefs = "refs/ccnavi/direct"
	// shaShown は文に出す K の sha の長さ。
	shaShown = 9
)

// v4Need は版 4 の記録に要る欄。
var v4Need = []string{"head", "tip", "c_tree"}

// leftoverRefKinds は取り込みの跡として挙げる ref の種類。s（起点）と pending（切る前の確認待ち）は普通の状態。
var leftoverRefKinds = []string{"t", "c", "k"}

// recordsDir はそのツリーの ccnavi-integrate/。無ければ空。`.git` がディレクトリならファイルの有無で確かめ、
// ファイル（ワークツリーやサブモジュール）のときだけ git に場所を引く。
func recordsDir(top string, deadline time.Time) string {
	dot := ospath.Join(top, ".git")
	if ospath.IsDir(dot) {
		path := ospath.Join(dot, integrate.RecordDir)
		if ospath.IsDir(path) {
			return path
		}
		return ""
	}
	if !ospath.DiskExists(dot) {
		return ""
	}
	if path, ok := integrate.GitPath(top, integrate.RecordDir, deadline); ok && ospath.IsDir(path) {
		return path
	}
	return ""
}

// gitShIn は top へ移ってから ccnavi-git.sh を打つ形（`cd <top> && sh …/ccnavi-git.sh`）。
func gitShIn(root, top string) string {
	return "cd " + config.ShellPath(top) + " && " + config.ScriptCommand(root, config.GitWrapper)
}

// unknownLine は版 4 でない marker 1 件の知らせ。
func unknownLine(root, top, name, version string) string {
	if version == "" {
		version = "無し"
	}
	return fmt.Sprintf("%s（取り込み先のツリー %s）: 記録の版（%s）を知りません。操作せず、"+
		"`%s status` の結果をユーザに見せて確かめてください。"+
		"不要なら、ユーザが git reset --merge で統合先の staged を戻し、"+
		"ccnavi-integrate/%s を手で消します", name, config.ShellPath(top), version, gitShIn(root, top), name)
}

// v4Line は版 4 の marker 1 件の知らせ。
func v4Line(root, top, name string, fields map[string]string, deadline time.Time) string {
	head := name + "（取り込み先のツリー " + config.ShellPath(top) + "）: "
	cd := gitShIn(root, top)
	broken := func(why string) string {
		return head + "記録が壊れているか、今の状態と合いません（" + why + "）。" +
			"操作せず、`" + cd + " status` の結果をユーザに見せて確かめてください"
	}
	var missing []string
	for _, k := range v4Need {
		if fields[k] == "" {
			missing = append(missing, k)
		}
	}
	if len(missing) > 0 {
		return broken(strings.Join(missing, "・") + " がありません")
	}
	base := ""
	if rec := integrate.DirectRecord(top, name); rec != nil {
		base = rec.Fields["base_branch"]
	}
	now := git.Head(top, git.Options{Deadline: deadline})
	if now == "" {
		return broken("HEAD を読めません")
	}
	abort := "やめるなら `" + cd + " integrate --abort " + name + "`"
	redo := "ユーザに確かめてから `" + cd + " integrate --abort " + name + "` で戻し、" +
		"`" + cd + " integrate " + name + "` を打ち直してください"
	if k := fields["k"]; k != "" && k != "-" {
		parent, okP := git.Text(top, []string{"rev-parse", "--verify", k + "^"}, git.Options{Deadline: deadline})
		treeOf, okT := git.Text(top, []string{"rev-parse", "--verify", k + "^{tree}"}, git.Options{Deadline: deadline})
		if !okP || !okT || parent != fields["head"] || treeOf != fields["c_tree"] {
			return broken("K の親か木が記録と違います。K を採用しません")
		}
		shown := pyfmt.Head(k, shaShown)
		if base != "" && git.IsAncestor(top, k, "refs/heads/"+base, git.Options{Deadline: deadline}).OK() {
			return head + "K（" + shown + "）は取り込み先に載っています。片付けが途中で止まっています。" +
				"`" + cd + " integrate-done " + name + "` を打ち直すと、残りを片付けます"
		}
		if now == fields["head"] {
			return head + "K（" + shown + "）を作ってありますが、取り込み先にはまだ載っていません。" +
				"ユーザの了承があるなら `" + cd + " integrate-done " + name + "` を打ち直すと続きから進みます。" + abort
		}
		return head + "K を作ったあとに取り込み先が動きました。自動では取り込みません。" + redo
	}
	if now != fields["head"] {
		return head + "確認の途中で取り込み先が動きました（記録の HEAD と違います）。" + redo
	}
	return head + "取り込み結果を一時ワークツリー（" + config.ShellPath(fields["tmp"]) + "）" +
		"に用意して、確認を待っています。差分と変わるファイルの一覧をユーザに見せ、" +
		"確定はユーザの了承（対話では確認の許可、非対話では返事を待って次の発言）を得ていれば、" +
		"`" + cd + " integrate-done " + name + " -m '<メッセージ>'` を打ちます。" + abort
}

// RepoKey は一時ワークツリーの置き場の名前（config.DirectTmpDir/<repo>/）。ワークスペース自身は tree.Self。
func RepoKey(root, top string) string {
	if pathcase.SameReal(top, root) {
		return tree.Self
	}
	return ospath.Basename(ospath.Normpath(top))
}

type pendingLine struct{ name, text string }

// leftovers は marker の無い名前で残っている、取り込みの途中の跡（一時ワークツリー・専用の ref・片付けの途中の
// done の記録）。名前の順。作業中の記録・s と pending の ref・作業ワークツリーは普通の状態なので挙げない。
func leftovers(root, top string, reported map[string]bool, deadline time.Time) []pendingLine {
	common, ok := tree.CommonDir(top)
	if !ok {
		return nil
	}
	names := map[string][]string{}
	tmpBase := ospath.Join(root, config.DirectTmpDir, RepoKey(root, top))
	if entries, err := ospath.DiskListdir(tmpBase); err == nil {
		for _, e := range entries {
			names[e] = append(names[e], "一時ワークツリー（"+config.ShellPath(ospath.Join(tmpBase, e))+"）")
		}
	}
	listed, _ := git.Text(top, []string{"for-each-ref", "--format=%(refname)", directRefs}, git.Options{Deadline: deadline})
	for _, ref := range ospath.Splitlines(listed) {
		parts := strings.Split(ref, "/")
		if len(parts) >= 5 && slices.Contains(leftoverRefKinds, parts[4]) {
			names[parts[3]] = append(names[parts[3]], "専用の ref（"+ref+"）")
		}
	}
	doneDir := ospath.Join(common, integrate.DoneDir)
	if entries, err := ospath.DiskListdir(doneDir); err == nil {
		for _, e := range entries {
			if integrate.ReadRecord(ospath.Join(doneDir, e))["version"] != tree.DirectVersion {
				continue
			}
			var remains []string
			if ospath.IsDir(tree.WorktreePath(root, e)) {
				remains = append(remains, "作業ワークツリー")
			}
			if _, ok := git.Text(top, []string{"show-ref", "--verify", "--quiet", "refs/heads/" + e}, git.Options{Deadline: deadline}); ok {
				remains = append(remains, "ブランチ")
			}
			if ospath.IsFile(ospath.Join(common, tree.DirectDir, e)) {
				remains = append(remains, "直接作業の記録")
			}
			if len(remains) > 0 {
				names[e] = append(names[e], "片付けの途中の done の記録（残り: "+strings.Join(remains, "・")+"）")
			}
		}
	}
	t := config.ShellPath(top)
	cd := gitShIn(root, top)
	var lines []pendingLine
	for _, name := range slices.Sorted(maps.Keys(names)) {
		if reported[name] {
			continue
		}
		lines = append(lines, pendingLine{name, name + "（取り込み先のツリー " + t + "）: 取り込みの途中の跡が残っています: " +
			strings.Join(names[name], "、") + "。" +
			"done の記録があれば `" + cd + " integrate-done " + name + "` を打ち直すと片付けます。" +
			"記録が無いものは、操作せずにユーザに確かめてください（消してよいかは持ち主が決めます）"})
	}
	return lines
}

// PendingIntegrate は終わっていない integrate の知らせ。無ければ空。見るのはワークスペースルートと各プロジェクトの
// 元ツリー。期限が過ぎたら残りのツリーは見ず、その旨を添える。記録が多ければ名前を全件 1 行に並べる（確認が要る件を
// 見落とさないため）。
func PendingIntegrate(conf *config.Settings, root string, deadline time.Time) string {
	tops := []string{root}
	for _, p := range tree.Projects(conf.Projects) {
		tops = append(tops, p.Root)
	}
	var lines []pendingLine
	var found []string // 知らせる行があったツリー
	cut := false
	for _, top := range tops {
		before := len(lines)
		if git.Expired(deadline) {
			cut = true
			break
		}
		reported := map[string]bool{}
		var names []string
		if marks := recordsDir(top, deadline); marks != "" {
			names, _ = ospath.DiskListdir(marks)
			for _, name := range names {
				if !ospath.IsDir(ospath.Join(marks, name)) {
					continue
				}
				// marker が無いものや版 4 でないものは、版を知らない記録として知らせる。
				reported[name] = true
				fields := integrate.ReadRecord(ospath.Join(marks, name, markerFile))
				if fields["version"] == tree.DirectVersion {
					lines = append(lines, pendingLine{name, v4Line(root, top, name, fields, deadline)})
				} else {
					lines = append(lines, pendingLine{name, unknownLine(root, top, name, fields["version"])})
				}
			}
		}
		if !git.Expired(deadline) {
			lines = append(lines, leftovers(root, top, reported, deadline)...)
		}
		if len(lines) > before {
			found = append(found, top)
		}
	}
	if len(lines) == 0 && !cut {
		return ""
	}
	out := []string{pendingHead}
	if len(lines) >= pendingShortFrom {
		var all []string
		for _, l := range lines {
			all = append(all, l.name)
		}
		out = append(out, summaryLine(root, all, found))
	} else {
		for _, l := range lines {
			out = append(out, l.text)
		}
	}
	if cut {
		out = append(out, pendingCut)
	}
	return strings.Join(out, "\n")
}

// summaryLine は記録が多いときの 1 行。同じ名前が別のツリーにもあり得るので、記録のあるツリーも並べる。
func summaryLine(root string, names, trees []string) string {
	var cds []string
	for _, t := range trees {
		cds = append(cds, "`"+gitShIn(root, t)+" status`")
	}
	return strings.Join(names, "、") + "。詳しくは " + strings.Join(cds, "、") + "（操作せずユーザに見せて確かめる）"
}
