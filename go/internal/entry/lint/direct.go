package lint

import (
	"fmt"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/notice"
)

// 直接作業（ccnavi-worktree.sh で切るワークツリー）の記録の検査。見るのは、ワークスペース自身と各プロジェクトの
// ユーザのチェックアウトの git の共通ディレクトリ（ccnavi-direct/・ccnavi-integrate/）、refs/ccnavi/direct/、
// 一時ワークツリーの置き場。git を起こすのは ref の一覧とブランチの有無だけ。

// directNeed は記録に欠かせない欄。欠ければ取り込み先と起点を読めない。
var directNeed = []string{"base_branch", "snap_sha", "common_dir", "main_tree"}

// directRefs は取り込みの途中の ref の置き場。その下の `<名前>/<種類>` のうち directRefKinds が専用の ref。
const directRefs = "refs/ccnavi/direct"

var directRefKinds = map[string]bool{"t": true, "c": true, "k": true}

func directProblems(conf *config.Settings, root string) []rules.Problem {
	var out []rules.Problem
	for _, main := range mainTrees(conf, root) {
		common, ok := tree.CommonDir(main.Root)
		if !ok {
			continue
		}
		label := tree.RepoKey(main.Project)
		out = append(out, directRecords(root, main, common, label)...)
		out = append(out, directMarkers(common, label)...)
		out = append(out, directLeftovers(root, main, label)...)
		out = append(out, directUnrecorded(conf, root, main, common, label)...)
	}
	return out
}

func directWhere(label, name string) string { return "(direct/" + label + "/" + name + ")" }

// visibleNames は dir の下の、`.` で始まらない名前（名前の順）。読めなければ偽。
func visibleNames(dir string) ([]string, bool) {
	names, err := ospath.DiskListdir(dir)
	if err != nil {
		return nil, false
	}
	var out []string
	for _, n := range names {
		if !strings.HasPrefix(n, ".") {
			out = append(out, n)
		}
	}
	return out, true
}

// directRecords は記録（ccnavi-direct/ の 1 件ごと）を見る。壊れた記録は warn（integrate は断る）、作業ワークツリーも
// ブランチも無い記録は片付け残りとして info、有効な横で作業する記録は自動復元が緩むことを info で言う。
func directRecords(root string, main tree.Tree, common, label string) []rules.Problem {
	names, ok := visibleNames(ospath.Join(common, tree.DirectDir))
	if !ok {
		return nil
	}
	// 実行時の判定が有効と読む記録の名前。ファイルがあっても、ここに入らないものは「有効な横作業」と言わない。
	valid := map[string]bool{}
	for _, r := range tree.DirectRecords(root, main) {
		valid[r.Name] = true
	}
	var out []rules.Problem
	for _, name := range names {
		path := ospath.Join(common, tree.DirectDir, name)
		if !ospath.IsFile(path) {
			continue
		}
		text, _ := fsio.ReadDiskText(path, fsio.Replace)
		// 記録の壊れ方を調べる。版が違うか必須の欄が欠けていれば、取り込み先と起点を読めない。
		fields := tree.ParseDirect(text)
		where := directWhere(label, name)
		var missing []string
		for _, k := range directNeed {
			if fields[k] == "" {
				missing = append(missing, k)
			}
		}
		badVersion := fields["version"] != tree.DirectVersion
		if badVersion || len(missing) > 0 {
			var why []string
			if badVersion {
				why = append(why, "版が "+tree.DirectVersion+" ではありません")
			}
			if len(missing) > 0 {
				why = append(why, strings.Join(missing, "・")+" がありません")
			}
			out = append(out, rules.Problem{Severity: rules.Warn, Rule: where, Detail: fmt.Sprintf(
				"直接作業の記録（%s/%s）が壊れています（%s）。取り込み先と起点を読めないので、integrate は断ります。"+
					"ユーザに確かめてから、記録を消して切り直すか、--adopt で引き取ります", tree.DirectDir, name, strings.Join(why, "、"))})
			continue
		}
		// 壊れていない記録は、実体（ワークツリーとブランチ）が残っているかで言うことを分ける。
		switch {
		case !ospath.IsDir(tree.WorktreePath(root, name)) && !hasBranch(main.Root, name):
			out = append(out, rules.Problem{Severity: rules.Info, Rule: where, Detail: fmt.Sprintf(
				"記録（%s/%s）はありますが、作業ワークツリーもブランチもありません。"+
					"取り込み済みの片付け残りか、手で消したものです。ユーザに確かめて、記録を消します", tree.DirectDir, name)})
		case valid[name]:
			out = append(out, rules.Problem{Severity: rules.Info, Rule: where, Detail: "横で作業する指定が有効です。" + main.Root +
				" の通常のソースの編集は、実行後チェックで自動復元せず、出所不明の変更として報告します" +
				"（ccnavi の設定ファイルは戻します）。ワークツリーを消すと元の動きに戻ります"})
		}
	}
	return out
}

func hasBranch(top, name string) bool {
	return git.Run(top, []string{"show-ref", "--verify", "--quiet", "refs/heads/" + name}, git.Options{}).OK()
}

// directMarkers は取り込みの marker。版を知らないものは warn（integrate は断る）、版 4 の途中のものは info。
func directMarkers(common, label string) []rules.Problem {
	base := ospath.Join(common, integrate.RecordDir)
	names, ok := visibleNames(base)
	if !ok {
		return nil
	}
	var out []rules.Problem
	for _, name := range names {
		where := directWhere(label, name)
		switch integrate.MarkerKind(base, name) {
		case integrate.MarkerUnknown:
			out = append(out, rules.Problem{Severity: rules.Warn, Rule: where, Detail: fmt.Sprintf(
				"取り込みの marker（%s/%s）の版を知りません。integrate は断ります。版 4 でない記録は ccnavi が読めません。"+
					"ユーザが統合先のstaged を git status で確かめ、不要なら git reset --merge で戻してから、"+
					"記録のディレクトリを手で消します", integrate.RecordDir, name)})
		case integrate.MarkerV4:
			out = append(out, rules.Problem{Severity: rules.Info, Rule: where, Detail: fmt.Sprintf(
				"取り込みの途中の marker（%s/%s）が残っています。integrate-done か integrate --abort で終わらせます",
				integrate.RecordDir, name)})
		}
	}
	return out
}

// directLeftovers は残った一時ワークツリーと専用の ref（info）。
func directLeftovers(root string, main tree.Tree, label string) []rules.Problem {
	var out []rules.Problem
	// 取り込みの途中の一時ワークツリー。リポジトリごとの置き場に作られる。
	tmp := ospath.Join(root, config.DirectTmpDir, notice.RepoKey(root, main.Root))
	entries, _ := ospath.DiskListdir(tmp)
	for _, entry := range entries {
		out = append(out, rules.Problem{Severity: rules.Info, Rule: directWhere(label, entry), Detail: "一時ワークツリー（" +
			ospath.Join(tmp, entry) + "）が残っています。integrate --abort か integrate-done が片付けます。中身を確かめずに消しません"})
	}
	// 専用の ref の名前は `<接頭辞>/<名前>/<種類>` の形。種類が取り込みの途中を表すものだけ言う。
	listed := git.Run(main.Root, []string{"for-each-ref", "--format=%(refname)", directRefs}, git.Options{})
	if !listed.OK() {
		return out
	}
	for _, ref := range ospath.Splitlines(listed.Out) {
		parts := strings.Split(ref, "/")
		if len(parts) >= 5 && directRefKinds[parts[4]] {
			out = append(out, rules.Problem{Severity: rules.Info, Rule: directWhere(label, parts[3]), Detail: "取り込みの途中の ref（" +
				ref + "）が残っています。integrate --abort か integrate-done が片付けます"})
		}
	}
	return out
}

// directUnrecorded は記録の無い直接作業のワークツリー（手の worktree add で切ったもの。info）。
func directUnrecorded(conf *config.Settings, root string, main tree.Tree, common, label string) []rules.Problem {
	base := tree.WorktreesPath(root)
	entries, err := ospath.DiskListdir(base)
	if err != nil {
		return nil
	}
	var out []rules.Problem
	for _, name := range entries {
		work := ospath.Join(base, name)
		if !ospath.IsDir(work) || ospath.IsLink(work) {
			continue
		}
		// 別のリポジトリのワークツリーや、リンクは言わない。共通の .git が同じものだけを、ここのワークツリーとみなす。
		mine, ok := tree.CommonDir(work)
		if !ok || pathcase.Fold(mine) != pathcase.Fold(common) {
			continue
		}
		if ospath.IsFile(ospath.Join(common, tree.DirectDir, name)) {
			continue
		}
		// 直接作業として取り込める形のものだけ言う（DirectCheck が断る・決められないものは、直接作業ではない）。
		here := tree.Tree{Name: name, Root: work, Project: main.Project, Kind: tree.KindWorktree}
		if reason, unsure := integrate.DirectCheck(conf, root, here, time.Time{}); reason != "" || unsure {
			continue
		}
		out = append(out, rules.Problem{Severity: rules.Info, Rule: directWhere(label, name), Detail: fmt.Sprintf(
			".claude/worktrees/%s に、直接作業の記録（%s）がありません。integrate は断ります。"+
				"ユーザに取り込み先を確かめて、ccnavi-worktree.sh %s --adopt --base <取り込み先> で引き取ります", name, tree.DirectDir, name)})
	}
	return out
}
