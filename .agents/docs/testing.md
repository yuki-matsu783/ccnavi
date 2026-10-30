---
type: guide
title: ccnaviのPythonのテストを回す
description: ccnaviのPythonのテストを全件回すときの決まり。1本で回さず、グループに分けるか同時実行の入口を使う
tags: [process-test]
keywords: [テスト, 全件, discover, グループ, python -m tests, 時間切れ, 同時実行]
---

# ccnaviのPythonのテストを回す

## 全件を1本で回さない

全件を`uv run python -m unittest discover -s tests -t .`の1本で回さない。
直列では長くかかり、セッションの時間の上限で途中で打ち切られることがある。打ち切られると終了コードも失敗の一覧も残らないので、通ったかどうか分からない。

次のどちらかで回す。

- グループに分けて回す。グループごとに別のプロセスで同時に走らせ、それぞれの終了コードをファイルに残す
- `uv run python -m tests`を使う。モジュールごとに別プロセスで同時に回す。中身は`discover`と同じ

```sh
uv run python -m tests                 # 全件（同時実行）
uv run python -m tests tests/ticket    # グループを名指し（複数書ける）
uv run python -m tests --plan          # 何をどの順で回すかだけ出す
```

## どのグループを回すか

変えたファイルから回すグループを決める。表は`.claude/skills/commit/references/test-groups.md`にある。
表に該当しない変更や、統合先に取り込む前・MRに出す前は、全件を回す。

`tests/fake_gitlab.py`（GitLab の模擬）、`tests/sh/gitlab_host.py` とその見本（`extensions/chrome/ccnavi-approval/test/fixtures/host/gitlab/`）の応答を変えたら、`contract`（`uv run python -m tests tests/contract`）を必ず回す。本物から録った応答の形と比べる試験で、模擬が本物とずれると落ちる。落ちたら本物に合わせて模擬を直す。仕様と録り直しは [gitlab-contract.md](gitlab-contract.md)。

`tests/contract/shape.py` と、`tests/contract/gitlab/` の `normalize-cases.json`・`requests.json`・`recorded.json` を直したら、`extensions/chrome/ccnavi-approval` の `pnpm test` も回す（TS の比較器が同じ表を読む）。Chrome 拡張の `MockGitLab` を直したときも `pnpm test` を回す。拡張だけの要求（`used_by` が `["chrome"]`）は `python -m tests tests/contract` では `fake_gitlab.py` に送らず、`pnpm test` でだけ `MockGitLab` と比べる。`python -m tests tests/contract` は TS を回さないので、食い違いは `pnpm test` を回すまで見つからない。

## 待つとき

長く走るテストは、バックグラウンドで走らせて終了を待つ。フォアグラウンドで`sleep`を繰り返さない。
途中で打ち切られたときは、通ったとは扱わない。分けて回し直す。
