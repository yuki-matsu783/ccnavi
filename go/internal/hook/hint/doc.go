// Package hint は PostToolUse のあとに足す、判定を変えない案内を組む。直接作業のコミットのあとのレビュー手順の
// 次の一手（CommitNotice）と、親の提案を手書きしたときの書式の誤り（ProposalHint）の 2 つ。どちらも出さないときは空で、
// 読めない・期限が足りないときも空にする（案内の足しなので、実行後チェックを止めない）。
//
// コミット後の案内は、メインのエージェントの `ccnavi-git.sh commit`（打った場所が対象）か、メインの Agent ツールが返ったとき
// （全ワークツリーのうち直前にコミットが積まれたもの）に、直接作業のワークツリー（integrate.DirectCheck）で実際にコミットが
// 積まれていれば出す。セッションの頭にも Stop の促しにも置かない。連続する作業は、前の作業の完了のときに次だけを案内する。
// Stop の促しはターンを伸ばすため。ワークツリーの名前ごとにセッションで 1 回だけ出し、覚え方はルールの additionalContextOnce と
// 同じ（ctxfile）。
//
// Python からの対応: hook/commitnote.notice → CommitNotice、proposalhint.hint → ProposalHint。CODE_PARENT_*・
// ONCE_ID・TEXT_ID は私的な定数にした。
package hint
