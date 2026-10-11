---
type: guide
title: 概念スキルと reference
description: スキルを標準概念ごとの薄い入口に絞り、細かい挙動を references に溜める置き場・書き方・引き方
tags: [skills, docs-search]
keywords: [概念スキル, reference, references, skill-reference, 入口, designing, testing, debugging, reviewing, releasing, tags, ccnavi --docs, コンテキスト]
---

# 概念スキルと reference

スキルの `name` と `description` は、セッションの文脈に常に入る。スキルが増えるほど文脈が汚れるため、スキルは標準概念ごとの入口に絞り、
細かい挙動は `references/` に溜めて、必要なときに `ccnavi --docs` で引く。

## 概念スキル

| スキル | 概念 |
|---|---|
| `designing` | 設計・方式の検討 |
| `testing` | テストの作成・実行・切り分け |
| `debugging` | 不具合の原因調査 |
| `reviewing` | 差分やチケットのレビュー |
| `releasing` | 統合先への取り込み・版の切り出し |

- 本文は「基本の考え方」の短い列と、引き方の一文だけにする。細かい手順は書かない
- 既存のスキル（`commit` `ccnavi-config` `eli5` `eli10` `yomiyasu`）はこの決まりの対象外。直し方は `skill-review.md` のとおり
- 概念スキルを増やすのは、既存のどの概念にも当たらない作業が繰り返し出たときだけ。ユーザの承認を取る

## reference の置き場

| 範囲 | 置き場 |
|---|---|
| 共通（ワークスペース） | `.claude/skills/<概念>/references/` |
| プロジェクト固有 | `projects/<名前>/skills/<概念>/references/` |

- 共通の概念スキル（SKILL.md と references）は、親チケットの `ticket start` がプロジェクトの `skills/<概念>/` へ写す。上書きと追加だけで、消さない。
  プロジェクトだけを clone しても、同じ入口と reference が使えるようにするため。写した分は着手の出力に出るので、その場でコミットする
- 共通の概念スキルは、SKILL.md の frontmatter に `concept: true` を持つ。写されるのはこのスキルだけ
- 写されたファイルは共通側の写しなので、プロジェクトの中で直さない。直すと次の着手で上書きされる。共通を直したいときは、共通の置き場（`.claude/skills/<概念>/`）の提案にする
- プロジェクト固有の reference は、共通と違うパスに置く。同じパスは次の着手で共通の中身に上書きされる
- 目録（cwd がそのプロジェクトの中にあるときに渡る、名前・説明・場所の一覧）に載るのは、プロジェクトの `skills/<名前>/SKILL.md`。写された概念スキルもここに載る
- `references/` の中のディレクトリ構造は任意。深さも分け方も決めない。構造が見えてきたら、このファイルに足す

## reference の書き方

- 1ファイル1話題。頭に frontmatter を付ける（`docs/claude/frontmatter.md`）
- `type: skill-reference`。`tags` には概念のスキル名（`testing` など）を必ず入れる。引くときの鍵になるため
- `keywords` には、検索する人が打つ短い語を入れる（`docs/claude/frontmatter.md` の「keywordsの注意」）
- 本文は、手順の流れに沿って「規則 + なぜ（1句）」で書く。日付・チケット番号・エラー文を見出しや名前にしない

## 引き方

```sh
ccnavi --docs --type skill-reference --tag testing                    # 共通とプロジェクトの両方から概念で絞る
ccnavi --docs --type skill-reference --tag testing --keyword <語>     # 語でさらに絞る
```

ワークスペースとプロジェクトは同じ索引で引けるので、cwd にかかわらず両方が出る。当たらなかったときは、キーワードの付け方を疑い、reference の `keywords` を足す。

## 振り返りとの関係

スキル改善（`skill-improve`）と wiki（`skill-wiki.md`）の結果は、原則 reference の追加か追記として入れる。
wiki のパターンが繰り返し有効だと分かって手順として確かめられたものが、reference になる。
