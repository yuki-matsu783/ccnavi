---
type: guide
title: スクリプトの出力に次の一手を書く
description: チケット作業を進めるshと実行ファイルの出力で、次の一手を「次:」で始まる行に出す決まり。行頭の語の使い分け、書き方、出さない場面、テストの型
tags: [sh-scripts, exe-boundary]
keywords: [出力, 案内, 次:, 次の一手, 必須:, 案内:, 次の手:, チケット作業, ccnavi-start.sh, ticket new, printf, stdout]
---

# スクリプトの出力に次の一手を書く

エージェントは、スクリプトの出力を読んで次に何をするかを決める。出力が次の一手を言わないと、エージェントは
手元の知識で手順を補う。補えなければ、決まりを外れた道（提案を手で書く、チケットを起こさずに直接実装するなど）に進む。

チケット作業を進めるsh（`.ccnavi/scripts/`）と実行ファイル（`src/ccnavi/`）の出力は、次の決まりで書く。拒否の文面の型と、止め方の選び方は[止め方の型と選び方](stop-patterns.md)にある。

## 行頭の語

`次:` `必須:` `案内:` `次の手:` の意味は[ccnaviの呼び名](../../.ccnavi/docs/terms.md)の「出力の行頭の語」が正本（読む側の約束）。ここは出す側の数と、ほかの語を書く。

| 行頭の語 | 意味 | 1回の出力での数 |
|---|---|---|
| `次:` | 次の一手（1行に1手だけ） | 1行 |
| `必須:` | 書き漏らすと止まる・警告される項目 | 必要なだけ |
| `案内:` | 補足と注意。`次:` の一手とは別で、補足として手順を含むことがある。手が複数あるときの2手目以降もここに置く | 必要なだけ |
| `次の手:` | 次の一手が1つに決まらないときの、番号つきの選択肢の見出し | 1つ |
| `MCP で行う手順:` | ホストに届かないときの、MCPでの代行手順（`ccnavi-start.sh`など） | 1つ |
| `確認:` | 事前確認の項目ごとの結果（`ccnavi-start.sh --check` は標準出力、`ccnavi-ticket.sh` の自動の確認は標準エラーに、ok でない項目だけ） | 項目ごとに1行 |

## 決まり

- 成功の終わりと、「止まったが続きがある」終わり（終了コード4の代行案内など）では、次の一手が1つに決まるなら `次:` を出す
- `次:` は1行に1手だけ。手が複数あって順に打つときは、1手目だけを `次:` にし、2手目以降は `案内:`（文面ファイルの `note`）に回す。承認の関門（`tickets/core/approve.py` の `Readiness`）が複数の手を出すときも、最初の手だけを `次:` にする
- 次の一手が複数あって決まらないときは、`次:` にせず、`次の手:` に番号つきで並べる。決められるのはユーザなら、そう言う
- 次がユーザの操作のときは、「ユーザに〜を頼んで返事を待つ」の形にする。エージェントが打てない操作を、エージェントへの指示として書かない
- パスの引用: パスは `ccnavi_sh_quote`（sh）・`settings.shell_path`（Python）で引用し、別のツリーで打つものは 1 行で完結する `cd <引用したパス> && …` で始める。Write/Edit に渡すパスだけは、引用しない生の形で出す。メッセージは `-m '<メッセージ>'` と単引用符で書き、文全体を `'...'` で囲まない
- 識別子・タイトル・番号・パスは、分かる値を埋めて、そのまま打てる形にする。分からない値だけ `'<触るファイルの glob>'` のように `<…>` で残し、`sh_quote` などで1語に包む
- `<…>` の仮の値を残した次の一手を、そのまま打たれても、誤った成果物ができないようにする。受け側（`ccnavi ticket new` の `--scope` と `--title`）が `<…>` を含む値を断る。新しく仮の値を出すときは、受け側にも同じ断りを足す
- 「環境が揃っていない」で止まる出力（終了コード 3 など、道具・依存・ブラウザが無い失敗）には、直し方を書いた文書のパスと、その索引の引き方（`ccnavi --docs --keyword <語>`）を `案内:` で添える。エージェントが出力の 1 行を読み飛ばして、資料を引かずに「この環境には無い」と報告しないようにする。hook も、失敗の最初の 1 回と、連続が 3 の倍数になる回に索引の案内を出すが、スクリプトの出力そのものにも直し方の行き先を置く（例は `extensions/vscode/ccnavi-board/scripts/render-board.js`）。索引に載らない文書（frontmatter の無い手順書）へ案内するときは、パスだけ書く
- 詰まったときの行き先も言う。承認が通らない、ホストに届かないなど、エージェントが自力で進められない場面では、「直接実装に切り替えず、ユーザに報告して止まる」と添える。決まりを外れた道を、出力が先回りして塞ぐ
- 書き漏らすと止まる・警告される項目（検査が断る・警告するもの）は、長い `次:` の途中に置かず、`必須:` の行に独立させる。`次:` に並べた手順は、途中の 1 つが読み飛ばされやすい
- 次の一手の中身をshが計算しない。チケットの状態から決まる次は、`ccnavi-ticket.sh status` を打つよう促す（`exe-boundary.md`）
- 文言にADR番号を書かない（`environment.md`）

## 出さない場面

- 状態を報告するだけで、次の一手がユーザの判断になるとき（例: `integrate-done` のあと、取り込んだコミットをリモートへ送るか）
- 何も動かなかったときは何も出さない、と決めているとき（`ccnavi-fetch.sh`）
- ユーザがターミナルで打つスクリプト（`ccnavi-user-*.sh`。`ccnavi-user-agree.sh`、`ccnavi-user-push-approved.sh` など）。エージェントへの通知は、拡張が渡す承認の文（`user agree --yes` の `prompt`）と `ccnavi-ticket.sh status` が受け持つ
- 共通関数（`ccnavi-common*.sh`）。止める理由ごとに次が違うので、呼び出し元で出す
- `ccnavi-clean.sh` の dry-run。node があると `exec` で `ccnavi-clean.js` に代わり、出力はそちらが出す。shから行を足せない

## 実装済みの場面

次を言う場面と、その文の置き場（`.ccnavi/messages/next-cli.yml`）。状態の報告はコードが言い、次の一手だけを文面ファイルから引く。

| 場面 | 出力元 | 次 |
|---|---|---|
| 子の `ticket cancel` | `tickets/lifecycle/commands.py`（`NEXT_OPS_CHILD_DONE`） | 親の `status` を確かめる。親の取り消しと、close-early がまとめて行う取り消しでは言わない |
| `ticket record-risk` | 同上（`NEXT_OPS_RECORD_RISK`） | `finish` を打ち直す |
| `ticket start`・`finish`・`cancel`・`consolidate` が、書く前に分かる条件で断ったとき（`--check` も同じ） | 同上・`tickets/lifecycle/consolidate.py`（`NEXT_OPS_REFUSED`・`NEXT_OPS_REFUSED__CANCEL`） | 理由の行のあとに 1 行。直して同じ操作を打ち直す（`cancel` は `--reason <理由>` つき） |
| `review-request`・`decide` が、書く前に分かる条件で断ったとき（`--check` も同じ） | `tickets/review/request.py`・`tickets/review/decide.py`（`NEXT_REVIEW_REQUEST_REFUSED`・`NEXT_DECIDE_REFUSED`） | 理由の行のあとに 1 行。直して同じ操作を打ち直す（`decide` はユーザが打つ） |
| 親の `ticket start` | 同上・`tickets/lifecycle/plan.py`（`NEXT_OPS_START_PARENT`・`NEXT_OPS_START_CHILDLESS`） | `status` で承認済みの子を確かめる。子のないフェーズがあれば、子の提案を書く |
| `ccnavi-review.sh confirm` | `tickets/core/review.py`（`NEXT_REVIEW_CONFIRMED`・`NEXT_REVIEW_LAST`） | 途中は親の `status`。最後のフェーズは親の `finish`（そのあとの ready は `案内:`） |
| `approval-request --check` | `tickets/proposal/propose.py`（`NEXT_APPROVAL_REQUEST_CHECKED`） | `approval-request` を打つ |
| `--fill-phases`・`--plan-order --write` | `entry/cli_plan.py`（`NEXT_PLAN_VERIFY`） | `--lint` か `--agree --preview --verify` で確かめる |
| `ticket learn` | `tickets/learning/notes.py`（`NEXT_LEARN_*`） | 記録をコミットする |
| `ticket new`・`ticket revise` | `tickets/proposal/scaffold.py`・`revise.py`（`NEXT_NEW_REWRITE`・`NEXT_REVISE`） | 雛形を直す。送るまでの手は `案内:` |
| レビューの依頼 | `tickets/review/request.py`（`NEXT_REVIEW_REQUESTED`） | ユーザのレビューを待つ |
| `ccnavi-review.sh ready` の成功 | `ccnavi-review.sh`（`NEXT_READY_DONE`。`next-sh.yml`） | マージをユーザに頼んで待つ。マージ後の同期は `案内:` |

ユーザが端末で打つ入口（`ccnavi-user-decide.sh`・`ccnavi-user-close-early.sh`）の完了の行は、事実と手順の説明に留め、`次:` を足さない（上の「出さない場面」）。

## 次の一手を言う操作の区分

操作を足すときは、成功した出力が次のどの区分かを決める。穴は、次を言うべき操作が黙ることで生まれる。

| 区分 | 意味 | 出力の形 |
|---|---|---|
| 次を言う | 成功のあとに、エージェントがやることが 1 つに決まる | `次:` を 1 行。2 手目は `案内:` |
| ユーザ待ち | 次はユーザの操作で、エージェントは返事を待つ | `次:` を「ユーザに〜を頼んで返事を待つ」の形で 1 行。マージ後の手など、待ったあとの手は `案内:` |
| 終わり | そこで作業が閉じる。言うべき次が無い | `次:` を出さない。結果の事実だけを言う |
| 対象外 | 次の一手を渡す役ではない出力 | `次:` を出さない |

- 「終わり」と「対象外」で `次:` を出さないのは、無理に足すとエージェントが要らない手を打つからである。「次を言う」「ユーザ待ち」で黙ると、エージェントは手元の知識で手順を補ってしまう
- 終わりの例: `ccnavi-sync.sh` の成功、`worktree drop`・`tidy`・`retire` の成功、`integrate-done` の片付け（取り込んだコミットをリモートへ送るかはユーザの判断。上の「出さない場面」）、`ticket next-id` のように値を返す操作
- 対象外の例: 読むだけの操作（`status`・`--lint`・`--docs`・`worktree remaining` など）、ユーザが端末で打つ入口（`ccnavi-user-*.sh`）、共通関数（`ccnavi-common*.sh`）
- この一覧は網羅ではない。一覧に無い操作は、成功したら次を言うか、終わりか対象外かを、理由つきで決める。決めた理由は、操作を足す変更の説明に書く
- 機械が守れるのは一部だけ。直書きの `次:` を増やさないことは `tests/core/test_next_line_literal.py` が見て、`next` の文が 1 行・補足が `案内:` になることは `tests/core/test_texts_catalog.py` が見る。しかし、区分の宣言はコードに持たないので、`次:` を言うべき操作が黙っていることは、機械では全部は見つけられない。レビューで、成功した出力の区分を確かめる

## 書き方

- 文は[文面ファイル](message-files.md)に置く。shは `ccnavi_text "$root" <識別子> "名前=値"…` で標準出力へ出し、実行ファイルは `texts.say(root, "<識別子>", {…})` で組む。`次:` や `案内:` の見出しは文面ファイルの側が付けるので、shとコードに書かない（文面ファイルへ移していない出力は[文面の移行の一覧](message-migration.md)の「残り」にあり、そこは `printf '次: …\n' …` と `stdout.write("次: …\n")` で出す）。止まる枝も、既存の出力と同じ側に出す（`>&2` を付ける）
- 1行にする。長くなる補足は、別の行の `案内:` に分ける
- 行頭の `次: ` は、機械が数える。行の途中に「次:」を書かない。インデントして出す一覧（承認の確認の下の注記など）の中でも、`次: ` の行は行頭に置く。文面ファイルの `next` は `次:` で始めず、1 行にする（検査が見る）
- 既存の言い方（`次は、…`、`次にやること:`、`次を実行する:`、`流れ:`）は、次が 1 つに決まるものだけ `次:` に付け替える。文の中身は変えない（テストが語句を見ている）

## テストの型

- 行頭が `次: ` の行を数えて、1行であることを見る（`tests/sh/test_start_sh.py`）
- 値が埋まっていることを見る。識別子・タイトル・`--issue`・`--mr` が、そのまま打てる形で並んでいる。移した出力のテストは、文面ファイルを引いた結果に対して書く
- 出さない場面で出ていないことを見る（既存の検査で足りることが多い）
