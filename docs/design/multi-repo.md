---
type: design
title: 11. 複数のリポジトリ
description: ワークスペースとプロジェクトの複数リポジトリ構成の入口。何を解くかと、置き場・ルール・守るものなど各節への案内
tags: [design-doc, projects]
keywords: [複数のリポジトリ, ワークスペース, プロジェクト, 共通レイヤー, レイヤー]
---

[設計書の入口に戻る](../design.md)

## 11. 複数のリポジトリ

道具（hook の登録、実行ファイル、保護済みスクリプト、スキル、CLAUDE.md）を持つ private の
リポジトリをワークスペースと呼び、その下の置き場に clone した別々のリポジトリをプロジェクトと呼ぶ。
Claude Code はワークスペースを開く。要求は [要件 REQ-MLT](../requirements/multi-repo.md)。

- 道具はワークスペース、設定はプロジェクト、git はツリーごと
- ルールとリスクの配点は「共通レイヤー + そのツリーのレイヤー」の和で判定する。足すだけで上書きは無く、厳しいほうを採る。フェーズ定義も「共通レイヤー + 親の `project:` が指すレイヤー」の和で、同 `id` で中身が違う定義と `title` の重なりは error（11.4）
- 共通と config はどちらか片方だけでも、両方無くても動く。ファイルが無いのは「設定が無い」正常で、不正なときだけ不備として出す（11.2）
- パスを持つツールは共通レイヤー + 行き先の 1 レイヤー、パスを持たないツールは共通レイヤー + 全部のレイヤー（11.4）
- チケットのプロジェクトは提案を置いた場所で決まる
- 着手済みの親があるとき、ユーザの発言のたびに、ワークスペースルートの共通レイヤーを親のワークツリーのプロジェクトの `.ccnavi/config/common/` へミラーする。プロジェクトの config には触れない。ワークスペースの中ではルートの共通レイヤーだけを読み、ミラーはプロジェクトを単体で clone したときだけ共通レイヤーとして読まれる（11.12）

### 11.0 使い方は 2 通りある

プロジェクトは、ワークスペースの `projects/<名前>/` に clone して使う形（ワークスペース内 clone）と、そのリポジトリだけを clone して使う形（単体 clone）のどちらでも使われる。ccnavi はどちらでも動かす。判断の経緯は [ワークスペース内 clone と単体 clone の両方で動かす](../adr/0145-work-in-workspace-and-standalone-clone.md)。

ワークスペースにあるものに頼る機能は、単体 clone で「動く」「縮退して動く」「動かない」のどれになるかを、足すときに決める。いまの状態は次のとおり。

- 共通レイヤー: ワークスペースの中ではルートのものだけを読み、単体 clone では配られたミラーを読む。動く（11.4、11.12）
- 工程スキル: 共通レイヤーのミラーと同じく、ユーザの発言のたびに、着手済みの親のワークツリーのプロジェクトの `skills/<工程>/` へコピーする。コピーは git に入り、reference は `ccnavi --docs` で引ける。入口の SKILL.md を読ませる目録は、単体 clone では出ない。縮退で、未解決（11.13）
- プロジェクトのスキル: プロジェクトの git に入る。目録は単体 clone では出ない。縮退しており、この点は未解決（11.13）

個別の不都合は [11.14 単体 clone で不都合な処理の一覧](multi-repo/standalone-clone-gaps.md) にまとめた。

「モード A / モード B」は、手引きと sh のコメントなどで使う、ワークスペース全体の形（`projects/` の有無）の呼び分けで、この設計書には定義が無い。ワークスペース内 clone は、モード B でそのプロジェクト 1 つを指す呼び方になる。単体 clone にはワークスペースが無いので、どちらでもない。ワークスペース内 clone でも、プロジェクトのディレクトリで Claude Code を起動すると、ワークスペースの hook は動かず、ccnavi の見え方は単体 clone に近くなる。この形も対象に含む。前提は要件書の「想定する使い方」にある。ルートの決め方が食い違うなど、まだ直っていない点は [11.14](multi-repo/standalone-clone-gaps.md) にある。

### 11.1 何を解くか

- 相対パスの解決、実行後チェック、ワークツリーの列挙を、ワークスペースルートだけでなくプロジェクトごとの git でも行う
- プロジェクトごとのルールを置く場所を持つ。プロジェクトが自前で ccnavi を導入していれば、プロジェクトも自分の `.claude/` を持つ。ワークスペースから起動したときに、そのプロジェクトの `.claude/skills` が読み込まれるかは、`projects/` がワークスペースの git で無視されているかで変わる。無視されている標準の構成では読み込まれない（11.13）
- どのツリーにも適用したい deny（hook の保護、認証情報、main への直書き）を共通レイヤーに 1 度書けば全プロジェクトに適用される
- フェーズ定義の `scope` はワークツリーのレイアウトに縛られるので、フェーズ定義をツリーごとに持つ（配点はツリーごとにも持てる）

レイヤーは和であり、後ろのレイヤーが前のレイヤーを上書きすることはできない。プロジェクトのレイヤーは、そこを編集するエージェントが書きうる。上書きできると、共通レイヤーの deny を消す手段になる。

## 詳細

| 節 | ファイル | 何が書いてあるか |
|---|---|---|
| 11.2 | [multi-repo/places.md](multi-repo/places.md) | ワークスペースとプロジェクトで何をどこに置くか |
| 11.3 | [multi-repo/trees.md](multi-repo/trees.md) | 3 種のツリーと所属の決め方 |
| 11.4 | [multi-repo/rules.md](multi-repo/rules.md) | ルールとリスクの配点は共通レイヤーとツリーのレイヤーの和、フェーズ定義は共通レイヤーと親の `project:` が指すレイヤーの和 |
| 11.5 | [multi-repo/tickets.md](multi-repo/tickets.md) | チケットのプロジェクトは提案を置いた場所で決まる |
| 11.6 | [multi-repo/protection.md](multi-repo/protection.md) | 複数リポジトリでのコアファイル |
| 11.7 | [multi-repo/post-check.md](multi-repo/post-check.md) | ツリーごとの実行後チェック |
| 11.8 | [multi-repo/scripts.md](multi-repo/scripts.md) | 保護済みスクリプトの案内と `{root}` |
| 11.9 | [multi-repo/diagnostics.md](multi-repo/diagnostics.md) | レイヤーごとの診断と記録 |
| 11.10 | [multi-repo/to-measure.md](multi-repo/to-measure.md) | 実装時に実測するもの |
| 11.11 | [multi-repo/out-of-scope.md](multi-repo/out-of-scope.md) | 見ないもの、入れないもの |
| 11.12 | [multi-repo/distribute-common.md](multi-repo/distribute-common.md) | 共通レイヤーをプロジェクトの `.ccnavi/config/common/` へミラーする。ずれの確認と同期は `user mirror` |
| 11.13 | [multi-repo/skills.md](multi-repo/skills.md) | プロジェクトのスキルの置き場と目録 |
| 11.14 | [multi-repo/standalone-clone-gaps.md](multi-repo/standalone-clone-gaps.md) | 単体 clone で不都合な処理の一覧（調査の記録。直し方は未決） |
