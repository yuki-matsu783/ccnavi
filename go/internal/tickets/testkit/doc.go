// Package testkit は tickets のテストが共有する道具（テストからだけ読む）。見本（Python の実装の答えの写し。読むのは
// infra/golden）とキーごとに比べる部品（SameJSON）、コミットの時刻を固定した git（Clock。同じ形を作り直すと sha が
// 見本とそろう）、ワークスペース・ワークツリー・ファイルを作る部品と、
// 複数のパッケージが同じものを使うチケットの見本（lifecycle の ParentText と片付けの後の形の写し Shape、proposal の
// Proposal*、親が閉じる側の Closing*）を持つ。1 つのパッケージだけが使う見本は、そのパッケージの fixture_test.go に置く。
//
// tickets の本体のどのパッケージにも依存しない（依存すると、そのパッケージの中のテストから読めない）。段の表では
// tickets の一番上に置き、本体のコードから読めないようにしてある。
package testkit
