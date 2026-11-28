// Package layers は、設定（共通・ワークスペース・プロジェクト）の置き場・同期・差し替えと、ルールの `every`・`match: Stop`・配布する設定の自己検査を、
// 実行ファイルを外から動かして確かめるテストを置く。書き方は親（clitest）の doc を見る。
// 統合結果の判定そのものは隣の union が見る。ワークスペースの雛形と見本は harness/configws にある。
package layers
