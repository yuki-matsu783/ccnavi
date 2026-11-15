---
type: guide
title: sh と Go の分担に合っていない所
description: .ccnavi/scripts/ の sh のうち、sh と Go の分担に合っていない処理の一覧。sh ごとに Go で実装したもの・するものと sh で実装するもの
tags: [exe-boundary, sh-scripts]
keywords: [sh, 実行ファイル, Go, 分担, 一覧, 進捗, 段取り, 外との出入り, ccnavi-start.sh, ccnavi-git.sh, integrate, write-and-push, テスト]
---

# sh と Go の分担に合っていない所

sh と Go の分担は[実行ファイルの境界](exe-boundary.md)の「sh で実装するものと、Go で実装するもの」にある。ここには、今の sh のうち分担に合っていない処理を並べる。直したら「済み」の表へ動かす。直す手順は同じ文書の「分担に合っていない処理を直すとき」にある。表は「済み」「残り」の 2 つだけにする。

## 済み

| sh | Go で実装したもの | 実行ファイルの入口 | 備考 |
|---|---|---|---|
| `ccnavi-start.sh` | ホストの応答の読み取り（issue の 1 件、MR が閉じる issue（GraphQL のページ・本文の Closes・GitLab の closes_issues）、似た issue・MR、open の一覧、MR・issue の番号と URL） | `ccnavi start issue`・`linked-page`・`linked-body`・`linked-gitlab`・`similar`・`list-open`・`pick`（`go/internal/tickets/lifecycle/start`） | API に送る本文の組み立て（`jq -n`）は外との出入りなので sh |
| `ccnavi-start.sh` | 候補の振り分け（候補が複数・ホストを見られない・新しく作る・採用・作りかけ・古いブランチ・MR の無い候補などの道）と、採用の確認のうちローカルで決まるもの（MR の状態・フォーク・マージ先・保護されたブランチ） | `ccnavi start route`（`go/internal/tickets/lifecycle/start`） | 止めるときの文は `ccnavi start stop`（下の行） |
| `ccnavi-start.sh` | ホストに届かないときの案内（`stop_host` の文。GitHub の MCP の手順、GitLab の認証を頼む次の一手） | `ccnavi start guidance`（`go/internal/tickets/lifecycle/start`） | 案内のログを書くことと、ホストの種類が読めないときに止めることは sh |
| `ccnavi-start.sh` | 引数の検査と `--repo` と origin の照合、識別子と同じ識別子の別ブランチの確認、マージ済み MR の判定、MR が閉じる issue の決め方、closed の issue の止め方、ワークツリーの作成・早送り・空の初期コミット・作ったものの片付け、テンプレートの数、結果と dry-run の出力 | `ccnavi start args`・`branch-ok`・`merged`・`resolve`・`issue-state`・`prepare`・`report`・`dry`（`go/internal/tickets/lifecycle/start`） | ls-remote・fetch・push とホストの API は sh。ワークツリーの作成と初期コミットは、ラッパーを通さず実行ファイルが git を打つ |
| `ccnavi-start.sh` | 止めるときの文（`fail` の文と、聞くために止める道（候補が複数・似た issue・タイトルが長い・MR の無い候補など）の文）、`--check` の結論の出力 | `ccnavi start stop`・`check-report`（`go/internal/tickets/lifecycle/start`） | 実行ファイルを呼べないときの文（ワークスペースが無い・実行ファイルが答えない・案内を組み立てられない）は sh |
| `ccnavi-common-host.sh` | 事前確認（`ccnavi_preflight`）の手元の項目（道具・origin・統合先・作成者）、項目ごとの結果と勧める対処、行と次の一手、`ccnavi_host_access` の権限の判定 | `ccnavi preflight plan`・`check`・`next`・`access`・`uid`（`go/internal/tickets/lifecycle/preflight`） | 接続・プロジェクトの取得・権限の問い合わせ・ls-remote と時間の枠は sh |
| `ccnavi-git.sh` | 引数だけで決まる判定（サブコマンドより前のオプション、全引数で止める形とその略、読むだけのサブコマンド、`cat-file`・`grep`・`branch`・`tag`・`remote`・`stash`・`add`・`commit`、通さないサブコマンド）と、そのオプションの許可リストの読み | `ccnavi git check`（`go/internal/tickets/lifecycle/gitwrap`） | 実行ファイルが無いとラッパーは止まる |
| `ccnavi-git.sh` | 状態を見るサブコマンド（`rm`・`restore`・`checkout`・`switch`・`merge`・`merge-file`・`fetch`・`pull`・`push`・`worktree`）の判定、守る場所の判定（承認済みチケットの置き場と ccnavi 自身を守る場所）、`worktree add` の起点の新しさ、push のゲートを掛けるか | `ccnavi git check`（`go/internal/tickets/lifecycle/gitwrap`） | ゲートの ls-remote と、fetch が失敗した印の書き込みは sh が行う |
| `ccnavi-git.sh` | 直接作業の取り込み（`integrate` / `integrate --abort` / `integrate-done`）の判定と状態操作（記録・一時ワークツリー・T と C の計算・コミット・ff-only・片付け） | `ccnavi git integrate`（`go/internal/tickets/lifecycle/land`） | ロックは sh が取る。git が断った理由は `refusal.Of`（下の行と同じ関数） |
| `ccnavi-git.sh` | ログの書き出し（見出しと全量）・要約・先頭と末尾の行・世代の切り捨て、失敗したときの案内（worktree remove の cwd、統合先の pull の衝突） | `ccnavi git report`（`go/internal/tickets/lifecycle/gitlog`） | git を打つのと fetch が失敗した印は sh。出力は一時ファイルで渡す |
| `ccnavi-common-state.sh` | push のゲートの判定（親のワークツリーか・送った形跡・次の一手）、送った形跡（`ccnavi_parent_trace`）、同じ識別子のブランチ（`ccnavi_id_clash`）、git が断った理由（`ccnavi_git_refusal`）、統合先と案内（`ccnavi_integration`・`ccnavi_integration_hint`）。`ccnavi_record_*` は使う sh が無くなったので消し、記録の読み書きは `integrate.ReadRecord`・`WriteRecord`（読み方は `tree.ParseRecord`）の 1 つにした | `ccnavi tree gate`・`tree parent <ツリー> <名前> <ブランチ>` の `trace`（`go/internal/entry/shask`）、`ccnavi worktree clash`（`ids.IDClash`）、`ccnavi git refusal`（`go/internal/tickets/lifecycle/refusal`）、`ccnavi git integration`（`integrate.IntegrationName`・`IntegrationHint`） | sh に残すのは `ccnavi_git_timed`、ゲートの ls-remote、実行ファイルに聞く口。実行ファイルが答えないとき（`ccnavi-fetch.sh`・`ccnavi-user-push-approved.sh`）に省くものは各 sh の先頭に書いた |
| 規則の写し | 保護するブランチ（`ids.Protected`。`gitwrap` の push・`land` の名前・`BranchProblem`・チケットの名前・Chrome の書き込み）、`ops get`（操作記録の 1 行 1 項目）、`path under`（`ccnavi-review.sh` の依頼文の置き場）、`stage importable`（`ccnavi-stage.sh`）、`branches pulls`（`ccnavi-branches.sh` の GitHub の PR と元ブランチ名の番号） | 左の名前のサブコマンド | 実行ファイルを起こさない `ccnavi-user-push-approved.sh` と `ccnavi_is_branch` は写しの `ccnavi_is_protected` を使い、`tests/fixtures/ident-cases.json` で一致を確かめる |
| `ccnavi-common-write-and-push.sh` | 途中の操作の確かめ（`ccnavi_wap_busy`）、コミット（`ccnavi_wap_commit`・`ccnavi_wap_unstage`）、書いたものの戻し（`ccnavi_wap_restore_written`）、合流（`ccnavi_wap_merge_begin`・`ccnavi_wap_unmerge`・`ccnavi_wap_commit_merge`）、比較つきの戻し（`ccnavi_wap_undo`）、取り込み直したら届いていたかの確かめ、送った中身の申し送り（`file=`）。6〜8（元の先頭・合流・書く・コミット）は 1 回の起動で、失敗と INT・TERM・HUP の戻しもその中で持つ。approval-request のワークツリーを戻さない決まりと、戻さない record-risk の記録（`wap.Kept`）も実行ファイルが決める | `--record-commit <文>`（`--record-merge <sha>`。答えは `<一覧>.answer`）、`ccnavi write-and-push busy`・`commit`・`undo`・`landed`（`go/internal/tickets/lifecycle/wapmerge` の `local.go`、`go/internal/entry/cli/wapwrite.go`） | sh に残すのは push と ls-remote、ロック、ゲートの呼び出し、`ccnavi-sync.sh` の入れ子の起動。sh の trap は前景の子を待ってから動くので、起動の前に答えのファイルを覚え、push の前に切られたら `write-and-push undo` で戻させる。git の期限切れは TERM を送ってから殺す（`git.Options.Graceful`。index.lock を残さない） |
| `ccnavi-user-push-approved.sh` | 取り込み済みの親子のチケットの途中の操作の確かめとコミット（`write-and-push busy`・`commit`。実行ファイルが取り込み済みと答えたときだけ通る道）、push に失敗したツリーの未送信のパス（`unsent_paths`） | `ccnavi write-and-push busy`・`commit`・`unsent` | 実行ファイルを起こさずに動く作り。ツリーの列挙とリンクの検査、置き場の変更のパス（`carry_paths`・`given_in`）、親子のチケットでないツリーのコミット、保護されたブランチの写し（`ccnavi_is_protected`）は、実行ファイルが無くても承認を送れるように sh に残す。実行ファイルが無いときは未送信のパスの一覧を出さない（先頭の説明に書いた） |
| `ccnavi-user-lib-review.sh`・`ccnavi-user-decide.sh`・`ccnavi-user-close-early.sh` | 受け入れの計画の読み（スレッドの数・操作ID・返信の本文）と、ホストに書く前の確かめ（id の字・GitLab の 16 進・取り直した結果に在るか。`accept_threads`）、返信を送り済みか（`reply_already_sent`。記録の印とホストのスレッドの最後のコメント）、`--resume` の記録の読み（`resume_prepare` の要約・選択・ダイジェスト） | `ccnavi accept plan`・`accept threads`（`host.ReadAcceptThreads`・`AcceptCheck`）、`ccnavi ops replied`（`host.RepliedByHost`）、`ccnavi ops resume`（`oplog.Record.ResumeLines`） | 返信・解決・issue・コメントの送信、y/N の確認、ロックは sh。Chrome 拡張とホストで書く順（返信と解決）が違うので、順は共有していない |

## 残り

直す順は上から。処理は、そこに入る主な関数の名前で書く。

| sh | Go で実装するもの（いま sh にある） | sh で実装するもの | 備考 |
|---|---|---|---|
| `ccnavi-sync.sh` | 親子の選び方、`sync_family`・`merged_present`・`sync_absent`・`sync_retired` の分岐、ff・merge・abort、origin/HEAD の向け直し、出力の文 | ls-remote、`fetch_one`、`ccnavi-review.sh merged` の呼び出し、ロック、trap | merge の abort は実行ファイルの 1 回の起動の中で持つ |
| `ccnavi-worktree.sh` | 引数と C1〜C7 の検査、`same_id_guard`、`make_candidate`、記録の書き出し、`worktree add`、報告の文 | ccnavi-git.sh 経由の fetch と ls-remote | 記録 `ccnavi-direct/<名前>` は integrate が読む |
| `ccnavi-fetch.sh` | ff merge・update-ref・symbolic-ref・ワークツリーの一覧の解析、`family`・`seen` の選び方、出力の文 | `ccnavi_fetch_git`、`ccnavi_fetch_retarget` の ls-remote と fetch、時間の枠、ロック | 実行ファイルを起こさずに動く作り。Go で実装する場合は、実行ファイルが無いときに親の早送りを省く |
| `ccnavi-branches.sh` | GitHub と GitLab の MR・issue の応答の整形（jq の 3 通り。GitHub の issue の PR は `branches pulls` で済み） | 一覧の取得 | 生の一覧を `branches --result` に渡す形にする |
| `ccnavi-common-host.sh` | `ccnavi_host_fail_status`・`ccnavi_host_graphql_blocked`・`ccnavi_host_fail_body` の解析、`ccnavi_host_probe` の応答の読み（JSON のオブジェクトかを jq で見る） | `ccnavi_host_connect`・`ccnavi_host_api`・`ccnavi_host_pages`・`ccnavi_git_auth`・probe の取得、`ccnavi_find_tool` | `ccnavi_host_parse` は接続の前に必要。実行ファイルの `host.classify` と規則が重なる |
| `ccnavi-review.sh`・`ccnavi-common-review.sh` | `mr_state_classify`、`merged`・`undraft` の読み、`threads`・`reviews`・`find_marked` の解釈、`ready` の出力の読み取り | `api`・`gql`・`pages`、GraphQL が断られたときの REST への切り替え、ページの cursor の読み | `threads` の GitLab の枝は Chrome 拡張の `gitlab.ts` と同じ読み方。Go で実装し、WASM と共有する。`mr-state` の 2 行の形と終了コードは、ボードとの約束 |
| `ccnavi-user-decide.sh`・`ccnavi-user-close-early.sh`・`ccnavi-user-lib-review.sh` | 取ってきた結果の MR の番号と URL の読み（`.mr.number`・`.mr.url`）、`--choose-out` の答えの読み、置いた答えの JSON に issue の URL と警告を足すこと、issue の作成の答えの読み | API に送る本文の組み立てと応答の id の有無 | 受け入れの計画と返信の印は済み（上の「済み」） |
| `ccnavi-ticket.sh` | `ticket_id` の検査、approval-request の結果ファイルの読み、`ensure_child_worktree`、`ticket_unsent_parent_next` の文 | preflight、write-and-push の組み立て | |
| `ccnavi-stage.sh`・`ccnavi-dirs.sh` | パスとリンクの検査、読み替えの表、コピー、手順の文。`projects/` ごとの最新の並べ方 | — | ほぼ全部が Go で実装するもの |

分担に合っているもの: `ccnavi-launcher.sh`・`ccnavi-common.sh` の探し方・`ccnavi-common-log.sh`・`ccnavi-common-lock.sh`・`ccnavi-idle.sh`・`ccnavi-session-start.sh`・`ccnavi-user-agree.sh`・`ccnavi-clean.sh` と、`ccnavi-user-handoff.sh`・`ccnavi-user-prune.sh`・`ccnavi-user-mirror.sh`・`ccnavi-user-clean-branch.sh`（起動役・ログ・ロック・待機・ユーザだけが打つ入口で、実行ファイルへ渡すだけの作り）。
