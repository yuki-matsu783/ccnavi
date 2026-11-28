# ccnaviボード（VS Code拡張）

ccnaviのチケットが、どのワークツリーでどこまで進んでいるかをカンバンで見られます。

この拡張は、人がチケットの状態を確かめ、承認などの操作をするための画面です。判定はccnaviが行います。作業中のエージェントのセッションへプロンプトを送る手段はありません（理由はccnaviのREADMEの「複数人・別 PC・別セッションで進める」を参照）。

この拡張が何をするか（画面ごとのふるまい）は[docs/requirements.md](docs/requirements.md)、設計（構成・画面の処理の流れ・テストのID）は[docs/design.md](docs/design.md)に書いてあります。このREADMEには、必要なものと、組み立てと導入（ビルドとインストール）の手順だけを書きます。拡張開発ホストでの動かし方と手動確認の手順は[docs/design/manual-checks.md](docs/design/manual-checks.md)にあります。

## 必要なもの

| 必要なもの | 条件 |
|---|---|
| VS Code | 1.90以上 |
| ccnaviの実行ファイル | 探す順は、設定`ccnaviBoard.binPath` → `.claude/settings.json`（`settings.local.json`が優先）の`CCNAVI_ENTRY_PATH` → `dist/ccnavi/ccnavi[.exe]` → `.ccnavi/scripts/ccnavi-launcher.sh` → PATHの`ccnavi`（PATHのうち絶対パスのフォルダだけを探す）。起動スクリプト（`ccnavi-launcher.sh`）を指していれば、スクリプトは実行せず、`../bin/<os>-<arch>/`にあるこのOSとCPU向けの実行ファイルを使う。無ければ次の候補へ進む（Windowsではshを直接起動できないため）。それ以外のパスは、そのまま実行ファイルとして使う |
| ccnaviの版 | 拡張とccnaviは同じ版を一緒に配る前提。版が合っているかは事前に確かめない。版がずれていてccnaviがエラーを返したときは、そのエラー文をそのまま表示する（[版のJSON](../../../docs/11_requirements/json-shapes.md#版の-json)） |
| bash | clone / fetch / pullのコマンドと、承認済みチケットのコミットとpushのshを送るターミナルのシェル。承認と、未解決の指摘の対応方針は、ターミナルには送らず子プロセスで実行するが、そこで動かすshにも同じシェルを使う。WindowsはGit Bash（`C:\Program Files\Git\bin\bash.exe`、無ければPATHの`bash`） |
| git | PATHにあること。プロジェクト管理画面がoriginを読み、ターミナルでcloneを実行する |
| ffmpeg | 任意。バグ報告の録画にだけ使う（無ければ録画せず、スクリーンショットとログだけをzipにする）。WindowsはPATHから呼べること、WSLはWindows側のPATHに`ffmpeg.exe`があること、macOSはPATHかHomebrewの置き場にあること。入れ方は`winget install ffmpeg`・`brew install ffmpeg`など。入れたあとはVS Codeを再起動する（起動中のVS Codeは新しいPATHを読まない） |
| Node.js / pnpm | 22.13.1以上 / 10。組み立てとテストにだけ要る |

設定（`settings.json`）は次のとおりです。

| キー | 説明 |
|---|---|
| `ccnaviBoard.binPath` | 実行ファイルの場所。空なら上の順で探す。相対ならワークスペースルートから |
| `ccnaviBoard.bashPath` | Windowsで使うシェル。空ならGit Bash |
| `ccnaviBoard.expectationsPath` | ルール管理画面の「期待する判定を一括で確認する」で使うファイル。既定は`.ccnavi/config/common/rule-expectations.yml`。相対ならワークスペースルートから。ファイルが無いときは、ccnaviを呼ばずに作り方を案内する |
| `ccnaviBoard.ffmpegPath` | バグ報告の録画に使うffmpegの絶対パス。WSLではWindows側の`ffmpeg.exe`を指定する（例: `/mnt/c/ffmpeg/bin/ffmpeg.exe`）。空ならOSごとの候補を探す |
| `ccnaviBoard.appearance` | 画面の配色。`vscode`（テーマに従う）・`claude-light`・`claude-dark` |
| `ccnaviBoard.zoom` | 画面全体の拡大率（0.8〜2、既定1）。文字が小さいときに上げる |
| `ccnaviBoard.flowSaveReview` | フロー編集画面で保存の前に変更の一覧を出すか。既定は出す |

共通設定のルールファイルの場所は`.ccnavi/config/common/rules.yml`に固定です。プロジェクトのフォルダは`projects/`に固定です。

ワークスペースの設定とプロジェクトの設定のルールファイルのパスは、拡張では組み立てず、`--explain --json`の`layers[]`が返すパスを使います（ccnaviと食い違わないようにするため。場所はgitプロジェクトルートからの`.ccnavi/config/project/rules.yml`）。

## 組み立てと導入

次のコマンドは`extensions/vscode/ccnavi-board/`で実行します。

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

`pnpm run package`は`dist/ccnavi-board-<version>.vsix`（リポジトリの`dist/`、gitignore済み）に出力します。`<version>`は`package.json`の`version`の値です。出来上がったパスはpackageの最後にも表示されます。Marketplaceには出しません。入れるには次を実行します。

```sh
code --install-extension dist/ccnavi-board-<version>.vsix --force   # --forceは入れ替えのとき
```

入れたあと、開いているウィンドウは再読み込み（`Developer: Reload Window`）すると新しい版になります。

入れたときの`[DEP0169] DeprecationWarning: url.parse() ...`は`code`コマンド自身の警告で、拡張とは関係ありません。`successfully installed`が出ていれば入っています。

テストの分け方、tsconfigの3本、依存の理由、生成物の消し方（`pnpm run clean`）といった開発者向けの説明は[docs/design/development.md](docs/design/development.md)に書いてあります。

## 拡張開発ホストでの動かし方と手動確認

拡張開発ホストで動かす手順と、単体テストの対象外の部分を手で確かめる項目は[docs/design/manual-checks.md](docs/design/manual-checks.md)に書いてあります。
画面だけならVS Codeを使わずChromiumで描いて確かめられます。使い方は[画面をVS Codeなしで描く](docs/design/development.md#画面を-vs-code-なしで描くrender-boardjs)にあります。

本物のVS Code（code-server）の画面を撮って日本語ユーザ目線で確かめる手順は[docs/design/real-screen-check.md](docs/design/real-screen-check.md)にあります。
