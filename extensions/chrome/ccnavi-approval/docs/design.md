---
title: ccnavi 承認ボードの設計
type: design
description: Chrome 拡張 ccnavi 承認ボードの作りの概要と、詳細への案内
tags: [design-doc, extension, approval]
keywords: [設計, 構成, Chrome 拡張, service worker, Pyodide, Web Worker]
---

# ccnavi 承認ボードの設計

Chrome 拡張「ccnavi 承認ボード」の作りを書く。PAT を持ってホストの API を呼ぶ service worker、ボードと設定画面、同梱の ccnavi を Pyodide で動かす Web Worker に分かれる。何をするかは [requirements.md](requirements.md)、組み立てと試験は [README](../README.md) にある。

## 詳細

| 読むとき | 開くファイル |
|---|---|
| ディレクトリごとの役目（service worker・ボード・設定画面・Worker・core・Pyodide の入口・通信先） | [design/components.md](design/components.md) |
