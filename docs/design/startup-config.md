---
type: design
title: 4. 起動と設定
description: hook としての起動の形、イベント、設定の解決、動作モード、応答の形、導入と配布、記録
tags: [design-doc, hook, config]
keywords: [起動, hook, イベント, 設定, 動作モード, dry-run, 導入, 配布, 記録]
---

[設計書の入口に戻る](../design.md)

## 4. 起動と設定

### 4.1 起動の形

実行ファイルは 1 本で、`.claude/settings.json` の hook として 7 つのイベントに同じコマンドで登録する。
標準入力の JSON（payload）の `hook_event_name` でイベントを見分け、標準出力に応答を返して終わる。

| 起動 | 挙動 |
|---|---|
| hook から（payload あり） | イベントごとの手順（4.2）。終了コード 0 は通す、2 は止める・差し戻す |
| hook の入力なし | 使い方を標準エラーに出して終了コード 1 |
| 診断用のオプション（`--lint` `--test` `--test-samples` `--explain`） | payload を読まない。ユーザと VS Code 拡張が使う（10 章） |
| `--agree` / `--reviewed` / `ticket …` / `review …` | チケット制御の操作。ユーザが端末で打つか、保護済みスクリプトが呼ぶ（9 章） |

ワークスペースルートは `--root` → 環境変数 `CLAUDE_PROJECT_DIR` → 作業ディレクトリから上へ辿って
`.claude/` を持つ最初のディレクトリ、の順で決める。

配布物は PyInstaller の onedir。実行時の依存は PyYAML 1 本で、読むのは `safe_load` に限る。
常駐はせず、呼び出しごとに起動して終わる。

### 4.2 hook のイベント

| イベント | すること | 応答 |
|---|---|---|
| `SessionStart` | コアファイルのバックアップを取る（実行ファイルはここだけ）。`additionalContextOnce` の記憶を捨てる（`source=startup` なら `match: Stop` の数えも。6.6）。サブエージェントでなければ、ccnavi が前提にしている作業の決まり（ワークツリー・`ccnavi-git.sh`・`scratchpad/`・プロジェクトへの `cd`・サブエージェント・合意の要る変更。チケット制御が有効なら `ccnavi-ticket.sh status` も）を 1 行ずつ渡し（ワークツリーと git の行は、共通レイヤーと自身のレイヤーの deny に `workspace-root`・`raw-git` があるときだけ）、索引の案内を出す回は `--docs` で詳しく引くよう添える（REQ-SES-06）。チケット制御が有効なら、直接作業とチケット作業の使い分けをモデルに渡す（9.1）。cwd がプロジェクトの中なら、そのプロジェクトのスキルの目録を渡す（11.13）。サブエージェントでなければ、ワークスペースとプロジェクトの md の frontmatter の索引を差分で新しくし（3 秒と hook の判定の期限の残りの小さいほうまで）、`--docs` での引き方と、索引の対象外にしたツリーと書き換えなかった index.jsonl を渡す（10） | `additionalContext` |
| `UserPromptSubmit` | 保護領域にいまある変更を記録し、ターンの基準にする（7.4）。このセッションがまだ知らない承認を 1 度伝える（9.4）。依頼文に issue・MR の指定があれば、紐づくブランチを探してユーザに確かめる指示を渡す（9.13） | 無し。伝えることがあれば `additionalContext` |
| `PreToolUse` | 呼び出しを判定する（6 章）。コアファイルをバックアップする（8 章）。cwd がプロジェクトの中に入った最初の回に、そのスキルの目録を添える（11.13） | `permissionDecision` と `additionalContext` |
| `PostToolUse` | コアファイルをバックアップと突き合わせて戻す。作業ツリーを git で読み、保護領域の変更を報告し、設定に従って戻す（7 章）。チケットの状態を承認済みチケットへ書き出し、フェーズの終わりを告げる（9.8）。サブエージェントが差し戻しを無視して終わったことを親に言う | 終了コード 2 と標準エラー、または `additionalContext` |
| `Stop` | このターンで変わった保護領域をユーザへ報告する（7.4）。メインエージェントの cwd のワークツリーのチケットが、作業を終えたように見えるのに `finish` されていなければ、1 回だけ止めて促す（9.6）。促さなかった回は、`match: Stop` のルールが渡す回なら止めてその文を渡す（6.6） | `systemMessage`。止めるときは `decision: block` と `reason` も |
| `SubagentStart` | 承認済みで開いている子チケットの一覧を渡す（9.12）。cwd がプロジェクトの中なら、そのプロジェクトのスキルの目録を頭に置く（11.13）。気づいたスキル候補は最後の報告に節を足して返すよう 1 行渡す（6.6） | `additionalContext` |
| `SubagentStop` | 子のワークツリーに範囲外の変更が残っていれば 1 回だけ差し戻す（9.12） | 終了コード 2 と標準エラー |
| その他 | 何もせず通す。記録に `event-not-checked` | 無し |

`matcher` は絞らない（絞ると書かなかったツールで hook が起動しない）。同じイベントの他の hook は並行して順不同で走るので、
その結果には依存しない。

### 4.3 設定の解決

設定は環境変数 `CCNAVI_*` で渡す。プロジェクトは `.claude/settings.json` の `env` ブロックに書く。読む順は
プロセス環境 → `ccnavi.settings.local.json`（ccnavi 自身のソースツリーでだけ読む、1 人の上書き）→
コマンドラインのオプション。変数の一覧と意味は README「設定」。

`disable` は起動側の環境で指定したときしか有効にならない（REQ-CMN-04）。`ccnavi.settings.local.json` の `disable` は無視して理由を出す。
`.claude/settings.json` の `env` の `disable` はプロセス環境と区別できないので、書く経路を閉じる。`.claude/settings.json` は
コアファイル（8 章）である。`Write` / `Edit` はルールの `deny` が、シェルからの書き込みは組み込みが止め、それでも書かれたら実行後にバックアップから戻す。
`env` は再読み込みされないので、書けても同じセッションでは反映されない。書かれていれば `--lint` が error で名指しし、
導入スクリプトは `--mode disable` を断る。

読めない値は報告して `enable` として扱う。

### 4.4 動作モード

| `CCNAVI_MODE` | 挙動 |
|---|---|
| `enable`（既定） | 判定し、`deny` なら止め、`ask` ならユーザに確認を出す |
| `dry-run` | 同じ判定を行い、呼び出しには手を出さず「`enable` なら何をしていたか」を `additionalContext` で伝える |
| `disable` | 判定しない。記録に `mode-disabled` を残す |

戻す働きの 2 つ（`CCNAVI_RESTORE_IF_DENY`、`CCNAVI_GUARD_CORE_FILES`）も同じ 3 値を取り、`CCNAVI_MODE=dry-run` のときは
こちらが `enable` でも `dry-run` として振る舞う。組み合わせの表は [要件 2.2](../requirements/common.md)。

`CCNAVI_GUARD_TICKET_APPROVAL` と `CCNAVI_TICKET_CONTROL`、`CCNAVI_GUARD_UNWATCHED` は `enable` / `disable` の 2 値。

### 4.5 応答の形と結果

| 場合 | 応答 |
|---|---|
| `enable` の deny / ask | `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny\|ask", "permissionDecisionReason": "…", "additionalContext": "…"}}`。`additionalContext` はルールが文を持つときだけ |
| `enable` の allow、委ねた回 | 判定は出さない。通知（既定を使った、コアファイルを戻した、ルールの文）があれば `additionalContext` だけ |
| `dry-run` の deny / ask | `additionalContext` に「enable なら止めていた / 聞いていた」と理由を並べる。終了コード 0 |
| `Stop` | `{"systemMessage": "…"}`。`finish` を促すとき（`enable`）は `{"decision": "block", "reason": "…", "systemMessage": "…"}`。`dry-run` なら促しの文を `systemMessage` に載せて止めない |
| `PostToolUse` / `SubagentStop` の差し戻し | 標準エラーに文、終了コード 2。`dry-run` なら `additionalContext` で 0 |

期限は `PreToolUse` で 3 秒。ルール照合の途中で超えたら `deadline-exceeded` として、`enable` は
終了コード 2、`dry-run` は 0（REQ-CMN-08）。この block は理由を返せないので、生の文字列に `>` が 50 個を超えて並ぶ入力は、シェルから書き込む形の組み込みの保護だけを当てずに、ほかのルールの `deny` と `ask` を当てたあとで、`deny` に当たっていなければ `DENY_REDIRECT_LIMIT` に上げて理由と書き直し方を返す（付録 A）。この保護の照合は `>` の個数 × その後ろの空白の無い語の長さで伸びるので、上限で `>` の個数を抑える。上限の下でも尾が長い入力（400KB 程度）は期限に届きうる。外側で打ち切られた場合には手が出ない。`PostToolUse` に期限は無く、
git の読み取りが 2 秒で打ち切られる。

payload が JSON でない・オブジェクトでない・`hook_event_name` が無いときは `payload-unusable` で
同じ結果になる。記録を書けないときは標準エラーに出して捨て、判定は変えない。

### 4.6 導入と配布

`scripts/ccnavi-setup.sh <対象>` が、対象プロジェクトに設定を書き、道具を配る。何度打っても同じ形に
なる。既にある値と、ccnavi と関係のない hook はそのまま残す。

| 書くもの | 中身 |
|---|---|
| `.claude/settings.json` の `env` | `CCNAVI_MODE` / `CCNAVI_BIN_PATH` / `CCNAVI_RESTORE_IF_DENY` / `CCNAVI_GUARD_CORE_FILES` / `CCNAVI_GUARD_TICKET_APPROVAL` / `CCNAVI_GUARD_UNWATCHED` / `CCNAVI_TICKET_CONTROL`。`--all` は受けるが、今は足すものが無い。置き場の env は書かない（置き場は固定。残っていても読まない） |
| `.claude/settings.json` の `hooks` | 7 つのイベントに実行ファイルを登録する。既に別の表記で登録されていれば足さずに名前を挙げる |
| `.vscode/settings.json` | `git.detectWorktrees: true`。`--no-vscode` で触らない |
| 配るもの | `dist/ccnavi/` の中身を `.ccnavi/bin/<os>-<arch>/` へ、設定 3 本のひな形、`.ccnavi/scripts/ccnavi-{ticket,review,git,common,push-approved,agree,fetch,sync,clean,branches,start,launcher}.sh`、共通部の部品 `ccnavi-common-{state,lock,c1,host,log}.sh` と `ccnavi-clean.js`。取り込み（`ccnavi-fetch.sh`）は `SessionStart` に別の 1 行で登録する（`--no-fetch` で外す）。配布先に既にあるものは触らず、`--force` のときだけ入れ替える。振り分けの sh は配った回に実行ビットを付け、配らなかった回でも外れていれば付け直す（`--no-deploy` の回と、配布元と配布先が同じ回には触らない） |
| 配布先の `.gitignore` | 配った機械の置き場 `/.ccnavi/bin/<os>-<arch>/` の 1 行と、`--docs` の索引の `**/index.jsonl` の 1 行。索引の行は別の見出しの下に入り、`index.jsonl` の行が既にあれば足さない。`index.jsonl` を否定する行があればユーザの除外として足さない。どちらも配布先が git のリポジトリで、配るときだけ。振り分けの sh は追跡する側に置く。`projects/` の下のプロジェクトには足さない |

置き場は 2 つに分けて固定する。

```
.ccnavi/scripts/ccnavi-launcher.sh      ← CCNAVI_BIN_PATH が指す振り分けの sh（追跡する）
.ccnavi/scripts/ccnavi-ticket.sh など   ← 代わりに通る sh
.ccnavi/bin/darwin-arm64/ccnavi         ← 機械ごとの組み立て（_internal/ も同じ置き場。無視する）
.ccnavi/bin/linux-x86_64/ccnavi
.ccnavi/bin/windows-x86_64/ccnavi.exe
```

hook の `command` は振り分けの sh を指す 1 行で、どの実行ファイルを起動するかは起動した機械が決める。置き場の名前は
`build.py` が書く `dist/ccnavi.target` の `<os>-<arch>` で、語は `src/ccnavi/infra/platformtag.py`・`.ccnavi/scripts/ccnavi-launcher.sh`・
`scripts/ccnavi-setup.sh` の `host_target` の 3 か所で揃える。sh は ccnavi のリポジトリでも配布先でも同じパスに置き、
ccnavi のリポジトリの hook も同じ sh を通る。

sh の探し方: `here=${0%/*}`（`$0` に `/` が無ければ `.`）から `bin_dir=$here/../bin` を作り、`uname -sm` を 1 回読んで
`<os>-<arch>` を決める。arm64 の macOS と Windows は、自分向けが無いときだけ x86_64 へ回る。語ごとに `ccnavi` →
`ccnavi.exe` の順に `[ -f ]` を見て、見つかったものへ `exec` で引数と標準入力を渡す。

- 隣（`.ccnavi/scripts/<os>-<arch>/`）は探さない。自己防衛のパス（8.2）が当たらない置き場になるため
- `bin_dir` は `..` を含むまま使い、正規化しない。シンボリックリンクも解かない
- 見つからなければ終了コード 127 で、文面に `<os>-<arch>` と探した `bin_dir` を出す。在るが実行できなければ `exec` が失敗して 126

hook は sh を直に起動するので、sh に実行ビットが要る。ccnavi のリポジトリでは追跡するモードを 100755 にする。配布先では
導入スクリプトが付け、付いていなければ `--lint` が error で言う（10 章）。

ccnavi のリポジトリでの組み立て: `build.py` はまず `git rev-parse HEAD`（未コミットの変更があれば `-dirty` を付ける）を
`build/stamp/ccnavi_buildinfo.py` に書き、PyInstaller に一緒にバンドルさせる（`--version` の `commit`。パッケージの外に置くので、
ソースで動かしたときに前の組み立ての値を出さない）。次に PyInstaller の出力を `dist/ccnavi/` に入れ替え、`dist/ccnavi.target` を
書いたあと、`install()` で `dist/ccnavi/` を `.ccnavi/bin/<os>-<arch>/` へコピーする（`dist/` は導入スクリプトの配布元で、
代わりに通る sh が env の無いときに探す先でもあるので残す）。隣の `<os>-<arch>.new` にコピーし切ってから `_swap` で入れ替える。
`_swap` は置き場を `.old` へ退避してから新しいほうを移す（Windows でも走っている実行ファイルの名前は変えられる。付録 C）。
失敗したら 0.3 秒おきに 5 回までやり直し、やり直しきれなければ `.old` を置き場に戻してから投げる。`install()` が失敗したら
`build.py` は 1 を返し、`dist/` は新しく、`.ccnavi/bin/<os>-<arch>/` は前のまま、と言う。

- rename 2 回の間（数 ms）に来た hook は sh が 127 で終わり、その 1 回は判定が走らない
- ワークツリーで組み立てるとコピー先はそのワークツリーの `.ccnavi/bin/` なので、走っている hook（ワークスペースルートの sh）は変わらない
- 組み立て直すと、セッション開始で取った実行ファイルのバックアップ（8.3）と食い違う。開き直すまで、`enable` なら次のツール呼び出しのあと
  バックアップした実行ファイルを置き直し（`selfguard._check_heavy`）、`dry-run` なら「戻すはずだった」と言う

既定でないパス: 導入スクリプトは、`.claude/settings.json` にいま書かれている `CCNAVI_BIN_PATH` で動きを決める。

| いまの `CCNAVI_BIN_PATH` | 動き |
|---|---|
| 無い | `.ccnavi/scripts/ccnavi-launcher.sh` を書く |
| `.ccnavi/scripts/ccnavi-launcher.sh` | 何もしない |
| それ以外（ユーザが決めたパス） | 書き換えず、名指しで 1 行出す。導入は止めず終了コードも変えない。`--check` では揃っていない側に数える。「値が違う env」の一覧には入れない |

`--mode disable` は断る（4.3）。`--check` は書かずに揃っていないところだけを並べ、揃っていなければ終了コード 1。
名指しした `--deploy` が組み立てられていないか、目印が無くて置き場を決められなければ終了コード 2 で断る。ただし、
既定の配布元が使えないだけなら理由を 1 行出して設定は書く。別の機械向けの組み立ては、その機械の置き場へ配り、
この機械で動くものが無いことを言う。仕様は `tests/sh/test_setup.py` と `tests/sh/test_launcher.py` が固定している。

### 4.7 記録

ツール呼び出しは、通したものも判定しなかったものも 1 件 1 行の JSON として
`logs/decisions.jsonl`（固定）に追記する。1 行の欄は付録 B。判定しなかった回も残すので、記録が無ければ ccnavi が動かなかったと読める。

記録に書く `subject` / `unwrapped` / `detail` は、書く直前に秘密の形を伏せる（`src/ccnavi/records/redact.py`）。伏せるのは
記録だけで、判定は伏せる前の文字列で下す。記録が 10 MB を超えたら `decisions.<日時>.jsonl` へローテートし、ローテートした
記録と終わったセッションの記録（`logs/state/`）は 14 日で消す。走るのはセッション開始と `ccnavi --prune` だけで、
実行前チェックでは走らない。記録はセッションごとにまとめて、どれかが保持日数のうちに書かれていれば全部残す
（`src/ccnavi/records/prune.py`）。`<セッション>.json` と読むのは UUID の形の名前だけで、知らない名前のファイルと、
リンクになった置き場（`logs/state` そのものと `selfguard/`）には触らない。しきい値は 0 のほか 1 MB・1 日より
小さい値と有限でない値を受けず、既定で動く。ローテート先は `O_EXCL` で先に押さえてから名前を変える。
伏せる前にも 4000 字（上限の 4 倍）で切り、伏せる手間が実行前チェックの期限に届かないようにする。「記録が無ければ動かなかった」は、ローテートした分と合わせて読む。

| 欄 | 数えるもの |
|---|---|
| `decision` = `allow` / `ask` / `deny` / `handover` / `nudge` / `skip` | 下した判定。`handover` は権限モードに委ねた回。`nudge` は `Stop` で `finish` を促した回と、`match: Stop` のルールで止めた回（ツール呼び出しの判定ではないので `deny` と数えない） |
| `enforced` | 実際に適用したか。`dry-run` は常に偽 |
| `code` | 判定の根拠の種別（付録 A） |
| `reason` | `skip` の理由 |
| `degraded` | コマンドを読み切れず生の文字列に当てたかどうか |
| `fallback` | ルールを読めず組み込みの既定で判定したかどうか |
| `paths` / `detail` | 実行後チェックが拾った変更と件数 |
