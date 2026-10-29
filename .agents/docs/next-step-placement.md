---
type: guide
title: 次の一手の置き場の分担
description: 契機ごとに、何がエージェントへ次の一手を渡しているかの表、sh の側と実行ファイルの側の分担、新しく足すときの選び方、status の対象外の状態を拾う担当
tags: [exe-boundary, sh-scripts]
keywords: [次の一手, 置き場, 分担, SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, SubagentStart, Stop, 促し, 案内, status, guidance, review_next, standing, 対象外, 契機, 次の操作]
---

# 次の一手の置き場の分担

エージェントに次の一手を渡す文を新しく足すとき、どの契機のどの担当に置くかを選ぶための表。
拒否の文面の型と止め方の選び方は[止め方の型と選び方](stop-patterns.md)、出力の `次:` の書き方は[スクリプトの出力に次の一手を書く](script-output.md)、文を置く場所は[文面ファイルに文を足す・直す](message-files.md)にある。ここは、どの担当が何を受け持っているかだけを書く。

## 契機ごとの担当

担当（仕組み）の単位で並べる。識別子と出力元の細かい対応は[文面の移行の一覧](message-migration.md)と各文面ファイルにある。Stop の促しの条件は設計書の[状態遷移](../../docs/21_design/tickets/state-transitions.md)、理由コードは設計書の付録にある。

| 契機 | 担当 | 状態の読み方 | 出す先 |
|---|---|---|---|
| SessionStart（`hook/events.py` の `decide_at_start`。サブエージェントには出さない） | 作業の決まり（`hook/ccnavimd.py` が `.ccnavi/CCNAVI.md` の条件と差し込みを解く） | 状態は読まない。ルールの有無・付き添いの有無・入口の有無で行を選ぶ | セッションの文脈 |
| 同上 | 今の現況（`tickets/notice/overview.py` の作業中の親の一覧）、却下された要求（`tickets/notice/session_rejects.py`）、終わっていない integrate の続きの一手（`tickets/notice/pending_integrate.py`） | 手元のファイルと git の ref。ネットワークには出ない。期限を超えたら打ち切る | セッションの文脈。却下された要求と integrate は、ユーザにも出す |
| 同上 | 道具の案内（`selfguard.report`、`docsearch.at_start`、`projskills.notice`） | 設定ファイル・索引・cwd | セッションの文脈 |
| UserPromptSubmit（`decide_at_prompt`） | 依頼文の issue・MR への指示（`tickets/lifecycle/branchfind.py` の `prompt_context`）、終わったサブエージェントの印（`hook/subagent.py` の `unchecked_scope`・`outcome_notices`）、手で `done/` に動かされた子の確定待ち（`_manual_confirm_note`） | 依頼文、`state` の印、承認済みチケットの置き場 | 文脈に足すだけで、止めない。承認後の知らせは hook でなく拡張が渡す（`tickets/proposal/agree_screen.py` の `approved_text`） |
| PreToolUse | 拒否・確認（組み込みの関門とルールの `deny`・`ask`。拒否の文は `.ccnavi/messages/deny-*.yml`、確認の理由は `hook/reasons.py` の `ask_hint`） | ツール呼び出しの対象（コマンド・パス・名前）と、ファイルだけで引ける状態。hook は git もネットワークも起こさない | 拒否の文・確認の理由 |
| 同上 | 止めない案内（ルールの `additionalContext`・`additionalContextOnce` と `…File`。`policy/ctxfile.py`）、サブエージェントの起動前のプロンプトへの書き足し（`hook/agentlaunch.py`） | 同上 | 文脈 |
| PostToolUse | 書いた結果への案内と報告（`hook/proposalhint.py`、`hook/post.py`・`post_report.py`） | 書いたファイルと実行後のツリー | 文脈、実行後チェックの差し戻しの文 |
| SubagentStart | 子の担当の案内（`hook/subagent.py` の `at_start`、`hook/childnote.py`） | cwd のツリーに対応する親か子 | サブエージェントの文脈 |
| Stop | 促し（`finish`・依頼までの道・フィードバック計画・サブエージェントの結果・`integrate`・`match: Stop` のルール。`tickets/lifecycle/stop*.py` と `hook/events.py`） | チケットの置き場・git・サブエージェントの印・数えの記録 | `Stop` を止めて渡す文。1 回の `Stop` で止める理由は 1 つ（順序は `decide_at_stop`）。同じ状態では 1 回だけ |
| 同上 | AI による判定（`hook/stopjudge.py`。構成は設計書の[Stop の AI 判定](../../docs/21_design/stop-review.md)） | 判定役が材料を取る | 差し戻しの文 |
| 同上 | ターンの終わりの報告（`post.at_stop`）と、同じ理由での繰り返しの知らせ（`repeat.at_stop`） | 実行後チェックの報告、拒否の記録 | ユーザへの `systemMessage`。エージェントに次の一手を渡す担当ではない |
| スクリプト・実行ファイルの出力 | `次:`・`案内:`・`次の手:`（場面は[実装済みの場面](script-output.md#実装済みの場面)の表）。状態を動かす操作の結果は、その操作が出す（`tickets/lifecycle/commands.py` の `finish`・`start`、`tickets/lifecycle/plan.py`） | 操作が動かした状態 | 標準出力。止まるときは標準エラー |
| `ccnavi-ticket.sh status`（`entry/status.py`） | 「次の操作」の行。レビュー待ちの子は `tickets/phase/gates.py` の `review_next`（`finish` の出力も使う）。立ち位置が決まらないときの解き方は `tickets/model/standing.py` の `guidance()` | 承認済みチケットと提案の置き場、git、親子の立ち位置。読むだけで書かない | 標準出力 |

- `status` の「次の操作」と `review_next` の文は、文面ファイルに移していない（識別子なし。コード内）。`guidance()` の文は `NEXT_STANDING`（`nudge-stop.yml`）
- 促しの文は `nudge-stop.yml`・`nudge.yml`、通知は `notice.yml`、sh の `次:` は `next-sh.yml`・`next-start.yml`、実行ファイルの `次:` は `next-cli.yml`。どれが移ったかは移行の一覧にある

### `guidance()` の出し先

呼び手によって、同じ解き方を別の場所に出す。

| 呼び手 | 出し先 |
|---|---|
| `entry/status.py` | 標準出力の「止まっている理由」の下の行 |
| `tickets/approval/write_and_push.py` | 実行ファイルと sh の間の約束の `hint` 行（sh がそのまま出す） |
| `tickets/approval/checks.py` の `family_stop_text` | `--lint` の問題の文と、承認の断り |
| `tickets/lifecycle/close.py` | 標準エラー |

## sh の側と実行ファイルの側

次の一手の中身を計算するのは実行ファイルで、sh は計算しない。2 つは別の決まりで、混ぜて読まない。

- sh の側の決まり: sh は、チケットの状態から次を割り出す処理を持たない。状態が決める次は `ccnavi-ticket.sh status` を打つよう促す。sh が出す `次:` は、その sh 自身の操作の結果に対する文面ファイルの文である（`script-output.md` の「決まり」）
- 実行ファイルの側: 状態から次を計算して出す。`status`、`guidance()`、`review_next`、Stop の促し、状態を動かす操作の結果の `次:` がそれにあたる。これらを全部 `status` に集める決まりはない。契機が違えば、読む状態も出す先も違う（上の表）

## 新しく足すときの選び方

今の分担から読み取れる範囲で書く。

1. 状態から決まる次は、実行ファイルが計算する。sh には書かず、sh は `status` を促す
2. 打った操作がその場で失敗する形は、拒否の `next` に書く（型は[止め方の型と選び方](stop-patterns.md)の「拒否の文面の型」）
3. その操作の成功の結果として次が決まるなら、その操作の `次:` に書く。ユーザの返事を待つ次は、そう書く
4. ターンの終わりに済ませ忘れる、決まった道は、Stop の促しにする。止めるので、1 回だけにし、同じ状態では繰り返さない（型の表の「促し」の行）
5. 止めずに言えば足りる補足は、`additionalContext`・`additionalContextOnce`（[止め方の型と選び方](stop-patterns.md)の選び方 4）。書いた結果への案内は PostToolUse、サブエージェントの担当は SubagentStart
6. セッションの頭に渡す、常に守る作業の決まりは `.ccnavi/CCNAVI.md` に書く。ccnavi の振る舞いが変わったら、そこを直す
7. 今の現況は、SessionStart の一覧（`overview`）が事実として言う。SessionStart が続きの一手まで言うのは、終わっていない integrate だけで、ほかの状態の次を足す決まりは無い。状態の次は `status` で聞く

## `status` の対象外の状態

`status` は承認済みチケットと提案を読む。チケットに結び付かない状態や、親子が決まらない状態は、次の担当が拾う。どの担当も拾わない状態の一覧は、陳腐化するので文書に残さない。

| 状態 | 拾う担当 |
|---|---|
| チケットに結び付かない直接作業の、取り込みが済んでいない作業 | Stop の `integrate` の促し（cwd のツリーと、このセッションが書いたツリー）。`--lint`（`entry/lint_direct.py`）が記録の無いワークツリーと壊れた記録を言う |
| 直接作業の、取り込みの途中の跡（marker・一時ワークツリー・専用の ref・片付けの途中の done の記録） | SessionStart の `pending_integrate`。`--lint` の `_markers`・`_leftovers` |
| 直接作業へ切り替えたあとに残る承認待ちの提案・MR | `ccnavi-worktree.sh` の切り替え 1 回の出力（`NOTE_WORKTREE_LEFTOVER*`。残っているかは `ccnavi tree leftover` に聞く）。親のワークツリーからの切り替えは承認前（`stage` が `todo`）のときだけで、承認済み・レビュー待ち・着手済みの子があるときは切る前に断る（`NOTE_WORKTREE_PARENT_*`・`NEXT_WORKTREE_PARENT_ACTIVE`）。取り下げの手順を言うのはこの 1 回だけで、あとのセッションでは SessionStart の一覧（`MR !<番号>`）と `status` が状態を事実として言う |
| 親子の立ち位置が決まらない（親のワークツリーが無い・HEAD が合わない・途中の操作） | `status` の「止まっている理由」と `guidance()` |
| 同じ識別子のチケットが複数の置き場にある | `status` の「次の操作」（ユーザに重複の解消を頼む） |
| 閉じた子の、消せずに残ったワークツリー | `ccnavi worktree remaining <親>`（`ccnavi-review.sh ready` が呼ぶ。`tickets/lifecycle/worktrees.py`） |
