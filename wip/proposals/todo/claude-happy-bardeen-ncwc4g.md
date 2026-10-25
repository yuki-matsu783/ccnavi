---
version: 1
ticket: claude-happy-bardeen-ncwc4g
issue: 572
mr: 575
title: "feat: Docker の本物の GitLab を起動・停止する道具を整える（#572）"
phases:
  quick-work:
    kind: work
    title: 小さい作業
    review: mr
    scope: inherit
    when: 設計判断や並行作業が要らないとき
plan:
  - quick-work
rationale: |
  issue #572 の残りを 1 本で終える。GitLab を立てる手順がリポジトリに無く、起動・停止・状態確認を
  1 つの入口にまとめないと、#551（応答の録り）と docker-gitlab（受入の相手）が毎回手で立てることになる。
  PR #575 は API 部品の切り出しの途中で、残りは docker 側の道具、probe の呼び出しの置き換え、
  Docker 無しの自動テスト、文書。compose は手動の GitLab の立て方を宣言として 1 か所に残すために使う。
  設計判断（compose の置き場・版の定数・トークンの置き場）は下の「範囲」に書いて、レビューで見てもらう。
allow:
  - match: Write|Edit
    glob: "tests/manual/gitlab/**"
  - match: Write|Edit
    glob: "tests/core/test_gitlab_manual*.py"
  - match: Write|Edit
    glob: "docs/42_claude/**"
  - match: Write|Edit
    glob: "docs/41_guide/**"
  - match: Write|Edit
    glob: "docs/40_development.md"
---
## 目的

- `up` / `down` / `status` の 1 つの入口で、Docker の GitLab CE（版固定）を起動・停止・確認できる。
- `probe_gitlab.py` が import で副作用を起こさず、API 部品（`gitlab_api.py`）を使う形になる。動作は変えない。
- Docker の無い環境でも、自動テストで道具の振る舞い（引数・取得の抑止・トークンの権限・作り直しの抑止）を確かめられる。

## 範囲

1. **置き場**: `tests/manual/gitlab/` に `docker_gitlab.py` と `compose.yml` を置く。compose は `scratchpad/gitlab/compose.yml` の内容を移す（image・ポート・メモリ上限・ボリューム）。
2. **版の定数**: `docker_gitlab.py` の `GITLAB_IMAGE` の 1 か所。値は `gitlab/gitlab-ce@sha256:9b33b45b…`（19.4.1、ローカル取得分）。compose へは環境変数で渡す。#551 の記録と docker-gitlab の記録は、この定数を参照する。
3. **`up`**: イメージが無いときは取得せず `次:` で止まる（`--pull` で取得）。動いていれば起動しない。起動後は、ヘルスチェック・`/api/v4/version`・`gitlab-rails runner` の成功を待つ（上限 15 分、進捗を出す）。root パスワードは起動ごとに乱数で作り、`.env` 相当の 0600 ファイルに書く。
4. **トークン**: 有効なトークンがあれば作り直さない。`up --rotate` のときだけ作り直す（rb の `revoke!` を通る）。トークンは端末に出さず、0600 のファイルに書く。置き場はユーザのキャッシュ（`logs/` の外）。`status --env` で環境変数の形に出す入口を作る。
5. **`down`**: コンテナを止めて消す。ボリュームは残す。`--purge` でボリュームも消す。
6. **`status`**: 動作、版、ポート、トークンの有効性を出す。
7. **メモリ**: 4GB より少なければ `次:` で警告。
8. **probe_gitlab.py**: main 内の `api()` 直呼びを `gitlab_api.Client` の名前付きメソッドに置き換える。import 時の `sys.exit`・一時ディレクトリ・ログのオープンは main の中へ移す。
9. **gitlab_api.py の docstring**: 公開する関数名の一覧は PR #575 で書いてある。変えた分だけ直す。
10. **ログ**: トークンを本文に出さない。拒否・停止の出力に `次:` `案内:` を付ける（script-output.md）。
11. **自動テスト**: `tests/core/test_gitlab_manual_tools.py`（Docker 不要）。引数、`--pull` なしで取得しないこと（`docker` 呼び出しを差し替えて確かめる）、トークンファイルの権限、`up` の 2 回目がトークンを作り直さないこと、`gitlab_api` と `probe_gitlab` の import に副作用が無いこと。
12. **文書**: `docs/42_claude/` の該当する文書に、立て方・版・CE の限界（変更要求は EE の機能。probe が実際に 404 を返したことは実機の結果で書く）を書く。`tests/manual/gitlab/` の説明も直す。経緯は書かない。

## 対象外

- #551 の応答の録り、docker-gitlab の受入シナリオ（後続の issue）。
- 録った応答の redact（#551 の責務）。`redact` は probe に残すだけで、動作は変えない。
- 版の更新の自動化。版は定数を書き換えて上げる。
- CLAUDE.md の表の変更（この issue は表への追加を完了条件にしていない）。

## 完了の確かめ方

- `up` から `down` まで 1 つの入口で操作できる（自動テストで確かめる。実機は下の手動で確かめる）。
- 切り出したモジュールが副作用なしに import できる。
- 自動テストが `tests/core` で緑。ruff が通る。`python -m tests` の全件を回す。
- 手動の通し確認（`up --pull` → `status` → `probe_gitlab.py` を 1 周 → `down`）は、ユーザの確認を取ってから行い、結果を PR の説明に書く。立てられなければ理由を書き、PR は `Refs` にする。

## 計画の理由

完了の見方を満たすには、テストと実装と文書が 1 本で揃う必要がある。設計の判断は範囲の 1〜2 に書いたので、承認時に見てほしい点は版の定数の置き場とトークンの置き場。
