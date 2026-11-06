// Package via は、いまの起動を誰がどの経路で動かしたかを 1 回の起動の間だけ覚える。マーカーの
// `via`・`actor` に書く値の持ち主。状態の履歴は持たない（置き場とマーカーが状態のすべて）。
//
// Python からの対応: VIA_* → Route の定数、session → Session（返した関数で前の値へ戻す）、set_via → SetRoute、
// set_actor → SetActor、via() → Current、actor() → Actor。
package via
