---
type: guide
title: 設定と工程スキルが育つ流れ
description: ルール・フェーズ定義・リスクの配点・計画のテンプレート・工程スキルの reference が、下書きから配布まで育つ共通の 6 段と、対象ごとの文書への入口
tags: [config, ticket]
keywords: [育つ, grow, rules.yml, phases.yml, risks.yml, テンプレート, 工程スキル, 下書き, 検証, 承認, 配布, ミラー, 共通レイヤー]
---

# 設定と工程スキルが育つ流れ

作業中に見つけた直しは、対象によらず次の 6 段で進める。1 回で完成させず、止まった・通ってしまった・重さが合わなかった事実を材料に足していく。

```
きっかけ → 下書き → 検証 → 承認（ユーザが置く） → 効く → 配る
```

| 段階 | 共通の中身 | 誰が |
|---|---|---|
| きっかけ | 実際の振る舞いと意図のずれに気づく | ユーザ。記録からは ccnavi が候補を出す |
| 下書き | 本物の全文を写して直す。置き場は `wip/handoff/`（`ccnavi-stage.sh` で写す）。ルール・フェーズ・配点は `/ccnavi-config` | エージェント |
| 検証 | 読めるか・当たるかを機械に聞く。目で判定を真似しない | エージェント |
| 承認 | 何をなぜ変えるかと一緒に渡し、ユーザが差分を読んで `ccnavi user handoff` で置く。設定ファイルは保護対象で、エージェントは直接書けない | ユーザ |
| 効く | 置いたあとの判定・作業から効く。進行中の親や子には届かないものがある（対象ごとの文書） | ccnavi |
| 配る | 共通レイヤーは、親の `ticket start` がプロジェクトの `.ccnavi/config/common/` へミラーする。ミラーは誰も編集しない写しで、プロジェクト固有のものは `.ccnavi/config/project/` に置く。ずれたときの同期は、ユーザが `ccnavi user mirror` で行う | ccnavi |

テンプレートと工程スキルの reference は、承認と効く段の主体が上の表と違う（それぞれの文書に書く）。

## 対象ごとの文書

各文書は、その対象だけの「きっかけ・検証・効き方」と規則を書く。

| 確かめるもの | 開くファイル |
|---|---|
| ルール（rules.yml） | [grow-rules.md](grow-rules.md) |
| フェーズ定義 | [grow-phases.md](grow-phases.md) |
| リスクの配点 | [grow-risks.md](grow-risks.md) |
| 作業・レビュー計画のテンプレート | [grow-templates.md](grow-templates.md) |
| 工程スキルの reference | [grow-process-skills.md](grow-process-skills.md) |
