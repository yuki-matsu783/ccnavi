package docs

import (
	"io"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/catalog/docindex"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/texts"
)

const (
	// ExitUsage は `--docs` の使い方の誤り（引くのは 0 件でも 0）。
	ExitUsage = 1

	errPrefix = "ccnavi: "
	// unignoredSaid・ignoreAddedSaid は標準エラーと SessionStart の案内でツリーの名前・.gitignore の後ろに続ける文。
	unignoredSaid   = "は .gitignore に `" + docindex.IgnoreLine + "` が無く、足せなかったので索引の対象外"
	ignoreAddedSaid = "に `" + docindex.IgnoreLine + "` を追加しました（未コミットの差分が残る）"
	unignoredWhy    = "（.gitignore が書けないか、" + docindex.IndexName + " を否定する行がある）"
	foreignSaid     = "ccnavi の索引ではないので書き換えなかった: "
	noticePrefix    = "[ccnavi] md の索引（--docs）: "

	// conventionDoc は frontmatter の決まりの文書。ワークスペースに在るときだけ案内に名前を出す。
	conventionDoc = ".ccnavi/docs/frontmatter.md"
	// foreignShown は案内で名指しする、触らなかった ccnavi-index.jsonl の数の上限。
	foreignShown = 3

	// GuideHead は引き方の案内の頭。作業の決まりの案内が、この案内を出した回にだけ「詳しくは --docs で引く」を
	// 添えるのに使う。
	GuideHead = "[ccnavi] ドキュメント（*.md）を探すときは"
	guideLead = GuideHead + "、grep・Glob より先に"
)

// SessionStart で索引を新しくするのに使う時間。使い回せていれば数百本でも 1 秒に届かず、初めての大きな
// プロジェクトだけがここに届く（書けた分は次のセッションで使い回す）。
const (
	// startBudget は使ってよい時間の上限。実際は hook の判定の期限の残りと小さいほう。
	startBudget = 3 * time.Second
	// minRefresh に残りが満たなければ新しくしない（既存の ccnavi-index.jsonl だけで案内する）。
	minRefresh = 500 * time.Millisecond
	// staleBudget は新しくしないときに git に与える時間（ls-files と check-ignore）。
	staleBudget = time.Second
)

// Run は `ccnavi --docs`。ワークスペースと、索引の対象になるプロジェクトの md を引く。使い方の誤りは ExitUsage、
// 0 件でも 0。期限は無い（初めての回は md を全部読む。2 回目からは mtime の変わった md だけ）。
func Run(stdout, stderr io.Writer, conf *config.Settings, root string, q Query, refresh bool) int {
	if problems := usageProblems(q); len(problems) > 0 {
		for _, p := range problems {
			io.WriteString(stderr, errPrefix+p+"\n")
		}
		return ExitUsage
	}
	found := collect(conf, root, collectOptions{refresh: refresh, parse: true})
	var said strings.Builder
	for _, name := range withoutGit(conf) {
		said.WriteString(errPrefix + "プロジェクト " + name + " は .git を持たないので引かない\n")
	}
	for _, failure := range found.failed {
		said.WriteString(errPrefix + "git への問い合わせに失敗したので引かない: " + failure + "\n")
	}
	for _, problem := range found.problems {
		said.WriteString(errPrefix + "索引を残せなかった（引くのは続ける）: " + problem + "\n")
	}
	for _, path := range found.ignoreAdded {
		said.WriteString(errPrefix + path + " " + ignoreAddedSaid + "\n")
	}
	if len(found.unignored) > 0 {
		said.WriteString(errPrefix + strings.Join(found.unignored, ", ") + " " + unignoredSaid + "\n")
	}
	io.WriteString(stderr, said.String())
	hits, matched := search(found.rows, q)
	render(stdout, hits, matched, len(found.rows), q.format())
	if f := q.format(); f == FormatTable || f == FormatDetail {
		io.WriteString(stderr, countLine(len(hits), matched, len(found.rows)))
	}
	return 0
}

// AtStart は SessionStart の案内。ワークスペースとプロジェクトの索引を差分で新しくし、引き方の案内を返す。
// 使う時間は startBudget と deadline（hook の判定の期限。ゼロ値は無し）の残りの小さいほう。残りが minRefresh に
// 満たなければ、md を読まず書かずに既存の索引だけで案内する。md が 1 本も無ければ空。git への問い合わせに
// 失敗したツリーは何も言わない（対象外と取り違えさせない）。
func AtStart(conf *config.Settings, root string, deadline time.Time) string {
	now := time.Now()
	limit := now.Add(startBudget)
	if !deadline.IsZero() && deadline.Before(limit) {
		limit = deadline
	}
	var found *collected
	if limit.Sub(now) >= minRefresh {
		found = collect(conf, root, collectOptions{refresh: true, parse: true, deadline: limit})
	} else {
		found = collect(conf, root, collectOptions{deadline: now.Add(staleBudget)})
		found.timedOut = true
	}
	return notice(conf, root, found)
}

func notice(conf *config.Settings, root string, found *collected) string {
	var extra []string
	if len(found.ignoreAdded) > 0 {
		extra = append(extra, strings.Join(found.ignoreAdded, ", ")+" "+ignoreAddedSaid)
	}
	if len(found.unignored) > 0 {
		extra = append(extra, strings.Join(found.unignored, ", ")+" "+unignoredSaid+unignoredWhy)
	}
	if len(found.foreign) > 0 {
		names := strings.Join(found.foreign[:min(len(found.foreign), foreignShown)], ", ")
		if rest := len(found.foreign) - foreignShown; rest > 0 {
			names += " ほか " + strconv.Itoa(rest) + " 本"
		}
		extra = append(extra, foreignSaid+names)
	}
	// 行が無くても、対象のツリーに md がある（打ち切りで読めなかった）か、時間切れで git に聞けなかったが
	// ワークスペースに md があるなら、引き方は案内する。
	guide := len(found.rows) > 0 || found.indexable
	if !guide && found.timedOut && len(found.unignored) == 0 {
		guide = hasMarkdown(root)
	}
	if !guide {
		lines := make([]string, len(extra))
		for i, e := range extra {
			lines[i] = noticePrefix + e
		}
		return strings.Join(lines, "\n")
	}
	lines := guideLines(conf, root, guideLead)
	for _, e := range extra {
		lines = append(lines, "  "+e)
	}
	return strings.Join(lines, "\n")
}

// hasMarkdown はワークスペースルートの直下に md があるか（git に聞けないときの目安）。
func hasMarkdown(root string) bool {
	names, err := ospath.DiskListdir(root)
	if err != nil {
		return false
	}
	for _, name := range names {
		if strings.HasSuffix(name, ".md") && ospath.IsFile(ospath.Join(root, name)) {
			return true
		}
	}
	return false
}

// guideLines は引き方の案内（lead の後ろに `--docs` の呼び方が続く 1 行）。起動・再開・compact・clear のたびに
// 届く常駐の文脈なので、要点の 1 行だけにする（オプションの一覧と frontmatter の書き方は渡さない）。
func guideLines(conf *config.Settings, root, lead string) []string {
	return []string{texts.Say(root, "NOTICE_DOCS_GUIDE", texts.Values{"lead": lead, "command": command(conf, root)},
		texts.Variants{"what": conventionCase(root)})}
}

// FailStreakNote は最初の失敗か拒否のとき（first）と、連続（count）が閾値の倍数になったときの案内。止められた
// 呼び出しを迂回させず、止まった理由と決まった手順を資料で確かめさせる。
func FailStreakNote(conf *config.Settings, root string, count int, first bool) string {
	var lead string
	if first {
		lead = texts.Say(root, "NOTICE_DOCS_FAIL_FIRST_LEAD", texts.Values{}, nil)
	} else {
		lead = texts.Say(root, "NOTICE_DOCS_FAIL_STREAK_LEAD", texts.Values{"n": strconv.Itoa(count)}, nil)
	}
	return strings.Join(guideLines(conf, root, lead), "\n")
}

// command は案内に書く ccnavi の打ち方。設定が相対ならワークスペースルートから解く。
func command(conf *config.Settings, root string) string {
	path := conf.Bin
	if path != "" && !ospath.IsAbs(path) {
		path = ospath.Realpath(ospath.Join(root, path))
	}
	return config.BinCommand(path)
}

// conventionCase は文面の場合分け（決まりの文書がワークスペースに在るか）。
func conventionCase(root string) string {
	if ospath.IsFile(ospath.Join(root, strings.Split(conventionDoc, "/")...)) {
		return "convention"
	}
	return "plain"
}
