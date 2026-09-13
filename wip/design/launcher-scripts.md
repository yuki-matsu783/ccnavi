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
`sys.executable`、前の形のワークスペース）のために残す。**緩めるところは無い。**

### 3.2 `binary_clause` の新しい形

```python
# platformtag.py
LAUNCHER_NAME = "ccnavi-launcher.sh"

# selfguard.py
def binary_clause(bin_path: str) -> str:
    parts = [...]                      # 今と同じ分解
    if len(parts) >= 3 and parts[-1] == platformtag.LAUNCHER_NAME:
        home, scripts, name = (re.escape(p) for p in parts[-3:])
        return rf"{home}[\\/](?:{scripts}[\\/]{name}|bin[\\/]{_BUILD_DIR}(?:[\\/][^\x00]*)?)"
    # それ以外は今の形（末尾 2 要素と、隣の組み立ての置き場）
```

- **sh かどうかは名前で決める。** ファイルを読まないので判定の期限に効かず、在るかどうかに依らず同じ綴りを返す。
  代償: 別の名前で置いた sh は前の形で扱われ、その sh の `../bin/` は `builtin-guard-binary` から外れる。
  導入スクリプトはもうその形を作らない
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

`selfguard.targets` は変えない。sh（数 KB）も今と同じく heavy のまま控える。

### 3.4 承認の経路（`builtin-guard-ticket-approval`）

`phase.ticket_approval_rule` を今の実装に当てて確かめた（`--approve --yes x` を付けた綴り）。

| `CCNAVI_BIN_PATH` | 直に起動 | `/ws/` から起動 | `sh <綴り>` | `bash <綴り>` |
|---|---|---|---|---|
| `.ccnavi/bin/ccnavi`（今の既定） | 止まる | 止まる | **通る** | **通る** |
| `.ccnavi/scripts/ccnavi-launcher.sh`（新規） | 止まる | 止まる | **通る** | **通る** |
| `dist/ccnavi/ccnavi` | 止まる | 止まる | **通る** | **通る** |

`sh`/`bash` を前に付けた形は、今の既定の配置でもすでに通っている。名前に `.sh` が付くと
`sh .ccnavi/scripts/ccnavi-launcher.sh …` が自然な打ち方になるので、ここで塞ぐ。

変更: `launcher` の綴りの前に `((sh|bash)\s+)?` を許す。**止まる側への変更**で、チケットの「変える場所」には
書かれていない（15 節 D-5）。`--approve --preview` を通す既存の除外はそのまま効く。

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
- `COPY.md` に「写す → 組み立てる → 開き直す」の順と、確かめる 1 行
  （`echo '{}' | .ccnavi/scripts/ccnavi-launcher.sh; echo $?` が 127 以外）を書く

## 11. 実装の入口ごとの変更点

| 入口 | 変えること | フェーズ |
|---|---|---|
| `.ccnavi/scripts/ccnavi-launcher.sh`（新規） | 2 節の sh。`../bin/` を探す。文面の直し | staging（人が写す） |
| `scripts/ccnavi-launcher.sh` | 消す | implement |
| `scripts/ccnavi-setup.sh` | `--bin` の廃止、定数、sh を `DEPLOY_SCRIPTS` へ、移し替え、古い sh の片付け、`.gitignore`、まだ無いもの（4 節） | implement |
| `ccnavi/platformtag.py` | `LAUNCHER_NAME`、`launched_executable` の 2 つの形、冒頭の説明 | implement |
| `ccnavi/selfguard.py` | `binary_clause` の 2 つの形、説明の直し | implement |
| `ccnavi/phase.py` | 承認の綴りに `sh`/`bash` の前置を許す（D-5） | implement |
| `ccnavi/settings.py` | `OLD_BIN_PATHS` | implement |
| `ccnavi/lint.py` | 前の既定の warn、実行できない sh の error | implement |
| `build.py` | `install()` で `.ccnavi/bin/<target>/` へ写す | implement |
| `vscode-extension/ccnavi-board/src/core/locate.ts` | 7 節 | implement |
| `vscode-extension/ccnavi-board/src/ccnavi.ts`・`package.json`・`README.md` | 文面 | implement / docs |
| `.claude/settings.json` | `CCNAVI_BIN_PATH` | implement |
| `.gitignore` | `/.ccnavi/bin/` | 人が直す（staging の `COPY.md`。D-4） |
| `tests/test_launcher.py` | `LAUNCHER` の綴り、12 節 L1–L5 | acceptance |
| `tests/test_setup.py` | `--bin` 系の 6 本を「断る」1 本に、配置と移し替えを 12 節 S1–S14 に | acceptance |
| `tests/test_selfguard.py` | 12 節 G1–G5（今の `launcher_layout` は前の形として残す） | acceptance |
| `tests/test_repo_rules.py` | 12 節 A1–A2 | acceptance |
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

**自己保護（`test_selfguard.py`）**

- G1 `CCNAVI_BIN_PATH` が `.ccnavi/scripts/ccnavi-launcher.sh` なら、`.ccnavi/bin/<host>/ccnavi` をセッション開始で控え、差し替えを戻す
- G2 `.ccnavi/bin/<host>/_internal/x` への Write は `builtin-guard-binary` で止まる（`CCNAVI_PROJECT_HOME` を動かしていても）
- G3 `rm -rf .ccnavi/bin/linux-x86_64` は止まる
- G4 sh を `tools/scripts/ccnavi-launcher.sh` に置いたとき、`tools/scripts/linux-x86_64/x` は `builtin-guard-binary` に当たらず、`tools/bin/linux-x86_64/x` は当たる
- G5 前の形（`tools/bin/ccnavi` と隣の `<os>-<arch>/`）は今のテストのまま通る

**承認の経路（`test_repo_rules.py`）**

- A1 `sh .ccnavi/scripts/ccnavi-launcher.sh --approve --yes x` と `bash …` を止める。`sh … --approve --preview` は通す
- A2 `sh .ccnavi/bin/ccnavi --approve --yes x`（前の形）も止まる

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

## 14. requirements.md の候補（文面だけ）

番号は docs フェーズで空きを取る。

- **新規（HKS）** 常時 | 導入スクリプトは、hook が起動する振り分けのスクリプトと実行ファイルを、それぞれ決まった置き場に置き、置き場を引数で動かせないこと
- **新規（MLT）** 事象 | 前の既定の置き場を指す実行ファイルの位置の設定を見つけたとき、導入スクリプトは、新しい置き場で起動できる場合に限りそれを書き換え、既定でない位置は書き換えずに名指しすること
- **REQ-SLF-07 の説明文に足す** 位置が振り分けのスクリプトを指すとき、そのスクリプトが起動する実行ファイルの置き場も守る対象に含める。スクリプトだけを守ると、実体を差し替えても判定が入れ替わったことに気付かない
- **承認の経路の要求の説明文に足す（該当する REQ を docs で引く）** 実行ファイルをシェルの引数として起動する形（`sh <位置>`）も、直に起動する形と同じく止めること

## 15. 人が決めたこと

**決定（利用者、2026-09-13）:** D-1・D-2・D-3・D-5 は推す案。D-4 と D-6 は人が直す。
改版で変えられるのは `plan` と `feedback` だけで（README「改版」）、`allow` には足せないため。
直し方（`.gitignore` に 1 行、`SKILL.md` の 46–47 行）は staging の `COPY.md` に書き、人が `launcher-scripts` の
ブランチでコミットする。

| # | 何を決めるか | 推す案 | 得るもの | 失うもの | 代案 |
|---|---|---|---|---|---|
| D-1 | `--bin` を渡されたとき | 2 で断る | 名指しを黙って無視しない（打った人が指した場所に置いたつもりで進まない） | `--bin .ccnavi/bin/ccnavi` を書いた手順書や CI が止まる | 受けて無視し 1 行出す |
| D-2 | 名前が `ccnavi-launcher.sh` でない `CCNAVI_BIN_PATH` の「隣を探す」形 | 残す | 打ち直す前のワークスペースでも隣の実体を守り続ける | `binary_clause` と `launched_executable` が 2 つの形を持ち続ける | 消す（打ち直すまで隣の実体の `builtin-guard-binary` と控えが外れる。組み込みの `.ccnavi` の守りは残る） |
| D-3 | 配布先での sh の実行ビット | A: 追跡し、導入スクリプトが毎回 `chmod +x`、lint が error | ゲートの sh と同じ扱いで 1 つの置き場にまとまる | Windows（`core.filemode=false`）で最初に足すとモード 100644 で入り、別の機械で clone した直後は導入スクリプトを打つまで hook が 126 で起動しない（実行ファイルも無視されているので、どのみち打つ手順ではある） | B: 配布先では sh を無視し、機械ごとに配る（同じ置き場に追跡と無視が混ざる）／C: hook の `command` を `sh "…"` にする（実行ビットが要らなくなるが、登録済みの hook の書き換えと `looks` の見分けが要り、範囲が広がる） |
| D-4 | このリポジトリの `.gitignore` に `/.ccnavi/bin/` | **人が 1 行足す（決定）** | チケットが増えない | 人の手が 1 つ増える | 親チケットの allow に足す（改版では足せないので、別の親を出すか親を出し直すことになる） |
| D-5 | 承認の経路の `sh`/`bash` 前置 | 今回入れる | 今の既定の配置にもある穴を、`.sh` の名前で打ちやすくなる前に塞ぐ | チケットの「変える場所」に無い変更が 1 つ増える | 別チケットに分ける（分けるまで `sh .ccnavi/scripts/ccnavi-launcher.sh --approve --yes` が通る） |
| D-6 | `.claude/skills/ccnavi-config/SKILL.md` の綴り | **人が直す（決定）** | 範囲を広げない | 人の手が 1 つ増える | 親チケットの allow に足す |

## 16. 今回入れないもの

- ゲートの sh の、env が無いときの既定の探し先（`dist/ccnavi/ccnavi` → ソース）を変えること。配布先では env の無い
  端末から打つと「実行ファイルが無い」で落ちる。今と同じ
- sh のシンボリックリンクを解くこと
- 実行ファイルを onefile にする形、`CCNAVI_BIN_PATH` という env の名前の変更（親チケットのとおり）
- `tools/gitlab/probe_gitlab.py` の実体の探し方
