---
title: 実機確認（VS Code 拡張の画面）を頼まれたとき
type: guide
description: ユーザに実機確認を頼まれたとき、エージェントが Playwright と code-server で自分で確かめる決まり。道具を入れてよい範囲、単体の Chromium との使い分け、報告の書き方
tags: [testing]
keywords: [実機確認, 実画面, 画面確認, Playwright, playwright-core, code-server, Chromium, VS Code, 拡張, ccnavi-board, 検証, 報告]
---

# 実機確認（VS Code 拡張の画面）を頼まれたとき

ユーザに「実機確認して」と頼まれたら、エージェントは Playwright と code-server で、本物の VS Code の画面を動かして確かめる。
ユーザの手元での確認を頼む前に、まず自分で確かめる。

手順（準備、詰まりどころ、撮り方）は、拡張の設計書 [real-screen-check.md](../../extensions/vscode/ccnavi-board/docs/design/real-screen-check.md)。ここには、頼まれたときの決まりだけを書く。

## 決まり

- 先に、環境に code-server・playwright・Chromium があるかを確かめる（手順書の 1.0）。あれば、それを使う。入れ直さない
- 無いものは、入れてよいとユーザが許可している。ただし入れる前に、グローバルに入れるかローカルに入れるかを、ユーザに確かめる。選びは手順書の 1.0 の 2 通り（グローバルは `pnpm add -g`、ローカルはリポジトリの追跡されるファイルの外の `scratchpad/` など）。`pnpm setup` はシェルの設定ファイルを書き換えるので、エージェントは打たず、ユーザに頼む。入れたものと後片付けは、報告に書く
- 撮影のスクリプトは、拡張の `node_modules/` の `playwright-core` を使う。グローバルの `playwright` は、ブラウザの取得用。Chromium は実行ファイルを `executablePath` で明示する（Playwright が探す版と、入っている版が食い違うことがあるため）
- 単体の Chromium で webview だけを開く方法（`render-board.js`、ビルド済みの HTML を `acquireVsCodeApi` の代わりを入れて開く方法）は、CSS の効き方などの部分的な確認に使う。VS Code の通知・コマンド・テーマの色変数が絡む点は、code-server の画面で確かめる
- 確かめた方法（code-server の画面、単体の Chromium、自動テスト、コード読みのどれか）を、項目ごとに報告へ書く。確かめられなかった点と理由は分けて書き、単体の Chromium やコード読みの結果を「実機で確認済み」と書かない
- 不具合を見つけたら、再現手順と場所を書く。実機確認の中では直さない
- 起動した code-server などのプロセスは、終わったら止める。一時ファイルは `scratchpad/` に置き、終わったら消す
