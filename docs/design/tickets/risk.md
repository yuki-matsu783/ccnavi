---
type: design
title: 9.9 実績で測るリスク
description: 子を閉じるときに差分から測るリスクの点と、その使い方
tags: [design-doc, ticket, risk]
keywords: [リスク, risks.yml, risk.json, 差分, HIGH, 配点]
---

[設計書の入口](../../design.md) > [9. チケット制御](../tickets.md)

### 9.9 実績で測るリスク

リスクは宣言ではなく実績で測る。子を
`ticket finish` で閉じるとき、その子のワークツリーで `base_sha..HEAD` の差分を数えて点を付け、
`phases/<親>/<子>.risk.json` に残す。フェーズの点は子の最大値。HIGH 以上なら、宣言に関わらず
そのフェーズは人間レビューが要る扱いになる。実績が小さくても宣言のレビュー要は下げない。
上げるのは要否だけで、見る場所は指さない（9.8）。宣言が `none` だったフェーズは `chat` に上がり、
マージリクエストを勧める文が出る。勧めるだけで、選ぶのは端末に座っているユーザ。

配点は `risks.yml`（共通レイヤーと、親の `project:` が指す config のレイヤーの和。どちらも無くてよい。11.4.2）。両方に無ければ組み込みの 4 項目、壊れていれば
組み込みを使い、そのことを `--lint` と閉じたときの出力が言う。リスクレベルの名前は `LOW` / `MEDIUM` / `HIGH` /
`CRITICAL` で固定し、境目の点（既定 20 / 40 / 70）だけを動かせる。境目の点は「以上」で判定する。

| 系統 | 書き方 | 誰が測るか |
|---|---|---|
| 定量（組み込み） | `lines_over` / `files_over` / `deleted_over` / `glob`（当たるごとに加点。`max` で上限） | ccnavi が差分から数える |
| 定量（スクリプト） | `script: <.ccnavi/common/scripts/ の下>`（共通レイヤー。ミラーも同じパス。レイヤーごとの解決先は 11.4.2） | ccnavi が `sh` で走らせる。cwd は子のワークツリー、`CCNAVI_BASE_SHA` / `CCNAVI_HEAD` / `CCNAVI_TICKET` / `CCNAVI_PARENT` を渡し、標準出力の整数か `{"points": N, "message": "…"}` を受け取る。失敗や読めない出力は重いほうとして扱い、その項目の点を加える |
| 定性（サブエージェント） | `judge: <問い>` | 判定が揃うまで子は閉じられない。`finish` が問いと差分の要約を `state/risk-judge-<子>.md` に書き、親がサブエージェントに渡し、報告を `ccnavi-ticket.sh record-risk <子> <項目> yes\|no --reason` で記録する。判定は子の HEAD に結び、HEAD が動けば取り直し。記録できるのは親だけ |

点・リスクレベル・加点した理由は、閉じたときの出力、フェーズの終わりの文面、`--explain`、レビューの
依頼文の先頭に出る。
