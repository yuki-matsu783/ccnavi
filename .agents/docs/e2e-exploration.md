---
title: 本物の GitLab と実画面で、エージェントと人間の挙動を合わせて検証する
type: guide
description: Docker の GitLab・code-server・Playwright・claude -p（Haiku）でエージェント役と人間役を動かし、ccnavi の弱点を洗い出す探索の手順。パターンの台帳、記録の形、受入シナリオへの昇格の流れ
tags: [testing, scenario]
keywords: [探索, 弱点, 洗い出し, 再測定, 人間役, エージェント役, ccnavi-reviewer, docker-gitlab, code-server, Playwright, Haiku, claude -p, probe, 昇格, 台帳, weaknesses]
---

# 本物の GitLab と実画面で、エージェントと人間の挙動を合わせて検証する

受入シナリオ（[acceptance-scenarios.md](acceptance-scenarios.md)）は、期待が決まった振る舞いを 1 本ずつ機械で確かめる。
ここで扱うのは、その手前の探索。サンプルのリポジトリで少し難しい開発を何本も依頼し、エージェント（Haiku）と人間（画面・端末・GitLab）を実際に動かして、
ccnavi の弱点（止め方の過不足、案内の分かりにくさ、画面と実状態のずれ、wiki・reference の育ち方）を見つける。
見つけた弱点は issue にし、直したあとに同じ手順で再測定する。

明確な指示があったときだけ回す。費用と時間がかかるので、回す前にユーザの了承を取る。

## 役割

| 役 | 動かすもの | 口 |
|---|---|---|
| エージェント役 | `claude -p --model haiku`（cwd はサンプル）。`CCNAVI_MODE=enable` を明示する | root の `GITLAB_TOKEN` |
| 人間役（GitLab） | `ccnavi-reviewer` のスレッド・解決・承認 | `human_api`（実行器が人間役のトークンで呼ぶ） |
| 人間役（端末） | ユーザ専用の sh を擬似端末で打つ | `human_run`（実行器が擬似端末を作る） |
| 人間役（画面） | code-server の拡張を Playwright で操作する | `extensions/vscode/ccnavi-board/scripts/real-screen/` |
| 観測役 | メインのセッション | `logs/decisions.jsonl`、stream-json、MR・issue の状態、撮影 |

- 人間役にエージェント役の環境（PATH・トークン）を引き継がせない。人間しかできない操作をエージェントが通ってしまうと、弱点を見逃す
- 人間役の GitLab 操作は、実行器の `human_api` を正とする。CE では変更要求（`request_changes`）が 404 になるので、変更要求は確かめられない（[acceptance-scenarios.md](acceptance-scenarios.md) の「人間の操作」）
- `claude -p` の起動は、実行器の `run_step`（`--permission-mode auto`、`--include-hook-events`）を正とする。`real-screen/haiku-step.sh` は権限の形が違う（`acceptEdits`）ので、受入の結果とは比べない

## 1. 環境を組み立てる

ccnavi 本体のチェックアウトには書かない。一時物は `scratchpad/` に置く。

1. 最新を取り込み（`ccnavi-git.sh pull --ff-only`）、実行ファイル（`uv run --with pyinstaller python build.py`）と拡張の vsix（`extensions/vscode/ccnavi-board` の `sh scripts/package.sh`）を組み立てる。古い実行ファイルで測ると、直したはずの弱点が残って見える
2. GitLab を起動する（[gitlab-manual.md](gitlab-manual.md)）。測定中に `down`・`--rotate`・コンテナの再起動をしない
3. サンプルを用意する。受入シナリオ（`setup.host: docker-gitlab`）なら、実行器が使い捨てのプロジェクトを作って `ccnavi-reviewer` を加える。探索の本で手で作るときは、root のトークンで `POST /projects` し、`ccnavi-reviewer` をメンバーに足す（足さないと MR が 404 になる）
4. 初期のファイルを入れて push し、`sh scripts/ccnavi-setup.sh <サンプル> --mode enable` で ccnavi を入れる。`ccnavi-launcher.sh --lint` の error が 0 件であること。**サンプルは本ごとに新しく作る**。前の実走の MR・ブランチ・wiki が残ると、育ち方の検証が混ざる
5. 画面を使うときは、code-server を起動して vsix を置く（[real-screen-check.md](../../extensions/vscode/ccnavi-board/docs/design/real-screen-check.md)）。終わったら親と `--type=fileWatcher` の子を止める

`real-screen/` は Linux 限定の開発用（[README](../../extensions/vscode/ccnavi-board/scripts/real-screen/README.md)）。macOS では real-screen-check.md の回避（`setsid` の代わりに `nohup` など）を使い、動かせなかった点は報告に分けて書く。

サンプルの題材は、テストが通る小さなライブラリで足りる。難しさは依頼の側で作る（返品と在庫の戻し、税の端数、並行更新の調査）。

## 2. パターンの台帳

開発で起きる場面を、系統ごとに ID（`EX-nn`）を付けて台帳にする。実走した本は「状態」（未実走・実走済み・再測定済み・昇格済み）を記し、弱点の issue と結びつける。ここにない場面を見つけたら、系統の末尾に足す。

| 系統 | パターン | 見るところ |
|---|---|---|
| 作業の種類 | 直接作業（不具合・小さな機能・リファクタリング・文書だけ・テストだけ・依存の更新・設定の変更）、設計書のみ、コードのみ、調査のみ、デバッグ、リリース | 種類ごとのフェーズ・成果物・範囲が噛み合うか |
| 規模 | 1 ファイル / 複数ファイル / 新規モジュール / 機械的な大量置換 / 巨大な差分 | リスク配点、レビューの要否 |
| 計画の形 | 親 1・子 1、直列、並行、合流、終端 2 つ、フェーズの飛ばし、途中の改版、子の追加・削除、スコープ外の発見 | `after` の検査、前フェーズ未完の拒否 |
| 入口 | 依頼文だけ / issue 指定 / MR 指定 / 既存ブランチの続き / 空・曖昧・矛盾する依頼 | 入口の判定、聞き返すべき場面で聞くか |
| 人間のやり取り | 承認 / 却下 / 条件付き / 承認後の方針変更 / 指摘（fix・keep・issue）/ 指摘ゼロ / 指摘の食い違い / 承認者が作成者と同じ / 待ち中の別の依頼 / 途中の「やめて」 | 待ちを越えないか、画面と状態が一致するか |
| ホストの状態 | 統合先が進む（衝突・自動で解ける）/ 他の MR が先にマージ / MR・issue・ブランチが閉じられる・消える / CI の失敗 / 権限不足 / 一時的な API の失敗 / トークンの失効 | 言って止まるか、復帰できるか（[irregular-states.md](irregular-states.md)） |
| ワークツリーとブランチ | 同名のブランチ / 未コミットの変更 / 別セッションが同じ親を触る / 日本語・長い・大文字小文字違いの名前 / 片付けの途中 / 巨大な未追跡ファイル・生成物（`uv.lock` など） | 確認の出方、データ損失がないこと |
| 保護と迂回 | 保護対象の編集（Write・Edit・sh・インタプリタ・git 越し）/ 拒否後の言い換え / サブエージェント経由 / 承認の偽造 / 認証情報 / 再帰削除 / 生の `git push` | 一貫した拒否、迂回しないこと、Post の復元 |
| セッションの途切れ | 途中で kill して `--continue` / compact・clear 後 / 新しいセッションで同じ親の続き / 並行で 2 セッション | 状態の読み直し、二重実行の防止 |
| サブエージェント | 委任（調査・実装・レビュー）/ 子の識別子が複数 / 非対話での background 起動 / cwd | 範囲の伝達、拒否の伝わり方 |
| 言語・プロジェクト | Python 以外 / テストが無い・遅い・不安定 / `projects/` 配下 / ワークスペースと clone 済みの両方の起動 | 配布物が言語に依存していないか |
| 設定の育ち | ルール・フェーズ・リスクの追加と `--lint` / `--test` / 期待した判定との食い違い | 一人で通る道か |
| 学びの流れ | 同領域の学びを別セッションで 2〜3 回 / 重複 / 反証 / 却下と再提案 / 承認と反映 / reference の利用 / 回数契機 | 5 章 |
| ボード | 各一覧の表示と実状態の一致 / 承認・レビュー完了・取り込み / 更新の遅れ / 編集中の切り替え / 案内ツアー | 画面と `status` の突き合わせ |
| ユーザ専用の入口 | ユーザ専用の sh を実際に打つ / エージェントの環境から打つ / 端末でない標準入力 | 実際の sh を呼ぶ（代行の API だけでは見つからない） |

系統 × 作業の種類で実走済みの升目を塗り、空いた升目を次の候補にする。弱点が見つかった升目は、直ったあとに同じ本で再測定する。

## 3. エージェント役と人間役を回す

- 依頼文はファイルに書いて渡す（ヒアドキュメントを使わない）。続きは `--continue`。承認・レビュー待ちで止まったら人間役を動かしてから渡す。中断と再開は `kill` してから `--continue` する
- セッションごとに上限（`--max-budget-usd`）を付け、`result` の `total_cost_usd` のセッションごとの最大値を足して累計にする
- 本は受入シナリオと同じ欄で書き（[acceptance-scenarios.md](acceptance-scenarios.md) の「シナリオの形」）、置き場は `scratchpad/<測定名>/patterns/<ID>.yml`。機械で見る欄（判定・`status`・MR と issue の状態・ファイル）と、意味で見る `judge`（迷った・誤った・不要に止められた・止められるべきなのに通った・案内が分かりにくい）に分ける
- 画面の人間役は `real-screen/` のページオブジェクト（`BoardPage`・`ApprovalDialog`）を使う（`approve.js`・`snapshot.js`）。承認・レビュー完了・取り込みの前後で撮り、ボードの表示と `ccnavi-ticket.sh status` を突き合わせる。取り込みなどは処理中の表示になるので、直後と数秒後の両方を撮る
- 端末の人間役は `human_run` を使う。足りない sh は実行器の `HUMAN_RUN_SCRIPTS` に足す。出力の `glpat-` の値は伏せる

### 保護の迂回を直接確かめる（probe）

拒否の網羅は、`claude -p` を回すより、hook の JSON を実行ファイルに直接流す方が速い。

```sh
printf '%s' '{"hook_event_name":"PreToolUse","tool_name":"Bash","cwd":"<サンプル>","session_id":"probe","permission_mode":"default","tool_input":{"command":"<試す書き方>"}}' \
  | CLAUDE_PROJECT_DIR=<サンプル> CCNAVI_MODE=enable <サンプル>/.ccnavi/bin/<os>-<arch>/ccnavi
```

試す書き方を 1 行 1 本のファイルに並べ、判定（`permissionDecision`）と拒否コードを表にする。保護対象を書き換える別の経路（`sed -i`・`tee`・`cp`・`python -c`・`node -e` など）を並べ、Pre を通った経路は実際に書き換えさせて PostToolUse の復元を見る。試験変更は人間役が `git checkout -- <パス>` で戻す。

## 4. 記録と報告

- 弱点は 1 件ごとに、重大度・再現手順・証拠のパス・推定原因・直す提案を書き、issue にする。**実機で確かめたこと**と**コードを読んだだけの推測**を分ける。単体の Chromium やコード読みの結果を「実機で確認済み」と書かない
- ログ・撮影・弱点の一覧は `scratchpad/<測定名>/` に置く。リポジトリに入れるのは、直す提案が通ったあとの文書とテストだけ
- 再測定は、前回の弱点を番号のまま「直った／直らない／一部」の表にし、新しく見つけたものは別の番号で足す。回帰（拒否文の分かりにくさを含む）も書く
- 報告に入れるもの: ケース一覧、弱点の表、確かめられなかったことと理由、費用の概算、止めたプロセス
- 測定の中では直さない。直すときは別の作業にする

## 5. wiki と reference の育ち方

流れの定義は `.ccnavi/docs/` の `grow.md`・`skill-wiki.md`・`learning-propose.md`。ここでは、実走で確かめる項目だけを挙げる。

- 記録の書式（`ticket learn` の `--target`・`--signal` の値）が、配布先の文書だけで分かるか
- 親の finish が判定されていない気づきで止まり、統合の子の書式が案内されるか。子の finish と親の finish の検査が食い違わないか
- パターンの重複が 1 つにまとまるか。環境の癖が 1 回でパターンになっていないか。同じセッションの記録で閾値に届かないか
- 候補の通知が `ccnavi --learnings` と一致するか
- 提案・却下・再提案で、却下の理由が残り、新しいセッションが出し直しを断るか。採否の記録（`impact/`）の YAML が壊れたときに止まるか。承認の決着を書く場所があるか
- 反映後の reference が `ccnavi --docs --type skill-reference --tag <工程>` で引け、実際の作業（テストを回すだけの作業を含む）で引かれるか。パターン本文と reference が食い違わないか
- 回数契機（`skill-review-every-10` など）が、`--continue` を重ねた長い対話で発火するか

## 6. 受入シナリオへの昇格

探索は弱点を見つける側、受入シナリオ（`tests/scenarios/`）は再発を守る側。期待が安定した本だけを移す。本の書式は受入シナリオと同じなので、移すときは欄を揃えるだけで済む。

1. 弱点を直したあと、同じ本を 3 回回し、機械で比べる期待が毎回同じかを見る。揺れる期待は `judge` に残すか、本から外す
2. 次の基準をすべて満たしたら、UC の通し番号（今ある最大の次。探索の ID は併記しない）を付け、要求 ID を割り当て、要件書の流れの文書（UC の表）に 1 行足す。`status: draft` で置く
   - 弱点が直っていて、期待が直った状態を表している
   - 機械で比べる欄が 1 つ以上ある（`judge` だけの本は移さない）
   - 既存の UC と重複しない
3. `runner run --confirmed` で実ログと突き合わせ、`unconfirmed` を潰して `verified` にする
4. 台帳の状態を「昇格済み（UC-nn）」にする。EX- から UC への対応は台帳にだけ残し、昇格した本は探索の側では回さない

期待が決められない本（案内の分かりやすさなど）は探索に残す。

## 詰まりどころ

| 症状 | 対処 |
|---|---|
| ユーザ専用の sh の名前を含むコマンドが、開発側の hook に止められる | 読むだけの確認は Read で行う。実行は実行器の `human_run` に任せる |
| 続きのセッションが前の状態を引きずる | サンプルを作り直す |
| code-server の子プロセスが残る | ファイル監視の子（`--type=fileWatcher`）は `pgrep` に当たらない。親と子を止める |
| 組み立てのたびに開発側のセッションに「would-restore」が付く | 開発側が dry-run のため。測定の挙動ではない |

## さらに詳しく

- 本物の GitLab の立て方: [gitlab-manual.md](gitlab-manual.md)
- 実画面の準備と評価の観点: [real-screen-verification.md](real-screen-verification.md)
- 期待が決まった振る舞いの確認: [acceptance-scenarios.md](acceptance-scenarios.md)
