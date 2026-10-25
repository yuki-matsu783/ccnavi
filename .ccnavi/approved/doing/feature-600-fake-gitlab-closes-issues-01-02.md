---
version: 1
ticket: feature-600-fake-gitlab-closes-issues-01-02
parent: feature-600-fake-gitlab-closes-issues
phase: 1
human_review:
  required: true
  reason: 模擬のルート追加で、ユーザのレビューを経て取り込む
title: tests/fake_gitlab.py に closes_issues ほか、sh が打つ欠けたルートを足す
rationale: |
  ROUTES の本体は tests/fake_gitlab.py にある（tests/manual/gitlab/fake_gitlab.py は起動するだけの入口）。前の子 -01-01 は allow に本体が無く、実装に入れず取り消した。
allow:
  - match: Write|Edit
    glob: "tests/fake_gitlab.py"
  - match: Write|Edit
    glob: "tests/manual/gitlab/fake_gitlab.py"
  - match: Write|Edit
    glob: "tests/sh/test_fake_gitlab.py"
  - match: Write|Edit
    glob: "tests/sh/gitlab_host.py"
---
## 目的

`tests/fake_gitlab.py` に MR の closes_issues を返すルートを足し、`ccnavi-start.sh --mr` を模擬の相手で通せるようにする。

## 範囲

- `ROUTES` に `GET /projects/:id/merge_requests/:iid/closes_issues` を足す。MR の `description` の `Closes #N`（Closes/Fixes/Resolves の活用形）から出し、無ければ空の配列
- `.ccnavi/scripts/` が打つ GitLab のルートのうち、模擬に無いものを同じ変更で足す: `GET /user`、`GET /projects/:p/members/all/:uid`、`GET /issues/:iid`（今は PUT だけ）、`GET /issues/:iid/related_merge_requests`、`GET`/`POST /issues/:iid/notes`、`POST /merge_requests/:iid/discussions/:did/notes`（今は PUT だけ）。足せないものは理由を書く
- `tests/sh/test_fake_gitlab.py` に、closes_issues が `Closes #N` 有り・無しで 200 を返す試験と、足した他のルートの 200 の試験を足す

## 対象外

- 本物の GitLab の形の記録・契約テスト（#551・#553）
- `ccnavi-start.sh` の変更
