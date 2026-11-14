// Package start は、ccnavi-start.sh の段取りのうち、ホストの応答の読み取り・判定・ローカルの git の操作・出力の文を持つ。
// ネットワークには出ない（ホストの API・fetch・push・ls-remote は sh）。
//
// sh は応答を整えずにファイルへ書き、`ccnavi start <読み方> … <ファイル>` で渡す。答えは 1 行 1 項目で、
// sh は行の先頭の語で分ける。読み方ごとの答えの形は、それぞれの関数の説明にある。段取りの動詞の答えの形は outcome にある。
//
//   - issue: issue の 1 件（タイトル・状態・PR の番号か）
//   - linked-page・linked-body・linked-gitlab: MR が閉じる issue（GitHub の GraphQL の 1 ページ、MR の本文の Closes、
//     GitLab の closes_issues）
//   - similar: タイトルの似た open の issue・MR
//   - list-open: open の issue・MR の一覧
//   - pick: MR・issue の一覧や作った答えから、番号と URL を 1 つ選ぶ
//   - route・guidance: 候補から進む道と、ホストに届かないときの案内
//   - args: 引数の検査と、依頼のリポジトリ（--repo）と origin の照合
//   - branch-ok・merged・resolve・issue-state: 識別子と同じ識別子の別ブランチ、マージ済み MR、閉じる issue、closed の issue
//   - prepare: ワークツリーの作成・早送り・空の初期コミット（積めなければ作った分を片付ける）
//   - report・dry: 結果と --dry-run の出力
//
// 本文の Closes の式は後読みを使うので、pyre で読む。
package start
