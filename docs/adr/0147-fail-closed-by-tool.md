---
type: adr
title: 判定に達せなかったときは、ツールの種類で決める
description: 内部例外・期限切れ・payload が読めない回の結論を、ツールごとに決める。読み取りは最低限の deny を適用して通し、書き込みは止める
tags: [hook, rules, selfguard]
keywords: [fail-closed, 内部エラー, 期限切れ, payload, 許可リスト, Read, Bash, dry-run, failsafe]
---

# ADR-0147: 判定に達せなかったときは、ツールの種類で決める

状態: 採用

## 状況

判定に達せなかった回（想定外の例外、内部の期限切れ、payload が読めない）は、ツールの種類に関わらず PreToolUse を止め、
dry-run では通していた。書き込みやサブエージェントの起動まで dry-run で通る一方、enable では読み取り（`ls`、`Read`）まで
止まり、ccnavi の不具合で調べものまで止まっていた。

## 決定

ツールの種類で結論を決める（`modes.fail_closed`、`hook.failsafe`）。設定は読まない。

| 状況・ツール | 結論 |
|---|---|
| payload が読めない（ツールが分からない） | 止める（dry-run でも） |
| Write・Edit・NotebookEdit・Agent | 止める（dry-run でも） |
| Read・Grep・Glob | 最低限の deny に該当しなければ通す。評価できなければ止める。最低限の deny は、通常の判定の `credentials` が止める認証情報の形（`.env`・`.ssh`・`.aws`・`*.pem`・`credentials.json`・`secrets/` など）と、通常の判定にはない独自の追加として `.git` の中と `logs/state/ops/` |
| Bash | 狭い許可リストだけ通す。読み切れて、連結・置換・リダイレクト・glob を含まない単独の `pwd`・`ls`・`cat` / `head` / `tail` / `wc` の 1 パス（オプションは取らず、パスは `-` で始めず、`/dev/`・`/proc/` は除く）と、このワークスペースの `ccnavi-ticket.sh` の `status` / `--help`（別の場所に置かれた同名のスクリプトは通さない） |
| PowerShell | 止める（読めない） |
| 未知のツール | enable は止め、dry-run は通す |

許可リストは既存の読み取りの表（`phase_forms._READERS`）を流用しない。あれは各コマンドの先頭の語しか見ず、`rg --pre` や
`less` のようにコマンドを走らせられるものを数えている。ここは「判定できない間に通してよいもの」なので、独立に持つ。

## 得たもの・失ったもの

得たもの: 判定できない間も調べものは進み、書き込みは止まる。失ったもの: 許可リスト外の読み取りは、ccnavi が直るまで
止まる。Claude Code 側の強制終了・hook が起動しない・外部のタイムアウトは、ここでは止められない。

## 採らなかった案

- すべて止める（今の enable）: 不具合が起きると、調べものまで止まる。
- 読み取りの表（`_READERS`）を流用する: 走らせられる道具が混ざる。
