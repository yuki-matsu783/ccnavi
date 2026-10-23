---
type: skill-reference
title: 更新（--force）の手順
description: ccnavi を入れ直す・更新するときの手順。入れ替わるファイルを見せる、バックアップ、実行ファイルと拡張機能をそろえる
tags: [ccnavi-install, skills]
keywords: [更新, force, バックアップ, 入れ替え, 配り直し, quarantine, 拡張機能, enable]
---

# 更新（--force）の手順

新しい zip（または新しくビルドしたソース）を配布元にして、入れる先に配り直す。`--force` は利用者の手直しを消すので、
次の順で進める。

1. **入れる先が enable なら**、Claude Code の外の端末で打ってもらう。install は、enable として使われている入れる先には Claude Code の中から入れない
   （doctor の `existing` が `warn enable`）。コマンドを示して、ユーザに打ってもらう
2. **`--check` で一覧を見せる**: `install <ws> --from <配布元> --check`。setup が「配布先に既にあるので配っていないもの」として並べたものは、
   `--force` が無ければ**古いまま残る**。何が残るかをユーザに伝える
3. **`--force` を付ける前に了承を得る**: `--force` を付けると、共通レイヤーの rules.yml・CCNAVI.md・docs・sh・スキルが配布元の内容に戻り、
   **利用者の手直しが消える**。消えるものを見せて、了承を得る。install は `--force` のとき、setup の前に入れる先の外へバックアップを取る。
   置き場は `${XDG_CACHE_HOME:-$HOME/.cache}/ccnavi/backup/<ワークスペースの短いハッシュ>-<日時>-<PID>/`（権限 700）。
   その場所と戻し方（`cp -R <バックアップ>/<パス> <ws>/<パス>`。ディレクトリは `<パス>/.` から `<ws>/<パス>/` へ）を、install の出力のとおりにユーザへ伝える
4. **そろえて更新する**: 実行ファイル・sh・VS Code 拡張・Chrome 拡張は、互換のある版どうしで組になっている。実行ファイルと sh だけ新しくして
   拡張が古いまま（またはその逆）にしない。配布元に vsix や Chrome 拡張があれば、install の最後の案内に従って入れてもらう
5. 打ち終わったら、Claude Code を終了して起動し直してもらう

手直しした自分のルール（`.ccnavi/config/project/` の中身）は配布の対象ではなく、`--force` でも消えない。
