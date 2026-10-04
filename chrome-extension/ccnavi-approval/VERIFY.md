# 本物の GitHub・GitLab で確かめる手順

この拡張を本物の GitHub と GitLab に繋ぎ、模擬のホストでは確かめられなかったホストの振る舞い（応答の形・トークンの権限と期限など）と、
承認・取り下げ・レビュー済み・GitLab・「始める」が本物のホストでも同じに動くかを確かめるための手順です。
拡張の試験（`pnpm test`・`pnpm test:e2e`）は模擬のホストと手で組んだ見本で回しており、本物の応答からは録っていません。

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

   `--mode dry-run` は判定を記録するだけで止めません。手元の hook が本番どおりに止める流れ（git のラッパーの拒否、C1 の取り込み）まで
   見る回は `--mode enable` にします（こちらを勧めます）。

3. `.gitignore` に `logs/` と `.claude/worktrees/` が無ければ足します（判定の記録とエージェントのワークツリーを送らないため）。

   ```sh
   cd ~/ccnavi-verify-gh
   grep -qxF 'logs/' .gitignore || printf 'logs/\n' >> .gitignore
   grep -qxF '.claude/worktrees/' .gitignore || printf '.claude/worktrees/\n' >> .gitignore
   ```

4. 置かれたもの（`.claude/settings.json`・`.ccnavi/common/`・`.ccnavi/config/`・`.ccnavi/scripts/`・`.gitignore`・`.vscode/settings.json`）を
   コミットして `main` へ push します。`.ccnavi/bin/` は `.gitignore` に入るので送りません。
   **統合先（`main`）への commit と push はユーザが素の `git` で打ちます。** ccnavi のラッパー（`ccnavi-git.sh`）は `main` へ直接は送らず、
   エージェントにも打たせません。
   拡張は統合先の `.ccnavi/scripts/ccnavi-common.sh` の `CCNAVI_COMPAT` と `.claude/settings.json` を読むので、この 2 つが統合先に要ります。
5. 統合先は `main`（デフォルトブランチ）のままにします。別の名前を試すときだけ、手元は `.claude/settings.local.json` の env に
   `CCNAVI_INTEGRATION_BRANCH`、拡張は設定画面の「統合先の名前」に同じ名前を書きます。
6. ブランチ保護は 2 通りで試します。GitHub と GitLab の両方で同じにします。
   - **保護なし**: 何も設定しない
   - **保護あり**: 統合先（`main`）を保護し、必須のチェック（何でもよい。例: 常に通る CI のジョブ 1 本）と、
     「新しいコミットで既存の Approve を外す」設定（GitHub の Dismiss stale pull request approvals、GitLab の
     Remove all approvals when commits are added to the source branch）を有効にします。
     親のブランチの名前の形（例 `i*`）にも保護を掛けた回を 1 回入れます（4.8 の X6）
7. GitLab は同じことを `ccnavi-verify-gl` で行います。入れ子のグループを試すなら `group/sub/ccnavi-verify-gl` に作ります（L9）。
8. プロジェクトのリポジトリ（4.6）を試すときは、もう 1 つ使い捨てのリポジトリ（例 `ccnavi-verify-proj`）を作り、
   ワークスペースの clone の `projects/verify/` に clone します（手元の名前 `verify` を拡張の設定でも使います）。

## 2. 拡張を組み立てて Chrome に読み込む

1. 組み立てます。

   ```sh
   cd chrome-extension/ccnavi-approval
   pnpm install --frozen-lockfile
   pnpm build
   ```

   プロキシの内側では `NODE_USE_ENV_PROXY=1 pnpm build` にします（PyYAML の sdist を PyPI から取るため）。
2. セルフホストの GitLab（または GHES）を試すときは、通信先の一覧を書いて組み立てます。通信先は組み立てのときに埋め込み、設定画面では足せません。
   `api`・`web` は https に限ります。GitHub の行には `graphql` も要ります。

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
4. 「パッケージ化されていない拡張機能を読み込む」で `chrome-extension/ccnavi-approval/dist/` を選びます。
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
   | Pull requests | Read-only | 開いた PR・レビュー・`reviewThreads`、Approve の有無 |
   | Issues | Read-only | 「始める」の issue の一覧 |

   これより広い権限は付けません。足りずに断られたら、その文面を書いておきます（4.8 の X9）。

### 3.2 GitLab

1. まず project access token を試します。プロジェクトの Settings → Access tokens を開き、作れるかを書いておきます。
   GitLab.com の無料版では作れない見込みです（確信中）。作れれば role は Developer 以上、スコープは `api` にします。
2. 作れなければ、設定画面の「PAT」の下のリンク（`https://gitlab.com/-/user_settings/personal_access_tokens`）から個人の PAT を作ります。
   スコープは `api` です（Commits API は `write_repository` では書けません）。個人の PAT はプロジェクトを限れないので、期限を短くし、
   使い捨てのリポジトリしか持たないテスト用のアカウントで作るのが安全です。
3. どちらも Expiration date を入れます。

### 3.3 拡張に登録する

1. 設定画面の「リポジトリ」で、ホスト・owner・リポジトリ・統合先の名前（空なら既定のブランチ）・直近の日数を入れて登録します。
   GitLab の入れ子のグループは owner に `group/sub` と書きます。
2. 「PAT」でホストを選び、トークンを貼って登録します。期限の欄は空にします（ホストの応答から読めるかを確かめるため。X3）。
3. ボードを開き、統合先の名前が見出しに出ることを確かめます。

### 3.4 試すための親子チケットを用意する

承認待ちを作るのは開発者の側（エージェント）です。使い捨てのワークスペースで Claude Code を開き、親のブランチの上で提案を書き、push してから承認を頼む流れで進めさせます。
拡張の「始める」（S1）で親のブランチを作るなら、拡張の読み込み（2 章）と PAT の登録（3.1〜3.3）の後に行います。

1. issue を 1 つ作り（例 #1）、拡張の「始める」（S1）かホストの画面で親のブランチ `i0001` を `main` の先頭から作ります。
2. エージェントに、親のワークツリーで提案（`issue: 1`、子を 1〜2 本、子の 1 本は `human_review.required: true`）を書かせ、
   `ccnavi --agree --preview --verify <識別子>` で確かめてから、コミットして `i0001` を push させます。
3. 「始める」を使わない親子チケット（フォールバック。例 `verify-a`）も 1 つ用意します。名前は `^i\d+$` と `-\d{2}$` の形を避けます。

詳しい流れ（着手・終了・依頼）はエージェントが SessionStart で受ける案内と、`ccnavi-ticket.sh`・`ccnavi-review.sh` の `--help` のとおりです。

## 4. 確かめる項目

各項目の「書いておくもの」は、ずれたとき（期待と違ったとき）に書いておきます。通ったときは「通った」だけで構いません。
ホストの応答は、ブラウザの開発者ツールでは見えにくいので（service worker が呼ぶ）、`chrome://extensions` の拡張の
「service worker」のリンクから開く開発者ツールの Network で見ます。トークンが載るヘッダ（`Authorization`・`PRIVATE-TOKEN`）は書いておくものに入れません。

### 4.1 読み取りボード

| # | 操作 | 期待する結果 | 書いておくもの |
|---|---|---|---|
| R1 | 3.4 の親子チケットを push した後、ボードを開く | 親子チケットごとに承認待ちが並ぶ。範囲・リスク・計画が最初に開いた形で出る | 画面の文面 |
| R2 | 設定の統合先に無いブランチ名を書いて開く | 止まり、統合先が無いと言う（既定に落ちない） | 画面の文面 |
| R3 | 統合先の `CCNAVI_COMPAT` を書き換えて push し、開く | どちらを更新するかが出て、承認・取り下げのボタンが出ない | 画面の文面。戻した後に元に戻ること |
| R4 | 提案の本文に `<script>`・`<img onerror>`・`javascript:` のリンク・HTML コメントを書いて push し、開く | 実行されず、HTML コメントは「〈HTML コメント: …〉」で見える | 画面の見た目（スクリーンショット） |
| R5 | 親のブランチに置き場の外のコミットを足して開く | 判定は変わらない（直近 N 日・指定のブランチは表示用） | 画面の文面 |

### 4.2 承認（GitHub）

| # | 操作 | 期待する結果 | 書いておくもの |
|---|---|---|---|
| A1 | 「承認する」を押す | `i0001` に 1 コミット（見出し「ccnavi: <識別子> を承認（Chrome 拡張 <版>）」）で、`doing/` の承認済みチケットと提案の削除が乗る。CI が `[skip ci]` 無しで走る | コミットの sha と変更の一覧 |
| A2 | 2 つのブラウザ（またはタブ）で同じボードを開き、ほぼ同時に承認を押す | 片方が書き、もう片方は読み直して判定し直す。同じダイジェストなら重ねて書かず、違えば見直しを求める。3 周で書けなければユーザの対応に切り替える | 両方の画面の文面、`createCommitOnBranch` の競合の応答（`errors` の中身） |
| A3 | 承認のボタンを押す直前に、手元から同じ親のブランチへ別のコミットを push する | 先頭が動いたことを捕まえ、新しい先頭で判定し直して書く（置き場の外の変更ならダイジェストは同じ） | 画面の文面、GraphQL の応答 |
| A4 | 承認の後、手元で `sh .ccnavi/scripts/ccnavi-sync.sh i0001` | 取り込まれ、親子チケットが `blocked` にならない（終了コード 0） | sync の出力、`logs/state/sync/` の親子チケットの同期状態 |
| A5 | 承認コミットを見た後、エージェントに `start` させる | C1 で着手が `i0001` に届く | コミット |

### 4.3 取り下げ

| # | 操作 | 期待する結果 | 書いておくもの |
|---|---|---|---|
| W1 | 着手前の新規の承認に「承認を取り下げる」を押す | 1 コミットで `doing/` が消え、承認コミットの親にあった提案のバイト列が `todo/` に戻る。`events/<識別子>.ndjson` に `withdrawn` の行（`actor` が PAT の持ち主、`via: chrome`） | コミットの sha、戻った提案と元の提案の差分 |
| W2 | 取り下げた後に承認し直し、もう一度取り下げる | 新しい方の承認コミットを選ぶ | コミット |
| W3 | 承認を別の枝で行って merge で `i0001` に入れる（手元の端末の承認を merge する、など）| 取り下げのボタンが出ない（`GET /commits?sha=&path=` が merge コミットを返すか・飛ばすかに依らず同じ答え） | `GET /commits?sha=<P>&path=<doing のパス>` の応答に merge コミットが入るか |
| W4 | 着手の後にボードを開く | 取り下げが出ず、`ccnavi-ticket.sh cancel` を案内する | 画面の文面 |
| W5 | 取り下げの後、手元で `ccnavi-sync.sh i0001` | blocked にならない | sync の出力 |

### 4.4 レビュー済み（GitHub）

ユーザのレビューの要る子を `finish` させ、エージェントに `ccnavi-review.sh request --phase <N> --body-file <依頼文> --eli5 wip/eli5/phase-<N>.html`（ELI5 の HTML はコミットして push 済み）を打たせて Draft の PR と依頼を作らせてから試します。
Approve の付いた PR を試すには、PR の作者と別のアカウントが要ります（自分の PR に Approve は付けられません）。

| # | 操作 | 期待する結果 | 書いておくもの |
|---|---|---|---|
| V1 | 未解決のスレッドがある間にボードを開く | 「レビュー済みにする」が出ず、解決するか `decide` で受け入れると出る。ccnavi の依頼のスレッドは数えない | 画面の文面、`reviewThreads` の応答（`pageInfo`・`isResolved`・`path`・`line`・最初のコメントの `url`） |
| V2 | 変更要求のレビューを出し、その後に同じユーザがコメントだけのレビューを出す | 変更要求が残り、通らない | `GET /pulls/<N>/reviews` の応答（`state`・`submitted_at`・`user.id`） |
| V3 | スレッドを解決し、変更要求を Approve に変えてから「フェーズ N をレビュー済みにする」を押す | 1 コミットで子が `done/` へ動き、`phases/<親>/<N>.reviewed` に `actor`（PAT の持ち主）と `via: chrome` が入る | コミット、マーカーの中身、GraphQL の `viewer` の応答 |
| V4 | 依頼の後に置き場の外を変えるコミットを push してから開く | compare の変更の一覧で「動いた」かを判定する。ユーザが見るものが動いていれば通らない | `GET /compare/<base>...<head>` の応答（`status`・`files` の件数・改名の `previous_filename`） |
| V5 | PR に Approve が付いた状態で「レビュー済み」か承認を押す | 確認の文に「このコミットで MR の Approve が外れることがある」が出る。保護ありの設定で Approve が実際に外れる | 確認の文、PR の Approve の有無 |
| V6 | 書きかけ（PENDING）のレビューを別のアカウントで残す | 結論が変わらない | `reviews` の応答に PENDING が出るか |
| V7 | 手元で `ccnavi-review.sh confirm --phase <N>` と `decide <N>` を（別の親子チケットで）打つ | マーカーに `actor`（`gh`・`glab`・curl のどれでもトークンの持ち主）が入る | マーカーの中身 |
| V8 | `GET /pulls?state=open&head=<owner>%3A<branch>` が `:` のままと同じ答えになるか | 同じ PR を返す | 応答 |

### 4.5 GitLab

GitLab でも 4.1〜4.4 と同じ操作を行い、加えて次を確かめます。

| # | 操作 | 期待する結果 | 書いておくもの |
|---|---|---|---|
| L1 | 承認を押す | `POST repository/commits` で 1 コミット。答えの `parent_ids[0]` が読んだ先頭 | 応答の `parent_ids` |
| L2 | 本体の書き込みで同じファイルを競合させる（下の「同じファイルを競合させる手順」の A か B。対象は承認で書き換わる `events/<識別子>.ndjson` など update になるファイル） | `last_commit_id` が合わず 400 になり書かない。拡張は読み直して周を回し、3 周で書けなければユーザの対応に切り替える | 400 の応答本文、`repository/files/:path?ref=<sha>` の `last_commit_id` |
| L3 | 打ち消しで同じファイルを競合させる（L11 で打ち消しの `POST repository/commits` が出るときに、下の手順 A で止めて、同じファイルへ手元から push する） | 打ち消しは自分のコミットを `last_commit_id` に付けるので 400 になり、他人の変更を消さない。親子チケットが「要確認」で出て、「確かめた」を押すまでそのブラウザから書かない | 400 の応答本文、push したコミットが残っていること |
| L11 | 別のファイルへの書き込みが間に入るよう、2 つのブラウザで別の子の承認と取り下げを同時に押す | 事後確認で判定し直し、書くものが違えば打ち消しのコミットが積まれる。収まらなければ親子チケットが「要確認」で出る | 両方の画面の文面、積まれたコミットの順序 |
| L12 | MR を出したブランチを消した後に `GET projects/:id/repository/commits/refs/merge-requests/<N>/head`（または `git ls-remote origin 'refs/merge-requests/*'`）を見る | ブランチ消去後も残るか（どちらでも拡張の答えは変わらない。`seq` の前提の確認） | 応答 |
| L13 | GraphQL（`https://<ホスト>/api/graphql`）で `project(fullPath:) { repository { blobs(ref:, paths:) { nodes { path rawTextBlob } } } }` を `curl` で投げる | まとめて取れるか（拡張は REST で 1 件ずつ読む。取れれば速くできる） | 応答の形 |
| L4 | merge コミットを挟んだ後の `last_commit_id` と `repository/commits?ref_name=&path=&first_parent=true` | 承認コミットの選び方が GitHub と同じ答え | 応答 |
| L5 | MR の discussions と reviewers を見る | 一般のコメント（依頼の投稿）の `resolvable`、システムのノートの `resolvable: false`、`notes[].author.id`、reviewers の `state` | 応答 |
| L6 | 置き場に大きな差分（多数のファイル・多数の行）を入れて依頼の後に push する | compare の `collapsed`・`too_large`・`compare_timeout`・450 件以上のどれかで「動いた」と数え、通らない | `repository/compare` の応答の該当の欄。インスタンスの `diff_max_files`・`diff_max_lines`（管理者の Settings → General → Diff limits。GitLab.com は公開の設定値）を書いておく |
| L7 | フォークから同じ `source_branch` の MR を出す | `source_project_id` で外れ、元の MR だけを見る | 画面の文面、`merge_requests` の応答 |
| L8 | PAT の期限 | `GET /personal_access_tokens/self` の `expires_at` が読める（個人の PAT と、作れたなら project access token の両方） | 応答の `expires_at` |
| L9 | 入れ子のグループのリポジトリを登録して開く | `projects/<符号化した表記>` で引ける | 画面の文面 |
| L10 | `repository/tree` と `repository/blobs/:sha` | ページングで取り切れる。`encoding: base64` と `size` が合う | 応答 |

同じファイルを競合させる手順（L2・L3）。**使い捨てのリポジトリに限ります。**

- **A（ブレークポイント）**: `chrome://extensions` の拡張の「service worker」から開発者ツールを開き、Sources で `background.js` の
  `repository/commits` への POST の直前（`createCommit` の中の `post(` の行）にブレークポイントを置きます（minify されているので、
  Network の Initiator から辿るか、「Pretty print」で `repository/commits` を探します）。ボードで操作を押し、止まったら手元の clone から
  同じファイル（例 `.ccnavi/approved/events/<識別子>.ndjson` に 1 行足す）をコミットして親のブランチへ push し、再開します
- **B（curl で直接）**: `repository/files/<符号化したパス>?ref=<親のブランチ>` で今の `last_commit_id` を書いておき、手元から同じファイルを変えて push した後、
  書いておいた古い値を付けて直接 POST します。

  ```sh
  curl -sS -X POST -H "PRIVATE-TOKEN: $GITLAB_TOKEN" -H 'Content-Type: application/json' \
    "https://gitlab.com/api/v4/projects/<符号化した表記>/repository/commits" \
    -d '{"branch":"i0001","commit_message":"verify","actions":[{"action":"update","file_path":"<パス>","content":"x","last_commit_id":"<保存した古い値>"}]}'
  ```

  400 で書かれないことを確かめます（書かれたら L2 は「ずれた」。その親のブランチは捨てます）

### 4.6 プロジェクトのリポジトリ

| # | 操作 | 期待する結果 | 書いておくもの |
|---|---|---|---|
| P1 | 設定画面で `ccnavi-verify-proj` を、プロジェクト名 `verify`、ワークスペースのリポジトリを選んで登録する | ボードに出る。共通層・置き場のパス・互換のマーカーはワークスペースの統合先から読む | 画面の文面 |
| P2 | プロジェクトの親子チケットを承認し、手元で `ccnavi-sync.sh <P>` | blocked にならない | sync の出力 |
| P3 | 設定のプロジェクト名をわざと手元のディレクトリ名とずらして承認し、手元で `ccnavi-sync.sh <P>` | 手元の判定し直しが親子チケットを止める（判定を厳しくする向き） | sync の出力、同期状態の `reason` |

### 4.7 「始める」

| # | 操作 | 期待する結果 | 書いておくもの |
|---|---|---|---|
| S1 | ボードの「issue を読む」で開いた issue の「始める」を押す | 識別子 `i<4 桁>`（プロジェクトは `<名前>-i<4 桁>`）のブランチが統合先の先頭から作られる。PR/MR は作られない | 作られたブランチと起点の sha |
| S2 | 同じ issue でもう一度押す | 拡張（Python）が既にある名前だと見て「始めなかった」と断り、ホストへブランチを作る要求が出ない（Network に `git/refs`・`repository/branches` への POST が無い） | 画面の文面 |
| S2b | ホストの断り方を見る: 既にある名前で `curl` から直接 POST する（GitHub `POST /repos/<o>/<r>/git/refs`、GitLab `POST projects/:id/repository/branches`） | GitHub は 422、GitLab は 400 | 応答 |
| S3 | 統合先の `done/` に同じ識別子があるとき・大文字小文字だけ違うブランチがあるときに押す | 作らない | 画面の文面 |
| S4 | GitHub の `GET /issues` の一覧 | PR が混ざっても `pull_request` で見分けて出さない。Issues: Read で読める | 応答 |
| S5 | ブランチが 100 本を超えるリポジトリで押す | 全件のページングで重なりを見る（GitHub `GET /branches`、GitLab `GET /repository/branches`） | 画面の文面 |

### 4.8 共通（ホストを問わない）

| # | 操作 | 期待する結果 | 書いておくもの |
|---|---|---|---|
| X1 | 承認の後、手元で `ccnavi-sync.sh <P>`（承認・取り下げ・レビュー済みのそれぞれの後） | 取り込まれ、blocked にならない。違えば理由に「Chrome <版> と手元 <版> で判定が違う」が付く | sync の出力、同期状態の `reason` |
| X2 | 手元で push しながら承認を押す（A3 と同じことを GitLab でも） | GitHub は `expectedHeadOid` で、GitLab は事後確認で捕まえる | 画面の文面、コミットの順序 |
| X3 | GitHub の PAT を期限の欄を空にして登録し、ボードを開く | 応答ヘッダ `github-authentication-token-expiration` から期限が読まれる | 応答ヘッダの値、設定画面の期限の表示 |
| X4 | 期限が 7 日以内の PAT を登録する | ボードの帯とアイコンのバッジで知らせる | 帯の文面、バッジ |
| X5 | 統合先の保護と必須チェックを有効にして承認する | 統合先へは書かず、親のブランチへのコミットは通る。ccnavi のコミットでも CI が走る | コミット、CI の結果 |
| X6 | 親のブランチの名前の形（例 `i*`）に保護と必須チェックを掛けて承認する | 設計に決まりが無い。書けなければ原因の分かる文面で止まり、親のブランチが変わらないこと | 画面の文面、ホストの応答 |
| X7 | レート制限 | 当てるのは難しいので、当たったときだけ書いておく。拡張は原因と回復の時刻を言い、Retry-After が 60 秒以内なら 1 回だけ待ち直す | 画面の文面、`x-ratelimit-*`・`retry-after` のヘッダ |
| X8 | PAT を失効させてからボードを開く | 401 で差し替えを促す | 画面の文面 |
| X9 | 権限の足りない PAT（例 Pull requests を外す）で開く | 権限が原因だと言う | 画面の文面、応答 |
| X11 | 古い実行ファイルと新しい sh の組み合わせ: `--version --json` を知らない古い実行ファイルか、flags に `--via` の無い実行ファイルを `CCNAVI_BIN_PATH` で指し、手元で `ccnavi-review.sh confirm --phase <N>` と `decide <N>` を打つ | どちらも落ちず、`--actor`・`--via` を渡さずに前と同じマーカーを置く（`actor` が無い） | マーカーの中身、sh の出力 |
| X12 | Windows の Git Bash を 2 つ開き、片方で `sh -c 'echo $$; exec sleep 300'` を打って pid を書いておき、もう一方から `kill -0 <pid>; echo $?` を打つ。`sleep` が終わった後にもう一度打つ | 生きている間は 0、終わった後は 0 以外（ロックの持ち主が生きているかを sh が見分けられる） | 2 回の終了コード |
| X10 | GitHub の stale の外しにパスの除外があるか、GitLab の Code Owners で `.ccnavi/approved/` を外せるか | ホストの設定画面で確かめる（拡張の操作ではない） | 見つけた設定の名前、無ければ「無い」 |

## 5. 結果の返し方

1. 次の形でまとめ、Claude との会話に貼ります（ファイルにするなら、使い捨てのワークスペースの外に置きます）。

   ```text
   拡張の版: 0.4.0（package.json）  Chrome: <版>  ホスト: github.com / gitlab.com（または自前のホスト名）
   X3: 通った（ヘッダの値: 2026-11-01 00:00:00 UTC）
   W3: ずれた — merge コミットが一覧に入った（応答の抜粋: ...）
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
