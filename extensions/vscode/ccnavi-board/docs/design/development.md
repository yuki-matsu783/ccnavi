---
title: ccnavi ボードの開発
type: design
description: VS Code 拡張 ccnavi ボードのテストの分け方（グループ・実行の流れ）、tsconfig の 3 本、依存の理由、生成物の消し方、画面を VS Code なしで Chromium に描かせる開発用スクリプト render-board.js の使い方と前提
tags: [design-doc, extension, testing]
keywords: [開発, テスト, test-groups, tsconfig, バンドル, esbuild, 依存, pnpm run clean, happy-dom, グループ, render-board, 画面確認, Chromium, playwright-core, PLAYWRIGHT_BROWSERS_PATH, devtools, PNG, 終了コード]
---

[設計の入口に戻る](../design.md)

テストの入口は `scripts/test-groups.js` の 1 本で、`pnpm test` とその仲間はすべてここを通る。回すグループを決め、`clean-out` → `tsc -p tsconfig.test.json` →（要るときだけ画面の型の検査）→ 画面のバンドル → `node --test <出来上がったファイル>` の順に進む。グループをいくつ選んでもコンパイルは 1 回で、`src/` と `test/` を全部見るので、絞っても短くならない（3〜4 秒）。短くなるのは実行（全部で約 4.6 秒）と、画面の型検査を省けるぶん（1.5 秒）。

バンドルは、画面の型を見ないときでも必ず回す（`clean-out.js` が `out/webview` を消すため）。バンドルするのはスクリプト（`<名前>.js`）と CSS（`<名前>.css`）の 2 本で、CSS は小さくしない。小さくしても減るのは数 KB なので、開発者ツールで読めるほうを選ぶ。

どのグループがどのファイルを読むかは、テストの `import`（CSS は `@import`）を辿って決める。辿れないものだけはパスで決める。その決まりは `test-groups.js` の先頭にまとめてあり、固定データ `test/fixtures/` は全部のグループを回す。画面 `src/webview/` は、辿れたぶんとバンドルしたものを読むグループを回す。組み立てに使う `package.json`・`tsconfig*.json`・`scripts/`・`pnpm-lock.yaml` は全部を回し（開発用の画面確認ツール `scripts/render-board.js` と `scripts/lib/` だけは `devtools` グループ）、文書 `*.md` と画像 `media/` は何も回さない。`out/`・`node_modules/` は数えない。テストが 1 つも読まないファイルは `tsc` だけ通す。`test/<グループ>/` を増やすときに直すのは、`package.json` の行 1 本だけ。

`test/shared/` は 9 画面を比べるので、バンドルした画面を読む。そのため `shared` を含む計画には必ず画面の型検査とバンドルが付き、`src/core/` の大半は `shared` を含む。絞っても減らせるのは 1 回あたり 2.5〜4.5 秒です。

ワークスペース側の hook（`.claude/hooks/mark-ext.sh` と `test-ext.sh`）は `pnpm test:for` と同じ仕組みで動く。拡張のファイルを触ったターンの終わりに、関わるグループだけが回る。

テストは画面の領域ごとのディレクトリに分けてあり、一部を直したときはその領域だけを流せる。組み立てと HTML の文字列を見る単体テスト（`*.test.ts`）と、画面を happy-dom で実際に動かすテスト（`*.dom.test.ts`）を同じディレクトリに置く。ボードの画面は React なので、何を描くかも動かして見る（`board/render.dom.test.ts`）。React は押した直後には描き直さないので、操作のあとは `await page.settle()` を挟んでから見る。

`pnpm test` と `pnpm run compile` は、tsc の出力（`out/src` と `out/test`）を先に消す。esbuild がバンドルした `out/extension.js` は消さないので、package の compile → test の順でも入口は残る。出荷物の型検査は `tsconfig.json`（`src/webview` を除く。lib は DOM 無し、拾う型は node と vscode だけ）と `tsconfig.webview.json`（`src/webview` だけ。lib に DOM、JSX は `react-jsx`、`noEmit`。JS を出すのは esbuild のほう）の 2 本に分かれている。テストは `tsconfig.test.json`（`skipLibCheck`。happy-dom の型定義が `@types/node` 22 と合わないため）。

実行時の依存は `yaml`（コメントを残して書き戻すため）の 1 つ。開発時の依存に happy-dom と react / react-dom を足してある（画面はバンドルして配るので、実行時の依存にはしない）。vsix には `node_modules/` を入れず、`scripts/bundle.js`（esbuild）が本体ごと `out/extension.js` にバンドルする。テストはバンドルする前の `out/src/` を使い、画面だけはバンドルした `out/webview/<名前>.js` を使う。`out/webview/` は vsix に入る。拡張が起動時に読んで `<script nonce>` に流し込むので、Webview の `localResourceRoots` は空のままでよく、CSP も nonce だけで済む。

テストの置き場に `*.e2e.ts` があれば、`node --test` に渡さず、`test-groups.js` が単体テストのあとに別に起こす。実機のブラウザが要るもので、環境が無いときにスキップして成功に数えないよう、終了コード（0 通った / 1 失敗 / 3 環境が揃っていない）をそのまま返すため。

生成物を消すときは `pnpm run clean`（`scripts/clean.js`）。消すのは `node_modules/` と `out/` の 2 つだけで、引数は取らない（`rm -rf` はルール `recursive-delete` が止める）。ワークツリー（`.claude/worktrees/<名前>`）でこの拡張を組み立てたら、`git worktree remove` の前にこれを走らせる。走らせないと、`node_modules/.pnpm/` が残って削除が途中で止まることがある。

## 画面を VS Code なしで描く（render-board.js）

`scripts/render-board.js`（CommonJS）は、ボードの画面を VS Code なしで Chromium に描かせ、PNG とテキストに残す開発用のツール。
VS Code を持たない環境（クラウドのセッション、CI）や、実リポジトリのチケットの状態を画面で見たいときに使う。
拡張の配布物（vsix）と ccnavi の配布物には含めない（`package.sh` の対象外）。

```sh
pnpm install && pnpm run compile
node scripts/render-board.js --fixture test/fixtures/board.json --out "${TMPDIR:-/tmp}/ccnavi-board"
node scripts/render-board.js --repo <ccnavi を配布済みのリポジトリ> --out <リポジトリの外のディレクトリ>
```

| 引数 | 内容 |
|---|---|
| `--repo <dir>` | ccnavi を配布済みのリポジトリ。拡張と同じ `loadBoard`（ランチャーの探し方・`--root`・60 秒の期限・失敗の文面）で中身を得る。リポジトリの中身は読むだけ |
| `--fixture <file>` | BoardJson のファイル（`test/fixtures/board.json` など）。`--repo` とどちらか一方 |
| `--out <dir>` | 出力ディレクトリ（無ければ作り、既存の `board.*` は上書きする）。`--repo` と同じか下は不可（実体にして、大文字小文字と区切りを区別せず比べる） |
| `--appearance` | `vscode` / `claude-light` / `claude-dark`（値は拡張の `APPEARANCES` で確かめる）。既定は `claude-light`。`vscode` は VS Code のテーマ変数（`--vscode-*`）に頼るので、Chromium 単体では色が欠ける |
| `--width` | ページの幅。320〜3000 の整数（既定 1400）。高さは全体が入るまで伸ばす |

出力は `<out>/` の 4 つ。

| ファイル | 内容 |
|---|---|
| `board.png` | 画面全体（`fullPage`） |
| `board.txt` | `body` の見えているテキスト |
| `board.html` | 入れ物の HTML（`renderBoardPage` の出力） |
| `board.log` | ページの記録: コンソール、ページエラー、CSP 違反、画面が拡張ホストへ送ったメッセージ。アプリのログではない |

終了コードは、`scripts/test-groups.js` の `NOT_READY` と同じ意味に揃えてある。

| コード | 意味 |
|---|---|
| 0 | 描けて、失敗の条件に当たらない |
| 1 | 描けなかった、または失敗の条件に当たった（`loadBoard` / `parseBoardJson` の失敗、ページエラー、コンソールの `error`（`favicon` の取得失敗を除く）、CSP 違反、描画の完了待ちが 15 秒で切れた）。理由を 1 行出す |
| 2 | 使い方の誤り（引数の誤り、`--repo` が無い、`--out` が `--repo` の下） |
| 3 | 環境が揃っていない（`out/` のバンドルが無い、`playwright-core` が無い、ブラウザが無い・起動できない） |

描画の完了は、`#root` の「読み込み中」の表示が消えることで見る。

### 前提と依存

- `pnpm run compile` で `out/` を作っておく。このスクリプトからはビルドしない。
- ブラウザは `PLAYWRIGHT_BROWSERS_PATH` の Chromium を使う（Chrome 拡張の試験と同じやり方。`playwright install` はしない）。
  固定した `playwright-core` の版が期待する Chromium と入っているものの版が違うときは、`CCNAVI_CHROMIUM` に実行ファイルを指す。
- `playwright-core` は devDependencies に `1.56.1` を `^` 無しで固定している（Chrome 拡張と同じ版）。ブラウザ本体を同梱せず、
  実行ファイルを起こして操作する部分だけが要るため。画面を描くのは Chromium で、画面の部品（React）と CSS は実行時の依存に増やさない。
- 画面は `page.addInitScript` で `acquireVsCodeApi` のスタブ（`scripts/lib/vscode-stub.js`）を差し込み、`page.goto` で `board.html` の
  `file:` URL を開いて描く。`page.setContent` は使わない（差し込みが効かず、画面が「読み込み中」のまま止まる）。
  スタブは `postMessage` を `window.__posted` に積み、`getState` / `setState` は覚えておくだけ。

### 確かめられること・られないこと

確かめられる: 列・カード・札・件数・文面・親子と計画の並び、見た目（`--appearance` の 3 つ）、画面が開いた直後に拡張ホストへ送るメッセージ（`ready`）、
ページのエラーと CSP 違反。`--repo` では、実リポジトリのチケットの状態と画面が合っているか。

確かめられない: VS Code のテーマ（`--vscode-*`）、パネルの開閉・タブ・非表示と作り直し、コマンドの実行、拡張ホスト側（`board-panel.ts` など VS Code の API に触れる部分）、
ボタンを押す操作（このツールは描くだけで、押さない）。ボード以外の画面。画像の比較（スナップショットのテスト）もしない。
手動確認の表のどの行がツールで代わりに確かめられるかは、[手動確認の手順](manual-checks.md)の「ツールで代替できるか」の列にある。

テストは `test/devtools/`（`pnpm run test:devtools`）。引数・`--out` の拒否・失敗の条件・スタブの単体テストと、
`--fixture test/fixtures/board.json` で PNG が出ることを確かめる E2E（`render-board.e2e.ts`）がある。ブラウザが無いときは、E2E は終了コード 3 で終わる。
