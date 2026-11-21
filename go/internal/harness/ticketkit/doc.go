// Package ticketkit は、チケット・フェーズ・レビューを CLI で通すテスト（internal/clitest/ticket/…）が共有する道具である。
// tests/ticket/test_ticket.py の TicketTest と、それを継いだ tests/ticket/test_phases.py の PhaseHarness の写し。
//
// 置くもの
//   - kit.go: git・ファイルの読み書き・終了コードと文面の確かめ（Git・Write・Read・ReadJSON・OK・Fails・Has・Lacks）
//   - text.go: 提案の本文（TicketText。親は最小の計画とフェーズ定義のコピーを持つ）
//   - tree.go: git のリポジトリと親のワークツリー i0001 を組み、承認・着手・hook の呼び出しをたどる Tree
//     （New・Family・ClosePhase・Ccnavi・Hook・Judge・Propose・Approve・StartParent・Worktree・CommitParent）
//   - review.go: リモートの代わりの結果ファイル（Fixture）と、sh の request と同じ 3 段階（Remote・Request・Confirm・Decide）
//
// 木はテストごとに自分の置き場（base.TempDir）に組む。Python の treecache（写しを戻す）は使わない。
// Go の実行ファイルは起動が速く、木の組みは 1 秒に満たないので、並行に回せるほうが速い。
//
// 使い方（日々の見本は clitest/ticket/core）:
//
//	func TestX(t *testing.T) {
//		t.Parallel()
//		tr := ticketkit.New(t)
//		tr.Family(ticketkit.FamilyOpts{})
//		ticketkit.OK(t, tr.Ccnavi("ticket", "finish", "i0001-01-01"))
//	}
package ticketkit
