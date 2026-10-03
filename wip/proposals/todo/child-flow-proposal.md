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
  - match: Write|Edit
    glob: ".ccnavi/scripts/ccnavi-push-approved.sh"
---

# エージェントが子のフローの下書きを書き、ユーザがフロー編集画面で取り込めるようにする

決めた中身は ADR-0100（`docs/adr/0100-agent-drafts-child-flow-user-imports.md`）。ここには段取りと範囲だけを書く。

## 何をなぜ変えるか

- エージェントは、頼まれたときに子のフローの下書きを `wip/proposals/flows/<子>.yml` に書く。効力は無い
  （`SubagentStart` も着手の指紋も承認の指紋も読まない）
- フロー編集画面は、下書きが在っていまのフローと中身が違えば「提案あり」を出す。lint（`--lint --json --flow`）に掛け、
  文の前後まで含む差分を見せ、ユーザが取り込むと編集中の内容に入る。保存はいつもどおり `approved/flows/<子>.yml` に書く
- 取り込んだ内容の保存が成功したら、取り込んだときと同じ中身の下書きだけを消す。消したことは `ccnavi-push-approved.sh` が
  フローの保存と一緒にコミットに入れる（消えた `todo/` の提案と同じ拾い方）
- フロー編集画面に「エージェントにフローの作成を頼む」ボタンを置く（ボード本体には置かない）。依頼文を組み、承認の文と同じ
  「コピー / 新しいセッションで開く」で渡す。出すのは着手の前だけ（作成を頼む / 直しを頼む / 頼み直す）。依頼は書き込みの許可条件にしない
- フローの中身の危険を見る検査は足さない。別のチケットで扱う

書き起こしの手間をユーザから外し、決めるのはユーザのまま残すため。

## 範囲

| 場所 | やること |
|---|---|
| `ccnavi/`（`tickets/flow.py`・`entry/` の `--explain --json`・lint） | `tickets[].flow` に下書きの置き場と有無を載せる。`briefing` は下書きを読まない（確かめるだけ）。`selfguard`・`rules.yml` の判定は変えない |
| `tests/` | 下書きへの書き込みが通る、`approved/flows/` への書き込みは今までどおり止まる、`briefing` と指紋が下書きを読まない、JSON の欄、`ccnavi-push-approved.sh` が消えた下書きを運び、追跡していない下書きや書き直された下書きに触れない |
| `vscode-extension/ccnavi-board/`（`flow-panel.ts`・`core/flow-diff.ts`・`core/flow-write.ts`・`webview/flow/`） | 「提案あり」、取り込み（lint・文の前後まで見せる差分・編集中への取り込み）、保存の成功のあとの下書きの削除（中身の照合・リンクとハードリンクは消さない）と保存のあとの知らせ、依頼のボタンと依頼文、ボタンを出す状態 |
| `.ccnavi/scripts/ccnavi-push-approved.sh`（implement） | 消えた下書き（`wip/proposals/flows/` の追跡されていて消えたファイル）も運ぶものに足す |
| `docs/`・`ccnavi.md`・`README.md`・`requirements.md` | ADR-0100 の状態、ADR-0085 の状態の行、`docs/claude/projects.md` の「子のフローは、エージェントが書かない」、設計書のフローの節、README の JSON の形 |

保護済みファイルのうち直すのは `.ccnavi/scripts/ccnavi-push-approved.sh` だけで、範囲もこの 1 本に限る。implement でエージェントが直接直す
（ユーザの判断: いまは dry-run で守りが止めないため、エージェントが直接直す。dry-run を外したあとなら staging でユーザが写す形になる）。`.ccnavi/common/`・`.claude/hooks/`・`ccnavi c1 sort` の置き場は変えない。

## 得るもの

- エージェントが段取りの案をユーザへ渡せる。ユーザは差分を読んで取り込むだけでよい
- 判定・守り・承認の点・着手中のロックは増えも緩みもしない

## 失うもの

- エージェントの文が、ユーザの取り込みを経て子への案内になる経路が増える。歯止めはユーザが差分を読むことだけ
- 「依頼されたときだけ書く」は運用で守り、判定では止めない
- フロー編集画面のボタンと表示が増える
- 範囲に保護済みの `ccnavi-push-approved.sh` が入る。dry-run を外したあとなら、ユーザが写す手間が要る

## やらない場合

案はチャットで渡り、ユーザがエディタで写し続ける。写し間違いと手間が残る。

## 代案

- 依頼の印が無ければ下書きの書き込みを止める（B）。印の置き場と判定の分岐が増え、印が許可に見える。下書きは効かないので守るものが小さい
- 下書きを `--agree` の digest に含める。下書きを直すたびに承認をやり直すことになり、判断の点が増える

## フェーズ

| # | 種類 | やること |
|---|---|---|
| 1 | design | ADR-0100 を詰める。依頼文の文面と、削除を運ぶ sh の直し方を決める |
| 2 | acceptance | 上の「範囲」の `tests/` の受入テストと、拡張のテスト |
| 3 | implement | 実行ファイルの JSON の欄、フロー編集画面の「提案あり」・取り込み・保存のあとの下書きの削除・依頼のボタン、消えた下書きも運ぶ `ccnavi-push-approved.sh` |
| 4 | docs | ADR-0085 と ADR-0100 の状態、`docs/claude/projects.md`、設計書、README |
