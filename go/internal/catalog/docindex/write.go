package docindex

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
)

const (
	// IndexName は索引のファイルの名前。md が直下にあるディレクトリごとに 1 つ。
	IndexName = "ccnavi-index.jsonl"
	// tmpPrefix・tmpSuffix は git のディレクトリの中の一時ファイルの名前。
	tmpPrefix = "ccnavi-index-"
	tmpSuffix = ".tmp"
	// tmpStale を過ぎた一時ファイル（打ち切りの残骸）は次の回に消す。
	tmpStale = 10 * time.Minute
	// localTmpPrefix は、`.git` が別のファイルシステムにあるときに索引の隣に作る一時ディレクトリの名前。
	// 中の ccnavi-index.jsonl は `**/ccnavi-index.jsonl` で無視される。
	localTmpPrefix = ".ccnavi-tmp-"
	indexPerm      = 0o644
	holderPerm     = 0o700
)

var counter atomic.Int64

// rename は差し替えられる（テストが別のファイルシステムへの置き換えの失敗を作る）。
var rename = os.Rename

func uniqueName(prefix, suffix string) string {
	return prefix + strconv.Itoa(os.Getpid()) + "-" + strconv.FormatInt(counter.Add(1)-1, 10) + suffix
}

// writer は ccnavi-index.jsonl を置き換える。一時ファイルはふだん git のディレクトリに作る。置き換えが別のファイル
// システムをまたいで失敗したら、以後は索引の隣に作る。
type writer struct {
	base     string
	tmpDir   string
	deadline time.Time
	beside   bool
	// started は Build を始めた時刻。書いた索引の更新時刻にする（settle）。
	started time.Time
}

func (w *writer) write(indexPath, indexRel, text string) error {
	if !w.beside {
		err := writeAtomic(indexPath, text, ospath.Join(w.tmpDir, uniqueName(tmpPrefix, tmpSuffix)))
		if err == nil || !fsio.CrossDevice(err) {
			return err
		}
		w.beside = true
	}
	return w.writeBeside(indexPath, indexRel, text)
}

func (w *writer) writeBeside(indexPath, indexRel, text string) error {
	parent := ospath.Dirname(indexPath)
	sweepBeside(parent)
	name := uniqueName(localTmpPrefix, "")
	probe := JoinRel(dirOf(indexRel), name+"/"+IndexName)
	ignored, err := Ignored(w.base, []string{probe}, left(w.deadline))
	if err != nil {
		return fmt.Errorf("一時ファイルの置き場を git に確かめられない: %w", err)
	}
	if !ignored[probe] {
		return errors.New("一時ファイルの置き場 " + probe + " が git に無視されないので反映しません")
	}
	holder := ospath.Join(parent, name)
	if err := os.Mkdir(holder, holderPerm); err != nil {
		return err
	}
	defer os.Remove(holder)
	return writeAtomic(indexPath, text, ospath.Join(holder, IndexName))
}

// writeAtomic は temporary に排他で書き、path へ置き換える。残った一時ファイルは消す。
func writeAtomic(path, text, temporary string) error {
	f, err := os.OpenFile(temporary, os.O_WRONLY|os.O_CREATE|os.O_EXCL|fsio.NoFollow, indexPerm)
	if err != nil {
		return err
	}
	defer func() {
		if ospath.DiskLexists(temporary) {
			os.Remove(temporary)
		}
	}()
	_, err = f.WriteString(text)
	if closeErr := f.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	return rename(temporary, path)
}

// sweep は git のディレクトリに残った古い一時ファイルを消す。
func sweep(tmpDir string) {
	names, err := ospath.DiskListdir(tmpDir)
	if err != nil {
		return
	}
	for _, name := range names {
		if !strings.HasPrefix(name, tmpPrefix) || !strings.HasSuffix(name, tmpSuffix) {
			continue
		}
		path := ospath.Join(tmpDir, name)
		if info, err := ospath.Lstat(path); err == nil && info.Mode().IsRegular() && time.Since(info.ModTime()) > tmpStale {
			os.Remove(path)
		}
	}
}

// sweepBeside は、索引の隣に残った古い一時ディレクトリを消す。消すのは、リンクでない古いディレクトリのうち、
// 中身がふつうのファイルの ccnavi-index.jsonl だけ（か空）のものに限る。ほかは他人のものとして残す。
func sweepBeside(parent string) {
	names, err := ospath.DiskListdir(parent)
	if err != nil {
		return
	}
	for _, name := range names {
		if !strings.HasPrefix(name, localTmpPrefix) {
			continue
		}
		holder := ospath.Join(parent, name)
		info, err := ospath.Lstat(holder)
		if err != nil || !info.IsDir() || time.Since(info.ModTime()) <= tmpStale {
			continue
		}
		inside, err := ospath.DiskListdir(holder)
		if err != nil || len(inside) > 1 || (len(inside) == 1 && inside[0] != IndexName) {
			continue
		}
		if len(inside) == 1 {
			left := ospath.Join(holder, IndexName)
			if !ospath.IsRegular(left) || os.Remove(left) != nil {
				continue
			}
		}
		os.Remove(holder)
	}
}
