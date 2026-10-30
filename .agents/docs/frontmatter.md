---
title: ccnaviのリポジトリのfrontmatterの値
type: guide
description: ccnaviのリポジトリで使うtypeの値とtagsの使い分け。frontmatterの書き方そのものは.ccnavi/docs/frontmatter.md
tags: [frontmatter, docs-search]
keywords: [frontmatter, type, tags, design-doc, settings, config, skill, skill-reference, learning-wiki, 索引, 検索]
---

# ccnaviのリポジトリのfrontmatterの値

frontmatterのキーと書き方、索引、対象外の置き場は[.ccnavi/docs/frontmatter.md](../../.ccnavi/docs/frontmatter.md)（配布先にも配る）。
ここには、ccnaviのリポジトリ固有の`type`の値と、`tags`の使い分けだけを書く。

## tagsの使い分け

意味の近いtagsは、次の基準で使い分ける。

| tag | 付けるもの | 付けないもの |
|---|---|---|
| `design-doc` | `docs/`の要件書・設計書・ADR（`docs/10_requirements.md`・`docs/11_requirements/**`・`docs/20_design.md`・`docs/21_design/**`・`docs/30_adr.md`・`docs/30_adr/**`）と、ADRの書き方や置き場についての文書 | tag `design`は使わない（`design-doc`に統合した。`type: design`とは別） |
| `settings` | Claude Codeの`settings.json`（hookの登録・`env`・`permissions`）と、そこで渡す環境変数 | ccnavi自身の設定ファイル |
| `user-guide` | `docs/50_guide/*.md`（人間の利用者が読む手引き）。`type`は`guide`のまま、読み手の区別だけに使う | `docs/40_development.md`と`.ccnavi/docs/*.md` |
| `config` | ccnaviの設定ファイル（`.ccnavi/config/common/`の`rules.yml`・`risks.yml`と、`.ccnavi/config/project/`の`phases.yml`など）の形・読み方・レイヤー | `settings.json`と環境変数 |

`settings.json`の`env`でccnaviの設定ファイルの置き場を渡す話のように両方にまたがるときだけ、両方を付ける。

## ccnaviのリポジトリ固有のtypeの値

`type`の値の選び方と、どのリポジトリでも使う値（`guide`・`skill`・`skill-reference`・`learning-wiki`・`report`など）は[.ccnavi/docs/frontmatter.md](../../.ccnavi/docs/frontmatter.md)の「typeの値」にある。
ここには、ccnaviのリポジトリの`docs/`で使う、固有の値だけを置く。

| type | 対象 |
|---|---|
| `idea` | 製品の根底にある考え方（Why・What・How）。`docs/00_idea.md`。ユーザが依頼しない限り直さない |
| `requirements` | 外から観測できる約束。`docs/10_requirements.md`・`docs/11_requirements/**` |
| `design` | 現在の実装の説明。`docs/20_design.md`・`docs/21_design/**` |
| `adr` | 設計判断の記録。`docs/30_adr/<主題>/NNNN-*.md` |
| `glossary` | 用語集。`docs/CONTEXT.md` |

次は`guide`を付ける。`.ccnavi/docs/*.md`、`.agents/docs/*.md`、`docs/30_adr.md`、`docs/40_development.md`、`docs/50_guide/*.md`。
`docs/wiki/**`は`learning-wiki`、スキルから切り出した資料は`skill-reference`。

表に無い種類の文書を足すときは、この表に行を足してから使う。

`CLAUDE.md`・`AGENTS.md`・`README.md`・`CHANGELOG.md`・`SKILL.md`にfrontmatterの`type`を付けない理由と探し方は、[.ccnavi/docs/frontmatter.md](../../.ccnavi/docs/frontmatter.md)の「typeの値」と「対象外」にある。

## 対象外の表と実行ファイル

`.ccnavi/docs/frontmatter.md`の「対象外」の表の置き場は、実行ファイルにも同じ一覧（`src/ccnavi/catalog/docsearch.py`の`NO_FRONTMATTER_PLACES`）があり、テストが表と比べる。表に行を足したら一覧にも足す。
