---
title: ccnavi ボードの要件
type: requirements
description: VS Code 拡張 ccnavi ボードが外から見てどうふるまうかの概要と、画面ごとの詳細への案内
tags: [design-doc, extension, board]
keywords: [要件, VS Code 拡張, ボード, チケット管理, ルール管理, リスク管理, フェーズ管理, プロジェクト管理, フロー編集, サイドパネル, コマンド]
---

# ccnavi ボードの要件

VS Code 拡張「ccnavi ボード」が外から見てどうふるまうかを書く。入口はサイドパネルとコマンドパレットで、チケット管理（ボード）・ルール管理・リスク管理・フェーズ管理・プロジェクト管理の 5 画面と、ボードの子のカードから開くフロー編集画面がある。使い方と組み立ては [README](../README.md)、作りは [design.md](design.md) にある。

拡張と ccnavi 本体の間の取り決め（拡張が呼ぶコマンド、読む JSON の形、互換の版）は本体側に書く。本体の [要件書](../../../../docs/requirements.md) と [設計](../../../../docs/ccnavi.md) を見る。

## 詳細

| 読むとき | 開くファイル |
|---|---|
| サイドパネルの入口、コマンドパレットのコマンド、タブの出方、見た目（配色）の切り替え | [requirements/commands.md](requirements/commands.md) |
| チケット管理画面（ボード）。列・カード・バッジ・履歴・絞り込み・承認とレビューのオーバーレイ・自動の読み直し | [requirements/board.md](requirements/board.md) |
| フロー編集画面（子チケットのフローを図で書く） | [requirements/flow.md](requirements/flow.md) |
| プロジェクト管理画面（`projects/` の一覧と clone・設定の雛形） | [requirements/projects.md](requirements/projects.md) |
| ルール管理画面（`rules.yml` の編集・判定を試す・hook の一覧） | [requirements/rules.md](requirements/rules.md) |
| リスク管理画面（`risks.yml` の配点の編集） | [requirements/risk.md](requirements/risk.md) |
| フェーズ管理画面（`phases.yml` の種類の編集と図） | [requirements/phases.md](requirements/phases.md) |

## 用語と参照

共通の設定（`.ccnavi/common/`）・ワークスペースの設定・プロジェクトの設定を、実行ファイルや ccnavi 本体の docs では「層（layer）」と呼ぶ（共通層・自身の層（`self`）・プロジェクトの層）。

- 出力の形: ccnavi の README「ボードの JSON」「試験の JSON」
- 設計: ccnavi.md 10、要求 REQ-DIA-02 / REQ-DIA-03 / REQ-DIA-06
