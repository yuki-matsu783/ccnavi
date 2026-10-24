---
name: ccnavi-install
description: >-
  ccnavi をこの機械・このプロジェクトに入れる、更新する。「ccnavi を入れて」「セットアップして」
  「更新して」「/ccnavi-install」と言われたときに使う。機械の状況（OS・git・jq・curl・GitLab の認証・
  ccnavi が既に入っているか）を調べ、足りないものは入れ方を OS と状況に合わせて案内してから入れる。
  glab が無い人・入れられない人も前提にする。ルール・フェーズ・配点の中身を書く・直すのは
  ccnavi-config で、ここでは扱わない。
type: skill
---

# ccnavi-install

ccnavi を入れる作業を、状況の調査 → 足りないものの案内 → 入れる → lint の順で進める。入れ方は 2 つ。

- ビルド済み（既定）: 社内の共有フォルダの zip を受け取り、**ユーザが自分で展開**し、そのフォルダから入れる
- ビルドから: ccnavi のソースで実行ファイルを組み立て、そのソースのフォルダから、ビルド済みと同じ手順で入れる

## スクリプトの場所

スクリプトは**配布元**（展開した zip のフォルダ。ビルドからなら ccnavi のソース）の `scripts/` にある。
このスキルを置いた場所からは決まらない。以下 `<配布元>` はそのフォルダ、`<ws>` は入れる先のワークスペースルート。

| スクリプト | 役目 |
|---|---|
| `<配布元>/scripts/ccnavi-doctor.sh` | 状況を調べる。何も書き換えない。通信するのは `--online` のときだけ |
| `<配布元>/scripts/ccnavi-install.sh` | 入口。doctor を打ち、setup を呼び、lint で確かめる |
| `<配布元>/scripts/ccnavi-package.sh` | 配る側が zip を作る（入れる人は使わない） |

打ち方と終了コードは各スクリプトの `--help`。

## 手順

0. **配布元を用意する**: 展開したフォルダの場所をユーザに聞く。zip しか無ければ [references/start.md](references/start.md) に従い、
   受け取り元とチェックサムを確かめて展開してもらい、フォルダの場所を返してもらう。ビルドからなら、ソースの場所を聞く。
1. **状況を確かめる**: 入れる先（`<ws>`）とパターン（ビルド済み／ビルドから、新規／更新）を、分からないものだけ 1 つずつ聞く。
   「更新」なら [references/update.md](references/update.md) へ。
2. **調べる**: `sh <配布元>/scripts/ccnavi-doctor.sh --workspace <ws> --from <配布元> [--mode build]`。
   出力の `check: <項目> <段階> <コード> <詳細>` の `<コード>` で、下の表から案内を選ぶ。
3. **直してもらう**: `ng` は必ず、`todo` は入れる前か、遅くとも使い始める前に直す。**入れるのはユーザ**。
   PATH が変わる操作のあとは、Claude Code を終了して新しいターミナルから起動し直してもらう（Bash ツールの環境は起動時のまま変わらない）。
   認証まで済んだら `--online` を付けて打ち直し、`remote` と `host-api` を確かめる。
4. **ビルドから**のとき: `sh <ソース>/scripts/ccnavi-install.sh <ws> --build --check` で副作用を見せて了承を得てから `--build` を打ち、出力の `次:` に従う（手順 5 へ）。
5. **何が変わるかを見せる**: `sh <配布元>/scripts/ccnavi-install.sh <ws> --from <配布元> --check` の一覧を見せて、了承を得る。
6. **入れる**: `--check` を外して打つ。終了コード 5（lint に error）なら ccnavi-config へ回す。
7. **終わったら**: install の最後の案内に従う。README の「導入」の進め方（dry-run で記録を読む → ルールを直す → enable へ切り替える）を伝える。

出力の `次:` は 1 行。そこに書かれたことをそのまま次の一手にする。

## check 行から読む reference

| check 行 | 読む reference / すること |
|---|---|
| `git`・`jq`・`curl`・`uv`・`node` | [references/tools.md](references/tools.md) |
| `glab` が todo | [references/glab.md](references/glab.md)。ただし、代わりの道（[references/auth.md](references/auth.md)）を選んでもよいとユーザに伝える |
| `glab` が warn の `optional:` | 入れなくてよいと伝える |
| `remote`・`host-api`・`source-remote` が todo、`origin` が warn ssh | [references/auth.md](references/auth.md) |
| `origin` が warn github | 対象外と伝え、続けるかをユーザに聞く |
| `package` が ng | `wrong-target` なら、この機械の `<os>-<arch>` 向けの zip を頼むか `--build` にするかを聞く。`missing`・`no-executable` なら、zip を受け取り直してもらう |
| `package` が warn quarantined | `--clear-quarantine` の意味（ダウンロードしたものを信頼して隔離の印を外す）を伝え、ユーザが決める |
| `existing` が warn enable | 自分では install を打たない。コマンドを示し、Claude Code の外のターミナルでユーザに打ってもらう |
| `existing` が warn other | 保存された `CCNAVI_MODE` の値が読めない。ccnavi は読めない値を enable として扱うので、`warn enable` と同じに扱う |
| `existing` が warn unchecked / invalid | jq を入れるか JSON を直すまで、install に進まない |

## 絶対ルール

- トークン・パスワードを会話に出させない、書かせない。`glab auth login` は別のターミナルで打ってもらう
- 道具は勝手に入れない。入れ方を示し、ユーザに打ってもらう
- `ng` を残したまま先へ進まない。直せなければユーザに報告して止まる
- `--mode enable`・`--force`・`--no-fetch`・`--no-idle`・`--clear-quarantine` は、ユーザの了承なしに付けない。`--force` は [references/update.md](references/update.md) の手順を通してからにする
- 配布元の信頼（受け取り元・チェックサム）を確かめずに進めない
- TodoWrite と Agent ツールを使わない
