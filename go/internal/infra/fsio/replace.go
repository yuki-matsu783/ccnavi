package fsio

import (
	"crypto/rand"
	"io/fs"
	"os"

	"ccnavi/internal/infra/ospath"
)

// 一時ファイルの名前の印。先頭の "." と ".part" は、異常終了して残ったものを承認済みの置き場から
// 運ぶ側（ccnavi-user-push-approved.sh と write-and-push）が除く印。IsTempName が同じ形を見る。
const (
	tempPrefix = "."
	tempMark   = ".part"
)

// 一意な部分（Python の tempfile と同じ字と長さ）。"." を含めると IsTempName が元の名前を取り違える。
const (
	tempChars    = "abcdefghijklmnopqrstuvwxyz0123456789_"
	tempLength   = 8
	tempTries    = 10000
	tempFileMode = 0o600
)

// テストが失敗を差し込む差し替え点。
var (
	chmod    = os.Chmod
	syncFile = func(f *os.File) error { return f.Sync() }
	syncDir  = syncDirectory
)

// replaceFile は同じディレクトリの一時ファイルに書き切って差し替える（Atomic と Durable の本体）。
//
// 差し替えが一瞬で終わるのは同じファイルシステムの中だけなので、一時ファイルは行き先の隣に作る。
// 一時ファイルの名前は毎回変える。固定にすると、同時に書く 2 つが同じ一時ファイルを取り合う。
// 差し替えまで行けなかった一時ファイルは消す（消せなくても名前が掃除の条件にヒットする）。
func replaceFile(path string, content []byte, durable bool) error {
	directory := dirOrDot(path)
	name := ospath.Basename(path)
	prefix, suffix := tempPrefix+name+".", tempMark
	if durable {
		// 差し替えはディレクトリの書き込み権だけで通るので、読み取り専用にしたファイルも上書きできてしまう。
		// ユーザが chmod で止めた書き換えを、差し替えで素通りさせない。
		if ospath.DiskLexists(path) && !writable(path) {
			return pathError("open", path, errnoEACCES)
		}
	} else {
		stem, ext := ospath.Splitext(name)
		prefix, suffix = tempPrefix+stem+".", tempMark+ext
	}
	if err := MakeDirs(directory); err != nil {
		return err
	}
	f, part, err := createTemp(directory, prefix, suffix)
	if err != nil {
		return err
	}
	err = fill(f, part, path, content, durable)
	if err == nil {
		err = replace(part, path)
	}
	if err != nil {
		_ = os.Remove(part)
		return err
	}
	if durable {
		syncDir(directory)
	}
	return nil
}

func fill(f *os.File, part, path string, content []byte, durable bool) error {
	// 一時ファイルは 0600 で作られ、差し替えると行き先の権限がそれに狭まる。素の open と同じ見え方に戻す。
	// Durable では最善努力（chmod を受け付けないファイルシステムでも、素の open なら書けたので）。
	if err := chmod(part, modeFor(path)); err != nil && !durable {
		f.Close()
		return err
	}
	_, err := f.Write(content)
	if err == nil && durable {
		err = syncFile(f)
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	return err
}

// modeFor は差し替えた後に残す権限。在ればその権限、無ければ素の open が作るのと同じ 0666 から umask を引いたもの。
func modeFor(path string) fs.FileMode {
	if info, err := os.Stat(path); err == nil {
		return info.Mode().Perm()
	}
	return fs.FileMode(newFileMode &^ umask())
}

// createTemp は tempfile.mkstemp(dir=directory, prefix=prefix, suffix=suffix)。
func createTemp(directory, prefix, suffix string) (*os.File, string, error) {
	var err error
	for range tempTries {
		part := ospath.Join(directory, prefix+randomName()+suffix)
		var f *os.File
		f, err = os.OpenFile(part, os.O_RDWR|os.O_CREATE|os.O_EXCL, tempFileMode)
		if err == nil {
			return f, part, nil
		}
		if pyErrno(err) != errnoEEXIST {
			return nil, "", err
		}
	}
	return nil, "", err
}

func randomName() string {
	buf := make([]byte, tempLength)
	_, _ = rand.Read(buf)
	for i, b := range buf {
		buf[i] = tempChars[int(b)%len(tempChars)]
	}
	return string(buf)
}

// syncDirectory は差し替えた名前をディスクまで届ける。開けない（Windows）・できないファイルシステムでは
// 何もしない。差し替えはもう済んでいるので、ここで失敗したことにはしない。
func syncDirectory(directory string) {
	d, err := os.Open(directory)
	if err != nil {
		return
	}
	_ = d.Sync()
	d.Close()
}
