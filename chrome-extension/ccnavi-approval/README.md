# ccnavi 承認ボード（Chrome 拡張）

承認者がブラウザと PAT だけで、リモートのブランチの承認待ちを見て、承認と承認の取り下げを親のブランチへ書く拡張。
ADR-0093 の段階 3（GitHub・ワークスペースのリポジトリだけ）。レビュー済み・「始める」・GitLab は持たない（段階 4・5）。

## 何をするか

- 統合先（設定の名前か、ホストのデフォルトブランチ）と、直近 N 日・指定のブランチ（表示用）の置き場を GitHub の API で読む
- 家族（親のブランチ）ごとに、統合先・`P`・先行の閉包の `P_X` だけを入力にして、同梱の ccnavi の `--approve --preview --json` を
  Pyodide の上で動かし、承認待ちと承認の対象にしない提案を並べる（ADR-0093 の D2・8.1）。判定は Python が出し、TS は並べるだけ（ADR-0035）
- 統合先の `CCNAVI_COMPAT` と同梱の互換の版が違えば、どちらを更新するかを出し、承認と取り下げを出さない（7.3）
- 承認（8.3・8.4）: 押すと家族 1 つぶんを読み直し、Python に見せた一覧と指紋を比べさせ、Python が返した書くもの（Changes）を
  GraphQL の `createCommitOnBranch`（`expectedHeadOid` = 読んだ先頭）の 1 コミットで親のブランチへ書く。先頭が動いていたら
  読み直して判定し直し、指紋が同じなら書き直す（3 周まで）。書いた後、新しい先頭の中身が書いたとおりかを blob の sha で確かめる
- 取り下げ（8.8）: 着手前の新規の承認だけ。承認コミット（`doing/<識別子>.md` を足した、親が 1 つの最新のコミット）の親の提案を
  そのまま `todo/` へ戻す 1 コミット。出すかは Python が決める
- PAT の期限（D25）: service worker が応答ヘッダ `github-authentication-token-expiration` から読み、読めなければ登録のときの日付。
  切れる 7 日前からボードの帯とアイコンのバッジで知らせる（`chrome.alarms` で 1 日 1 回）
- GitLab は読まない（段階 5）。設定画面には焼き込んだホストとして出る

## 構成

| 場所 | 役目 |
|---|---|
| `src/background/` | service worker。PAT を持ち、ホストの API を呼ぶのはここだけ（5.5 の 4）。PAT の期限のバッジ |
| `src/board/` | ボード（拡張のページ）。Worker を起こし、読んだ中身を Python に渡し、答えを描く |
| `src/options/` | 設定画面。リポジトリ（統合先の名前・直近の日数・指定のブランチ）と PAT |
| `src/worker/` | Pyodide を動かす Web Worker |
| `src/core/` | 画面に依らない部品（通信先と manifest、GitHub の読み書き、画面と service worker の約束、読み取りの流れ、承認と取り下げの流れ（`write.ts`）、PAT の期限、Markdown の消毒、描画） |
| `py/ccnavi_chrome.py` | Pyodide の上の入口。MEMFS に仮のツリーと取り込みの控え相当を組んで今の ccnavi を呼ぶ。判定のコア（`ccnavi.core`）の `plan`・`withdraw` の答え（書くもの）を拡張が 1 コミットにする。`confirm` は段階 4 |
| `hosts.json` | 焼き込む通信先（D24）。組織ごとのビルドはこれを替える |

## 組み立て

```sh
cd chrome-extension/ccnavi-approval
pnpm install --frozen-lockfile
pnpm build                                  # dist/ に組む。Chrome の「パッケージ化されていない拡張機能を読み込む」で dist/ を選ぶ
node scripts/build.js --hosts <一覧の JSON> # 組織ごとのビルド（GHES など）
```

- Pyodide（約 13MB）はリポジトリに入れない。npm の `pyodide` から写し、`scripts/pyodide-files.json` のハッシュと突き合わせる
- PyYAML は純 Python 版を PyPI の sdist から取り、版とハッシュはリポジトリの `uv.lock` から読む（手元の ccnavi と同じ版）。
  プロキシの内側では `NODE_USE_ENV_PROXY=1` を付ける
- ccnavi は組み立てたときのリポジトリの `ccnavi/` をそのまま同梱する（.pyc 付きの zip）

## 試験

```sh
pnpm test       # 単体・Node の上の Pyodide での組み立て・手元の CPython との突き合わせ（uv を使う）。
                # 判定のコアの見本 test/fixtures/core-scenarios.json は、リポジトリの tests/ticket/test_core.py が
                # 書き出す（CCNAVI_CHROME_FIXTURE=1）
pnpm test:e2e   # 拡張を読み込んだ Chromium（Playwright、PLAYWRIGHT_BROWSERS_PATH）と模擬の GitHub で実機の試験（承認・取り下げを含む）
```

試験の名前の ID は `CX-T<番号>`。足すときは最後尾の次を採る。
