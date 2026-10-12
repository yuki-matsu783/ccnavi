---
type: adr
title: 工程スキルの名前をフェーズ定義の id の語に揃え、implement を足す
description: designing・investigating・testing を design・research・test に改め、実装の入口として implement を7本目に足す。debugging・reviewing・releasing はそのまま
tags: [skills, phase]
keywords: [スキル, 工程スキル, フェーズ定義, phases.yml, 名前, design, research, implement, test, 改名]
---
# ADR-0133: 工程スキルの名前をフェーズ定義の id の語に揃え、implement を足す

状態: 採用（2026-10-12 にユーザが決めた。ADR-0126 の工程スキルの名前と「6本」を改める。ほかの決定は ADR-0126 のまま）

## 状況

工程スキルの名前（`designing` `investigating` `testing`）と、フェーズ定義の id（`design` `research` `acceptance` `implement`）は、同じ工程を指しているのに語が揃っていなかった。
「調査」は `research` と `investigating` に分かれ、設計は名詞と動名詞に分かれていた。実装にはスキルが無く、テストはフェーズ定義が `acceptance` と `implement` に分かれる一方でスキルは1本だった。
フェーズ定義とスキルを結ぶ仕組みはコードに無く、読む人（モデルを含む）が名前から「このフェーズならこのスキル」と引くしかない。
配布はまだしていないので、名前の互換は考えなくてよい。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| `designing` を `design`、`investigating` を `research`、`testing` を `test` に改める | フェーズ定義の id と同じ語になり、名前から対応が引ける。`research` は、ユーザが調査の英語として指定した | フェーズ定義の id をスキルの語（`investigating` など）に寄せる（id は計画や写しに書かれるので、影響が広い）、対応表だけ置く（語の食い違いが残る） |
| 実装の入口として `implement` を7本目に足す | フェーズ定義に `implement` があり、実装には入口が無かった。`debugging` は原因の調査に絞られている | `test` に混ぜる（実装とテストは別の作業で、description が膨らむ） |
| `debugging` `reviewing` `releasing` はそのままにする | 対応するフェーズ定義が無い。Claude Code の組み込みの名前（`debug` `review`）と重なるおそれがある | 同じ形（`debug` `review` `release`）に揃える |
| 対応するフェーズ定義の id を `.ccnavi/docs/skill-processes.md` の表に書く | 対応が無いスキルは「なし」と明記し、後から足すときに表を見る | 対応表を別の文書にする |

## 得たもの・失ったもの

- 得たもの: フェーズ定義の id とスキル名が同じ語になり、実装の入口ができる
- 失ったもの: スキル名の形が揃わない（`design` と `debugging` が並ぶ）。対応するフェーズ定義の有無で分けた結果
- 失ったもの: 常駐するスキルの説明文が1本増える（7本）
- 失ったもの: reference の `tags` の鍵が変わる。`ccnavi --docs --tag testing` は当たらなくなる。まだ配っていないので、ワークスペースの reference の `tags` を新しい名前に直した

## 採らなかった案

- **フェーズ定義の id を改める。** `phases.yml` の id は提案チケットの `plan:` や親の写しに書かれる。スキルの名前を変えるほうが影響が小さい
- **スキルの名前は変えず、対応表だけ置く。** 語の食い違いが残り、表を引かないと対応が読めない
