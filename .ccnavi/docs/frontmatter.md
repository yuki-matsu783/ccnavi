---
title: md の frontmatter の決まり
type: guide
description: mdの頭に付けるfrontmatterのキーとtypeの選び方。ccnavi --docsはこれを索引にして検索する
tags: [frontmatter, docs-search]
keywords: [frontmatter, type, tags, keywords, ccnavi-index.jsonl, ccnavi --docs, 索引, 検索, タグ, kebab-case]
---

# md の frontmatter の決まり

`ccnavi --docs`はmdの本文ではなく、頭のfrontmatterを検索する。絞り込みは`--type` `--tag` `--keyword` `--text` `--path`、出力の形式は`--format`で選ぶ。旗の一覧は`ccnavi --docs --help`で出る。
frontmatterが無いmdも一覧には出るが、`--type` `--tag` `--keyword`では見つからず、`--text`で照合されるのもパスと日時だけになる。
frontmatterの書き方しだいで、文書の探しやすさがそのまま決まる。

## キー

| キー | 要否 | 書くこと |
|---|---|---|
| `type` | 必須 | 文書の種類。下の表の値から選ぶ |
| `title` | 推奨 | 人が読む題名。本文の見出しと揃える |
| `description` | 推奨 | 何の文書かを1文で。一覧から開くかどうかを決める手がかりになる |
| `tags` | 推奨 | 文書をまたいで使う分類。kebab-caseで2〜4個（`worktree`、`ticket-control`など）。既にある語に揃える |
| `keywords` | 推奨 | 本文の特徴語。3〜20個（目安10個）。日本語の文書には日本語の語も入れる |

```yaml
---
title: ワークツリーで作業する
type: guide
description: ワークツリーの切り方・統合先への取り込み方・片付け方と、他セッションの変更の扱い
tags: [worktree, git]
keywords: [ワークツリー, 統合先, ccnavi-git.sh, worktree add, fast-forward, 片付け, 他セッション]
---
```

- mdはUTF-8で書く。frontmatterを閉じる`---`は、ファイルの先頭から64 KiB以内に置く。閉じる`---`がそれより後ろにあるmdと、
  UTF-8でないmdはfrontmatterを読めないので、frontmatterが無いもの（`null`）として扱う
- 値は1行で書く。`tags`と`keywords`は`[a, b]`の形で書く。`tags: a`のように括弧なしで書いても1要素として読めるが、`[a]`と書いて揃える
- YAMLのエイリアス（`*名前`）は使わない。使うとfrontmatter全体を読めず`null`になる
- すでに使われているtagsは`ccnavi --docs --format jsonl`の`frontmatter.tags`で確かめられる。同じ意味で表記だけ違う語を増やさない

## keywordsの注意

`--keyword`は完全一致で検索する。大文字と小文字の違いと、NFC正規化の違いは無視する。`構造化ログ`だけを入れた文書は`--keyword ログ`では見つからない。
複合語を入れるときは、`ログ`、`承認`、`レビュー`など、検索する人が打ちそうな短い語も別の要素として加える。

## typeの値

`type`は文書の種類で、値はプロジェクトで決める。`ccnavi --docs`は値の意味を解釈せず、`--type`で完全一致の絞り込みに使うだけである。
既に使われている値は`ccnavi --docs --format jsonl`の`frontmatter.type`で確かめ、同じ意味で表記だけ違う値を増やさない。
`guide`（使い方と作業の手引き）、`design`（現在の実装の説明）、`requirements`（外から観測できる約束）、`adr`（設計判断の記録）、
`report`（調査結果や作業報告）のように、文書の役割で分けると探しやすい。

- `CLAUDE.md`・`README.md`・`CHANGELOG.md`には付けない。中身がそのまま使われるためで、`CLAUDE.md`は毎回モデルの文脈に入り、`README.md`はホストやエディタが
  先頭から表示する。先頭にYAMLを足すと、そのYAMLがユーザにも見え、モデルの文脈にも毎回入る。
  これらは`--path`（`--path README`、`--path CLAUDE`）か、`--text`でパスを指定して探す
- `SKILL.md`には、Claude Codeがスキルを選ぶときに読む`name`と`description`がある。`type: skill`を足す決まりはあるが、
  既存のスキルにはまだ足していない。共通の工程スキルは`process: true`、配るだけのスキルは`distribute: true`（どちらも真偽値）も持つ（`.ccnavi/docs/skill-processes.md`）。スキルは`--path .claude/skills`で探す

## プロジェクト

`projects/<名前>/`のmdも同じ決まりで書く。`ccnavi --docs`はワークスペースとプロジェクトをまとめて検索し、プロジェクトのmdは
`projects/<名前>/…`のパスで表示する。`docs/spec/`のような、プロジェクト固有の置き場にある文書には、既にある値で近いものを使う。
プロジェクトの`.gitignore`に`**/ccnavi-index.jsonl`が無ければ、ccnaviが索引を書く前にその1行を追記する（ファイルが無ければ作る）。
追記するのは1行だけで、既存の行は変えない。追記したプロジェクトは、SessionStartの案内とstderrに名前が出る。
`.gitignore`が未コミットの差分として残るので、そのプロジェクトのチケットの中でコミットする。
ユーザが`!`で`ccnavi-index.jsonl`を否定している（索引を追跡している）プロジェクトには追記せず、索引の対象外として案内に名前を出す。
追記に失敗したときも、対象外として案内する。ccnaviの導入スクリプト（`ccnavi-setup.sh`）が追記するのはワークスペースの`.gitignore`だけ

## 索引のファイル

- `ccnavi-index.jsonl`はmdのあるディレクトリごとにccnaviが書く生成物。手で直さない。ccnaviの形でないファイルを同じ名前で
  置かれている場合、ccnaviはそのファイルを上書きせず、案内にそのパスを出す
- 初回の実行（`--docs`かSessionStart）ではmdをすべて読むので、mdが数千本あると数秒かかる。SessionStartでは短い制限時間で
  打ち切り、読めた分だけを索引に書いて、残りは次回に回す。2回目からは、更新日時が変わったmdだけを読む
- mdを消しても`ccnavi-index.jsonl`が残ることがある。gitで追跡していないmdしか無いディレクトリや、ディレクトリごと消したときなどである。
  残った`ccnavi-index.jsonl`は読まれないので、結果は変わらない。気になるなら消してよい。gitの無視対象なので差分は出ない

## 対象外

| 対象 | 扱い | 理由 |
|---|---|---|
| チケット（`wip/proposals/**`、`.ccnavi/approved/**`、`wip/design/scripts/i*.md`などの下書き） | 付けない | チケットは独自のfrontmatter（`version:` `ticket:` …）を持ち、ccnaviが読む。`.ccnavi/`の下は（`.ccnavi/docs/`を除いて）索引にも載らない |
| `SKILL.md`の`name`と`description` | 変えない | Claude Codeがスキルを選ぶときに実際に使うキー。`description`は流用し、`type`などを下に足す。工程スキルの`process: true`・`distribute: true`も変えない |
| ユーザに渡す物（`wip/handoff/`）、使い捨て（`scratchpad/`） | 付けなくてよい | 使い捨て。`wip/handoff/`は統合のときに`integrate`が外し、`scratchpad/`は追跡されない |
| `.claude/skills/yomiyasu/**` | 付けない | 外部から取り込んだもの（`UPSTREAM.md`を参照）。中身を書き換えないのでfrontmatterも足さない |

既にあるmdにfrontmatterを足すときは、既存のキーの値と順序を変えず、足りないキーだけを後ろに足す。

Writeで新しく作ったmdにfrontmatterか`type`が無いと、ccnaviが`NOTICE_DOC_FRONTMATTER`で1度だけ案内する。`README.md`・`CLAUDE.md`・`CHANGELOG.md`と、上の対象外の表で「付けない」「付けなくてよい」とした置き場には出さない。
