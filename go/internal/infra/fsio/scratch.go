package fsio

import "os"

// ScratchDir は OS の一時置き場に、名前が prefix で始まる空のディレクトリを作る。返す関数は中身ごと消す
// （呼び手が defer で呼ぶ）。消せなかったときは言わない（OS の一時置き場なので、残っても害が無い）。
func ScratchDir(prefix string) (dir string, remove func(), err error) {
	dir, err = os.MkdirTemp("", prefix)
	if err != nil {
		return "", nil, err
	}
	return dir, func() { _ = os.RemoveAll(dir) }, nil
}
