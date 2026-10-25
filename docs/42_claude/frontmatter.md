---
title: ccnaviのリポジトリのfrontmatterの値
type: guide
description: ccnaviのリポジトリで使うtypeの値とtagsの使い分け。frontmatterの書き方そのものは.ccnavi/docs/frontmatter.md
tags: [frontmatter, docs-search]
keywords: [frontmatter, type, tags, design-doc, settings, config, skill, skill-reference, learning-wiki, 索引, 検索]
---

# ccnaviのリポジトリのfrontmatterの値

frontmatterのキーと書き方、索引、対象外の置き場は[.ccnavi/docs/frontmatter.md](../../.ccnavi/docs/frontmatter.md)（配布先にも配る）。
ここには、ccnaviのリポジトリで使う`type`の値と`tags`の使い分けだけを書く。

## tagsの使い分け

意味の近いtagsは、次の基準で使い分ける。

| tag | 付けるもの | 付けないもの |
|---|---|---|
| `design-doc` | 設計文書（`docs/20_design.md`・`docs/21_design/**`・`docs/10_requirements.md`・`docs/11_requirements/**`・`wip/design/**`）、ADRの書き方や置き場についての文書、画面の構成や状態遷移などの設計を決めたADR | `design`は使わない。`design-doc`に統合した |
| `settings` | Claude Codeの`settings.json`（hookの登録・`env`・`permissions`）と、そこで渡す環境変数 | ccnavi自身の設定ファイル |
| `user-guide` | `docs/41_guide/*.md`（人間の利用者が読む手引き）。`type`は`guide`のまま、読み手の区別だけに使う | `docs/40_development.md`と`.ccnavi/docs/*.md` |
| `config` | ccnaviの設定ファイル（`.ccnavi/config/common/`の`rules.yml`・`risks.yml`と、`.ccnavi/config/project/`の`phases.yml`など）の形・読み方・レイヤー | `settings.json`と環境変数 |

`settings.json`の`env`でccnaviの設定ファイルの置き場を渡す話のように両方にまたがるときだけ、両方を付ける。

## typeの値

| type | 対象 | 現状 |
|---|---|---|
| `guide` | 使い方と作業の手引き。`.ccnavi/docs/*.md`、`docs/42_claude/*.md`、`docs/30_adr.md`、`docs/41_guide/*.md`（利用者向け）、`docs/40_development.md`（開発者向けの入口） | 付いている。ルートの`README.md`と拡張の`README.md`（`extensions/**`）には付けていない（理由は表の下） |
| `rule` | 常に守る決まり。`CLAUDE.md` | 付けていない（理由は表の下）。`--type rule`では何も出ない |
| `design` | 現在の実装の説明。`docs/20_design.md`・`docs/21_design/**`、`wip/design/**`の設計メモ | `docs/20_design.md`・`docs/21_design/**`に付いている |
| `idea` | 製品の根底にある考え方（Why・What・How）。`docs/00_idea.md`。ユーザが依頼しない限り直さない | `docs/00_idea.md`に付いている |
| `requirements` | 外から観測できる約束。`docs/10_requirements.md`・`docs/11_requirements/**` | 付いている |
| `glossary` | 用語集。`docs/CONTEXT.md` | 付いている |
| `adr` | 設計判断の記録。`docs/30_adr/<主題>/NNNN-*.md` | 付いている |
| `skill` | スキル本体。`.claude/skills/*/SKILL.md`、プロジェクトの`skills/*/SKILL.md` | 付けていない（理由は表の下）。新しく作る`SKILL.md`には`type: skill`を足す案内が出る。`name`・`description`を持つ。共通の工程スキルは`process: true`、配るだけのスキルは`distribute: true`（どちらも真偽値）も持つ（`.ccnavi/docs/skill-processes.md`）。`--type skill`では何も出ない |
| `skill-reference` | スキルから切り出した資料。`.claude/skills/*/references/**/*.md`、プロジェクトの`skills/*/references/**/*.md`。工程スキルの`references/`は最上段を`common/`と`project/`に分け、中のディレクトリ構造は任意。工程スキルの reference は`tags`にスキル名を入れる（`.ccnavi/docs/skill-processes.md`） | 付いている |
| `learning-wiki` | 気づきを統合した知識（パターン・採否の記録）と受信箱。`docs/wiki/**`、`projects/<名前>/docs/wiki/**`（`.ccnavi/docs/skill-wiki.md`） | 書いたときに付ける。`ccnavi --docs` は `--type learning-wiki` を明示したときだけ出す（作業のセッションに渡さないため） |
| `report` | 調査結果や作業報告。`wip/`の下など、置き場はその都度決める | 書いたときに付ける |

表に無い種類の文書を足すときは、この表に行を足してから使う。

表で「付けていない」とした文書（`CLAUDE.md`・`README.md`・`CHANGELOG.md`・`SKILL.md`）の理由と探し方は、[.ccnavi/docs/frontmatter.md](../../.ccnavi/docs/frontmatter.md)の「typeの値」と「対象外」にある。

## 対象外の表と実行ファイル

`.ccnavi/docs/frontmatter.md`の「対象外」の表の置き場は、実行ファイルにも同じ一覧（`docsearch.NO_FRONTMATTER_PLACES`）があり、テストが表と比べる。表に行を足したら一覧にも足す。
