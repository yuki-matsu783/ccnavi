# ccnavi 承認ボード（Chrome 拡張）

承認者がブラウザと PAT だけで、リモートのブランチの承認待ちを見て、承認・承認の取り下げ・レビュー済みを親のブランチへ書き、
issue から親のブランチを始める拡張。ADR-0093 の段階 5（GitHub と GitLab、ワークスペースとプロジェクトのリポジトリ）。
GitLab の `seq` ファイル（8.4 の 2 段目）は持たない（段階 5b）。

## 何をするか

- 統合先（設定の名前か、ホストのデフォルトブランチ）と、直近 N 日・指定のブランチ（表示用）の置き場を GitHub・GitLab の API で読む
- 親子チケット（親のブランチ）ごとに、統合先・`P`・先行の閉包の `P_X` だけを入力にして、同梱の ccnavi の `--approve --preview --json` を
  Pyodide の上で動かし、承認待ちと承認の対象にしない提案を並べる（ADR-0093 の D2・8.1）。判定は Python が出し、TS は並べるだけ（ADR-0035）
- 統合先の `CCNAVI_COMPAT` と同梱の互換の版が違えば、どちらを更新するかを出し、承認と取り下げを出さない（7.3）
- 承認（8.3・8.4）: 押すと親子チケット 1 組ぶんを読み直し、Python に見せた一覧とダイジェストを比べさせ、Python が返した書くもの（Changes）を
  GraphQL の `createCommitOnBranch`（`expectedHeadOid` = 読んだ先頭）の 1 コミットで親のブランチへ書く。先頭が動いていたら
  読み直して判定し直し、ダイジェストが同じなら書き直す（3 回まで）。書いた後、新しい先頭の中身が書いたとおりかを blob の sha で確かめる
- 取り下げ（8.8）: 着手前の新規の承認だけ。承認コミット（`doing/<識別子>.md` を足した、親が 1 つの最新のコミットで、親のブランチの first-parent の鎖の上にあるもの）の親の提案を
  そのまま `todo/` へ戻す 1 コミット。出すかは Python が決める
- レビュー済み（8.9。段階 4）: 依頼済み（`phases/<親>/<N>.requested`）でまだレビュー済みでないフェーズに、MR のスレッドと
  レビューを読んで出す。スレッドの本文は承認の画面と同じ規則で描く（DOMPurify で消毒、隠れる書き方を通さない、HTML コメントは
  見える文字にする）。通るか（依頼の記録・依頼の後に人が見るものが動いていないか・同じ MR か・変更要求・未解決のスレッド）は同梱の
  ccnavi の `confirm`（手元の `ccnavi review confirm` と同じコア）が決め、通るときだけ「レビュー済みにする」を出す。押すと
  親子チケットとスレッドを読み直して判定し直し、レビュー待ちの子の `done/` への移動とマーカー（`actor` = PAT の持ち主、`via: chrome`）を
  1 コミットで書く。依頼の後に先頭が動いていれば compare API の変更の一覧を Python に渡し、打ち切られた（300 件）・依頼時の
  先頭が祖先でないときは「動いた」と数えて書かない。MR に Approve があれば書く前に外れうることを出す（8.10）。
  マーカーの `actor` は押した人ではなく PAT の持ち主（手元の confirm はトークンの持ち主）。MR の作者本人でも通る（自己レビューは防がない）。
  MR は親のブランチの開いた MR を引き、依頼の記録の番号と照合する。レビュー済みのフェーズには重ねて書かない
- PAT の期限（D25）: service worker が応答ヘッダ `github-authentication-token-expiration` から読み、読めなければ登録のときの日付。
  切れる 7 日前からボードの帯とアイコンのバッジで知らせる（`chrome.alarms` で 1 日 1 回）
- GitLab（段階 5）: 同じ操作を REST（v4）で読み書きする（`src/core/gitlab.ts`）。Commits API には「先頭がこの sha のときだけ」の指定が
  無いので、書く直前に先頭を読み、書き換える・消すファイルに `last_commit_id` を付け（同じファイルを他人が変えていれば GitLab が断る）、
  書いた後にコミットの親が読んだ先頭かを確かめる（8.4 の 1 段目）。違えば直前の状態で同じ時刻で判定し直し、書くものが同じなら残し、
  違えば打ち消しのコミット（各ファイルに自分のコミットを `last_commit_id` で付ける）を積んで読み直す。打ち消しのコミットも別の書き込みとぶつかって 2 回までに積めない・途中で
  ホストが落ちたときは人の対応に切り替え、ボードに親子チケットを「要確認」で出す（人が確認を挟んで「確かめた」を押すまで。このブラウザの
  `chrome.storage.local` にだけ保存し、ほかの承認者には見えない。要確認の親子チケットにはこのブラウザから書かない）。PAT の期限は `GET /personal_access_tokens/self` を 1 日 1 回読む
- プロジェクトのリポジトリ（段階 5。3.3 の 7）: 設定画面でプロジェクト名（手元の `projects/<名前>` の名前）と、先に登録したワークスペースの
  リポジトリを選ぶ。置き場の表記・共通層・互換のマーカーはワークスペースの統合先から、閉じたもの（`done/`）とプロジェクトの層はプロジェクトの統合先
  から読み、層は D28 の計算（共通層を写したもの）で判定する。Chrome の登録の名前は手元のディレクトリ名と揃える（ずれると、手元で取り込んだ後に
  判定し直したとき親子チケットが止まる）
- 「始める」（段階 5。8.6）: ボードの「issue を読む」で開いた issue を読み、「始める」を押すと、issue の番号から決めた識別子
  （`i<番号>`・`<プロジェクト名>-i<番号>`。同梱の ccnavi の `ticket.issue_identifier`）の親のブランチを統合先の今の先頭から作る。
  統合先の `done/` にある・同じ名前のブランチがある・開いた親子チケットに同じ識別子がある・予約の名前・互換の版が違う、のどれかなら作らない。
  PR/MR は作らない（最初の push の後に `ccnavi-review.sh request` が作る）

## 構成

| 場所 | 役目 |
|---|---|
| `src/background/` | service worker。PAT を持ち、ホストの API を呼ぶのはここだけ（5.5 の 4）。PAT の期限のバッジ |
| `src/board/` | ボード（拡張のページ）。Worker を起こし、読んだ中身を Python に渡し、答えを描く |
| `src/options/` | 設定画面。リポジトリ（統合先の名前・直近の日数・指定のブランチ）と PAT |
| `src/worker/` | Pyodide を動かす Web Worker |
| `src/core/` | 画面に依らない部品（通信先と manifest、GitHub の読み書き（`github.ts`）、GitLab の読み書き（`gitlab.ts`）、「始める」（`start.ts`）、画面と service worker の約束、読み取りの流れ、レビュー済みの材料の読み（`reviewed.ts`）、承認・取り下げ・レビュー済みの流れ（`write.ts`）、PAT の期限、Markdown の消毒、描画） |
| `py/ccnavi_chrome.py` | Pyodide の上の入口。MEMFS に仮のツリーと取り込みの同期状態に当たるものを組んで今の ccnavi を呼ぶ。判定のコア（`ccnavi.core`）の `plan`・`withdraw`・`confirm` の答え（書くもの）を拡張が 1 コミットにする |
| `hosts.json` | 焼き込む通信先（D24）。組織ごとのビルドはこれを替える |

## 組み立て

```sh
cd chrome-extension/ccnavi-approval
pnpm install --frozen-lockfile
pnpm build                                  # dist/ に組む。Chrome の「パッケージ化されていない拡張機能を読み込む」で dist/ を選ぶ
node scripts/build.js --hosts <一覧の JSON> # 組織ごとのビルド（GHES など）
```

- Pyodide（約 13MB）はリポジトリに入れない。npm の `pyodide` から写し、`scripts/pyodide-files.json` のハッシュと照らし合わせる
- PyYAML は純 Python 版を PyPI の sdist から取り、版とハッシュはリポジトリの `uv.lock` から読む（手元の ccnavi と同じ版）。
  プロキシの内側では `NODE_USE_ENV_PROXY=1` を付ける
- ccnavi は組み立てたときのリポジトリの `ccnavi/` をそのまま同梱する（.pyc 付きの zip）

## 試験

```sh
pnpm test       # 単体・Node の上の Pyodide での組み立て・手元の CPython との突き合わせ（uv を使う）。
                # 判定のコアの見本 test/fixtures/core-scenarios.json は、リポジトリの tests/ticket/test_core.py が
                # 書き出す（CCNAVI_CHROME_FIXTURE=1）
pnpm test:e2e   # 拡張を読み込んだ Chromium（Playwright、PLAYWRIGHT_BROWSERS_PATH）と模擬の GitHub・GitLab で実機の試験
                # （承認・取り下げ・レビュー済み・「始める」を含む。GitLab はセルフホストとして通信先に足したビルドで回す）
```

### ホストの応答の見本（レビュー済み。ADR-0093 の 8.9）

GitHub（段階 4）と GitLab（段階 5）の 2 つ。どちらも本物の形に合わせて手で組んだもので、本物からは録っていない。GitLab の見本は `test/fixtures/host/gitlab/<場面>/`（`scene.json` に名前空間・プロジェクト・
親のブランチ・依頼を投稿したアカウントの id `poster`、ホストの応答は `mrs.json`・`discussions.<N>.json`・`reviewers.<N>.json`・`user.json`）。
代役は sh の試験の `tests/sh/gitlab_host.py` と拡張の試験の `test/helpers/gitlab-fixture.ts`、試験は `tests/sh/test_review_host_fixture.py` の
`GitLabHostFixtureTest` と拡張の CX-T144。結論（`conclusion.json`）を出すときは、`poster` が投稿した ccnavi の依頼のスレッドを数えない（11.8.1 の決定 C）。
更新の手順は下の GitHub と同じ（本物は `glab api` で取る）。

MR のスレッドとレビューを取ってくる処理は、手元の sh（`.ccnavi/scripts/ccnavi-review.sh` の `find_mr`・`threads`・`reviews`）と
この拡張（`src/core/github.ts` の `reviewCopy`）の 2 か所にある。判定は Python の同じ関数なので、2 つが同じコピーを組めば同じ結論になる。
2 つが同じコピーを組むかは、手で組んだホストの応答の見本で確かめる。

- 置き場: `test/fixtures/host/github/<場面>/`。`scene.json`（持ち主・リポジトリ・親のブランチと、場面の説明）、ホストの応答
  （`pulls.json`・`threads.<k>.json`（GraphQL の `reviewThreads` のページ）・`reviews.<N>.json`（REST のページ）・`user.json`）、
  期待値（`expected.json` = sh がその見本から組んだコピーから `fetched_at` を除いたもの、`conclusion.json` = 変更要求と未解決の結論）
- 見本を返す代役は 2 つ（sh の試験の `tests/sh/github_host.py`、拡張の試験の `test/helpers/host-fixture.ts`）で、同じ規則で返す
- 試験: sh はリポジトリの `tests/sh/test_review_host_fixture.py`、拡張は `test/reviewed.test.ts` の CX-T129。どちらも同じ `expected.json` と比べる

ホストの API の形が変わったら（応答の欄が増える・名前が変わる・ページの切り方が変わる）、次の手順で手作業で更新する。

1. 見本を直す。本物の MR を `gh api`（REST）と `gh api graphql`（`ccnavi-review.sh` の `threads` と同じ問い合わせ）で取り、
   該当の場面のファイルを置き換えるか、形の変わった欄だけを手で直す。トークン・個人の名前・社内の URL は見本に残さない
2. リポジトリのルートで `CCNAVI_HOST_FIXTURE=1 uv run python -m unittest tests.sh.test_review_host_fixture` を回し、sh が組んだコピーで
   `expected.json` と `conclusion.json` を書き直す。差分が意図どおりか（sh が新しい形を正しく読めているか）を目で見る。
   読めていなければ `ccnavi-review.sh` を直す
3. `pnpm test` を回す。CX-T129 が落ちたら `src/core/github.ts` を直して sh と同じコピーにする
4. 見本・期待値・sh・TS の変更を同じコミットに入れる（片方だけ変えると、もう片方の試験が落ちる）

試験の名前の ID は `CX-T<番号>`。足すときは最後尾の次を採る。

本物の GitHub・GitLab に繋いで確かめる手順は [VERIFY.md](VERIFY.md)。
