package stopreview

import (
	"os"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
)

// noteSuffix は、approval-request の中の段取り（write-and-push）が道（route=）と送ったもの（commit=・file=）を書き足す
// 申し送りのファイルの後ろ。結果ファイルの名前の後ろに付ける。
const noteSuffix = ".note"

// BeginResult は approval-request の結果ファイルの名前を決め、申し送りのファイルを空で作る。返すのは結果ファイルの
// パス（申し送りはその後ろに .note）と、始めた時刻（UNIX 秒）。名前は始めた時刻と、この起動の番号。
func BeginResult(state string, now time.Time) (string, int64, bool) {
	dir := ospath.Join(state, ResultDir)
	if fsio.MakeDirs(dir) != nil {
		return "", 0, false
	}
	started := now.Unix()
	path := ospath.Join(dir, strconv.FormatInt(started, 10)+"-"+strconv.Itoa(os.Getpid())+resultSuffix)
	if os.WriteFile(path+noteSuffix, nil, 0o644) != nil {
		return "", 0, false
	}
	return path, started, true
}

// EndResult は approval-request が終わったあとに結果ファイルを書く（Stop の AI 判定が、提案を送ったか送らなかったかを
// 確定する材料）。道は中の段取りが申し送りに書いた最後の route=。書かれていなければ送る前の失敗、送ったと書いたのに
// 終了コードが 0 でなければ送ったあとの失敗にする。申し送りのほかの行はそのまま足す。中がどこで終わっても（止めた・
// 送る前の失敗・出力を切られた）、外側の段取りが呼ぶので結果は残る。
func EndResult(path, id string, rc int, started int64, now time.Time) {
	text, _ := fsio.ReadText(path+noteSuffix, fsio.Replace)
	route := ""
	var rest []string
	for _, line := range ospath.Splitlines(text) {
		if value, found := strings.CutPrefix(line, "route="); found {
			route = value
			continue
		}
		rest = append(rest, line)
	}
	switch {
	case route == "":
		route = string(routeFailedBefore)
	case rc != 0 && route == string(RouteSent):
		route = string(routeFailedAfter)
	}
	lines := []string{"id=" + id, "route=" + route, "rc=" + strconv.Itoa(rc),
		"started=" + strconv.FormatInt(started, 10), "ended=" + strconv.FormatInt(now.Unix(), 10)}
	body := strings.Join(append(lines, rest...), "\n") + "\n"
	_ = fsio.ReplaceFile(path, []byte(body), ".tmp")
	_ = os.Remove(path + noteSuffix)
}
