// Package start は、ccnavi-start.sh が取ってきたホストの応答を読む。読むだけで、何も書かず、ネットワークにも出ない。
//
// sh は応答を整えずにファイルへ書き、`ccnavi start <読み方> … --result <ファイル>` で渡す。答えは 1 行 1 項目で、
// sh は行の先頭の語で分ける。読み方ごとの答えの形は、それぞれの関数の説明にある。
//
//   - issue: issue の 1 件（タイトル・状態・PR の番号か）
//   - linked-page・linked-body・linked-gitlab: MR が閉じる issue（GitHub の GraphQL の 1 ページ、MR の本文の Closes、
//     GitLab の closes_issues）
//   - similar: タイトルの似た open の issue・MR
//   - list-open: open の issue・MR の一覧
//   - pick: MR・issue の一覧や作った答えから、番号と URL を 1 つ選ぶ
//
// 本文の Closes の式は後読みを使うので、pyre で読む。
package start
