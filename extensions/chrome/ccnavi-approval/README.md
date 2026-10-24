# ccnavi 承認ボード（Chrome 拡張）

承認者がブラウザと PAT だけで、リモートのブランチの承認待ちを見て、承認・レビュー済みを親のブランチへ書き、
issue から親のブランチを始める拡張。GitHub と GitLab の、ワークスペースとプロジェクトのリポジトリを扱う。
GitLab での ccnavi の書き込みどうしの競合の検出は、実際の GitLab で前提を確かめるまで未実装。
PAT に要る権限（GitHub は Contents・Pull requests・Issues の Read and write、GitLab は `api` スコープと Developer 以上）は、設定画面の案内と [VERIFY.md](VERIFY.md) の表にある。

承認者の操作を、作業者の開発環境やセッションの外に置くための入口で、判定のコアは手元の ccnavi と同じ Python を同梱して使う。
同期はリアルタイムではなく、書いた結果は作業側の次の fetch / sync で届く（ccnavi の docs/guide/multi-pc.md）。

何をするか（ふるまい）は [docs/requirements.md](docs/requirements.md)、構成は [docs/design.md](docs/design.md) にある。この README には、組み立てと試験の手順だけを書く。

## 組み立て

```sh
cd extensions/chrome/ccnavi-approval
pnpm install --frozen-lockfile
pnpm build                                  # dist/ に組む。Chrome の「パッケージ化されていない拡張機能を読み込む」で dist/ を選ぶ
node scripts/build.js --hosts <一覧の JSON> # 組織ごとのビルド（GHES など）
```

- Pyodide（約 13MB）はリポジトリに入れない。npm の `pyodide` からコピーし、`scripts/pyodide-files.json` のハッシュと照らし合わせる
- PyYAML は純 Python 版を PyPI の sdist から取り、版とハッシュはリポジトリの `uv.lock` から読む（手元の ccnavi と同じ版）。
  プロキシの内側では `NODE_USE_ENV_PROXY=1` を付ける
- ccnavi は組み立てたときのリポジトリの `src/ccnavi/` をそのまま同梱する（.pyc 付きの zip）

## 試験

```sh
pnpm test       # 単体・Node の上の Pyodide での組み立て・手元の CPython との突き合わせ（uv を使う）。
                # 判定のコアの見本 test/fixtures/core-scenarios.json は、リポジトリの tests/ticket/test_swap_points.py が
                # 書き出す（CCNAVI_CHROME_FIXTURE=1）
pnpm test:e2e   # 拡張を読み込んだ Chromium（Playwright、PLAYWRIGHT_BROWSERS_PATH）と模擬の GitHub・GitLab で実機の試験
                # （承認・レビュー済み・「始める」を含む。GitLab はセルフホストとして通信先に足したビルドで回す）
```

### ホストの応答の見本（レビュー済み）

GitHub と GitLab の 2 つ。どちらも実際の形に合わせて手で組んだもので、実物からは録っていない。GitHub の形は、2026-10-12 に実物と突き合わせて、
sh の読み方と合うことを確かめた（1 アカウントだけ。`CHANGES_REQUESTED` は未確認）。`tests/manual/github/probe_github.py` が見るのは
`reviewThreads` の欄・解決済みの区別・書き換えられた行の `line` が null になること・COMMENTED のレビュー・gh と curl の両経路。
ページング（`first:1` と `after: <endCursor>`）は probe には無く、同じ問い合わせの `first` を 1 にして手で打って確かめた
（2026-10-12、2 本のスレッドで 1 ページ目 `hasNextPage: true`、`endCursor` を渡した 2 ページ目に残りの 1 本）。GitLab の見本は `test/fixtures/host/gitlab/<場面>/`（`scene.json` に名前空間・プロジェクト・
親のブランチ・依頼を投稿したアカウントの id `poster`、ホストの応答は `mrs.json`・`discussions.<N>.json`・`reviewers.<N>.json`・`user.json`）。
代役は sh の試験の `tests/sh/gitlab_host.py` と拡張の試験の `test/helpers/gitlab-fixture.ts`、試験は `tests/sh/test_review_host_fixture.py` の
`GitLabHostFixtureTest` と拡張の CX-T144。結論（`conclusion.json`）を出すときは、`poster` が投稿した ccnavi の依頼のスレッドを数えない（目印は誰でも書けるので、書いたアカウントで見分ける）。
ホストの自動判定の表は `src/core/hostclass.ts`（設定画面は種類を手で選ぶので、この表は GHE.com の接続先の組み立てと、sh・実行ファイルとの突き合わせに使う。`github.com`・`ghe.com`・`*.ghe.com` は GitHub、それ以外は GitLab。`tests/fixtures/host-kinds.json` の表で sh・実行ファイルと突き合わせる）。設定画面でユーザが明示した種類は自動判定より優先する。GHE.com を GitHub として登録すると API は `https://api.<ホスト>`（GraphQL は `/graphql`）、許可は画面と API の origin の両方に取る。それ以外の GitHub の登録は Enterprise Server（`/api/v3`・`/api/graphql`）。未登録のホストへ自動で通信したり PAT を送ったりしない。GHE.com の URL の規則は未検証。
依頼コメントへの返信の見本（`replies`・`replies-accepted`）は、system のノートを除いたノートから代表のノートと `own` を決め、返信を指摘として数える。実物の GitLab で返信がスレッドになり resolvable になるかは未検証。
更新の手順は下の GitHub と同じ（実物は `glab api` で取る）。

MR のスレッドとレビューを取ってくる処理は、手元の sh（`.ccnavi/scripts/ccnavi-review.sh` の `find_mr`・`threads`・`reviews`）と
この拡張（`src/core/github.ts` の `reviewCopy`）の 2 か所にある。判定は Python の同じ関数なので、2 つが同じ結果を組めば同じ結論になる。
2 つが同じ結果を組むかは、手で組んだホストの応答の見本で確かめる。

- 置き場: `test/fixtures/host/github/<場面>/`。`scene.json`（持ち主・リポジトリ・親のブランチと、場面の説明）、ホストの応答
  （`pulls.json`・`threads.<k>.json`（GraphQL の `reviewThreads` のページ）・`reviews.<N>.json`（REST のページ）・`user.json`）、
  期待値（`expected.json` = sh がその見本から組んだ結果から `fetched_at` を除いたもの、`conclusion.json` = 変更要求と未解決の結論）
- 見本を返す代役は 2 つ（sh の試験の `tests/sh/github_host.py`、拡張の試験の `test/helpers/host-fixture.ts`）で、同じ規則で返す
- 試験: sh はリポジトリの `tests/sh/test_review_host_fixture.py`、拡張は `test/reviewed.test.ts` の CX-T129。どちらも同じ `expected.json` と比べる

ホストの API の形が変わったら（応答の欄が増える・名前が変わる・ページの切り方が変わる）、次の手順で手作業で更新する。

1. 見本を直す。実際の MR を `gh api`（REST）と `gh api graphql`（`ccnavi-review.sh` の `threads` と同じ問い合わせ）で取り、
   該当の場面のファイルを置き換えるか、形の変わった欄だけを手で直す。トークン・個人の名前・社内の URL は見本に残さない
2. リポジトリのルートで `CCNAVI_HOST_FIXTURE=1 uv run python -m unittest tests.sh.test_review_host_fixture` を回し、sh が組んだ結果で
   `expected.json` と `conclusion.json` を書き直す。差分が意図どおりか（sh が新しい形を正しく読めているか）を目で確かめる。
   読めていなければ `ccnavi-review.sh` を直す
3. `pnpm test` を回す。CX-T129 が失敗したら `src/core/github.ts` を直して sh と同じ結果にする
4. 見本・期待値・sh・TS の変更を同じコミットに入れる（片方だけ変えると、もう片方の試験が失敗する）

試験の名前の ID は `CX-T<番号>`。足すときは最後尾の次を採る。

実際の GitHub・GitLab に繋いで確かめる手順は [VERIFY.md](VERIFY.md)。
