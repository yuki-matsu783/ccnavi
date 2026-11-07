// Package turn は、セッションごとのターンの状態を state の置き場に 1 ファイルずつ置く。
// いまターンの途中か（turn-）、前のターンの終わりに動かしている作業が残っていたか（busy-）、
// ユーザが最後に発言した時刻（userprompt-）の 3 つ。どれも起動と再開で消し（Clear）、
// 残ったものは後始末（prune）がほかのセッションの記録と同じく消す。
//
// 書けない・読めないときの倒し方は、記録ごとに違う。
// ターンの印と発言の時刻は「無い」に倒す。拡張の警告が出ないだけで、ゲートが閉じる側に倒れるだけなので、保護は弱まらない。
// 動かしている作業の印は「残っている」に倒す。受け入れない側が安全なため。
//
// Python からの対応: records.turn・busy・userprompt をまとめた。turn.begin・touch・end・active → Begin・Touch・
// End・Active（Active の型は Ongoing）、busy.record・is_busy → RecordBusy・Busy、userprompt.note・last →
// NotePrompt・LastPrompt。起動と再開で一緒に呼ぶ 3 つの clear は Clear 1 つにした。
// STALE_SECONDS → config.TurnStale。
package turn
