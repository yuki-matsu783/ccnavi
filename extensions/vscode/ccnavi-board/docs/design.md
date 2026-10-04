---
title: ccnavi ボードの設計
type: design
description: VS Code 拡張 ccnavi ボードの作りの概要と、構成・画面の段取りの詳細への案内
tags: [design-doc, extension, board]
keywords: [設計, VS Code 拡張, 構成, 拡張ホスト, webview, React, core, screen-host, テストの ID]
---

# ccnavi ボードの設計

VS Code 拡張「ccnavi ボード」の作りを書く。拡張ホスト（`src/`）が ccnavi の実行ファイルを子プロセスで呼んで中身を組み、画面（`src/webview/`、React）がそれを描く。何をするかは [requirements.md](requirements.md)、使い方と組み立ては [README](../README.md) にある。

拡張が呼ぶ実行ファイルのコマンドと読む JSON の形、互換の版は本体側の取り決めで、本体の [要件書](../../../../docs/requirements.md) と [設計](../../../../docs/design.md) に書く。

## 詳細

| 読むとき | 開くファイル |
|---|---|
| ファイルの置き場と役目（`src/`・`media/`・`test/`・`scripts/`）、テストの ID の決まり | [design/structure.md](design/structure.md) |
| 承認のオーバーレイの遷移、見た目の実装、ボードや画面に機能を足すときに触る場所、画面に中身を渡す段取り | [design/screens.md](design/screens.md) |

## 拡張ホストと画面の境目

`core/` は `vscode` を import しない。ここだけを `node --test` で試す。

`webview/` は反対に、DOM だけを触り、`vscode` も `node` も import しない。拡張ホストと共有するのは、`core/` のうち import で辿れるぶん（`board.ts`・`board-view.ts` など、判定も I/O もしないもの）だけ。画面はどれも React で（サイドパネルの 5 画面と、ボードから開くフロー編集）、拡張ホストは中身（`BoardData` / `ProjectsData` / `RiskData` / `PhasesData` / `RulesData` / `FlowData`）を渡すだけで、DOM を組み立てない。
