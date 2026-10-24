---
type: guide
title: 工程スキルと reference
description: スキルを標準の工程ごとの薄い入口に絞り、細かい挙動を references に溜める置き場・書き方・引き方
tags: [skills, docs-search]
keywords: [工程スキル, reference, references, skill-reference, 入口, process-design, process-research, process-implement, process-test, process-debug, process-review, process-release, フェーズ定義, tags, ccnavi --docs, コンテキスト]
---

# 工程スキルと reference

スキルの `name` と `description` は、セッションの文脈に常に入る。スキルが増えるほど文脈が汚れるため、スキルは標準の工程ごとの入口に絞り、
細かい挙動は `references/` に溜めて、必要なときに `ccnavi --docs` で引く。

## 工程スキル

| スキル | 工程 | 対応するフェーズ定義（`phases.yml` の id） |
|---|---|---|
| `process-design` | 設計・方式の検討 | `design` |
| `process-research` | 調査（既存コードの読み解き、技術や案の比較、影響範囲の洗い出し）。不具合の原因調査は `process-debug` | `research` |
| `process-implement` | 実装（機能の追加、不具合の修正、挙動を変えないコード整理） | `implement` |
| `process-test` | テストの作成・実行・切り分け | `acceptance`、`implement` の「テスト」の部分 |
| `process-debug` | 不具合の原因調査と修正の確認 | なし |
| `process-review` | 差分やチケットのレビュー | なし（フェーズ定義の `review:` と `kind: feedback` が近い） |
| `process-release` | 統合先への取り込み・版の切り出し | なし |

- スキル名は `process-` に続けて、対応するフェーズ定義の id と同じ語を置く（対応するフェーズ定義が無いものは `process-debug` のように工程の語を置く）。`process-` は、組み込みや各自が作ったスキルと名前が重ならないようにするための印で、SKILL.md の frontmatter の `process: true` とは別。フェーズ定義の id を足したり改めたりするときは、この表も見る
- 本文は「基本の考え方」の短い列と、引き方の一文だけにする。細かい手順は書かない
- 既存のスキル（`commit` `ccnavi-config` `eli5` `eli10` `yomiyasu`）はこの決まりの対象外。直し方は `skill-review.md` のとおり
- 工程スキルを増やすのは、既存のどの工程にも当たらない作業が繰り返し出たときだけ。ユーザの承認を取る

## reference の置き場

`references/` の最上段は 2 つに分ける。ワークスペースもプロジェクトも同じ形にする。

| 最上段 | 中身 | 写されるか |
|---|---|---|
| `common/` | 共通の reference。ワークスペースの `.claude/skills/<工程>/references/common/` が元 | プロジェクトの `skills/<工程>/references/common/` へ写される。共通側から無くなった `.md` は写し先からも消える |
| `project/` | そのリポジトリ固有の reference。ワークスペースの固有分は `.claude/skills/<工程>/references/project/`、プロジェクトの固有分は `projects/<名前>/skills/<工程>/references/project/` | 写されない。触れられない |

`common/` と `project/` の中のディレクトリ構造は任意。最上段の 2 つ以外のディレクトリや、`references/` 直下のファイルは作らない。

- 共通の工程スキルの `SKILL.md` と `references/common/` は、着手済みの親があるときのユーザの発言が、そのワークツリーのプロジェクトの `skills/<工程>/` へ写す。`common/` は共通の写しなので、上書き・追加に加えて、共通側から無くなった `.md` を消す（空になったディレクトリも片付ける）。`SKILL.md` は消さない。
  プロジェクトだけを clone した人の手元にも入口と reference が入るようにするため。写した分は同期の出力に出るので、その場でコミットする。単体 clone では、reference は`ccnavi --docs`で引ける。入口の`SKILL.md`を読ませる目録は出ない（未解決）
- 共通の工程スキルは、SKILL.md の frontmatter に `process: true`（真偽値。文字列の `"true"` や BOM 付きの SKILL.md は写されない）を持つ。写されるのはこのスキルだけ。ディレクトリ名は `^[A-Za-z0-9._-]+$` にする。当たらない名前は、写されても目録に載らない
- 工程スキルのディレクトリ自体や、その下にシンボリックリンクがあると、何も写さず着手しない
- 写されたファイル（`SKILL.md` と `references/common/`）は共通側の写しなので、プロジェクトの中で直さない。直すと次の発言のときに上書きされる。共通を直したいときは、共通の置き場（`.claude/skills/<工程>/`）を直す提案をする
- プロジェクト固有の reference は `references/project/` に置く。`project/` は写されず、上書きも削除もされない
- 目録（cwd がそのプロジェクトの中にあるときに渡る、名前・説明・場所の一覧）に載るのは、プロジェクトの `skills/<名前>/SKILL.md`。写された工程スキルもここに載る

## reference の書き方

- 置くのは `common/` か `project/` の下。どちらに置くかは、他のプロジェクトでも使えるかで決める。使えるなら `common/`（共通の提案にする）、そのリポジトリだけの事情なら `project/`

- 1ファイル1話題。頭に frontmatter を付ける（`.ccnavi/docs/frontmatter.md`）
- `type: skill-reference`。`tags` には工程スキルの名前（`process-test` など）を必ず入れる。引くときの鍵になるため
- `keywords` には、検索する人が打つ短い語を入れる（`.ccnavi/docs/frontmatter.md` の「keywordsの注意」）
- 本文は、手順の流れに沿って「規則 + なぜ（1句）」で書く。日付・チケット番号・エラー文を見出しや名前にしない

## 引き方

```sh
ccnavi --docs --type skill-reference --tag process-test                # 共通とプロジェクトの両方から工程で絞る
ccnavi --docs --type skill-reference --tag process-test --keyword <語> # 語でさらに絞る
```

ワークスペースとプロジェクトは同じ索引で引けるので、cwd にかかわらず両方が出る。当たらなかったときは、キーワードの付け方を疑い、reference の `keywords` を足す。

## 振り返りとの関係

スキル改善（`skill-improve`）と wiki（`skill-wiki.md`）の結果は、原則 reference の追加か追記として入れる。
wiki のパターンが繰り返し有効だと分かって手順として確かめられたものが、reference になる。
