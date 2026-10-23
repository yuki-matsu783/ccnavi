---
title: 更新したときの変更点
type: guide
description: ccnavi を更新したときに変わる挙動と、そろえて更新するもの、旧い記録の扱い
tags: [development, release]
keywords: [更新, 変更点, 互換の版, リリースノート, dry-run, feedback, decide, GitLab, GHE.com, 操作記録]
---

# 更新したときの変更点

ccnavi を更新したら、次の 4 点が変わる。

- `dry-run` でも、チケットの操作（状態の操作）と、承認済みの置き場・操作記録への書き込みは止まる。一般のルールの `deny`・`ask` だけは止まらず、報告になる。`ccnavi --explain` の「dry-run のときに止めるもの」で確かめられる
- 互換の版が 18 になった（17 から。意味の変更・公開している入口の削除・新旧が混ざったときの誤判定があるため）。ccnavi の実行ファイル、`.ccnavi/scripts/` の sh、VS Code 拡張、Chrome 拡張を、そろえて更新する（`sh scripts/ccnavi-setup.sh <ワークスペース> --force` と拡張の入れ直し）。食い違えば sh と拡張と `--lint` が `(version)` で知らせる。主な変更は次のとおり。
  - フィードバック計画（`feedback:`）を初めて足す改版の承認は、レビューの状態を変えない。最後のフェーズがレビュー済みでなければ拒み、同じ承認に全体計画の子が入っていれば改版と子を両方拒む。古い版が承認で置いた `reviewed` の `by: feedback-plan` は読めるまま残り、`--lint` が info で一覧にする（消すかはユーザが決める）
  - `decide` の「issue に回す」はいつでも選べる（`can_issue` は常に真）。`decide` のダイジェストは版 2 のまま（`can_issue` の値が変わるので、更新前に見せた一覧は「見せた指摘が変わった」で止まり、見せ直しになる）
  - GitLab は、ccnavi の依頼コメントへの返信を指摘として数える。スレッドの代表の note が返信になり、`last_*` は system の note を除いた最後の note になる（受け入れ台帳の項目で、system の note の id を最後のコメントとして残しているものは一度合わなくなり、未解決として数え直される）
  - `github.com` に加えて GHE.com（`ghe.com`・`*.ghe.com`）を GitHub と分類する（API は `https://api.<ホスト>`。実物では未検証）。ポート付きの GHE.com の origin は止まる
  - Chrome 拡張の承認の取り下げをやめた。子は `cancel --reason` で取り消して新しい連番で出し直し、親は中身を直すなら改版、やめるなら `cancel`
  - 案内: 変更要求の案内がホストごとに分かれる、fix の resolve はユーザ、依頼のあとに HEAD が動いたときの説明、レビュー待ちの間の新しい依頼は別のセッションで、Stop の促しにユーザが止めたときの 1 文
- 旧い子（`finished_to` が無い。この更新より前に閉じた子）は、`review/` から `done/` へ手で動かしても確定できない。`confirm` か `decide` を使う。`--lint` が info として並べる
- 未完了の操作記録（`logs/state/ops/`）は、prune で消えない。`ccnavi-ticket.sh status` か `--lint` で見つけ、decide は `--resume <操作ID>` で続きを送り、request・comment・ready・close-early は同じコマンドを打ち直す
