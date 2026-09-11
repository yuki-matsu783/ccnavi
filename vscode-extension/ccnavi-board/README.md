# ccnavi ボード（VS Code 拡張）

ccnavi のチケットが、どの作業ツリーでどこまで進んでいるかをカンバンで見る。
列は提案の置き場（未着手 / 作業中 / 完了 / 取り消し）で、写し（未承認・承認済・閉）、
フェーズの印（レビュー依頼済・レビュー済・省略）、ゲートの開閉、作業ツリーの有無、
実績のリスクをカードのバッジで出す。親カードにはフェーズの一覧と段階が付く。

人の承認はボタンから統合ターミナルへコマンドを送る。拡張は承認を自分では実行しない。
ccnavi は `--approve` / `accept` / `wrapup` を端末から打つものと決めていて（エージェントが
人の合意を出せないための壁）、拡張の子プロセスもその壁の外に置く。y/N は人がターミナルで押す。

同じ拡張に「ルール設定画面」がある。ルールファイル（`rules.yml`）を画面で直し、保存する前に
「この操作はどう判定されるか」を試し、hook の一覧を眺める。判定は実行ファイルの
`--test --json` / `--test-samples --json` を通り、拡張は glob も regex も自分で当てない。

入れると VS Code の左端（アクティビティバー）に ccnavi のアイコンが出る。押すとサイドパネルに
「チケット画面」と「ルール設定画面」の 2 つの入口が並ぶ。

- 出力の形: ccnavi の README「ボードの JSON」「試験の JSON」
- 設計: ccnavi.md §24.10、要求 REQ-DIA-02 / REQ-DIA-03 / REQ-DIA-06

## できること

| コマンド（コマンドパレット） | 動き |
|---|---|
| `ccnavi ボード: ボードを開く` | ボードを開く。既に開いていれば増やさず前面に出す |
| `ccnavi ボード: ボードを更新` | `ccnavi --explain --json` を走らせ直して内容を差し替える |
| `ccnavi ボード: 承認待ちを承認する（--approve）` | ボードを開かずに `--approve` をターミナルへ送る |
| `ccnavi ボード: ルール設定画面を開く` | ルール設定画面を開く。既に開いていれば前面に出す |

サイドパネル（左端の ccnavi アイコン）の「チケット画面」「ルール設定画面」は、それぞれ
`ボードを開く` と `ルール設定画面を開く` と同じ。

### ルール設定画面

タブは 3 つ。

| タブ | 何ができるか |
|---|---|
| ルール | `rules.yml` を区画（deny / ask / allow）ごとに一覧し、id・match・glob か regex・message・additionalContext（当たるたびにモデルへ渡す文）・additionalContextOnce（文脈で最初に当たったときだけ渡す文）を直す。足す・消す・上下に動かす・区画を移す。保存の前に一時ファイルへ書いて `--lint` を通し、error があれば保存しない |
| 判定を試す | ツール名と subject を入れて `--test --json` に掛ける。判定・根拠コード・当たったルール（翻訳後の正規表現まで）・返る文面と、そのツールで走る hook を出す。「見本を一括で流す」は `--test-samples --json` で見本をすべて回し、期待と食い違ったものを赤く出す。どちらも**編集中の内容**で試す（保存は要らない） |
| hook | `.claude/settings.json` と `.claude/settings.local.json` の hooks を読むだけの一覧。書き換えない。利用者ごとの設定（`~/.claude/settings.json`）は載らない |

守っていること。

- **判定は実行ファイルが出す。** 拡張は `--test` の答えを並べるだけで、glob も regex も自分で当てない。
  hook の「走る／走らない」だけは matcher を拡張で当てる（Claude Code の配線であって ccnavi の判定ではない）
- **作業中のチケットがある間は保存できない。** 提案が `doing` のチケットが 1 件でもあれば（どの作業ツリーでも）、
  編集はできるが保存ボタンが押せない。hook はツール呼び出しのたびにルールを読み直すので、セッションの途中で
  判定が変わるのを避ける。ボードの JSON が読めないときも保存しない（確かめられないなら閉じる側）
- **外で変わったら上書きしない。** 読み込んだときの更新時刻と保存時のそれが違えば止める。編集中に
  `rules.yml` や `settings.json` が変わると上部に「外で変わった」と出るので、再読込してから直し直す
- **コメントを残す。** `rules.yml` のコメントと折り返しは、変えていない場所ではそのまま。変えた欄も
  引用符や折り返しの書き方は元のまま。新しく足すルールは glob / regex を単引用符で囲む
- **記録を汚さない。** 試し打ちは `--log "" --state "" --approved ""` で走らせ、`log.jsonl` に残さない
- 上部に `CCNAVI_MODE` が `enable` でないときの注意が出る。試す判定は enable のときの答え

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
| `ccnaviBoard.samplesPath` | ルール設定画面が一括で流す見本。既定は `testdata/rule-samples.yml`。相対ならワークスペースルートから |

ルールファイルの場所は `.claude/settings.json` の `env.CCNAVI_RULES`、無ければ `.claude/ccnavi/rules.yml`。

## 組み立てとインストール

`vscode-extension/ccnavi-board/` で。

```sh
pnpm install --frozen-lockfile
pnpm run compile   # tsc -p . で out/ に出し、esbuild で out/extension.js に束ねる
pnpm test          # tsc のあと node --test out/test/*.test.js
pnpm run package   # scripts/package.sh: install → compile → test → vsce package
```

`pnpm run package` は `dist/ccnavi-board-<version>.vsix`（リポジトリの `dist/`、gitignore 済み）に出す。
入れるには次を打つ。Marketplace には出さない。

```sh
code --install-extension dist/ccnavi-board-0.2.0.vsix
```

`node --test` にはディレクトリではなくグロブ（`out/test/*.test.js`）を渡す。

実行時の依存は `yaml`（コメントを残して書き戻すため）の 1 つ。vsix には `node_modules/` を入れず、
`scripts/bundle.js`（esbuild）が本体ごと `out/extension.js` に束ねる。テストは束ねる前の
`out/src/` を使う。

生成物を消すときは `pnpm run clean`（`scripts/clean.js`）。消すのは `node_modules/` と `out/` の
2 つだけで、引数は取らない。`rm -rf` は ccnavi のルール（`recursive-delete`）が止めるので使わない。
作業ツリー（`.claude/worktrees/<名前>`）でこの拡張を組み立てたら、`git worktree remove` の**前に**
これを走らせる。pnpm の `node_modules/.pnpm/` は深くて symlink も含み、git の削除が途中で止まって
抜け殻が残ることがある。

## デバッグ実行（拡張ホストで動かす）

1. `pnpm install && pnpm run compile`
2. VS Code で `vscode-extension/ccnavi-board/` を開く
3. `F5`（実行とデバッグ → 拡張機能）で拡張開発ホストを起動する
4. そのウィンドウで、ccnavi を入れたリポジトリ（`.claude/ccnavi/` を持つフォルダ）を開く
5. コマンドパレットから `ccnavi ボード: ボードを開く` を実行する

## 手動確認の手順

`extension.ts` / `board-panel.ts` / `rules-panel.ts` / `sidebar.ts` / `terminal.ts` / `ccnavi.ts` は
VS Code の API か子プロセスに触れるので単体テストの対象外。次を拡張開発ホストで確かめる。チケットのある状態を作るには
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
| 14 | 左端のアイコン | 拡張を入れる | アクティビティバーに ccnavi のアイコン。押すと「チケット画面」「ルール設定画面」の 2 つ |
| 15 | ルール設定画面が開く | サイドパネルの「ルール設定画面」 | deny / ask / allow の 3 区画にルールが並ぶ。上部に dry-run の注意 |
| 16 | 編集中の内容で判定 | あるルールの glob を変え、保存せずに「判定を試す」で当たる subject を入れて「判定」 | 変えた後の glob で判定される。当たったルールがルール一覧で枠付きになる。「このツールで走る hook」に PreToolUse / PostToolUse の該当行と Stop などが並ぶ |
| 17 | 見本の一括 | 「見本を一括で流す」 | 区画ごとの件数と食い違い 0 件。glob を壊してから流すと食い違いの行が赤くなる |
| 18 | lint で止まる | message を空にした deny のルールを作って「保存」 | 下部に `--lint` の error が出て保存されない |
| 19 | 作業中はロック | 子チケットを `start` してから「保存」 | 上部に赤で「作業中のチケットがある」。保存ボタンが押せない。`done` にすると押せる |
| 20 | 外で変わった | 画面を開いたまま `rules.yml` をエディタで変える | 上部に「外で変わった」。この状態で「保存」を押しても上書きしない |
| 21 | コメントが残る | ルールの message を 1 つ変えて保存し、`git diff` を見る | 変えた行だけが差分。先頭やルール間のコメントは残っている |
| 22 | 未保存の再読込 | 何か変えてから「再読込」 | 「捨てて読み直す？」の確認。「読み直す」で編集が消える |

## 構成

```
src/
  extension.ts        コマンド登録とサイドパネルの登録（vscode に依存する）
  sidebar.ts          左端のアイコンから開くサイドパネルの 2 つの入口（vscode に依存する）
  board-panel.ts      ボードの Webview パネルの生成・更新・破棄、監視、操作の受け付け（vscode に依存する）
  rules-panel.ts      ルール設定画面の Webview パネル。判定・検証・保存の受け付け（vscode に依存する）
  terminal.ts         「ccnavi」ターミナルの用意とコマンドの送信（vscode に依存する）
  ccnavi.ts           実行ファイルの探索と --explain --json / --test --json / --test-samples --json / --lint の実行（Node の子プロセス）
  core/
    model.ts          ボードの JSON の形（実行ファイルとの契約）と読み取り
    testmodel.ts      試験の JSON の形（--test --json / --test-samples --json）と読み取り
    board.ts          列とカードへの組み立て、操作の有無
    render.ts         ボードの HTML（外部資源なし、テーマ変数だけ）
    rules-render.ts   ルール設定画面の HTML と、その中で動くスクリプト
    rules-doc.ts      rules.yml の読み書き（yaml の Document でコメントを残す）
    hooks.ts          settings.json の hooks の読み取りと、ツール名で走る hook の絞り込み
    lock.ts           保存できるか（doing のチケットの有無）
    commands.ts       ターミナルに送るコマンド行
    locate.ts         実行ファイルの探索順
media/
  icon.svg            アクティビティバーのアイコン
test/
  fixtures/board.json 実行ファイルの出力の実例。Python 側の tests/test_board.py が書き出す
  fixtures/test.json, samples.json  --test --json / --test-samples --json の実例。tests/test_test_json.py が書き出す
  *.test.ts           core の単体テスト CB-T01〜CB-T52
scripts/
  bundle.js           esbuild で本体を out/extension.js に束ねる
  package.sh          vsix の組み立て
```

`core/` は `vscode` を import しない。ここだけを `node --test` で試す。
