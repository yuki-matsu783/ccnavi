// Package decide は、レビューで残った指摘の行き先を決める。対象は `ccnavi --reviewed N --accept-unresolved` の対話・
// `--preview --json`・`--yes`・`--choose-out`・`--check`、そして `ops` の投稿の本文の作り直しである。
//
// ユーザは指摘ごとに、3 つのどれかを選ぶ。このまま（Keep）、直す（Fix）、issue に回す（Issue）。このまま残すものは
// 手元に置く。投稿する下書き（決めた内容のコメントと issue）は、state の置き場に書き、sh が投稿する。下書きのファイル名・
// 目印・`--json` の形（版 version）・ダイジェストの形は、sh とボード（拡張の decidemodel.ts）との契約である。変えない。
// 前提の検査は gate の 1 か所にある。本体と `--check` が同じものを通る。
package decide
