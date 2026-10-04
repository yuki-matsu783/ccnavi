---
title: ccnavi 承認ボードの構成
type: design
description: Chrome 拡張 ccnavi 承認ボードのディレクトリごとの役目
tags: [design-doc, extension, approval]
keywords: [設計, 構成, service worker, Pyodide, Web Worker, hosts.json, ccnavi_chrome.py, core, MEMFS]
---

# 構成

入口: [ccnavi 承認ボードの設計](../design.md)

| 場所 | 役目 |
|---|---|
| `src/background/` | service worker。PAT を持ち、ホストの API を呼ぶのはここだけ（画面には PAT を渡さない）。PAT の期限のバッジ |
| `src/board/` | ボード（拡張のページ）。Worker を起動し、読んだ中身を Python に渡し、答えを描く |
| `src/options/` | 設定画面。リポジトリ（統合先の名前・直近の日数・指定のブランチ）と PAT |
| `src/worker/` | Pyodide を動かす Web Worker |
| `src/core/` | 画面に依らない部品（通信先と manifest、GitHub の読み書き（`github.ts`）、GitLab の読み書き（`gitlab.ts`）、「始める」（`start.ts`）、画面と service worker の約束、読み取りの流れ、レビュー済みの材料の読み（`reviewed.ts`）、承認・取り下げ・レビュー済みの流れ（`write.ts`）、PAT の期限、Markdown の消毒、描画） |
| `py/ccnavi_chrome.py` | Pyodide の上の入口。MEMFS に仮のツリーと取り込み状態に当たるものを組んで今の ccnavi を呼ぶ。判定のコア（`ccnavi.hook.core`）の `plan`・`withdraw`・`confirm` の答え（書くもの）を拡張が 1 コミットにする |
| `hosts.json` | 焼き込む通信先。組織ごとのビルドはこれを替える |
