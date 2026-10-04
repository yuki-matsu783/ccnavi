---
type: adr
title: 共通層の設定は .ccnavi/common/ に
description: 共通設定を .ccnavi/common/ に集約し、記録と状態ディレクトリは logs ディレクトリに配置する構成
tags: [config, records]
keywords: [共通層, .ccnavi, 設定, logs, 記録, 共有]
---

# ADR-0042: 共通層の設定は `.ccnavi/common/` に、記録と状態ディレクトリは `logs/` に置く

状態: 採用

## 状況

ccnavi の置き場が `.claude/` と `.ccnavi/` の 2 か所に分かれていた。

| 何 | 置き場 |
|---|---|
| 共通層のルール・フェーズの種類・リスクの配点、見本 | `.claude/ccnavi/` |
| 共通層の配点スクリプト | `.claude/ccnavi/` か `.claude/scripts/` |
| 記録、状態ディレクトリ、セッションの状態 | `.claude/ccnavi/` |
| 自身の層・プロジェクトの層、承認済みチケット、HITL ポイントの sh、実行ファイル | `.ccnavi/` |

設計 11.2 は「共通層は今の `.claude/ccnavi/` のまま」としていた。ccnavi ディレクトリを作ったとき、既存の置き場を
動かす理由が無かったからで、層の構造から決まったものではない。`.claude/` は Claude Code 自身の置き場
（settings.json・hooks・skills・worktrees）で、そこに ccnavi の設定と実行時の記録が混ざっていた。

## 決定

| 何 | 前 | 後 |
|---|---|---|
| 共通層の 3 本（`CCNAVI_RULES` / `CCNAVI_PHASES` / `CCNAVI_RISK` の既定）と見本 | `.claude/ccnavi/` | `.ccnavi/common/` |
| 共通層の配点スクリプト | `.claude/ccnavi/`・`.claude/scripts/` | `.ccnavi/common/scripts/` |
| 判定の記録（`CCNAVI_LOG`） | `.claude/ccnavi/log.jsonl` | `logs/log.jsonl` |
| 状態ディレクトリ（`CCNAVI_STATE`） | `.claude/ccnavi/state` | `logs/state` |

3 層の構造は変えない。共通層はどのツリーにも適用され、自身の層（`.ccnavi/config/`）はワークスペースの
ツリーだけに適用される。共通層を `.ccnavi/config/` に混ぜないのは、混ぜるとプロジェクトに共通層の deny が
適用されなくなるから。`.ccnavi/` 直下（`.ccnavi/rules.yml`）にしないのは、プロジェクトの `.ccnavi/` にも
同じ名前が置けて、共通層と見分けにくくなるから。

共通層の置き場は `CCNAVI_PROJECT_HOME`（ccnavi ディレクトリの名前）に付いて動かない。ccnavi ディレクトリの名前は各層のパスに入る名前で、
共通層を動かすなら `CCNAVI_RULES` などで動かす。

移し替えは導入スクリプトが受け持つ。前の置き場にあって新しい置き場に無いものを移し、`env` が前の既定の
パスと一字一句同じなら書き換える。`--lint` は、前の置き場に読まれない設定が残っていれば warn で言う。

## 理由

ccnavi のものが 1 つのディレクトリの下にまとまり、`.claude/` には Claude Code 自身のものだけが残る。共通層も
ccnavi ディレクトリを守る組み込みルール（`*/.ccnavi/*`）で名指しのツールから守られる。前は `rules.yml` の 1 行に
任せていたので、ルールを書き換えれば外せた。

記録と状態ディレクトリはユーザが持つ設定ではなく、実行のたびに書かれるもの。git のラッパースクリプトの記録と同じ `logs/` に置けば、
`.gitignore` の `/logs/` 1 行で追跡から外れる。

## 失うもの

- エージェントが共通層の yml を直接書けなくなる。 ccnavi ディレクトリを守る組み込みのルールはルールファイルから外せない。
  見本（`rule-samples.yml`）も同じで、前は「見本を足すのはエージェントの仕事」としていた。見本の下書きは
  scratchpad に置き、ルールの下書きと一緒にユーザに渡す
- `logs/` を守るパスが増える。 前は `.claude/ccnavi/` を守るパスの中に記録と状態ディレクトリが入っていた。
  移したぶん守りが外れないよう、`logs/log.jsonl` と `logs/state` を組み込みのシェル書き込みの deny に
  足した。`logs/` の下の git のラッパースクリプトの記録は守らない。プロジェクトに `state` という名前を付けると、
  git のラッパースクリプトの記録（`logs/<プロジェクト>/`）が状態ディレクトリと同じディレクトリに入る
- `.claude/ccnavi/` を守るパスは外せない。 `env` で前のパスを指したままのワークスペースがあるので、
  守る場所に残してある
- 移すと、開いているセッションの判定が組み込みの既定に戻る。 `env` はセッションを開き直すまで
  変わらないので、開いているセッションは前の置き場を読みに行き、何も読めない。導入スクリプトを打った
  セッション自身がそうなる形だけは、`env` を見て移さずに待つ。それ以外のセッションは開き直してもらう
- `env` を名指しも `--force` も無しに書き換える。 「既にある env は触らない」の例外になる
  （ADR-0041 の `CCNAVI_BIN_PATH` と同じ扱い）。ユーザが意図して前のパスを書いていても区別できない
- 設計書・README・拡張・テストに前のパスが多く入っていた。 過去の ADR とチケットは書き換えない。
  読むときは、その時点の置き場の話として読む
