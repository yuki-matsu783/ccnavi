// Package judge は、受入シナリオの期待の評価と、証拠の読み取りである（tests/scenarios/expect.py・evidence.py の写し）。
//
//   - expect.go: 判定の記録（decisions.jsonl）・最終状態・expect_after を期待と比べ、総合（Overall）を決める。意味の判定の表も読む
//   - evidence.go: hook の応答から、渡った文脈・拒否の傾向・迂回の疑い・文面の一覧の元を取り出す
//
// 読むだけで、git・ネットワークには触れない（expect.state のコマンドだけ sh で起こす）。
package judge
