---
type: guide
title: sh と Go の分担に合っていない所
description: .ccnavi/scripts/ の sh のうち、sh と Go の分担に合っていない処理の一覧。sh ごとに Go で実装するものと sh で実装するもの、規則の写しと読み方のずれ
tags: [exe-boundary, sh-scripts]
keywords: [sh, 実行ファイル, Go, 分担, 一覧, 進捗, 段取り, 外との出入り, ccnavi-start.sh, ccnavi-git.sh, integrate, write-and-push, テスト]
---

# sh と Go の分担に合っていない所

sh と Go の分担は[実行ファイルの境界](exe-boundary.md)の「sh で実装するものと、Go で実装するもの」にある。ここには、今の sh のうち分担に合っていない処理を並べる。直したら「済み」の表へ動かす。直す手順は同じ文書の「分担に合っていない処理を直すとき」にある。表は「済み」「残り」の 2 つだけにする。

## 済み

| sh | Go で実装したもの | 実行ファイルの入口 | 備考 |
|---|---|---|---|
| `ccnavi-start.sh` | ホストの応答の読み取り（issue の 1 件、MR が閉じる issue（GraphQL のページ・本文の Closes・GitLab の closes_issues）、似た issue・MR、open の一覧、MR・issue の番号と URL） | `ccnavi start issue`・`linked-page`・`linked-body`・`linked-gitlab`・`similar`・`list-open`・`pick`（`go/internal/tickets/lifecycle/start`） | API に送る本文の組み立て（`jq -n`）は外との出入りなので sh |
| `ccnavi-start.sh` | 候補の振り分け（候補が複数・ホストを見られない・新しく作る・採用・作りかけ・古いブランチ・MR の無い候補などの道）と、採用の確認のうちローカルで決まるもの（MR の状態・フォーク・マージ先・保護されたブランチ） | `ccnavi start route`（`go/internal/tickets/lifecycle/start`） | 止めるときの文は sh |
| `ccnavi-start.sh` | ホストに届かないときの案内（`stop_host` の文。GitHub の MCP の手順、GitLab の認証を頼む次の一手） | `ccnavi start guidance`（`go/internal/tickets/lifecycle/start`） | 案内のログを書くことと、ホストの種類が読めないときに止めることは sh |
| `ccnavi-start.sh` | 引数の検査と `--repo` と origin の照合、識別子と同じ識別子の別ブランチの確認、マージ済み MR の判定、MR が閉じる issue の決め方、closed の issue の止め方、ワークツリーの作成・早送り・空の初期コミット・作ったものの片付け、テンプレートの数、結果と dry-run の出力 | `ccnavi start args`・`branch-ok`・`merged`・`resolve`・`issue-state`・`prepare`・`report`・`dry`（`go/internal/tickets/lifecycle/start`） | ls-remote・fetch・push とホストの API は sh。ワークツリーの作成と初期コミットは、ラッパーを通さず実行ファイルが git を打つ |
| `ccnavi-git.sh` | 引数だけで決まる判定（サブコマンドより前のオプション、全引数で止める形とその略、読むだけのサブコマンド、`cat-file`・`grep`・`branch`・`tag`・`remote`・`stash`・`add`・`commit`、通さないサブコマンド）と、そのオプションの許可リストの読み | `ccnavi git check`（`go/internal/tickets/lifecycle/gitwrap`） | 実行ファイルが無いとラッパーは止まる |
| `ccnavi-git.sh` | 状態を見るサブコマンド（`rm`・`restore`・`checkout`・`switch`・`merge`・`merge-file`・`fetch`・`pull`・`push`・`worktree`）の判定、守る場所の判定（承認済みチケットの置き場と ccnavi 自身を守る場所）、`worktree add` の起点の新しさ、push のゲートを掛けるか | `ccnavi git check`（`go/internal/tickets/lifecycle/gitwrap`） | ゲートの ls-remote と、fetch が失敗した印の書き込みは sh が行う |
| `ccnavi-git.sh` | 直接作業の取り込み（`integrate` / `integrate --abort` / `integrate-done`）の判定と状態操作（記録・一時ワークツリー・T と C の計算・コミット・ff-only・片付け） | `ccnavi git integrate`（`go/internal/tickets/lifecycle/land`） | ロックは sh が取る。コミットや fast-forward を git が断った理由の読み方は、sh の `ccnavi_git_refusal`（write-and-push などが使う）と同じ規則を写したもの |

## 残り

直す順は上から。処理は、そこに入る主な関数の名前で書く。

| sh | Go で実装するもの（いま sh にある） | sh で実装するもの | 備考 |
|---|---|---|---|
| `ccnavi-start.sh` | `--check`（`ccnavi_preflight`）の判定と出力、ホストを繋ぐときに止める文（origin が読めない・認証や権限が足りない）、候補の道ごとの止める文（候補が複数・MR の無い候補・作りかけなど）、slug が長いときの文 | ホストの API の取得と POST、`remote_heads`・fetch・push、`ccnavi-branches.sh` と `ccnavi-sync.sh` の呼び出し、段取りの順 | `--check` は `ccnavi-common-host.sh` と一緒に直す |
| `ccnavi-git.sh` | ログの書き出しと要約 | 環境の消去、実行ファイルへの受け渡し、integrate のロック、fetch・pull・push とゲートの ls-remote | |
| `ccnavi-common-state.sh` | `ccnavi_push_gate` の判定、`ccnavi_parent_trace`、`ccnavi_id_clash`、`ccnavi_git_refusal`、`ccnavi_record_*`、`ccnavi_integration` | `ccnavi_git_timed`、ゲートの ls-remote、実行ファイルに聞く口（`ccnavi_ask` など） | ゲートは素の push を打つ 3 経路が共有する |
| `ccnavi-sync.sh` | 親子の選び方、`sync_family`・`merged_present`・`sync_absent`・`sync_retired` の分岐、ff・merge・abort、origin/HEAD の向け直し、出力の文 | ls-remote、`fetch_one`、`ccnavi-review.sh merged` の呼び出し、ロック、trap | merge の abort は実行ファイルの 1 回の起動の中で持つ |
| `ccnavi-worktree.sh` | 引数と C1〜C7 の検査、`same_id_guard`、`make_candidate`、記録の書き出し、`worktree add`、報告の文 | ccnavi-git.sh 経由の fetch と ls-remote | 記録 `ccnavi-direct/<名前>` は integrate が読む |
| `ccnavi-fetch.sh` | ff merge・update-ref・symbolic-ref・ワークツリーの一覧の解析、`family`・`seen` の選び方、出力の文 | `ccnavi_fetch_git`、`ccnavi_fetch_retarget` の ls-remote と fetch、時間の枠、ロック | 実行ファイルを起こさずに動く作り。Go で実装する場合は、実行ファイルが無いときに親の早送りを省く |
| `ccnavi-branches.sh` | GitHub と GitLab の MR・issue の応答の整形（jq の 4 通り） | 一覧の取得 | 生の一覧を `branches --result` に渡す形にする |
| `ccnavi-common-host.sh` | `ccnavi_host_fail_status`・`ccnavi_host_graphql_blocked`・`ccnavi_host_fail_body` の解析、`ccnavi_host_access` の判定、preflight の集計と `fix` の順、`ccnavi_pf_line`・`ccnavi_pf_next` | `ccnavi_host_connect`・`ccnavi_host_api`・`ccnavi_host_pages`・`ccnavi_git_auth`・probe の取得、`ccnavi_find_tool` | `ccnavi_host_parse` は接続の前に必要。実行ファイルの `host.classify` と規則が重なる |
| `ccnavi-review.sh`・`ccnavi-common-review.sh` | `mr_state_classify`、`merged`・`undraft` の読み、`threads`・`reviews`・`find_marked` の解釈、`ready` の出力の読み取り | `api`・`gql`・`pages`、GraphQL が断られたときの REST への切り替え、ページの cursor の読み | `threads` の GitLab の枝は Chrome 拡張の `gitlab.ts` と同じ読み方。Go で実装し、WASM と共有する。`mr-state` の 2 行の形と終了コードは、ボードとの約束 |
| `ccnavi-common-write-and-push.sh` | `ccnavi_wap_commit`・`ccnavi_wap_unstage`・`ccnavi_wap_restore_written`・`ccnavi_wap_merge_begin` / `ccnavi_wap_unmerge` / `ccnavi_wap_commit_merge`・`ccnavi_wap_undo`・`ccnavi_wap_busy` | push と ls-remote、ロック、ゲートの呼び出し、`ccnavi-sync.sh` の入れ子の起動 | 書く、コミット、push、確かめ、戻してやり直す、の一続き。ローカルの書き込みと戻しは 1 回の起動にまとめる |
| `ccnavi-user-push-approved.sh` | `carry_paths`・`given_in`・`unsent_paths`、ツリーの列挙とリンクの検査、保護ブランチの判定 | push、ロック、ユーザだけが打つ入口 | 実行ファイルを起こさない作り（承認の直後、入れ替え中でも push できるように）。Go で実装する場合は、実行ファイルが無いときに何を省くかを先に決める |
| `ccnavi-user-lib-review.sh`・`ccnavi-user-decide.sh`・`ccnavi-user-close-early.sh` | `accept_threads` の検査、`reply_already_sent`、`resume_prepare` の読み | 返信・解決・issue・コメントの送信、y/N の確認、ロック | Chrome 拡張とホストで書く順が違う。共有するなら、先に順を揃える |
| `ccnavi-ticket.sh` | `ticket_id` の検査、approval-request の結果ファイルの読み、`ensure_child_worktree`、`ticket_unsent_parent_next` の文 | preflight、write-and-push の組み立て | |
| `ccnavi-stage.sh`・`ccnavi-dirs.sh` | パスとリンクの検査、読み替えの表、コピー、手順の文。`projects/` ごとの最新の並べ方 | — | ほぼ全部が Go で実装するもの |

分担に合っているもの: `ccnavi-launcher.sh`・`ccnavi-common.sh` の探し方・`ccnavi-common-log.sh`・`ccnavi-common-lock.sh`・`ccnavi-idle.sh`・`ccnavi-session-start.sh`・`ccnavi-user-agree.sh`・`ccnavi-clean.sh` と、`ccnavi-user-handoff.sh`・`ccnavi-user-prune.sh`・`ccnavi-user-mirror.sh`・`ccnavi-user-clean-branch.sh`（起動役・ログ・ロック・待機・ユーザだけが打つ入口で、実行ファイルへ渡すだけの作り）。

### 規則の写しと読み方のずれ

上の表の sh を直すより先に直せる。

| ずれ | 場所 | 直し方 |
|---|---|---|
| 保護するブランチの一覧が 3 か所にあり、形が揃っていない（`release-*` と `release/*`） | 実行ファイルの `gitwrap` の `protected`（push）と `land` の `checkName`（integrate の名前）・`ccnavi-user-push-approved.sh`。用途の違う写しとして、ブランチ名に使えない語の検査 `ccnavi-common.sh` の `ccnavi_is_branch`（実行ファイルの `ids.BranchProblem` の写し）も保護の名前を持つ | 実行ファイルの 1 つの関数で判定し、sh はそれに聞く。実行ファイルを起こさない `ccnavi-user-push-approved.sh` の写しは、一致をテストで確かめる |
| 実行ファイルの答え（`ops show` の JSON）を jq で読む | `ccnavi-user-lib-review.sh` の `op_field`・`reply_already_sent` | 1 行 1 項目で答えるサブコマンドを足す |
| パスを自分で小文字にして比べている | `ccnavi-review.sh` の依頼文の置き場の照合 | 実行ファイルの `pathcase` で比べる |
| 実行ファイルの規則の写し | `ccnavi-stage.sh` の `is_importable`、`ccnavi-branches.sh` の `names_number` | 実行ファイルに聞く |
| git が断った理由の読み方が 2 か所にある | `ccnavi-common-state.sh` の `ccnavi_git_refusal`（write-and-push・`ccnavi-sync.sh`・`ccnavi-fetch.sh`・`ccnavi-user-push-approved.sh`）と、実行ファイルの `land` の `refusal`（integrate-done） | 使う sh を Go で実装し直すときに、実行ファイルの 1 つの関数に寄せる |
