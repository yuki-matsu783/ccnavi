---
title: CCNAVI.md の書き方
type: guide
description: .ccnavi/CCNAVI.md の条件（ccnavi:if / else / endif）と差し込み（{{名前}}）の書き方、条件の名前の一覧、判定の仕組み
tags: [ccnavi-md, session-start]
keywords: [CCNAVI.md, ccnavi:if, ccnavi:else, ccnavi:endif, 条件, 差し込み, 入れ子, 判定, ccnavi-md, lint, SessionStart]
---

# CCNAVI.md の書き方

`.ccnavi/CCNAVI.md` は、セッションの開始（起動・再開・compact・clear）のたびに ccnavi がモデルへ渡す作業の決まり。
文面は md が持ち、ccnavi は条件と差し込みを解いて1つの文にするだけで、何を言うかは持たない。

## 書き方

書き方は3つだけ。

| 書き方 | 意味 |
|---|---|
| `<!-- ccnavi:if 名前 -->` … `<!-- ccnavi:endif -->` | 名前が成り立つときだけ、囲んだ行を載せる（入れ子にできる） |
| `<!-- ccnavi:else -->` | `if`と`endif`の間に1つだけ置ける。名前が成り立たないときだけ、`else`から`endif`までの行を載せる（成り立つときは`if`から`else`までの行） |
| `{{名前}}` | ワークスペースごとの値に置き換える |

- `ccnavi:`の囲みは、1行に1つ、単独で書く
- 囲みの中の行は、深さ1段につき半角空白2つでインデントしてよい。載せるときに、深さの分だけ行頭の空白を取る（それより深い空白は残る）
- 入れ子は、外側も内側も成り立つときだけ載る。外側が成り立たなければ、内側の`else`側も載らない
- 論理式（and・or・not）は無い。「AかつB」は入れ子で書ける。それ以外は、判定の関数の中で組んで1つの名前にする

## 条件の名前

| 条件の名前 | 成り立つとき |
|---|---|
| `workspace_root` | ワークスペースのルール（共通レイヤーか自身のレイヤー）の`deny`にid `workspace-root`がある |
| `git_wrapper` | ワークスペースに git の入口（`.ccnavi/scripts/ccnavi-git.sh`）がある。生の git は組み込みでいつも止まるので、git の行は、これが偽のときは入口を名指しせず「ユーザに頼む」形になる |
| `projects` | プロジェクトの置き場に`.git`を持つプロジェクトがある |
| `tickets` | チケット制御を使っている（`CCNAVI_TICKET_CONTROL`が`disable`でない） |
| `docs` | mdの索引の案内（`--docs`）を出す回 |
| `dry_run` | `CCNAVI_MODE`が`dry-run` |

ワークツリーとgitの行は、それを支えるdenyがあるときだけ出す。ルールが無いワークスペースで出すと、文と判定が食い違う。
共通レイヤーが読めずに組み込みの既定に戻ったときも出ない（既定にはこの2つのidが無い）。

## 条件を足す（配布先）

組み込みに無い名前は、ワークスペースの`.ccnavi/scripts/ccnavimd.sh`に書いた関数`ccnavi_cond_<名前>`が判定する。実行ファイルを触らずに条件を足せる。
1つのshに、条件の数だけ関数を書く。

```sh
# .ccnavi/scripts/ccnavimd.sh
ccnavi_cond_has_docker() {
	command -v docker >/dev/null 2>&1
}

ccnavi_cond_in_ci() {
	[ -n "${CI:-}" ]
}
```

mdでは`<!-- ccnavi:if has_docker -->`と書く。

- 戻り値0が真、1が偽。それ以外の戻り値、走らせられない、2秒で終わらないときは、判定できないので真にする
- 関数が無い名前や、`ccnavimd.sh`が無いときも真にする。どちらも、囲んだ行をそのまま出す。黙って落とすと、載るはずの決まりが誰にも気づかれずに届かなくなるため
- 関数は`名前() { … }`の形で書く（`function`は使わない。shはdashのこともある）。名前は英数字・`-`・`_`。組み込みと同じ名前の関数は使わない
- mdで使っている名前の関数だけを走らせる。cwdはワークスペースルート
- 状況は環境変数で受け取る: `CCNAVI_ROOT`（ワークスペースルート）、`CCNAVI_DOCS`（索引の案内を出す回なら1）、`CCNAVI_DENY_IDS`（ルールのdenyのidを空白区切り）、`CCNAVI_MODE`
- shの置き場は他のshと同じ`.ccnavi/scripts/`なので、エージェントのWrite / Editは止まる（ルール`guard-scripts`）。セッションの頭に載る文面を、エージェントが条件の側から書き換えられない。足す・直すのはユーザ

## 差し込みの名前

| 差し込みの名前 | 値 |
|---|---|
| `worktrees` | ワークツリーの置き場（`.claude/worktrees`） |
| `git_sh` / `ticket_sh` / `stage_sh` | `ccnavi-git.sh` / `ccnavi-ticket.sh` / `ccnavi-stage.sh`を打てる形にしたコマンド |
| `worktree_sh` | `ccnavi-worktree.sh`を打てる形にしたコマンド（直接作業のワークツリーを切る） |
| `handoff` | ユーザに渡す物の置き場（`wip/handoff`） |
| `scratch` | 使い捨ての置き場（ワークスペースルート直下の`scratchpad`の絶対パス。`.gitignore`で追跡から外れる。ワークツリーの中から相対で書くとワークツリー直下に作ってしまうため、絶対パスで差し込む） |
| `projects` | プロジェクトの置き場（ワークスペースルートからの相対） |
| `tickets_dir` / `approved_dir` | 提案の置き場 / 承認済みチケットの置き場 |
| `mode_env` | `CCNAVI_MODE` |

## 判定の仕組み

条件の判定は、名前ごとに独立した関数が持つ。

- 関数は、そのセッションの状況（設定・ワークスペースルート・`--docs`を出す回か・ルールのdenyのid・動作モード）を受け取り、真偽を返す。1つの関数は1つの名前だけを判定する
- 読み込みのたびに、全部の関数を呼んで、真になった名前の集合を作る。判定はmdを読む前に済む
- mdを上から読み、`ccnavi:if`で囲まれた行は、囲みの名前がすべて集合に入っているときだけ載る
- 載せる行が決まったら、`{{名前}}`を値に置き換え、続く空行をまとめて1つの文にする
- 組み込みにも`ccnavimd.sh`の関数にも無い名前は、判定できないので真にして囲んだ行を出す。`ccnavi --lint`が警告する。差し込みの一覧に無い`{{名前}}`は、そのまま残る（書き間違いが見える形で届く）

## 壊れたときと lint

mdが無い・読めないときは、無いことだけを1行で言う（黙ると、決まりが渡っていないことに誰も気づけない）。
本文は6000文字で切り、切ったことを末尾に言う。

`ccnavi --lint`は、次を`(ccnavi-md)`のwarnで言う。行番号が付くのは、`endif`の無い`if`を除く。

- mdが無い
- 条件の名前が、英数字と`_`だけになっていない（`has-docker`など）。これはerrorで言う。ほかはwarn
- 組み込みにも`ccnavimd.sh`の関数にも無い条件の名前と、知らない差し込みの名前
- 対の取れていない囲み（`endif`の無い`if`、`if`の無い`else`と`endif`）
- 1つの`if`に2つ目の`else`
- `ccnavi:`の囲みが1行に2つある、または他の文字と同じ行にある（1行に1つ、単独で書く）
