---
title: 手動の GitLab（Docker）の立て方と使い方
type: guide
description: Docker で本物の GitLab CE を起動・停止・確認する手順、版の固定、トークンの扱い、probe_gitlab.py で 1 周する手順、CE の限界
tags: [testing]
keywords: [GitLab, Docker, docker_gitlab, probe_gitlab, make_gitlab_tokens, compose, CE, EE, 手動, トークン, 版]
---

# 手動の GitLab（Docker）の立て方と使い方

本物の GitLab を立てて、ccnavi の sh と実行ファイルを 1 周させる手動の道具。自動テストは呼ばない。
道具は `tests/manual/gitlab/` にある。

| ファイル | 役割 |
|---|---|
| `docker_gitlab.py` | 起動（`up`）・確認（`status`）・停止（`down`）の入口。版の定数 `GITLAB_IMAGE` を持つ |
| `compose.yml` | GitLab CE の立て方（ポート・メモリ・ボリューム・`external_url`）。`docker_gitlab.py` が環境変数を渡して使う。直接 `compose` を打たない |
| `make_gitlab_tokens.rb` | root（エージェント役）と `ccnavi-reviewer`（人間役）の personal access token を作る。`docker_gitlab.py up` が呼ぶ |
| `gitlab_api.py` | 人間役の API 呼び出し。import しても副作用は無い |
| `probe_gitlab.py` | 動いている GitLab に使い捨てのプロジェクトを作り、親の Draft MR を API で先に作ってから、sh と実行ファイルを 1 周させる |
| `scenario_tools.py` | シナリオ（`setup.host: docker-gitlab`）の人間の操作の step で止めたときの操作者の道具。`issue`（人間役で issue を作る）・`agree`（代役の git の環境の中で、擬似 tty から承認を代行する）・`leak`（トークンの値が記録に出ていないかを、ファイル名だけで報告する）。ワークツリーから使うときは `--root` にワークスペースルートを渡す |
| `fake_gitlab.py` | Docker が無い環境で使う代役の GitLab |

## 起動・確認・停止

```sh
uv run python tests/manual/gitlab/docker_gitlab.py up --pull   # 初回。イメージ（約 4GB）を取る
uv run python tests/manual/gitlab/docker_gitlab.py status      # 状態・版・トークンの有効性
uv run python tests/manual/gitlab/docker_gitlab.py down        # 止める。ボリュームは残す
uv run python tests/manual/gitlab/docker_gitlab.py down --purge  # ボリュームと状態のファイルも消す
```

- `up` は、イメージが無いとき取得せずに止まる。取得は `--pull` を付けたときだけ。
- `up` は、動いていれば起動しない。健康になり、`/api/v4/version` が応答するまで待つ（上限 15 分）。起動には数分かかる（イメージを初めて取る時間は含まない）。
- CE は 4GB のメモリが要る。コンテナの上限は余裕をとって 6g（`MEM_LIMIT`）。Docker に割り当てたメモリが 6GB 未満なら、`up` が `次:` で警告する。足りないと engine ごと落ちる。
- 公開ポートは `127.0.0.1` の `CCNAVI_GITLAB_PORT`（既定 8929）だけ。LAN には公開しない。`probe_gitlab.py` の既定の接続先と `external_url` も、この値から作る。`external_url` と公開ポートは同じ値にする（ずれると clone URL と `web_url` が壊れる）。
- `restart: "no"`。Docker Desktop の起動時に自動では起動しないようにしている。
- root のパスワードは、コンテナもボリュームも無いときだけ乱数で作り、0600 の状態のファイルに書く。ボリュームが残っていれば作り直さない（GitLab は初回のパスワードを持っているため）。`down --purge` のあと作り直す。

## 版の固定

版は `docker_gitlab.py` の `GITLAB_IMAGE` の 1 か所で、ダイジェストで固定する。ここが版の正で、ほかの文書に版の数字を写さない（契約テストの記録 `recorded.json` は、録った版を `gitlab_version` に持つ）。
タグ（`<版>-ce.0` など）と `latest` は使わない。版を上げるときは、この値を書き換えて `up --pull` を打つ。

## ほかの作業が使っているとき

同じ GitLab を、ほかのセッションの probe・受入シナリオ・契約の録り直しが使っているかもしれない。動いていなければ `up` で起こすが、`down`・`up --rotate`・`docker restart` は打たない（実行中の作業のトークンと接続が切れる）。

## トークン

- `up` は、有効なトークンがあれば作り直さない。作り直すと、既存のトークンが失効するため。
- `up --rotate` のときだけ作り直す。既存は失効する。
- トークンは `~/.cache/ccnavi/gitlab/tokens.env`（`XDG_CACHE_HOME` があればその下）に 0600 で書く。リポジトリの外。端末には出さない。
- 使うときは、`set -a; . <status が出すパス>; set +a` で環境変数に載せる。受入シナリオの実行器と契約の録り直しは、回すたびにこのファイルから読む。
- 同じ uid のエージェントはこのファイルを読め、root のトークンで管理者 API から人間役のトークンを作れる。人間役の独立は保証しない。
- トークンの有効期限は 7 日。期限が切れたら、`up` が失効（401）を見て自動で作り直す。`--rotate` は有効なものも作り直したいときだけ。

## probe_gitlab.py で 1 周する

```sh
set -a; . ~/.cache/ccnavi/gitlab/tokens.env; set +a
uv run python tests/manual/gitlab/probe_gitlab.py
```

- 必要なもの: 組み立て済みの exe（`dist/ccnavi/ccnavi`。無いと `_exe()` が止まる。`CCNAVI_ENTRY_PATH` で差し替え。ワークツリーで回すときは、組み立て済みのある場所を絶対パスで渡す）、jq と curl。
- トークンは `set -a; . <status が出すパス>; set +a` で環境変数に載せてから回す（上のコマンド）。
- `CCNAVI_PROBE_GITLAB`（既定 `http://127.0.0.1:<CCNAVI_GITLAB_PORT、既定 8929>`）で接続先を変える。
- `CCNAVI_PROBE_PREFIX`（既定 `ccnavi-probe`）で、作るプロジェクト名の前置きを変える。名前は `<前置き>-<日時>`。並行して別の作業が同じ GitLab を使うとき、作業の識別（例 `p614`）を付けて区別する。
- 結果と生の JSON は `CCNAVI_PROBE_OUT`（既定は一時ディレクトリ）に残る。
- probe はユーザの役として、承認（`ccnavi-user-agree.sh` に当たる `user agree`）・承認の push（`ccnavi-user-push-approved.sh`）・受け入れ（`ccnavi-user-decide.sh`）・早めに閉じる（`ccnavi-user-close-early.sh`）を使い捨てのリポジトリで実行する。子は finish の前に `ticket learn` で学びの記録を書く。
- 親は `plan` だけで作るので、閉じる前に、`ccnavi ticket revise <親> --feedback` で `feedback: []` の改版の雛形を作って出し、承認を受ける（probe は雛形を使わず、承認済みの親の全文に `feedback: []` を足して、承認のあとに ccnavi が書く欄を空にして出す）。
- 受け入れ（decide）は、別の親 `i0003` で通す。未解決の指摘が残る親は `confirm` が止まる → `ccnavi-review.sh decide <N> --preview` が JSON（`threads`・`digest`）を返す → `--preview` なしの直呼びは rc 2 で断られる → ボードの形（`--choices` と `--digest`）で「対応しない」を置く → スレッドが解決済みになって返信が付き、`reviewed` が置かれる → 同じフェーズへの `confirm` は「レビュー済み」で拒まれる。判定は GitLab の側（スレッドの `resolved` と note の数）とローカルの置き場で見る。
- ready のあとは、MR の Draft が外れて squash が立つこと、ready の note があること、GitLab の親のブランチに `wip/` が無いこと、閉じた親子がワークスペースの `logs/archive/` へ移ったことを見る（`logs/archive/` は git の外にあり、GitLab には無い）。`wip/` に未追跡ファイルがあると ready が止まり、MR は Draft のままであることも、先に確かめる。
- 取り消した子は、置き場でなく欄（`cancelled_at`）で表す。早めに閉じた親では、未着手の子が `.ccnavi/approved/done/` に移って `cancelled_at` が入ることを見る（`cancelled/` という置き場は無い）。
- 終わりに、この実行が作ったプロジェクトだけを `DELETE` で消す（他の作業のものは触らない）。GitLab の削除は非同期で、応答は 202、直後の `GET` は 200 で `marked_for_deletion_on` が入る（本当に消えるまで待たない）。調べるために残したいときは `CCNAVI_PROBE_KEEP=1`。
- まとめは `PASS n / SKIP m / FAIL k`。SKIP は確かめられなかった段（CE の変更要求）で、PASS に数えない。FAIL があれば終了コードは 1。
- `--help` は、トークンも exe も無くても終わる。

## CE の限界

- 変更要求（`request_changes`）は GitLab EE の機能。CE では 404 になる。probe は 404 のとき、その段を SKIP とする（PASS に数えない）。EE では出して、続く「変更要求が立っていると confirm は止まる」段も通す。
  この 404 は、実機の CE（版は `GITLAB_IMAGE`）で probe を回して確かめた（`request_changes` が `status=404`）。ほかの版では未確認。

## 手動の通し確認

手動の確認は、ユーザに確認を取ってから行う（コンテナの起動・プロジェクトの作成と削除を伴う）。
順番は `up --pull` → `status` → `probe_gitlab.py` を 1 周 → `down`。結果は PR の説明に書く。
