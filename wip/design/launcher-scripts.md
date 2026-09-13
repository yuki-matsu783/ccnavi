# 設計: 振り分けの sh を .ccnavi/scripts/ に移し、--bin を廃止する

親チケット `launcher-scripts`、フェーズ 1（設計）の成果物。ADR-0041 を改める。
コードと文書本体には触らない。人が決めたことは 15 節にまとめた。

## 0. 何を解くか

- `.ccnavi/scripts/` は「原本と配布先が同じ綴りで、置いた場所でそのまま動く sh」の置き場。
  振り分けの sh は配布先の hook が必ず通るのに、原本は `scripts/ccnavi-launcher.sh`、配布先は
  `.ccnavi/bin/ccnavi` と、名前も場所も変わる
- `.ccnavi/bin/` に sh と実行ファイルが混ざっている
- このリポジトリの hook は `dist/ccnavi/ccnavi` を直に起動していて、振り分けの sh を通らない。
  同じフォルダを Windows と WSL から開くと片方で起動しない。sh の不具合にも自分で気づけない

**要点: 置き場を 2 つに分けて固定する。** sh は `.ccnavi/scripts/ccnavi-launcher.sh`、実行ファイルは
`.ccnavi/bin/<os>-<arch>/`。sh は自分の隣ではなく `../bin/` を探す。置き場が動かないので `--bin` は要らない。

**あわせて塞ぐもの（3.5 節）:** `env`・`sudo`・`sh -c` などでコマンドを包むと、組み込みの守り（設定ファイル・チケットの状態・承認）と
サブエージェントの禁止が当たらない穴。振り分けの sh の名前に `.sh` が付くと `sh <sh> --approve --yes` が自然な打ち方になるので、
その発見から広げて、包みを外した形を止める側のルールに当てる仕組みで直す。

## 1. 置き場（改版後）

| 何 | ccnavi のリポジトリ | 配布先 | 前 |
|---|---|---|---|
| 振り分けの sh | `.ccnavi/scripts/ccnavi-launcher.sh`（追跡。モード 100755） | 同じ綴り（導入スクリプトが配る） | 原本 `scripts/ccnavi-launcher.sh`、配布先 `.ccnavi/bin/ccnavi`（`--bin` で動く） |
| 実行ファイル | `dist/ccnavi/`（組み立ての出力）と `.ccnavi/bin/<os>-<arch>/`（`build.py` が写す。無視） | `.ccnavi/bin/<os>-<arch>/`（導入スクリプトが配る。無視） | 配布先は `--bin` の隣の `<os>-<arch>/` |
| 機械の印 | `dist/ccnavi.target` | — | 変わらない |
| `CCNAVI_BIN_PATH` | `.ccnavi/scripts/ccnavi-launcher.sh` | 同じ | このリポジトリ `dist/ccnavi/ccnavi`、配布先 `.ccnavi/bin/ccnavi` |
| hook の `command` | `"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}"` | 同じ | 変わらない |
| `--bin` | — | 廃止 | あり |

```
.ccnavi/scripts/ccnavi-launcher.sh      ← CCNAVI_BIN_PATH が指す振り分けの sh
.ccnavi/scripts/ccnavi-ticket.sh など   ← ゲートの sh（今のまま）
.ccnavi/bin/darwin-arm64/ccnavi         ← 機械ごとの組み立て（_internal/ も同じ置き場）
.ccnavi/bin/linux-x86_64/ccnavi
.ccnavi/bin/windows-x86_64/ccnavi.exe
```

実行ファイルの置き場は、既定で配っていた配布先では前と同じ場所になる。移し替えで動くのは sh 1 本と
env の 1 行だけ。

## 2. 振り分けの sh

### 2.1 探す順

```sh
here=${0%/*}
[ "$here" != "$0" ] || here=.
bin_dir=$here/../bin
```

語の読み方と順（`uname -sm` を 1 回、arm64 の macOS と Windows は自分向けが無いときだけ x86_64）は今のまま。
各 target で `ccnavi` → `ccnavi.exe` の順に `[ -f ]` を見て、見つかったものへ `exec` で引数と標準入力を渡す。

- **隣（`.ccnavi/scripts/<os>-<arch>/`）は探さない。** そこは配る場所ではない。探すと、自己保護の綴り（3 節）が
  当たらない置き場から実体を起動する道ができる
- `bin_dir` は `..` を含むまま使い、正規化しない。`${here%/*}` で切る形は `here` が `.` や 1 段の名前のときに
  壊れる。正規化に `case` や `cd` を足すより、文面に `..` が出るほうが安い
- シンボリックリンクは解かない。`$0` がリンクなら、リンクの置き場から `../bin/` を探す（今と同じ扱い）

### 2.2 見つからないとき

終了コードは 127（今と同じ）。文面:

```
ccnavi: この機械（<os>-<arch>）で動く実行ファイルが <bin_dir>/ にありません。ccnavi のリポジトリでは build.py を回すと置かれます。配布先では、この機械で組み立てたものを scripts/ccnavi-setup.sh で配ってください。
```

実行ファイルは在るが実行できないときは、`exec` が失敗して sh が 126 で終わる。ここでは作り込まない。

### 2.3 実行ビット

hook は sh を `sh` 経由ではなく直に起動するので、sh に実行ビットが要る。このリポジトリでは
追跡するモードを 100755 にする（人が写すときに付ける。`COPY.md` に書く）。配布先の扱いは 15 節 D-3。

ゲートの sh（`ccnavi-ticket.sh` など）は `[ -x "$CCNAVI_BIN_PATH" ]` を見てから `exec` するので、
実行ビットが落ちた sh では「実行ファイルが無い (…/ccnavi-launcher.sh)」という食い違った文面で落ちる。
これも D-3 で塞ぐ理由の 1 つ。

## 3. 自己保護

### 3.1 何で守られるか

| 対象 | 組み込みの `.ccnavi` の守り | `CCNAVI_BIN_PATH` 由来の守り |
|---|---|---|
| `.ccnavi/scripts/ccnavi-launcher.sh` | シェル: `builtin-guard-setting-files`（`\.ccnavi` の綴り）。名指し: `builtin-guard-project-home`（`*/.ccnavi/*`） | `builtin-guard-binary`、控えの key `bin` |
| `.ccnavi/bin/<os>-<arch>/…` | 同上 | `builtin-guard-binary`（新しい形）、控えの key `bin-launched` |

既定の配置では両方が組み込みの `.ccnavi` の守りで二重に止まる。`CCNAVI_BIN_PATH` 由来の守りは、
REQ-SLF-07 を ccnavi ディレクトリの守りに寄りかからせないためと、既定でない綴り（テストの
`sys.executable`、前の形のワークスペース）のために残す。

**既定の配置では緩めるところは無い。** 既定でない浅い綴り（`scripts/ccnavi-launcher.sh` など）では、相対のシェル書き込み
（`rm -rf bin/linux-x86_64`）が `builtin-guard-binary` に当たらない形が残る（3.2 節の表、15 節 D-7）。前の形にも
同じ種類の限界がある。実行後の控えと復元（`bin-launched`）は、どの綴りでも効く。

### 3.2 `binary_clause` の新しい形

```python
# platformtag.py
LAUNCHER_NAME = "ccnavi-launcher.sh"

# selfguard.py
def binary_clause(bin_path: str) -> str:
    parts = [...]                      # 今と同じ分解
    if parts and parts[-1] == platformtag.LAUNCHER_NAME:
        sh = r"[\\/]".join(re.escape(p) for p in parts[-2:])
        home = re.escape(parts[-3]) + r"[\\/]" if len(parts) >= 3 else ""
        return rf"(?:{sh}|{home}bin[\\/]{_BUILD_DIR}(?:[\\/][^\x00]*)?)"
    # それ以外は今の形（末尾 2 要素と、隣の組み立ての置き場）
```

- **sh かどうかは名前だけで決め、`launched_executable`（3.3 節）と条件を揃える。** 前の版は「3 段以上」も
  求めていて、名前だけで切り替える `launched_executable` と食い違っていた（敵対的レビュー 2）。
  ファイルを読まないので判定の期限に効かず、在るかどうかに依らず同じ綴りを返す。
  代償: 別の名前で置いた sh は前の形で扱われ、その sh の `../bin/` は `builtin-guard-binary` から外れる。
  導入スクリプトはもうその形を作らない
- **判定に渡る綴りは絶対パス。** `settings._resolve_bin` がワークスペースルートを継ぎ足すので、段数はふつう
  3 以上になる。名指しのツール（解決済みの絶対パスに当てる）は浅い綴りでも止まる。残るのは、相対で書かれた
  シェルのコマンドだけ

測った結果（`CCNAVI_BIN_PATH` の綴りごと。ワークスペースルートは `/ws/proj`）:

| 綴り | Write: 実体 | Write: sh | シェル: 相対 | シェル: 絶対 |
|---|---|---|---|---|
| `.ccnavi/scripts/ccnavi-launcher.sh` | 止まる | 止まる | 止まる（`rm -rf .ccnavi/bin/linux-x86_64`） | 止まる |
| `tools/scripts/ccnavi-launcher.sh` | 止まる | 止まる | 止まる（`rm -rf tools/bin/linux-x86_64`） | 止まる |
| `scripts/ccnavi-launcher.sh` | 止まる | 止まる | **通る**（`rm -rf bin/linux-x86_64`） | 止まる |
| `ccnavi-launcher.sh` | 止まる | 止まる | **通る**（`rm -rf ../bin/linux-x86_64`） | 止まる |

浅い 2 行の「通る」は、`bin/<os>-<arch>` を位置を問わず当てれば 2 段のほうは塞がるが、どのディレクトリの
`bin/linux-x86_64` にも当たる。1 段のほうは、それでも塞がらない（15 節 D-7）
- **前の形を残す。** `.ccnavi/bin/ccnavi` を指したままのワークスペース（導入スクリプトを打ち直すまで）で、
  隣の実体を守り続けるため。外すと打ち直すまでの間だけ守りが緩む（15 節 D-2）

### 3.3 `launched_executable`

```python
def launched_executable(launcher: str, host: str | None = None) -> str:
    here = os.path.dirname(launcher)
    if os.path.basename(launcher) == LAUNCHER_NAME:
        places = os.path.join(os.path.dirname(here), "bin")
    else:
        places = here                  # 前の形
    ...                                # 探す順は sh と同じ
```

切り替えの条件は `binary_clause` と同じ「名前が `ccnavi-launcher.sh`」だけ。どちらかだけ条件を足すと、
守る場所と控える場所が食い違う。`selfguard.targets` は変えない。sh（数 KB）も今と同じく heavy のまま控える。

### 3.4 承認の経路（`builtin-guard-ticket-approval`）

**今の実装で起きること**（`CCNAVI_BIN_PATH=.ccnavi/scripts/ccnavi-launcher.sh`、このリポジトリの `rules.yml` で
`--test Bash … --json` を打って確かめた。`<sh>` は sh の綴り、末尾はすべて `--approve --yes x`）:

| 形 | 判定 | コード | 当たったルール |
|---|---|---|---|
| `<sh> …`、`/ws/…/<sh> …` | deny | `DENY_TICKET_APPROVAL_CLI` | `builtin-guard-ticket-approval` |
| `sh <sh>`、`bash`、`/bin/sh`、`env sh`、`env FOO=1 sh`、`command sh`、`exec sh`、`nohup sh`、`zsh`、`dash`、`sh -x`、`cd /tmp && sh <sh>`、`sh .ccnavi/bin/ccnavi`（前の形） | ask | `UNDECLARED` | なし |
| `sh -c '<sh> …'`、`bash -lc "…"`、`. <sh>`、`source <sh>` | ask | `PARSE_UNCERTAIN`（読み切れない） | なし |

間接起動は黙って通ってはいないが、承認のルールでは止まらず、確認に落ちている。判定を権限モードに渡すモード
（読み切れる形だけ）では、モードの側で通りうる。前の版の表（`sh`/`bash` の 2 列だけ）は狭すぎた（敵対的レビュー 1）。

**塞ぎ方: 承認のルールの正規表現は変えず、3.5 節の「包みを外した形」に当てて塞ぐ（D-8）。**
包みの問題は承認のルールだけでなく、組み込みの守りと利用者のルールに共通していた（3.5 節の実測）。承認のルールにだけ
ラッパの一覧を持たせる案は、同じ穴を 1 本ずつ塞ぐことになるのでやめた。`--approve --preview` を通す既存の除外と、
`--yes` を独立した枝で当てる形は変えない。

launcher-scripts-03 で比べた、承認のルール専用の案（記録として残す）:

| 案 | 止めたい 25 形 | 止めすぎの候補 7 形 | 残る形 |
|---|---|---|---|
| 前の版の `((sh\|bash)\s+)?` | 一部 | 0 | `/bin/sh`・`env`・`command`・`exec`・`zsh`・`sh -c`・`source` など |
| A: 同じコマンドの中なら語の位置を問わない | 25 | **7**（引用しない `echo ccnavi --approve --yes x`、`git log --grep ccnavi --yes` など） | なし |
| B: ラッパの並び + 引用 | 23 | 0 | `sudo -u me sh -c '…'`、`find -exec`（ask） |
| **3.5 節の外した形（採る）** | 25（試作で確かめた 20 形を含む） | 0（`echo` などは包むコマンドではないので外さない） | 包むコマンドの一覧に無いもの（3.5.6） |

**止まる側への変更**で、チケットの「変える場所」には書かれていない（15 節 D-5・D-8）。

### 3.5 包みを外した形を、止める側のルールに当てる

利用者の決定（2026-09-13）により、launcher-scripts の中でまとめて直す。

#### 3.5.1 何が起きているか（実測）

このリポジトリの `rules.yml` と組み込みで `--test Bash … --json` を打った。包み方は `env`・`FOO=1`・`command`・`exec`・`nohup`・
`time`・`sudo`・`sudo -u me`・`timeout 5`・`nice -n 5`・`stdbuf -o0`・`/usr/bin/env` の 13 通り（「包み 13」）と、
読み切れない形の `sh -c '…'`・`echo x | xargs …`。

| 守り | 包まない | 包み 13 | `sh -c`・`xargs` | 原因 |
|---|---|---|---|---|
| 設定ファイル（`builtin-guard-setting-files`。`rm`・`mv`・`tee`・`sed -i`・`cp`・`truncate` で `.ccnavi/common/rules.yml`・`.claude/settings.json`） | deny | **ask（すべて）** | ask | `selfguard._WRITE_VERBS` が `(^\|\x00)(mv\|rm\|…)` で先頭に固定 |
| チケットの状態（`builtin-ticket-state-shell`。`mv … wip/tickets/doing/`） | deny | **ask** | ask | 同じ `_WRITE_VERBS` を使う |
| 承認（`builtin-guard-ticket-approval`） | deny | **ask** | ask | `launcher` を先頭に固定（3.4 節） |
| 実行ファイル・記録（`rm -rf .ccnavi/bin/…`、`rm -rf logs/state`） | deny | deny（組み込みは外れ、利用者のルール `recursive-delete` だけが当たる） | deny（同） | 同上 |
| リダイレクト（`env echo x > .ccnavi/common/rules.yml`） | deny | deny | deny | `>` の行き先は包みの外に付く |
| 利用者: `raw-git`・`recursive-delete` | deny | deny | deny | 正規表現が語の途中にも当たる書き方 |
| 利用者: `prefer-webfetch`（`curl`） | ask | **当たらない**（未宣言の ask） | 当たらない | `(^\|\x00)` で先頭に固定 |
| サブエージェントの禁止（`phase.forbidden`。`sh .ccnavi/scripts/ccnavi-ticket.sh start`） | 禁止 | **`env sh`・`command sh`・`/bin/sh` は素通り** | 素通り | `(^\|[;&\|]\s*)(sh\|bash)\s+` で先頭に固定 |
| ゲートの中で通す形（`phase.exempt`） | 通す | 包むと通さない（広がっていない） | — | — |

`cd /tmp && …`・`( … )`・`$( … )` は今も当たる。切り分けは効いていて、足りないのは「包み」だけ。

ask に落ちる形は人に確認されるが、読み切れる形は判定を権限モードに渡すので（`undeclared_verdict`）、モードによっては
尋ねずに通る。サブエージェントの禁止には確認の段が無い。

#### 3.5.2 外し方（`shellread`）

`shellread` に「1 本のコマンドから、包みを 1 枚ずつ外した層を全部返す」処理を足す。元の形は含めない。途中の層も残す
（`env sh .ccnavi/scripts/ccnavi-approve.sh` の `sh …approve.sh` の層に、承認のルールの `script` の枝が当たるため）。深さは 4 まで。

| 形 | 外した層 |
|---|---|
| `FOO=1 <cmd>` | `<cmd>` |
| `/bin/sh x`、`git.exe x`（区切りの前の道筋や拡張子が付いた名前） | `sh x`、`git x`（`_base` で読み替えた層） |
| 包むコマンド `env` `command` `exec` `nohup` `time` `nice` `sudo` `doas` `timeout` `stdbuf` `chrt` `ionice` `taskset` | オプション（`-` で始まる語）、値を取るオプションの値、位置引数、`--`、`env` の `FOO=1` を飛ばした残り |
| `sh` `bash` `zsh` `dash` `ksh` `<ファイル> <引数>` | `<ファイル> <引数>` |
| `sh -c '<文字列>'`（`-lc` のような組み合わせも） | 文字列を読み直したコマンドと、その層 |
| `eval <語…>` | 語をつないで読み直したコマンドと、その層 |
| `.` / `source` `<ファイル> <引数>` | `<ファイル> <引数>` |
| `xargs [オプション] <cmd…>` | `<cmd…>` |
| `find … -exec` / `-execdir` / `-ok` / `-okdir` `<cmd…> ;` または `+` | `<cmd…>`（`-exec` が複数ならそれぞれ） |

値を取るオプション（試作の一覧。実装で `--help` と突き合わせる）:

| コマンド | 値を取る | 位置引数 |
|---|---|---|
| `env` | `-u` `--unset` `-C` `--chdir` | 0 |
| `exec` | `-a` | 0 |
| `time` | `-f` `-o` | 0 |
| `nice` | `-n` `--adjustment` | 0 |
| `sudo` | `-u` `-g` `-C` `-h` `-p` `-U` `-r` `-t` `-T` | 0 |
| `doas` | `-u` `-C` | 0 |
| `timeout` | `-s` `--signal` `-k` `--kill-after` | 1（時間） |
| `stdbuf` | `-i` `-o` `-e` | 0 |
| `chrt` / `taskset` | — | 1 |
| `ionice` | `-c` `-n` `-p` | 0 |
| `xargs` | `-I` `-n` `-P` `-d` `-L` `-s` `-E` `-a` `--max-args` `--max-procs` `--delimiter` | 0 |

`Reading` に欄 `unwrapped: str` を足す。全コマンドの層を `SEP` でつないだ文字列で、層が無ければ空。**読み切れない形
（`degraded`）でも、トークンに割れる限り作る。** 閉じない引用と閉じないヒアドキュメントでは作らない。`degraded` と
理由はそのまま残す。

#### 3.5.3 当てる先の線引き（`judge`）

| 当てる先 | 元の形（読めれば組み直した文字列、読み切れなければ生の文字列） | 外した形 |
|---|---|---|
| deny（組み込み・利用者） | 当てる | **当てる** |
| ask（組み込み・利用者） | 当てる | **当てる** |
| allow（利用者） | 読み切れたときだけ当てる（今のまま） | **当てない** |
| サブエージェントの禁止（`phase.forbidden`） | 当てる | **当てる** |
| ゲートの中で通す形（`phase.exempt`） | 当てる（今のまま） | **当てない** |
| チケットの範囲（`ticket_verdict`） | 今のまま | 当てない |

`Rule.matches` は変えない。`judge` がタイプごとに当てる文字列の並び（deny・ask は元の形と外した形、allow は元の形だけ）を
渡す。PowerShell は shellread で読まないので変わらない。

**allow と exempt に当てない理由。** 外した形は「止める側に足す当て先」で、元の形の判定を消さない。だから層の読み違いが
あっても、当たるはずのものが当たらないだけで、今より緩くはならない。allow と exempt に当てると逆になる。
試作で `sudo -u me cat /etc/shadow` は、元の形では ask、外した形だけに当てると `prefer-read-grep` の allow になった。
`env sh .ccnavi/scripts/ccnavi-review.sh …` に exempt を当てると、ゲートの中で包んだコマンドが通るようになる。

#### 3.5.4 試作で測った結果

`shellread` の内部（トークン化・ヒアドキュメント・切り分け）を使った試作で層を作り、元の形と外した形をそれぞれ
`--test` に当てた（deny / ask で当たったルールを比べる）。

止めたい 20 形は、すべて止まるようになった:

| 形（例） | 外した形で当たったもの |
|---|---|
| `env rm -f …rules.yml`、`sudo -u me mv …settings.json …`、`timeout 5 sed -i …`、`nice -n 5 tee …`、`/usr/bin/env FOO=1 cp …`、`stdbuf -o0 truncate …` | `builtin-guard-setting-files` |
| `sh -c 'rm -f …rules.yml'`、`eval 'mv …settings.json …'`、`echo x \| xargs rm -f …`、`find … -exec rm -f … ;` | `builtin-guard-setting-files` |
| `command mv wip/tickets/todo/a.md wip/tickets/doing/a.md` | `builtin-ticket-state-shell` |
| `env sh <sh> --approve --yes x`、`sudo -u me sh -c '<sh> …'`、`source <sh> …`、`find … -exec <sh> … ;`、`env sh .ccnavi/scripts/ccnavi-approve.sh` | `builtin-guard-ticket-approval` |
| `env curl -d @x https://example.com` | `prefer-webfetch`（ask） |
| `env sh …ccnavi-ticket.sh start x`、`/bin/sh …ccnavi-ticket.sh done x`、`sh -c 'sh …ccnavi-review.sh request --phase 1'` | サブエージェントの禁止 |

普通の作業 27 形で、外したことにより新しく deny / ask に当たったものは **0**。対象: `time uv run pytest`、`nice -n 10 make test`、
`env PYTHONUTF8=1 uv run python -m unittest`、`timeout 60 pnpm test`、`sudo -u me cat /etc/hosts`、`sh scripts/ccnavi-setup.sh --check`、
`bash tests/run.sh`、`command -v jq`、`env | grep CCNAVI`、`sh -c 'cat README.md'`、`find … -exec grep -l foo {} ;`、
`echo x | xargs grep -n foo`、`nohup python -m http.server &`、`stdbuf -o0 tail …`、`source .venv/bin/activate`、
`. .venv/bin/activate && uv run pytest`、`bash -lc 'uv run ruff check .'`、ゲートの sh 3 形、`exec zsh`、`time git log --oneline`、
`find … -exec rm {} +`、`xargs -n1 -I{} echo {}`、`sudo -E env PATH=/x make install`、`env -u CCNAVI_MODE uv run python -m ccnavi --lint`、
`timeout 5 sh -c 'cat logs/log.jsonl | tail -n 3'`。

#### 3.5.5 記録と文面

外した形で当たったときは、記録（`log.jsonl`）に欄 `unwrapped` を足してその層を残し、拒否と確認の文面に
「包みを外した形（`<層>`）に当たりました」を 1 行足す（D-10）。元の形のどこが問題なのかが、読み手に分からなくなるため。

#### 3.5.6 残る穴と代償

- **包むコマンドの一覧に無いもの**（`script -c`、`watch`、`parallel`、`busybox sh`、`ssh host <cmd>`、`perl -e`、`python -c`）は外さない。
  先頭に固定したルールは今と同じく抜け、未宣言なら ask に落ちる。一覧は shellread の組み込みに持つ（D-9）
- **値を取るオプションの読み違い。** 一覧に無いオプション（`sudo --preserve-env=X` は 1 語なので正しく飛ぶが、将来の
  `sudo -R dir` など）は、値を命令と読んで層がずれる。ずれても当たらないだけで、今より緩くはならない（3.5.3）
- **変数・alias・関数**（`$SUDO rm …`）には届かない。shellread の既知の限界のまま
- **止めすぎ。** 外した形は止める側にしか足さないので、起こりうるのは止めすぎだけ。試作の 27 形では 0 だったが、
  `time` や `nice` の後ろに利用者の deny に当たるコマンドを書く普通の作業は、これから止まる（その deny は元々そのコマンドを
  止めるために書かれたものなので、意図どおり）
- **判定の期限。** 当てる文字列が 1 本増える。層の数は深さ 4 と語数で抑える

## 4. 導入スクリプト（`scripts/ccnavi-setup.sh`）

### 4.1 引数

- `--bin` を消す。usage、冒頭の説明、`--force` の説明（「`--mode` / `--ticket-control`」だけになる）も直す
- `--bin` を渡されたら **2 で断り、何も書かない**（15 節 D-1）。文面:
  `--bin は廃止しました。振り分けの sh は .ccnavi/scripts/ccnavi-launcher.sh、実行ファイルは .ccnavi/bin/<os>-<arch>/ に固定です。`
- 綴りの検査（空・改行・絶対・`..`・`.exe`）は、値が固定になるので消す

### 4.2 定数

```sh
# CCNAVI_BIN_PATH に書く綴り。hook が起動する振り分けの sh。固定。
BIN_PATH=".ccnavi/scripts/ccnavi-launcher.sh"
# 実行ファイルの置き場。<os>-<arch>/ はこの下に並ぶ。
BUILD_ROOT=".ccnavi/bin"
DEPLOY_SCRIPTS="ccnavi-ticket.sh ccnavi-review.sh ccnavi-git.sh ccnavi-common.sh ccnavi-launcher.sh"
# 前の既定の綴り。ここを指した env は新しい綴りへ書き換える。
OLD_BIN_PATHS=".ccnavi/bin/ccnavi .claude/ccnavi/ccnavi .claude/ccnavi/ccnavi.exe"
```

`DEFAULT_BIN`・`DEPLOY_LAUNCHER`・`bin_given` を消す。`build_dir_rel` は `$BUILD_ROOT/$built`、
`runnable_there` は `$root/$BUILD_ROOT/<target>/` を見る。

### 4.3 配る

- sh は他のゲートの sh と同じ手順（`verdict` → `copy_file`）で配る。配ったあと `chmod +x`
- `launcher_ready` は「`$root/$BIN_PATH` が在る、または sh の判定が copy / replace」
- 実行ファイルは今と同じく `copy_tree` で `.ccnavi/bin/<built>/` へ
- 「まだ無いもの」は、ゲートの sh の決め打ちの並びをやめて `$DEPLOY_SCRIPTS` を回す。
  実行ファイルの行は `.ccnavi/bin/<host>/ccnavi（この機械で動く実行ファイル。この機械で build.py を回して配る）`

### 4.4 移し替え（env の書き換え）

| 今の `CCNAVI_BIN_PATH` | 新しい置き場で起動できるか | 動き |
|---|---|---|
| 無い | — | 新しい綴りを書く（今の「足りない env」と同じ） |
| `.ccnavi/scripts/ccnavi-launcher.sh` | — | 何もしない |
| 前の既定（`OLD_BIN_PATHS`） | できる | 書き換える（`forced` に足す）。`.claude/ccnavi/ccnavi` からも 1 回で新しい綴りへ |
| 前の既定 | できない | 書き換えず、理由を 1 行（今の `migrate_blocked` と同じ） |
| それ以外 | — | 書き換えない。名指しで 1 行出す。導入は止めず終了コードも変えない（`--check` では「揃っていない」に数える。今の「値が違う env」と同じ） |

「それ以外」の文面。今の文面が案内する `--bin --force` はもう無いので、この 1 行だけ分ける:

```
CCNAVI_BIN_PATH は既定でない綴り（<値>）です。書き換えていません。揃えるなら .claude/settings.json の値を .ccnavi/scripts/ccnavi-launcher.sh に直してください（前に置いた sh と実行ファイルは消していません）。
```

### 4.5 前の `.ccnavi/bin/ccnavi` を消す条件

次の**すべて**が揃ったときだけ消す。

1. 通常のファイルとして在る（ディレクトリなら触らない）
2. 書き終えたあとの `CCNAVI_BIN_PATH`（`bin_after`）がそこを指さない
3. 新しい置き場で起動できる（`new_ready`）
4. このスクリプトを打ったセッションの env の `CCNAVI_BIN_PATH` がそこを指さない。指していれば、env は
   書き換えるが sh は消さず、開き直してから打ち直すよう 1 行出す（ADR-0041 と同じ扱い）

`.ccnavi/bin/<os>-<arch>/` には触らない（新しい置き場そのもの）。`.claude/ccnavi/` の実行ファイルの片付けは
今の条件のまま（`is_old_bin` を `OLD_BIN_PATHS` で見るように揃える）。`--check` は消さずに名前を挙げる。

消すと戻せないが、消すのは導入スクリプトが前に配った、無視されている生成物だけ。前の版の導入スクリプトで配り直せる。

### 4.6 `.gitignore`

足すのは `/.ccnavi/bin/<built>/` だけ。sh は他のゲートの sh と同じく追跡する側に置き、無視しない（D-3）。
前に足した `/.ccnavi/bin/ccnavi` の行は残す。消すと、人が書いた行と見分けられない。害も無い。

### 4.7 自分自身に打ったとき

配布元と配布先が同じ（ccnavi のリポジトリ）なら、今と同じく配らない。このリポジトリの `CCNAVI_BIN_PATH` は
implement で `.claude/settings.json` を直に書くので、導入スクリプトに頼らない。

## 5. `build.py`

```
1. PyInstaller で build/dist/ccnavi に組み立てる          （今と同じ）
2. _swap で dist/ccnavi に入れ替える                      （今と同じ）
3. dist/ccnavi.target を書く                              （今と同じ）
4. dist/ccnavi を .ccnavi/bin/<target>.new へ写し、_swap で .ccnavi/bin/<target> に入れ替える（新規）
```

- 4 は `install(dist_dir, root, target)` に切り出し、テストから偽の組み立てで呼べるようにする
- 4 で落ちたら 1 を返し、`dist/ は新しい。.ccnavi/bin/<target>/ は前のまま` と言う。3 までを巻き戻さない
- **起動できない窓。** `_swap` の rename 2 回の間（数 ms）に来た hook は、sh が 127 で終わる。Claude Code は
  それを hook のエラーとして扱い、その 1 回は判定が走らない（止まらない側に倒れる）。Windows では走っている
  実行ファイルを含むディレクトリを rename できないことがあり、`_swap` の再試行（0.3 秒 × 5）で抜ける。
  窓を無くすには起動中の置き場へ上書きで写すことになり、PyInstaller の `_internal` が前後の版で混ざるので採らない
- **組み立てたあとの控え。** 実行ファイルが差し替わると、セッション開始で取った控えと大きさ・時刻が食い違い、
  開き直すまで `bin-launched` の would-restore（enable なら戻す）が出る。今の `dist/ccnavi/ccnavi` を指す形と同じで、
  新しく増える代償ではない
- **作業ツリーで組み立てたとき。** `build.py` は自分のツリーの `.ccnavi/bin/` に写す。hook が起動するのは
  ワークスペースルートの sh なので、作業ツリーで組み立てても走っている hook は変わらない（今の `dist/` と同じ）
- **このリポジトリの `.gitignore` に `/.ccnavi/bin/` が要る。** 無いと組み立ての出力が `.ccnavi/` の下の
  未追跡として git に出て、実行後の監視が報告し、作業ツリーの `worktree remove` も未追跡のファイルで止まる。
  `.gitignore` は親チケットの `allow` に無い（D-4）

## 6. `ccnavi.settings.local.json`

今もキー `bin` で `CCNAVI_BIN_PATH` を上書きできる（`settings.py` の `overrides`）。ただしこれが変えるのは
**自己保護が何を守るか**だけで、hook が何を起動するかは `.claude/settings.json` の env が決める。
「dist を直に起動する上書き」はこのファイルでは作れない。

**決定案: 残し、足しも止めもしない。** 開発中に `dist/` を直に起動したい人は、Claude Code の
`.claude/settings.local.json`（追跡しない）の env で `CCNAVI_BIN_PATH` を `dist/ccnavi/ccnavi` にできる。
この形では自己保護も `dist/` の実体を守るので、食い違わない。README の開発の節に 1 段落足す。
代償: その人の機械では振り分けの sh を通らないので、sh の不具合に気づけない。

## 7. VS Code 拡張（`core/locate.ts`）

- `DEFAULT_BINS = ["dist/ccnavi/ccnavi", ".ccnavi/scripts/ccnavi-launcher.sh", ".ccnavi/bin/ccnavi"]`。
  末尾の前の既定は、移し替える前のワークスペースのために残す
- 候補の名前が `ccnavi-launcher.sh` なら、`dirOf(dirOf(base))/bin/<target>/<name>` を探す。
  **sh そのものは返さない**（Windows では起動できない）。見つからなければ次の候補へ
- それ以外の候補は今のまま（隣の `<target>/`、次に綴りそのもの）
- 冒頭の説明、`ccnavi.ts` の通知の文面、`package.json` の `binPath` の説明、拡張の README の表を新しい順に直す
- 語（`hostTarget` / `runnableTargets`）は変えない

## 8. ゲートの sh（`ccnavi-ticket.sh` / `ccnavi-review.sh` / `ccnavi-approve.sh`）

変えない。env が sh を指せば、`-x` を見て sh へ `exec` し、sh が実体へ渡す。
env が無いときの既定の探し先（`dist/ccnavi/ccnavi` → ソース）は据え置く（16 節）。

## 9. `settings.py` / `lint.py`

- `settings.py`: `CCNAVI_BIN_PATH` の既定は持たない（ADR-0021「指定しなければ対象に入らない」のまま）。
  前の既定の綴りの一覧 `OLD_BIN_PATHS` だけを足す
- `lint.py` に 2 つ足す
  - **warn**: `CCNAVI_BIN_PATH` が `OLD_BIN_PATHS` のどれか。「前の既定の綴り。scripts/ccnavi-setup.sh を打ち直すと
    書き換わる」。判定は動いているので warn（`_old_common` と同じ扱い）
  - **error**: `CCNAVI_BIN_PATH` の指す先が在るのに実行できない（POSIX で `os.access(X_OK)` が偽。Windows では見ない）。
    hook が 126 で起動しないので、判定そのものが動いていない（D-3）

## 10. このリポジトリの切り替え

- `.claude/settings.json` の `CCNAVI_BIN_PATH` を `.ccnavi/scripts/ccnavi-launcher.sh` にする（implement。allow に明記済み）
- **順序。** env の変更が効くのはセッションを開き直したとき。開き直す前に、ワークスペースルートに次の 2 つが
  揃っていないと hook は 127 で起動しない
  1. `.ccnavi/scripts/ccnavi-launcher.sh`（staging の成果物を人が写す。100755）
  2. `.ccnavi/bin/<この機械>/ccnavi`（ワークスペースルートで `build.py` を回す）
- `COPY.md` に「写す → 組み立てる → 確かめる → 開き直す」の順を書く。確かめる手順は原因ごとに分ける
  （敵対的レビュー 4。`echo $?` だけでは 126 と 127 を区別できない）
  1. 実行ビット: `[ -x .ccnavi/scripts/ccnavi-launcher.sh ] && echo ok || echo NOT-EXECUTABLE`
  2. git のモード: `git ls-files -s .ccnavi/scripts/ccnavi-launcher.sh` の先頭が `100755`
  3. 起動: `echo '{}' | .ccnavi/scripts/ccnavi-launcher.sh; echo $?` の読み方
     - 126: 実行ビットが無い（1 に戻る）
     - 127 で `ccnavi: この機械（…）で動く実行ファイルが…` が出る: 実体が無い（`build.py` を回す）
     - 127 で ccnavi の文面が出ない: sh 自体が無いか綴りが違う
     - それ以外: 実体まで届いている

## 11. 実装の入口ごとの変更点

| 入口 | 変えること | フェーズ |
|---|---|---|
| `.ccnavi/scripts/ccnavi-launcher.sh`（新規） | 2 節の sh。`../bin/` を探す。文面の直し | staging（人が写す） |
| `scripts/ccnavi-launcher.sh` | 消す | implement |
| `scripts/ccnavi-setup.sh` | `--bin` の廃止、定数、sh を `DEPLOY_SCRIPTS` へ、移し替え、古い sh の片付け、`.gitignore`、まだ無いもの（4 節） | implement |
| `ccnavi/platformtag.py` | `LAUNCHER_NAME`、`launched_executable` の 2 つの形、冒頭の説明 | implement |
| `ccnavi/selfguard.py` | `binary_clause` の 2 つの形、説明の直し | implement |
| `ccnavi/shellread.py` | 包みを外した層を作る処理と、包むコマンド・値を取るオプションの一覧、`Reading.unwrapped`（3.5.2） | implement |
| `ccnavi/judge.py` | deny・ask とサブエージェントの禁止に外した形も当てる。allow・exempt・チケットの範囲には当てない（3.5.3） | implement |
| `ccnavi/audit.py`・`ccnavi/reasons.py` | 記録の欄 `unwrapped` と、文面の 1 行（3.5.5） | implement |
| `ccnavi/phase.py` | `forbidden` が外した形も受け取る口。承認のルールの正規表現は変えない（3.4 節、D-8） | implement |
| `ccnavi/settings.py` | `OLD_BIN_PATHS` | implement |
| `ccnavi/lint.py` | 前の既定の warn、実行できない sh の error | implement |
| `build.py` | `install()` で `.ccnavi/bin/<target>/` へ写す | implement |
| `vscode-extension/ccnavi-board/src/core/locate.ts` | 7 節 | implement |
| `vscode-extension/ccnavi-board/src/ccnavi.ts`・`package.json`・`README.md` | 文面 | implement / docs |
| `.claude/settings.json` | `CCNAVI_BIN_PATH` | implement |
| `.gitignore` | `/.ccnavi/bin/` | 人が直す（staging の `COPY.md`。D-4） |
| `tests/test_launcher.py` | `LAUNCHER` の綴り、12 節 L1–L5 | acceptance |
| `tests/test_setup.py` | `--bin` を使う 10 か所（関数 9 本とループ 1 か所）を 12 節の表のとおり直し、配置と移し替えを 12 節 S1–S14 に | acceptance |
| `tests/test_selfguard.py` | 12 節 G1–G5（今の `launcher_layout` は前の形として残す） | acceptance |
| `tests/test_repo_rules.py` | 12 節 A1–A4、W1–W4、W6 | acceptance |
| `tests/test_shellread.py`（無ければ新規） | 12 節 U1–U3 | acceptance |
| `tests/test_phase.py` | 12 節 W5（サブエージェントの禁止） | acceptance |
| `tests/test_audit.py`（無ければ新規） | 12 節 W7（記録と文面） | acceptance |
| `tests/test_lint.py` | 12 節 N1–N2 | acceptance |
| `tests/test_config_union_guard.py` | 偽の配布元に sh を置く綴りを `.ccnavi/scripts/` に | acceptance |
| `tests/test_build.py`（新規） | 12 節 B1 | acceptance |
| `vscode-extension/ccnavi-board/test/locate.test.ts` | 12 節 E1–E3 | acceptance |
| `README.md` | 「実行ファイルとルールを配る」の図と表、env の表の `CCNAVI_BIN_PATH`、`--bin` の記述、6 節の開発の段落 | docs |
| `ccnavi.md` | §4.6 の表と本文、§8.1 の表の行、§8.2 の説明 | docs |
| `requirements.md` | 14 節 | docs |
| `docs/adr/0043-launcher-in-scripts.md`（新規）と `docs/adr/README.md` の一覧 | 13 節 | docs |
| `.claude/skills/ccnavi-config/SKILL.md` | `.ccnavi/bin/ccnavi` の綴り（46–47 行） | 人が直す（staging の `COPY.md`。D-6） |

`tools/gitlab/probe_gitlab.py`（`dist/` の実体を直に使う）と `HANDOVER.md`（過去の記録）は変えない。

## 12. 受入テストで押さえる振る舞い

**振り分けの sh（`test_launcher.py`）**

- L1 `.ccnavi/scripts/ccnavi-launcher.sh` は `../bin/<host>/ccnavi` を起動し、引数・標準入力・終了コードをそのまま渡す
- L2 arm64 の macOS と Windows は、自分向けが無いときだけ x86_64 へ回る
- L3 隣（`.ccnavi/scripts/<host>/`）に置いた実体は起動しない
- L4 どこにも無ければ 127。文面に `<os>-<arch>` と探した場所が出る
- L5 `launched_executable` は、名前が `ccnavi-launcher.sh` なら `../bin/`、それ以外は隣を探す

**導入スクリプト（`test_setup.py`）**

- S1 `CCNAVI_BIN_PATH` に `.ccnavi/scripts/ccnavi-launcher.sh` を書く
- S2 sh を `.ccnavi/scripts/` へ配り、実行ビットを付ける
- S3 実行ファイルを `.ccnavi/bin/<built>/` へ配る
- S4 `--bin` は 2 で断り、何も書かない
- S5 `.gitignore` に足すのは `/.ccnavi/bin/<built>/` だけ
- S6 前の既定 `.ccnavi/bin/ccnavi` を新しい綴りへ書き換え、揃っていれば `.ccnavi/bin/ccnavi` を消す。`<os>-<arch>/` は残す
- S7 `.claude/ccnavi/ccnavi` からも 1 回で新しい綴りへ書き換える
- S8 新しい置き場が揃わなければ、書き換えも消しもしない
- S9 打ったセッションの env が `.ccnavi/bin/ccnavi` なら、書き換えるが消さず、開き直しを促す
- S10 既定でない綴りは書き換えずに名指しし、終了コードは 0（`--check` は 1）。前の sh と実体に触らない
- S11 `--check` は古い sh を消さずに名前を挙げる
- S12 「まだ無いもの」に sh と `.ccnavi/bin/<host>/ccnavi` が並ぶ
- S13 配布元に `.ccnavi/scripts/ccnavi-launcher.sh` が無ければ「配布元に無くて配れないもの」に出る
- S14 配布先の sh の実行ビットが落ちていれば、配らない回（keep）でも付け直す（D-3）

今の `tests/test_setup.py` で `--bin` を使うところ（`grep -n '"--bin"'` と `grep -n 'def test_.*bin'` で洗い出した 10 か所。
前の版の「6 本」は数え違い。敵対的レビュー 3）の扱い:

| 今のテスト | 扱い |
|---|---|
| `test_bin_path_points_at_the_launcher` | S1 に書き直す |
| `test_refuses_a_bin_spelled_with_exe` / `_a_newline_in_the_bin_path` / `_an_empty_bin_path` / `_a_bin_path_that_climbs_out_of_the_project` / `_an_absolute_bin_path` | 消す。S4 の 1 本が `--bin` そのものを断る |
| `test_follows_the_bin_option_for_where_it_puts_the_executable` | S3 に書き直す（置き場は固定） |
| `test_does_not_move_a_named_bin_without_force` | 消す（名指しの `--bin` が無くなる）。既定でない綴りを書き換えないことは S10 が見る |
| `test_leaves_the_old_directory_alone_when_bin_lives_there` | 消す（sh を `.claude/ccnavi/` に置く形が作れなくなる） |
| `test_does_not_ignore_the_rules_that_sit_next_to_the_executable` | S5 に吸収する。足すのが `/.ccnavi/bin/<built>/` だけで、`.ccnavi/` や `.ccnavi/common/` を無視しないことを S5 で確かめる |
| `test_refuses_an_option_without_its_value` のループ（`--mode` / `--bin`） | `--bin` を外す。`--bin` は値の有無に依らず S4 で断る |

**自己保護（`test_selfguard.py`）**

- G1 `CCNAVI_BIN_PATH` が `.ccnavi/scripts/ccnavi-launcher.sh` なら、`.ccnavi/bin/<host>/ccnavi` をセッション開始で控え、差し替えを戻す
- G2 `.ccnavi/bin/<host>/_internal/x` への Write は `builtin-guard-binary` で止まる（`CCNAVI_PROJECT_HOME` を動かしていても）
- G3 `rm -rf .ccnavi/bin/linux-x86_64` は止まる
- G4 sh を `tools/scripts/ccnavi-launcher.sh` に置いたとき、`tools/scripts/linux-x86_64/x` は `builtin-guard-binary` に当たらず、`tools/bin/linux-x86_64/x` は当たる
- G5 前の形（`tools/bin/ccnavi` と隣の `<os>-<arch>/`）は今のテストのまま通る
- G6 `CCNAVI_BIN_PATH=scripts/ccnavi-launcher.sh`（浅い綴り）でも、`bin/<host>/_internal/x` への Write は `builtin-guard-binary` で止まり、
  `launched_executable` が控える場所と同じ置き場を指す（3.2・3.3 節の条件が揃っていること）

**承認の経路（`test_repo_rules.py`）**

- A1 次を `builtin-guard-ticket-approval` で止める（`<sh>` は `.ccnavi/scripts/ccnavi-launcher.sh`）:
  `<sh> --approve --yes x`、`sh <sh> …`、`bash <sh> …`、`/bin/sh <sh> …`、`env sh <sh> …`、`env FOO=1 sh <sh> …`、
  `command sh <sh> …`、`exec sh <sh> …`、`nohup sh <sh> …`、`zsh <sh> …`、`dash <sh> …`、`sh -x <sh> …`、
  `cd /tmp && sh <sh> …`、`sh <sh> ticket start x`、`sh .ccnavi/bin/ccnavi …`（前の形）、`sh .ccnavi/bin/<host>/ccnavi …`、`sh ccnavi …`
- A2 読み切れない形（`sh -c '<sh> --approve --yes x'`、`bash -lc "…"`、`. <sh> …`、`source <sh> …`）も同じルールで止まり、
  コードが `DENY_TICKET_APPROVAL_CLI` になる（今は ask の `PARSE_UNCERTAIN` に落ちる）
- A3 次は承認のルールに当たらない: `<sh> --approve --preview x`、`sh <sh> --approve --preview x`、`cat <sh>`、
  `grep -n 'ccnavi --approve --yes' README.md`、`sed -n 1,20p <sh>`、`sh .ccnavi/scripts/ccnavi-ticket.sh start x`、
  `sh .ccnavi/scripts/ccnavi-review.sh request --phase 1 --body-file x.md`、`echo <sh>`、
  `git commit -m 'docs: ccnavi --approve --yes の説明'`（`raw-git` には当たる）、
  引用しない `echo ccnavi --approve --yes x`、`grep -rn ccnavi --yes docs`、`git log --grep ccnavi --yes`
- A4 `sudo -u me sh -c 'ccnavi --approve --yes x'` と `find . -name x -exec ccnavi --approve --yes {} \;` も、外した形で
  `builtin-guard-ticket-approval` に当たって止まる（3.5 節。B の案で残っていた 2 形）

**包みを外した形（`test_shellread.py`・`test_repo_rules.py`・`test_phase.py`）**

- U1 3.5.2 の表の形ごとに、外した層が表のとおりになる（途中の層も並ぶ。深さ 4 で止まる）
- U2 読み切れない形（`sh -c`・`bash -lc`・`eval`・`source`・`.`・`xargs`・`find -exec`）でも層を作り、`degraded` と理由は残る
- U3 閉じない引用・閉じないヒアドキュメントでは層を作らない
- W1 組み込みの守りが、包み 13 と `sh -c`・`eval`・`xargs`・`find -exec` で包んだ形でも deny になる
  （設定ファイルの 6 動詞、チケットの状態、承認、`env sh .ccnavi/scripts/ccnavi-approve.sh`）
- W2 利用者の先頭に固定したルール（`prefer-webfetch`）も、`env curl …` で当たる（ask）
- W3 外した形は allow に当てない: `sudo -u me cat /etc/hosts` は `prefer-read-grep` に当たらない
- W4 外した形は exempt に当てない: レビュー待ちのゲートの中で、`env sh .ccnavi/scripts/ccnavi-review.sh check --phase 1` は通らない
- W5 サブエージェントの禁止: `env sh …ccnavi-ticket.sh start x`、`/bin/sh …ccnavi-ticket.sh done x`、
  `sh -c 'sh …ccnavi-review.sh request --phase 1'` が禁止される
- W6 3.5.4 の普通の作業 27 形で、deny / ask に当たるルールが元の形のときから増えない
- W7 外した形で当たったとき、記録に `unwrapped` の層が残り、文面に「包みを外した形」の 1 行が出る。元の形で当たったときは出ない

**設定lint（`test_lint.py`）**

- N1 `CCNAVI_BIN_PATH` が前の既定の綴りなら warn
- N2 指す先が在るのに実行できなければ error（POSIX だけ。Windows では skip）

**組み立て（`test_build.py`）**

- B1 `install()` は偽の `dist/ccnavi/` を `.ccnavi/bin/<target>/` へ写し、前の版にだけあったファイルを残さない

**VS Code 拡張（`locate.test.ts`）**

- E1 env が `.ccnavi/scripts/ccnavi-launcher.sh` なら `.ccnavi/bin/<host>/ccnavi[.exe]` を返す
- E2 sh そのものは返さない。実体が無ければ次の候補へ進む
- E3 前の `.ccnavi/bin/ccnavi` の形は今のまま隣を探す

**順序について。** 受入テストが見る `.ccnavi/scripts/ccnavi-launcher.sh` は、staging を人が写すまで作業ツリーに無い。
L1–L4 と S2・S13 は、写す前は `wip/design/scripts/ccnavi-launcher.sh` を名指しで読んで確かめる
（前例 `sh-ws-root` の `COPY.md` と同じ）。

## 13. ADR-0043 の骨子

**題:** 振り分けの sh は `.ccnavi/scripts/` に、実行ファイルは `.ccnavi/bin/<os>-<arch>/` に固定する

**状態:** 採用。ADR-0041 は「置き換え（ADR-0043）」にする（決定の置き場の部分を覆すため。機械ごとに並べる・
sh が選ぶ・語を揃える、は引き継ぐ）

**状況:** 1 節の「前」の列。原本と配布先で sh の名前と場所が変わる。`.ccnavi/bin/` に sh が混ざる。
このリポジトリの hook が sh を通らない

**決定:** 1 節の表。`--bin` の廃止。移し替えは env が前の既定の綴りのときだけ、既定でない綴りは報告だけ

**理由:** `.ccnavi/scripts/` は「原本と配布先が同じ綴り」の置き場で、振り分けの sh はその定義に当てはまる。
置き場が固定なら、sh は自分の位置から実体を一意に決められ、`--bin` と「隣を探す」が要らなくなる。
このリポジトリでも同じ経路を通るので、sh の不具合に自分で気づける

**得たもの・失ったもの:**

- 得たもの: 原本と配布先で綴りが同じ。`.ccnavi/bin/` は実行ファイルだけになる。このリポジトリも Windows と WSL で同じ hook が起動する
- 失ったもの: このリポジトリでもツール呼び出しのたびに sh の起動と `uname` 1 回ぶんが乗る
- 失ったもの: 組み立てたあと `.ccnavi/bin/<os>-<arch>/` に写すまで、hook は新しい実行ファイルを起動しない。`build.py` が ccnavi ディレクトリに書く
- 失ったもの: `--bin` で置き場を動かしていた配布先は、手で戻す必要がある
- 失ったもの: 自己保護の対象が `.ccnavi/scripts/` と `.ccnavi/bin/` の 2 か所に分かれ、`binary_clause` と `launched_executable` が sh の名前で 2 つの形を持つ
- 失ったもの: sh を追跡すると、実行ビットを git のモードで運ぶことになる（D-3）

**採らなかった案:** 自分に導入スクリプトを当てる（ビルドのたびに 1 手増える）。sh を `.ccnavi/bin/` に残して
名前だけ揃える（sh と実行ファイルの混在が残る）。hook の `command` を `sh "…"` にする（D-3 の案 C）

### ADR-0044 の骨子（判定の当て方の変更は ADR-0043 と分ける）

**題:** シェルのコマンドは、包みを外した形にも止める側のルールだけを当てる

**状況:** 3.5.1 の表。組み込みの守りとサブエージェントの禁止が先頭の語に固定されていて、`env`・`sudo`・`sh -c` などで包むと当たらない

**決定:** shellread が包みを外した層を作り、deny・ask・サブエージェントの禁止に元の形と一緒に当てる。allow・exempt・チケットの範囲には当てない

**理由:** 包みの問題はルールの書き方ではなく、読みの側の不足。ルールごとに包みを書かせると、組み込みも利用者のルールも同じ穴を 1 本ずつ塞ぐことになる。
止める側にだけ足すので、層の読み違いは緩む方向に効かない

**得たもの・失ったもの:**

- 得たもの: 組み込み・利用者のルール・サブエージェントの禁止で同じ判断になる。ルールを書く人が包みを意識しなくてよい
- 失ったもの: 包むコマンドと値を取るオプションの一覧を shellread が持ち、足すまで一覧に無い包みは抜ける
- 失ったもの: 当てる文字列が 1 本増え、記録の欄が 1 つ増える
- 失ったもの: 包みの後ろに書いた、利用者の deny に当たるコマンドが止まるようになる（意図どおりだが、今まで通っていた）

**採らなかった案:** 承認のルールだけにラッパの一覧を持たせる（B。同じ穴が他の守りに残る）。語の位置を問わずに当てる（A。止めすぎる）。
外した形を allow にも当てる（`sudo -u me cat …` が通るようになる）

## 14. requirements.md の候補（文面だけ）

番号は docs フェーズで空きを取る。

- **新規（HKS）** 常時 | 導入スクリプトは、hook が起動する振り分けのスクリプトと実行ファイルを、それぞれ決まった置き場に置き、置き場を引数で動かせないこと
- **新規（MLT）** 事象 | 前の既定の置き場を指す実行ファイルの位置の設定を見つけたとき、導入スクリプトは、新しい置き場で起動できる場合に限りそれを書き換え、既定でない位置は書き換えずに名指しすること
- **REQ-SLF-07 の説明文に足す** 位置が振り分けのスクリプトを指すとき、そのスクリプトが起動する実行ファイルの置き場も守る対象に含める。スクリプトだけを守ると、実体を差し替えても判定が入れ替わったことに気付かない
- **新規（PRE）** 常時 | シェルのコマンドが他のコマンドに包まれて起動される場合（環境変数の代入、`env`・`sudo`・`timeout` などの包み、`sh -c` の文字列、`xargs`・`find -exec` の後ろ）、ccnavi は、拒否と確認のルールとサブエージェントの禁止を、包みを外した形にも当てること
- **新規（PRE）** 常時 | ccnavi は、包みを外した形に、許可のルールとゲートの中で通す形を当てないこと
- **新規（PRE）** 事象 | 包みを外した形でルールに当たったとき、ccnavi は、その層を記録と返す文面に残すこと

## 15. 人が決めたこと

**決定（利用者、2026-09-13）:** D-1・D-2・D-3・D-5 は推す案。D-4 と D-6 は人が直す。
改版で変えられるのは `plan` と `feedback` だけで（README「改版」）、`allow` には足せないため。
直し方（`.gitignore` に 1 行、`SKILL.md` の 46–47 行）は staging の `COPY.md` に書き、人が `launcher-scripts` の
ブランチでコミットする。

**launcher-scripts-03 で足したもの（まだ決まっていない。このレビューで決める）:** D-7（浅い綴りの相対のシェル書き込み）と
D-8（承認の経路の塞ぎ方）。D-5 は「塞ぐ」ことは決まっているが、前の版が推した塞ぎ方では塞ぎきれていなかったので、塞ぎ方を D-8 に分けた。

**利用者の決定（2026-09-13、launcher-scripts-03 のレビューの途中）:** D-7 は受け入れる（守らない）。D-8 はいったん B としたが、
組み込みの守りとサブエージェントの禁止にも同じ穴があることを実測で確かめ（3.5.1）、利用者の決定で「全部このチケットで直す」に
なった。launcher-scripts-04 で D-8 を「3.5 節の外した形で塞ぐ」に書き直し、D-9・D-10 を足した（まだ決まっていない。このレビューで決める）。

| # | 何を決めるか | 推す案 | 得るもの | 失うもの | 代案 |
|---|---|---|---|---|---|
| D-1 | `--bin` を渡されたとき | 2 で断る | 名指しを黙って無視しない（打った人が指した場所に置いたつもりで進まない） | `--bin .ccnavi/bin/ccnavi` を書いた手順書や CI が止まる | 受けて無視し 1 行出す |
| D-2 | 名前が `ccnavi-launcher.sh` でない `CCNAVI_BIN_PATH` の「隣を探す」形 | 残す | 打ち直す前のワークスペースでも隣の実体を守り続ける | `binary_clause` と `launched_executable` が 2 つの形を持ち続ける | 消す（打ち直すまで隣の実体の `builtin-guard-binary` と控えが外れる。組み込みの `.ccnavi` の守りは残る） |
| D-3 | 配布先での sh の実行ビット | A: 追跡し、導入スクリプトが毎回 `chmod +x`、lint が error | ゲートの sh と同じ扱いで 1 つの置き場にまとまる | Windows（`core.filemode=false`）で最初に足すとモード 100644 で入り、別の機械で clone した直後は導入スクリプトを打つまで hook が 126 で起動しない（実行ファイルも無視されているので、どのみち打つ手順ではある） | B: 配布先では sh を無視し、機械ごとに配る（同じ置き場に追跡と無視が混ざる）／C: hook の `command` を `sh "…"` にする（実行ビットが要らなくなるが、登録済みの hook の書き換えと `looks` の見分けが要り、範囲が広がる） |
| D-4 | このリポジトリの `.gitignore` に `/.ccnavi/bin/` | **人が 1 行足す（決定）** | チケットが増えない | 人の手が 1 つ増える | 親チケットの allow に足す（改版では足せないので、別の親を出すか親を出し直すことになる） |
| D-5 | 承認の経路の間接起動を塞ぐか | 今回入れる（決定）。塞ぎ方は D-8 | 今の既定の配置にもある穴を、`.sh` の名前で打ちやすくなる前に塞ぐ | チケットの「変える場所」に無い変更が 1 つ増える。前の版が推した `((sh\|bash)\s+)?` では `/bin/sh`・`env`・`zsh`・`sh -c`・`source` などが通り、塞ぎきれていなかった（敵対的レビュー 1） | 別チケットに分ける（分けるまで間接起動は ask に落ちるだけで、承認のルールでは止まらない） |
| D-7 | 浅い綴り（`scripts/ccnavi-launcher.sh`、`ccnavi-launcher.sh`）での相対のシェル書き込み | **受け入れて書き残す（決定）**。導入スクリプトはその綴りを作らない | ルールが素直なまま。どのディレクトリの `bin/linux-x86_64` にも当たる形を持ち込まない | 既定でない浅い綴りで `rm -rf bin/linux-x86_64` が `builtin-guard-binary` に当たらない（実行後の控えと復元は効く）。前の形にも同じ限界がある（`CCNAVI_BIN_PATH=ccnavi` で `rm -rf linux-x86_64` が通ることを今の実装で確かめた） | `bin/<os>-<arch>` を位置を問わず当てる（2 段は塞がるが当たりすぎる。1 段の `../bin/` は塞がらない）／浅い綴りを lint で warn する（止めはしないが気づける。lint の項目が 1 つ増える） |
| D-8 | 承認の経路の塞ぎ方（3.4 節） | 3.5 節の外した形で塞ぐ（承認のルールの正規表現は変えない） | 承認だけでなく、組み込みの守り・利用者のルール・サブエージェントの禁止の同じ穴が一緒に塞がる。B で残った 2 形も止まる | 判定の仕組みの変更で、すべての Bash の判定に効く。包むコマンドの一覧を shellread が持つ | B: ラッパの並び + 引用（承認だけ。23/25。他の守りの穴が残る）／A: 語の位置を問わない（止めすぎる） |
| D-9 | 包むコマンドと値を取るオプションの一覧の置き場 | shellread の組み込み | 利用者のルールファイルから消せない（守りの根拠を守られる側に置かない、ADR-0021 と同じ向き） | 一覧に足すには ccnavi を作り直す | `rules.yml` に足せる欄を作る（利用者が足せるが、消すこともできる） |
| D-10 | 外した形で当たったことを記録と文面に残すか | 残す（記録の欄 `unwrapped`、文面の 1 行） | 元の形だけを見た読み手に、どこが当たったのかが分かる。記録を数えれば、包みで当たった件数が分かる | 記録の欄と文面が 1 つずつ増える。`--test` の JSON の形が変わる | 残さない（文面が短いが、`env rm …` がなぜ止まったのか読み手に分からない） |
| D-6 | `.claude/skills/ccnavi-config/SKILL.md` の綴り | **人が直す（決定）** | 範囲を広げない | 人の手が 1 つ増える | 親チケットの allow に足す |

## 16. 今回入れないもの

- ゲートの sh の、env が無いときの既定の探し先（`dist/ccnavi/ccnavi` → ソース）を変えること。配布先では env の無い
  端末から打つと「実行ファイルが無い」で落ちる。今と同じ
- sh のシンボリックリンクを解くこと
- 実行ファイルを onefile にする形、`CCNAVI_BIN_PATH` という env の名前の変更（親チケットのとおり）
- `tools/gitlab/probe_gitlab.py` の実体の探し方
