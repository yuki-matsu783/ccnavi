# 実際の GitHub・GitLab で確かめる手順

この拡張を実際の GitHub と GitLab に繋ぎ、模擬のホストでは確かめられなかったホストの振る舞い（応答の形・トークンの権限と期限など）と、
承認・レビュー済み・GitLab・「始める」が実際のホストでも同じように動くかを確かめるための手順です。
拡張の試験（`pnpm test`・`pnpm test:e2e`）は模擬のホストと手で組んだ見本で回しています。見本は実際の応答からは録っていません。`MockGitLab` が答える要求（ブランチ・tree・blob・コミット・compare・approvals・トークンの期限とスコープ・コミット／ブランチの作成など 22 本）は、`reads` の範囲で本物（GitLab CE 19.4.1）の記録と突き合わせました（見本由来の応答と、複数ページ・`update`/`delete` の action は対象外）。

**本番のリポジトリでは試しません。** 拡張は親のブランチへコミットを書き、「始める」はブランチを作ります。
使い捨てのリポジトリと、そのリポジトリだけで使える期限付きのトークンで試します。

## 1. 準備

### 1.1 手元の ccnavi を組み立てる

1. ccnavi のリポジトリ（このリポジトリ）で実行ファイルを組み立てます。

   ```sh
   uv run --with pyinstaller python build.py
   ```

2. 手元の `gh`（GitHub）と `glab`（GitLab）を、使い捨てのリポジトリにアクセスできるアカウントで認証しておきます。
   `ccnavi-review.sh` は `gh`・`glab` があればそれを、無ければ `curl` と `GITHUB_TOKEN`／`GITLAB_TOKEN` を使い、結果の組み立てに `jq` を使います。
   エージェント（手元）用のトークンは、拡張に登録する PAT とは別にします。

### 1.2 使い捨てのリポジトリを作る（GitHub と GitLab に 1 つずつ）

1. ホストの画面で空のリポジトリを作ります。名前は本番と紛れないもの（例 `ccnavi-verify-gh`・`ccnavi-verify-gl`）にし、private にします。
   README を 1 本入れてデフォルトブランチ（`main`）を作っておきます。
2. 手元に clone し、ccnavi を入れます（実行ファイル・sh・ルールのひな形・`.claude/settings.json` が置かれます）。

   ```sh
   git clone <使い捨てのリポジトリの URL> ~/ccnavi-verify-gh
   sh <ccnavi のリポジトリ>/scripts/ccnavi-setup.sh ~/ccnavi-verify-gh --mode dry-run
   ```

   `--mode dry-run` は一般の保護を記録するだけで、承認の関門と補助の保護は止めます。手元の hook が本番どおりに止める流れ（git のラッパーの拒否、write-and-push の取り込み）まで
   見る回は `--mode enable` にします（こちらを勧めます）。

3. `.gitignore` に `logs/` と `.claude/worktrees/` が無ければ足します（判定の記録とエージェントのワークツリーを送らないため）。

   ```sh
   cd ~/ccnavi-verify-gh
   grep -qxF 'logs/' .gitignore || printf 'logs/\n' >> .gitignore
   grep -qxF '.claude/worktrees/' .gitignore || printf '.claude/worktrees/\n' >> .gitignore
   ```

4. 置かれたもの（`.claude/settings.json`・`.ccnavi/config/common/`・`.ccnavi/config/project/`・`.ccnavi/scripts/`・`.gitignore`・`.vscode/settings.json`）を
   コミットして `main` へ push します。`.ccnavi/bin/` は `.gitignore` に入るので送りません。
   **統合先（`main`）への commit と push はユーザが素の `git` で打ちます。** ccnavi のラッパー（`ccnavi-git.sh`）は `main` へ直接は送らず、
   エージェントにも打たせません。
   拡張は統合先の `.ccnavi/scripts/ccnavi-common.sh` の `CCNAVI_COMPAT` と `.claude/settings.json` を読むので、この 2 つが統合先に要ります。
5. 統合先は `main`（デフォルトブランチ）のままにします。別の名前を試すときは、ホストの既定ブランチをその名前に変えます。
   手元の `origin/HEAD` は、次のセッションの開始時に `ccnavi-fetch.sh` が向け直します（すぐ要るときは `git fetch origin` のあと
   `git remote set-head origin -a`）。拡張は設定画面の「統合先の名前」に同じ名前を書きます。
6. ブランチ保護は 2 通りで試します。GitHub と GitLab の両方で同じにします。
   - **保護なし**: 何も設定しない
   - **保護あり**: 統合先（`main`）を保護し、必須のチェック（何でもよい。例: 常に通る CI のジョブ 1 本）と、
     「新しいコミットで既存の Approve を外す」設定（GitHub の Dismiss stale pull request approvals、GitLab の
     Remove all approvals when commits are added to the source branch）を有効にします。
     親のブランチの名前の形（例 `i*`）にも保護を掛けた回を 1 回入れます（4.7 の X6）
7. GitLab は同じことを `ccnavi-verify-gl` で行います。入れ子のグループを試すなら `group/sub/ccnavi-verify-gl` に作ります（L9）。
8. プロジェクトのリポジトリ（4.5）を試すときは、もう 1 つ使い捨てのリポジトリ（例 `ccnavi-verify-proj`）を作り、
   ワークスペースの clone の `projects/verify/` に clone します（手元の名前 `verify` を拡張の設定でも使います）。

## 2. 拡張を組み立てて Chrome に読み込む

1. 組み立てます。

   ```sh
   cd extensions/chrome/ccnavi-approval
   pnpm install --frozen-lockfile
   pnpm build
   ```

   プロキシの内側では `NODE_USE_ENV_PROXY=1 pnpm build` にします（PyYAML の sdist を PyPI から取るため）。
2. セルフホストの GitLab（または GHES）や `http://localhost:8080` を試すときは、設定画面の「通信先」で足します（組み立て直しは要りません）。
   追加すると Chrome がそのオリジンへの通信の許可を求めるので、許可します。http は localhost と 127.0.0.1 のみです。
   組み立てのときから入れておきたいホストは、通信先の一覧を書いて組み立てます（`api`・`web` は https に限り、GitHub の行には `graphql` も要ります）。

   ```json
   {
     "hosts": [
       { "id": "github.com", "kind": "github", "api": "https://api.github.com", "graphql": "https://api.github.com/graphql", "web": "https://github.com" },
       { "id": "gitlab.example.com", "kind": "gitlab", "api": "https://gitlab.example.com/api/v4", "web": "https://gitlab.example.com" }
     ]
   }
   ```

   ```sh
   node scripts/build.js --hosts <上の JSON のパス>
   ```

3. Chrome で `chrome://extensions` を開き、右上の「デベロッパー モード」を有効にします。
4. 「パッケージ化されていない拡張機能を読み込む」で `extensions/chrome/ccnavi-approval/dist/` を選びます。
5. ツールバーの拡張のアイコンを押すとボードが開きます。設定画面は拡張の「詳細」→「拡張機能のオプション」から開きます。

組み立て直したら `chrome://extensions` で拡張の再読み込みを押します。

## 3. PAT を作って登録する

拡張に登録するトークンは、使い捨てのリポジトリだけで使えるものにし、期限を付けます（7〜30 日で足ります。拡張の案内の既定は 90 日）。

### 3.1 GitHub（fine-grained personal access token）

1. 設定画面の「PAT」の下のリンク（`https://github.com/settings/personal-access-tokens/new`）を開きます。
2. Expiration に期限を、Repository access に「Only select repositories」で使い捨てのリポジトリだけを選びます。
3. Repository permissions を次のとおりにします（設定画面の案内と同じ）。

   | 権限 | 値 | 拡張が使うところ |
   |---|---|---|
   | Contents | Read and write | 読み取り（tree・blob・commits・compare・branches）、`createCommitOnBranch`、「始める」の `POST /git/refs` |
   | Metadata | Read-only | 選ぶと必ず付く。リポジトリの読み取り |
   | Pull requests | Read and write | 開いた PR・レビュー・`reviewThreads`、Approve の有無（読み取り）。残った指摘の行き先を決めるときの、スレッドへの返信（`addPullRequestReviewThreadReply`）・解決（`resolveReviewThread`）・PR へのコメント |
   | Issues | Read and write | 「始める」の issue の一覧（読み取り）。残った指摘を issue に回すときの issue の作成・検索とコメント |

   これより広い権限は付けません。classic の PAT なら `repo` スコープです。足りずに断られたら、その文面を記録します（4.7 の X9、4.8 の D1〜D3）。
   Pull requests と Issues の書き込みが fine-grained の PAT で足りるか、`resolveReviewThread` がこの組で通るかは、実機でしか確かめられません（4.8 の D2）。

### 3.2 GitLab

1. まず project access token を試します。プロジェクトの Settings → Access tokens を開き、作れるかを記録します。
   GitLab.com の無料版では作れない見込みです（確信中）。作れれば role は Developer 以上（スレッドの解決に要ります）、スコープは `api` にします。
2. 作れなければ、設定画面の「PAT」の下のリンク（`https://gitlab.com/-/user_settings/personal_access_tokens`）から個人の PAT を作ります。
   スコープは `api` です（Commits API は `write_repository` では書けません。スレッドへの返信・解決・issue の作成にも `api` が要ります）。ロールは Developer 以上にします。個人の PAT はプロジェクトを限れないので、期限を短くし、
   使い捨てのリポジトリしか持たないテスト用のアカウントで作るのが安全です。
3. どちらも Expiration date を入れます。

### 3.3 拡張に登録する

1. 設定画面の「リポジトリ」で、ホスト・owner・リポジトリ・統合先の名前（空なら既定のブランチ）・検索期間（過去 N 日。既定 7 日）を入力して登録します。
   GitLab の入れ子のグループは、「オーナー／グループ」欄に `group/sub` の形式で入力します。
2. 「PAT」でホストを選び、トークンを貼って登録します。期限の欄は空にします（ホストの応答から読めるかを確かめるため。X3）。
3. ボードを開き、統合先の名前が見出しに出ることを確かめます。

### 3.4 試すための親子のチケットを用意する

承認待ちを作るのは開発者の側（エージェント）です。使い捨てのワークスペースで Claude Code を開き、親のブランチの上で提案を書き、push してから承認を頼む流れで進めさせます。
拡張の「始める」（S1）で親のブランチを作るなら、拡張の読み込み（2 章）と PAT の登録（3.1〜3.3）の後に行います。

1. issue を 1 つ作り（例 #1）、拡張の「始める」（S1）かホストの画面で親のブランチ（`feature-1-<slug>` の形）を `main` の先頭から作ります。
2. エージェントに、親のワークツリーで提案（`issue: 1`、子を 1〜2 本、子の 1 本は `human_review.required: true`）を書かせ、
   `ccnavi --agree --preview --verify <識別子>` で確かめてから、コミットして `i0001` を push させます。
3. 「始める」を使わない親子のチケット（フォールバック。例 `verify-a`）も 1 つ用意します。名前は `^i\d+$` の形と、末尾が `-\d{2}` の形を避けます（子の識別子 `<親>-<2 桁のフェーズ番号>-<2 桁の連番>` と紛れるため）。

詳しい流れ（着手・終了・依頼）はエージェントが SessionStart で受ける案内と、`ccnavi-ticket.sh`・`ccnavi-review.sh` の `--help` のとおりです。

## 4. 確かめる項目

各項目の「記録するもの」は、ずれたとき（期待と違ったとき）に記録します。通ったときは「通った」だけで構いません。
ホストの応答は、ブラウザの開発者ツールでは見えにくいので（service worker が呼ぶ）、`chrome://extensions` の拡張の
「service worker」のリンクから開く開発者ツールの Network で見ます。トークンが載るヘッダ（`Authorization`・`PRIVATE-TOKEN`）は記録に入れません。

### 4.0 ツールで描いて確かめる（render-scene.js）

実際のホストへ繋がずに、実リポジトリの状態（ローカルブランチ・コミットの日付・提案ファイル）を模擬の GitHub / GitLab に載せ、
拡張を読み込んだ Chromium でボードを描いて、PNG・テキスト・DOM の要約に残します。以降の表の「ツールで代替できるか」の列は、この方法で確かめられる項目を示します。

```sh
node scripts/test.js --e2e      # 先に dist-e2e/ と out/ を組む（render-scene.js からはビルドしない。e2e の試験全体も回る。CCNAVI_CHROMIUM を使う環境では、組み立てのあとに 3 で終わるが、組み上がってはいる）
node scripts/render-scene.js --repo <ccnavi を配布済みのリポジトリ> --out <リポジトリの外のディレクトリ> [--host github|gitlab] [--width 1400]
```

| 項目 | 内容 |
|---|---|
| 出力 | `<out>/board.png`（全体）・`board.txt`（見えているテキスト）・`board.dom.json`（親子のチケットごとのバッジ・操作・レビュー待ち・承認対象外）・`board.log`（ページと service worker のコンソール・エラー。リクエストは記録しない） |
| 載せるもの | ローカルブランチごとの、許可リスト（`wip/`・`.ccnavi/approved/`・`.ccnavi/config/{common,project}/`・`.ccnavi/scripts/ccnavi-common.sh`・`.claude/settings.json` の `CCNAVI_` の env だけ）のファイルと、先頭のコミットの日付。`.env`・鍵・コード・バイナリ・`.git/` は載せない。1 ファイル 256 KiB・ブランチごとに 8 MiB を超えたら終了コード 1 |
| 通信先・PAT | 127.0.0.1:18787（GitHub）・18788（GitLab）の模擬。PAT は模擬の固定のダミー。同時に 2 本は回せない（使用中は終了コード 3） |
| 終了コード | 0 描けた / 1 描けなかった（画面の状態が `done` でない・120 秒で終わらない・ページか service worker のエラー・載せる上限を超えた・出力に PAT が出た）/ 2 使い方の誤り（`--out` が `--repo` の下など）/ 3 環境が揃っていない（`dist-e2e/`・Chromium・ポート） |
| ブラウザ | `playwright-core` 1.56.1 の Chromium（`PLAYWRIGHT_BROWSERS_PATH`。`playwright install` はしない）。版が合わないときは `CCNAVI_CHROMIUM` に実行ファイルのパス |

確かめられるのは、実リポジトリの提案ファイルとブランチが、承認待ち・レビュー待ち・承認対象外として画面に正しく出るかです。
確かめられないのは、ホストの応答の形、MR のスレッドとレビュー（模擬には載せない。レビュー待ちの欄は「対応する MR が無い」と出る）、PAT の権限と期限、承認などの書き込み、保護の挙動です。これらは次の表の手順で実際のホストを使います。

DOM の要約（`src/core/dom-summary.ts`）は、この拡張の中だけに置きます。`extensions/shared/` は今は CSS だけで、TS の関数を置くには
tsconfig・esbuild のバンドル・両拡張の `workspace:*` 依存・拡張ごとの lockfile の整合が要るためです。VS Code 側には、まだ実装がありません。足すときは同じ形（`schema: 1`。Chrome の `family` は VS Code 側では `parent`、
`column` は Chrome 側では持たない）を別に書き、形を確かめるテストを両方に置きます。

### 4.1 読み取りボード

| # | 操作 | 期待する結果 | 記録するもの | ツールで代替できるか |
|---|---|---|---|---|
| R1 | 3.4 の親子のチケットを push した後、ボードを開く | 親子のチケットごとに承認待ちが並ぶ。範囲・リスク・計画が最初に開いた形で出る | 画面の文面 | ○ 画面の表示（承認待ち・範囲・計画）は、実リポジトリの状態からでも描いて確かめられる |
| R2 | 設定の統合先に無いブランチ名を書いて開く | 止まり、統合先が無いと知らせる（既定に切り替わらない） | 画面の文面 | × 実際のホストが要る |
| R3 | 統合先の `CCNAVI_COMPAT` を書き換えて push し、開く | どちらを更新するかが出て、承認のボタンが出ない | 画面の文面。戻した後に元に戻ること | × 実際のホストが要る |
| R4 | 提案の本文に `<script>`・`<img onerror>`・`javascript:` のリンク・HTML コメントを書いて push し、開く | 実行されず、HTML コメントは「〈HTML コメント: …〉」で見える | 画面の見た目（スクリーンショット） | △ 見た目だけ。ホストの返し方は確かめられない |
| R5 | 親のブランチに置き場の外のコミットを足して開く | 判定は変わらない（直近 N 日・指定のブランチは表示用） | 画面の文面 | ○ 置き場の外のコミットを足したリポジトリを描いて、判定が変わらないことを確かめる |
| R6 | ボードを開いたまま 5 分待つ。別のタブに切り替えて 5 分以上置き、戻る | 5 分で自動で読み直す。見えない間は読み直さず、戻ったときにすぐ読み直す。承認などの結果の表示は消えない | 開発者ツールの Network で読み直しの時刻 | × 実際のホストが要る |
| R7 | 見出しの「表示対象のブランチを検索する期間（過去 N 日）」を 3 に変更して「変更」を押す。設定画面を開く。0〜90 の外・小数・空を入れる | 保存して読み直し、設定画面の一覧も同じ値になる。外れた値はエラーを出して保存しない | 画面の文面 | × 実際のホストが要る |

### 4.2 承認（GitHub）

| # | 操作 | 期待する結果 | 記録するもの | ツールで代替できるか |
|---|---|---|---|---|
| A1 | 「承認する」を押す | `i0001` に 1 コミット（見出し「ccnavi: <識別子> を承認（Chrome 拡張 <版>）」）で、`doing/` のチケットと提案の削除が乗る。CI が `[skip ci]` 無しで走る | コミットの sha と変更の一覧 | × 実際のホストが要る |
| A2 | 2 つのブラウザ（またはタブ）で同じボードを開き、ほぼ同時に承認を押す | 片方が書き、もう片方は読み直して判定し直す。同じダイジェストなら重ねて書かず、違えば見直しを求める。3 周で書けなければユーザの対応に切り替える | 両方の画面の文面、`createCommitOnBranch` の競合の応答（`errors` の中身） | × 実際のホストが要る |
| A3 | 承認のボタンを押す直前に、手元から同じ親のブランチへ別のコミットを push する | 先頭が動いたことを検出し、新しい先頭で判定し直して書く（置き場の外の変更ならダイジェストは同じ） | 画面の文面、GraphQL の応答 | × 実際のホストが要る |
| A4 | 承認の後、手元で `sh .ccnavi/scripts/ccnavi-sync.sh i0001` | 取り込まれる（終了コード 0）。親のブランチがリモートにあるので push 関門にも止められない | sync の出力 | × 実際のホストが要る |
| A5 | 承認コミットを見た後、エージェントに `start` させる | write-and-push で着手が `i0001` に届く | コミット | × 実際のホストが要る |

### 4.3 レビュー済み（GitHub）

ユーザのレビューの要る子を `finish` させ、エージェントに `ccnavi-review.sh request --phase <N> --body-file <依頼文> --eli5 wip/eli5/phase-<N>.html`（ELI5 の HTML はコミットして push 済み）を打たせて Draft の PR と依頼を作らせてから試します。
Approve の付いた PR を試すには、PR の作者と別のアカウントが要ります（自分の PR に Approve は付けられません）。

| # | 操作 | 期待する結果 | 記録するもの | ツールで代替できるか |
|---|---|---|---|---|
| V1 | 未解決のスレッドがある間にボードを開く | 「レビュー済みにする」が出ず、「残った指摘の行き先を決める」が出る。そこで行き先を決めて確定する（または解決する）と「レビュー済みにする」が出る。ccnavi の依頼のスレッドは数えない | 画面の文面、`reviewThreads` の応答（`pageInfo`・`isResolved`・`path`・`line`・最初のコメントの `url`） | × 実際のホストが要る |
| V2 | 変更要求のレビューを出し、その後に同じユーザがコメントだけのレビューを出す | 変更要求が残り、通らない | `GET /pulls/<N>/reviews` の応答（`state`・`submitted_at`・`user.id`） | × 実際のホストが要る |
| V3 | スレッドを解決し、変更要求を Approve に変えてから「フェーズ N をレビュー済みにする」を押す | 1 コミットで子が `done/` へ動き、`phases/<親>/<N>.reviewed` に `actor`（PAT の持ち主）と `via: chrome` が入る | コミット、マーカーの中身、GraphQL の `viewer` の応答 | × 実際のホストが要る |
| V4 | 依頼の後に置き場の外を変えるコミットを push してから開く | compare の変更の一覧で「動いた」かを判定する。ユーザが見るものが動いていれば通らない | `GET /compare/<base>...<head>` の応答（`status`・`files` の件数・改名の `previous_filename`） | × 実際のホストが要る |
| V5 | PR に Approve が付いた状態で「レビュー済み」か承認を押す | 確認の文に「このコミットで MR の Approve が外れることがある」が出る。保護ありの設定で Approve が実際に外れる | 確認の文、PR の Approve の有無 | × 実際のホストが要る |
| V6 | 書きかけ（PENDING）のレビューを別のアカウントで残す | 結論が変わらない | `reviews` の応答に PENDING が出るか | × 実際のホストが要る |
| V7 | 手元で `ccnavi-review.sh confirm --phase <N>` と `ccnavi-user-decide.sh <N>` を（別の親子のチケットで）打つ | マーカーに `actor`（`gh`・`glab`・curl のどれでもトークンの持ち主）が入る | マーカーの中身 | × 実際のホストが要る |
| V8 | `GET /pulls?state=open&head=<owner>%3A<branch>` が `:` のままと同じ答えになるか | 同じ PR を返す | 応答 | × 実際のホストが要る |
| V9 | 未解決のスレッドが 2 件以上ある間にボードを開き、「残った指摘の行き先を決める」を押す | 全件「このフェーズで直す」が選ばれ、確定を押せる。「すべて: …」の行で全件が切り替わり、個別に選び直せる。本文は消毒して描かれ、URL は http(s) のときだけリンクになる。解決できない種類のスレッドは選択肢が出ず「Chrome では扱えません。VS Code で」と理由が出て、確定が出ない。確定中は全部押せない | 画面の見た目（スクリーンショット） | × 実際のホストが要る |

### 4.4 GitLab

GitLab でも 4.1〜4.3 と同じ操作を行い、加えて次を確かめます。

| # | 操作 | 期待する結果 | 記録するもの | ツールで代替できるか |
|---|---|---|---|---|
| L1 | 承認を押す | `POST repository/commits` で 1 コミット。答えの `parent_ids[0]` が読んだ先頭 | 応答の `parent_ids` | × 実際のホストが要る |
| L2 | 本体の書き込みで同じファイルを競合させる（下の「同じファイルを競合させる手順」の A か B。対象は改版の承認で書き換わる `doing/<識別子>.md` など update になるファイル） | `last_commit_id` が合わず 400 になり書かない。拡張は読み直して再試行し、3 周で書けなければユーザの対応に切り替える | 400 の応答本文、`repository/files/:path?ref=<sha>` の `last_commit_id` | × 実際のホストが要る |
| L3 | 元に戻すコミットで同じファイルを競合させる（L11 で元に戻すコミットの `POST repository/commits` が出るときに、下の手順 A で止めて、同じファイルへ手元から push する） | 元に戻すコミットは自分のコミットを `last_commit_id` に付けるので 400 になり、他人の変更を消さない。親子のチケットが「要確認」で出て、「確かめた」を押すまでそのブラウザから書かない | 400 の応答本文、push したコミットが残っていること | × 実際のホストが要る |
| L11 | 別のファイルへの書き込みが間に入るよう、2 つのブラウザで別の子の承認を同時に押す | 事後確認で判定し直し、書くものが違えば元に戻すコミットが積まれる。収まらなければ親子のチケットが「確認が必要」で表示される | 両方の画面の文面、積まれたコミットの順序 | × 実際のホストが要る |
| L12 | MR を出したブランチを消した後に `GET projects/:id/repository/commits/refs/merge-requests/<N>/head`（または `git ls-remote origin 'refs/merge-requests/*'`）を見る | ブランチ消去後も残るか（どちらでも拡張の答えは変わらない。`seq` の前提の確認） | 応答 | × 実際のホストが要る |
| L13 | GraphQL（`https://<ホスト>/api/graphql`）で `project(fullPath:) { repository { blobs(ref:, paths:) { nodes { path rawTextBlob } } } }` を `curl` で投げる | まとめて取れるか（拡張は REST で 1 件ずつ読む。取れれば速くできる） | 応答の形 | × 実際のホストが要る |
| L5 | MR の discussions と reviewers を見る | 一般のコメント（依頼の投稿）の `resolvable`、システムのノートの `resolvable: false`、`notes[].author.id`、reviewers の `state` | 応答 | × 実際のホストが要る |
| L6 | 置き場に大きな差分（多数のファイル・多数の行）を入れて依頼の後に push する | compare の `collapsed`・`too_large`・`compare_timeout`・450 件以上のどれかで「動いた」とみなし、通らない | `repository/compare` の応答の該当の欄。インスタンスの `diff_max_files`・`diff_max_lines`（管理者の Settings → General → Diff limits。GitLab.com は公開の設定値）を記録する | × 実際のホストが要る |
| L7 | フォークから同じ `source_branch` の MR を出す | `source_project_id` で外れ、元の MR だけを見る | 画面の文面、`merge_requests` の応答 | × 実際のホストが要る |
| L8 | PAT の期限 | `GET /personal_access_tokens/self` の `expires_at` が読める（個人の PAT と、作れたなら project access token の両方） | 応答の `expires_at` | × 実際のホストが要る |
| L9 | 入れ子のグループのリポジトリを登録して開く | `projects/<符号化した表記>` で引ける | 画面の文面 | × 実際のホストが要る |
| L10 | `repository/tree` と `repository/blobs/:sha` | ページングで取り切れる。`encoding: base64` と `size` が合う | 応答 | × 実際のホストが要る |

同じファイルを競合させる手順（L2・L3）。**使い捨てのリポジトリに限ります。**

- **A（ブレークポイント）**: `chrome://extensions` の拡張の「service worker」から開発者ツールを開き、Sources で `background.js` の
  `repository/commits` への POST の直前（`createCommit` の中の `post(` の行）にブレークポイントを置きます（minify されているので、
  Network の Initiator から辿るか、「Pretty print」で `repository/commits` を探します）。ボードで操作を押し、止まったら手元の clone から
  同じファイル（例 `.ccnavi/approved/doing/<識別子>.md` に 1 行足す）をコミットして親のブランチへ push し、再開します
- **B（curl で直接）**: `repository/files/<符号化したパス>?ref=<親のブランチ>` で今の `last_commit_id` を記録し、手元から同じファイルを変えて push した後、
  記録した古い値を付けて直接 POST します。

  ```sh
  curl -sS -X POST -H "PRIVATE-TOKEN: $GITLAB_TOKEN" -H 'Content-Type: application/json' \
    "https://gitlab.com/api/v4/projects/<符号化した表記>/repository/commits" \
    -d '{"branch":"i0001","commit_message":"verify","actions":[{"action":"update","file_path":"<パス>","content":"x","last_commit_id":"<記録した古い値>"}]}'
  ```

  400 で書かれないことを確かめます（書かれたら L2 は「ずれた」。その親のブランチは破棄します）

### 4.5 プロジェクトのリポジトリ

| # | 操作 | 期待する結果 | 記録するもの | ツールで代替できるか |
|---|---|---|---|---|
| P1 | 設定画面で `ccnavi-verify-proj` を、プロジェクト名 `verify`、ワークスペースのリポジトリを選んで登録する | ボードに出る。共通レイヤー・置き場のパス・互換のマーカーはワークスペースの統合先から読む | 画面の文面 | × 実際のホストが要る |
| P2 | プロジェクトの親子のチケットを承認し、手元で `ccnavi-sync.sh <P>` | 取り込まれる（終了コード 0） | sync の出力 | × 実際のホストが要る |
| P3 | 設定のプロジェクト名をわざと手元のディレクトリ名とずらして承認し、手元で `ccnavi-sync.sh <P>` | 手元の判定し直しが親子のチケットを止める（厳しくなる向き） | sync の出力 | × 実際のホストが要る |

### 4.6 「始める」

| # | 操作 | 期待する結果 | 記録するもの | ツールで代替できるか |
|---|---|---|---|---|
| S1 | ボードの「Issue 一覧を取得」で開いた issue の「始める」を押す | 識別子 `feature-<番号>-<slug>`（プロジェクトは `feature-<番号>-<名前>-<slug>`）のブランチが統合先の先頭から作られる。PR/MR は作られない | 作られたブランチと起点の sha | × 実際のホストが要る |
| S2 | 同じ issue でもう一度押す | 拡張（Python）が既にある名前だと見て「作業を開始できませんでした」と断り、ホストへブランチを作る要求が出ない（Network に `git/refs`・`repository/branches` への POST が無い） | 画面の文面 | × 実際のホストが要る |
| S2b | ホストの断り方を見る: 既にある名前で `curl` から直接 POST する（GitHub `POST /repos/<o>/<r>/git/refs`、GitLab `POST projects/:id/repository/branches`） | GitHub は 422、GitLab は 400 | 応答 | × 実際のホストが要る |
| S3 | 統合先の `done/` に同じ識別子があるとき・大文字小文字だけ違うブランチがあるときに押す | 作らない | 画面の文面 | × 実際のホストが要る |
| S4 | GitHub の `GET /issues` の一覧 | PR が混ざっても `pull_request` で見分けて出さない。Issues: Read で読める | 応答 | × 実際のホストが要る |
| S5 | ブランチが 100 本を超えるリポジトリで押す | 全件のページングで重なりを見る（GitHub `GET /branches`、GitLab `GET /repository/branches`） | 画面の文面 | × 実際のホストが要る |

### 4.7 共通（ホストを問わない）

| # | 操作 | 期待する結果 | 記録するもの | ツールで代替できるか |
|---|---|---|---|---|
| X1 | 承認の後、手元で `ccnavi-sync.sh <P>`（承認・レビュー済みのそれぞれの後） | 取り込まれる（終了コード 0）。違えば sync の出力に理由が出る | sync の出力 | × 実際のホストが要る |
| X2 | 手元で push しながら承認を押す（A3 と同じことを GitLab でも） | GitHub は `expectedHeadOid` で、GitLab は事後確認で検出する | 画面の文面、コミットの順序 | × 実際のホストが要る |
| X3 | GitHub の PAT を期限の欄を空にして登録し、ボードを開く | 応答ヘッダ `github-authentication-token-expiration` から期限が読まれる | 応答ヘッダの値、設定画面の期限の表示 | × 実際のホストが要る |
| X4 | 期限が 7 日以内の PAT を登録する | ボードの帯とアイコンのバッジで知らせる | 帯の文面、バッジ | × 実際のホストが要る |
| X5 | 統合先の保護と必須チェックを有効にして承認する | 統合先へは書かず、親のブランチへのコミットは通る。ccnavi のコミットでも CI が走る | コミット、CI の結果 | × 実際のホストが要る |
| X6 | 親のブランチの名前の形（例 `i*`）に保護と必須チェックを掛けて承認する | 設計に決まりが無い。書けなければ原因の分かる文面で止まり、親のブランチが変わらないこと | 画面の文面、ホストの応答 | × 実際のホストが要る |
| X7 | レート制限 | 起こすのは難しいので、起きたときだけ記録する。拡張は原因と回復の時刻を示し、Retry-After が 60 秒以内なら 1 回だけ待ち直す | 画面の文面、`x-ratelimit-*`・`retry-after` のヘッダ | × 実際のホストが要る |
| X8 | PAT を失効させてからボードを開く | 401 で差し替えを促す | 画面の文面 | × 実際のホストが要る |
| X9 | 権限の足りない PAT（例 Pull requests を外す）で開く | 権限が原因だと示す | 画面の文面、応答 | × 実際のホストが要る |
| X11 | 古い実行ファイルと新しい sh の組み合わせ: flags に `--via` の無い古い実行ファイルを `CCNAVI_ENTRY_PATH` で指し、手元で `ccnavi-review.sh confirm --phase <N>` と `ccnavi-user-decide.sh <N>` を打つ | sh は `--via` を常に渡すので、実行ファイルが知らないオプションとして失敗し、前と同じマーカーは置かない。互換の版が食い違っていれば、標準エラーに直し方（組み立て直し・配り直し）が出る | マーカーの中身、sh の出力 | × 実際のホストが要る |
| X12 | Windows の Git Bash を 2 つ開き、片方で `sh -c 'echo $$; exec sleep 300'` を打って pid を記録し、もう一方から `kill -0 <pid>; echo $?` を打つ。`sleep` が終わった後にもう一度打つ | 生きている間は 0、終わった後は 0 以外（ロックの持ち主が生きているかを sh が見分けられる） | 2 回の終了コード | × 実際のホストが要る |
| X10 | GitHub の stale の外しにパスの除外があるか、GitLab の Code Owners で `.ccnavi/approved/` を外せるか | ホストの設定画面で確かめる（拡張の操作ではない） | 見つけた設定の名前、無ければ「無い」 | × 実際のホストが要る |

### 4.8 decide のホスト書き込み（実機で確かめる）

残った指摘の行き先を確定する操作が、ホストへ書く層を通ることを確かめます。模擬のホストでは、権限の組と保留中のレビューの挙動を確かめられません。
未解決のスレッドが 3 件以上ある MR を用意し、使い捨てのリポジトリだけで行います。

| # | 操作 | 期待する結果 | 記録するもの | ツールで代替できるか |
|---|---|---|---|---|
| D1 | GitHub: fine-grained の PAT に Pull requests と Issues を Read-only だけにして、行き先を決めて確定する（1 件は「issue に回す」） | 何も書かれず（返信も issue も無い）、足りない権限の名前（`Pull requests: Read and write` など）が出て止まる。書く前の確かめで足りないと分からなくても、最初の書き込みの 403/404 で権限の名前が出る | 画面の文面、MR と issue の一覧に何も増えていないこと、Network の最初に断られた要求の状態コードと `x-accepted-github-permissions` | × 実際のホストが要る |
| D2 | GitHub: fine-grained の PAT に Pull requests と Issues を Read and write（Contents は Read and write のまま）にして、「対応しない」「issue に回す」「このフェーズで直す」を 1 件ずつ選んで確定する | issue が作られ、「対応しない」と「issue に回す」のスレッドに返信が付いて解決済みになる。返信の issue の URL が作った issue を指す。「このフェーズで直す」のスレッドは返信も解決もされない。MR に決めた内容のコメントが付く。**`resolveReviewThread` がこの権限の組で通るか**が本題（GraphQL で `Resource not accessible by personal access token` が出れば、必要な権限の組を記録する） | 通ったか、断られたなら応答の `errors` の文面と、足したら通った権限 | × 実際のホストが要る |
| D3 | GitHub: classic の PAT（`repo` スコープ）で D2 と同じことをする | D2 と同じ結果。応答ヘッダ `x-oauth-scopes` が読まれ、`repo` が無ければ書く前に止まる | 結果、`x-oauth-scopes` の値 | × 実際のホストが要る |
| D4 | GitHub: 自分の保留中（PENDING）のレビューに属するスレッドを含めて確定する | 保留中のレビューのスレッドは「Chrome では扱えない。VS Code で」と出て、何も書かない。**保留中のスレッドが GraphQL の `reviewThreads` に出るか、`viewerCanReply`・最初のコメントの `state: PENDING` で見分けられるか**を記録する（見分けられなければ、返信が公開されるまで他の人に見えない形で付くかを記録する） | 画面の文面、`reviewThreads` の応答の抜粋（欄の名前と値） | × 実際のホストが要る |
| D5 | GitHub: 他のユーザが作ったスレッド・自分の PR のスレッドで、`viewerCanResolve` がそれぞれ何になるか | 書き込み権限のあるリポジトリでは真。権限の無いスレッドは偽で、権限の名前つきで止まる | 応答の抜粋 | × 実際のホストが要る |
| D6 | GitLab: スコープ `api`・role Developer の PAT で、差分への指摘 2 件と、MR 全体への discussion 1 件（差分に紐づかない）を用意して確定する | 差分以外の discussion が含まれると「Chrome では扱えない。VS Code で」と出て、何も書かない。差分への指摘だけなら返信が付いて解決済みになり、issue が作られ、MR にメモが付く | 画面の文面、discussion の `notes[].type`・`resolvable` の値 | × 実際のホストが要る |
| D7 | GitLab: スコープ `read_api` の PAT と、role Reporter の PAT で確定する | `read_api` は「api スコープ」が足りないと出て何も書かない。Reporter は「スレッドの解決は Developer 以上」と出て何も書かない。`GET /personal_access_tokens/self` の `scopes` が読めるか（project access token では 404 になるか）も記録する | 画面の文面、`personal_access_tokens/self` の応答の抜粋 | × 実際のホストが要る |
| D8 | 確定の途中で止める: issue の作成後に PAT を失効させる（または ネットワークを切る） | 止まった時点で、作った issue の番号と URL、返信・解決の済んだ分、失敗の理由が出る。MR を読み直して残った指摘を決め直すと、同じ操作 ID の issue は作り直されず使われ、続きのコメントは足りない分（`part=<i>` の印が無い分）だけ付き、返信済みのスレッド（`<!-- ccnavi:reply op=… -->` の印がある）には返信が重ならない。**GitHub の issue の一覧が作った直後に新しい順で見えるか**（見えなければ重複する）を記録する | 画面の文面、issue の一覧 | × 実際のホストが要る |
| D10 | 権限の見立てが実際と合うかを確かめる: GitLab の Reporter の MR 作者・グループから継承したメンバー・GitHub の fine-grained の PAT で、書く前の確かめの結果と、実際に書いたときの結果を比べる | 書く前に「足りない」と出たのに実際は書けるもの（偽陽性。Reporter の MR 作者は自分の MR の discussion を解決できる）、書く前に通ったのに実際は断られるもの（偽陰性。fine-grained の権限は書く前に読めない）を見つける | 食い違った組み合わせと画面の文面 | × 実際のホストが要る |
| D11 | GitLab: 依頼コメントと ccnavi の返信（`<!-- ccnavi:accept -->` など）を別のアカウントが書いた場合の、未解決の数え | 目印で始まるノートの書き手が全部同じスレッドだけを機構自身のものと見るので、別アカウントが書いた返信が混ざったスレッドは未解決に数えられる。数えられて確認が止まる場合は、その組み合わせを記録する | 書き手のアカウント、スレッドの `notes[].author`、確認の結果 | × 実際のホストが要る |
| D9 | 作った issue をホストで開く | assignee・labels・milestone が空。本文の先頭に `<!-- ccnavi:issue op=… -->` の印がある（画面では見えない） | issue の見え方 | × 実際のホストが要る |
| D12 | D2 の確定の直後に、画面の結果の欄を見る。続けて「このフェーズで直す」だけを選んだ別のフェーズで確定する | 1 回目はコミットの短い sha と作った issue の URL が出る。2 回目は issue もコメントの失敗も無いのに、MR に決めた内容のコメントが 1 件だけ付く。どちらも親のブランチのコミットは 1 件で、続きの子が `doing/` に出来ている | 画面の文面、親のブランチのコミットの数 | × 実際のホストが要る |

## 5. 結果の返し方

1. 次の形でまとめ、Claude との会話に貼ります（ファイルにするなら、使い捨てのワークスペースの外に置きます）。

   ```text
   拡張の版: 0.4.0（package.json）  Chrome: <版>  ホスト: github.com / gitlab.com（または自前のホスト名）
   X3: 通った（ヘッダの値: 2026-11-01 00:00:00 UTC）
   A3: ずれた — 先頭が動いたのに判定し直さなかった（応答の抜粋: ...）
   A2: 通った
   L3: ずれた — 画面「...」、コミットの順序: abc1234 → def5678
   ```

   - 4 章の番号（R1・A2 など）ごとに「通った」か「ずれた」を書きます
   - ずれたものには、画面の文面、ホストの応答の抜粋（欄の名前と値。全文は要りません）、関係するコミットの sha を添えます
   - 見本（`test/fixtures/host/`）と形が違う応答を見つけたら、その応答の該当部分を添えます（README の「ホストの応答の見本」の手順で直します）
2. **PAT・トークン・`Authorization`／`PRIVATE-TOKEN` のヘッダ・Cookie は貼りません。** 応答を貼る前に、個人の名前・社内の URL も伏せます。
   取り違えて貼ったら、そのトークンをすぐ失効させます。

## 6. 後片付け

1. 拡張の設定画面で PAT を「消す」で消します。
2. ホストでトークンを失効させます（GitHub は Settings → Developer settings → Fine-grained tokens、GitLab は User settings → Access tokens か
   プロジェクトの Settings → Access tokens）。
3. 使い捨てのリポジトリを消します（GitHub は Settings → Danger Zone、GitLab は Settings → General → Advanced → Delete project）。フォークを作ったなら、それも消します。
4. 手元の clone と、`chrome://extensions` の拡張（要らなければ）を消します。

## 分からないこと

- 親のブランチに保護と必須チェックを掛けたとき（X6）の拡張の振る舞いは、設計にも試験にも無く、どの文面になるかは分かりません。
- GitLab の project access token が無料版で作れるか、作れたとして `GET /personal_access_tokens/self` が期限を返すかは未確認です（L8 で確かめます）。
- 開発者の側（3.4）の細かい手順は、使い捨てのワークスペースでエージェントが受ける案内に任せています。この手順書では流れだけを書きました。
