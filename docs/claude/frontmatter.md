---
title: md の frontmatter の決まり
type: guide
description: mdの頭に付けるfrontmatterのキーとtypeの値。ccnavi --docsの索引はこれを引く
tags: [frontmatter, docs-search]
keywords: [frontmatter, type, tags, keywords, index.jsonl, ccnavi --docs, 索引, 検索, タグ, kebab-case]
---

# md の frontmatter の決まり

`ccnavi --docs`が検索に使うのは、mdの本文ではなく頭のfrontmatterだ（出力の形と絞り込み方はREADMEの「ドキュメントの索引」）。
frontmatterが無いmdも一覧には出るが、`--type` `--tag` `--keyword`では見つからない。`--text`もパスと日時にしか一致しない。
**frontmatterの書き方しだいで、文書の探しやすさが決まる。**

## キー

| キー | 要否 | 書くこと |
|---|---|---|
| `type` | 必須 | 文書の種類。下の表の値から選ぶ |
| `title` | 推奨 | 人が読む題名。本文の見出しと揃える |
| `description` | 推奨 | 何の文書かを1文で書く。読む人は一覧でこれを見て、開くかどうかを決める |
| `tags` | 推奨 | 文書をまたいで使う分類。kebab-caseで2〜4個（`worktree`、`ticket-control`など）。既にある語に揃える |
| `keywords` | 推奨 | 本文に特徴的な語。3〜20個（目安は10個）。日本語の文書なら日本語の語も混ぜる |

```yaml
---
title: ワークツリーで作業する
type: guide
description: ワークツリーの切り方・統合先へ戻し方・片付け方と、他セッションの変更の扱い
tags: [worktree, git]
keywords: [ワークツリー, 統合先, ccnavi-git.sh, worktree add, fast-forward, 片付け, 他セッション]
---
```

- mdはUTF-8で書き、frontmatterはファイルの先頭から64 KiB以内で閉じる。それより後ろで閉じるものとUTF-8でないものは読めず、
  frontmatterが無いもの（`null`）として扱う
- 値は1行で書く。`tags`と`keywords`は並び（`[a, b]`）で書く。スカラーで書いても1要素の並びとして読むが、並びの形に揃える
- YAMLの別名（`*名前`）は使わない。使うとfrontmatter全体が読まれず、`null`になる
- 既存の語は`ccnavi --docs --format jsonl`の`frontmatter.tags`で確かめられる。同じ意味の語を別の表記で増やさない

## tagsの使い分け

意味の近いtagは、次のように使い分ける。

| tag | 付けるもの | 付けないもの |
|---|---|---|
| `design-doc` | 設計の書き下し（`ccnavi.md`・`requirements.md`・`wip/design/**`）、ADRの書き方や置き場を扱う文書、仕組みの設計（画面の組み立て・状態の遷移など）を決めたADR | `design`は使わない（`design-doc`に統合した） |
| `settings` | Claude Codeの`settings.json`（hookの登録・`env`・`permissions`）と、そこで渡す環境変数 | ccnavi自身の設定ファイル |
| `config` | ccnaviの設定ファイル（`.ccnavi/common/rules.yml`・`phases.yml`・`risks.yml`）の形・読み方・層 | `settings.json`と環境変数 |

両方を付けるのは、`settings.json`の`env`でccnaviの設定ファイルの置き場を渡す話のように、両方にまたがるときだけにする。

## keywordsを選ぶときの注意

`--keyword`は語が完全に一致したときだけ見つかる（大文字小文字とNFCの違いは揃えてから比べる）。
`構造化ログ`だけを入れた文書は、`--keyword ログ`では見つからない。
複合語を入れるときは、探す人が打ちそうな短い語（`ログ`、`承認`、`レビュー`など）も別の要素として並べる。

## typeの値

| type | 対象 | 今の付け方 |
|---|---|---|
| `guide` | 使い方と作業の手引き。`docs/claude/*.md`、`docs/adr/README.md` | 付いている。ルートの`README.md`と拡張の`README.md`（`vscode-extension/**`、`chrome-extension/**`）には付けていない（下の注） |
| `rule` | 常に守る決まり。`CLAUDE.md` | 付けていない（下の注）。`--type rule`では何も出ない |
| `design` | 今の実装の書き下し。`ccnavi.md`、`wip/design/**`の設計メモ | `ccnavi.md`に付いている |
| `requirements` | 外から観測できる約束。`requirements.md` | 付いている |
| `glossary` | 用語集。`CONTEXT.md` | 付いている |
| `handover` | 引き継ぎ。`HANDOVER.md` | 付いている |
| `adr` | 設計判断の記録。`docs/adr/NNNN-*.md` | 付いている |
| `skill` | スキル本体。`.claude/skills/*/SKILL.md`、プロジェクトの`docs/skills/*/SKILL.md` | 付けていない（下の注）。`name`・`description`だけを持つ。`--type skill`では何も出ない |
| `skill-reference` | スキルから切り出した資料。`.claude/skills/*/references/*.md` | 付いている |
| `report` | 調べた結果や作業の報告を残すもの。置き場はその都度決める（`wip/`の下など） | 書いたときに付ける |

表に無い種類の文書を足すときは、先にこの表へ行を足してから使う。

「付けていない」ものの注。

- `CLAUDE.md`と`README.md`類は、中身がそのまま表示されたり読み込まれたりする（`CLAUDE.md`は毎回モデルの文脈に入り、
  `README.md`はホストやエディタが頭から表示する）。頭にYAMLを足すと、読む人の目にもモデルの文脈にも毎回そのYAMLが入るので、付けない。
  これらは`--path`（`--path README`、`--path CLAUDE`）か、`--text`でパスを検索して探す
- `SKILL.md`は、Claude Codeがスキルを選ぶときに読む`name`・`description`を持つ（下の「対象外」）。`type: skill`を足す
  決まりはあるが、今のスキルにはまだ足していない。スキルは`--path .claude/skills`で探す

## プロジェクト

`projects/<名前>/`のmdも同じ決まりで書く。`ccnavi --docs`はワークスペースとプロジェクトをまとめて検索し、パスを
`projects/<名前>/…`の形で出す。プロジェクト独自の置き場にある文書（`docs/spec/`など）には、上の表の近い値を使うか、表に行を足す。
索引に載るのは、そのリポジトリの`.gitignore`に`**/index.jsonl`があるプロジェクトだけ。無いプロジェクトは、SessionStartの
案内に名前が出る。足す作業は、そのプロジェクトのチケットの範囲で行う。ccnaviはプロジェクトの`.gitignore`を書き換えず、導入スクリプト
`scripts/ccnavi-setup.sh`が足すのもワークスペースの`.gitignore`だけだ。

## 索引のファイル

- `index.jsonl`は、mdのあるディレクトリごとにccnaviが書く生成物で、手で直さない。同じ名前で別の道具のファイルが
  置かれていると、ccnaviはそのファイルを書き換えず、案内でそのファイルを示す
- 初回（`--docs`かSessionStart）はmdを全部読むので、mdが数千本あると数秒かかる。SessionStartは短い期限で打ち切り、
  読めた分までを書いて、残りは次の回に読む。2回目からは、更新日時の変わったmdだけを読む
- mdを消しても`index.jsonl`が残ることがある（追跡されていないmdだけのディレクトリや、ディレクトリごと消したときなど）。
  残ったファイルは読まれないので、結果は変わらない。気になれば消してよい（gitに無視されているので差分は出ない）

## 対象外

| もの | 扱い | 理由 |
|---|---|---|
| チケット（`wip/proposals/**`、`.ccnavi/approved/**`、`wip/design/scripts/i*.md`などの下書き） | 付けない | チケットは独自のfrontmatter（`version:` `ticket:` …）を持ち、ccnaviがそれを読む。`.ccnavi/`の下は索引にも載らない |
| `SKILL.md`の`name`と`description` | 変えない | Claude Codeがスキルを選ぶときに実際に使うキー。`description`はそのまま流用し、`type`などを下に足す |
| 下書き（`scratchpad/`） | 付けなくてよい | 使い捨ての文書。`.gitignore`にあるので索引にも載らない |
| `.claude/skills/yomiyasu/**` | 付けない | 外から取り込んだもの（`UPSTREAM.md`）。中身を書き換えないので、frontmatterも足さない |

既にあるmdに付けるときは、既存のキーの値と順を変えず、足りないキーだけを下に足す。
