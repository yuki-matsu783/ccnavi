---
type: design
title: 9.18 ユーザ専用の入口
description: ユーザだけが打つ操作を表 1 つで定義し、ccnavi user の入口・ccnavi-user-*.sh・hook の止め・実行ファイルの読み替えと断り・端末要求をその表から作る仕組み
tags: [design-doc, ticket, approval, selfguard]
keywords: [ユーザ専用, user, 入口, 表, user_only, ccnavi-user, hook, 読み替え, 断り, 端末要求, 互換, user-entry]
---

[設計書の入口](../../design.md) > [9. チケット制御](../tickets.md)

### 9.18 ユーザ専用の入口

ユーザだけが打つ操作（承認・レビュー済みの記録・早めに閉じる・記録の削除・保護対象の取り込み（handoff）・承認済みチケットの push・
残った指摘の対応方針・子のブランチの削除）が、どの入口にあるかと、hook と実行ファイルがそれをどう止めるかを書く。
止める判定の細部は [9.5](judging-by-path.md)、承認の手順は [9.4](approval.md)、端末要求は 9.4、`handoff` の中身は [9.19](handoff.md)。

#### 表が正本

何がユーザ専用かは `src/ccnavi/tickets/user_only.py` の表だけが持つ。ほかの場所に手書きの一覧や正規表現を持たない。

| 表 | 持つもの |
|---|---|
| `OPS` | 操作。何をする操作か（`what`）と、実行ファイルが端末を求めるときに標準エラーへ出すオプションの表示名（`terminal`） |
| `FLAGS` | 実行ファイル（`where` が空）と sh スクリプトのオプション。どの止めに入るか（`cli`・`script`）、所属するグループ、`user` の外で断るか（`gated`）、断らない条件（`unless`。`preview` は `--preview` と一緒のとき、`no-yes` は `--yes` が無いとき） |
| `SCRIPTS` | コマンドの位置で止める sh。前の名前が残っていれば並べる |
| `SUBS` | 実行ファイルを直に呼ぶ形のサブコマンド（`ticket`・`review`・`sync`・`worktree`） |
| `BOARD_COMBOS` | ボードで押した操作用のオプションの組み合わせ。全部のグループから 1 つずつがコマンド行に並んだら止める。語のグループ `user-board-verb` に `handoff` を含み、旧い `templates` の組み合わせは `legacy` のグループに残す |
| `VERBS` | `ccnavi user <操作>` の読み替え。操作の語、読み替えた先の内部のオプション、値を取るか |
| `WRAPPER_OPTIONS` | sh と拡張が先に付ける値つきのオプション（`--root`・`--cwd`・`--record-writes`・`--record-tree`） |

表から作られるもの: hook の正規表現とオプションの一覧（`phase_forms`）、実行ファイルの読み替えと断り（`rewrite`・`refused`）、
端末要求のオプションの表示名（`terminal`）。操作を足すときは、この表の行を直す。

実行ファイルを通さず sh が呼ぶだけの操作（`ticket start/finish/cancel/record-risk/prepare/requested/confirm/ready`、`review confirm`、
`sync tidy`、`worktree retire/branch`）は `ccnavi user` に入れず、`SUBS` に置く。

#### `ccnavi user` の入口

ユーザだけが打つ実行ファイルの操作は `ccnavi user <操作>` に集まる。`user` は `WRAPPER_OPTIONS` を読み飛ばした後の最初の語で、大文字小文字は区別しない。
先頭の `--` は読み飛ばさず、実行ファイルは名前空間として扱わない。

| 操作 | 内部の形 |
|---|---|
| `user agree [識別子…]` | `--agree` |
| `user reviewed <N>` | `--reviewed <N>` |
| `user close-early` | `--close-early` |
| `user prune` | `--prune` |
| `user handoff [<相対パス>…]` | `--handoff [<相対パス>…]` |

付随する `--yes`・`--digest`・`--expect`・`--via`・`--reason`・`--result`・`--json` などは、内部の形のときと同じ位置と意味で読む。

見るだけの形のうち、`user` に入れず今までの形のまま通すのは次の 3 つ: `--agree --preview [--verify] [--json]`、`--prune --preview`、
`--reviewed N --accept-unresolved --preview`（`ccnavi-review.sh decide <N> --preview` が内部で呼ぶ）。
`user handoff` は見るだけの形（`--yes` なし）も `user` の入口の中に置き、外では断る（見るだけの形を通す `unless` を持たない）。

ユーザ専用の sh は `ccnavi-user-<操作>.sh` に分かれる。

| sh | もとの形 |
|---|---|
| `ccnavi-user-agree.sh` | `ccnavi-agree.sh` |
| `ccnavi-user-push-approved.sh` | `ccnavi-push-approved.sh` |
| `ccnavi-user-close-early.sh` | `ccnavi-review.sh close-early` |
| `ccnavi-user-decide.sh` | `ccnavi-review.sh decide` の書く側 |
| `ccnavi-user-clean-branch.sh` | `ccnavi-clean.sh --branch` |

`ccnavi-review.sh decide <N> --preview`（一覧を見るだけ）は `ccnavi-review.sh` に残る。書く処理（返信・解決・マーカーを置く呼び出し）は
`ccnavi-user-*.sh` の中だけに置き、共通のファイルには読む側と、エージェントも使うホスト API の関数だけを置く。

#### 実行ファイルの読み替えと断り

`_run`（`entry/cli.py`）は argparse の前に `user_only.rewrite(argv)` を呼ぶ。`user <操作> …` なら、`VERBS` の行に従って内部の形に書き換える。
値を取る操作（`reviewed`）で値が無い、または知らない操作のときは `UserEntryError` で、使い方を標準エラーに出して終了コード 2 で終わる。
`user` で始まらない argv はそのまま通る。

「`user` の入口から来た」という事実は argparse の結果（`args`）に入れず、`rewrite` が返す操作を別の変数（`entry`）で持つ。
オプションで偽装できないようにするため、`args` には何も足さない。

argparse が読んだ後、`entry` が空（`user` の外）で、`user_only.refused(args)` が空でなければ、何も動かさず標準エラーに断りを書いて終了コード 2 で終わる。
`refused` は `FLAGS` の `gated` の行を、argparse の結果で見る（argv の文字列を探さない）。`--yes` は、どの操作にも付くので最後に見る。
`unless` の条件に当たる形（`--agree --preview`、`--prune --preview`、`--reviewed … --preview`）は断らない。`--handoff` は `--yes` の有無によらず断る。
断りの文は、対応する `user` の形（`ccnavi user agree` など）と、見るだけの形があればそれを案内する。

`--version --json` は互換の版（`compat`）15 を返し、`features` に `user-entry` と `handoff` を持つ。

#### hook の止め

hook の止めは表から作る。ユーザ専用の入口の止めは `DENY_TICKET_APPROVAL_CLI`（`builtin:guard-ticket-approval`）の規則にまとまる。種類は 5 つ。

| 種類 | 止める形 |
|---|---|
| `user` の入口 | 実行ファイルの呼び出しで、`WRAPPER_OPTIONS` を読み飛ばした後の最初の語が `user`（`user_entry_forms`）。`--` を挟んだ形も止める。変数・glob・コマンド置換で `user` になりうる語も止める（`hidden_user_entry`）。`prune` は別の deny（`prune_form`）が止める |
| `ccnavi-user-*.sh` | コマンド行のどこに名前が出ても止める（`user_script_pattern`。`human_path_form`）。引数に依らない。表示・検索・閲覧の道具だけが並ぶ行は通す |
| 旧い形 | 旧いオプション（`--agree`・`--reviewed`・`--close-early` など。実行ファイルから外したテンプレート専用のオプション `--import`・`--save-draft`・`--delete-draft`・`--rename-template`・`--move-template`・`--delete-template` も、古い実行ファイルが手元に残るときのために止め続ける）、旧い sh の名前（`ccnavi-agree.sh`・`ccnavi-push-approved.sh`・`ccnavi-approve.sh`）、旧い sh の中のユーザ専用のモード（`ccnavi-review.sh close-early`・`ccnavi-clean.sh --branch`）。古い実行ファイルと sh が手元に残るときのために残る |
| 名前に依らない文字列検査 | 端末を求めない操作（`user agree`・`user reviewed`・`user handoff` と `--yes` の組、`--choices` と `--digest` の組、`--via board`、旧い形の組）と、端末要求を切る形（`CCNAVI_GUARD_TICKET_APPROVAL`・`--guard-ticket-approval`）。名前を変えたコピー・`python -c`・`awk` の `system()` でも止まる |
| sh から呼ぶだけの操作 | `SUBS` の `ticket start/finish/…`・`review confirm/…`・`sync tidy`・`worktree retire/branch` を実行ファイルで直に打つ形 |

エージェントが sh で打てるのは `ccnavi-ticket.sh`・`ccnavi-review.sh`・`ccnavi-git.sh`（`AGENT_SCRIPTS`）で、サブエージェントにはさらに
`SUBAGENT_FORBIDDEN` のサブコマンドが閉じる。`ccnavi user --help` も `user` の入口なので止まる。

hook は擬似端末を作る実行役（`script`・`tmux`・`screen`・`expect`）越しの呼び出しを読めない。

#### 端末要求の位置づけ

実行ファイルは、`user agree`・`user reviewed`・`user close-early`・`user prune` と、書くとき（`--yes`）の `user handoff` で、標準入力が端末のときだけ動く。
端末を求めるときに標準エラーへ出すオプション名は `OPS` の `terminal`（`user_only.terminal`）が持つ。

端末を求めない経路（`--yes` の承認、ボードの指摘の対応方針、ボードからの handoff の取り込み）では、hook の文字列検査が唯一の防御で、端末要求は 2 段目ではない。
擬似端末を使えば端末要求は満たせる。
