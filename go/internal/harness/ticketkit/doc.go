// Package ticketkit は、チケット・フェーズ・レビューを CLI で通すテスト（internal/clitest/ticket/…）が共有する道具である。
// Python のテストの土台（TicketTest・PhaseHarness）から、Go のテストに要る部分を移したもの。
//
// 置くもの
//   - kit.go: git・ファイルの読み書き・終了コードと文面の確かめ（Git・Write・Read・ReadJSON・OK・Fails・Has・Lacks）
//   - text.go: 提案の本文（TicketText。親は最小の計画とフェーズ定義のコピーを持つ）
//   - tree.go: git のリポジトリと親のワークツリー i0001 を組み、承認・着手・hook の呼び出しをたどる Tree
//     （New・Family・ClosePhase・Ccnavi・Hook・Judge・Propose・Approve・StartParent・Worktree・CommitParent）と、
//     組んだ木の写しを戻す Cached（FamilyTree・ClosedTree・RequestedTree）
//   - fixture.go: 見本（拡張のフィクスチャ）を書き直す道具（PortableText・WriteFixture）。見本を書くのはここだけ
//   - review.go: リモートの代わりの結果ファイル（Fixture）と、sh の request と同じ 3 段階（Remote・Request・Confirm・Decide）
//
// 木の置き場は 2 通りある。
//   - New: テストごとの自分の置き場（base.TempDir）。並行に回せる。
//   - Cached 系（FamilyTree・ClosedPhaseTree・ClosedTree・RequestedTree）: treecache が組んだ木の写しを戻す。
//     木の根は鍵と Options ごとに固定のパスで、同じ鍵を使うテストは順に並ぶ。t.Parallel() は Cached より前に呼ぶ。
//     親のテストが持つ鍵を t.Run の子で使わない（落ちる）。鍵には Options が入る。
//
// 使い方（日々の見本は clitest/ticket/core）:
//
//	func TestX(t *testing.T) {
//		t.Parallel()
//		tr := ticketkit.New(t)
//		tr.Family(ticketkit.FamilyOpts{})
//		ticketkit.OK(t, tr.Ccnavi("ticket", "finish", "i0001-01-01"))
//	}
//
// 組んだ木の写しを使うなら、New と Family の代わりに FamilyTree を呼ぶ。
//
//	func TestY(t *testing.T) {
//		t.Parallel()
//		tr := ticketkit.FamilyTree(t, ticketkit.FamilyOpts{})
//		ticketkit.OK(t, tr.Ccnavi("ticket", "finish", "i0001-01-01"))
//	}
package ticketkit
