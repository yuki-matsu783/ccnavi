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
| `.claude/settings.json` / `.claude/settings.local.json` | hook の登録そのもの | ツール実行前 |
| 共通レイヤーの 3 本（`.ccnavi/common/{rules,phases,risks}.yml`） | 判定の中身そのもの | ツール実行前 |
| 自身のレイヤーと各プロジェクトのレイヤーの `.ccnavi/config/{rules,phases,risks}.yml` | 同上 | ツール実行前 |
| `CCNAVI_BIN_PATH` が指すファイル（振り分けの sh）と、sh がこの機械で起動する実行ファイル | 判定器の実体 | セッション開始 |

上のうち追跡されているものは、その元リポジトリから切ったワークツリー側の設定も対象に入る（11.6）。
hook スクリプトと保護済みスクリプトはここに無く、ルールの `deny` で止めている。ccnavi ディレクトリの下のスクリプトも無く、組み込みの `deny` で止める。

### 8.2 止める側

`CCNAVI_GUARD_CORE_FILES` が有効な間、`deny` の先頭に最大 5 本を挿す。

| id | 足すとき | 止めるもの |
|---|---|---|
| `builtin-guard-setting-files` | 常に | Bash で、書き込みの形（`>` 系のリダイレクト、`mv` `rm` `tee` `dd` `truncate` `patch` `shred`、`sed -i`、`cp` `ln` `install` の行き先、`cp` `ln` `install` `mv` で守る名前を入っているディレクトリへ置く形）と場所（`.claude/hooks/`、`.claude/settings*.json`、`ccnavi-git.sh`、実行ファイル、レイヤーの設定 3 本と ccnavi ディレクトリ、共通レイヤーの 3 本（`.ccnavi/common/`）、記録と state の `logs/decisions.jsonl` `logs/state`、閉じたチケットの退避の `logs/archive`）の組 |
| `builtin-guard-binary` | `CCNAVI_BIN_PATH` が設定されているとき | `Write` `Edit` `NotebookEdit` |
| `builtin-guard-project-home` | 常に（ccnavi ディレクトリは固定の `.ccnavi`） | 同上 |
| `builtin-guard-common-layer` | 共通レイヤーの 3 本の置き場が決まっているとき（11.6） | 同上 |
| `builtin-guard-records` | 常に | `Write` `Edit` `NotebookEdit` で、記録と state の置き場（`logs/decisions*.jsonl`、`logs/state/`、閉じたチケットの退避 `logs/archive/`。診断のフラグ `--log` / `--state` で動かしたときは、その置き場とローテートした分にも当てる）。シェルの側の `builtin-guard-setting-files` と同じ場所 |

同じ設定が有効な間、`ccnavi --prune`（`--preview` の無い形）をシェルから打つ形も、チケット制御に依らず
`DENY_RECORDS_PRUNE`（`builtin-guard-records-prune`）で止める（`phase_forms.prune_form`）。実行ファイルの端末要求は
擬似端末（`script -qc`）を使えば満たせてしまい、チケット制御を切ったワークスペースでは端末要求を切る変数も止まらないため。
見るのは引用符を落としたコマンド行で、コマンドの切れ目の中に ccnavi の名前と単独の語の `--prune` が並べば止める。
免除は `--preview` が `--prune` の隣に引用をまたがずに並んだ形だけ。

`logs/` の下の git のラッパースクリプトの記録は守らない。

実行ファイルのパス（`selfguard_shell.binary_clause`）: hook が実際に走らせるのは sh が起動する実体（4.6）なので、実体の置き場にも当てる。
このパスは `builtin-guard-binary` と、`builtin-guard-setting-files` の場所の一覧と、承認のルール（9.5）の実行ファイルの名前に入る。
パスを `.` `..` を落とした要素に割り、名前で 2 つの形を切り替える。

| sh の名前 | 当てるパス |
|---|---|
| `ccnavi-launcher.sh` | sh の末尾 2 要素（`scripts/ccnavi-launcher.sh`）と、その 1 つ上の親の下の `bin/<os>-<arch>/`（既定なら `.ccnavi/bin/<os>-<arch>/`）。sh の隣の `<os>-<arch>/` は当てない |
| それ以外 | 末尾 2 要素（実行ファイルそのもの） |

- sh かどうかは名前だけで決め、`platformtag.launched_executable`（8.3）と条件を揃える。ファイルは読まない。
  別の名前で置いた sh は実行ファイルそのものとして読まれ、その `../bin/` は `builtin-guard-binary` から外れる
- 既定の配置では、`builtin-guard-setting-files` の `.ccnavi` のパスと `builtin-guard-project-home` でも二重に止まる

判定に渡るパスはワークスペースルートを継ぎ足した絶対パス（`settings._resolve_bin`）なので、名指しのツールは浅いパスでも止まる。
相対で書いたシェルのコマンドは守らない（`CCNAVI_BIN_PATH=scripts/ccnavi-launcher.sh` での `rm -rf bin/linux-x86_64`、
`ccnavi-launcher.sh` での `rm -rf ../bin/linux-x86_64` は `builtin-guard-binary` に当たらない）。導入スクリプトはこのパスを作らない。
実行後のバックアップと復元（8.3）は、どのパスでも働く。

止めるのは書き込みの形と場所の組で、場所の名前が出ただけでは止めない（`cat rules.yml` も `git add <パス>` も通る）。
組み込みはルールファイルに何が書いてあっても足す。`builtin-guard-` で始まる id はルールファイルのどのレイヤーにも書けず、
書けばそのルールを読み込まずに error にする（`--lint` も言う）。組み込みを外したいなら env（`CCNAVI_GUARD_CORE_FILES`）でこの機能ごと切る。
組み込みの既定を使っている間と `disable` のときは足さない。既定を使っている間は、組み込みの既定（REQ-PRE-06）のシェルの 1 本
（`builtin-guard-config-via-bash`）が、呼び出しごとに同じ設定から組んで、実行ファイル・ccnavi ディレクトリ・共通レイヤーを
動かした先への書き込みも止める。

共通レイヤーの 3 本は、置き場がどこでも `builtin-guard-common-layer` が `Write` / `Edit` から止める。当てるのはワークスペースルートと、
そこから切ったワークツリーの下の同じ相対（ワークスペースルートの外に置いたなら、そのパス）。既定の置き場（`.ccnavi/common/`）では
`builtin-guard-project-home` より先にこちらが当たる。見本（`rule-samples.yml`）は 3 本に入らず、既定の置き場なら
`builtin-guard-project-home` が止める。

保護済みの sh の前に置く環境変数（`DENY_SCRIPT_ENV_OVERRIDE`）: 保護済みの sh（`ccnavi-*.sh`）は、
ワークスペースルート・承認済みチケットの置き場・実行ファイルを環境変数から読む。そのため、`CCNAVI_GUARD_CORE_FILES` が有効な間は、保護済みの sh を呼ぶ
コマンド行で `CCNAVI_` で始まる変数と `CLAUDE_PROJECT_DIR` を置く・外す形を、ルールより先に止める。数えるのは前置きの代入、
`env X=…` と `env -u X`、`export` `declare` `readonly` `local` `unset` の引数、素の代入、環境を丸ごと空にする `env -i` で、
コマンド行のどこに書いても数える。実行役のコマンド越し（`sudo env X=… bash …`）と、関数の定義の本体（`f() { …; }`）も同じ。
子の push の検査も関数の本体を見る。出力の量と待ち時間だけを変える 4 つ（`CCNAVI_GIT_MAX_LINES`・
`CCNAVI_GIT_FAIL_LINES`・`CCNAVI_GIT_KEEP_LOGS`・`CCNAVI_FETCH_TIMEOUT`）は通す。通すものを並べる形なので、sh が
あとから読むようになった変数は止まる側になる。読み切れない形（`sh -c "…"`）は allow が当たらず確認になるので、
ここでは見ない。hook 自身は settings.json の env で起動するので、ここで見る代入の影響を受けない。

### 8.3 戻す側

実行前に対象の中身を `state/selfguard/<セッション>/<鍵>` へバックアップし、実行後に比べて、変わって
いればバックアップから戻す。戻す先は「このツール呼び出しの直前」なので、コミットしていない編集は残る。
バックアップが無いときだけ git のコミット済みから戻し、そうしたことを報告に書く。実行前に対象が消えていれば、
バックアップ → git の順で戻す。どちらも無ければ `.absent` のマーカーを置き、以後は何も言わない。置いていない設定ファイルに
ついては何も言わず、無かったところに現れた場合は言うが消さない。

組み込みの既定を使っている間の修復だけは戻さない（REQ-PRE-06）。共通レイヤーのルールファイルが実行前のバックアップの時点で読めず、その呼び出しが
`Write` / `Edit` / `NotebookEdit` でそのファイルを書いたときに限る。判断は実行前の中身で行う。シェルからの書き込み、
他の設定ファイル、ワークツリー側の設定は戻す。戻さなかったことは `left-as-repair` として報告し、直した中身をユーザが確かめるよう促す。

実行ファイルは `SessionStart` で 1 度だけバックアップする。バックアップするのは `CCNAVI_BIN_PATH` が指す sh（バックアップの鍵 `bin`）と、
sh がこの機械で起動する実体（`bin-launched`）の 2 つ。実体は `platformtag.launched_executable` が sh と同じ順で探し、
名前が `ccnavi-launcher.sh` なら `../bin/`、それ以外は隣を見る（8.2 と同じ条件）。見つからなければ足さない。
実体は中身のハッシュで名前を付けて `state/selfguard/store/` へ 1 本だけ置き、セッションの側は参照（ハッシュ・大きさ・更新時刻）だけ
持つ。比べるのは大きさと更新時刻。3 日より長く触られていないセッションのバックアップは、次のセッション開始で落とす。

実行前にも見るのは、`PostToolUse` の登録を消されるとその次の呼び出しから実行後チェックが動かないため。`PreToolUse` の
登録ごと消された場合は ccnavi が一切動かない（12.3）。
