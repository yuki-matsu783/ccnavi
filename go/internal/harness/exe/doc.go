// Package exe は、テストが起こす実行ファイル（ccnavi・ccnavi-chrome・teststub）を 1 か所で用意する。
//
// 環境変数（CCNAVI_TEST_EXE・CCNAVI_TEST_CHROME_EXE・CCNAVI_TEST_STUB_EXE）があればそれを使う（配る物そのものを測る）。
// 無ければ go/ を go build して、一時置き場の `ccnavi-test-exe/<ソースの指紋>/` に置く。指紋は go/ のソース
// （`_test.go` を除く .go と、埋め込む txt・json・jsonl、go.mod・go.sum）の中身と Go の版・OS・CPU から作るので、
// ソースが同じなら回し直しても組み直さない。同時に走るプロセスが同じ指紋を組んでも、出来た物は置き場の中で名前を差し替えて置くので、
// 組みかけのファイルを起こすことは無い。組んだ物を指紋で共有する。
//
// 組むのはプロセスごとに 1 度で、並行のテストから呼んでよい。
package exe
