# ccnavi ボード（VS Code 拡張）

ccnavi のチケットが、どの作業ツリーでどこまで進んでいるかをカンバンで見る。
列は提案の置き場（未着手 / 作業中 / 完了 / 取り消し）で、写し（未承認・承認済・閉）、
フェーズの印（レビュー依頼済・レビュー済・省略）、ゲートの開閉、作業ツリーの有無、
実績のリスクをカードのバッジで出す。親カードにはフェーズの一覧と段階が付く。

人の承認はボタンから統合ターミナルへコマンドを送る。拡張は承認を自分では実行しない。
ccnavi は `--approve` / `accept` / `wrapup` を端末から打つものと決めていて（エージェントが
人の合意を出せないための壁）、拡張の子プロセスもその壁の外に置く。y/N は人がターミナルで押す。

- 出力の形: ccnavi の README「ボードの JSON」
- 設計: ccnavi.md §24.10、要求 REQ-DIA-06

## できること

| コマンド（コマンドパレット） | 動き |
|---|---|
| `ccnavi ボード: ボードを開く` | ボードを開く。既に開いていれば増やさず前面に出す |
| `ccnavi ボード: ボードを更新` | `ccnavi --explain --json` を走らせ直して内容を差し替える |
| `ccnavi ボード: 承認待ちを承認する（--approve）` | ボードを開かずに `--approve` をターミナルへ送る |

ボードの中で。

| 操作 | 何が起きるか |
|---|---|
| カードをクリック / Enter | そのチケットの提案（無ければ写し）をエディタで開く |
| 上部の「承認待ち N 件を承認」、未承認カードの「承認」 | ターミナルに `ccnavi --approve` を送る。束（いま承認待ちのもの全部）を見せて y/N を取る。1 件だけの承認はできない |
| フェーズ行の「受け入れ」 | 依頼済みでゲートが閉じたままのフェーズに出る。親の作業ツリーに `cd` して `sh .claude/scripts/ccnavi-review.sh accept <N>` を送る |
| 親カードの「締める」 | 理由を入力し、残りを issue に起こすかを選んでから、`sh .claude/scripts/ccnavi-review.sh wrapup --reason <理由> [--no-issue]` を送る |
| プロジェクトの絞り込み | `projects/` があるときだけ出る。選択は Webview の状態として覚える |
| 「更新」 | ボードを読み直す |

- 提案（`wip/**/tickets/`、全作業ツリーの同じ場所）・写しと印（`.claude/ccnavi/tickets/`）・
  作業ツリーの登録（`.git/worktrees/`、`projects/*/.git/worktrees/`）を監視し、変化から 120 ミリ秒
  静まったら自動で読み直す。承認コマンドの終了は追わず、写しや印が変わったことで読み直す
- 対象はワークスペースの最初のフォルダだけ。`projects/` 配下のプロジェクト向けチケットも
  同じボードに出る（`project` バッジ）
- レビューのスレッドの現状は取りに行かない。ボードが見せるのは印まで
- 実行ファイルが無い、JSON が読めない、版が違うときはボードを開かず通知で伝える。
  開いている間に読めなくなったら、前の表示を消して理由を出す

## 必要なもの

| もの | 版 |
|---|---|
| VS Code | 1.90 以上 |
| ccnavi の実行ファイル | `dist/ccnavi/ccnavi[.exe]`。無ければ `.claude/settings.json` の `CCNAVI_BIN_PATH`、それも無ければソースを `uv run python -m ccnavi` で走らせる |
| bash | 承認コマンドを送るターミナル。Windows は Git Bash（`C:\Program Files\Git\bin\bash.exe`、無ければ PATH の `bash`） |
| Node.js / pnpm | 22 以上 / 10。組み立てとテストにだけ要る |

設定（`settings.json`）。

| 鍵 | 何 |
|---|---|
| `ccnaviBoard.binPath` | 実行ファイルの場所。空なら上の順で探す。相対ならワークスペースルートから |
| `ccnaviBoard.bashPath` | Windows で使うシェル。空なら Git Bash |

## 組み立てとインストール

`vscode-extension/ccnavi-board/` で。

```sh
pnpm install --frozen-lockfile
pnpm run compile   # tsc -p . で out/ に出す
pnpm test          # tsc のあと node --test out/test/*.test.js
pnpm run package   # scripts/package.sh: install → compile → test → vsce package
```

`pnpm run package` は `dist/ccnavi-board-<version>.vsix`（リポジトリの `dist/`、gitignore 済み）に出す。
入れるには次を打つ。Marketplace には出さない。

```sh
code --install-extension dist/ccnavi-board-0.1.0.vsix
```

`node --test` にはディレクトリではなくグロブ（`out/test/*.test.js`）を渡す。

## デバッグ実行（拡張ホストで動かす）

1. `pnpm install && pnpm run compile`
2. VS Code で `vscode-extension/ccnavi-board/` を開く
3. `F5`（実行とデバッグ → 拡張機能）で拡張開発ホストを起動する
4. そのウィンドウで、ccnavi を入れたリポジトリ（`.claude/ccnavi/` を持つフォルダ）を開く
5. コマンドパレットから `ccnavi ボード: ボードを開く` を実行する

## 手動確認の手順

`extension.ts` / `board-panel.ts` / `terminal.ts` / `ccnavi.ts` は VS Code の API か子プロセスに
触れるので単体テストの対象外。次を拡張開発ホストで確かめる。チケットのある状態を作るには
`tests/test_board.py` の `scene()` と同じ手順（親を承認、子を着手・閉じる、次の子を提案）を
実際のリポジトリで踏む。

| # | 確認すること | 手順 | 期待 |
|---|---|---|---|
| 1 | ボードが開く | `ボードを開く` | 4 列と、上部に残り・全件・不備・承認待ちの件数 |
| 2 | カードでファイルが開く | カードをクリック。ボードのタブに戻ってから Tab で別のカードへ移り Enter | どちらも提案の Markdown が開く |
| 3 | 自動更新 | ボードを開いたまま、別のターミナルで `sh .claude/scripts/ccnavi-ticket.sh start <子>` | コマンドを打たなくてもカードが作業中へ動く |
| 4 | 承認 | 未承認の提案を置き、「承認待ち 1 件を承認」を押す | 「ccnavi」ターミナルが開いて `--approve` が走り、束が出て y/N を聞く。y を押すとカードが承認済になる |
| 5 | 受け入れ | レビュー依頼済みでゲートが閉じたフェーズの「受け入れ」を押す | ターミナルで親の作業ツリーに `cd` してから `accept <N>` が走る |
| 6 | 締める | 親カードの「締める」を押し、理由と issue の有無を答える | ターミナルで `wrapup --reason ... [--no-issue]` が走る |
| 7 | 増やさず前面に出す | 開いたまま、もう一度 `ボードを開く` | タブは 1 つのまま |
| 8 | 再表示で読み直す | タブを裏に回して提案を 1 枚足し、タブに戻る | 戻った時点で足した提案が出る |
| 9 | ワークスペースが無い | フォルダを開いていないウィンドウで `ボードを開く` | 「ワークスペースが開かれていない」の通知。ボードは開かない |
| 10 | 実行ファイルが無い | `dist/ccnavi/` を一時的に名前を変え、ソースも無いフォルダで `ボードを開く` | 「実行ファイルが見つからない」の通知 |
| 11 | 未表示で更新 | ボードを閉じた状態で `ボードを更新` | 「ccnavi ボードが開かれていない」の通知 |
| 12 | 読めない写し | ボードを開いたまま `.claude/ccnavi/tickets/<id>.md` の frontmatter を壊す | 上部の問題の一覧にその写しが出て、他のカードはそのまま |
| 13 | プロジェクト | `projects/<repo>` を持つワークスペースで開く | `project` バッジと絞り込みが出る |

## 構成

```
src/
  extension.ts        コマンド登録（vscode に依存する）
  board-panel.ts      Webview パネルの生成・更新・破棄、監視、操作の受け付け（vscode に依存する）
  terminal.ts         「ccnavi」ターミナルの用意とコマンドの送信（vscode に依存する）
  ccnavi.ts           実行ファイルの探索と --explain --json の実行（Node の子プロセス）
  core/
    model.ts          JSON の形（実行ファイルとの契約）と読み取り
    board.ts          列とカードへの組み立て、操作の有無
    render.ts         HTML の組み立て（外部資源なし、テーマ変数だけ）
    commands.ts       ターミナルに送るコマンド行
    locate.ts         実行ファイルの探索順
test/
  fixtures/board.json 実行ファイルの出力の実例。Python 側の tests/test_board.py が書き出す
  *.test.ts           core の単体テスト CB-T01〜CB-T22
scripts/
  package.sh          vsix の組み立て
```

`core/` は `vscode` を import しない。ここだけを `node --test` で試す。
