---
type: design
title: 8. 自己防衛（コアファイル）
description: ルールの外に組み込みで持つコアファイルの保護。止める側と戻す側
tags: [design-doc, selfguard]
keywords: [自己防衛, コアファイル, 保護, バックアップ, 復元]
---

[設計書の入口に戻る](../design.md)

## 8. 自己防衛（コアファイル）

### 8.1 守るもの

ルールから導く保護領域ではルールファイル自身を守れないので、次のものはルールの外に組み込みで持つ
（`CCNAVI_GUARD_CORE_FILES`、REQ-SLF）。

| 対象 | 何が懸かっているか | バックアップを取る時点 |
|---|---|---|
| `.claude/settings.json` / `.claude/settings.local.json` | hook の登録そのもの | ツール実行前と、ユーザの発言時 |
| 共通レイヤーのファイル（`.ccnavi/config/common/{rules,phases,risks}.yml`） | 判定の中身そのもの | ツール実行前と、ユーザの発言時 |
| 自身のレイヤーと各プロジェクトのレイヤーの `.ccnavi/config/project/{rules,phases,risks}.yml` | 同上 | ツール実行前と、ユーザの発言時 |
| `CCNAVI_BIN_PATH` が指すファイル（振り分けの sh）と、sh がこの機械で起動する実行ファイル | 判定器の実体 | セッション開始 |

作業・レビュー計画のテンプレート（`.ccnavi/config/{common,project}/templates/`）は、この表に足さない。`*/.ccnavi/*` の一般の保護で止める。

名指しのツール（Write / Edit）の保護には、ツリー（ワークスペースルート、`.claude/worktrees/<名前>`、`projects/<名前>`）の直下の `wip/handoff/` の下を除く例外がある。守る場所の写し（`wip/handoff/.ccnavi/scripts/x.sh` など）は、ユーザが `ccnavi user handoff` で取り込むまで効力が無い。例外が効くのは、`..` の段が無く、リンクを解いた実パスが綴りと同じで、既にあるファイルがハードリンクでつながっていないとき。`docs/wip/handoff/` や `wip/handoffX/` のような綴りは除かない。組み込みの保護（`builtin:guard-project-home` と、名前で書くツールの 4 本 `builtin:guard-hooks`・`builtin:guard-settings`・`builtin:guard-scripts`・`builtin:guard-ccnavi-config`。どれも `selfguard_shell.handoff_write_exempt`）が同じ範囲を除く。この関数は、ハードリンクでつながった写しを除かない。利用者がリポジトリのルールに `except` で同じ範囲を除く場合、`except` は式なのでファイルを見ず、ハードリンクの写しも除いてしまう。そこで、置き場の中のリンクを通る対象・ハードリンクでつながった対象・`..` を含む対象への書き込みは、組み込みの `builtin:guard-handoff-links`（`selfguard_shell.handoff_link_target`）が別に止める。置き場の中にリンクを作るシェルの形も `builtin:guard-handoff-ln` が止める（8.2）。シェルからの書き込みの保護は、ほかは変えない。

上のうち追跡されているものは、その元リポジトリから切ったワークツリー側の設定も対象に入る（11.6）。
hook スクリプトと保護済みスクリプトは、この表の対象（バックアップと復元）には無い。名前で書くツールからの書き込みは、組み込みの 4 本（8.2）が実行前に止め、実行後の保護範囲（`events.watched_for`）が未コミットの変更を検知する。ccnavi ディレクトリの下のスクリプトも同じで、`builtin:guard-project-home` も止める。

### 8.2 止める側

`CCNAVI_GUARD_CORE_FILES` が有効な間、`deny` の先頭に最大 7 本を挿す（`selfguard_shell.add_rules`）。そのあとに、名前で書くツールの 4 本（下）を挿す（`selfguard_shell.add_named_rules`）。これとは別に、チケット制御が有効な間は、承認済みの置き場（`.ccnavi/approved/` の下すべて）と操作記録の置き場（`logs/state/ops/`）への書き込みを止める組み込みの関門（`ticket_guard.gate_rules`）を足す。関門は `CCNAVI_GUARD_CORE_FILES` の値にも `dry-run` にも依らず止める。承認済みの置き場は、チケット制御の保護として扱う。チケット制御が有効な間は自己防衛の値に依らず、実行前は拒否し（`builtin:ticket-approved`。文面は承認の経路・`status`・`finish`・`propose` を案内する）、実行後は未コミットの変更を検知する。実行後の保護範囲には、同じ式と文面の 1 本（`ticket_guard.approved_write_rule`。シェル版は要らない）を `events.watched_for` が足す。復元は `CCNAVI_RESTORE_IF_DENY` の実効値が `enable` のときだけ。ccnavi のスクリプトによる正規の書き込みは、コマンド名ではなく変更の中身の形で外れる（`post_findings._script_writes`、ADR-0075）。コミット済みの変更の `Stop` での報告は対象外で（`post._committed_findings` がチケットの置き場のコミット済みの変更を一律に外す）、MR のレビューで確かめる。チケット制御を `disable` にすると、実行後の保護は外れる（実行前は、自己防衛が有効なら `builtin:guard-project-home` が止める）。

| id | 足すとき | 止めるもの |
|---|---|---|
| `builtin:guard-setting-files` | 常に | Bash で、書き込みの形（`>` 系のリダイレクト、`mv` `rm` `tee` `dd` `truncate` `patch` `shred`、`sed -i`、`cp` `ln` `install` の行き先、`cp` `ln` `install` `mv` で守る名前を入っているディレクトリへ置く形）と場所（`.claude/hooks/`、`.claude/settings*.json`、`ccnavi-git.sh`、実行ファイル、レイヤーの設定 3 本と ccnavi ディレクトリ、共通レイヤーの 3 本（`.ccnavi/config/common/`）、記録と state の `logs/decisions.jsonl` `logs/state`、閉じたチケットの退避の `logs/archive`）の組 |
| `builtin:guard-binary` | `CCNAVI_BIN_PATH` が設定されているとき | `Write` `Edit` `NotebookEdit` |
| `builtin:guard-project-home` | 常に（ccnavi ディレクトリは固定の `.ccnavi`） | 同上 |
| `builtin:guard-common-layer` | 共通レイヤーの 3 本の置き場が決まっているとき（11.6） | 同上 |
| `builtin:guard-records` | 常に | `Write` `Edit` `NotebookEdit` で、記録と state の置き場（`logs/decisions*.jsonl`、`logs/state/`、閉じたチケットの退避 `logs/archive/`。診断のフラグ `--log` / `--state` で動かしたときは、その置き場とローテートした分にも当てる）。シェルの側の `builtin:guard-setting-files` と同じ場所 |
| `builtin:guard-handoff-links` | ワークスペースルートが決まっているとき | `Write` `Edit` `NotebookEdit` で、ツリーの直下の `wip/handoff/` の下の対象のうち、リンクを通るもの・ハードリンクでほかの名前とつながったもの・`..` を含むもの。つながっていない写しと置き場の外には当たらない |
| `builtin:guard-handoff-ln` | ワークスペースルートが決まっているとき | Bash で、リンクを作るコマンド（`ln`・`link`・`cp -l` / `-s` / `--link` / `--symbolic-link`）があり、コマンド行に `handoff` の綴りがあるか、作業ディレクトリが置き場の中の形。行き先は読み分けない（止める側に倒す） |

名前で書くツールの 4 本（`selfguard_shell.add_named_rules`。`Write` `Edit` `NotebookEdit`）。`CCNAVI_GUARD_CORE_FILES` の実効値が `disable` でなければ足す。

| id | 当てるもの | 書き込みを許す例外 |
|---|---|---|
| `builtin:guard-hooks` | glob `*/.claude/hooks/*` | `handoff_write_exempt`（直下の `wip/handoff/` の普通の写し） |
| `builtin:guard-settings` | regex `[\\/]\.claude[\\/]settings(\.local)?\.json$` | 同上 |
| `builtin:guard-scripts` | glob `*/<ccnavi ディレクトリ>/scripts/*` | 同上 |
| `builtin:guard-ccnavi-config` | regex `[\\/]<ccnavi ディレクトリ>[\\/]config[\\/]common[\\/](rules\|phases\|risks)\.yml$` | 同上 |

- 共通のルールファイルには書かない。ルールファイルの `deny` に置くと、書き換えられた時点で保護も一緒に消える
- ccnavi ディレクトリの名前が空なら、それを使う 2 本（scripts と共通の設定）は足さない
- `builtin:guard-ccnavi-config` はルートを固定せず、パスの末尾で当てる。`builtin:guard-common-layer` と同じ範囲だとは仮定しない
- 足す場所は 3 つで、同じ関数から足す。実行前の通常時は `add_rules` の後に呼ぶ（`deny` の先頭に挿すので、専用の文面が `builtin:guard-project-home` / `builtin:guard-common-layer` より先に並ぶ。`add_rules` の中身は変えず、二重に足さない）。実行前で、共通のルールファイルが読めず組み込みの既定で動いている間は、`add_rules` は呼ばずこの関数だけ呼ぶ。実行後は `events.watched_for` が、各ツリーのルールの組にこの関数だけを足す（読めない間も同じ。`builtin:guard-project-home` 全体は足さない）
- 一般の保護と同じ扱いで、`dry-run` では報告だけにする。実行後は、`CCNAVI_RESTORE_IF_DENY` を全体モードと掛け合わせた実効値（`modes.effective_setting`）が `enable` なら未コミットの変更を復元し、全体モードが `dry-run` なら報告だけにする。`Stop` は報告だけで復元しない。`UserPromptSubmit` と `SessionStart` は同じ保護範囲で基準を取り、前からある変更とユーザが受け入れた変更を戻さない
- `disable` のときは、実行前にも実行後にも足さない。ワークスペースルートの `.claude/hooks/` と `.claude/settings*.json` は、ルールの `workspace-root` が実行前に止める

共通のルールファイルが読めない間の修復は、エージェントが `Write` / `Edit` で直接直す形では通さない（ADR-0158。ADR-0012 を覆す）。読めない間も `builtin:guard-ccnavi-config` は外れない。直し方は 1 つで、エージェントが `wip/handoff/.ccnavi/config/common/rules.yml` に下書きを置き（置き場は例外で書ける）、ユーザが端末の `ccnavi user handoff` で取り込むか、手で直す。今の中身が読めない `rules.yml` の取り込みは、`handoff._defense_change` が「確かめられないので端末に限る」と扱う。そのため、ボードからの取り込みは断られる。`builtin:guard-config-via-bash` と `builtin:guard-ccnavi-config` の文面が、この手順を案内する。

同じ設定が有効な間、`ccnavi user prune`（古い形は `ccnavi --prune` の `--preview` の無い形）をシェルから打つ形も、チケット制御に依らず
`DENY_RECORDS_PRUNE`（`builtin:guard-records-prune`）で止める（`phase_forms.prune_form`）。実行ファイルの端末要求は
擬似端末（`script -qc`）を使えば満たせてしまい、チケット制御を切ったワークスペースでは端末要求を切る変数も止まらないため。
見るのは引用符を外したコマンド行で、コマンドの切れ目の中に ccnavi の名前と `user prune`（`user` の次の語が `prune`）か単独の語の `--prune` が並べば止める。
免除は `--preview` が `--prune` の隣に引用をまたがずに並んだ古い形だけ（`user prune` に免除は無い）。

`logs/` の下の git のラッパースクリプトの記録は守らない。

`.git` の中への書き込みは、ここではなく土台の deny（5.6 の `builtin:guard-git-dir`・`builtin:guard-git-dir-via-bash`）で止める。
リダイレクトと `cp` `ln` `install` の行き先は同じ部品で読み、書き換える動詞・`dd`・`sed -i` は読むだけの語を外した `.git` 用の形で読む（`find` と `xargs` の形も足す）。
`CCNAVI_GUARD_CORE_FILES` にも、ルールファイルが読めるかにも依らず当たる。

実行ファイルのパス（`selfguard_shell.binary_clause`）: hook が実際に走らせるのは sh が起動する実体（4.6）なので、実体の置き場にも当てる。
このパスは `builtin:guard-binary` と、`builtin:guard-setting-files` の場所の一覧と、承認のルール（9.5）の実行ファイルの名前に入る。
パスを `.` `..` を除いた要素に割り、名前で 2 つの形を切り替える。

| sh の名前 | 当てるパス |
|---|---|
| `ccnavi-launcher.sh` | sh の末尾 2 要素（`scripts/ccnavi-launcher.sh`）と、その 1 つ上の親の下の `bin/<os>-<arch>/`（既定なら `.ccnavi/bin/<os>-<arch>/`）。sh の隣の `<os>-<arch>/` は当てない |
| それ以外 | 末尾 2 要素（実行ファイルそのもの） |

- sh かどうかは名前だけで決め、`platformtag.launched_executable`（8.3）と条件を揃える。ファイルは読まない。
  別の名前で置いた sh は実行ファイルそのものとして読まれ、その `../bin/` は `builtin:guard-binary` から外れる
- 既定の配置では、`builtin:guard-setting-files` の `.ccnavi` のパスと `builtin:guard-project-home` でも二重に止まる

判定に渡るパスはワークスペースルートを継ぎ足した絶対パス（`settings._resolve_bin`）なので、名指しのツールは浅いパスでも止まる。
相対で書いたシェルのコマンドは守らない（`CCNAVI_BIN_PATH=scripts/ccnavi-launcher.sh` での `rm -rf bin/linux-x86_64`、
`ccnavi-launcher.sh` での `rm -rf ../bin/linux-x86_64` は `builtin:guard-binary` に当たらない）。導入スクリプトはこのパスを作らない。
実行後のバックアップと復元（8.3）は、どのパスでも働く。

止めるのは書き込みの形と場所の組で、場所の名前が出ただけでは止めない（`cat rules.yml` も `git add <パス>` も通る）。

下書きの置き場（ワークスペースルートの `wip/handoff/`。ワークツリーの中の `wip/handoff/` は含まない。`ccnavi-stage.sh` は sh の中で写すので、この例外には頼らない）へ写す cp は、行き先の最後の語に守る名前があっても
`builtin:guard-setting-files` から外す（`selfguard_shell.handoff_copy_exempt`）。守る場所を直すときは、置き場へ写して
写しを直すため（`cp .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh`）。式は先読みを使えないので（5.1）、
外すかどうかは式の外で、行き先を実際のパスに解いて決める。外すのは次をすべて満たすときだけ。

- コマンドが 1 本だけ（つないだ形は外さない。前のコマンドが置き場にリンクを作る形や `cd` を読み切れないため）
- `cp` か `install` で、選択肢は `-f` `-p` `-v` `-n` `-u` を並べた短い形と `--` だけ。止めるものを並べる形にしないのは、
  長い選択肢の省略（`--target` は `--target-directory`）や知らない選択肢に負けるため。`-t`・リンクを作る `-s` `-l`・
  リンクをリンクのまま写す `-P` `-d` `-a` `-r` は許す側に入れない
- 変数・グロブ・引用・リダイレクト・`~` など、表記どおりに読めない文字が無い
- 行き先に `..` の段が無く、payload の cwd から解いた実パス（リンクを解いた先）が置き場の中。行き先がディレクトリなら、
  元の名前を足した先も置き場の中。cwd が無ければ相対の行き先は外さない
- 置き場（`wip/` か `wip/handoff/`）そのものがリンクではない
- 行き先に既にあるファイルが、ハードリンクでほかの名前とつながっていない（上書きがつながった先も書き換えるため）

組み込みの既定のシェルの 1 本（`builtin:guard-config-via-bash`）には、この例外を付けない。
組み込みはルールファイルに何が書いてあっても足す。`builtin:guard-` で始まる id はルールファイルのどのレイヤーにも書けず、
書けばそのルールを読み込まずに error にする（`--lint` も言う）。組み込みを外したいなら env（`CCNAVI_GUARD_CORE_FILES`）でこの機能ごと切る。
組み込みの既定を使っている間（共通レイヤーのルールが不正な間）と `disable` のときは足さない。土台の deny（5.6）は別で、この間も常に当たる（設計 11.2）。既定を使っている間は、組み込みの既定（REQ-PRE-06）のシェルの 1 本
（`builtin:guard-config-via-bash`）が、呼び出しごとに同じ設定から組んで、実行ファイル・ccnavi ディレクトリ・共通レイヤーを
動かした先への書き込みも止める。

共通レイヤーの 3 本は、置き場がどこでも `builtin:guard-common-layer` が `Write` / `Edit` から止める。当てるのはワークスペースルートと、
そこから切ったワークツリーの下の同じ相対（ワークスペースルートの外に置いたなら、そのパス）。既定の置き場（`.ccnavi/config/common/`）では
`builtin:guard-project-home` より先にこちらが当たる。期待する判定（`rule-expectations.yml`）は 3 本に入らず、既定の置き場なら
`builtin:guard-project-home` が止める。

生の git（`builtin:guard-raw-git`）: いつも、生の git を組み込みで止める（9.1）。ラッパーがあればその打ち方を文面で名指しする（ADR-0131）。
ラッパーの中のチケット制御の可否を、ラッパーの外から打つ git で外させないためで、ガード自身を守る組み込みに数える（P4）。
`CCNAVI_GUARD_CORE_FILES` では切れず、チケット制御（`CCNAVI_TICKET_CONTROL`）を切ると外れる。

git のラッパーで守る場所を消す・戻す形（`DENY_WRAPPER_CORE_PATH`、`builtin:guard-wrapper-paths`）: `ccnavi-git.sh` の
`rm`（`--cached` も）・`restore --source <ref>`・`checkout <ref> <パス>` で、`builtin:guard-setting-files` と同じ場所を
消す・別のコミットの中身に戻す形を、ルールより先に止める（`wrapguard.check_core_paths`）。シェルの `rm` は止まるのに、ラッパーの
`git rm` は同じファイルを消せた。`ccnavi-git.sh` 自身が消えても、生の git の組み込みは足したままなので外れない。
`CCNAVI_GUARD_CORE_FILES` が有効な間、チケット制御と組み込みの既定への退避に依らず足す。

| 当たるもの | 理由 |
|---|---|
| 書いたとおりのパスか、居場所（payload の cwd から `cd` を追った先）で解いた絶対パスに、`builtin:guard-setting-files` の場所の式が当たる | シェルの書き込みと同じ場所を、同じ式で見る。実行ファイル・動かした ccnavi ディレクトリ・共通レイヤーもそこに入っている |
| 守る場所を中に含むディレクトリ（ワークスペースルート・実行ファイル・共通レイヤー・記録と state・プロジェクトの置き場の上と、`.ccnavi`・`.claude`・`.git` を直に、または 1 つ下に持つディレクトリ） | `restore --source <ref> -- .` や `rm -r projects` で中身ごと戻す・消す |
| `:` で始まる pathspec、グロブの前に固定のディレクトリが無い指定、`--pathspec-from-file`（省略形も） | 当たる先を読みで決められない。git の pathspec のグロブは `/` もまたぐ |
| 対象を読み切れない形。変数・置換で組み立てたパス、居場所の読めない `cd` / `pushd` の後の相対パス、走査が読み切れないコマンド行 | 共通ルールの allow（`*ccnavi-git.sh*`）が生の文字列に当たるので、ここで止めないと確認に落ちずに通る（ADR-0121 の追記） |
| 承認済みチケットの置き場（`.ccnavi/approved/` とその下） | 置き場を動かすのはユーザと ccnavi のスクリプト。文面は「ユーザに依頼する」。レビュー済みのマーカーを消す `rm` はユーザが端末で打つ手順なので、sh は通す |
| レビュー待ちの置き場（`wip/proposals/review/` とその下。提案の置き場の設定に従う）と、置き場に何かがある間のその親（`wip/`・`wip/proposals/`） | 承認済みチケットの置き場と同じ扱い。文面は「レビュー待ちの提案を外すのはユーザに依頼する」。sh は `rm` を通し、過去の中身に戻す形は前からの置き場の検査が止める。空の置き場の親を消す形（親を閉じた後の案内の `rm -r wip`）は通す。親を過去の中身に戻す形（`restore --source <ref> wip`・`checkout <ref> -- wip`）は空でも当てる（ADR-0121 の追記） |
| ラッパーの名前があるのに、サブコマンドが文字どおりの語でない形（`$S`・`$(echo rm)`・`r?`）、標準入力から読むシェル（`bash --norc`・`--rcfile <値>`・`-o pipefail`・`-O extglob` のようなオプションを前に置いた形と、`env sh`・`command sh` を含む）に流す文字列の中の名前、走査が読み切れず名前の直後に文字どおりのサブコマンドが無い形（`… \| xargs sh …ccnavi-git.sh`） | 共通ルールの allow（`*ccnavi-git.sh*`）は名前があるだけで当たるので、読めない形を確認に落とせない（ADR-0121 の再レビューの追記。`CDPATH`・別の言語の中・`xargs` の値・オプションの変数は限界として 12.1 に書く） |
| `checkout -- <パス>`（起点の無い、索引の中身に戻す形） | sh はこの形を通さない。文面は sh と同じく `restore <パス>` を名指しで使うよう案内する |

通すのは、索引の中身に戻す `restore`（`--staged`、衝突を解く `--ours` / `--theirs`）と、何も消さない `rm -n` / `--dry-run`（後ろに `--no-dry-run` があれば、git と同じく消す形として見る）。
衝突したルールファイルを直す道がここしか無く、索引の中身はエージェントが書けない場所から来る。拒否の文面は、変更をユーザに依頼することと、通る形を案内する。
`--source` の長いオプションは、git が受ける省略形（`--sou=`・`--so`）も同じに読む。
ラッパーも同じ場所の検査（`core_hit`）を 2 重目に持つ。見るのは既定の置き場の名前だけで、動かした置き場は hook が見る。
記録の置き場（`logs/`）はワークスペースルートの下だけに当てる。承認済みチケットの置き場は `core_hit` では見ず、過去の中身に戻す形を
前からの置き場の検査（`restore-store-source`・`checkout-store`）が止める。`rm -n` / `--dry-run` は通す。
見るサブコマンドは `rm`・`restore`・`checkout` だけ（`stash` と `merge` は作業ツリーの変更を退避・取り込むだけで、守る場所を自分では過去の中身にしないため）。

保護済みの sh の前に置く環境変数（`DENY_SCRIPT_ENV_OVERRIDE`）: 保護済みの sh（`ccnavi-*.sh`）は、
ワークスペースルート・承認済みチケットの置き場・実行ファイルを環境変数から読む。そのため、保護済みの sh を呼ぶ
コマンド行で `CCNAVI_` で始まる変数と `CLAUDE_PROJECT_DIR` を置く・外す形を、ルールより先に止める。数えるのは前置きの代入、
`env X=…` と `env -u X`、`export` `declare` `readonly` `local` `unset` の引数、素の代入、環境を丸ごと空にする `env -i` で、
コマンド行のどこに書いても数える。実行役のコマンド越し（`sudo env X=… bash …`）と、関数の定義の本体（`f() { …; }`）も同じ。
子の push の検査も関数の本体を見る。出力の量と待ち時間だけを変える 4 つ（`CCNAVI_GIT_MAX_LINES`・
`CCNAVI_GIT_FAIL_LINES`・`CCNAVI_GIT_KEEP_LOGS`・`CCNAVI_FETCH_TIMEOUT`）は通す。通すものを並べる形なので、sh が
あとから読むようになった変数は止まる側になる。読み切れない形（`sh -c "…"`）は allow が当たらず確認になる。
この止めは補助の保護で、`CCNAVI_GUARD_CORE_FILES` では切れず、`dry-run` でも止める。
hook 自身は settings.json の env で起動するので、ここで見る代入の影響を受けない。

### 8.2.1 設計方針: イレギュラーな場面は止めて人間に直してもらう

例外的な場面（共通のルールファイルが読めない、など）のために判定の経路が複雑になるときは、仕組みで解かない。
判定の例外を足すと、その例外が攻める入口になり、読み手も経路を追い切れなくなる。止めて、人間に直してもらう案内にする
（読めない間の修復は、下書きとユーザの取り込み。ADR-0158）。保護の実装中に、想定していない複雑さが見つかったときも、
実装で抱え込まず、報告して判断を仰ぐ。

### 8.3 戻す側

実行前に対象の中身を `state/selfguard/<セッション>/<鍵>` へバックアップし、実行後に比べて、変わって
いればバックアップから戻す。戻す先は「このツール呼び出しの直前」なので、コミットしていない編集は残る。
実行ファイル（大きい対象）は例外で、ツール呼び出しの外で変わった版は実行前に新しい基準へ取り込み、戻すのはそのツール呼び出しの中で変わったものだけにする。戻す前に、書かれていた版は `state/aside/` に退避する。
バックアップが無いときだけ git のコミット済みから戻し、そうしたことを報告に書く。実行前に対象が消えていれば、
バックアップ → git の順で戻す。どちらも無ければ `.absent` のマーカーを置き、以後は何も言わない。置いていない設定ファイルに
ついては何も言わず、無かったところに現れた場合は言う。

ユーザの発言（`UserPromptSubmit`）では、その時点の状態を基準に取り直す。前のターンの終わりから発言までに起きた変更は、
エージェントが何も実行していない間のことで、ユーザの手による編集か削除だから、次のツール呼び出しで戻さない。
設定ファイルは今の中身でバックアップを取り直し、消えていたらバックアップを捨てて `.absent` のマーカーを置く。実行ファイルは
大きさか更新時刻が変わっていれば新しい版を基準に取り込み、消えていれば参照を捨てて `.absent` を置く（再び置かれたら、
次の実行前で新しい版を基準にする）。`dry-run` でも取り直す。バックアップを取り直すだけで、戻さない。
`SessionStart` では小さい対象のバックアップを取らない。最初の発言までの変更を戻す根拠が無いため、最初の発言で取る。
この受け入れは `UserPromptSubmit` の登録が前提で、登録が無いと、ターンの間に消えた設定ファイルは次の実行前に
前回のバックアップから戻る（`PreToolUse` の登録が無いのと同じく、`--lint` がまだ言わない）。

ユーザが消した実行ファイルを受け入れたあと、ツール呼び出しの中でまた現れたときは、設定ファイルの「無かったところに
現れた」と同じく、言うだけで消さない。次の実行前に新しい版として基準に入る。

ユーザの手によるものと言い切れないときは受け入れない。受け入れない代わりに、次のツール呼び出しの前と同じ照合（実行前の
バックアップと、消えた設定ファイルの復元）をその場で行う。受け入れないのは次の 2 つ。

- バックグラウンド作業の完了通知。`UserPromptSubmit` を起こす（`prompt` に `<task-notification>` が含まれる）が、その間に動いた
  プロセスの変更は、ユーザの変更と区別できない
- 前のターンの終わりに、動かしている作業が残っていたとき。`Stop` の payload の `background_tasks`（バックグラウンドのコマンド）
  か `session_crons`（予約した繰り返し）が空でなければ、`state/busy-<セッション>.json` に印を置く。残っていない `Stop`、起動、
  再開で消す。印が読めないときは残っているものとして扱う

残る穴は、`Stop` の payload に現れない手段（外部の予約など）で、ユーザの発言に見える `UserPromptSubmit` を作る場合。
受け入れた消えたことと取り直しは、記録の `guarded`（`accepted`・`rebased`）に残す。

失敗した呼び出し（`PostToolUseFailure`）も、成功した呼び出しと同じ照合を行う。失敗する前に消した・書き換えたものを
通すと、次の発言で「ユーザが消した」ものとして受け入れられるため。

組み込みの既定を使っている間の、dry-run で通った修復だけは戻さない（REQ-PRE-06。エージェントが名前で書くツールで直す道は、8.2 の `builtin:guard-ccnavi-config` が止める。そのため、通るのは `dry-run` で名前で書くツールが通った場合だけになる。復元側の既存の例外として残し、実行後のチェック（`post.check`）には引き継がない）。共通レイヤーのルールファイルが実行前のバックアップの時点で読めず、その呼び出しが
`Write` / `Edit` / `NotebookEdit` でそのファイルを書いたときに限る。判断は実行前の中身で行う。シェルからの書き込み、
他の設定ファイル、ワークツリー側の設定は戻す。戻さなかったことは `left-as-repair` として報告し、直した中身をユーザが確かめるよう促す。

実行ファイルは `SessionStart` でバックアップする（ツール呼び出しの外で変わったら、次の発言か実行前に取り直す）。バックアップするのは `CCNAVI_BIN_PATH` が指す sh（バックアップの鍵 `bin`）と、
sh がこの機械で起動する実体（`bin-launched`）の 2 つ。実体は `platformtag.launched_executable` が sh と同じ順で探し、
名前が `ccnavi-launcher.sh` なら `../bin/`、それ以外は隣を見る（8.2 と同じ条件）。見つからなければ足さない。
実体は中身のハッシュで名前を付けて `state/selfguard/store/` へ 1 本だけ置き、セッションの側は参照（ハッシュ・大きさ・更新時刻）だけ
持つ。比べるのは大きさと更新時刻。3 日より長く触られていないセッションのバックアップは、次のセッション開始で消す。

実行前にも見るのは、`PostToolUse` の登録を消されるとその次の呼び出しから実行後チェックが動かないため。`PreToolUse` の
登録ごと消された場合は ccnavi が一切動かない（12.3）。
