---
type: design
title: 11.2 置き場
description: ワークスペースとプロジェクトで何をどこに置くか。ルールとリスクとフェーズ定義は common と config の両方。ファイルが無いのは設定が無い正常
tags: [design-doc, projects]
keywords: [置き場, ワークスペース, プロジェクト, .ccnavi/config/project, .ccnavi/config/common, ミラー, 単体 clone, 無い = 空]
---

[設計書の入口](../../20_design.md) > [11. 複数のリポジトリ](../multi-repo.md)

### 11.2 置き場

| 何 | 場所 | git |
|---|---|---|
| hook の登録、実行ファイル、保護済みスクリプト、スキル、CLAUDE.md | ワークスペースルート | ワークスペース |
| 共通レイヤーのルール / フェーズ定義 / リスクの配点 | `.ccnavi/config/common/{rules,phases,risks}.yml`（置き場は固定） | ワークスペース |
| ルールの期待する判定 | `.ccnavi/config/common/rule-expectations.yml` | ワークスペース |
| 共通の作業・レビュー計画のテンプレート | `.ccnavi/config/common/templates/<名前>.yml`（置き場は固定。9.15。`*/.ccnavi/*` の一般の保護で守る） | ワークスペース |
| 共通レイヤーのミラー | `projects/<名前>/.ccnavi/config/common/`（着手済みの親があるとき、ユーザの発言でワークスペースルートの `.ccnavi/config/common/` から配る。11.12） | プロジェクト |
| ワークスペース自身のレイヤーの 3 本 | `<ワークスペースルート>/.ccnavi/config/project/{rules,phases,risks}.yml` | ワークスペース |
| プロジェクトのレイヤーの 3 本 | `projects/<名前>/.ccnavi/config/project/{rules,phases,risks}.yml` | プロジェクト |
| 自身・プロジェクトのレイヤーのテンプレート | `<ツリー>/.ccnavi/config/project/templates/<名前>.yml`（9.15。共通のテンプレートと同じく一般の保護で守る） | そのツリーのリポジトリ |
| 固有スクリプト（配点の `script:` が指す先） | 共通レイヤーとそのミラーは `.ccnavi/config/common/scripts/`。自身のレイヤーとプロジェクトのレイヤーは、それぞれの `.ccnavi/scripts/` | レイヤーと同じ |
| プロジェクト | `projects/<名前>/`（`projects/` は固定。ワークスペースルートからの相対）。直下で `.git` を持つディレクトリだけ。1 段に固定 | ワークスペースでは無視。プロジェクト自身の git |
| ワークツリー | `.claude/worktrees/<識別子>/`。ワークスペースかプロジェクトから切る | 管理外 |
| 提案 | そのツリーの `wip/proposals/<状態>/<識別子>.md`。プロジェクト向けはそのプロジェクトの git が持つ | そのツリーのリポジトリ |
| テンプレートの下書き | そのツリーの `wip/handoff/` の下の、取り込み先と同じ相対パス（ユーザが `ccnavi user handoff` で取り込む。9.15、9.19） | そのツリーのリポジトリ |
| 承認済みチケット（閉じたものを含む）、フェーズのマーカー | 親チケットのツリーの `.ccnavi/approved/`（9.2）。承認済みチケットに `project:` が入る | そのツリーのリポジトリ |
| 記録、state、セッションの状態 | ワークスペースの `logs/`（`logs/decisions.jsonl`、`logs/state/`、`logs/session/`） | 管理外 |
| git のラッパースクリプトの記録 | ワークスペースの `logs/<プロジェクト>/`。ワークスペース自身は `logs/` | 管理外 |

`.ccnavi/` は ccnavi ディレクトリの固定の名前。この表の置き場はどれも固定で、環境変数では動かない。

レイヤーは 3 種で、形は同じ。共通レイヤーはどのツリーにも適用され、ワークスペースルートの `.ccnavi/config/common/` に置く。
ユーザが持つ設定は ccnavi ディレクトリの下、記録と state は `logs/`、`.claude/` には Claude Code 自身のものだけを置く。
共通レイヤーのファイルと期待する判定も、名指しのツールからは組み込みの deny（`*/.ccnavi/*`）が止める。
共通レイヤーの置き場は固定で、置き場を動かすフラグと環境変数の扱いは設計の startup-config（4 章）にある。
自身のレイヤーとプロジェクトのレイヤーはそのツリーにだけ適用され、どちらも git プロジェクトルートの下の `.ccnavi/config/project/` に置く。
ワークスペース自身にレイヤーを分けるのは、ワークスペースのフェーズ定義の一部（`scope` に `build.py` や `extensions/*` を持つものなど）がそのレイアウトにしか合わないから。

**何をどこに置けるか。** ルールとリスクの配点は、共通（`.ccnavi/config/common/`）と config（ワークスペース自身かプロジェクトの `.ccnavi/config/project/`）の
両方に置け、効くのは和（合成は 11.4）。フェーズ定義（`phases.yml`）も同じで、どのプロジェクトでも同じ構成を前提にできる定義は共通に、
レイアウトに依存する定義はワークスペース自身のレイヤー `<ワークスペースルート>/.ccnavi/config/project/phases.yml` か、
そのプロジェクトの `.ccnavi/config/project/phases.yml` に置く。共通に足すレイヤーは親の `project:` が決める（11.4.1）。
導入スクリプトのひな形は、共通レイヤーにルールと配点とどのプロジェクトでも使えるフェーズ定義を置く。レイアウトに依存するフェーズ定義（自身のレイヤーの `phases.yml`）は配らず、ユーザが書く。既にあれば触らない（11.9）。
自身のレイヤーの `.ccnavi/config/project/` もユーザの持ち物で、エージェントの Write / Edit は通らない（チケットの `allow` に `.ccnavi/*` があっても組み込みの deny が優先される）。

**共通レイヤーのミラー。** プロジェクトの `.ccnavi/config/common/` は、ワークスペースルートの共通レイヤーのコピー（11.12）。
ワークスペースの中で共通レイヤーとして読むのは、ワークスペースルートの `.ccnavi/config/common/` だけ。
プロジェクトだけを clone したときは、そのプロジェクトがルートになるので、`.ccnavi/config/common/`（ミラー）が共通レイヤー、`.ccnavi/config/project/` が自身のレイヤーとして読まれる。
どちらの場合も「共通 + 1 レイヤー」の和になる（11.4）。

ccnavi ディレクトリは git プロジェクトルートの直下の `.ccnavi` で、自身のレイヤーとプロジェクトのレイヤーに同じ名前が使われる。
`config/` と `scripts/` と 3 本のファイル名は固定。プロジェクトの `config/project/` と混ぜないのは、組み込みの deny を `*/.ccnavi/*` の 1 行で済ませるため。

読むのは常に元リポジトリに checkout されている版（ブランチは問わない。REQ-MLT-04）。

**ファイルが無いのは「設定が無い」正常。** common だけ、config だけ、両方無し、のどれでも動く。ルール・リスクの配点・フェーズ定義のすべてで
「無い = 空」に揃える。不正なとき（読めない・error）だけ、そのファイルは空として扱い、記録の `fallback` にレイヤーの名前を残し、`--lint` が error で言う。
土台の deny（取り返しの付かない操作の止め。`rm -rf`・`git push`・`git reset --hard`・認証情報の置き場。5.6）は、共通レイヤーと config の有無・状態によらず常に有効で、common と config のルールは、その上に足す。
土台の deny と、`.ccnavi/` などを守る `builtin:guard-*`（Write / Edit・シェルの設定保護。8.2）は別で、`add_rules` の分は組み込みの既定を使っている間（共通レイヤーのルールが不正な間）は外す。名前で書くツールの 4 本（`builtin:guard-hooks`・`-settings`・`-scripts`・`-ccnavi-config`）は、読めない間も外さない。
common と config の両方が無ければ、ルールは組み込みの既定だけで判定する。

共通レイヤーのルール（`.ccnavi/config/common/rules.yml`）自身が破損して読めないときは、組み込みの既定を使い（REQ-PRE-06）、自身のレイヤーとプロジェクトのレイヤーは足さない。
共通レイヤーのルールが「無い」ときは、これと違って組み込みの既定へは戻らず、空として config のレイヤーだけを土台の deny の上に足す。

プロジェクトの git に入るのは、ccnavi ディレクトリ（`config/project/` の 3 本、ミラーの `config/common/`、配点のスクリプト）と、提案・承認済みチケット・フェーズのマーカー。
A が承認して B の機械で作業する流れでは、これらを共有する（9.2）。feature ブランチに乗り、squash merge で 1 コミットにまとめられる。
git のラッパースクリプトの記録（`logs/`）はワークスペース側に残す。

プロジェクトが無いとき（`projects/` が無いか空）は、共通レイヤーと自身のレイヤーだけで判定し、提案・承認済みチケット・フェーズのマーカー・記録は
ワークスペースの置き場だけに書く（REQ-MLT-15）。
