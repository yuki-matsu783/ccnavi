package gitlog

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/texts"
)

// marker は記録の見出しと本文の境目の行。本文はこの次の行から。
const marker = "--- 出力 ---"

// Input は 1 回の git の結果と、出し方。
type Input struct {
	Root, Cwd string
	// LogDir は記録の置き場（ワークスペースルートの logs/、プロジェクトの中なら logs/<プロジェクト>/）。
	LogDir string
	Sub    string
	Args   []string
	Status int
	Output []byte
	// MaxLines は成功したときに返す先頭の行数、FailLines は失敗したときに返す末尾の行数、Keep は残す記録の本数。
	MaxLines, FailLines, Keep int
	// Warn は成功したときに後ろに出す警告、Target は統合先のユーザのチェックアウト（空でなければ）。
	Warn, Target string
	// SelfQ は案内に載せるラッパーの呼び方（引用済み）。
	SelfQ string
}

// Report は記録を書き、要約と本文の一部を w に書く。記録の相対パス（ワークスペースルートから。cwd から開けなければ
// 絶対パス）と本文の行数を返す。記録を書けなければ err。
//
// 処理フロー:
//  1. 見出し（時刻・cwd・打った git）と出力を記録に書く（成功でも書く。後から「何が出ていたか」を確かめられるように）
//  2. 要約（Summary）と、成功なら先頭 MaxLines 行と残りの案内・警告、失敗なら末尾 FailLines 行を書く
//  3. 失敗のときだけ、直し方の案内を足す（Windows で cwd が中にあって worktree remove が消せなかった、統合先の
//     ユーザのチェックアウトの pull が衝突した）
//  4. 記録を新しい順に Keep 本だけ残す
func Report(w io.Writer, in Input) (rel string, lines int, err error) {
	if err := os.MkdirAll(in.LogDir, 0o755); err != nil {
		return "", 0, err
	}
	now := time.Now()
	path := ospath.Join(in.LogDir, fmt.Sprintf("git-%s-%d.log", now.Format("20060102-150405"), os.Getpid()))
	head := fmt.Sprintf("# %s  cwd=%s\n$ git %s", now.Format("2006-01-02T15:04:05"), in.Cwd, in.Sub)
	for _, a := range in.Args {
		head += " " + a
	}
	if err := os.WriteFile(path, append([]byte(head+"\n"+marker+"\n"), in.Output...), 0o644); err != nil {
		return "", 0, err
	}
	rel = relative(in.Root, in.Cwd, path)
	body := splitLines(string(in.Output))
	if in.Status == 0 {
		fmt.Fprintf(w, "ok  git %s  %s  log=%s\n", in.Sub, Summary(in.Sub, body), rel)
		writeLines(w, body[:min(len(body), in.MaxLines)])
		if len(body) > in.MaxLines {
			fmt.Fprintf(w, "... 残り %d 行は %s にある（全文は Read ツールでこのファイルを読む）\n", len(body)-in.MaxLines, rel)
		}
		if in.Warn != "" {
			fmt.Fprintf(w, "警告: %s\n", in.Warn)
		}
	} else {
		fmt.Fprintf(w, "fail  git %s  exit=%d  log=%s\n", in.Sub, in.Status, rel)
		writeLines(w, body[max(0, len(body)-in.FailLines):])
		notes(w, in, body)
	}
	prune(in.LogDir, in.Keep)
	return rel, len(body), nil
}

// notes は失敗したときの直し方の案内。
func notes(w io.Writer, in Input, body []string) {
	// Windows では、プロセスの cwd になっているディレクトリは使用中になる。Bash ツールの cwd は呼び出しをまたいで残る
	// 親のシェルのものなので、ワークツリーの中へ cd したまま remove すると、git が中身を消したあと最後のディレクトリで
	// Permission denied になり、空のディレクトリが残る。ここで cwd を見ても親の cwd は分からないので、起きたときに直し方を言う。
	if in.Sub == "worktree" && len(in.Args) > 0 && in.Args[0] == "remove" && contains(body, "Permission denied") {
		pathQ := "<パス>"
		if len(in.Args) > 1 && in.Args[1] != "" {
			pathQ = shell.SingleQuote(in.Args[1])
		}
		fmt.Fprintln(w, texts.Say(in.Root, "NOTE_GIT_WORKTREE_REMOVE_CWD", texts.Values{"ws": in.Root, "self_q": in.SelfQ, "path_q": pathQ}, nil))
	}
	// 統合先のユーザのチェックアウトの pull が衝突したとき（未解決の索引がある）だけ、解かずにユーザへ頼む案内を足す。
	// ワークツリーの中の衝突は、そこで解く普段の作業なので足さない。
	if in.Target != "" {
		if code, out := git.Output(in.Cwd, []string{"ls-files", "-u"}, git.Options{}); code == 0 && strings.TrimSpace(out) != "" {
			fmt.Fprintln(w, texts.Say(in.Root, "NOTE_GIT_PULL_CONFLICT_ON_TARGET", texts.Values{"integ": in.Target}, nil))
		}
	}
}

// Summary は、次の判断に使う数だけを 1 句で言う（本文をもう 1 度読ませないため。本文の代わりではない）。
// diff・show はファイル数と増減の行数、log・shortlog はコミットの数、status は変更の件数、ほかは行数。
func Summary(sub string, body []string) string {
	var files, plus, minus, commits, entries int
	for _, l := range body {
		switch {
		case strings.HasPrefix(l, "diff --git "):
			files++
		case strings.HasPrefix(l, "+") && !strings.HasPrefix(l, "+++"):
			plus++
		case strings.HasPrefix(l, "-") && !strings.HasPrefix(l, "---"):
			minus++
		}
		if len(l) > 7 && strings.HasPrefix(l, "commit ") && strings.ContainsRune("0123456789abcdef", rune(l[7])) {
			commits++
		}
		if statusEntry(l) {
			entries++
		}
		if strings.HasPrefix(l, "\t") {
			entries++
		}
	}
	switch {
	case (sub == "diff" || sub == "show") && files > 0:
		return fmt.Sprintf("%d ファイル +%d -%d", files, plus, minus)
	case (sub == "log" || sub == "shortlog") && commits > 0:
		return fmt.Sprintf("%d コミット", commits)
	case sub == "status":
		return fmt.Sprintf("変更 %d 件", entries)
	}
	return fmt.Sprintf("%d 行", len(body))
}

// statusEntry は `status --short` の 1 件の行か（先頭の 2 字が状態の字で、3 字目が空白）。
func statusEntry(l string) bool {
	const marks = " MADRCU?!"
	return len(l) >= 3 && strings.IndexByte(marks, l[0]) >= 0 && strings.IndexByte(marks, l[1]) >= 0 && l[2] == ' '
}

// relative は記録のパスの言い方。ワークスペースルートの下なら相対（エージェントがそのまま開ける）、cwd から相対で
// 開けなければ絶対パス。2 つは並べない（受け取った側がどちらを開くか迷う）。
func relative(root, cwd, path string) string {
	rel, ok := strings.CutPrefix(path, root+ospath.Sep)
	if !ok || !ospath.IsFile(ospath.Join(cwd, rel)) {
		return path
	}
	return rel
}

// prune は記録を新しい順に並べ、keep 本より後ろを消す。
func prune(dir string, keep int) {
	logs, _ := filepath.Glob(filepath.Join(dir, "git-*.log"))
	type dated struct {
		path string
		at   time.Time
	}
	var all []dated
	for _, p := range logs {
		if info, err := os.Stat(p); err == nil {
			all = append(all, dated{p, info.ModTime()})
		}
	}
	sort.SliceStable(all, func(i, j int) bool { return all[i].at.After(all[j].at) })
	for i := keep; i < len(all); i++ {
		os.Remove(all[i].path)
	}
}

// splitLines は本文を行に分ける（最後の改行の後ろの空は行に数えない。awk の行数と同じ）。
func splitLines(text string) []string {
	if text == "" {
		return nil
	}
	return strings.Split(strings.TrimSuffix(text, "\n"), "\n")
}

func writeLines(w io.Writer, lines []string) {
	for _, l := range lines {
		fmt.Fprintln(w, l)
	}
}

func contains(lines []string, word string) bool {
	for _, l := range lines {
		if strings.Contains(l, word) {
			return true
		}
	}
	return false
}
