---
version: 1
ticket: sh-role
title: sh の役割を決め、sh と拡張が持っている ccnavi の判定の写しを実行ファイルへ寄せる
human_review:
  required: true
  reason: 保護済みの sh（ccnavi-git.sh の push の検査）と、拡張の保存の止め方の出どころを変えるため。配布先すべてに届く
rationale: |
  実行ファイル・sh・拡張の分担のうち、決まっているのは 2 本だけ。
  「実行ファイルはネットワークに出ない」（P11、ADR-0028）と、「拡張は判定を自分で出さない」
  （ADR-0035）。sh が持ってはいけないものは、どこにも書かれていない。

  そのため sh が ccnavi の状態を自分で解いている。`ccnavi-git.sh` の push の検査は、
  承認済みチケットを探すツリーを `approval.trees` と同じ形に書き直して持つ。直前の #120 で直した
  「親のワークツリーに置かれた子を見落とす」不具合は、この写しがずれたことで起きた。
  写しは探す順（実行ファイルの在りか、gh / glab / curl）にもあり、拡張の側にも 1 か所ある。

  sh の役割を決めて、ccnavi の状態を読む判定は実行ファイルだけが持つ形にする。
  sh に残すのは、外との出入りと、打たれた形の検査だけ。
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
  glob: vscode-extension/*
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: CONTEXT.md
- match: Write|Edit
  glob: README.md
started_at: ""
completed_at: ""
base_sha: ""
---

## いま何がそうなっているか

### 決まっている線

| 境界 | 決まり | 守られているか |
|---|---|---|
| 実行ファイル ↔ 外 | P11、ADR-0028。外のものは sh が取ってきて `--result` で写しを渡す | 守られている。`ccnavi/` にネットワークに出るコードは無い |
| 実行ファイル ↔ 拡張 | ADR-0035、P9。拡張は JSON を並べるだけ | ほぼ守られている（下の E） |
| 実行ファイル ↔ sh（sh 側） | **無い** | — |

### sh が持っている 4 つの役

どの役を持つのかを書いた一覧はありません。

| 役 | sh | 中身 |
|---|---|---|
| 外との出入り | `ccnavi-review.sh`、`ccnavi-fetch.sh`、`ccnavi-push-approved.sh` | リモートを読み書きして、写しを渡す |
| 入口 | `ccnavi-ticket.sh`、`ccnavi-approve.sh`、`ccnavi-launcher.sh` | 実行ファイルを探して呼ぶ |
| 打たれた形の検査 | `ccnavi-git.sh` | git の許可リスト、ワークツリーの行き先、**子チケットの push** |
| 運用の道具 | `ccnavi-clean.sh`（＋ `ccnavi-clean.js`） | 生成物を消す |

### 写し（同じ手順が 2 か所以上にあるもの）

| # | 何 | 原本 | 写し | 種類 |
|---|---|---|---|---|
| A | 子チケットのツリーかどうか | `approval.trees` / `approval.home_dir` | `ccnavi-git.sh:469-512` | **状態の解釈** |
| B | ワークツリーの定義 | `tree.py`（`.git` の `gitdir:` が `.git/worktrees/<名前>` を指すもの） | `ccnavi-git.sh:255` と `:481`（`.claude/worktrees/*` の下にあるかどうか） | **状態の解釈**（定義が違う） |
| C | gh / glab / curl を探す順 | `ccnavi-review.sh:161-179` | `review.py:1320` の `transport_problem`（`--lint` が使う） | 探す順 |
| D | 実行ファイルを探す順 | `ccnavi-common.sh` の `ccnavi_bin` | `vscode-extension/.../core/locate.ts` | 探す順 |
| E | 作業中のチケットの定義（保存を止める条件） | 無い（ADR-0035 の文だけ） | `vscode-extension/.../core/lock.ts`（`open` かつ `started_at` あり） | **状態の解釈** |
| F | 生成物の消し方 | `ccnavi-clean.js` | `ccnavi-clean.sh` の `clean_with_sh`（node が無いとき） | 同じ手順を 2 つの言語で |

写しの中で危ないのは **状態の解釈**（A・B・E）です。承認済みチケットの置き場やツリーの定義が
変わると、写しの側は黙って古いまま動きます。#120 はこの形で起きました。
探す順（C・D）と F は、ずれても「見つからない」「消し残す」で止まります。

## 何をするか

### 決めること（ccnavi.md §3 に 1 行、§4 か §9 に表を 1 つ）

> **P13 sh は ccnavi の状態を解かない。** sh がするのは、外との出入り、実行ファイルへの入口、
> 打たれた形（コマンド行・パスの綴り）の検査、運用の道具の 4 つ。承認済みチケット・マーカー・
> ツリーの定義を読んで決めることは実行ファイルに問い、答えを JSON か終了コードで受け取る。

- 「打たれた形の検査」は残します。git の許可リストや、行き先が `.claude/worktrees/` の下 1 段かどうかは、
  文字列だけで決まり、ccnavi の状態を読みません。拡張の「入力の形の検査」（`core/projects.ts`）と同じ扱いです
- 探す順（C・D）は写しを消せません（D は、Windows の拡張から sh を起こせないため）。
  代わりに順を ccnavi.md の表 1 つに書き、両方のテストが同じ見本を読む形にします

### 1. 設計（`design`）

`wip/design/sh-role.md` に次を書いて、レビューを受けます。

- P13 の文面と、sh ごとに 4 つのうちどの役かの表
- A・B を問う副命令の形。案は `ccnavi tree --cwd <パス> --json` で、そのツリーの種類（main / project / worktree）・
  名前・承認済みチケットの識別子・`parent:` を返すもの。push の検査は `parent:` があるかだけを見る
- 実行ファイルが見つからないときの `ccnavi-git.sh push` の振る舞い（下の「決めてほしいこと」）
- E のために `--explain --json` のチケットに `in_progress`（作業中かどうか）を足す形

### 2. 受入テスト（`acceptance`）

| 何を言うテストか |
|---|
| 親のワークツリーに子の承認済みチケットがあるとき、子のワークツリーからの push は止まる（#120 の回帰。いまも通るはず） |
| `.claude/worktrees/x` にあるが git のワークツリーではないディレクトリは、sh と実行ファイルが同じ答えを出す（B。いまは食い違う） |
| `ccnavi tree --json` の出力が、`approval.trees` と `tree.py` の答えと一致する |
| `--explain --json` の `in_progress` が、拡張の保存を止める条件と一致する |
| gh / glab / curl の探す順の見本を、`ccnavi-review.sh` と `transport_problem` が同じ答えで通る（C） |
| 実行ファイルの探す順の見本を、`ccnavi_bin` と `locate.ts` が同じ答えで通る（D） |
| node の道と `clean_with_sh` の道が、同じツリーで同じものを消し、同じ文面を出す（F。今あるテストが両方の道を通るかをまず確かめる） |

### 3. 実装（`implement`）

- `ccnavi/` に `tree --json` の副命令を足す。中身は `approval.trees` / `approval.home_dir` / `tree.py` を呼ぶだけ
- `--explain --json` に `in_progress` を足し、`lock.ts` はその値だけを見る
- C・D は見本の表をテストに置いて、両方をそれで回す

### 4. 写す版（`staging`）

`ccnavi-git.sh` は保護済みなので、完成品を `wip/design/scripts/ccnavi-git.sh` に全文で置きます。人が写します。

- push の検査（469-512 行）を、`ccnavi tree --cwd "$push_top" --json` の `parent` を見る数行に置き換える
- 行き先の検査（213-288 行）は形の検査なので残す

### 5. 文書（`docs`）

- ccnavi.md §3 に P13、sh の役の表
- CONTEXT.md に「打たれた形の検査」の語を足すか判断する
- ADR を 1 枚足す（sh は ccnavi の状態を解かない）。ADR-0028 の `.claude/scripts/` という古い綴りは
  経緯の記録なので直さない

## 得るもの

- 状態の解釈が実行ファイルの 1 か所になり、#120 と同じ形の不具合が出る道がなくなる
- B の食い違い（sh は「`.claude/worktrees/` の下にあるもの」、実行ファイルは「git のワークツリーとして登録されたもの」）が
  消え、ガードが「効いているつもりで効いていない」形が 1 つ減る
- 「このロジックは sh に書いてよいか」を決める文ができ、次に sh を直す人が迷わない
- 探す順の写しは残るが、テストがずれを捕まえる

## 失うもの

- **`ccnavi-git.sh push` のたびに実行ファイルが起きる。** バンドル版の起動の分だけ遅くなる（push は回数が少ないので
  体感は小さいと見ているが、未計測）
- **`ccnavi-git.sh` が実行ファイルに依存する。** いまは sh だけで子の push を止められる。実行ファイルが無い・壊れている
  環境での落ち方を新たに決める必要がある
- sh と実行ファイルの契約（`tree --json` の形）が 1 つ増え、変えるときは両方を直す
- 保護済みの sh を人が写す手間が 1 回かかる
- C・D の写しは消えない。テストを 2 か所に持つぶん、維持の手間はむしろ増える

## やらない場合

- 承認済みチケットの置き場やツリーの定義を次に変えるとき、`ccnavi-git.sh` を直し忘れると、子の push の検査が黙って外れる。
  #120 と同じ形の不具合がまた出る
- B の食い違いは残る。`.claude/worktrees/` の下に git のワークツリーでないディレクトリがあると、sh と実行ファイルが違う答えを出す
- sh に何を書いてよいかの線が無いまま、sh が増える

## 代案

| 代案 | 得るもの | 失うもの |
|---|---|---|
| 写しは残し、共通の見本で両方をテストするだけ（A・B・E も C・D と同じ扱い） | 実行ファイルへの依存が増えない。sh を写す手間が無い | 状態の解釈が 2 か所に残る。見本に無い形のずれは捕まらない |
| push の子の検査を sh から外し、hook（実行前の判定）だけで止める | sh が単純になる | hook は `ccnavi-git.sh push` の中身を見ないので、止める場所が無くなる。緩む向きの変更 |
| P13 だけ書き、コードは次に触るときに寄せる | 今の手間が小さい | 次に触るまで写しが残る。#120 の形の不具合は防げない |

## 決めてほしいこと

1. **実行ファイルが見つからないときの `ccnavi-git.sh push`。** 案は「止めて、実行ファイルを置くよう案内する」（P1 の拒否側）。
   逆に「今の sh だけの検査に落とす」なら写しが残り、この提案の意味が半分になる
2. **E を入れるか。** 拡張の保存の止め方は ADR-0035 の決まりを拡張側で読んでいるだけで、
   ずれの実害はまだ無い。範囲を小さくするなら外せる
3. **C・D・F を範囲に入れるか。** ずれても止まる向きなので、別のチケットに分けてもよい
