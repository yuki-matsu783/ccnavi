---
title: ccnavi ボードの開発
type: design
description: VS Code 拡張 ccnavi ボードのテストの分け方（グループ・実行の流れ）、tsconfig の 3 本、依存の理由、生成物の消し方
tags: [design-doc, extension, testing]
keywords: [開発, テスト, test-groups, tsconfig, バンドル, esbuild, 依存, pnpm run clean, happy-dom, グループ]
---

[設計の入口に戻る](../design.md)

テストの入口は `scripts/test-groups.js` の 1 本で、`pnpm test` とその仲間はすべてここを通る。回すグループを決め、`clean-out` → `tsc -p tsconfig.test.json` →（要るときだけ画面の型の検査）→ 画面のバンドル → `node --test <出来上がったファイル>` の順に進む。グループをいくつ選んでもコンパイルは 1 回で、`src/` と `test/` を全部見るので、絞っても短くならない（3〜4 秒）。短くなるのは実行（全部で約 4.6 秒）と、画面の型検査を省けるぶん（1.5 秒）。

バンドルは、画面の型を見ないときでも必ず回す（`clean-out.js` が `out/webview` を消すため）。バンドルするのはスクリプト（`<名前>.js`）と CSS（`<名前>.css`）の 2 本で、CSS は小さくしない。小さくしても減るのは数 KB なので、開発者ツールで読めるほうを選ぶ。

どのグループがどのファイルを読むかは、テストの `import`（CSS は `@import`）を辿って決める。辿れないものだけはパスで決める。その決まりは `test-groups.js` の先頭にまとめてあり、固定データ `test/fixtures/` は全部のグループを回す。画面 `src/webview/` は、辿れたぶんとバンドルしたものを読むグループを回す。組み立てに使う `package.json`・`tsconfig*.json`・`scripts/`・`pnpm-lock.yaml` は全部を回し、文書 `*.md` と画像 `media/` は何も回さない。`out/`・`node_modules/` は数えない。テストが 1 つも読まないファイルは `tsc` だけ通す。`test/<グループ>/` を増やすときに直すのは、`package.json` の行 1 本だけ。

`test/shared/` は 9 画面を比べるので、バンドルした画面を読む。そのため `shared` を含む計画には必ず画面の型検査とバンドルが付き、`src/core/` の大半は `shared` を含む。絞っても減らせるのは 1 回あたり 2.5〜4.5 秒です。

ワークスペース側の hook（`.claude/hooks/mark-ext.sh` と `test-ext.sh`）は `pnpm test:for` と同じ仕組みで動く。拡張のファイルを触ったターンの終わりに、関わるグループだけが回る。

テストは画面の領域ごとのディレクトリに分けてあり、一部を直したときはその領域だけを流せる。組み立てと HTML の文字列を見る単体テスト（`*.test.ts`）と、画面を happy-dom で実際に動かすテスト（`*.dom.test.ts`）を同じディレクトリに置く。ボードの画面は React なので、何を描くかも動かして見る（`board/render.dom.test.ts`）。React は押した直後には描き直さないので、操作のあとは `await page.settle()` を挟んでから見る。

`pnpm test` と `pnpm run compile` は、tsc の出力（`out/src` と `out/test`）を先に消す。esbuild がバンドルした `out/extension.js` は消さないので、package の compile → test の順でも入口は残る。出荷物の型検査は `tsconfig.json`（`src/webview` を除く。lib は DOM 無し、拾う型は node と vscode だけ）と `tsconfig.webview.json`（`src/webview` だけ。lib に DOM、JSX は `react-jsx`、`noEmit`。JS を出すのは esbuild のほう）の 2 本に分かれている。テストは `tsconfig.test.json`（`skipLibCheck`。happy-dom の型定義が `@types/node` 22 と合わないため）。

実行時の依存は `yaml`（コメントを残して書き戻すため）の 1 つ。開発時の依存に happy-dom と react / react-dom を足してある（画面はバンドルして配るので、実行時の依存にはしない）。vsix には `node_modules/` を入れず、`scripts/bundle.js`（esbuild）が本体ごと `out/extension.js` にバンドルする。テストはバンドルする前の `out/src/` を使い、画面だけはバンドルした `out/webview/<名前>.js` を使う。`out/webview/` は vsix に入る。拡張が起動時に読んで `<script nonce>` に流し込むので、Webview の `localResourceRoots` は空のままでよく、CSP も nonce だけで済む。

生成物を消すときは `pnpm run clean`（`scripts/clean.js`）。消すのは `node_modules/` と `out/` の 2 つだけで、引数は取らない（`rm -rf` はルール `recursive-delete` が止める）。ワークツリー（`.claude/worktrees/<名前>`）でこの拡張を組み立てたら、`git worktree remove` の前にこれを走らせる。走らせないと、`node_modules/.pnpm/` が残って削除が途中で止まることがある。
