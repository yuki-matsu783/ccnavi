---
version: 1
ticket: feature-789-new-issue-first
issue: 789
mr: 791
title: "fix: issue なしの依頼で ccnavi-start.sh --new-issue を打たず、ユーザに issue の作成を頼んで止まる"
phases:
  quick-work:
    kind: work
    title: 小さい作業
    review: true
    scope: inherit
    skill: process-implement
    when: 設計判断や並行作業が要らないとき
plan:
  - quick-work
rationale: |
  案内の変更だけで、判定や止める条件は変えない。1 フェーズで足りる。
allow:
  - match: Write|Edit
    glob: ".ccnavi/CCNAVI.md"
  - match: Write|Edit
    glob: ".ccnavi/docs/**"
  - match: Write|Edit
    glob: ".ccnavi/messages/**"
  - match: Write|Edit
    glob: "tests/**"
  - match: Write|Edit
    glob: ".ccnavi/scripts/ccnavi-start.sh"
  - match: Write|Edit
    glob: ".agents/docs/script-output.md"
  - match: Write|Edit
    glob: ".agents/docs/message-migration.md"
---
## 目的

issue なしの依頼で、エージェントが ユーザに頼む前に `ccnavi-start.sh --new-issue <タイトル>` を先に打つようにする（issue #789。Haiku が 1 度も打たず、「issue を作ってほしい」とユーザに頼んで止まった）。

原因の見立て: 案内に「作れなければユーザに作ってもらう」が、`--new-issue` を打つ前の条件なしで書かれている（`ticket-work.md`・`ticket-flow.md`・`worktree-start.md`）。さらに、非対話でも読む `CCNAVI.md` の「作業の進め方」と、入口の `--check` の出力には `--new-issue` が出てこない。

## 範囲

設計に関わる判断（承認を求める点）は次の 3 つ。

1. 文言の方針: 「ユーザに頼む」は「`--new-issue` を打って失敗した場合だけ」と読める形にする。どの案内も「先に `--new-issue`」を先に言い、ユーザへの依頼はそのあとに置く。
   - `CCNAVI.md` 作業の進め方: 「issue が無い依頼は、ユーザに頼む前に自分で `ccnavi-start.sh --new-issue <タイトル>` を打つ。ユーザに頼むのは、打って作れなかったとき（ホストに届かない・認証が無い・権限が無い）だけ」を 1 文足す。
   - `ticket-work.md`・`ticket-flow.md`・`worktree-start.md`: 「作れなければユーザに作ってもらう」を「`--new-issue` を打って作れなかったときだけ、ユーザに作ってもらう」に直す。
2. 挙動の変更 1 つ: `ccnavi-start.sh --check` の成功時（`結論: チケット作業を始められる状態です。`）に、`次:` を 1 行足す。「issue が無い依頼なら、ユーザに頼む前に `ccnavi-start.sh --new-issue <タイトル>` を打つ。issue があるなら `--issue <番号>`」。置き場は `next-step-placement.md` の選び方 3（その操作の成功の結果として次が決まる）。文は `.ccnavi/messages/next-start.yml` に `NEXT_START_CHECK_OK` として足す（`message-files.md` の手順）。`ng`・不明のときは変えない。止める条件は変えない。
3. 失敗側の文: `NEXT_START_NEW_ISSUE__GITLAB`（ユーザに issue を頼む文）は、すでに `--new-issue` を打った結果の文で、順序は正しい。文の頭に「--new-issue で作れなかったので」を足し、試したあとであることを明示する。

## 対象外

- `--new-issue` の挙動（似た issue の確認、`--similar-checked`）の変更。
- 止める条件・hook・Stop の促しの追加（Haiku が `--new-issue` を打たない事実に対する、止めて直させる仕組みは作らない。案内の順序で足りるかを見て、足りなければ別の issue）。
- `claude -p` での受入シナリオの実行（明確な指示が無いので回さない）。

## 完了の確かめ方

- `tests/sh/test_start_sh.py` に `--check` の成功時の出力に `--new-issue` の `次:` が出る（失敗時・不明時には出ない）テストを足す。
- 文面のテスト（`tests/core/test_texts_catalog.py`）が通る（`NEXT_START_CHECK_OK` の引用と `values` の一致）。
- 案内の文書のテスト: `CCNAVI.md` と上記 3 文書で「ユーザに作ってもらう」が `--new-issue` を試したあとの条件つきで書かれ、条件なしの記述が残っていないことを見る。
- 以前の記述（条件なしの「作れなければユーザに」）がリポジトリ内に残っていない（`grep` で確かめる）。

## 計画の理由

案内の文言と 1 行の追加が同じ目的で、1 つのフェーズで足りる。ユーザのレビューが要る（案内の方針の判断を含む）。
