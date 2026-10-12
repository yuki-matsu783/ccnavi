---
type: guide
title: ワークツリーの片付け
description: ccnaviがワークツリーを片付けるタイミングと、自分で片付けるときの手順（ccnavi-clean.sh）、自動モードの分類器に止められたとき
tags: [git, worktree]
keywords: [片付け, ワークツリー, worktree remove, ccnavi-clean.sh, 分類器, 自動モード, ready, finish, cancel, node_modules]
---

# ワークツリーの片付け

## ccnaviが片付けるもの

- ワークツリーはccnaviが片付ける。マージを待たない
  - 方針は、閉じた子のワークツリーは完了でも取り消しでも消すこと。閉じた子の続きを進めるときは、新しいチケットで
    新しいワークツリーを切る。残るのは、作業中（`doing/`）の子と、レビュー待ち（`review/`）の子（指摘を直す場所）の
    ワークツリー。`decide`・`chat`・Chromeのレビュー済みで`done/`へ動いた子のワークツリーは、その場では消えず、
    次にその親子で`finish`か`cancel`が打たれるまで残る
  - 子のワークツリーは、`confirm`が子を`done/`へ動かしたあと（そのフェーズの子）と、`finish`・`cancel`のあと
    （親でも子でも、その親子の閉じた子を全部。親の`finish`では取りこぼしを拾う安全網）に消える。
    レビュー待ち（`review/`）の子のものは、指摘を直す場所として残す
  - 親のワークツリーは、`ready`がDraftを外したあとの最後に消す。Draftを外す前と、外せなかったときは消さない
  - 消し方は生成物の掃除（`ccnavi-clean.sh`と同じもの）のあと`worktree remove`（`--force`なし）で、ブランチは消さない
  - cwdが消す対象の中にあると消さず、「cwdが中にあるので消さなかった」と、外に出てから打つ1本
    （`sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-clean.sh --worktree <名前>`）が出る。`ready`を親のワークツリーの中から
    `--parent`なしで打ったときはこの形になる。ExitWorktreeか`cd`でワークスペースルートに出てから、出た1本をそのまま打つ
  - 未コミットの変更があるワークツリーは、何も消さずに名指しされる。別のセッションの作業かもしれないので、ユーザに確かめる
  - gitが無視しているファイル（`.env`など）があるワークツリーも、何も消さずに名指しされる。`worktree remove`はそれごと消し、
    履歴から戻せないため。残したいならワークツリーの外へ退避し、要らないならユーザに確かめて消してから打ち直す。
    数えないのは、生成物（`ccnavi-clean.sh`が消すもの）だけ。下書きの置き場は`wip/handoff/`（追跡される。
    .ccnavi/docs/scratchpad.md）で、gitが無視するファイルではない。未コミットの下書きは「未コミットの変更」として名指しされる

## 自分で片付けるとき

- 自分で`worktree remove`を打つとき（`<統合先>`に直接取り込んだワークツリーなど）は、その前に`ccnavi-clean.sh <名前>`で
  node_modules・.venvなどの生成物を消す。Windowsで削除が途中で止まるのを防ぐためである。未コミットの変更があるワークツリーでは、
  `ccnavi-clean.sh`は何も消さずに止まる。`ccnavi-clean.sh --worktree <名前>`は掃除と`worktree remove`を1本で行う
- 片付けの1本（`ccnavi-clean.sh --worktree`）や`ccnavi-git.sh rm -r wip`がClaude Codeの自動モードの分類器に止められたら、迂回しない。
  ユーザに許可を頼む。続けて許すなら、ユーザがホームの`~/.claude/settings.json`の`permissions.allow`に、操作ごとに絞った許可
  （例 `Bash(sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-clean.sh --worktree *)`）を足す。分類器はプロジェクトの
  `.claude/settings.json`を読まないので、そこに足しても効かない。`ccnavi-git.sh *`のような広い許可はpushまで分類器を通さなくなるので勧めない
