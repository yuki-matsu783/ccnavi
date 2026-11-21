// Package base は、CLI を通すテスト（internal/clitest）の土台である。tests/__init__.py と tests/draft.py の写しで、
// ほかの道具（exe・run など）と clitest が読む。
//
// 置くもの
//   - Main: TestMain の共通。走った機械の git の設定を締め出し、PATH から ccnavi を外し、MSYS の引数の書き換えを止める。
//     使い捨ての置き場の後片付けもする。clitest の各パッケージは必ずこれで始める
//   - Root・TempDir: リポジトリの根と、実パスの使い捨ての置き場
//   - Env: 起こす子に渡す環境（git の締め出しを含む）
//   - CommonPath・ConfigPath・FixtureWorkspace: 設定ファイルの置き場と、見本のルールを据えたワークスペース
//   - CanSymlink・CanCaseSensitive・CanPassArgument・LiveShPid: この機械でできるかの確かめ
//   - PutCCNaviMD・PutMessages・ScriptCommand・ShellPath: 文面と作業の決まりを置く、文面が案内するパスの書き方
//   - Final・ScriptsDir: 保護対象の下書き（wip/handoff/）があれば下書きを読む
//
// 使い方（TestMain）:
//
//	func TestMain(m *testing.M) { base.Main(m) }
//
// 環境の決まり: プロセスの環境は Main が 1 度だけ書く。テストの中では t.Setenv も os.Chdir も使わず（t.Parallel と一緒に
// 使えない）、子に渡す環境は exec.Cmd の Env に明示する（run.Opts.Env）。
//
// go test のキャッシュ: sh・文面・見本は子のプロセスが読み、リポジトリの根はモジュールの根（go/）の外なので、
// go test の「入力が変わっていないので前の結果を返す」には見えない。変えても「通った (cached)」が返りうる。
// Main は、キャッシュを使える起動（-test.testlogfile が付く）を見つけたら、実行せずに止めて -count=1 を求める。
// clitest は -count=1 で回す。
package base
