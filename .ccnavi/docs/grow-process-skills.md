---
type: guide
title: 工程スキルが育つ流れ
description: 工程スキルの reference が、振り返りで拾った知識から wiki・候補・承認・反映・配布までどう育つかの全体像
tags: [skills, docs-search]
keywords: [工程スキル, reference, 育つ, 全体像, 振り返り, wiki, skill-improve, 候補, common, project, process-design, process-research, process-implement, process-test, process-debug, process-review, process-release]
---

# 工程スキルが育つ流れ

工程スキル（`process-design` `process-research` `process-implement` `process-test` `process-debug` `process-review` `process-release`）の SKILL.md は薄い入口に保ち、育つのは `references/` の側。
スキルの `name` と `description` は常に文脈に入るため、増やすのは reference だけにして、必要なときに `ccnavi --docs` で引く。

```
きっかけ → 下書き → 検証 → 承認 → 効く → 配る
```

| 段階 | 何が起きるか | 誰が | 使うもの |
|---|---|---|---|
| きっかけ | ユーザの訂正、時間をかけて分かった落とし穴、操作の失敗と回避策、読んだスキルの誤り。サブエージェントは報告の「スキル候補」の節で返す | エージェント（ターンの終わりの振り返り） | `.ccnavi/docs/skill-review.md` |
| 下書き | 拾ったものを `docs/wiki/` に溜める（パターンと採否の記録）。その上で、対象の工程の `references/` への追記を候補にする。振り返りの最中は `.claude/skills/` を書き換えない | エージェント | `wip/handoff/skill-candidates.md`、`.ccnavi/docs/skill-wiki.md` |
| 検証 | 既存と重複しないか、他のプロジェクトでも使えるか（`common/` か `project/` か）を決める。却下済みの提案を根拠なしに出し直さない | エージェント | `ccnavi --learnings` の一覧と wiki の `impact/` |
| 承認 | 親の下ではフィードバック計画の `skill-improve` フェーズ、それ以外は提案チケット。ユーザが承認し、子の MR で差分を見る | ユーザ | `ticket`、MR |
| 効く | reference を追加・追記する。工程スキルの reference は `type: skill-reference`、`tags` に工程名を入れる | エージェント（承認した子の中） | `ccnavi --docs --type skill-reference --tag <工程>` |
| 配る | `common/` は親の `ticket start` がプロジェクトの `skills/<工程>/` へ写す（共通側から消えた `.md` も消える）。`project/` は写されない | ccnavi | 親の `ticket start` |

## 規則

- 手順の本文は SKILL.md に足さない。工程スキルの SKILL.md は「基本の考え方」と引き方の一文だけ。直すのは `references/` のほう（細かい手順で入口が重くなるのを防ぐ）
- 1 ファイル 1 話題。書き方は手順の流れに沿った「規則 + なぜ（1 句）」。日付・チケット番号・エラー文を名前や見出しにしない
- 他のプロジェクトでも使えるなら `common/`（共通の提案にする）、そのリポジトリだけの事情なら `project/`
- 写された `common/` はプロジェクトの中で直さない。次の着手で上書きされる。直すなら共通の置き場 `.claude/skills/<工程>/` の提案にする
- wiki のパターンが繰り返し有効で、手順として確かめられたものだけが reference になる。wiki は作業のエージェントには読ませない（スキルの効きが見えなくなるため）
- 新しい工程スキルを増やすのは、どの工程にも当たらない作業が繰り返し出たときだけ。ユーザの承認を取る
- 環境が原因の失敗や、道具を否定する主張は拾わない

## 次に読む

| 読むとき | 開くファイル |
|---|---|
| 置き場と書き方 | `.ccnavi/docs/skill-processes.md` |
| 振り返りの拾い方・候補の渡し方 | `.ccnavi/docs/skill-review.md` |
| wiki の中身 | `.ccnavi/docs/skill-wiki.md` |
