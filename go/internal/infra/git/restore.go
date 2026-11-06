package git

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
)

// 元に戻す git の引数。
var (
	restoreArgs  = []string{"restore", "--staged", "--worktree", "--"}
	unstageArgs  = []string{"restore", "--staged", "--"}
	rmCachedArgs = []string{"rm", "--cached", "--"}
)

// Undo はこの 1 件を元に戻すコマンドの文（ユーザとエージェントが読む）。1 件に 1 つだけ返す。
//
// wrapper はラッパーの打ち方。空なら返さない（ラッパーの無いワークスペースでは生の git も止まるので、
// 呼び手がユーザに頼む文にする）。ラッパーは rm -f と clean を通さないので、現れたファイルは
// restore --staged で索引から外してシェルの rm で消す。コミット済みには 1 つに決まる手順が無いので
// 空を返す（間違った手順を書くより書かないほうがよい）。
func Undo(c Change, wrapper string) string {
	if c.Kind == KindCommitted || wrapper == "" {
		return ""
	}
	quoted := `"` + c.Path + `"`
	if c.Kind != KindNew {
		return wrapper + " " + strings.Join(restoreArgs, " ") + " " + quoted
	}
	if c.Staged {
		return wrapper + " " + strings.Join(unstageArgs, " ") + " " + quoted + " && rm -- " + quoted
	}
	return "rm -- " + quoted
}

// Restore はこの 1 件を実際に元に戻す。戻せたら空文字、駄目なら理由。
//
// 現れたファイルは消さずに aside へ退避する。消すと「保護領域を汚した実行」と「間違って出力しただけの
// 要るもの」を見分けられないまま片方を選ぶことになる。退避先は報告に載せるので、要るものなら拾い直せる。
func Restore(top string, c Change, aside string) string {
	if c.Kind != KindNew {
		return gitText(top, append(append([]string(nil), restoreArgs...), c.Path))
	}
	if c.Staged {
		// 索引から外してから動かす。先に動かすと索引に消えたファイルへの追加が残り、
		// 次の status が「消えた」を新しい変更として持ち出す。
		if failed := gitText(top, append(append([]string(nil), rmCachedArgs...), c.Path)); failed != "" {
			return failed
		}
	}
	source := ospath.Join(top, c.Path)
	target := ospath.Join(aside, filepath.FromSlash(c.Path))
	if err := fsio.MakeDirs(ospath.Dirname(target)); err != nil {
		return fsio.ErrorText(err)
	}
	if err := moveAside(source, target); err != nil {
		return fsio.ErrorText(err)
	}
	return ""
}

// RestoreCommitted は名指しした 1 つのパスをコミット済みの内容へ戻す。戻せたら空文字、駄目なら理由。
// バックアップが無く git に頼るしかないものを戻す（Change が無い）。git が追っていないパスなら失敗を返し、
// 呼び手はそれを報告に載せる。
func RestoreCommitted(top, path string) string {
	return gitText(top, append(append([]string(nil), restoreArgs...), path))
}

// gitText は index を書き換える git を起こし、失敗の理由を 1 行の文にする。
func gitText(top string, args []string) string {
	done := runGit(top, args, Options{Timeout: writeTimeout})
	if done.Failure != nil {
		return done.Failure.Error()
	}
	if done.Code != 0 {
		if text := strings.Join(strings.Fields(done.Stderr), " "); text != "" {
			return text
		}
		return fmt.Sprintf("git %s が失敗した", args[0])
	}
	return ""
}

// moveAside は shutil.move。rename できなければ（ファイルシステムをまたぐなど）写してから消す。
// 行き先がディレクトリなら、その中へ同じ名前で動かす。
func moveAside(source, target string) error {
	if ospath.IsDir(target) {
		target = ospath.Join(target, ospath.Basename(strings.TrimRight(source, ospath.Sep)))
		if ospath.DiskExists(target) {
			return fmt.Errorf("Destination path '%s' already exists", target)
		}
	}
	if err := os.Rename(source, target); err == nil {
		return nil
	}
	if err := copyTree(source, target); err != nil {
		return err
	}
	return os.RemoveAll(source)
}

// copyTree はリンクはリンクのまま、ディレクトリは中身ごと、ファイルは中身と権限と更新時刻を写す。
func copyTree(source, target string) error {
	info, err := os.Lstat(source)
	if err != nil {
		return err
	}
	switch {
	case info.Mode()&os.ModeSymlink != 0:
		link, err := os.Readlink(source)
		if err != nil {
			return err
		}
		return os.Symlink(link, target)
	case info.IsDir():
		if err := os.MkdirAll(target, info.Mode().Perm()); err != nil {
			return err
		}
		entries, err := os.ReadDir(source)
		if err != nil {
			return err
		}
		for _, e := range entries {
			if err := copyTree(ospath.Join(source, e.Name()), ospath.Join(target, e.Name())); err != nil {
				return err
			}
		}
		return nil
	}
	in, err := os.Open(source)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, info.Mode().Perm())
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
	return os.Chtimes(target, info.ModTime(), info.ModTime())
}
