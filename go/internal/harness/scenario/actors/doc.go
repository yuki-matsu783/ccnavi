// Package actors は、受入シナリオの人間役の操作である。
//
//   - API（human_api）: 人間役のトークンで GitLab の API を打つ（スレッド・解決・承認）
//   - Terminal（human_run）: 人間役として端末で sh を打つ。擬似端末は `script` コマンドで包む
//     （Linux は `script -qec`、macOS は `script -q /dev/null`）。`script` の無い機械と Windows は ErrNoTerminal で、
//     呼び手が「手で打つ」で止まる経路に落とす。Playwright の ui 役者は移さずに消した（配線されていない足場だったため）
//
// ccnavi を起こす・端末を作る所は Terminal の欄で差し替えられる（このパッケージは testing に依存しない）。
// 実行器の状態は core.State。state.json を書くのは呼び手の save。
package actors
