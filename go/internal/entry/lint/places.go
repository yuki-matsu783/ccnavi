package lint

import (
	"fmt"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
)

// 置き場の検査。プロジェクトの置き場が正しい形か、走査されないチケットの置き場が残っていないかを見る。

// trackedLead は projects/ がワークスペースの git の索引に載っているときの warn の先頭の句（%s は置き場）。ぶつかりと
// 載せ忘れで共通にする。載せ忘れは句の直後が `（入れ子のリポジトリとして` になる。導入スクリプト（ccnavi-setup.sh）は
// 実行ファイルを起こさずに同じ文面を組むので、変えるならそちらも直す（tests/sh/test_setup.py が文面全体の一致を見る）。
const trackedLead = "`%s/` はワークスペースの git が追跡している"

// 置き場の苦情の種類。`--lint --json` の problems[].kind に載り、VS Code 拡張のプロジェクト管理画面は文面でなく
// これで見分ける（索引に載っている・無視されていない）。
const (
	kindProjectsTracked   = "projects-tracked"
	kindProjectsUnignored = "projects-unignored"
)

// gitlinkMode は索引の mode のうち gitlink（入れ子のリポジトリを 1 つの版として載せたもの）。
const gitlinkMode = "160000"

// shSpecial は sh が語を割る・展開する文字。導入スクリプトの sh_word が同じ集合を持つ（TestShWordAgreesWithTheSetupSh が比べる）。
const shSpecial = " \t\n'\"\\$`!*?[](){}<>|&;#~"

// projectPlaces はプロジェクトの置き場が正しい形か。置き場が索引に載っていればそれを言い、載っていなければ git で
// 無視されているかを見る（置き場のディレクトリが在るときだけ。無いのはプロジェクトを使わないワークスペース）。
// 予約名のプロジェクト（error）と、プロジェクトの中のワークツリー（warn）も言う。
func projectPlaces(conf *config.Settings, root string) []rules.Problem {
	// 1. 置き場が索引に載っているか、載っていなければ無視されているかを見る。
	var out []rules.Problem
	rel := projectsRel(conf, root)
	indexed, isIndexed := "", false
	if rel != "" {
		indexed, isIndexed = inIndex(root, rel)
	}
	if isIndexed {
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: whereProjects, Detail: indexed, Kind: kindProjectsTracked})
	}
	// 末尾の `/` は付けない。projects がリンクだと git が `projects/` を rc=128 で断る。
	if rel != "" && !isIndexed && ospath.IsDir(conf.Projects) {
		if ignored, known := isIgnored(root, rel); known && !ignored {
			out = append(out, rules.Problem{Severity: rules.Warn, Rule: whereProjects, Detail: rel +
				"/ がワークスペースの git で無視されていない。プロジェクトは自分の git を持つので、ワークスペースの `.gitignore` に入れてください",
				Kind: kindProjectsUnignored})
		}
	}
	// 2. プロジェクトごとの検査。予約名のプロジェクトはレイヤーとして数えないので error にする。
	quoted := make([]string, len(config.ReservedLayerNames()))
	for i, name := range config.ReservedLayerNames() {
		quoted[i] = "`" + name + "`"
	}
	for _, p := range tree.Projects(conf.Projects) {
		where := "(projects/" + p.Name + ")"
		if config.IsReservedLayerName(p.Name) {
			out = append(out, rules.Problem{Severity: rules.Error, Rule: where, Detail: fmt.Sprintf(
				"%s はレイヤーの名前として予約してある（`%s` は共通レイヤー、`%s` はワークスペース自身のレイヤー）。"+
					"このプロジェクトはレイヤーとして数えていない（id の `%s:` がどちらのレイヤーを指すか決まらないため。"+
					"大文字小文字の違いは問わない）。ここに置いた宣言は 1 件も使われておらず、このプロジェクトを行き先にするパスを持つ"+
					"ツール（Read / Grep / Glob / Write / Edit / MultiEdit / NotebookEdit）は共通レイヤーだけで判定している。"+
					"プロジェクトを別の名前に変えてください",
				strings.Join(quoted, " と "), config.LayerCommon, config.LayerSelf, p.Name)})
		}
		out = append(out, misplacedWorktrees(p, where)...)
	}
	return out
}

// misplacedWorktrees はプロジェクトの中の `.claude/worktrees/` にあるワークツリー（warn。承認の走査の対象外で、そこに
// 書いた提案は承認待ちに並ばない）。プロジェクトの ccnavi-common.sh のコピーをルートと取り違えて着手するとこの形になる。
func misplacedWorktrees(project tree.Tree, where string) []rules.Problem {
	// ディレクトリだけを数える（ファイルは置き場の飾りで、ワークツリーではない）。
	base := tree.WorktreesPath(project.Root)
	entries, err := ospath.DiskListdir(base)
	if err != nil {
		return nil
	}
	var names []string
	for _, n := range entries {
		if ospath.IsDir(ospath.Join(base, n)) {
			names = append(names, n)
		}
	}
	if len(names) == 0 {
		return nil
	}
	return []rules.Problem{{Severity: rules.Warn, Rule: where, Detail: "プロジェクトの中にワークツリーがある（" + strings.Join(names, ", ") +
		"）。ワークツリーはワークスペースの `.claude/worktrees/` に置く。ここのものは承認の走査の対象外で、" +
		"書いた提案は承認待ちに並ばない。ワークスペースの ccnavi が古い（`projects/<名前>` のコピーをルートと取り違える）" +
		"可能性があるので、`scripts/ccnavi-setup.sh --force` で配り直し、このワークツリーはユーザに伝えて片付ける"}}
}

// projectlessWorktrees は元のプロジェクトが無いワークツリー（warn）。
func projectlessWorktrees(conf *config.Settings, root string) []rules.Problem {
	var out []rules.Problem
	for _, w := range tree.WorktreesWithoutProject(root, conf.Projects) {
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: "(worktrees/" + w.Name + ")", Detail: w.Said()})
	}
	return out
}

// projectsRel はプロジェクトの置き場の、ワークスペースルートからの相対（"/" 区切り）。空やワークスペースの外
// （診断のフラグ・別のドライブ）を指すときは空（ワークスペースの git の話ではなくなる）。
func projectsRel(conf *config.Settings, root string) string {
	if conf.Projects == "" {
		return ""
	}
	rel, ok := pathcase.Under(conf.Projects, root)
	if !ok || rel == "." {
		return ""
	}
	return ospath.Slashed(rel)
}

// indexUnder は索引で `<rel>/` の下に載っているものの、最初の通常のファイルと gitlink の一覧。-z で読むのでパスの
// 引用に左右されない。衝突中の段で同じパスが並ぶのは 1 本に畳む。git が無い・失敗したときは偽。
func indexUnder(root, rel string) (string, []string, bool) {
	code, out := git.Output(root, []string{"ls-files", "-s", "-z", "--", rel + "/"}, git.Options{Timeout: git.StateTimeout})
	if code != 0 {
		return "", nil, false
	}
	// -z の出力は `<mode> <object> <stage>\t<path>` を NUL でつないだもの。mode が gitlink のものだけを別に集める。
	firstFile := ""
	var links []string
	seen := map[string]bool{}
	for _, record := range strings.Split(out, "\x00") {
		head, path, found := strings.Cut(record, "\t")
		if !found || path == "" {
			continue
		}
		if mode, _, _ := strings.Cut(head, " "); mode == gitlinkMode {
			if !seen[path] {
				seen[path] = true
				links = append(links, path)
			}
		} else if firstFile == "" {
			firstFile = path
		}
	}
	return firstFile, links, true
}

// shWord は案内のコマンドに載せるパスを、sh で 1 語として読める表記にする。割れる文字を含まなければそのまま、
// 含めば `'…'` で囲み、中の `'` は、引用を閉じて `\'` を挟み、また開く形に置き換える。導入スクリプトの sh_word と 1 字違わず同じ表記にする。
func shWord(path string) string {
	if !strings.ContainsAny(path, shSpecial) {
		return path
	}
	return "'" + strings.ReplaceAll(path, "'", `'\''`) + "'"
}

// inIndex は置き場が索引に載っているなら、その苦情の文。通常のファイルがあればぶつかり（ワークスペース自身のソースに
// projects/ がある。.gitignore に足すとソースが追跡から外れるので改名を案内する）、gitlink だけなら載せ忘れ（索引から
// 外して無視に入れる 2 手を案内する）。導入スクリプトが同じ条件と同じ文を持つので、変えるならそちらも直す。
func inIndex(root, rel string) (string, bool) {
	firstFile, links, ok := indexUnder(root, rel)
	if !ok {
		return "", false
	}
	// 通常のファイルがあるとき（ぶつかり）と、gitlink だけのとき（載せ忘れ）で、直し方が逆になる。
	lead := fmt.Sprintf(trackedLead, rel)
	if firstFile != "" {
		text := fmt.Sprintf("%s（例: `%s`）。ccnavi はワークスペース直下の `%s/` をプロジェクトの置き場として使い、名前は変えられない。"+
			"このままだと `%s/` の下で `.git` を持つディレクトリ（サブモジュールを含む）がプロジェクトとして数えられ、"+
			"その中の設定が判定に使われる。直すには、ワークスペースの `%s/` を別の名前に移す（例: `git mv %s apps`）。"+
			"ccnavi でプロジェクトを置かないなら、このままでも動く。そのときは `%s/` の下に `.git` を持つものを置かない",
			lead, firstFile, rel, rel, rel, rel, rel)
		if len(links) > 0 {
			named := make([]string, len(links))
			words := make([]string, len(links))
			for i, p := range links {
				named[i] = "`" + p + "`"
				words[i] = shWord(p)
			}
			text += fmt.Sprintf("。索引には入れ子のリポジトリ（%s）も載っている。ccnavi のプロジェクトとして使うなら、改名の前に"+
				" `git rm --cached %s` で索引から外し、改名のあとで `%s/` の下へ戻す", strings.Join(named, "、"), strings.Join(words, " "), rel)
		}
		return text, true
	}
	if len(links) == 0 {
		return "", false
	}
	more := ""
	if len(links) > 1 {
		more = fmt.Sprintf(" ほか %d 件", len(links)-1)
	}
	return fmt.Sprintf("%s（入れ子のリポジトリとして: `%s`%s）。`.gitignore` に入れる前に `git add` したものとみられる。"+
		"プロジェクトは自分の git を持つので、ワークスペースの git には載せない。"+
		"載せたままだと、ワークスペースのコミットがプロジェクトの版を記録し続け、`.gitignore` に `/%s/` を追加しても追跡は外れない。"+
		"直すには、ワークスペースで `git rm -r --cached %s` を実行し（ファイルは消えない。まだコミットしていなければ `git reset -- %s`）、"+
		"`.gitignore` に `/%s/` を追加して（既にあればそのまま）、コミットする", lead, links[0], more, rel, rel, rel, rel), true
}

// ticketPlaces は走査されないチケットの置き場（ワークスペースの `wip/<名前>/proposals/`）が残っていないか。提案があっても
// 画面にもボードにも出ず、気づかないうちに消えるので名指しする。判定は動いているので warn。
func ticketPlaces(conf *config.Settings, root string) []rules.Problem {
	// チケットの置き場のパス（例 `wip/<名前>/proposals`）を要素に分け、名前を挟む位置を決める。
	var parts []string
	for _, p := range strings.Split(conf.Tickets, "/") {
		if p != "" {
			parts = append(parts, p)
		}
	}
	if len(parts) < 2 {
		// 名前を挟む位置が置き場のパスから決められない。
		return nil
	}
	head, tail := parts[0], parts[1:]
	known := map[string]bool{}
	for _, p := range tree.Projects(conf.Projects) {
		known[p.Name] = true
	}
	where := projectsRel(conf, root)
	if where == "" {
		where = conf.Projects
	}
	if where == "" {
		where = "(無し)"
	}
	// 先頭の下にある兄弟のうち、提案の置き場の形をしているものを探す。tail[0] と同じ名前は、走査される本来の置き場なので飛ばす。
	names, err := ospath.DiskListdir(ospath.Join(root, head))
	if err != nil {
		return nil
	}
	var out []rules.Problem
	for _, name := range names {
		if name == tail[0] {
			continue
		}
		if !ospath.IsDir(ospath.Join(root, append([]string{head, name}, tail...)...)) {
			continue
		}
		rel := strings.Join(append([]string{head, name}, tail...), "/")
		why := name + " が " + where + " のプロジェクトとして数えられていない（`.git` がまだ無いか、名前が違う）"
		if known[name] {
			proper := strings.Join(append([]string{where, name}, parts...), "/")
			why = "プロジェクト向けの提案はそのプロジェクトの側 " + proper + "/ に置いてください"
		}
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: whereProjects, Detail: rel + "/ の提案は走査されていない。" + why})
	}
	return out
}

// isIgnored はこのパスをワークスペースの git が無視しているか。git が無い・答えが読めなければ known が偽。
func isIgnored(root, rel string) (ignored, known bool) {
	// check-ignore の終了コードは、無視していれば 0、していなければ 1。それ以外（128 など）は答えが読めない。
	done := git.Run(root, []string{"check-ignore", "-q", rel}, git.Options{Timeout: git.StateTimeout})
	if done.Failure != nil {
		return false, false
	}
	switch done.Code {
	case 0:
		return true, true
	case 1:
		return false, true
	}
	return false, false
}
