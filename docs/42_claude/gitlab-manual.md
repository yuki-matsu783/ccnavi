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
| `probe_gitlab.py` | 動いている GitLab に使い捨てのプロジェクトを作り、sh と実行ファイルを 1 周させる |
| `fake_gitlab.py` | Docker が無い環境で使う代役の GitLab |

## 起動・確認・停止

```sh
uv run python tests/manual/gitlab/docker_gitlab.py up --pull   # 初回。イメージ（約 4GB）を取る
uv run python tests/manual/gitlab/docker_gitlab.py status      # 状態・版・トークンの有効性
uv run python tests/manual/gitlab/docker_gitlab.py down        # 止める。ボリュームは残す
uv run python tests/manual/gitlab/docker_gitlab.py down --purge  # ボリュームと状態のファイルも消す
```

- `up` は、イメージが無いとき取得せずに止まる。取得は `--pull` を付けたときだけ。
- `up` は、動いていれば起動しない。健康になり、`/api/v4/version` が応答するまで待つ（上限 15 分。初回は 5〜10 分かかる）。
- CE は 4GB のメモリが要る。コンテナの上限は余裕をとって 6g（`MEM_LIMIT`）。Docker に割り当てたメモリが 6GB 未満なら、`up` が `次:` で警告する。足りないと engine ごと落ちる。
- 公開ポートは `127.0.0.1` の `CCNAVI_GITLAB_PORT`（既定 8929）だけ。LAN には出さない。`probe_gitlab.py` の既定の接続先と `external_url` も、この値から作る。`external_url` と公開ポートは同じ値にする（ずれると clone URL と `web_url` が壊れる）。
- `restart: "no"`。Docker Desktop の起動で勝手に上がらないようにしている。
- root のパスワードは、コンテナもボリュームも無いときだけ乱数で作り、0600 の状態のファイルに書く。ボリュームが残っていれば作り直さない（GitLab は初回のパスワードを持っているため）。`down --purge` のあと作り直す。

## 版の固定

版は `docker_gitlab.py` の `GITLAB_IMAGE` の 1 か所で、ダイジェストで固定する。タグ（`19.4.1-ce.0` など）は使わない。
`latest` は使わない。版を上げるときは、この値を書き換えて `up --pull` を打つ。

## トークン

- `up` は、有効なトークンがあれば作り直さない。作り直すと、既存のトークンが失効するため。
- `up --rotate` のときだけ作り直す。既存は失効する。
- トークンは `~/.cache/ccnavi/gitlab/tokens.env`（`XDG_CACHE_HOME` があればその下）に 0600 で書く。リポジトリの外。端末には出さない。
- 使うときは、`set -a; . <status が出すパス>; set +a` で環境変数に載せる。
- トークンの有効期限は 7 日。期限が切れたら、`up` が失効（401）を見て自動で作り直す。`--rotate` は有効なものも作り直したいときだけ。

## probe_gitlab.py で 1 周する

```sh
set -a; . ~/.cache/ccnavi/gitlab/tokens.env; set +a
uv run python tests/manual/gitlab/probe_gitlab.py
```

- 必要なもの: 組み立て済みの exe（`dist/ccnavi/ccnavi`。`CCNAVI_ENTRY_PATH` で差し替え）、jq と curl。
- `CCNAVI_PROBE_GITLAB`（既定 `http://127.0.0.1:<CCNAVI_GITLAB_PORT、既定 8929>`）で接続先を変える。
- 結果と生の JSON は `CCNAVI_PROBE_OUT`（既定は一時ディレクトリ）に残る。
- `--help` は、トークンも exe も無しに終わる。

## CE の限界

- 変更要求（`request_changes`）は GitLab EE の機能。CE では 404 になる。probe はその段だけ失敗を許す。
  この 404 は、実機の CE で probe を 1 周させて確かめたものだけをここに書く（未確認）。

## 手動の通し確認

手動の確認は、ユーザの確認を取ってから行う（コンテナの起動・プロジェクトの作成と削除を伴う）。
順番は `up --pull` → `status` → `probe_gitlab.py` を 1 周 → `down`。結果は PR の説明に書く。
