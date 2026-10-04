---
type: design
title: 11. 複数のリポジトリ
description: ワークスペースとプロジェクトの複数リポジトリ構成の入口。何を解くかと、置き場・ルール・守るものなど各節への案内
tags: [design-doc, projects]
keywords: [複数のリポジトリ, ワークスペース, プロジェクト, 共通層, 層]
---

[設計書の入口に戻る](../design.md)

## 11. 複数のリポジトリ

道具（hook の登録、実行ファイル、保護済みスクリプト、スキル、CLAUDE.md）を持つ private の
リポジトリをワークスペースと呼び、その下の置き場に clone した別々のリポジトリをプロジェクトと呼ぶ。
Claude Code はワークスペースを開く。要求は [要件 REQ-MLT](../requirements/multi-repo.md)。

- 道具はワークスペース、設定はプロジェクト、git はツリーごと
- 設定 3 本（ルール・フェーズの種類・リスクの配点）は「共通層 + そのツリーの層」の和で判定する。足すだけで上書きは無く、厳しいほうを採る
- パスを持つツールは共通層 + 行き先の 1 層、パスを持たないツールは共通層 + 全部の層（11.4）
- チケットのプロジェクトは提案を置いた場所で決まり、判定はその値を見ない
- 共通層は共通の設定を配るための定義で、優先するのは各プロジェクトの層。親の着手で共通層をプロジェクトの層へコピーし、最初のレビューで知らせる（11.12）

### 11.1 何を解くか

- 相対パスの解決、実行後チェック、ワークツリーの列挙を、ワークスペースルートだけでなくプロジェクトごとの git でも行う
- プロジェクトごとのルールを置く場所を持つ。プロジェクトに `.claude/` は置かない（Claude Code がそこのスキルを読み、`--lint` が置き場違いとして指摘する）
- どのツリーにも適用したい deny（hook の保護、認証情報、main への直書き）を共通層に 1 度書けば全プロジェクトに適用される
- フェーズの種類の `scope` はワークツリーのレイアウトに縛られるので、種類と配点をツリーごとに持つ

層は和であり、後ろの層が前の層を上書きすることはできない。プロジェクトの層は、そこを編集するエージェントが書きうる。上書きできると、共通層の deny を消す手段になる。

## 詳細

| 節 | ファイル | 何が書いてあるか |
|---|---|---|
| 11.2 | [multi-repo/places.md](multi-repo/places.md) | ワークスペースとプロジェクトで何をどこに置くか |
| 11.3 | [multi-repo/trees.md](multi-repo/trees.md) | 3 種のツリーと所属の決め方 |
| 11.4 | [multi-repo/rules.md](multi-repo/rules.md) | 共通層とツリーの層の和で、ルール・フェーズの種類・リスクの配点を合成する |
| 11.5 | [multi-repo/tickets.md](multi-repo/tickets.md) | チケットのプロジェクトは提案を置いた場所で決まる |
| 11.6 | [multi-repo/protection.md](multi-repo/protection.md) | 複数リポジトリでのコアファイル |
| 11.7 | [multi-repo/post-check.md](multi-repo/post-check.md) | ツリーごとの実行後チェック |
| 11.8 | [multi-repo/scripts.md](multi-repo/scripts.md) | 保護済みスクリプトの案内と `{root}` |
| 11.9 | [multi-repo/diagnostics.md](multi-repo/diagnostics.md) | 層ごとの診断と記録 |
| 11.10 | [multi-repo/to-measure.md](multi-repo/to-measure.md) | 実装時に実測するもの |
| 11.11 | [multi-repo/out-of-scope.md](multi-repo/out-of-scope.md) | 見ないもの、入れないもの |
| 11.12 | [multi-repo/distribute-common.md](multi-repo/distribute-common.md) | 共通層をプロジェクトの層へ配る |
| 11.13 | [multi-repo/skills.md](multi-repo/skills.md) | プロジェクトのスキルの置き場と目録 |
