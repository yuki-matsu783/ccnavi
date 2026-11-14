---
type: guide
title: sh の処理の移行の一覧
description: .ccnavi/scripts/ の sh から実行ファイルへ移した処理と、まだ sh に残る処理。sh ごとの移す範囲、先に直すずれ
tags: [exe-boundary, sh-scripts]
keywords: [sh, 実行ファイル, 移行, 一覧, 進捗, 段取り, 外との出入り, ccnavi-start.sh, ccnavi-git.sh, integrate, write-and-push, テスト]
---

# sh の処理の移行の一覧

sh の処理は 1 本ずつ実行ファイルへ移す。何を残して何を移すかと、移す手順は[実行ファイルの境界](exe-boundary.md)の「sh に残すものと、実行ファイルへ寄せるもの」にある。移したら「移した」の表へ動かす。表は「移した」「残り」の 2 つだけにする。

## 移した

| sh | 移した処理 | 実行ファイルの入口 | 備考 |
|---|---|---|---|

## 残り

移す順は、上から。移すものは、そこに入る主な関数で書く。

| sh | 移すもの | sh に残すもの | 備考 |
|---|---|---|---|
| `ccnavi-start.sh` | 引数の検査、`--repo` と origin の照合、`issue_title_get`・`mr_linked_issues`・`find_similar`・`list_open_items` の応答の解釈、候補の振り分け、`adopt_check`・`id_check`・`clash_check`・`merged_check` の判定、`resolve_issue`、`ensure_worktree`・`fast_forward`・`ensure_initial_commit`・`cleanup_created_worktree`、`stop_host` の案内、`report` と dry-run の出力 | `host_open`、API の取得と POST、`remote_heads`、fetch・push、`ccnavi-branches.sh` と `ccnavi-sync.sh` の呼び出し、`--check` | `--check`（`ccnavi_preflight`）は `ccnavi-common-host.sh` と一緒に移す |
| `ccnavi-git.sh` | オプションの許可リスト、サブコマンドごとの拒否、`integrate` / `integrate-done` の判定と状態操作（一時ワークツリー・コミット・ff-only・`update-ref -d`）、ログの書き出しと要約 | 環境の消去、実行ファイルへの受け渡し、fetch・pull・push とゲートの ls-remote、子のワークツリーからの push の独立した検査 | 移した時点で、ラッパーの中の判定を sh に置くと決めた例外を置き換える |
| `ccnavi-common-state.sh` | `ccnavi_push_gate` の判定、`ccnavi_parent_trace`、`ccnavi_id_clash`、`ccnavi_git_refusal`、`ccnavi_record_*`、`ccnavi_integration` | `ccnavi_git_timed`、ゲートの ls-remote、実行ファイルに聞く口（`ccnavi_ask` など） | ゲートは素の push を打つ 3 経路が共有する |
| `ccnavi-sync.sh` | 親子の選び方、`sync_family`・`merged_present`・`sync_absent`・`sync_retired` の分岐、ff・merge・abort、origin/HEAD の向け直し、出力の文 | ls-remote、`fetch_one`、`ccnavi-review.sh merged` の呼び出し、ロック、trap | merge の abort は実行ファイルの 1 回の起動の中で持つ |
| `ccnavi-worktree.sh` | 引数と C1〜C7 の検査、`same_id_guard`、`make_candidate`、記録の書き出し、`worktree add`、報告の文 | ccnavi-git.sh 経由の fetch と ls-remote | 記録 `ccnavi-direct/<名前>` は integrate が読む |
| `ccnavi-fetch.sh` | ff merge・update-ref・symbolic-ref・ワークツリーの一覧の解析、`family`・`seen` の選び方、出力の文 | `fetch_git`、`retarget` の ls-remote と fetch、時間の枠、ロック | 実行ファイルを起こさない作り。移すなら、無いときは親の早送りを省くと決めてから |
| `ccnavi-branches.sh` | GitHub と GitLab の MR・issue の応答の整形（jq の 4 通り） | 一覧の取得 | 生の一覧を `branches --result` に渡す形にする |
| `ccnavi-common-host.sh` | `ccnavi_host_fail_status`・`graphql_blocked`・`fail_body` の解析、`host_access` の判定、preflight の集計と `fix` の順、`pf_line`・`pf_next` | `host_connect`・`host_api`・`host_pages`・`git_auth`・probe の取得、`find_tool` | `host_parse` は接続の前に要る。実行ファイルの `host.classify` と規則が重なる |
| `ccnavi-review.sh`・`ccnavi-common-review.sh` | `mr_state_classify`、`merged`・`undraft` の読み、`threads`・`reviews`・`find_marked` の解釈、`ready` の出力の読み取り | `api`・`gql`・`pages`、GraphQL が断られたときの REST への切り替え、ページの cursor の読み | `threads` の GitLab の枝は Chrome 拡張の `gitlab.ts` と同じ読み方。Go に移して WASM と共有する。`mr-state` の 2 行の形と終了コードはボードとの約束 |
| `ccnavi-common-write-and-push.sh` | `ccnavi_wap_commit`・`unstage`・`restore_written`・`merge_begin` / `unmerge` / `commit_merge`・`undo`・`busy` | push と ls-remote、ロック、ゲートの呼び出し、`ccnavi-sync.sh` の入れ子の起動 | 書く→コミット→push→確かめ→戻してやり直しの一続き。ローカルの書き込みと戻しは 1 回の起動にまとめる |
| `ccnavi-user-push-approved.sh` | `carry_paths`・`given_in`・`unsent_paths`、ツリーの列挙とリンクの検査、保護ブランチの判定 | push、ロック、ユーザだけが打つ入口 | 実行ファイルを起こさない作り（承認の直後、入れ替え中でも push できるように） |
| `ccnavi-user-lib-review.sh`・`ccnavi-user-decide.sh`・`ccnavi-user-close-early.sh` | `accept_threads` の検査、`reply_already_sent`、`resume_prepare` の読み | 返信・解決・issue・コメントの送信、y/N の確認、ロック | Chrome 拡張とホストへ書く順が違う。共有するなら順を揃えてから |
| `ccnavi-ticket.sh` | `ticket_id` の検査、approval-request の結果ファイルの読み、`ensure_child_worktree`、`ticket_unsent_parent_next` の文 | preflight、write-and-push の組み立て | |
| `ccnavi-stage.sh`・`ccnavi-dirs.sh` | パスとリンクの検査、読み替えの表、コピー、手順の文。`projects/` ごとの最新の並べ方 | — | ほぼ全部を実行ファイルへ移せる |

移さないもの: `ccnavi-launcher.sh`・`ccnavi-common.sh` の探し方・`ccnavi-common-log.sh`・`ccnavi-common-lock.sh`・`ccnavi-idle.sh`・`ccnavi-session-start.sh`・`ccnavi-user-agree.sh` と、`ccnavi-user-handoff.sh`・`prune.sh`・`mirror.sh`・`clean-branch.sh`（起動役・ログ・ロック・待機・ユーザだけが打つ入口で、すでに実行ファイルへ渡すだけの薄さ）。

### 先に直すずれ

| ずれ | 場所 | 直し方 |
|---|---|---|
| 保護するブランチの一覧が 4 か所にあり、形が揃っていない（`release-*` と `release/*`） | `ccnavi-git.sh`（2 か所）・`ccnavi-user-push-approved.sh`・`ccnavi-common.sh` | 実行ファイルの 1 つの関数に寄せる。実行ファイルを起こさない `ccnavi-user-push-approved.sh` の写しは、一致をテストで確かめる |
| 実行ファイルの答え（`ops show` の JSON）を jq で読む | `ccnavi-user-lib-review.sh` の `op_field`・`reply_already_sent` | 1 行 1 項目で答えるサブコマンドを足す |
| パスを自分で小文字にして比べる | `ccnavi-review.sh` の依頼文の置き場の照合 | 実行ファイルの `pathcase` で比べる |
| 実行ファイルの規則の写し | `ccnavi-stage.sh` の `is_importable`、`ccnavi-branches.sh` の `names_number` | 実行ファイルに聞く |
