// Package core は、チケットの土台を CLI で通すテストを置く。承認・着手・判定のキー（ファイルの行き先）・子と親の範囲・
// 状態の置き場・フェーズの終わりとレビューの前提・サブエージェントの差し戻し・ユーザの判断の経路・診断（--lint・--explain）・
// 状態の表示（ticket status）・雛形（ticket new）・直接作業の番号と識別子の形。
// tests/ticket の test_ticket・ticket_status・ticket_new・ticket_control・next_id・overview・ops_cli・lint_ops・lint_places・
// lint_branch_names・branch_to_id の移し。道具は harness/ticketkit（木・提案の本文・リモートの代わりの結果ファイル）にある。
package core
