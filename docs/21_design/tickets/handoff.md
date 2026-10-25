---
type: design
title: 9.19 保護対象の変更の取り込み（handoff）
description: エージェントが書けない保護対象の変更を、wip/handoff/ の下書きから ccnavi user handoff で取り込む一本の手順。種類の表（許可リスト）・取り込みの安全・ターミナル要求とボードの経路・ready の止め・検査の共有
tags: [design-doc, ticket, approval, selfguard]
keywords: [handoff, 保護対象, 取り込み, 下書き, user handoff, 許可リスト, 種類の表, expect, conflict, ボード, テンプレート, ready]
---

[設計書の入口](../../20_design.md) > [9. チケット制御](../tickets.md)

### 9.19 保護対象の変更の取り込み（handoff）

エージェントが書けない保護対象（`rules.yml`・hook・ラッパースクリプト・テンプレート・文書など）の変更が、どの手順で元の場所に届くかを書く。
取り込む処理は `tickets/handoff.py`、入口は `ccnavi user handoff`（9.18 の表の操作の 1 つ）。テンプレートの下書きの作り方は [9.15](templates.md)、
ユーザ専用の入口の仕組みは [9.18](user-entry.md)。

#### 手順

保護対象の変更は 1 本の手順で行う。

| 段 | 誰が | 何をする |
|---|---|---|
| コピーする | エージェント | `ccnavi-stage.sh <パス>` が元のファイルを `wip/handoff/<ツリーからの相対パス>`（`.claude` の階層は `handoff-.claude` に読み替える。下の「階層名の読み替え」）にコピーし、コピーした時点の元の中身を `<下書き>.orig` に残す。新規のテンプレートはコピーする元が無いので、`ccnavi --templates --draft` が `wip/handoff/` に作る |
| 編集 | エージェント | `wip/handoff/` の下書きを編集する |
| 見る | ユーザ | `ccnavi user handoff [<相対パス>…]`。何も書かない。対象の一覧・差分・検査の結果・`--expect` のハッシュを出す |
| 取り込む | ユーザ | `ccnavi user handoff <相対パス>… --yes --expect <ハッシュ>`。取り込み先に書く |

`ccnavi user handoff [<相対パス>…] [--yes --expect <ハッシュ>] [--via board] [--json]`。

- `--yes` が無い形は見るだけで、何も書かない。パスを省くと、種類の表に該当する下書き全部が対象になる
- 見るだけの形を含めて、すべてユーザ専用（`user` の入口の中。外では断る）。エージェントは `ccnavi-stage.sh --commands` で状態を見る
- `ccnavi-stage.sh --commands` は、取り込める種類の下書きにこのコマンドの手順を出し、`mv` は `defense` にだけ出す（`mv` は検査・差分・ハッシュ確認を通らないため）。取り込めるかの判定は sh の `is_importable` が `KINDS` と同じ表を持つ（ずれはテストで見つかる）

**ツリー。** `--root` で指した 1 本のツリーだけを見る。ほかのツリーの `wip/handoff/` は読まない。`ccnavi-stage.sh` と `user handoff` は
同じツリーの決め方（`ccnavi_workspace` と同じ規則）を使う。取り込み先は、下書きがあるツリーの、階層名の読み替えを戻した相対パス。
ワークツリーで取り込んだ結果は、そのブランチの変更として残り、ふつうの合流で届く。

#### 階層名の読み替え

Claude Code 本体は `.claude` という名前の階層（深さを問わない）を機密パスとして扱い、非対話（`claude -p`）では編集を許可できない。そのため、下書きの相対パスの中の `.claude` の階層は `handoff-.claude` に読み替えて置き、取り込むときに戻す。

| 元のパス | 下書きのパス（`wip/handoff/` の下） |
|---|---|
| `.claude/settings.json` | `handoff-.claude/settings.json` |
| `.claude/hooks/lint-py.sh` | `handoff-.claude/hooks/lint-py.sh` |
| `projects/<名前>/.claude/settings.json` | `projects/<名前>/handoff-.claude/settings.json` |

- **表は 1 か所。** 読み替える名前（元の階層名 → 下書きでの階層名）は `.ccnavi/scripts/ccnavi-handoff-names.txt` の 1 か所にあり、`ccnavi-stage.sh`（sh）が実行時に読む。実行ファイルは、`build.py` が同梱した同じファイルを読む（`tickets/handoff_names.py`。ソースでは同じパス）。`ccnavi-stage.sh` は実行ファイルが無くても動くため、sh が実行ファイルに聞く形にはしない。名前を足すときは表に 1 行足し、テストの見本に 1 行足す
- **変換の位置。** 元 → 下書きは `ccnavi-stage.sh`（下書きのパスを組む）と `handoff.draft_rel`（テンプレートの下書き）。下書き → 元は `handoff.walk` と、引数のパスを引く `handoff.wanted_key`。`ccnavi-git.sh integrate` は下書きを起点の同じパスと比べるだけなので、変換を持たない
- **比べ方。** 要素ごとに大文字小文字を区別せず比べる（`.CLAUDE` も `Handoff-.Claude` も表に当たる）。表の名前は ASCII だけで、非 ASCII の要素は表に当てない。sh が ASCII の大文字小文字しか畳めないため、Python も同じ答えにする。戻した名前は表の元の階層名の綴りになる。置き場にある実際の綴りは `spelled` が引く
- **予約名。** 下書きでの階層名（`handoff-.claude`）は予約した名前。元のパスにその名前の階層があるファイルは、`ccnavi-stage.sh` が断る（読み替えを逆向きにたどれなくなるため）
- **JSON。** `user handoff --json` の各対象は、元のパス（`path`）と下書きのパス（`draft`）の両方を持つ。ボードは `path`（元のパス）で引く

#### 種類の表（許可リスト）

取り込み先は、種類の表に載せたものだけ。下書きの読み替えを戻した取り込み先のパスは、リンクを解いた実パスの全要素を `pathcase.fold` と NFC に正規化してから表に適用する。
表に該当しないパスと、リンクのディレクトリ（中は読まない）は「対象外」とし、JSON に件数（`ignored`）と、置き場からの相対パスと理由の並び（`ignored_paths: [{path, why}]`。`why` は `not-allowed`（表に無い）か `linked-dir`（リンクのディレクトリ）。`ignored` と件数が一致し、順序は走査順で、各ディレクトリのファイル（名前順）、リンクのディレクトリ、下のディレクトリの順）を出す。形の版は上げない（足すだけ。古い実行ファイルには欄が無い）。テキスト版は件数だけ。error には数えない（`wip/handoff/` には ccnavi が書く `learnings/` や、
再現用のスクリプトも置かれる）。バイナリ（NUL を含む）と、実行ファイル（`.ccnavi/bin/`）は対象外。

| 種類 | パス（ツリーからの相対） | 新規作成 | ボード | 検査 |
|---|---|---|---|---|
| `template` | `.ccnavi/config/common/templates/<名前>.yml`（共通。ワークスペースルートのツリーでだけ書ける）、`.ccnavi/config/project/templates/<名前>.yml`、`projects/<名前>/.ccnavi/config/project/templates/<名前>.yml` | できる | 可 | テンプレートの検査（`plan_templates`）。`description` と `when` の `TODO` の印は warn、制御文字は error、共通と層で同名なのに中身が違えば error、ファイル名と中身の `name:` の照合、名前の形（`NAME_RE`・`NAME_LIMIT`） |
| `messages` | `.ccnavi/messages/<名前>.yml`（ワークスペースルート直下だけ。`projects/<名前>/` の下の `messages/` は読まれないので対象外） | できる | 不可 | 文面ファイルの検査（`policy/texts.py`。欄・差し込みの名前・`次:` の数・長さ・ADR 番号・相対の script 呼び出し。[文面ファイル](../messages.md)）。error のある下書きは取り込めない |
| `layer-config` | `.ccnavi/config/{common,project}/{rules,phases,risks}.yml`、`projects/<名前>/.ccnavi/config/project/{rules,phases,risks}.yml` | できない | 可（`rules.yml` で防御の記述を動かすものは不可。下記） | `ccnavi --lint` が同じファイルに掛ける検査と同じ水準（下の「検査の共有」） |
| `docs` | `.ccnavi/docs/**/*.md`、`.ccnavi/CCNAVI.md` | できない | 可 | UTF-8 であること |
| `defense` | `.ccnavi/scripts/**/*.sh`、`.claude/hooks/**`、`.claude/settings.json`、`.claude/settings.local.json`、`.claude/skills/**/*.md`（スキルの frontmatter は使えるツールや hook を持てる） | できない | 不可（`--via board` は断る。ターミナルの前のユーザだけ） | `.sh` は CRLF を error にして `sh -n`。`settings*.json` は JSON として読めること |

`layer-config` の `rules.yml` のうち、防御の記述を動かす取り込みは、`defense` と同じくターミナルに限る（`--via board` は断り、`--json` の `board` は偽になる）。防御の記述とみなすのは、次の 2 つ。1 つめは `guard-` で始まる id のルールを足す・消す・判定を移す・`except` を含む中身を変える場合で、この id は利用者が防御のルールに付ける印（ccnavi 自身の保護は組み込みの `builtin:guard-*` にあり、共通のルールファイルには無い）。2 つめは今ある deny を消す・中身を変える場合。deny を足すのは止める側への変更なので数えない。今の中身か下書きが読めなければ、確かめられないのでターミナルに限る（`handoff._defense_change`）。共通のルールファイルが読めない間の修復はこの経路になる。エージェントは `wip/handoff/.ccnavi/config/common/rules.yml` に下書きを置き（`builtin:guard-ccnavi-config` が共通のルールファイルへの直接の書き込みを止めるが、置き場のコピーは例外で書ける）、ユーザがターミナルの `ccnavi user handoff` で取り込む。そのため、ボードからの取り込みは断られる。`phases.yml`・`risks.yml` はこの判定の外。

表に足すときは、行を足し、テストで表と docs の食い違いを検出する。

#### 取り込みの安全

| 項目 | 内容 |
|---|---|
| 断る条件（error） | 絶対パス・`..`・リンクを含むパス（途中も）・通常のファイルでない・ハードリンク・全要素のどれかが `.git` で始まる（`.git` ファイル自体、`.gitmodules` などを含む）・ワークスペースの外。同じ取り込み先に落ちる下書きが 2 本あれば（大文字小文字、NFC/NFD の違い）両方を断る |
| `--expect` | 対象の一覧から作った 1 つのハッシュ。各対象について、(相対パスの NFC・fold、種類、下書きのバイト列の SHA-256、取り込み先のバイト列の SHA-256（無ければ `-`）、`.orig` のバイト列の SHA-256（無ければ `-`）) を長さの前置きつきで並べ、パスの並び順で連結して SHA-256 を取る。改行コードは正規化せずバイト列のまま。`--json` の見るだけの形が、対象ごとの `entry_sha` と、選んだ集合の `expect` を返す。拡張はハッシュを自分で組み直さない |
| conflict | `ccnavi-stage.sh` がコピーした `<下書き>.orig` と取り込み先を比べ、コピーしたあとに取り込み先が変わっていれば、その対象は `conflict`。選んだ対象に 1 つでも `conflict` か error があれば、**何も書かない**。`.orig` が無い対象（新規のテンプレートなど）は、この確認をしない。`.orig` の確認は助言で、歯止めは `--expect` が取り込み先の今のハッシュを含むこと |
| 差分の見え方 | バイト列の違いを必ず出す。改行コード（CRLF）・BOM・末尾の改行・制御文字・ゼロ幅の文字は、見える形（`\uXXXX`）に直す。差分が空なのにバイト列が違うときは、そう言って断る |
| 書き込み | 一時ファイル → `fsync` → 差し替え。書く直前に、リンクと取り込み先のハッシュをもう一度確かめる |
| 巻き戻し | 複数の対象を書く途中で失敗したら、書いたものを元に戻し（書く前のバイト列をメモリに持つ）、戻せなかったものを名指しする。複数ファイルの書き込みは、プロセスの途中終了に対して原子的ではない |
| 下書きの片付け | 取り込めたら、下書きの今のバイト列が取り込んだものと同じときだけ、下書きと `.orig` を消す。違えば消さずにそう言う。リンクは消さない |

#### ターミナル要求とボードの経路

| 経路 | 要求 |
|---|---|
| ターミナル | `--yes` で書くときは、標準入力がターミナルであること（9.18）。見るだけの形はターミナルを求めない |
| ボード | `--via board` はターミナルを求めない。`--expect` と `--json` が揃うこと、種類のボード欄が「可」であること、hook の止めに頼る。`defense` の種類と、防御の記述を動かす `rules.yml` は断る |

「作業中のチケットがある間は取り込めない」制限は、実行ファイルには無い。拡張の画面だけが持ち、`template` と `layer-config` の種類の取り込みに掛ける。

**ボードの画面。** 拡張に「取り込み待ち」の画面がある。一覧は `user handoff --json`（見るだけ）で作り、`template`・`layer-config`・`docs` の種類を出す。
取り込みは `user handoff <パス…> --yes --expect <ハッシュ> --via board --json`。`defense`（と、防御の記述を動かす `rules.yml`）は一覧に出し、ターミナルで取り込むことと打つコマンド（`ccnavi --root <ツリー> user handoff <パス> --yes --expect <ハッシュ>`。引用は POSIX の sh の形で、PowerShell では単引用符の中の `'\''` を `''` に打ち直す）を案内する。
テンプレート管理の画面は、一覧・検索・表示のほか、ユーザが図で直したテンプレートを拡張が直接保存する（実行ファイルは読むだけの `--templates --check-file` で保存前に検査する。取り込み済みの本文を書くのはこの保存と、ユーザの手作業だけ）。下書きの取り込みは取り込み待ちの画面。ボードの rules・phases・risks の画面は、拡張が直接書くまま。

**表。** `user_only.py` の表が `handoff` の操作と `--handoff` を持つ。`--version --json` の `features` に `handoff` を持つ。

#### `--draft` と `ready`

`ccnavi --templates --draft <親> [--name <名前>] [--to common|project] [--layer <名前>]` は、下書きを `wip/handoff/` の下の取り込み先と同じ相対パスに書く
（`--to` の既定は `project`。詳細は 9.15）。取り込み先に同名のテンプレートがあれば、上書きになる下書きを作らず断る。同名の下書きがあれば上書きせず断る。

`ready` は、種類の表に該当する未取り込みの下書き（取り込み先と中身が違うもの）が `wip/handoff/` に残っているとき、止める。取り込むか、下書きを消してから打つ。
`ccnavi --lint` は、`wip/handoff/` の `template` の下書きを検査する。

#### 直接作業の `integrate` の検査

`ccnavi-git.sh integrate`（と片付け・`integrate-done`）は、作業ワークツリーの `wip/handoff/` を、`ccnavi tree handoff-pending <ツリー> <起点>` で起点（S）の木の元のパスと突き合わせる。
元のパスは、置き場を除いた相対パスの `.claude` の階層名を `handoff-.claude` と読み替えた分を戻したもので、大文字小文字と NFC/NFD の違いは同じものとして探す。
応答は `foreign <パス>`（元があり中身が違う写し。止める）と `note <パス>`（元が無い下書き。通して「取り込まない下書き」として出す）。
元と中身が同じ写し、S の同じ下書きと同じものは、どちらの一覧にも入れない。元の無い下書きでも、コミットしていないものは止める。
許可リストに当たる未取り込みの下書きは、従来どおり `pending` の件数で止める。起点の木を読めなければ、応答を返さずに失敗する。

#### 検査の共有

`layer-config` の検査は、`ccnavi --lint` が同じファイルに掛ける検査と同じ水準にする。`--lint` が持つ「防御を消す・全部止める記述」の error、
期待する判定（`rule-expectations.yml`）、共通との合成の衝突を含む。`policy`・`tickets` の側に「ファイルのパスを受けて検査する関数」があり、
`--lint` と `user handoff` の両方がそれを呼ぶ。`tickets` から `entry` は読まない。同じ回に取り込む対象は、取り込んだあとの状態で検査する
（`phases.yml` とそれを使うテンプレートを同時に取り込むときなど）。
