---
type: adr
title: GitHub の GraphQL が 403 の環境では、GraphQL を先に試し、使えないと分かったときだけ REST に替える
description: Claude Code のクラウドは GraphQL を 403 で断る。スレッドの取得・Draft 外し・指摘への返信と解決・MR が閉じる issue の 4 か所を、GraphQL が使えない 403 のときだけ REST（ccr の道）で代える。通る環境の動きは変えない。案内から、ユーザに JSON を作らせる手順を外す
tags: [review, hosts, github, sh]
keywords: [GraphQL, 403, REST, ccr, review_threads, ready_for_review, resolve, replies, closingIssuesReferences, comment_ids, スレッドの id, api_failed, gql, Claude Code on the web]
---
# ADR-0173: GitHub の GraphQL が 403 の環境では、GraphQL を先に試し、使えないと分かったときだけ REST に替える

状態: 採用（`/ccr/` の道の応答の形は、Claude Code のクラウドでの実機の呼び出し（`review_threads`・`comments`）と issue の検証で確かめた範囲。テストはホストの代役で書いた。GHE.com は未検証）

## 状況

Claude Code のクラウド環境では、GitHub の GraphQL が 403 で拒否され（`GitHub GraphQL is not available from Claude Code sessions`）、REST だけが通る。ccnavi は GitHub の次の 4 か所を GraphQL で行っていた。

- レビューのスレッドの取得と解決状態（`threads`。`fetch`・`confirm`）
- Draft 外し（`undraft`。`ready`）
- 受け入れた指摘への返信と解決（`accept_reply`・`accept_resolve`。`decide`）
- MR が閉じる issue（`closingIssuesReferences`。`ccnavi-start.sh --mr`）

そのため `confirm` と `ready` が通らず、チケット作業が最後まで進まなかった。案内は「スレッドとレビューの JSON を作り、ユーザが `ccnavi review confirm --result` を打つ」だったが、この手順は write-and-push の検査で止まる（`--record-tree` が付かないため）。どの経路でも通らなかった。

同じクラウドの REST には `pulls/<N>/ccr/review_threads`・`ccr/ready_for_review`・`ccr/comments/<id>/resolve` と、通常の REST の `pulls/<N>/comments/<id>/replies` がある。`ccr` の道は通常の GitHub では 404 になる。

## 決定

| 決めたこと | なぜ |
|---|---|
| 4 か所とも、GraphQL を先に試し、使えない 403 のときだけ REST（`ccr` の道）に替える（GitHub だけ。GitLab は変えない） | REST 専用に切り替えると、`ccr` の無い通常の GitHub が動かなくなる。GraphQL が通る環境では、前と同じ動きを保つ |
| 「使えない 403」は、gh なら本文の `GraphQL is not available`、curl なら番号だけの 403（`-f` が本文を捨てるので認証の失敗と見分けが付かない）。401 と、本文が別の理由の 403 は替えない | 認証の失敗や権限不足を REST に流すと、原因が見えなくなる。curl の 403 を替えても、`ccr` が 404 で失敗するだけなので、失敗は元の文面で言う |
| REST の `threads` は、`ccr/review_threads`（`resolved`・`path`・`line`・`comment_ids`）と `pulls/<N>/comments`（全ページ）から、従来と同じ形 `{id, resolved, url, path, line, body, created_at, last_id, last_at, last_author}` を組む。スレッドの id は先頭コメントの id（数字の文字列）。url・本文・作成の時刻は先頭コメントから、`last_*` は `comment_ids` の最後のコメントから取る | 実行ファイルに渡す形を変えない（Python 本体は変えない）。REST のスレッドは id・本文・作成者を持たない |
| `accept_reply`・`accept_resolve` は、スレッドの id の形で道を選ぶ。数字だけなら REST（`replies`・`ccr/.../resolve`）、そうでなければ GraphQL の node id | 計画の id は取り直した結果にあるものだけを通すので、取った道と書く道が同じになる。1 回の実行の中で道が混ざらず、実行をまたいだ計画（`--resume`）でも道が決まる |
| REST の返信は、スレッドの先頭コメントへの返信にする | GraphQL の `addPullRequestReviewThreadReply` と同じく、スレッドに返信が付く |
| `undraft` は、題の `Draft:` 除去の PATCH はそのまま。`markPullRequestReadyForReview` が使えない 403 のときだけ `ccr/ready_for_review` を打ち、`GET pulls/<N>` の `draft` を返す | 戻り値（`true` / `false`）を GraphQL と同じにし、呼び手の判定を変えない |
| `closingIssuesReferences` が使えない 403 のときは、MR の本文の `close(s/d)`・`fix(es/ed)`・`resolve(s/d)` に続く `#n`・`owner/repo#n` を読む（大文字小文字不問）。コードブロック・インラインコード・HTML コメント・引用行は読まない。`not close #n` と、語の途中の `fix`（`bug-fix #n`）も拾わない。MCP で確かめた値を渡す回避路（`--mcp-checked`）は残す | GitHub のリンクのうち、本文のキーワードで付いたものだけを読む形に縮める。コミットメッセージや手で付けたリンクは拾わない |
| `api_failed` の GraphQL の枝は外し、GraphQL の失敗は `gql` が分ける。REST の代わりの道も失敗したときの案内（`rest_fallback_failed`）は、ユーザに接続（認証と権限）の確認を頼んで止まる。JSON を作らせる手順は書かない。gh も curl も無いときの案内からも、同じ手順を外す | 拒否される手順を案内しない |

## 理由

GraphQL を先に試す順は、`ccr` が無い環境を壊さない点で、ホストの種類（`api.github.com` か GHE か）で REST を先に試す案より安全。GraphQL が通る環境では、1 回の余分な呼び出しも増えない。

## 得たもの・失ったもの

- 得たもの: クラウド環境では、`confirm`・`ready`・`decide`・`--mr` が REST だけで通る。案内は、拒否される手順を含まない
- 失ったもの: GraphQL が 403 の環境では、REST の `ccr/review_threads` でページ送りをせず、1 回で読む。curl の経路では、認証の失敗の 403 でも 1 回 `ccr` を試す（失敗は元の文面で言う）。REST の `threads` には、GraphQL の node id が無い（id が数字になる）。本文のキーワード以外で付いたリンクは読めない
- 実機で確かめていない: GHE.com の `ccr` の道、`ccr/review_threads` が 100 件を超える MR

## 採らなかった案

- REST 専用に切り替える。`ccr` が 404 になる通常の GitHub が壊れる
- ホストの種類（`api.github.com` か GHE か）で先に試す側を決める。クラウドかどうかはホスト名では決まらない
- `ccnavi-review.sh confirm --result <json>` を sh の入口で受ける案。GraphQL を避けられれば不要で、JSON を作る手が増える
- 案内を「この環境では confirm / ready は使えない」に絞る案。REST で通るので、使えない前提にはしない
