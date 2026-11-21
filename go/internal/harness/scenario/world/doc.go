// Package world は、受入シナリオが触る外の世界である。
//
//   - workspace.go: ccnavi 本体（ワークスペース）の事実（HEAD・ブランチの記録、確認に出す git の状態、判定のモード）
//   - issues.go: setup.issue の issue を gh / glab で作って閉じる
//   - repo.go: 使い捨てのリポジトリ（projects/test-repo）の判定・事前状態・戻し・排他
//   - sandbox.go: スタブで回すシナリオの、使い捨てのワークスペースを組む
//
// 子のプロセス（git・gh・glab・sh）を起こす。testing に依存しない（入口の実行ファイルからも使う）。
//
// 簡素にした所: 対象のロックは、あれば止めて、使っている回が無いなら消すよう言う（PID の生死を調べて奪わない）。
package world
