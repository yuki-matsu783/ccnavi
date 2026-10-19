---
type: design
title: Stop の AI 判定（agent 型 Stop フック）
description: チケット提案の取りこぼし（R1 提案を書かず許可だけ取る・R2 具体性が無い・R4 送らない）を、独立したコンテキストの agent 型 Stop フックで判定する仕組みの構成、状態、記録、評価、限界
tags: [design-doc, tickets, hooks]
keywords: [Stop, agent hook, AI 判定, 判定役, 共有キー, 試行, 判定計画, material, record, 差し戻し, 再判定, propose, 操作実績, 評価, dry-run]
---

# Stop の AI 判定（agent 型 Stop フック）

決めた理由は [docs/adr/0146-ai-review-at-stop.md](../adr/0146-ai-review-at-stop.md)。ここは現在の実装の構成だけを書く。

## 構成

```
Stop ─┬─ command hook（ccnavi）: 既存の促しを実行 → 共有キーで判定計画と試行を 1 件書く
      └─ agent hook（判定役）  : stop-review material → 判定 → stop-review record → 返り値をそのまま返す
```

- **共有キー**: セッション ID と、transcript 末尾のサイドチェーンでないアシスタント行の `uuid`（`records/stopreview.py` の `shared_key`）。末尾が書きかけなら短く読み直し、それでも読めなければ導けない（未検査）。
- **判定計画**（`stopreview-plan-*.json`）: 候補か否かと理由、AI による継続か・再判定か、対象（Stop の機会と提案の版）、材料の版、実効モード、issue／MR の指定、提出済みの提案と承認状態、汚染の印。同じキーへの書き込みは冪等。
- **候補条件**（`stopreview.candidate`）: メインエージェントの Stop、`stop_hook_active` が偽（または AI による継続で未再判定）、`CCNAVI_STOP_REVIEW` が disable でない、チケット制御が有効、agent hook が登録済み、既存の促しが止めていない、対象を解決できる、かつ（着手済みでない親に属する提案の操作実績か未送信の提案がある、または cwd に着手済みの承認済みチケットが無い）。候補でない Stop は ccnavi が最終分類（対象外・検査省略・未検査）まで確定する。
- **試行の状態**: `plan_wait` → `pending` → `running`（material）→ `done`（record が受理）／ `sent_back`（差し戻し送出）→ `consumed`（次の Stop で消費）。期限切れ（`expired`）は次の hook 起動時と集計時に確定する。

## stop-review

`ccnavi stop-review material|record <セッション> <transcript>`（`entry/cli_stopreview.py`）。どちらも終了コード 0 で、`record` の標準出力は agent hook がそのまま返す JSON 1 行だけ。拒否・失敗・打てないときは `{"ok": true}`。

- `material`: 計画を最大 5 秒待ち、材料を区切ったデータとして返す。材料は、今回のターン、直前 5 件のユーザ発言、判定用 Markdown（`.ccnavi/config/stop-review.md`、無ければ組み込み）、issue／MR の指定、提出済みの提案と承認状態、提案の版ごとの本文（送ったコミットから読む。未送信は書いたファイルの中身）。本文中の区切りは無害にする。判定に渡した材料は伏せ字にして `logs/stop-review/material/` に保存する。
- `record`: 構造を検証し（試行 ID・対象・基準・種別つきの問題箇所と修正・必要な基準の網羅）、材料の版と承認状態を確かめ直し、最終分類を確定する。enable で不備検出のときだけ `{"ok": false, "reason": …}`。差し戻しの文に、試行 ID、基準 ID・問題箇所・修正、「本当にユーザの判断が要る問いなら、修正の後にその問いを最後に書き直して終える」の一文を必ず含める。再判定は差し戻さない。
- `report`: 評価の集計（基準ごとの検査カバー率・見逃し率・誤差し戻し率・不要介入率・修正率と、分けて数えるもの）。独立した評価は `logs/stop-review/labels.jsonl`。

最終分類は、AI が「チケット作業の依頼ではない」→ 対象外（判定者 AI）、材料不足 → 未検査、R1・R2・R4 に受理された不備 → 不備検出、必要な基準に判定不能 → 未検査、それ以外 → 終了可。出力の検証は構造だけを確かめる。指摘が正しいことは保証しない。

## propose の操作実績（`records/stopops.py`）

- PreToolUse が `ccnavi-ticket.sh propose <識別子>` を読んで `tool_use_id` と識別子を登録する（1 つの Bash に複数あれば識別子ごと）。
- `ccnavi-ticket.sh propose` は、自分をもう一度起動する外側の層で、どの道を通っても `logs/state/stopops-result/*.kv` を 1 つ書く。道は 7 つ（送った・変更なし・書くだけ・止めた・コードが未 push・送る前に失敗・push 後の後処理で失敗）。送ったコミットとパスごとのブロブも書く。
- PostToolUse・PostToolUseFailure が識別子と時刻の範囲で結び付ける。結べないものは Stop と集計で結び直し、30 秒過ぎても見つからなければ未連携。
- 提案ごとの最終の有効版が判定の対象。`todo/<識別子>.md` への Write・Edit を記録し、書いた後に送った操作が無いものを未送信とする（Bash で書いた提案は検出できない）。

## 判定役の除外（`hook/stopjudge.py`）

`CCNAVI_STOP_REVIEW_JUDGE_TYPE` に判定役の `agent_type` が設定されているときだけ、判定役の呼び出しを見分ける。PreToolUse は `stop-review material|record` の Bash だけ通し、ほかは止める。PostToolUse・PostToolUseFailure・SubagentStart・SubagentStop・Stop は何もしない。作業者の `stop-review` は止めて汚染の印を付ける（dry-run では印だけ）。

## 登録と診断

- `scripts/ccnavi-stop-review.sh register|unregister|check`: `.claude/settings.json` に agent 型の Stop hook を 1 件（prompt の先頭行 `[ccnavi-stop-review]` が目印）と `CCNAVI_STOP_REVIEW_AGENT=1` を足す・外す・点検する。他の hook・env は保持し、重複は 1 件に直す。
- `ccnavi --lint`: `CCNAVI_STOP_REVIEW` の値、印と hook の食い違い、agent hook の重複、enable で判定役を見分ける値が無いこと。

## 記録の置き場と後始末

試行のイベントは `logs/stop-review/events.jsonl`（`decisions.jsonl` には書かない）。計画・試行・操作は state の置き場。`records/prune.py` が、セッションを名前に持つものはセッションごとに、持たないものはそれ自身の日付で（state の保持日数）、再評価用の材料と events は `CCNAVI_STOP_REVIEW_KEEP_DAYS`（既定 90 日）で消す。

## 限界

- 成立性の検証（共有キーの不一致率・判定役の見分け・Bash の実行可否・記録外の停止の率・費用）は実機でまだ測っていない。しきい値も未決。
- 作業者が `stop-review` を打つことは、判定役を見分ける値が設定されていなければ止められない。悪意のある作業者を完全には防げない。
- 費用・待ち時間は取れていない（集計は「不明」）。
- 子の作業完了の判定・finish の促しの引き継ぎ・着手済みの親への子の追加は対象外。
