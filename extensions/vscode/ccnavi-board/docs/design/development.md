---
title: ccnaviボードの開発
type: design
description: VS Code拡張ccnaviボードのテストの分け方（グループ・実行の流れ）、tsconfigの3本、依存の理由、生成物の消し方、VS CodeなしでChromiumに画面を描く開発用スクリプトrender-board.jsの使い方と前提
tags: [design-doc, extension, testing]
keywords: [開発, テスト, test-groups, tsconfig, バンドル, esbuild, 依存, pnpm run clean, happy-dom, グループ, render-board, 画面確認, Chromium, playwright-core, PLAYWRIGHT_BROWSERS_PATH, devtools, PNG, 終了コード]
---

[設計の入口に戻る](../design.md)

テストの入口は`scripts/test-groups.js`の1本で、`pnpm test`とその仲間はすべてここを通る。回すグループを決め、`clean-out` → `tsc -p tsconfig.test.json` →（要るときだけ画面の型の検査）→ 画面のバンドル → `node --test <出来上がったファイル>`の順に進む。グループをいくつ選んでもコンパイルは1回で、`src/`と`test/`を全部見るので、絞っても短くならない（3〜4秒）。短くなるのは実行（全部で約4.6秒）と、画面の型検査を省けるぶん（1.5秒）。

バンドルは、画面の型を見ないときでも必ず回す（`clean-out.js`が`out/webview`を消すため）。バンドルするのはスクリプト（`<名前>.js`）とCSS（`<名前>.css`）の2本で、CSSは小さくしない。小さくしても減るのは数KBなので、開発者ツールで読めるほうを選ぶ。

どのグループがどのファイルを読むかは、テストの`import`（CSSは`@import`）を辿って決める。辿れないものだけはパスで決める。その決まりは`test-groups.js`の先頭にまとめてあり、固定データ`test/fixtures/`は全部のグループを回す。画面`src/webview/`は、辿れたぶんとバンドルしたものを読むグループを回す。組み立てに使う`package.json`・`tsconfig*.json`・`scripts/`・`pnpm-lock.yaml`は全部を回し、文書`*.md`と画像`media/`は何も回さない。ただし`scripts/render-board.js`と`scripts/lib/`は開発用の画面確認ツールなので`devtools`グループに入る。そのツールが描く`src/ccnavi.ts`・`src/core/render.ts`・`src/webview-asset.ts`とボードの画面を直したときも、`devtools`を回す。`out/`・`node_modules/`は数えない。テストが1つも読まないファイルは`tsc`だけ通す。`test/<グループ>/`を増やすときに直すのは、`package.json`の行1本だけ。

`test/shared/`は9画面を比べるので、バンドルした画面を読む。そのため`shared`を含む計画には必ず画面の型検査とバンドルが付き、`src/core/`の大半は`shared`を含む。絞っても減らせるのは1回あたり2.5〜4.5秒です。

ワークスペース側のhook（`.claude/hooks/mark-ext.sh`と`test-ext.sh`）は`pnpm test:for`と同じ仕組みで動く。拡張のファイルを触ったターンの終わりに、関わるグループだけが回る。

テストは画面の領域ごとのディレクトリに分けてあり、一部を直したときはその領域だけを流せる。組み立てとHTMLの文字列を見る単体テスト（`*.test.ts`）と、画面をhappy-domで実際に動かすテスト（`*.dom.test.ts`）を同じディレクトリに置く。ボードの画面はReactなので、何を描くかも動かして見る（`board/render.dom.test.ts`）。Reactは押した直後には描き直さないので、操作のあとは`await page.settle()`を挟んでから見る。

`pnpm test`と`pnpm run compile`は、tscの出力（`out/src`と`out/test`）を先に消す。esbuildがバンドルした`out/extension.js`は消さないので、packageのcompile → testの順でも入口は残る。出荷物の型検査は`tsconfig.json`（`src/webview`を除く。libはDOM無し、拾う型はnodeとvscodeだけ）と`tsconfig.webview.json`（`src/webview`だけ。libにDOM、JSXは`react-jsx`、`noEmit`。JSを出すのはesbuildのほう）の2本に分かれている。テストは`tsconfig.test.json`（`skipLibCheck`。happy-domの型定義が`@types/node` 22と合わないため）。

実行時の依存は`yaml`（コメントを残して書き戻すため）の1つ。開発時の依存にhappy-domとreact / react-domを足してある（画面はバンドルして配るので、実行時の依存にはしない）。vsixには`node_modules/`を入れず、`scripts/bundle.js`（esbuild）が本体ごと`out/extension.js`にバンドルする。テストはバンドルする前の`out/src/`を使い、画面だけはバンドルした`out/webview/<名前>.js`を使う。`out/webview/`はvsixに入る。拡張が起動時に読んで`<script nonce>`に流し込むので、Webviewの`localResourceRoots`は空のままでよく、CSPもnonceだけで済む。

テストの置き場に`*.e2e.ts`があれば、`node --test`には渡さず、単体テストのあとに`test-groups.js`が別に起こす。E2Eは実機のブラウザを使うので、環境が無いときはスキップして成功に数えず、終了コード（0通った / 1失敗 / 3環境が揃っていない）をそのまま返す。

生成物を消すときは`pnpm run clean`（`scripts/clean.js`）。消すのは`node_modules/`と`out/`の2つだけで、引数は取らない（`rm -rf`はルール`recursive-delete`が止める）。ワークツリー（`.claude/worktrees/<名前>`）でこの拡張を組み立てたら、`git worktree remove`の前にこれを走らせる。走らせないと、`node_modules/.pnpm/`が残って削除が途中で止まることがある。

## 画面をVS Codeなしで描く（render-board.js）

`scripts/render-board.js`（CommonJS）は、VS CodeなしでChromiumにボードの画面を描き、PNGとテキストに残す開発用ツール。
VS Codeを持たない環境（クラウドのセッション、CI）や、実リポジトリのチケットの状態を画面で見たいときに使う。
拡張の配布物（vsix）とccnaviの配布物には含めない（`package.sh`の対象外）。

```sh
pnpm install && pnpm run compile
node scripts/render-board.js --fixture test/fixtures/board.json --out "${TMPDIR:-/tmp}/ccnavi-board"
node scripts/render-board.js --repo <ccnavi を配布済みのリポジトリ> --out <リポジトリの外のディレクトリ>
```

| 引数 | 内容 |
|---|---|
| `--repo <dir>` | ccnaviを配布済みのリポジトリ。拡張と同じ`loadBoard`（ランチャーの探し方・`--root`・60秒の期限・失敗の文面）で中身を得る。リポジトリの中身は読むだけ |
| `--fixture <file>` | BoardJsonのファイル（`test/fixtures/board.json`など）。`--repo`とどちらか一方 |
| `--out <dir>` | 出力ディレクトリ（無ければ作り、既存の`board.*`は上書きする）。`--repo`と同じか下は不可（実際のパスにそろえ、大文字小文字と区切りの違いを無視して比べる） |
| `--appearance` | `vscode` / `claude-light` / `claude-dark`（値は拡張の`APPEARANCES`で確かめる）。既定は`claude-light`。`vscode`はVS Codeのテーマ変数（`--vscode-*`）を使うので、Chromiumだけでは色が欠ける |
| `--width` | ページの幅。320〜3000の整数（既定1400）。高さは全体が入るまで伸ばす |

出力は`<out>/`の4つ。

| ファイル | 内容 |
|---|---|
| `board.png` | 画面全体（`fullPage`） |
| `board.txt` | `body`の見えているテキスト |
| `board.html` | 入れ物のHTML（`renderBoardPage`の出力） |
| `board.log` | ページの記録: コンソール、ページエラー、CSP違反、画面が拡張ホストへ送ったメッセージ。アプリのログではない |

終了コードの意味は、`scripts/test-groups.js`の`NOT_READY`と同じにしてある。

| コード | 意味 |
|---|---|
| 0 | 描けて、失敗の条件に当たらない |
| 1 | 描けなかった、または失敗の条件に当たった（`loadBoard` / `parseBoardJson`の失敗、ページエラー、コンソールの`error`（`favicon`の取得失敗を除く）、CSP違反、描画の完了待ちが15秒で切れた）。理由を1行出す |
| 2 | 使い方の誤り（引数の誤り、`--repo`が無い、`--out`が`--repo`の下） |
| 3 | 環境が揃っていない（`out/`のバンドルが無い、`playwright-core`が無い、ブラウザが無い・起動できない） |

描画が終わったかは、`#root`の「読み込み中」の表示が消えたかで判定する。

### 前提と依存

- `pnpm run compile`で`out/`を作っておく。このスクリプトからはビルドしない。
- ブラウザは`PLAYWRIGHT_BROWSERS_PATH`のChromiumを使う（Chrome拡張の試験と同じやり方。`playwright install`はしない）。
  `playwright-core`が想定するChromiumと入っているものの版が違うときは、`CCNAVI_CHROMIUM`に実行ファイルのパスを指定する。
- `playwright-core`はdevDependenciesに`1.56.1`を`^`無しで固定している（Chrome拡張と同じ版）。ブラウザ本体は同梱せず、
  ブラウザを起動して操作する部分だけが要るため。画面を描くのはChromiumで、画面の部品（React）とCSSは実行時の依存に増やさない。
- 画面は`page.addInitScript`で`acquireVsCodeApi`のスタブ（`scripts/lib/vscode-stub.js`）を差し込み、`page.goto`で`board.html`の
  `file:` URLを開いて描く。`page.setContent`は使わない（差し込みが効かず、画面が「読み込み中」のまま止まる）。
  スタブは`postMessage`を`window.__posted`に積み、`getState` / `setState`は覚えておくだけ。

### 確かめられること・られないこと

確かめられる: 列・カード・バッジ・件数・文面・親子と計画の並び、見た目（`--appearance`の3つ）、画面が開いた直後に拡張ホストへ送るメッセージ（`ready`）、
ページのエラーとCSP違反。`--repo`では、実リポジトリのチケットの状態と画面が合っているか。

確かめられない: VS Codeのテーマ（`--vscode-*`）、パネルの開閉・タブ・非表示と作り直し、コマンドの実行、拡張ホスト側（`board-panel.ts`などVS CodeのAPIに触れる部分）、
ボタンを押す操作（このツールは描くだけで、押さない）。ボード以外の画面。画像の比較（スナップショットのテスト）もしない。
手動確認の表のどの行がツールで代わりに確かめられるかは、[手動確認の手順](manual-checks.md)の「ツールで代替できるか」の列にある。

テストは`test/devtools/`（`pnpm run test:devtools`）。引数・`--out`の拒否・失敗の条件・スタブの単体テストと、
`--fixture test/fixtures/board.json`でPNGが出ることを確かめるE2E（`render-board.e2e.ts`）がある。ブラウザが無いときは、E2Eは終了コード3で終わる。

## 実画面で日本語ユーザ目線を確かめる

VS Codeに拡張を入れた実画面（code-server）をChromiumで撮り、日本語を読む利用者にとって分かりづらい点を洗い出す手順は、[実画面（code-serverのVS Code）で日本語ユーザ目線を確かめる](real-screen-check.md)にある。
