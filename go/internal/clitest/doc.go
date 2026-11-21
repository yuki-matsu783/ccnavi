// Package clitest は、実行ファイル（ccnavi）と配る sh を起こして外から確かめるテストの置き場である。tests/ の Python のテストを
// 移したもので、置き場はグループごとのサブパッケージ（core・ticket/…・sh/…・guard/…・config/…）に分け、go test の -p で並行に回す。
// このパッケージ自身には本体が無い。道具は internal/harness（base・exe・run）にある。
//
// 書き方の見本（core/version_test.go・core/texts_test.go が実物）:
//
//	// パッケージごとに main_test.go を 1 本置く。
//	func TestMain(m *testing.M) { base.Main(m) }
//
//	func TestSomething(t *testing.T) {
//		t.Parallel()                                  // 基本は並行にする
//		root := base.TempDir(t)                       // 使い捨ての置き場。t.TempDir は使わない
//		// … root の下に木を組む（base.CommonPath・base.PutMessages など）
//		r := run.Ccnavi(t, []string{"--root", root, "text", "X"}, run.Opts{Env: run.CleanEnv(nil)})
//		if r.Code != 0 || !strings.HasPrefix(r.Stdout, "次:") { // 終了コード・標準出力・標準エラーを見る
//			t.Errorf("code = %d, stdout = %q, stderr = %q", r.Code, r.Stdout, r.Stderr)
//		}
//	}
//
// 守ること:
//   - 回すのは `go test -count=1 ./internal/clitest/...`。-count=1 が無いと Main が断る（理由は harness/base の doc）
//   - テストの中で t.Setenv・os.Chdir を使わない（t.Parallel と一緒に使えない）。環境は run.Opts.Env、作業ディレクトリは run.Opts.Dir で渡す
//   - 起こす子の環境は「渡したマップ＝見える環境」。走らせた人の CCNAVI_* に答えを変えさせたくないときは run.CleanEnv を渡す
//   - 同じ約束を入力だけ変えて何本も書かず、表駆動の 1 本にまとめる（t.Run で並行にする）。テスト名は約束を言う
//   - OS・機械の差で成り立たないものは、base.CanSymlink・base.CanCaseSensitive・base.CanPassArgument で飛ばす
//   - 文面や下書きは base.PutMessages・base.PutCCNaviMD・base.ScriptsDir で、できあがりの姿（wip/handoff/ の下書きがあれば下書き）を読む
//   - 1 つのグループだけが使う道具は、そのパッケージの _test.go に置く。共有が要る物は harness へ上げる
package clitest
