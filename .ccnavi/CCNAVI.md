# ccnavi の作業の決まり

セッションの開始（起動・再開・compact・clear）のたびに ccnavi が渡す。サブエージェントには渡さない。
ここには概念だけを書き、決まりは危険な作業や失敗を検知した時に案内する。案内が出たらそれに従い、迂回しない。

## 考え方

- メインのコンテキストを清潔に保つ。実装・調査・テストはサブエージェントにバックグラウンドで任せ、メインはユーザとの相談・判断・報告を受け持つ。
- 判断するのはユーザで、エージェントは材料を出す。判定が緩む・やり取りの形が変わる・元に戻せない・影響範囲を読み切れない変更は、実装する前にユーザと合意する。
- 並行して動く他セッションと影響し合わない。自分のワークツリーの外にある未コミットの変更は他セッションのもの。コミット・ブランチの移動・reset をしない。

## 作業の決まり

<!-- ccnavi:if workspace_root -->
  - 編集する前に {{worktree_sh}} <名前> でワークツリー（{{worktrees}}/<名前>）を切り、その中で編集する。直接作業の名前は <先頭の語>-<番号>-<slug>（.ccnavi/docs/worktree.md）。
  - ワークツリーを切るスクリプトが「確認が要る」と言ったら、報告をユーザに伝え、了承を得るまで `--base`・`--accept`・`--side-by-side` を付けない。
  - 取り込み先のブランチを切り替えない。integrate-doneの前に、変わるファイルをユーザに伝え、未保存の編集は保存してもらう。
<!-- ccnavi:endif -->
<!-- ccnavi:if git_wrapper -->
  - git は直接呼ばず {{git_sh}} を通す。止められたら迂回せず、出力の案内に従う。
<!-- ccnavi:else -->
  - git は直接呼ばない。止められたら迂回せず、出力の案内に従う。git の操作が要るときはユーザに頼む。
<!-- ccnavi:endif -->
- ホスト（issue・MR など）の操作は glab を基本にする。gh は origin が GitHub のリポジトリでだけ使う。ccnavi のスクリプト（ccnavi-start.sh・ccnavi-review.sh など）がある操作はスクリプトを優先する。認証で止まったら、ユーザに glab auth login（GitHub なら gh auth login）を頼む。
- 下書きの置き場は 2 つ。ユーザに渡す物は {{handoff}}/（追跡される。integrate が外し、ready が消す）、一時的な物・コミットしたくない物はワークスペースルート直下の {{scratch}}/（追跡されない。終わったら自分で消す）。セッションのスクラッチパッドには置かない。使い分けは `.ccnavi/docs/scratchpad.md`。
- 書き込みを止められている場所（保護対象など）は、{{stage_sh}} <元のパス> で {{handoff}}/ に写して編集する。複数あるときはすべて写してからターンを終える。対話セッションでは、ユーザに `ccnavi user handoff` での取り込みを案内してターンを終える。非対話セッションでは、編集済みの下書きを push し、確認と取り込みをユーザに頼んでターンを終える。
<!-- ccnavi:if projects -->
  - {{projects}}/<名前>/ を直すときは、そこへ cd してから作業する（git の操作は cwd のリポジトリに向く）。
<!-- ccnavi:endif -->
<!-- ccnavi:if tickets -->
  - 承認済みチケットの状態は、ファイルを読んで推測せず {{ticket_sh}} status [<親>] で確かめる。
<!-- ccnavi:endif -->
- サブエージェントに子の作業を任せるときは、プロンプトに子の識別子を 1 つだけ書く（担当以外の子の識別子やワークツリーのパスは書かない）。ccnavi が範囲とフローの案内をプロンプトに書き足す。止められたら、文面の次の一手に従う。isolation は使わない。
<!-- ccnavi:if docs -->
  - 詳しい決まりは下の案内の --docs で、--keyword <語> を付けて引く（例: ワークツリー、下書き、サブエージェント、相談）。
<!-- ccnavi:endif -->

## 詳細

詳しい決まりは `.ccnavi/docs/` にある。作業が表の「読むとき」に当てはまったら開く。

| 読むとき | 開くファイル |
|---|---|
| ワークツリーを切る、他セッションの影響を受けた、親の正本・ワークツリーが無い、`mr:` を確かめる、issue・MR を指定して頼まれた | `.ccnavi/docs/worktree.md`、`worktree-start.md` |
| `<統合先>` に取り込む、子の finish で親へ合流する・衝突を解く、MR に出す、ワークツリーや子のブランチを片付ける | `.ccnavi/docs/worktree-merge.md`、`worktree-cleanup.md` |
| 下書き・使い捨てのファイルを置く | `.ccnavi/docs/scratchpad.md` |
| `projects/` 配下を直す、チケット・マーカー・記録の置き場を確かめる、`rules.yml` の `message` を書く | `.ccnavi/docs/projects.md` |
| 提案を書く、承認・レビュー済みをユーザに頼む、相談が要るか迷う、ccnavi に止められた、サブエージェントに任せる | `.ccnavi/docs/decisions.md` |
| 親の提案を始める、完了した親の計画とフローを使い回す、親を閉じるときにテンプレートの案内が出た | `.ccnavi/docs/templates.md` |
| 用語（ワークスペースルート・統合先ブランチなど）を引く | `.ccnavi/docs/terms.md` |
| `.ccnavi/CCNAVI.md` の条件・差し込みを書く | `.ccnavi/docs/ccnavi-md.md` |
| md に frontmatter を付ける・直す、索引で引く | `.ccnavi/docs/frontmatter.md` |
| フィードバックが届いた、`.claude/skills/` を直す | `.ccnavi/docs/skill-review.md` |
| スキルの振り返りで知識を溜める・読む、工程スキル（process-*）の reference を引く・足す、スキルを新しく作る | `.ccnavi/docs/skill-wiki.md`、`skill-processes.md` |
| ルール・フェーズ・リスク・計画テンプレート・工程スキルが育つ流れを確かめる | `.ccnavi/docs/grow.md` |

<!-- ccnavi:if tickets -->
  ## 作業の進め方

  このワークスペースはチケット制御を使っている。調査・小さな修正は直接作業。設計に触れる・複数のフェーズに分かれる・ユーザのレビューが要るものはチケット作業で、{{tickets_dir}}/todo/ に提案を書き {{ticket_sh}} propose で送る。迷ったらユーザに聞く。チケット作業は issue と MR のセットで、入口で ccnavi-start.sh --check を打つ。issue・MR 指定の実装は必ずこれ。詰まったら直接実装せず、報告して止まる。
  親のワークツリーには書かず、子のワークツリーで作業する。レビュー準備中・レビュー待ちは、別の依頼を別のセッションで受けるようユーザに伝える。詳細は `.ccnavi/docs/ticket-work.md`。
  <!-- ccnavi:if dry_run -->
    （現状: {{mode_env}}=dry-run。一般の deny は止まらないが、チケットの関門と補助の保護で止まる。通ったことを許可と読まず、表示された案内に次からは従う）
  <!-- ccnavi:endif -->
<!-- ccnavi:endif -->
