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
| `OPS` | 操作。何をする操作か（`what`）と、実行ファイルが端末を求めるときに標準エラーへ出す表示名（`terminal`。`user agree` の形） |
| `FLAGS` | 実行ファイル（`where` が空）と sh スクリプトのオプション。どの止めに入るか（`cli`・`script`）、所属するグループ、`user` の外で断るか（`gated`）、断らない条件（`unless`。`preview` は `--preview` と一緒のとき、`no-yes` は `--yes` が無いとき） |
| `SCRIPTS` | コマンドの位置で止める sh（今は空。ユーザ専用の sh は `ccnavi-user-*.sh` の名前で止める） |
| `SUBS` | 実行ファイルを直に呼ぶ形のサブコマンド（`ticket`・`review`・`sync`・`worktree`・`ops`。`ops` は書き込む副命令だけを止め、読むだけの `pending`・`show`・`reconcile` は通す） |
| `BOARD_COMBOS` | ボードで押した操作の、オプションの組み合わせ。どのグループからも 1 つずつがコマンド行に並んだら止める。語のグループ `user-board-verb` に `handoff` を含む。`--via`（グループ `via`）と `board` の語（グループ `board`）の組は、`user` を伴わなくても止める |
| `VERBS` | `ccnavi user <操作>` の読み替え。操作の語、読み替えた先の内部のオプション、値を取るか |
| `WRAPPER_OPTIONS` | sh と拡張が先に付ける値つきのオプション（`--root`・`--cwd`・`--record-writes`・`--record-tree`） |

表から作られるもの: hook の正規表現とオプションの一覧（`phase_forms`）、実行ファイルの読み替えと断り（`rewrite`・`refused`）、
端末要求のときの表示名（`terminal`）。操作を足すときは、この表の行を直す。

実行ファイルを通さず sh が呼ぶだけの操作（`ticket start/finish/cancel/propose/consolidate/record-risk/prepare/requested/confirm/ready`、`review confirm`、
`sync tidy`、`worktree retire/branch`）は `ccnavi user` に入れず、`SUBS` に置く。

#### `ccnavi user` の入口

ユーザだけが打つ実行ファイルの操作は `ccnavi user <操作>` に集まる。`user` は `WRAPPER_OPTIONS` を読み飛ばした後の最初の語で、ASCII の大文字小文字は区別しない。
比べるのはコマンドの語なので `casefold` は使わず、ASCII の大文字だけを小文字にする（`casefold` は `ſ`（U+017F）を `s` に、`ﬀ`（U+FB00）を `ff` にする）。
`user` の位置か操作の位置に ASCII でない語があり、互換の読み（NFKC と casefold）で入口の語になるなら（`uſer`・`handoﬀ`）、`UserEntryError` で断る。
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

- `ccnavi-user-agree.sh`
- `ccnavi-user-push-approved.sh`
- `ccnavi-user-close-early.sh`
- `ccnavi-user-decide.sh`
- `ccnavi-user-clean-branch.sh`
- `ccnavi-user-lib-review.sh`（`decide` と `close-early` が呼ぶ部品で、ユーザの判断をホストに書く。単独では打たない）

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

`--version --json` は互換の版（`compat`）18 を返し、`features` に `user-entry` と `handoff` を持つ。

#### hook の止め

hook の止めは表から作る。ユーザ専用の入口の止めは `DENY_TICKET_APPROVAL_CLI`（`builtin:guard-ticket-approval`）の規則にまとまる。種類は 5 つ。

| 種類 | 止める形 |
|---|---|
| `user` の入口 | 実行ファイルの呼び出しで、`WRAPPER_OPTIONS` を読み飛ばした後の最初の語が `user`（`user_entry_forms`）。`--` を挟んだ形も止める。変数・glob・コマンド置換で `user` になりうる語も止める（`hidden_user_entry`）。`prune` は別の deny（`prune_form`）が止める。実行ファイルの呼び出しとして読む表記は、名前（`ccnavi`・`ccnavi.exe`・実行ファイルのパス・`ccnavi-launcher.sh`）と、Python から走らせる形（`python[版]`・`py` に、`-u`・`-I`・`-OO`・`-3`・`-W x`・`-X utf8` などの選択肢を挟み、`-m ccnavi`・`-mccnavi`・`-m ccnavi.__main__`・`main.py` で入る。前に `uv run`・`poetry run`・`pipenv run` を置いてもよい） |
| `ccnavi-user-*.sh` | コマンド行のどこに名前が出ても止める（`user_script_pattern`。`human_path_form`）。引数に依らない。表示・検索・閲覧の道具だけが並ぶ行は通す |
| 直接打つ形 | 実行ファイルのオプション（`--agree`・`--reviewed`・`--close-early` など）を `user` の外で直に打つ形 |
| 名前に依らない文字列検査 | 端末を求めない操作（`user agree`・`user reviewed`・`user handoff` と `--yes` の組、`--choices` と `--digest` の組、`user` を伴わない単独の `--via board` / `--via=board`）と、端末要求を切る形（`CCNAVI_GUARD_TICKET_APPROVAL`、`CCNAVI_` で始まる名前の途中に展開が混じる形（`env CCNAVI_GUARD_TICKET_${X}APPROVAL=disable`）、`--guard-ticket-approval`）。名前を変えたコピー・`python -c`・`awk` の `system()` でも止まる。暫定の止めとして、単独の語の `user` があるコマンド行で、`--` で始まる語に展開（`$`・バッククォート）が混じれば止める（`--y${X}es`・`--y$(echo es)`。入口の表記を読めない形のため。無関係の語の展開まで止める側に広がる） |
| sh から呼ぶだけの操作 | `SUBS` の `ticket start/finish/…`・`review confirm/…`・`sync tidy`・`worktree retire/branch` を実行ファイルで直に打つ形 |

エージェントが sh で打てるのは `ccnavi-ticket.sh`・`ccnavi-review.sh`・`ccnavi-git.sh`（`AGENT_SCRIPTS`）で、サブエージェントは
さらに役割の表（`user_only.SUBAGENT_ROLES`）で決めた操作を打てない。実行ファイルを直に呼ぶ `SUBS` の状態操作も、`CCNAVI_GUARD_TICKET_APPROVAL` の値によらず打てない（REQ-TKT-10）。状態を持つ `ccnavi-ticket.sh` と `ccnavi-review.sh` は、読み取りの副命令だけ打てる。`ccnavi-git.sh` は push・worktree・integrate 系だけを止め、`ccnavi-start.sh`・`ccnavi-sync.sh`・`ccnavi-worktree.sh`・`ccnavi-clean.sh` は副命令に関わらず止める。`ccnavi user --help` も `user` の入口なので止まる。

hook は擬似端末を作る実行役（`script`・`tmux`・`screen`・`expect`）越しの呼び出しを読めない。
`--` で始まらない語（`$X` だけ）で `--yes` を組み立て、入口の名前も読めない形（名前を変えたコピー）は、文字列検査に当たらない。

#### 端末要求の位置づけ

実行ファイルは、`user agree`・`user reviewed`・`user close-early`・`user prune` と、書くとき（`--yes`）の `user handoff` で、標準入力が端末のときだけ動く。
端末を求めるときに標準エラーへ出すオプション名は `OPS` の `terminal`（`user_only.terminal`）が持つ。

端末を求めない経路（`--yes` の承認、ボードの指摘の対応方針、ボードからの handoff の取り込み）では、hook の文字列検査が唯一の防御で、端末要求は 2 段目ではない。
擬似端末を使えば端末要求は満たせる。
