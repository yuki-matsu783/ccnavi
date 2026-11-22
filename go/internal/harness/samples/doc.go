// Package samples は、CLI を通すテスト（internal/clitest）が共有する見本の組み立てである。//
// 置くもの
//   - ticket.go: ルール（Rules）・フェーズ定義（Phases・PlainPhases）・親と子の提案の本文（ParentText・ChildText・PlainParent）・
//     計画のステップ（Step・Plan）・WritePlainPhases
//   - phasecopy.go: 親に phases:（計画が使う定義のコピー）を差し込む（InsertPhases・Definitions・CombinedPhases）
//   - chrome.go: Chrome の入口（ccnavi-chrome）に要求を渡す（ChromeHandle・ChromePlacement。実行ファイルのパスは呼び手が exe.ChromePath で渡す）
//   - merge.go: RecordMerge（write-and-push の finish の代わりに merged_sha を書く）・SetFields（frontmatter の欄の書き換え）
//   - names.go・learning.go: 下書きの階層名の見本・学びのパターンと採否の記録の文面
//   - userform.go・oprecord.go・wapflag.go: user の入口の並び（Place）・操作記録（BeginRecord）・write-and-push の印（AsWriteAndPush）
//
// sh のスタブから起こされる口（teststub のサブコマンドから呼ぶ関数）
//   - UserformMain(args, stderr) int: `teststub userform <引数>…`。引数は `--entry` を含む先頭のラッパーのオプション。
//     標準出力は使わない。断る並びなら文を stderr に出して終了コード 2、通れば 0
//   - OprecordMain(args, stderr) int: `teststub oprecord <state> <計画の出力先> <JSON ファイル>`。JSON は
//     `{"begin": {kind, repo, parent, phase, mr, request_head, shown, choices, plan, stages: [[段階ID, 種類]…]}, "plan": {計画の欄}}`。
//     記録を作り、plan の欄に "op": <操作ID> を足した JSON（Python の json.dump の形）を <計画の出力先> に書く。成功 0・失敗は文を stderr に出して 1
//
// 見本は実行ファイルの関数を呼んで作らない（見本が実装の書き方に引きずられないように）。例外は操作記録で、
// tickets/model/oplog の型を直に使う（記録の形を別に書かず、同じ型を使うことで食い違いを防ぐ）。もう 1 つは userform で、
// 入口の判定に useronly.TakeEntry を呼ぶ。
package samples
