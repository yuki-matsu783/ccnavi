---
title: ccnavi 承認ボードの構成
type: design
description: Chrome 拡張 ccnavi 承認ボードのディレクトリごとの役目
tags: [design-doc, extension, approval]
keywords: [設計, 構成, service worker, Pyodide, Web Worker, hosts.json, ccnavi_chrome.py, core, MEMFS, React, controller]
---

# 構成

入口: [ccnavi 承認ボードの設計](../design.md)

| 場所 | 役目 |
|---|---|
| `src/background/` | service worker。PAT を持ち、ホストの API を呼ぶのはここだけ（画面には PAT を渡さない）。PAT の期限のバッジ |
| `src/board/` | ボード（拡張のページ）。Worker を起動し、読んだ中身を Python に渡し、答えを描く。状態と副作用（読み直し・書く操作・確認が必要な状態・PAT の期限の帯・自動の読み直し）は `controller.ts` が持つ（`chrome`・`window` には、入口の `main.tsx` が渡す外界 `BoardEnv` を通してだけ触る）。描くのは React（`App.tsx` が `useSyncExternalStore` で controller を読み、リポジトリの欄 `Repo.tsx`、親子のチケット `Family.tsx`、レビュー `Review.tsx`、「始める」`Start.tsx`、要確認 `Attention.tsx` を並べる）。Markdown の本文は `Markdown.tsx` が消毒した断片（`src/core/sanitize.ts`）を ref で差し込む（HTML の文字列には戻さない）。CSS の入口は `style.css`（共通の部品の CSS と、部品 1 つに 1 本のボードの CSS（`App.css`・`Repo.css` など）を読み、ビルドが `board.css` に組む） |
| `src/options/` | 設定画面。通信先（追加すると許可を求め、削除すると PAT と許可を削除する。`Hosts.tsx`）、リポジトリ（統合先の名前・直近の日数・指定のブランチ）と PAT。React で組み、見た目は VS Code 拡張と共通の部品（`extensions/shared` の `@ccnavi/shared`。ボタン・骨組み・一覧の CSS と、Claude の配色で `--vscode-*` を定義する `browser-theme.css`）を使う。CSS の入口は `style.css`（ビルドが `options.css` に組む） |
| `src/worker/` | Pyodide を動かす Web Worker |
| `src/core/` | 画面に依らない部品（通信先と manifest、設定画面で足すホスト（`custom-hosts.ts`）、GitHub の読み書き（`github.ts`）、GitLab の読み書き（`gitlab.ts`）、「始める」（`start.ts`）、画面と service worker の約束、読み取りの流れ、レビュー済みの材料の読み（`reviewed.ts`）、承認・レビュー済みの流れ（`write.ts`）、PAT の期限、Markdown の消毒） |
| `py/ccnavi_chrome.py` | Pyodide の上の入口。MEMFS に仮のツリーを組んで今の ccnavi を呼ぶ。判定のコア（`ccnavi.tickets.core`）の `plan`・`confirm` の答え（書くもの）を拡張が 1 コミットにする |
| `hosts.json` | 焼き込む通信先（組み込み）。組織ごとのビルドはこれを替える。設定画面で足したホストは `chrome.storage.local` の `customHosts` に持つ（`custom-hosts.ts`） |
