---
type: guide
title: ccnaviのPythonのテストを回す
description: ccnaviのPythonのテストを回すときの決まり。全件を1本で回さず、変えたモジュールから絞って広げる。時間の実測と、出力が出ないときの扱い
tags: [process-test]
keywords: [テスト, 全件, discover, グループ, モジュール, python -m tests, 時間切れ, 同時実行, 実測, 重い]
---

# ccnaviのPythonのテストを回す

## 全件を1本で回さない

全件を`uv run python -m unittest discover -s tests -t .`の1本で回さない。
数十分かかり、セッションの時間の上限（30分など）で途中で打ち切られる。打ち切られると終了コードも失敗の一覧も残らないので、通ったかどうか分からない。

次のどちらかで回す。

- グループに分けて回す。グループごとに別のプロセスで同時に走らせ、それぞれの終了コードをファイルに残す
- `uv run python -m tests`を使う。モジュールごとに別プロセスで同時に回す。中身は`discover`と同じ

```sh
uv run python -m tests                 # 全件（同時実行）
uv run python -m tests tests/ticket    # グループを名指し（複数書ける）
uv run python -m tests --plan          # 何をどの順で回すかだけ出す
```

## どのグループを回すか

変えたファイルから回すグループを決める。表と時間の目安は`.claude/skills/commit/references/test-groups.md`にある。
表に該当しない変更や、統合先に取り込む前・MRに出す前は、全件を回す。

### 回す単位は、変えたモジュールまで絞る

グループ（`tests/core`・`tests/ticket`・`tests/guard`など）でも、数十分かかることがある。最初は、変えたファイルのテストのモジュールを名指しする（`uv run python -m unittest tests.sh.test_stage_sh`のように）。通ったら、近いモジュール、グループの順に広げる。広げる目安は、hook の判定や共通の関数のように、変えた動きが呼び出し元の多い所に及ぶとき。

大量のテストは、本物の git のリポジトリを一時的に作り、本物のシェルを起動して確かめる。1本ごとに数秒かかるものがあり、数が増えると合計が伸びる。この理由は観察からの推測で、確かめてはいない。

時間の実測（2026-10。機械で変わる）:

| 回したもの | 本数 | 時間 |
|---|---|---|
| `tests.sh.test_stage_sh` | 22 | 約 20 秒 |
| `tests.guard`の関連 3 モジュール（`test_handoff_copy`・`test_wrapguard`・`test_selfguard`） | 184 | 約 66 秒 |
| `tests/core`・`ticket`・`guard`を同時に | — | 30 分たっても出力が出ず、打ち切った（遅いのか止まったのかは未確認） |
| `tests.sh.test_write_and_push_merge` | — | 長く止まり、打ち切った |

出力が長く出ないときは、待ち続けない。10 分を目安に止めて、範囲を狭めてやり直す。出力が無いままでは、通ったのか止まったのか分からない。通ったと扱わない（下の「待つとき」と同じ）。変更が大きいときや、取り込む前は、関係しそうな範囲を広めに選ぶ。絞ると、関係ないと思った場所への影響を見落とす。

`tests/fake_gitlab.py`（GitLab の模擬）、`tests/sh/gitlab_host.py` とその見本（`extensions/chrome/ccnavi-approval/test/fixtures/host/gitlab/`）の応答を変えたら、`contract`（`uv run python -m tests tests/contract`。約 15 秒）を必ず回す。本物から録った応答の形と比べる試験で、模擬が本物とずれると落ちる。落ちたら本物に合わせて模擬を直す。仕様と録り直しは [gitlab-contract.md](gitlab-contract.md)。

`tests/contract/shape.py`・`normalize-cases.json`・`requests.json`・`recorded.json` を直したら、`extensions/chrome/ccnavi-approval` の `pnpm test` も回す（TS の比較器が同じ表を読む）。Chrome 拡張の `MockGitLab` を直したときも `pnpm test` を回す。拡張だけの要求（`used_by` が `["chrome"]`）は `python -m tests tests/contract` では `fake_gitlab.py` に送らず、`pnpm test` でだけ `MockGitLab` と比べる。`python -m tests tests/contract` は TS を回さないので、食い違いは `pnpm test` を回すまで見つからない。CI で `pnpm` が回るかは未確認（`.github/workflows/` に記述がない）。

## 待つとき

長く走るテストは、バックグラウンドで走らせて終了を待つ。フォアグラウンドで`sleep`を繰り返さない。
途中で打ち切られたときは、通ったとは扱わない。分けて回し直す。
