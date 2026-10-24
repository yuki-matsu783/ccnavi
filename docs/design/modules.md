---
type: design
title: モジュール構成
description: src/ccnavi/ の各サブパッケージとモジュールの役割の表
tags: [design-doc, modules]
keywords: [モジュール, 構成, infra, records, policy, tickets, hook, entry, サブパッケージ]
---

[設計書の入口に戻る](../design.md)

# モジュール構成

`src/ccnavi/` のサブパッケージとモジュールごとの役割を表にする。サブパッケージの層（読む向き）と、リポジトリのトップレベルの置き場は [development.md](../development.md) にある。

| 場所 | 中身 |
|---|---|
| `src/ccnavi/__main__.py` | 入口。標準入出力を整えて `entry/cli.py` の `run` を呼ぶだけ |
| `src/ccnavi/infra/` | 土台。ファイル・git・パス照合・hook の入出力・設定・シェルの読み・ワークツリー。どのサブパッケージも読まない |
| `src/ccnavi/infra/hookio.py` | stdin の payload の解釈と、stdout に返す応答の組み立て |
| `src/ccnavi/infra/globmatch.py` | glob から正規表現への翻訳 |
| `src/ccnavi/infra/shellread.py` | コマンド文字列のうち実際に実行される部分の切り出し |
| `src/ccnavi/infra/shellread_scan.py` | 原文の走査。引用の状態を持ったまま、置換・ヒアドキュメント・コメント・改行を片付ける |
| `src/ccnavi/infra/shellread_words.py` | コマンドの語の見分け。コマンド名・実行役のコマンド・オプションの幅・リダイレクト |
| `src/ccnavi/infra/shellread_cd.py` | `cd` で移った先の追跡 |
| `src/ccnavi/infra/settings.py` | 環境と設定ファイルからの設定解決 |
| `src/ccnavi/infra/gitstate.py` | 作業ツリーで実際に何が変わったかを git から読む |
| `src/ccnavi/infra/tree.py` | ワークツリー（git worktree）の特定。判定の鍵はファイルの行き先 |
| `src/ccnavi/infra/directrec.py` | 直接作業の記録（`ccnavi-direct/<名前>`）を読む。横で作業する指定がいま有効か（作業ワークツリーの有無と共通ディレクトリの一致を含む）だけを答える。書かない |
| `src/ccnavi/infra/modes.py` | enable / dry-run / disable の 3 値と終了コード。モードの解決 |
| `src/ccnavi/infra/gitcmd.py` | git を 1 回起動する。期限（単調時計の絶対時刻）つきの起動（`run_until`）は、期限を過ぎてから起動しない |
| `src/ccnavi/infra/fsio.py` | ファイルの読み書きの型。state の記録・マーカー・承認済みチケット・下書きが全部これを通る |
| `src/ccnavi/infra/yamlread.py` | YAML を safe な読み手で読む。libyaml があれば C で読み、結果が分かれうる文書と深い入れ子は純 Python に回す |
| `src/ccnavi/infra/platformtag.py` | 機械の語（`<os>-<arch>`）。組み立ての目印と、振り分けの sh が起動する実体の探し方 |
| `src/ccnavi/infra/pathcase.py` | パスと名前を比べるための鍵。大文字小文字だけが違う名前を同じものとして扱う。`os.path.normcase` は単独で使わず、比べるときは必ずここを通す。返すのは比べる文字列で、ファイルを開く表記には使わない |
| `src/ccnavi/records/` | 記録。伏せ字・判定の記録・診断ログ・後始末・拒否の数え |
| `src/ccnavi/records/audit.py` | 1 行 1 件の追記記録 |
| `src/ccnavi/records/redact.py` | 記録に残す文字列から、秘密の形をした部分を伏せる |
| `src/ccnavi/records/diaglog.py` | 診断ログ。`logs/diag/<出どころ>.log` に 1 行ずつ足す |
| `src/ccnavi/records/prune.py` | 記録と state の後始末。記録を大きさでローテートし、古い記録と終わったセッションの記録を消す |
| `src/ccnavi/records/busy.py` | ターンの終わりに、エージェントが動かしている作業（バックグラウンドのコマンド、予約した繰り返し）が残っていたか。ユーザの発言で変更を受け入れてよいかの判断が読む |
| `src/ccnavi/records/failstreak.py` | ツール呼び出しの失敗や ccnavi の拒否が続いた回数を数える |
| `src/ccnavi/records/repeat.py` | 同じルールが同じ呼び出しを繰り返し止めた回数をセッションごとに数え、拒否の文面に足す一文の元にする。判定は変えない |
| `src/ccnavi/records/stopreview.py` | Stop の AI 判定（agent 型 Stop hook）の中核。判定計画・試行・判定結果の検証と最終分類 |
| `src/ccnavi/records/stopeval.py` | Stop の AI 判定の評価の集計。採用を判断する材料を、基準ごとの単位で偏りなく出す |
| `src/ccnavi/records/stopops.py` | propose の操作実績。どの提案を送ったか、送らなかったかを、どの経路を通っても確定する |
| `src/ccnavi/policy/` | ルール。読み込み・照合・組み込み・レイヤーの合成・自己防衛・文脈ファイル |
| `src/ccnavi/policy/rules.py` | ルールファイルの読み込みと検証。文の差し込み（`fill_text`: `{root}`・`{script:<名前>}`） |
| `src/ccnavi/policy/rule_lint.py` | ルールファイルの中身の検査。`--lint`、保護対象の下書きの取り込み（`user handoff`）、提案（`entry/suggest.py`）の候補の検査が呼ぶ |
| `src/ccnavi/policy/subjects.py` | ツールの呼び出しのうち、ルールを当てる文字列（subject）を選ぶ |
| `src/ccnavi/tickets/direct_candidates.py` | 直接作業の促しの候補の記録（書き込み先のワークツリー。git を呼ばない） |
| `src/ccnavi/tickets/pending_integrate.py` | セッション開始で、終わっていないintegrateの記録（marker・doneの記録・一時ワークツリー・専用のref）を知らせる |
| `src/ccnavi/tickets/next_id.py` | `ticket next-id`。通し番号に数える名前の集め方と、数えきれないときの終了コード 2 |
| `src/ccnavi/tickets/archive.py` | 閉じた親子のチケットの退避（`logs/archive/`）。`done/` の親子のチケット・`phases/<親>/`・フローを手元の退避へ移す |
| `src/ccnavi/tickets/branchfind.py` | issue・MR に紐づくブランチを探す。依頼文の指定を見つけて確かめる指示を足す入口と、`ccnavi branches` の報告 |
| `src/ccnavi/tickets/config_check.py` | レイヤーの設定 3 本（rules・phases・risks）の検査。`--lint` と `user handoff` が同じ関数を呼ぶ |
| `src/ccnavi/tickets/configsync.py` | 着手の前に、共通レイヤーをプロジェクトの `.ccnavi/config/common/` へミラーする |
| `src/ccnavi/tickets/skillsync.py` | 着手の前に、共通の工程スキルをプロジェクトの `skills/` へ写す |
| `src/ccnavi/tickets/donecopy.py` | 閉じた親チケットの写し（手元の退避、統合先の `done/<識別子>.md`）から、手元の親と同じ親かを決めるのに要る欄だけを取り出す |
| `src/ccnavi/tickets/handoff.py` | 保護対象の下書き（`wip/handoff/`）を元の場所へ取り込む（`ccnavi user handoff`） |
| `src/ccnavi/tickets/ops_plan.py` | 親の `start` が見る計画の有無。計画の無い親は止め、子の無いフェーズが残るときは案内する |
| `src/ccnavi/tickets/overview.py` | このPCで作業中の親の一覧（セッション開始の表示と `ticket status` の頭） |
| `src/ccnavi/tickets/retire.py` | 親を閉じたときの手元の片付け（ワークツリーとブランチ）。成果が失われないと確かめられたものだけを消す |
| `src/ccnavi/tickets/worktrees.py` | ワークツリーの片付け（`ccnavi worktree drop <名前>` と `ccnavi worktree tidy <親>`） |
| `src/ccnavi/tickets/session_rejects.py` | セッション開始で示す、却下された要求（REQ-SES-02）。承認済みチケットの allow / ask の項のうち、上位に退けられて効かないものを要求・理由・出所つきで並べる。判定は変えない |
| `src/ccnavi/tickets/standing.py` | 親子のチケットの立ち位置。正とする側を親のブランチ（親のワークツリー）に固定し、決まらない形を見分ける |
| `src/ccnavi/tickets/user_only.py` | ユーザだけが打つ操作の表。どの操作をユーザ専用にするかを 1 か所に宣言する正本（hook の止めと端末要求が引く） |
| `src/ccnavi/tickets/via.py` | いまの起動を誰がどの経路で動かしたか。マーカーの `via`・`actor` に書く値の持ち主 |
| `src/ccnavi/tickets/workflow.py` | 計画の待ち方。親の計画の項の `after` から都度計算し、延期した項の引き受け手を決める |
| `src/ccnavi/tickets/write_and_push_merge.py` | write-and-push の finish が、子を親のブランチへ合流する前に sh が聞くこと |
| `src/ccnavi/policy/builtin.py` | ルールファイルを読めないときの組み込み既定 |
| `src/ccnavi/policy/ruleload.py` | この呼び出しに当てるルール集合を決める（ワークスペース・プロジェクト・その和） |
| `src/ccnavi/policy/ctxfile.py` | 当たったルールがモデルへ渡す文（additionalContext）。ファイルの本文と once の記録 |
| `src/ccnavi/policy/selfguard.py` | ccnavi 自身の設定ファイルと実行ファイルのバックアップと復元 |
| `src/ccnavi/policy/selfguard_targets.py` | 守る対象の一覧。設定ファイル・層の 3 本・実行ファイルと、ワークツリー側の写し |
| `src/ccnavi/policy/selfguard_shell.py` | 守る対象へのシェルからの書き込みを止める組み込みのルール（正規表現と `add_rules`） |
| `src/ccnavi/tickets/` | チケット。承認済みチケットの置き場（approval）と合意の手続き（agree）、承認・レビュー済みの判定のコア（core）、write-and-push の見分け、フェーズ、リスク、操作 |
| `src/ccnavi/tickets/ticket.py` | チケットの読み込みと、そこが宣言する作業範囲。親子の部分集合の検査 |
| `src/ccnavi/tickets/ticket_model.py` | チケットの形。書式の定数・状態の名前・範囲の項・計画の項・待ち方と `Ticket` |
| `src/ccnavi/tickets/ticket_ids.py` | 識別子とブランチ名の規則。issue から識別子を作る手順 |
| `src/ccnavi/tickets/ticket_places.py` | 範囲を当てない置き場（チケット・下書き・ELI5）と、状態の置き場の出入りの見分け |
| `src/ccnavi/tickets/ticket_fold.py` | 同じ識別子のチケットのまとめ方。正とするツリーと、決まらない形の数え方 |
| `src/ccnavi/tickets/ticket_guard.py` | 状態の置き場を守る組み込みのルールと、提案を書いた回に渡す確認の文 |
| `src/ccnavi/tickets/ticket_fields.py` | スクリプトが書く欄の、行単位の書き換えと読み取り |
| `src/ccnavi/tickets/approval.py` | 承認済みチケットの置き場。読み込み・走査・提案の集め方・置き場の決め方 |
| `src/ccnavi/tickets/approval_ops.py` | 承認済みチケットを動かす・書く操作。承認、欄の書き換え、閉じる、レビューへ送る、続きの子 |
| `src/ccnavi/tickets/approval_marks.py` | フェーズのマーカー、親ごとのマーカー、子ごとの記録（`phases/<親>/`） |
| `src/ccnavi/tickets/manual_settle.py` | 手で `done/` に動かされた子の判定（確定してよい形か、確認待ちか、未共有か）。ボードの `manual` の 1 行もここが組む |
| `src/ccnavi/tickets/oplog.py` | 操作記録（`logs/state/ops/<操作ID>.json`）。ホストへの送信を段階ごとに残し、`--resume` で続きから送る |
| `src/ccnavi/tickets/approval_checks.py` | 承認済みチケットの構造の検査。親子と統合先、先行、プロジェクトの欄 |
| `src/ccnavi/tickets/approval_times.py` | 承認の時刻（表示だけ）。git から引く |
| `src/ccnavi/tickets/agree.py` | 合意（承認）の手続き。承認の対象を集めて `--preview` / `--verify` に答え、書き込みを並べて置き場へ動かす。承認待ちの一覧 |
| `src/ccnavi/tickets/agree_candidates.py` | 承認の候補と、その検査（計画・改版・欄・ブランチ） |
| `src/ccnavi/tickets/agree_screen.py` | 承認の画面の文面と、承認を伝える文 |
| `src/ccnavi/tickets/agree_digest.py` | 承認の対象の指紋（提案の識別子の一覧と各提案ファイルのバイト列）と、承認で書く本文 |
| `src/ccnavi/tickets/risk.py` | 実績で測るリスク。`risks.yml` の読み込み、差分の計測、スクリプトと定性項目 |
| `src/ccnavi/tickets/phase.py` | フェーズの終わりと HITL ポイント。フェーズの組み立てと順序の検査、止めたときの文、親がいまどの局面にいるか |
| `src/ccnavi/tickets/phase_forms.py` | ユーザだけが打つコマンドの形（承認・レビュー済み・ガードの切り替え・記録の片付け）を見分ける組み込みのルール |
| `src/ccnavi/tickets/phase_scope.py` | 子チケットの範囲の当て方と、範囲の外の変更の洗い出し |
| `src/ccnavi/tickets/phasetypes.py` | フェーズ定義（`phases.yml`）の読み込みと検証 |
| `src/ccnavi/tickets/plan_templates.py` | 作業・レビュー計画のテンプレートの読み込み・2 層の統合結果・検査・`--templates` の JSON（読む側）と、下書きの置き場 |
| `src/ccnavi/tickets/template_drafts.py` | テンプレートの下書きの作成（完了した親から）・検査（溜める側。取り込みは `tickets/handoff.py`） |
| `src/ccnavi/tickets/flow.py` | 子チケットのフロー（作業の手順のグラフ）。置き場・着手中のロック・読み込み・子に渡す案内 |
| `src/ccnavi/tickets/flow_text.py` | フローに書かれた文字列の整え方。制御文字・長さ・印や囲みのなりすまし |
| `src/ccnavi/tickets/flow_shape.py` | フローの形の検査。ノード・枝・名前の食い違い |
| `src/ccnavi/tickets/flow_render.py` | フローを文に描く。子に渡す手順の一覧 |
| `src/ccnavi/tickets/review.py` | レビューの依頼と確認。作業ツリーの中の前提検査と、sh が渡した結果の判定（JSON の形は `review_host.py`） |
| `src/ccnavi/tickets/review_host.py` | sh が渡す `--result` の JSON の形、投稿の目印、origin の種類（sh との契約） |
| `src/ccnavi/tickets/review_decide.py` | 残った指摘の行き先を決める（`user reviewed` の決め方と `decide`） |
| `src/ccnavi/tickets/review_close.py` | 親を閉じる（`review ready` と `close-early`） |
| `src/ccnavi/tickets/plan_order.py` | 計画の番号の振り直しと、提案への書き戻し（`--plan-order <親>`）。書き換えた全文を読み直す検査はここにまとめる |
| `src/ccnavi/tickets/plan_phases.py` | 計画が使うフェーズ定義を、親の提案の `phases:` に写す（`--plan-order <親> --fill-phases`） |
| `src/ccnavi/tickets/scaffold.py` | 親の提案の雛形を作る（`ticket new`）。選んだフェーズの `scope` を集めて `allow` に書き、一直線の `plan`（`--template` ならテンプレートの計画）と `phases:` の写しを入れる |
| `src/ccnavi/tickets/proposal_form.py` | 提案の形式検査（最小版）。雛形の空欄の目印と親の必須の見出しの定数、目印の残りと見出しの欠けの検査（`form_problems`）。`scaffold.py` と `agree_candidates.py` の両方が読む |
| `src/ccnavi/tickets/template_use.py` | テンプレートを使う側。名前で引く（使えるものだけ）・`after` を提案の絶対番号に付け直す・子のフローの下書きの識別子と文面を組む |
| `src/ccnavi/tickets/learning.py` | 学びの蓄積の記録（気づきの記録・受信箱・判定記録の要約）と、`ticket learn`、子を閉じる前の記録の検査、親の `finish` の検査 |
| `src/ccnavi/tickets/learning_records.py` | 気づきの記録の読み取りと、処理すべきものの判定 |
| `src/ccnavi/tickets/consolidate.py` | `ticket consolidate`（統合の子を作る） |
| `src/ccnavi/tickets/learning_notice.py` | SessionStart の学びの通知 |
| `src/ccnavi/records/wiki.py` | パターン・採否の記録の読み取りとパターンの状態 |
| `src/ccnavi/records/wiki_load.py` | 学びの蓄積の wiki（`docs/wiki/`）を、作業ツリーか git のオブジェクトから読む |
| `src/ccnavi/tickets/propose.py` | 親子の承認待ちの提案（`todo/`）と子のフローの下書き（`flows/`）を、書いたパスの一覧に載せる（`ticket propose`）。コミットと push は sh が write-and-push の手順で行う |
| `src/ccnavi/tickets/ops.py` | チケットの状態を動かす `ticket start / finish / cancel / record-risk`。閉じるときに実績のリスクを数える |
| `src/ccnavi/tickets/ops_close.py` | チケットを引いて動かしてよいかの検査。置き場の引き当て、取り込み済みの親子の停止、着手の前（親・先行）と終了の前（親を閉じられる・成果物）の検査 |
| `src/ccnavi/tickets/ops_stop.py` | Stop で `finish` の打ち忘れを促す判定と、基準点の確認（git の読み取り） |
| `src/ccnavi/tickets/ops_stop_integrate.py` | Stop で、チケットに結び付かない直接作業が `integrate` まで進んでいないことを見つけ、打ち方を促す判定（統合先のツリーが綺麗でないときは促さず理由を言う） |
| `src/ccnavi/tickets/base_bound.py` | `ccnavi tree base-bound`。直接作業の取り込み先のブランチがチケットに結び付くか（識別子の衝突はワークスペース全体、取り込み先との結び付きは取り込み先のリポジトリだけを見る） |
| `src/ccnavi/tickets/ops_stop_request.py` | Stop でレビュー依頼までの道（`finish`・すべての子が合流済みかの確認・ELI5・`request`）の途中を見つけ、次の一手を促す判定 |
| `src/ccnavi/tickets/ops_stop_feedback.py` | Stop で、親の局面が「フィードバック計画待ち」のまま終えようとしたとき、改版を書いて送るよう 1 回だけ促す（`NUDGE_TICKET_FEEDBACK`）。改版を提案済みかで文面を分ける |
| `src/ccnavi/tickets/core.py` | 承認・レビュー済みの判定のコアの入口。名前を束ね直すだけで、`entry/cli.py` と Chrome 拡張（Pyodide）はここを引く |
| `src/ccnavi/tickets/core_base.py` | 判定のコアの共通部分。入力（`Snapshot`）・承認の判定（`judge_approval`）・書くもの（`Changes`）・ディスクへの書き込み |
| `src/ccnavi/tickets/core_approve.py` | 判定のコアを通す手元の承認の入口（`user agree` の 4 つの枝） |
| `src/ccnavi/tickets/core_review.py` | 判定のコアを通すレビュー済み（`review confirm`）の判定 |
| `src/ccnavi/tickets/write_and_push.py` | write-and-push で sh が聞くこと。親子のチケットが write-and-push の対象か（`write-and-push family`）と、置き場の変更の分け方（`write-and-push sort`） |
| `src/ccnavi/hook/` | hook の判定。実行前チェック・文面・実行後チェック・イベント・サブエージェント |
| `src/ccnavi/hook/post.py` | 実行後チェックの手順。作業ツリーの読み取り、前からあった変更の記録、復元 |
| `src/ccnavi/hook/post_findings.py` | 変わったファイルを、守る場所とチケットの範囲に当てる。スクリプト自身の書き込みの見分け |
| `src/ccnavi/hook/post_report.py` | 実行後チェックの差し戻しの文 |
| `src/ccnavi/hook/events.py` | hook のイベントごとの手順。1 回の起動で何が起きるかはここを上から読む |
| `src/ccnavi/hook/judge.py` | 実行前チェック。通す・聞く・止めるを決める |
| `src/ccnavi/hook/reasons.py` | 判定に添える文面と理由コード |
| `src/ccnavi/hook/failsafe.py` | 判定に達せなかったとき（内部の例外・設定が読めない・内部の期限切れ）に、通してよい呼び出しか。設定もルールも読まずに答える |
| `src/ccnavi/hook/parentwrite.py` | サブエージェントが Bash から親のワークツリーへ書き込む形を、実行前に止める。シェルの書き込み先を取り出す読みは `rootwrite` も使う |
| `src/ccnavi/hook/rootwrite.py` | Bash からワークスペースルート直下へ書く形を、ルールファイルの `workspace-root`（Write / Edit と同じ決まり）に当てて実行前に止める |
| `src/ccnavi/hook/wrapguard.py` | 保護済みの sh（`.ccnavi/scripts/ccnavi-*.sh`）を呼ぶ形のうち、実行前に止めるもの 3 つ |
| `src/ccnavi/hook/proposalhint.py` | 親の提案を手書きしたときの書式の誤りを、書いた時点で 1 度だけ案内する（PostToolUse） |
| `src/ccnavi/hook/projskills.py` | プロジェクトのスキルの目録（名前・説明・場所）を組む |
| `src/ccnavi/hook/stopjudge.py` | Stop の AI 判定の判定役（agent hook）の呼び出しを見分け、ccnavi の各 hook から除く |
| `src/ccnavi/hook/ccnavimd.py` | `SessionStart` で渡す作業の決まり（`.ccnavi/CCNAVI.md`）の条件と差し込みを解く。`--lint` の書き方の検査も持つ |
| `src/ccnavi/hook/subagent.py` | SubagentStart / SubagentStop。開いている子の案内と、同じ作業ツリーで編集する別のサブエージェントの案内、範囲外の変更の差し戻し |
| `src/ccnavi/hook/subagent_outcome.py` | サブエージェントの終了結果（`ok`・`fixed`・`outside`・`unchecked`・`unattributed`）の記録（`.outcome`）、親への 1 度きりの知らせ、Stop の促しの文 |
| `src/ccnavi/hook/agentkind.py` | サブエージェントが編集するものか、読み取り専用かの見分け |
| `src/ccnavi/hook/agentlaunch.py` | 起動ツール（`Agent`・`Task`）の PreToolUse で、プロンプトの言及（子の識別子・ワークツリーのパス）から担当を決める。止める理由（`DENY_AGENT_*`）か、プロンプトに書き足す担当の案内の計画を返すだけで、応答は書かない |
| `src/ccnavi/hook/childnote.py` | 子 1 本の担当の案内（ワークツリー・先行・範囲・フロー）と親の局面の行を組む。`SubagentStart` の文と、起動前にプロンプトへ書き足す案内が共有する |
| `src/ccnavi/hook/docsearch.py` | `--docs` と `SessionStart` の入口。索引を置く場所（ワークスペースとプロジェクト）を決めて集め、引き方を案内する |
| `src/ccnavi/hook/docsearch_index.py` | md の frontmatter の索引（`ccnavi-index.jsonl`）を組む。変わった md だけを読み直す |
| `src/ccnavi/hook/docsearch_query.py` | 索引の引き方。`--docs` の問いの検査、当たり、並べ方と表 |
| `src/ccnavi/entry/` | 入口。CLI・診断・lint・提案・版。どのサブパッケージからも読まれない |
| `src/ccnavi/entry/lint.py` | 設定とルールの検証 |
| `src/ccnavi/entry/lint_project.py` | lint のうち、`.claude/settings.json`・`settings.local.json` の hook と env の検査 |
| `src/ccnavi/entry/lint_places.py` | lint のうち、下書き・プロジェクト・チケットの置き場の検査 |
| `src/ccnavi/entry/lint_layers.py` | lint のうち、設定のレイヤーと、取り込んだレイヤーの食い違いの検査 |
| `src/ccnavi/entry/lint_templates.py` | lint のうち、作業・レビュー計画のテンプレートの検査 |
| `src/ccnavi/entry/lint_ticket.py` | lint のうち、承認済みチケット・承認・提案と、親子の運用に要る hook の検査 |
| `src/ccnavi/entry/lint_branch.py` | lint のうち、チケットのブランチ名・連番・既存ブランチ・ワークツリーの検査 |
| `src/ccnavi/entry/lint_direct.py` | lintのうち、直接作業の記録（記録の無いワークツリー・壊れた記録・横で作業する指定・残った一時ワークツリー、ref、marker・版を知らないmarker）の検査 |
| `src/ccnavi/entry/lint_wiki.py` | lint のうち、学びの蓄積の wiki（`docs/wiki/`）のパターンと採否の記録の検査 |
| `src/ccnavi/entry/diagnose.py` | 判定を実行せずに試す `--test` と `--explain`。名前を引き受けるだけで、中身は下の 4 つに分けてある |
| `src/ccnavi/entry/diagnose_try.py` | diagnose のうち、`--test` と `--test-expectations`。判定と同じ経路で 1 件と期待を試す |
| `src/ccnavi/entry/diagnose_explain.py` | diagnose のうち、`--explain` の本文 |
| `src/ccnavi/entry/diagnose_board.py` | diagnose のうち、ボードの中身（`--explain --json`） |
| `src/ccnavi/entry/diagnose_shared.py` | diagnose のうち、explain・board・try が共有するレイヤーの読み出しとルールの書き方 |
| `src/ccnavi/entry/cli.py` | 1 回の起動の入口。標準入出力とコマンドラインを判定や各コマンドへ振り分け、ワークスペースルートを見つける |
| `src/ccnavi/entry/cli_usage.py` | `--help` の本文。話題の一覧（`OVERVIEW`）、話題ごとの節（`TOPICS`）、全文（`USAGE`。`--help --all`）の三つから成る |
| `src/ccnavi/entry/cli_args.py` | 引数の読み分け。設定を上書きするオプション、診断だけのオプション、`--docs` と並べられないオプションを分けるほか、パスかどうかも見分ける。`--help` の話題の選び方（`help_text`） |
| `src/ccnavi/entry/cli_ops.py` | チケットとレビューのサブコマンドを ops / review へ渡す。`sync` の問い合わせ |
| `src/ccnavi/entry/cli_templates.py` | `--templates` の引数を受けて、テンプレートの一覧・検索・表示と、下書きの作成（`--draft`）を行う |
| `src/ccnavi/entry/cli_plan.py` | `--plan-order` の引数を受けて答えを出す。振り直しと書き戻しは `tickets/plan_order.py` と `tickets/plan_phases.py` |
| `src/ccnavi/entry/cli_handoff.py` | `ccnavi user handoff`。保護対象の下書き（`wip/handoff/`）を元の場所へ取り込む入口（中身は `tickets/handoff.py`） |
| `src/ccnavi/entry/cli_learnings.py` | `ccnavi --learnings`。学びの蓄積の wiki のパターンごとの状態と観測元の数を一覧にする |
| `src/ccnavi/entry/cli_stopreview.py` | `ccnavi stop-review material|record`。Stop の agent hook（判定役）が Bash から打つ入口 |
| `src/ccnavi/entry/sh_ask.py` | `.ccnavi/scripts/` の sh がチケットについて聞くこと（`ccnavi tree parent` など）。1 行 1 項目で答え、何も書かない |
| `src/ccnavi/entry/status.py` | `ccnavi ticket status [<親>]`。承認済みチケットの状態を、置き場と git から読んで言う。読むだけ |
| `src/ccnavi/entry/suggest.py` | `ccnavi --suggest`。判定の記録を数えて、ルールを足す候補と文面を見直す候補を出す。検証を通った `deny` と `ask` の候補だけを出す |
| `src/ccnavi/entry/version.py` | `ccnavi --version [--json]`。実行ファイルが自分について言うこと（版・コミット・受け付けるフラグ・互換の版・書式の版・`features`） |
