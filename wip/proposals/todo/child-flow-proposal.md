---
version: 1
ticket: child-flow-proposal
human_review:
  required: true
  reason: エージェントの書いた文がユーザの取り込みを経て子への案内になる経路を足し、フロー編集画面の操作が変わるため
plan: [design, acceptance, implement, docs]
feedback: [design-feedback, implement-feedback]
title: エージェントが子のフローの下書きを書き、ユーザがフロー編集画面で取り込めるようにする
rationale: |
  子のフローはユーザがフロー編集画面で書き、エージェントには書かせない（ADR-0085）。
  エージェントは段取りの案を作れるのに渡す置き場が無く、ユーザがチャットの案をエディタで写している。
  効力の無い下書きを wip/proposals/flows/<子>.yml に書けるようにし、ユーザが差分を読んで取り込む形にする
  （ADR-0100）。判定・守り・承認・着手中のロックは変えない。
allow:
  - match: Write|Edit
    glob: "ccnavi/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "vscode-extension/*"
  - match: Write|Edit
    glob: "wip/design/*"
  - match: Write|Edit
    glob: "docs/*"
  - match: Write|Edit
    glob: "ccnavi.md"
  - match: Write|Edit
    glob: "README.md"
  - match: Write|Edit
    glob: "requirements.md"
---

# エージェントが子のフローの下書きを書き、ユーザがフロー編集画面で取り込めるようにする

決めた中身は ADR-0100（`docs/adr/0100-agent-drafts-child-flow-user-imports.md`）。ここには段取りと範囲だけを書く。

## 何をなぜ変えるか

- エージェントは、頼まれたときに子のフローの下書きを `wip/proposals/flows/<子>.yml` に書く。効力は無い
  （`SubagentStart` も着手の指紋も承認の指紋も読まない）
- フロー編集画面は、下書きが在っていまのフローと中身が違えば「提案あり」を出す。lint（`--lint --json --flow`）に掛け、
  文の前後まで含む差分を見せ、ユーザが取り込むと編集中の内容に入る。保存はいつもどおり `approved/flows/<子>.yml` に書く
- フロー編集画面に「エージェントにフローの作成を頼む」ボタンを置く。依頼文を組み、承認の文と同じ「コピー / 新しいセッションで開く」で渡す。
  依頼は書き込みの許可条件にしない

書き起こしの手間をユーザから外し、決めるのはユーザのまま残すため。

## 範囲

| 場所 | やること |
|---|---|
| `ccnavi/`（`tickets/flow.py`・`entry/` の `--explain --json`・lint） | `tickets[].flow` に下書きの置き場と有無を載せる。`briefing` は下書きを読まない（確かめるだけ）。`selfguard`・`rules.yml` の判定は変えない |
| `tests/` | 下書きへの書き込みが通る、`approved/flows/` への書き込みは今までどおり止まる、`briefing` と指紋が下書きを読まない、JSON の欄 |
| `vscode-extension/ccnavi-board/`（`flow-panel.ts`・`core/flow-diff.ts`・`webview/flow/`） | 「提案あり」、取り込み（lint・文の前後まで見せる差分・編集中への取り込み）、依頼のボタンと依頼文、ボタンを出す状態 |
| `docs/`・`ccnavi.md`・`README.md`・`requirements.md` | ADR-0100 の状態、ADR-0085 の状態の行、`docs/claude/projects.md` の「子のフローは、エージェントが書かない」、設計書のフローの節、README の JSON の形 |

保護済みファイル（`.ccnavi/scripts/`・`.ccnavi/common/`・`.claude/hooks/`）は直さない見込み。直す必要が出たら staging を足す改版を出す。

## 得るもの

- エージェントが段取りの案をユーザへ渡せる。ユーザは差分を読んで取り込むだけでよい
- 判定・守り・承認の点・着手中のロックは増えも緩みもしない

## 失うもの

- エージェントの文が、ユーザの取り込みを経て子への案内になる経路が増える。歯止めはユーザが差分を読むことだけ
- 「依頼されたときだけ書く」は運用で守り、判定では止めない
- フロー編集画面のボタンと表示が増える

## やらない場合

案はチャットで渡り、ユーザがエディタで写し続ける。写し間違いと手間が残る。

## 代案

- 依頼の印が無ければ下書きの書き込みを止める（B）。印の置き場と判定の分岐が増え、印が許可に見える。下書きは効かないので守るものが小さい
- 下書きを `--agree` の digest に含める。下書きを直すたびに承認をやり直すことになり、判断の点が増える

## フェーズ

| # | 種類 | やること |
|---|---|---|
| 1 | design | ADR-0100 の下書きを詰める（相談したいことへの答えを入れる）。依頼文の文面とボタンを出す状態を決める |
| 2 | acceptance | 上の「範囲」の `tests/` の受入テストと、拡張のテスト |
| 3 | implement | 実行ファイルの JSON の欄、フロー編集画面の「提案あり」・取り込み・依頼のボタン |
| 4 | docs | ADR-0085 と ADR-0100 の状態、`docs/claude/projects.md`、設計書、README |
