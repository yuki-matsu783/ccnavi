// Package manual は、手で使う GitLab の道具（`harness/cmd/gitlabmanual`・`harness/cmd/fakegitlab`）を、組み立てた実行ファイルで通す。
//
// 本物の GitLab（Docker）は立てない（.agents/docs/gitlab-manual.md）。見るのは、本物を立てずに回る分だけである。
//   - gitlabmanual: 引数の振り分け・`probe --help` がトークンも実行ファイルも要らず終わること・トークンが無いときは何も作らず止まること
//   - fakegitlab: 起動（待ち受けを標準エラー 1 行で知らせる）・1 往復（受けた要求を 1 行ずつ出す）・停止
//
// Docker の操作・probe の組み立て・契約の録りの中身は、各パッケージ（harness/gitlabdocker・gitlabprobe・contractrecord）の単体テストが見る。
package manual
