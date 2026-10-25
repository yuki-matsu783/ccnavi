---
title: GitLab の応答の形の契約テスト
type: guide
description: 本物の GitLab の応答の形を録った JSON と、Python の模擬（fake_gitlab.py）の応答の形を比べる契約テスト。要求の列・場面・正規化・記録の形・許可リスト・限界・録り直し
tags: [testing]
keywords: [契約テスト, contract, GitLab, recorded.json, recorded.sample.json, requests.json, 正規化, 許可リスト, 模擬, fake_gitlab, 形]
---

# GitLab の応答の形の契約テスト

本物の GitLab（Docker）の応答の**形**を録り、模擬（`tests/fake_gitlab.py`）の応答の形と比べる。値（ID・時刻・SHA）は比べない。
置き場は `tests/contract/`（テストのグループ `contract`）と、データの `tests/contract/gitlab/`。

## 何が証明されるか

- `recorded.sample.json` は模擬の応答から作ったサンプル。これと一致することが示すのは**比較器と要求の列が動くこと**だけで、模擬が本物に近いことは示さない。「契約テストが緑＝本物と一致」とは読まない。
- 模擬が本物と合っていると言えるのは、本物から録った `recorded.json` が入り、許可リストを当てても緑のとき。許可リストの項目が、本物と模擬の既知の違い。
- `recorded.json` を録る工程（Docker の GitLab が要る）は**未実施**。録る手順は下の「録り直し」。

## ファイル

| ファイル | 中身 |
|---|---|
| `requests.json` | sh が打つ GitLab API の列（23 本）。本物にも模擬にも同じ列を送る |
| `recorded.json` | 本物から録った形。**本物だけ**。まだ無い |
| `recorded.sample.json` | 模擬から作ったサンプル（`"source": "sample"`）。`uv run python -m tests.contract.fakeside` で作り直す |
| `normalize-cases.json` | 正規化の入力と期待の対の表。Python のテストが読む。#553 の TypeScript も同じ表を読む |
| `allow.json` | 許可リスト（`entries`）と、録らないものの一覧（`not_recorded`） |
| `forbidden.json` | 記録に入れない文字列の正規表現 |

比較器と正規化は `tests/contract/shape.py` の 1 本。模擬の側の場面と送り手は `tests/contract/fakeside.py`。

## 要求の列（requests.json）

1 要求の書き方。

```json
{"name": "discussion_reply", "method": "POST",
 "path": "/projects/${project}/merge_requests/${mr.iid}/discussions/${discussion.id}/notes",
 "query": {"order_by": "created_at"}, "body": {"body": "contract reply"},
 "token": "main", "used_by": ["sh"], "save": {"user.id": "id"}}
```

- `path` は `/api/v4` より後ろ。`query`・`body` は省略できる
- `token` は `main`（認証あり）か `none`（認証なし）
- `used_by` はその要求を使うもの（`sh`。#553 が `chrome` を足す）
- `save` は、応答の本文のキーを後ろの要求の `${…}` に使う（`user` が `user.id` を置く）
- 状態の宣言は持たない。場面は下に固定し、録る側と比べる側が作る
- 書き込みは最後にまとめてある（読みの形が前の書き込みで変わらないように）
- `used_by` が `sh` だけなので、`POST repository/commits` などの拡張だけが使う要求は載せていない（#553 で足す）

### 置き換えの書き方

要求の `path`・`query`・`body` の中の `${名前}` を、録る側・比べる側が実体に置き換える。

| 名前 | 置き換えるもの |
|---|---|
| `${project}` | プロジェクトのパスを `%2F` で符号化したもの（`ns%2Fproj`）。**記録にも `requests.json` にも実体の名前は入れず、`${project}` のまま持つ** |
| `${user.id}` | `user` の応答の `id`（`save` で置く） |
| `${mr.iid}` | 場面の open の MR の iid |
| `${mr.branch}` / `${mr.merged_branch}` / `${mr.new_branch}` | open の MR の元ブランチ / merged の MR の元ブランチ / MR をこれから作るブランチ（存在するが MR は無い） |
| `${issue.iid}` | 場面の issue の iid |
| `${discussion.id}` | 場面のスレッドの id |

### 場面（事前状態）

本物は録る側が `Client`（`create_project`・`create_mr` …）で、模擬は `fakeside.scene_state()` で、同じ場面を作る。

- プロジェクト 1 つ。issue が 1 件（メモ 1 件）
- open の MR が 1 本（`${mr.branch}` から `main` へ。本文は `Closes #<issue>`）。スレッド 1 つ（未解決、メモ 1 件）、レビュアー 1 人、MR のメモ 2 件
- merged の MR が 1 本（`${mr.merged_branch}`）。本物で merge が作れなければ一覧は空になるので、空の配列は比べない（下の正規化）ことにより許される
- `${mr.new_branch}` は存在するブランチで、MR は無い

## 正規化（形の表し方）

形は JSON の辞書で、次のキーを持つ。

```json
{"t": ["integer", "null"], "keys": {"名前": {}}, "opt": ["名前"], "items": {}}
```

- `t`: 型の和集合（`string` `integer` `number` `boolean` `null` `object` `array` `datetime`。並べ替え済み）。`integer` と `number` は分ける。ISO 8601 の日時文字列は `datetime`
- `keys`: object のキーごとの形。キーの順は持たない
- `opt`: `keys` のうち、一部の要素にだけあるもの（配列の要素を合わせたとき）。`null` と欠落は区別する（`null` は型に `null` を足し、欠落は `opt`）
- `items`: array の要素の形の和集合。空の配列は持たない（持たない側とは比べない）
- 深さ 5 より深い入れ物は `object` / `array` に丸める
- 環境で変わる欄は落とす: `avatar_url` `web_url` `username` `email` `public_email` `commit_email` `token`

入力と期待の対は `normalize-cases.json`。実装を変えたら、まずこの表を直す。

## 比べ方

要求ごとに、ステータスと形を比べ、違いを「要求名＋キー＋違いの種類」で出す。キーは `author.id`・`notes[].system`（配列の要素は `[]`）。根は `$`、ステータスは `$status`。

| 種類 | 意味 |
|---|---|
| `missing` | 録った側にあり、模擬に無い |
| `extra` | 模擬にあり、録った側に無い |
| `type` | 型が違う（`integer` と `string`、`integer` と `number` など） |
| `presence` | 常在か任意かが違う |
| `status` | HTTP のステータスが違う |

## 記録の形（recorded.json）

```json
{
  "version": 1,
  "source": "gitlab",
  "gitlab_image": "gitlab/gitlab-ce@sha256:…（docker_gitlab.py の GITLAB_IMAGE）",
  "recorded_at": "2026-10-26",
  "not_recorded": ["headers", "page2", "error_403"],
  "requests": {
    "mr_get": {"status": 200, "shape": {"t": ["object"], "keys": {"iid": {"t": ["integer"]}}}}
  }
}
```

`recorded.sample.json` は同じ形で、`source` が `sample`、`gitlab_image` と `recorded_at` が `null`。

## 許可リスト（allow.json）

意図して許す違い。`entries` の 1 項目は `{"request", "key", "kind", "reason"}`。

- 理由（`reason`）が空なら失敗する
- 許可したのに実際は差が出ていない項目は失敗する（古い許可の検出）。模擬を直したら外す
- `not_recorded` は、録らないものの宣言（ヘッダ・2 ページ目・403）。比べる対象ではない

## 機密の検査

`forbidden.json` の正規表現が、`recorded*.json` と `requests.json` に当たらないこと。`glpat-` `gldt-` `glptt-` `glrt-`、`PRIVATE-TOKEN`、`Bearer `、メールの形、`root`、`ccnavi-reviewer`（単語境界つき。`root_ref` には当たらない）。GitLab の版（`GITLAB_IMAGE`）は入れてよい。サンプルに禁止語を 1 つ混ぜると落ちる試験がある。

## 限界

比べられない・比べないもの。

- ヘッダ（`Link`・`X-Next-Page`・`X-Total`）と 2 ページ目。`Client` がヘッダを捨てる。模擬も出さない
- 403（模擬が出せない）。権限の細かい差
- GitLab の版差（記録は 1 つの版）、EE 限定の機能（変更要求は CE では 404）
- レート制限、競合、実時間の遅延
- 値（ID・時刻・SHA）の一致
- 空の配列の要素の形

## 模擬を直したとき

`tests/fake_gitlab.py` の応答を変えたら `uv run python -m tests tests/contract` を回す。落ちたら、本物に合わせて模擬を直すか、意図した差なら許可リストに理由つきで足す。サンプルが古くなって落ちたら `uv run python -m tests.contract.fakeside` で作り直す（本物の記録 `recorded.json` は作り直さない）。

## 録り直し（本物の GitLab）

**未実施。** 手順は Docker の GitLab（[gitlab-manual.md](gitlab-manual.md)）が動いている機械で行う。録り直すのは「模擬の応答を変える PR のとき」と「GitLab の版（`GITLAB_IMAGE`）を上げるとき」。

1. `uv run python tests/manual/gitlab/docker_gitlab.py status` で動いていることを確かめる（動いていなければ gitlab-manual.md のとおりに起こす）
2. 使い捨てのプロジェクトを作り、上の場面を `Client` で組む
3. `requests.json` の列を本物に送り、応答を正規化して一時ファイルに書く
4. 全部成功したときだけ `recorded.json` に置き換える。禁止文字列の検査を通す
5. 使い捨てのプロジェクトを消す（自分が作ったものだけ）
