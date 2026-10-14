# ccnavi の作業の決まり

ccnavi が前提にしている決まり。セッションの開始（起動・再開・compact・clear）のたびに ccnavi が渡す。サブエージェントには渡さない。
常にセッションに注入されるルールは概念的な説明のみに絞る（関係ない作業用のルールでコンテキストが薄まるため）。危険な作業や失敗を ccnavi が検知した時点で、具体的なルールを案内する。案内が出たらそれに従い、迂回しない。

## 考え方

- メインセッションのコンテキストを清潔に保つ（コンテキストロットにより、ユーザとの相談や判断が薄まるため）。実装・調査・テストはサブエージェントにバックグラウンドで任せ、メインはユーザとの相談・判断・報告を受け持つ。
- 判断するのはユーザで、エージェントは材料を出す（誤りを、取り返しが付かなくなる前にユーザが止められるようにするため）。判定が緩む・ユーザとのやり取りの形が変わる・元に戻せない・影響範囲を読み切れない変更は、実装する前にユーザと合意する。
- 並行して動く他セッションと互いに影響しない（同じワークスペースで複数のセッションが同時に作業するため）。自分のワークツリーの外にある未コミットの変更は他セッションのもの。コミット・ブランチの移動・reset をしない。

## 作業の決まり

<!-- ccnavi:if workspace_root -->
  - 編集する前にワークツリー（{{worktrees}}/<名前>）を切り、その中で編集する。切り方は ccnavi-git.sh の --help（worktree add）。直接作業の名前は <先頭の語>-<番号>-<slug>（.ccnavi/docs/worktree.md）。
<!-- ccnavi:endif -->
<!-- ccnavi:if git_wrapper -->
  - git は直接呼ばず {{git_sh}} を通す。止められたら迂回せず、出力の案内に従う。
<!-- ccnavi:else -->
  - git は直接呼ばない。止められたら迂回せず、出力の案内に従う。git の操作が要るときはユーザに頼む。
<!-- ccnavi:endif -->
- 置き場は 2 つある。ユーザに渡す物（保護対象を直した写し・MR と一緒に見せる物）は、ワークツリーの中でも {{handoff}}/ に置く（追跡され、チケットの範囲では報告されない。統合のときに integrate が外し、ready が消す）。その場で一時的に見せる物・コミットしたくない物は、ワークスペースルート直下の {{scratch}}/ に置く（.gitignore で追跡から外れる。ccnavi は消さないので、終わったら自分で消す）。セッションのスクラッチパッド（Environment 欄の場所）には置かない。使い分けは `.ccnavi/docs/scratchpad.md`。
- 書き込みを止められている場所（保護対象のファイルなど）を直すときは、{{stage_sh}} <元のパス> で {{handoff}}/ に写して編集する。複数あるときはすべて写してからターンを終える。対話セッションでは、{{stage_sh}} --commands <元のパス> が出した mv（フルパス）をユーザに案内してターンを終える。非対話セッションでは、編集済みの下書きを push し、ユーザに確認と差し替えを頼んでターンを終える。
<!-- ccnavi:if projects -->
  - {{projects}}/<名前>/ を直すときは、そこへ cd してから作業する（git の操作は cwd のリポジトリに向く）。
<!-- ccnavi:endif -->
<!-- ccnavi:if tickets -->
  - 承認済みチケットの状態は、ファイルを読んで推測せず {{ticket_sh}} status [<親>] で確かめる。
<!-- ccnavi:endif -->
<!-- ccnavi:if docs -->
  - 詳しい決まりは下の案内の --docs で、--keyword <語> を付けて引く（例: ワークツリー、下書き、サブエージェント、相談）。
<!-- ccnavi:endif -->

## 詳細

詳しい決まりは `.ccnavi/docs/` にある。作業が表の「読むとき」に当てはまったら開く。

| 読むとき | 開くファイル |
|---|---|
| ワークツリーを切る、他セッションの変更の影響を受けた | `.ccnavi/docs/worktree.md` |
| issue・MR を指定して作業を頼まれた | `.ccnavi/docs/worktree-start.md` |
| `<統合先>` に取り込む、マージリクエストに出す | `.ccnavi/docs/worktree-merge.md` |
| ワークツリーを片付ける | `.ccnavi/docs/worktree-cleanup.md` |
| 下書きや使い捨てのファイルを置く | `.ccnavi/docs/scratchpad.md` |
| `projects/` 配下を修正する、チケット・マーカー・記録の置き場を確かめる、`rules.yml` の `message` を書く | `.ccnavi/docs/projects.md` |
| 提案を書く、相談が要るか迷う、ccnavi に止められた、サブエージェントに任せる | `.ccnavi/docs/decisions.md` |
| 親の提案を始める、完了した親の計画とフローを次に使い回したい、親が閉じるときにテンプレートの案内が出た | `.ccnavi/docs/templates.md` |
| ワークスペースルート・統合先ブランチなどの用語を確かめる | `.ccnavi/docs/terms.md` |
| `.ccnavi/CCNAVI.md` の条件（`ccnavi:if`）や差し込みを書く・直す | `.ccnavi/docs/ccnavi-md.md` |
| md に frontmatter を付ける・直す、md の索引で引く | `.ccnavi/docs/frontmatter.md` |
| フィードバックメッセージが届いた、`.claude/skills/` を直したくなった、フィードバック計画を書く | `.ccnavi/docs/skill-review.md` |
| スキルの振り返りで知識を `docs/wiki/` に溜める、wiki のパターンや提案の採否を読む・書く | `.ccnavi/docs/skill-wiki.md` |
| 工程スキル（process-design・process-research・process-implement・process-test・process-debug・process-review・process-release）の reference を引く・足す、スキルを新しく作りたくなった | `.ccnavi/docs/skill-processes.md` |

<!-- ccnavi:if tickets -->
  ## 作業の進め方

  このワークスペースはチケット制御を使っている。進め方は 2 つ。

  - 直接作業（調査・小さな修正）: チケットを起こさずそのまま進める。判定は全体ルールだけ。
  - チケット作業（設計に触れる・複数のフェーズに分かれる・ユーザのレビューが要る）: {{tickets_dir}}/todo/ に提案を書いて承認を受ける。承認されると提案は {{approved_dir}}/doing/ へ動く。以後の操作は {{ticket_sh}} を通す（使い方は --help）。

  どちらで進めるか迷ったら、ユーザに聞く。

  親のワークツリーの名前（識別子）は、親のブランチ名の `/` を `-` に置き換えたもの。ワークツリーはそのブランチを直接チェックアウトし、識別子の名前のブランチは作らない。セッションの開始時に、このPCで作業中の親が1行ずつ出る（親・ブランチ・MR・次の子・最終取り込み・未送信）。最終取り込みが「リモート未確認」の親は、まだ {{ticket_sh}} status や `ccnavi-sync.sh` でリモートと突き合わせていない。親の提案には `mr:` を必ず書く（`ccnavi-start.sh` が出す番号）。
  <!-- ccnavi:if dry_run -->
    （現状: {{mode_env}}=dry-run。deny にヒットしても止まらない。通ったことを許可と読まず、表示された案内に次からは従う）
  <!-- ccnavi:endif -->
<!-- ccnavi:endif -->
