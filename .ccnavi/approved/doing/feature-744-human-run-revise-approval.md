---
version: 1
ticket: feature-744-human-run-revise-approval
issue: 744
mr: 749
title: '[Bug] human_run が親の改版（同じ識別子）の承認を成功と数えず halt する'
phases:
  quick-work:
    kind: work
    title: 小さい作業
    review: mr
    scope: inherit
    when: 設計判断や並行作業が要らないとき
plan:
- quick-work
rationale: 'UC-20 の step 10（親の改版の承認）で human_run が承認と push を通したのに、runner が「新しく承認済みにならなかった」として
  halt した（#744）。改版は親と同じ識別子で、打つ前から承認済みなので、成否の見方が合わない。

  '
allow:
- match: Write|Edit
  glob: tests/scenarios/**
- match: Write|Edit
  glob: .agents/docs/acceptance-scenarios.md
started_at: 2026-10-31T04:35:05+0900
base_sha: b8b9c8a92acc4427928e822194712e8c22b51be0
feedback: []
---
## 目的

`human_run` が親の改版（同じ識別子の提案）の承認を成功と数え、次の step へ進めるようにする。

## 範囲

- `tests/scenarios/runner.py` の `_run_human_run` の成否の見方を直す。対象の識別子が「新しく承認済みになった」か、「打つ前から承認済みで、打ったあとに承認待ち（`todo/`）から消えた」（改版が承認された）なら成功と数える。承認待ちに残ったままなら、従来どおり halt する
- `tests/scenarios/test_runner.py` に、改版の成功と、改版が承認待ちに残った失敗のテストを足す
- `.agents/docs/acceptance-scenarios.md` の human_run の「対象」「成否」を直す（親の改版も対象で、成否の見方を書く）

## 対象外

- UC-20 など既存シナリオの yml（#734 で step 10 を手動の代行にした扱いを human_run に戻すかは、別に決める）
- 改版の中身（`feedback` の有無など）の比較。承認前後で `todo/` から消えたかだけを見る

## 完了の確かめ方

`tests/scenarios/test_runner.py` の HumanRunTest が通る（改版の成功、改版が承認待ちに残ったときの halt）。

## 計画の理由

設計判断が要らない runner の小さな修正と文書の追従なので quick-work 1 本にする。issue の直し案のうち「成否の見方を直す」側を選んだ。改版を対象外にする案では、基準が空のとき改版が選ばれてしまう矛盾が残る。
