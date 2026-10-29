# ccnavi ボード（VS Code 拡張）

ccnavi のチケットが、どのワークツリーでどこまで進んでいるかをカンバンで見る。

この拡張は、チケットの状態を見て、承認などの人の操作を受ける入口です。判定は実行ファイルが行い、作業中のエージェントのセッションへプロンプトを送り込む手段は持ちません（ccnavi の README「複数人・別 PC・別セッションで進める」）。

この拡張が何をするか（画面ごとのふるまい）は [docs/requirements.md](docs/requirements.md)、作り（構成・画面の段取り・テストの ID）は [docs/design.md](docs/design.md) に書いてある。この README には、必要なもの・組み立てと導入だけを書く。拡張開発ホストでの動かし方と手動確認の手順は [docs/design/manual-checks.md](docs/design/manual-checks.md) にある。

## 必要なもの

| もの | 版 |
|---|---|
| VS Code | 1.90 以上 |
| ccnavi の実行ファイル | 探す順は、設定 `ccnaviBoard.binPath` → `.claude/settings.json`（`settings.local.json` が優先）の `CCNAVI_ENTRY_PATH` → `dist/ccnavi/ccnavi[.exe]` → `.ccnavi/scripts/ccnavi-launcher.sh` → ソースがあれば `uv run python -m ccnavi` → PATH の `ccnavi`（絶対パスの項目だけ）。振り分けの sh（名前が `ccnavi-launcher.sh`）を指していれば、sh を通さず `../bin/<os>-<arch>/` にあるこの OS と CPU 向けの実行ファイルを使い、無ければ次の候補へ進む（Windows では sh を直接起動できないため、sh そのものは使わない）。それ以外のパスは、そのまま実行ファイルとして使う |
| 実行ファイルの版 | 起動のときに 1 度 `--version --json` を聞き、互換の版（`compat`）が拡張の `EXTENSION_COMPAT` と違うときに通知を出す。版を聞けなかったとき（実行ファイルが `--version --json` に失敗する・答えが読めない）は、起動では何も言わない。画面ごとに、使うフラグを確かめられない理由を出して先へ進まない。通知では、どちらが古いかと直し方（ccnavi のリポジトリなら `build.py` で組み立て直し、配布先なら `scripts/ccnavi-setup.sh --force` で配り直し、拡張が古ければ入れ直し）を示す。画面は止めない（[版の JSON](../../../docs/11_requirements/json-shapes.md#版の-json)） |
| bash | clone / fetch / pull を送るターミナル。承認と未解決の指摘の対応方針はこのターミナルを通らない（子プロセスで打つ。sh はこのシェルで走らせる）。Windows は Git Bash（`C:\Program Files\Git\bin\bash.exe`、無ければ PATH の `bash`） |
| git | PATH にあること。プロジェクト管理画面が origin を読み、ターミナルで clone を打つ |
| ffmpeg | 任意。バグ報告の録画にだけ使う（無ければ録画せず、スクリーンショットとログだけを zip にする）。Windows は PATH から呼べること、WSL は Windows 側の PATH に `ffmpeg.exe` があること、macOS は PATH か Homebrew の置き場にあること。入れ方は `winget install ffmpeg`・`brew install ffmpeg` など。入れたあとは VS Code を再起動する（起動中の VS Code は新しい PATH を読まない） |
| Node.js / pnpm | 22.13.1 以上 / 10。組み立てとテストにだけ要る |

設定（`settings.json`）。

| キー | 何 |
|---|---|
| `ccnaviBoard.binPath` | 実行ファイルの場所。空なら上の順で探す。相対ならワークスペースルートから |
| `ccnaviBoard.bashPath` | Windows で使うシェル。空なら Git Bash |
| `ccnaviBoard.expectationsPath` | ルール管理画面が一括で確かめる期待する判定のファイル。既定は `.ccnavi/config/common/rule-expectations.yml`。相対ならワークスペースルートから。ファイルが無いときは、実行ファイルを呼ばずに作り方を案内する |
| `ccnaviBoard.ffmpegPath` | バグ報告の録画に使う ffmpeg の絶対パス。WSL では Windows 側の `ffmpeg.exe` を指す（例 `/mnt/c/ffmpeg/bin/ffmpeg.exe`）。空なら OS ごとの候補を探す |
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
pnpm test          # 全部
pnpm test:rules    # 領域だけ。board / rules / risk / phases / projects / templates / handoff / flow / workflow / shared / devtools / bugreport
pnpm test:devtools  # 画面確認ツール（render-board.js）だけ。Chromium が無いと終了コード 3
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

テストの分け方、tsconfig の 3 本、依存の理由、生成物の消し方（`pnpm run clean`）といった開発者向けの説明は [docs/design/development.md](docs/design/development.md) に書いてある。

## 拡張開発ホストでの動かし方と手動確認

拡張開発ホストで動かす手順と、単体テストの対象外の部分を手で確かめる項目は [docs/design/manual-checks.md](docs/design/manual-checks.md) に書いてある。
画面だけなら VS Code を使わず Chromium で描いて確かめられる。使い方は [画面を VS Code なしで描く](docs/design/development.md#画面を-vs-code-なしで描くrender-boardjs) にある。

本物の VS Code（code-server）の画面を撮って日本語ユーザ目線で確かめる手順は [docs/design/real-screen-check.md](docs/design/real-screen-check.md) にある。
