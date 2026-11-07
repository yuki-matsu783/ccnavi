# ccnaviボード（VS Code拡張）

ccnaviのチケットが、どのワークツリーでどこまで進んでいるかをカンバンで見られます。

この拡張は、チケットの状態を見て、承認などの人の操作を受ける入口です。判定は実行ファイルが行い、作業中のエージェントのセッションへプロンプトを送り込む手段は持ちません（ccnaviのREADME「複数人・別 PC・別セッションで進める」）。

この拡張が何をするか（画面ごとのふるまい）は[docs/requirements.md](docs/requirements.md)、作り（構成・画面の段取り・テストのID）は[docs/design.md](docs/design.md)に書いてあります。このREADMEには、必要なもの・組み立てと導入だけを書きます。拡張開発ホストでの動かし方と手動確認の手順は[docs/design/manual-checks.md](docs/design/manual-checks.md)にあります。

## 必要なもの

| もの | 版 |
|---|---|
| VS Code | 1.90以上 |
| ccnaviの実行ファイル | 探す順は、設定`ccnaviBoard.binPath` → `.claude/settings.json`（`settings.local.json`が優先）の`CCNAVI_ENTRY_PATH` → `dist/ccnavi/ccnavi[.exe]` → `.ccnavi/scripts/ccnavi-launcher.sh` → PATHの`ccnavi`（絶対パスの項目だけ）。振り分けのsh（名前が`ccnavi-launcher.sh`）を指していれば、shを通さず`../bin/<os>-<arch>/`にあるこのOSとCPU向けの実行ファイルを使い、無ければ次の候補へ進む（Windowsではshを直接起動できないため、shそのものは使わない）。それ以外のパスは、そのまま実行ファイルとして使う |
| 実行ファイルの版 | 拡張と実行ファイルは同時に配る前提で、起動のときに互換の版を比べて知らせることはしない。使うフラグや機能の有無も事前に確かめず、版のずれは実行ファイルの断りをそのまま見せる（[版のJSON](../../../docs/11_requirements/json-shapes.md#版の-json)） |
| bash | clone / fetch / pullを送るターミナル。承認と未解決の指摘の対応方針はこのターミナルを通らない（子プロセスで打つ。shはこのシェルで走らせる）。WindowsはGit Bash（`C:\Program Files\Git\bin\bash.exe`、無ければPATHの`bash`） |
| git | PATHにあること。プロジェクト管理画面がoriginを読み、ターミナルでcloneを打つ |
| ffmpeg | 任意。バグ報告の録画にだけ使う（無ければ録画せず、スクリーンショットとログだけをzipにする）。WindowsはPATHから呼べること、WSLはWindows側のPATHに`ffmpeg.exe`があること、macOSはPATHかHomebrewの置き場にあること。入れ方は`winget install ffmpeg`・`brew install ffmpeg`など。入れたあとはVS Codeを再起動する（起動中のVS Codeは新しいPATHを読まない） |
| Node.js / pnpm | 22.13.1以上 / 10。組み立てとテストにだけ要る |

設定（`settings.json`）。

| キー | 何 |
|---|---|
| `ccnaviBoard.binPath` | 実行ファイルの場所。空なら上の順で探す。相対ならワークスペースルートから |
| `ccnaviBoard.bashPath` | Windowsで使うシェル。空ならGit Bash |
| `ccnaviBoard.expectationsPath` | ルール管理画面が一括で確かめる期待する判定のファイル。既定は`.ccnavi/config/common/rule-expectations.yml`。相対ならワークスペースルートから。ファイルが無いときは、実行ファイルを呼ばずに作り方を案内する |
| `ccnaviBoard.ffmpegPath` | バグ報告の録画に使うffmpegの絶対パス。WSLではWindows側の`ffmpeg.exe`を指す（例`/mnt/c/ffmpeg/bin/ffmpeg.exe`）。空ならOSごとの候補を探す |
| `ccnaviBoard.appearance` | 画面の配色。`vscode`（テーマに従う）・`claude-light`・`claude-dark` |
| `ccnaviBoard.zoom` | 画面全体の拡大率（0.8〜2、既定1）。文字が細かいときに上げる |
| `ccnaviBoard.flowSaveReview` | フロー編集画面で保存の前に変更の一覧を出すか。既定は出す |

ルールファイルの場所は`.ccnavi/config/common/rules.yml`固定。
プロジェクトのフォルダは`projects/`固定。ワークスペースの設定とプロジェクトの設定のルールファイルは
拡張が組まず、`--explain --json`の`layers[]`のパスを使う（組み方を実行ファイルとずらさないため。
場所はgitプロジェクトルートからの`.ccnavi/config/project/rules.yml`）。

## 組み立てと導入

次のコマンドは`extensions/vscode/ccnavi-board/`で打つ。

```sh
pnpm install --frozen-lockfile
pnpm run compile   # tsc -p .（拡張ホスト）とtsc -p tsconfig.webview.json（画面）で型を見て、esbuildでout/webview/<名前>.jsとout/extension.jsにバンドルする
pnpm test          # 全部
pnpm test:rules    # 領域だけ。board / rules / risk / phases / projects / templates / handoff / flow / workflow / shared / devtools / bugreport
pnpm test:devtools  # 画面確認ツール（render-board.js）だけ。Chromiumが無いと終了コード3
pnpm test:templates # テンプレート管理だけ（templates）
pnpm test:handoff   # 取り込み待ちだけ（handoff）
pnpm test:dom      # happy-domで画面のスクリプトを動かすものだけ（*.dom.test.ts）
pnpm test:for src/core/rules-doc.ts   # 触ったファイルが関わる領域だけ
pnpm test:plan src/core/rules-doc.ts  # 何を回すかだけ出す
pnpm run package   # scripts/package.sh: install → compile → vsce package
```

`pnpm run package`は`dist/ccnavi-board-<version>.vsix`（リポジトリの`dist/`、gitignore済み）に出力する。`<version>`は`package.json`の`version`の値。出来上がったパスはpackageの最後にも表示される。Marketplaceには出さない。入れるには次を打つ。

```sh
code --install-extension dist/ccnavi-board-<version>.vsix --force   # --forceは入れ替えのとき
```

入れたあと、開いているウィンドウは再読み込み（`Developer: Reload Window`）すると新しい版になる。

入れたときの`[DEP0169] DeprecationWarning: url.parse() ...`は`code`コマンド自身の警告で、拡張とは関係ない。`successfully installed`が出ていれば入っている。

テストの分け方、tsconfigの3本、依存の理由、生成物の消し方（`pnpm run clean`）といった開発者向けの説明は [docs/design/development.md](docs/design/development.md)に書いてある。

## 拡張開発ホストでの動かし方と手動確認

拡張開発ホストで動かす手順と、単体テストの対象外の部分を手で確かめる項目は [docs/design/manual-checks.md](docs/design/manual-checks.md)に書いてある。
画面だけならVS Codeを使わずChromiumで描いて確かめられる。使い方は [画面をVS Codeなしで描く](docs/design/development.md#画面を-vs-code-なしで描くrender-boardjs)にある。

本物のVS Code（code-server）の画面を撮って日本語ユーザ目線で確かめる手順は [docs/design/real-screen-check.md](docs/design/real-screen-check.md)にある。
