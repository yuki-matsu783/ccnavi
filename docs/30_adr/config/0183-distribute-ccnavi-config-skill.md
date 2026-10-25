---
type: adr
title: ccnavi-config スキルは配布先にも配る。配る対象は process とは別の目印 distribute で宣言する
description: 配布先の文書が /ccnavi-config を案内するのに、スキルが配られない食い違いを、スキルを配る側に倒して直す。配布の目印を process: true と別に持ち、ユーザの発言ごとの同期（skillsync）には載せない
tags: [setup, skills, distribution]
keywords: [ccnavi-config, distribute, process, 配布, 工程スキル, skillsync, references/common, 配布先, ccnavi-setup]
---
# ADR-0183: ccnavi-config スキルは配布先にも配る。配る対象は process とは別の目印 distribute で宣言する

状態: 採用

## 状況

導入スクリプト（`ccnavi-setup.sh`）が配布先へ配るスキルは、SKILL.md の frontmatter に `process: true` を持つ工程スキルだけ。
一方、配布先に置かれる使い方の文書（`.ccnavi/docs/grow*.md`）は、ルール・フェーズ・リスクの下書きを `/ccnavi-config` で作ると案内し、
`.claude/skills/ccnavi-config/references/` の書式を指す。配布先に無いスキルを案内している。
配布先の Claude Code は「`/ccnavi-config` is not installed」と答え、`--lint` を直接打って辿り着くことになる。

ADR-0111 は既存スキル（`ccnavi-config` を含む）を、概念スキルへの寄せの対象外にした。配るかどうかは決めていない。
`ccnavi-config` の SKILL.md は、配布先の `CCNAVI_ENTRY_PATH` が指す launcher で ccnavi を打つ前提で書かれており、
期待する判定（`rule-expectations.yml`）も `/ccnavi-config` が回す前提を持つ。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| `ccnavi-config` を配布先にも配る | 文書・スキル本文・期待する判定の三つが「配布先で使う」前提で書かれている。スキルを配る一か所を直すほうが、三つを書き換えるより小さい | 配らず、文書から `/ccnavi-config` を外して `--lint` などを直接案内する。下書きの作り方（足したら必ず確かめる、期待する判定を一緒に足す）の手順が文書へ散る |
| 配る対象は、SKILL.md の frontmatter の `distribute: true` で宣言する。`process: true` を持つ工程スキルも、これまでどおり配る | `process: true` は「工程スキル」という意味を持ち、プロジェクトの `skills/` へのコピーと目録、`process-` の名前の決まりに結びついている。配るだけの宣言をそこへ混ぜると、`ccnavi-config` が工程スキルになる | `ccnavi-config` に `process: true` を付ける。プロジェクトの `skills/` にコピーされ、工程スキルの表と名前の決まりに合わなくなる |
| `distribute: true` のスキルは、ユーザの発言ごとの同期（skillsync）の対象にしない | 設定 3 本を直すのはワークスペースの作業で、プロジェクトだけを clone した人に入口を渡す必要が無い。同期すると、プロジェクトの `skills/` にコピーが生える。書き込み保護の例外（工程スキルの `common/` だけを通す）も広げずに済む | 同期の対象にする。コピーが増え、例外を広げる |
| 配るのは SKILL.md と `references/common/` の下の `.md`。`ccnavi-config` の `add.md`・`check.md` は `references/common/` へ動かす | 配る範囲の決まりを工程スキルと同じに保つ。`references/` 直下にファイルを置かない決まりにも合う | `references/` 全体を配る。工程スキルと範囲が食い違う |
| 配布先で動かない記述（配布元の `scripts/check_rules.py`、`uv run python -m ccnavi`、配布先に無い `docs/` や `src/` への参照）をスキルと文書から外す | 配布先で辿れない案内は、スキルがあっても迷わせる | そのまま配る |
| 配布先の文書が指す `/<スキル名>` と `.claude/skills/...` が、配布物の中に実在することをテストで見る | 同じ食い違いが再発したとき、配布のテストで気づく | 目視 |

## 得たもの・失ったもの

- 得たもの: 配布先のエージェントが、文書の案内どおりに `/ccnavi-config` で設定 3 本を育てられる
- 得たもの: 配る目印が、同期の対象（工程スキル）と別になり、後から配るだけのスキルを足せる
- 失ったもの: 配布先に置かれるスキルが 1 本増え、常駐する説明文（`name` と `description`）が増える
- 失ったもの: 配るスキルの宣言が `process: true` と `distribute: true` の二通りになる。`ccnavi-setup.sh` の判定が二つのキーを読む
- 失ったもの: 配布先で直せない記述を避ける制約が `ccnavi-config` に付く（配布元の docs や src を指せない）

## 採らなかった案

- **配らない側に倒す。** 上の表のとおり、三つの前提を書き換える。ADR-0111 の対象外の趣旨（用途が固定のスキルは移さない）にも、配るほうが合う
- **配る対象を `.claude/skills/` の全スキルにする。** `commit` や `yomiyasu` は配布元固有の手順や外部の取り込みで、配ると配布先の文脈を汚す
