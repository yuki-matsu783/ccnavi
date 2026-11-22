package base

import (
	"os"
	"path/filepath"
	"time"
)

// uncacheMark は go/ 直下の目印のファイル（.gitignore に載せてある）。名前は拡張子を持たず、exe の指紋に入らない。
const uncacheMark = ".clitest-uncached"

// go test は、テストが開いたモジュールの中の通常ファイルの更新時刻が 2 秒より新しいと、その結果を保存しない
// （cmd/go の hashOpen の errFileTooNew）。Main が目印の更新時刻を未来にして書き、Root と run の起動が
// テストの中で開くので、sh・文面・見本（モジュールの外）を読むパッケージの結果は毎回走る。
// 開く物は相対パスで渡す。go コマンドが自分のパッケージの場所と継ぐので、Windows でパスの字の大小が食い違っても
// 「モジュールの中」と判定される。本体の単体テストのキャッシュには触れない。

// relMark は作業ディレクトリから見た目印の相対パス。writeUncacheMark（Main の setup）だけが決める。
var relMark string

// markFor は dir の下の目印を、未来の更新時刻で書く。
func markFor(dir string) error {
	path := filepath.Join(dir, uncacheMark)
	if err := os.WriteFile(path, nil, 0o644); err != nil {
		return err
	}
	future := time.Now().Add(24 * time.Hour)
	return os.Chtimes(path, future, future)
}

func writeUncacheMark() error {
	dir, rel, err := findRoot()
	if err != nil {
		return err
	}
	if err := markFor(filepath.Join(dir, "go")); err != nil {
		return err
	}
	relMark = filepath.Join(rel, "go", uncacheMark)
	return nil
}

// Uncache は目印を相対パスで開いて閉じる。テストの中で呼ばれると、go test はそのパッケージの結果を保存しない。
func Uncache() {
	requireMain()
	if f, err := os.Open(relMark); err == nil {
		_ = f.Close()
	}
}
