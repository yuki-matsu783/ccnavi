package subagent

import (
	"errors"
	"io/fs"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
)

// 印の名前。records/prune が同じ前置きと後ろで古いセッションのものを消す。
const (
	markPrefix      = "subagent-"
	bouncedSuffix   = ".bounced"
	uncheckedSuffix = ".unchecked"
	runningSuffix   = ".running"
	// unknownName は空のセッション・相手の代わりに名前に入れる語。
	unknownName = "unknown"
)

// 印の中身の行の頭。1 行目はセッション、実行中の印の 3 行目以降は編集するサブエージェントのツリーと種類。
const (
	sessionHead = "session "
	treeHead    = "tree "
	typeHead    = "type "
)

// markPath は印の置き場。セッションを名前に入れるのは state の置き場がワークスペースに 1 つだけだから（入れないと、
// 別のセッションの差し戻しを読んで、一度も差し戻していない相手を通す）。who は agent_id、無ければワークツリーの名前
// （1 つの名前にまとめると、最初の 1 体のあと誰も差し戻されなくなる）。
func markPath(state, session, who, suffix string) string {
	return ospath.Join(state, sessionPrefix(session)+fsio.SafeNameOr(who, unknownName, fsio.SafeNameLimit)+suffix)
}

func sessionPrefix(session string) string {
	return markPrefix + fsio.SafeNameOr(session, unknownName, fsio.SafeNameLimit) + "-"
}

// sessionLine は印の 1 行目。
func sessionLine(session string) string {
	return sessionHead + strings.NewReplacer("\r", " ", "\n", " ").Replace(session)
}

// markText は印の中身。1 行目にセッション、続けて 1 行に 1 つ。子の識別子の無い範囲の印は、SubagentStop が内部エラーで
// 終わった回。
func markText(session string, lines []string) string {
	var b strings.Builder
	for _, line := range append([]string{sessionLine(session)}, lines...) {
		b.WriteString(line + "\n")
	}
	return b.String()
}

// markLines は印の行。読めなければ ok が偽。
func markLines(file string) (lines []string, ok bool) {
	text, ok := fsio.ReadText(file, fsio.Replace)
	if !ok {
		return nil, false
	}
	return ospath.Splitlines(text), true
}

// readMark はこのセッションの範囲の印なら子の識別子（内部エラーの回は空）。違う・無いなら ok が偽。
func readMark(file, session string) ([]string, bool) {
	lines, ok := markLines(file)
	if !ok || len(lines) == 0 || lines[0] != sessionLine(session) {
		return nil, false
	}
	tickets := []string{}
	for _, line := range lines[1:] {
		if line != "" {
			tickets = append(tickets, line)
		}
	}
	return tickets, true
}

// startedAt は実行中の印の開始時刻（エポック秒）。読めなければ ok が偽。
func startedAt(lines []string) (float64, bool) {
	if len(lines) < 2 {
		return 0, false
	}
	seconds, err := pyfmt.ParseFloat(lines[1])
	return seconds, err == nil
}

// flat は splitlines が行の区切りとみなす字を空白に替える。印は行で読むので、区切りが残ると値が途中で切れる。
func flat(text string) string { return strings.Join(ospath.Splitlines(text), " ") }

// mark は前置きで見つけた印 1 つ。
type mark struct{ who, file string }

// sessionMarks はこのセッションの前置きに当たる印（名前順）。置き場が無ければ空、一覧できなければ ok が偽。
func sessionMarks(state, session, suffix string) ([]mark, bool) {
	names, err := fsio.Listdir(state)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, true
	}
	if err != nil {
		return nil, false
	}
	slices.Sort(names)
	head := sessionPrefix(session)
	var out []mark
	for _, name := range names {
		if strings.HasPrefix(name, head) && strings.HasSuffix(name, suffix) && len(name) > len(head)+len(suffix) {
			out = append(out, mark{name[len(head) : len(name)-len(suffix)], ospath.Join(state, name)})
		}
	}
	return out, true
}

// runningMax は実行中の印を「実行中」と数える上限（秒）。長すぎる害は、取りこぼした印が促しを止める時間が伸びるだけ。
// 短すぎると、長く走る体を待つ回で誤って促す。
func runningMax() float64 { return config.SubagentRunningMax.Value().Seconds() }

// Running は、このセッションに実行中のサブエージェントが居るか。メインの Stop が、委任して待つ回を促しで止めないために
// 読む。読めないときは真（促さない側）。開始時刻を読めない印は更新時刻で期限を見る。
func Running(state, session string) bool {
	if state == "" {
		return false
	}
	marks, ok := sessionMarks(state, session, runningSuffix)
	if !ok {
		return true
	}
	when := fsio.UnixSeconds(fsio.Now())
	for _, m := range marks {
		lines, read := markLines(m.file)
		if read && len(lines) > 0 && lines[0] != sessionLine(session) {
			continue
		}
		started, ok := startedAt(lines)
		if !ok {
			mtime, _, found := ospath.Stat(m.file)
			if !found {
				return true
			}
			started = fsio.UnixSeconds(mtime)
		}
		if when-started <= runningMax() {
			return true
		}
	}
	return false
}

// ForgetRunning はこのセッションの実行中の印を全部消す。起動（startup）と再開（resume）の後始末で、どちらも新しい
// プロセスなので前の体は生きていない。前置きが重なる別のセッションの印は消さない。1 行目を読めない印はここで消す。
func ForgetRunning(state, session string) {
	if state == "" {
		return
	}
	marks, _ := sessionMarks(state, session, runningSuffix)
	for _, m := range marks {
		if lines, _ := markLines(m.file); len(lines) > 0 && lines[0] != sessionLine(session) {
			continue
		}
		fsio.Remove(m.file)
	}
}

// bounced は、この相手を差し戻したことがあるか。
func bounced(state, session, who string) bool {
	return state != "" && fsio.Exists(markPath(state, session, who, bouncedSuffix))
}
