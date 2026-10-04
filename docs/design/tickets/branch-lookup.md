---
type: design
title: 9.13 issue・MR に紐づくブランチ
description: issue や MR を指定された作業で、紐づくブランチを確かめる指示
tags: [design-doc, ticket, git]
keywords: [issue, MR, ブランチ, ccnavi-branches.sh, 着手]
---

[設計書の入口](../../design.md) > [9. チケット制御](../tickets.md)

### 9.13 issue・MR に紐づくブランチ

ユーザが issue や MR を指定して作業を頼んだとき、エージェントは着手の前に、紐づくブランチが既にあるかを確かめて
ユーザに聞く。ccnavi は**指示を足すだけで、止めない**。

**UserPromptSubmit。** チケット制御が有効なら、依頼文（payload の `prompt`）から issue・MR の指定を探す
（`branchfind.prompt_refs`。`#152`・`issue 152`・`/issues/152`、`!5`・`MR 5`・`PR #12`・`/pull/5`・`/-/merge_requests/5`）。
囲みのコードブロックの中、`C#`・`&#123;`・`##12`・`# 見出し`・`#fff`・0 で始まる番号・CSS の色の値・`すごい!5` は拾わない。
見つけたら、指定ごとに `'{root}/.ccnavi/scripts/ccnavi-branches.sh --issue N'`（`--mr N`）を打ち、候補があれば一覧をユーザに
見せて「既存のブランチで続ける（承認済みの `branch:` で使う。承認前の提案の `branch:` は使わない）・新しく
`<先頭の語>-<番号>-<slug>` を切る・やめる」を聞いて返事を待つ、候補が無ければ進めてよい、という文を `additionalContext` で
渡す（`branchfind.prompt_context`。sh のパスはワークスペースルートの絶対パス）。
dry-run でも渡す。判定は返さない。

**`ccnavi-branches.sh (--issue N | --mr N) [--json]`。** cwd のリポジトリ（ワークスペース・`projects/<名前>`・そのワークツリー）
について探す。読むだけ。

1. sh がホストを読む。繋ぎ方は `ccnavi-common.sh` の「ホスト（GitHub / GitLab）への接続」で、`ccnavi-review.sh` と同じ
   （gh / glab、無ければ curl と `GITHUB_TOKEN` / `GITLAB_TOKEN`）。MR 指定はその MR の元ブランチ、issue 指定はその issue を
   参照している開いた MR の元ブランチ（GitHub は開いた PR の題・本文・元ブランチ名、GitLab は `related_merge_requests`）。
   繋げなければ止めず、理由を書く
2. 結果を `logs/state/branches-host-<pid>.json` に書き、実行ファイルの `ccnavi branches <issue|mr> <N> --result <json>` に渡す
3. 実行ファイルが手元を読む（git の ref、ツリーの HEAD、チケット）。issue 指定なら、名前に番号を含むブランチ（手元と origin。
   番号の前後が数字でない）と、`issue: <N>` を持つ親の親のブランチ（承認済みは `branch:`、提案は識別子）を足す。どの候補にも、
   チェックアウトしているツリーと結び付くチケットを添えて、1 候補 1 行か JSON で出す

```text
ccnavi-branches: issue #152 に紐づくブランチ（ワークスペース: .）
ホスト: github.com acme/widgets を見た
候補 feature/152-login  在りか=まだ無い  由来=チケット  MR=-  ワークツリー=-  チケット=feature-152-login(doing)
候補 topic/a  在りか=ホストだけ  由来=MR  MR=!11(open)  ワークツリー=-  チケット=-
候補 feature-152-login  在りか=手元  由来=名前に番号  MR=-  ワークツリー=.claude/worktrees/feature-152-login  チケット=feature-152-login(doing)
チケット feature-152-login  承認済み  状態=doing  題=ログイン
候補 3 件。ユーザに見せ、既存のブランチで続けるか・新しく切るか・やめるかを聞いて返事を待つ
```

ホストを見ていなければ 2 行目が `ホストは見ていない（<理由>）。手元の候補だけを出す` になる。チケット制御が disable なら
チケットは見ず、そう書く。終了コードは 0（出した）・1（git の外・実行ファイルが落ちた）・2（引数の誤り）。

sh と実行ファイルの間の JSON（`--result`）:

| 鍵 | 中身 |
|---|---|
| `checked` | ホストを見たら `true`。`false` なら `reason` に理由（origin が無い・道具もトークンも無い・API が失敗した（どの呼び出しか）など）だけ |
| `host` / `repo` | ホスト名（ポートを含む）とプロジェクトのパス |
| `mrs` | `{number, branch, state, url, title, fork}` の配列。MR 指定なら 0 か 1 件。実行ファイルは番号が整数でない・元ブランチが空の要素を落とし、制御文字を空白に置き換える |

`--json` の形は `{kind, number, repo: {project, root}, host: {checked, reason, name, repo}, tickets_checked,
candidates: [{branch, local, origin, sources, mrs, worktrees, tickets: [{ticket, state, approved, title, issue}]}], issue_tickets}`。
`sources` は `mr`・`ticket`・`name` のどれか。
