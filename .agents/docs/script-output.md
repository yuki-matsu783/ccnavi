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
`ccnavi-start.sh --issue` が「親チケットか提案が出来たら…打ち直す」としか言わず、提案を作る `ccnavi ticket new` を示さなかったために、
提案を手書きして詰まり、直接実装に進んだ事故があった。

チケット作業を進めるsh（`.ccnavi/scripts/`）と実行ファイル（`src/ccnavi/`）の出力は、次の決まりで書く。拒否の文面の型と、止め方の選び方は[止め方の型と選び方](stop-patterns.md)にある。

## 行頭の語

`次:` `必須:` `案内:` `次の手:` の意味は[ccnaviの呼び名](../../.ccnavi/docs/terms.md)の「出力の行頭の語」が正本（読む側の約束）。ここは出す側の数と、ほかの語を書く。

| 行頭の語 | 意味 | 1回の出力での数 |
|---|---|---|
| `次:` | 次の一手 | 1行 |
| `必須:` | 書き漏らすと止まる・警告される項目 | 必要なだけ |
| `案内:` | 補足と注意。`次:` の一手とは別で、補足として手順を含むことがある | 必要なだけ |
| `次の手:` | 次の一手が1つに決まらないときの、番号つきの選択肢の見出し | 1つ |
| `MCP で行う手順:` | ホストに届かないときの、MCPでの代行手順（`ccnavi-start.sh`など） | 1つ |
| `確認:` | 事前確認の項目ごとの結果（`ccnavi-start.sh --check` は標準出力、`ccnavi-ticket.sh` の自動の確認は標準エラーに、ok でない項目だけ） | 項目ごとに1行 |

## 決まり

- 成功の終わりと、「止まったが続きがある」終わり（終了コード4の代行案内など）では、次の一手が1つに決まるなら `次:` を出す
- 次の一手が複数あって決まらないときは、`次:` にせず、`次の手:` に番号つきで並べる。決められるのはユーザなら、そう言う
- 次がユーザの操作のときは、「ユーザに〜を頼んで返事を待つ」の形にする。エージェントが打てない操作を、エージェントへの指示として書かない
- パスの引用: パスは `ccnavi_sh_quote`（sh）・`settings.shell_path`（Python）で引用し、別のツリーで打つものは 1 行で完結する `cd <引用したパス> && …` で始める。Write/Edit に渡すパスだけは、引用しない生の形で出す。メッセージは `-m '<メッセージ>'` と単引用符で書き、文全体を `'...'` で囲まない
- 識別子・タイトル・番号・パスは、分かる値を埋めて、そのまま打てる形にする。分からない値だけ `'<触るファイルの glob>'` のように `<…>` で残し、`sh_quote` などで1語に包む
- `<…>` の仮の値を残した次の一手を、そのまま打たれても、誤った成果物ができないようにする。受け側（`ccnavi ticket new` の `--scope` と `--title`）が `<…>` を含む値を断る。新しく仮の値を出すときは、受け側にも同じ断りを足す
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

## 書き方

- 文は[文面ファイル](message-files.md)に置く。shは `ccnavi_text "$root" <識別子> "名前=値"…` で標準出力へ出し、実行ファイルは `texts.say(root, "<識別子>", {…})` で組む。`次:` や `案内:` の見出しは文面ファイルの側が付けるので、shとコードに書かない（移していない場面は[文面の移行の一覧](message-migration.md)にあり、そこは今までどおり `printf '次: …\n' …` と `stdout.write("次: …\n")`）。止まる枝も、既存の出力と同じ側に出す（`>&2` を付ける）
- 1行にする。長くなる補足は、別の行の `案内:` に分ける
- 行頭の `次: ` は、機械が数える。行の途中に「次:」を書かない。文面ファイルの `next` は `次:` で始めず、1 行にする（検査が見る）
- 既存の言い方（`次は、…`、`次にやること:`）は、`次:` に付け替える。文の中身は変えない（テストが語句を見ている）

## テストの型

- 行頭が `次: ` の行を数えて、1行であることを見る（`tests/sh/test_start_sh.py`）
- 値が埋まっていることを見る。識別子・タイトル・`--issue`・`--mr` が、そのまま打てる形で並んでいる。移した出力のテストは、文面ファイルを引いた結果に対して書く
- 出さない場面で出ていないことを見る（既存の検査で足りることが多い）

## 実装済みの場面

| 場面 | 出す次の一手 | 場所 |
|---|---|---|
| `ccnavi-start.sh` の成功、親チケットも提案も無い | `ccnavi-launcher.sh ticket new …`（識別子・タイトル・issue・MRを埋める） | `ccnavi-start.sh` の `next_ticket_lines` |
| `ccnavi-start.sh` の成功、親チケットも提案も無い（使えるテンプレートがあるときだけ） | `案内:` を 1 行足す（`次:` は増やさない。`--template` と `ccnavi --templates`。件数は `--templates --json` の `usable` の数） | `ccnavi-start.sh` の `template_note` |
| `ccnavi-start.sh` の成功、チケットが既にある | `ccnavi-ticket.sh status <識別子>` | `ccnavi-start.sh` の `report` |
| `ccnavi-start.sh` の成功、取り込みが他の理由で止まった | 理由を直して `ccnavi-sync.sh` を打ち直す | `ccnavi-start.sh` の `run_sync` |
| `ccnavi-start.sh --issue` で issue が closed（MR なし） | 次の一手は決まらないので `次:` は出さず、`次の手:` に reopen して打ち直す・`--reopened` で続ける・別の issue・やめる、を番号つきで並べる（終了コード 3）。状態を読めないときは止めず `確認: issue #<番号> の状態: 未確認` | `ccnavi-start.sh` の `issue_state_gate` |
| `ccnavi-start.sh` の初期コミットで git の作成者情報が無い | ユーザに `git config user.name` と `user.email` の設定を頼んで返事を待ち、設定できたら打ち直す（`NEXT_START_IDENT`）。作ったワークツリーを消せなかったときは `案内:` で残りを言う（`NOTE_START_WORKTREE_KEPT`） | `ccnavi-start.sh` の `ensure_initial_commit` |
| `ccnavi-start.sh --check` | 結果に応じた一手（ユーザに認証や権限を頼む、MCPで代行する、作成者情報の設定を頼む、など。ok なら出さない） | `ccnavi-common-host.sh` の `ccnavi_pf_next` |
| `ccnavi-ticket.sh` の start・finish・propose・consolidate の事前確認 | 同上（標準エラーに `確認:` と `次:` を出す。標準出力には何も足さない） | `ccnavi-ticket.sh` の `ticket_preflight` |
| `ccnavi-start.sh --dry-run` | `--dry-run` を外した同じコマンド | `ccnavi-start.sh` の `real_args` |
| `ccnavi-stage.sh` | 下書きを編集し、ユーザに `ccnavi-user-handoff.sh` での取り込みを案内する | `ccnavi-stage.sh` |
| `ccnavi-review.sh` の早めに閉じた | 親に閉じて片付け、`ready` を打たせる | `ccnavi-review.sh` |
| `ccnavi ticket new` の成功 | `次:` は提案の空欄を埋めること。子の提案は、無いと承認の確認と `propose` が警告するので、`必須:` の行に独立させる。`--lint`・`--agree --preview --verify`・`propose`・承認の依頼は `案内:` の行 | `tickets/proposal/scaffold.py` |
| `ccnavi ticket revise` の成功 | `--feedback` なら `--agree --preview --verify` を通して `propose` で送り、ユーザに承認を頼む。写しだけのときは、まず雛形を直す | `tickets/proposal/revise.py` |
| `ccnavi-ticket.sh propose` の成功 | ユーザに承認を頼んで返事を待つ | `ccnavi-ticket.sh` |
| `ccnavi-ticket.sh propose` が親の計画の誤りで断る | 直して `propose` を打ち直す（指摘の行は `workflow` の返す文面をそのまま出す） | `tickets/proposal/propose.py` |
| `ccnavi --agree --preview --verify` | 通れば承認を頼む。通らなければ提案を直して打ち直す | `tickets/proposal/agree.py` |
| 承認後の通知 | 親の `start`、子の `start` | `tickets/proposal/agree_screen.py` |
| `ccnavi-ticket.sh start`（子） | サブエージェントに実装を任せ、終わったら `finish` | `tickets/lifecycle/commands.py` |
| `ccnavi-ticket.sh start`（親で子の無いフェーズが残る） | 子の提案を書いて `propose` で送り、承認をユーザに頼む（子の識別子の例を埋める） | `tickets/lifecycle/plan.py` |
| `ccnavi-ticket.sh finish`（子で、レビュー不要） | 親の `status` | `tickets/lifecycle/commands.py` |
| `ccnavi-ticket.sh finish`（親） | 移動をコミットして push し、Draft を解除する | `tickets/lifecycle/commands.py` |
| `ccnavi-git.sh integrate` | 差分と変わるファイルをユーザに見せる。対話では、続けて `integrate-done` を打つと確認が出て、許可されたら確定する。非対話では、返事を待ってターンを終え、了承を得ていれば `integrate-done` で確定する（やめるなら `integrate --abort`）。取り込む差分が無いときも、ユーザに伝えて返事を待ち、片付けは了承のあと | `ccnavi-git.sh` |
| `ccnavi-worktree.sh` の成功（切ったとき・既にあるとき） | 編集は、ワークツリーの中で行う。git は `cd <引用したパス> && <ラッパー> <サブコマンド>`。作業を終えてからまとめてコミットし、続けて integrate を打つ（確認を待つ間はコミットしない）。名前の警告の後に出る最後の行 | `ccnavi-worktree.sh` の `print_next` |

issue・MRを指定された依頼文には、ccnaviのUserPromptSubmitが「出力に『次:』があればそれに従う」と足す（`tickets/lifecycle/branchfind.py` の `_start_lines`）。
