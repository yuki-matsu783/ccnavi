// Package phases は、チケットのフェーズ（全体計画・順序・親に固定する定義・範囲の上限・テンプレート・リスク）を、
// 実行ファイルを外から動かして確かめるテストを置く。書き方は親（clitest）の doc を見る。
//
// 土台は harness_test.go の ws（親 i0001 のワークツリーを持つリポジトリ）。tests/ticket/test_phases.py の PhaseHarness の写し。
// テストごとに使い捨ての置き場へ組み直す（Go の実行ファイルは速いので、Python が持っていた組み置きの写しは持たない）。
// チケットの本文の見本は harness/samples を使う。計画を持たない親の本文（ticketText）はこのパッケージの _test.go が持つ。
package phases
