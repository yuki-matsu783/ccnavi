---
type: design
title: 9.10 レビューの依頼と確認
description: mr のフェーズでのレビューの依頼と確認の流れ
tags: [design-doc, ticket, review]
keywords: [レビュー, 依頼, 確認, mr, ccnavi-review.sh, GraphQL, 403, REST, ccr]
---

[設計書の入口](../../20_design.md) > [9. チケット制御](../tickets.md)

### 9.10 レビューの依頼と確認

この節は、見る場所が `mr` のフェーズの話。

リモート（GitHub / GitLab）を読み書きするのは `.ccnavi/scripts/ccnavi-review.sh` で、実行ファイルは
ネットワークに出ない（P11）。実行ファイルが持つのはワークツリーの中で分かる前提検査と、
sh が取得して JSON ファイルに書き、そのパスを `--result` で渡す。実行ファイルはその JSON の判定とマーカーの操作だけを持つ。sh と実行ファイルの間の契約は、この JSON の形で決まる。
実行ファイルの側では、JSON を読む形と投稿の目印は `tickets/model/review_host.py`、`review-request` と `confirm` の段は `tickets/review/request.py`、
`decide` の段は `tickets/review/decide.py`、`ready` と `close-early` の段は `tickets/review/close.py` が持つ。

| `--result` の JSON | 形 |
|---|---|
| `confirm` / `decide` が受け取るもの | `{host: github\|gitlab, mr: {number, url}, threads: [{id, resolved, url, path, line, body, created_at, author}], reviews: [{state, url, submitted_at, author}], fetched_at}` |
| `review-request` の投稿の結果 | `{host, mr, url, created_at}` |

| sh の呼び方 | 実行ファイルの段 |
|---|---|
| `review-request --phase <N> --body-file <文> --eli5 <HTML>`（成功後、`--body-file` が workspace 直下の `scratchpad/` の中なら sh が消す。外のファイルは消さない。消せなければ警告のみで終了コードは変えない） | sh が `--eli5` を確かめる → `review prepare` → sh が親ブランチの open のマージリクエストを探し（無ければ作らずに止める。元ブランチが HEAD と一致し、マージ先が統合先であることも確かめる）、依頼を投稿 → `review requested` |
| `review-request --phase <N> --eli5 <HTML> --check` | 読むだけ。ロックも取り込みもせず、`review-request` の前提（ELI5・`review prepare`・MR の確認）を通して、投稿とマーカーの前で `確認: ok` を出して終わる |
| `confirm --phase <N>` | sh がスレッドとレビューを取ってくる → `review confirm` |
| `decide <N> --preview` | 一覧とダイジェストを見るだけ（`--reviewed N --accept-unresolved --preview`）。書く側は `ccnavi-user-decide.sh <N>`（下の行） |
| `ccnavi-user-decide.sh <N>` | ユーザが打つ。`ccnavi-review.sh` には無い。sh が取ってくる → `user reviewed N --accept-unresolved` → sh が投稿。途中で失敗した送信は `ccnavi-user-decide.sh --resume <操作ID>` で続きを送る（操作記録は `logs/state/ops/`） |
| `comment --body-file <本文>` | sh が投稿する。実行ファイルは関わらず、レビューの状態も変えない |
| `ready` | `review ready`（条件を確かめ、マーカーを置き、閉じた親子のチケットを `logs/archive/` へ退避する。`wip/` の追跡済みのファイルを消す。標準出力は 1 行目が下書きのパス、2 行目が `tree <退避したツリーのルート>`、続けて消したものがあれば `wip-from <消す前の HEAD>` と `wip <パス>`）→ write-and-push が退避と `wip/` の削除をコミットして push する（write-and-push の外なら、sh が 2 行目のツリーの置き場と `wip/` に未コミットの変更が無いことを確かめ、あれば止める）→ sh が Draft を外し、コメントを投稿する → sh が実行ファイルの `worktree drop <親>` で親のワークツリーを消す（cwd が中なら消さず、外から打つ 1 本を出す）。`--parent <親>` なら親のワークツリーの外から打てる（sh が親のワークツリーへ移って同じ段を回し、消す前に打った場所へ戻る） |
| `ccnavi-user-close-early.sh --reason <理由> [--no-issue]` | ユーザがターミナルで打つ。`ccnavi-review.sh` には無い。`user close-early` → sh が残りを issue に書き出し、コメントを投稿する |
| `fetch` / `origin` | 取得した JSON を標準出力へ / origin をどう読んだか |

**`review-request` の段**。次の順に進む。

1. sh が `--eli5` を確かめる。確かめるのはロックを取る前。`--eli5` が無い、値が無い、
   拡張子が `.html` / `.htm` でない、のどれかなら終了コード 2 で止まる。次にヒットするものがあれば、ヒットしたものを
   全部挙げて終了コード 1 で止まる。
   - ファイルが無い、または中身が空白だけ
   - 打った場所のワークツリーの外にある
   - ツリーのルートからの相対パスが `wip/eli5/` で始まらない
   - 名前に英数字と `. _ / -` 以外の字がある
   - HEAD に無い、または HEAD でのモードが 100644 でない（シンボリックリンク・実行ビット付き）
   - 手元の中身が HEAD と違う

   `--eli5` は実行ファイルには渡さない。
2. `review prepare` が前提を確かめ、依頼の本文とマージリクエストの下書きを state の置き場に書き出す。
   どちらの末尾にも固定の一文「`wip/handoff/` 配下はユーザに渡すコピーで、レビューの対象外。integrate で消える。」を足す
   （`review.HANDOFF_NOTE`）。レビュアーは `wip/handoff/` の下の変更を読まなくてよい。
3. sh が依頼を投稿する。マージリクエストが無ければ、先に下書きで作る。投稿する本文の末尾には
   「ELI5 は差分の `<相対パス>`。ルートで `crit review <相対パス>` を開いてソースの行に指摘を付け、`crit push <番号>` で送る」
   という 1 行を足す。
4. `review requested` が HEAD が動いていないことを確かめ、`{head, mr, url, host, since}` をマーカーに置く。続けて、親の
   承認済みチケットの `mr:` が空なら、この MR の番号を書き足す（`approval.ops.note_mr`。書いたパスは write-and-push のコミットに入る）。
   同じ番号が既にあれば何もせず、違う値があれば上書きせず「`mr:` に別の値が既にある」と警告する（依頼は通る）。
   実行後チェックはこの書き足しだけを ccnavi の書き込みと見なす（post-check.md の `mr:` の例外）。
5. 終わったら、sh はユーザが打つ `cd '<ツリーのルート>'`・`crit review <相対パス>`・`crit push <番号>` を標準出力に出す。
   ツリーのルートはいつも `'…'` で包み、中の `'` は `'\''` に置き換える。

**`confirm` の段**。sh がスレッドとレビューを取ってきて、`review confirm` に渡す。`review confirm` は判定し、
通れば `reviewed` のマーカーを置いて、そのフェーズと引き受けた延期の分の `review/` の子を `done/` へ動かす。
sh がトークンの持ち主を引けたら `--actor` で渡し、マーカーに `actor` と `via: cli` を残す。引けないときは渡さないので、
マーカーに `actor` が無い。
Chrome 拡張のレビュー済みも同じ判定（`core.confirm`）を通る。
レビュー済みにする入口は、ターミナルの `confirm`、VS Code のボードの［レビュー完了］（残った指摘を読み、0 件なら［レビュー済みにする］で `decide` を打つ。1 件以上なら対応方針の画面。ボードは `confirm` を打たせず、文も渡さない）、
Chrome 拡張のレビュー済み（マーカーと `done/` への移動を親のブランチへ 1 コミットで書く）、そして `decide`（指摘が 0 件、またはどれも直さず、見せたあとに増えた指摘も無いとき）。
Chrome 拡張も `decide` を行える。拡張が issue・返信・解決をホストへ書いたあと、同じコアの `decide` が組んだ続きの子・マーカー・`done/` への移動を親のブランチへ 1 コミットで書く（keep・fix・issue のどれも書ける）。
手元の `confirm` は、親を閉じられる状態になった（最後のレビューが済んだ）ときに、親の `finish` から `ready` までの流れを
標準出力に足す。sh は `review confirm` が通ったあと（write-and-push なら送ったあと）、実行ファイルの `worktree tidy <親> --phase <N>` で
`done/` へ動いた子のワークツリーを消す（未コミットがあるもの・cwd が中のものは消さずに言う）。
Chrome 拡張のレビュー済みと `decide` のワークツリーは、親の `finish` の片付けが消す。

**`decide` の段**（`ccnavi-user-decide.sh`）。ユーザがボードの［レビュー完了］かターミナルで打つ。sh がスレッドとレビューを取ってきて、実行ファイルを
`user reviewed N --accept-unresolved` で呼ぶ。実行ファイルは残っているスレッドを 1 件ずつ見せ、ユーザに対応方針を
選ばせる。方針は次の 3 つ。

- 対応しない。受け入れる（スレッドに返信を残して解決する。下の「受け入れの返信と解決」）
- このフェーズで直す。続きの子チケットを同じフェーズの番号で `.ccnavi/approved/doing/` に直に置く。本文は「### 指摘 N」の見出しごとの全文転記で、frontmatter に操作記録の ID `ccnavi_op` が入る
- issue に回す。受け入れたうえで、issue の下書きを書く。フィードバック計画の有無に関わらず、いつでも選べる。ただし `decide` が求める前提（依頼の記録があること、HEAD が動いていないこと、変更要求が無いこと）を満たすときに限る。本文が 60000 字に収まらなければ、本文に目次（キー・URL・版）だけを置き、全文は同じ issue への続きのコメントに分ける

直す指摘が無く、見せたあとに増えた指摘（追加分）も無ければ `reviewed` のマーカーを置き、直す指摘があればマーカーを消す。この 2 つの場合は `review/` の子を `done/` へ動かす。
追加分は止めずに `pending_extra` として未対応のまま残す。直す指摘が無く追加分だけが残るときは、マーカーを置かず、子も動かさない（次の `decide` で扱う）。
そのあと、issue に回す分があれば sh が issue を作る。決めた内容は、sh がマージリクエストのコメントにコピーする。

**受け入れの返信と解決**。受け入れる指摘（対応しない・issue に回す、`close-early` の未解決）は、記録ファイルには
持たない（手で動かした子の確定だけは例外で、受け入れ台帳に持つ。後述）。スレッドに「受け入れて進む」旨の
返信を残して、そのスレッドを解決済みにする。issue に回す分には、issue の下書きを置く旨も書く（`close-early` は
`--no-issue` でなければ書く）。返信の頭には目印 `<!-- ccnavi:accept -->` を付ける。実行ファイルはネットワークに
出ないので、返信と解決は sh がする。置く前に済ませ、間で結果を取り直すので、`decide` は次の順で通る。

1. 選択とダイジェストが決まる（ボードは `--preview` の一覧、ターミナルは `--choose-out`）。ホストに触る前に、ローカルで
   失敗しうること（write-and-push のロック・ツリーの確認）を済ませ、結果を取り直す。選んでいる間に見せた指摘が変わった・消えたなら、
   取り直した結果とのダイジェストの照合で止まる。増えただけなら止まらず、追加分として未対応で残す
2. `--yes` に `--accept-plan <ファイル>` を付けた呼び出し。ダイジェストの照合、選択の検査、置く側の事前検査
   （続きの子を起こせるか、issue の下書きの置き場）を置くときと同じに適用し、通れば受け入れるスレッドの id・キー・返信の
   本文・見せたダイジェストを書く。通らなければ計画を書かず、ホストにも触れない
3. sh が計画の id を確かめ（API の経路に使える字か。GitLab は 16 進。取り直した結果に在るか）、全部に返信してから、
   全部を解決する（GitHub は GraphQL の `addPullRequestReviewThreadReply` と `resolveReviewThread`。GraphQL が使えない環境では
   REST の `pulls/<N>/comments/<id>/replies` と `ccr/comments/<id>/resolve` を使い、id の形で道を選ぶ（下の「GraphQL が使えない環境」）。GitLab は
   discussions の返信と `resolved=true`）。返信か解決に 1 件でも失敗したら、受け入れは未完了として止め、何も置かない。
   そのあと結果を取り直す
4. 置く呼び出しは `--accept-done <ファイル>` を付ける。取り直した結果を読み、受け入れるスレッドが全部ホストで
   解決済みであることを実状態で確かめ、受け入れたスレッドを見せた指摘に数え直してダイジェストを照合する
   （見せた指摘が変わった・消えていれば止まる。増えた指摘は止めず、追加分として未対応で残す）。通れば置く

受け入れは親全体に該当する。`confirm` が数えるのはホスト上の未解決のスレッドから、受け入れ台帳（`phases/<親>/accepted.json`。後述）に版つきで受け入れた指摘を除いたもので、解決して受け入れたスレッドを他の人が
開き直せば、また数える。返信か解決のあとで置けずに終わると、解決済みのスレッドだけが残る。sh は計画を残して
そのパスを言う（打ち直すと、解決済みのスレッドは見せる指摘から外れる）。操作記録（`logs/state/ops/`）が残るので、`ccnavi-user-decide.sh --resume <操作ID>` でも続きを送れる。同じ親・同じフェーズで `decide` を同時に打つ排他は持たない。

ボードも同じ道を通る。ボードは `--preview` で一覧とダイジェストを受け取り、ユーザが選んだものを
`ccnavi-user-decide.sh <N> --choices <JSON> --digest <ダイジェスト>` で渡す。この道でも、sh は上の順で実行ファイルを `--yes` 付きで呼び、実行ファイルは
見せたダイジェスト（版 2。`v2.` で始まる不透明な文字列で、指摘ごとの版を含む）と今のダイジェストが食い違わないときだけ置く。

**指摘の版と転記の形。** 指摘の版は `review.thread_version` が 1 か所で作る。SHA-256(キー + 改行 + 正規化した最初のコメントの本文) の
先頭 32 桁で、正規化するのは改行コード（CRLF・CR を LF に）と Unicode の NFC だけ。CLI と Chrome が同じ Python のコアでこれを使い、
見せた指摘と取り直した指摘を版で照合する。返信をすべて取ることはせず、全文の範囲は最初のコメントにする。再提起を見分けるため、
sh はスレッドごとに最後のコメントの id・時刻・作成者も取る（GitHub は `comments(last:1)`、GitLab は notes の最後）。
続きの子と issue の本文は、指摘ごとに「### 指摘 N」の見出し、出どころの行（キー・URL・位置・版・取得元・操作ID）、
本文の全文をこの順で書く。本文は、本文の中で最も長いバッククォートの連続より長いフェンスで囲む。メンションも quick action も
ホストはコードブロックの中では働かせないので、転記が副作用を起こさない。`thread_label`（要約）は表示専用で、メインと起動時の文脈には
要約と保存先だけを渡し、担当のサブエージェントは子の本文から全文を読む。

sh がトークンの持ち主を引けたら `--actor=<名前> --via=<terminal|board>` で渡し、`reviewed` のマーカーに
`actor` と `via` を残す。引けないときと、実行ファイルの flags に `--actor` と `--via` のどちらかが無いときは渡さないので、
マーカーは前と同じ中身になる。実行ファイルは、`--yes` の無い呼び方では `--via board` を受け付けない。

**`comment` の段**。crit の指摘は、ユーザが `crit push` で送る
（下の「ELI5 と crit」）。

**`ready` の段**。親が閉じたあとに打つ。`review ready` は次の 3 つを確かめる。親を閉じられる条件を満たしていること。
親の承認済みチケットが `done/` にあること（閉じる前に Draft を外してマージされると、親が `done/` に無いまま親のブランチが消えるため）。`wip/` に未追跡のファイルが無く、未コミットの変更が無く、
push 済みであること。満たしていれば `ready.json` とコメントの下書きを置き、閉じた親子のチケット（今回の親と、統合先に
たまっていた過去の親）を親のワークツリーの承認済みの領域から `logs/archive/<リポジトリ>/` へ退避し（9.11）、`wip/` の追跡済みのファイルを
消す。`wip/` の未追跡のファイルは履歴から戻せないので、消さずに名指しして止める。承認済みチケットが
親のワークツリーに無い（ワークスペースルートに在る）ときは、何も置かずに止める。標準出力は 1 行目がコメントの下書きのパス、
2 行目が `tree <退避したツリーのルート>` で、sh はこの 2 行目のツリーで未コミットを確かめる（sh と実行ファイルの約束）。
`wip/` から消したものがあれば、続けて `wip-from <消す前の HEAD>` と `wip <パス>`（1 行 1 本）を出し、sh がユーザに見せる。
write-and-push の書いたパスの一覧の検査は、`review ready` が消した `wip/` の下のファイル（今は無いもの）だけを置き場の外でも通す。
続いて write-and-push が退避と `wip/` の削除をコミットして push し、sh が Draft を外し、コメントを投稿し、最後に `worktree drop <親>` で
親のワークツリーを消す（cwd が中にあれば消さず、外から打つ `ccnavi-clean.sh --worktree <親>` を出す。Draft を外す前と、
外せなかったときは消さない）。互換の版が食い違っていても止めず、組み立て直しはユーザに頼むと言う。
親は cwd から引くので、`review-request`・`confirm`・`decide`・`ready`（`--parent` なし）を統合先のブランチの上（ワークスペースルートなど）で打つと、sh はホストへ繋ぐ前に止まる。cwd を示して「親のワークツリーの中で打ってください」と言い、`次:` に `cd` 付きの打ち直しを出す。ホストは cwd の origin で決まるので、先に繋ぐと親のプロジェクトとは別のホストで止まってしまう。
`ready --parent <親>` は親のワークツリーの外から打つ形。sh は `.claude/worktrees/<親>` へ移ってから origin・ブランチ・親の識別子を
読み、実行ファイルにもそこを cwd として渡すので、条件の確かめ・退避・write-and-push・Draft 外しは中から打ったときと同じに進む。
最後の `worktree drop` だけは打った場所へ戻ってから、打った場所を cwd として渡すので、親のワークツリーが消える。GitLab では、Draft を外すときに `squash` も
有効にする。同じ親に 2 度打っても通る。退避を始めた後の打ち直しは、ready のマーカーが今のツリーのもので、`ready.json` のマージリクエストの
番号が今回と同じときだけ、ワークツリーの側の条件だけを見て残りを移す。マージするのはユーザ。

**`close-early` の段**（`ccnavi-user-close-early.sh`）。ユーザがターミナルで打つ。sh は実行ファイルを `user close-early` で呼び、そのあと残りを issue に書き出して、
コメントを投稿する。`user close-early` は、変更要求のレビューがあれば拒む。無ければ残りを見せて y/N を聞く。
未解決のスレッドの返信と解決を置く前に済ませるため、3 回に分けて呼ぶ（間で sh が結果を取り直す）。
1 回目（`--accept-plan`）は y/N を聞いて計画だけを書く。2 回目（`--accept-check`）は、y/N を答えている間に
未解決の指摘が変わっていないかだけを確かめる（変わっていればホストに触れずに止まる）。sh が返信と解決を済ませたあと、
3 回目（`--accept-done`）が取り直した結果で、計画のスレッドがホストで解決済みで、ほかに未解決が無いことを確かめ、
聞き直さずに次を行う。
- 未着手の子（`doing/` にあって着手の欄が空のもの）を取り消し、`done/` へ動かす
- 終わっていないフェーズのうち、レビュー待ちか閉じた子が居るもの（未着手の子を取り消すと終わるもの）に `skipped` を置く。
  レビュー待ちも閉じた子も無い、手を付けていないフェーズには置かず、残りの issue に書き出す
- レビュー未了のフェーズに `reviewed` を置き、その `review/` の子を `done/` へ動かす
- 未解決のスレッドを受け入れる（返信を残して解決済みにしてある。マーカーの `accepted` 欄にキーを残す。台帳には書かない）
- `close-early.json` を置く

**`review-request` の前提**は 6 つ。フェーズが終わっている、その番号が延期でない、閉じた（`review/` か `done/`）子の
ブランチが全部親ブランチに取り込まれている（子はブランチ名で引く。引けなければ未充足）、親の
ワークツリーに追跡されている未コミットの変更が無い、親ブランチの HEAD が push 済み、
まだ依頼していない（依頼の後に親の HEAD が動き、まだレビュー済みでなければ出し直せる。依頼文を
投稿し直し、マーカーの `head` と `since` を今のものに書き換える。レビュー済みのフェーズは、依頼の記録が
無くても（`close-early` が置いた形）拒む）。1 つでも欠けたら全件を列挙して拒み、何もしない。その前に sh が ELI5 の HTML を確かめる（下の「ELI5 と crit」）。マージリクエストのタイトル・本文・
`Closes #<課題>` は親チケットの `title` / `rationale` / 本文 / `issue` からコピーする。

**ELI5 と crit**。`review-request` には、変更をやさしく説明した HTML を `--eli5` で必ず添える。HTML は
マージリクエストの差分に載せ、ユーザは手元のターミナルで外部の CLI `crit` で開いて、指摘を `crit push` でマージリクエストの行のスレッドとして送る。
手順は次のとおり。

1. エージェントは依頼の前に ELI5 の HTML を書き、親のワークツリーの `wip/eli5/` の下（既定の名前は `wip/eli5/phase-<N>.html`。
   名前は英数字と `. _ / -` だけ。依頼文とユーザが打つ行にそのまま入るため）に普通のファイル（シンボリックリンクや実行ビットは付けない）として置いて
   コミットし、push する。相対パスは `--body-file` と同じく打った場所から。`wip/eli5/` の下はチケットの範囲を適用しないので
   （9.5）、親チケットの範囲に書き足さなくてよい
2. 中身の目安: 変更の目的・何が変わるか・リスクを専門用語なしで書く。1 枚で完結させ、外部の読み込み（CSS・JS・画像・フォントの URL）を
   使わない。スタイルは `<style>` に入れる
3. `review-request --phase <N> --body-file <依頼文> --eli5 wip/eli5/phase-<N>.html` を打つ。投稿が済むと
   `ELI5 を見る: ユーザがターミナルで cd <ルート> してから crit review <相対パス> を打ち、… crit push <番号> で …` が出るので、その行をユーザに渡して
   ターンを終える。sh は crit を起動しない（crit は閉じるまで戻らない）
4. ユーザはツリーのルート（か、同じブランチのチェックアウトのルート）で `crit review <相対パス>` を開き、HTML のソースの行に指摘を付けて、
   `crit push <番号>` で送る。`crit <相対パス>` だけだと描画のプレビューになり、そこで付けたピンは `crit push` で送られない（crit v0.21.0 で確認）。crit push は GitHub では `gh`、GitLab では `glab` を使い、マージリクエストの差分にあるファイルの行にだけ送れる。
   手元のチェックアウトがマージリクエストの先頭より古いと、crit が送る行の番号が
   ずれる。`crit review` を開く前に、手元をマージリクエストの先頭に合わせる
5. 送られた指摘はユーザのスレッドで、本文が目印で始まらないので、`confirm` は未解決として数えて止め、`decide` は 1 件ずつ行き先を選ばせる
   （`review_host._unresolved`。依頼者と同じアカウントが送っても数える）。`crit push --event request-changes` は変更要求のレビューに
   なり、`decide` でも通せない
6. 依頼の後に `wip/eli5/` の下だけを変えたコミットは「ユーザが見るものが動いた」に数えない。直したら push するだけで、
   `confirm` は止まらず、`review-request` の打ち直しも要らない（打つと「依頼済み」で止まる）。**代わりに、直した ELI5 をユーザが見直す保証は無い。**
   直す前の行に付いたスレッドは未解決のまま残るので、ユーザが resolve する（か `decide` で決める）。ELI5 とほかのファイルを一緒に変えた
   コミットは動いたと数え、打ち直しを求める
7. `wip/` は `ready` の前に丸ごと消してコミットする（`ready` の前提）。ELI5 もここで消え、squash した成果物には残らない。`ready` の前提は大文字小文字を区別せず（`WIP/` も）、名前が `wip\` で始まる 1 ファイルも残りとして止める

**`confirm`** は `requested` が無ければ拒む。依頼のマーカーにあるホストとマージリクエストの番号が承認済みチケットと
一致しなければ拒む。依頼時の HEAD と今の HEAD が同じで push 済みであることを求める。ただし
ccnavi 自身の置き場（`.ccnavi/approved/` と `wip/proposals/`）だけを変えたコミットは、
動いたとも未 push とも数えない（マーカーやチケットの移動で依頼が止まらないように）。出し直し（`review-request`）の
「依頼済み」の判定と、`review-request` の push の前提（`_unmet`）も同じ基準で見る。ELI5 の置き場（`wip/eli5/`）だけを変えた
コミットも、動いたとは数えない（`review._unseen_by_review`。Chrome のレビュー済みも同じ `moved_since` を通る）。
ただし push は求める（未 push の ELI5 はマージリクエストの差分に無く、crit push で送れない）。`ready` の前提
（`_merge_problems`）だけは、置き場も含めて完全に push させる。
マーカーの `head` は、`^[0-9a-f]{7,64}$` でなければ差分の相手にせず、記録が無いマーカーと同じく
「動いた」として扱い、出し直させる。数えるのは
いま解決されていない指摘の全部で、付いた時刻では絞らない。除くのは機構自身の投稿
（`<!-- ccnavi: …`）だけ。ユーザが `decide` で受け入れたものは、返信を残して解決済みにしてあるので、
解決済みとして数えない。レビューの状態（変更要求）は
レビュアーごとの最新だけを見て、取り下げは無いものとして扱う。

| 結果 | 動き |
|---|---|
| 変更要求のレビューがある | マーカーを置かない。`decide` でも通せない。解除のしかたはホストで違うので、拒否文はホスト（取得した結果の `host`）ごとに変える。拒否文は、変更要求があること・解除のしかたと、修正を始める道（同じフェーズに新しい子を提案して承認を受ける）を分けて言う |
| 未解決のスレッドがある | マーカーを置かず、一覧（URL・場所・要旨）を返す。台帳に受け入れた指摘は、版と最後のコメントの id が同じ間は含めない |
| どちらも無い | `reviewed` を置く。止まっていた状態が解除される |

変更要求の案内は、判定は変えず、文面だけをホストで分ける。`confirm`・`decide`・`close-early`・手で動かした子の確定の拒否・Chrome 拡張が同じ文面を使う。

GitLab に GitHub の dismiss にヒットする操作は適用していないので、GitLab では dismiss を案内しない。

**手で動かした子の確定（受け入れ台帳）。** ユーザが子を `review/` から `done/` へ手で動かして commit・push した（共有済み）とき、`confirm` は移動の前に付いた未解決の指摘をホストで解決せずに、版つき（キー・指摘の版・最後のコメントの id）で `phases/<親>/accepted.json` に足して `reviewed` を置く（マーカーに `via: manual` と `moved`）。`accepted.json` は、この確定にだけ使う台帳である。台帳に受け入れた指摘は、版と最後のコメントの id が同じ間は、`confirm`・`decide`・`close-early` のどれでも未解決として数えない（`review_host._unresolved` の `ledger` 引数）。本文が変わる・受け入れの後にコメントが付く指摘は数える。移動の後に付いた指摘が残れば確定せず、`decide` へ案内する。判定は指摘の最初と最後のコメントの両方で見る。変更要求は保留する。Stop は共有済みの手動移動を見つけたら `confirm` を 1 回促し（`STEP_CONFIRM`）、`status` は「レビュー完了確認待ち」「移動が未共有」を出す。Chrome 拡張の確定も同じ。git が無いので、移動の時刻は拡張がホストの履歴（そのパスを最初に加えたコミット）から読んで渡す。

**依頼のあとに HEAD が動いたときの説明。** 判定は変えない（`review.moved_since` / `_moved_since_request` の結果をそのまま使う）。判定で止めたあと、理由の説明を足す（`review.moved_explanation`。`confirm`・`decide`・Chrome 拡張が同じ文）。説明は何が変わったかを断定しない固定の 1 文で、「依頼時のコミットといまの HEAD が違う。何が変わったかユーザに確かめてもらい、依頼し直す」。依頼時のコミットが祖先でない・読めない場合もあるので、コミットの一覧や `git log` の範囲は出さない。プロジェクトの親で、レビューの対象を動かしたのがミラー側（共通レイヤーのコピーか `skills/`）だけのときは、直し方が違うので「動かしたのはミラーのずれのコミット」の 1 文を足す。依頼時の HEAD の記録が無い・HEAD を読めない・push されていないだけのときは、判定の文だけで説明は足さない。依頼を打ち直す手順は変わらない。依頼のあとに変えたものを元へ戻した（revert で差分が空になった）場合は、判定は通る。

**fix で直した指摘は、ユーザが resolve する。** `decide` の「このフェーズで直す」を選んだスレッドは、ホスト上で未解決のまま残るのが仕様で、続きの子で直したあと、ユーザが確かめて resolve する。案内の文は、ホストを問わず同じで、「修正を確認したら、ユーザがホスト上で該当スレッドを resolve する。エージェントは fix を理由に resolve しない」。入れる場所は、`decide` の結果の文（`review.decide._decided_prompt`。fix があるとき）、VS Code ボードの `decided` の note、依頼の本文の末尾の固定の 1 文（`review.RESOLVE_NOTE`。`HANDOFF_NOTE` のあとに足す）。依頼の目印のキーは、この 1 文を足す前の本文と親のブランチの先頭から作るので、1 文を足す前に投稿した依頼を打ち直しても二重に投稿されない。keep・issue を選んだスレッドに sh が返信して resolve する仕組み（受け入れの返信と解決）は、この案内の対象外。

**依頼の目印。** 依頼文の 1 行目は目印 `<!-- ccnavi:request <親>:<フェーズ番号> key=<キー> -->` で、キーは依頼文の本文と親のブランチの先頭（HEAD）から作る（`review prepare`）。`review-request` はマージリクエストのコメントに同じ目印で始まるものがあれば投稿し直さず、その投稿を記録に使う。打ち直しや write-and-push のやり直しで依頼が二重にならない。子を足してやり直した依頼は先頭が違うので、キーが変わり別に投稿する。**限界:** 1 回目と 2 回目の間に親のブランチが動くとキーが変わり、同じ依頼がもう 1 度投稿される（見つけたらユーザが古いほうのコメントを消す）。コメントの一覧を取れなければ、投稿せずに止まる（`request-posted-unknown`）。

**ホストの分類。** ホストの分類は、origin から取り出したホストを小文字にしてポートを除いたもの（分類に使うホスト）を、`.` の区切りで照合して決める（`evilgithub.com`・`github.com.evil`・`evilghe.com`・`ghe.com.evil` は GitHub にならない）。scheme の大文字小文字は区別しない（`HTTPS://` も読む）。

| 分類に使うホスト | 種類 | REST | GraphQL |
|---|---|---|---|
| `github.com` | GitHub | `https://api.github.com` | `https://api.github.com/graphql` |
| `ghe.com`・`*.ghe.com`（GHE.com） | GitHub | `https://api.<分類に使うホスト>` | `https://api.<分類に使うホスト>/graphql` |
| 上のどれにもヒットしないもの全部（`gitlab.com`・`*.aslead.cloud`・セルフホスト・未知のホスト） | GitLab | origin の scheme・ホスト・ポートを保った `<scheme>://<ホスト[:ポート]>/api/v4` | 使わない（REST だけ） |

ポートを除くのは分類のときだけで、GitLab の接続先はポートを保つ。GHE.com の origin にポートが付いていると API の URL を組めないので、理由を言って止まる。GHE.com の API と GraphQL の URL の規則は、この表を前提として固定している。判定は sh（`ccnavi_host_parse`）・実行ファイル（`review_host.classify`・`transport_problem`）・Chrome 拡張（`hostclass.ts`）の 3 か所に同じ規則で置き、`tests/fixtures/host-kinds.json` の同じ表で突き合わせる。Chrome 拡張では、この表は自動判定に使い、設定画面でユーザが明示した種類（GitHub / GitLab）は、これより優先する（未知のホストを GitHub として登録すれば Enterprise Server の `/api/v3`・`/api/graphql`。GHE.com を GitHub として登録した場合だけ上の表の接続先）。登録していないホストへ自動で通信したり PAT を送ったりはしない。権限は画面の origin だけでなく API と GraphQL の origin に対しても取る（GHE.com は画面と API の origin が違う）。

**GraphQL が使えない環境。** Claude Code のクラウドでは、GitHub の GraphQL が 403 で拒否される（REST は通る）。GitHub の次の 4 か所は、GraphQL を先に試し、使えない 403 のときだけ REST（`ccr` の道）に替える。GraphQL が通る環境の動きは変わらない。GitLab は変えない。「使えない 403」とは、gh なら本文が `GraphQL is not available` を含むもの、curl なら番号だけの 403 を指す。curl は本文を捨てるので、認証の失敗と見分けが付かない。curl の 403 は替えて、それも失敗したら元の文面を言う。401 と別の理由の 403 は替えない。判定は `ccnavi_host_graphql_blocked`、GraphQL を打つ入口は `gql`（戻り値 3 が「使えない」）。

| 場所 | REST での代わり |
|---|---|
| スレッドの取得（`threads`） | `GET pulls/<N>/ccr/review_threads`（`resolved`・`path`・`line`・`comment_ids`）と `GET pulls/<N>/comments`（全ページ）から、GraphQL と同じ形の JSON を組む。スレッドの id は先頭コメントの id（数字の文字列）。url・本文・作成の時刻は先頭コメントから、`last_*` は `comment_ids` の最後のコメントから取る |
| Draft 外し（`undraft`） | タイトルの `Draft:` 除去の PATCH のあとで `POST pulls/<N>/ccr/ready_for_review` を打つ。戻り値は `GET pulls/<N>` の `draft`（戻り値の形は GraphQL と同じ） |
| 受け入れの返信と解決（`accept_reply`・`accept_resolve`） | `POST pulls/<N>/comments/<id>/replies` と `POST pulls/<N>/ccr/comments/<id>/resolve`。計画の id が数字だけなら REST、そうでなければ GraphQL の node id を使う。id は取り直した結果にあるものだけを通すので、取った道と書く道は同じになる |
| MR が閉じる issue（`ccnavi-start.sh` の `mr_linked_issues`） | `GET pulls/<N>` の本文の `close(s/d)`・`fix(es/ed)`・`resolve(s/d)` に続く `#n`・`owner/repo#n`（大文字小文字不問）。コードブロック・インラインコード・HTML コメント・引用行の中の文字は対象外とする。`not close #n` や、語の途中の `fix`（`bug-fix #n`）も拾わない。MCP で確かめた値を渡す回避路（`--mcp-checked`）は残す |

REST の代わりの道も失敗したときは、ユーザに接続（認証と権限）の確認を頼んで止まる案内を出す（`rest_fallback_failed`）。スレッドとレビューの JSON を作って実行ファイルに直接渡す手順は案内しない（write-and-push の検査で止まるため）。`ccr` の道は通常の GitHub では 404 になる。

**GitLab の依頼コメントへの返信。** GitLab では、ccnavi の依頼コメント（`<!-- ccnavi:request`）にユーザが返信するとスレッドになる。スレッドの判定に使う note は、discussion の note から `system: true` を除いたもの（空なら discussion ごと除く）。note が 1 つでも `resolvable` なら対象にし、全部 `resolvable` でないもの（MR 画面の普通のコメント）は数えない。`resolved` は、`resolvable` な note が全部 resolved のとき。**代表の note**（本文・URL・位置・作成日時・書き手）は、本文が `<!-- ccnavi:` で始まらない最初の note（無ければ先頭）。依頼コメントへの返信があれば返信が代表になり、`decide` が見せる本文、issue と続きの子に転記する本文、キー（URL）、版は返信のものになる。`own` は note の本文が全部目印で始まるときだけ真（JSON の真偽値）で、実行ファイルは `true` / `false` だけを受け、それ以外は `own` が無いものとして、本文の目印と書き手で判定する。ccnavi の投稿として未解決から除くのは、書き手が依頼を投稿したアカウントで、`own` が真のとき。依頼したアカウントが目印なしで返信したスレッドは数える。`ccnavi:accept` の返信を足しても、代表の note・URL・版は変わらない。`last_*`（最後のコメントの id・時刻・書き手）は system を除いた集合の最後の note である。受け入れ台帳（`accepted.json`）は最後のコメントの id で再提起を見分けるので、system note（コミットの追加・行の変更・解決の通知）が付くだけでは再提起にならない。**制約:** `decide` のダイジェストはキーと代表の本文の版だけで照合する。preview のあとに代表の note が消える・変わると止まるが、同じ discussion へ別の返信が付いても検知しない。`last_*` を使うのは、手で動かした子の確定と受け入れ台帳での再提起の判定だけ。

sh は GitLab と GitHub をリモート URL のホストで見分け（`github.com` と GHE.com なら GitHub、それ以外は
GitLab。上の「ホストの分類」）、`gh` / `glab` がそのホストで認証されていれば（起動時に 1 度実行して確かめる）それを、
そうでなければ `curl` と `GITLAB_TOKEN` / `GITHUB_TOKEN` を使う。
どちらも無ければ止まり、MCP などでリモートを読む形を提案する。結果の組み立てに `jq` が要る。
道具は起動時に絶対パスへ解いて固定する（`ccnavi_find_tool` が PATH の絶対パスのエントリだけを見る。相対と空のエントリは飛ばすので、リポジトリの中の `gh` は選ばれない）。origin はホストのポートと scheme をそのまま使い、
URL に埋めた資格情報は読み飛ばして出力では伏せる。実行ファイルの側も同じ規則で origin を読み、
`--lint` が sh の道具が揃っているかを warn で言う。見るのは有無だけで、PATH に `jq` があるか、`gh` / `glab` か `curl` があるか、
`curl` を使う形ならホストに合うトークンの環境変数が空でないか。確かめるのはこの空でないかだけで、ホストへは通信しないので、認証が通るかやトークンが有効かは確かめない（実行ファイルはネットワークに出ない）。

**push の認証。** gh / glab でログイン済みならそのまま通る。origin が http(s) のとき、`ccnavi_git_auth`（`ccnavi-common-host.sh`）が
`gh auth git-credential` / `glab auth git-credential` を origin のホストだけに環境変数で足す（git 2.31 以上。origin と `.gitconfig` は書き換えず、既存の `credential.helper` が先に使われる）。
それ以外は git の設定側（Git Credential Manager か `credential.helper`）に置く。git のラッパースクリプトは `GIT_CONFIG_COUNT` を外し
`GIT_TERMINAL_PROMPT=0` で動くので、環境変数での差し替えも認証画面も使えない。GitLab の実物で分かった注意点は[付録 C](../appendix.md)、
確かめ直すための道具は `tests/manual/gitlab/probe_gitlab.py`。`--result` を実行ファイルに直接渡せるのはユーザの手だけ（`CCNAVI_GUARD_TICKET_APPROVAL`）で、
エージェントはスクリプト 2 本を通す。

push は親だけが行う。hook は子チケットのワークツリーからの `ccnavi-git.sh push` を、誰が打っても拒み
（`DENY_CHILD_PUSH`）、サブエージェントからの `ccnavi-git.sh push` はどこからでも拒む。リモートに置く枝は
マージリクエストの付いた親ブランチ 1 本で、子の成果は子の `finish` が親のブランチへ合流し、そのまま push する。

子のワークツリーかは、push を打つ居場所（payload の cwd から `cd` を追った先、6.3.2）が `.claude/worktrees/<名前>` の
中にあり、その `<名前>` の承認済みチケットに `parent:` の行があるかで決める。探すのはワークスペースルート・プロジェクトの
置き場の下・`.claude/worktrees/` の下のディレクトリ全部の `doing/`・`done/` と `wip/proposals/review/`。git に登録されて
いるか、チケットが閉じているか、YAML として読めるかは問わない（閉じた子のツリーも、親が取り込んで片付けるまでは子のもの）。
`cd` の行き先が読めない形で push を打つと止める。git のラッパースクリプトも同じ検査を持つ（P13）。
