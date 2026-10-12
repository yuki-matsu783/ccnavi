# CLAUDE.md

このファイルには常に守ることと、その背景にある考え方だけを書く。詳細は `.ccnavi/docs/`（ccnavi の使い方。配布先にも配る）と `docs/claude/`（ccnavi の開発）に記載。作業が表の「読むとき」に当てはまった場合に参照する。

## 応答ルール

1. 簡潔で明確な日本語を使う
2. 「です・ます」調は使うが、冗長な敬語表現は避ける
3. 「いたします」→「します」、「ございます」→「あります」に置き換える
4. 「恐れ入りますが」などのクッション言葉は省く

## 考え方

- メインセッションのコンテキストを清潔に保つ。作業の出力が積もると、ユーザとの相談や判断が薄まるため
- 決まりは最初から全部は渡さない。危険な作業や失敗を ccnavi が検知した時点で、正しい方法を案内する。案内が出たらそれに従い、迂回しない。関係ない作業のための案内でコンテキストが薄まるため
- 判断するのはユーザで、エージェントはその材料を出す。エージェントの誤りを、取り返しが付かなくなる前にユーザが止められるようにするため
- 作業中は並行して動く他セッションの作業と互いに影響しない。同じワークスペースで、複数のセッションが同時に作業するため

## 常に守ること

- sh は Windows (Git Bash / WSL)・Linux・macOS のどれでも動くように書く
- ccnavi の動作に依存する決まり（ワークツリー・git の入口・下書きの置き場・サブエージェントへの委任・合意が要る変更の範囲）は、セッション開始時に ccnavi が渡す。細かいことは、その案内の `--docs` で ccnavi に聞く
- 書き込みを制限されている場所（保護対象のファイルなど）の修正を求められた場合は、`ccnavi-stage.sh <元のパス>`でワークスペースルートの`wip/scratchpad/`に元ファイルをcpした上で編集する。複数ファイルを同時に直すときは、すべて作ってからターンを終える。
  - 対話セッション: `ccnavi-stage.sh --commands <元のパス>`が出したmvのコマンドをユーザに案内してターンを終える。（フルパス記載）
  - 非対話セッション: `wip/scratchpad/`の編集済みファイルをpushし、ユーザに確認と制限対象ファイルの差し替えを頼んでターンを終える。

## 詳細

| 読むとき | 開くファイル |
|---|---|
| ワークツリーを切る、他セッションの変更の影響を受けた | `.ccnavi/docs/worktree.md` |
| issue・MR を指定して作業を頼まれた | `.ccnavi/docs/worktree-start.md` |
| `<統合先>` に取り込む、マージリクエストに出す | `.ccnavi/docs/worktree-merge.md` |
| ワークツリーを片付ける | `.ccnavi/docs/worktree-cleanup.md` |
| 下書きや使い捨てのファイルを置く | `.ccnavi/docs/scratchpad.md` |
| `projects/` 配下を修正する、チケット・マーカー・記録の置き場を確かめる、`rules.yml` の `message` を書く | `.ccnavi/docs/projects.md` |
| 提案を書く、相談が要るか迷う、ccnavi に止められた、サブエージェントに任せる | `.ccnavi/docs/decisions.md` |
| ワークスペースルート・統合先ブランチなどの用語を確かめる | `.ccnavi/docs/terms.md` |
| md に frontmatter を付ける・直す、`ccnavi --docs` で引く | `.ccnavi/docs/frontmatter.md`（ccnavi のリポジトリの `type` の値は `docs/claude/frontmatter.md`） |
| sh やスクリプトを書く、文書・コメント・ユーザに見える文言で ADR に触れたくなった | `docs/claude/environment.md` |
| ccnavi の実行ファイル（`src/ccnavi/`）か `.ccnavi/scripts/` の sh を直す | `docs/claude/exe-boundary.md` |
| sh・Python・TS でログを書く、logger を直す | `docs/claude/logging.md` |
| フィードバックメッセージが届いた、`.claude/skills/` を直したくなった、フィードバック計画を書く | `.ccnavi/docs/skill-review.md` |
| スキルの振り返りで知識を `docs/wiki/` に溜める、wiki のパターンや提案の採否を読む・書く | `.ccnavi/docs/skill-wiki.md` |
| 概念スキル（designing・testing・debugging・reviewing・releasing）の reference を引く・足す、スキルを新しく作りたくなった | `.ccnavi/docs/skill-concepts.md` |
