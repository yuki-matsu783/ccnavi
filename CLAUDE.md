# CLAUDE.md

このファイルには、ccnavi の開発で常に守ることだけを書く。詳細は `.ccnavi/docs/`（ccnavi の使い方。配布先にも配る）と `docs/claude/`（ccnavi の開発）に記載。作業が表の「読むとき」に当てはまった場合に参照する。

## 応答ルール

1. 簡潔で明確な日本語を使う
2. 「です・ます」調は使うが、冗長な敬語表現は避ける
3. 「いたします」→「します」、「ございます」→「あります」に置き換える
4. 「恐れ入りますが」などのクッション言葉は省く

## 常に守ること

- sh は Windows (Git Bash / WSL)・Linux・macOS のどれでも動くように書く
- ccnavi の使い方に関わる考え方と決まり（ワークツリー・git の入口・下書きの置き場・サブエージェントへの委任・合意が要る変更の範囲・保護対象の直し方）は `.ccnavi/CCNAVI.md` に書く。セッション開始時に ccnavi がそれを渡すので、ここには書かない。ccnavi の振る舞いが変わったら CCNAVI.md を直す

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
