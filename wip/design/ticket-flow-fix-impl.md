---
type: report
title: ccnavi チケット作業フロー修正 実装設計書
description: 原設計書の R1〜R10 を main（c715ceaa）の実装に反映するための実装設計。段階1〜5の構成と部品を決める
tags: [design-doc, tickets]
keywords: [チケット, ゲート, dry-run, 操作記録, oplog, 受け入れ台帳, 役割のガード, 実装設計]
---

# ccnavi チケット作業フロー修正 実装設計書

## 目的

原設計書「ccnavi チケット作業フロー修正依頼書・仕様書」の R1〜R10 を、main の最新（c715ceaa）の実装に反映するための実装設計書です。原設計書が実装設計に任せた項目（保存形式・復旧の入口・判定の方式など）もここで決めます。敵対的レビューの指摘（A1〜A6、B1〜B9、C群）を取り込んだ版です。

読み手は、ccnavi を改修する開発者と、設計をレビューするユーザです。

完成の条件は次の3つです。

- 5つの段階をすべて実装し、原設計書の AT1〜AT31 と、この設計書で足したテストに合格すること。
- ソースと組み立て済みの実行ファイルの両方で、受け入れテストが通ること。
- GitHub と GitLab の実機で、転記と照合を確かめること。

## 範囲

### 対象

- R1：dry-run の影響を受けないチケットゲート（承認済みの置き場と操作記録の保護を含む）、判定できないときのツール別の扱い
- R2：ユーザが子を手で動かした（review→done）ことを、ccnavi が自分で動かした場合と区別して読むこと。confirm での版付きの確定と、親ごとの受け入れ台帳
- R3：続きの子と issue に、指摘本文の全文を保存し、安全に転記すること
- R4：fix を選んだことを承認として扱う続きの子と、通常の子の作業への接続
- R5：見せた指摘と、適用の直前に取り直した結果の照合（指摘の追加は許し、版で照合する。ホストに書く前と後の2段で照合する）
- R6：操作記録（`logs/state/ops/`）、送信の段階、結果が分からない場合の扱い、復旧の入口
- R7：サブエージェントの役割のガードを、公開の入口すべてと、通常の呼び方すべてに広げること
- R8：子の終了結果の分類・記録・通知
- R9：案内文の矛盾の修正
- R10：diagnose・lint・ボードの JSON 契約・文書・移行の告知

### 対象外

- 原設計書の「対象外」に挙げたものすべて（難読化への網羅的な対策、sh／bash 以外のシェル、全経路での Approve の必須化、スレッドの返信をすべて取得すること、分散ロック、厳密に1回だけ届けること、作業中に追加された fix から次の子を自動で作ることなど）
- 範囲外の変更を自動で元に戻す機能
- `cd` で移動してから実行する形の例外（扱いは「未決事項」で確認します）
- 保存済みの判断を、エージェントが送り直す入口（送り直しはユーザだけが行います）
- ホスト上でスレッドの解決状態を変えること（手動移動による受け入れでは、スレッドを解決済みにしません）

## 前提と制約

### 調査で分かった現状（原設計書の前提との差）

| 項目 | 現状 | 設計への影響 |
|---|---|---|
| 承認済みの置き場の保護 | 組み込みのチケットゲート（`ticket_guard.guard_rules`）が守るのは、`wip/proposals/` の下の状態の置き場だけ。`.ccnavi/approved/`（`done/`、`phases/`、マーカー、`flows/`）を守っているのは、ルールファイルの `guard-approved-tickets` と自己防衛（`CCNAVI_GUARD_CORE_FILES` で切り替わる）だけ | 承認済みの置き場をゲートに入れる必要がある（A1） |
| 受け入れの仕組み | 「ホストでスレッドを解決済みにする」ことが受け入れになっている。マーカーの `accepted`（キーだけ）は記録で、confirm はホストで未解決のスレッドだけを数える | 「未解決のまま受け入れる」は新しい仕組みになる。版を持たない古い記録で、判定に効いているものはない |
| 未解決の数え方 | MR 全体の未解決のスレッドを、付いた時刻で絞らずに数える | 受け入れの記録をフェーズのマーカーに置くと、次のフェーズで届かない（A5） |
| マーカーの寿命 | フェーズを開き直すと（子を足す承認、decide の fix）、そのフェーズのマーカーが消える | 受け入れはマーカーとは別に保存する（A5） |
| 子の移動の順番 | decide・confirm・close-early は、マーカーを置く前に子を `done/` へ動かす | 置き場の状態だけでは、手動で動かしたのかどうか分からない（A2） |
| review 行きの判定 | finish が、その時点の `review_required`（`ops._needs_review`）で置き場を決める。`review_kind` は、あとから実績のリスク・計画の項・延期の引き受けで変わる | 計算し直しても「review 行きだったか」は求まらないので、記録として残す（A3） |
| dry-run | `judge.refuse()` と `modes.fail_closed()` は、dry-run だと理由に関わらず通す | R1 で、止めている箇所ごとに分類する |
| 内部エラー | ツールの種類に関わらず PreToolUse を止める（dry-run では通す）。設定が読めずに落ちたときは、チケット制御が有効かどうか分からない | ツール別の扱いを決め、判定できないときの既定を決める（B1） |
| 読み取りの判定 | `phase_forms._only_readers` は、区切りで分けた各コマンドの先頭の語しか見ない | 判定できないときの許可リストに流用できない（B1） |
| decide の本文 | `thread_label()`（URL・位置・本文の1行目）を使っている | 本文全体を保存する形に置き換える |
| decide の照合 | 全体のダイジェストが一致するかだけを見る。ホストに書く前（`--accept-plan`）と後（`--accept-done`）の2回照合する | 指摘ごとの版で照合する形に置き換える（B5） |
| 投稿 | decide の issue とコメントは、置いた後に送る。下書きは次の decide で消える。`create_issue` の成否は、URL が空かどうかで判断している。ready の補助コメントは失敗しても何も言わない | 操作記録と送信の段階に置き換える |
| request の二重投稿の防止 | 本文と親の head から作る鍵を目印に入れている（別のPCでも同じ鍵になる） | この鍵は残す（B3） |
| 作成者 | GitHub のスレッドは作成者を持たない。GitLab は作成者の id を持つ | 照合に使う作成者の取り方を sh 側で決める（B4） |
| 記録の削除 | `ccnavi user prune` とセッション開始時の後始末が、記録と state を消す | 未完了の操作記録は消さない（B2） |
| write-and-push | コミットするのは、fsio が記録したパスだけ | ユーザがまだコミットしていない移動は運ばれない（B7） |
| サブエージェントのガード | 止めるのは `sh`／`bash` で呼ぶ `ticket`・`review`・`git.sh` の一部の副命令だけ。実行ファイルを直接呼ぶ形は、`CCNAVI_GUARD_TICKET_APPROVAL=disable` で外れる止めにしか掛かっていない | （B 群） |
| SubagentStop | 親のツリーから起動されたときは、親のすべての子を調べ、1回差し戻す。差し戻しの記録があるだけで「差し戻されたまま」と通知する。印は出力より前に消す | 担当の子を見分け、終了結果を分類する（B9） |
| スレッドの取得 | GitHub・GitLab とも、最初のコメント全文を取る | 全文の範囲は最初のコメントにする。再提起を見分けるため、最後のコメントの id と時刻も取る（B6） |
| 既存のファイル名 | `tickets/ops.py` はすでにある（start・finish・cancel の本体） | 操作記録は `tickets/oplog.py` にする（A6） |
| reviewed マーカーを作る処理 | `core_base.reviewed_mark`、`review.reviewed_mark`、Chrome の書き込みの3か所 | 1か所にまとめる |
| 共通の sh | 原設計書の `ccnavi-common-cl.sh` は存在しない | `ccnavi-common-write-and-push.sh` と読み替える |

### 守る条件

- 実行ファイルはネットワークに出ません。ホストへの読み書きは sh と Chrome 拡張が行います。git の読み取りは実行ファイルでも行えます。
- 原設計書の「維持・確定する方針」1〜10と、設定の関係の表に従います。
- ユーザ専用の操作（`ccnavi-user-*.sh`・`ccnavi user …`）は、エージェントからは今までどおり止めます。
- 内部の期限（3秒）は、hook の期限（10秒）より短いままにします。
- ボードの JSON 契約を変えるときは版を上げ、古い画面からの選択は断ります。

## 構成

段階ごとに親チケットの計画の項（フェーズ）を立て、前の段階が終わってから次へ進みます。段階5は、各段階の終わりに少しずつ進めます。

| 段階 | 対象 | 主に直す場所 | 依存 |
|---|---|---|---|
| 1 判定の土台 | R1・R7、R9 の文言 | `hook/judge.py`、`tickets/ticket_guard.py`（承認済みの置き場と操作記録の組み込みゲート）、`infra/modes.py`、`entry/cli.py`（`_internal_error`・`_hook_crashed`）、`tickets/user_only.py`、`tickets/phase_forms.py`、`tickets/phase.py`（`hold_reason`）、`tickets/review_decide.py`（案内文） | なし |
| 2 判断の保存と送信 | R3・R4・R5・R6 | 新しく作る `tickets/oplog.py`、`tickets/review_decide.py`、`tickets/review_close.py`、`tickets/approval_ops.py`、`tickets/review.py`、`tickets/ops.py`（finish が `finished_to` を書く）、`.ccnavi/scripts/ccnavi-user-decide.sh`・`ccnavi-user-lib-review.sh`・`ccnavi-user-close-early.sh`・`ccnavi-review.sh`・`ccnavi-common-review.sh`、`records/prune.py`、VS Code のボードの decide 画面 | 段階1 |
| 3 手動移動 | R2 | `tickets/phase.py`（Phase）、`tickets/core_review.py`、`tickets/review.py`、受け入れ台帳（`approval_marks`）、`entry/status.py`、hook の Stop・UserPromptSubmit の案内、Chrome の `core/reviewed.ts`・`write.ts`、VS Code のボード | 段階2 |
| 4 子の終了 | R8 | `hook/subagent.py`、`hook/events.py` | 段階1 |
| 5 横断 | R10 | `entry/diagnose*.py`、`entry/lint*.py`、README、`docs/design/`、関連する ADR、`.ccnavi/docs/`、配布文書 | 各段階 |

段階どうしで共有するデータは次の4つです。

- **指摘の版：** `review.thread_version()` の1か所で作り、CLI と Chrome（同じ Python のコア）が使います。
- **受け入れ台帳：** `.ccnavi/approved/phases/<親>/accepted.json`。git で共有し、どのフェーズの confirm・decide からも読みます。
- **子の経路の欄：** 子の frontmatter に書く `finished_to` と `settled_by`。git で共有します。
- **操作記録：** `logs/state/ops/<操作ID>.json`。git の外に置き、チケットゲートで守ります。

## 部品

### 段階1：ゲートの分類（R1）

- 役割：止めている箇所ごとに分類を持たせ、dry-run のときの扱いを分類で決めます。
- 使い方：`refuse(..., kind=GENERAL|GATE|AUX)`。dry-run で報告だけにするのは GENERAL だけで、GATE と AUX は dry-run でも拒否します。拒否文には「dry-run でもこの止めは効く」と1行添えます。
- 頼るもの：`judge.decide_before` の各分岐、`ticket_verdict`、`phase.held_phase`、`ticket_guard`。

| 分類 | 箇所 |
|---|---|
| GATE | ・サブエージェントによる状態の操作と、実行ファイルを直接呼ぶ形（後述の役割の表）<br>・親のツリーへの書き込みと git の操作<br>・子のワークツリーからの push<br>・レビュー待ちの止め<br>・範囲・チケットの deny と ask、親のツリー、信頼できないチケット<br>・プロジェクトの取り違え<br>・フローのロック<br>・ユーザの判断の経路（承認・decide・入口を読めない形）<br>・提案の状態の置き場の直接変更（`ticket_guard.guard_rules`）<br>・**承認済みの置き場への書き込み**（新しく組み込みのゲートにする。承認済みチケット、`doing/`・`done/`、`phases/` のマーカー・子の記録・受け入れ台帳、`flows/`。Write・Edit・NotebookEdit と、シェルからの書き込みの形の両方）<br>・**操作記録の置き場**（`logs/state/ops/`）への書き込み |
| AUX | ・生の git<br>・保護済みの sh への環境変数の指定<br>・書き直しを求める形のうち、「呼び手がサブエージェント」または「解析した層に ccnavi の入口が含まれる」もの |
| GENERAL | ・ルールファイルの deny と ask<br>・設定ファイル・hook・ラッパースクリプトの自己防衛（`CCNAVI_GUARD_CORE_FILES` の対象）<br>・記録の削除（`DENY_RECORDS_PRUNE`。ただし未完了の操作記録は prune 自体が消さない）<br>・上記以外の書き直しを求める形<br>・`>` の数の上限 |

- チケット制御が無効なときは、GATE を足しません。生の git は今までどおりいつも止め、そのときは GENERAL として扱います。
- `CCNAVI_GUARD_TICKET_APPROVAL=disable` で外れるのは、今と同じ「承認経路」の形だけです。サブエージェントへの禁止、範囲、レビュー、承認済みの置き場の保護は外れません。
- `CCNAVI_GUARD_CORE_FILES` で切り替わるのは GENERAL の自己防衛だけです。環境変数の指定の止めは AUX に移します。

### 段階1：判定できないときの扱い（R1）

- 役割：内部例外・状態が読めない・内部の期限切れのときに、ツールの種類から結論を決めます。
- 使い方：`modes.fail_closed(mode, tool, payload)` を、`cli._internal_error`、`_hook_crashed`、payload が読めないときの経路、`judge` の期限切れの経路から呼びます。

| 状況・ツール | 結論 |
|---|---|
| payload が読めない（ツールが分からない） | 止める |
| 設定が読めない（チケット制御の値が分からない） | チケット制御は有効とみなし、下の表に従う |
| Read・Grep・Glob | 組み込みの最低限の deny（認証情報の形、`.git` の中、承認済みの置き場と操作記録のうち秘密に当たるもの）を、設定を読まずに評価する。当たらなければ通す。この評価もできなければ止める。今の enable（すべて止める）より緩むことを文書に書く |
| Write・Edit・NotebookEdit・Agent | 止める（dry-run でも） |
| Bash・PowerShell | 判定できないとき専用の、狭い許可リストに当たるときだけ通す。それ以外は止める |
| 未知のツール | 止める（dry-run では通す。保証の対象外と文書に書く） |

- 判定できないとき専用の許可リスト：`shellread` で最後まで読めること、リダイレクト・置換・ヒアドキュメント・glob・連結が無いことを条件に、単独のコマンド `pwd`・`ls`・`cat <パス>`・`head <パス>`・`tail <パス>`・`wc <パス>` と、`sh <ルート>/.ccnavi/scripts/ccnavi-ticket.sh status|--help` だけを通します。パスは最低限の deny に当てます。`less`・`rg`・`echo`・`grep` は入れません。既存の `_READERS` は流用しません。
- 文書と diagnose には、「Claude Code 側の強制終了・hook が起動しない・外部のタイムアウトは止められない」ことを書きます。

### 段階1：役割のガード（R7）

- 役割：サブエージェントからの状態の操作と公開の操作を止めます。
- 使い方：`user_only` に「スクリプト×副命令」の表を持ち、`phase_forms.forbidden` がそこから組み立てます。
- 止める呼び方：`sh|bash`（選択肢付きも含む）、直接実行（`./x.sh`・絶対パス・相対パス・引用したパス）、`exec`・`command`・`source`・`.`、既存の解析で外せる前置きの後ろ。
- 実行ファイルを直接呼ぶ形（`ccnavi ticket start` など、`user_only.SUBS` にある状態操作）も、サブエージェントからは `CCNAVI_GUARD_TICKET_APPROVAL` の値に関係なく止めます。

| スクリプト | 副命令 | サブエージェント |
|---|---|---|
| ccnavi-ticket.sh | status・next-id・`--help`・learn | 許可 |
| ccnavi-ticket.sh | start・finish・cancel・propose・record-risk・judge（judge は「未決事項」で確認） | 禁止 |
| ccnavi-ticket.sh | 表に無い副命令 | 禁止 |
| ccnavi-review.sh | fetch・origin・merged・`decide <N> --preview`・`--help` | 許可 |
| ccnavi-review.sh | request・confirm・comment・ready、表に無い副命令 | 禁止 |
| ccnavi-git.sh | push・worktree・integrate・integrate-done | 禁止 |
| ccnavi-git.sh | それ以外 | 今の条件のまま（`wrapguard` による親のツリーと自己防衛の検査）。表に無い副命令を既定で禁止にはしない |
| ccnavi-start.sh・ccnavi-sync.sh・ccnavi-worktree.sh・ccnavi-clean.sh | すべて | 禁止 |
| ccnavi-stage.sh | 子のワークツリーの中での写しと `--commands` | 許可。それ以外の場所では禁止 |
| ccnavi-fetch.sh・ccnavi-branches.sh | すべて | 許可（読み取り） |
| ccnavi-launcher.sh | `--docs`・`--lint`・`--test`・`--explain`・`--help` | 許可 |
| ccnavi-user-*.sh、`ccnavi user …` | すべて | 禁止（今のまま。メインも禁止） |

- sh の中の処理は hook を通りません。sh の側でも行っている検査（子の push の検査など）は残します。

### 段階1：案内の修正（R9）

- `phase.hold_reason`・`announce`：「子の成果を親ブランチへ合流し」を「子が合流済み（finish 済み）であることを確かめ」に直します。
- `review_decide._decided_prompt`：「サブエージェントには渡さない」を「状態の操作はメインが打つ。実装はサブエージェントに任せてよい」に直します。
- `review_decide._followup_next`：origin があるときは `ccnavi-ticket.sh start <子>` だけを案内します。origin が無いときだけ `worktree add` を添えます。
- フィードバック計画の改版の長い案内は、最終フェーズが終わったときに1回だけ出します。
- 対話の成功文に内部の JSON を混ぜません。判断の保存・適用・共有・投稿・レビュー待ちを、それぞれ別の言葉で区別して言います。

### 段階2：操作記録 `tickets/oplog.py`（R6）

- 役割：ユーザの判断と外部への書き込みを、副作用を起こす前に保存します。部分的に成功した操作や打ち直しを、同じ記録から復旧します。
- 使い方：`oplog.begin(...) -> op_id`、`stage_set(op_id, stage_id, state, url, account)`、`pending()`、`load(op_id)`。
- 保存する形（`logs/state/ops/<操作ID>.json`）：

| 欄 | 中身 |
|---|---|
| version | 1 |
| id | UUID。判断を確定したときに振り、復旧では同じ値を使い回す |
| kind | decide・close-early・request・comment・ready |
| repo | ホストの種類・ホスト・パス |
| parent・phase・mr・request_head | 対象 |
| shown | 見せた指摘の［キー・URL・位置・版・最後のコメントの id と時刻・本文］ |
| choices | 指摘ごとの選択 |
| plan | 予定した子の識別子、置くマーカー、台帳に足す項目 |
| applied | 適用したもの［ホストで解決したスレッド・作った子・動かした子・置いたマーカー・台帳の項目・push した先頭］ |
| stages | 送信の段階［id・種類・状態（pending・sent・unknown）・結果の URL・送ったアカウント・試した回数］ |
| created_at・updated_at | 日時 |

- 記録には構造化した中身だけを持たせ、送る本文は持ちません。本文は送るたびに `shown`・`choices` から作り直します（改ざんしにくくするため）。記録の置き場はチケットゲート（GATE）で守ります。
- 保存できなければ何もしません（返信・解決にも進みません）。
- 同じPCでの並行実行は、操作IDごとのロック（`ccnavi-common-lock.sh`）で防ぎます。別のPCでは、照合で送ったかどうかを確定できなければ止めます。
- 記録を消すのは、すべての段階が `sent` になったときと、ready の片付けでその親の記録がすべて終わっているときだけです。`ccnavi user prune`、セッション開始時の後始末、そのほかの後始末は、未完了の記録を消しません。prune の下見（`--preview`）では「残す」と表示します。
- 古い下書き（`review-decide-*.md`・`review-issue-*.md`）が残っていれば、`--resume` が記録に取り込みます。黙って消すことはしません。

### 段階2：送信の照合（R6）

- 目印：本文の末尾に `<!-- ccnavi:op=<操作ID> stage=<段階ID> -->` を足します。既存の先頭の目印（`<!-- ccnavi:request …`・`<!-- ccnavi:decide -->` など）と、GitLab で自分の投稿を見分ける判定（本文が `<!-- ccnavi:` で始まるか）は変えません。sh の中で既存の目印を先頭一致で探している箇所は、すべて点検します。
- request の目印に入っている、内容から作る鍵（本文と head のハッシュ）は残し、操作IDは足すだけにします。別のPCでの打ち直しでは、内容の鍵で二重投稿を防ぎます。
- 作成者の取り方（sh の仕様）：GitHub の issue とコメントは REST の `.user.login`、GitLab は `.author.username` を使います。段階ごとに、送ったアカウントを記録します。

| 照合の結果 | 扱い |
|---|---|
| 目印が一致し、送ったアカウントの作成で1件だけ | `sent`。URL を保存する |
| 一覧を上限で打ち切らずに最後まで読み、0件だった | 未送信と確定する。同じ段階IDで送る |
| 一覧を上限で打ち切った、作成直後で反映が遅れているかもしれない、今のトークンの持ち主が送ったアカウントと違う | `unknown`。自動では送らない |
| 複数件ある、読めない | 止めて、確かめ方を表示する |

- issue の照合には、検索 API を使いません。そのリポジトリの issue の一覧を、作成者で絞って新しい順に読みます。

### 段階2：操作ごとの段階表（R6）

| 操作 | 段階（この順で進む） | 失敗したときに未完了とするもの | 工程 |
|---|---|---|---|
| decide | 1 記録の保存 → 2 版の照合（1回目）→ 3 受け入れる分のスレッドへ返信・解決 → 4 取り直して版を照合（2回目）→ 5 ローカルへの適用（子の移動と `settled_by`、台帳、続きの子かマーカー）→ 6 write-and-push → 7 issue → 8 記録のコメント | 3 が失敗したら何も置かない。7・8 は「引き継ぎ未完了」 | 7・8 が失敗しても続けられる |
| close-early | 1 判断と残った作業の保存 → 2 返信・解決 → 3 既存の状態遷移（`settled_by: close-early`）→ 4 write-and-push → 5 issue → 6 コメント | 5・6 は「引き継ぎ未完了」 | 続けられる |
| request | 1 本文の保存（`prepare`）→ 2 投稿 → 3 結果の保存 → 4 依頼のマーカー → 5 write-and-push | 2〜4 が失敗したら「依頼済み」にしない | ユーザ待ちへ進まない |
| comment | 1 本文の保存 → 2 投稿 → 3 結果の保存 | 投稿 | 影響しない |
| ready（操作名は元の画像の下端で切れており、ready と推定） | 1 材料の保存 → 2 退避と `wip/` の削除 → 3 write-…（以降は元の画像で欠落） | 4 が失敗したら ready は未…（以降は欠落） | 4 まで済めば ready の主な…（以降は欠落） |

## 欠落している箇所

元の資料は写真から書き起こしたもので、次の箇所が欠けています。

- 「操作ごとの段階表」の ready の行は、途中で切れています。
- 「止める呼び方」などの記述は、書き起こし時にバッククォートが閉じていない箇所がありました（そのまま書き写しました）。
- 「未決事項」の節は写っておらず、本文中で参照されている項目（`judge` の扱い、`cd` で移動してから実行する形の例外）の結論は不明です。
- 原設計書（R1〜R10、AT1〜AT31）はリポジトリに見当たりませんでした。
