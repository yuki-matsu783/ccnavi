package wapmerge

import (
	"fmt"
	"io"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/wap"
	"ccnavi/internal/tickets/lifecycle/closing"
	"ccnavi/internal/tickets/lifecycle/treegate"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/phase/scope"
)

// key は答えの行の頭（sh が読む）。
type key string

const (
	keySkip  key = "skip"
	keyStop  key = "stop"
	keyHint  key = "hint"
	keyChild key = "child"
	keyTree  key = "tree"
	keyHead  key = "head"
	keyBase  key = "base"
	keyMerge key = "merge"

	mergeYes = "yes"
	mergeNo  = "no"
)

const (
	// shown は文に並べるパスの数の上限。
	shown = 8
	// gitlink は submodule の項のモード。
	gitlink = "160000"
)

func gitOptions() git.Options { return git.Options{Timeout: git.MergeTimeout, RawPaths: true} }

func names(listing string) []string {
	var out []string
	for _, p := range strings.Split(listing, "\x00") {
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

func shownPaths(paths []string) string {
	more := ""
	head := paths
	if len(paths) > shown {
		more = " ほか " + strconv.Itoa(len(paths)-shown) + " 件"
		head = paths[:shown]
	}
	return strings.Join(head, ", ") + more
}

// oneLine は 1 行 1 項目の値に改行を混ぜない（`\n` の 2 文字にする）。
func oneLine(text string) string {
	return strings.ReplaceAll(strings.ReplaceAll(text, "\r", `\r`), "\n", `\n`)
}

// say は値の空白（Python の str.split の空白）を 1 つの空白につめて 1 行にする。
func say(stdout io.Writer, k key, text string) {
	fmt.Fprintf(stdout, "%s %s\n", k, strings.Join(strings.FieldsFunc(text, ospath.IsSpace), " "))
}

func stopWith(stdout io.Writer, why string, hints ...string) {
	say(stdout, keyStop, why)
	for _, hint := range hints {
		say(stdout, keyHint, hint)
	}
}

// Plan は `ccnavi write-and-push merge <子>`。合流の前の検査。子の先頭は 1 回だけ読み、検査も合流（sh の
// `git merge <sha>`）もこの sha を使う（検査のあとに子が積んだコミットは合流に載せない）。
func Plan(stdout io.Writer, conf *config.Settings, root, ident string) int {
	io.WriteString(stdout, wap.Head+"\n")
	parentID := wap.FamilyOf(ident)
	if parentID == ident {
		say(stdout, keySkip, "親は合流しない")
		return 0
	}
	found := closing.Find(io.Discard, conf, root, ident)
	if found == nil || !found.IsChild() {
		say(stdout, keySkip, "子の承認済みチケットを引けない（finish が理由を言う）")
		return 0
	}
	if found.State != place.Doing || found.StartedAt == "" {
		say(stdout, keySkip, "作業中の着手済みの子ではない（finish が理由を言う）")
		return 0
	}
	parentTree := tree.WorktreePath(root, parentID)
	childTree := tree.WorktreePath(root, ident)
	if why, hints, bad := treeProblem(conf, parentTree, childTree, ident, found.Project != ""); bad {
		stopWith(stdout, why, hints...)
		return 0
	}
	head := git.Head(childTree, git.Options{Timeout: git.StateTimeout})
	parentHead := git.Head(parentTree, git.Options{Timeout: git.StateTimeout})
	if head == "" || parentHead == "" {
		stopWith(stdout, "子 "+ident+" の先頭を読めない。何も変えずに止めた")
		return 0
	}
	copies, _ := approval.Scan(conf, root, false, nil)
	parent := approval.ByID(copies)[parentID]
	places, outside, err := scope.MergeFindings(conf, root, found, parent, head)
	if err != nil {
		stopWith(stdout, "子 "+ident+" の変更を調べられない（"+err.Error()+"）。何も変えずに止めた")
		return 0
	}
	if len(places) > 0 {
		stopWith(stdout, "子 "+ident+" のコミットに、チケットの置き場の変更がある（"+shownPaths(places)+"）。"+
			"置き場はユーザの承認と ccnavi が書くもので、子のコミットでは親へ持ち込めない。合流しない",
			"置き場の変更を子のコミットから取り除いてください"+
				"（ccnavi-git.sh では置き場のパスを戻せないので、できなければユーザに頼む）")
		return 0
	}
	if len(outside) > 0 {
		area := strings.Join(append(found.Paths(rules.Allow), found.Paths(rules.Ask)...), ", ")
		if area == "" {
			area = "(空)"
		}
		listed := make([]string, len(outside))
		for i, f := range outside {
			listed[i] = f.Path + limitNote(f.Verdict)
		}
		stopWith(stdout, "子 "+ident+" のコミットに、範囲の外のパスがある（"+shownPaths(listed)+"）。範囲は "+area+"。合流しない",
			"範囲の中へ戻すか、必要ならユーザに伝えて次のチケットにしてください。直してコミットしてから finish をやり直す")
		return 0
	}
	base, err := git.ChildBase(childTree, parentTree, 0, head)
	if err != nil {
		stopWith(stdout, "子 "+ident+" の先頭を読めない（"+err.Error()+"）。何も変えずに止めた")
		return 0
	}
	ignored, why := ignoredInParent(parentTree, childTree, base, head)
	if why != "" {
		stopWith(stdout, "子 "+ident+" の変更を調べられない（"+why+"）。何も変えずに止めた")
		return 0
	}
	if len(ignored) > 0 {
		stopWith(stdout, "子 "+ident+" が変えたパスが、親のワークツリーに git の無視するファイルとしてある（"+shownPaths(ignored)+"）。"+
			"合流すると黙って上書きし、戻すときに消すので、合流しない",
			"親のワークツリーのそのファイルを、ユーザが中身を確かめて別の場所へ移すか消してから finish をやり直す")
		return 0
	}
	unsafe, why := unrestorable(childTree, base, head)
	if why != "" {
		stopWith(stdout, "子 "+ident+" の変更を調べられない（"+why+"）。何も変えずに止めた")
		return 0
	}
	if len(unsafe) > 0 {
		stopWith(stdout, "子 "+ident+" のコミットに、合流を戻せない形の変更がある（"+strings.Join(unsafe, "。")+"）。"+
			"push が通らなかったときに元へ戻せないので、合流しない",
			"その変更を子のコミットから取り除くか、ユーザに伝えて別の手順で入れてください。直してコミットしてから finish をやり直す")
		return 0
	}
	merge := mergeYes
	if git.IsAncestor(childTree, head, parentHead, git.Options{Timeout: git.MergeTimeout}).OK() {
		merge = mergeNo
	}
	say(stdout, keyChild, ident)
	// ワークツリーのパスは空白を含みうるので、つめずにそのまま出す。
	fmt.Fprintf(stdout, "%s %s\n", keyTree, childTree)
	say(stdout, keyHead, head)
	say(stdout, keyBase, base)
	say(stdout, keyMerge, merge)
	return 0
}

var limitNames = map[scope.Limit]string{
	scope.LimitParent:  "親の範囲の外",
	scope.LimitType:    "フェーズ定義の範囲の外",
	scope.LimitBlocked: "子のチケットを信頼できない",
}

func limitNote(v scope.Verdict) string {
	if note := limitNames[v.Limit]; note != "" {
		return "（" + note + "）"
	}
	return ""
}

// treeProblem は親と子のワークツリーが合流できる形か。できなければ（止める理由, 案内, 真）。project はプロジェクトの
// 子のとき。親のワークツリーがプロジェクトのツリーなので、共通のコピーのずれは作業の変更ではなく、ユーザに揃えて
// もらう（ready と同じ案内）。ワークスペース自身の親では、共通のコピーの置き場は共通レイヤー本体なので、ふつうの
// 変更として言う。
func treeProblem(conf *config.Settings, parentTree, childTree, ident string, project bool) (string, []string, bool) {
	if refusals := treegate.Check(treegate.Finish, parentTree, conf, treegate.Options{PlainDirty: !project}); len(refusals) > 0 {
		return refusals[0].Reason, refusals[0].Hints, true
	}
	if !ospath.IsDir(childTree) {
		return "子 " + ident + " のワークツリー（" + childTree + "）が無い。何も変えずに止めた",
			[]string{"ワークツリーを作り直して、子のブランチの作業を確かめてください"}, true
	}
	commonParent := git.Run(parentTree, []string{"rev-parse", "--git-common-dir"}, gitOptions())
	commonChild := git.Run(childTree, []string{"rev-parse", "--git-common-dir"}, gitOptions())
	if !commonParent.OK() || !commonChild.OK() ||
		!pathcase.SameReal(ospath.Join(parentTree, ospath.Strip(commonParent.Out)), ospath.Join(childTree, ospath.Strip(commonChild.Out))) {
		return "子 " + ident + " のワークツリーが親のワークツリーと同じリポジトリのものでない。何も変えずに止めた", nil, true
	}
	symbolic := git.Run(childTree, []string{"symbolic-ref", "-q", "HEAD"}, gitOptions())
	ref := ospath.Strip(symbolic.Out)
	if !symbolic.OK() || ref != "refs/heads/"+ident {
		shownRef := ref
		if shownRef == "" {
			shownRef = "分離した HEAD"
		}
		return "子 " + ident + " のワークツリーが子のブランチ " + ident + " にいない（" + shownRef + "）。何も変えずに止めた",
			[]string{"子のワークツリーで子のブランチ " + ident + " に切り替えてから finish をやり直す"}, true
	}
	status := git.Status(childTree, git.Porcelain{NUL: true, Untracked: git.UntrackedAll, NoRenames: true}, gitOptions())
	if !status.OK() {
		return "子 " + ident + " のワークツリーの状態を読めない。何も変えずに止めた", nil, true
	}
	var unsaved []string
	for _, entry := range names(status.Out) {
		if len(entry) > 3 {
			unsaved = append(unsaved, entry[3:])
		}
	}
	if len(unsaved) > 0 {
		return "子 " + ident + " のワークツリーに未コミットの変更がある（" + shownPaths(unsaved) + "）。" +
				"サブエージェントの作業がコミットされていません。何も変えずに止めた",
			[]string{"サブエージェントにコミットさせるか、不要な変更を取り除いてから finish をやり直す。" +
				"追跡外で、自分のコマンドが作ったと分かる生成物（lock・キャッシュなど）は" +
				"rm で消す（権限の確認が出る。得られなければユーザに頼む）。" +
				"ディレクトリや -rf は止まるので、ユーザに頼む。" +
				"出所が分からないものは消さず、ユーザに確かめる。" +
				"追跡済みのファイルの変更は rm せず、不要なら ccnavi-git.sh restore で戻す"}, true
	}
	return "", nil, false
}

// ignoredInParent は子が base..head で変えたパスのうち、親のワークツリーに無視されたファイルとしてあるもの。git の
// merge は無視されたファイルを黙って上書きし、合流を戻すとそのパスを消す。2 つめは読めなかった理由。
func ignoredInParent(parentTree, childTree, base, head string) ([]string, string) {
	diff := git.Run(childTree, []string{"diff", "--name-only", "--no-renames", "-z", base + ".." + head}, gitOptions())
	if !diff.OK() {
		return nil, "子の仕事の始まりからの差分を読めない"
	}
	var present []string
	for _, p := range names(diff.Out) {
		if ospath.DiskLexists(ospath.Join(parentTree, p)) {
			present = append(present, p)
		}
	}
	if len(present) == 0 {
		return nil, ""
	}
	input := strings.Join(present, "\x00") + "\x00"
	o := gitOptions()
	o.Input = &input
	o.PlainPathspecs = true
	done := git.Run(parentTree, []string{"check-ignore", "-z", "--stdin"}, o)
	if done.Code == 1 && done.Failure == nil {
		return nil, ""
	}
	if !done.OK() {
		return nil, "親のワークツリーで無視されたファイルを確かめられない"
	}
	seen := map[string]bool{}
	var found []string
	for _, p := range names(done.Out) {
		if !seen[p] {
			seen[p] = true
			found = append(found, p)
		}
	}
	slices.Sort(found)
	return found, ""
}

// unrestorable は base..head のうち、push が通らなかったときに sh が親のワークツリーを戻せない変更の理由の文
// （空なら戻せる）と、読めなかった理由。sh の戻しはパスを 1 行 1 件で扱い、無いパスは rm -f で消す。
//   - 改行を含むパスは行が割れて、戻すパスを取り違える
//   - 大文字小文字だけが違うパスは、大文字小文字を区別しないファイルシステムで片方を消すともう片方の中身まで消える
//   - submodule の追加・削除（ファイルとの入れ替えを含む）はディレクトリになり、ファイルとして戻せない
func unrestorable(childTree, base, head string) ([]string, string) {
	diff := git.Run(childTree, []string{"diff", "--raw", "--no-renames", "--ignore-submodules=none", "-z", base + ".." + head}, gitOptions())
	if !diff.OK() {
		return nil, "子の仕事の始まりからの差分を読めない"
	}
	type entry struct{ old, new, rel string }
	parts := strings.Split(diff.Out, "\x00")
	var entries []entry
	for i := 0; i+1 < len(parts); i += 2 {
		cols := strings.Fields(strings.TrimLeft(parts[i], ":"))
		if len(cols) >= 2 && parts[i+1] != "" {
			entries = append(entries, entry{cols[0], cols[1], parts[i+1]})
		}
	}
	var reasons []string
	var broken, linked []string
	folded := map[string]map[string]bool{}
	for _, e := range entries {
		if strings.ContainsAny(e.rel, "\n\r") {
			broken = append(broken, oneLine(e.rel))
		}
		key := pyfmt.Casefold(e.rel)
		if folded[key] == nil {
			folded[key] = map[string]bool{}
		}
		folded[key][e.rel] = true
		if (e.old == gitlink) != (e.new == gitlink) {
			linked = append(linked, oneLine(e.rel))
		}
	}
	if len(broken) > 0 {
		reasons = append(reasons, "改行を含むパス "+shownPaths(broken))
	}
	var clash []string
	for _, group := range folded {
		if len(group) > 1 {
			for p := range group {
				clash = append(clash, p)
			}
		}
	}
	if len(clash) > 0 {
		slices.Sort(clash)
		for i, p := range clash {
			clash[i] = oneLine(p)
		}
		reasons = append(reasons, "大文字小文字だけが違うパス "+shownPaths(clash))
	}
	if len(linked) > 0 {
		reasons = append(reasons, "submodule の追加・削除 "+shownPaths(linked))
	}
	return reasons, ""
}
