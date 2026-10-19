---
type: guide
title: ルールが育つ流れ
description: rules.yml のルールが、きっかけから下書き・検証・ユーザの配置・効き始め・配布までどう育つかの全体像
tags: [rules, config]
keywords: [ルール, rules.yml, 育つ, 全体像, suggest, 下書き, lint, test-expectations, rule-expectations, dry-run, 共通レイヤー, ミラー]
---

# ルールが育つ流れ

ルールは 1 回で完成させず、止まった・通ってしまった事実を材料に足していく。書くのはエージェントでも、置いて効かせるのはユーザ。
`rules.yml` は保護対象で、エージェントは直接書けないため。

```
きっかけ → 下書き → 検証 → 承認（ユーザが置く） → 効く → 配る
```

| 段階 | 何が起きるか | 誰が | 使うもの |
|---|---|---|---|
| きっかけ | 通したくなかった操作が通った、止めすぎた、`UNDECLARED` が多い、記録で同じ呼び出しが何度も止まった | ユーザが気づく。記録からは ccnavi が候補を出す | `ccnavi --suggest`、`--explain`、記録（`decisions.jsonl`） |
| 下書き | 本物の全文を写して 1 件足す。期待する判定も 1 行足す（止めたくないものも一緒に） | エージェント | `/ccnavi-config`、置き場は `wip/handoff/`（`ccnavi-stage.sh` で写す） |
| 検証 | 防御を消す記述が無いか、当たるかどうかを機械に聞く。目で判定を真似しない | エージェント | `ccnavi --lint`、`--test-expectations`、`scripts/check_rules.py` |
| 承認 | 何をなぜ変えるかと一緒に渡し、ユーザが差分を読んで置く | ユーザ | `ccnavi user handoff` |
| 効く | 次の判定から効く。`dry-run` の間、一般の保護は止めず「enable なら止めていた」を伝えるだけ（チケットの関門と補助の保護は止める） | ccnavi | `CCNAVI_MODE` |
| 配る | 共通レイヤーのルールは、親の着手でプロジェクトの `.ccnavi/config/common/` へミラーされる | ccnavi | 親の `ticket start` |

## 規則

- 食い違いが出ても、期待する判定を実際の判定に合わせて書き換えない。ルールと期待のどちらを直すかはユーザが決める（期待は「意図」の記録で、合わせると意図が消えるため）
- ルールの強さは `deny` > `ask` > `allow`。戻せないもの・代わりの経路があるものは `deny`、ユーザが毎回見たいものは `ask`、`allow` は権限を配る場所ではない
- 条件を 1 本の regex に詰めない。「除く」は `except:`、組み合わせは `when:` で書く（先読みを受け付けないため、詰めると 1 文字ずつの場合分けになる）
- `deny` の `message` は「なぜ止めたか＋代わりに何をするか」。止められたエージェントがそれだけで次の一手を選べるようにする
- 共通レイヤーのルールが壊れていても、組み込みの `deny` は残る。壊れた設定が防御を消さないため
- ミラーは誰も編集しない写し。プロジェクト固有のルールは `.ccnavi/config/project/` に置く（ADR-0107）

## 次に読む

| 読むとき | 開くファイル |
|---|---|
| 書式・記法の細部 | `docs/design/rules.md`、`.claude/skills/ccnavi-config/references/add.md` |
| 確かめ方 | `.claude/skills/ccnavi-config/references/check.md` |
| 共通レイヤーの配り方 | `docs/adr/0107-common-layer-is-mirrored-and-phases-live-in-config.md` |
