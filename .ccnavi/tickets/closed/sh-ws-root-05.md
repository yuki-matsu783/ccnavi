---
version: 1
ticket: sh-ws-root-05
parent: sh-ws-root
phase: 5
predecessors:
- sh-ws-root-04
human_review:
  required: true
  reason: 受入テストが古い sh を測っていた。測る対象の決め方は、テストが何を保証しているかに直結する
title: 受入テストが作業ツリーの古い sh を測る欠陥を直す
rationale: '写したあとに受入テストを回すと 16 件赤になった。写しの失敗ではなく、

  テストの欠陥。SH_DIR を「自分が居るツリーの .claude/scripts/」から決めているため、

  作業ツリーから回すと、そこに checkout されている古い版を測る。

  実際に効くのはワークスペース側の 1 本だけ。実行ファイルの探し方（find_dist）は

  上へ歩く形にしてあるのに、sh の探し方だけ直していなかった。

  COPY.md と HANDOVER.md の「ワークスペースルートで回す」も誤り。tests/ の新しい

  ファイルはブランチにしか無いので、そこからは import できない。

  '
allow:
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: HANDOVER.md
ccnavi_approved:
  approved_at: 2026-09-13T04:58:31+0900
  source_tree: sh-ws-root
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\sh-ws-root\wip\tickets\todo\sh-ws-root-05.md
started_at: 2026-09-13T04:59:35+0900
base_sha: eb98f9e2777f2863b3b7e12689403b75eabea176
completed_at: 2026-09-13T05:16:43+0900
---

# 受入テストが作業ツリーの古い sh を測る欠陥を直す

## 何が起きたか

利用者が `wip/design/scripts/` の 7 本を配布先へ写したあと、作業ツリーから

```
CCNAVI_E2E=1 uv run python -m unittest tests.test_e2e_sh -v
```

を回すと 16 件赤。写す前と同じ数。確かめた結果、

- `SH_DIR` = `<作業ツリー>/.claude/scripts`（`ROOT` が作業ツリーなので）
- 作業ツリーの `ccnavi-git.sh` に `ccnavi_workspace` は 0 件（古い版）
- ワークスペースの `ccnavi-git.sh` には 1 件（写された新しい版）

**写しは成功していた。テストが見る先が違っていた。**

これは `HANDOVER.md` に書いた非対称そのもの。`.claude/scripts/` は git が運ぶので
作業ツリーにも写しがあるが、実際に効くのはワークスペース側の 1 本だけ。
テスト自身がその罠に落ちていた。

## どう直すか

1. **`SH_DIR` を上へ歩いて探す。** `find_dist` と同じ形にし、`.claude/worktrees/` の
   下は候補から外す。実装（`ccnavi_workspace`）と同じ規則にする。
   `CCNAVI_SH_DIR` の明示があればそれを優先するのは今までどおり
2. **どこを測ったかを出す。** 走り出しに `SH_DIR` と `DIST` を出す。今回のように
   「緑と赤が写しの成否と対応しない」ことが起きたとき、最初に見るべき情報が
   出力に無かった
3. **`COPY.md` の確かめ方を直す。** 「ワークスペースルートで回す」は誤り。
   `tests/test_e2e_sh.py` はブランチにしか無いので import できない。
   作業ツリーから回す綴りに直し、1 で SH_DIR がワークスペース側を向くことを書く
4. **`HANDOVER.md` の同じ記述も直す**

## 得るもの

- 写したあとの確認が、実際に効いている sh を測る
- 「どこを測ったか」が出力に残るので、次に食い違ったとき 1 回で分かる
- テストの探し方と実装の探し方が同じ規則になる

## 失うもの

- `CCNAVI_SH_DIR` を付けずに回したとき、測る対象が「cwd から上へ歩いた先」になる。
  作業ツリーの中の版を意図的に測りたい人は、明示する必要がある。そういう使い方は
  無い（作業ツリーの版は誰も読まない）ので、失うものとしては小さい

## やらない場合どうなるか

写したあとの確認が常に赤になり、写した人が「写しが失敗した」と誤解する。
今回それが実際に起きた。

## 確かめ方

- 作業ツリーから `CCNAVI_E2E=1 uv run python -m unittest tests.test_e2e_sh` を回して
  20 件緑（ワークスペースには写し済みの新しい sh がある）
- `CCNAVI_SH_DIR=wip/design/scripts` を付けても 20 件緑
- 出力の頭に、測った `SH_DIR` と `DIST` が出る
- 既存の in-process テストが緑のまま
