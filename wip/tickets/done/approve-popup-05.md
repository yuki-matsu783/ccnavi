---
version: 1
ticket: approve-popup-05
parent: approve-popup
phase: 3
predecessors: [approve-popup-03]
title: 敵対的レビューの指摘を直す（deny の穴・伝え漏れ・拡張のタイムアウト）
rationale: |
  approve-popup-03 の成果に対する敵対的レビュー（読み取り専用サブエージェント、経緯を渡さない）が
  13 件を挙げた。うち実装で直すものをここで直す。文書に回すもの（README.md / ccnavi.md）は
  approve-popup-04、設定ファイル（.claude/ccnavi/rules.yml）は利用者に依頼する。

  高い順に 4 つ。

  1. 組み込み deny の穴（実際に regex に当てて確認）。`--preview` の除外を「同じコマンドの中」で
     判断しているが、PowerShell は shellread を通らず `\x00` が無いので、後ろのコマンドに書いた
     `--preview` が前の `--approve --yes` まで免除する。同じコマンドに両方書いた
     `--approve --preview --yes a` も regex に当たらない（cli.py の排他検査が拾うので実害は無いが、
     壁が 1 枚に減っている）。
  2. 改版が伝わらない。親の改版は写しを書き換えるだけで識別子が増えないので、識別子の集合を
     比べる控えでは新しい承認として検知できない。`--yes` の `prompt` には改版の行が出るのに、
     hook は黙る。
  3. 閉じた写しが伝わらない。承認の直後・次の hook の前に子が `done` になると、控えは
     「知っている」に更新されるのに、文面の対象は開いている写しだけなので、その承認は
     二度と伝わらない。
  4. 控えが壊れていると黙って起点になる。読めない控えを「無い」と同じに扱うので、まだ伝えて
     いない承認ごと現状で上書きし、何も言わずに終わる。

  ほかに、拡張の子プロセスに期限が無く承認が固まると「承認している…」から戻れない、
  terminal.ts の見出しが承認を送る前提のまま、フィクスチャの照合が鍵の名前しか見ていない、
  judge.py の英文が「利用者が端末で --approve を打つ」のまま。

  同時に走る hook が同じ承認を 2 度伝えうる点（控えの読み書きに排他が無い）は、伝わりすぎる側
  なので直さず、そう決めたことをコメントに残す。
human_review:
  required: true
  reason: 人の合意の壁の regex を直す。緩む向きに誤ると承認を自分で出せるようになる
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: vscode-extension/*
started_at: "2026-09-12T22:02:20+0900"
completed_at: "2026-09-12T22:29:47+0900"
base_sha: "9d389fdb05dbaa32522770bafa0e0be589adfdab"
---

# 敵対的レビューの指摘を直す

## 直すもの

| # | 何 | どこ |
|---|---|---|
| F1 | PowerShell で後続のコマンドの `--preview` が `--yes` を免除する | `ccnavi/phase.py` |
| F2 | 同じコマンドに `--preview` と `--yes` を並べると regex に当たらない | `ccnavi/phase.py` |
| F3 | 親の改版が hook で伝わらない | `ccnavi/approval.py` |
| F4 | 承認の直後に閉じた写しが伝わらない | `ccnavi/approval.py` |
| F6 | 壊れた控えを「無い」と同じに扱い、伝えずに起点化する | `ccnavi/approval.py` |
| F13 | 承認の子プロセスに期限が無く、オーバーレイが戻らなくなる | `vscode-extension/.../ccnavi.ts` |
| F8 | `terminal.ts` の見出しが承認を送る前提のまま | `vscode-extension/.../terminal.ts` |
| F9 | フィクスチャの照合が鍵の名前しか見ていない | `tests/test_approve_json.py` |
| F10 | `judge.py` の英文が「端末で `--approve`」のまま | `ccnavi/judge.py` |

## 直し方

**F1・F2.** `--preview` の除外を当てにするのをやめ、`--yes` を独立した形として必ず当てる。
`_CLI_FORMS` に `--yes\b` の枝を足し（前に付く launcher の条件はそのまま効く）、`--approve` の
先読みはコマンドの区切りとして `;` `&` `|` 改行も見るようにする。これで PowerShell の生の文字列でも
区切りをまたがない。

**F3.** 控えを識別子の集合から「識別子 → 印」の対応に変える。印は写しの `approved_at` と
`revised_at`（`revise_copy` が書く）を合わせたもの。印が変われば改版として伝える。文面は
`reasons.approved` の改版の行を使う。

**F4.** 文面の対象を、開いている写しだけでなく閉じた写しからも引く。

**F6.** 控えが「無い」のと「読めない」のを分ける。読めないときは起点化せず、その回に全部を
新しい承認として伝えてから控えを書き直す。伝えすぎる側に倒す。

**F5.** 直さない。同時に走った hook が同じ承認を 2 度伝えうることを `news` のコメントに残す。

**F13.** 承認と preview の子プロセスに期限（60 秒）を付け、超えたら `error` として返す。

## 確かめること

- F1・F2 の形が deny になり、`--approve --preview --json` は通ること（PowerShell も含めて）
- 改版を承認したあとの hook が 1 度だけ伝えること
- 承認の直後に閉じた子が 1 度だけ伝わること
- 控えを壊してから hook を走らせると、まだ伝えていない承認が伝わること
- 既存の 424 件と拡張の 71 件が通ること
