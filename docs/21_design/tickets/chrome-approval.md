---
type: design
title: 9.20 Chrome 拡張による承認
description: 承認者がブラウザと PAT だけで親のブランチに承認・レビュー済み・「始める」を書く Chrome 拡張の構成。形と CSP、ブランチの読み方と読み取り量、承認の流れと競合、1 コミットの作り方、PAT の権限、ccnavi-review.sh との関係、レビュー済みのマーカー、ホストの Approve が外れる設定
tags: [design-doc, ticket, extension, approval]
keywords: [Chrome拡張, PAT, Pyodide, MV3, CSP, createCommitOnBranch, expectedHeadOid, last_commit_id, IndexedDB, GraphQL, レビュー済み, 始める, Approve, 競合, GitLab Commits API]
---

[設計書の入口](../../20_design.md) > [9. チケット制御](../tickets.md)

### 9.20 Chrome 拡張による承認

承認者の機械に clone も ccnavi も無い場合に、承認者がブラウザと PAT だけで、リモートの親のブランチ（`P`）へ承認を書くための拡張（`extensions/chrome/ccnavi-approval/`）。
判定は同梱の ccnavi を Pyodide で動かして出し、拡張は判定を自分で書かない。要求は [承認の要件](../../11_requirements/approval.md)（REQ-APV）と [拡張の入口](../../11_requirements/extension-if.md)。
有効とするチケットを `P` の上のものだけに限る規則は [9.5](judging-by-path.md)、共有の全体は [9.17](sharing.md)。

#### 形

- MV3。ボードは拡張のページ（サイドパネルかタブ）。Pyodide はそのページが起動する Web Worker で動かす
- CSP: `script-src 'self' 'wasm-unsafe-eval'; object-src 'self'; connect-src 'self' https: http://localhost:* http://127.0.0.1:*`。Pyodide・PyYAML・ccnavi の .pyc・DOMPurify を同梱する
- PAT とホストの API の呼び出しは service worker が持つ
- ビルドのときに API のホストの一覧から `host_permissions` を生成する
- 同梱の ccnavi の互換の版が統合先の `CCNAVI_COMPAT` と違えば、承認・「始める」など書く操作は出さない（表示だけ）

#### ブランチの読み方と読み取り量

- 統合先: 拡張の設定にあればその名前、空なら `GET /repos/{o}/{r}` / GitLab `GET /projects/:id` のデフォルトブランチ。設定した名前のブランチが無ければ（404）止めて知らせ、使った名前をボードの見出しに出す
- 判定の入力は、統合先・承認するチケットの `P`・参照の閉包の `P_X` に固定する。直近 N 日（GitHub は GraphQL の `refs { target { committedDate } }`、GitLab は `GET /repository/branches`）は表示用
- 読むのは置き場のサブツリーだけ。GitHub はパスで置き場の tree の sha を引き `?recursive=1` で取る。`truncated: true` なら error で止める。GitLab は `repository/tree`（`path`・`recursive`）をページングで取り切り、取り切れなければ error
- blob は sha で引き、IndexedDB に保存する（sha が同じなら中身は同じなので失効が要らない）。ボードを開くたびに読むのは tree だけになる
- まとめて取る: GitHub は GraphQL の別名（`f1: object(expression: "P:path") { ... on Blob { text } }`）で、IndexedDB に無い blob を 1 回に最大 50 件ほど取る。GitLab は REST の `repository/blobs/:sha` を 1 件ずつ引く
- 統合先の `done/` は、判定が要るもの（閉包の先行、同じ識別子の検査でヒットしたもの）だけを取る。取らなかったものは `NOT_FETCHED` として判定に渡す

初回の blob の数は、判定に入る親子のチケットの置き場のファイル数の和に統合先の `done/` の件数を足した数。GraphQL の呼び出しは ⌈blob の数 / 50⌉ + 1。2 回目以降は変わった blob の分だけ。GitHub の上限（REST・GraphQL とも 5,000 回（点）/時）に対して十分小さい。

#### 承認の流れと競合

1. preview: Snapshot を組み、`judge` → 画面とダイジェスト。書く先の `P` の先頭 `H` を記録する
2. yes: Snapshot を組み直し、ダイジェストが同じか確かめる。違えば preview からやり直す
3. `plan` の変更を、`P` へ 1 コミットで書く。条件は「`P` の先頭が `H` のまま」
4. 先頭が動いていたら、新しい先頭で Snapshot を組み直し、`judge` と `plan` を必ずやり直す。ダイジェストが同じなら新しい plan を新しい先頭に書く（ユーザに見せ直さない）。違えば preview からやり直す。古い plan をそのまま書かない。3 回やり直しても書けなければユーザの対応に切り替える

1 回の承認は 1 つの `P` に限る。レビュー済みも同じ流れで書く。

#### 1 コミットの作り方

- GitHub: GraphQL の `createCommitOnBranch`（`expectedHeadOid = H`）。先頭が `H` でなければ書き込みが失敗するので、そのまま競合の検出になる。Git Data API + `PATCH refs` は force push 後の巻き戻しを早送りとして通すので使わない
- GitLab: `POST projects/:id/repository/commits`（`branch: P`、`actions`）。既存ブランチに「先頭がこの sha のときだけ」の指定が無いので、次の組み合わせで守る
  - 書き換える・消すファイルに `last_commit_id`（そのファイルを最後に変えたコミット）を付ける。同じファイルを他人が変えていれば GitLab が断るので書かない
  - 書いた後にコミットの `parent_ids[0]` が `H` と同じか確かめる。違えば（別のファイルへの書き込みが間に入った）自分の書き込みの直前の状態で同じ時刻で判定し直し、書くものが違えば元に戻すコミット（revert）を積んで知らせる。元に戻すコミットの各ファイルにも「最後に変えたのは自分のコミット」を付け、他人の変更の上には書かない。2 回で収まらなければ止めてユーザの対応に切り替える（ボードに「要確認」で出す）
  - 別のファイルどうしの競合は `last_commit_id` では検出できないので、取り下げ以外の同時の書き込みで判定の変わるものが一時的に `P` に残りうる（受け入れた危険）
  - `seq` ファイルによる比較つきの書き込みは入れていない。GitLab の実機で前提（`last_commit_id` の比較の相手）を確かめてから足す
- 提案が `P` の上にあれば同じコミットで消す。`P` 以外のブランチにある提案は承認しない
- コミットメッセージはユーザが読む説明（「ccnavi: <識別子> を承認」など）と拡張の版だけ。どの処理も読まない。`[skip ci]` は付けない

#### PAT の権限

| ホスト | 承認・レビュー済み | 「始める」 |
|---|---|---|
| GitHub（fine-grained、リポジトリを限る） | Contents: Read and write、Metadata: Read、Pull requests: Read | 同じ + Issues: Read |
| GitLab | `api`（Commits API は `write_repository` では書けない） | 同じ |

GitLab は project access token（リポジトリを限れる）を勧めるが、GitLab.com の無料版では使えない見込み。使えなければ `api` スコープの個人の PAT になり、リポジトリを限れない。保護されたブランチ（`main` など）と統合先へは書かない。

#### 「始める」

issue の一覧に「始める」を出し、`P`（`i<番号>`、プロジェクトでは `<プロジェクト名>-i<番号>`）を統合先の先頭から作る。マージリクエストは作らない（差分 0 のブランチからは GitHub が作らせないので、最初の push の後に `ccnavi-review.sh review-request` が作る）。

拒否する場合: 求めた識別子が統合先の `done/` にある、同じ名前のブランチが既にある（大文字小文字を区別しない）、開いた親子のチケットに同じ識別子がある、予約の名前にヒットする。互換の版が違うときも断る。

#### `ccnavi-review.sh` との関係

| 操作 | Chrome 拡張との関係 |
|---|---|
| `review-request` | 変わらない。依頼のマーカーは write-and-push で送る |
| `confirm` | 中身はコアの `confirm` / `reviewed_mark`。sh がトークンの持ち主を引いて `--actor` で渡す。write-and-push で送る |
| `decide` | Chrome に載せる。ホストへの書き込み（issue・返信・解決）は拡張が行い、親のブランチへは write-and-push の代わりに 1 コミットで送る（下の「残った指摘の行き先を決める」） |
| `ready` | 前提に「親が `done/` にある」を足す。マーカーは write-and-push。`[skip ci]` を付けない |
| 子の push | 子のブランチはリモートに出さない |

#### 残った指摘の行き先を決める（`decide`）

未解決の指摘が残るフェーズで、ユーザが指摘ごとに行き先（対応しない・このフェーズで直す・issue に回す）を決める。判定と変更の組み立てはコアの `decide`（preview と apply）が持ち、ホストへの書き込みは service worker が行う。

- 書く順は、issue の作成、スレッドへの返信（issue の URL を入れる）、スレッドの解決、親のブランチへの 1 コミット（続きの子・マーカー・`done/` への移動）、MR へのコメント。ホストへの書き込みは、書き込みの周回（先頭が動くと読み直す繰り返し）の外で 1 度だけ行う。周回の中では、判定とコミットだけを行う
- コミットの前に、書き終えた「対応しない」「issue に回す」の指摘のキーを `carry` に渡す。コアは読み直した結果でそれらが解決済みであることを確かめ、`fix` のキーは受けない。ホストの読み取りの遅れで確かめが通らないときだけ、短く待って最大 3 回取り直す
- 書く前に、PAT の権限（返信・解決・issue の作成）と、スレッドが返信・解決できる種類かを確かめる。権限が足りなければ、何も書かずに必要な権限名を出して止まる。解決できない種類のスレッド（GitLab の差分以外のコメント、保留中のレビュー）は、Chrome では扱えない理由を出し、VS Code かターミナルへ誘導する。画面が先に、扱えない指摘を含む確定をブロックする。書く前の確かめで同じ見分けをするのは、読み直しの間に変わった場合の保険である
- 「このフェーズで直す」の指摘には返信も解決もしない（直した結果をユーザがホストで確かめて解決する）。続きの子の採番は、親のブランチ上のチケットだけを見る
- 途中で失敗したら、そこで止まり、済ませたことと理由を返す。操作の記録は持たない。未解決のスレッドを読み直して決め直せば、続きが進む。issue と返信には操作 ID の印を入れ、同じ印があれば作り直さない
- 指摘が 0 件の確定は、ホストへ何も書かず、コメントも投稿しない（「レビュー済みにする」と同じコミットになる）

#### レビュー済みのマーカーを Chrome から付ける

開発者と承認者が別の人のとき、マージリクエストを見てスレッドを片付けるのは承認者側なので、Chrome に「レビュー済み」を置く。`ccnavi-review.sh confirm` は手元用で、どちらも同じコアの `confirm` と `reviewed_mark` を使う。

マーカー `phases/<親>/<N>.reviewed` の中身は次のとおり。

```json
{"mr": 42, "accepted": [], "actor": "<ホストのアカウント名>", "via": "chrome", "at": "<時刻>"}
```

- 既存のキー `by` は経路名なので、アカウントは新しいキー `actor` に入れる。`actor` は押したユーザではなく、トークン（手元は `gh`・`glab` の認証か `GITHUB_TOKEN`/`GITLAB_TOKEN`、Chrome は PAT）の持ち主。記録であり、判定にも表示にも使わない
- 読む側（止める判定）はマーカーが在るかと `mr`・`accepted` しか見ないので、キーを足しても判定は変わらない

`confirm` の検査は Chrome でも同じにする。

| 検査 | Chrome での材料 |
|---|---|
| 依頼のマーカーがある | `phases/<親>/<N>.requested` を API で読む |
| 依頼の後にユーザが見るものが動いていない | compare API の変更パスをコアに渡す。打ち切られたら（GitHub は 300 件）動いたとみなす |
| 同じマージリクエストか | 親のブランチの開いたマージリクエストを引き、依頼の記録の `host`・`mr` と照合する |
| 変更要求のレビューが無い | GitHub `GET /pulls/{n}/reviews`、GitLab は approvals と discussions |
| 未解決スレッドが無い | GitHub GraphQL の `reviewThreads.isResolved`、GitLab `discussions` の `resolved` |
| レビュー待ちの子を `done/` へ | 同じ移動を `Changes` にして同じコミットに入れる |

push 済みかの検査は手元だけに残す。ホストから取得した内容は、`ccnavi-review.sh` が組む JSON（`{host, mr, threads, reviews, fetched_at}`）と同じ形にして `Result.load` に渡す。未解決が残るときは Chrome では通さず、「解決するか、`decide`（ターミナル・VS Code のボード）で受け入れる」と出す。

sh と TS の二重実装は、本物のホストの応答の形に合わせて手で組んだ見本（拡張の `test/fixtures/host/<github|gitlab>/<場面>/`）と、「その見本から sh が組んだ JSON」の期待値 1 つで揃える。sh の試験と TS の試験の両方が期待値と突き合わせる。ホストの応答の形が変わったら、見本のうち形の変わったキーを直し、sh の試験を `CCNAVI_HOST_FIXTURE=1` で回して期待値を作り直し、TS の試験を回す。見本・期待値・sh・TS の変更は同じコミットに入れる（手順の詳細は拡張の README の「ホストの応答の見本」）。

#### ホストの「新しいコミットで既存の承認を外す」設定

GitHub の「Dismiss stale pull request approvals when new commits are pushed」、GitLab の「Remove all approvals when commits are added to the source branch」が有効だと、ccnavi のコミットが `P` に積まれるたびにマージリクエストの Approve が外れる。

ccnavi のコミットはチケットの置き場だけを変えるので、コードの最後の Approve の前に済ませる運用にする。ccnavi 自身はマージリクエストの Approve を読まないので、途中の Approve が外れても ccnavi の流れは止まらない。

| 時点 | ccnavi のコミット（`P` へ） | ホストの Approve |
|---|---|---|
| 親の計画の承認 | Chrome の承認 | 無い |
| 着手・子の承認・子の終了 | write-and-push、Chrome の承認 | 付けない |
| フェーズのレビュー | 依頼のマーカー → Chrome の「レビュー済み」か `confirm` | 付けない |
| 親を閉じる | `finish` → `wip/` を消して push → `ready` のマーカー | なし |
| マージ | 無い | ここで最後の Approve を付ける |

Chrome の画面では、マージリクエストに Approve が付いているときに「このコミットでマージリクエストの Approve が外れることがあります」と出す。止めはしない。
