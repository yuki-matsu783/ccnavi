# ccnavi

Claude Code のツール呼び出しを hook で止め、止めた理由と代わりに取る手段を返す。

用語は [CONTEXT.md](docs/CONTEXT.md)、要求は [requirements.md](docs/requirements.md)、設計は
[docs/design.md](docs/design.md)、判断の理由と経緯は [docs/adr/](docs/adr/README.md) にある。
「承認済みチケット」「マーカー」「ワークツリー」「直接作業」のような呼び名は用語集で定義している。

## 導入

どんなコマンドが打たれ、ルールが何に当たるかは、動かしてみるまで分からない。`dry-run` で導入し、記録を読みながら
ルールを直し、記録が落ち着いてから `enable` に切り替える。

### 1. dry-run で立てる

実行ファイルを組み立て、7 つのイベントに登録する（「設定」の節。導入スクリプトが書く）。
判定だけを見るなら `PreToolUse` と `PostToolUse` の 2 つでも動く（残りを登録しないと何が欠けるかは「設定」の表）。
`CCNAVI_MODE` は `dry-run` にする。

```json
"env": {
  "CCNAVI_MODE": "dry-run"
}
```

ルールファイルには、取り返しの付かない操作とガード自身の設定だけを `deny` に書いて始める
（`rm -rf`、`git push`、`git reset --hard`、認証情報の置き場、`.claude/` の下）。組み込みの既定
（「ルールファイルが読めないとき」の節）がちょうどその範囲なので、写して直すのが早い。

`allow` は空のままでよい。`dry-run` は呼び出しに手を出さないので作業は止まらず、ルールの抜けている箇所だけが記録に溜まる。

導入したら、設定そのものを確かめる。

```sh
ccnavi --lint
```

`dry-run` の間は `warn` が 2 件出続ける（モードが `dry-run`、`allow` が空）。終了コードは 0 のまま。

`env` の変更はセッションを開き直すまで反映されない。

### 2. 普段どおり開発する

ccnavi は判定し、呼び出しには手を出さず「`enable` なら何をしていたか」を返す。
その文面はエージェントが読むので、代わりに取る手段が書けているかもここで分かる。

### 3. 記録を読む

通した回も含めて 1 行 1 件で残っている（「記録」の節）。読むのは 4 つ。

```sh
# 判定の内訳
jq -r '.decision' logs/decisions.jsonl | sort | uniq -c | sort -rn

# 止めた回。誤検知はここに出る
jq -r 'select(.decision=="deny")|[((.rules//[])|join(",")),(.subject|gsub("[ \t\n]+";" "))]|join("\t")' \
  logs/decisions.jsonl | sort | uniq -c | sort -rn

# どこにも当たらなかった回。enable ではこれが全部、権限モードに渡る
jq -r 'select(.code=="UNDECLARED")|(.subject|gsub("[ \t\n]+";" "))' \
  logs/decisions.jsonl | sort | uniq -c | sort -rn | head -30

# 通した回を、当たったルール別に。広すぎる allow はここに出る
jq -r 'select(.event=="PreToolUse" and .decision=="allow")|[((.rules//[])|join(",")),(.subject|gsub("[ \t\n]+";" "))]|join("\t")' \
  logs/decisions.jsonl | sort | uniq -c | sort -rn | head -30
```

`subject` は 1 行にまとめてから数える（複数行のコマンドを `uniq -c` が行ごとに別々に数えないように）。
ローテートした分（`logs/decisions.<日時>.jsonl`）も合わせて数えるなら、`logs/decisions.jsonl` の代わりに `logs/decisions*.jsonl` を渡す。

`decision` が `skip` の行は判定まで進まなかった回で、`reason` に理由が入る。
`paths` が付いた行は実行後チェックが拾った変更で、引数に現れない書き込みはここで分かる。

### 4. ルールを直す

| 記録に出るもの | 直す先 |
|---|---|
| 止めるつもりのなかったものが `deny` に出ている | ルールのパターンを絞る。語の切れ目が要るなら `regex` へ |
| 同じ呼び出しが `UNDECLARED` で並ぶ | `allow` に足す。1 行足すたびにユーザが見なくなる範囲が広がる |
| `allow` で通っているが止めたいものがある | `deny` か `ask` に足す。強いタイプが先に当たる |

直したら、そのつど 3 つを回す。

```sh
ccnavi --lint                              # 防御を無効化しうる記述が無いか
ccnavi --test Bash "cd /repo && git push"  # 1 件が何に当たるか
uv run python tools/check_rules.py         # 見本をまとめて回す
```

ルールを 1 件足したら見本も 1 行足す（「見本で確かめる」の節）。止めたくないものも必ず一緒に置く。
`/ccnavi-config` スキルがこの流れをまとめて回す。記録を数えて `deny` / `ask` の下書きを起こすのは `ccnavi --suggest`
（「記録から候補を起こす」の節）。

### 5. enable へ切り替える

目安は記録の側にある。

- 普段の作業で `UNDECLARED` がほとんど出ない（残ったままだと、作業の大半が判定を受けずに権限モードへ渡る）
- `deny` に出ているものが、全部「意図して止めたもの」になっている
- `--lint` の `error` が 0 件

`CCNAVI_MODE` を `enable` にして、セッションを開き直す。記録は同じファイルに続き、
`deny` と `ask` の `enforced` が `false` から `true` に変わる。

`disable` は `.claude/settings.json` に書いても有効にならない（「動作モード」の節）。
一時的に外すなら、セッションを起動する側の環境から渡す。

## 開発

Python 3.12 以降。実行時の依存は PyYAML 1 本だけ。
組み立てと検査の道具（PyInstaller、ruff）は開発時にしか要らない。

```sh
uv run python -m unittest discover -s tests -t .   # テスト
uv run python -m unittest discover -s tests/core -t .  # 1 グループ（core guard config ticket sh e2e）
uv run --with ruff ruff check .                    # 静的検査
uv run --with ruff ruff format .                   # 整形
uv run --with ruff ruff format --check .           # 整形の確認だけ
uv run --with pyinstaller python build.py          # 実行ファイルの組み立て
```

テストは `python -m unittest` で回す。ファイルを直接実行すると `tests` パッケージを import できずに落ちる。
回すグループは `.claude/skills/commit/references/test-groups.md` の表で決める。

`knowledge/` は参照専用の資料で git 管理外。Claude Code の hook にまつわる実測が入っている。

手で 1 回動かす。

```sh
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git push"}}' \
  | uv run python -m ccnavi --mode enable
```

Python のファイルを編集するたびに `.claude/hooks/lint-py.sh`（`PostToolUse` の
`Write|Edit|NotebookEdit`）が整形と検査をかける。テストはそこでは走らせない。

テストは `.claude/hooks/test-py.sh` で、`Stop` に登録してターンの終わりに 1 回だけ回す。
落ちていたら差し戻すが、差し戻しは 3 回まで。上限に達したら止まらせて判断をユーザへ返す。
このリポジトリの `.claude/settings.json` の `Stop` には ccnavi の実行ファイルしか無いので、
テストを回すなら `Stop` に足す（ユーザごとの `settings.local.json` でよい）。

`lint-py.sh` は、編集したファイルからいちばん近い `pyproject.toml` を上に辿ってツリーを決め、
触ったツリーを `logs/session/<セッション>.trees` に書き残す。`test-py.sh` は触ったツリーだけをテストする。

拡張（`extensions/vscode/ccnavi-board`）のテストも同じ形で回す。`PostToolUse` の
`.claude/hooks/mark-ext.sh` が、拡張のファイルを触ったら `logs/session/<セッション>.ext-files` に
書き残し、`Stop` の `.claude/hooks/test-ext.sh` がターンの終わりに 1 回回す。回すのは触ったファイルが
関わるグループだけで、決めるのは `extensions/vscode/ccnavi-board/scripts/test-groups.js`
（テストの `import` を辿る。辿れない 6 つだけは表に名前を書いてある）。拡張を触っていないターンは何もしない。
差し戻しは 3 回までで、回数は `test-py.sh` と別に数える。

この 2 本で気づけるのは「同じ機械で 1 回回して落ちること」だけで、混み具合で結果が変わる失敗（React の描き直しを
待つテストなど）は見つけられない（CI も繰り返しの仕掛けも無い）。Bash で変えたもの（`sed -i`、`merge` で入った変更）は
書き残されないので回らない。

これも `.claude/settings.json` には登録していないので、回すなら `settings.local.json` で
`PostToolUse`（`Write|Edit`）と `Stop` に足す。

```json
"PostToolUse": [
  { "matcher": "Write|Edit",
    "hooks": [{ "type": "command", "command": "sh \"${CLAUDE_PROJECT_DIR}/.claude/hooks/mark-ext.sh\"", "timeout": 10 }] }
],
"Stop": [
  { "matcher": "",
    "hooks": [{ "type": "command", "command": "sh \"${CLAUDE_PROJECT_DIR}/.claude/hooks/test-ext.sh\"", "timeout": 180 }] }
]
```

実行ファイルはどちらの hook でも作り直さない。確かめるときに手で `uv run --with pyinstaller python build.py` を回す。

このリポジトリの hook も、配布先と同じく振り分けの sh（`.ccnavi/scripts/ccnavi-launcher.sh`）を通る。
`build.py` は `dist/ccnavi/` に組み立てたあと、それを `.ccnavi/bin/<os>-<arch>/` へコピーする（隣の `<os>-<arch>.new` に
コピーし切ってから入れ替える）。`.ccnavi/bin/` は `.gitignore` に入っている。コピーする段で失敗したら次の行を出して
終了コード 1 で終わる。原因（ディスク、権限、Windows で走っている実行ファイルのロック）を直して回し直す。

```
dist/ は新しい。.ccnavi/bin/<target>/ は前のまま
```

ワークツリーで組み立てると、コピー先はそのワークツリーの `.ccnavi/bin/` になり、走っている hook は変わらない。
ワークスペースルートで組み立て直すと、セッション開始で取った実行ファイルのバックアップと食い違う（「コアファイルを守る」）。
`CCNAVI_GUARD_CORE_FILES` が `enable` なら次のツール呼び出しのあとでバックアップの版に戻される（`dry-run` なら報告だけ）。
ワークスペースルートで組み立てたら、セッションを開き直す。

`dist/` を直に起動したいときは、`.claude/settings.local.json`（追跡しない）の `env` で `CCNAVI_BIN_PATH` を
`dist/ccnavi/ccnavi` にする。自己防衛も同じ env を読むので、守る実体と起動する実体は食い違わない。代償は、その機械では
振り分けの sh の不具合に気づけないこと。`ccnavi.settings.local.json` のキー `bin` でも
`CCNAVI_BIN_PATH` を上書きできるが、変わるのは自己防衛が何を守るかだけで、hook が何を起動するかは変わらない。

### 配布する sh を確かめる

```sh
uv run python -m unittest tests.e2e.test_e2e_sh -v
```

実行の最初に出る `sh =` がワークスペースルート側を指していることを確かめる。実際に使われるのはワークスペース側の 1 本だけ。
写す前の版を測るときは `CCNAVI_SH_DIR=<場所>` で差し替える。組み立て済みの実行ファイルを試すので、
`src/ccnavi/` を直したら組み立て直してから回す（無ければ skip）。モード B（`projects/` を使う形）に触ったら回す。

### 配布物

PyInstaller の onedir で組み立てる。Windows なら `dist/ccnavi/ccnavi.exe`、
Linux なら `dist/ccnavi/ccnavi`。onefile は起動のたびにランタイムを展開して遅いので使わない。

| 形式 | 1 呼び出しあたり | 配布物 |
|---|---|---|
| onedir | 約 220 ms | 17 MB のフォルダ |
| onefile | 1000〜1500 ms | 7 MB の 1 ファイル |

## 実測で分かった落とし穴

Claude Code の振る舞いについて測った前提は設計書の付録 C。測り直したときは両方を直す。

- **ワークツリーが消せない（Windows）。** uv のハードリンクで `.venv` の `_yaml.*.pyd` が全ツリーで同じ実体になり、
  誰かのテストが読み込んでいる間は消せない。`pyproject.toml` の `link-mode = "copy"` で分けてあるが、
  ハードリンクで作った `.venv` はそのままなので、テストが走っていないときに作り直す。自分のテストが走っている間は残る。
  落ちたら残ったディレクトリを `mv` で `.claude/worktrees/` の外へ出して `git worktree prune`。
  先に `sh .ccnavi/scripts/ccnavi-clean.sh <名前>` で生成物を消すと node_modules で止まる分は避けられる
- **`${CLAUDE_PROJECT_DIR}` は hook の `command` では展開されるが `env` では展開されない。** `env` には相対パスを書く
- **`env` ブロックは再読み込みされない。** セッションを開き直すまで古い値が残る。`command` は即座に反映される
- **ツールのプロセスに `CLAUDE_PROJECT_DIR` は入っていない。** hook の環境にだけ来る
- **`.claude/settings.json` は未知のトップレベルキーを拒否する**
- **PyInstaller は指定したスクリプトをパッケージの外で走らせる。** 相対 import が解けないので、絶対 import の `main.py` を渡す
- **`shlex` は引用・`#`・改行・行継続・`<<` の扱いがシェルと違う。** `shellread.py` は先に原文を走査してから語の分割だけを
  shlex に任せる。走査か shlex のどちらかに手を入れたら `tests/core/test_shellread.py` の `SHELL_CASES`（bash 3.2 と zsh で実測）を回す。
  引用された `<<` による縮退は許容する誤検知（設計 6.3、12.2）
- **`$( )` の中の `case` は bash 3.2 と zsh で読みが分かれる。** `case` が現れたら縮退させている（許容した誤検知）
- **複合コマンドは 1 つの区間が読めなければ全体が縮退する。** 安全側なのでそのまま
- **Python の識別子に空白は入らない。** `def test_warn は…` のように英字と日本語の間に空白を入れると構文エラー
- **Windows のコンソール経由で日本語を引数に渡すと CP932 になり、`jq --arg` が UTF-8 でない JSON を作る。** 本文はファイルで渡す。
  `jq` の実体は `C:\Program Files\jq\jq` で、`"$JQ"` と引用しないと空白で分かれる
- **Windows の `gitdir:` の表記**（git 2.39.2、Git Bash と PowerShell）。絶対パス、区切りは `/`、ドライブレターは大文字、
  `gitdir:` の後ろは半角空白 1 個。`ccnavi_project`（sh）と `tree.py` がこれを前提にしている
- **Docker Desktop を起動すると `restart=unless-stopped` の GitLab が勝手に上がり、2GB の VM では engine ごと落ちる。**
  GitLab CE には 4GB 要る

GitLab の実物（CE 18.5.4）で分かったこと。

| 分かったこと | どうしたか |
|---|---|
| 変更要求（`POST .../request_changes`）は EE 限定 | 当てられない。CE の `reviewers` の `state` は `unreviewed` / `reviewed` / `approved` だけ |
| URL にトークンを埋めた origin はそのままでは `origin` の出力に出る | sh はユーザの情報を落として伏せる。実行ファイルの `remote_kind` も読み飛ばす |
| ラッパースクリプト経由の push は `GIT_CONFIG_COUNT` を落とすので、環境変数で credential helper を差し替えても反映されない | 認証は git の設定側に置く（probe はリポジトリの `credential.helper` を空にしてから足す） |
| トークンは `docker exec -i gitlab gitlab-rails runner -` に Ruby を流して作れる（`tools/gitlab/make_gitlab_tokens.rb`） | root と reviewer の 2 人分を作る |
| 起動直後は API の `PUT` が 30 秒を超えることがある | probe は 120 秒で 3 回まで待つ |

`dist/`、`build/`、`logs/` は git 管理外。記録には絶対パスとコマンド全文が入るのでコミットしない。

## 設定

設定は環境変数で、`.claude/settings.json` の `env` ブロックに書く（Claude Code の設定スキーマは
独自のキーを受け付けない）。hook には実行ファイルだけを登録すればよい。

下の形は `sh scripts/ccnavi-setup.sh <ワークスペースルート>` が書く。何度打っても同じ形に
なり、既にある値と、ccnavi と関係のない hook はそのまま残る。書かずに揃っていない
ところだけを見たいときは `--check` を付ける。`--all` も受けるが、今は足すものが無い（置き場の env は廃止した）。
同じ 1 回で `.vscode/settings.json` も見る（次の節）。触ってほしくないときは `--no-vscode`。
セッション開始時の取り込み（`ccnavi-fetch.sh`）も `SessionStart` に別の 1 行で登録する。
1 台だけで使いリモートに合わせる必要が無ければ `--no-fetch` で外す。

既にある値は置き換えず、違えば並べて見せる。置き換えるのは `--mode` か `--ticket-control` を
名指しして `--force` を付けたときだけで、名指ししていない値はそのままにする。

戻す働きの 2 つ（`CCNAVI_RESTORE_IF_DENY` / `CCNAVI_GUARD_CORE_FILES`）は `CCNAVI_MODE` と
同じ値で書く。`CCNAVI_MODE` が `dry-run` のうちは、この 2 つも `dry-run` になる（「動作モード」の節）。
`enable` へ切り替える前に、記録の `would-restore` で何が戻るはずだったかを確かめられる。
`dry-run` で入れたままだとこの 2 つも `dry-run` のまま残るので、`--mode enable` で打ち直すか 3 行を書き換える。

`CCNAVI_GUARD_TICKET_APPROVAL` はモードに合わせず、いつも `enable` で書く。この変数は
`enable` か `disable` しか取らない（承認は通れば済んでしまうので、報告だけのモードを持てない）。
`dry-run` と書かれていたら `--lint` が error にする（今は `enable` として動いている、と添えて）。

hook は、そのイベントに ccnavi が登録されていなければ足す。別の表記で登録されているように
見えるイベントは、足さずに名前を挙げる（知らせずに足すと判定が 2 回走る）。

置き場の env 6 つ（`CCNAVI_PROJECTS`・`CCNAVI_PROJECT_HOME`・`CCNAVI_TICKETS_PROPOSAL`・
`CCNAVI_TICKETS_APPROVED`・`CCNAVI_LOG`・`CCNAVI_STATE`）は書かない。既にある `env` に残っていれば外す
（置き場は固定で、書いても読まれない。下の「置き場は固定」の段落）。外した値が既定と違っていれば、
名前と値を 1 行ずつ出す。**以前の導入スクリプトが書いた `CCNAVI_LOG=logs/log.jsonl` も、既定
（`logs/decisions.jsonl`）と違う値として名指しされる。** 記録の書き先が変わり、古い `logs/log.jsonl` は
もう書かれず `--suggest` も数えないので、黙っては外さない。導入は止めず、終了コードも変えない
（`--check` では「揃っていない」に数える）。

入れ終わったところで、ワークスペースの git の索引に `projects/` の下が載っていないかを見る。
載っていれば `--lint` の `(projects)` と同じ条件で、同じ案内を出す。ワークスペース自身のソースに
`projects/` がある（ぶつかり）なら `projects/` の改名を、入れ子のリポジトリだけが載っている（載せ忘れ）なら
索引から外して `.gitignore` に `/projects/` を足す手順を案内する。止めず、終了コードも変えない。
`--check` でも出すが、導入の不足ではないので「揃っていない」には数えない。索引も `.gitignore` も変えない
（直すのはユーザ）。

```json
{
  "hooks": {
    "SessionStart": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}\"", "timeout": 10 }
      ]},
      { "matcher": "", "hooks": [
        { "type": "command", "command": "sh \"${CLAUDE_PROJECT_DIR}/.ccnavi/scripts/ccnavi-fetch.sh\"", "timeout": 60 }
      ]}
    ],
    "UserPromptSubmit": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}\"", "timeout": 10 }
      ]}
    ],
    "PreToolUse": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}\"", "timeout": 10 }
      ]}
    ],
    "PostToolUse": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}\"", "timeout": 10 }
      ]}
    ],
    "Stop": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}\"", "timeout": 10 }
      ]}
    ],
    "SubagentStart": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}\"", "timeout": 10 }
      ]}
    ],
    "SubagentStop": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}\"", "timeout": 10 }
      ]}
    ]
  },
  "env": {
    "CCNAVI_MODE": "dry-run",
    "CCNAVI_BIN_PATH": ".ccnavi/scripts/ccnavi-launcher.sh",
    "CCNAVI_RESTORE_IF_DENY": "dry-run",
    "CCNAVI_GUARD_CORE_FILES": "dry-run",
    "CCNAVI_GUARD_TICKET_APPROVAL": "enable"
  }
}
```

同じ実行ファイルを上の 7 つのイベントに登録する。payload にイベント名が入っているので、どれを走らせるかは ccnavi が選ぶ。
`matcher` は絞らない。絞ると、書かなかったツールで hook 自体が起動しなくなる。
`SessionStart` の 2 本目は取り込みの sh（`ccnavi-fetch.sh`）で、承認済みチケットと
マーカー（親ブランチに乗って届く）と、ワークツリーの起点になるデフォルトブランチを fast-forward で
取ってくる。登録しないと、別の機械で承認したものが反映されず、古い `main` からブランチを切ることになる。

| イベント | ここで何をするか | 登録しないと |
|---|---|---|
| `SessionStart` | 実行ファイルのバックアップを取る。ccnavi が前提にしている作業の決まり（ワークツリー・git の入口・下書きの置き場など）を 1 行ずつモデルに渡す。チケット制御を使っていれば、直接作業とチケット作業の使い分けも渡す。cwd がプロジェクトの中なら、そのプロジェクトのスキルの目録を渡す | 実行ファイルが差し替えられても戻せない。モデルが ccnavi の決まりとチケットをいつ起こすかを知らないまま進む |
| `UserPromptSubmit` | 保護領域の状態とツリーごとの HEAD を記録し、ターンの基準にする | ターンの終わりの報告が出ない（コミットに入った変更も見えない） |
| `PreToolUse` | 呼び出しを判定し、設定ファイルをバックアップする | 判定そのものが働かない |
| `PostToolUse` | 作業ツリーを見て、変わっていれば戻す。チケットの状態を承認済みチケットへ書き出す | 引数に現れない書き込みを取りこぼす。フェーズの終わりが伝わらない |
| `Stop` | このターンで変わった保護領域をユーザへ報告する。cwd のワークツリーのチケットを `finish` し忘れていそうなら 1 回だけ止めて促す。`match: Stop` のルールがあれば、そのタイミングで止めて文を渡す | 変更がユーザの目に触れない。閉じ忘れたチケットが作業中に残る。ターンの終わりの促しが届かない |
| `SubagentStart` | 承認済みで開いている子チケットの一覧を渡す。cwd がプロジェクトの中なら、そのプロジェクトのスキルの目録も | サブエージェントが自分の範囲を知らずに始める |
| `SubagentStop` | 子のワークツリーに範囲外の変更が残っていれば 1 回だけ差し戻す | 範囲外のコミット済みの変更が親に届く |

`SessionStart` で渡す作業の決まりは次の形で、起動・再開・compact・clear のたびに届く（サブエージェントには渡さない）。
ccnavi の動作に依存する決まりなので、ワークスペースの `CLAUDE.md` に同じことを書かなくてよい。各項目は要点の 1 行だけで、
詳しいことは名指しした入口（`--help`、拒否の案内、`status`、`--docs`）に聞く。

```
[ccnavi] ccnavi が前提にしている作業の決まり。
- 編集する前にワークツリー（.claude/worktrees/<名前>）を切り、その中で編集する。切り方は ccnavi-git.sh の --help（worktree add）。
- git は直接呼ばず sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-git.sh を通す。止められたら迂回せず、出力の案内に従う。
- 下書きと使い捨てのファイルはワークツリーの scratchpad/ に置く（追跡されず、範囲外の変更としても報告されない）。
- projects/<名前>/ を直すときは、そこへ cd してから作業する（git の操作は cwd のリポジトリに向く）。
- 実装・調査・テストはサブエージェントにバックグラウンドで任せる。メインはユーザとの相談・判断・報告を受け持つ。
- 判定が緩む・ユーザとのやり取りの形が変わる・元に戻せない・影響範囲を読み切れない変更は、実装する前にユーザと合意する。
- 承認済みチケットの状態は、ファイルを読んで推測せず sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-ticket.sh status [<親>] で確かめる。
詳しい決まりは下の案内の --docs で、--keyword <語> を付けて引いてください（例: ワークツリー、下書き、サブエージェント、相談）。
```

行によっては、条件に合うときだけ出る。条件の無い行（下書き・サブエージェント・合意）はいつも出る。

| 行 | 出る条件 |
|---|---|
| ワークツリー | ワークスペースのルール（共通レイヤーか自身のレイヤー）の `deny` に id `main-tree` がある |
| git | 同じく `deny` に id `raw-git` がある |
| `projects/` | プロジェクトの置き場に `.git` を持つプロジェクトがある |
| `status` | チケット制御を使っている（`CCNAVI_TICKET_CONTROL` が `disable` でない） |
| 最後の行（`--docs`） | md の索引の案内（`--docs`）を出す回 |

ワークツリーと git の行は、それを支える deny があるときだけ出す。ルールが無いワークスペースで出すと、文と判定が食い違う。
共通レイヤーが読めずに組み込みの既定に戻ったときも出ない（既定にはこの 2 つの id が無い）。

`command` を `${CCNAVI_BIN_PATH}` で書くのは、守る対象と起動する実体を 1 か所にまとめるため
（shell form なので環境変数は shell が展開する）。その代わり、`env` からこの 1 行が消えると hook が起動しなくなる。

**置き場は固定で、環境変数では動かない。**

| 置き場 | 場所 |
|---|---|
| プロジェクト | `projects/`（ワークスペースルートの直下。直下で `.git` を持つディレクトリがプロジェクトになる） |
| ccnavi ディレクトリ | `.ccnavi/`（各 git プロジェクトルートの直下。その下の `config/{rules,phases,risks}.yml` が 1 つのレイヤーの 3 本、`scripts/` が配点の `script:` の置き場） |
| 提案 | `wip/proposals/`（各ツリーのルートの直下。そのツリーの git が追跡する） |
| 承認済みチケットとフェーズのマーカー | `.ccnavi/approved/`（各ツリーのルートの直下。そのツリーの git が追跡し、親チケットのブランチに乗って他の機械へ届く） |
| 判定の記録 | `logs/decisions.jsonl`（ワークスペースルートの下） |
| state | `logs/state/`（ワークスペースルートの下。実行後チェックの記録） |

別の場所を指せるのはフラグ（`--log` / `--state` / `--approved` / `--tickets` / `--projects` /
`--project-home`）だけで、hook からは渡らない。共通レイヤーの置き場（`.ccnavi/common/`）は `--project-home` で
ccnavi ディレクトリを動かしても動かず、別の場所を指せるのは `--rules` / `--phases` / `--risk` のフラグだけ。
レイヤーの置き場を動かすこれらのフラグ（`--projects` / `--project-home` を含む）は診断（`--lint` / `--test` /
`--test-samples` / `--explain`）に限り、hook からの判定と `ticket` / `review` の副命令に渡すと無視し、標準エラーに出す。

以前は 6 つの環境変数（`CCNAVI_PROJECTS`・`CCNAVI_PROJECT_HOME`・`CCNAVI_TICKETS_PROPOSAL`・
`CCNAVI_TICKETS_APPROVED`・`CCNAVI_LOG`・`CCNAVI_STATE`）で置き場を動かせたが、廃止した。
`settings.json` の `env` に残っていても読まない。導入スクリプトを打ち直すと外れ、既定と違う値だったものは
名前と値が 1 行ずつ出る（以前の導入スクリプトが書いた `CCNAVI_LOG=logs/log.jsonl` もここで名指しされる）。
「記録しない」「state を保存しない」「プロジェクトを数えない」も指定できない。プロジェクトを数えたくなければ
`projects/` を作らない。

| 変数 | 意味 |
|---|---|
| `CCNAVI_MODE` | `enable`（既定）、`dry-run`、`disable` |
| `CCNAVI_LOG_ROTATE_MB` | 記録をローテートする大きさ（MB）。既定は `10`。`0` でローテートしない。`1` より小さい値は既定で動く（「記録の後始末」） |
| `CCNAVI_LOG_KEEP_DAYS` | ローテートした記録を残す日数。既定は `14`。`0` で消さない。`1` より小さい値は既定で動く |
| `CCNAVI_STATE_KEEP_DAYS` | 終わったセッションの記録を残す日数。既定は `14`。`0` で消さない。`1` より小さい値は既定で動く |
| `CCNAVI_RESTORE_IF_DENY` | `enable`（既定）、`dry-run`、`disable`。`deny` と宣言した場所が副作用で変わったとき、git から戻すか。`dry-run` は戻さずに「戻すはずだった」と知らせる |
| `CCNAVI_GUARD_CORE_FILES` | `enable`（既定）、`dry-run`、`disable`。ccnavi が動くために要るファイルを守るか。書き込みを止める側と、バックアップして戻す側の両方が切り替わる |
| `CCNAVI_DENY_REPEAT` | 同じ理由で同じ呼び出しを何回止めたら、拒否の文面に「言い換えずにユーザに相談する」一文を足し、ターンの終わりにユーザへ報告するか。既定は `3`。2 未満と読めない値は既定に戻る。判定は変わらない（「同じ呼び出しを繰り返し止めたとき」） |
| `CCNAVI_GUARD_UNWATCHED` | `enable`（既定）、`disable`。ユーザにも classifier にも確認できないモード（`dontAsk` / `bypassPermissions`）で、ルールがどこも言及しない呼び出しを止めるか。`disable` なら判定を返さず、そのモードの取り決めに委ねる（読み切れなかった呼び出しは委ねない。「ルールが言及していない呼び出し」）。`dry-run` は無く、それ以外の値は `enable` として動き、`--lint` が指摘する |
| `CCNAVI_BIN_PATH` | hook が起動する ccnavi 自身。指定すると守る対象に入る。既定は無い（導入スクリプトは振り分けの sh `.ccnavi/scripts/ccnavi-launcher.sh` と書き、実行ファイルは `.ccnavi/bin/<os>-<arch>/` に入る。「実行ファイルとルールを配る」）。拡張子は書かない。Windows の `.exe` は ccnavi が補う |
| `CCNAVI_TICKET_CONTROL` | `enable`（既定）、`disable`。チケット制御（提案の承認・承認済みチケットの範囲・フェーズの HITL ポイント・サブエージェントの制限）を使うか。全体ルールは全プロジェクトが使い、チケットまで使うかをここで決める。`disable` なら `--agree` と `ticket` / `review` の副命令は動かず、セッション開始の案内も出ず、VS Code 拡張の「チケット管理」も出ない。それ以外の値は `enable` として動き、`--lint` が error にする |
| `CCNAVI_GUARD_TICKET_APPROVAL` | `enable`（既定）、`disable`。チケットの承認の経路を守るか。enable なら、シェルから ccnavi の実行ファイルを `--agree` / `--reviewed` / `--close-early` / `ticket …` / `review …` 付きで打つ形を止め（`DENY_TICKET_APPROVAL_CLI`）、`--agree` と `--reviewed` と `--close-early` は標準入力が端末であることを求める。エージェントのコマンド行にこの変数の名前を（読むだけの形のほかで）書く形と、`--guard-ticket-approval` に `enable` 以外を渡す形も同じ理由コードで止める（表示・検索の道具だけのコマンドは除く）。テストや端末の無い実行環境（CI など）で切る。`dry-run` は取らず、書かれていたら `enable` として扱い、`--lint` が error にする |
| `GITHUB_TOKEN` / `GITLAB_TOKEN` | レビューの依頼と確認がリモートを読み書きするときの認証。どちらが要るかは origin の URL で決まる |

`${CLAUDE_PROJECT_DIR}` は hook の `command` では展開されるが `env` では展開されない。
`env` には相対パスを書く。値の変更はセッションを開き直すまで反映されない。

ワークスペースルートは `CLAUDE_PROJECT_DIR`、無ければ作業ディレクトリから親へ辿って
`.claude` を探して決める。

### 実行ファイルとルールを配る

`CCNAVI_BIN_PATH` が指す実行ファイル、`rules.yml`、拒否の文面が案内する sh が、対象プロジェクトの中に要る。
`dist/` は `.gitignore` に入っていて、`CCNAVI_BIN_PATH` は絶対パスも `..` も受け付けないので、
設定を書く 1 回で配布物も置く。

```sh
# ccnavi のリポジトリで、まず組み立てる
uv run --with pyinstaller python build.py

# 対象プロジェクトへ、設定を書いて実行ファイルを配る
sh scripts/ccnavi-setup.sh /path/to/project --mode dry-run
```

配布元は既定で、打ったスクリプトの置き場の 1 つ上（ccnavi のリポジトリ）。
よそから配るときは `--deploy <ccnavi の根>` で名指しする。配布物が要らないときは `--no-deploy`。

実行ファイルは組み立てた機械の OS と CPU でしか動かないが、`settings.json` は Windows・WSL・Linux・macOS で
共有し、hook の `command` は 1 行しか書けない。そこで実行ファイルは機械ごとに置き場を分け、hook は振り分けの sh
（`.ccnavi/scripts/ccnavi-launcher.sh`）を起動する。sh は起動のたびに `uname` を 1 回読み、自分の 1 つ上の
`bin/<os>-<arch>/` から合う実行ファイルを選んで、引数と標準入力をそのまま渡す。

```
.ccnavi/scripts/ccnavi-launcher.sh      ← CCNAVI_BIN_PATH が指す振り分けの sh（追跡する）
.ccnavi/scripts/ccnavi-ticket.sh など   ← 代わりに通る sh（同じ置き場）
.ccnavi/bin/darwin-arm64/ccnavi         ← 機械ごとの組み立て（_internal/ も同じ置き場。無視する）
.ccnavi/bin/linux-x86_64/ccnavi
.ccnavi/bin/windows-x86_64/ccnavi.exe
```

置き場は 2 つとも固定で、原本と配布先で同じパスになる。`.ccnavi/bin/` には実行ファイルだけが並ぶ。
sh は自分の隣（`.ccnavi/scripts/<os>-<arch>/`）は探さない（自己防衛のパスが当たらない置き場から起動できる経路を作らないため）。

置き場の名前は、`build.py` が `dist/ccnavi.target` に書く `<os>-<arch>` の目印から取る。別の機械向けの
組み立てでも、その機械の置き場に入るだけなので配る。Windows と WSL で同じフォルダを開くなら、
それぞれの機械で打てば両方が並ぶ。この機械で動くものが無ければ、1 行の出力と最後の「まだ無いもの」の一覧で知らせる。
arm64 の macOS と Windows は、自分向けが無ければ x86_64 の組み立てを変換して動かす。どこにも無ければ sh は、
この機械の `<os>-<arch>` と探した置き場を名指しして終了コード 127 で終わる。実行できないときは 126。

目印が無いか読めない配布元からは配らない。名指しの `--deploy` なら終了コード 2 で断り、
既定の配布元なら理由を出して設定だけ書く。

振り分けの代償は、ツール呼び出しのたびに sh の起動と `uname` 1 回ぶんが乗ること（macOS の実測で約 9 ms。
Git Bash の Windows ではこれより大きい）。VS Code 拡張は sh を通さず、env が振り分けの sh を指していれば、
sh と同じ順で `.ccnavi/bin/<os>-<arch>/` の実行ファイルを自分で選んで起動する（Windows では sh を直接起動できないため）。
実行ファイルが無ければ次の候補へ進む。

| 配布元 | 配布先 |
|---|---|
| `dist/ccnavi/`（中身ごと） | `.ccnavi/bin/<os>-<arch>/`（`<os>-<arch>` は `dist/ccnavi.target` の目印） |
| `.ccnavi/common/rules.yml` | 同じパス |
| `.ccnavi/common/risks.yml` | 同じパス |
| `.ccnavi/config/phases.yml` | 同じパス |
| `.ccnavi/scripts/ccnavi-{ticket,review,git,common,push-approved,agree,fetch,clean,branches,start}.sh`、`ccnavi-common-{state,lock,c1,host,log}.sh`、`ccnavi-clean.js` | 同じパス |
| `.ccnavi/scripts/ccnavi-launcher.sh` | 同じパス。配ったあと実行ビットを付ける |

ルールと配点のひな形は共通レイヤー（`.ccnavi/common/`）へ、フェーズ定義のひな形はワークスペース自身のレイヤー
（`.ccnavi/config/`）へ配る。フェーズ定義の `scope` はそのワークスペースのレイアウトに合わせて書くもので、
共通レイヤーに置くと全プロジェクトに適用されてしまう（「ルールは 3 つのレイヤーの和で当たる」）。
3 本とも、無ければ最後の点検が「まだ無いもの」として並べる。振り分けの sh と、この機械で動く
実行ファイル（`.ccnavi/bin/<この機械>/ccnavi`）も同じ一覧に出る。

振り分けの sh は hook が直に起動するので実行ビットが要る。配った回は `chmod +x` を掛け、
配らない回でも実行ビットが外れていれば付け直す（中身は入れ替えない。Windows（`core.filemode=false`）で
足した sh は、別の機械で clone した直後に実行ビットが無いことがある）。
`--no-deploy` の回と、配布元と配布先が同じ回には触らない。付けられなかったら名指しする。`--check` は実行ビットの
外れた sh を揃っていないものに数え、`--lint` は error として報告する（「設定の検証」）。

配った組み立ての置き場は、配布先が git のリポジトリなら `.gitignore` にも足す（入れると履歴から消すのが難しい）。
置き場は配った機械のぶんだけ足す。同じ回に、配った実行ファイルの `--docs` が索引を書けるよう `**/index.jsonl` も
別の見出しで足す（「ドキュメントの索引」。`**/index.jsonl` か `index.jsonl` の行が既にあれば足さない。行は CRLF の `\r` と
行末の空白を落として比べる。`!**/index.jsonl` や `!docs/index.jsonl` のように `index.jsonl` を否定する行があれば、ユーザが
除外しているとみなして足さず、`--check` でもそう言う）。

```
# ccnavi が配る実行ファイル（scripts/ccnavi-setup.sh）
/.ccnavi/bin/darwin-arm64/

# ccnavi --docs が書く索引（scripts/ccnavi-setup.sh）
**/index.jsonl
```

足すのはワークスペースの `.gitignore` だけ。`projects/<名前>/` の各プロジェクトには触らない（そのプロジェクトのチケットの範囲で足す）。

足すのは `/.ccnavi/bin/<os>-<arch>/` だけ。振り分けの sh は追跡し、`.ccnavi/` を丸ごと無視もしない
（sh と `rules.yml` が git から消える）。

配布先に既にあるものは触らず、並べて見せる。入れ替えるのは `--force` を付けたときだけ
（ルールも sh も配布先で直されている前提）。

実行ファイルを入れ替えるときは、配布先に残っていた同梱物を先に消してから配る
（前の版が残ると新しい実行ファイルがそれを読み込んでしまう）。

名指しした `--deploy` が組み立てられていなければ、終了コード 2 で断る。
既定の配布元が使えないとき（組み立てていない、配布先が ccnavi 自身）は断らず、
配るのを諦めた理由を 1 行出して `.claude/settings.json` は書く。

## ルール

ルールファイルは YAML で、`deny` `ask` `allow` の 3 つのタイプに分かれる。
1 件のルールは、当てるツール・探すもの・見つけたときに返す文を 1 組で持つ。
`message` は `deny` だけに書く（必須）。止められたモデルに「なぜ止めたか、代わりに何を
するか」を伝える文で、`permissionDecisionReason` として届く。`ask` と `allow` に書くと
`--lint` が error にする（`ask` の文面はユーザの確認ダイアログにしか出ず、`allow` の文面はどこにも出ない）。
モデルに伝えたいことはタイプによらず `additionalContext` に書く。

```yaml
version: 1

deny:
  - id: force-push
    match: Bash
    glob: "*git push*--force*"
    message: リモートの履歴を書き換えます。送り直したい理由をユーザに伝えてください。

ask:
  - id: migrations
    match: Write|Edit
    glob: "*/migrations/*"
    additionalContext: 移行ファイルは実行前にユーザが中身を見る。何が変わるかを先に言うこと。

allow:
  - id: source
    match: Write|Edit
    glob: "*/src/*"
    additionalContext: src の下は自由に直してよい。ただし公開 API の名前を変えたら docs/api.md も直すこと。
```

`glob` と `regex` は必ず引用符で囲む。囲まないと YAML が先に解釈する
（`<<` はマージキー、`*` はエイリアス、`&` はアンカー、`!` はタグ）。

### `id` と表記の決まり

- `id` にコロンは書けない（レイヤーの名前を添えた `self:id` / `<プロジェクト名>:id` と見分けが付かないため）。
  `--lint` が error にし、その 1 件は読み込まれない。ルール・フェーズ定義・リスクの配点の 3 本とも同じ
- `glob` も `regex` も、どの機械でも大文字小文字を区別せずに当てる。`*/.ccnavi/*` は `.Ccnavi/` にも当たる。
  `allow` も表記違いにまで当たるので、既存のルールを持ち込むときは `--test-samples` で、通したくないものが
  通っていないことを確かめる
- 区別が要る `regex` は、区別したい部分だけを `(?-i:...)` で囲む。`[\\/](?-i:strict)[\\/]` は
  `/strict/` にだけ当たる。否定の文字クラスは要注意で、`\.[^c\\/]` は `.C` まで除外して除外の側が広がる。
  `\.(?-i:[^c\\/])` と囲む
- コマンドの名前も区別しない。`CAT file` は `cat file` と同じ判定になる。区別する機械で `CAT` という別の
  実行ファイルが在れば、`cat` 向けの `allow` がそれにも当たる
- `common` と `self` はレイヤーの名前として予約してある。`projects/common/` や `projects/self/` は、表記違い（`projects/Self/`）を
  含めてレイヤーとして数えない。そこに置いた宣言は 1 件も適用されず、そのプロジェクトへの書き込みは共通レイヤーだけで判定される。
  `--lint` が error で名指しし、`--agree` はその `project:` を承認しない

### ルールは 3 つのレイヤーの和で当たる

ルールとリスクの配点は 3 種のレイヤーに置ける。当たるのは共通レイヤー + そのツリーのレイヤーの和で、
足すだけ、上書き無し、厳しいほうが採られる。フェーズ定義は config（自身のレイヤーかプロジェクトのレイヤー）にだけ置き、
足し算をしない。使うのは親の `project:` が指す 1 本（空ならワークスペース自身のレイヤー）。設計は [11.4](docs/design/multi-repo/rules.md)、要求は
[要件 REQ-MLT](docs/requirements/multi-repo.md)。

| レイヤー | 置き場 | 何を置くか |
|---|---|---|
| 共通レイヤー | `.ccnavi/common/{rules,risks}.yml`（置き場は固定。`phases.yml` は置けず、あれば `--lint` が error） | どのツリーにも適用するもの |
| ワークスペース自身のレイヤー | `<ワークスペースルート>/.ccnavi/config/{rules,phases,risks}.yml` | ワークスペース自身のツリーにだけ適用するもの |
| プロジェクトのレイヤー | `projects/<名前>/.ccnavi/config/{rules,phases,risks}.yml` | そのプロジェクトのツリーにだけ適用するもの |

3 レイヤーとも置き場は固定で、環境変数では動かない（「設定」の「置き場は固定」）。
自身のレイヤーとプロジェクトのレイヤーは形が同じで、どちらも git プロジェクトルートの直下に置く。

| ツール | 当たるレイヤー |
|---|---|
| パスを持つツール: `Read` / `Grep` / `Glob` / `Write` / `Edit` / `NotebookEdit` | 共通レイヤー + 行き先の 1 レイヤー。行き先がプロジェクトの中ならそのレイヤー、ワークスペースのツリー（ワークスペースルートと、そこから切ったワークツリー）なら自身のレイヤー。`Grep` と `Glob` が探す場所を省いたときは `cwd` が行き先 |
| パスを持たないツール: `Bash` / `PowerShell` / `WebFetch` / `Skill` / `Agent` | 共通レイヤー + 自身のレイヤー + 全プロジェクトのレイヤー。どこに `cwd` があっても、`cd` を挟んでも同じ判定になる。あるレイヤーの `allow` は他のプロジェクトの作業にも適用される |

順は 共通レイヤー → 自身のレイヤー → プロジェクトのレイヤー（名前順）で、`deny` `ask` `allow` の順は変わらない。

- 読むのは元リポジトリに checkout されている版だけ。ワークツリーでレイヤーを直しても、統合されるまで反映されない。
  ワークツリーにしかないファイルは `--lint` が warn で知らせる。承認済みの領域（`.ccnavi/approved/` の承認済みチケット・
  マーカー・子の記録・フロー）は数えない。そこは親のワークツリーの版が読まれる
- 共通と config はどちらか片方だけでも、両方無くても動く。ファイルが無いのは「設定が無い」正常で空として扱い、記録の `fallback` にも
  `--lint` や診断の不備にも出さない（ルール・リスクの配点・フェーズ定義のすべて）。ルールは両方に無いときだけ組み込みの既定を使う。
  壊れているレイヤーも空として扱い、記録にレイヤーの名前が残り、`--lint` が error にする。組み込みの既定は
  使わない。共通レイヤーのルール自身が壊れて読めないときだけ組み込みの既定を使い、そのときレイヤーは足さない
- ワークスペースの中で共通レイヤーとして読むのは、ワークスペースルートの `.ccnavi/common/` だけ。プロジェクトの `.ccnavi/common/`
  （下のミラー）は、そのプロジェクトだけを clone したときにだけ共通レイヤーとして読まれ、そのとき `.ccnavi/config/` は自身のレイヤーになる
- id にはレイヤーの名前が付く。共通レイヤーは裸の `id`、自身のレイヤーは `self:id`、プロジェクトは `<名前>:id`。記録と文面にはこの形で出る
- 同じ宣言の重複は 1 本にまとめる。裸の `id` が同じで全欄（`{root}` を置き換えた後）が一致する定義は、後ろのレイヤーのものを
  捨て、`--lint` が info で知らせる
- 同じ `id` で中身が違うルールは両方とも適用され、`--lint` が warn で知らせる。`deny` と `ask` は増えるほうになる。リスクの配点も両方を数え、後ろのレイヤーの項目は
  `<レイヤー>:<id>` という名前になる。フェーズ定義は足し算をしないので、この衝突は無い
- プロジェクト向けの親チケットに着手するとき、ワークスペースルートの `.ccnavi/common/` の中身を、そのプロジェクトの `.ccnavi/common/` へ
  ミラーする。プロジェクトの `.ccnavi/config/` には触れない。ミラーは誰も編集しないので、上書きで失うものは無く、名指しも知らせも要らない。
  配点の `script:` の値は書き換えない。コピーするものがプロジェクトのレイヤーとして読めなければ、何もコピーせず着手しない。
  コミットはエージェントが `ccnavi-git.sh` で行う

できないこと: プロジェクトのレイヤーで共通レイヤーの `ask` を `allow` に緩める、共通レイヤーの `allow` をプロジェクトごとに外す、
他のプロジェクトのレイヤーをパスを持つツールの判定に足す（「A のルールを B にも」は共通レイヤーへ上げる）。

ccnavi ディレクトリの下（既定なら `.ccnavi/`。共通レイヤーの `.ccnavi/common/` も入る）は、ルールに書かなくても書き込みが止まる
（設定 3 本と配点のスクリプトは判定の中身そのものなので。「コアファイルを守る」）。

### 通す・聞く・止めるのどれでも、一言添える

`additionalContext` は、そのルールに当たったときにモデルへ渡す文。`message` が止められた側への言葉なのに対し、
こちらは「通すが、これを踏まえて進めろ」を書く。どのタイプにも書ける。

| タイプ | どう届くか（Claude Code 2.1 で実測） |
|---|---|
| `allow` | 応答の `additionalContext` として届く。判定の理由は無いので、これだけが届く |
| `ask` | ユーザが Yes を押したときだけ `additionalContext` が届く。No なら拒否の定型文だけが届いてターンが終わり、文は届かない。ダイアログには `[ccnavi] RULE_ASK (rule: …)` と subject が出る |
| `deny` | `permissionDecisionReason`（`message`）と一緒に `additionalContext` として届く |
| `dry-run` のとき | 止める代わりに返す文に続けて届く |

同じタイプに複数当たれば、全部の文を空行で区切って並べる。`--test` と `--test --json` の
`response` で、書いた文が何と一緒に届くかが見える。

`additionalContextOnce` は、1 つの文脈で最初に当たったときだけ届く文。文脈はセッション 1 本で、
サブエージェントはその 1 回の起動ごとに別に数える（親で渡した文は子にも 1 度届く）。
セッションの開始（起動・再開・compact の後）で忘れ、改めて 1 度届く。記憶は
`logs/state/once-<セッション>-<エージェント>.json` に置く。state の置き場が無い
（`--state ""`）なら毎回届く。

`additionalContext` と両方書けば、初回は 2 つを空行で並べて届け、2 回目からは
`additionalContext` だけが届く。

```yaml
allow:
  - id: source
    match: Write|Edit
    glob: "*/src/*"
    additionalContext: src の下を直したら docs/api.md も見直すこと。
    additionalContextOnce: >-
      src の下は自由に直してよい。公開 API の名前を変えたら docs/api.md も直し、
      テストは tests/ に同じ名前で置く。CHANGELOG は締めるときにまとめて書く。
```

### 何回かに 1 度だけ渡す

`every: N` は「渡す回」の間隔を決める欄。そのルールが当たった回数が `N` の倍数になった回だけが
渡す回になる。書かなければ間隔は 1。

| 欄 | いつ渡るか | `every: 5` のとき |
|---|---|---|
| `additionalContext` | 渡す回のたび | 5・10・15… 回目 |
| `additionalContextOnce` | 渡す回の最初の 1 回 | 5 回目だけ。10 回目には渡さない |

```yaml
allow:
  - id: nudge-rule-check
    match: Write|Edit|NotebookEdit
    glob: "*"
    every: 5
    additionalContext: >-
      ここまでに 5 件の編集があった。次へ進む前に、この変更が CLAUDE.md と
      docs/adr/ の決まりに沿っているかを Agent ツールで見てもらうこと。
```

- この例は `--lint` の warn（「広い allow に additionalContext がある」）を 1 件出すが、`every` を書いた
  ルールには当てはまらない指摘なので、`glob` は広いままでよい
- 数えるのは「そのルールが当たった回数」。同じファイルを 5 回直せば 5。`Bash` を `match` に書かない限り
  シェルやビルドが書いたぶんは数えない
- 数えた回数は `SessionStart`（起動・再開・compact の後）で 0 に戻る。`additionalContext` に添えたときは届くのが
  遅れ（N 回に届く前に 0 に戻ることが続けば一度も届かない）、`additionalContextOnce` に添えたときはセッション全体で 1 度より多く届く。
  正確に N 回ごとを守る欄ではない
- `additionalContextOnce` に添えると、1 回目ではなく N 回目に届く。「1 回目に言いたいこと」も要るならルールを 2 件に分ける
- 回数は文脈ごと（セッションと、サブエージェントならその起動）に数え、`logs/state/once-*.json` に
  「鍵 → 回数」で残る。state の置き場が無い（`--state ""`）なら数えずに毎回届く
- `every: 1` には `--lint` は何も指摘しない。`0`・負・整数でない値は error で名指しし、判定は 1 として扱って通す。
  渡すものが 1 つも無い `every` は warn

### ターンの終わりに止めて渡す

`match: Stop` の `allow` のルールは、メインエージェントのターンの終わり（`Stop`）に当たる。
渡す回（`every` で決まる回）にだけ `{"decision": "block", "reason": "NUDGE_STOP_RULE: …"}` で止め、ルールの文を渡す。
振り返り（`docs/claude/skill-review.md`）のための仕組みで、`reason` の頭には実行ファイルが決まった前置きを付ける。
前置きの中身は、タスクの続きではないこと、振り返りだけをすること、ユーザへの問いで終わったターンならその問いを最後に書き直すこと、何も無ければ「振り返り: 無し」と書くこと。
当てる文字列は無いので `glob: "*"` と書く。

```yaml
allow:
  - id: skill-review-every-10
    match: Stop
    glob: "*"
    every: 10
    additionalContextFile: docs/claude/skill-review.md
```

- 読むのは共通レイヤーと自身のレイヤーだけ。プロジェクトのレイヤーに書いたものは使わない（外のリポジトリの 1 行でメインのターンを止めさせない）。同じ id は 1 本だけ
- `every` が 2 より小さいものは使わない。本文のファイルはワークスペースルートの版だけを読む
- 回数の記録は `logs/state/stop-<セッション>.json`。compact・再開・clear では捨てず、起動のときと古いセッションの後始末でだけ捨てる
- 同じ `Stop` で `finish` の促し（下の「ターンの終わりの報告」）が止めるなら、そちらだけを出し、このルールは数えもしない
- `stop_hook_active` が真の回、サブエージェント、記録を置けない・読めない・書けないときは止めない（止め続けないため）。
  サブエージェントには、代わりに `SubagentStart` で「気づいたスキル候補は最後の報告に節を足して返す」の 1 行を渡す
- `dry-run` では止めず、止めたはずの文を `systemMessage` に載せる。記録の `decision` は `nudge`、`rules` は渡したルールだけ
- `--test Stop "(stop)"` と見本の `tool: Stop` で、使われるルールを確かめられる
- `deny` / `ask` に書いたもの（`Bash|Stop` なら `Stop` の部分）、プロジェクトのレイヤーに書いたもの、`(stop)` に当たらない表記、`every` の無いものは `--lint` が warn で知らせる

### ファイルの本文を渡す

`additionalContextFile` と `additionalContextOnceFile` は、文の代わりに（または文に続けて）
ファイルの本文を渡す。それぞれ `additionalContext` と `additionalContextOnce` の後ろに、空行で区切って並ぶ。
once の記憶は文とファイルで分けず、ルール 1 件で 1 度と数える。

```yaml
allow:
  - id: tests
    match: Write|Edit
    glob: "*/tests/*"
    additionalContextOnce: テストの決まりは次のとおり。
    additionalContextOnceFile: docs/testing.md
```

- パスはワークスペースルートからの相対で書く。絶対パスと `..` で上に出るパスは
  `--lint` が error にし、実行時も読まない
- 行き先（Bash なら cwd）がワークツリーの中なら、まずワークツリーの同じパスを見る。
  無ければその元リポジトリ、最後にワークスペースルート
- ファイルが無ければ何も足さない。`--lint` はそのことを warn で知らせる
- 読むのは先頭 4000 文字まで（固定）。超えたら先頭だけを載せ、末尾に「先頭だけを載せた。
  続きはこのファイルを読むこと」と添える。`--lint` は上限を超えるファイルにも warn を出す
- `--test` と「判定を試す」には、読んだ本文がモデルに届くものと同じ形（切った状態）で出る

広い `allow` には書かない。当たった回ごとに同じ文がコンテキストに積まれる。`--lint` は、何にでも当たる `allow`
（`glob: "*"` など）と選択肢が 3 つ以上ある `regex`（`(ls|cat|sed)`）に書いた
`additionalContext` を warn にする。狭いルールに分けて、そのルールにだけ書く。

### 強さ

強い順に `deny` `ask` `allow`。どれにも当たらなければ、ccnavi は判定を持たない。

| 当たったタイプ | 呼び出しはどうなるか |
|---|---|
| `deny` | 止まる |
| `ask` | ユーザに確認が出る（`RULE_ASK`） |
| `allow` | ccnavi は何も返さない。その先は Claude Code の権限モードが決める |
| どこにも当たらない | Claude Code の権限モードに従う（`UNDECLARED`） |

1 件でも `deny` に当たれば拒否で、弱いタイプは見に行かない。同じタイプに複数
当たったら全部の文面を返す。

`allow` は Claude Code に「許可」を返すのではない。ccnavi が `permissionDecision` を返すのは `deny` と `ask` の
ときだけで、`allow` では何も返さない（`additionalContext` があればそれだけを添える）。その先は Claude Code の
権限モードと `settings.json` の `permissions` が決めるので、`allow` を足しても Claude Code 側の確認は飛ばせない。

`allow` を書くと消えるのは、ccnavi 自身が出す確認と拒否。言及の無い呼び出しは、
知らないモードでは ccnavi が確認を出し、`dontAsk` / `bypassPermissions` では通さない
（次の節）。`allow` に当たればどちらも起きない。`auto` / `default` / `acceptEdits` / `plan`
は権限モードに渡すので変わらない。記録には `decision` が `allow` の行が残り、
`additionalContext` を当てる先にもなる。

ワークツリーに結び付いた承認済みチケットがあれば、その範囲の判定とも比べて強い側を採る。
ルールの `allow` に当たっても、範囲の外なら止まる（「判定の鍵はファイルの行き先」）。

`ask` に当たった呼び出しは、権限モードによらず確認に出す（ユーザが意図して置いた確認ポイントなので）。

### ルールが言及していない呼び出し

ccnavi は判定を返さず、Claude Code の権限モードに従う。

| `permission_mode` | 呼び出しはどうなるか | 記録の `decision` |
|---|---|---|
| `auto` | classifier が判断する | `handover` |
| `default` / `acceptEdits` / `plan` | Claude Code 自身の権限の仕組み（`settings.json` の `permissions` と、モードごとの既定）が決める | `handover` |
| 不明なモード / モードが来ない | ユーザに確認が出る | `ask` |
| `dontAsk` / `bypassPermissions` | 通さない。`CCNAVI_GUARD_UNWATCHED=disable` なら委ねる | `deny` |

渡した回も記録には `decision` が `handover` の行で残り、ユーザに聞いた回の `ask` とは混ざらない。

```sh
jq -r 'select(.decision == "handover") | .subject' logs/decisions.jsonl | sort | uniq -c | sort -rn
```

確認できる者が居ないモードでは、ask を返しても誰も答えないまま通るので通さない（REQ-PRE-08）。
知らないモードの名前は確認にする。

`CCNAVI_GUARD_UNWATCHED=disable` と書いたレイヤーでは、`dontAsk` と `bypassPermissions` でも判定を返さず、
そのモードの取り決めに委ねる。`--lint` が、切れていることを warn で知らせる。

読み切れなかったコマンド（`PARSE_UNCERTAIN`）は、この設定でも権限モードに委ねない
（読めなかったという事実が委ねた先に伝わらないため）。

既定は許可ではなく、判断を誰かに渡すこと。`allow` は「このプロジェクトで普通にやること」を並べる場所で、
権限を配る場所ではない。1 行足すたびに、ccnavi が何も言わない範囲が広がる。

判定が対象を取り出せるのは次のツールだけ。名前は Claude Code の権限ルール
`ToolName(指定子)` から括弧の中を除いたものに揃えてある。

| `match` に書く名前 | 当てる対象 |
|---|---|
| `Bash` `PowerShell` | コマンド（`Bash` はシェルとして読んでから当てる） |
| `Read` `Edit` `Write` `NotebookEdit` | ファイルのパス（行き着く先まで解いてから当てる） |
| `Grep` `Glob` | 探す場所のパス（`path`。省略されていれば呼び出し側の cwd） |
| `Skill` | スキル名 |
| `Agent` | 起動の見出し（`description`、無ければ `prompt`） |
| `WebFetch` | URL |
| `Stop` | 当てる文字列は無い（固定の `(stop)`。`glob: "*"`）。ツールではなく、`allow` に書いてターンの終わりに文を渡すためだけのもの（上の「ターンの終わりに止めて渡す」） |

`WebSearch` のように対象を取り出せないツールは判定されないまま通るので、
`allow` に書いても意味の無い行になる（`--lint` が指摘する）。`match` は
名前をそのまま比べるので、`Bash` のルールは `PowerShell` に及ばない。
及ぼしたければ `Bash|PowerShell` と並べる。

`glob` の意味は標準ライブラリの `fnmatch` そのまま。`*` が任意の文字列、
`?` が 1 文字、`[abc]` が文字クラス。

文字列全体に当たる。部分一致が欲しければ前後に `*` を自分で書く。
`git push` は素の `git push` にしか当たらず、`cd /repo && git push` には当たらない。
`*git push*` と書けば当たる。

語の切れ目は入らない。`*sed*` は `sedate` にも当たる。右側だけなら空白を
書いて `*sed *` とすれば切れ目を表せる。左側は glob では書けない（`*git push*` は `legit push` にも当たり、
`* git push*` は行頭の `git push` が外れる）。左の切れ目が要るルールは `regex` を使う。

区切り文字だけは正規化する。`/` と書けば `\` にも当たる（`fnmatch` の外で足している唯一の処理）。
`*/secrets/*` は `C:\repo\secrets\key` にも当たる。

`{root}` はワークスペースルートに置き換わる。hook なら `CLAUDE_PROJECT_DIR`、端末なら `--root` の
実パス。glob なら `{root}/wip/*`、regex なら `^{root}[\\/]` のように書く。
区切りは `/` と `\` のどちらにも当たり、大文字小文字はどの機械でも区別しない。
文面（`message` / `additionalContext` / `additionalContextOnce`）に書いた `{root}` も、モデルへ渡すときに
実パス（区切りは `/`）になる。拒否の文面で sh を案内するときは `'sh {root}/.ccnavi/scripts/...'` と書く。
`--explain` と `--test` は書いた表記のまま `{root}` を出す。このリポジトリのルールでは、
ワークスペースルート直下の Write / Edit を止める `main-tree` がこれを使っている。

書けないものは `glob` の代わりに `regex` に正規表現を書く。両方書いたルールは受け付けない。
先読み・後読み・後方参照は受け付けない（エンジンをまたいで同じ意味に保ち、組み合わせ爆発を避けるため。
判定の途中で止まったままの hook は期限に達し、呼び出しはそのまま通ってしまう）。

### 同じ呼び出しを繰り返し止めたとき

止められたエージェントは、言い回しを少し変えて同じことを打ち直すことがある。同じルールが同じ呼び出しを
`CCNAVI_DENY_REPEAT` 回（既定 3）止めたら、その回から拒否の文面の末尾に「言い換えて打ち直さず、ユーザに相談する」一文を足し、
ターンの終わりの `systemMessage` にもルールの id と回数を載せる。**判定は変わらない**（止めたものは止めたまま）。

- 数える鍵は（判定を下したルールの id、無ければ根拠コード, 均した対象のハッシュ）。均すのは、引用符（`'` `"` `` ` ``）を除き、
  連続する空白（改行・タブを含む）を 1 つにして前後の空白を除くことだけ。大文字と小文字は分けたまま
- 数えるのは `enable` で実際に止めた `deny` だけ。`dry-run` と `ask` は数えない
- 記録はセッションごとに `logs/state/denied-<セッション>.json`。対象の表記は置かずハッシュだけを置く。payload にセッションが無ければ数えない
- 記録を読めない・書けないときは、何も知らせず一文を足さない。数えるのに失敗しても判定は変わらない

## ワークツリーを VS Code から見えるようにする

ccnavi は作業を `.claude/worktrees/` の中でさせる。VS Code の設定に下の 1 行が無いと、
エディタからはワークスペースルートのブランチしか見えない。

```json
"git.detectWorktrees": true
```

`sh scripts/ccnavi-setup.sh <ワークスペースルート>` が、`.claude/settings.json` と同じ 1 回で
`.vscode/settings.json` にもこれを書く。無ければ作り、あれば足りないキーだけを足す。
VS Code の他の設定は残し、`false` と書いてあれば変えずに並べて見せる。

`.vscode/settings.json` にコメントや末尾のカンマがあると（`jq` が読めない）、そのファイルは触らずに、
何を足せばよいかだけを出し、`.claude/settings.json` は書く。自分で書きたいときや
VS Code を使わないときは `--no-vscode` を付ける。

チケットがどのワークツリーでどこまで進んでいるかは、VS Code の拡張「ccnavi ボード」
（`extensions/vscode/ccnavi-board/`）で見られる。拡張は `ccnavi --explain --json` の出力を
並べるだけ。承認はボードのオーバーレイで一覧を見せ、ユーザが押したら `--agree --yes` を子プロセスで
打つ（形は下の「承認の JSON」）。レビューで残った指摘は、フェーズ行の「決める」で指摘ごとに対応方針を
選び、拡張が `ccnavi-review.sh decide` を子プロセスで打つ（形は下の「残った指摘の JSON」）。
`close-early` はボードに置かず、端末で打つ。組み立て方と使い方はそこの README、
出力の形は下の「ボードの JSON」。

同じ拡張の「ルール管理画面」で、ルールファイルを画面で直し、保存する前に判定を試せる。編集する 1 本（共通・ワークスペース・プロジェクト）は切り替えられ、共通+ワークスペース、共通+プロジェクトの足し算は読み取りで見られる。
判定は `ccnavi --test --json` と `--test-samples --json` を通る（形は「試験の JSON」）。
hook の一覧は `.claude/settings.json` と `settings.local.json` を読むだけで書き換えない。作業中のチケット
（承認済みチケットが `doing`）がある間は保存できない（セッションの途中で判定が変わるのを避けるため）。

同じ拡張の「リスク管理画面」で、実績で測るリスクの配点（`.ccnavi/common/risks.yml`、ワークスペースとプロジェクトの `.ccnavi/config/risks.yml`）の境目の点と項目を
画面で直せる。編集する 1 本は切り替えられ、共通+ワークスペース、共通+プロジェクトの足し算は読み取りで見られる。保存の前に `--lint` を通す（共通は `--risk`、ワークスペースとプロジェクトは `--project-risk-file` で、共通の設定と合わせて検証する）。点を数えるのは実行ファイルで、拡張は差分を数えない。
ファイルが無ければ組み込みと同じ値で作れる。`CCNAVI_TICKET_CONTROL` が `disable` なら入口ごと出ない。

同じ拡張の「フェーズ管理画面」で、フェーズ定義（「フェーズ定義と計画」の節）を画面で直せる。対象は自身のレイヤー
（`.ccnavi/config/phases.yml`）とプロジェクトのレイヤーの 2 種（共通レイヤーには置けない）。
保存の前に `--lint --project-phases-file` を通すので、提案がワークツリーに在る親の計画が指すフェーズ定義を消すとそこで止まる
（承認済みチケットの計画は照合しない。チケット制御が disable なら照合は走らない）。
子の範囲が上限に収まるかを判定するのは実行ファイルで、拡張はフェーズ定義を書く場所だけ。共通レイヤーに `phases.yml` を置けない
（あれば画面の上部に error を出す）。組み込みの既定も雛形も無い。フェーズ定義は自身のレイヤーかプロジェクトのレイヤーに置き、プロジェクト管理画面から開く。`CCNAVI_TICKET_CONTROL` が `disable` なら入口ごと出ない。

## Bash のコマンドは実行される部分だけを見る

`Bash` のルールは、コマンド文字列そのものではなく、シェルが実際に実行する部分に
当てる。禁止された語を書いただけの操作は止まらない。

```sh
grep -n "git push" README.md      # 通る。git push は grep の引数
echo "git push origin main"       # 通る
grep -n '$(git push)' f           # 通る。単一引用の中は文字
grep -n "\$(git push)" f          # 通る。\$( は置換にならない
# git push origin main            # 通る。コメント
cat <<'EOF' > notes.md            # 通る。区切りを引用したヒアドキュメントの本文は文字
git push origin main
EOF

git push origin main              # 止まる
cd /repo && git push              # 止まる
/usr/bin/git push                 # 止まる
GIT_DIR=/repo/.git git push       # 止まる
echo $(git push origin main)      # 止まる。$( ) の中は実行される
echo "$(git push origin main)"    # 止まる。二重引用の中の $( ) も実行される
cat <(git push)                   # 止まる。プロセス置換の中も実行される
if true; then git push; fi        # 止まる。予約語の後ろはコマンドの先頭
```

シェルが実行する中身は、書いた場所によらず独立したコマンドとして読む。二重引用の中の `$( )`、
プロセス置換 `<( )` `>( )`、区切りを引用しないヒアドキュメント（`<<EOF`）の本文の `$( )` がこれにあたる
（バッククォートは読まずに止める。下の「書き直しを求める形は止めて案内する」）。中身は外側のコマンドの後ろにつながれ、外側の置換のあった場所には
`$` が 1 文字残る。`grep -n "$(git push)" f` は `grep -n $ f` と `git push` の 2 本として読まれるので、
grep の allow は後ろの `git push` まで通さない。外側のコマンドは分割されないので、`find $(pwd) -name x -delete` の
`-delete` は find と同じコマンドとして見える。

引用の外の改行はコマンドの区切りになる。`#` がコメントになるのは語の始まりだけで、`a#b` や `$#` の `#` は文字。
`if` `then` `do` `{` などの予約語はコマンドの位置にあればそれだけで 1 本になり、後ろの語がコマンドの先頭になる。

引用が 1 語につないだ空白は、コマンドと引数の間の空白としては読まれない（`"git push"` と `git push` を分ける）。
引用の中身そのものは残るので、`cat "/home/u/.env"` は `.env` のルールに当たる。

切れ目にはルールから見える目印が 2 つ置かれる。コマンドとコマンドの間は `\x00`、
引用がつないだ空白と語の中の演算子の両側は `\x01`。ルールの regex で
`[^\x00]*` と書けば「同じコマンドの中」を指す。`.ccnavi/common/rules.yml` では、
`grep -n "git push" README.md` や `find . -name "a b"` のような引用付きの grep / cat / find が、
後ろにコマンドが続かなければ `prefer-read-grep` / `prefer-glob`（allow）に当たる。
`cat README.md | head -20` は `\x00` をまたぐので当たらない。

語の中に入った `>` `<` `|` `&` `(` `)` も、両側に `\x01` が置かれ、演算子としては読まれない。
`grep -n "x>y" notes.md` の `>` は文字で、素の `echo x>f` は演算子として残る。
例外は、引用符の中身が演算子の文字だけでできている 1 語（`grep -n ">" f`）で、
`shlex` が引用の有無を返さないので演算子と区別が付かない。

### 文字として書きたいとき

二重引用の中の `$( )` とバッククォートは、シェルが実行するので止まる（バッククォートは中身に依らず止まる）。
引用の中から切り出したコマンドにだけルールが当たったときは、返る文面にそのことと回避策が添えられ、
記録と `--test` に `quoted`（そのルールの id）が出る。

| 書きたいもの | 止まる書き方 | 通る書き方 |
|---|---|---|
| 本文にバッククォートを含む issue やマージリクエスト | ``gh issue create --body "use `git push` here"``（`DENY_BACKQUOTE`） | 単一引用で包む、`` \` `` で書く、`--body-file <ファイル>` |
| 複数行のコミットメッセージ | `commit -m "$(cat <<'EOF' … EOF)"`（heredoc のルール） | 本文を Write で置いて `commit -F <ファイル>`、改行を含む `-m "…"` |
| git の値を使うコマンド | `cd "$(git rev-parse --show-toplevel)"` | ラッパースクリプトで値を出して読み、次のコマンドにその値を書く |
| 今の場所を渡す検索 | `grep -rn foo "$(pwd)"`（allow から外れて確認になる） | 値をそのまま書く |

単一引用の中に `'` を書くときは `'"'"'` とつなぐ。

### 読み切れないとき

静的に読めないコマンドは、生の文字列との一致に縮退する。縮退した拒否の文面には、
読めなかったので生の文字列に当てたという断りが、`note:` で始まる英文の 1 行で付く。

縮退した呼び出しに `allow` は当てず、`deny` と `ask` は当てる（生の文字列に当たりすぎるぶん、判定は厳しい側にずれる）。
どこにも当たらなければコード `PARSE_UNCERTAIN` でユーザに確認が出る。ここは権限モードに委ねない。

コメントだけの行のように、実行される部分が何も残らないコマンドは判定に入らない。
記録には `nothing-to-run` が残る。

| 縮退する条件 | 例 |
|---|---|
| 文字列をコードとして実行する呼び出し | `bash -c`、`sh -c`、`eval`、`xargs`、`find -exec` |
| 引用やヒアドキュメントが閉じていない | `echo "git push` |
| 置換が閉じていない | `echo $(git push` |

置換の中身が 1 つでも読み切れなければ、コマンド全体が縮退する。シェルによって読み方が分かれる形と
バッククォートは、縮退ではなく止めて書き直しを案内する（下の「書き直しを求める形は止めて案内する」）。

引用でコマンド名を区切った `"git" push` や `g"it" push` は縮退しない。
シェルと同じ字句規則で語を組み直すので、本来の push としてそのまま止まる。

perl や python は縮退の対象に入れていない（`perl -pi -e 's/git push/.../' README.md` のような 1 行編集を通すため）。

### 書き直しを求める形は止めて案内する

書き直す方法が必ずあり、読み分けると規則が増えるか、読み違えると止めずに通してしまう形は、
読み解かずにルールより先に形ごとの理由コードで止め、書き直し方を返す。
`sh -c`・`eval`・`xargs`・`find -exec` は止めずに、縮退と「実行役のコマンド」の読みで扱う。

#### ブレース展開は語を並べて書く

引用の外の `{a,b}` や `{1..3}` は、シェルが実行する前に複数の語に広げる（`{git,push,origin,main}` は
`git push origin main` として実行される）。ccnavi は展開せず、ルールより先にコード `DENY_BRACE_EXPANSION` で止める
（広げ方が bash と zsh で分かれるため）。止めた文面は
見つけた表記と書き直し方を示す。

| 止まる書き方 | 通る書き方 |
|---|---|
| `grep -rn foo --exclude-dir={node_modules,.git} .` | `grep -rn foo --exclude-dir=node_modules --exclude-dir=.git .` |
| `cp f{,.bak}` | `cp f f.bak` |
| `mkdir -p src/{a,b}` | `mkdir -p src/a src/b` |
| `touch file{1..3}` | `touch file1 file2 file3` |
| ブレースを文字として渡す `echo {a,b}` | `echo '{a,b}'` |

引用の中、`\{`、ヒアドキュメントの本文、コメントのブレースは止まらない。`find . -exec rm {} \;` や
`HEAD@{1}` のように、カンマも範囲も無いブレースも止まらない。代入の右辺（`x={a,b}`）、case のパターン
（`case $x in {a,b})`）、`[[ $f == *.{jpg,png} ]]` はシェルが広げないが止まる。許容した誤検知で
（[設計 12.2](docs/design/limits.md#122-許容する誤検知)）、引用するか、パターンを `a|b)` や `*.jpg || … *.png` のように書けば通る。

#### コマンド名はそのまま書く

変数・置換・グロブをコマンド名に置くと、どのプログラムが走るかが書かれた文字列に無く、どのルールも当たらない
（`c=git; $c push origin main`）。コード `DENY_COMMAND_NAME_EXPANSION` で止める。
前に置いた代入とリダイレクト（`FOO=1 >/dev/null $c`、`{fd}>/dev/null $c`）の後ろも、実行役のコマンドの中
（`env $c`・`sudo $c`・`sh $SCRIPT`）も見る。`sh -c "$c"` と `eval "$(…)"` の文字列の中は見ず、
読み切れないものとして確認にする。

| 止まる書き方 | 通る書き方 |
|---|---|
| `c=git; $c status` | `git status` |
| `G="sh .ccnavi/scripts/ccnavi-git.sh"; $G add f` | `sh .ccnavi/scripts/ccnavi-git.sh add f` |
| `sh $S/run.sh` | `sh /tmp/work/run.sh`（パスをそのまま書く） |
| `/usr/bin/gi? status` | `/usr/bin/git status` |

引数の位置の変数とグロブ（`echo $HOME`、`ls *.py`、`cd $S`、`timeout $T make`）、`[ -f x ]`、`case $x in` は止まらない。

#### バッククォートは `$( )` か単一引用で書く

バッククォートは二重引用の中でも、区切りを引用しないヒアドキュメントの中でも実行される。ccnavi は中身を読まず、
コード `DENY_BACKQUOTE` で止める。コマンド置換なら `$( )` で書く。文字として渡したいなら（issue やマージリクエストの本文の
Markdown など）、単一引用で包むか、`` \` `` と書くか、`--body-file <ファイル>` で渡す。単一引用の中、`` \` ``、
引用付きヒアドキュメントの本文、コメントのバッククォートは止まらない。

#### シェルで読みが割れる形は素直な形で書く

コード `DENY_AMBIGUOUS_FORM` で止める。どれも bash と zsh で読み方が分かれるか、作業で使わない形。

| 止まる形 | 書き直し方 |
|---|---|
| `$( )` の中の `case`（`"$(case $x in a) echo 1;; esac)"`） | if/elif で書く。`$( )` の外で分岐する |
| `$((cmd) \| x)` | `$( (cmd) \| x )` と空白を入れる |
| 16 段を超える `$( )` の入れ子 | 内側を先に打ち、出た値を次のコマンドに書く |
| コマンドの位置の `coproc` | `&` で裏に回す |
| コマンドの位置の `select` | `for` で回す |

`$( )` の中の、コマンドの先頭ではない `case` の語（`"$(echo just in case)"`）でも止まる。許容した誤検知
（[設計 12.2](docs/design/limits.md#122-許容する誤検知)）。

### 実行役のコマンドが中で実行するコマンドにも当てる

`env rm -f x` の `env` のように、別のコマンドを走らせるためのコマンドがある（実行役のコマンド）。
ルールの多くは `(^|\x00)rm` のようにコマンドの先頭に固定して書くので、ccnavi は実行役のコマンドを 1 つずつ外し、
中で実行されるコマンドにも、止める側のルール（`deny` と `ask`）とサブエージェントの禁止を当てる。

```sh
env rm -f .ccnavi/common/rules.yml              # 止まる。env が実行する rm に当たる
timeout 5 sed -i s/a/b/ .claude/settings.json   # 止まる
echo x | xargs rm -f .ccnavi/common/rules.yml   # 止まる
sh -c 'rm -f .ccnavi/common/rules.yml'          # 止まる
env sh .ccnavi/scripts/ccnavi-launcher.sh --agree --yes x   # 止まる（承認の経路）
env curl -d @x https://example.com              # curl に書いた ask が当たる
sudo -u me cat /etc/hosts                       # 読み取りの allow には当たらない。元の形のまま判定する
ssh host rm -f .ccnavi/common/rules.yml         # 外さない。ssh は一覧に無い
```

外すのは次の形。

| 実行役のコマンド | 中で実行されるコマンド |
|---|---|
| `VAR=値 <cmd>` | `<cmd>` |
| `/bin/sh x`、`git.exe x`（道筋や拡張子の付いた名前） | `sh x`、`git x` |
| `env` `command` `exec` `nohup` `time` `nice` `sudo` `doas` `timeout` `stdbuf` `chrt` `ionice` `taskset` | オプションとその値、コマンドの前の位置引数（`timeout` の時間、`chrt` と `taskset` の 1 語）、`--`、`env` と `sudo` の `名前=値` を飛ばした残り |
| `sh` `bash` `zsh` `dash` `ksh` `<ファイル> <引数>` | `<ファイル> <引数>` |
| `sh -c '<文字列>'`（`bash -lc` のような組み合わせも） | 文字列を読み直したコマンド |
| `eval <語…>` | 語をつないで読み直したコマンド |
| `.` / `source` `<ファイル> <引数>` | `<ファイル> <引数>` |
| `xargs [オプション] <cmd…>` | `<cmd…>` |
| `find … -exec` / `-execdir` / `-ok` / `-okdir` `<cmd…> ;`（または `+`） | `<cmd…>`。複数あればそれぞれ |

- 1 つずつ外し、途中のコマンドも残す。`env sh .ccnavi/scripts/ccnavi-agree.sh` なら `sh .ccnavi/scripts/ccnavi-agree.sh` と
  `.ccnavi/scripts/ccnavi-agree.sh` の両方に当てる。外す深さは 4 まで（元の形は数えない）。1 回の読みで作る語の数にも
  上限（2000）がある
- シェルや `.` / `source` に渡したファイルの先は外さない（スクリプトの引数）。
  `command -v` / `-V`（探すだけ）と `sh -s`（標準入力を読む）も外さない
- 読み切れない形（`sh -c`・`eval`・`xargs`・`find -exec`・`source`・`.`）でも、語に分けられる限り外す。
  閉じない引用と閉じないヒアドキュメントでは外さない
- 一覧は ccnavi の組み込みで、`rules.yml` からは足せない。一覧に無いもの（`ssh host <cmd>`・`python -c`・`perl -e`・`script -c`・`watch`・
  `busybox sh` など）は外さないので、先頭に固定したルールは外れる。変数の値・alias・関数
  の中までは追わない（コマンド名に変数を置いた `$SUDO rm …` は、上の「コマンド名はそのまま書く」で止まる）

当てる先は止める側だけにする。

| 当てる先 | 元の形 | 中で実行されるコマンド |
|---|---|---|
| `deny` | 当てる | 当てる |
| `ask` | 当てる | 当てる |
| サブエージェントの禁止 | 当てる | 当てる |
| `allow` | 読み切れたときだけ当てる | 当てない |
| 止めている間でも通す形 | 当てる | 当てない |
| チケットの範囲 | 当てる | 当てない |

中で実行されるコマンドは止める側に足す当て先で、元の形の判定を消さないので、外し方を読み違えても緩くはならない。
`allow` や止めている間でも通す形に当てると、実行役のコマンドを前に置くだけで通るようになる
（`sudo -u me cat /etc/hosts`、`env sh .ccnavi/scripts/ccnavi-review.sh confirm --phase 1`）。

中で実行されるコマンドで当たったときは、拒否と確認の文面に 1 行足す。

```
[ccnavi] DENY_COMMAND_PATTERN (rule: builtin-guard-setting-files)
subject: env rm -f .ccnavi/common/rules.yml
`env` が実行する `rm -f .ccnavi/common/rules.yml` に当たりました。
ガード自身の設定と hook を、シェルからの書き込みで変えようとしています。…
```

記録には欄 `unwrapped` に当たったコマンドが残り（「記録」）、`--test` は `unwrapped:` の行で出す（「ルールが何に当たるかを確かめる」）。

読み切れない形（`sh -c '…'`・`xargs` など）で、当たったルールが全部中で実行されるコマンドで当たったときは、拒否のコードを
`PARSE_UNCERTAIN` ではなく `DENY_COMMAND_PATTERN` にし、読めなかったので生の文字列に当てたという断り（`note:` の行）も付けない。記録の `degraded` は残る。
`PARSE_UNCERTAIN` の件数で読み切れない拒否を数えている集計とはずれる。

### ヒアドキュメント自体は既定のルールで止める

既定のルールは `<<` を含む Bash 呼び出しを止める。ヒアドキュメントで書いたファイルは、
`permissions` の宣言も `PostToolUse` に登録した検査も通らないため。
代わりは `Write` / `Edit` ツール。プログラムに読ませる入力も、`Write` でファイルに置いてから渡す。

```sh
cat <<'EOF' > notes.md      # 止まる
uv run python - <<'PY'      # 止まる。ヒアストリング <<< も同じ
uv run python scratch.py    # 通る。scratch.py は Write で置く
```

算術式の `$((1 << 2))` は左シフトなので、読む前に取り除いてある。

`grep -n "<<" README.md` は止まる（引用された `<<` と素の `<<` を `shlex` が区別しない）。
許容する誤検知として設計に記載してある（設計 [6.3](docs/design/pre-check.md#63-bash-は実行される部分だけを見る)、[12.2](docs/design/limits.md#122-許容する誤検知)）。対象をファイルへ移せば回避できる。

## ファイルのパスは行き着く先で見る

`Read` `Write` `Edit` `NotebookEdit` のルールは、payload に来た
`file_path` そのものではなく、絶対パスに直し `..` を解決しシンボリックリンクを解いた
結果に当てる。同じ場所を指す別の表記でルールを外せない。

```sh
.env                  # どれも同じ判定に行き着く
./.env
docs/../.env
/abs/path/to/.env
```

相対パスは payload の `cwd` から決まる。まだ存在しないファイルへの書き込みは
シンボリックリンクを解けないので、絶対パスにして `..` を解決するところまでにとどめる。

## 探すツールが読むファイルは、ルールに届かない

`Grep` と `Glob` の対象は探し始める場所のパスで、そこから降りて読まれたファイルは
判定に届かない。`Grep(path=<git プロジェクトルート>)` は `.env` や `secrets/` の中身を返しうるが、
ルールは起点にしか当たらない。

だから共通レイヤーの `credentials` ルールの `match` は `Bash|Read|Write|Edit|NotebookEdit` で、
`Grep` と `Glob` を含めていない。

`Grep` と `Glob` は ccnavi が受け持たない。探すツールが読むファイルを見るのは、下の表の上 2 つ。

| 段 | 何を止めるか |
|---|---|
| `.gitignore`（ripgrep が読む） | `Grep` が起点から降りていく途中で出会うファイル |
| `.claude/settings.json` の `permissions.deny` の `Read(...)` | ファイル 1 つ 1 つ。`Grep` のファイル読み取りにも適用される |
| ccnavi のルール | `Read` `Write` `Edit` `NotebookEdit` `Bash`。`Grep` と `Glob` は受け持たない |

`projects/foo/.env` が foo の `.gitignore` に入っている場合に、それぞれの段がどう働くか。

| 呼び方 | ccnavi のルール | `.gitignore` | `Read()` の `deny` |
|---|---|---|---|
| `Grep(path=<git プロジェクトルート>)` | 当たらない | 弾く | 弾く |
| `Grep(path=.../foo/.env)` | 当たらない | 当たらない | 弾く |
| `Read(.../foo/.env)` | 止める | 当たらない | 弾く |
| `Bash: cat .../foo/.env` | 止める | 当たらない | 当たらない |

2 行目は `Read()` の `deny` が唯一の保護になる。`permissions.deny` を持たない配布先では、
ignore されたファイルを名指しした `Grep` は通る。

### `.gitignore` を当てにしてよい範囲

ripgrep の既定の挙動で、ccnavi の側では変えられない。

- 起点そのものに指定されたパスには当たらない。ignore されたディレクトリやファイルを `path` に渡すと、その中は読まれる
- `.git` が無いディレクトリでは `.gitignore` を読まない（ripgrep の `--require-git`）。clone
  していない置き場、git 化していない `projects/<名前>/` が該当する
- 降りた先に `.git` があれば、そこから下はそのリポジトリの `.gitignore` が適用される。ワークスペース
  ルートが git 管理下でなくても適用される。`.git` がファイル（ワークツリーの gitdir ポインタ）でも同じ
- 隠しファイルは弾かれない。ドットで始まるものも、ignore されていなければ `Grep` が読む（素の `rg` の既定とは違う）
- `Read` ツールには関係しない。こちらは `credentials` ルールと `Read()` の `deny` が止める

`.ignore`・`.rgignore`・`.git/info/exclude`・git のグローバルな除外（`core.excludesFile`、既定では
`~/.config/git/ignore`）も ripgrep は読む。どれも弾く側に働く。

ワークスペースルートが git 管理下で、`.gitignore` に `/projects/` があるときは、ルートからの
`Grep` は `projects/` へ降りない。プロジェクトのコードを探すときは `cd projects/<名前>` してから
呼ぶ（起点に指定した場所には ignore が当たらない）。

## 動作モード

| 値 | 挙動 |
|---|---|
| `enable` | 判定し、`deny` なら止め、`ask` ならユーザに確認を出す。指定が無いときはこれ |
| `dry-run` | 同じ判定を行い、呼び出しには手を出さず「`enable` なら何をしていたか」を伝える |
| `disable` | 判定しない |

`disable` は `.claude/settings.json` に書いても有効にならない（その指定を名指しで無視し、理由を出す）。エージェントが書き換えられるファイルなので、
監視される側が監視を止められないようにしている。`disable` にするときはセッションを起動する側の環境から渡す。
読めない値は報告して `enable` として扱う。詳細は [設計 4.4](docs/design/startup-config.md#44-動作モード)。

## ツール実行後チェック

実行前チェックは引数しか見ないので、引数に現れない書き込み（ビルドの出力、スクリプトの内部、読み切れないシェル構文）は
止められずに通る。実行後チェックは、走ったあとの作業ツリーを `git status` で読んでそれを拾う。設計は [7](docs/design/post-check.md)。

保護領域は別に宣言しない。`match` に `Write` `Edit` `NotebookEdit` のどれかを含むルールが、そのまま保護領域の宣言になる。
例外は ccnavi 自身の書き込み。記録と state の置き場は最初から保護領域に入らない。チケットの置き場では、ccnavi の副命令（`ticket start` / `finish` や
`review request` / `ready` など）が書いたと内容から見分けられる変更だけが外れる。作業範囲・親・フェーズが変わっていれば報告する。

```
[ccnavi] POST_VIOLATION (rule: guard-config)
path: .ccnavi/common/probe.json (?? / new)
after: Bash(npm run build)
undo: git clean -f -- ".ccnavi/common/probe.json"
ガード自身のルールです。エージェントの判断で書き換えず、変更が要る理由を伝えてユーザに依頼してください。
```

呼び出しは取り消せないので、`enable` では exit 2 と標準エラーで差し戻し、`dry-run` では `additionalContext` で報告だけする。
戻す手順は対象ごとに 1 つ。

| 変更の種類 | 戻し方 |
|---|---|
| 中身が変わった・消えた | `git restore --staged --worktree -- <path>` |
| 追跡されていないものが現れた | `git clean -f -- <path>` |
| 索引に足された状態で現れた | `git rm -f -- <path>` |

### 前から在った変更は原因にしない

セッション前からある変更（他のセッションやユーザの書きかけ）は、初めて見たときに記録し、以降は新しく現れたものだけを
差し戻す。記録した側は `POST_PREEXISTING` として 1 度だけ伝え、戻すなと明示し、自動復元の対象にもしない。
記録はセッションごとに `logs/state/` へ置く（消えても次の起動で取り直す）。同じ場所でも「変わった」の次に「消えた」が来れば、もう一度伝える。

### ターンの終わりにユーザへ報告する

`Stop` に登録すると、そのターンで変わった保護領域を戻す手順付きで `systemMessage` に返す。比べる相手は `UserPromptSubmit` で
記録した状態なので、`UserPromptSubmit` を登録していないと `Stop` は何も報告しない（記録には `no-turn-baseline`）。
ここでは戻さず、`CCNAVI_MODE` も見ない（見えたことを伝えるだけなので、`dry-run` でも報告する）。

同じ `Stop` で、`finish` の打ち忘れを 1 回だけ促す。メインエージェントの cwd のワークツリーに結び付いたチケットが
着手済みで、そのワークツリーに未コミットの変更が無く（追跡していないファイルも数える）、基準点より先に自分で作ったコミットがあれば
（子なら親のブランチ、親なら `origin/HEAD` を取り込んだだけのコミットとマージのコミットは数えない）、
`{"decision": "block", "reason": …}` で止め、`ccnavi-ticket.sh finish <識別子>` の書き方と「まだ続けるなら理由を書いてから終える」を
渡す（理由コード `NUDGE_TICKET_FINISH`）。報告があれば同じ JSON の `systemMessage` に載る。記録の `decision` は `nudge`。同じセッションで
同じチケットを同じ HEAD のまま促すのは 1 回だけで（記録は `logs/state/nudged-<セッション>.json`）、コミットを足せばまた促す。
payload の `stop_hook_active` が真なとき、記録を置けないとき、子で親のブランチを引けないときも促さない。チケット制御かモードが `disable`、未着手、書き込み停止中（`blocked`）、親で `finish` が通らない形（開いている子・
レビュー準備中／レビュー待ち・フィードバック計画待ち・終わっていないフェーズ）、git を読めないとき、`SubagentStop` では促さない。
`dry-run` では止めず、止めたはずの文を `systemMessage` に載せる。

`finish` を促さなかった回は、`match: Stop` のルールが渡す回ならそこで止める（「ターンの終わりに止めて渡す」）。

同じ `Stop` で、このセッションで同じ理由の拒否が `CCNAVI_DENY_REPEAT` 回に達した呼び出しを、ルールの id と回数で 1 回だけ並べる
（「同じ呼び出しを繰り返し止めたとき」）。回数が増えればまた並べる。

### 自動復元

`CCNAVI_RESTORE_IF_DENY=enable`（既定）のとき、ccnavi 自身が戻す。`dry-run` では戻さず、報告に `would-restore` の行を足す。
`disable` では戻さず、その行も出さない。`CCNAVI_MODE=dry-run` のときは、こちらが `enable` でも `dry-run` として振る舞う
（組み合わせは [要件 2.2](docs/requirements/common.md#22-共通の動作--req-cmn)）。

戻す先はコミット済みの内容なので、保護領域に置いた未コミットの変更は失われる。守りたいなら `disable` にするか、宣言を狭める。
現れたファイルは消さずに `logs/state/aside/<日時>/` へ退避し、退避先を報告に載せる。

### コアファイルを守る

実行後チェックはルールファイルから保護領域を決めるので、ルールファイル自身はそこでは守れない。そこで次のものは組み込みで持つ。
`CCNAVI_GUARD_CORE_FILES` が切り替える。設計は [8](docs/design/self-guard.md)。

| 対象 | 何が懸かっているか | バックアップを取る時点 |
|---|---|---|
| `.claude/settings.json` | hook の登録そのもの | ツール実行前 |
| `.claude/settings.local.json` | 同上。個人の上書き | ツール実行前 |
| 共通レイヤーのファイル（`.ccnavi/common/{rules,risks}.yml`。置かれた `phases.yml` も） | 判定の中身そのもの | ツール実行前 |
| `<ワークスペースルート>/.ccnavi/config/{rules,phases,risks}.yml`（自身のレイヤーの 3 本） | 同上 | ツール実行前 |
| `projects/<名前>/.ccnavi/config/{rules,phases,risks}.yml`（各プロジェクトのレイヤーの 3 本） | 同上 | ツール実行前 |
| `CCNAVI_BIN_PATH` が指すファイル（既定の配置では振り分けの sh） | 判定器の実体 | セッション開始 |
| それが振り分けの sh（名前が `ccnavi-launcher.sh`）なら、1 つ上の `bin/<os>-<arch>/` にあるこの機械の実行ファイル | 同上。hook が実際に走らせるもの | セッション開始 |

- git が追跡しているものは、ワークツリーが持つ同じファイルも対象に入る（統合すればそのまま反映されるため）。実行ファイルは除く
- 無いファイルは対象から外れ、無いことは報告しない。無かったところに現れた場合は報告するが、消さない
- `.ccnavi/scripts/` のスクリプトはこの一式に入れない。組み込みの `deny`（`*/.ccnavi/*`）と `CCNAVI_RESTORE_IF_DENY` が受け持つ
- 実行ファイルは大きいので、セッションに 1 度だけバックアップし、大きさと更新時刻で比べる。`SessionStart` に登録しないと守られない。
  `CCNAVI_BIN_PATH` を指定しなければ対象に入らない
- 設定ファイルは実行前に `logs/state/selfguard/<セッション>/` へバックアップし、実行後に変わっていればバックアップから戻す（未コミットの編集は残る）。
  バックアップが無いときだけ git から戻し、そう報告する
- 実行ファイルの実体は `logs/state/selfguard/store/` にハッシュ名で 1 本だけ置く。3 日より長く触られていないセッションのバックアップは、
  次のセッション開始で消す

戻す前に止める側も組み込みで持つ。この機能が有効な間、次の場所へシェルから書き込む形は、ルールに書いていなくても拒否される。
ルールファイルが壊れて組み込みの既定を使っている間も同じ。

- `.claude/` の `hooks/` と `settings*.json`、ccnavi ディレクトリ（`.ccnavi`）、`ccnavi-git.sh`、実行ファイル、
  共通レイヤーの 3 本（`.ccnavi/common/`）、記録と state の置き場（`logs/decisions.jsonl` と `logs/state`）、閉じたチケットの退避（`logs/archive`）
- パスは「区切りが続くか、そこで終わる」形で当てるので、`rm -rf .ccnavi` や `mv .ccnavi .ccnavi.bak` も止まる
- 場所のパスは大文字小文字を区別せずに当てる。コマンドの名前（`rm` / `cp`）も区別しない。止める側が広がるだけなので問題にしない

名指しのツール（`Write` / `Edit` / `NotebookEdit`）からも守る。

- `builtin-guard-project-home`: ccnavi ディレクトリの下（`*/.ccnavi/*`）と、`CCNAVI_BIN_PATH` のパスおよび実行ファイルの置き場
  （名前が `ccnavi-launcher.sh` なら 1 つ上の `bin/<os>-<arch>/`、それ以外なら隣の `<os>-<arch>/`）
- `builtin-guard-common-layer`: 共通レイヤーの 3 本。ワークツリーの中の同じファイルも止まる
- 見本 `.ccnavi/common/rule-samples.yml` も `builtin-guard-project-home` が止める。見本の下書きはワークツリーの `scratchpad/` に置き、
  ルールの下書きと一緒にユーザに渡す

止めるのは書き込みの形と場所の組で、場所の名前が出ただけでは止めない（`cat .ccnavi/common/rules.yml` や `git add <パス>` は通る）。
`builtin-guard-` で始まる id はどのレイヤーのルールファイルにも書けない（書けば error。`--lint` も指摘する）。外したいなら env でこの機能ごと切る。
ルールファイルが読めず既定を使っている間は、共通レイヤーのルールファイルを Write / Edit で直した結果を戻さない（実行前に読めなかったときに限る）。

`PreToolUse` の登録ごと消された場合は ccnavi が一切動かない。hook の登録を hook 自身で守ることはできない。

### 見えないもの

| 見えない | なぜ |
|---|---|
| `.gitignore` に入っているファイル | `git status` に出ない |
| git の作業ツリーの外 | 実行後チェックはリポジトリの中だけを見る |
| この呼び出しが触っていないツリー | 呼び出しごとに見るのはワークスペースルートと、この呼び出しの行き先（Bash は cwd）が属するツリーだけ。ターンの区切り（`UserPromptSubmit` と `Stop`）では全部のツリーを見る |
| 失敗したツール呼び出しの副作用 | `PostToolUse` は成功した呼び出しの後にしか走らない |
| `Read` `Grep` `Glob` `WebFetch` `WebSearch` の直後 | 見に行かない。次の書きうるツールの直後に見える（遅れであって見落としではない） |

## チケットによる作業範囲

ルールが「どこに書かせないか」を長く決めるのに対し、チケットは作業 1 本のあいだ「今回どこに書くか」を決める。
チケットは複数を同時に有効にできる。親（メインエージェント）が作業を子チケットに分け、子は別々のワークツリーでサブエージェントが実行する。
設計は [9](docs/design/tickets.md)、要求は [要件 REQ-TKT](docs/requirements/tickets.md)。

### 使うかどうかはワークスペースが決める

`CCNAVI_TICKET_CONTROL` は `.claude/settings.json` の `env` に 1 つだけ書く設定で、ワークスペース（Claude Code を開いた場所）の
下のプロジェクト全部に同じ値が適用される。既定は `enable`。全体ルールだけで足りるワークスペースは `disable` を書く。
導入スクリプトは `--ticket-control` で聞き、常にこの 1 行を書く。

チケット制御が有効なワークスペースでも、作業の進め方は 2 つある。

- **直接作業**: 調査や小さな修正。チケットを起こさず、判定は全体ルールだけ。ワークスペースルート直下と、承認済みチケットの無いワークツリーがこれ
- **チケット作業**: 設計に触れる・複数のフェーズに分かれる・ユーザのレビューが要る修正。提案を書いて承認を受け、フェーズとリスクの配点に従って進める

どちらで進めるかはモデルが決める。ccnavi は判定で強制せず、`SessionStart`（起動・再開・compact・clear）で次の案内を渡す。
サブエージェントには渡さない。

```
[ccnavi] このワークスペースはチケット制御を使っている。作業の進め方は 2 つ。
- 直接作業（調査・小さな修正）: チケットを起こさずそのまま進める。判定は全体ルールだけ。
- チケット作業（設計に触れる・複数のフェーズに分かれる・ユーザのレビューが要る）: wip/proposals/todo/ に
  提案を書いて承認を受ける。承認されると提案は .ccnavi/approved/doing/ へ動く。以後の操作は sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-ticket.sh を通す（使い方は --help）。
どちらで進めるか迷ったら、ユーザに聞いてください。
（現状: CCNAVI_MODE=dry-run。deny にヒットしても止まらない。通ったことを許可と読まず、表示された案内に次からは従う）
```

最後の行は dry-run のときだけ出る。それ以外の手順（レビューの sh、`review: mr` / `chat`、配点の書き方、後工程）は、必要になった場所で改めて届く。

### 置き場と状態

チケットは 1 本のファイルで、2 つの置き場を行き来する。提案は `wip/proposals/<状態>/<識別子>.md`、
承認済みチケットは `.ccnavi/approved/<状態>/<識別子>.md`。状態は置き場が表す。

```
  wip/proposals/todo ──ユーザが承認──→ .ccnavi/approved/doing
                                          │
                              エージェントが finish（レビュー要）
                                          ▼
  wip/proposals/review ──ユーザがレビュー──→ .ccnavi/approved/done
                                          ▲
                              エージェントが finish（レビュー不要）／cancel
```

| 置き場 | 意味 | 動かすもの |
|---|---|---|
| `wip/proposals/todo/` | 承認待ち | 親が書く。作成と編集は自由 |
| `.ccnavi/approved/doing/` | 承認済み。判定が範囲を読むのはここだけ | `ccnavi --agree`（ユーザ）、ボード。続きの子をユーザが起こすときも直にここ |
| `wip/proposals/review/` | 作業が終わり、ユーザのレビューを待つ | `sh .ccnavi/scripts/ccnavi-ticket.sh finish <識別子>`（フェーズがレビュー要のとき） |
| `.ccnavi/approved/done/` | 閉じた。取り消しは `cancelled_at` を持ってここに入る | `ccnavi-review.sh confirm` / `decide`、`ccnavi --reviewed`、`close-early`（ユーザ）。レビュー不要の `finish` と `cancel --reason` |

- `.ccnavi/approved/` へ動かすのはユーザ、`wip/proposals/` へ動かすのはエージェント
- `review/` への直接の作成・移動は誰がやっても止まる（`builtin-ticket-state`）。`.ccnavi/approved/` は ccnavi ディレクトリを守る組み込みのルールが止める
- 動かすスクリプトと push はサブエージェントには打てない（`DENY_SUBAGENT_TICKET_OP`）。子チケットのワークツリーからの push は
  git のラッパースクリプトが拒む。合流と push と閉じるのは親の仕事
- `start` は置き場を動かさず、着手の時刻と基準点（そのワークツリーの HEAD）を書く。`finish` は完了の時刻を書き、フェーズが
  レビュー要（延期を含む）なら `review/`、不要なら `done/` へ動かす。`cancel` は `doing/` から `done/` へ動かし、時刻と理由を書く
- 順序は「子の成果をマージ → finish → ワークツリーを消す」
- 再開するときはユーザが `done/` から `doing/` へ戻す

フェーズのマーカー（`pending` `skipped` `requested` `reviewed`）を含む遷移は [設計 9.6](docs/design/tickets/state-transitions.md)。

### 書式

frontmatter は rules.yml と同じタイプ（`deny` / `ask` / `allow`）。適用されるのは Write / Edit 系のパスの項だけで、
`match` に Bash を書いた項や `tools` は「効かない」と名指しで警告する。

```yaml
---
version: 1
ticket: feature-50-settings-split-02-01
issue: 50                # 親だけ。マージリクエストの Closes に写す。省ける
project: lib             # 置き場と同じ名前。省ける（提案を置いた場所が決める）
parent: feature-50-settings-split  # 子だけ。親は書かない
phase: 2                 # 子だけ。同じ親の同じ番号が 1 つのまとまり
predecessors: [feature-50-settings-split-01-01] # 子だけ。先に閉じているべき子。承認と着手（start）で求める。書き込みは止めない
human_review:
  required: true         # 既定。省くなら理由を書く
  reason: 設定の読み込み経路を変えるため
title: 設定画面の分割
rationale: |
  Settings 配下のコンポーネント分割。
allow:
  - match: Write|Edit
    glob: "src/components/Settings/*"
ask:
  - match: Write|Edit
    glob: "src/components/*"
started_at: ""           # 以下はスクリプトが書く
completed_at: ""
base_sha: ""
---
```

- 識別子は子が `<親>-<2 桁のフェーズ番号>-<2 桁のフェーズ内の連番>`（親 feature-50-settings-split のフェーズ 2 の 1 枚目は
  `feature-50-settings-split-02-01`）。フェーズ番号は `phase:` と同じ値で、食い違うと読めない（error）。
  親の識別子は `<先頭の語>-<番号>-<slug>`（`feature-63-integration-branch`、
  `hotfix-64-統合先の解決`）。先頭の語は既定で `feature` `hotfix` `fix` `bugfix` `chore` `refactor` `docs` のどれかで、
  チャットで「hotfix で」と言えばエージェントがその語で書く。リストを変えるときだけ `.claude/settings.json` の `env` に
  `CCNAVI_BRANCH_PREFIXES=feature,hotfix,fix` のように書く。番号は issue があれば issue の番号、無ければ通し番号
  （`ccnavi --lint` の warn が次の番号を示す）。slug には日本語（ひらがな・カタカナ・漢字）も使える。形に合わないものと、
  末尾が `-<2 桁>` の親は `--lint` の warn で、承認は止めない
- ワークツリーの名前は識別子と同じ。`.claude/worktrees/feature-50-settings-split-02-01/`
- 親のブランチ名も識別子と同じ。既にある `feature/123-login` のような `/` を含むブランチで作業するときは、識別子は
  `feature-123-login` のまま、親に `branch: feature/123-login` を書く。承認画面に
  「既存のブランチ feature/123-login を使う」と出る。承認されるまでは `branch:` を使わず、識別子のブランチ
  （`worktree add .claude/worktrees/feature-123-login -b feature-123-login <起点>`）で作業する。承認の後、親のワークツリーで
  `ccnavi-git.sh switch feature/123-login` を打つと、そのブランチへ移って承認済みチケットとマーカーを取り込む（既にあれば
  識別子のブランチを merge し、無ければ切る）。続けて `push -u origin feature/123-login` で取り込み状態がそのブランチになる。
  統合先・保護されたブランチの名前、`origin/main` のような git の ref と紛れる名前、別の親子のチケットが使うブランチは書けない。
  承認の後は変えられない。子のブランチは子の識別子
- 子は親の部分集合として書く。親やフェーズ定義の `scope` を超える項は承認で warn に出るだけで、判定がその上限で切り詰める
- 書いていない場所は範囲外。親子は厳しい側が採られる
- 深さは 2 段。範囲は 20 件まで
- 大文字小文字は、どの機械でも区別せずに当てる（`docs/Design/*` は `docs/design/plan.md` にも当たる）。表記は書いたまま記録と承認画面に出る。
  `regex` も同じで、区別が要るなら `(?-i:...)` で囲む

### 効くのは承認したものだけ

判定が読むのは `.ccnavi/approved/doing/` の承認済みチケットで、`wip/proposals/todo/` の提案ではない。承認のあとに同じ識別子の
提案を書いても範囲は適用されない（親の計画の改版だけが承認の対象に入る）。承認の詳細は [設計 9.4](docs/design/tickets/approval.md)。

```sh
ccnavi --agree
ccnavi --agree i0002 i0002-01-01   # 並べた識別子だけを承認の対象にする
ccnavi --agree --preview --verify i0002 i0002-01-01   # 承認できる状態かを確かめるだけ（置かない）
```

全ツリー（ワークスペース、プロジェクト、ワークツリー）の `wip/proposals/` を走査し、未承認のものをまとめて見せる。
画面に出るのは、親が新たに書けるようにする領域、子が親の範囲をどこまで絞ったか、子ごとの人間レビュー要否、計画、親の `issue:`。
子の範囲が上限を超えていれば「チケットで編集対象としているが、書き込めない場所」の見出しで出る。

- 識別子を並べると対象を狭める。承認待ちに無い識別子や、承認待ちの親の改版を外した子が混じれば、何も承認しない
- 対象から外した親を持つ子は「親が承認されていない」で承認されない。前のフェーズが閉じていない子も承認されない
- 終わったフェーズに子を足して承認すると、そのフェーズは開き直り、マーカー 4 種（`pending` `requested` `reviewed` `skipped`）は全部消える
- リスクの点は承認では数えない。子を閉じるときに実績で測る（「実績のリスク」）

承認する場所は 3 つ（Chrome 拡張を足せば 4 つ）。どこで承認しても**承認はチケットの中身を変えない**。提案のファイルを
`todo/` から `doing/` へ動かすだけで、欄を書き足さず、改行も BOM も変えない。手で動かした承認と ccnavi の承認は、承認済みチケットが
提案とバイト単位で同じになり、見分けが付かない（欄が無いことや未コミットであることは、承認が途中で止まった印ではない）。
親の全体計画の待ち方は、`--agree` がチケットではなく `.ccnavi/approved/phases/<親>/workflow.yml` に書く。

| 経路 | 形 |
|---|---|
| 端末 | `sh .ccnavi/scripts/ccnavi-agree.sh [<識別子>...]`。一覧を見せて y/N を取り、通れば `ccnavi-push-approved.sh` でコミットして push する。`-` で始まる語と空の語は受けず 2 で終わる |
| ボード | `--agree --preview --json` で読んでオーバーレイに出し、押したら `--agree --yes <識別子,…> --digest <値> --json` を打つ（「承認の JSON」）。1 件以上通れば `ccnavi-push-approved.sh` を統合ターミナルへ送る |
| ファイルを動かす | 提案を `.ccnavi/approved/doing/` へ動かす。GitHub の画面しか使えないユーザ向け。承認の画面を通らないぶん、構造の検査は判定のときに行い、引っかかれば `DENY_TICKET_BLOCKED` で止まる（`--lint` とボードの JSON の `blocked` でも分かる） |

エージェントが `--yes` を打つ形は組み込みの deny（`builtin-guard-ticket-approval`）が止める。`--preview` は通す。

**承認済みチケットは親チケットのブランチに乗って他の機械へ届く。** 承認しても push しなければ、他の機械では承認されなかったことになる。
承認の push は `sh .ccnavi/scripts/ccnavi-push-approved.sh` で行う。

- ワークスペース、`projects/*`、`.claude/worktrees/*` のツリーごとに、変更があれば置き場（`.ccnavi/approved/`）だけをコミットし、そのブランチへ push する
- シンボリックリンクは辿らず、名指しして飛ばす
- `main` / `master` / `develop` / `release` / `release/*` と、そのリポジトリの統合先（`CCNAVI_INTEGRATION_BRANCH`、無ければ
  `ccnavi-sync.sh` の取り込み結果、無ければ `origin/HEAD`・`origin/main`・`origin/master`。決まらなければ固定のリストだけ）、
  ブランチをチェックアウトしていないツリーは push せず、コミットまでで止める
- コミットするものが無ければ `コミットして push する承認済みチケットは無い。` と 1 行出す
- エージェントが打つ形は組み込みの deny（`DENY_TICKET_APPROVAL_CLI`）が止める

| 終了コード | いつ |
|---|---|
| 0 | コミットするものが無い、または全部コミットした（push しなかったブランチ、飛ばしたツリーを含む） |
| 1 | `git add` / `commit` / push が失敗したツリーが 1 つ以上ある。巻き戻さないので、もう一度打てば送れる |
| 2 | 引数の誤り、ワークスペースルートが見つからない |

`ccnavi-agree.sh` は承認が通れば、承認の push の段が 1 で終わっても 0 を返す。

受け取る側では、セッション開始時に `.ccnavi/scripts/ccnavi-fetch.sh` が取ってくる。進めるのは fast-forward だけで、未コミットの変更があるツリーや
分岐したツリーは触らず理由を 1 行で示す。取ってくるのは、チェックアウト中のブランチと、ワークツリーの起点になる統合先（`CCNAVI_INTEGRATION_BRANCH`、無ければ
`ccnavi-sync.sh` の取り込み結果、無ければデフォルトブランチ＝`origin/HEAD`）。統合先の決め方は
`ccnavi-common-state.sh` の `ccnavi_integration` 1 か所にあり、`ccnavi-git.sh` の push の拒否と `ccnavi-review.sh` が作るマージリクエストの宛先も同じ順で決める。
リモートに届かないときも止めず、手元の版で判定する。認証は尋ねず、fetch 1 回を `CCNAVI_FETCH_TIMEOUT` 秒（既定 15）で切る。
認証で失敗したときはその旨を 1 行添えるので、ユーザが端末で一度 `git fetch origin` を打って資格情報を保存すれば、次のセッションから通る。

親のワークツリーのうち親子のチケットの取り込み状態（`logs/state/sync/<リポジトリ>/families/<P>`）があるものは、未コミットの変更があっても
早送りを試し、書きかけと重なれば重なったパスを示す。分かれていれば「取り込みが要る」と 1 行出すだけで merge はしない。
取り込み（merge）と、親のブランチがリモートから消えたかの確かめは、ユーザが打つ `.ccnavi/scripts/ccnavi-sync.sh [<P>...]` がする。
統合先は環境変数 `CCNAVI_INTEGRATION_BRANCH`（`.claude/settings.local.json` の `env` でもよい）、空ならホストのデフォルトブランチで、
使った名前を出力の冒頭に出す。親子のチケットの取り込み状態は、親のブランチへの push が `ccnavi-git.sh` で通ったときに作られる。
取り込んだ後は親子のチケットを判定し直し、error があればその取り込み状態を `blocked` にして止める（直してからオンラインで打ち直せば戻る。
止める理由があるのに取り込み状態を書けなければ 3 回試し、それでも駄目なら終了コード 3）。
取り込み状態のある親子のチケット（取り込み済みの親子のチケット）は、親のブランチ上のチケットだけを本物とし、取り込み状態が `gone`・`blocked`・壊れている、
親のワークツリーが無いなどで決まらなければ、その親子のチケットの承認・状態の操作・実行前チェックを止める（設計 9.2）。
親子のチケットの取り込み状態は親のワークツリーを片付けても消さずに残す。捨てた親子のチケットの取り込み状態は、片付けた後にユーザが
`.ccnavi/scripts/ccnavi-sync.sh --forget <P>` で消す（エージェントからは組み込みの deny が止める）。
取り込んだことがあるリポジトリでは、新規の提案の識別子を統合先の取り込み結果の `done/` と比べ、閉じた識別子と、
取り込み結果が無い・壊れていて確かめられないものは承認しない（理由を承認の画面に出す）。

順序は「承認 → 承認済みチケットをコミット → 子のワークツリーを作る → `start`」。コミットの前にワークツリーを作ると、その子には範囲が適用されない。

**承認を頼む前に、エージェントが自分で確かめる。** `--agree --preview --verify [<識別子>...]` は、置かずに「いま `--agree` を打てば
その提案が承認の対象に入るか」を返す。0 が「はい」、3 が「いいえ」、1 は使い方か設定の誤り。「いいえ」は、承認待ちに無い識別子、
承認待ちが 1 件も無い、承認の対象にしない提案がある、の 3 つ。範囲の超過と読めない提案は「いいえ」にしない（本文には出す）。
`--json` を足すと「承認の JSON」の形に `verify` が付く。提案を `wip/proposals/todo/` に書くと、この確認を勧める文が文脈ごとに 1 度届く。

承認したことは、拡張が渡す文と `ccnavi-ticket.sh status` で伝わる。hook は承認を伝えない。拡張は承認の文
（`--agree --yes` の `prompt`）をオーバーレイの 2 ボタン（コピー、新しいセッションで開く）から渡せる。端末・GitHub の画面・Chrome 拡張で
承認したときは、ユーザがそのあとセッションに一言送る。ユーザがレビューを終えたことは、ボードの「レビュー済み連絡」が
「親のワークツリーで `ccnavi-review.sh confirm --phase <N>` を打て」の文を同じ 2 ボタンで渡す。マーカーを置くのはその `confirm`。

**承認済みチケットの状態は `status` で聞く。** ファイルや `git status` を読んで推測せず、ccnavi に聞く。

```sh
sh .ccnavi/scripts/ccnavi-ticket.sh status          # 作業中・レビュー待ち・承認待ちのチケットがある親子を全部
sh .ccnavi/scripts/ccnavi-ticket.sh status i0002    # その親子だけ（子の識別子を渡しても親子で出す）
```

チケットごとに、置き場とツリー、承認の時刻、着手しているか、置き場のファイルが未コミットか・コミット済みで未 push か、止まっている理由、
次の一手を出す。読むだけで何も書かず、ネットワークにも出ない。サブエージェントも打てる。

- 承認の時刻は、状態の履歴の `approved`（続きの子は `raised`）、無ければ `doing/<識別子>.md` を足したコミットの時刻。
  どちらも無ければ「未コミット（手で置いた）」
- push 済みかは手元のリモート追跡の ref で見るので、古いかもしれない。最新にしたければ先に `ccnavi-sync.sh` を打つ
- 未コミットの承認済みチケットには、ユーザに `ccnavi-push-approved.sh <親>` を打ってもらうことだけを言う（エージェントは運ばない）。
  取り込み済みの親子（C1 の対象）なら「ユーザが運ぶまで `start` は止まる」、C1 の対象外なら「`start` へ進んでよい」と添える。
  C1 が状態の操作を断る親子では、その理由を止まっている理由に出す
- 止まっている理由は、`blocked`、取り込み済みの親子が決まらない、C1 が断る、満たしていない先行、親が未着手、の各場面。
  `base_sha` がワークツリーの HEAD の祖先でない着手、再開で残った閉じるときの欄、履歴に着手の行が無い着手、待ち方の固定が
  無い親は「注意」で出す（`start` も判定も止めない。`--lint` も warn）
- 状態を動かすコマンド（`start`・`finish`・ワークツリーを作る）の行には「親（メインエージェント）だけが実行する」と書く。
  サブエージェントがその行を打っても hook が止める
- 親を渡して何も見つからなければ終了コード 1

### 判定の鍵はファイルの行き先

Write / Edit の対象を解いた先が `.claude/worktrees/<名前>/` の中なら、その名前と同じ識別子の承認済みチケットで判定する。
呼び出し元の cwd も、サブエージェントかどうかも見ない。ワークスペースルート直下と、チケットの無いワークツリーはルールだけで判定する。
ルールとチケットの判定を両方出し、**強い側を採る**（`deny` > `ask` > `allow` > 何も言わない）。同じ強さならルールの判定と文面を使う。

| ルール | チケット | 結果 |
|---|---|---|
| `deny` に当たる | どれでも | 止まる（ルールの文面） |
| `ask` / `allow` に当たる | 範囲の外、または `deny` の項 | 止まる（`DENY_TICKET_SCOPE`） |
| `ask` に当たる | 範囲の中（ask / allow）、またはチケットが無い | ユーザに確認が出る（`RULE_ASK`） |
| `allow` に当たる | 範囲の中（ask） | ユーザに確認が出る（`TICKET_ASK`） |
| `allow` に当たる | 範囲の中（allow）、またはチケットが無い | 通る |
| 何も言わない | 範囲の中（allow） | 通る |
| 何も言わない | 範囲の中（ask） | ユーザに確認が出る（`TICKET_ASK`） |
| 何も言わない | 範囲の外、または `deny` の項 | 止まる（`DENY_TICKET_SCOPE`） |
| 何も言わない | ワークツリーにチケットが無い | 権限モードに従う（`UNDECLARED`） |

チケットの判定がルールの判定より強かった回は、文面の頭に `rule: <id> (allow) lets this through, but the ticket for this worktree narrows it` が載り、
チケットの `deny` の項に当たったなら `ticket entry: deny <パターン>` も載る。記録では `rules` が `(ticket-scope)` とルールの id のリストになり、`source` は空になる。

```sh
jq -r 'select(.rules[0]? == "(ticket-scope)" and (.rules | length) > 1) | .subject' logs/decisions.jsonl
```

子の範囲は、子の宣言を親の範囲とフェーズ定義の `scope` の両方で切り詰めたもの（フェーズ定義が `inherit` か、`phases.yml` がどのレイヤーにも無ければ親だけ）。
上限で止めたときは、文面の頭に `limit: phase type 設計 (design): wip/design/*, docs/*` や `limit: parent i0001: src/*, tests/*` の形で 1 行載る。
`phases.yml` があるのにその番号のフェーズ定義を引けないときは、親でだけ切り詰め、notice で知らせる。

範囲の外として扱わない場所。

- 提案の置き場（`wip/proposals/`）と承認済みチケットの置き場（`.ccnavi/approved/`）。`review/` と承認済みチケットは組み込みの deny が止める
- 下書きの置き場。ワークツリーのルートの直下 1 段にある `scratchpad/` で、大文字小文字を区別する。外すのは**実行前チェックだけ**で、
  実行後チェックとサブエージェント終了時チェックは外さない。この 2 つに `scratchpad/` の変更が現れるのは `scratchpad/` が
  追跡されているときなので、範囲外として報告する
- ELI5 の HTML の置き場。ワークツリーのルートからの `wip/eli5/` の下で、大文字小文字を区別する。追跡される置き場なので、
  実行前チェック・実行後チェック・サブエージェント終了時チェックのどれでも外す。`wip/` のほかの場所や `wip/eli5x/` は外さない。
  チケットの `deny` と全面停止（`LIMIT_BLOCKED`）より先に適用される
- どの置き場も `\` を `/` に直さずに見る。`wip\eli5\x.py` や `wip\proposals\todo\x.py` という名前の 1 ファイル（Linux / macOS で作れる）は
  置き場ではなく、範囲を当てる

シェルが書いたものは実行後チェックが `POST_TICKET_SCOPE` で報告する（ルールの `allow` に当たる場所でも同じ）。子のワークツリーは、
サブエージェントの終了時に基準点（`base_sha`）からのコミット済みの差分と未コミットの両方を見て、範囲外が残っていれば 1 回だけ差し戻す。

ワークツリーへの書き込みを `allow` で許しているルールがあっても、承認済みチケットに結び付いたワークツリーの書き込みはチケットの範囲に制限される。
止まったら、ルールを緩めるのではなく、その場所を範囲に持つチケットを提案する。

### フェーズと HITL ポイント

フェーズの終わりは、ユーザの手が入るところ（HITL ポイント）の 1 つ。ユーザの手は範囲の承認・レビュー・未解決の受け入れの 3 か所にまとめてある。
同じ親の同じ `phase` の子が `doing/` に 1 枚も無く、`review/` か `done/` に 1 枚以上あれば、そのフェーズは終わり。取り消しだけのフェーズは終わらない。
設計は [9.8](docs/design/tickets/hitl.md)。

ユーザがどこで見るかは `none` / `chat` / `mr` の 3 つで、次のうち厳しい側が採られる（`none` < `chat` < `mr`）。

- フェーズ定義の `review`
- 親の計画の項の `mr`（フェーズ定義の `review` が `none` でも `mr` にする）
- 延期した番号の分を引き受けているなら、その分の宣言

閉じた子の `human_review.required` が `true` か、実績のリスクが HIGH 以上のときは、`none` を `chat` に上げるだけで場所は指定しない。

| | `mr` | `chat` | `none` |
|---|---|---|---|
| 返す文 | 合流と push を済ませ、`request` でレビューを頼み、ターンを終えてユーザを待て | 合流してユーザに差分を見てもらい、ターンを終えて待て | レビューを省略して次のフェーズへ進める |
| HITL ポイント | 来る。レビュー済みのマーカーまで止まる | 来る。レビュー済みのマーカーまで止まる | 来ない。省略のマーカーを残す |
| 先へ進める者 | `ccnavi-review.sh confirm` / `decide` | ユーザが親のワークツリーの端末で `ccnavi-review.sh chat <N>`（中身は `ccnavi --reviewed <N> --chat`） | — |
| `review/` の子 | レビュー済みで `.ccnavi/approved/done/` へ動く | 同じ | `finish` の時点で `done/` へ |

`chat` はホストへ出ないので、マージリクエストもトークンも要らない。`--chat` はフェーズ定義が `chat` と宣言したフェーズにしか当たらず、
`request` を出したあとのフェーズも断る。逆向き（`chat` のフェーズを `request` でマージリクエストに出す）は通り、そこから先は `mr` と同じになる。

止めている間、その親の cwd からの `Agent` と Bash を止める（`DENY_PHASE_REVIEW`）。通すのは `ccnavi-ticket.sh` `ccnavi-review.sh`
`ccnavi-git.sh` の 3 本だけ。Write / Edit は通すので、次のフェーズの計画はレビュー前に進められる。
止めている間は、依頼を出すまでが「レビュー準備中」（次に動くのはエージェント）、出してからが「レビュー待ち」（次に動くのはユーザ）。

### フェーズ定義と計画

`phases.yml` に**フェーズ定義**を書き、親が `plan:` にそのリストを書くと、フェーズに意味が付く。
置き場はレイヤーごと（共通レイヤー `.ccnavi/common/`、自身のレイヤーとプロジェクトのレイヤー `.ccnavi/config/`）で、`scope` のパスが
レイアウトに依存するならワークスペース自身のレイヤーに置く（このリポジトリもそう）。
フェーズ定義はユーザが持つ設定で、エージェントは書き換えない。どのレイヤーにも無ければ番号だけの挙動のまま。設計は [9.7](docs/design/tickets/phases.md)。

```yaml
# .ccnavi/config/phases.yml
version: 1
phases:
  research:
    kind: work            # work は全体計画に、feedback はフィードバック計画にだけ置ける
    title: 調査           # id と title はどちらも一意
    review: none          # none | chat | mr。既定であって上限ではない
    scope: ["wip/research/*"]          # 子の範囲の上限。省略か inherit なら親の範囲
    deliverables: ["wip/research/summary.md"]   # 閉じる前に在って追跡されているべきもの
    agent: explorer       # .claude/agents/<名前>.md。案内。判定には使わない
    when: 既存の振る舞いが分からないとき          # 案内。判定には使わない
  implement:
    kind: work
    title: 実装とテスト
    review: mr
    scope: ["src/*", "tests/*"]
    requires: [acceptance]             # 計画に置くなら一緒に要る
  chores:
    kind: work
    title: 片付け
    review: chat          # このセッションでユーザが見る。マージリクエストは作らない
    scope: ["src/*"]
  acceptance:
    kind: work
    title: 受入テスト作成
    review: mr
    scope: ["tests/*"]
    overlap: [implement]               # 並行してよい（対称）
  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: mr                         # feedback に none は書けない（chat は可）
    scope: inherit
```

```yaml
# 親チケット
ticket: i0001
issue: 1
plan:                                  # 全体計画。--agree が通ることが合意
  - research
  - {type: design, review: defer}      # レビューを次と一緒に見る（延期。省略ではない）
  - acceptance
  - implement
feedback:                              # フィードバック計画。レビューのあと改版で足す。空でも出す
  - implement-feedback
```

子は `phase: N`。N 番目のフェーズ定義は親の計画で決まる。番号は全体計画が 1 から、フィードバック計画がその続き。

- **合意は全部 `--agree` を通る。** 全体計画は親の承認、各フェーズの計画はその番号の子の承認、フィードバック計画は `feedback:` を
  足した親の改版の承認（全体計画の最後のレビューが済んでから 1 回だけ。指摘が無くても `feedback: []` で出す）
- **順序は承認で止まる。** N 番目の子は、N-1 番目までが全部閉じてレビュー（延期でなければ）が済むまで承認されない。`overlap` の組だけ例外。
  フェーズには必ず子が 1 本以上あり、親は計画・合流・依頼だけをする
- **改版**で変えられるのは `plan`（子がまだ承認されていない番号の項）と `feedback` だけ。`feedback` の承認後は新しいフィードバック作業
  フェーズを足せないが、その中で子を足すのは何度でもできる。残る指摘はユーザが `decide` で issue に回す
- **改版は本物とするツリー（承認済みチケットが在るツリー。親のワークツリー `.claude/worktrees/<親>` に在ればそこ、無ければ元ツリー）の
  `todo/` に書く。** 承認済みの識別子の提案は、承認済みチケットで決めた本物とするツリーの側だけを読む。
  ほかのツリー（子のワークツリー、ワークスペースルート）に書いた改版は承認待ちに入らず、`--agree` と `--lint` が
  場所と書く置き場を名指しする
- **改版の提案では、承認済みチケットを写したときに入る `started_at`・`completed_at`・`base_sha`・`cancelled_at`・`cancel_reason` を空にする。**
  承認はチケットの中身を変えないので、提案にスクリプトだけが書く欄の値があると `--agree` と `--lint` が error にする。
  改版は承認済みチケットの側の値を残し、計画だけを差し替える。前の版の承認が書いた記録 `ccnavi_approved` と、続きの子の目印
  `followup_of` も提案に書けば error になるので消す

**DAG で待たせる。** ファイルの頭に `order: dag` を書き、フェーズ定義に `after:` を書くと、N 番目はフェーズ定義の祖先に当たる番号だけを待ち、
繋がっていないフェーズ定義は並行して進む。

```yaml
version: 1
order: dag                 # 親の project: が指すこの 1 本が dag と書いたときだけ効く
phases:
  design:     {title: 設計, review: mr}
  acceptance: {title: 受入テスト作成, review: none, after: [design]}
  implement:  {title: 実装とテスト, review: none, after: [design]}   # acceptance と並行
  docs:       {title: 文書, review: mr, after: [acceptance, implement]}   # 合流点でユーザが見る
```

- 待ち方は全体計画の承認のときに計算され、`.ccnavi/approved/phases/<親>/workflow.yml` に書き込まれる（チケットには書かない）。あとで `phases.yml` を直しても、改版を出すまで進行中の親には反映されない
- 手で `doing/` へ動かした親はこのファイルを持たず、一直線（前の番号を全部待つ）で読む。並行にしたければ改版で `--agree` を通す
- 承認は、`after` の循環、後ろの項が前の項の祖先になる順序、終端が 2 つ以上の計画を拒む。**辺の書き漏れは並行として通る**ので、承認の画面の待ちで確かめる
- フィードバック計画はいつも一直線。レビュー待ちで止めるのは親ごとなので、どれかの枝がレビューを待つ間は別の枝にも子を起こせない

`--explain` と `SubagentStart` はフェーズを「3: 実装とテスト」のように示す。親の局面（作業中・レビュー待ち・フィードバック計画待ち・
フィードバック対応中・閉じられる）を名指しするのは `--explain` とボードだけ。

### レビューの依頼と確認

```sh
sh .ccnavi/scripts/ccnavi-review.sh request --phase 2 --body-file wip/tmp/request.md --eli5 wip/eli5/phase-2.html
sh .ccnavi/scripts/ccnavi-review.sh confirm --phase 2
sh .ccnavi/scripts/ccnavi-review.sh comment --body-file wip/tmp/decision.md
```

設計は [9.10](docs/design/tickets/review.md) と [9.11](docs/design/tickets/close.md)。

- `request` には ELI5 の HTML（`--eli5`）が必須。変更の目的・何が変わるか・リスクを専門用語なしで書いた
  1 枚の HTML で、外部の読み込み（CSS・JS・画像の URL）は使わない。親のワークツリーの `wip/eli5/` の下（既定の名前は
  `wip/eli5/phase-<N>.html`。名前は英数字と `. _ / -` だけ）に普通のファイルとして置いてコミットし、push しておく。マージリクエストの差分に載せるため。
  拡張子が `.html` / `.htm` でないか `--eli5` が無いなら 2、ファイルが無い・空・`wip/eli5/` の外・使えない字・HEAD に無い（未追跡・未コミット）・
  HEAD でモードが 100644 でない（シンボリックリンク・実行ビット付き）・HEAD と中身が違う、なら全部を挙げて 1 で止まる。追跡しない `wip/tmp/` に置く前の形は止まる。`wip/eli5/` の下はチケットの
  範囲を当てないので、親チケットの範囲に書き足さなくてよい
- 投稿が済むと `ELI5 を見る: ユーザが端末で cd <ルート> してから crit review <相対パス> を打ち、… crit push <番号> で …` が出る。
  ユーザはその手順を手元の端末で打つ（sh は crit を起動しない。crit・gh・glab が無い環境でも依頼は通る）。依頼の本文にも同じ手順を 1 行載せる
- ユーザは `crit review <相対パス>` で HTML のソースの行に指摘を付け、`crit push <番号>` でマージリクエストの行のスレッドとして送る
  （GitHub は `gh`、GitLab は `glab` が要る。GitLab での crit push は未確認）。`crit <相対パス>` だけだと描画のプレビューになり、そこで付けたピンは送られない。
  手元のチェックアウトがマージリクエストの先頭より古いと行の番号がずれるので、先に合わせる。差分の外の行がホストに拒まれるかは未確認。
  送った指摘はユーザのスレッドなので、`confirm` が未解決として数えて止め、`decide` で 1 件ずつ行き先を選べる
- 依頼の後に `wip/eli5/` の下だけを直したコミットは、HEAD が動いたとは数えない。push すれば `confirm` は止まらず、打ち直しも要らない。
  直した ELI5 をユーザが見直す保証は無く、直す前の行のスレッドは残るのでユーザが resolve する。ほかのファイルと一緒に変えたコミットは今までどおり
  打ち直しが要る。`wip/` は `ready` の前に丸ごと消すので、squash した成果物に HTML は残らない。`ready` の前提は大文字小文字を区別せず
  （`WIP/` も）、名前が `wip\` で始まる 1 ファイルも残りとして止める

- `request` は前提（フェーズが終わっている・レビューが延期されていない・子ブランチが親に取り込まれている・未コミット無し・push 済み・未依頼）を
  確かめ、1 つでも欠けたら全件を列挙して何もしない。通れば依頼コメントを投稿し、マーカーを残す。マージリクエストが無ければ Draft で作る
  （題・本文・`Closes #<番号>` は親チケットから写す）
- 依頼後に親の HEAD が動いたら（ccnavi 自身の置き場の外が変わったときだけ数える）、レビュー済みになる前なら `request` を打ち直せる
- `confirm` は、依頼時の HEAD と今の HEAD が同じで push 済みであることを求め、今残っている未解決スレッドを数える（付いた時刻では絞らない）。
  未解決も変更要求も無ければレビュー済みのマーカーを置き、そのフェーズの `review/` の子を `done/` へ動かす。残るなら一覧を返す。
  変更要求はレビュアーごとに最新だけを数え、取り下げは無い扱い
- `comment` はこのセッションで受けた承認や判断をマージリクエストのコメントに写す

残った指摘の対応方針はユーザが決める（`decide`）。ボードの「決める」か、端末で `ccnavi-review.sh decide <N>`。

- **対応しない（受け入れて進む）。** `phases/<親>/accepted.json` に記録し、次の `confirm` から数えない
- **このフェーズで直す。** 直す指摘を写した続きの子を、同じフェーズの番号で `.ccnavi/approved/doing/<親>-<フェーズ番号>-<そのフェーズの次の連番>.md` に直に置く
  （範囲は見た子の範囲の和）。フェーズは開き直り、マーカーは消える
- **issue に回す。** 受け入れたうえで、その指摘を載せた issue を作る。フィードバック計画が承認されたあとだけ選べる

どれも直さなければレビュー済みにし、1 件でも直すなら見た子を `done/` へ動かしてマーカーは置かない。決めた内容は sh がコメントに写す。
ボードの経路は端末を求めない代わりにダイジェスト（`digest`）の一致を求め、エージェントが同じ形を打つ経路は組み込みの deny が止める。
`--reviewed <N> --chat` でも、レビュー済みにしたあとに指摘を 1 行ずつ打てば同じ形で続きの子を起こす。
変更要求のレビューが出ている間はどの経路も通らない。解除できるのはレビュアーの approve / dismiss だけ。

**リモートを読み書きするのは sh で、実行ファイルはネットワークに出ない。** マージリクエストの中身は `ccnavi-review.sh` が取ってきて
JSON で渡す（`--result <path>`）。

| sh の動き | 実行ファイルの段 |
|---|---|
| `request` | `review prepare`（前提を確かめ、マーカー付きの本文を state の置き場に書き出す）→ sh が投稿 → `review requested`（マーカーを置く） |
| `confirm` | sh がスレッドとレビューを取ってくる → `review confirm`（判定してマーカーを置き、`review/` の子を `done/` へ動かす） |
| `decide N` | sh が取ってくる → `--reviewed N --accept-unresolved`（ユーザに見せ、指摘ごとに対応方針を選ばせる）→ issue に回す分があれば sh が issue を作り、決めた内容をコメントに写す。ボードは `decide N --preview`（`--preview --json`。一覧とダイジェスト）と `decide N --choices <JSON> --digest <ダイジェスト>`（`--yes <JSON> --digest <ダイジェスト> --json`）で同じ経路を通る |
| （`chat` のフェーズ） | ユーザが親のワークツリーの端末で `ccnavi-review.sh chat <N>` を打つ（中身は `ccnavi --reviewed <N> --chat`。取り込み済みの親子のチケットなら最後に承認の push（`ccnavi-push-approved.sh`）を呼ぶ）。依頼も、取得した結果も無い |
| `comment` | sh が投稿する。実行ファイルは関わらない |
| `ready` | Draft を外す（「マージに進んでよい」の合図）。`review ready`（親を閉じられる状態かを確かめ、マーカー `phases/<親>/ready.json` とコメントの下書きを置き、閉じた親子のチケットを手元の `logs/archive/` へ退避する）→ C1 が退避の削除をコミットして push → sh が Draft を外してコメントを投稿する。親が打つ。マージはユーザ |
| `close-early --reason <理由> [--no-issue]` | まだ残っているが「キリの良いところまでやった」と早めに閉じる。ユーザが端末で打つ。`--close-early`（残りを見せて y/N、未着手の子を取り消し、マーカーを置く）→ sh が残りを issue に書き出し、コメントを投稿する。Draft は親が片付けてから `ready` で外す |
| `fetch` | 取得した JSON を標準出力へ。デバッグ用 |
| `origin` | origin をどう読んだか（ホスト・scheme・API の URL・使う道具）。origin の読み方が合わないときに確かめる |

- 道具は `gh` / `glab` があればそれ、無ければ `curl` と `GITHUB_TOKEN` / `GITLAB_TOKEN`。どちらも無ければ止まる。`jq` が要る。
  道具は起動時に絶対パスへ解いて固定する。GitHub と GitLab は origin の URL で見分ける
- origin の表記はポートと scheme をそのまま使う（`http://localhost:8929/g/p.git` なら `http://localhost:8929/api/v4`）。
  URL に埋めた資格情報は読み飛ばし、出力では伏せる
- push の認証は git の設定側（Git Credential Manager か `credential.helper`）に置く。git のラッパースクリプトは `GIT_CONFIG_COUNT` を外し
  `GIT_TERMINAL_PROMPT=0` で動くので、環境変数での差し替えも認証画面も使えない。GitLab の実物で分かった注意点は [実測で分かった落とし穴](#実測で分かった落とし穴)、
  確かめ直すための道具は `tools/gitlab/probe_gitlab.py`
- `--result` を実行ファイルに直接渡せるのはユーザの手だけ（`CCNAVI_GUARD_TICKET_APPROVAL`）。エージェントはスクリプト 2 本を通す

**Draft を外すのは親、マージはユーザ。** `ready` は、親を閉じられる状態（全フェーズが終わり、フィードバック計画が承認され、レビューが済んでいる）
に加えて、親の承認済みチケットが `done/` にあること（閉じる前に Draft を外してマージされると、親の記録の無いまま親のブランチが消え、
親子のチケットが決まらなくなる）と、`wip/` が追跡から消えていて、未コミットが無く、push 済みであることを求める。取り込みは squash（GitLab ではマージリクエストの
`squash` を有効にする）。順は「親を `finish` で閉じる → `rm -r wip` をコミット → push → `ready` → ワークツリーを片付ける」。
ワークツリーの片付けはマージを待たない。

**チケットの置き場は既定のブランチに残さない。** `ready` は条件を確かめてから、親のワークツリー（`.claude/worktrees/<親>`）の承認済みの領域にある閉じた親
（今回の親と、統合先にたまっていた過去の親。`done/` に在る親）について、次のファイルをワークスペースの `logs/archive/<リポジトリ>/` へ移す
（`<リポジトリ>` はワークスペース自身なら `self`、プロジェクトならその名前。その下は承認済みの領域と同じ構成）。

- `done/` の親と子のチケット
- `phases/<親>/` の下（マーカー・子の記録・`ready.json`・`closed.json`）
- `events/` の親と子の履歴（ツリーの中身をそのままコピーし、「退避した」`archived` の 1 行は退避の側にだけ足す。退避に既に在れば上書きせず、
  ツリーにあって退避に無い行だけを足す）。どの置き場にもチケットの無い子（取り下げた子など）の履歴も、その親子のものとして移す
- `flows/` の子のフロー

子は名前の形ではなくチケットの `parent:` 欄で親に結ぶ。移す順はマーカー・履歴・フロー・子のチケット・親のチケットで、1 本ずつ
一時ファイルから書いて読み戻してから元を消す。退避の置き場の途中（`logs` から行き先まで）にリンクがあれば書かずに止める。
親の承認済みチケットが親のワークツリーではなくワークスペースルートなどに在るときは、他の親子のチケットまで消さないよう、何も置かずに止める
（親のワークツリーで打ち直す）。

`logs/` は git が追跡しないので、git の上では削除になる。取り込み済みの親子のチケットでは C1 がこの削除をコミットして push してから
Draft を外すので、squash でマージすると既定のブランチにはチケットが残らない。取り込み済みでない親子のチケットでは、sh は Draft を外さずに止め、
削除をコミットして push してから `ready` を打ち直すよう案内する（確かめるのは、実行ファイルが答えた退避したツリーの置き場）。
条件を確かめた後、移す前に ready のマーカー `logs/archive/<リポジトリ>/ready/<親>.json`（どのツリーの、どの先頭（HEAD）から、どのファイルを
移すか）を書く。打ち直した `ready`（Draft を外し損ねた、移す途中で止まった）は、ready のマーカーが今のツリーのものであり、Draft を外したマーカー
（`ready.json`。ツリーか退避）のマージリクエストの番号が今回と同じときだけ、ワークツリーの側の条件（未コミット・push 済み）だけを見て、
残りを移してから通る。そろわなければ通常の条件の確かめに回る。退避の行き先に違う中身のチケットが既に在れば、上書きせずに止める。

`review ready` の標準出力は、1 行目がコメントの下書きのパス、2 行目が `tree <退避したツリーのルート>`。sh は C1 の外では 2 行目のツリーの
置き場に未コミットの変更が無いことを確かめてから Draft を外す（実行ファイルとの約束。2 行目が無い古い実行ファイルでは cwd のツリーを見る）。

退避は手元の機械にだけ残る補助の記録で、次のところが「閉じたもの」として読む。別の機械ではこの検査に使えない。

- 閉じた識別子の使い回し（承認と `--lint`）と、子の連番（続きの子の識別子）。同じリポジトリの退避だけを見て、大文字小文字だけが違う識別子も
  同じものとして数える
- 先行（`predecessors`）。どの置き場にも無い先行を、同じリポジトリの退避の `done/` から引く
- `ccnavi-sync.sh`。親のブランチがリモートから消えたとき、統合先の `done/` に親が無ければ、先に `ccnavi-review.sh merged` に聞き、
  マージ済みと答えれば閉じた親子のチケットにする。答えが得られないときだけ、統合先を取り直して確かめ直し、それでも無ければ退避に親
  （ツリーにチケットが残っていれば承認の時刻も同じ）があることで補う。判定の側も、取り込み状態が present のまま親のワークツリーを
  片付けた後なら、退避に親があれば閉じた親子のチケットとして読む（gone・blocked・壊れているときは今までどおり止める）
- 親のワークツリーの見分け（`ccnavi-sync.sh`・`ccnavi-fetch.sh`・`ccnavi-git.sh`・`--lint`）。ツリーから親のチケットが消えても、
  退避に親があれば親のワークツリーとして扱う
- 判定の走査。子のワークツリーに残った `doing/` の古いチケットは、退避に同じリポジトリで承認の時刻も同じチケットがあれば、作業中に戻さない

退避の削除は、C1 の見分けと実行後チェックが ccnavi の書き込みとして外す。外すのは、ready のマーカーにそのツリーから移したと載っていて
（比べている版がマーカーを書いたときの先頭と同じ間だけ。ready の削除をコミットしてツリーが進めば、マーカーは以後の削除に効かない）、
`done/`・`phases/`・`events/`・`flows/` の下にあり、消えた中身が退避したコピーと同じもの（履歴は ready の流れの行だけを足したもの）。
`logs/archive/` は記録と state の置き場の守り（`builtin-guard-records` と、シェルの側の `builtin-guard-setting-files`）が書き込みを止める。

**まだ残っているが早めに閉じたいとき**は、ユーザが端末で `close-early --reason <理由>` を打つ。作業中の子がいる間は打てない。残っているものを全部
見せてから y/N を取り、未着手の子の取り消し（理由は `close-early: <理由>`）、省略とレビュー済みのマーカー、未解決の受け入れ、残りの issue への書き出しを行う。
`phases/<親>/close-early.json` があれば、親はフィードバック計画が無くても閉じられる。Draft は親が片付けて push したあとの `ready` で外す。

### 実績のリスク

リスクは宣言ではなく実績で測る。子を `ticket finish` で閉じるとき、その子のワークツリーで `base_sha..HEAD` の差分を数えて点を付け、
`phases/<親>/<子>.risk.json` に残す。フェーズの点は子の最大値。**HIGH 以上なら、宣言に関わらずそのフェーズは人間レビューが要る扱いになる。**
宣言が `none` なら `chat` に上がり、マージリクエストを勧める文が出る（強制はしない）。設計は [9.9](docs/design/tickets/risk.md)。

配点は `.ccnavi/common/risks.yml`（組み込みの deny が守る。エージェントは書き換えない）。無ければ組み込み。

```yaml
version: 1
levels: {medium: 20, high: 40, critical: 70}     # リスクレベルの名前は固定、境目の点だけ動かす
factors:
  - {id: big-diff,   points: 25, lines_over: 300,   message: 行数が多い}
  - {id: many-files, points: 15, files_over: 10,    message: ファイルが多い}
  - {id: ci,         points: 35, glob: ".github/**", max: 35, message: CI に触った}
  - {id: deletes,    points: 20, deleted_over: 3,   message: 消したファイルが多い}
  - {id: complexity, points: 30, script: .ccnavi/common/scripts/complexity.sh, message: 複雑度}
  - {id: untested,   points: 30, judge: テストの無い振る舞いの変更を含むか, message: テスト無し}
```

| 系統 | 書き方 | 誰が測るか |
|---|---|---|
| 定量（組み込み） | `lines_over` / `files_over` / `deleted_over` / `glob`（当たるごとに加点。`max` で上限） | ccnavi が差分から数える |
| 定量（スクリプト） | `script: <.ccnavi/common/scripts/ の下>`（共通レイヤー。自身のレイヤーとプロジェクトのレイヤーはそのレイヤーの `.ccnavi/scripts/` の下） | ccnavi が `sh` で走らせる。cwd は子のワークツリー、`CCNAVI_BASE_SHA` / `CCNAVI_HEAD` / `CCNAVI_TICKET` / `CCNAVI_PARENT` を渡し、標準出力の整数か `{"points": N, "message": "…"}` を受け取る。失敗や読めない出力は**重いほうとして扱い**、その項目の点を加える |
| 定性（サブエージェント） | `judge: <問い>` | 判定が揃うまで子は閉じられない。`finish` が問いと差分の要約を `state/risk-judge-<子>.md` に書くので、親がそれをサブエージェントに渡し、報告を `sh .ccnavi/scripts/ccnavi-ticket.sh record-risk <子> <項目> yes\|no --reason <根拠>` で記録する。判定は子の HEAD に結び付けるので、HEAD が動けば判定し直す |

点と加点した理由は、閉じたときの出力、フェーズの終わりの文面、`--explain`、レビューの依頼文の先頭
（「このレビューのリスク: 58 (HIGH) — 行数が多い（…）」）に出る。壊れた `risks.yml` は組み込みを使い、`--lint` と閉じたときの出力がそのことを示す。

### issue・MR を指定された依頼

「#152 を直して」「!5 の指摘に対応して」のように issue や MR を指定して頼むと、`UserPromptSubmit` で ccnavi が依頼文から
指定（`#152`・`issue 152`・`.../issues/152`、`!5`・`MR 5`・`PR #12`・`.../pull/5`・`.../-/merge_requests/5`）を見つけ、
着手の前に `ccnavi-start.sh` を打つよう、エージェントに指示を足す。
**止めはしない**（指示を足すだけ）。コードブロックの中、`# 見出し`、色の `#fff`、`C#` などは拾わない。チケット制御が disable なら足さない。

エージェントが打つのは次の 1 本。既存の候補（下の `ccnavi-branches.sh` が探す）が無ければ、Draft MR・ワークツリー・ブランチを作る。

```sh
sh <ワークスペースルート>/.ccnavi/scripts/ccnavi-start.sh --issue 152   # MR なら --mr 5
```

候補を探すのは `ccnavi-branches.sh`（読むだけ。`--json` で JSON）で、出すのは、MR の元ブランチ、issue を参照している開いた MR の元ブランチ（ホストは `ccnavi-review.sh` と同じく gh / glab か、
curl と `GITHUB_TOKEN` / `GITLAB_TOKEN` で読む）、名前に番号を含むブランチ（手元と origin）、`issue: 152` を持つチケットの親のブランチ。
1 候補 1 行で、チェックアウトしているワークツリーと結び付くチケットを添える。ホストに繋げなければ手元の候補だけを出し、
「ホストは見ていない」と理由を書く。`projects/<名前>/` の中から打てば、そのプロジェクトのリポジトリを見る。

候補が複数（終了コード 3）なら、エージェントは一覧を見せて「既存のブランチで続ける・新しく `<先頭の語>-<番号>-<slug>` を切る・やめる」を聞き、
返事を待つ。ホストに届かない（終了コード 4）ときは、出力の案内どおり MCP で代行し、同じコマンドを打ち直す。終了コード 1・2 のときは、出力の理由をユーザに伝える。既存のブランチで続けるときは、親チケットの `branch:` に書いて承認を受け、承認の後に `ccnavi-git.sh switch <ブランチ>`
で移る（承認前の提案の `branch:` は使わない）。

### サブエージェントに渡すもの

`SubagentStart` で、cwd のワークツリーに関わる承認済みで開いている子の一覧（識別子・ワークツリー・範囲・満たしていない先行とその状態）を渡す。
親のワークツリーからならその親の子、子のワークツリーからならその子自身。それ以外には何も渡さない。判定は行き先で決まるので、これは案内でしかない。

### プロジェクトのスキル

プロジェクトは `.claude/` を持たない（hook やスキルなどの道具はワークスペースが持つ）ので、プロジェクト向けのスキルの形の手順書は
`projects/<名前>/docs/skills/<スキル>/SKILL.md` に置く（頭の frontmatter に `name` と `description`）。
Claude Code はそこを読まないので、ccnavi が `SessionStart` と `SubagentStart` で、cwd がそのプロジェクトの中にあるときだけ目録
（名前・説明・場所）を渡す。ワークスペースルートで始めて `cd` で入ったセッションには、cwd がそのプロジェクトの中にある最初の
`PreToolUse` で 1 度だけ添える。本文はエージェントが要るときに参考に開く（CLAUDE.md・ccnavi の知らせ・ガードと食い違えばそちらに従う）。
ディレクトリ名は `^[A-Za-z0-9._-]+$` のものだけを読む。上限は 30 本・4000 文字。専用の保護は無く、ほかのファイルと同じ判定になる
（直すのは承認したチケットの範囲の中。`.ccnavi/` の外に置いたのは、組み込みの保護を緩めずに書けるようにするため）。

### 参考にした運用

運用レイヤーは `参考/issue-mr-ticket-workflow`（`ticket.sh` / `worktree.sh` / `boundary.sh`）をもとにしている。

## ルールファイルが読めないとき

組み込みの既定を使って判定を続ける。止まらない（止めると壊れた設定を直す操作まで止まる）。設計は [5.6](docs/design/rules.md#56-ルールが読めないとき)。
既定に入っているのは取り返しの付かない操作だけ。`rm -rf`、`git push`、`git reset --hard`、認証情報の置き場、シェルからガード自身の設定への書き込み。

| 操作 | 既定での扱い | なぜ |
|---|---|---|
| `Write` / `Edit` でルールファイルを直す | 通す | ここを止めると直す手段が 1 つも残らない |
| シェルからルールファイルへ書き込む | 止める | ルールファイルを壊して緩い既定を使わせる手口を作らない |
| `git add` / `git restore --ours` でマージの衝突を解く | 通す | 戻すのは既にコミットされている内容で、新しい文面は書かない |

止めるのは書き込みの形だけで、場所の名前が出ただけでは止めない。既定を使ったことは、止めた回だけでなく通した回にも伝える。

## 記録

判定した呼び出しは、通したものも含めて 1 行 1 件で追記される。全 23 欄は [設計 付録 B](docs/design/appendix.md#付録-b-記録の-1-行)。

```
{"ts":"...","mode":"dry-run","event":"PreToolUse","tool":"Bash",
 "subject":"git push origin main","decision":"deny","enforced":false,
 "rules":["git-push"],"session":"...","ms":0.9}
```

| 欄 | 中身 |
|---|---|
| `decision` | `allow` `ask` `deny` `handover` `skip`。`handover` は権限モードに委ねた回 |
| `enforced` | 実際に適用したか。`dry-run` は `false` |
| `code` | 判定の根拠の種別。一覧は [設計 付録 A](docs/design/appendix.md)。よく出るのは `DENY_COMMAND_PATTERN`、`DENY_PATH`、`RULE_ASK`、`UNDECLARED`、`PARSE_UNCERTAIN`、`DENY_TICKET_SCOPE` |
| `reason` | `skip` の理由。`mode-disabled`、`event-not-checked`、`no-subject`、`nothing-to-run`、`payload-unusable`、`deadline-exceeded`、`tool-cannot-write`、`worktree-unreadable`（`detail` に理由）、`no-turn-baseline` の 9 つ |
| `tree` / `project` | 呼び出しの行き先が属するツリーとプロジェクト |
| `permission_mode` | Claude Code から来たモード。`handover` の行と合わせて読む |
| `paths` | 実行後チェックが検知した「種類とパス」 |
| `detail` | 実行後チェックでは `preexisting`・`known`・`restored`（予行では `would-restore`）の件数 |
| `degraded` | コマンドを読み切れずに生の文字列で判定した回。`command-taken-as-code`、`unterminated-quote`、`unterminated-substitution`、`ambiguous-substitution`、`backquote`（後ろの 2 つは `DENY_AMBIGUOUS_FORM` / `DENY_BACKQUOTE` で止めた回） |
| `quoted` | 引用の中から切り出したコマンドにだけ当たったルールの id |
| `unwrapped` | 実行役のコマンド（`env`・`sudo`・`sh -c` など）の中のコマンド、または `cd` で移った先から見たパス（設計 6.3.2）で当たったときの、そのコマンド。複数なら `\x00` でつなぐ |
| `fallback` | ルールファイルを読めず組み込みの既定で判定した回（`detail` にパス）。自身のレイヤーやプロジェクトのレイヤーが読めなかった回は、そのレイヤーの名前（`self`、`lib` など）が入り、そのレイヤーを空として判定している |
| `source` | 判定を下したルールのレイヤー。`common` / `self` / プロジェクトの名前。`rules` の id もレイヤーの名前付き（共通レイヤーは裸、それ以外は `self:worktrees`、`lib:schema`） |

```sh
jq -r 'select(.unwrapped) | .rules[]' logs/decisions.jsonl | sort | uniq -c
jq -r 'select(.decision == "deny") | .source' logs/decisions.jsonl | sort | uniq -c
```

チケットの子ごとの記録（`.ccnavi/approved/phases/<親>/<子>.risk.json` と `.judge.json`）にも項目ごとに `source` が入る。
フェーズ定義を根拠に置くフェーズのマーカーには、そのフェーズ定義のレイヤーが入る。

`subject`・`unwrapped`・`detail` は、書く直前に秘密の形（`Authorization:` / `Cookie:` の値、`token=` / `password=` /
`SECRET_KEY=` / `MYSQL_PWD=` などの値、`ghp_…` / `glpat-…` / `AKIA…` / `sk-…` / `AIza…` / `npm_…` / JWT などのトークン、
URL の `user:<値>@`、mysql の `-p<値>`、sshpass・docker login の `-p`、redis-cli の `-a` など）を伏せる。
18 字未満の値は `***`、それより長い値は先頭 6 字と末尾 4 字を残す（`ghp_Ab***Q7r8`）。判定は伏せる前の文字列で下す。

### 記録の後始末

セッションが始まるたびに、記録と state を片付ける。実行前チェックでは走らない。

- `decisions.jsonl` が `CCNAVI_LOG_ROTATE_MB`（既定 10 MB）を超えていたら、`logs/decisions.<日時>.jsonl` へ名前を変える。中身は捨てない
- ローテートした記録のうち、最後に書かれてから `CCNAVI_LOG_KEEP_DAYS`（既定 14 日）を過ぎたものを消す
- `logs/state/` の、セッションを名前に持つ記録を、そのセッションのどれもが `CCNAVI_STATE_KEEP_DAYS`（既定 14 日）
  書かれていなければまとめて消す。いま始まったセッションと、レビューの下書き・退避したファイルなどセッションを
  名前に持たないもの、知らない名前のファイル、リンクになった置き場は消さない

動かした数はセッション開始の行の `detail` に `pruned rotated=1 logs=2 state=5` の形で残る。手で走らせるなら端末から打つ。
エージェントが Bash から `--preview` の無い形を打つと、チケット制御に依らず `DENY_RECORDS_PRUNE` で止まる。
記録と state の置き場は、シェルからの書き込みと同じく `Write` / `Edit` からも止まる（`builtin-guard-records`）。

```sh
ccnavi --prune --preview   # 動かすものを並べるだけ
ccnavi --prune             # 動かして、動かしたものを出す（端末から）
```

## ルールが何に当たるかを確かめる

```sh
ccnavi --test Bash "cd /repo && git push"
```

```
verdict: deny (DENY_COMMAND_PATTERN)
tool: Bash
subject: cd /repo && git push
rules:
  deny:git-push  glob '*git push*'
    -> (?s:(?>.*?git\ push).*)\Z
response:
  [ccnavi] DENY_COMMAND_PATTERN (rule: git-push)
  ...
```

出るのは、判定と根拠コード、当たったルールとタイプ、`glob` を翻訳した正規表現、返る文面。パスなら行き着く先、実行役のコマンドの中で
当たったなら `unwrapped:` の行も出る。判定は実運用と同じ関数を通り（REQ-DIA-03）、モードは常に `enable`。
`permission_mode` は来ないので、ルールが言及していない呼び出しは `ask` として出る（権限モードごとの扱いを見たいなら payload を直接流す）。

いま適用されている宣言を数え上げるには `--explain`。レイヤーごとのルール、フェーズ定義とリスクの配点の表、承認されたチケットの作業範囲が出る。判定は行わない（REQ-DIA-01）。

```
■ rules 共通レイヤー（.ccnavi/common/rules.yml、deny 12 / ask 3 / allow 4）
  deny  guard-hooks                  Write|Edit|NotebookEdit            */.claude/hooks/*
■ rules 自身のレイヤー（.ccnavi/config/rules.yml、deny 0 / ask 0 / allow 1）
  allow self:worktrees               Write|Edit                         */.claude/worktrees/*
■ rules lib（projects/lib/.ccnavi/config/rules.yml、deny 1 / ask 0 / allow 0）
  deny  lib:schema                   Write|Edit                         */schema/*
■ phases（自身のレイヤー 7 種、lib 0 種）
  id              レイヤー        kind    title           review  scope
  design          自身のレイヤー  work    設計            mr      wip/design/*
■ risk（levels: medium 20 / high 40 / critical 70）
  id              レイヤー        加点条件            points  message
  big-diff        共通レイヤー    lines_over 300      25      行数が多い
```

（件数と中身は例。）順序は判定と同じ 共通レイヤー → 自身のレイヤー → プロジェクト（名前順）で、重複として捨てた定義は出ない。
読めないレイヤーは「読めない: <理由>。このレイヤーは空として扱う」、置いていないレイヤーは「このレイヤーは置いていない（無い = 空）」と出る。
`levels` の行は共通レイヤーの値。レイヤーで `levels` を書くとキーごとに小さいほうが採られるので、チケットに適用される値はその `project:` のレイヤーで変わる。

### 見本で確かめる

```sh
ccnavi --test-samples .ccnavi/common/rule-samples.yml
uv run python tools/check_rules.py     # 同じことを、state と記録を外して回す
```

見本をすべて判定に掛け、期待と食い違ったものを名指しする（1 件でもあれば終了コード 1）。見本は `deny` `ask` `allow` のタイプに置き、
タイプの名前が期待する判定になる。`subject` の `/repo` は走らせたワークスペースルートに読み替えられる。
`tool: Stop`（`subject: "(stop)"`）は、ターンの終わりに使われるルールがあるかを見る（あれば `allow`）。
ルールを 1 件足したら見本も 1 行足し、**止めたくないものも必ず一緒に置く**。

見本はエージェントが直接書けない（「コアファイルを守る」）。下書きはワークツリーの `scratchpad/` に置き、ルールの下書きと一緒にユーザに渡す。
`/ccnavi-config` スキルがこの流れを回し、フェーズ定義とリスクの配点も見る。

### 記録から候補を起こす

```sh
ccnavi --suggest [--json]
```

記録（`logs/decisions.jsonl` と、同じ置き場で回した `decisions.*.jsonl`）を数えて、ルールの下書きを出す。何も書かない。

| 候補 | 拾うもの | 出す下書き |
|---|---|---|
| ルールを足す（`rule`） | どのルールも言及せず（`UNDECLARED`）権限モードに渡った呼び出しが、同じ形で 5 回以上 | `ask` のルール。形は Bash なら先頭のコマンドとサブコマンド（`npm install`）、パスのツールならディレクトリの下（`{root}/docs/*`）、WebFetch ならホストの下 |
| 文面を見直す（`message`） | 同じルールが同じ呼び出し（「同じ呼び出しを繰り返し止めたとき」の均し方）を `CCNAVI_DENY_REPEAT` 回以上止めた | いまの `deny` のルールそのまま。直すのは `message` |

どちらも `rules.yml` の 1 タイプぶん（`rules:`）と、`rule-samples.yml` の 1 タイプぶん（`samples:`。記録の呼び出しを 3 件まで、ルートは `/repo`）の組で、
候補ごとに YAML の文書を 1 つずつ並べる。出すのは `deny` と `ask` だけで、`allow` は出さない（記録から通す側を勧めると、判定を緩める変更を機械が勧めることになる）。
候補はどれも、`--lint` と同じ読みでそのルールに error が無く、見本が `--test-samples` と同じ判定で期待したタイプになり、そのルールに当たったものだけ。
ルールを足す候補は、共通レイヤーのルールファイルのコピーに 1 件足した一時ファイルで試す。通らなかったもの（いまのルールでは別の判定になる形、
ルールファイルの外から来た根拠の拒否）は数だけを出す。読み切れなかった呼び出し（`degraded`）と、上限で切れた `subject` は数えない。
置くのはユーザで、`/ccnavi-config` の手順で確かめてから置く。

### 候補の JSON

`--suggest --json` の最上位。読み手は VS Code 拡張のルール管理画面。終了コードは常に 0。

| 鍵 | 何 |
|---|---|
| `version` | 形の版。整数。いま 1 |
| `root` / `rules_path` | ワークスペースルートと、共通レイヤーのルールファイル |
| `logs` / `records` | 読んだ記録の場所と、読めた行の数 |
| `candidates[]` | 候補 1 件ごと。`{kind, section, id, tool, count, summary, layer, rules_path, rule, samples, yaml}`。`kind` は `rule` か `message`、`section` は `ask` か `deny`、`layer` と `rules_path` は置くレイヤーとそのファイル、`rule` は書いたままの形の 1 件、`samples` は `{tool, subject, why}` の配列、`yaml` は文字で出すときと同じ下書き |
| `dropped` | 検証を通らずに除いた候補の数 |

### 試験の JSON

```sh
ccnavi --test Bash "cd /repo && git push" --json
ccnavi --test-samples .ccnavi/common/rule-samples.yml --json
```

`--test` と `--test-samples` の結果を JSON で出す。読み手は VS Code 拡張のルール管理画面。判定は文字で出すときと同じ関数を通る
（REQ-DIA-03）。`--json` のときは終了コードが常に 0 で、食い違いの数は `mismatches` で読む。
実例は `extensions/vscode/ccnavi-board/test/fixtures/test.json` と `samples.json`。`tests/core/test_test_json.py` が同じ例で形を確かめる
（形を変えたら `CCNAVI_BOARD_FIXTURE=1` を付けてそのテストを走らせ、例を書き直す）。

`--test --json` の最上位。

| 鍵 | 何 |
|---|---|
| `version` | 形の版。整数。いま 1。欄を足しただけでは上げない（拡張は知らない欄を読み飛ばす） |
| `root` / `rules_path` | ワークスペースルートと、当てたルールファイル |
| `known` | 判定が対象を取り出せるツールか。偽なら以下は空のまま（ルールを書いても当たらない） |
| `tool` / `subject` | 試した入力そのまま |
| `resolved` | パスを行き着く先まで解いた結果。入力と同じなら空 |
| `verdict` / `code` | 判定（`deny` / `ask` / `allow` / `skip`）と根拠コード |
| `reason` / `degraded` / `fallback` | `skip` の理由、生の文字列に当てたかどうか、組み込みの既定で判定したかどうか。無ければ空 |
| `unwrapped` | 実行役のコマンドが中で実行するコマンド、または `cd` で移った先から見たパスでルールに当たったときの、そのコマンド。複数なら `\x00` でつなぐ。元の形で当たったとき・当たらなかったときは空 |
| `rules[]` | 当たったルール。`{id, source, section, kind, written, pattern}`。`source` は `file`（ルールファイルの中）か `outside`（チケットの範囲のように外から来た根拠）。`kind` は `glob` か `regex`、`written` は書いたまま、`pattern` は翻訳後の正規表現 |
| `response` | エージェントに返る文面そのもの。無ければ空 |
| `quoted` | 当たったルールのうち、引用の中から切り出したコマンドにだけ当たったものの id。そういうルールがあるときだけ出る |

`--test-samples --json` の最上位。

| 鍵 | 何 |
|---|---|
| `version` / `root` / `rules_path` / `samples_path` | 上と同じ。加えて見本の場所 |
| `counts` | タイプごとの `{ok, total}` |
| `mismatches` / `skipped` | 食い違いの数と、allow の見本が判定に入らずに通った数 |
| `samples[]` | 見本 1 件ごと。`{expected, tool, subject, resolved_subject, why, known, verdict, code, reason, rules, ok, skipped}`。`expected` は置いたタイプ、`resolved_subject` は `/repo` を読み替えた後、`ok` は期待どおりか、`skipped` は判定に入らずに通ったか |

## 設定の検証

`--lint` は判定を行わず、防御を無効化しうる記述だけを報告する。ルールを書き換えたあとと、CI で走らせる。

```sh
ccnavi --lint                                # 実運用と同じ設定を見る
ccnavi --lint --rules .ccnavi/common/next.yml # 入れ替える前のファイルを見る
ccnavi --lint --json                         # 同じ苦情を JSON で（「lint の JSON」）
ccnavi --lint --flow .ccnavi/approved/flows/i0001-01-01.yml # 子のフロー 1 本も確かめる
```

```
ccnavi: 設定を検証する
  ルール: /repo/.ccnavi/common/rules.yml
  deny の場所を戻す: enable
  コアファイルを守る: enable
  チケット制御: enable
  チケットの承認の経路を守る: enable
  モード: dry-run（この起動の環境から解決したもの）
warn: (mode): dry-run なので判定しても呼び出しに手を出さない
error: deny:no-message: 文面が無い。ルールは代わりに何をすべきかを言わなければならない
error: deny:both: glob と regex の両方がある。どちらで判定するのか決められない
warn: (id 無し: ask Agent|Bash *npm audit*): id が無い。記録も報告もこのルールを名指しできない
error 2 件、warn 2 件、info 0 件
```

先頭の数行は「何を見て検証したか」（「チケットの承認の経路を守る」はチケット制御が有効なときだけ）。モードは検証を起動した環境から
解決したもので、`.claude/settings.json` の `env` はセッションにしか渡らないため、どこから来たかを示す。
`error` が 1 件でもあれば終了コードは 1、warn だけなら 0。CI で失敗にする対象は `error` だけでよい。
ルールの読み込みは判定と同じ経路を使う。

**ルール**

| 深刻度 | 拾うもの |
|---|---|
| error | ルールファイルが読めない、YAML として壊れている、版番号が違う |
| error | 文面（`deny` だけ）・`match`・`glob` を欠いたルール、`glob` と `regex` の両方があるルール |
| error | `ask` か `allow` に `message` を書いたルール（`ask` の文面はユーザの確認ダイアログにしか出ず、`allow` の文面はどこにも出ない。ルールは適用されたまま） |
| error | 組み立てられない正規表現、読み込み時に弾いている先読み・後読み・後方参照 |
| error | `deny` が空（何も止めないガードは、入っているように見えて入っていない） |
| error | `additionalContextFile` / `additionalContextOnceFile` が絶対パスか `..` で上に出るパス（実行時も読まない） |
| warn | `allow` が空（言及の無い呼び出しが全部、権限モードへ渡る） |
| warn | `id` の無いルール、`id` が重複するルール |
| warn | 判定が対象を取り出せないツールを `match` に書いたルール |
| warn | 何にでも当たる、または選択肢が 3 つ以上ある `allow` に `additionalContext`（か `additionalContextFile`）を書いたルール（当たるたびに同じ文が積まれる。`additionalContextOnce` は文脈ごとに 1 度なので指摘しない） |
| warn | `additionalContextFile` / `additionalContextOnceFile` が指すファイルが無い（作るまで何も足さない）、または先頭 4000 文字を超える（先頭だけが届き、切ったことを添える） |

**設定とモード**

| 深刻度 | 拾うもの |
|---|---|
| error | `.claude/settings.json` の `env` が `CCNAVI_MODE=disable` を宣言している |
| error | `CCNAVI_GUARD_TICKET_APPROVAL` / `CCNAVI_TICKET_CONTROL` が 2 値として読めない値（`enable` として扱って動く） |
| error | `.claude/settings.json` の `env` の `CCNAVI_BIN_PATH` が指す先は在るが、実行できない。hook が 126 で起動せず、何も判定していない |
| warn | モードが `disable` / `dry-run`、あるいはモードとして読めない値 |
| warn | 読めない `CCNAVI_RESTORE_IF_DENY` / `CCNAVI_GUARD_CORE_FILES` の値 |
| warn | 守る働きを持つ設定が止めない値になっている（`CCNAVI_GUARD_CORE_FILES` と `CCNAVI_RESTORE_IF_DENY` が `disable` か `dry-run`、`CCNAVI_GUARD_UNWATCHED` と `CCNAVI_GUARD_TICKET_APPROVAL` が `disable`） |
| warn | 上書き設定ファイル `ccnavi.settings.local.json` が読めない。このファイルを読むのは ccnavi 自身のソースツリーだけ（設計 4.3） |
| warn | `CCNAVI_TICKET_CONTROL=disable`（チケットの範囲も HITL ポイントも適用されない） |

`CCNAVI_BIN_PATH` が実行できるかは POSIX でだけ見る。Windows は実行ビットを持たないため。パスは `env` に書いたとおりに見て、`.exe` は補わない。

**レイヤー**

| 深刻度 | 拾うもの |
|---|---|
| error | 自身のレイヤーかプロジェクトのレイヤーのファイルが壊れている（そのレイヤーを空として扱っている） |
| info | 裸の `id` と全欄が一致する宣言を、後ろのレイヤーで捨てた（ルール / フェーズ定義 / 配点） |
| warn | ルールの同じ `id` がレイヤーをまたいで中身違いで在る（両方とも適用されている） |
| error | 共通レイヤーに `phases.yml` がある（判定に使わず空として扱っている。フェーズ定義は config にだけ置く） |
| error | フェーズ定義の 1 本の中で、同じ `id` が重複する、表示名が重なる、`overlap` / `requires` がそのファイルに無いフェーズ定義を指す（そのレイヤーを空として扱っている） |
| warn | 配点の同じ `id` で中身が違う（両方数え、後ろのレイヤーは `<レイヤー>:<id>`） |
| error | 合成後の `levels` が `medium <= high <= critical` になっていない（そのレイヤーを空として扱っている） |
| error | 配点の `script:` がレイヤーの外を指す（共通レイヤーから `.ccnavi/scripts/`、自身のレイヤーやプロジェクトのレイヤーから `.ccnavi/common/scripts/`）、または指す先が git プロジェクトルートに無い |
| error | `id` にコロンを書いた宣言（ルール / フェーズ定義 / 配点） |
| warn | ワークツリーの ccnavi ディレクトリに、元リポジトリに無いファイルがある（統合されるまで反映されない）。承認済みの領域の下は数えない（そのツリーの版が読まれる） |

レイヤーのファイルが無いことは報告しない（無いレイヤーは空で、正常な形）。

**hook の登録とスクリプト**

| 深刻度 | 拾うもの |
|---|---|
| warn | `PostToolUse` / `SubagentStart` / `SubagentStop` に ccnavi が登録されていない |
| warn | 登録はされているが git の作業ツリーではない（実行後チェックが何も検知しない） |
| warn | `.ccnavi/scripts/ccnavi-{ticket,review,git}.sh` が無い |
| warn | `.ccnavi/scripts/ccnavi-common.sh` の互換の版（`CCNAVI_COMPAT`）が実行ファイルと違うか、書かれていない（場所は `(version)`。直し方は「版の JSON」） |

登録の検査は `.claude/settings.json` しか見ない。そのファイルが無いときは何も報告しない（ユーザごとの設定は見えないため）。

**チケットとフェーズ**（チケット制御が有効なときだけ）

| 深刻度 | 拾うもの |
|---|---|
| error | 承認済みチケットの置き場への `Write` / `Edit` をルールが止めていない（承認の意味が消える） |
| error | 同じ識別子が複数の置き場にある、子の識別子が `<親>-<2 桁のフェーズ番号>-<2 桁の連番>` の形でないかフェーズ番号が `phase:` と食い違う、連番が重なる |
| error | 孫を持つ子 |
| warn | 範囲の超過がある子。超過は、親の範囲かフェーズ定義の `scope` を超える項と、regex の項。判定がその上限で切り詰めて止めるので、承認と同じく CI の終了コードを失敗にしない |
| error | ワークツリーの元リポジトリと承認済みチケットの `project:` が違う |
| error | 親が計画を持つのに `phases.yml` が読めない、`phases.yml` / `risks.yml` 自身の誤り |
| error | 承認済みチケットが読めない |
| warn | 未承認の提案がある |
| warn | `predecessors` を満たしていない（`done/` に無いか取り消し済み）のに着手している子 |
| warn | チケットの無いワークツリー、識別子と名前の一致しないワークツリー |
| warn | ワークツリー側に置かれた承認済みチケット（読まれない） |
| warn | 承認済みチケットはあるがワークツリーが無い（範囲が適用されない） |
| warn | リモートの種類に合うトークン（`GITHUB_TOKEN` / `GITLAB_TOKEN`）が無い、`origin` が無い |
| warn | ワークツリーでもワークスペースルートでもないのに `.claude/` を持つディレクトリがある |
| warn | 下書きの置き場（`scratchpad/`）に追跡されているファイルがある、または `scratchpad/` が git で無視されていない。ワークスペースと各プロジェクトをそれぞれ見る |

`scratchpad/` が追跡されていると、実行後チェックが下書きを範囲外として報告しはじめる。

**取り込みと本物とする側**。取り込み状態 `logs/state/sync/` があるときだけ見る。ただし 1 行目と 2 行目は取り込み状態が無くても見る。

| 深刻度 | 拾うもの |
|---|---|
| error | `.claude/settings.local.json` の `env` に承認と判定に影響する値（置き場のパス・プロジェクトの置き場・ccnavi ディレクトリ・state の置き場・記録の置き場・チケット制御・承認の保護・ccnavi 自身の設定の保護・戻す働き・確かめられないモードの止め・同じ理由の拒否の数え方・モード）がある。置けるのは `CCNAVI_INTEGRATION_BRANCH` だけ（`CCNAVI_BIN_PATH`・診断ログ・タイムアウト監視の秒は答えを変えないので指摘しない） |
| warn | 名前が親の識別子の親のワークツリーが、別のブランチをチェックアウトしている（取り込みと本物とする側の検査は、同じ名前のブランチだけを見る） |
| error | 取り込み済みの親子のチケット（取り込み状態がある）が決まらない: 取り込み状態が `gone`・`blocked`・壊れている（途中のリンクを含む）、`present` なのに親のワークツリーが無いか HEAD が別のブランチ。承認・状態の操作・実行前チェックがその親子のチケットを止める。解き方を添える |
| error | 取り込み済みの親子の承認済みチケットが、親のワークツリーの外にしか無い（元ツリーに未コミットで残ったチケットなど。信じないので止まる） |
| info | 閉じた親子のチケット（統合先の取り込み結果の `done/` に親のチケットがあるか、取り込み状態が `closed`）で、親のワークツリーが残っている（片付けてよい）。片付いた閉じた親子のチケットの削除せずに残す取り込み状態は報告しない |
| error | 統合先の取り込み結果が無い・壊れている・読めない・入れ替えが終わらない（途中のリンクを含む）。閉じた識別子の再利用を確かめられないので、そのリポジトリの新規の提案は承認しない |
| warn | 作業ツリーのレイヤー（共通レイヤー・自身のレイヤー）が統合先の取り込み結果のレイヤーと違う（統合先に入るまで、他の機械と Chrome の判定には使われない） |
| warn | 新規の提案の識別子が、統合先の取り込み結果の `done/` で閉じている（親子のチケットの取り込み状態の有無に依らない。承認はしない） |

**プロジェクト**（索引の 2 つのほかは `projects/` にプロジェクトがあるときだけ）

| 深刻度 | 拾うもの |
|---|---|
| warn | ワークスペースの git の索引に、`projects/` の下の通常のファイル（入れ子のリポジトリ以外）が載っている（ぶつかり）。ワークスペース自身のソースに `projects/` があり、名前は変えられないので、ワークスペースの `projects/` を別の名前に移すよう案内する。プロジェクトを置かないならそのままでも動く |
| warn | ワークスペースの git の索引に、`projects/` の下の入れ子のリポジトリ（gitlink）だけが載っている（載せ忘れ）。`.gitignore` に入れる前に `git add` したものとみて、索引から外して `.gitignore` に `/projects/` を足すよう案内する |
| warn | `projects/` がワークスペースの `.gitignore` に入っていない。上の 2 つのどちらかが出るときは出さない（同じ原因で、`.gitignore` に入れる案内が誤りか、先に索引から外さないと効かないため） |
| error | 予約名（`common` / `self`。大文字小文字は問わない）のプロジェクトがある |
| warn | プロジェクトが `.claude/` を持っている |

## lint の JSON

```sh
ccnavi --lint --json
```

`--lint` と同じ指摘を、同じ深刻度で 1 つの JSON にまとめて出す。終了コードも同じ（error があれば 1）。読み手は VS Code 拡張の
プロジェクト管理画面と、`--flow` で渡したフローへの指摘（`(flow)`）を読むフロー編集画面。この JSON の形は拡張との契約なので、変えるときは版を上げる。

| 鍵 | 何 |
|---|---|
| `version` | 形の版。整数（いま 1）。欄を足すだけなら上げない |
| `root` / `rules` / `mode` / `ticket_control` | 何を見て検証したか。ユーザ向けの文面が先頭に出すものと同じ |
| `projects[]` | 検証の対象になったプロジェクトの名前 |
| `problems[]` | 指摘 1 件ずつの `{severity, where, detail}`。`severity` は `error` / `warn` / `info` |
| `errors` / `warns` / `infos` | 件数 |
| `flow` | `--flow` を渡したときだけ在る。`{path, data, rendered, candidates}` |
| `flow.path` / `flow.data` | 確かめたファイルの絶対パス / 実行ファイルが読んだ中身（下）。`data` は読めなければ `null` |
| `flow.rendered` | `SubagentStart` で担当のサブエージェントに渡る手順の行。読めなければ `null` |
| `flow.candidates` | フローで選べる名前。`{agents: [{name, source}], skills: [{name, source}]}` |

`problems[].where` は、ユーザ向けの文面で `error:` の後ろに出る場所。`(projects/lib) rule-id` や `(self) (phases) design` のような形で、
`--flow` で渡したフローへの指摘なら `(flow)` になる。ファイル全体への指摘なら空。

`flow.rendered` は文字列の配列で、`flow_render.render` が返したままのもの。子のパスやロックの案内は入らない。

`flow.candidates` の `source` は `builtin` か `project`。`builtin` は `general-purpose`・`Explore`・`Plan` の 3 つで、`project` は
ワークスペースの `.claude/agents/*.md` と `.claude/skills/*/SKILL.md` から読む。`project` の名前は frontmatter の `name` で、
無ければファイルかディレクトリの名前を使う。frontmatter として読むのは頭の `---` の区間だけで、そこを YAML として読む。
エージェントはふつうのファイルだけを数え、`.md` の大文字小文字は区別しない。`.claude` を含む途中にリンクがあれば読まない。
`flow.candidates` は、フローが読めなくても載る。

### 子のフローを保存せずに確かめる

```sh
ccnavi --lint --json --flow /tmp/flow.yml
```

`--flow <パス>` は、子チケットのフロー（設計 9.3.1）1 本を、`SubagentStart` が読むのと同じ読み手・同じ検査
（大きさ、リンク・ふつうのファイルでない・ハードリンク、UTF-8 として読めない、YAML として読めない、別名、形）で読む。
読めなければ場所 `(flow)` の error として報告し、`detail` は渡したパスで始まる。無いファイルも error。VS Code の拡張の
フロー編集画面が、開くときと保存の前に本文を一時ファイルに書いて渡し、`(flow)` の指摘と `flow.data` を読む
（ほかの設定への指摘ではフローを止めない）。

読めたフローには、手順として怪しいところを `(flow)` の warn で足す（読むのも保存も止めない）。線の `from` / `to` が
無いノードを指す、`start` から届かないノード（`group` は外す）、`start` に入る線、`end` から出る線、分岐・問いの出口に
線が無い、`start` / `end` が無い、`subAgent` の `builtInType` と `skill` の `name` が `flow.candidates` に無い
（大文字小文字だけ違えば正しい表記を添える。空の欄と `:` を含むプラグインのスキルは報告しない）。巡回は報告しない。
`detail` は error と同じく渡したパスで始まる。

`flow.data` は読めた中身を JSON にしたもの（`flow.as_json`）。PyYAML（YAML 1.1）の読みのままで、`0755` は 493、
`yes` は `true`、`0o17` は文字列になる。JSON にそのまま載らない値は `{"$ccnavi": <種類>, ...}` の形で包む。

| 読めた値 | `data` での形 |
|---|---|
| 文字列・真偽値・null・リスト | そのまま |
| 整数（`±(2**53 - 1)` まで） | 数 |
| それより大きい整数 | `{"$ccnavi": "int", "text": <十進>}` |
| 浮動小数 | `{"$ccnavi": "float", "value": <数>}`。JSON では 1 と 1.0 の区別が消えるので包む。有限でなければ `"text"` に `inf` / `-inf` / `nan` |
| キーが全部文字列の辞書 | オブジェクト。キーに `$ccnavi` があれば下の `map` |
| キーが文字列でない辞書 | `{"$ccnavi": "map", "items": [[キー, 値], ...]}` |
| 日付・日時・バイト列（`!!binary`）・集合（`!!set`）・組（`!!omap` / `!!pairs`） | `{"$ccnavi": "date" \| "datetime" \| "bytes" \| "set" \| "tuple", "text": <表記>}`。ほかは `"other"` |

フロー編集画面は、開くときに自分の読み手（`yaml`、YAML 1.2）で読んだ中身とこれを見比べ、食い違えば開かない。
保存の前には、書き出す本文をこれに掛けて、画面が書こうとした中身と同じに読まれるときだけ書く。値の意味を
拡張が自分で決めないため。
読むのは `--lint` だけで、診断の外では無視し、`--test` / `--test-samples` / `--explain` でも「`--lint` でだけ読む」と
出して無視する。フローは判定の材料にならないので、レイヤーの置き場を動かすフラグ（下）とは別に数える。

### 1 つのプロジェクトのルールを保存せずに試す

```sh
ccnavi --test Write projects/lib/src/a.py --json --project-rules-file lib=/tmp/edited.yml
ccnavi --lint --json --project-rules-file lib=/tmp/edited.yml
ccnavi --lint --json --project-phases-file self=/tmp/phases.yml
ccnavi --lint --json --project-phases-file lib=/tmp/phases.yml
ccnavi --lint --json --project-risk-file self=/tmp/risks.yml
ccnavi --lint --json --project-risk-file lib=/tmp/risks.yml
```

- `--project-rules-file <名前>=<パス>` は、その名前のプロジェクトのルールファイルの代わりに `<パス>` を読む
- `--project-phases-file <名前>=<パス>` は、その名前のレイヤー（`self` は自身のレイヤー）のフェーズ定義の代わりに `<パス>` を読み、その 1 本として確かめる（足し算はしない）。配点（risk）のレイヤーには次の `--project-risk-file` がある
- `--project-risk-file <名前>=<パス>` は、その名前のレイヤー（`self` は自身のレイヤー）の配点の代わりに `<パス>` を読む。phases と違って足し算なので、共通レイヤーの配点と合わせて確かめる（境目の `levels` が合成後に逆転していないか、同 `id` で中身が違う衝突の warn、`script:` が指す先がそのレイヤーの git プロジェクトルートの `.ccnavi/scripts/` に在るか）。共通レイヤーの配点は今までどおり `--risk`。ファイルの無いレイヤーに渡しても、そのレイヤーの配点として合成する
- レイヤーの置き場を動かすフラグ 8 本（`--rules` / `--phases` / `--risk` / `--projects` / `--project-home` と上の 3 本）は、`--test` / `--test-samples` /
  `--lint` / `--explain` でだけ有効になる。診断の外（hook からの判定、`ticket` / `review` の副命令）に渡すと無視し、標準エラーにその旨を出す。
  守る対象（コアファイル）も差し替えを見ない
- `--root` と `--cwd` は 1 度しか渡せない（2 本目が在れば止める）。sh が自分のぶんを先に置くので、後勝ちの上書きを防ぐため

## ボードの JSON

```sh
ccnavi --explain --json
```

`--explain` のうちチケットに関わる部分を JSON で出す。読み手は VS Code の拡張「ccnavi ボード」。拡張はこれを並べるだけで、提案やマーカーを
自分では読まない。ネットワークには出ない。`version` が拡張の知っている版（いま 1）と違えば、拡張は読まずに版の違いを伝える。
実例は `extensions/vscode/ccnavi-board/test/fixtures/board.json`。`tests/ticket/test_board.py` が同じ例で形を確かめる
（形を変えたら `CCNAVI_BOARD_FIXTURE=1` を付けてそのテストを走らせ、例を書き直す）。

| 鍵 | 何 |
|---|---|
| `version` | 形の版。整数 |
| `root` / `generated_at` | ワークスペースルートと、出した時刻 |
| `settings` | `ticket_control`（チケット制御を使うか）/ `tickets`（提案の置き場、相対）/ `approved`（承認済みチケットの置き場）/ `projects`（プロジェクトの置き場） |
| `trees[]` | ワークスペースルート・プロジェクト・ワークツリー。`{name, root, project, kind}`。`kind` は `main` / `project` / `worktree` |
| `layers[]` | レイヤーごとの宣言。順序は 共通レイヤー → 自身のレイヤー → プロジェクト（名前順）。`{name, rules, phases, risk, phases_file}`。`name` は `common` / `self` / プロジェクトの名前。`rules` は `{path, unreadable, deny, ask, allow}` で、各タイプはそのレイヤーから実際に判定へ入ったルール `{id, section, source, match, kind, written, pattern, message}`（重複で捨てたものは入らない）。`phases` はそのレイヤーのファイルに書いてあるフェーズ定義 `{id, source, kind, title, review, scope}`、`risk` は `{path, unreadable, factors}`、`phases_file` は `{path, unreadable}`。phases と risk は合成前の、そのレイヤーのぶんだけ |
| `sums[]` | ワークスペースとプロジェクトごとの「共通 + 1 レイヤー」の和（判定と同じ合成。拡張は足し直さずこれを見せる）。順序は 自身のレイヤー → プロジェクト（名前順）。`{name, layers, rules, risk, phases}`。`name` は `self` / プロジェクトの名前、`layers` は `["common", <name>]`。`rules` は `{path, unreadable, missing, deny, ask, allow}` で、各タイプは共通レイヤーに足した和（`layers[]` と違い、他のレイヤーの定義で欠けない）。`risk` は `{levels, factors, fallback, problems}`（`levels` は実際に使う境目の点）、`phases` は使う 1 本 `{path, unreadable, order, types}`（足し算はしない） |
| `projects[]` | プロジェクトの名前 |
| `problems[]` | 読めなかった提案や承認済みチケットの説明。あっても他は出す |
| `pending_approval[]` | `--agree` で承認の対象に入る識別子（承認済みチケットの無い提案と、親の改版） |
| `tickets[]` | 識別子ごとに 1 件。提案と承認済みチケットのどちらか一方しか無くても出す |
| `parents[]` | 承認済みチケットのある親ごとの局面とフェーズ。承認前の親はフェーズを持たないのでここに無い |
| `archived[]` | 手元の退避（`logs/archive/`。`ready` が閉じた親子のチケットを移した先）にある閉じたチケット。表示のためだけの任意の欄で、判定（承認待ち・先行・局面）には混ぜない。`tickets[]` に同じ識別子があるものは出さない。`{ticket, parent, phase, title, project, path, approved_at, started_at, completed_at, cancelled_at, cancel_reason, history}`。`path` は退避したファイルの絶対パス、`history[]` は退避した履歴（`tickets[]` の `history[]` と同じ形。最後の行は `kind` が `archived`）。この欄を持たない古い実行ファイルの答えも同じ版のまま読める（拡張は無ければ空とする） |

`tickets[]` の 1 件。

| 鍵 | 何 |
|---|---|
| `ticket` / `parent` / `phase` / `title` / `project` / `issue` / `predecessors` / `human_review` | 提案（無ければ承認済みチケット）の frontmatter から |
| `predecessors_unmet[]` | 満たしていない先行。`{ticket, state, label}`。`state` は `todo` / `doing` / `review`（先行が閉じれば満たす）と `cancelled` / `missing` / `scattered` / `self` / `ancestor` / `cycle`（待っても満たさない）、`label` はユーザ向けの言葉（「作業中（doing/）」など）。空でなければ承認と着手（`start`）が止まる（書き込みと `finish` は止まらない）。先行が無い子・親・閉じたチケットは空。ボードはこれで「先行待ち」のバッジを出し、自分では数えない |
| `proposal` | `{state, tree, tree_root, path}`。本物とする側のツリー（親のツリー。無ければ元ツリー）の提案の置き場で見つけたもの。`state` は `todo`（承認待ち）/ `review`（レビュー待ち）。`doing/` `done/` に在るときは `null`。承認済みの識別子では、本物とする側のツリーを、承認済みチケットがどのツリーにあるかで決める。そのツリーの外に残った古い提案（承認の前に切ったワークツリーの `todo/` など）は出さない（`seen_in` には出る） |
| `copy` | `{status, approved_at, approved_from, source_tree, path}`。`status` は `none`（未承認）/ `open`（`doing/`）/ `review`（`wip/proposals/review/`）/ `closed`（`done/`）。`approved_at` は承認の時刻で、承認済みチケットの欄ではなく、状態の履歴の `approved`（続きの子は `raised`）か、無ければ前の版の承認が書いた記録（古い形の `ccnavi_approved.approved_at`）、それも無ければ `doing/<識別子>.md` を足したコミットから引く。`approved_from` はどこから引いたか（`history` / `record` / `commit` / `uncommitted`（履歴もコミットも無い。手で置いてまだコミットしていない）/ 空（分からない））。`uncommitted` と空のとき `approved_at` は空。`source_tree` は前の版の承認が書いた欄で、新しい承認済みチケットでは空 |
| `blocked` | 空でなければ「読めるが信じられない」理由。判定はこのチケットのワークツリーへの書き込みを `DENY_TICKET_BLOCKED` で全部止める。`status` は `open` のままなので、止まっていることはこの欄でしか分からない |
| `worktree` | `{exists, path, project}`。`.claude/worktrees/<識別子>` が本物のワークツリーか（設計 9.5 の相互参照） |
| `started_at` / `completed_at` / `base_sha` / `cancelled_at` / `cancel_reason` | スクリプトが書く欄 |
| `seen_in[]` | 同じ識別子のチケットがある場所の全部。`{tree, state, path}`。子のワークツリーは親のブランチから切るので、子のワークツリーにも親の提案があるのが普通 |
| `scattered[]` | どれが本物か決まらない、チケットがある場所の全部。`{tree, state, path}`。決まっていれば空。本物とする側のツリー（親のツリー → 元ツリーの順）で絞り込んでも 2 つ以上残り、その残りが 2 つの置き場にまたがるか同じ置き場に重なるときに入る。状態の操作が「複数の場所にある」で止まる条件と、`--lint` が ERROR を出す条件と同じ。`seen_in` の数は食い違いを意味しない |
| `flow` | 子のフロー（設計 9.3.1）。親と、フローが無い閉じた子（終わった・取り消した）は `null`（ボードはこのとき「フローを作る」を出さない）。`{path, rel, tree, exists, linked, locked, draft}`。`path` は読む先の絶対パス、`rel` はツリーのルートからの相対、`tree` はそのファイルを持つツリーのルート、`exists` はファイルが在るか、`linked` はファイルかツリーのルートからそこまでの途中がシンボリックリンクか（真なら読まないし書かない）、`locked` は判定がいまその書き込みを `DENY_TICKET_FLOW_LOCKED` で止めているか（着手中）、`draft` はエージェントが書く下書き（効力は無い）の `{path, rel, exists, linked}`。`path` が指すのは本物とする側のツリー＝承認済みチケットが在るツリーの版だけで、子のワークツリー上のフローは読まない。承認の前は提案が在るツリーで、承認でフローもチケットと一緒に動く。`rel` は承認済みの領域の固定の置き場 `.ccnavi/approved/flows/<子>.yml` で、中身は YAML。`draft` の置き場はフローと同じツリーの `wip/proposals/flows/<子>.yml` で、ボードはパスを組まずにこれを読み、いまのフローと違えば「提案あり」を出す。読むのは承認済みチケット（無ければ提案）の欄。ボードは `locked` をそのまま写し、自分で組み直さない |
| `risk` / `judge` | 子の記録 `phases/<親>/<子>.risk.json` と `.judge.json` の中身。無ければ `null` |
| `history[]` | 状態の履歴の新しい側 20 件を古い順に。`.ccnavi/approved/events/<識別子>.ndjson`（本物とする側のツリー＝承認済みチケットが在るツリーの版）の 1 行ずつで、`{at, ticket, kind, from, to, via, ...}`。`at` は UTC の ISO 8601、`kind` は `approved` / `revised` / `raised` / `started` / `finished` / `cancelled` / `settled` / `archived`（置き場が動いたもの。`archived` は `ready` が退避したことを示し、`to` は `archive`）と `phase-mark` / `phase-reopened` / `parent-mark`（マーカー。親の履歴に残り、`from` / `to` は `null` で `phase` / `mark` を持つ）、`from` / `to` は置き場の名前（`todo` / `doing` / `review` / `done`）、`via` は `cli`（sh の副命令）/ `terminal`（ユーザが端末で）/ `board`（ボード）/ `hook` / `chrome`（Chrome 拡張）。種類ごとに `phase`・`mark`・`reason`・`base_sha`・`tree`・`followup_of`・`cleared` が付く。補助で、状態は置き場の欄で決まる。履歴が無ければ空。読めない行があれば飛ばして `problems[]` で知らせる |

`parents[]` の 1 件。

| 鍵 | 何 |
|---|---|
| `ticket` / `closed` / `stage` | 識別子、閉じた承認済みチケットか、いまの局面（設計 9.7 の文。閉じた親は空文字） |
| `plan` / `feedback` | 全体計画とフィードバック計画（`null` は未計画） |
| `close_early` / `ready` / `closed_record` | 親のマーカー `close-early.json` / `ready.json` / `closed.json` の中身。無ければ `null`。`closed.json` は親を閉じたときに置かれ、どのフェーズをどこで見たか（`reviews`）を持つ |
| `accepted_threads[]` | ユーザが受け入れた未解決スレッド |
| `phases[]` | 番号順。`{number, type, title, label, state, tickets, states, marks, review_required, review_kind, gate_closed, review_waiting, deferred, review_at, covers, risk, risk_escalates, risk_line}`。`state` は `planned`（子がまだ無い）/ `active` / `ended`。`marks` はマーカーの種類 → 中身。`review_kind` はユーザがどこで見るか（`none` / `chat` / `mr`）。`gate_closed` は判定が使うのと同じ値で、レビューが済むまで止めているかを示す（ユーザに見せる名前は「レビュー準備中」「レビュー待ち」）。`review_waiting` は依頼を出したのに止まったまま（ユーザのレビュー待ち）で、ボードはこれを写すだけで組み直さない。`chat` のフェーズは依頼を出さないので常に `false`。このセッションで見る待ちは `review_kind` と `gate_closed` で読む |

## 承認の JSON

```sh
ccnavi --agree --preview --json [<絞り>...]           # 一覧を見る（承認済みチケットは置かない）
ccnavi --agree --preview --verify --json [<絞り>...]  # 承認できる状態かを確かめる（同上）
ccnavi --agree --yes <識別子,…> --digest <値> --json [<絞り>...]    # 見せた一覧をそのまま承認する
```

VS Code の拡張が、承認をボードのオーバーレイで行うための形。承認の対象を組むのは `--agree` と同じ関数で、`--explain --json` の
`pending_approval` と答えが食い違わない。実例は `extensions/vscode/ccnavi-board/test/fixtures/approve-preview.json` ほか。
`tests/ticket/test_approve_json.py` が同じ例で形を確かめる（形を変えたら `CCNAVI_BOARD_FIXTURE=1` を付けてそのテストを走らせ、例を書き直す）。
`version` が拡張の知っている版（いま 1）と違えば、拡張は読まずに版の違いを伝える。

`--preview` の答え。

| 鍵 | 何 |
|---|---|
| `version` | 形の版。整数。`--yes` と同じ番号 |
| `root` / `generated_at` | ワークスペースルートと、出した時刻 |
| `batch[]` | 承認の対象。`{ticket, title, parent, phase, revision, tree, path, branch, existing_branch, overflow}`。`branch` は親のブランチ名（親の `branch:`、無ければ識別子。子は子の識別子）、`existing_branch` は `branch:` が既にあるブランチ（手元か origin、または提案がそのブランチの上）を指すか。`parent` と `phase` は子だけ（親は `null`）。`revision` は親の改版。空なら承認待ちが無い |
| `batch[].overflow[]` | 範囲の超過（親の範囲・フェーズ定義の `scope` を超える項、regex の項）の説明。文字列の配列で、無ければ `[]`。承認は通るが、判定で止まる |
| `text` | 承認画面の本文そのまま。拡張はこれを等幅で並べ、項目には分けない |
| `digest` | 見せた中身のダイジェスト。`--yes` の `--digest` にそのまま渡す |
| `rejected[]` | 承認の対象にしない提案。`{ticket, problems[]}`。載るのは形の壊れた提案（親が承認されていない、計画に無い番号、順序、先行（`predecessors`）が `done/` に無いか取り消し済みなど）だけで、範囲の超過だけの子は `batch[]` に載る |
| `problems[]` | 読めない提案や承認済みチケットの説明 |

`--preview` 自身は、対象にしない提案があっても 0 で返る。答えが要るときだけ `--verify` を足す。
`--verify` の答えは `--preview` の答えに `verify` を足したもの。終了コードが答えそのもの（0 = はい、3 = いいえ。1 は使い方か設定の誤り）。

| 鍵 | 何 |
|---|---|
| `verify.ok` | 真偽。`--agree` を打てば、指定したもの（指定が無ければ承認待ち全部）がそのまま承認の対象に入るか |
| `verify.reason` | 答えの理由の名前。全部入るなら `ok`、入らないなら `refused`（絞りが通らない）/ `nothing-pending`（承認待ちが 1 件も無い）/ `rejected`（承認の対象にしない提案がある）。読めない提案（`problems[]`）はここに出ない |

`--verify` は、`--agree` が承認を止めない範囲の超過（`batch[].overflow`）と読めない提案（`problems[]`）では「いいえ」にしない。どちらも本文には出す。
承認されないものは `ccnavi --lint` も同じ関数で名指しする。ただし「まだ承認できない」子は `--lint` では warn で、先行が閉じれば通る子もここに入る。取り消し済み・どこにも無い・複数の場所にある・自分自身・自分の親・輪になった先行は error。

`--yes` の答え。値は `--preview` の `batch[].ticket` をカンマで並べたもの。

| 鍵 | 何 |
|---|---|
| `version` | 同上 |
| `approved[]` / `copies[]` | 承認した識別子と、置いた承認済みチケットのパス |
| `lines[]` | 端末なら標準出力に出ていた行（マーカーを消したことなど） |
| `prompt` | Claude Code に渡す文。拡張がオーバーレイの 2 ボタンで渡す。hook は承認を伝えない |
| `mismatch` | 一覧か中身が変わっていたとき。`{expected[], current[]}`、ダイジェストが違えば `digest: {expected, current}`。このとき承認済みチケットは置かれず、終了コードは 1 |
| `partial` | 置いている途中で止まったとき（書けない、など）。`{placed[], ticket, reason}`。`placed[]` はそこまでに承認済みチケットに入ったぶん（新規は置いた、改版は書き換えた）、`ticket` は止まったところ、`reason` は理由、`lines[]` は止まるまでに出た行（端末なら標準出力に出ていたぶん）。**置いたものは戻さない**ので、どこまで進んだかをそのまま返す。終了コードは 1。承認の push の sh（`ccnavi-push-approved.sh`）は送られていない |

- `--yes` は端末を求めない代わりに、見せた一覧と今の一覧が同じで、`--digest` の値（大文字小文字は問わない）が承認のときに読み直した中身のダイジェストと
  合うことを求める。`--digest` が無ければ承認せず、終了コードは 1
- ダイジェストが覆うのは承認画面の本文と、判定が読んだ中身（`read_set`。承認済みチケット・提案・マーカー・フェーズ定義・取り込み状態の、
  ブランチ名とツリーからの相対パスごとの中身のハッシュ。無かったファイルも「無い」として入る）と、一括のチケットごとに書き出す中身（新規は提案の
  ファイルのバイト列そのもの、改版は計画を差し替えた承認済みチケット。親なら `phases/<親>/workflow.yml` に書く中身を後ろに足す）。見せたあとに判定が読んだものが 1 つでも変われば、
  画面が同じでもダイジェストは変わる
- エージェントが Bash や PowerShell で `--yes` を打つ形は、組み込みの deny（`builtin-guard-ticket-approval`）が止める。`--preview` は通す
- 後ろの `<絞り>` は `ccnavi --agree <識別子>...` と同じで、承認の対象を狭める。`--yes` の値（ユーザが見た識別子）とは別に渡す。
  比べるのは「その絞りで今できる一覧」と「見た識別子」

## 残った指摘の JSON

ボードの「決める」が読み書きする形。拡張は sh（`ccnavi-review.sh decide`）を子プロセスで打ち、sh が取得した JSON を実行ファイルに渡す。
版は `version`（今は 1）で、承認の JSON と別に数える。

`decide <N> --preview`（実行ファイルは `--reviewed N --accept-unresolved --preview --json`）は何も置かない。

```json
{"version": 1, "parent": "i0050", "phase": 2,
 "mr": {"number": 7, "url": "https://…/merge_requests/7"},
 "can_issue": false,
 "threads": [{"key": "https://…#note_1", "url": "https://…#note_1", "path": "src/a.py", "line": 3, "body": "…"}],
 "digest": "<64 桁の 16 進>"}
```

- `key` は選択を指摘に結び付ける鍵（URL か、無ければスレッドの id）。受け入れの記録にもこの値が入る
- `can_issue` は issue に回せるか（フィードバック計画が承認されたあとだけ真）
- `digest` は見せた指摘のダイジェスト。親・フェーズ・マージリクエストの番号・`can_issue`・各指摘の鍵と場所と本文から組む

`decide <N> --choices <JSON> --digest <ダイジェスト>`（実行ファイルは `--yes <JSON> --digest <ダイジェスト> --json`）は
`{"<key>": "keep" | "fix" | "issue", …}` を受け、見せた指摘の全部に 1 つずつ付いていることを求める。

```json
{"version": 1, "ok": true, "parent": "i0050", "phase": 2,
 "reviewed": false, "followup": "i0050-02-02",
 "kept": ["…"], "fix": ["…"], "issue": [],
 "issue_draft": "", "prompt": "[ccnavi] ユーザが…",
 "issue_url": "", "warning": ""}
```

- `reviewed` はレビュー済みになったか（直す指摘が無ければ真）、`followup` は起こした続きの子
- `issue_url` と `warning` は sh が足す。置いたあとの投稿（issue とコメント）で起きたことで、置いたことは戻さない
- 見せた指摘と今の指摘が違えば、何も置かずに `{"version": 1, "ok": false, "mismatch": true, "digest": {"expected", "current"}}` を
  返して 1 で終わる

## 版の JSON

```sh
ccnavi --version          # 1 行 1 項目（`compat: 2` など。sh が読む）
ccnavi --version --json
```

実行ファイルが自分自身の情報を出す。設定もワークスペースも読まず、常に 0 で終わる。読み手は VS Code 拡張と
`.ccnavi/scripts/` の sh で、どちらも起動のときに互換の版を自分のものと比べる。

```json
{"schema": 1, "version": "0.1.0", "commit": "88182c2…", "built": true, "compat": 1,
 "flags": ["--accept-unresolved", "--agree", "…", "--version", "--yes"],
 "formats": {"phases": 1, "risks": 1, "rules": 1, "ticket": 1}}
```

| 鍵 | 何 |
|---|---|
| `schema` | この JSON の形の版（いま 1）。欄を足すだけなら上げない |
| `version` | ccnavi の版（`pyproject.toml` の `version`） |
| `commit` | 組み立ての元のコミット。`build.py` が組み立てのときに埋め、未コミットの変更があれば `-dirty` が付く。ソースで動かしているときは `unknown` |
| `built` | 組み立てた実行ファイルなら真、ソースで動いていれば偽 |
| `compat` | 互換の版。実行ファイルと、それを呼ぶ側（sh と拡張）の契約の版（下） |
| `flags` | 受け付けるフラグ。引数の定義から引くので、フラグを足せばここにも並ぶ |
| `formats` | 読む書式の版。レイヤーのファイル（`rules.yml` / `phases.yml` / `risks.yml`）とチケットの頭の `version:` と比べるもの |

**互換の版**は 3 か所に同じ値で書く。実行ファイル（`src/ccnavi/entry/version.py` の `COMPAT`）、sh（`ccnavi-common.sh` の
`CCNAVI_COMPAT`）、拡張（`src/core/version.ts` の `EXTENSION_COMPAT`）。Chrome 拡張は組み立てのときに実行ファイルの値を埋め込む。
sh や拡張が頼るフラグや出力の形を、呼ぶ側を直さないと動かない形に変えたときに上げる。データの形（承認済みの置き場に置くものの並び、
待ち方の置き場、取り下げの条件など）が変わるときも上げる。古い実行ファイル（古いコアを積んだ Chrome 拡張を含む）が
新しい形のデータを読み違えるため。フラグや欄を足すだけで、データの形も変わらないなら上げない（拡張は使う前に `flags` を見る）。
レイヤーのファイルは頭の `version:` が書式の版を示し、読めない版は `--lint` が既に error を出すので、レイヤーに別の版は足さない。

食い違ったとき、どこでも直し方を名指しする。止めはしない（止める・通すは実行ファイルと hook が持つ）。

| 見つける場所 | 言うこと |
|---|---|
| sh（`ccnavi-ticket.sh` / `ccnavi-agree.sh` / `ccnavi-review.sh`） | 実行ファイルを起動する前に `--version` を読み、食い違えば標準エラーに 1 行出して先へ進む |
| VS Code 拡張 | 起動のときに 1 度問い合わせ、食い違えば通知を出す。`--flow` を使う前には `flags` を見て、無ければフロー編集画面を開かない・保存しない |
| `ccnavi --lint` | sh の `CCNAVI_COMPAT` と比べ、`(version)` の warn を出す |

直し方は、ccnavi のリポジトリ（`build.py` とソースがある）なら `uv run --with pyinstaller python build.py` で組み立て直し、
配布先なら ccnavi のリポジトリで組み立てて `sh scripts/ccnavi-setup.sh <ワークスペース> --force` で実行ファイルと sh を配り直す。
拡張のほうが古ければ拡張を入れ直す。`--version` を知らない実行ファイルは、この仕組みより前の古い版として扱う。

## ドキュメントの索引

```sh
ccnavi --docs --text マージ --format detail           # 話題で当たりを付ける
ccnavi --docs --type adr --sort mtime -r --limit 10     # 新しい ADR から 10 本
ccnavi --docs --tag worktree --tag git --format path    # どちらかのタグを持つもの（OR）のパスだけ
ccnavi --docs --path docs/claude --format count         # 件数だけ
```

md を本文ではなく頭の frontmatter で引く。grep は当たった行を返すので、そのファイルが何の文書かは開くまで分からず、
よそからの言及も同じ重みで混ざる。frontmatter の書き方は [docs/claude/frontmatter.md](docs/claude/frontmatter.md)。

引くのはワークスペースと、プロジェクトの置き場の直下の各プロジェクト（それぞれ別の git）を合わせたもの。どこから打っても同じで、
パス（`concept_id`）はワークスペースルートから書く（`projects/lib/docs/x`）。`--path projects/lib` で 1 つのプロジェクトに絞れる。
どのツリーもルートに `.git` を持つものだけで、`.git` の無い `projects/<名前>/` は `--docs` が標準エラーで名指しする。
それぞれの `git ls-files --cached --others --exclude-standard` のうち `*.md` を載せ、実体の無いもの（消してまだステージしていないもの）、
シンボリックリンク、ccnavi ディレクトリ（`.ccnavi/`）の下は載せない。ワークスペースの一覧からはプロジェクトの置き場を外す。
実体のパスがそのツリーの外に出るディレクトリ（ジャンクションやシンボリックリンク越し）は読みも書きもしない。
同じ `concept_id`（NFC で揃えて同じもの）は 1 本にまとめる。

frontmatter は md の頭の 64 KiB までを UTF-8 として読む（それより後ろで閉じる frontmatter と、UTF-8 でない md は読めない）。
frontmatter の無い md は頭の 4 KiB だけで止める。

引く前に、md が直下にあるディレクトリごとの `index.jsonl` を新しくする（`--no-refresh` で省く）。`concept_id` と `mtime` が
同じ行は読み直さずに使い回し、中身が変わらなければ書かない。初めての回は md を全部読むので、md が数千本あるツリーでは
数秒かかる。2 回目からは `mtime` の変わった md だけを読む。

- **書くのは git がそこの `index.jsonl` を無視しているときだけ。** md を持つディレクトリのどれでも無視されていないツリー
  （ワークスペースかプロジェクト）は索引の対象外にし、引かずに標準エラーで名指しする。使うには、そのリポジトリの `.gitignore` に
  `**/index.jsonl` を足す（実行ファイルの `ccnavi` は `.gitignore` を書き換えない。ワークスペースには導入スクリプト
`scripts/ccnavi-setup.sh` が配るときに足す。プロジェクトには足さない）。一部のディレクトリだけが無視されていない（追跡されている
  `index.jsonl` がある）なら、そこは書かずに行だけを組む
- **ccnavi の形でない `index.jsonl` は上書きも削除もしない。** 空か、空でない行が全部下の 4 つの鍵を持つ行として読めるときだけを
  ccnavi のものとみなす。それ以外（よその道具のファイル、壊れた行、リンク、16 MiB を超えるもの、入れ子が 32 段より深い行、
  `NaN`・`Infinity` を持つ行）は触らず、行だけを組んで引き、`--docs` は標準エラーで、`SessionStart` は 1 行で名指しする。
  行は `\n` だけで割る（値の中の U+2028・U+2029・U+0085 では割らない。行末の `\r` は落とす）
- 書くときは git のディレクトリ（`.git/`、submodule なら指す先）に一時ファイルを排他で作り、置き換える。作業ツリーの
  `git status` には出ない。打ち切りで残った一時ファイル（`ccnavi-index-*.tmp`）は、10 分より古ければ次の回に消す。
  `.git` が作業ツリーと別のファイルシステムにある（置き換えが EXDEV で落ちる）ときは、そのディレクトリの下に
  `.ccnavi-tmp-<番号>/index.jsonl` を作って置き換え、ディレクトリごと消す。この名前が git に無視される（`**/index.jsonl` で当たる）
  ことを先に確かめ、無視されなければ書かない。残骸は 10 分より古く、中身が `index.jsonl` だけのものに限って消す
- git への問い合わせの失敗（git が無い・期限切れ・壊れたリポジトリ）は、`.git` が無いのとも無視されていないのとも別に扱う。
  `--docs` は「git への問い合わせに失敗したので引かない」とそのツリーを名指しし、`SessionStart` は何も出さない。
  git にはユーザの環境の pathspec の読み方（`GIT_LITERAL_PATHSPECS` など）を外して聞き、`:(exclude)` のような名前の
  ディレクトリも字どおりに扱う

`SessionStart` はサブエージェントでなければ、ワークスペースとプロジェクトの索引を同じ手順で新しくし、引き方と frontmatter の
決まりの要点を `additionalContext` につける。対象外にしたツリーと書き換えなかった `index.jsonl` があれば短く名指しする。
md が 1 本も無い・git の外なら何も出さない。新しくするのに使うのは 3 秒（`docsearch.START_SECONDS`）と hook の判定の期限の
残りの小さいほうまで。まず全部のディレクトリで使い回せる行を見て、読み直しの要る md を、待つ本数の少ないディレクトリから
1 本ずつ期限を見ながら読む。過ぎたら読むのをやめ、どのディレクトリも「読めた md の新しい行と、読めなかった md の既存の行」で
書く（読めなかった md は次の回に読む）。大きなディレクトリも回を重ねれば埋まり、小さなディレクトリはその後ろで待たない。
残りが 0.5 秒（`MIN_REFRESH_SECONDS`）に満たない（期限が既に切れている）ときは新しくせず、md を読まず書かずに既存の
`index.jsonl` の行だけで案内する（git には 1 秒だけ与える。それも間に合わなければ、ワークスペースルートに md があれば
引き方だけを出す）。`--docs` は引く前に期限なしで新しくする。何が起きてもセッションの開始は止めない。

| オプション | 対象 | 一致 |
|---|---|---|
| `--type <値>` | `frontmatter.type` | 完全一致 |
| `--tag <値>` | `frontmatter.tags` の要素（スカラーでも 1 要素のリストとして扱う） | 完全一致 |
| `--keyword <値>` | `frontmatter.keywords` の要素 | 完全一致 |
| `--path <部分>` | `concept_id` | 部分一致 |
| `--text <部分>` | `concept_id`・`mtime`・frontmatter のすべてのスカラーの値（キー名は含まない） | 部分一致 |
| `--since <日時>` / `--until <日時>` | `mtime`（`YYYY-MM-DD[THH[:MM[:SS]]]`。在る日時だけ受ける）。`--until` は書いた桁の終わりまで（日付だけなら `T23:59:59`、`THH` なら `:59:59`、`THH:MM` なら `:59`） | 以上 / 以下 |

同じオプションの繰り返しは OR、違うオプションどうしは AND。大文字小文字は区別せず、文字列は NFC に揃えてから比べる。
並べ方は `--sort path|mtime|type|title`（既定 `path`）、
`-r` / `--reverse` で逆、`--limit <N>` で並べた後の先頭 N 件（0 以下は全部）。
`--sort` の `type` と `title` は大文字小文字を区別しない。第 2 キーは `concept_id`。
`--format` は `table`（既定。`type` / `concept_id` / `title` を、全角を幅 2・結合文字を幅 0 として桁揃え）・`path`・`detail`・`json`・
`jsonl`・`count`（`matched=<絞った数> [shown=<出した数>] total=<全部>`。`shown` は `--limit` で切ったときだけ）。
`--json` は `--format json` と同じ。`table` と `detail` は同じ件数の 1 行を標準エラーにも出す。0 件でも終了コードは 0 で、
使い方の誤りだけが 1。値が `-` で始まるときは `--text=-A` のように `=` で繋ぐ。

絞り込みのフラグ（`--type` から `--no-refresh` まで）を `--docs` の外で渡すと、その旨を出して 1 で終わる（このフラグが無かったころに
argparse が止めていた打ち間違いを、知らせずに通さないため）。`--docs` にほかの経路のフラグ（`--lint` `--yes` `--preview` `--result`
`--tickets` `--flow` など）と、判定やチケットの経路の設定・sh が渡すフラグ（`--cwd` `--mode` `--approved` `--rules` `--phases`
`--risk` `--projects` `--project-home` `--ticket-control` `--guard-core-files` `--guard-ticket-approval` `--restore-if-deny`
`--integration-branch` `--record-writes` `--choose-out` `--actor` `--via` など）を付けたときも、無視せずに 1 で終わる。
使われるのは引く場所の `--root` と、記録の置き場の `--log` / `--state`（`--docs` は何も記録しないので結果は変わらない）だけ。

`index.jsonl` と `--format jsonl` の 1 行、`--format json` の配列の要素は同じ形。

```json
{"concept_id":"docs/claude/worktree","directory":"docs/claude","frontmatter":{"type":"guide","tags":["worktree","git"]},"mtime":"2026-09-27T19:56:23"}
```

| 鍵 | 何 |
|---|---|
| `concept_id` | 引いた結果ではワークスペースルートからの相対パス（`/` 区切り）から `.md` を除いたもの。`index.jsonl` に書く行は、そのリポジトリのルートから（プロジェクトの頭の `projects/<名前>/` が無い） |
| `directory` | そのファイルがあるディレクトリ（`concept_id` と同じ基準。リポジトリのルート直下は `.`、プロジェクトのルート直下は `projects/<名前>`） |
| `frontmatter` | 頭の `---` から `---`（か `...`）までを YAML（SafeLoader、別名は拒む）で読んだもの。無い・読めない・マッピングでない・JSON に書けない（桁の多すぎる整数など）なら `null`。日付などの JSON に無い値は文字列 |
| `mtime` | ファイルの更新日時。ローカル時刻の `YYYY-MM-DDTHH:MM:SS` |

**古い `index.jsonl` が残る条件。** md が全部消えたディレクトリの `index.jsonl` を消すのは、そこに追跡されている md が
あった（`git ls-files --cached` に出るが実体が無い）ときだけで、しかも ccnavi の形で、git に無視されているものに限る。
追跡されていない md だけを持っていたディレクトリ、ディレクトリごと消えたもの、`.gitignore` に入ったディレクトリ、索引の対象外に
なったツリーの `index.jsonl` は残る。残ったものは読まない（引くのは、いま md を持つディレクトリの分だけ）ので結果は変わらない。
`mtime` は秒で比べるので、同じ秒の中で 2 度書き換えた md は次に `mtime` が変わるまで古い行のままになる。

## 生の git は止めてラッパースクリプトへ寄せる

`.ccnavi/scripts/ccnavi-git.sh` は安全な git だけを通し、出力を抑えて結果だけを返す。生の `git` はルールで拒否し、拒否の文面からここへ誘導する。

```sh
sh .ccnavi/scripts/ccnavi-git.sh status
sh .ccnavi/scripts/ccnavi-git.sh log -p
sh .ccnavi/scripts/ccnavi-git.sh --help    # 通す形と通さない形の一覧
```

- **出力がコンテキストに丸ごと載るのを止める。** 全量は `logs/` に残し、標準出力へは要約 1 行と先頭 40 行だけを返す
- **オプションの抜け道を、入口を 1 本にして防ぐ。** `permissions.allow` の `Bash(git diff:*)` は前方一致でしかないので、サブコマンドごとに使ってよい形をここで決める

```
$ sh .ccnavi/scripts/ccnavi-git.sh log -p
ok  git log  56 コミット  log=logs/git-20260907-061907-23235.log
commit 845d832e329aa533ee8e0acf3ee61ea1990c47ca
...
... 残り 20893 行は logs/git-20260907-061907-23235.log にある
```

終了コードは 0 が成功、1 は git が失敗、2 は引数か環境の誤り（拒否を含む）。失敗したときは末尾 30 行を返す。

### 何を通し、何を止めるか

- 通す形の一覧に無いものは拒否する。`git branch -D`、`git worktree remove --force`、`git tag -d`、`git checkout -- <パス>` のように
  取り返しがつかない形も、サブコマンドの中で止める
- `push` は**今いるブランチを、そのままの名前で送る形だけ**通す。`--force`・`--force-with-lease`・`--delete`・`--all`・`--mirror`・`--tags`、
  別の名前へ送る refspec（`HEAD:main` など）は通さない。`main` `master` `develop` `release` `release/*` と、そのリポジトリの統合先
  （`CCNAVI_INTEGRATION_BRANCH`、無ければ `ccnavi-sync.sh` の取り込み結果、無ければ `origin/HEAD`・`origin/main`・`origin/master`。
  `develop-v1.0.0` のような名前でもよい）へ直接は送れない。統合先が決まらなければ固定のリストだけで判定する。マージはユーザの側に残す
- `fetch`・`pull` は**リモート名とブランチ名だけ**を通す。`:` か `+` を含む引数（refspec と URL）は通さない。
  `branch` の `-M`（強制の改名）と `-C`（強制の複製）も通さない。親のブランチを別のコミットへ付け替えると、親のブランチ上の承認済みチケットが差し替わるため
- 承認済みチケットの置き場（`.ccnavi/approved/`）とレビュー待ち（`wip/proposals/review/`）に当たるパスには、
  `checkout <ref> <パス>`・`restore --source <ref>`・`restore --ours / --theirs` を通さない。置き場を過去の中身に戻したり、
  衝突を片側に寄せたりすると、承認が無かったことにも戻ったことにもなる
- 親のワークツリーでは、親のブランチ（承認済みの親チケットの `branch:`、無ければ識別子）のほかへ `checkout`・`switch` で移れない。
  識別子のブランチの上から承認済みの `branch:` のブランチへ移るのは、承認済みチケットを取り込む 1 操作になる
  `worktree add` は行き先の名前とブランチ名を揃える形だけで、違う名前のブランチを出すのは行き先の名前の親チケットが
  承認済みの `branch:` でそう名乗っているときだけ
- 送るのは親だけ。子チケットのワークツリーからの push はラッパースクリプトが拒み、サブエージェントからの push は hook が拒む（`DENY_SUBAGENT_TICKET_OP`）
- サブコマンドより前のオプション（`git -c ...` など）は 1 つも受け取らない。`GIT_CONFIG_COUNT` と `GIT_EXTERNAL_DIFF` は実行前に消す

これは事故と浪費を減らすためのもので、敵対的な回避への防御ではない。実行役のコマンドの一覧に無いもの（`python -c`・`ssh` など）に埋め込めば
hook の文字列一致は当たらない。そこまで防ぐなら `permissions.deny` か sandbox が要る。

記録は `logs/git-<日時>-<pid>.log` に成功でも失敗でも全量を書き、新しい順に 50 本だけ残す。`logs/` は `.gitignore` に入れ、コミットしない。

## 構成

| 場所 | 中身 |
|---|---|
| `main.py` | 配布物の入口。PyInstaller が渡すスクリプト |
| `src/ccnavi/__main__.py` | `python -m ccnavi` の入口。サブパッケージは役割ごとに 6 つで、読む向きは infra < records < policy < tickets < hook < entry（`tests/core/test_module_layers.py` が見る） |
| `src/ccnavi/infra/` | 土台。ファイル・git・パス照合・hook の入出力・設定・シェルの読み・ワークツリー。どのサブパッケージも読まない |
| `src/ccnavi/infra/hookio.py` | stdin の payload の解釈と、stdout に返す応答の組み立て |
| `src/ccnavi/infra/globmatch.py` | glob から正規表現への翻訳 |
| `src/ccnavi/infra/shellread.py` | コマンド文字列のうち実際に実行される部分の切り出し |
| `src/ccnavi/infra/shellread_scan.py` | 原文の走査。引用の状態を持ったまま、置換・ヒアドキュメント・コメント・改行を片付ける |
| `src/ccnavi/infra/shellread_words.py` | コマンドの語の見分け。コマンド名・実行役のコマンド・オプションの幅・リダイレクト |
| `src/ccnavi/infra/shellread_cd.py` | `cd` で移った先の追跡 |
| `src/ccnavi/infra/settings.py` | 環境と設定ファイルからの設定解決 |
| `src/ccnavi/infra/gitstate.py` | 作業ツリーで実際に何が変わったかを git から読む |
| `src/ccnavi/infra/tree.py` | ワークツリー（git worktree）の特定。判定の鍵はファイルの行き先 |
| `src/ccnavi/infra/modes.py` | enable / dry-run / disable の 3 値と終了コード。モードの解決 |
| `src/ccnavi/infra/gitcmd.py` | git を 1 回起動する |
| `src/ccnavi/infra/fsio.py` | ファイルの読み書きの型。state の記録・マーカー・承認済みチケット・下書きが全部これを通る |
| `src/ccnavi/infra/yamlread.py` | YAML を safe な読み手で読む。libyaml があれば C で読み、結果が分かれうる文書と深い入れ子は純 Python に回す |
| `src/ccnavi/infra/platformtag.py` | 機械の語（`<os>-<arch>`）。組み立ての目印と、振り分けの sh が起動する実体の探し方 |
| `src/ccnavi/records/` | 記録。伏せ字・判定の記録・診断ログ・後始末・拒否の数え |
| `src/ccnavi/records/audit.py` | 1 行 1 件の追記記録 |
| `src/ccnavi/policy/` | ルール。読み込み・照合・組み込み・レイヤーの合成・自己防衛・文脈ファイル |
| `src/ccnavi/policy/rules.py` | ルールファイルの読み込みと検証 |
| `src/ccnavi/policy/builtin.py` | ルールファイルを読めないときの組み込み既定 |
| `src/ccnavi/policy/ruleload.py` | この呼び出しに当てるルール集合を決める（ワークスペース・プロジェクト・その和） |
| `src/ccnavi/policy/ctxfile.py` | 当たったルールがモデルへ渡す文（additionalContext）。ファイルの本文と once の記録 |
| `src/ccnavi/policy/selfguard.py` | ccnavi 自身の設定ファイルと実行ファイルのバックアップと復元 |
| `src/ccnavi/policy/selfguard_targets.py` | 守る対象の一覧。設定ファイル・層の 3 本・実行ファイルと、ワークツリー側の写し |
| `src/ccnavi/policy/selfguard_shell.py` | 守る対象へのシェルからの書き込みを止める組み込みのルール（正規表現と `add_rules`） |
| `src/ccnavi/tickets/` | チケット。承認済みチケットの置き場（approval）と合意の手続き（agree）、フェーズ、リスク、操作 |
| `src/ccnavi/tickets/ticket.py` | チケットの読み込みと、そこが宣言する作業範囲。親子の部分集合の検査 |
| `src/ccnavi/tickets/ticket_model.py` | チケットの形。書式の定数・状態の名前・範囲の項・計画の項・待ち方と `Ticket` |
| `src/ccnavi/tickets/ticket_ids.py` | 識別子とブランチ名の規則。issue から識別子を作る手順 |
| `src/ccnavi/tickets/ticket_places.py` | 範囲を当てない置き場（チケット・下書き・ELI5）と、状態の置き場の出入りの見分け |
| `src/ccnavi/tickets/ticket_fold.py` | 同じ識別子のチケットのまとめ方。本物とするツリーと、決まらない形の数え方 |
| `src/ccnavi/tickets/ticket_guard.py` | 状態の置き場を守る組み込みのルールと、提案を書いた回に渡す確認の文 |
| `src/ccnavi/tickets/ticket_fields.py` | スクリプトが書く欄の、行単位の書き換えと読み取り |
| `src/ccnavi/tickets/approval.py` | 承認済みチケットの置き場。読み込み・走査・提案の集め方・置き場の決め方 |
| `src/ccnavi/tickets/approval_ops.py` | 承認済みチケットを動かす・書く操作。承認、欄の書き換え、閉じる、レビューへ送る、続きの子 |
| `src/ccnavi/tickets/approval_marks.py` | フェーズのマーカー、親ごとのマーカー、子ごとの記録、受け入れたスレッドの記録（`phases/<親>/`） |
| `src/ccnavi/tickets/approval_checks.py` | 承認済みチケットの構造の検査。親子と統合先、先行、プロジェクトの欄 |
| `src/ccnavi/tickets/approval_times.py` | 承認の時刻（表示だけ）。状態の履歴か git から引く |
| `src/ccnavi/tickets/agree.py` | 合意（承認）の手続き。承認の対象を集めて `--preview` / `--verify` に答え、書き込みを並べて置き場へ動かす。承認待ちの一覧 |
| `src/ccnavi/tickets/agree_candidates.py` | 承認の候補と、その検査（計画・改版・欄・ブランチ） |
| `src/ccnavi/tickets/agree_screen.py` | 承認の画面の文面と、承認を伝える文 |
| `src/ccnavi/tickets/agree_digest.py` | 承認の対象の指紋（ダイジェストと読みの範囲）と、承認で書く本文 |
| `src/ccnavi/tickets/risk.py` | 実績で測るリスク。`risks.yml` の読み込み、差分の計測、スクリプトと定性項目 |
| `src/ccnavi/tickets/phase.py` | フェーズの終わりと HITL ポイント。フェーズの組み立てと順序の検査、止めたときの文、親がいまどの局面にいるか |
| `src/ccnavi/tickets/phase_forms.py` | ユーザだけが打つコマンドの形（承認・レビュー済み・ガードの切り替え・記録の片付け）を見分ける組み込みのルール |
| `src/ccnavi/tickets/phase_scope.py` | 子チケットの範囲の当て方と、範囲の外の変更の洗い出し |
| `src/ccnavi/tickets/phasetypes.py` | フェーズ定義（`phases.yml`）の読み込みと検証 |
| `src/ccnavi/tickets/flow.py` | 子チケットのフロー（作業の手順のグラフ）。置き場・着手中のロック・読み込み・子に渡す案内 |
| `src/ccnavi/tickets/flow_text.py` | フローに書かれた文字列の整え方。制御文字・長さ・印や囲みのなりすまし |
| `src/ccnavi/tickets/flow_shape.py` | フローの形の検査。ノード・枝・名前の食い違い |
| `src/ccnavi/tickets/flow_render.py` | フローを文に描く。子に渡す手順の一覧 |
| `src/ccnavi/tickets/review.py` | レビューの依頼と確認。作業ツリーの中の前提検査と、sh が渡した結果の判定（JSON の形は `review_host.py`）。ネットワークには出ない |
| `src/ccnavi/tickets/review_host.py` | sh が渡す `--result` の JSON の形、投稿の目印、origin の種類（sh との契約） |
| `src/ccnavi/tickets/review_decide.py` | 残った指摘の行き先を決める（`--reviewed` の決め方と `decide`） |
| `src/ccnavi/tickets/review_close.py` | 親を閉じる（`review ready` と `close-early`） |
| `src/ccnavi/tickets/ops.py` | チケットの状態を動かす `ticket start / finish / cancel / record-risk`。閉じるときに実績のリスクを数える |
| `src/ccnavi/tickets/ops_close.py` | チケットを引いて動かしてよいかの検査。置き場の引き当て、取り込み済みの親子の停止、着手の前（親・先行）と終了の前（親を閉じられる・成果物）の検査 |
| `src/ccnavi/tickets/ops_stop.py` | Stop で `finish` の打ち忘れを促す判定と、基準点の確認（git の読み取り） |
| `src/ccnavi/hook/` | hook の判定。実行前チェック・文面・実行後チェック・イベント・サブエージェント |
| `src/ccnavi/hook/post.py` | 実行後チェックの手順。作業ツリーの読み取り、前からあった変更の記録、復元 |
| `src/ccnavi/hook/post_findings.py` | 変わったファイルを、守る場所とチケットの範囲に当てる。スクリプト自身の書き込みの見分け |
| `src/ccnavi/hook/post_report.py` | 実行後チェックの差し戻しの文 |
| `src/ccnavi/hook/events.py` | hook のイベントごとの手順。1 回の起動で何が起きるかはここを上から読む |
| `src/ccnavi/hook/judge.py` | 実行前チェック。通す・聞く・止めるを決める |
| `src/ccnavi/hook/reasons.py` | 判定に添える文面と理由コード |
| `src/ccnavi/hook/subagent.py` | SubagentStart / SubagentStop。開いている子の案内と、範囲外の変更の差し戻し |
| `src/ccnavi/hook/docsearch.py` | `--docs` と `SessionStart` の入口。索引を置く場所（ワークスペースとプロジェクト）を決めて集め、引き方を案内する |
| `src/ccnavi/hook/docsearch_index.py` | md の frontmatter の索引（`index.jsonl`）を組む。変わった md だけを読み直す |
| `src/ccnavi/hook/docsearch_query.py` | 索引の引き方。`--docs` の問いの検査、当たり、並べ方と表 |
| `src/ccnavi/entry/` | 入口。CLI・診断・lint・提案・版。どのサブパッケージからも読まれない |
| `src/ccnavi/entry/lint.py` | 設定とルールの検証。判定を行わない |
| `src/ccnavi/entry/lint_rules.py` | lint のうち、ルールファイルの中身の検査。提案（`suggest.py`）も候補をここに通す |
| `src/ccnavi/entry/lint_project.py` | lint のうち、`.claude/settings.json`・`settings.local.json` の hook と env の検査 |
| `src/ccnavi/entry/lint_places.py` | lint のうち、下書き・プロジェクト・チケットの置き場の検査 |
| `src/ccnavi/entry/lint_layers.py` | lint のうち、設定のレイヤーと、取り込んだレイヤーの食い違いの検査 |
| `src/ccnavi/entry/lint_ticket.py` | lint のうち、承認済みチケット・承認・提案と、親子の運用に要る hook の検査 |
| `src/ccnavi/entry/lint_branch.py` | lint のうち、チケットのブランチ名・連番・既存ブランチ・ワークツリーの検査 |
| `src/ccnavi/entry/diagnose.py` | 判定を実行せずに試す `--test` と `--explain`。名前を引き受けるだけで、中身は下の 4 つに分けてある |
| `src/ccnavi/entry/diagnose_try.py` | diagnose のうち、`--test` と `--test-samples`。判定と同じ経路で 1 件と見本を試す |
| `src/ccnavi/entry/diagnose_explain.py` | diagnose のうち、`--explain` の本文 |
| `src/ccnavi/entry/diagnose_board.py` | diagnose のうち、ボードの中身（`--explain --json`） |
| `src/ccnavi/entry/diagnose_shared.py` | diagnose のうち、explain・board・try が共有するレイヤーの読み出しとルールの書き方 |
| `src/ccnavi/entry/cli.py` | 1 回の起動の入口。標準入出力とコマンドラインを判定や各コマンドへ振り分け、ワークスペースルートを見つける |
| `src/ccnavi/entry/cli_usage.py` | `--help` の本文 |
| `src/ccnavi/entry/cli_args.py` | 引数の読み分け。設定を上書きする旗・診断だけの旗・`--docs` と並べられない旗と、パスの見分け |
| `src/ccnavi/entry/cli_ops.py` | チケットとレビューの副命令を ops / review へ渡す。`sync` の問い合わせ |
| `build.py` | 配布物の組み立て。`dist/ccnavi/` を `.ccnavi/bin/<os>-<arch>/` へコピーする |
| `scripts/ccnavi-setup.sh` | 対象プロジェクトに設定を書き、実行ファイルとルールとスクリプトを配る |
| `.claude/hooks/lint-py.sh` / `test-py.sh` | このリポジトリ自身の開発用 hook。整形と検査、ターンの終わりのテスト |
| `.claude/hooks/mark-ext.sh` / `test-ext.sh` | 同じく拡張のぶん。触ったことの書き残しと、ターンの終わりに関わるグループだけ回すテスト |
| `extensions/vscode/ccnavi-board/scripts/test-groups.js` | 拡張のテストの入口。触ったファイルから回すグループを決め、コンパイルは 1 回で済ませる |
| `.claude/skills/ccnavi-config/` / `commit/` | 設定 3 本を足す・確かめるスキルと、コミットの手順 |
| `.ccnavi/scripts/ccnavi-launcher.sh` | hook が起動する振り分けの sh（モード 100755）。原本と配布先で同じパス。1 つ上の `bin/<os>-<arch>/` から、この機械の実行ファイルを選ぶ。無ければ 127 |
| `.ccnavi/scripts/ccnavi-git.sh` | 安全な git だけを通し、出力を抑えて結果だけ返すラッパースクリプト |
| `.ccnavi/scripts/ccnavi-ticket.sh` | チケットの状態を動かす。親だけが呼ぶ。状態を聞く `status` は読むだけで、サブエージェントも呼べる。本体は `ccnavi ticket`。取り込み済みの親子のチケットでは C1（取り込んでから書き、書いたパスだけをコミットして push するまで完了にしない） |
| `.ccnavi/scripts/ccnavi-review.sh` | レビューの依頼と確認。親だけが呼ぶ。本体は `ccnavi review`。状態を書く副命令は取り込み済みの親子のチケットで C1。ユーザの判断の入口 `chat <N>`・`close-early` はユーザが打ち、取り込み済みの親子のチケットなら最後に承認の push をする |
| `.ccnavi/scripts/ccnavi-agree.sh` | 承認し、`ccnavi-push-approved.sh` でコミットして push する。ユーザが端末で打つ。本体は `ccnavi --agree` |
| `.ccnavi/scripts/ccnavi-push-approved.sh` | 承認済みチケットの置き場だけをコミットし、保護されたブランチでなければ親のブランチへ push する。ユーザが打つ（エージェントからは止まる）。端末の `ccnavi-agree.sh`・ボードの承認とフローの保存・ユーザの判断の入口のあとに呼ばれる。`[<親>...]` で親子のチケットを限る。取り込み済みの親子のチケットはロックを取り、取り込んでから送る |
| `.ccnavi/scripts/ccnavi-fetch.sh` | セッション開始時に親ブランチと、ワークツリーの起点になるデフォルトブランチを取ってくる。進めるのは fast-forward だけ |
| `.ccnavi/scripts/ccnavi-sync.sh` | 親のブランチを取り込む（早送りか merge。衝突したら取りやめてユーザの対応に切り替える）。リモートから消えた親のブランチを閉じた・消えたに分け、親子のチケットの取り込み状態と統合先の取り込み結果を書く |
| `.ccnavi/scripts/ccnavi-branches.sh` | issue・MR に紐づくブランチを探す。読むだけ。ホストは sh が読み、手元の候補は `ccnavi branches` が集める |
| `.ccnavi/scripts/ccnavi-start.sh` | issue・MR を指定された依頼の着手の入口。`ccnavi-branches.sh` で候補を探し、無ければ Draft MR・ワークツリー・ブランチを作る |
| `.ccnavi/scripts/ccnavi-clean.sh` / `ccnavi-clean.js` | ワークツリー 1 本の生成物（node_modules・.venv など）を消す。`worktree remove` の前に打つ。node が無ければ sh で同じものを消す。配らない |
| `tests/` | 受入テスト。内部の関数は呼ばず、標準入出力と終了コードだけを見る |
| `tools/gitlab/` | 実物または代役の GitLab に sh と実行ファイルを当てて 1 周する、ユーザが手で回す道具。自動テストは呼ばない |
| `tests/fixtures/` | テスト用のルール（`rules.yml`、言及の無い呼び出しを見る `rules-undeclared.yml`） |
| `.ccnavi/common/rules.yml` / `risks.yml` | このリポジトリ自身の共通レイヤーの設定（共通レイヤーに `phases.yml` は置かない） |
| `.ccnavi/config/phases.yml` | このリポジトリ自身のレイヤーのフェーズ定義 |
| `.ccnavi/common/rule-samples.yml` | ルールが何を止めて何を通すかの見本 |
| `tools/check_rules.py` | 見本をぜんぶ判定に掛ける |
| `extensions/vscode/ccnavi-board/` | VS Code 拡張。ボード・ルール管理・リスク管理・プロジェクト管理の画面 |
| `docs/adr/` | 設計判断の記録 |

## 配布物の条件

- 実行ファイル 1 つとその同梱物 1 フォルダ。使う側にランタイムの導入を求めない
- 実行前チェックの間に外部プロセスを起動しない（期限に達した hook は呼び出しをそのまま通すので、遅いとガードが働かない）
- 実行後チェックは git を 1 回起動する
- ネットワークへは出ない。起動する外部プロセスはローカルの git と、子を閉じるときにリスクの配点の `script` 項目を走らせる `sh` だけ
- 作業ディレクトリに依らず同じ入力に同じ判定を返す
- 実行時の third-party 依存は PyYAML 1 本。読むのは `safe_load` に限り、`load` はエージェントが書けるファイルに向けては使わない
