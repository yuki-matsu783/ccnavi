# ccnavi の作業の決まり

セッションの開始（起動・再開・compact・clear）のたびに ccnavi が渡す。サブエージェントには渡さない。
ここには概念だけを書き、具体的な決まりは危険な作業や失敗を検知した時点で案内する。案内が出たらそれに従い、迂回しない。

## 考え方

- メインのコンテキストを清潔に保つ。実装・調査・テストはサブエージェントにバックグラウンドで任せ、メインはユーザとの相談・判断・報告を受け持つ。
- 判断するのはユーザで、エージェントは材料を出す。判定が緩む・やり取りの形が変わる・元に戻せない・影響範囲を読み切れない変更は、実装する前にユーザと合意する。
- 並行して動く他セッションと影響し合わない。自分のワークツリーの外にある未コミットの変更は他セッションのもの。コミット・ブランチの移動・reset をしない。

## 作業の決まり

<!-- ccnavi:if workspace_root -->
  - 編集する前にワークツリー（{{worktrees}}/<名前>）を切り、その中で編集する。切り方は ccnavi-git.sh の --help（worktree add）。直接作業の名前は <先頭の語>-<番号>-<slug>（.ccnavi/docs/worktree.md）。
<!-- ccnavi:endif -->
<!-- ccnavi:if git_wrapper -->
  - git は直接呼ばず {{git_sh}} を通す。止められたら迂回せず、出力の案内に従う。
<!-- ccnavi:else -->
  - git は直接呼ばない。止められたら迂回せず、出力の案内に従う。git の操作が要るときはユーザに頼む。
<!-- ccnavi:endif -->
- 下書きの置き場は 2 つ。ユーザに渡す物は {{handoff}}/（追跡される。integrate が外し、ready が消す）、一時的な物・コミットしたくない物はワークスペースルート直下の {{scratch}}/（追跡されない。終わったら自分で消す）。セッションのスクラッチパッドには置かない。使い分けは `.ccnavi/docs/scratchpad.md`。
- 書き込みを止められている場所（保護対象など）は、{{stage_sh}} <元のパス> で {{handoff}}/ に写して編集する。複数あるときはすべて写してからターンを終える。対話セッションでは、{{stage_sh}} --commands <元のパス> が出した mv（フルパス）をユーザに案内してターンを終える。非対話セッションでは、編集済みの下書きを push し、確認と差し替えをユーザに頼んでターンを終える。
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
| ワークツリーを切る、他セッションの変更の影響を受けた、親の正本・未準備・`mr:` を確かめる | `.ccnavi/docs/worktree.md` |
| issue・MR を指定して作業を頼まれた | `.ccnavi/docs/worktree-start.md` |
| `<統合先>` に取り込む、子の finish で親へ合流する・衝突を解く、マージリクエストに出す | `.ccnavi/docs/worktree-merge.md` |
| ワークツリーや子のブランチを片付ける、親を閉じたあとの片付け | `.ccnavi/docs/worktree-cleanup.md` |
| 下書きや使い捨てのファイルを置く | `.ccnavi/docs/scratchpad.md` |
| `projects/` 配下を修正する、チケット・マーカー・記録の置き場を確かめる、`rules.yml` の `message` を書く | `.ccnavi/docs/projects.md` |
| 提案を書く、相談が要るか迷う、ccnavi に止められた、サブエージェントに任せる | `.ccnavi/docs/decisions.md` |
| 親の提案を始める、完了した親の計画とフローを次に使い回したい、親が閉じるときにテンプレートの案内が出た | `.ccnavi/docs/templates.md` |
| 用語（ワークスペースルート・統合先ブランチなど）を確かめる | `.ccnavi/docs/terms.md` |
| `.ccnavi/CCNAVI.md` の条件や差し込みを書く・直す | `.ccnavi/docs/ccnavi-md.md` |
| md に frontmatter を付ける・直す、md の索引で引く | `.ccnavi/docs/frontmatter.md` |
| フィードバックが届いた、`.claude/skills/` を直したくなった | `.ccnavi/docs/skill-review.md` |
| スキルの振り返りで知識を `docs/wiki/` に溜める、wiki を読む・書く | `.ccnavi/docs/skill-wiki.md` |
| 工程スキル（process-*）の reference を引く・足す、スキルを新しく作りたくなった | `.ccnavi/docs/skill-processes.md` |

<!-- ccnavi:if tickets -->
  ## 作業の進め方

  このワークスペースはチケット制御を使っている。進め方は 2 つ。

  - 直接作業（調査・小さな修正）: チケットを起こさずそのまま進める。判定は全体ルールだけ。
  - チケット作業（設計に触れる・複数のフェーズに分かれる・ユーザのレビューが要る）: {{tickets_dir}}/todo/ に提案を書いて承認を受ける。承認されると提案は {{approved_dir}}/doing/ へ動く。以後の操作は {{ticket_sh}} を通す（使い方は --help）。

  どちらで進めるか迷ったら、ユーザに聞く。

  作業中の正本は親のブランチを checkout した親のワークツリー（名前はブランチ名の `/` を `-` にしたもの）。ワークツリーが無い・HEAD が違うPCでは操作が止まるので、案内の `ccnavi-start.sh` で準備する。開始時に出る作業中の親の一覧で「リモート未確認」の親は、{{ticket_sh}} status で突き合わせる。親の提案には `mr:` を必ず書く。詳細は `.ccnavi/docs/worktree.md`。
  <!-- ccnavi:if dry_run -->
    （現状: {{mode_env}}=dry-run。deny にヒットしても止まらない。通ったことを許可と読まず、表示された案内に次からは従う）
  <!-- ccnavi:endif -->
<!-- ccnavi:endif -->
