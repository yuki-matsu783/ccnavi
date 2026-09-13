---
version: 1
ticket: sh-ws-root-02
parent: sh-ws-root
phase: 2
predecessors:
- sh-ws-root-01
human_review:
  required: true
  reason: モード B の壊れ方を何で捕まえるかを決めるため。ここが緩いと、以後の実装が通ったことの意味が薄くなる
title: 受入テスト — モード B で sh が動くことと、モード A が退行しないこと
rationale: '設計 7.2 の 16 件を実際に走る形にする。隔離した一時ディレクトリに本物の

  ワークスペース（git init、projects/p1 と p2、プロジェクトから切った作業ツリー、

  ワークスペースから切った作業ツリー）を組み立て、組み立てた実行ファイルと sh を叩く。

  重いので CCNAVI_E2E=1 のときだけ走らせ、既定では skip する。

  加えて、モード A（projects/ が無い）で置き場と文面が §25 の前と同じであることを確かめる。

  '
allow:
- match: Write|Edit
  glob: tests/*
ccnavi_approved:
  approved_at: 2026-09-12T22:46:48+0900
  source_tree: sh-ws-root
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\sh-ws-root\wip\tickets\todo\sh-ws-root-02.md
started_at: 2026-09-12T22:48:11+0900
base_sha: ec91b5f591929d090c6ffcac7347f90e2ad50887
completed_at: 2026-09-12T23:03:41+0900
---

# 受入テスト — モード B で sh が動くことと、モード A が退行しないこと

設計 `wip/design/sh-ws-root.md` の 7 節を実装する。**実装より先に書く。** 今の sh で走らせて
落ちること（赤）を確かめてから、フェーズ 3 で通す。

## 成果物

`tests/test_e2e_sh.py` 1 枚。`unittest` の TestCase。`CCNAVI_E2E` が無ければ
`skipUnless` で飛ばす。

## 組み立てるもの

```
ws/                         git init、1 コミット、.claude/scripts/ に 4 本、dist/ に実行ファイル
ws/projects/p1              git init、1 コミット
ws/projects/p2              git init、1 コミット
ws/.claude/worktrees/wp1    p1 から切る
ws/.claude/worktrees/w0     ws から切る
```

このリポジトリとは別に組み立てる。設計 2 節の決定（上へ歩いて最初に当たった
`.claude/scripts/` を根とする）により、このリポジトリの作業ツリーの中から打つと
作業ツリーが根になるため、そこで検証すると何を測っているか分からなくなる。

## 確かめること（設計 7.2 の 16 件）

記録の置き場が 4 件（プロジェクトの中、プロジェクトから切った作業ツリー、ワークスペースから
切った作業ツリー、ワークスペース自身）。実行ファイルの発見が 1 件。プロジェクトに `.claude/` を
作らないことが 1 件。push ガードの発火が 1 件。`worktree add` の行き先検査が 3 件
（相対で外に出る／正しい綴り／知らないオプション）。資格情報の伏せ字が 2 件。
`CCNAVI_WORKSPACE` の上書きと、ワークスペースの外での失敗が 2 件。`test-py.sh` の素通りが 1 件。
ブランチ名のスラッシュが 1 件。

資格情報の 2 件は、**元のトークンの部分文字列が出力の全文に含まれないこと**で判定する。
伏せた結果を目視の綴りで比べると、「消えているつもりで残っている」形を取り逃す。

## モード A の非退行

`projects/` を作らないワークスペースで、記録の置き場と文面が §25 の前と同じであることを
確かめる。`projects/` を作って空にした場合も同じ。

## 判断が要るかもしれない点

1. **重さ。** git init と worktree add を 5 回ずつ行うので、1 回の実行が数十秒になる見込み。
   既定で skip するので毎ターンの負担は無いが、回し忘れると腐る。`HANDOVER.md` に
   「モード B を触ったら回す」と書く（フェーズ 4）
2. **実行ファイルの用意。** 組み立て済みの `dist/ccnavi/ccnavi` を使う。無ければ skip して
   理由を出す。テストの中でビルドはしない（数十秒かかるため）
3. **sh の写し。** フェーズ 3 の成果物は `wip/design/scripts/` に置かれ、人が写すまで
   `.claude/scripts/` には入らない。テストは**ワークスペースの `.claude/scripts/` から写す**ので、
   人が写す前に回すと今の sh を測ることになる。テストの冒頭でこれを明示し、
   `CCNAVI_SH_DIR` で写し元を差し替えられるようにする。写す前でも
   `wip/design/scripts/` を指せば新しい sh を測れる

## この子で決めないこと

- 実装（フェーズ 3）
- `wip/design/scripts/` に置く sh の中身
