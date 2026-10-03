---
title: md の frontmatter の決まり
type: guide
description: md の頭に付ける frontmatter のキーと type の値。ccnavi --docs はこれを索引にして検索する
tags: [frontmatter, docs-search]
keywords: [frontmatter, type, tags, keywords, index.jsonl, ccnavi --docs, 索引, 検索, タグ, kebab-case]
---

# md の frontmatter の決まり

`ccnavi --docs` は md の本文ではなく、頭の frontmatter を検索する。出力の形式と絞り込み方は README の「ドキュメントの索引」に書いてある。
frontmatter が無い md も一覧には出るが、`--type` `--tag` `--keyword` では見つからず、`--text` で照合されるのもパスと日時だけになる。
frontmatter の書き方しだいで、文書の探しやすさがそのまま決まる。

## キー

| キー | 要否 | 書くこと |
|---|---|---|
| `type` | 必須 | 文書の種類。下の表の値から選ぶ |
| `title` | 推奨 | ユーザが読む題名。本文の見出しと揃える |
| `description` | 推奨 | 何の文書かを 1 文で。一覧から開くかどうかを決める手がかりになる |
| `tags` | 推奨 | 文書をまたいで使う分類。kebab-case で 2〜4 個（`worktree`、`ticket-control` など）。既にある語に揃える |
| `keywords` | 推奨 | 本文の特徴語。3〜20 個（目安 10 個）。日本語の文書には日本語の語も入れる |

```yaml
---
title: ワークツリーで作業する
type: guide
description: ワークツリーの切り方・統合先へ戻し方・片付け方と、他セッションの変更の扱い
tags: [worktree, git]
keywords: [ワークツリー, 統合先, ccnavi-git.sh, worktree add, fast-forward, 片付け, 他セッション]
---
```

- md は UTF-8 で書く。frontmatter を閉じる `---` は、ファイルの先頭から 64 KiB 以内に置く。閉じる `---` がそれより後ろにある md と、
  UTF-8 でない md は frontmatter を読めないので、frontmatter が無いもの（`null`）として扱う
- 値は 1 行で書く。`tags` と `keywords` は `[a, b]` の形で書く。`tags: a` のように括弧なしで書いても 1 要素として読めるが、`[a]` と書いて揃える
- YAML のエイリアス（`*名前`）は使わない。使うと frontmatter 全体を読めず `null` になる
- すでに使われている tags は `ccnavi --docs --format jsonl` の `frontmatter.tags` で確かめられる。同じ意味で綴りだけ違う語を増やさない

## tags の使い分け

意味の近い tags は、次の基準で使い分ける。

| tag | 付けるもの | 付けないもの |
|---|---|---|
| `design-doc` | 設計文書（`ccnavi.md`・`requirements.md`・`wip/design/**`）、ADR の書き方や置き場についての文書、画面の構成や状態遷移などの設計を決めた ADR | `design` は使わない（`design-doc` に統合した） |
| `settings` | Claude Code の `settings.json`（hook の登録・`env`・`permissions`）と、そこで渡す環境変数 | ccnavi 自身の設定ファイル |
| `config` | ccnavi の設定ファイル（`.ccnavi/common/rules.yml`・`phases.yml`・`risks.yml`）の形・読み方・層 | `settings.json` と環境変数 |

`settings.json` の `env` で ccnavi の設定ファイルの置き場を渡す話のように両方にまたがるときだけ、両方を付ける。

## keywords の注意

`--keyword` は完全一致で検索する。大文字と小文字の違いと、NFC 正規化の違いは無視する。`構造化ログ` だけを入れた文書は `--keyword ログ` では見つからない。
複合語を入れるときは、検索するユーザが打ちそうな短い語（`ログ`、`承認`、`レビュー` など）も別の要素として加える。

## type の値

| type | 対象 | 現状 |
|---|---|---|
| `guide` | 使い方と作業の手引き。`docs/claude/*.md`、`docs/adr/README.md` | 付いている。ルートの `README.md` と拡張の `README.md`（`vscode-extension/**`、`chrome-extension/**`）には付けていない（理由は表の下） |
| `rule` | 常に守る決まり。`CLAUDE.md` | 付けていない（理由は表の下）。`--type rule` では何も出ない |
| `design` | 現在の実装の説明。`ccnavi.md`、`wip/design/**` の設計メモ | `ccnavi.md` に付いている |
| `requirements` | 外から観測できる約束。`requirements.md` | 付いている |
| `glossary` | 用語集。`CONTEXT.md` | 付いている |
| `handover` | 引き継ぎ。`HANDOVER.md` | 付いている |
| `adr` | 設計判断の記録。`docs/adr/NNNN-*.md` | 付いている |
| `skill` | スキル本体。`.claude/skills/*/SKILL.md`、プロジェクトの `docs/skills/*/SKILL.md` | 付けていない（理由は表の下）。`name`・`description` だけを持つ。`--type skill` では何も出ない |
| `skill-reference` | スキルから切り出した資料。`.claude/skills/*/references/*.md` | 付いている |
| `report` | 調査結果や作業報告。置き場はその都度決める（`wip/` の下など） | 書いたときに付ける |

表に無い種類の文書を足すときは、この表に行を足してから使う。

表で「付けていない」とした文書の理由と探し方は次のとおり。

- `CLAUDE.md` と `README.md` 類は、中身がそのまま使われる。`CLAUDE.md` は毎回モデルの文脈に入り、`README.md` はホストやエディタが
  先頭から表示する。先頭に YAML を足すと、その YAML がユーザにも見え、モデルの文脈にも毎回入るので付けない。
  これらは `--path`（`--path README`、`--path CLAUDE`）か、`--text` でパスを指定して探す
- `SKILL.md` には、Claude Code がスキルを選ぶときに読む `name` と `description` がある（下の「対象外」を参照）。`type: skill` を
  足す決まりはあるが、既存のスキルにはまだ足していない。スキルは `--path .claude/skills` で探す

## プロジェクト

`projects/<名前>/` の md も同じ決まりで書く。`ccnavi --docs` はワークスペースとプロジェクトをまとめて検索し、プロジェクトの md は
`projects/<名前>/…` のパスで表示する。プロジェクト固有の置き場にある文書（`docs/spec/` など）には、上の表で近い値を使うか、表に行を足す。
索引に載るのは、そのリポジトリの `.gitignore` に `**/index.jsonl` があるプロジェクトだけ。無いプロジェクトは、SessionStart の
案内に名前が出る。`.gitignore` への追記は、そのプロジェクトのチケットの中で行う。ccnavi はプロジェクトの `.gitignore` を書き換えず、
導入スクリプト `scripts/ccnavi-setup.sh` が追記するのもワークスペースの `.gitignore` だけ。

## 索引のファイル

- `index.jsonl` は md のあるディレクトリごとに ccnavi が書く生成物。手で直さない。同じ名前のファイルを別のツールが
  使っている場合、ccnavi はそのファイルを上書きせず、案内にそのパスを出す
- 初回の実行（`--docs` か SessionStart）では md をすべて読むので、md が数千本あると数秒かかる。SessionStart では短い制限時間で
  打ち切り、読めた分だけを索引に書いて、残りは次回に回す。2 回目からは、更新日時が変わった md だけを読む
- md を消しても `index.jsonl` が残ることがある（git で追跡していない md しか無いディレクトリや、ディレクトリごと消したときなど）。
  残った `index.jsonl` は読まれないので、結果は変わらない。気になるなら消してよい（git の無視対象なので差分は出ない）

## 対象外

| 対象 | 扱い | 理由 |
|---|---|---|
| チケット（`wip/proposals/**`、`.ccnavi/approved/**`、`wip/design/scripts/i*.md` などの下書き） | 付けない | チケットは独自の frontmatter（`version:` `ticket:` …）を持ち、ccnavi が読む。`.ccnavi/` の下は索引にも載らない |
| `SKILL.md` の `name` と `description` | 変えない | Claude Code がスキルを選ぶときに実際に使うキー。`description` は流用し、`type` などを下に足す |
| 下書き（`scratchpad/`） | 付けなくてよい | 使い捨て。`.gitignore` にあるので索引にも載らない |
| `.claude/skills/yomiyasu/**` | 付けない | 外部から取り込んだもの（`UPSTREAM.md` を参照）。中身を書き換えないので frontmatter も足さない |

既にある md に frontmatter を足すときは、既存のキーの値と順序を変えず、足りないキーだけを後ろに足す。
