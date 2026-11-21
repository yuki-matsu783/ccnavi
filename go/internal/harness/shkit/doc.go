// Package shkit は、sh を CLI で通すテスト（internal/clitest/sh/…）が共有する、ファイルと作業場所の道具である。
//
// 置くもの
//   - files.go: ファイルの読み書き・確かめ（Write・WriteExec・Read・Mkdir・Remove・Exists・IsDir・IsFile・Names・Slash・Need）と、
//     木の写し（CopyTree）
//   - scripts.go: 配る sh の写し（CopyScripts・CopyScriptsTo）、ccnavi の実行ファイルを起こすだけの sh（ExeWrapper）、
//     リポジトリの作者（Identity）
//
// 置かないもの: 作業木・リモートの組み立て。sh ごとに要る見本の中身（置く sh・リモート・ブランチ・追加のスタブ）が違い、
// 共通の組み立てにすると選択肢だらけになるので、各パッケージが自分の _test.go に持つ。
//
// 並びの決まり: 木を組む git は harness/run の run.Git を使う（ここには置かない）。
package shkit
