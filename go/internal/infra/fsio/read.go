package fsio

import (
	"bufio"
	"errors"
	"io"
	"os"
	"time"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
)

// 読みの打ち直し。差し替えの一瞬に重なっただけなら、すぐ開けるようになる。
const (
	readRetries   = 3
	readRetryWait = 20 * time.Millisecond
)

// readFile はディスクから読む（テストが失敗を差し込む）。
var readFile = os.ReadFile

// sleep は打ち直しの間の待ち（テストが差し替える）。
var sleep = time.Sleep

// ReadBytes は中身をそのまま読む。読めなければ false。
// 改行を変換しない。バックアップから戻したファイルを元とバイト単位で同じにするため。
func ReadBytes(path string) ([]byte, bool) {
	if content, staged, ok := stagedContent(path); staged {
		return content, ok
	}
	data, err := readFile(path)
	return data, err == nil
}

// ReadText は本文。読めなければ false。改行は "\n" にそろう。
// Strict で UTF-8 として読めないときも false にする（Python は例外のまま上へ抜けていた）。
func ReadText(path string, bad Undecodable) (string, bool) {
	data, ok := ReadBytes(path)
	if !ok {
		return "", false
	}
	text, err := Decode(data, bad)
	return text, err == nil
}

// LoadSource は 1 回だけ読んだバイト列と、それを ReadText と同じ読み方で解いた本文。
// 解析した中身と、照合や書き込みに使うバイト列を同じ読みから取るために使う。
// 読めなければ errors.Is(err, fs.ErrNotExist) で「無い」を分けられるエラー、UTF-8 でなければ *DecodeError。
func LoadSource(path string) ([]byte, string, error) {
	content, staged, ok := stagedContent(path)
	if !staged {
		var err error
		if content, err = readFile(path); err != nil {
			return nil, "", wrap(err)
		}
	} else if !ok {
		return nil, "", wrap(notFound(path))
	}
	text, err := Decode(content, Strict)
	if err != nil {
		return nil, "", err
	}
	return content, text, nil
}

// ReadJSON は JSON を読む。返すエラーで「無い」（errors.Is(err, fs.ErrNotExist)）と
// 「壊れている」（*pyjson.DecodeError・*DecodeError）を分ける。無いのは普通の状態で、壊れているのは
// 言わないと直らない。文は Python の str(exc) と同じ。
//
// 一時的に開けないだけなら数回打ち直す。Windows では差し替え（WriteText の Atomic）の一瞬に
// 開こうとした側が共有違反で開けず、打ち直さないと記録を「まだ何も無い」と読んで数えが 0 に戻る。
// 書く側の打ち直しと対で、片方だけでは並んで走る hook に対応できない。
func ReadJSON(path string) (any, error) {
	content, staged, ok := stagedContent(path)
	if staged && !ok {
		return nil, wrap(notFound(path))
	}
	if !staged {
		var err error
		for attempt := 0; ; attempt++ {
			if content, err = readFile(path); err == nil {
				break
			}
			// 無い（ENOENT）は待っても現れないので打ち直さない。
			if !transient(err) || attempt == readRetries {
				return nil, wrap(err)
			}
			sleep(readRetryWait)
		}
	}
	text, err := Decode(content, Strict)
	if err != nil {
		return nil, err
	}
	return pyjson.Loads(text)
}

// ReadObject は JSON のマップ。読めなければ false、読めたがマップでなければ空のマップ。
func ReadObject(path string) (*pyjson.Object, bool) {
	data, err := ReadJSON(path)
	if err != nil {
		return nil, false
	}
	if obj, ok := data.(*pyjson.Object); ok {
		return obj, true
	}
	return pyjson.NewObject(), true
}

// ReadLine はユーザの答えを 1 行読む（改行も含む）。読めなければ空文字（ターミナルが閉じている、など）。
func ReadLine(r *bufio.Reader) string {
	line, err := r.ReadString('\n')
	if err != nil && !errors.Is(err, io.EOF) {
		return ""
	}
	return line
}

// Exists は在るか（リンクは辿る）。溜める段があれば、そこで書いた・消した分を先に見る。
func Exists(path string) bool {
	if _, staged, ok := stagedContent(path); staged {
		return ok
	}
	return ospath.Exists(path)
}

// Lexists は在るか（リンクそのものも数える）。溜める段は Exists と同じに見る。
func Lexists(path string) bool {
	if _, staged, ok := stagedContent(path); staged {
		return ok
	}
	return ospath.Lexists(path)
}

// Listdir はディレクトリの名前の一覧（順序は決めない）。溜める段があれば、そこで足した名前を足し、
// 消した名前を除く。ディレクトリが無く、溜める段も何も足していなければエラー。
func Listdir(directory string) ([]string, error) {
	names, err := listNames(directory)
	if current == nil {
		return names, err
	}
	added, dropped := current.namesIn(directory)
	if err != nil {
		if !errors.Is(err, os.ErrNotExist) || len(added) == 0 {
			return nil, err
		}
		names = nil
	}
	have := map[string]bool{}
	var out []string
	for _, n := range names {
		have[n] = true
		if !dropped[n] {
			out = append(out, n)
		}
	}
	for _, n := range added {
		if !have[n] {
			out = append(out, n)
		}
	}
	return out, nil
}

func listNames(directory string) ([]string, error) {
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil, wrap(err)
	}
	names := make([]string, len(entries))
	for i, e := range entries {
		names[i] = e.Name()
	}
	return names, nil
}
