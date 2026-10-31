---
type: design
title: Stop の AI 判定（agent 型 Stop フック）
description: チケット提案の取りこぼし（R1 提案を書かず許可だけ取る・R2 具体性が無い・R4 送らない）を、独立したコンテキストの agent 型 Stop フックで判定する仕組みの構成、状態、記録、評価、限界
tags: [design-doc, tickets, hooks]
keywords: [Stop, agent hook, AI 判定, 判定役, 共有キー, 試行, 判定計画, material, record, 差し戻し, 再判定, propose, 操作実績, 評価, dry-run]
---

# Stop の AI 判定（agent 型 Stop フック）

## 構成

```
Stop ─┬─ command hook（ccnavi）: 既存の促しを実行 → 共有キーで判定計画と試行を 1 件書く
      └─ agent hook（判定役）  : 材料を取る → 判定 → 結果を渡す → 返り値をそのまま返す
```

- **共有キー**: セッション ID と、transcript 末尾のサイドチェーンでないアシスタント行の `uuid`（`records/stopreview.py` の `shared_key`）。末尾が書きかけなら短く読み直し、それでも読めなければ導けない（未検査）。
- **判定計画**（`stopreview-plan-*.json`）: 候補か否かと理由、AI による継続か・再判定か、対象（Stop の機会と提案の版）、材料の版、実効モード、issue／MR の指定、提出済みの提案と承認状態、汚染の印。同じキーへ何度書いても結果は変わらない。
- **候補条件**（`stopreview.candidate`）: メインエージェントの Stop、`stop_hook_active` が偽（または AI による継続で未再判定）、`CCNAVI_STOP_REVIEW` が disable でない、チケット制御が有効、agent hook が登録済み、既存の促しが止めていない、対象を解決できる、かつ（着手済みでない親に属する提案の操作実績か未送信の提案がある、または cwd に着手済みの承認済みチケットが無い）。候補でない Stop は ccnavi が最終分類（対象外・検査省略・未検査）まで確定する。
- **試行の状態**: `plan_wait` → `pending` → `running`（material）→ `done`（record が受理）／ `sent_back`（差し戻し送出）→ `consumed`（次の Stop で消費）。期限切れ（`expired`）は次の hook 起動時と集計時に確定する。計画を書くときに試行のファイルが在るのに読めなければ、判定せず未検査（`attempt_unreadable`）で確定する。次の Stop は別の共有キーなので、新しい試行になる。

## stop-review

判定役が使う 2 つの入口（材料を取る・結果を渡す。`entry/cli_stopreview.py`）。どちらも終了コード 0 で、結果を渡す側の標準出力は agent hook がそのまま返す JSON 1 行だけ。拒否・失敗・実行できないときは `{"ok": true}`。作業者向けの文書と索引には、打ち方を載せない。

- 材料を取る側: 計画を最大 5 秒待ち、材料を区切ったデータとして返す。材料は、今回のターン、直前 8 件のユーザ発言、判定用 Markdown（`.ccnavi/config/stop-review.md`、無ければ組み込み）、issue／MR の指定、提出済みの提案と承認状態、提案の版ごとの本文（送ったコミットから読む。未送信は書いたファイルの中身）。本文に区切りの文字列があっても無害にする。判定に渡した材料は伏せ字にして `logs/stop-review/material/` に保存する。
- 結果を渡す側: 構造を検証し（試行 ID・対象・基準・種別つきの問題箇所と修正・必要な基準の網羅）、材料の版と承認状態を確かめ直し、最終分類を確定する。enable で不備検出のときだけ `{"ok": false, "reason": …}`。差し戻しの文に、試行 ID、基準 ID・問題箇所・修正、「本当にユーザの判断が要る問いなら、修正の後にその問いを最後に書き直して終える」の一文を必ず含める。再判定は差し戻さない。
- 評価の集計（ユーザ向け）。基準ごとの検査カバー率・見逃し率・誤差し戻し率・不要介入率・修正率と、分けて数えるものを出す。独立した評価は `logs/stop-review/labels.jsonl` に書く。

最終分類は、AI が「チケット作業の依頼ではない」→ 対象外（判定者 AI）、材料不足 → 未検査、R1・R2・R4 に受理された不備 → 不備検出、必要な基準に判定不能 → 未検査、それ以外 → 終了可。出力の検証は構造だけを確かめる。指摘が正しいことは保証しない。

## approval-request の操作実績（`records/stopops.py`）

- PreToolUse が `ccnavi-ticket.sh approval-request <識別子>` を読んで `tool_use_id` と識別子を登録する（1 つの Bash に複数あれば識別子ごと）。
- `ccnavi-ticket.sh approval-request` は、自分をもう一度起動する外側の層で、どの道を通っても `logs/state/stopops-result/*.kv` を 1 つ書く。経路は 6 つ（送った・変更なし・書くだけ・止めた・送る前に失敗（コードが未 push で拒否した回を含む）・push 後の後処理で失敗）。送ったコミットとパスごとのブロブも書く。
- PostToolUse・PostToolUseFailure が識別子と時刻の範囲で結び付ける。結べないものは Stop と集計で結び直し、30 秒過ぎても見つからなければ未連携。
- 提案ごとの最終の有効版が判定の対象。`todo/<識別子>.md` への Write・Edit を記録し、書いた後に送った操作が無いものを未送信とする（Bash で書いた提案は検出できない）。

## 判定役の除外（`hook/stopjudge.py`）

判定役の呼び出しは、payload の `agent_id` が `hook-agent-` で始まることで見分ける（実機で確かめた。登録したワークスペースだけ）。PreToolUse は、判定役の 2 つの入口を呼ぶ Bash と返り値の道具だけ通し、ほかは止める。PostToolUse・PostToolUseFailure・SubagentStart・SubagentStop・Stop は何もしない。作業者の `stop-review` は止めて汚染の印を付ける（dry-run では印だけ）。

## 登録と診断

- `scripts/ccnavi-stop-review.sh register|unregister|check`: `.claude/settings.json` に agent 型の Stop hook を 1 件（prompt の先頭行 `[ccnavi-stop-review]` が目印）と `CCNAVI_STOP_REVIEW_AGENT=1` を足す・外す・点検する。他の hook・env は保持し、重複は 1 件に直す。
- `ccnavi --lint`: `CCNAVI_STOP_REVIEW` の値、印と hook の食い違い、agent hook の重複、enable で判定役を見分ける値が無いこと。

## 使い方の仕様

- **登録の既定。** 判定役のモデルは `haiku`、タイムアウトは 60 秒で、`register --model <名前>` で変えられる。既定では登録されず、登録しても `CCNAVI_STOP_REVIEW` の既定は `dry-run`（記録だけ残して止めない）。`ccnavi-stop-review.sh` は `--root <ワークスペース>` も受ける。
- **効き方。** `CCNAVI_STOP_REVIEW` は `dry-run` / `enable` / `disable`。`enable` のときは、検証に通った不備検出だけを 1 回差し戻す。差し戻しの次の Stop では、既存の促しを 1 回通常どおり動かし、修正後の材料で 1 回だけ再判定する。
- **判定の基準。** 組み込みの基準を、ワークスペースの `.ccnavi/config/stop-review.md` があればそれで置き換える（無ければ組み込み）。ファイルが読めない・空のときは、別の基準を推測せず未検査にする。
- **独立した評価。** 人が付ける正否は `logs/stop-review/labels.jsonl` に 1 行 1 件の JSON `{"attempt_id", "criterion", "target", "truth": "flaw"|"ok"}` で書く。評価が無い指標は集計に「不明」と出る（`ccnavi stop-review report`）。
- **1 Stop あたりの追加。** 待ち時間は中央値で約 +8.9 秒（許容は約 10 秒まで）、費用は +0.0025〜0.0046 ドル（実測値。下の「実測で確かめた前提」）。

## 記録の置き場と後始末

試行のイベントは `logs/stop-review/events.jsonl`（`decisions.jsonl` には書かない）。計画・試行・操作は state の置き場。`records/prune.py` が、セッションを名前に持つものはセッションごとに、持たないものはそれ自身の日付で（state の保持日数）、再評価用の材料と events は `CCNAVI_STOP_REVIEW_KEEP_DAYS`（既定 90 日）で消す。

## 実測で確かめた前提（Claude Code 2.1.303、`claude -p`）

- 共有キー: command hook と agent hook の `session_id`・`transcript_path` は同じ。キーの不一致は無かった。
- 判定役の見分け: agent 型 hook の中の呼び出しは `agent_id` が `hook-agent-<uuid>` で、`agent_type` は無い。PreToolUse・PostToolUse・SubagentStop（失敗時）が来る。
- Bash の実行: 許可の規則では実行できない（`dontAsk`。未信頼のワークスペースでは `permissions.allow` が無視される）。PreToolUse の許可の応答で実行できる。
- 返り値: 判定役は結果を渡す入口を実行し、その出力のとおりに返した。差し戻しの文は次の Stop で `stop_hook_active` が真になって届き、再判定で解消できる。
- 追加の時間と費用: 単純な 1 ターンで、判定役の追加は待ち時間が中央値で約 +8.9 秒、費用が +0.0025〜0.0046 ドル。

## 限界

- 判定役の Bash は 2 つの入口のどちらか 1 本（と標準入力を渡す `echo`・`printf`・`cat`、ヒアドキュメント）だけ通す。連結・リダイレクト・コマンド置換は止める。判定役の制限は dry-run でも実際に止める（判定役はほかの hook の判定の対象ではなく、制限しないと transcript の命令で任意のコマンドが通るため）。
- 作業者の呼び出しは、サブエージェントの分も含めて止めて印を付ける。`sh -c`・`eval`・変数展開のように読み取れない形は、`stop-review` と動詞が並んでいれば印だけ付ける（止められない）。
- ユーザ自身の agent 型 hook も `hook-agent-` で始まる `agent_id` になるので、登録したワークスペースでは判定役として扱われる（Bash は止まる）。
- `material` と `record` に認証は無い。セッション ID と transcript のパスが分かれば誰でも打てる。防ぐのは上の PreToolUse の制限だけで、見分ける値が無ければ防げない。
- 外側の層を飛ばす `CCNAVI_PROPOSE_INNER=1 sh ccnavi-ticket.sh approval-request …` と、外側に TERM だけが届いた場合は、操作実績が残らない（未連携として数える）。
- 差し戻しの消費には窓がある（既定 600 秒）。窓を過ぎた差し戻しは、次の Stop で消費されず、期限で閉じる（最終分類は不備検出のまま、修正率の「再判定なし」に数える）。
- 作業者による判定役の入口の実行は、悪意のある作業者を完全には防げない。
- 費用は hook から取れない（集計は「不明」）。待ち時間は、計画の作成から判定の完了までを集計に出す。
- 子の作業完了の判定・finish の促しの引き継ぎ・着手済みの親への子の追加は対象外。
