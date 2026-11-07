// Package oplog は操作記録を持つ。decide・close-early・review-request・comment・ready は、ホストへの投稿とローカルへの
// 適用が複数の段階に分かれる。途中で止まりうるので、ユーザの判断と送る予定を実行する前に、`<state>/ops/<操作ID>.json`
// に保存する。打ち直しと `--resume` は、同じ記録から続きを読む。
//
// 記録の JSON の形（キーの順序・indent=1・ensure_ascii=False）は、sh（`ccnavi ops show` を jq で読む）との契約である。
// 送る本文は持たない。shown・choices から、送るたびに作り直す。こうすると改ざんしにくくなる。ネットワークにも git にも
// 触れない。ロック（lock）は読み書きの間だけの短いもので、同じ PC の並行する更新だけを防ぐ。
//
// Python からの対応: OpError → *Error、begin → Begin（引数は Options）、load → Load（無ければ nil, nil）、
// stage_set → StageSet、applied_set → AppliedSet、reconcile → Reconcile（complete の True・False・None →
// Listing）、id_for → IDFor、mark_sent → MarkSent、retire_parent → RetireParent、discard_untouched →
// DiscardUntouched、pending → Pending（読めない記録は Broken に理由）、complete_if_done → CompleteIfDone、
// summary → Summary、unfinished・finished → Record.Unfinished・Record.finished、OPS_DIR → dirName、
// KIND_*・STAGE_*・PENDING・SENT・UNKNOWN・V_* → Kind・StageKind・State・Verdict の定数。
package oplog
