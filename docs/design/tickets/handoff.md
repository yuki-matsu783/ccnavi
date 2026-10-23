---
type: design
title: 9.19 保護対象の変更の取り込み（handoff）
description: エージェントが書けない保護対象の変更を、wip/handoff/ の下書きから ccnavi user handoff で取り込む一本の手順。種類の表（許可リスト）・取り込みの安全・端末要求とボードの経路・ready の止め・検査の共有
tags: [design-doc, ticket, approval, selfguard]
keywords: [handoff, 保護対象, 取り込み, 下書き, user handoff, 許可リスト, 種類の表, expect, conflict, ボード, テンプレート, ready]
---

[設計書の入口](../../design.md) > [9. チケット制御](../tickets.md)

### 9.19 保護対象の変更の取り込み（handoff）

エージェントが書けない保護対象（`rules.yml`・hook・ラッパースクリプト・テンプレート・文書など）の変更が、どの手順で元の場所に届くかを書く。
取り込む処理は `tickets/handoff.py`、入口は `ccnavi user handoff`（9.18 の表の操作の 1 つ）。テンプレートの下書きの作り方は [9.15](templates.md)、
ユーザ専用の入口の仕組みは [9.18](user-entry.md)。

#### 手順

保護対象の変更は 1 本の手順で行う。

| 段 | 誰が | 何をする |
|---|---|---|
| 写す | エージェント | `ccnavi-stage.sh <パス>` が元のファイルを `wip/handoff/<ツリーからの相対パス>` に写し、写した時点の元の中身を `<下書き>.orig` に残す。新規のテンプレートは写す元が無いので、`ccnavi --templates --draft` が `wip/handoff/` に作る |
| 編集 | エージェント | `wip/handoff/` の下書きを編集する |
| 見る | ユーザ | `ccnavi user handoff [<相対パス>…]`。何も書かない。対象の一覧・差分・検査の結果・`--expect` のハッシュを出す |
| 取り込む | ユーザ | `ccnavi user handoff <相対パス>… --yes --expect <ハッシュ>`。取り込み先に書く |

`ccnavi user handoff [<相対パス>…] [--yes --expect <ハッシュ>] [--via board] [--json]`。

- `--yes` が無い形は見るだけで、何も書かない。パスを省くと、種類の表に当たる下書き全部が対象になる
- 見るだけの形を含めて、すべてユーザ専用（`user` の入口の中。外では断る）。エージェントは `ccnavi-stage.sh --commands` で状態を見る
- 端末の前のユーザは、`ccnavi-stage.sh --commands` が出す `mv` の代わりにこのコマンドを使える

**ツリー。** `--root` で指した 1 本のツリーだけを見る。ほかのツリーの `wip/handoff/` は読まない。`ccnavi-stage.sh` と `user handoff` は
同じツリーの決め方（`ccnavi_workspace` と同じ規則）を使う。取り込み先は、下書きがあるツリーの同じ相対パス。
作業ツリーで取り込んだ結果は、そのブランチの変更として残り、ふつうの合流で届く。

#### 種類の表（許可リスト）

取り込み先は、種類の表に載せたものだけ。下書きのパスは、リンクを解いた実パスの全要素を `pathcase.fold` と NFC に正規化してから表に当てる。
表に当たらないパスは「対象外」とし、一覧に件数だけ出す。error には数えない（`wip/handoff/` には ccnavi が書く `learnings/` や、
再現用のスクリプトも置かれる）。バイナリ（NUL を含む）と、実行ファイル（`.ccnavi/bin/`）は対象外。

| 種類 | パス（ツリーからの相対） | 新規作成 | ボード | 検査 |
|---|---|---|---|---|
| `template` | `.ccnavi/config/common/templates/<名前>.yml`（共通。ワークスペースルートのツリーでだけ書ける）、`.ccnavi/config/project/templates/<名前>.yml`、`projects/<名前>/.ccnavi/config/project/templates/<名前>.yml` | できる | 可 | テンプレートの検査（`plan_templates`）。`description` と `when` の `TODO` の印は warn、制御文字は error、共通と層で同名なのに中身が違えば error、ファイル名と中身の `name:` の照合、名前の形（`NAME_RE`・`NAME_LIMIT`） |
| `layer-config` | `.ccnavi/config/{common,project}/{rules,phases,risks}.yml`、`projects/<名前>/.ccnavi/config/project/{rules,phases,risks}.yml` | できない | 可（`rules.yml` で防御の記述を動かすものは不可。下記） | `ccnavi --lint` が同じファイルに掛ける検査と同じ水準（下の「検査の共有」） |
| `docs` | `.ccnavi/docs/**/*.md`、`.ccnavi/CCNAVI.md` | できない | 可 | UTF-8 であること |
| `defense` | `.ccnavi/scripts/**/*.sh`、`.claude/hooks/**`、`.claude/settings.json`、`.claude/settings.local.json`、`.claude/skills/**/*.md`（スキルの frontmatter は使えるツールや hook を持てる） | できない | 不可（`--via board` は断る。端末の前のユーザだけ） | `.sh` は CRLF を error にして `sh -n`。`settings*.json` は JSON として読めること |

`layer-config` の `rules.yml` のうち、防御の記述を動かす取り込みは、`defense` と同じく端末に限る（`--via board` は断り、`--json` の `board` は偽になる）。防御の記述とみなすのは、次の 2 つ。1 つめは `guard-` で始まる id のルールを足す・消す・判定を移す・`except` を含む中身を変える場合で、この id は利用者が防御のルールに付ける印（ccnavi 自身の保護は組み込みの `builtin:guard-*` にあり、共通のルールファイルには無い）。2 つめは今ある deny を消す・中身を変える場合。deny を足すのは止める側への変更なので数えない。今の中身か下書きが読めなければ、確かめられないので端末に限る（`handoff._defense_change`）。共通のルールファイルが読めない間の修復はこの経路になる。エージェントは `wip/handoff/.ccnavi/config/common/rules.yml` に下書きを置き（`builtin:guard-ccnavi-config` が共通のルールファイルへの直接の書き込みを止めるが、置き場の写しは例外で書ける）、ユーザが端末の `ccnavi user handoff` で取り込む。そのため、ボードからの取り込みは断られる。`phases.yml`・`risks.yml` はこの判定の外。

表に足すときは、行を足し、テストで表と docs の食い違いを検出する。

#### 取り込みの安全

| 項目 | 内容 |
|---|---|
| 断る条件（error） | 絶対パス・`..`・リンクを含むパス（途中も）・通常のファイルでない・ハードリンク・全要素のどれかが `.git` で始まる（`.git` ファイル自体、`.gitmodules` などを含む）・ワークスペースの外。同じ取り込み先に落ちる下書きが 2 本あれば（大文字小文字、NFC/NFD の違い）両方を断る |
| `--expect` | 対象の一覧から作った 1 つのハッシュ。各対象について、(相対パスの NFC・fold、種類、下書きのバイト列の SHA-256、取り込み先のバイト列の SHA-256（無ければ `-`）、`.orig` のバイト列の SHA-256（無ければ `-`）) を長さの前置きつきで並べ、パスの並び順で連結して SHA-256 を取る。改行コードは正規化せずバイト列のまま。`--json` の見るだけの形が、対象ごとの `entry_sha` と、選んだ集合の `expect` を返す。拡張はハッシュを自分で組み直さない |
| conflict | `ccnavi-stage.sh` が写した `<下書き>.orig` と取り込み先を比べ、写したあとに取り込み先が変わっていれば、その対象は `conflict`。選んだ対象に 1 つでも `conflict` か error があれば、**何も書かない**。`.orig` が無い対象（新規のテンプレートなど）は、この確認をしない。`.orig` の確認は助言で、歯止めは `--expect` が取り込み先の今のハッシュを含むこと |
| 差分の見え方 | バイト列の違いを必ず出す。改行コード（CRLF）・BOM・末尾の改行・制御文字・ゼロ幅の文字は、見える形（`\uXXXX`）に直す。差分が空なのにバイト列が違うときは、そう言って断る |
| 書き込み | 一時ファイル → `fsync` → 差し替え。書く直前に、リンクと取り込み先のハッシュをもう一度確かめる |
| 巻き戻し | 複数の対象を書く途中で失敗したら、書いたものを元に戻し（書く前のバイト列をメモリに持つ）、戻せなかったものを名指しする。複数ファイルの書き込みは、プロセスの途中終了に対して原子的ではない |
| 下書きの片付け | 取り込めたら、下書きの今のバイト列が取り込んだものと同じときだけ、下書きと `.orig` を消す。違えば消さずにそう言う。リンクは消さない |

#### 端末要求とボードの経路

| 経路 | 要求 |
|---|---|
| 端末 | `--yes` で書くときは、標準入力が端末であること（9.18）。見るだけの形は端末を求めない |
| ボード | `--via board` は端末を求めない。`--expect` と `--json` が揃うこと、種類のボード欄が「可」であること、hook の止めに頼る。`defense` の種類と、防御の記述を動かす `rules.yml` は断る |

「作業中のチケットがある間は取り込めない」制限は、実行ファイルには無い。拡張の画面だけが持ち、`template` と `layer-config` の種類の取り込みに掛ける。

**ボードの画面。** 拡張に「取り込み待ち」の画面がある。一覧は `user handoff --json`（見るだけ）で作り、`template`・`layer-config`・`docs` の種類を出す。
取り込みは `user handoff <パス…> --yes --expect <ハッシュ> --via board --json`。`defense`（と、防御の記述を動かす `rules.yml`）は一覧に出し、端末で取り込むことと打つコマンド（`ccnavi --root <ツリー> user handoff <パス> --yes --expect <ハッシュ>`。引用は POSIX の sh の形で、PowerShell では単引用符の中の `'\''` を `''` に打ち直す）を案内する。
テンプレート管理の画面は、一覧・検索・表示のほか、ユーザが図で直したテンプレートを拡張が直接保存する（実行ファイルは読むだけの `--templates --check-file` で保存前に検査する。取り込み済みの本文を書くのはこの保存と、ユーザの手作業だけ）。下書きの取り込みは取り込み待ちの画面。ボードの rules・phases・risks の画面は、拡張が直接書くまま。

**表と互換。** `user_only.py` の表が `handoff` の操作と `--handoff` を持つ。テンプレート専用の書き込みのオプション（`--import` など）は、実行ファイルがもう受け付けない。
表ではそれらを `legacy` の行とし、古い実行ファイルが手元に残るときのために hook は止め続ける。互換の版（`compat`）は、この変更では上げていない（いまの値は `--version` で見る）。`--version --json` の `features` に `handoff` を持つ。

#### `--draft` と `ready`

`ccnavi --templates --draft <親> [--name <名前>] [--to common|project] [--layer <名前>]` は、下書きを `wip/handoff/` の下の取り込み先と同じ相対パスに書く
（`--to` の既定は `project`。詳細は 9.15）。取り込み先に同名のテンプレートがあれば、上書きになる下書きを作らず断る。同名の下書きがあれば上書きせず断る。

`ready` は、種類の表に当たる未取り込みの下書き（取り込み先と中身が違うもの）が `wip/handoff/` に残っているとき、止める。取り込むか、下書きを消してから打つ。
`ccnavi --lint` は、`wip/handoff/` の `template` の下書きを検査する。

#### 検査の共有

`layer-config` の検査は、`ccnavi --lint` が同じファイルに掛ける検査と同じ水準にする。`--lint` が持つ「防御を消す・全部止める記述」の error、
期待する判定（`rule-expectations.yml`）、共通との合成の衝突を含む。`policy`・`tickets` の側に「ファイルのパスを受けて検査する関数」があり、
`--lint` と `user handoff` の両方がそれを呼ぶ。`tickets` から `entry` は読まない。同じ回に取り込む対象は、取り込んだあとの状態で検査する
（`phases.yml` とそれを使うテンプレートを同時に取り込むときなど）。
