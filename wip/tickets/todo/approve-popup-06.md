---
version: 1
ticket: approve-popup-06
parent: approve-popup
phase: 5
predecessors: [approve-popup-04]
title: 設計文書を実装に合わせる（承認の JSON と組み込み deny）
rationale: |
  wip/design/approve-popup.md は、このチケットの設計フェーズの成果物で、実装の参照先になる。
  実装の途中で 2 か所が食い違ったまま残っている。どちらもフェーズ 3 の依頼文で申告し、
  レビューで受け入れられたが、文書の側は直っていない。次にここを読む人は、いま無い形の
  規則と JSON を読むことになる。

  1. §2.2 の JSON の例が `cleared_marks` のまま。実装は `lines`（端末なら出ていた行）を返す。
     フィクスチャも 2 つと書いてあるが、実際は preview / yes / mismatch の 3 つ。
  2. §2.3 の組み込み deny が、穴のある形のまま書いてある。設計は
     `--approve\b(?![^\x00]*--preview\b)` の 1 本だったが、これは PowerShell で後続のコマンドに
     書いた `--preview` が前の `--yes` を免除し、同じコマンドに両方書いても免除された。
     実装は `--yes` を独立した枝にし、免除の区切りに `;` `&` `|` 改行を足した形。
  3. §2.4 の控えが「識別子の集合」のまま。実装は「識別子 → 印（approved_at と revised_at）」で、
     閉じた写しも見て、壊れた控えは伝える側へ倒す。
  4. §5 の受入テストの一覧に、敵対的レビューで足した分（改版・閉じた写し・壊れた控え・
     PowerShell の形）が無い。
human_review:
  required: true
  reason: 次の変更が参照する設計の記述を直す
allow:
- match: Write|Edit
  glob: wip/design/*
started_at: ''
completed_at: ''
base_sha: ''
---

# 設計文書を実装に合わせる

## 直すもの

| 節 | いま書いてあること | 実装 |
|---|---|---|
| §2.2 | `--yes` の JSON に `cleared_marks` | `lines` |
| §2.2 | フィクスチャは preview / yes の 2 つ | preview / yes / mismatch の 3 つ |
| §2.3 | deny は `--approve` の先読み 1 本 | `--yes` を独立した枝にし、免除の区切りに `;` `&` `|` 改行 |
| §2.4 | 控えは識別子の集合 | 識別子 → 印。閉じた写しも見る。壊れた控えは伝える側へ |
| §5 | 受入テスト A1〜A14 | 改版・閉じた写し・壊れた控え・PowerShell の形を足した |

## 書き足すもの

- §2.3 に「免除は許さない側から書く」の理由（許す側から書くと、条件を 1 つ足すたびに
  免除が広がる）。ここは実際に穴を作った場所なので、なぜその形にしたかを残す
- 設計と実装が食い違ったまま進んだこと自体は、フェーズ 3 の依頼文で申告して受け入れられた。
  その経緯は HANDOVER に書いたので、設計文書には結果だけを書く
