package fsio

import (
	"bufio"
	"errors"
	"io"
	"io/fs"
	"os"
	"slices"
	"unicode/utf8"

	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// readFile はディスクから読む（テストが失敗を差し込む）。
var readFile = os.ReadFile

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
// Strict で UTF-8 として読めないときも false にする。
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

// ReadDiskBytes は open(path, "rb").read()。溜める段階は見ない。読めた中身は空のファイルでも nil にしない（「無い」と
// 分けるため）。読めなければ Wrap したエラーで、errors.Is(err, fs.ErrNotExist) なら無い（FileNotFoundError）。
func ReadDiskBytes(path string) ([]byte, error) {
	content, err := readFile(path)
	if err != nil {
		return nil, wrap(err)
	}
	if content == nil {
		content = []byte{}
	}
	return content, nil
}

// ReadDiskText はディスクの本文を UTF-8 として読む。溜める段階は見ない。読めなければ Wrap したエラー
// （errors.Is(err, fs.ErrNotExist) で「無い」を分けられる）、Strict で UTF-8 でなければ *DecodeError。改行は "\n" にそろう。
func ReadDiskText(path string, bad Undecodable) (string, error) {
	content, err := readFile(path)
	if err != nil {
		return "", wrap(err)
	}
	return Decode(content, bad)
}

// ReadDiskTextHead は open(path, encoding="utf-8", errors=bad).read(chars)。先頭の chars 字だけを返す（ファイルが短ければ全部）。
// UTF-8 の 1 字は最大 4 バイトなので chars*4 バイトまで読み、切れ目で欠けた文字は捨ててから解く。エラーは ReadDiskText と同じ。
func ReadDiskTextHead(path string, chars int, bad Undecodable) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", wrap(err)
	}
	defer f.Close()
	content := make([]byte, chars*utf8.UTFMax)
	n, err := io.ReadFull(f, content)
	if err != nil && !errors.Is(err, io.EOF) && !errors.Is(err, io.ErrUnexpectedEOF) {
		return "", wrap(err)
	}
	content = content[:n]
	if n == len(content) && n > 0 {
		// 続きがあるかもしれない。最後の文字の先頭まで戻り、そこから欠けていれば捨てる。
		start := n - 1
		for start > 0 && n-start < utf8.UTFMax && !utf8.RuneStart(content[start]) {
			start--
		}
		if !utf8.FullRune(content[start:]) {
			content = content[:start]
		}
	}
	text, err := Decode(content, bad)
	if err != nil {
		return "", err
	}
	runes := []rune(text)
	return string(runes[:min(len(runes), chars)]), nil
}

// ReadJSON は JSON を読む。返すエラーで「無い」（errors.Is(err, fs.ErrNotExist)）と
// 「壊れている」（*pyjson.DecodeError・*DecodeError）を分ける。無いのは普通の状態で、壊れているのは
// 言わないと直らない。文は ErrorText と同じ形。
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
			if !transient(err) || attempt == retries {
				return nil, wrap(err)
			}
			sleep(retryWait)
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

// ReadAnswer はユーザの答えを 1 行読み、前後の空白を除いて小文字にする。
// 読めなければ空文字（ターミナルが閉じている、など）。
func ReadAnswer(r *bufio.Reader) string {
	line, err := r.ReadString('\n')
	if err != nil && !errors.Is(err, io.EOF) {
		return ""
	}
	return pyfmt.Lower(ospath.Strip(line))
}

// yesAnswers は確かめの問いに「はい」と読む答え。
var yesAnswers = []string{"y", "yes"}

// ReadYes はユーザの答えを 1 行読み、y か yes（大文字小文字と前後の空白は問わない）なら真。
func ReadYes(r *bufio.Reader) bool {
	return slices.Contains(yesAnswers, ReadAnswer(r))
}

// Exists は在るか（リンクは辿る）。溜める段階があれば、そこで書いた・消した分を先に見る。
func Exists(path string) bool {
	if _, staged, ok := stagedContent(path); staged {
		return ok
	}
	return ospath.DiskExists(path)
}

// Lexists は在るか（リンクそのものも数える）。溜める段階は Exists と同じに見る。
func Lexists(path string) bool {
	if _, staged, ok := stagedContent(path); staged {
		return ok
	}
	return ospath.DiskLexists(path)
}

// Listdir はディレクトリの名前の一覧（順序は決めない）。溜める段階があれば、そこで足した名前を足し、
// 消した名前を除く。ディレクトリが無く、溜める段階も何も足していなければエラー。
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
	names, err := ospath.DiskListdir(directory)
	if err != nil {
		return nil, wrap(err)
	}
	return names, nil
}

// OpenNoFollow は読むために開き、開いたものの fstat を返す。リンクは開かず（NoFollow）、名前付きパイプでも
// 書き手を待たない（nonBlock）。lstat で確かめてから開くまでに差し替えられたものを、呼び手が返した FileInfo と
// os.SameFile で見分ける。開けなければ os の error。
func OpenNoFollow(path string) (*os.File, fs.FileInfo, error) {
	f, err := os.OpenFile(path, os.O_RDONLY|NoFollow|nonBlock, 0)
	if err != nil {
		return nil, nil, err
	}
	info, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, nil, err
	}
	return f, info, nil
}

// DiskDigest はディスクの中身の要約（16 進）。溜める段階は見ず、少しずつ読む（大きなビルドの出力でも中身を全部は
// 持たない）。消えているか読めなければ偽。
func DiskDigest(path string, a digest.Algo) (string, bool) {
	f, err := os.Open(path)
	if err != nil {
		return "", false
	}
	defer f.Close()
	sum, err := digest.HexOf(a, f)
	if err != nil {
		return "", false
	}
	return sum, true
}
