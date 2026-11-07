---
type: guide
title: 作業の決まりに条件を足す（実行ファイル側）
description: SessionStart で渡す作業の決まり（.ccnavi/CCNAVI.md）に、実行ファイル側で新しい条件の名前を足す手順
tags: [development, ccnavi-md]
keywords: [ccnavimd, CCNAVI.md, 条件, CONDITIONS, Context, flags_for, ccnavi:if, ccnavimd.sh, ccnavi_cond]
---

# 作業の決まりに条件を足す（実行ファイル側）

`SessionStart` で渡す作業の決まり（`.ccnavi/CCNAVI.md`）の書き方と、配布先で条件を足す方法は[`.ccnavi/docs/ccnavi-md.md`](../../.ccnavi/docs/ccnavi-md.md)にある。
条件を足す経路は 2 つある。

- 実行ファイル側: `go/internal/hook/ccnavimd/ccnavimd.go` の `conditions` に関数を登録する。ここに手順を書く
- 配布先側: `.ccnavi/scripts/ccnavimd.sh` に関数 `ccnavi_cond_<名前>` を書くだけで、実行ファイルのコードは変えない。配布先が作るファイルで、このリポジトリには無い

## 実行ファイル側の手順

1. `go/internal/hook/ccnavimd/ccnavimd.go` の `conditions` に、名前と `func(situation) bool` の関数を 1 組足す。名前は md に書くもの。英数字と `_` だけ（先頭は数字でない。`-` は `--lint` が error にする）。組み込みと同じ名前を `ccnavimd.sh` の関数に使わない。判定に要る状況が `situation` に無ければ、フィールドを足して、`situation` を組むところで値を入れる
2. `.ccnavi/CCNAVI.md` で `<!-- ccnavi:if 名前 -->` … `<!-- ccnavi:endif -->` で行を囲む
3. `.ccnavi/docs/ccnavi-md.md` の条件の表に 1 行足す。判定の関数のテストを `go/internal/hook/ccnavimd/ccnavimd_test.go` に足す

条件の判定のしかた（判定できない名前は真にして出す、など）は `.ccnavi/docs/ccnavi-md.md` の「条件を足す（配布先）」と「差し込みの名前」にある。
