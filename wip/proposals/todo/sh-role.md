---
version: 1
ticket: sh-role
title: 子チケットの push を hook でも止め、保護済みの sh の前に置いた環境変数で検査を外せる道を閉じ、sh の役割を決める
human_review:
  required: true
  reason: 組み込みの判定を足し、保護済みの sh（ccnavi-git.sh）の検査を直すため。配布先すべてに届く
rationale: |
  子チケットのワークツリーからの push は、2 か所が別々の材料で止めている。hook（`phase.py`）は
  サブエージェントだけを、`ccnavi-git.sh` は cwd のツリーの承認済みチケットを自分で探して止める。
  sh の検査は、承認済みチケットを探すツリーを `approval.trees` と同じ形に書き直して持っていて、
  #120 ではそれがずれて、親のワークツリーに置いた子を見落としていた。

  その sh の検査は、コマンドの前に置いた環境変数で外せる。`CCNAVI_TICKETS_APPROVED=/x sh
  .ccnavi/scripts/ccnavi-git.sh push` を hook は allow で通し、sh は存在しない置き場を見て通す。
  拒否されたエージェントが言い換えて再試行する形そのもので、§2.1 の「逸れていく LLM」の範囲に入る。

  止める場所を、プロセスの環境に左右されない hook に置く。sh の検査は 2 重目として残し、symlink を
  またぐと丸ごと外れる穴を直す。あわせて、sh が何を持ってよいかを原則として書く。
plan:
- design
- acceptance
- implement
- staging
- docs
feedback: []
allow:
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: docs/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: CONTEXT.md
- match: Write|Edit
  glob: CLAUDE.md
- match: Write|Edit
  glob: README.md
started_at: ""
completed_at: ""
base_sha: ""
---

## 初版からの変更

初版は「sh が ccnavi の状態を読む判定を実行ファイルへ問い合わせる（`ccnavi tree --json`）」形でした。
Sonnet と Opus の敵対的レビューで次が分かり、やめました。

- **実行ファイルを差し替えれば検査が開く。** sh は実行ファイルを `CCNAVI_BIN_PATH` の環境変数から探す
  （`ccnavi-common.sh:140`）。エージェントが `scratchpad/` に置いた偽物を指せば、「parent 無し」を返させて通せる。
  ADR-0028 が潰した `--review-fixture` と同じ形
- **止める範囲が狭まる。** いまの sh は Python の読み方より広く止めている（下の表）。`approval.scan` を素直に
  呼ぶと、閉じた子・レビュー待ちの子・壊れたチケット・登録されていないツリーからの push が通るようになる
- **「hook では止められない」は誤り。** `phase.py:49-52` は `ccnavi-git.sh … push` をすでに名指しで止めている
  （サブエージェントだけ）。hook は `settings.json` の env で起動するので、コマンドの前の代入の影響を受けない

## いま何がそうなっているか

### 子の push を止める 2 か所

| | hook（`phase.py:49`、`judge.py:210`） | `ccnavi-git.sh:465-518` |
|---|---|---|
| 誰に効くか | サブエージェントだけ（`payload.agent_id` があるとき） | 誰にでも |
| 何で見分けるか | 素性（サブエージェントかどうか） | cwd のツリーの名前と、その名前の承認済みチケットの `parent:` |
| 親のツリーへ `cd` して打つと | 止まる | 通る（コメントにそう書いてある） |
| 親エージェントが子のツリーで打つと | 通る | 止まる |

親エージェントが子のツリーで打つ push を止めるのは、sh だけです。

### sh の検査の広さ（Python の読み方との比較）

| 観点 | `ccnavi-git.sh` | Python（`approval.scan` / `tree.tree_of`） |
|---|---|---|
| どのツリーを子と見るか | `.claude/worktrees/<名前>` の下なら何でも | git にワークツリーとして登録されたものだけ |
| どのチケットを見るか | `doing/`・`done/`・`review/` の 3 か所、閉じたものも | 開いたもの。権威のツリー以外は落とす |
| 壊れたチケット | `grep '^parent:'` なので、YAML として読めなくても当たる | 苦情に回り、判定からは消える |

hook に持ってくるときは、この広さを保つ必要があります。

### 環境変数で外れる（`ccnavi --test` で確かめた）

| コマンド | hook の答え | sh の中で起きること |
|---|---|---|
| `CCNAVI_TICKETS_APPROVED=/x sh .ccnavi/scripts/ccnavi-git.sh push` | allow | 存在しない置き場を見て通す |
| `env CCNAVI_PROJECTS=/x sh .ccnavi/scripts/ccnavi-git.sh push` | allow | 数えるツリーが変わる |
| `export CCNAVI_WORKSPACE=/x; sh .ccnavi/scripts/ccnavi-git.sh push` | allow | ワークスペースルートが変わる |

sh が読む環境変数のうち、検査の材料を変えるものは `CCNAVI_WORKSPACE`・`CCNAVI_PROJECTS`・
`CCNAVI_TICKETS_APPROVED`・`CCNAVI_TICKETS_PROPOSAL`・`CCNAVI_STATE`・`CCNAVI_BIN_PATH` の 6 つ。
`CCNAVI_GIT_MAX_LINES`・`CCNAVI_GIT_FAIL_LINES`・`CCNAVI_GIT_KEEP_LOGS`・`CCNAVI_FETCH_TIMEOUT` は
出力の量と待ち時間だけを変えます。

### symlink をまたぐと sh の検査が丸ごと外れる

`WS` は `ccnavi_abs`（論理の `pwd`）、`push_top` は `git rev-parse --show-toplevel`（実際のパス）で作ります。
ワークスペースが symlink をまたぐ場所（macOS の `/tmp` → `/private/tmp` など）では
`"$push_root"/.claude/worktrees/*` の case に当たらず、検査が飛びます（Opus がスクラッチパッドで確かめた）。

### sh の役割が書かれていない

決まっているのは「実行ファイルはネットワークに出ない」（P11、ADR-0028）と「拡張は判定を自分で出さない」
（ADR-0035）の 2 本だけです。sh が持ってよいものは書かれていません。実際の sh は 4 つの役を持っています。

| 役 | sh |
|---|---|
| 外との出入り（写しを渡す、承認済みチケットを運ぶ） | `ccnavi-review.sh`、`ccnavi-fetch.sh`、`ccnavi-push-approved.sh` |
| 実行ファイルへの入口 | `ccnavi-ticket.sh`、`ccnavi-approve.sh`、`ccnavi-launcher.sh` |
| 打たれた形の検査 | `ccnavi-git.sh` |
| 運用の道具 | `ccnavi-clean.sh`（＋ `ccnavi-clean.js`） |

`ccnavi-push-approved.sh` と `ccnavi-fetch.sh` は、運ぶ対象を決めるためにツリーを自分で数えています。
`ccnavi-push-approved.sh:7` は「実行ファイルも起動しない」と決めていて、理由があります（承認の直後に
端末から打つ sh で、実行ファイルが入れ替え中でも運べるように）。原則はこれとぶつからない形で書く必要があります。

## 何をするか

### 1. 設計（`design`）

`wip/design/sh-role.md` に次を書いて、レビューを受けます。

- **原則 P13 の文面。** 案：
  > **P13 止める・通すの判定は hook と実行ファイルが持つ。** sh が持つのは、外との出入り、実行ファイルへの
  > 入口、打たれた形の検査、運用の道具の 4 つ。sh が ccnavi の状態（承認済みチケット、ツリー）を読んで止めるときは
  > 2 重目の守りとしてで、同じことを hook が先に止める。運ぶ対象を決めるためにツリーを数えるのは判定ではない
- **子の push を hook で止める判定。**
  - 対象：`ccnavi-git.sh … push` をコマンドの位置で呼ぶ形（`_FORBIDDEN_COMMAND` と同じ読み方）
  - 居場所：payload の cwd と、`cd` の追跡（§6.3.2）で決まる行き先
  - 子かどうか：行き先のパスが `.claude/worktrees/<名前>` の下なら、登録の有無を問わずその `<名前>` で探す。
    探す場所は `approval.trees` の全部の `doing/`・`done/` と、`wip/proposals/review/`。
    **閉じたものも、読めないものも数える**（読めないチケットは「子かもしれない」として止める）
  - 止めたときの文面：親のワークツリーで送ること。いまの sh の文面と揃える
  - 置き場：組み込み（`builtin.py` か `phase.py`）。ルールを空にされても消えないように（P4 の「ガード自身を守る」）
- **環境変数の代入を止める判定。**
  - 対象：`.ccnavi/scripts/` の sh をコマンドの位置で呼ぶ形のうち、同じコマンド行で検査の材料を変える変数を
    置くもの。前置きの代入（`X=… sh …`）、`env X=… sh …`、同じ行の先の `export X=…` の 3 つ
  - 止める変数：`CCNAVI_` で始まるもの全部と `CLAUDE_PROJECT_DIR`。出力の量と待ち時間だけの 4 つ
    （`CCNAVI_GIT_MAX_LINES` など）は通す。**通すものを並べる形**にして、あとから増えた変数は止まる側に倒す
  - 読み切れない形（縮退）は、いまと同じく allow を当てない
  - 置き場：組み込み。保護済みの sh の検査を守るもので、自己防衛（§8）の一部として扱う
- **sh の検査の直し方。** symlink の穴だけを直す（`WS` と `push_top` の両方を実際のパスへそろえる）。
  広さは変えない
- **2 か所の食い違いを見るテスト。** hook の判定と sh の検査に、同じ見本（ツリーの形 × チケットの状態）を渡し、
  両方が止めることを確かめる。見本の表を 1 か所に置く

### 2. 受入テスト（`acceptance`）

書いた時点で落ちるものに「落ちる」と付けます。

| 何を言うテストか | いま |
|---|---|
| 親エージェントが子のワークツリーで `ccnavi-git.sh push` を打つと、hook が止める | 落ちる |
| 親のワークツリーに置いた子の承認済みチケットでも止める（#120 の回帰） | 落ちる |
| 閉じた子（`done/`）、レビュー待ちの子（`review/`）のツリーからでも止める | 落ちる |
| 承認済みチケットが YAML として読めなくても、`parent:` の行があれば止める | 落ちる |
| git に登録されていない `.claude/worktrees/<名前>` からでも止める | 落ちる |
| `cd .claude/worktrees/<子> && sh …ccnavi-git.sh push` も止める | 落ちる |
| 親のワークツリーと、チケットの無いワークツリーからの push は止めない | 通る |
| `CCNAVI_TICKETS_APPROVED=/x sh …ccnavi-git.sh push` を止める（`env` 経由、同じ行の `export` も） | 落ちる |
| `CCNAVI_BIN_PATH=… sh …ccnavi-ticket.sh done …` を止める | 落ちる |
| `CCNAVI_GIT_MAX_LINES=200 sh …ccnavi-git.sh log` は止めない | 通る |
| 上の見本を、写す版の sh にも渡して同じく止まる（sh が 2 重目として効いている） | 写す版で確かめる |
| symlink をまたいだワークスペースでも、sh の検査が効く | 落ちる |

写す版の sh は、`tests/e2e/test_e2e_sh.py:21-24` の `CCNAVI_SH_DIR` と同じ切り替えで `wip/design/scripts/` を回します。
`tests/sh/test_gitwrap.py:21` は `.ccnavi/scripts/` に固定なので、そこに切り替えを足します。

### 3. 実装（`implement`）

- 組み込みに 2 つの判定を足す（子の push、環境変数の代入）。理由コードを付録 A に足す
- 見本の表と、hook と sh の両方に当てるテストの道具

### 4. 写す版（`staging`）

`ccnavi-git.sh` は保護済みなので、完成品を `wip/design/scripts/ccnavi-git.sh` に全文で置きます。人が写します。
変えるのは symlink の穴（465-518 行の中で、`WS` と `push_top` のそろえ方）だけです。

### 5. 文書（`docs`）

- ccnavi.md §3 に P13、sh の役の表。§8 に環境変数の代入を止める判定、§9 に子の push の判定
- CLAUDE.md の「実行ファイルの境界」に、sh の役割を 1 行
- ADR を 1 枚（止める判定は hook に置き、sh は 2 重目）
- ADR-0028 の `.claude/scripts/` という古い綴りは、経緯の記録なので直さない

## 範囲の外（別のチケットに分ける）

レビューで挙がった「同じ手順が 2 か所以上にあるもの」のうち、ずれても止まる向きのものと、拡張の側のものは
このチケットでは触りません。

| 何 | どこ |
|---|---|
| gh / glab / curl を探す順 | `ccnavi-review.sh:161-179`、`review.py:1320` |
| 実行ファイルを探す順、os/arch の語 | `ccnavi-common.sh`、`ccnavi-launcher.sh`、`platformtag.py`、`scripts/ccnavi-setup.sh`、`locate.ts` |
| 生成物の消し方 | `ccnavi-clean.js`、`ccnavi-clean.sh` |
| 拡張の「作業中」の定義 | `lock.ts`（ADR-0035 の「`doing` がある間」と定義が違う） |
| 保護ブランチの一覧 | `ccnavi-git.sh:518`、`ccnavi-push-approved.sh:172` |
| origin の URL の読み方 | `ccnavi-review.sh:108-155`、`review.py:1305-1317` |
| ワークスペースルートの決め方 | `ccnavi_workspace`、`cli.default_root` |
| 絶対パスの `CCNAVI_STATE` を壊す | `ccnavi-review.sh:85` |

## 得るもの

- 親エージェントが子のツリーから push する形を、プロセスの環境に左右されない場所（hook）で止められる
- 環境変数を前に置いて検査を外す道が閉じる。いま開いている穴で、この提案が無くても残る
- sh の検査がずれても、hook が先に止める。#120 と同じ形の不具合が出ても、止める側に倒れる
- symlink をまたぐ環境で、sh の検査が黙って外れる穴がなくなる
- 「sh に判定を書いてよいか」を決める文ができる

## 失うもの

- **子の push の検査が 2 か所に残る。** hook と sh が同じことを別の実装で持つ。見本の表を共有するテストで
  ずれを捕まえるが、表に無い形のずれは捕まらない
- **止まるものが増える。** 環境変数を前に置いて保護済みの sh を呼ぶ普通の使い方（テストの再現など）も、
  エージェントからは止まる。人が端末で打つ分には効かない
- **hook の判定が増える。** 組み込みが 2 つ増え、`ccnavi-git.sh push` のたびにツリーとチケットを読む
  （push の回数は少ないので影響は小さいと見ているが、未計測）
- 保護済みの sh を人が写す手間が 1 回かかる
- **「前置きの代入」は読み切れる形だけ止まる。** `sh -c "X=… sh …"` は縮退して人の確認に回る（いまも同じ。
  `--test` で `ask (PARSE_UNCERTAIN)`）。前の Bash 呼び出しで `export` した変数と、スクリプトに書いて
  `sh <ファイル>` で走らせる形は見えない（§12.1 の「敵対的な回避」の範囲）

## やらない場合

- 環境変数を前に置く形で、子の push の検査を外せるまま残る
- 次に承認済みチケットの置き場を変えたとき、sh を直し忘れると検査が黙って外れる。止めるのは sh だけなので、
  外れたことに誰も気づかない
- symlink をまたぐ環境（macOS の `/tmp` の下で作ったワークスペースなど）で、検査が効かないまま残る

## 代案

| 代案 | 得るもの | 失うもの |
|---|---|---|
| 初版：sh から実行ファイルに問う（`ccnavi tree --json`） | 判定が実行ファイル 1 か所になる | 実行ファイルを差し替えれば開く。止める範囲が狭まる。版がずれると全部の push が止まる |
| hook に移して、sh の検査は外す | 子の push の実装が 1 か所になる | hook が効かない環境（人が端末から打つ、hook の登録が壊れた）では何も止めない。緩む向き |
| sh の側で環境変数の上書きを無視する | hook を増やさずに済む | テストや人の設定（`CCNAVI_PROJECTS` を変えた配布先）が使えなくなる。`PATH` の差し替えなどは残る |
| 環境変数の代入をルール（rules.yml）で止める | 組み込みを増やさない | ルールは空にできるので、守りの根拠が守られる対象の中に入る（コアファイルの考え方に反する） |

## 決めてほしいこと

1. **止める変数を「通すものを並べる」形にしてよいか。** 案は `CCNAVI_GIT_MAX_LINES`・`CCNAVI_GIT_FAIL_LINES`・
   `CCNAVI_GIT_KEEP_LOGS`・`CCNAVI_FETCH_TIMEOUT` だけを通し、残りの `CCNAVI_*` と `CLAUDE_PROJECT_DIR` を止める。
   `PATH` や `GIT_*` まで止めるかも決めたい（`ccnavi-git.sh` は `GIT_CONFIG_*` などを自分で消している）
2. **読めないチケットを「子かもしれない」として止めてよいか。** いまの sh と同じ広さにする案。
   誤って止まったときは、チケットを直せば通る
3. **hook の判定を組み込みに置いてよいか。** P4 は組み込みを最小にすると決めている。ガード自身を守るものとして
   数える案だが、判断が要る
