// Package fakeclaude は `claude -p` のスタブである（tests/fake_claude.py と tests/contract/claudeside.py の写し）。
//
// 受入シナリオを台本（`--agent script`）で回すとき、実行器が `claude` の代わりに `teststub claude <引数>` を起こす。
// 台本どおりにツールを呼び、Claude Code と同じ形で hook を呼び、stream-json を出す。仕様は `.agents/docs/claude-contract.md`。
//
//   - Run: スタブの本体。`teststub claude` から呼ぶ。引数は `-p <prompt> --output-format stream-json [--verbose] [--include-hook-events]
//     [--permission-mode m] [--session-id id | --resume id] [--no-session-persistence]`。知らない引数は終了コード 2。
//     台本は環境変数 ScriptEnv が指す JSON（`{"script": [...], "after_stop_block": [...]}`）。セッションの記録は HomeEnv の下
//   - RecordHook: 契約の場面で使う hook（`teststub claude-hook <記録の置き場> <目印>`）。受けた JSON と環境を log.jsonl に足し、
//     tool_input の目印に合わせて止め方を変える
//   - scene.go: 本物とスタブで共有する契約の場面（使い捨ての木・打つ手・禁止する文字列）と、hook の入力と stream-json を
//     形（Node）と振る舞い（Observe）にまとめる Collect。本物の側（M2 の claudrecord）もこれを使う
//
// 形が本物と合っていることは clitest/contract/claude が、本物から録った tests/contract/claude/recorded.json と比べて確かめる。
// 形の表（Node・Normalize・Merge）は、契約の形の器（contractshape）が置かれたらそちらに寄せる。
//
// モデル・費用・Claude Code 自身の権限の仕組みは真似ない。hook が止めなければツールを実行する。同じイベントに当たった hook は、本物と同じく並行に起こす。
package fakeclaude
