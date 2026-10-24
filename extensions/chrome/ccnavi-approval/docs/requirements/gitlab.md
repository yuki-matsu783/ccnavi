---
title: ccnavi 承認ボードのGitLab
type: requirements
description: GitLab での読み書きと、書き込みがぶつかったときのふるまい
tags: [design-doc, extension, approval]
keywords: [要件, GitLab, REST, last_commit_id, revert, 要確認]
---

# GitLab

入口: [ccnavi 承認ボードの要件](../requirements.md)

- GitLab: 同じ操作を REST（v4）で読み書きする（`src/core/gitlab.ts`）。Commits API には「先頭がこの sha のときだけ」の指定が
  無いので、書く直前に先頭を読み、書き換える・消すファイルに `last_commit_id` を付け（同じファイルを他人が変えていれば GitLab が断る）、
  書いた後にコミットの親が読んだ先頭かを確かめる。違えば直前の状態で同じ時刻で判定し直し、書くものが同じなら残し、
  違えば元に戻すコミット（revert。各ファイルに自分のコミットを `last_commit_id` で付ける）を積んで読み直す。元に戻すコミットも別の書き込みとぶつかって 2 回までに積めない・途中で
  ホストが動かなくなったときはユーザの対応に切り替え、ボードに親子のチケットを「確認が必要」で表示する（ユーザが確認ダイアログを経て「確認済みとして警告を解除」を押すまで。このブラウザの
  `chrome.storage.local` にだけ記録し、ほかの承認者には見えない。確認が必要な親子のチケットにはこのブラウザから書かない）。PAT の期限は `GET /personal_access_tokens/self` を 1 日 1 回読む
- GitLab で残った指摘の行き先を確定するとき、スレッドへの返信と解決は、差分への指摘（解決できる discussion）だけに行う。
  差分以外の discussion と、解決できない投稿は「Chrome では扱えない。VS Code で」と出し、何も書かない。PAT は `api` スコープ、ロールは Developer 以上が要る
- 本文が機構の目印で始まる投稿を機構自身のものとして未解決から外すのは、書き手が全部同じ投稿のスレッドだけ（他人が目印で始まる返信を足したスレッドは外さない）
