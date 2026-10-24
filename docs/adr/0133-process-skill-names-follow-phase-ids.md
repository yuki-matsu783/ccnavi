---
type: adr
title: 工程スキルの名前を process- とフェーズ定義の id の語にし、implement を足す
description: 工程スキル7本を process-research・process-design・process-implement・process-test・process-debug・process-review・process-release に改める。後ろの語はフェーズ定義の id にそろえ、process- は他のスキルとの重なりを避ける印にする
tags: [skills, phase]
keywords: [スキル, 工程スキル, フェーズ定義, phases.yml, 名前, プレフィックス, process-, research, design, implement, test, 改名]
---
# ADR-0133: 工程スキルの名前を process- とフェーズ定義の id の語にし、implement を足す

状態: 採用（2026-10-12 にユーザが決めた。ADR-0126 の工程スキルの名前と「6本」を改める。ほかの決定は ADR-0126 のまま）

## 状況

工程スキルの名前（`designing` `investigating` `testing`）と、フェーズ定義の id（`design` `research` `acceptance` `implement`）は、同じ工程を指しているのに語が揃っていなかった。
「調査」は `research` と `investigating` に分かれ、設計は名詞と動名詞に分かれていた。実装にはスキルが無く、テストはフェーズ定義が `acceptance` と `implement` に分かれる一方でスキルは1本だった。
フェーズ定義とスキルを結ぶ仕組みはコードに無く、読む人（モデルを含む）が名前から「このフェーズならこのスキル」と引くしかない。

また、`test` や `design` のような短い名前は、組み込みのスキルや各自が作ったスキルと重なりやすい。個人のスキルはプロジェクトのスキルより優先されるので、同じ名前が個人側にあると工程スキルが黙って使われなくなる（この優先の順は Claude Code の仕様の理解で、この変更では動かして確かめていない）。
導入スクリプトは配布先に同名のファイルがあれば上書きしないので、配布先の自作スキルの中に共通の `references/common/` が入ってしまうこともある。

配布はまだしていないので、名前の互換は考えなくてよい。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| 名前を `process-` に続く語にする。後ろの語は、対応するフェーズ定義の id と同じにする（`process-research` `process-design` `process-implement` `process-test`） | 名前から対応が引ける。`process-` が付くので、他のスキルと重なりにくく、一覧でも並ぶ（重ならないことまでは保証しない）。`research` は、ユーザが調査の英語として指定した | フェーズ定義の id をスキルの語に寄せる（id は計画やコピーに書かれるので、影響が広い）、対応表だけ置く（語の食い違いが残る）、プレフィックス無し（個人のスキルに隠される） |
| 対応するフェーズ定義が無いものも同じ形にする（`process-debug` `process-review` `process-release`） | プレフィックスで組み込みの名前（`debug` `review`）と重ならなくなり、形を揃えられる | 古い名前のまま残す（形が揃わない） |
| 実装の入口として `process-implement` を7本目に足す | フェーズ定義に `implement` があり、実装には入口が無かった。`process-debug` は原因の調査に絞られている | `process-test` に混ぜる（実装とテストは別の作業で、description が膨らむ） |
| 対応するフェーズ定義の id を `.ccnavi/docs/skill-processes.md` の表に書く | 対応が無いスキルは「なし」と明記し、後から足すときに表を見る | 対応表を別の文書にする |
| reference の `tags` には、スキル名（`process-test` など）をそのまま入れる | 引くキーとスキル名が1対1のまま | 後ろの語だけをキーにする（スキル名とキーが食い違う） |

## 得たもの・失ったもの

- 得たもの: スキル名が、他のスキルと重ならず、フェーズ定義の id と同じ語を含む。実装の入口ができる
- 失ったもの: スキルの呼び出しで打つ文字が増える（`/process-test`）
- 失ったもの: 常駐するスキルの説明文が1本増える（7本）
- 失ったもの: reference の `tags` のキーが変わる。`ccnavi --docs --tag testing` はヒットしなくなる。まだ配っていないので、ワークスペースの reference の `tags` を新しい名前に直した

## 採らなかった案

- **フェーズ定義の id を改める。** `phases.yml` の id は提案チケットの `plan:` や親のコピーに書かれる。スキルの名前を変えるほうが影響が小さい
- **スキルの名前は変えず、対応表だけ置く。** 語の食い違いが残り、表を引かないと対応が読めない
