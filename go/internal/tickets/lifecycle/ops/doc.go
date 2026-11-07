// Package ops はチケットの状態を動かす操作（`ccnavi ticket start|finish|cancel|record-risk <識別子>` と、その
// `--check`）の本体。置き場を動かして欄を書くだけで、状態は置き場が表す。start は doing/ の欄を書き、finish は
// doing/ から review/（レビュー要）か done/（不要）へ、cancel は doing/ から done/ へ動かす。子を閉じるときは実績の
// リスクを数え、定性の判定が揃うまで閉じない。副作用の前に分かる検査は操作ごとに 1 つの関数（gate）に置き、本体と
// `--check` が同じものを通す。
//
// Python からの対応: tickets/lifecycle/commands を移した。start → Start、finish → Finish、cancel → Cancel（advise
// は引数）、check → Check（verb は Verb の定数）、record_risk → RecordRisk、ready_command → ReadyCommand、
// READY_FLOW_AGREED → ReadyFlowAgreed。start_gate・finish_gate・cancel_gate と _move・_score_child などは私的。
// 呼び手の無い公開名は移していない。
package ops
