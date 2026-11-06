// Package oplog は操作記録を持つ。decide・close-early・review-request・comment・ready はホストへの投稿と
// ローカルへの適用が複数の段階に分かれて途中で止まりうるので、ユーザの判断と送る予定を実行する前に
// `<state>/ops/<操作ID>.json` に保存し、打ち直しと `--resume` が同じ記録から続きを読む。
//
// 記録の JSON の形（キーの順序・indent=1・ensure_ascii=False）は sh（`ccnavi ops show` を jq で読む）との契約。
// 送る本文は持たない（shown・choices から送るたびに作り直す。改ざんしにくくするため）。ネットワークにも
// git にも触れない。ロック（lock）は読み書きの間だけの短いもので、同じ PC の並行する更新だけを防ぐ。
//
// Python からの対応: OpError → *Error、begin → Begin（引数は Options）、load → Load（無ければ nil, nil）、
// stage_set → StageSet、applied_set → AppliedSet、reconcile → Reconcile（complete の True・False・None →
// Listing）、id_for → IDFor、mark_sent → MarkSent、retire_parent → RetireParent、discard_untouched →
// DiscardUntouched、pending → Pending（読めない記録は Broken に理由）、complete_if_done → CompleteIfDone、
// summary → Summary、unfinished・finished → Record.Unfinished・Record.Finished、OPS_DIR → DirName、
// KIND_*・STAGE_*・PENDING・SENT・UNKNOWN・V_* → Kind・StageKind・State・Verdict の定数。
package oplog
