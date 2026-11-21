// Package base は、CLI を通すテスト（internal/clitest）の土台である。tests/__init__.py と tests/draft.py の写しで、
// ほかの道具（exe・run など）と clitest が読む。
//
// 置くもの
//   - Main: TestMain の共通。プロセスの環境から子の答えを変える変数（GIT_*・CLAUDE_*・CCNAVI_*・GITHUB_TOKEN・GITLAB_TOKEN）を外し、
//     走った機械の git の設定を締め出し、PATH から ccnavi を外し、MSYS の引数の書き換えを止め、go test に結果を保存させない
//     ようにする。使い捨ての置き場の後片付けもする。clitest の各パッケージは必ずこれで始める。呼び忘れは最初の 1 本で panic する
//   - TestVar: Main が外す前の CCNAVI_TEST_*（組まずに使う実行ファイルのパス）
//   - Root・TempDir: リポジトリの根と、実パスの使い捨ての置き場
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
// 使えない）、子に渡す環境は exec.Cmd の Env に明示する（run.Opts.Env は Main が整えた環境への差分、run.Opts.Unset は外す名前）。
// トークンがほしいテストは、GITHUB_TOKEN・GITLAB_TOKEN を run.Opts.Env で渡す。
// 一時置き場は TempDir（base.TempDir）。t.TempDir は使わない。
//
// go test のキャッシュ: sh・文面・見本は子のプロセスが読み、リポジトリの根はモジュールの根（go/）の外なので、
// go test の「入力が変わっていないので前の結果を返す」には見えず、変えても「通った (cached)」が返りうる。
// go test は、テストが開いたモジュールの中のファイルの更新時刻が 2 秒より新しいと結果を保存しない。
// Main が go/ 直下の目印（.clitest-uncached。.gitignore に載せてある）を未来の時刻で書き、Root と run の起動が
// テストの中で相対パスで開く（Uncache）。Root か run を使うパッケージは毎回走り、それ以外の単体テストのキャッシュは効く。
// -count=1 は要らない。Go を上げて効かなくなったときは TestGoDoesNotCacheATestThatOpensAFutureFile が落ちる。
package base
