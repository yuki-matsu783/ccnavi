package fsio

import (
	"bytes"
	"io"
	"os"
)

// ScanTail は path の末尾を first バイトから grow 倍ずつ広げて読み、visit が true を返すか、ファイル全体を
// 読むか、limit を超えるところで止める。visit には、先頭の欠けた行（最初の "\n" まで）を除いた中身と、
// ファイル全体を読んだか（このときは何も除かない）を渡す。開けない・読めなければ error。
// 追記が続く大きな記録（会話の記録）の最後の行だけを探す用で、溜める段は見ない。
func ScanTail(path string, first, limit, grow int64, visit func(tail []byte, whole bool) bool) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	for reach := first; reach <= limit; reach *= grow {
		info, err := f.Stat()
		if err != nil {
			return err
		}
		size := info.Size()
		data := make([]byte, min(size, reach))
		if _, err := f.ReadAt(data, size-int64(len(data))); err != nil && err != io.EOF {
			return err
		}
		whole := size <= reach
		if !whole {
			if i := bytes.IndexByte(data, '\n'); i >= 0 {
				data = data[i+1:]
			} else {
				data = nil
			}
		}
		if visit(data, whole) || whole {
			return nil
		}
	}
	return nil
}
