// Package core は、受入シナリオの実行器の芯である（tests/scenarios/common.py・schema.py・stream.py の写し）。
// 決まりは .agents/docs/acceptance-scenarios.md。
//
//   - Scenario・Step・Setup・Expect: シナリオの yml を読んだ型（Load・LoadFile）。形の検査は読むときに全部行う
//   - State: 回の状態（state.json）。準備・相手・人間役の操作が触る欄も、ここにまとめて持つ
//   - Refusal: 回す前の拒否・停止（理由・案内:・次:）。行頭の語は .agents/docs/script-output.md
//   - Result: 期待ごとの結果（種類・期待・判定・理由）と判定の定数
//   - ParseStream: `claude -p` の出力（stream-json）から証拠に使うものを抜き出す
//
// 外を呼ばない（git・sh・ネットワークに触れない）。上の層（prepare・actors・judge・world・report・runner）が読む。
package core
