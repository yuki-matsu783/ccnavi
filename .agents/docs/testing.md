---
type: guide
title: ccnaviのPythonのテストを回す
description: ccnaviのPythonのテストを回すときの決まり。全件を1本で回さず、変えたモジュールから絞って広げる。時間の実測と、出力が出ないときの扱い
tags: [process-test]
keywords: [テスト, 代役, STUB, 操作記録, 全件, discover, グループ, モジュール, python -m tests, 時間切れ, 同時実行, 実測, 重い]
---

# ccnaviのPythonのテストを回す

Go のテストの回し方は[実行ファイルをGoに書き直す](go-rewrite.md)の「回す」にある。

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

### 回す単位は、変えたモジュールまで絞る

グループ（`tests/core`・`tests/ticket`・`tests/guard`など）でも、数十分かかることがある。最初は、変えたファイルのテストのモジュールを名指しする（`uv run python -m unittest tests.sh.test_stage_sh`のように）。通ったら、近いモジュール、グループの順に広げる。広げる目安は、hook の判定や共通の関数のように、変えた動きが呼び出し元の多い所に及ぶとき。

大量のテストは、本物の git のリポジトリを一時的に作り、本物のシェルを起動して確かめる。1本ごとに数秒かかるものがあり、数が増えると合計が伸びる。この理由は観察からの推測で、確かめてはいない。

時間の実測（機械で変わる）:

| 回したもの | 本数 | 時間 |
|---|---|---|
| `tests.sh.test_stage_sh` | 22 | 約 20 秒 |
| `tests.guard`の関連 3 モジュール（`test_handoff_copy`・`test_wrapguard`・`test_selfguard`） | 184 | 約 66 秒 |
| `tests/core`・`ticket`・`guard`を同時に | — | 30 分たっても出力が出ず、打ち切った（遅いのか止まったのかは未確認） |
| `tests.sh.test_write_and_push_merge` | — | 長く止まり、打ち切った |

出力が長く出ないときは、待ち続けない。10 分を目安に止めて、範囲を狭めてやり直す。出力が無いままでは、通ったのか止まったのか分からない。通ったと扱わない（下の「待つとき」と同じ）。変更が大きいときや、取り込む前は、関係しそうな範囲を広めに選ぶ。絞ると、関係ないと思った場所への影響を見落とす。

`tests/fake_gitlab.py`（GitLab の模擬）、`tests/sh/gitlab_host.py` とその見本（`extensions/chrome/ccnavi-approval/test/fixtures/host/gitlab/`）の応答を変えたら、`contract`（`uv run python -m tests tests/contract`。約 15 秒）を必ず回す。本物から録った応答の形と比べる試験で、模擬が本物とずれると落ちる。落ちたら本物に合わせて模擬を直す。仕様と録り直しは [gitlab-contract.md](gitlab-contract.md)。

`tests/contract/shape.py` と、`tests/contract/gitlab/` の `normalize-cases.json`・`requests.json`・`recorded.json` を直したら、`extensions/chrome/ccnavi-approval` の `pnpm test` も回す（TS の比較器が同じ表を読む）。Chrome 拡張の `MockGitLab` を直したときも `pnpm test` を回す。拡張だけの要求（`used_by` が `["chrome"]`）は `python -m tests tests/contract` では `fake_gitlab.py` に送らず、`pnpm test` でだけ `MockGitLab` と比べる。`python -m tests tests/contract` は TS を回さないので、食い違いは `pnpm test` を回すまで見つからない。

## sh のテストは通しの確認だけにする

sh のテスト 1 本の時間は、sh を 1 回走らせる時間でほぼ決まる。観点ごとに 1 本書くと、同じ実行を観点の数だけ繰り返す。判断と解釈は実行ファイルへ移して Go の表駆動のテストで確かめ、sh のテストは経路ごとに数本にする。1 回の実行で、終了コード・出力・作られた物・ホストへの要求をまとめて見る。どこまでを実行ファイルへ移すかは[実行ファイルの境界](exe-boundary.md)の「sh に残すものと、実行ファイルへ寄せるもの」にある。

## sh の試験の実行ファイルの代役

sh を回す試験は、実行ファイルの代わりに小さな sh（代役、`STUB`）を `CCNAVI_ENTRY_PATH` に置く。sh は実行ファイルの答えに依存するので、sh に新しい要求が足されたら、代役も追従させる。追従が漏れると、代役が黙って空の答えを返し、sh が「操作記録を作れなかった」などで止まる。

- `ops …` は本物の `ccnavi ops`（`tests/exe.py` が組んだ実行ファイル。sh には `exe.sh_path()` を書く）へ渡す。sh は操作記録（`ops begin`・`show`・`stage-set`・`reconcile`・`applied`）を必須で使う
- 受け入れ計画（`--accept-plan` の JSON）は `op`（操作ID）を含む。代役が書く計画には、本物の `oplog.Begin` で作った op を入れる
- 事前検査（`--check`）は、取り込みより先に sh が呼ぶ。代役の `--check` が副作用（コミットを積むなど）を持たないようにする
- 文面ファイルを読む処理を試すための使い捨てのワークスペースには、`tests.put_messages(root)` で文面を置く
- 子プロセスで `git merge`・`git commit` を呼ぶときは、`-c user.email=… -c user.name=…` を付ける。付けないと、識別子の無い環境で衝突とは別の理由で失敗する

見本は `tests/sh/decide_support.py`（`STUB`・`BEGIN`）。

## 待つとき

長く走るテストは、バックグラウンドで走らせて終了を待つ。フォアグラウンドで`sleep`を繰り返さない。
途中で打ち切られたときは、通ったとは扱わない。分けて回し直す。
