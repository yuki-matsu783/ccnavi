---
version: 1
ticket: chore-757-handoff-drop-orig
issue: 757
mr: 758
title: "保護対象の下書きの控え（.orig）をやめ、取り込み時の確認を --expect のハッシュだけにする"
phases:
  quick-work:
    kind: work
    title: 小さい作業
    review: mr
    scope: inherit
    when: 設計判断や並行作業が要らないとき
plan:
  - quick-work
rationale: |
  方針（.orig をやめる）は、ユーザが決めた。調べる範囲は広い（取り込みの実体・下書きを作る sh・2 つの拡張・設計書）が、
  設計の選択肢は残っていないので、調査と設計のフェーズは分けず、子が先に依存を洗い出してから直す。
  ユーザに見える変更（--expect の形式、拡張の conflict の表示）は、MR のレビューで確かめてもらう。
allow:
  - match: Write|Edit
    glob: ".ccnavi/scripts/ccnavi-stage.sh"
  - match: Write|Edit
    glob: ".ccnavi/docs/*"
  - match: Write|Edit
    glob: "src/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "docs/21_design/*"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/*"
  - match: Write|Edit
    glob: "extensions/chrome/ccnavi-approval/*"
started_at: "2026-10-31T11:41:55+0900"
base_sha: "d849655a657ed66ed0b8474ccb6e1cc6a10ee821"
---
## 目的

保護対象のファイルを直すときの下書きの控え（`<下書き>.orig`）をやめ、取り込み時の確認を `--expect` のハッシュだけにする。

`.orig` は、下書きを作ってから取り込むまでの間に、取り込み先が他の人に直されていないか（`conflict`）を確かめるためにだけ使っている。この確認は助言で、本来の歯止めは `--expect`（取り込み先の今のハッシュを含む）。他のセッションの変更で退行しても、git の履歴で戻せる。一方 `.orig` が `wip/handoff/` に残ると、追跡されないファイルとして `integrate` と後片付けを止める。

## 範囲

- `ccnavi-stage.sh`: `.orig` を作る処理と、`--commands` の「下書きを作った後に元が変わった」の判定をなくす
- `handoff.py`・`cli_handoff.py`: `.orig` を読まない。`conflict` の判定をなくす。`--expect` のハッシュから `.orig` の項を外す。取り込んだあとの `.orig` を消す処理もなくす
- VS Code 拡張（ccnavi-board）と Chrome 拡張（ccnavi-approval）の、`conflict` の表示と処理
- 設計書（`docs/21_design/tickets/handoff.md`）と `.ccnavi/docs/` の `.orig`・`conflict` の記述。設計書に、`.orig` をやめた理由を書く
- 上の各モジュールのテスト（最低限。変えた範囲だけ回す）
- 子は、最初に `.orig`・`conflict` を使っている箇所を洗い出し、結果を子の MR の説明に書く

## 対象外

- ADR 0144（履歴なので直さない）
- 今ある `.orig` を消す処理（読まれなくなるだけ。残ったものは手で消す。後方互換は考えない）
- `--expect` の、取り込み先のハッシュまわりの強化（今のまま）

## 完了の確かめ方

- `.orig` を作る・読む・消す処理がリポジトリに残っていない（grep で確かめる）
- 下書きを作って取り込む一連の流れが通る（変えた範囲のテスト）
- 保護対象の `.ccnavi/scripts/` と `.ccnavi/docs/` は、エージェントが直接書けない。ガードが有効なときは、下書きでユーザに取り込みを頼む

## 計画の理由

方針が決まっていて設計の選択肢が残っていないので、`quick-work`（1 ステップ）にする。調査から文書まで通す大きな計画にすると、レビューの待ちが増えるわりに、決める内容がない。
