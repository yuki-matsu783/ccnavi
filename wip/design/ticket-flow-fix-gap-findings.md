---
type: report
title: チケット作業フロー修正 抜け調査報告書
description: 実装設計書（R1〜R10、AT1〜AT45）に対する実装の抜けと設計との違いの調査結果。コードは変えていない
tags: [design-doc, tickets]
keywords: [チケット, 手動移動, 受け入れ台帳, 操作記録, 互換の版, hook, 複数プロジェクト, 調査]
---

# チケット作業フロー修正 抜け調査報告書

実装設計書（`wip/design/ticket-flow-fix-impl.md`。main では削除済みで、`docs/design/`・`docs/requirements/`・ADR-0147〜0156 に反映された）の項目が、main の実装でどう扱われているかを調べた結果です。本番のコードは変えていません。行番号は目安です。

結論の見方は 対応済み／一部／未対応／判断できない の 4 つです。A（テスト追加）の結果は各項目と 8 節に反映済みです。

| 項目 | 結論 |
|---|---|
| 1 新旧の部品の混在 | 一部 |
| 2 作業途中の親の移行 | 一部 |
| 3 hook の処理時間 | 一部（PreToolUse は対応済み） |
| 4 複数プロジェクト | 一部 |
| 5 境界のケース | 一部 |
| 6 設計書との違い | 判断材料のみ（下の各小項目） |
| 7 保証しないことの一覧 | 一部 |

## 1. 新旧の部品の混在

### 1-1 Chrome 拡張が埋め込んだ互換の版の比較先

結論: 対応済み（比べる相手は sh の版で、実行ファイルではない）

- 根拠
  - 拡張は組み立てのときに `version.py` の `COMPAT` を埋め込みます（`extensions/chrome/ccnavi-approval/scripts/build.js:60-88`、`src/background/main.ts:48` の `compat`）。
  - 比べる相手は、ワークスペースの統合先にある `.ccnavi/scripts/ccnavi-common.sh` の `CCNAVI_COMPAT`（`py/ccnavi_chrome.py` の `_compat`、1043 行付近）です。完全一致でなければ「表示のみ」になり、承認・レビュー済みの書き込みは受け付けません（`_write_refusal`、735 行付近。`protocol.ts` の `startGuard`、340 行付近は「始める」を断ります）。
  - 比べるのは「拡張」対「リポジトリの sh」の 2 者で、手元の実行ファイル（`ccnavi`）の版とは比べません。実行ファイルの版を見るのは sh 側（`ccnavi-common.sh` の `ccnavi_compat_skew`、231 行付近）と `--lint`（`lint._sh_compat`）です。
- 勧める対応: 現状のまま。文書（`docs/design/` か ADR）に「拡張が比べるのは統合先の sh で、手元の実行ファイルではない」と 1 行書きます。

### 1-2 互換 16 以前のコアを積んだ Chrome 拡張が、`settled_by` なしで done/ へ動かした場合

結論: 一部（判定はするが、通常の経路では起きない）

- 根拠
  - `manual_settle.assess`（`tickets/manual_settle.py`）は、`finished_to: review` の子が `done/` にあり `settled_by` が無ければ手動とみなします。誰が動かしたか（コミットの作者・メッセージ・`via`）は見ません。依頼のマーカーがあり、移動が共有済みなら READY になります。
  - ただし、古い Chrome は子の移動とレビュー済みのマーカーを 1 コミットで書きます（`write.ts` の `settleRace`・コミットの説明 538 行付近）。`assess` は冒頭で、reviewed マーカーがあれば `Manual()`（NONE）を返します（`MARK_REVIEWED in ph.marks`）。そのため、通常の経路では手動移動と判定されません。
  - 判定されるのは、古い Chrome の書き込みの途中で子の移動だけが残った場合、またはマーカーだけが後で消えた場合に限ります。
- 勧める対応: 拡張を `COMPAT` と同じ版に更新する案内（1-3）を優先します。判定を堅くするなら、Chrome の書き込みのコミットに付く「Chrome 拡張 <版>」の行（`write.ts` の `commitMessage`、57 行付近）を `assess` が読んで除く方法があります。今は読んでいません。

### 1-3 防ぐ手立て

結論: 一部

- 今あるもの
  - Chrome 側の書き込みの拒否（1-1。ただし拡張と統合先の sh の食い違いだけを見ます）。
  - reviewed マーカーを同じコミットに書く作り（1-2）。
  - sh と実行ファイルの食い違いの警告（`ccnavi_compat_skew`。参照 17 行。警告を出して止めずに進みます）と `--lint`。
  - 配布文書の更新手順（`.ccnavi/docs/ticket-flow.md` 130 行付近「更新したときの変更点」）。
- 今ないもの
  - コミットの中の目印を `assess` が読むこと。
  - Chrome の版の記録（マーカーに拡張の版は入りません。`via: chrome` だけです）。
  - 実行ファイルが実行時に互換の版を見て確定を拒むこと（`COMPAT` を使うのは `lint.py` と `version.py` だけです）。
- 勧める対応: 手動移動の確定（`confirm` の READY）の前に、reviewed の書き手が古いコアでないかを見る検査は、通常は起きないため不要です。起きたときの被害は「移動前の未解決の指摘が台帳に受け入れられる」ことで、`confirm` の時刻の余裕幅（`MARGIN_SECONDS`）が上限を決めます。更新の告知を強める程度で足ります。

### 1-4 古い sh と新しい実行ファイルの組み合わせ（最後のコメント・作成者を取らない）

結論: 一部（安全側に倒れるが、止まらない）

- 根拠
  - 古い sh は `last_id`・`last_at`・`last_author` を返しません。実行ファイルは空文字で読みます（`review_host.py` 189-191 行）。
  - 台帳の照合 `_accepted_by_ledger`（`review_host.py` 420 行付近）は、鍵か最後のコメントの id が空なら「受け入れ済みにしない」とします。台帳で受け入れた指摘が未解決として数え直され、見えなくなる側には倒れません。
  - 再提起の見分けは使えません。台帳に受け入れた指摘が毎回数え直されるだけで、誤って受け入れることはありません。
  - sh と実行ファイルの食い違いは警告だけで、止まりません（`ccnavi_compat_skew` は 1 を返すだけです。呼び手は警告を書いて先へ進みます）。
- 勧める対応: 現状のまま。更新の案内に「sh を先に更新しないと、受け入れた指摘が再び数えられる」と書きます。

## 2. 作業途中の親の移行

| 状態 | 更新後の振る舞い | ユーザがすること |
|---|---|---|
| `finished_to` の無い review/ の子 | review/ にある間は何も変わりません（`waiting` に数えます）。confirm・decide は今までどおり子を done/ へ動かし `settled_by` を書きます。ユーザが手で done/ へ動かすと ROUTE_UNKNOWN（`manual_settle.py`）になり、手動移動としては確定しません | 手で動かさず、confirm か decide を使います。手で動かしたなら confirm（未解決が無ければ通ります）か decide を使います。`--lint` が info で並べます |
| 依頼済みでレビュー待ちのフェーズ | 依頼のマーカーはそのまま有効です。confirm・decide・close-early は変わらず使えます。手で動かした子がすべて `finished_to: review` なら、そこから手動移動の経路に入ります | 特になし。古い子と新しい子が混ざるときは、古い子を confirm か decide で動かします |
| 古い下書き（`review-decide-*.md`・`review-issue-*.md`） | prune は消しません（`records/prune.py` 25 行付近）。`ccnavi-user-decide.sh --resume` は、未完了の判断が無いときに下書きを見つけ、y/N のあと操作記録の段階に載せて送り、送れたものを消します（`resume_legacy_drafts`、167-217 行） | `ccnavi-user-decide.sh --resume` を打ちます。未完了の判断が 1 件以上あると下書きの取り込みに入らない（`resume_prepare` 113-127 行）ので、先にその判断を終えてから打ち直します |
| dry-run を使っていたワークスペース | チケット制御が有効なら、dry-run でも、サブエージェントの状態操作・承認済みの置き場と `logs/state/ops/` への書き込み・親のツリーへの書き込みは拒否されます。一般のルールの deny・ask だけが報告になります | dry-run を前提に、エージェントが承認済みの置き場へ直接書く運用があれば止めます。`ccnavi --explain` の「dry-run のときに止めるもの」で確かめます |

### `.ccnavi/docs/ticket-flow.md` の告知で足りているか

結論: 一部

- 載っているもの（130-139 行）
  - dry-run の変更。
  - 互換 18（sh・実行ファイル・VS Code・Chrome をそろえて更新する）。
  - `finished_to` の無い旧い子。
  - 未完了の操作記録は prune で消えない。
- 載っていないもの
  - 古い下書き（`review-*.md`）の取り込み方（上の表）。
  - `DECIDE_VERSION` が 2 になり、版 1 のボードの選択を断ること。互換 18 の項に「ダイジェストは版 2 のまま」とあるだけで、版 1 を断ることと「拡張を更新してください」の表示は書かれていません。
  - 実行ファイルより先に拡張だけ、または sh だけ更新した場合の振る舞い（1-3・1-4）。
- 勧める対応: 4 行足します（下書きの取り込み、`DECIDE_VERSION` 2、更新の順番、dry-run の運用の見直し）。

## 3. hook の処理時間

`manual_settle.assess` の呼び出し元（grep で網羅）:

| 呼び出し元 | hook／入口 | 呼び出しの回数 |
|---|---|---|
| `tickets/ops_stop_request.py` `pending_at_stop`（142 行） | Stop（`events._request_nudge`、838 行）と UserPromptSubmit（`events._manual_confirm_note`、339 行） | 依頼済みでゲートが閉じたフェーズごとに 1 回。READY で打ち切ります |
| `tickets/core_review.py` `confirm` の入口（228 行）・`manual_for`（266 行） | CLI の `confirm`・Chrome | 1 回 |
| `entry/status.py`（189 行） | CLI の `status` | レビューが要るフェーズごとに 1 回 |
| `entry/diagnose_board.py`（656 行） | CLI の `--diagnose` のボード | 同上 |

- PreToolUse（`hook/judge.py`）と PostToolUse（`hook/post*.py`）は `assess` を呼びません。PreToolUse が git の履歴を読まないことは守られています。
- git の履歴を読むもの
  - `manual_settle._shared`: 手で動かした子 1 つにつき `status`・`log`・`merge-base` の 3 回（`gitcmd.run`、既定の期限 5 秒）。呼ばれるのは `hand`（手で動かした子）がある READY 候補のときだけです。
  - `approval_times.approved_times`: `git log` をツリーごとに 1 回。ボード・`--diagnose`・`status` だけで、コメントで hook の判定から呼ばないと明記されています。
  - `gitstate.committed`: PostToolUse（`post.py` 392 行）と SubagentStop が使う、ターンごとの差分です。
  - `hook/subagent.py` 241 行の `phase_scope.scope_findings`: SubagentStop で、親のツリーから起動したときは子の数だけ繰り返します。子のワークツリーから起動したときは 1 回です。
  - `review._unseen_commits`: CLI の `request`・`confirm` だけです。
- Stop と UserPromptSubmit の回数は、子の総数ではなく、手で動かした子の数に比例します（3 回 × 動かした子の数）。動かした子が無ければ git は呼ばれず、ファイルの走査だけになります。
- 結論: 一部。PreToolUse は対応済みです。Stop と UserPromptSubmit は `gitcmd.run` を内部の期限なしで呼びます（`run_until` を使っていません）。そのため git が止まると、1 回あたり 5 秒まで待ちます。複数の子を手で動かした親で git が重いと、hook の期限（`settings.json` の timeout は 10 秒、内部の期限は `judge.DEADLINE_SECONDS` の 3 秒）に近づきます。
- 勧める対応: `_shared` を `gitcmd.run_until` に換えて期限を渡します。子ごとの 3 回を 1 回の `git log --name-status` にまとめます。

## 4. 複数プロジェクト（`projects/<名前>/`）

テストで確かめました（`tests/ticket/test_multi_project_gaps.py`。ワークスペース + `projects/web` + 親のワークツリー）。

| 対象 | 結論 | 根拠 |
|---|---|---|
| manual_settle と confirm | 対応済み | 親のワークツリーは、プロジェクトの親でも `<ワークスペース>/.claude/worktrees/<親>` に置かれ、持ち主がプロジェクトと決まります（`tree.worktrees`、`owner_of`）。`_shared` はその親のツリーで `status`・`log`・`origin/<ブランチ>` を読み、プロジェクトのリポジトリの履歴で共有済みかを決めます。テストは `test_the_worktree_of_a_project_parent_belongs_to_the_project`・`test_a_move_in_the_project_repository_is_judged_by_its_own_history` です |
| 受け入れ台帳 | 対応済み | `review.ledger_of`・`settle_and_mark` は `home_dir(..., project=parent.project)` で台帳の置き場を決めます。プロジェクトの親のワークツリーの中の `.ccnavi/approved/phases/<親>/accepted.json` になります。テストは `test_the_ledger_home_of_a_project_parent_is_inside_its_worktree` です |
| 操作記録の repo | 一部 | 保存するのは decide だけで `{host, mr_url}` です（`review_decide.py` 639 行）。request・comment・ready は `repo={}` です（`entry/cli_ops.py` 529 行）。設計の「ホストの種類・ホスト・パス」「プロジェクト」は記録にありません。操作記録は `logs/state/ops/` に 1 つのワークスペースで共通なので、親の識別子が同じ別リポジトリの親があると区別できません（ワークツリーの置き場が 1 つなので、同じ名前の親のワークツリーは同時には存在できません） |
| `ccnavi-user-decide.sh --resume` | 一部 | 対象は記録の `parent` から決め、`$root/.claude/worktrees/<親>` へ移動します（`ccnavi-user-decide.sh` 145-156 行）。プロジェクトの親でも置き場は同じなので動きます。ただし親のワークツリーが無いと `decide-no-worktree` で止まります。設計の「親のワークツリーが無くても、記録の repo から決めて動く」（AT25）は実装されていません。テスト `test_resume_from_the_workspace_root_after_the_parent_worktree_is_gone`（`tests/sh/test_decide_recovery.py`）は expectedFailure で固定しました。同じ実装の欠陥（AT25）です。 |

勧める対応: 操作記録の `repo` に `project` を足します。`--resume` は、ワークツリーが無いときに `repo` から対象を決めます（実装する場合）。または設計書を実装に合わせます。

## 5. 境界のケース

### 5-1 依頼していないフェーズに close-early した場合

結論: 対応済み

- 根拠: `review_close.close_early` は MR（`review._result_with_mr`、`review.py` 878 行付近）を必要とし、依頼のマーカーは不要です。終わっていて reviewed が無いフェーズ（`unreviewed`）は「済んだ扱い」として、子を `settled_by: close-early` で done/ へ動かし、`{"by": "close-early", "accepted": []}` の reviewed マーカーを置きます（`_settle`、565-625 行）。ユーザには y/N の前に一覧で見せます（`_show_leftovers`、「レビューが済んでいない → 済んだ扱い」）。MR が無い親（依頼もしていない親）では、`_result_with_mr` が断るため close-early は打てません。
- 勧める対応: MR が無い親でも早めに閉じたい要望があれば、別に決めます。

### 5-2 延期を引き受けたフェーズで一部だけ手で動かした場合

結論: 一部（確定しない側に倒れます。設計どおり止めます）

- 実装の読み: `assess` は延期を引き受けた分を含めます（`numbers = {*ph.covers, ph.number}`）。引き受けた側の子のうち 1 つだけ手で動かすと、まだ review/・doing/ にある子が `waiting` に入り、PARTIAL になります。確定しません。
- テストの結果（`tests/ticket/test_manual_settle.py` の `ManualSettleGapsTest`）
  - 延期した側と引き受けた側のどちらの子だけを動かしても PARTIAL になります。待ちは動かしていない側の子です。
  - 両方動かすと READY になり、confirm が `via: manual` と `moved: [両方の子]` を書きます。
  - 延期したフェーズ自身は常に NONE です（レビューを持たないため）。判定の単位は引き受けた側のフェーズです。
- 一部だけ動かして未解決の指摘が無いと、通常の confirm が通り、残りの子を `settled_by: confirm` で動かします（設計の「止めたまま」と違い、通常 confirm に任せています）。

### 5-3 親の範囲を改版した後の受け入れ台帳

結論: 対応済み（設計どおり）

- 根拠: 台帳は親ごとの `phases/<親>/accepted.json` です。改版の承認（`approval_ops` の開き直し）はフェーズのマーカーだけを消し、台帳は消しません（`approval_marks.py` 189 行のコメント）。数え方は鍵と版と最後のコメントの id だけを見て、フェーズ番号や範囲は見ません。台帳を消すのは ready の退避（`archive.py`、`phases/<親>/` を丸ごと）だけです。
- 注意: 改版で範囲が広がっても、受け入れた指摘は版が変わらない限り数えません。範囲が変わったことを理由に受け入れを失効させる仕組みはありません。設計もそれを求めていません。
- 勧める対応: 現状のまま。ADR-0152 に 1 行足してもかまいません。

## 6. 設計書との違い（判断材料だけ。直さない）

### 6-1 台帳に書くのが手動移動だけ

結論: 未対応（設計との違い。実害は小さい）

- 根拠: `approval_marks.py` 194-196 行に「実際に書くのは今は manual だけ。decide・close-early は台帳には書かない。`LEDGER_BY` の後ろ 2 つは書く側の無い予約の値」とあります。`add_ledger` の呼び出し元は `review.settle_and_mark`（423 行）だけで、その台帳の中身は `core_review.py` 182 行の `accepted_by: manual` だけです。`review_decide.py`・`review_close.py` は台帳を読むだけです（`review.ledger_of`）。設計の段階2「書いた後に外れた場合」の台帳（`accepted_by: decide`）と B5 の「解決した分を台帳に残す」も、ありません。
- 実害: 数え方には影響しません。decide・close-early が受け入れる指摘は、ホストで解決済みにするので、未解決として数えられません。失うのは「誰がどの版を受け入れたか」の記録と、解決を後で開き直されたときの再提起の見分けです（開き直しは未解決として数え直されるので、見落としにはなりません）。
- 勧める対応: 設計書を実装に合わせます（台帳は手動移動専用と書く）。または監査の記録が要るなら、操作記録の `applied` に残します。AT43 のテスト（`tests/ticket/test_decide_exe.py`・`tests/sh/test_decide_recovery.py`）で確かめた結果、ホストに書いた後に照合が外れた場合は、解決した分のキーが操作記録の `applied.accepted_on_host` に残ります。続きの子とマーカーは置かれません。台帳には残りません。

### 6-2 `thread_version` が 2 か所にある

結論: 対応済み（片方が呼んでいるだけ）

- 根拠: 実体は `review_host.thread_version`（409 行）です。`review.thread_version`（476 行）は `return review_host.thread_version(t)` と呼ぶだけです。台帳の照合 `_accepted_by_ledger` と `core_review.py` 180 行は `review_host` のものを、`review_decide.py` は `review.` のものを使いますが、中身は同じです。
- 勧める対応: 呼び出し元をどちらかにそろえます（任意）。

### 6-3 `settled_by: feedback-plan` を書く経路

結論: 対応済み（経路は無くなりました。設計どおりです）

- 根拠: `src/` に `feedback-plan` を書く処理はありません。残るのは、読める値としての定義（`ticket_model.SETTLED_BY_VALUES`）と、古い reviewed マーカーの `by: feedback-plan` を info で並べる lint（`lint_ticket.py` 248-276 行）だけです。判定には使いません。

### 6-4 `test_phases.py` の `--accept-done` が「計画のほかに未解決の指摘が残っている」で断るテスト

結論: close-early の経路のもの。decide とは食い違いません

- 根拠: `tests/ticket/test_phases.py` の `test_close_early_accepts_threads_in_three_stages`（1143 行付近）は、`user close-early` の `--accept-plan`・`--accept-check`・`--accept-done` を打ちます。断る文言の出どころは `review_close.py` 470 行です。`user reviewed --accept-unresolved` の経路ではありません。
- decide の経路は別で、追加の指摘を許します。`review_decide.changed_since_shown`（184 行付近）は「見せたあとに追加された指摘は食い違いに数えない」とします。設計の「追加だけなら適用する」と合います。
- 勧める対応: close-early は設計の対象外です（設計書の照合の表は decide 用です）。close-early が追加の指摘で断るのは、早めに閉じる前に増えた指摘を黙って捨てないためです。このままで足ります。

## 7. 保証しないことの一覧（`docs/design/limits.md`）

結論: 一部（12.1 に載っていない項目があります）

載っているもの: 外側での打ち切り（「ccnavi の外側で打ち切られた場合」）、hook の登録が消された場合、期限内に読み切れない場合、リモートの読み書きそのもの、レビューの中身の評価、プロジェクトをまたぐ依存です。

載っていないもの（別の文書にはあります）

| 項目 | 今の置き場 |
|---|---|
| コミットの時刻は手元の時計で、ホストの時計との差を余裕幅（10 分）で受けます。時計のずれは保証しません | ADR-0152 に余裕幅の記述があります。時計のずれの記述はありません |
| 手動移動でホスト上の未解決のスレッドが残ること。GitLab の「すべてのスレッドを解決しないとマージできない」設定では、MR をマージできません | ADR-0152 |
| 全文の範囲は最初のコメントだけで、返信をすべては取りません | `docs/design/tickets/review.md` 116-118 行 |
| 別の PC からの同時の送信は、ロックではなく照合で見分けます。結果が分からないときは自動で送らず `unknown` にします。厳密に 1 回だけ送ることは保証しません。同じ PC のロックだけが同時実行を防ぎます | ADR-0151 |
| 判定できないときの扱い（未知のツールは `dry-run` で通します。Read は最低限の deny だけで、`enable` の全拒否より緩いです） | `docs/design/startup-config.md` 121 行、ADR-0147 |
| 担当の子が分からない SubagentStop は差し戻さず、親に知らせるだけです | 設計書（削除済み） |
| 通常の形だけを守ります。手でマージを偽装するなど、通常でない形の手動移動は検出しません。この言い回しの項目はありません | `limits.md` の「敵対的な回避への防御」が近いです |

勧める対応: 12.1 に、上の表のうち上 4 行と、未知のツール・Read の緩和を 1 行ずつ足します。詳細の置き場へ誘導します。

## 8. A のテストで見つかった実装の欠陥（expectedFailure で固定したもの）

| テスト | 内容 | 設計 |
|---|---|---|
| `test_manual_settle.py` `test_the_same_child_in_review_and_done_is_broken` | `phase.phases_of` が同じ識別子を done 側 1 件に畳み、`assess` の重複検査に届きません。review/ に写しが残っていても READY になります | BROKEN |
| `test_manual_settle.py` `test_an_unshared_move_is_not_confirmed_even_with_no_threads` | 未共有の移動でも、未解決の指摘が無いと通常の confirm が通ります | AT38 は確定しない |
| `test_decide_exe.py` `test_the_same_operation_replayed_after_the_marker_came_back_makes_no_second_child` | `ccnavi_op` は書くだけで、同じ操作IDの子を使い回しません（`approval_ops._followup_ticket`） | AT13 |
| `test_decide_recovery.py` `test_resume_from_the_workspace_root_after_the_parent_worktree_is_gone` | `--resume` は親のワークツリーが無いと `decide-no-worktree` で断ります | AT25 |
| `test_decide_recovery.py` `test_two_resumes_of_the_same_operation_do_not_post_twice` | decide の `--resume` は操作IDのロックを取らず、issue が 2 件できます（`oplog.locked` は本番から呼ばれません） | AT20 |
| `test_write_and_push_post_fail.py` `test_a_post_response_without_a_url_is_not_reported_as_success` | GitLab の `id` 無しの 201 応答で、comment が「記録した」と言い `sent` にします（`ccnavi-common-review.sh` が `.id | tostring` で URL を組みます） | AT21 |

その他の設計との違い: AT28 の `.outcome` 保存に失敗したとき、標準エラーに書いて続行し、`unchecked` にはしません（従来の差し戻し無視の経路で補います）。`unchecked_scope` が親に知らせた時点で「促した」印を付けるため、知らせの後は Stop が重ねて促しません。AT33 では、keep だけで追加の指摘が残ると、子は動かず、マーカーも置かれません（fix を選んだときだけ `settled_by: decide` で動き、依頼のマーカーも消えます）。

## 9. 横展開と文書の更新漏れ（調査の追加分。直していない）

### 9-1 同じ原因が別の経路にある（確実。実地の頻度は低いと推測します）

- GitLab の応答に `id` が無いと URL が `#note_null` になり、送信済みにされます。8 節の AT21 と同じ原因が、次の箇所にもあります。
  - `ccnavi-user-lib-review.sh` 217 行付近 `issue_note()`（呼び元 315 行付近は、`.url` が空でなければ sent にします）
  - `ccnavi-common-review.sh` 303 行付近 `comment()`（request・ready の補助・comment・decide の note・close-early が通ります）
  - 照合側の `ccnavi-review.sh` 429 行付近 `find_posted()`、`ccnavi-common-review.sh` 345 行付近
  - Chrome 拡張の読み側 `core/gitlab.ts` 383・385・392 行付近（実害は要確認です）
  - GitHub は `.html_url // ""` で、空なら unknown にするので、同じ形ではありません。
- 操作IDのロックを取らないのは、decide の `--resume` だけではありません。`post_staged`（`ccnavi-user-lib-review.sh` 258 行付近）は decide の投稿すべて（新規の decide と `--resume`）に使われ、ロックを取りません。ロックを取るのは `staged_send`（`ccnavi-common-review.sh` 385 行付近）だけです。`ccnavi ops begin` が失敗すると、記録もロックも無いまま直接送ります（393-397 行付近）。`oplog.locked` は本番から呼ばれていません。ADR-0151 の「同じ操作の単発の投稿は操作IDのロックで直列にする」と、decide の投稿が合っているかは文言次第です（要確認）。
- `ccnavi_op` は `approval_ops.py` 318 行付近で書くだけで、読む箇所がありません（AT13）。
- 勧める対応: 欠けた `id` は成功と扱わない形にそろえます。decide の投稿にもロックを取らせるか、ADR-0151 を実装に合わせます。

### 9-2 文書の更新漏れ（確実）

- `docs/design/limits.md` 12.1 に無い項目（7 節の補足）: ホストの時計との差（余裕 10 分）、手動移動で未解決のスレッドが残ること（GitLab のマージ制限）、別の PC からの送信が 1 回きりとは限らないこと、判定できないときの緩和、担当の子が分からない SubagentStop の扱い、全文の範囲が最初のコメントだけであること。
- `.ccnavi/docs/ticket-flow.md` 130-139 行付近の告知に、古い下書きの取り込み（`resume_legacy_drafts`）と、`DECIDE_VERSION` 2 で版 1 のボードを断ることがありません（2 節の補足）。
- `.claude/skills/commit/references/test-groups.md` 21 行付近は「`test_write_and_push_*.py` の 6 本」と書いていますが、実数は 7 本です（本ブランチの `test_write_and_push_post_fail.py` を含みます）。この依頼では本番・文書を変えないため、直していません。
- expectedFailure で実装の欠陥を固定する運用は、どの文書にも書かれていません。8 節の 6 件が最初の例になります。

### 9-3 テストの残り（作らなかったもの）

- AT21 の欠けた `id` は comment の 1 経路だけ確かめました。`issue_note`・`find_posted` は未確認です。
- AT4 は、判定できないときの未知のツール名と空のツール名を、モードごとに確かめていません（Grep・Glob・PowerShell は足しました）。
