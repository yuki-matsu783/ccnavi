---
title: ccnavi ボードの画面の作り
type: design
description: 承認のオーバーレイの遷移、見た目の実装、機能や画面を足すときに触る場所と、画面に中身を渡す段取り
tags: [design-doc, extension, board]
keywords: [設計, 画面, approval-machine, 見た目, ScreenHost, screenHost, retainedHost, retainContextWhenHidden, screens.ts, 契約, BoardMessage]
---

# 画面の作り

## 承認のオーバーレイ

この画面の遷移は `core/approval-machine.ts` の 1 か所にまとめてある。散らばっていると必要なガード条件を書き落とすため。「この状態ではこれを受けない」というガード条件は、そのファイルの先頭の表にある。`board-panel.ts` は入力を渡し、返ってきた「やること」を実行するだけ。単体テストは CB-T169〜181（CB-T181 は、ガード条件を 1 つ消すとテストが落ちることを確かめる変異テスト）。

## 見た目

実装は body のクラスの下でテーマ変数を上書きするだけ（配色は `src/webview/styles/appearance.css`、切り替えは `src/core/appearance.ts` と `src/webview/appearance.ts`）。配色もほかの中身と同じ段取り（`ScreenHost`）で送る。組み上がっていない画面と捨てられた画面には送らない。保持しない画面には作り直されたあと `ready` で、保持する画面には表に戻ったところで送り直す。ハイコントラストのテーマでは Claude の配色を適用しない。設定を書く先は、いま値が定義されている場所（フォルダ → ワークスペース → ユーザ）。

## 機能や画面を足すとき

**ボードに機能を足すときに触る場所。**

| 足すもの | 触る場所 |
|---|---|
| カードに出す項目 | `core/model.ts`（実行ファイルとの契約。JSON を増やすなら Python 側の `tests/ticket/test_board.py` が書き出す見本 `test/fixtures/board.json` も）→ `core/board.ts`（出すか出さないかの判断）→ `webview/board/text.ts`（出す言葉）→ `webview/board/Card.tsx`（見せ方）→ `webview/board/Card.css`（CSS）→ テスト（`test/board/model.test.ts`・`board.test.ts`・`render.dom.test.ts`） |
| ユーザが押せる操作 | `core/board-view.ts` の `BoardMessage` → 画面のボタン → `board-panel.ts` の `KNOWN`（形の確認の一覧）と `asMessage`（形の確認）と `handleMessage`（処理）。`KNOWN` か処理を書き忘れると型が合わなくなる（どちらも型で網羅を強制してある） |
| 画面が覚えるもの | `webview/board/state.ts`（形と既定）→ `App.tsx`（読み書き）→ `test/board/board.dom.test.ts` |

**画面を 1 つ足すとき、使い回せるもの。** `webviewScript` / `webviewStyle`（画面の名前で読む。拡張子は関数の側が付ける）、`poster<M>()`、`core/screen-host.ts`（`screenHost` / `retainedHost` と `embedJson`）、`webview/styles/`（`page.css`・`list.css`）、`webview/vscode.ts`・`webview/initial.ts`・`webview/appearance.ts`、テストの共通部品（`test/helpers/dom.ts`・`test/helpers/bundle.ts`）。

画面ごとに要るのは、契約（`*-view.ts`）・入れ物を組む関数・画面の CSS（`webview/<名前>/style.css` と部品ごとの CSS）・`ready` を受けて渡し直す数行・テストの入口（`test/helpers/<名前>.ts`）と、`test/shared/style.test.ts` の `reactPages` への 1 行（足し忘れは CB-T127 が止める）。

**実行ファイルへの往復がある画面（ルール管理）も、やり取りの形は同じ。** 画面は編集中の中身を付けて頼み（`judge` / `samples`）、拡張ホストが実行ファイルの結果をそのまま返す（`judged` / `sampled`）。画面は判定の理屈を持たない。VS Code のダイアログが要る欄（`pickFile` → `picked`）も同じで、行を名指しする鍵は画面が渡し、拡張ホストは読まずにそのまま返す。

**画面を 1 つ足すときに触る場所。**

| 足すもの | 触る場所 |
|---|---|
| バンドルと回り方 | **どちらも直さない。** パスの付け方の約束で決まる。画面は `src/webview/<名前>/main.tsx`、バンドルした出口は `out/webview/<名前>.js`、CSS は `src/webview/<名前>/style.css` → `out/webview/<名前>.css`、テストの入口は `test/helpers/<名前>.ts`、グループは `test/<名前>/`。`bundle-webview.js` と `test-groups.js` が同じ見つけ方でディスクから拾う |
| 回すものの決まり方 | 触ったファイルがどの画面のバンドルに入るかを閉包で見る（スクリプトは `main.tsx` から `import`、CSS は `style.css` から `@import`。置き場のパスでは決めない）。どの画面にも入らないもの（`webview/vscode.ts`・`webview/styles/` など）は全画面に関わると見る。画面と同じ名前のグループは、`test/helpers/<名前>.ts` を作り忘れても必ず回る |
| 契約に置く型 | 画面に渡す形（`ProjectsPage` のような）は契約の側（`*-view.ts`）に置く（`core/` の判定のファイルに置くと、`node:path` を読むファイルを辿って画面の型検査が落ちる） |

**段取りは 2 系統ある。パネルの `retainContextWhenHidden` と対で選ぶ**。

| パネル | 作るもの | 裏に回ったとき |
|---|---|---|
| `retainContextWhenHidden: false`（ボード・プロジェクト管理） | `screenHost(surface, render)` | 画面は捨てられる。入れ物ごと入れ直す |
| `retainContextWhenHidden: true`（ルール管理・リスク管理・フェーズ管理） | `retainedHost(surface, render)` | 画面は生きている。何もしない |

返る形（`ScreenHost<D>`）と呼び方（`send` / `post` / `ready` / `hidden`）は同じ。取り違えても型では止まらない（保持する画面に `screenHost` を使うと打ちかけが消え、逆だと中身が古いまま更新されない）。保持する画面のパネルは、`ScreenHost` を返す関数（`rulesHost`・`riskHost`・`phasesHost`・`flowHost`）で `retainedHost` の形に合わせる。この関数は `panel.options.retainContextWhenHidden` を見て、偽なら診断ログに残す。

保持する画面には、表に戻ったところで知らせ（`lock`・`changed`）と見た目を送り直す。裏の画面に届くかどうかについて、VS Code の型定義の記述が食い違っているため。中身（`data`）は送り直さない。

保持する画面に中身（`data`）を渡すのは、画面の編集を捨ててよいときだけ（ユーザが「更新」を押した、保存や作成が通って中身が入れ替わった）。監視がファイルの変化に気づいても中身は渡さず、帯（`changed`）を出してユーザに決めてもらう。

画面（`*-panel.ts`）どうしは互いを import しない。プロジェクト管理画面からチケット管理やルール管理を開くような導線は、相手のパネルの関数を直に呼ばず、`core/screens.ts` の帳面（`screens().board(...)`）を通す。帳面に入口を載せるのは `extension.ts` だけで、載せ方を見る場所もそこ 1 か所。
