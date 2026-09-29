# ccnavi 承認ボード（Chrome 拡張）

承認者がブラウザと PAT だけで、リモートのブランチの承認待ちを見る拡張。ADR-0093 の段階 1（読み取り専用ボード）。
承認・取り下げ・レビュー済み・「始める」は持たない（段階 3 以降）。

## 何をするか

- 統合先（設定の名前か、ホストのデフォルトブランチ）と、直近 N 日・指定のブランチ（表示用）の置き場を GitHub の API で読む
- 家族（親のブランチ）ごとに、統合先・`P`・先行の閉包の `P_X` だけを入力にして、同梱の ccnavi の `--approve --preview --json` を
  Pyodide の上で動かし、承認待ちと承認の対象にしない提案を並べる（ADR-0093 の D2・8.1）。判定は Python が出し、TS は並べるだけ（ADR-0035）
- 統合先の `CCNAVI_COMPAT` と同梱の互換の版が違えば、どちらを更新するかを出す（7.3）
- GitLab は読まない（段階 5）。設定画面には焼き込んだホストとして出る

## 構成

| 場所 | 役目 |
|---|---|
| `src/background/` | service worker。PAT を持ち、ホストの API を呼ぶのはここだけ（5.5 の 4） |
| `src/board/` | ボード（拡張のページ）。Worker を起こし、読んだ中身を Python に渡し、答えを描く |
| `src/options/` | 設定画面。リポジトリ（統合先の名前・直近の日数・指定のブランチ）と PAT |
| `src/worker/` | Pyodide を動かす Web Worker |
| `src/core/` | 画面に依らない部品（通信先と manifest、GitHub の読み取り、画面と service worker の約束、読み取りの流れ、Markdown の消毒、描画） |
| `py/ccnavi_chrome.py` | Pyodide の上の入口。MEMFS に仮のツリーを組んで今の ccnavi を呼ぶ |
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
pnpm test       # 単体・Node の上の Pyodide での組み立て・手元の CPython との突き合わせ（uv を使う）
pnpm test:e2e   # 拡張を読み込んだ Chromium（Playwright、PLAYWRIGHT_BROWSERS_PATH）と模擬の GitHub で実機の試験
```

試験の名前の ID は `CX-T<番号>`。足すときは最後尾の次を採る。
