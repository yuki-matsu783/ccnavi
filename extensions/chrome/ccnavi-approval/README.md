# ccnavi 承認ボード（Chrome 拡張）

承認者がブラウザと PAT だけで、リモートのブランチの承認待ちを見て、承認・承認の取り下げ・レビュー済みを親のブランチへ書き、
issue から親のブランチを始める拡張。GitHub と GitLab の、ワークスペースとプロジェクトのリポジトリを扱う。
GitLab で ccnavi の書き込みどうしの競合を捕まえる seq ファイルは、本物の GitLab で前提を確かめるまで持たない。

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
                # 判定のコアの見本 test/fixtures/core-scenarios.json は、リポジトリの tests/ticket/test_core.py が
                # 書き出す（CCNAVI_CHROME_FIXTURE=1）
pnpm test:e2e   # 拡張を読み込んだ Chromium（Playwright、PLAYWRIGHT_BROWSERS_PATH）と模擬の GitHub・GitLab で実機の試験
                # （承認・取り下げ・レビュー済み・「始める」を含む。GitLab はセルフホストとして通信先に足したビルドで回す）
```

### ホストの応答の見本（レビュー済み）

GitHub と GitLab の 2 つ。どちらも本物の形に合わせて手で組んだもので、本物からは録っていない。GitLab の見本は `test/fixtures/host/gitlab/<場面>/`（`scene.json` に名前空間・プロジェクト・
親のブランチ・依頼を投稿したアカウントの id `poster`、ホストの応答は `mrs.json`・`discussions.<N>.json`・`reviewers.<N>.json`・`user.json`）。
代役は sh の試験の `tests/sh/gitlab_host.py` と拡張の試験の `test/helpers/gitlab-fixture.ts`、試験は `tests/sh/test_review_host_fixture.py` の
`GitLabHostFixtureTest` と拡張の CX-T144。結論（`conclusion.json`）を出すときは、`poster` が投稿した ccnavi の依頼のスレッドを数えない（目印は誰でも書けるので、書いたアカウントで見分ける）。
更新の手順は下の GitHub と同じ（本物は `glab api` で取る）。

MR のスレッドとレビューを取ってくる処理は、手元の sh（`.ccnavi/scripts/ccnavi-review.sh` の `find_mr`・`threads`・`reviews`）と
この拡張（`src/core/github.ts` の `reviewCopy`）の 2 か所にある。判定は Python の同じ関数なので、2 つが同じ結果を組めば同じ結論になる。
2 つが同じ結果を組むかは、手で組んだホストの応答の見本で確かめる。

- 置き場: `test/fixtures/host/github/<場面>/`。`scene.json`（持ち主・リポジトリ・親のブランチと、場面の説明）、ホストの応答
  （`pulls.json`・`threads.<k>.json`（GraphQL の `reviewThreads` のページ）・`reviews.<N>.json`（REST のページ）・`user.json`）、
  期待値（`expected.json` = sh がその見本から組んだ結果から `fetched_at` を除いたもの、`conclusion.json` = 変更要求と未解決の結論）
- 見本を返す代役は 2 つ（sh の試験の `tests/sh/github_host.py`、拡張の試験の `test/helpers/host-fixture.ts`）で、同じ規則で返す
- 試験: sh はリポジトリの `tests/sh/test_review_host_fixture.py`、拡張は `test/reviewed.test.ts` の CX-T129。どちらも同じ `expected.json` と比べる

ホストの API の形が変わったら（応答の欄が増える・名前が変わる・ページの切り方が変わる）、次の手順で手作業で更新する。

1. 見本を直す。本物の MR を `gh api`（REST）と `gh api graphql`（`ccnavi-review.sh` の `threads` と同じ問い合わせ）で取り、
   該当の場面のファイルを置き換えるか、形の変わった欄だけを手で直す。トークン・個人の名前・社内の URL は見本に残さない
2. リポジトリのルートで `CCNAVI_HOST_FIXTURE=1 uv run python -m unittest tests.sh.test_review_host_fixture` を回し、sh が組んだ結果で
   `expected.json` と `conclusion.json` を書き直す。差分が意図どおりか（sh が新しい形を正しく読めているか）を目で見る。
   読めていなければ `ccnavi-review.sh` を直す
3. `pnpm test` を回す。CX-T129 が落ちたら `src/core/github.ts` を直して sh と同じ結果にする
4. 見本・期待値・sh・TS の変更を同じコミットに入れる（片方だけ変えると、もう片方の試験が落ちる）

試験の名前の ID は `CX-T<番号>`。足すときは最後尾の次を採る。

本物の GitHub・GitLab に繋いで確かめる手順は [VERIFY.md](VERIFY.md)。
