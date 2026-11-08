package notice

import (
	"fmt"
	"regexp"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/records/wiki"
	"ccnavi/internal/tickets/lifecycle/integrate"
)

// 学びの通知の通知済みの記録は logs/state/learning-notice/<リポジトリ>.json に置く。中身は
// {"patterns": {パターン名: 閾値以上だったか}}。読めない・書けないときは空とみなす（通知が改めて出ることがある）。
const (
	learningNoticeDir = "learning-notice"
	learningSelfName  = "_self"
	seenKey           = "patterns"
	// stateNameLimit はファイル名に使う名前の長さ（符号位置）。
	stateNameLimit = 40
	stateHashLen   = 8
)

var unsafeName = regexp.MustCompile(`[^A-Za-z0-9._-]`)

// learningStatePath は通知済みの記録の場所。name はプロジェクトの名前（ワークスペース自身は空）。名前は大文字小文字を
// 区別せずに比べる。ファイル名に使えない字は `_` になるので、衝突しないよう畳んだ名前の短いハッシュを足す。
// ワークスペース自身とプロジェクトは別のキーにする（`_self` という名前のプロジェクトと紛れない）。
func learningStatePath(conf *config.Settings, name string) string {
	key, hashed := learningSelfName, "self"
	if name != "" {
		key = pathcase.Fold(name)
		hashed = "project:" + key
	}
	safe := []rune(unsafeName.ReplaceAllString(key, "_"))
	safe = safe[:min(len(safe), stateNameLimit)]
	return ospath.Join(conf.State, learningNoticeDir, string(safe)+"-"+digest.Hex(digest.SHA1, hashed, stateHashLen)+fsio.JSONExt)
}

// readSeen は通知済みの記録（パターン名 → 閾値以上だったか）。読めなければ空。並びは書かれた順。
func readSeen(path string) *pyjson.Object {
	seen := &pyjson.Object{}
	data, ok := fsio.ReadObject(path)
	if !ok {
		return seen
	}
	patterns, ok := data.Value(seenKey).(*pyjson.Object)
	if !ok {
		return seen
	}
	for k, v := range patterns.All() {
		name, isName := k.(string)
		hit, isBool := v.(bool)
		if isName && isBool {
			seen.Set(name, hit)
		}
	}
	return seen
}

// decideLearning は通知を出すかと、書き直す通知済みの記録。出すのは、候補が noticeCount 件以上か閾値以上が 1 件
// 以上で、かつ記録に無い候補か新しく閾値に届いた候補があるとき。出さないときも候補でなくなった名前は外し、
// 残す名前の値は通知したときのまま持つ。
func decideLearning(rows []wiki.Row, seen *pyjson.Object, threshold, noticeCount int) (bool, *pyjson.Object) {
	current := &pyjson.Object{}
	for _, r := range rows {
		current.Set(r.Name, r.Reached(threshold))
	}
	due, changed := current.Len() >= noticeCount, false
	for name, hit := range current.All() {
		was, known := seen.Get(name.(string))
		if hit.(bool) {
			due = true
		}
		if !known || hit.(bool) && was != true {
			changed = true
		}
	}
	if due && changed {
		return true, current
	}
	kept := &pyjson.Object{}
	for name, was := range seen.All() {
		if _, ok := current.Get(name.(string)); ok {
			kept.Set(name.(string), was)
		}
	}
	return false, kept
}

// sameSeen は 2 つの記録が同じ中身か（並びは見ない）。
func sameSeen(a, b *pyjson.Object) bool {
	if a.Len() != b.Len() {
		return false
	}
	for k, v := range a.All() {
		if w, ok := b.Get(k.(string)); !ok || w != v {
			return false
		}
	}
	return true
}

func learningMessage(label string, rows []wiki.Row, threshold int) string {
	reached := 0
	for _, r := range rows {
		if r.Reached(threshold) {
			reached++
		}
	}
	var parts []string
	for _, target := range wiki.Targets() {
		n := 0
		for _, r := range rows {
			if r.Target == target {
				n++
			}
		}
		if n > 0 {
			parts = append(parts, fmt.Sprintf("%s %d", target, n))
		}
	}
	where := ""
	if label != "" {
		where = label + " の"
	}
	return fmt.Sprintf("[ccnavi] %s学びの提案の候補が %d 件ある（観測元が %d 以上: %d 件。内訳: %s）。"+
		"提案を出すには、エージェントに「学びの提案を出して」と頼んでください"+
		"（手順は .ccnavi/docs/learning-propose.md、一覧は %s --learnings）。",
		where, len(rows), threshold, reached, strings.Join(parts, "、"), config.Launcher)
}

// Learning は学びの蓄積の「提案の候補」が新しく現れたことをユーザに知らせる文。通知が無ければ空。リポジトリごと
// （ワークスペース自身と projects/<名前>/）に統合先の手元の参照（origin/<統合先>）が指すコミットの docs/wiki/ を
// チェックアウトせずに読む。決められない・読めないリポジトリはその回を見送り、通知済みの記録も変えない。
// deadline のゼロ値は期限なし。
func Learning(conf *config.Settings, root string, deadline time.Time) string {
	if !conf.LearningEnabled() {
		return ""
	}
	threshold := wiki.SourcesThreshold(conf.LearningSources)
	count := wiki.NoticeCount(conf.LearningNoticeCount)
	var out []string
	for _, main := range append([]tree.Tree{tree.MainTree(root)}, tree.Projects(conf.Projects)...) {
		if !deadline.IsZero() && !time.Now().Before(deadline) {
			break
		}
		if text := learningOne(conf, main, threshold, count, deadline); text != "" {
			out = append(out, text)
		}
	}
	return strings.Join(out, "\n")
}

func learningOne(conf *config.Settings, main tree.Tree, threshold, count int, deadline time.Time) string {
	branch, _ := integrate.Integration(main.Root, deadline)
	if branch == "" {
		return ""
	}
	sha := wiki.CommitOf(main.Root, "refs/remotes/origin/"+branch, deadline)
	if sha == "" {
		return ""
	}
	found := wiki.LoadCommit(main.Root, sha, deadline)
	if found == nil {
		return ""
	}
	rows := wiki.Candidates(found)
	path := learningStatePath(conf, main.Project)
	seen := readSeen(path)
	show, record := decideLearning(rows, seen, threshold, count)
	if show || !sameSeen(record, seen) {
		fsio.WriteJSON(path, pyjson.NewObject(seenKey, record))
	}
	if !show {
		return ""
	}
	return learningMessage(main.Project, rows, threshold)
}
