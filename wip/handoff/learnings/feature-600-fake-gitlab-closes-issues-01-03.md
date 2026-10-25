---
ticket: feature-600-fake-gitlab-closes-issues-01-03
parent: feature-600-fake-gitlab-closes-issues
learnings: none
---

- 提案の allow は、issue に書かれたパスでなく実コードで確かめて決める。今回は入口の `tests/manual/gitlab/fake_gitlab.py` を指定し、ROUTES の本体 `tests/fake_gitlab.py` が漏れて子を 2 回取り消し、親を改版した（入口の docstring に「本体は別ファイル」とあった）。
- 承認済みの親は、作業中の子がいると範囲を変える改版を出せない。範囲を直すときは、先に未完の子を取り消す。
