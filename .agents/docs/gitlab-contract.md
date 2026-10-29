---
title: GitLab の応答の形の契約テスト
type: guide
description: 本物の GitLab の応答の形を録った JSON と、Python の模擬（fake_gitlab.py）の応答の形を比べる契約テスト。要求の列・reads・場面・正規化・記録の形・許可リスト・限界・録り直し
tags: [testing]
keywords: [契約テスト, 見本, gitlab_host, host_entries, contract, GitLab, recorded.json, recorded.sample.json, requests.json, reads, expect_status, 正規化, 許可リスト, 模擬, fake_gitlab, 形]
---

# GitLab の応答の形の契約テスト

本物の GitLab（Docker）の応答の**形**を録り、模擬（`tests/fake_gitlab.py`）の応答の形と比べる。値（ID・時刻・SHA）は比べない。
置き場は `tests/contract/`（テストのグループ `contract`）と、データの `tests/contract/gitlab/`。

## 何が証明されるか

- `recorded.sample.json` は模擬の応答から作ったサンプル。これと一致することが示すのは**比較器と要求の列が動くこと**だけで、模擬が本物に近いことは示さない。
- 模擬が本物と合っていると言えるのは、本物から録った `recorded.json` と、**`reads` のキー**で比べて緑のとき（許可リストの項目は、本物と模擬の既知の違い）。
- 合っていると言えるのは、記録した場面（下の「場面」）の、`reads` のキーに限る。`reads` に無い本物だけのキーは比べない。限界は下の「限界」。

## ファイル

| ファイル | 中身 |
|---|---|
| `requests.json` | sh が打つ GitLab API の列（24 本）。本物にも模擬にも同じ列を送る。各要求に `expect_status` と `reads` を持つ |
| `recorded.json` | 本物から録った形。**本物だけ**。`uv run python tests/manual/gitlab/record_contract.py` で作る |
| `recorded.sample.json` | 模擬から作ったサンプル（`"source": "sample"`）。`uv run python -m tests.contract.fakeside` で作り直す |
| `normalize-cases.json` | 正規化の入力と期待の対の表。Python のテストと、Chrome 拡張の TypeScript の試験（`test/helpers/shape.ts`）が同じ表を読む |
| `allow.json` | `fake_gitlab.py` の許可リスト（`entries`）、見本（`gitlab_host.py`）の許可リスト（`host_entries`）、Chrome 拡張の `MockGitLab` の許可リスト（`chrome_entries`。Python は読まない）、録らないものの一覧（`not_recorded`） |
| `tests/contract/test_host_sample.py` | 見本（`tests/sh/gitlab_host.py` とフィクスチャ）の比較。下の「見本」 |
| `forbidden.json` | 記録に入れない文字列の正規表現 |

比較器と正規化は `tests/contract/shape.py` の 1 本。模擬の側の場面と送り手は `tests/contract/fakeside.py`。本物の側の場面を組むのは `tests/manual/gitlab/record_contract.py`（contract のテストは manual に依存しない。置くか置かないかの判定と `os.replace` は `shape.py` に置き、Docker 無しで試験する）。

## 要求の列（requests.json）

1 要求の書き方。

```json
{"name": "discussion_reply", "method": "POST",
 "path": "/projects/${project}/merge_requests/${mr.iid}/discussions/${discussion.id}/notes",
 "query": {"order_by": "created_at"}, "body": {"body": "contract reply"},
 "token": "main", "used_by": ["sh"], "expect_status": 201, "reads": ["id"],
 "save": {"user.id": "id"}}
```

- `path` は `/api/v4` より後ろ。`query`・`body` は省略できる
- `token` は `main`（認証あり）か `none`（認証なし）
- `used_by` は、その要求を比べる側（`sh`、`chrome`、`scenarios`）。`sh` は sh が打つ要求。`scenarios` は受入シナリオの相手側の記録（`tests/scenarios/hostsnap.py`）だけが読む要求（`approvals_get`）。`chrome` は、Chrome 拡張の `MockGitLab` が内部の状態から 200/201 で答え、TS の契約の試験が比べる要求だけに付ける。録る側と `fakeside` は `used_by` を見ずに全要求を送る
- `expect_status` は録るときに本物が返すべきステータス。`error_401` は 401、`error_404` は 404、ほかは 200 か 201。**1 本でも違えば `recorded.json` を置かない**
- `reads` は、その要求の応答のうち、sh と拡張が実際に読むキー（下の「reads」）
- `save` は、応答の本文のキーを後ろの要求の `${…}` に使う（`user` が `user.id` を置く）
- 状態の宣言は持たない。場面は下に固定し、録る側と比べる側が作る
- 書き込みは最後にまとめてある（読みの形が前の書き込みで変わらないように）
- `POST repository/commits` などの拡張だけが使う要求は載せていない

### 置き換えの書き方

要求の `path`・`query`・`body` の中の `${名前}` を、録る側・比べる側が実体に置き換える。

| 名前 | 置き換えるもの |
|---|---|
| `${project}` | プロジェクトのパスを `%2F` で符号化したもの（`ns%2Fproj`）。**記録にも `requests.json` にも実体の名前は入れず、`${project}` のまま持つ** |
| `${user.id}` | `user` の応答の `id`（`save` で置く） |
| `${mr.iid}` | 場面の open の MR の iid |
| `${mr.branch}` / `${mr.merged_branch}` / `${mr.new_branch}` | open の MR の元ブランチ / merged の MR の元ブランチ / MR をこれから作るブランチ（存在するが MR は無い） |
| `${issue.iid}` | 場面の issue の iid |
| `${discussion.id}` | 場面のスレッドの id（録る側は場面を組んだときの応答から取る） |

### 場面（事前状態）

本物は `record_contract.py` が `gitlab_api.Client` で、模擬は `fakeside.scene_state()` で、同じ場面を作る。

- プロジェクト 1 つ。`main` に最初のコミット。`feature`・`done`・`fresh` の 3 ブランチ（それぞれ `main` から切り、ファイルを 1 つ足す）
- issue が 1 件（メモ 1 件）
- open の MR が 1 本（`feature` から `main` へ。本文は `Closes #<issue>`）。スレッド 1 つ（未解決、メモ 1 件）、レビュアー 1 人（`CCNAVI_PROBE_REVIEWER_ID` を member にして入れる）、MR のメモ 2 件。実物のスレッド一覧にはレビュアー割り当ての system メモも混じる（模擬の場面にも 1 件入れてある）
- merged の MR が 1 本（`done`）
- `fresh` は存在するブランチで、MR は無い
- 録る前に、open の MR の `merge_status` が `checking` でなくなり `diff_refs` が入るまで待つ。`related_merge_requests` に MR が出るまでも待つ（参照は非同期に作られる）

## reads（比べるキー）

本物の応答は 30〜100 のキーを返すが、sh が読むのはその一部（拡張は project の権限の検査（`project_access` と `group_access`）と既定のブランチ（default_branch）だけ。拡張だけが使う要求は載せていない）。模擬は読まれるキーだけ返せばよい。そこで要求ごとに `reads`（読むキーの一覧）を持ち、比べるキーを絞る。

- **導き方**: `.ccnavi/scripts/` の `jq` の式（`ccnavi-start.sh`・`ccnavi-branches.sh`・`ccnavi-review.sh`・`ccnavi-common-review.sh`・`ccnavi-user-lib-review.sh`・`ccnavi-common-host.sh`）と、拡張の `extensions/chrome/ccnavi-approval/src/core/gitlab.ts` の読み方から導く。sh が `// ""` で受ける省略できるキーでも、本物が返すものは載せる。本物が返さないキー（`closes_issues` の `references`、`reviewers` の `updated_at`、非差分のスレッドの `position`）は、存在の検査に通らないので載せない
- **書き方**: 比較器の差のキーと同じ。`author.id`、`[].iid`（応答が配列のとき）、`notes[].system`（配列の中）
- **環境で変わる欄**（`web_url`・`username` など。`shape.DROPPED_KEYS`）は形から落とすので、`reads` に載せられない（試験が落とす）

比べ方は 3 つ。

1. `reads` のキーは、本物と模擬で必ず比べる（足りない `missing`・型が違う `type`・常在か任意か `presence`）。`reads` のキーの親が模擬に無いときも、そのキーの差として出る
2. `reads` に無いキーで**本物にしか無い**ものは、比べない（許可リストにも載せない）
3. **模擬にしか無い**キーは、`reads` に関わらず従来どおり `extra` として出す

加えて、`reads` の全キーが `recorded.sample.json` と `recorded.json` の両方に存在することを検査する（無ければ試験が落ち、録るときは置かない）。`reads` の実体は `requests.json` を見る。

## 正規化（形の表し方）

形は JSON の辞書で、次のキーを持つ。

```json
{"t": ["integer", "null"], "keys": {"名前": {}}, "opt": ["名前"], "items": {}}
```

- `t`: 型の和集合（`string` `integer` `number` `boolean` `null` `object` `array` `datetime`。並べ替え済み）。`integer` と `number` は分ける。ISO 8601 の日時文字列は `datetime`
- `keys`: object のキーごとの形。キーの順は持たない
- `opt`: `keys` のうち、一部の要素にだけあるもの（配列の要素を合わせたとき）。`null` と欠落は区別する（`null` は型に `null` を足し、欠落は `opt`）
- `items`: array の要素の形の和集合。空の配列は持たない（持たない側とは比べない。録るときに、場面が配列を返していることを確かめる）
- 深さ 5 より深い入れ物は `object` / `array` に丸める
- 環境で変わる欄は落とす: `avatar_url` `web_url` `username` `email` `public_email` `commit_email` `token`

入力と期待の対は `normalize-cases.json`。実装を変えたら、まずこの表を直す。

## 比べ方

要求ごとに、ステータスと形を比べ、違いを「要求名＋キー＋違いの種類」で出す。キーは `author.id`・`notes[].system`（配列の要素は `[]`）。根は `$`、ステータスは `$status`。

| 種類 | 意味 |
|---|---|
| `missing` | 録った側にあり、模擬に無い（`reads` のキーに関わるものだけ数える） |
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
  "gitlab_version": "19.4.1",
  "recorded_at": "2026-10-26",
  "not_recorded": ["headers", "page2", "error_403"],
  "requests": {
    "mr_get": {"status": 200, "shape": {"t": ["object"], "keys": {"iid": {"t": ["integer"]}}}}
  }
}
```

- `version` は**記録形式の版**（1 のまま）。GitLab の版ではない。GitLab の版は `gitlab_image`（`GITLAB_IMAGE`）と `gitlab_version`（`GET /version`）
- `requests` はパスを持たない（`{status, shape}` だけ）。実体の名前（`root/p551-…`）は入らない
- `not_recorded` は `allow.json` の同名の一覧から作る。空にしない
- `recorded.sample.json` は同じ形で、`source` が `sample`、`gitlab_image`・`gitlab_version`・`recorded_at` が `null`

## 許可リスト（allow.json）

意図して許す違い。`entries` の 1 項目は `{"request", "key", "kind", "reason"}`。

- 理由（`reason`）が空なら失敗する
- 許可したのに実際は差が出ていない項目は失敗する（古い許可の検出）。模擬を直したら外す
- `not_recorded` は、録らないものの宣言（ヘッダ・2 ページ目・403）。比べる対象ではない

**`entries`（fake 側）と `chrome_entries` の項目は 0 件**。`reads` で絞ったうえで、録った差は全部模擬を直して消した。版差や EE の機能（直せない差）が出たら、理由つきで足す。見本の許可は別キーの `host_entries`（下の「見本」）。

### 契約テストが見つけた模擬の誤り

録った本物と比べて見つかり、模擬（`tests/fake_gitlab.py`・`fakeside.scene_state()`）を直したもの。

- issue の応答に、状態が内部に持つ `notes` が漏れていた（`issue_get` など）
- `closes_issues` が `references` を返していた。本物は返さないので、sh の `.references.full // …` の代替の枝が、模擬では通っていなかった
- スレッドへの返信のメモが `system`・`resolvable`・`resolved` を持たなかった
- `project` が `archived`・`issues_enabled`・`issues_access_level`・`permissions` を返さなかった（拡張の権限の検査が読む）
- MR の応答が `draft`・`sha`・`description` を返さなかった（sh が `.draft` と `.sha` を読む）
- 作成・取得したメモと issue の `author` が無かった
- スレッドのメモに `type`（`DiscussionNote`。system のメモは null）が無かった

## 見本（gitlab_host.py）

`tests/sh/gitlab_host.py` と、その読む場面（`extensions/chrome/ccnavi-approval/test/fixtures/host/gitlab/`）の応答の形も、`recorded.json` と `reads` のキーで比べる。試験は `tests/contract/test_host_sample.py`。`gitlab_host.py` は import せず、パスを指して読み込む（`tests/contract` は `tests/sh` を import しない）。場面の一覧と置き場は `gitlab_host.scene_names()`・`SCENES` から取る。

### 場面

| 場面 | 扱い | 理由 |
|---|---|---|
| `nested`・`paged`・`replies`・`replies-accepted`・`requested-changes`・`resolved` | 比べる | 本物の形に近い見本。`paged` は `discussions` の 1 ページ目だけ比べる |
| `hostile`・`odd-types`・`impostor` | 除く | 異常な値や型をわざと入れた見本（悪意のある本文・型の違う欄・フォークの MR）。本物の形とは合わない |

場面を足したら、比べるか除くかを試験の一覧に足す（どちらにも無ければ試験が落ちる）。

### 要求名

比べる要求は試験の固定リスト。名前が `requests.json` に無ければ失敗する。要求の URL は `scene.json`・`mrs.json`（`source_project_id` が 42 の MR）から組む。

| 要求名 | 見本の答え | 扱い |
|---|---|---|
| `user` `mrs_opened` `discussions` `reviewers` | `user.json`・`mrs.json`・`discussions.1.json`・`reviewers.1.json` | 場面ごとに比べる |
| `notes_post` `notes_get` | 状態ファイル（`FAKE_GITLAB_STATE`） | 場面によらないので 1 場面で 1 回。先に `notes_post` を打ち、`notes_get` が空にならないようにする |
| `mrs_merged` | 全場面で `[]` | 除く。`state=merged` の枝が無く、比べても配列が空で何も比べない |
| `project` | 固定値 `{"id":42,"default_branch":"main"}` | 除く。`reads` は 5 キーで、見本が持つのは `id` と `default_branch` だけ（比べられるのは `id` のみ） |
| 上以外 | 404 | 見本が答えない（見本は読み取りだけの代役） |

### 比べ方と許可

- 比べるキーは `reads`。要求ごと・場面ごとに、`reads` のうち見本にも記録にも存在するキーが 0 件なら失敗する。`reads` の全キーが見本にあることも確かめる
- 差は場面をまたいで集め、`allow.json` の `host_entries`（項目の形は `entries` と同じ）に 1 回だけ通す。`entries` は fake 側が読むので共用しない
- 失敗には場面名・要求名・キー・違いの種類が出る

| 要求・キー・種類 | 扱い | 理由 |
|---|---|---|
| `mrs_opened` `[].description` | 見本を直した | `mrs.json` に足した |
| `discussions` `[].notes[].type` が `null` | 見本を直した | system のメモの `type` を `null` にした（`nested`・`resolved`） |
| `discussions` `[].notes[].position` の `extra` | `host_entries` | 本物の記録に DiffNote が無い。sh が読む |
| `discussions` `[].notes[].type` の `type` | `host_entries` | system のメモが無い場面（`paged`・`requested-changes`）は文字列だけ。足すと `expected.json` が変わる |
| `discussions` `[].notes[].resolved` の `presence` | `host_entries` | 本物は system のメモに `resolved` が無い。system のメモが無い場面は全ノートが持つ。足すと `expected.json` が変わる |

## 機密の検査

`forbidden.json` の正規表現が、`recorded*.json` と `requests.json` に当たらないこと。`glpat-` `gldt-` `glptt-` `glrt-`、`PRIVATE-TOKEN`、`Bearer `、メールの形、`root`、`ccnavi-reviewer`（単語境界つき。`root_ref` には当たらない）、`p551-`（録るときのプロジェクト名）。GitLab の版（`GITLAB_IMAGE`）は入れてよい。サンプルに禁止語を 1 つ混ぜると落ちる試験がある。録るときも、記録の全文をこれで検査してから置く。

## Chrome 拡張の模擬（MockGitLab）との比較

`extensions/chrome/ccnavi-approval/test/gitlab-contract.test.ts`（`pnpm test`。`--e2e` には入れない）が、`MockGitLab` の `handle` を直接呼び、`used_by` に `chrome` を持つ要求だけを `recorded.json` と `reads` で比べる。比較器 `test/helpers/shape.ts` は `shape.py` の写しで、名前も規則も同じ。正本は Python で、規則を変えたいときは Python を直し、`normalize-cases.json` を直してから TS を合わせる。

- 比べる要求（`MockGitLab` が内部の状態から 200/201 で答えるもの）: `user`・`project`・`mrs_merged`・`mr_create`・`error_401`・`error_404`。`MockGitLab` が 404 か形の違う応答を返す要求（`member` など）は `chrome` を付けず、比べない
- `mrs_opened` は比べない。`source_branch` を付けると `MockGitLab` は `pulls` から `iid`・`source_project_id`・`web_url` だけを返し、場面を付けると見本から返すため、`reads` の範囲で答える経路がない
- 見本（`test/fixtures/host/gitlab/*`）から返す応答は比べない。手で組んだもので、本物の形を写していない
- 許可リストは `chrome_entries`（`entries` に混ぜない。`apply_allow` が当たらない項目を古い許可として落とす）
- 同じ生の入力を Python と TS で正規化して一致することも確かめる。`uv` が無ければ、その試験は skip と出る
- `shape.py`・`normalize-cases.json`・`requests.json`・`recorded.json` を直したら、`pnpm test` も回す。`python -m tests tests/contract` は TS を回さないので、食い違いは `pnpm test` を回すまで見つからない

## 限界

比べられない・比べないもの。見本は、2 ページ目・`reads` 外のキー・値の並び順を比べない（`paged` は 1 ページ目のみ）。除く場面と要求は「見本」の節の表。

- `reads` に無いキー。本物だけのキーは比べないので、sh や拡張が新しいキーを読むようになったら、`requests.json` の `reads` に足す（足し忘れは検出できない）
- ヘッダ（`Link`・`X-Next-Page`・`X-Total`）と 2 ページ目。`Client` がヘッダを捨てる。模擬も出さない
- 403（模擬が出せない）。権限の細かい差
- GitLab の版差（記録は 1 つの版）、EE 限定の機能（変更要求は CE では 404）
- レート制限、競合、実時間の遅延（非同期の判定は録る前に待って確定させる）
- 値（ID・時刻・SHA）の一致。`state`・`source_branch` の絞り込みが効くか、`order_by`・`sort` の並び、日時の書式は、型が同じなら緑になる
- `web_url` は sh が読むが、環境で変わる欄として形から落とすので、模擬が返さなくなっても検出できない
- TS の比較器との既知の差。`1.0` のように小数点以下が 0 の値は、Python では `number`、JS の `JSON.parse` では `integer` になる。`DATETIME` の正規表現は、Python の `\d` が Unicode の数字にも、`$` が末尾の改行の前にも当たるのに対し、JS は ASCII の数字だけに当たり、`$` は末尾だけに当たる（JS の方が厳しい）
- Chrome 拡張の比較は `reads` の範囲と、`MockGitLab` が内部の状態から答える要求だけ。見本由来の応答と、拡張だけが使う要求（branches・tree・commits・compare など）は対象外
- 空の配列の要素の形（録るときに、`NON_EMPTY` の要求が空でないことを確かめる）
- 録った場面の外（差分への指摘のスレッド、複数ページ、フォークからの MR、グループ配下のプロジェクトで `group_access` が整数になる形など）

## 模擬を直したとき

`tests/fake_gitlab.py` の応答、または見本（`tests/sh/gitlab_host.py`・`extensions/chrome/ccnavi-approval/test/fixtures/host/gitlab/`）を変えたら、**必ず** `uv run python -m tests tests/contract` を回す（`tests/fake_gitlab.py` を変えるコミットの前）。Chrome 拡張の `MockGitLab`（`test/helpers/mock-gitlab.ts`）を変えたら、`extensions/chrome/ccnavi-approval` で `pnpm test` を回す。落ちたら、本物に合わせて模擬を直す（基本）。版差・EE など直せない差だけ、意図した差として許可リストに理由つきで足す。サンプルが古くなって落ちたら `uv run python -m tests.contract.fakeside` で作り直す（本物の記録 `recorded.json` は作り直さない）。

## 録り直し（本物の GitLab）

録り直すのは「模擬が返すキーを足して `reads` を変えたとき」「sh や拡張が読むキーが変わったとき」「GitLab の版（`GITLAB_IMAGE`）を上げるとき」。録るのは手動で、自動テストは Docker を使わない。

1. `uv run python tests/manual/gitlab/docker_gitlab.py status` で、コンテナが healthy・トークンが「有効」であることを確かめる。動いていなければ [gitlab-manual.md](gitlab-manual.md) のとおりに起こす（`record_contract.py` は起動も停止もしない）。他の作業が使っているかもしれないので、`down`・`up --rotate`・`docker restart` などは打たない。トークンは `docker_gitlab.py` が書く `tokens.env` から読む
2. `uv run python tests/manual/gitlab/record_contract.py --dry-run > /dev/null` で試す（置かない。検査に当たれば理由を標準エラーに出す）
3. `uv run python tests/manual/gitlab/record_contract.py` で録る。約 1 分。使い捨てのプロジェクト `p551-<乱数>` を作って場面を組み、`requests.json` の列を送り、終わったら消す（自分が作ったものだけ。途中で失敗しても消す。`CCNAVI_PROBE_KEEP=1` で残せる）。**2 回録り（2 回目は別のプロジェクト）、`reads` の各キーの型が一致すること**を確かめる。一致しないキーは、`reads` から外すか、型の和集合で済むかを判断する
4. 置く前の検査に 1 つでも当たれば `recorded.json` を置かない: 期待のステータスと違う応答、`NON_EMPTY` の配列が空、`reads` のキーが記録に無い、禁止文字列、動いているコンテナのイメージが `GITLAB_IMAGE` と違う。通れば同じ場所の一時ファイルに書いて `os.replace` で置く
5. `uv run python -m tests tests/contract` を回す。落ちたら、本物に合わせて模擬を直す。意図した差なら許可リストに理由つきで足す

場面の組み立てに使う `POST repository/commits` などは、`gitlab_api.Client` の追加のメソッド（`first_commit`・`commit_file`・`create_mr_described`・`get_mr`・`merge_mr`・`create_issue_note`・`create_mr_note`・`wait_mr_checked`）で、録る側だけが使う。`merged` の MR は `PUT merge` で作る（mergeability の判定が非同期なので、`wait_mr_checked` で待ち、405 の間は 5 秒おいて試し、`merged` になるまで待つ）。
