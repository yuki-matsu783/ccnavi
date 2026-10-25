---
type: adr
title: チケット作業を issue と MR のセットに限り、入口でホストの API と git のリモートの使える状態を確かめる
description: issue なしで親チケットを始める道をやめ、開始の成功の条件を「採用した MR・その MR が閉じる issue・元ブランチの確定」にする。入口と子の作業から始める操作で、ホストと git の使える状態を事前に確かめる
tags: [ticket, worktree]
keywords: [issue, MR, ccnavi-start.sh, 事前確認, preflight, 権限, closingIssuesReferences, next-id, SessionStart, 標準エラー]
---
# ADR-0160: チケット作業を issue と MR のセットに限り、入口でホストの API と git のリモートの使える状態を確かめる

状態: 採用（ADR-0102 の通し番号の、親チケットへの用途をやめる。識別子の形と動きは変えない。移行と後方互換は扱わない）

改訂（2026-10-24）: issue が無いときの道を、ユーザに作ってもらうことから、エージェントが `--new-issue` で作ることに改めた。`--mcp-checked` は「MCP またはユーザがブラウザで確かめた」ことの申告で、エージェントが確かめずに付けない。ADR-0161。

## 状況

issue なしで親チケットを始める道（`next-id` → `ccnavi ticket new`）があった。レビューは MR が前提で、MR を作る道具は `ccnavi-start.sh --issue` だけなので、この道は最後まで回らなかった。
また、ホストの認証・権限・git のリモートが使えない状態は、ワークツリーや MR を作ったあと、または書き込みの途中で初めて分かっていた。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| チケット作業は issue と MR のセット。親の提案の `issue:` と `mr:` を、`ticket new`・承認（新規と改版）・`--lint`（承認待ち・doing・review・done の親）で必須にする。子には求めない | 回らない道を入口で消す | issue なしの親を許す |
| `ccnavi-start.sh` の開始が終了コード 0 で終わったら、採用した MR・その MR が**閉じる** issue・元ブランチが確定している。`--dry-run` は完了ではない。閉じるリンクは GitHub の GraphQL（`closingIssuesReferences`）と GitLab の `closes_issues` で読み、読めない・読み切れないときは 0 件と取り違えず終了コード 4 にする | 本文で参照しているだけの issue と、閉じる issue を区別するため | 本文の `#番号` の文字列検索 |
| ホストを見られないときは手元の候補だけで続けない。`--issue`・`--mr`・`--branch` と `--mcp-checked` がそろうときだけ、ホストを見ずに成功してよい。`--mcp-checked` は「別の手段（MCP、またはユーザのブラウザ）で確かめた」ことを示すだけで、ホストにしか無い情報は補わない | 既存の MR も閉じる issue も確かめられないため | 手元の候補が 1 件なら続ける |
| `next-id` は残し、直接作業のワークツリー名に付ける通し番号に役割を絞る | 直接作業の名前の形をそろえる用途は残るため | 廃止 |
| 事前確認 `ccnavi-start.sh --check`（手で打つ）と、`ccnavi-ticket.sh` の start・finish・propose・consolidate の自動の確認（ロックの前）。項目は tools・origin・integration・api・access・git。結果は ok・ng（確定した不備）・unverified（確かめられなかった）・skipped（前提が ok でない）。全体は ng があれば ng、無くて unverified か skipped があれば unverified、それ以外は ok。権限だけ、ok に「判定できない」の印を付けてよい | 作る前・書く前に、ユーザの対応が要る不備を名指しするため | 失敗してから分かる形のまま |
| 権限の判定が保証するのは「リポジトリのロール上は書き込める」まで（GitLab は `access_level` が 30 以上、GitHub は `permissions.push`）。トークンの scope・ブランチの保護・API と git が別の資格情報で動いていること・`is_admin` は見ない。文面にもそう書く | 見ていないものを見たと言わないため | `is_admin` で通す近道 |
| キャッシュはしない。1 回の問い合わせは 15 秒、事前確認全体は 60 秒まで。gh / glab / curl の呼び出しは、`timeout` コマンドに頼らずバックグラウンドで動かして上限で止める。上限を過ぎたら unverified、全体を過ぎたら残りは skipped | 認証や権限は途中で変わる。応答しないホストで止まり続けないため | キャッシュ、`timeout` コマンド |
| 自動の確認は SessionStart に入れない | 直接作業のセッションでも走ってしまう。制限時間がある。実行ファイルはネットワークに出ない（ホストへの問い合わせは sh が行う） | SessionStart |
| 自動の確認の出力は標準エラーに出す。標準出力には何も足さない | propose の標準出力は契約（先頭が `提案: …`）で、Stop の判定が読むため | 標準出力 |
| 自動の確認が続けてよいのは、全体が ok のときと、ng が無く「api が unverified、access が skipped、git が ok」のときだけ（後者は注意を添える）。事前確認は保証ではなく、通ったあとの取り込みや push の関門で止まることはある | API を使わない環境（トークンなし）でも、git が通れば今までどおり進めるため | api が unverified なら止める |

## 得たもの・失ったもの

issue なしの親チケットは作れなくなる。入口の 1 回に、API 2〜3 回と ls-remote が増える。
