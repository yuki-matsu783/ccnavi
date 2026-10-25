---
version: 1
ticket: feature-600-fake-gitlab-closes-issues-01-01
parent: feature-600-fake-gitlab-closes-issues
phase: 1
human_review:
  required: true
  reason: 模擬のルート追加で、ユーザのレビューを経て取り込む
title: fake_gitlab.py に closes_issues のルートを足す
rationale: |
  fake-gitlab の模擬に closes_issues のルートが無く、受入シナリオで --mr を回すと 404 で止まるため。
allow:
  - match: Write|Edit
    glob: "tests/manual/gitlab/fake_gitlab.py"
  - match: Write|Edit
    glob: "tests/sh/test_fake_gitlab.py"
  - match: Write|Edit
    glob: "tests/sh/gitlab_host.py"
started_at: "2026-10-25T19:44:41+0900"
base_sha: "e3fe96dcdb0e6a1d5a251435591a75b997422fdc"
---
## 目的

`fake_gitlab.py` に MR の closes_issues を返すルートを足し、`ccnavi-start.sh --mr` を模擬の相手で通せるようにする。

## 範囲

- `ROUTES` に `GET /projects/:id/merge_requests/:iid/closes_issues` を足す。MR の本文の `Closes #N` から出し、無ければ空の配列
- sh が打つ GitLab のルートを洗い出し、`ROUTES` と突き合わせる。無いものは足すか、足せない理由を書く
- `tests/sh/test_fake_gitlab.py` に 200 を返す試験を足す

## 対象外

- 本物の GitLab の形の記録・契約テスト（#551・#553）
- `ccnavi-start.sh` の変更
