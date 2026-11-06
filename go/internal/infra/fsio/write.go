package fsio

import (
	"io"
	"os"
	"time"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
)

// How は書き方。
type How int

const (
	// Plain は開いた時点で中身を捨てて書く（open(path, "w")）。書いている間に読まれると空に見え、
	// 途中で異常終了すると空か書きかけが残る。
	Plain How = iota
	// Atomic は同じディレクトリの一時ファイル（`.<名前の拡張子の前>.<一意>.part<拡張子>`）に書き切って
	// 差し替える。読む側は前か後の中身しか見ない。電源断は守らない（fsync しない）。
	// 取り合いになる記録と、書きかけを次の起動に拾わせたくない記録に使う。
	Atomic
	// Durable は Atomic に加えて、差し替えの前に一時ファイルを、後に親ディレクトリを fsync する。
	// 一時ファイルは `.<名前>.<一意>.part`（元の拡張子で終わらせず、承認済みの読み手に拾わせない）。
	// 書き込み権の無い行き先は上書きしない。取り直せない承認済みチケットに使う。
	Durable
	// New はまだ無いファイルとしてだけ書く（O_EXCL）。在れば書かない。途中で異常終了すると書きかけが残る。
	New
	// NewDurable は在るかを確かめてから Durable で書く。確かめてから置くまでの間に置かれたものは上書きする。
	NewDurable
)

// 一時的に開けない・書けないときの打ち直し（読みと書きで同じ）。並列に走る hook が同じ記録を
// 置き換え・削除している最中に開くと、Windows は ERROR_DELETE_PENDING や共有違反で返す
// （Python の errno で EINVAL と EACCES）。待てば通るので数回だけ打ち直す。EPERM は本物の
// 権限エラーでも出て待っても直らないので入れない（transient）。
const (
	retries   = 3
	retryWait = 20 * time.Millisecond
)

// sleep は打ち直しの間の待ち（テストが差し替える）。
var sleep = time.Sleep

// newFileMode は素の open が作るファイルの権限（umask が引かれる）。
const newFileMode = 0o666

// dirMode は親ディレクトリを作るときの権限（umask が引かれる）。
const dirMode = 0o777

func transient(err error) bool {
	n := pyErrno(err)
	return n == errnoEINVAL || n == errnoEACCES
}

// withRetry は書き込みを、一時的な失敗なら数回まで打ち直す。書けたら空文字、駄目なら理由。
func withRetry(write func() error) string {
	for attempt := 0; ; attempt++ {
		err := write()
		if err == nil {
			return ""
		}
		if !transient(err) || attempt == retries {
			return ErrorText(err)
		}
		sleep(retryWait)
	}
}

// WriteFile は中身をそのまま how で書く。親ディレクトリが無ければ作る（New は作らずに失敗しない）。
// 書けたら空文字、駄目なら理由（Python の str(OSError) と同じ文）。
func WriteFile(path string, content []byte, how How) string {
	if current != nil {
		if (how == New || how == NewDurable) && Lexists(path) {
			return ErrorText(pathError("open", path, errnoEEXIST))
		}
		return current.put(Op{kind: opWrite, how: how, Path: path, Content: content}, false)
	}
	return record(path, writeNow(path, content, how))
}

// WriteText は本文を how で書く。改行は nl で書き換える（open(path, "w", newline=nl) と同じバイト列）。
func WriteText(path, text string, nl Newline, how How) string {
	if current != nil {
		return current.put(Op{kind: opWrite, how: how, Path: path, Content: nl.staged(text), text: text, newline: nl, isText: true}, false)
	}
	return record(path, writeNow(path, nl.onDisk(text), how))
}

// WriteJSON は JSON（json.dumps(data, ensure_ascii=False)）を Atomic で書く。書けたら空文字、駄目なら理由。
func WriteJSON(path string, data any) string {
	text, err := pyjson.Dumps(data, pyjson.Options{RawUnicode: true})
	if err != nil {
		return err.Error()
	}
	return WriteText(path, text, NativeNewline, Atomic)
}

func writeNow(path string, content []byte, how How) string {
	switch how {
	case Atomic:
		return withRetry(func() error { return replaceFile(path, content, false) })
	case Durable:
		return withRetry(func() error { return replaceFile(path, content, true) })
	case NewDurable:
		return withRetry(func() error {
			if ospath.DiskLexists(path) {
				return pathError("open", path, errnoEEXIST)
			}
			return replaceFile(path, content, true)
		})
	case New:
		err := createExclusive(path, content)
		if err != nil {
			return ErrorText(err)
		}
		return ""
	}
	return withRetry(func() error {
		if err := MakeDirs(dirOrDot(path)); err != nil {
			return err
		}
		return writeFile(path, content, os.O_WRONLY|os.O_CREATE|os.O_TRUNC)
	})
}

func createExclusive(path string, content []byte) error {
	if err := MakeDirs(ospath.Dirname(path)); err != nil {
		return err
	}
	return writeFile(path, content, os.O_WRONLY|os.O_CREATE|os.O_EXCL)
}

// openFile はファイルを開く（テストが失敗を差し込む）。
var openFile = os.OpenFile

func writeFile(path string, content []byte, flag int) error {
	f, err := openFile(path, flag, newFileMode)
	if err != nil {
		return err
	}
	_, err = f.Write(content)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	return err
}

func dirOrDot(path string) string {
	if dir := ospath.Dirname(path); dir != "" {
		return dir
	}
	return "."
}

// MakeDirs は os.makedirs(name, exist_ok=True)。失敗の文を Python と同じにするため、
// os.MkdirAll ではなく同じ手順で作る（在るのがファイルなら、その名前で File exists になる）。
func MakeDirs(name string) error {
	head, tail := ospath.Dirname(name), ospath.Basename(name)
	if tail == "" {
		head, tail = ospath.Dirname(head), ospath.Basename(head)
	}
	if head != "" && tail != "" && !ospath.DiskExists(head) {
		// 頭が在った（作り合いに負けた）ことだけは飲み、次の mkdir で決める。
		if err := MakeDirs(head); err != nil && pyErrno(err) != errnoEEXIST {
			return err
		}
		if tail == "." {
			return nil
		}
	}
	if err := os.Mkdir(name, dirMode); err != nil {
		if !ospath.IsDir(name) {
			return err
		}
	}
	return nil
}

// ReplaceFile は名前の決まった一時ファイル（path+tempSuffix）に書いてから置き換える。駄目なら理由。
// 異常終了して残った一時ファイルを、呼び手が名前で見分けるため（configsync の `*.ccnavi-sync`）。
func ReplaceFile(path string, content []byte, tempSuffix string) string {
	if current != nil {
		return current.put(Op{kind: opReplace, Path: path, Content: content, tempSuffix: tempSuffix}, false)
	}
	temp := path + tempSuffix
	err := MakeDirs(ospath.Dirname(path))
	if err == nil {
		err = writeFile(temp, content, os.O_WRONLY|os.O_CREATE|os.O_TRUNC)
	}
	if err == nil {
		err = replace(temp, path)
	}
	if err != nil {
		_ = os.Remove(temp)
		return ErrorText(err)
	}
	return record(path, "")
}

// Remove は消す。無くても、消せなくても何も言わない。
func Remove(path string) {
	if current != nil {
		if Lexists(path) {
			current.put(Op{kind: opRemove, Path: path, Gone: true}, true)
		}
		return
	}
	if unlinkFile(path) == nil {
		record(path, "")
	}
}

// Unlink は消す。消せたら空文字、無い・消せないなら理由（Remove と違って理由を返す）。
// リンクは辿らずにリンクそのものを消す。ディレクトリは消さない。
func Unlink(path string) string {
	if current != nil {
		if !Lexists(path) {
			return ErrorText(notFound(path))
		}
		return current.put(Op{kind: opUnlink, Path: path, Gone: true}, false)
	}
	if err := unlinkFile(path); err != nil {
		return ErrorText(err)
	}
	return record(path, "")
}

// Prior はある時点のファイルの中身。Gone なら無かった。
type Prior struct {
	Path    string
	Content []byte
	Gone    bool
}

// PutBack はパスごとに前の中身へ戻す（Gone なら消す）。戻せなくても何も言わない。
// 戻すのは承認で書いたものなので、承認済みチケットと同じ Durable で戻す。
func PutBack(priors []Prior) {
	for _, p := range priors {
		if p.Gone {
			Remove(p.Path)
		} else {
			WriteFile(p.Path, p.Content, Durable)
		}
	}
}

// Move はファイルを動かす。動かせたら空文字、駄目なら理由。行き先の有無は呼び手が見る。
//
// 同じファイルシステムの中なら rename 1 回で済む。またぐとき（EXDEV）だけコピーして消し、消せなければ
// コピーした側を消す（両方に残さない）。ほかの理由で rename が失敗したときまでコピーに回すと、
// その間に別のプロセスが置いた行き先を消すことになる。
func Move(source, target string) string {
	if current != nil {
		content, staged, ok := stagedContent(source)
		if !staged {
			var err error
			content, err = readFile(source)
			ok = err == nil
		}
		if !ok {
			return ErrorText(notFound(source))
		}
		return current.put(Op{kind: opMove, Path: target, Content: content, Source: source}, false)
	}
	err := MakeDirs(ospath.Dirname(target))
	if err == nil {
		err = pyRename(source, target)
	}
	if err == nil {
		record(source, "")
		return record(target, "")
	}
	if pyErrno(err) != errnoEXDEV {
		return ErrorText(err)
	}
	if err := CopyFile(source, target); err != nil {
		Remove(target)
		return ErrorText(err)
	}
	if err := unlinkFile(source); err != nil {
		Remove(target)
		return ErrorText(err)
	}
	record(source, "")
	return record(target, "")
}

// CopyFile は shutil.copy2（中身・権限・更新時刻を写す。リンクは辿る）。親のディレクトリは作らない。
// 溜める段は通さない（実行ファイルのバックアップと退避に使う）。
func CopyFile(source, target string) error {
	in, err := os.Open(source)
	if err != nil {
		return err
	}
	defer in.Close()
	info, err := in.Stat()
	if err != nil {
		return err
	}
	out, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, newFileMode)
	if err != nil {
		return err
	}
	_, err = io.Copy(out, in)
	if cerr := out.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return err
	}
	if err := os.Chmod(target, info.Mode().Perm()); err != nil {
		return err
	}
	return os.Chtimes(target, info.ModTime(), info.ModTime())
}

// AppendLine は line に機械の改行を付けて、追記モードの 1 回の書き込みで足す（Python の open(path, "a") の
// write(line + "\n") と、Windows でテキストモードになる os.open の O_APPEND）。並行するプロセスの行と混ざらない。
// perm は新しく作るファイルの権限。親のディレクトリは作らない。溜める段は通さない（記録のログ用）。
func AppendLine(path, line string, perm os.FileMode) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_APPEND, perm)
	if err != nil {
		return err
	}
	_, err = f.WriteString(line + lineSep)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	return err
}
