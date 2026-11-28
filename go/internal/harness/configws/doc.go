// Package configws は、設定 3 本（rules・phases・risks）の和と、その周りの同期を、実行ファイルを外から動かして確かめるテストの土台である。
// ワークスペース 1 つとプロジェクト 2 つ（lib と app）を組んだ雛形を、種類ごとにプロセスで 1 度だけ組み、テストごとに写す
// （git を 9 回起こすのを 1 度に減らす）。lib は設定 3 本を持ち、app は `.ccnavi/` を持たない（無い設定 = 空）。
// 見本の設定は fixtures.go にある。clitest/config の下のパッケージが使う。
package configws
