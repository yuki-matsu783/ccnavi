// Package oplog は操作記録を読み書きする。decide・close-early・review-request・comment・ready は、ホストへの投稿とローカルへの
// 適用が複数の段階に分かれる。途中で止まりうるので、ユーザの判断と送る予定を実行する前に、`<state>/ops/<操作ID>.json`
// に保存する。打ち直しと `--resume` は、同じ記録から続きを読む。
//
// 記録の JSON の形（キーの順序・indent=1・ensure_ascii=False）は、`ccnavi ops show` を読む側（ボードと、ccnavi-user-decide.sh の
// 再開の表示）との契約である。sh が段階と applied を読むときは、1 行 1 項目の `ccnavi ops get`（Record.Lines）を使う。
// 送る本文は記録に入れない。shown・choices から、送るたびに作り直す。こうすると改ざんしにくくなる。ネットワークにも git にも
// 触れない。ロック（lock）は読み書きの間だけの短いもので、同じ PC の並行する更新だけを防ぐ。
package oplog
