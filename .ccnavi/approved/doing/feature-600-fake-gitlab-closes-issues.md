---
version: 1
ticket: feature-600-fake-gitlab-closes-issues
issue: 600
mr: 605
title: fake_gitlab.py に MR の closes_issues が無く、模擬の相手で ccnavi-start.sh --mr が 404 で止まる
phases:
  quick-work:
    kind: work
    title: 小さい作業
    review: mr
    scope: inherit
    when: 設計判断や並行作業が要らないとき
plan:
- quick-work
rationale: 'fake-gitlab の模擬に closes_issues のルートが無く、受入シナリオで --mr を回すと 404 で止まるため。

  '
allow:
- match: Write|Edit
  glob: tests/fake_gitlab.py
- match: Write|Edit
  glob: tests/manual/gitlab/fake_gitlab.py
- match: Write|Edit
  glob: tests/sh/test_fake_gitlab.py
- match: Write|Edit
  glob: tests/sh/gitlab_host.py
started_at: 2026-10-25T19:42:49+0900
base_sha: 8d9494afd295a1be0dee15f532e971ab7150233c
feedback: []
---
## 目的

`fake_gitlab.py` に MR の closes_issues を返すルートを足し、`ccnavi-start.sh --mr` を模擬の相手で通せるようにする。

## 範囲

- `ROUTES` に closes_issues を足す。MR の本文の `Closes #N` から出し、無ければ空の配列
- sh が打つ GitLab のルートを grep で洗い出し、`ROUTES` と突き合わせる。無いものは同じ PR で足すか、足せない理由を書く
- `tests/sh/test_fake_gitlab.py` に 200 を返す試験を足す

## 対象外

- 本物の GitLab の形の記録・契約テスト（#551・#553）
- `ccnavi-start.sh` の変更

## 完了の確かめ方

- `tests/sh/test_fake_gitlab.py` が通る
- `setup.host: fake-gitlab` で `ccnavi-start.sh --mr 1 --dry-run` が 404 で止まらない

## 計画の理由

模擬のルート追加だけで設計判断が無いため quick-work。
