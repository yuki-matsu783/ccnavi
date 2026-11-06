package stopreview

import (
	"bytes"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyjson"
)

const (
	// 末尾から tailChunk ずつ 2 倍に広げて読み、tailLimit で止める。Python は 64 KiB ずつ足して読むが、
	// 末尾から並べた完全な行は広げても前に足されるだけなので、倍に広げても見つかる行は同じ。
	tailChunk = 64 * 1024
	tailLimit = 4 * 1024 * 1024
	tailGrow  = 2
	// 末尾の行が書きかけなら、少し待って読み直す。
	rereads    = 3
	rereadWait = 50 * time.Millisecond
	// asciiSpace は Python の bytes.strip が落とす字。
	asciiSpace = " \t\n\r\v\f"
)

// SharedKey は、hook と判定役が同じ Stop を指すためのキー。セッション ID と、transcript 末尾のサイドチェーンでないアシスタント行の uuid からなる。
// 導けなければ空を返す。末尾の行が書きかけなら少し待って読み直す。
// それでも読めなければ導けないとする。1 つ前の行の uuid で代用すると、hook と判定役でキーがずれるため。
func SharedKey(sessionID, transcript string) string {
	if sessionID == "" || transcript == "" {
		return ""
	}
	for attempt := range rereads {
		uuid, complete := lastAssistantUUID(transcript)
		if complete {
			if uuid == "" {
				return ""
			}
			return sessionID + ":" + uuid
		}
		if attempt+1 < rereads {
			time.Sleep(rereadWait)
		}
	}
	return ""
}

// lastAssistantUUID は末尾のアシスタント行の uuid。無ければ空。末尾が書きかけなら complete が偽。
func lastAssistantUUID(path string) (uuid string, complete bool) {
	uuid, complete = "", true
	err := fsio.ScanTail(path, tailChunk, tailLimit, tailGrow, func(tail []byte, _ bool) bool {
		uuid, complete = scan(bytes.Split(tail, []byte("\n")))
		return !complete || uuid != ""
	})
	if err != nil {
		return "", true
	}
	return uuid, complete
}

func scan(lines [][]byte) (string, bool) {
	first := true
	for i := len(lines) - 1; i >= 0; i-- {
		raw := bytes.Trim(lines[i], asciiSpace)
		if len(raw) == 0 {
			continue
		}
		row, ok := parseLine(raw)
		if !ok {
			if first {
				return "", false // 末尾の行が書きかけ
			}
			continue
		}
		first = false
		if row == nil || row.Value("type") != "assistant" || row.Value("isSidechain") == true {
			continue
		}
		if uuid, ok := row.Value("uuid").(string); ok && uuid != "" {
			return uuid, true
		}
	}
	return "", true
}

// parseLine は JSON の 1 行。JSON として読めなければ false、読めたがオブジェクトでなければ nil。
func parseLine(raw []byte) (*pyjson.Object, bool) {
	text, err := fsio.Decode(raw, fsio.Strict)
	if err != nil {
		return nil, false
	}
	v, err := pyjson.Loads(text)
	if err != nil {
		return nil, false
	}
	row, _ := v.(*pyjson.Object)
	return row, true
}
