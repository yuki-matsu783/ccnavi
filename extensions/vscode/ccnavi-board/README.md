# ccnavi ボード（VS Code 拡張）

ccnavi のチケットが、どのワークツリーでどこまで進んでいるかをカンバンで見る。

この拡張は、チケットの状態を見て、承認などの人の操作を受ける入口です。判定は実行ファイルが行い、作業中のエージェントのセッションへプロンプトを送り込む手段は持ちません（ccnavi の README「複数人・別 PC・別セッションで進める」）。

この拡張が何をするか（画面ごとのふるまい）は [docs/requirements.md](docs/requirements.md)、作り（構成・画面の段取り・テストの ID）は [docs/design.md](docs/design.md) に書いてある。この README には、必要なもの・組み立てと導入だけを書く。拡張開発ホストでの動かし方と手動確認の手順は [docs/design/manual-checks.md](docs/design/manual-checks.md) にある。

## 必要なもの

| もの | 版 |
|---|---|
| VS Code | 1.90 以上 |
| ccnavi の実行ファイル | 探す順は、設定 `ccnaviBoard.binPath` → `.claude/settings.json`（`settings.local.json` が優先）の `CCNAVI_BIN_PATH` → `dist/ccnavi/ccnavi[.exe]` → `.ccnavi/scripts/ccnavi-launcher.sh` → ソースがあれば `uv run python -m ccnavi`。振り分けの sh（名前が `ccnavi-launcher.sh`）を指していれば、sh を通さず `../bin/<os>-<arch>/` にあるこの OS と CPU 向けの実行ファイルを使い、無ければ次の候補へ進む（Windows では sh を直接起動できないため、sh そのものは使わない）。それ以外のパスは、そのまま実行ファイルとして使う |
| 実行ファイルの版 | 起動のときに 1 度 `--version --json` を聞き、互換の版（`compat`）が拡張の `EXTENSION_COMPAT` と違うか、`--version` を知らない古い実行ファイルなら通知を出す。通知では、どちらが古いかと直し方（ccnavi のリポジトリなら `build.py` で組み立て直し、配布先なら `scripts/ccnavi-setup.sh --force` で配り直し、拡張が古ければ入れ直し）を示す。画面は止めない（ccnavi の README「版の JSON」） |
| bash | clone / fetch / pull を送るターミナル。承認と未解決の指摘の対応方針はこのターミナルを通らない（子プロセスで打つ。sh はこのシェルで走らせる）。Windows は Git Bash（`C:\Program Files\Git\bin\bash.exe`、無ければ PATH の `bash`） |
| git | PATH にあること。プロジェクト管理画面が origin を読み、ターミナルで clone を打つ |
| Node.js / pnpm | 22 以上 / 10。組み立てとテストにだけ要る |

設定（`settings.json`）。

| 鍵 | 何 |
|---|---|
| `ccnaviBoard.binPath` | 実行ファイルの場所。空なら上の順で探す。相対ならワークスペースルートから |
| `ccnaviBoard.bashPath` | Windows で使うシェル。空なら Git Bash |
| `ccnaviBoard.expectationsPath` | ルール管理画面が一括で確かめる期待する判定のファイル。既定は `.ccnavi/config/common/rule-expectations.yml`。相対ならワークスペースルートから |
| `ccnaviBoard.flowSaveReview` | フロー編集画面で保存の前に変更の一覧を出すか。既定は出す |

ルールファイルの場所は `.ccnavi/config/common/rules.yml` 固定。
プロジェクトのフォルダは `projects/` 固定。ワークスペースの設定とプロジェクトの設定のルールファイルは
拡張が組まず、`--explain --json` の `layers[]` のパスを使う（組み方を実行ファイルとずらさないため。
場所は git プロジェクトルートからの `.ccnavi/config/project/rules.yml`）。

## 組み立てと導入

次のコマンドは `extensions/vscode/ccnavi-board/` で打つ。

```sh
pnpm install --frozen-lockfile
pnpm run compile   # tsc -p .（拡張ホスト）と tsc -p tsconfig.webview.json（画面）で型を見て、esbuild で out/webview/<名前>.js と out/extension.js にバンドルする
pnpm test          # 全部（867 本）
pnpm test:rules    # 領域だけ。board / rules / risk / phases / projects / templates / handoff / flow / workflow / shared
pnpm test:templates # テンプレート管理だけ（templates）
pnpm test:handoff   # 取り込み待ちだけ（handoff）
pnpm test:dom      # happy-dom で画面のスクリプトを動かすものだけ（*.dom.test.ts）
pnpm test:for src/core/rules-doc.ts   # 触ったファイルが関わる領域だけ
pnpm test:plan src/core/rules-doc.ts  # 何を回すかだけ出す
pnpm run package   # scripts/package.sh: install → compile → vsce package
```

`pnpm run package` は `dist/ccnavi-board-<version>.vsix`（リポジトリの `dist/`、gitignore 済み）に出力する。`<version>` は `package.json` の `version` の値。出来上がったパスは package の最後にも表示される。Marketplace には出さない。入れるには次を打つ。

```sh
code --install-extension dist/ccnavi-board-<version>.vsix --force   # --force は入れ替えのとき
```

入れたあと、開いているウィンドウは再読み込み（`Developer: Reload Window`）すると新しい版になる。

入れたときの `[DEP0169] DeprecationWarning: url.parse() ...` は `code` コマンド自身の警告で、拡張とは関係ない。`successfully installed` が出ていれば入っている。

テストの入口は `scripts/test-groups.js` の 1 本で、`pnpm test` とその仲間はすべてここを通る。回すグループを決め、`clean-out` → `tsc -p tsconfig.test.json` →（要るときだけ画面の型の検査）→ 画面のバンドル → `node --test <出来上がったファイル>` の順に進む。グループをいくつ選んでもコンパイルは 1 回で、`src/` と `test/` を全部見るので、絞っても短くならない（3〜4 秒）。短くなるのは実行（全部で約 4.6 秒）と、画面の型検査を省けるぶん（1.5 秒）。

バンドルは、画面の型を見ないときでも必ず回す（`clean-out.js` が `out/webview` を消すため）。バンドルするのはスクリプト（`<名前>.js`）と CSS（`<名前>.css`）の 2 本で、CSS は小さくしない。小さくしても減るのは数 KB なので、開発者ツールで読めるほうを選ぶ。

どのグループがどのファイルを読むかは、テストの `import`（CSS は `@import`）を辿って決める。辿れないものだけはパスで決める。その決まりは `test-groups.js` の先頭にまとめてあり、固定データ `test/fixtures/` は全部のグループを回す。画面 `src/webview/` は、辿れたぶんとバンドルしたものを読むグループを回す。組み立てに使う `package.json`・`tsconfig*.json`・`scripts/`・`pnpm-lock.yaml` は全部を回し、文書 `*.md` と画像 `media/` は何も回さない。`out/`・`node_modules/` は数えない。テストが 1 つも読まないファイルは `tsc` だけ通す。`test/<グループ>/` を増やすときに直すのは、`package.json` の行 1 本だけ。

`test/shared/` は 9 画面を比べるので、バンドルした画面を読む。そのため `shared` を含む計画には必ず画面の型検査とバンドルが付き、`src/core/` の大半は `shared` を含む。絞っても減らせるのは 1 回あたり 2.5〜4.5 秒です。

ワークスペース側の hook（`.claude/hooks/mark-ext.sh` と `test-ext.sh`）は `pnpm test:for` と同じ仕組みで動く。拡張のファイルを触ったターンの終わりに、関わるグループだけが回る。

テストは画面の領域ごとのディレクトリに分けてあり、一部を直したときはその領域だけを流せる。組み立てと HTML の文字列を見る単体テスト（`*.test.ts`）と、画面を happy-dom で実際に動かすテスト（`*.dom.test.ts`）を同じディレクトリに置く。ボードの画面は React なので、何を描くかも動かして見る（`board/render.dom.test.ts`）。React は押した直後には描き直さないので、操作のあとは `await page.settle()` を挟んでから見る。

`pnpm test` と `pnpm run compile` は、tsc の出力（`out/src` と `out/test`）を先に消す。esbuild がバンドルした `out/extension.js` は消さないので、package の compile → test の順でも入口は残る。出荷物の型検査は `tsconfig.json`（`src/webview` を除く。lib は DOM 無し、拾う型は node と vscode だけ）と `tsconfig.webview.json`（`src/webview` だけ。lib に DOM、JSX は `react-jsx`、`noEmit`。JS を出すのは esbuild のほう）の 2 本に分かれている。テストは `tsconfig.test.json`（`skipLibCheck`。happy-dom の型定義が `@types/node` 22 と合わないため）。

実行時の依存は `yaml`（コメントを残して書き戻すため）の 1 つ。開発時の依存に happy-dom と react / react-dom を足してある（画面はバンドルして配るので、実行時の依存にはしない）。vsix には `node_modules/` を入れず、`scripts/bundle.js`（esbuild）が本体ごと `out/extension.js` にバンドルする。テストはバンドルする前の `out/src/` を使い、画面だけはバンドルした `out/webview/<名前>.js` を使う。`out/webview/` は vsix に入る。拡張が起動時に読んで `<script nonce>` に流し込むので、Webview の `localResourceRoots` は空のままでよく、CSP も nonce だけで済む。

生成物を消すときは `pnpm run clean`（`scripts/clean.js`）。消すのは `node_modules/` と `out/` の 2 つだけで、引数は取らない（`rm -rf` はルール `recursive-delete` が止める）。ワークツリー（`.claude/worktrees/<名前>`）でこの拡張を組み立てたら、`git worktree remove` の前にこれを走らせる。走らせないと、`node_modules/.pnpm/` が残って削除が途中で止まることがある。

## 拡張開発ホストでの動かし方と手動確認

拡張開発ホストで動かす手順と、単体テストの対象外の部分を手で確かめる項目は [docs/design/manual-checks.md](docs/design/manual-checks.md) に書いてある。
