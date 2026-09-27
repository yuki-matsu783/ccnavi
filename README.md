# ccnavi

Claude Code のツール呼び出しを hook で止め、止めた理由と代わりに取る手段を返す。

用語は [CONTEXT.md](CONTEXT.md)、要求は [requirements.md](requirements.md)、設計は
[ccnavi.md](ccnavi.md)、判断の理由と経緯は [docs/adr/](docs/adr/README.md) にある。
「承認済みチケット」「マーカー」「ワークツリー」「直接作業」のような呼び名は用語集で定義している。

## 導入

何が打たれ、ルールが何に当たるかは走らせるまで分からない。`dry-run` で立て、記録を読みながら
ルールを寄せ、記録が落ち着いてから `enable` に切り替える。

### 1. dry-run で立てる

実行ファイルを組み立て、7 つのイベントに登録する（「設定」の節。導入スクリプトが書く）。
判定だけを見るなら `PreToolUse` と `PostToolUse` の 2 つでも動く（残りを登録しないと何が落ちるかは「設定」の表）。
`CCNAVI_MODE` は `dry-run` にする。

```json
"env": {
  "CCNAVI_MODE": "dry-run",
  "CCNAVI_LOG": "logs/log.jsonl"
}
```

ルールファイルには、取り返しの付かない操作とガード自身の設定だけを `deny` に書いて始める
（`rm -rf`、`git push`、`git reset --hard`、認証情報の置き場、`.claude/` の下）。組み込みの既定
（「ルールファイルが読めないとき」の節）がちょうどその範囲なので、写して直すのが早い。

`allow` は空のままでよい。`dry-run` は呼び出しに手を出さないので作業は止まらず、穴の位置だけが記録に溜まる。

立てたら、設定そのものを見る。

```sh
ccnavi --lint
```

`dry-run` の間は `warn` が 2 件出続ける（モードが `dry-run`、`allow` が空）。終了コードは 0 のまま。

`env` の変更はセッションを開き直すまで効かない。

### 2. 普段どおり開発する

ccnavi は判定し、呼び出しには手を出さず「`enable` なら何をしていたか」を返す。
その文面はエージェントが読むので、代わりに取る手段が書けているかもここで分かる。

### 3. 記録を読む

通した回も含めて 1 行 1 件で残っている（「記録」の節）。読むのは 4 つ。

```sh
# 判定の内訳
jq -r '.decision' logs/log.jsonl | sort | uniq -c | sort -rn

# 止めた回。誤検知はここに出る
jq -r 'select(.decision=="deny")|[((.rules//[])|join(",")),(.subject|gsub("[ \t\n]+";" "))]|join("\t")' \
  logs/log.jsonl | sort | uniq -c | sort -rn

# どこにも当たらなかった回。enable ではこれが全部、権限モードに渡る
jq -r 'select(.code=="UNDECLARED")|(.subject|gsub("[ \t\n]+";" "))' \
  logs/log.jsonl | sort | uniq -c | sort -rn | head -30

# 通した回を、当たったルール別に。広すぎる allow はここに出る
jq -r 'select(.event=="PreToolUse" and .decision=="allow")|[((.rules//[])|join(",")),(.subject|gsub("[ \t\n]+";" "))]|join("\t")' \
  logs/log.jsonl | sort | uniq -c | sort -rn | head -30
```

`subject` は 1 行に均してから数える（複数行のコマンドを `uniq -c` が行数ぶんに割らないように）。

`decision` が `skip` の行は判定が届かなかった回で、`reason` に理由が入る。
`paths` が付いた行は実行後の監視が拾った変更で、引数に現れない書き込みがそこにある。

### 4. ルールを直す

| 記録に出るもの | 直す先 |
|---|---|
| 止めるつもりのなかったものが `deny` に出ている | ルールの綴りを絞る。語の切れ目が要るなら `regex` へ |
| 同じ呼び出しが `UNDECLARED` で並ぶ | `allow` に足す。1 行足すたびに人が見なくなる範囲が広がる |
| `allow` で通っているが止めたいものがある | `deny` か `ask` に足す。強いタイプが先に当たる |

直したら、そのつど 3 つを回す。

```sh
ccnavi --lint                              # 防御を無効化しうる記述が無いか
ccnavi --test Bash "cd /repo && git push"  # 1 件が何に当たるか
uv run python tools/check_rules.py         # 見本をまとめて回す
```

ルールを 1 件足したら見本も 1 行足す（「見本で確かめる」の節）。止めたくないものも必ず一緒に置く。
`/ccnavi-config` スキルがこの流れをまとめて回す。

### 5. enable へ切り替える

目安は記録の側にある。

- 普段の作業で `UNDECLARED` がほとんど出ない（残ったままだと、作業の大半が判定を受けずに権限モードへ渡る）
- `deny` に出ているものが、全部「意図して止めたもの」になっている
- `--lint` の `error` が 0 件

`CCNAVI_MODE` を `enable` にして、セッションを開き直す。記録は同じファイルに続き、
`deny` と `ask` の `enforced` が `false` から `true` に変わる。

`disable` は `.claude/settings.json` に書いても効かない（「動作モード」の節）。
一時的に外すなら、セッションを起動する側の環境から渡す。

## 開発

Python 3.12 以降。実行時の依存は PyYAML 1 本だけ。
組み立てと検査の道具（PyInstaller、ruff）は開発時にしか要らない。

```sh
uv run python -m unittest discover -s tests -t .   # テスト
uv run --with ruff ruff check .                    # 静的検査
uv run --with ruff ruff format .                   # 整形
uv run --with pyinstaller python build.py          # 実行ファイルの組み立て
```

手で 1 回動かす。

```sh
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git push"}}' \
  | uv run python -m ccnavi --mode enable
```

Python のファイルを編集するたびに `.claude/hooks/lint-py.sh`（`PostToolUse` の
`Write|Edit|NotebookEdit`）が整形と検査をかける。テストはそこでは走らせない（ADR-0036）。

テストは `.claude/hooks/test-py.sh` で、`Stop` に登録してターンの終わりに 1 回だけ回す。
落ちていたら差し戻すが、差し戻しは 3 回まで。上限に達したら止まらせて判断を人へ返す。
このリポジトリの `.claude/settings.json` の `Stop` には ccnavi の実行ファイルしか無いので、
テストを回すなら `Stop` に足す（利用者ごとの `settings.local.json` でよい）。

`lint-py.sh` は、編集したファイルからいちばん近い `pyproject.toml` を上に辿ってツリーを決め、
触ったツリーを `logs/session/<セッション>.trees` に書き残す。`test-py.sh` は触ったツリーだけをテストする。

拡張（`vscode-extension/ccnavi-board`）のテストも同じ形で回す（ADR-0061）。`PostToolUse` の
`.claude/hooks/mark-ext.sh` が、拡張のファイルを触ったら `logs/session/<セッション>.ext-files` に
書き残し、`Stop` の `.claude/hooks/test-ext.sh` がターンの終わりに 1 回回す。回すのは触ったファイルが
関わるグループだけで、決めるのは `vscode-extension/ccnavi-board/scripts/test-groups.js`
（テストの `import` を辿る。辿れない 6 つだけが綴りの表）。拡張を触っていないターンは何もしない。
差し戻しは 3 回までで、回数は `test-py.sh` と別に数える。

この 2 本で気づけるのは「同じ機械で 1 回回して落ちること」だけで、混み具合で結果が変わる失敗（React の描き直しを
待つテストなど）には効かない（CI も繰り返しの仕掛けも無い）。Bash で変えたもの（`sed -i`、`merge` で入った変更）には
印が付かないので回らない。

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
`build.py` は `dist/ccnavi/` に組み立てたあと、それを `.ccnavi/bin/<os>-<arch>/` へ写す（隣の `<os>-<arch>.new` に
写し切ってから入れ替える）。`.ccnavi/bin/` は `.gitignore` に入っている。写す段で落ちたら次の行を出して
終了コード 1 で終わる。原因（ディスク、権限、Windows で走っている実行ファイルのロック）を直して回し直す。

```
dist/ は新しい。.ccnavi/bin/<target>/ は前のまま
```

ワークツリーで組み立てると、写す先はそのワークツリーの `.ccnavi/bin/` になり、走っている hook は変わらない。
ワークスペースルートで組み立て直すと、セッション開始で取った実行ファイルの控えと食い違う（「コアファイルを守る」）。
`CCNAVI_GUARD_CORE_FILES` が `enable` なら次のツール呼び出しのあとで控えの版に戻される（`dry-run` なら報告だけ）。
ワークスペースルートで組み立てたら、セッションを開き直す。

`dist/` を直に起動したいときは、`.claude/settings.local.json`（追跡しない）の `env` で `CCNAVI_BIN_PATH` を
`dist/ccnavi/ccnavi` にする。自己防衛も同じ env を読むので、守る実体と起動する実体は食い違わない。代償は、その機械では
振り分けの sh の不具合に気づけないこと。`ccnavi.settings.local.json` のキー `bin` でも
`CCNAVI_BIN_PATH` を上書きできるが、変わるのは自己防衛が何を守るかだけで、hook が何を起動するかは変わらない。

### 配布物

PyInstaller の onedir で組み立てる。Windows なら `dist/ccnavi/ccnavi.exe`、
Linux なら `dist/ccnavi/ccnavi`。onefile は起動のたびにランタイムを展開して遅いので使わない。

| 形式 | 1 呼び出しあたり | 配布物 |
|---|---|---|
| onedir | 約 220 ms | 17 MB のフォルダ |
| onefile | 1000〜1500 ms | 7 MB の 1 ファイル |

## 設定

設定は環境変数で、`.claude/settings.json` の `env` ブロックに書く（Claude Code の設定スキーマは
独自のキーを受け付けない）。hook には実行ファイルだけを登録すればよい。

下の形は `sh scripts/ccnavi-setup.sh <ワークスペースルート>` が書く。何度打っても同じ形に
落ち着き、既にある値と、ccnavi と関係のない hook はそのまま残る。書かずに揃っていない
ところだけを見たいときは `--check`、既定を持つつまみも並べたいときは `--all` を付ける。
同じ 1 回で `.vscode/settings.json` も見る（次の節）。触ってほしくないときは `--no-vscode`。
セッションの頭の取り込み（`ccnavi-fetch.sh`）も `SessionStart` に別の 1 行で登録する。
1 台だけで使いリモートに合わせる必要が無ければ `--no-fetch` で外す。

既にある値は置き換えず、違えば並べて見せる。置き換えるのは `--mode` か `--ticket-control` を
名指しして `--force` を付けたときだけで、名指ししていない値はそのままにする。

戻す働きの 2 つ（`CCNAVI_RESTORE_IF_DENY` / `CCNAVI_GUARD_CORE_FILES`）は `CCNAVI_MODE` と
同じ値で書く。`CCNAVI_MODE` が `dry-run` のうちは、この 2 つも `dry-run` になる（「動作モード」の節）。
`enable` へ切り替える前に、記録の `would-restore` で何が戻るはずだったかを確かめられる。
`dry-run` で入れたままだとこの 2 つも `dry-run` のまま残るので、`--mode enable` で打ち直すか 3 行を書き換える。

`CCNAVI_GUARD_TICKET_APPROVAL` はモードに合わせず、いつも `enable` で書く。この門は
`enable` か `disable` しか取らない（承認は通れば済んでしまうので、報告だけの段を持てない）。
`dry-run` と書かれていたら `--lint` が error にする（今は `enable` として動いている、と添えて）。

hook は、そのイベントに ccnavi が登録されていなければ足す。別の綴りで登録されているように
見えるイベントは、足さずに名前を挙げる（黙って足すと判定が 2 回走る）。

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
    "CCNAVI_LOG": "logs/log.jsonl",
    "CCNAVI_BIN_PATH": ".ccnavi/scripts/ccnavi-launcher.sh",
    "CCNAVI_RESTORE_IF_DENY": "dry-run",
    "CCNAVI_GUARD_CORE_FILES": "dry-run",
    "CCNAVI_GUARD_TICKET_APPROVAL": "enable"
  }
}
```

同じ実行ファイルを上の 7 つのイベントに登録する。payload がイベント名を名乗るので、どれを走らせるかは ccnavi が選ぶ。
`matcher` は絞らない。絞ると、書かなかったツールで hook 自体が起動しなくなる。
`SessionStart` の 2 本目は取り込みの sh（`ccnavi-fetch.sh`）で、承認済みチケットと
マーカー（親ブランチに乗って届く）と、ワークツリーの起点になるデフォルトブランチを fast-forward で
取ってくる（ADR-0060）。登録しないと、別の機械で承認したものが効かず、古い `main` から枝を切る。

| イベント | ここで何をするか | 登録しないと |
|---|---|---|
| `SessionStart` | 実行ファイルの控えを取る。チケット制御が効いていれば、直接作業とチケット作業の使い分けをモデルに渡す | 実行ファイルが差し替えられても戻せない。モデルがチケットをいつ起こすかを知らないまま進む |
| `UserPromptSubmit` | 保護領域の状態とツリーごとの HEAD を控え、ターンの基準にする | ターンの終わりの報告が出ない（コミットに入った変更も見えない） |
| `PreToolUse` | 呼び出しを判定し、設定ファイルを控える | 判定そのものが働かない |
| `PostToolUse` | 作業ツリーを見て、変わっていれば戻す。チケットの状態を承認済みチケットへ写す | 引数に現れない書き込みを取りこぼす。フェーズの終わりが伝わらない |
| `Stop` | このターンで変わった保護領域を利用者へ報告する。cwd のワークツリーのチケットを `finish` し忘れていそうなら 1 回だけ止めて促す | 変更が人の目に触れない。閉じ忘れたチケットが作業中に残る |
| `SubagentStart` | 承認済みで開いている子チケットの一覧を渡す | サブエージェントが自分の範囲を知らずに始める |
| `SubagentStop` | 子のワークツリーに範囲外の変更が残っていれば 1 回だけ差し戻す | コミット済みの範囲外が親に届く |

`command` を `${CCNAVI_BIN_PATH}` で書くのは、守る対象と起動する実体を 1 か所に寄せるため
（shell form なので環境変数は shell が展開する）。代わりに、`env` からこの 1 行が消えると hook が起動しなくなる。

| 変数 | 意味 |
|---|---|
| `CCNAVI_MODE` | `enable`（既定）、`dry-run`、`disable` |
| `CCNAVI_LOG` | 記録先。既定は `logs/log.jsonl`。空文字にすると記録しない |
| `CCNAVI_STATE` | 実行後の監視の控えの置き場。既定は `logs/state`。空文字にすると控えを持たない |
| `CCNAVI_RESTORE_IF_DENY` | `enable`（既定）、`dry-run`、`disable`。`deny` と宣言した場所が副作用で変わったとき、git から戻すか。`dry-run` は戻さずに「戻すはずだった」と言う |
| `CCNAVI_GUARD_CORE_FILES` | `enable`（既定）、`dry-run`、`disable`。ccnavi が動くために要るファイルを守るか。書き込みを止める側と、控えて戻す側の両方が切り替わる |
| `CCNAVI_GUARD_UNWATCHED` | `enable`（既定）、`disable`。人にも classifier にも確認できないモード（`dontAsk` / `bypassPermissions`）で、ルールがどこも言及しない呼び出しを止めるか。`disable` なら判定を返さず、そのモードの取り決めに委ねる（読み切れなかった呼び出しは委ねない。「ルールが言及していない呼び出し」）。`dry-run` は無く、それ以外の値は `enable` として動いて `--lint` が言う |
| `CCNAVI_BIN_PATH` | hook が起動する ccnavi 自身。指定すると守る対象に入る。既定は無い（導入スクリプトは振り分けの sh `.ccnavi/scripts/ccnavi-launcher.sh` と書き、実行ファイルは `.ccnavi/bin/<os>-<arch>/` に入る。「実行ファイルとルールを配る」）。拡張子は書かない。Windows の `.exe` は ccnavi が補う |
| `CCNAVI_TICKETS_PROPOSAL` | チケットの提案の置き場。各ツリーのルートからの相対。既定は `wip/proposals`。そのツリーの git が追跡する。VS Code 拡張は提案の変化を既定の綴りでしか見ないので、既定から動かすと提案の増減でボードが自動更新されず、手で「更新」を押す（承認は反映される） |
| `CCNAVI_TICKETS_APPROVED` | 承認済みチケットとフェーズのマーカーの置き場。各ツリーのルートからの相対。既定は `.ccnavi/approved`（ccnavi ディレクトリの下）。そのツリーの git が追跡し、親チケットのブランチに乗って他の機械へ届く。空文字は受けず、既定の置き場に戻る（切るのは `CCNAVI_TICKET_CONTROL`。空なら `--lint` が言う） |
| `CCNAVI_TICKET_CONTROL` | `enable`（既定）、`disable`。チケット制御（提案の承認・承認済みチケットの範囲・フェーズの HITL ポイント・サブエージェントの制限）を使うか。全体ルールは全プロジェクトが使い、チケットまで使うかをここで決める。`disable` なら `--approve` と `ticket` / `review` の副命令は動かず、セッション開始の案内も出ず、VS Code 拡張の「チケット管理」も出ない。それ以外の値は `enable` として動き、`--lint` が error にする |
| `CCNAVI_PROJECTS` | プロジェクトの置き場（設計 11）。ワークスペースルート（Claude Code を開いた場所）からの相対。既定は `projects`。直下で `.git` を持つディレクトリがプロジェクトになる。空文字にすると数えず、共通層とワークスペース自身の層だけで判定する |
| `CCNAVI_PROJECT_HOME` | ccnavi ディレクトリ（「ルールは 3 層の和で当たる」）。各 git プロジェクトルート（`.git` のある場所）からの相対。既定は `.ccnavi`。その下の `config/{rules,phases,risks}.yml` が 1 つの層の 3 本になり、`scripts/` が配点の `script:` の置き場になる。動かせるのは ccnavi ディレクトリの名前だけで、`config/` と `scripts/` と 3 本のファイル名は固定。共通層の置き場（`.ccnavi/common/`）は ccnavi ディレクトリの名前をどう変えても動かない（この env でも `--project-home` でも）。別の場所を指せるのは `--rules` / `--phases` / `--risk` のフラグだけで、それも診断（`--lint` / `--test` / `--test-samples` / `--explain`）に限る（ADR-0067）。`--project-home` も同じ。hook からの判定と `ticket` / `review` の副命令に渡すと落とし、標準エラーに出す |
| `CCNAVI_GUARD_TICKET_APPROVAL` | `enable`（既定）、`disable`。チケットの承認の経路を守るか。enable なら、シェルから ccnavi の実行ファイルを `--approve` / `--reviewed` / `--close-early` / `ticket …` / `review …` 付きで打つ形を止め（`DENY_TICKET_APPROVAL_CLI`）、`--approve` と `--reviewed` と `--close-early` は標準入力が端末であることを求める。エージェントのコマンド行にこの変数の名前を（読むだけの形のほかで）書く形と、`--guard-ticket-approval` に `enable` 以外を渡す形も同じ理由コードで止める（表示・検索の道具だけのコマンドは除く。ADR-0080）。テストや端末の無い実行環境（CI など）で切る。`dry-run` は取らず、書かれていたら `enable` として扱い、`--lint` が error にする |
| `GITHUB_TOKEN` / `GITLAB_TOKEN` | レビューの依頼と確認がリモートを読み書きするときの認証。どちらが要るかは origin の URL で決まる |

`${CLAUDE_PROJECT_DIR}` は hook の `command` では展開されるが `env` では展開されない。
`env` には相対パスを書く。値の変更はセッションを開き直すまで効かない。

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
`bin/<os>-<arch>/` から合う実行ファイルを選んで、引数と標準入力をそのまま渡す（ADR-0044）。

```
.ccnavi/scripts/ccnavi-launcher.sh      ← CCNAVI_BIN_PATH が指す振り分けの sh（追跡する）
.ccnavi/scripts/ccnavi-ticket.sh など   ← 代わりに通る sh（同じ置き場）
.ccnavi/bin/darwin-arm64/ccnavi         ← 機械ごとの組み立て（_internal/ も同じ置き場。無視する）
.ccnavi/bin/linux-x86_64/ccnavi
.ccnavi/bin/windows-x86_64/ccnavi.exe
```

置き場は 2 つとも固定で、原本と配布先で同じ綴りになる。`.ccnavi/bin/` には実行ファイルだけが並ぶ。
sh は自分の隣（`.ccnavi/scripts/<os>-<arch>/`）は探さない（自己防衛の綴りが当たらない置き場から起動する道を作らない）。

置き場の名前は、`build.py` が `dist/ccnavi.target` に書く `<os>-<arch>` の目印から取る。別の機械向けの
組み立てでも、その機械の置き場に入るだけなので配る。Windows と WSL で同じフォルダを開くなら、
それぞれの機械で打てば両方が並ぶ。この機械で動くものが無ければ、1 行と最後の「まだ無いもの」で言う。
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
| `.ccnavi/common/rules.yml` | 同じ綴り |
| `.ccnavi/common/risks.yml` | 同じ綴り |
| `.ccnavi/config/phases.yml` | 同じ綴り |
| `.ccnavi/scripts/ccnavi-{ticket,review,git,common,push-approved,approve,fetch,clean}.sh`、`ccnavi-clean.js` | 同じ綴り |
| `.ccnavi/scripts/ccnavi-launcher.sh` | 同じ綴り。配ったあと実行ビットを付ける |

ルールと配点のひな形は共通層（`.ccnavi/common/`）へ、フェーズの種類のひな形はワークスペース自身の層
（`.ccnavi/config/`）へ配る。種類の `scope` はそのワークスペースのレイアウトに合わせて書くもので、
共通層に置くと全プロジェクトに効いてしまう（「ルールは 3 層の和で当たる」）。
3 本とも、無ければ最後の点検が「まだ無いもの」として並べる。振り分けの sh と、この機械で動く
実行ファイル（`.ccnavi/bin/<この機械>/ccnavi`）も同じ一覧に出る。

振り分けの sh は hook が直に起動するので実行ビットが要る。配った回は `chmod +x` を掛け、
配らない回でも実行ビットが落ちていれば付け直す（中身は入れ替えない。Windows（`core.filemode=false`）で
足した sh は、別の機械で clone した直後に実行ビットが無いことがある）。
`--no-deploy` の回と、配布元と配布先が同じ回には触らない。付けられなかったら名指しする。`--check` は実行ビットの
落ちた sh を揃っていないものに数え、`--lint` は error で言う（「設定の検証」）。

配った組み立ての置き場は、配布先が git のリポジトリなら `.gitignore` にも足す（入れると履歴から消すのが難しい）。
置き場は配った機械のぶんだけ足す。

```
# ccnavi が配る実行ファイル（scripts/ccnavi-setup.sh）
/.ccnavi/bin/darwin-arm64/
```

足すのは `/.ccnavi/bin/<os>-<arch>/` だけ。振り分けの sh は追跡し、`.ccnavi/` を丸ごと無視もしない
（sh と `rules.yml` が git から消える）。

配布先に既にあるものは触らず、並べて見せる。入れ替えるのは `--force` を付けたときだけ
（ルールも sh も配布先で直されている前提）。

実行ファイルを入れ替えるときは、配布先に残っていた同梱物を先に消してから配る
（前の版が残ると新しい実行ファイルがそれを掴む）。

名指しした `--deploy` が組み立てられていなければ、終了コード 2 で断る。
既定の配布元が使えないとき（組み立てていない、配布先が ccnavi 自身）は断らず、
配るのを諦めた理由を 1 行出して `.claude/settings.json` は書く。

## ルール

ルールファイルは YAML で、`deny` `ask` `allow` の 3 つのタイプに分かれる。
1 件のルールは、当てるツール・探すもの・見つけたときに返す文を 1 組で持つ。
`message` は `deny` だけに書く（必須）。止められたモデルに「なぜ止めたか、代わりに何を
するか」を伝える文で、`permissionDecisionReason` として届く。`ask` と `allow` に書くと
`--lint` が error にする（`ask` の文面は人の確認ダイアログにしか出ず、`allow` の文面はどこにも出ない）。
モデルに伝えたいことはタイプによらず `additionalContext` に書く。

```yaml
version: 1

deny:
  - id: force-push
    match: Bash
    glob: "*git push*--force*"
    message: リモートの履歴を書き換えます。送り直したい理由を利用者に伝えてください。

ask:
  - id: migrations
    match: Write|Edit
    glob: "*/migrations/*"
    additionalContext: 移行ファイルは実行前に人が中身を見る。何が変わるかを先に言うこと。

allow:
  - id: source
    match: Write|Edit
    glob: "*/src/*"
    additionalContext: src の下は自由に直してよい。ただし公開 API の綴りを変えたら docs/api.md も直すこと。
```

`glob` と `regex` は必ず引用符で囲む。囲まないと YAML が先に解釈する
（`<<` はマージキー、`*` はエイリアス、`&` はアンカー、`!` はタグ）。

### `id` と綴りの決まり

- `id` にコロンは書けない（層の名前を添えた `self:id` / `<プロジェクト名>:id` と見分けが付かないため）。
  `--lint` が error にし、その 1 件は読み込みから落ちる。ルール・フェーズの種類・リスクの配点の 3 本とも同じ
- `glob` も `regex` も、どの機械でも大文字小文字を区別せずに当てる。`*/.ccnavi/*` は `.Ccnavi/` にも当たる。
  `allow` も綴り違いに広がるので、既存のルールを持ち込むときは `--test-samples` で、通したくないものが
  通っていないことを確かめる
- 区別が要る `regex` は、区別したい部分だけを `(?-i:...)` で囲む。`[\\/](?-i:strict)[\\/]` は
  `/strict/` にだけ当たる。否定の文字クラスは要注意で、`\.[^c\\/]` は `.C` まで除外して除外の側が広がる。
  `\.(?-i:[^c\\/])` と囲む
- コマンドの名前も区別しない。`CAT file` は `cat file` と同じ判定になる。区別する機械で `CAT` という別の
  実行ファイルが在れば、`cat` 向けの `allow` がそれにも掛かる（ADR-0051）
- `common` と `self` は層の名札に予約してある。`projects/common/` や `projects/self/` は、綴り違い（`projects/Self/`）を
  含めて層として数えない。そこに置いた宣言は 1 件も効かず、そのプロジェクトへの書き込みは共通層だけで判定される。
  `--lint` が error で名指しし、`--approve` はその `project:` を承認しない

### ルールは 3 層の和で当たる

ルールファイルは 3 種の層に置ける。当たるのは共通層 + そのツリーの層の和で、
足すだけ、上書き無し、厳しいほうが勝つ。設計は [ccnavi.md](ccnavi.md) の 11.4、要求は
[requirements.md](requirements.md) の REQ-MLT。

| 層 | 置き場 | 何を置くか |
|---|---|---|
| 共通層 | `.ccnavi/common/{rules,phases,risks}.yml`（置き場は固定） | どのツリーにも効くもの |
| ワークスペース自身の層 | `<ワークスペースルート>/.ccnavi/config/{rules,phases,risks}.yml` | ワークスペース自身のツリーにだけ効くもの |
| プロジェクトの層 | `projects/<名前>/.ccnavi/config/{rules,phases,risks}.yml` | そのプロジェクトのツリーにだけ効くもの |

変えられるのは ccnavi ディレクトリの名前（`.ccnavi`。`CCNAVI_PROJECT_HOME`）だけ。
自身の層とプロジェクトの層は形が同じで、どちらも git プロジェクトルートの直下に置く。

| ツール | 当たる層 |
|---|---|
| パスを持つツール: `Read` / `Grep` / `Glob` / `Write` / `Edit` / `NotebookEdit` | 共通層 + 行き先の 1 層。行き先がプロジェクトの中ならその層、ワークスペースのツリー（ワークスペースルートと、そこから切ったワークツリー）なら自身の層。`Grep` と `Glob` が探す場所を省いたときは `cwd` が行き先 |
| パスを持たないツール: `Bash` / `PowerShell` / `WebFetch` / `Skill` / `Agent` | 共通層 + 自身の層 + 全プロジェクトの層。どこに `cwd` があっても、`cd` を挟んでも同じ判定になる。ある層の `allow` は他のプロジェクトの作業にも効く |

順は 共通層 → 自身の層 → プロジェクトの層（名前順）で、`deny` `ask` `allow` の順は変わらない。

- 読むのは元リポジトリに checkout されている版だけ。ワークツリーで層を直しても、統合されるまで効かない。
  `--lint` が「ワークツリーにしかないファイル」を warn で言う。承認済みの領域（`.ccnavi/approved/` の承認済みチケット・
  マーカー・子の記録・フロー）は数えない。そこは親のワークツリーの版が読まれる
- 無い層は空。壊れている層も空として扱い、記録に層の名前が残り、`--lint` が error にする。組み込みの既定は
  使わない。共通層のルール自身が読めないときだけ組み込みの既定を使い、そのとき層は足さない
- id には層の名前が付く。共通層は裸の `id`、自身の層は `self:id`、プロジェクトは `<名前>:id`。記録と文面がこの形で出す
- 同じ宣言の重複は 1 本にまとめる。裸の `id` が同じで全欄（`{root}` を置き換えた後）が一致する定義は、後ろの層のものを
  捨て、`--lint` が info で言う
- 同じ `id` で中身が違うルールは両方効き、`--lint` が warn で言う。`deny` と `ask` は増えるほうになる。リスクの配点も両方を数え、後ろの層の項目は
  `<層>:<id>` と名乗る。フェーズの種類は両方効かせられないので、`--lint` が error にしてその層を空として扱う
- 共通層は配る定義で、正本は各プロジェクト。プロジェクト向けの親チケットに着手するとき、共通層にあるファイルごとに
  プロジェクトの `.ccnavi/config/` と比べ、違えば共通層で上書きする（共通層に無いファイルは消さない）。上書きで消える識別子は
  着手の出力に出る。配点が指す共通層のスクリプトも写す。上書きしたことは、その親の最初のレビューの依頼の頭に載る。
  レビューの無い親は、利用者が端末で `ccnavi --config-synced <親>` を打って見るまで閉じられない

できないこと: プロジェクトの層で共通層の `ask` を `allow` に緩める、共通層の `allow` をプロジェクトごとに外す、
他のプロジェクトの層をパスを持つツールの判定に足す（「A のルールを B にも」は共通層へ上げる）。

ccnavi ディレクトリの下（既定なら `.ccnavi/`。共通層の `.ccnavi/common/` も入る）は、ルールに書かなくても書き込みが止まる
（設定 3 本と配点のスクリプトは判定の中身そのものなので。「コアファイルを守る」）。

### 通す・聞く・止めるのどれでも、一言添える

`additionalContext` は、そのルールに当たったときにモデルへ渡す文。`message` が止められた側への言葉なのに対し、
こちらは「通すが、これを踏まえて進めろ」を書く。どのタイプにも書ける。

| タイプ | どう届くか（Claude Code 2.1 で実測） |
|---|---|
| `allow` | 応答の `additionalContext` として届く。判定の理由は無いので、これだけが届く |
| `ask` | 人が Yes を押したときだけ `additionalContext` が届く。No なら拒否の定型文だけが届いてターンが終わり、文は届かない。ダイアログには `[ccnavi] RULE_ASK (rule: …)` と subject が出る |
| `deny` | `permissionDecisionReason`（`message`）と一緒に `additionalContext` として届く |
| `dry-run` のとき | 止める代わりに返す文に続けて届く |

同じタイプに複数当たれば、全部の文を空行で割って並べる。`--test` と `--test --json` の
`response` で、書いた文が何と一緒に届くかが見える。

`additionalContextOnce` は、1 つの文脈で最初に当たったときだけ届く文。文脈はセッション 1 本で、
サブエージェントはその 1 回の起動ごとに別に数える（親で渡した文は子にも 1 度届く）。
セッションの開始（起動・再開・compact の後）で忘れ、改めて 1 度届く。記憶は
`logs/state/once-<セッション>-<エージェント>.json` に置く。控えの置き場が無い
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
      src の下は自由に直してよい。公開 API の綴りを変えたら docs/api.md も直し、
      テストは tests/ に同じ名前で置く。CHANGELOG は締めるときにまとめて書く。
```

### 何回かに 1 度だけ渡す

`every: N` は「渡す回」を刻む欄。そのルールが当たった回数が `N` の倍数になった回だけが
渡す回になる。書かなければ刻みは 1。

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
  ルールでは当たらない苦情なので、`glob` は広いままでよい
- 数えるのは「そのルールが当たった回数」。同じファイルを 5 回直せば 5。`Bash` を `match` に書かない限り
  シェルやビルドが書いたぶんは数えない
- 刻みは `SessionStart`（起動・再開・compact の後）で 0 に戻る。`additionalContext` に添えたときは届くのが
  遅れ（N 回に届く前に 0 に戻ることが続けば一度も届かない）、`additionalContextOnce` に添えたときはセッション全体で 1 度より多く届く。
  正確に N 回ごとを守る欄ではない
- `additionalContextOnce` に添えると、1 回目ではなく N 回目に届く。「1 回目に言いたいこと」も要るならルールを 2 件に分ける
- 数えは文脈ごと（セッションと、サブエージェントならその起動）で、`logs/state/once-*.json` に
  「鍵 → 回数」で残る。控えの置き場が無い（`--state ""`）なら刻まず毎回届く
- `every: 1` には `--lint` は何も言わない。`0`・負・整数でない値は error で名指しし、判定は 1 として扱って通す。
  渡すものが 1 つも無い `every` は warn

### ファイルの本文を渡す

`additionalContextFile` と `additionalContextOnceFile` は、文の代わりに（または文に続けて）
ファイルの本文を渡す。それぞれ `additionalContext` と `additionalContextOnce` の後ろに、空行で割って並ぶ。
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
- ファイルが無ければ何も足さない。`--lint` はそのことを warn で言う
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
| `ask` | 人に確認が出る（`RULE_ASK`） |
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

`ask` に当たった呼び出しは、権限モードによらず確認に出す（人が意図して置いた確認ポイントなので）。

### ルールが言及していない呼び出し

ccnavi は判定を返さず、Claude Code の権限モードに従う（ADR-0009、ADR-0049）。

| `permission_mode` | 呼び出しはどうなるか | 記録の `decision` |
|---|---|---|
| `auto` | classifier が判断する | `handover` |
| `default` / `acceptEdits` / `plan` | Claude Code 自身の権限の仕組み（`settings.json` の `permissions` と、モードごとの既定）が決める | `handover` |
| 不明なモード / モードが来ない | 人に確認が出る | `ask` |
| `dontAsk` / `bypassPermissions` | 通さない。`CCNAVI_GUARD_UNWATCHED=disable` なら委ねる | `deny` |

渡した回も記録には `decision` が `handover` の行で残り、人に聞いた回の `ask` とは混ざらない。

```sh
jq -r 'select(.decision == "handover") | .subject' logs/log.jsonl | sort | uniq -c | sort -rn
```

確認できる者が居ないモードでは、ask を返しても誰も答えないまま通るので通さない（REQ-PRE-08）。
知らないモードの名前は確認にする。

`CCNAVI_GUARD_UNWATCHED=disable` と書いた層では、`dontAsk` と `bypassPermissions` でも判定を返さず、
そのモードの取り決めに委ねる。`--lint` が「切れている」と warn で言う。

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

`WebSearch` のように対象を取り出せないツールは判定に届かないまま通るので、
`allow` に書いても死んだ行になる（`--lint` が咎める）。`match` は
名前をそのまま突き合わせるので、`Bash` のルールは `PowerShell` に及ばない。
及ぼしたければ `Bash|PowerShell` と並べる。

`glob` の意味は標準ライブラリの `fnmatch` そのまま。`*` が任意の文字列、
`?` が 1 文字、`[abc]` が文字クラス。

文字列全体に当たる。部分一致が欲しければ前後に `*` を自分で書く。
`git push` は素の `git push` にしか当たらず、`cd /repo && git push` には当たらない。
`*git push*` と書けば当たる。

語の切れ目は入らない。`*sed*` は `sedate` にも当たる。右側だけなら空白を
書いて `*sed *` とすれば守れる。左側は glob では書けない（`*git push*` は `legit push` にも当たり、
`* git push*` は行頭の `git push` が外れる）。左の切れ目が要るルールは `regex` を使う。

区切り文字だけは正規化する。`/` と書けば `\` にも当たる（`fnmatch` の外で足している唯一の処理）。
`*/secrets/*` は `C:\repo\secrets\key` にも当たる。

`{root}` はワークスペースルートに置き換わる。hook なら `CLAUDE_PROJECT_DIR`、端末なら `--root` の
実パス。glob なら `{root}/wip/*`、regex なら `^{root}[\\/]` のように書く。
区切りは `/` と `\` のどちらにも当たり、大文字小文字はどの機械でも区別しない。
文面（`message` / `additionalContext` / `additionalContextOnce`）に書いた `{root}` も、モデルへ渡すときに
実パス（区切りは `/`）になる。拒否の文面で sh を案内するときは `'sh {root}/.ccnavi/scripts/...'` と書く。
`--explain` と `--test` は書いた綴りのまま `{root}` を出す。このリポジトリのルールでは、
ワークスペースルート直下の Write / Edit を止める `main-tree` がこれを使っている。

書けないものは `glob` の代わりに `regex` に正規表現を書く。両方書いたルールは受け付けない。
先読み・後読み・後方参照は受け付けない（エンジンをまたいで同じ意味に保ち、組み合わせ爆発を避けるため。
判定の途中で固まった hook は期限に達して素通りになる）。

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
（`vscode-extension/ccnavi-board/`）で見られる。拡張は `ccnavi --explain --json` の出力を
並べるだけ。承認はボードのオーバーレイで一覧を見せ、人が押したら `--approve --yes` を子プロセスで
打つ（形は下の「承認の JSON」）。レビューで残った指摘は、フェーズ行の「決める」で指摘ごとに対応方針を
選び、拡張が `ccnavi-review.sh decide` を子プロセスで打つ（形は下の「残った指摘の JSON」）。
`close-early` はボードに置かず、端末で打つ。組み立て方と使い方はそこの README、
出力の形は下の「ボードの JSON」。

同じ拡張の「ルール設定画面」で、ルールファイルを画面で直し、保存する前に判定を試せる。
判定は `ccnavi --test --json` と `--test-samples --json` を通る（形は「試験の JSON」）。
hook の一覧は `.claude/settings.json` と `settings.local.json` を読むだけで書き換えない。作業中のチケット
（承認済みチケットが `doing`）がある間は保存できない（セッションの途中で判定が変わるのを避けるため）。

同じ拡張の「リスク管理画面」で、実績で測るリスクの配点（`.ccnavi/common/risks.yml`）の境目の点と項目を
画面で直せる。保存の前に `--lint --risk` を通す。点を数えるのは実行ファイルで、拡張は差分を数えない。
ファイルが無ければ組み込みと同じ値で作れる。`CCNAVI_TICKET_CONTROL` が `disable` なら入口ごと出ない。

同じ拡張の「フェーズ管理画面」で、フェーズの種類（「フェーズの種類と計画」の節）を画面で直せる。対象は共通層
（`.ccnavi/common/phases.yml`）、自身の層（`.ccnavi/config/phases.yml`）、プロジェクトの層の 3 種。
保存の前に `--lint --phases`（層なら `--project-phases-file`）を通すので、提案がワークツリーに在る親の計画が指す種類を消すとそこで止まる
（承認済みチケットの計画は照合しない。チケット制御が disable なら照合は走らない）。
子の範囲が上限に収まるかを判定するのは実行ファイルで、拡張は種類を書く場所だけ。共通層のファイルは画面から作らない
（組み込みの既定も雛形も無い）。種類は自身の層かプロジェクトの層に置き、プロジェクト管理画面から開く。`CCNAVI_TICKET_CONTROL` が `disable` なら入口ごと出ない。

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
grep の allow は後ろの `git push` まで通さない。外側は割れないので、`find $(pwd) -name x -delete` の
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

静的に読めないコマンドは、生の文字列との一致に縮退する。縮退した拒否は
「読めなかったので文字列に当てた」と名乗る。

縮退した呼び出しに `allow` は当てず、`deny` と `ask` は当てる（生の文字列に当たりすぎるぶんは厳しい側へ外れる）。
どこにも当たらなければコード `PARSE_UNCERTAIN` で人に確認が出る。ここは権限モードに委ねない。

コメントだけの行のように、実行される部分が何も残らないコマンドは判定に入らない。
記録には `nothing-to-run` が残る。

| 縮退する条件 | 例 |
|---|---|
| 文字列をコードとして実行する呼び出し | `bash -c`、`sh -c`、`eval`、`xargs`、`find -exec` |
| 引用やヒアドキュメントが閉じていない | `echo "git push` |
| 置換が閉じていない | `echo $(git push` |

置換の中身が 1 つでも読み切れなければ、コマンド全体が縮退する。シェルによって読みが割れる形と
バッククォートは、縮退ではなく止めて書き直しを案内する（下の「書き直しを求める形は止めて案内する」）。

引用でコマンド名を割った `"git" push` や `g"it" push` は縮退しない。
シェルと同じ字句規則で語を組み直すので、本来の push としてそのまま止まる。

perl や python は縮退の対象に入れていない（`perl -pi -e 's/git push/.../' README.md` のような 1 行編集を通すため）。

### 書き直しを求める形は止めて案内する

書き直す道が必ずあって、読み分けると規則が増えるか、読み違えると素通りになる形は、
読み解かずにルールより先に形ごとの理由コードで止め、書き直し方を返す（[ADR-0047](docs/adr/0047-rewrite-forms.md)）。
`sh -c`・`eval`・`xargs`・`find -exec` は止めずに、縮退と「実行役のコマンド」の読みで扱う。

#### ブレース展開は語を並べて書く

引用の外の `{a,b}` や `{1..3}` は、シェルが実行する前に複数の語に広げる（`{git,push,origin,main}` は
`git push origin main` として実行される）。ccnavi は展開せず、ルールより先にコード `DENY_BRACE_EXPANSION` で止める
（広げ方が bash と zsh で割れるため。[ADR-0046](docs/adr/0046-brace-expansion.md)）。止めた文面は
見つけた綴りと書き直し方を言う。

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
（[ccnavi.md](ccnavi.md) 12.2）、引用するか、パターンを `a|b)` や `*.jpg || … *.png` のように書けば通る。

#### コマンド名はそのまま書く

変数・置換・グロブをコマンド名に置くと、どのプログラムが走るかが綴りに無く、どのルールも当たらない
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

コード `DENY_AMBIGUOUS_FORM` で止める。どれも bash と zsh で読みが割れるか、作業で使わない形。

| 止まる形 | 書き直し方 |
|---|---|
| `$( )` の中の `case`（`"$(case $x in a) echo 1;; esac)"`） | if/elif で書く。`$( )` の外で分岐する |
| `$((cmd) \| x)` | `$( (cmd) \| x )` と空白を入れる |
| 16 段を超える `$( )` の入れ子 | 内側を先に打ち、出た値を次のコマンドに書く |
| コマンドの位置の `coproc` | `&` で裏に回す |
| コマンドの位置の `select` | `for` で回す |

`$( )` の中の、コマンドの先頭ではない `case` の語（`"$(echo just in case)"`）でも止まる。許容した誤検知
（[ccnavi.md](ccnavi.md) 12.2）。

### 実行役のコマンドが中で実行するコマンドにも当てる

`env rm -f x` の `env` のように、別のコマンドを走らせるためのコマンドがある（実行役のコマンド）。
ルールの多くは `(^|\x00)rm` のようにコマンドの先頭に固定して書くので、ccnavi は実行役のコマンドを 1 枚ずつ外し、
中で実行されるコマンドにも、止める側のルール（`deny` と `ask`）とサブエージェントの禁止を当てる。

```sh
env rm -f .ccnavi/common/rules.yml              # 止まる。env が実行する rm に当たる
timeout 5 sed -i s/a/b/ .claude/settings.json   # 止まる
echo x | xargs rm -f .ccnavi/common/rules.yml   # 止まる
sh -c 'rm -f .ccnavi/common/rules.yml'          # 止まる
env sh .ccnavi/scripts/ccnavi-launcher.sh --approve --yes x   # 止まる（承認の経路）
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

- 1 枚ずつ外し、途中のコマンドも残す。`env sh .ccnavi/scripts/ccnavi-approve.sh` なら `sh .ccnavi/scripts/ccnavi-approve.sh` と
  `.ccnavi/scripts/ccnavi-approve.sh` の両方に当てる。外す深さは 4 まで（元の形は数えない）。1 回の読みで作る語の数にも
  上限（2000）がある
- シェルや `.` / `source` に渡したファイルの先は外さない（スクリプトの引数）。
  `command -v` / `-V`（探すだけ）と `sh -s`（標準入力を読む）も外さない
- 読み切れない形（`sh -c`・`eval`・`xargs`・`find -exec`・`source`・`.`）でも、語に割れる限り外す。
  閉じない引用と閉じないヒアドキュメントでは外さない
- 一覧は ccnavi の組み込みで、`rules.yml` からは足せない。一覧に無いもの（`ssh host <cmd>`・`python -c`・`perl -e`・`script -c`・`watch`・
  `busybox sh` など）は外さないので、先頭に固定したルールは外れる。変数の値・alias・関数
  にも届かない（コマンド名に変数を置いた `$SUDO rm …` は、上の「コマンド名はそのまま書く」で止まる）

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
`PARSE_UNCERTAIN` ではなく `DENY_COMMAND_PATTERN` にし、「読めなかったので文字列に当てた」の断りも付けない。記録の `degraded` は残る。
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

算術式の `$((1 << 2))` は左シフトなので、読む前に落としてある。

`grep -n "<<" README.md` は止まる（引用された `<<` と素の `<<` を `shlex` が区別しない）。
許容する誤検知として設計に記載してある（[ccnavi.md](ccnavi.md) 6.3、12.2）。対象をファイルへ逃がせば回避できる。

## ファイルのパスは行き着く先で見る

`Read` `Write` `Edit` `NotebookEdit` のルールは、payload に来た
`file_path` そのものではなく、絶対パスに直し `..` を畳みシンボリックリンクを解いた
結果に当てる。同じ場所を指す別の綴りでルールを外せない。

```sh
.env                  # どれも同じ判定に行き着く
./.env
docs/../.env
/abs/path/to/.env
```

相対パスは payload の `cwd` から決まる。まだ存在しないファイルへの書き込みは
解けないので、絶対パスにして `..` を畳むところまでで止める。

## 探すツールが読むファイルは、ルールに届かない

`Grep` と `Glob` の対象は探し始める場所のパスで、そこから降りて読まれたファイルは
判定に届かない。`Grep(path=<git プロジェクトルート>)` は `.env` や `secrets/` の中身を返しうるが、
ルールは起点にしか当たらない。

だから共通層の `credentials` ルールの `match` は `Bash|Read|Write|Edit|NotebookEdit` で、
`Grep` と `Glob` を含めていない（[ADR-0050](docs/adr/0050-search-tools-and-ignore.md)）。

`Grep` と `Glob` は ccnavi が受け持たない。探すツールが読むファイルを見るのは、下の表の上 2 つ。

| 段 | 何を止めるか |
|---|---|
| `.gitignore`（ripgrep が読む） | `Grep` が起点から降りていく途中で出会うファイル |
| `.claude/settings.json` の `permissions.deny` の `Read(...)` | ファイル 1 つ 1 つ。`Grep` のファイル読み取りにも効く |
| ccnavi のルール | `Read` `Write` `Edit` `NotebookEdit` `Bash`。`Grep` と `Glob` は受け持たない |

`projects/foo/.env` が foo の `.gitignore` に入っている場合の噛み合い方。

| 呼び方 | ccnavi のルール | `.gitignore` | `Read()` の `deny` |
|---|---|---|---|
| `Grep(path=<git プロジェクトルート>)` | 当たらない | 弾く | 弾く |
| `Grep(path=.../foo/.env)` | 当たらない | 当たらない | 弾く |
| `Read(.../foo/.env)` | 止める | 当たらない | 弾く |
| `Bash: cat .../foo/.env` | 止める | 当たらない | 当たらない |

2 行目は `Read()` の `deny` が唯一の守りになる。`permissions.deny` を持たない配布先では、
ignore されたファイルを名指しした `Grep` は通る。

### `.gitignore` を当てにしてよい範囲

ripgrep の既定の挙動で、ccnavi の側では変えられない。

- 起点そのものに指定されたパスには当たらない。ignore されたディレクトリやファイルを `path` に渡すと、その中は読まれる
- `.git` が無いディレクトリでは `.gitignore` を読まない（ripgrep の `--require-git`）。clone
  していない置き場、git 化していない `projects/<名前>/` が該当する
- 降りた先に `.git` があれば、そこから下はそのリポジトリの `.gitignore` が効く。ワークスペース
  ルートが git 管理下でなくても効く。`.git` がファイル（ワークツリーの gitdir ポインタ）でも同じ
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
| `enable` | 判定し、`deny` なら止め、`ask` なら人に確認を出す。指定が無いときはこれ |
| `dry-run` | 同じ判定を行い、呼び出しには手を出さず「`enable` なら何をしていたか」を伝える |
| `disable` | 判定しない |

`disable` は `.claude/settings.json` に書いても効かない（名指しで無視して理由を出す）。エージェントが書き換えられるファイルなので、
監視される側が監視を止められないようにしている。`disable` にするときはセッションを起動する側の環境から渡す。
読めない値は報告して `enable` として扱う（ADR-0007）。詳細は [ccnavi.md](ccnavi.md) の 4.4。

## ツール実行後の監視

実行前の判定は引数しか見ないので、引数に現れない書き込み（ビルドの出力、スクリプトの内部、読み切れないシェル構文）は
素通りする。実行後の監視は、走ったあとの作業ツリーを `git status` で読んでそれを拾う。設計は [ccnavi.md](ccnavi.md) の 7。

保護領域は別に宣言しない。`match` に `Write` `Edit` `NotebookEdit` のどれかを含むルールが、そのまま保護領域の宣言になる。
例外は ccnavi 自身の書き込み。記録と控えの置き場は最初から外れる。チケットの置き場では、ccnavi の副命令（`ticket start` / `finish` や
`review request` / `ready` など）が書いたと内容から見分けられる変更だけが外れる（ADR-0075）。作業範囲・親・フェーズが変わっていれば報告する。

```
[ccnavi] POST_VIOLATION (rule: guard-config)
path: .ccnavi/common/probe.json (?? / new)
after: Bash(npm run build)
undo: git clean -f -- ".ccnavi/common/probe.json"
ガード自身のルールです。エージェントの判断で書き換えず、変更が要る理由を伝えて利用者に依頼してください。
```

呼び出しは取り消せないので、`enable` では exit 2 と標準エラーで差し戻し、`dry-run` では `additionalContext` で報告だけする。
戻す手順は対象ごとに 1 つ。

| 変更の種類 | 戻し方 |
|---|---|
| 中身が変わった・消えた | `git restore --staged --worktree -- <path>` |
| 追跡されていないものが現れた | `git clean -f -- <path>` |
| 索引に足された状態で現れた | `git rm -f -- <path>` |

### 前から在った変更は原因にしない

セッション前からある変更（他のセッションや人の書きかけ）は、初めて見たときに控えを取り、以降は新しく現れたものだけを
差し戻す。控えた側は `POST_PREEXISTING` として 1 度だけ伝え、戻すなと明示し、自動復元の対象にもしない。
控えはセッションごとに `logs/state/` へ置く（消えても次の起動で取り直す）。同じ場所でも「変わった」の次に「消えた」が来れば、もう一度言う。

### ターンの終わりに人へ報告する

`Stop` に登録すると、そのターンで変わった保護領域を戻す手順付きで `systemMessage` に返す。比べる相手は `UserPromptSubmit` で
控えた状態なので、`UserPromptSubmit` を登録していないと `Stop` は何も言わない（記録には `no-turn-baseline`）。
ここでは戻さず、`CCNAVI_MODE` も見ない（見えたことを言うだけなので、`dry-run` でも報告する）。

同じ `Stop` で、`finish` の打ち忘れを 1 回だけ促す（ADR-0087）。メインエージェントの cwd のワークツリーに結び付いたチケットが
着手済みで、そのワークツリーに未コミットの変更が無く（追跡していないファイルも数える）、基準点より先に自分で作ったコミットがあれば
（子なら親のブランチ、親なら `origin/HEAD` を取り込んだだけのコミットとマージのコミットは数えない）、
`{"decision": "block", "reason": …}` で止め、`ccnavi-ticket.sh finish <識別子>` の綴りと「まだ続けるなら理由を書いてから終える」を
渡す（理由コード `NUDGE_TICKET_FINISH`。報告があれば同じ JSON の `systemMessage` に載る。記録の `decision` は `nudge`）。同じセッションで
同じチケットを同じ HEAD のまま促すのは 1 回だけで（控えは `logs/state/nudged-<セッション>.json`）、コミットを足せばまた促す。
payload の `stop_hook_active` が真なとき、控えを置けないとき、子で親のブランチを引けないときも促さない。チケット制御かモードが `disable`、未着手、書き込み停止中（`blocked`）、親で `finish` が通らない形（開いている子・
レビュー準備中／レビュー待ち・フィードバック計画待ち・終わっていないフェーズ）、git を読めないとき、`SubagentStop` では促さない。
`dry-run` では止めず、止めたはずの文を `systemMessage` に載せる。

### 自動復元

`CCNAVI_RESTORE_IF_DENY=enable`（既定）のとき、ccnavi 自身が戻す。`dry-run` では戻さず、報告に `would-restore` の行を足す。
`disable` では戻さず、その行も出さない。`CCNAVI_MODE=dry-run` のときは、こちらが `enable` でも `dry-run` として振る舞う
（組み合わせは [requirements.md 2.2](requirements.md#22-共通の動作--req-cmn)）。

戻す先はコミット済みの内容なので、保護領域に置いた未コミットの変更は失われる。守りたいなら `disable` にするか、宣言を狭める。
現れたファイルは消さずに `logs/state/aside/<日時>/` へ退避し、退避先を報告に載せる（ADR-0019）。

### コアファイルを守る

実行後の監視はルールファイルから保護領域を導くので、ルールファイル自身はそこでは守れない。そこで次のものは組み込みで持つ。
`CCNAVI_GUARD_CORE_FILES` が切り替える。設計は [ccnavi.md](ccnavi.md) の 8。

| 対象 | 何が懸かっているか | 控えを取る時点 |
|---|---|---|
| `.claude/settings.json` | hook の登録そのもの | ツール実行前 |
| `.claude/settings.local.json` | 同上。個人の上書き | ツール実行前 |
| 共通層の 3 本（`.ccnavi/common/{rules,phases,risks}.yml`） | 判定の中身そのもの | ツール実行前 |
| `<ワークスペースルート>/.ccnavi/config/{rules,phases,risks}.yml`（自身の層の 3 本） | 同上 | ツール実行前 |
| `projects/<名前>/.ccnavi/config/{rules,phases,risks}.yml`（各プロジェクトの層の 3 本） | 同上 | ツール実行前 |
| `CCNAVI_BIN_PATH` が指すファイル（既定の配置では振り分けの sh） | 判定器の実体 | セッション開始 |
| それが振り分けの sh（名前が `ccnavi-launcher.sh`）なら、1 つ上の `bin/<os>-<arch>/` にあるこの機械の実行ファイル | 同上。hook が実際に走らせるもの | セッション開始 |

- git が追跡しているものは、ワークツリーが持つ同じファイルも対象に入る（統合すればそのまま効くため）。実行ファイルは除く
- 無いファイルは対象から外れ、無いことは報告しない。無かったところに現れた場合は言うが、消さない
- `.ccnavi/scripts/` のスクリプトはこの一式に入れない。組み込みの `deny`（`*/.ccnavi/*`）と `CCNAVI_RESTORE_IF_DENY` が受け持つ
- 実行ファイルは大きいので、セッションに 1 度だけ写し、大きさと更新時刻で突き合わせる。`SessionStart` に登録しないと守られない。
  `CCNAVI_BIN_PATH` を指定しなければ対象に入らない
- 設定ファイルは実行前に `logs/state/selfguard/<セッション>/` へ控え、実行後に変わっていれば控えから戻す（未コミットの編集は残る）。
  控えが無いときだけ git から戻し、そう報告する
- 実行ファイルの実体は `logs/state/selfguard/store/` にハッシュ名で 1 本だけ置く。3 日より長く触られていないセッションの控えは、
  次のセッション開始で落とす

戻す前に止める側も組み込みで持つ。この機能が有効な間、次の場所へシェルから書き込む形は、ルールに書いていなくても拒否される。
ルールファイルが壊れて組み込みの既定を使っている間も同じ。

- `.claude/` の `hooks/` と `settings*.json`、ccnavi ディレクトリ（`.ccnavi`）、`ccnavi-git.sh`、実行ファイル、
  共通層の 3 本（`.ccnavi/common/`）、記録と控え（`logs/log.jsonl` と `logs/state`）
- 綴りは「区切りが続くか、そこで終わる」形で当てるので、`rm -rf .ccnavi` や `mv .ccnavi .ccnavi.bak` も止まる
- 場所の綴りは大文字小文字を区別せずに当てる。コマンドの名前（`rm` / `cp`）も区別しない（ADR-0051）。止める側が広がるだけなので問題にしない

名指しのツール（`Write` / `Edit` / `NotebookEdit`）からも守る。

- `builtin-guard-project-home`: ccnavi ディレクトリの下（`*/.ccnavi/*`）と、`CCNAVI_BIN_PATH` のパスおよび実行ファイルの置き場
  （名前が `ccnavi-launcher.sh` なら 1 つ上の `bin/<os>-<arch>/`、それ以外なら隣の `<os>-<arch>/`）
- `builtin-guard-common-layer`: 共通層の 3 本。ワークツリーの中の同じファイルも止まる
- 見本 `.ccnavi/common/rule-samples.yml` も `builtin-guard-project-home` が止める。見本の下書きはワークツリーの `scratchpad/` に置き、
  ルールの下書きと一緒に利用者に渡す

止めるのは書き込む綴りと場所の組で、場所の名前が出ただけでは止めない（`cat .ccnavi/common/rules.yml` や `git add <パス>` は通る）。
`builtin-guard-` で始まる id はどの層のルールファイルにも書けない（書けば error。`--lint` も言う）。外したいなら env でこの機能ごと切る。
ルールファイルが読めず既定を使っている間は、共通層のルールファイルを Write / Edit で直した結果を戻さない（実行前に読めなかったときに限る）。

`PreToolUse` の登録ごと消された場合は ccnavi が一切動かない。hook の登録を hook 自身で守ることはできない。

### 見えないもの

| 見えない | なぜ |
|---|---|
| `.gitignore` に入っているファイル | `git status` に出ない |
| git の作業ツリーの外 | 監視はリポジトリの中だけを見る |
| この呼び出しが触っていないツリー | 呼び出しごとに見るのはワークスペースルートと、この呼び出しの行き先（Bash は cwd）が属するツリーだけ。ターンの区切り（`UserPromptSubmit` と `Stop`）では全部のツリーを見る |
| 失敗したツール呼び出しの副作用 | `PostToolUse` は成功した呼び出しの後にしか走らない |
| `Read` `Grep` `Glob` `WebFetch` `WebSearch` の直後 | 見に行かない。次の書きうるツールの直後に見える（遅れであって見落としではない） |

## チケットによる作業範囲

ルールが「どこに書かせないか」を長く決めるのに対し、チケットは作業 1 本のあいだ「今回どこに書くか」を決める。
チケットは複数が同時に効く。親（メインエージェント）が作業を子チケットに分け、子は別々のワークツリーでサブエージェントが実行する。
設計は [ccnavi.md](ccnavi.md) の 9、要求は [requirements.md](requirements.md) の REQ-TKT。

### 使うかどうかはワークスペースが決める

`CCNAVI_TICKET_CONTROL` は `.claude/settings.json` の `env` に 1 つだけ書くつまみで、ワークスペース（Claude Code を開いた場所）の
下のプロジェクト全部に同じ値が効く。既定は `enable`。全体ルールだけで足りるワークスペースは `disable` を書く。
導入スクリプトは `--ticket-control` で聞き、常にこの 1 行を書く。

効いているワークスペースでも、作業の進め方は 2 つある。

- **直接作業**: 調査や小さな修正。チケットを起こさず、判定は全体ルールだけ。ワークスペースルート直下と、承認済みチケットの無いワークツリーがこれ
- **チケット作業**: 設計に触れる・複数のフェーズに分かれる・人のレビューが要る修正。提案を書いて承認を受け、フェーズとリスクの配点に従って進める

どちらで進めるかはモデルが決める。ccnavi は判定では担保せず、`SessionStart`（起動・再開・compact・clear）で次の案内を渡す。
サブエージェントには渡さない。

```
[ccnavi] このワークスペースはチケット制御を使っている。作業の進め方は 2 つ。
- 直接作業（調査・小さな修正）: チケットを起こさずそのまま進める。判定は全体ルールだけ。
- チケット作業（設計に触れる・複数のフェーズに分かれる・人のレビューが要る）: wip/proposals/ に
  提案を書いて承認を受ける。以後の操作は sh .ccnavi/scripts/ccnavi-ticket.sh を通す（使い方は --help）。
どちらで進めるか迷ったら、利用者に聞く。
（現状: CCNAVI_MODE=dry-run。deny に当たっても止まらない。通ったことを許可と読まず、出た案内に次から従う）
```

最後の行は dry-run のときだけ出る。それ以外の手順（レビューの sh、`review: mr` / `chat`、配点の綴り、後工程）は、必要になった場所で改めて届く。

### 置き場と状態

チケットは 1 本のファイルで、2 つの置き場を行き来する（ADR-0055）。提案は `wip/proposals/<状態>/<識別子>.md`
（`CCNAVI_TICKETS_PROPOSAL`）、承認済みチケットは `.ccnavi/approved/<状態>/<識別子>.md`（`CCNAVI_TICKETS_APPROVED`）。状態は置き場が表す。

```
  wip/proposals/todo ──人が承認──→ .ccnavi/approved/doing
                                          │
                              エージェントが finish（レビュー要）
                                          ▼
  wip/proposals/review ──人がレビュー──→ .ccnavi/approved/done
                                          ▲
                              エージェントが finish（レビュー不要）／cancel
```

| 置き場 | 意味 | 動かすもの |
|---|---|---|
| `wip/proposals/todo/` | 承認待ち | 親が書く。作成と編集は自由 |
| `.ccnavi/approved/doing/` | 承認済み。判定が範囲を読むのはここだけ | `ccnavi --approve`（人）、ボード。続きの子を人が起こすときも直にここ |
| `wip/proposals/review/` | 作業が終わり、人のレビューを待つ | `sh .ccnavi/scripts/ccnavi-ticket.sh finish <識別子>`（フェーズがレビュー要のとき） |
| `.ccnavi/approved/done/` | 閉じた。取り消しは `cancelled_at` を持ってここに入る | `ccnavi-review.sh confirm` / `decide`、`ccnavi --reviewed`、`close-early`（人）。レビュー不要の `finish` と `cancel --reason` |

- `.ccnavi/approved/` へ動かすのは人、`wip/proposals/` へ動かすのはエージェント
- `review/` への直接の作成・移動は誰がやっても止まる（`builtin-ticket-state`）。`.ccnavi/approved/` は ccnavi ディレクトリの守りが止める
- 動かすスクリプトと push はサブエージェントには打てない（`DENY_SUBAGENT_TICKET_OP`）。子チケットのワークツリーからの push は
  git のラッパースクリプトが拒む。合流と push と閉じるのは親の仕事
- `start` は置き場を動かさず、着手の時刻と基準点（そのワークツリーの HEAD）を書く。`finish` は完了の時刻を書き、フェーズが
  レビュー要（延期を含む）なら `review/`、不要なら `done/` へ動かす。`cancel` は `doing/` から `done/` へ動かし、時刻と理由を書く
- 順序は「子の成果をマージ → finish → ワークツリーを消す」
- 再開するときは人が `done/` から `doing/` へ戻す

フェーズのマーカー（`pending` `skipped` `requested` `reviewed`）を含む遷移は [ccnavi.md](ccnavi.md) の 9.6。

### 書式

frontmatter は rules.yml と同じタイプ（`deny` / `ask` / `allow`）。効くのは Write / Edit 系のパスの項だけで、
`match` に Bash を書いた項や `tools` は「効かない」と名指しで警告する。

```yaml
---
version: 1
ticket: i0050-03
issue: 50                # 親だけ。マージリクエストの Closes に写す。省ける
project: lib             # 置き場と同じ名前。省ける（提案を置いた場所が決める）
parent: i0050            # 子だけ。親は書かない
phase: 2                 # 子だけ。同じ親の同じ番号が 1 つのまとまり
predecessors: [i0050-01] # 子だけ。先に閉じているべき子。承認と着手（start）で求める。書き込みは止めない（ADR-0088）
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

- 識別子は子が `<親>-<2 桁連番>`。親の識別子は人が決める（issue 番号など）
- ワークツリーの名前は識別子と同じ。`.claude/worktrees/i0050-03/`
- 子は親の部分集合として書く。親やフェーズの種類の `scope` を超える項は承認で warn に出るだけで、判定がその上限で切り詰める
- 書いていない場所は範囲外。親子は厳しい側が勝つ
- 深さは 2 段。範囲は 20 件まで
- 大文字小文字は、どの機械でも区別せずに当てる（`docs/Design/*` は `docs/design/plan.md` にも当たる）。綴りは書いたまま記録と承認画面に出る。
  `regex` も同じで、区別が要るなら `(?-i:...)` で囲む

### 効くのは承認したものだけ

判定が読むのは `.ccnavi/approved/doing/` の承認済みチケットで、`wip/proposals/todo/` の提案ではない。承認のあとに同じ識別子の
提案を書いても範囲は効かない（親の計画の改版だけが承認の対象に入る）。承認の詳細は [ccnavi.md](ccnavi.md) の 9.4。

```sh
ccnavi --approve
ccnavi --approve i0002 i0002-01   # 並べた識別子だけを承認の対象にする
ccnavi --approve --preview --verify i0002 i0002-01   # 承認できる状態かを確かめるだけ（置かない）
```

全ツリー（ワークスペース、プロジェクト、ワークツリー）の `wip/proposals/` を走査し、未承認のものをまとめて見せる。
画面に出るのは、親が新たに書けるようにする領域、子が親の範囲をどこまで絞ったか、子ごとの人間レビュー要否、計画、親の `issue:`。
子の範囲が上限を超えていれば「チケットで編集対象としているが、書き込めない場所」の見出しで出る。

- 識別子を並べると対象を狭める。承認待ちに無い識別子や、承認待ちの親の改版を外した子が混じれば、何も承認しない
- 対象から外した親を持つ子は「親が承認されていない」で落ちる。前のフェーズが閉じていない子も落ちる
- 終わったフェーズに子を足して承認すると、そのフェーズは開き直り、マーカー 4 種（`pending` `requested` `reviewed` `skipped`）は全部消える
- リスクの点は承認では数えない。子を閉じるときに実績で測る（「実績のリスク」）

承認する場所は 3 つ。

| 経路 | 形 |
|---|---|
| 端末 | `sh .ccnavi/scripts/ccnavi-approve.sh [<識別子>...]`。一覧を見せて y/N を取り、通れば `ccnavi-push-approved.sh` で運ぶ。`-` で始まる語と空の語は受けず 2 で終わる |
| ボード | `--approve --preview --json` で読んでオーバーレイに出し、押したら `--approve --yes <識別子,…> --digest <値> --json` を打つ（「承認の JSON」）。1 件以上通れば `ccnavi-push-approved.sh` を統合ターミナルへ送る |
| ファイルを動かす | 提案を `.ccnavi/approved/doing/` へ動かす（ADR-0058）。GitHub の画面しか使えない人向け。承認の画面を通らないぶん、構造の検査は判定が当て、引っかかれば `DENY_TICKET_BLOCKED` で止まる（`--lint` とボードの JSON の `blocked` が言う） |

エージェントが `--yes` を打つ道は組み込みの deny（`builtin-guard-ticket-approval`）が塞ぐ。`--preview` は通す。

**承認済みチケットは親チケットのブランチに乗って他の機械へ届く。** 承認しても push しなければ、他の機械では承認されなかったことになる。
運ぶのは `sh .ccnavi/scripts/ccnavi-push-approved.sh`。

- ワークスペース、`projects/*`、`.claude/worktrees/*` のツリーごとに、変更があれば置き場（`CCNAVI_TICKETS_APPROVED`）だけをコミットし、そのブランチへ push する
- シンボリックリンクは辿らず、名指しして飛ばす
- `main` / `master` / `develop` / `release` / `release/*` と、ブランチの上に居ないツリーは push せず、コミットまでで止める
- 運ぶものが無ければ `運ぶ承認済みチケットは無い。` と 1 行出す
- エージェントが打つ形は組み込みの deny（`DENY_TICKET_APPROVAL_CLI`）が止める

| 終了コード | いつ |
|---|---|
| 0 | 運ぶものが無い、または全部コミットした（push しなかったブランチ、飛ばしたツリーを含む） |
| 1 | `git add` / `commit` / push が落ちたツリーが 1 つ以上ある。巻き戻さないので、もう一度打てば送れる |
| 2 | 引数の誤り、ワークスペースルートが見つからない |

`ccnavi-approve.sh` は承認が通れば、運ぶ段が 1 で終わっても 0 を返す。

受け取る側はセッションの頭に `.ccnavi/scripts/ccnavi-fetch.sh` が取ってくる。進めるのは fast-forward だけで、未コミットの変更があるツリーや
分岐したツリーは触らず理由を 1 行で言う。取ってくるのは、チェックアウト中のブランチと、リポジトリのデフォルトブランチ（`origin/HEAD`。ADR-0060）。
リモートに届かないときも止めず、手元の版で判定する。認証は尋ねず、fetch 1 回を `CCNAVI_FETCH_TIMEOUT` 秒（既定 15）で切る。
認証で落ちたときはそう 1 行添えるので、人が端末で一度 `git fetch origin` を打って資格情報を保存すれば、次のセッションから通る。

順序は「承認 → 承認済みチケットをコミット → 子のワークツリーを作る → `start`」。コミットの前にワークツリーを作ると、その子には範囲が効かない。

**承認を頼む前に、エージェントが自分で確かめる。** `--approve --preview --verify [<識別子>...]` は、置かずに「いま `--approve` を打てば
その提案が承認の対象に入るか」を返す。**0 が「はい」、3 が「いいえ」、1 は使い方か設定の誤り。**「いいえ」は、承認待ちに無い識別子、
承認待ちが 1 件も無い、承認の対象にしない提案がある、の 3 つ。範囲の超過と読めない提案は「いいえ」にしない（本文には出す）。
`--json` を足すと「承認の JSON」の形に `verify` が付く。提案を `wip/proposals/todo/` に書くと、この確認を勧める文が文脈ごとに 1 度届く（ADR-0059）。

承認されたことは、次の `UserPromptSubmit` か `PreToolUse` で 1 度だけモデルに届く（`additionalContext`）。拡張は同じ文を
オーバーレイの 2 ボタン（コピー、新しいセッションで開く）から渡せる。人がレビューを終えたことは、ボードの「レビュー済み連絡」が
「親のワークツリーで `ccnavi-review.sh confirm --phase <N>` を打て」の文を同じ 2 ボタンで渡す。マーカーを置くのはその `confirm`。

### 判定の鍵はファイルの行き先

Write / Edit の対象を解いた先が `.claude/worktrees/<名前>/` の中なら、その名前と同じ識別子の承認済みチケットで判定する。
呼び出し元の cwd も、サブエージェントかどうかも見ない。ワークスペースルート直下と、チケットの無いワークツリーはルールだけで判定する。
ルールとチケットの判定を両方出し、**強い側を採る**（`deny` > `ask` > `allow` > 何も言わない）。同じ強さならルールの判定と文面を使う。

| ルール | チケット | 結果 |
|---|---|---|
| `deny` に当たる | どれでも | 止まる（ルールの文面） |
| `ask` / `allow` に当たる | 範囲の外、または `deny` の項 | 止まる（`DENY_TICKET_SCOPE`） |
| `ask` に当たる | 範囲の中（ask / allow）、またはチケットが無い | 人に確認が出る（`RULE_ASK`） |
| `allow` に当たる | 範囲の中（ask） | 人に確認が出る（`TICKET_ASK`） |
| `allow` に当たる | 範囲の中（allow）、またはチケットが無い | 通る |
| 何も言わない | 範囲の中（allow） | 通る |
| 何も言わない | 範囲の中（ask） | 人に確認が出る（`TICKET_ASK`） |
| 何も言わない | 範囲の外、または `deny` の項 | 止まる（`DENY_TICKET_SCOPE`） |
| 何も言わない | ワークツリーにチケットが無い | 権限モードに従う（`UNDECLARED`） |

チケットの判定がルールに勝った回は、文面の頭に `rule: <id> (allow) lets this through, but the ticket for this worktree narrows it` が載り、
チケットの `deny` の項に当たったなら `ticket entry: deny <綴り>` も載る。記録では `rules` が `(ticket-scope)` とルールの id の並びになり、`source` は空になる。

```sh
jq -r 'select(.rules[0]? == "(ticket-scope)" and (.rules | length) > 1) | .subject' logs/log.jsonl
```

子の範囲は、子の宣言を親の範囲とフェーズの種類の `scope` の両方で切り詰めたもの（種類が `inherit` か、`phases.yml` がどの層にも無ければ親だけ）。
上限で止めたときは、文面の頭に `limit: phase type 設計 (design): wip/design/*, docs/*` や `limit: parent i0001: src/*, tests/*` の形で 1 行載る。
`phases.yml` があるのにその番号の種類を引けないときは、親でだけ切り詰め、notice で言う。

範囲の外として扱わない場所。

- 提案の置き場（`wip/proposals/`）と承認済みチケットの置き場（`.ccnavi/approved/`）。`review/` と承認済みチケットは組み込みの deny が止める
- 下書きの置き場（ワークツリーのルートの直下 1 段の `scratchpad/`。大文字小文字を区別する）。**実行前の判定だけ**で、実行後の監視と
  サブエージェント終了時の検査は外さない。そこに現れるのは `scratchpad/` が追跡されているときなので、範囲外として報告する

シェルが書いたものは実行後の監視が `POST_TICKET_SCOPE` で言う（ルールの `allow` に当たる場所でも同じ）。子のワークツリーは、
サブエージェントの終了時に基準点（`base_sha`）からのコミット済みの差分と未コミットの両方を見て、範囲外が残っていれば 1 回だけ差し戻す。

ワークツリーを `allow` で開けているルールがあっても、承認済みチケットに結び付いたワークツリーの書き込みはチケットの範囲に縛られる。
止まったら、ルールを緩めるのではなく、その場所を範囲に持つチケットを提案する。

### フェーズと HITL ポイント

フェーズの終わりは、人の手が入るところ（HITL ポイント）の 1 つ。人の手は範囲の承認・レビュー・未解決の受け入れの 3 か所に寄せてある。
同じ親の同じ `phase` の子が `doing/` に 1 枚も無く、`review/` か `done/` に 1 枚以上あれば、そのフェーズは終わり。取り消しだけのフェーズは終わらない。
設計は [ccnavi.md](ccnavi.md) の 9.8。

人がどこで見るかは `none` / `chat` / `mr` の 3 つで、次の厳しい側が勝つ（`none` < `chat` < `mr`）。

- フェーズの種類の `review`
- 親の計画の項の `mr`（種類が `none` でも `mr` にする）
- 延期した番号の分を引き受けているなら、その分の宣言

閉じた子の `human_review.required` が `true` か、実績のリスクが HIGH 以上のときは、`none` を `chat` に上げるだけで場所は指さない。

| | `mr` | `chat` | `none` |
|---|---|---|---|
| 返す文 | 合流と push を済ませ、`request` でレビューを頼み、ターンを終えて利用者を待て | 合流して利用者に差分を見てもらい、ターンを終えて待て | レビューを省略して次のフェーズへ進める |
| HITL ポイント | 来る。レビュー済みのマーカーまで止まる | 来る。レビュー済みのマーカーまで止まる | 来ない。省略のマーカーを残す |
| 先へ進める者 | `ccnavi-review.sh confirm` / `decide` | 人が端末で `ccnavi --reviewed <N> --chat` | — |
| `review/` の子 | レビュー済みで `.ccnavi/approved/done/` へ動く | 同じ | `finish` の時点で `done/` へ |

`chat` はホストへ出ないので、マージリクエストもトークンも要らない。`--chat` は種類が `chat` と宣言したフェーズにしか当たらず、
`request` を出したあとのフェーズも断る。逆向き（`chat` のフェーズを `request` でマージリクエストに出す）は通り、そこから先は `mr` と同じになる。

止めている間、その親の cwd からの `Agent` と Bash を止める（`DENY_PHASE_REVIEW`）。通すのは `ccnavi-ticket.sh` `ccnavi-review.sh`
`ccnavi-git.sh` の 3 本だけ。Write / Edit は通すので、次のフェーズの計画はレビュー前に進められる。
止めている間は、依頼を出すまでが「レビュー準備中」（次に動くのはエージェント）、出してからが「レビュー待ち」（次に動くのは人）。

### フェーズの種類と計画

`phases.yml` に**フェーズの種類**を定義し、親が `plan:` にその並びを書くと、フェーズに意味が付く。
置き場は層ごと（共通層 `.ccnavi/common/`、自身の層とプロジェクトの層 `.ccnavi/config/`）で、`scope` の綴りが
レイアウトに付くならワークスペース自身の層に置く（このリポジトリもそう）。
種類は人が持つ設定で、エージェントは書き換えない。どの層にも無ければ番号だけの挙動のまま。設計は [ccnavi.md](ccnavi.md) の 9.7。

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
    review: chat          # このセッションで人が見る。マージリクエストは作らない
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
plan:                                  # 全体計画。--approve が通ることが合意
  - research
  - {type: design, review: defer}      # レビューを次と一緒に見る（延期。省略ではない）
  - acceptance
  - implement
feedback:                              # フィードバック計画。レビューのあと改版で足す。空でも出す
  - implement-feedback
```

子は `phase: N`。N 番目の種類は親の計画が言う。番号は全体計画が 1 から、フィードバック計画がその続き。

- **合意は全部 `--approve` を通る。** 全体計画は親の承認、各フェーズの計画はその番号の子の承認、フィードバック計画は `feedback:` を
  足した親の改版の承認（全体計画の最後のレビューが済んでから 1 回だけ。指摘が無くても `feedback: []` で出す）
- **順序は承認で止まる。** N 番目の子は、N-1 番目までが全部閉じてレビュー（延期でなければ）が済むまで承認されない。`overlap` の組だけ例外。
  フェーズには必ず子が 1 本以上あり、親は計画・合流・依頼だけをする
- **改版**で変えられるのは `plan`（子がまだ承認されていない番号の項）と `feedback` だけ。`feedback` の承認後は新しいフィードバック作業
  フェーズを足せないが、その中で子を足すのは何度でもできる。残る指摘は人が `decide` で issue に回す

**DAG で待たせる。** ファイルの頭に `order: dag` を書き、種類に `after:` を書くと、N 番目は種類の祖先に当たる番号だけを待ち、
繋がっていない種類は並行して進む。

```yaml
version: 1
order: dag                 # 合成に入る層が全部 dag と書いたときだけ効く
phases:
  design:     {title: 設計, review: mr}
  acceptance: {title: 受入テスト作成, review: none, after: [design]}
  implement:  {title: 実装とテスト, review: none, after: [design]}   # acceptance と並行
  docs:       {title: 文書, review: mr, after: [acceptance, implement]}   # 合流点で人が見る
```

- 待ち方は全体計画の承認のときに計算され、親の承認済みチケットの `workflow:` に写る。あとで `phases.yml` を直しても、改版を出すまで進行中の親には効かない
- 承認は、`after` の循環、後ろの項が前の項の祖先になる並び、終端が 2 つ以上の計画を拒む。**辺の書き漏れは並行として通る**ので、承認の画面の待ちで確かめる
- フィードバック計画はいつも一直線。レビュー待ちで止めるのは親ごとなので、どれかの枝がレビューを待つ間は別の枝にも子を起こせない

`--explain` と `SubagentStart` はフェーズを「3: 実装とテスト」のように示す。親の局面（作業中・レビュー待ち・フィードバック計画待ち・
フィードバック対応中・閉じられる）を名指しするのは `--explain` とボードだけ。

### レビューの依頼と確認

```sh
sh .ccnavi/scripts/ccnavi-review.sh request --phase 2 --body-file wip/tmp/request.md
sh .ccnavi/scripts/ccnavi-review.sh confirm --phase 2
sh .ccnavi/scripts/ccnavi-review.sh comment --body-file wip/tmp/decision.md
```

設計は [ccnavi.md](ccnavi.md) の 9.10 と 9.11。

- `request` は前提（フェーズが終わっている・レビューが延期されていない・子ブランチが親に取り込まれている・未コミット無し・push 済み・未依頼）を
  確かめ、1 つでも欠けたら全件を列挙して何もしない。通れば依頼コメントを投稿し、マーカーを残す。マージリクエストが無ければ Draft で作る
  （題・本文・`Closes #<番号>` は親チケットから写す）
- 依頼後に親の HEAD が動いたら（ccnavi 自身の置き場の外が変わったときだけ数える）、レビュー済みになる前なら `request` を打ち直せる
- `confirm` は、依頼時の HEAD と今の HEAD が同じで push 済みであることを求め、今残っている未解決スレッドを数える（付いた時刻では絞らない）。
  未解決も変更要求も無ければレビュー済みのマーカーを置き、そのフェーズの `review/` の子を `done/` へ動かす。残るなら一覧を返す。
  変更要求はレビュアーごとに最新だけを数え、取り下げは無い扱い
- `comment` はこのセッションで受けた承認や判断をマージリクエストのコメントに写す

残った指摘の対応方針は人が決める（`decide`）。ボードの「決める」か、端末で `ccnavi-review.sh decide <N>`。

- **対応しない（受け入れて進む）。** `phases/<親>/accepted.json` に控え、次の `confirm` から数えない
- **このフェーズで直す。** 直す指摘を写した続きの子を、同じフェーズの番号で `.ccnavi/approved/doing/<親>-<次の連番>.md` に直に置く
  （範囲は見た子の範囲の和）。フェーズは開き直り、マーカーは消える
- **issue に回す。** 受け入れたうえで、その指摘を載せた issue を作る。フィードバック計画が承認されたあとだけ選べる

どれも直さなければレビュー済みにし、1 件でも直すなら見た子を `done/` へ動かしてマーカーは置かない。決めた内容は sh がコメントに写す。
ボードの経路は端末を求めない代わりに指紋（`digest`）の一致を求め、エージェントが同じ形を打つ道は組み込みの deny が止める。
`--reviewed <N> --chat` でも、レビュー済みにしたあとに指摘を 1 行ずつ打てば同じ形で続きの子を起こす。
変更要求のレビューが立っている間はどの道も通らない。解くのはレビュアーの approve / dismiss だけ。

**リモートを読み書きするのは sh で、実行ファイルはネットワークに出ない。** マージリクエストの中身は `ccnavi-review.sh` が取ってきて
JSON で渡す（`--result <path>`）。

| sh の動き | 実行ファイルの段 |
|---|---|
| `request` | `review prepare`（前提を確かめ、マーカー付きの本文を控えの置き場に書き出す）→ sh が投稿 → `review requested`（マーカーを置く） |
| `confirm` | sh がスレッドとレビューを取ってくる → `review confirm`（判定してマーカーを置き、`review/` の子を `done/` へ動かす） |
| `decide N` | sh が取ってくる → `--reviewed N --accept-unresolved`（人に見せ、指摘ごとに対応方針を選ばせる）→ issue に回す分があれば sh が issue を作り、決めた内容をコメントに写す。ボードは `decide N --preview`（`--preview --json`。一覧と指紋）と `decide N --choices <JSON> --digest <指紋>`（`--yes <JSON> --digest <指紋> --json`）で同じ道を通る |
| （`chat` のフェーズ） | sh は動かない。人が端末で `ccnavi --reviewed <N> --chat --cwd <親のワークツリー>` を打つ。依頼も写しも無い |
| `comment` | sh が投稿する。実行ファイルは関わらない |
| `ready` | Draft を外す（「マージに進んでよい」の合図）。`review ready`（親を閉じられる状態かを確かめ、マーカー `phases/<親>/ready.json` とコメントの下書きを置く）→ sh が Draft を外してコメントを投稿する。親が打つ。マージは人 |
| `close-early --reason <理由> [--no-issue]` | まだ残っているが「キリの良いところまでやった」と締める。人が端末で打つ。`--close-early`（残りを見せて y/N、未着手の子を取り消し、マーカーを置く）→ sh が残りを issue に写し、コメントを投稿する。Draft は親が片付けてから `ready` で外す |
| `fetch` | 取ってきた写しを標準出力へ。デバッグ用 |
| `origin` | origin をどう読んだか（ホスト・scheme・API の綴り・使う道具）。当たらないときの出口 |

- 道具は `gh` / `glab` があればそれ、無ければ `curl` と `GITHUB_TOKEN` / `GITLAB_TOKEN`。どちらも無ければ止まる。`jq` が要る。
  道具は起動時に絶対パスへ解いて固定する。GitHub と GitLab は origin の URL で見分ける
- origin の綴りはポートと scheme をそのまま使う（`http://localhost:8929/g/p.git` なら `http://localhost:8929/api/v4`）。
  URL に埋めた資格情報は読み飛ばし、出力では伏せる
- push の認証は git の設定側（Git Credential Manager か `credential.helper`）に置く。git のラッパースクリプトは `GIT_CONFIG_COUNT` を落とし
  `GIT_TERMINAL_PROMPT=0` で動くので、環境変数での差し替えも認証画面も通らない。GitLab の実物で分かった落とし穴は [HANDOVER.md](HANDOVER.md)、
  繰り返す道具は `tools/gitlab/probe_gitlab.py`
- `--result` を実行ファイルに直接渡せるのは人の手だけ（`CCNAVI_GUARD_TICKET_APPROVAL`）。エージェントはスクリプト 2 本を通す

**Draft を外すのは親、マージは人。** `ready` は、親を閉じられる状態（全フェーズが終わり、フィードバック計画が承認され、レビューが済んでいる）
に加えて、`wip/` が追跡から消えていて、未コミットが無く、push 済みであることを求める。取り込みは squash（GitLab ではマージリクエストに
`squash` を立てる）。順は「親を `finish` で閉じる → `rm -r wip` をコミット → push → `ready` → マーカー `ready.json` をコミットして push →
ワークツリーを片付ける」。ワークツリーの片付けはマージを待たない。

**まだ残っているが締めたいとき**は、人が端末で `close-early --reason <理由>` を打つ。作業中の子がいる間は打てない。残っているものを全部
見せてから y/N を取り、未着手の子の取り消し（理由は `close-early: <理由>`）、省略とレビュー済みのマーカー、未解決の受け入れ、残りの issue 化を行う。
`phases/<親>/close-early.json` があれば、親はフィードバック計画が無くても閉じられる。Draft は親が片付けて push したあとの `ready` で外す。

### 実績のリスク

リスクは宣言ではなく実績で測る。子を `ticket finish` で閉じるとき、その子のワークツリーで `base_sha..HEAD` の差分を数えて点を付け、
`phases/<親>/<子>.risk.json` に残す。フェーズの点は子の最大値。**HIGH 以上なら、宣言に関わらずそのフェーズは人間レビューが要る扱いになる。**
宣言が `none` なら `chat` に上がり、マージリクエストを勧める文が出る（強制はしない）。設計は [ccnavi.md](ccnavi.md) の 9.9。

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
| 定量（スクリプト） | `script: <.ccnavi/common/scripts/ の下>`（共通層。自身の層とプロジェクトの層はその層の `.ccnavi/scripts/` の下） | ccnavi が `sh` で走らせる。cwd は子のワークツリー、`CCNAVI_BASE_SHA` / `CCNAVI_HEAD` / `CCNAVI_TICKET` / `CCNAVI_PARENT` を渡し、標準出力の整数か `{"points": N, "message": "…"}` を受け取る。失敗や読めない出力は**重いほうとして扱い**、その項目の点を加える |
| 定性（サブエージェント） | `judge: <問い>` | 判定が揃うまで子は閉じられない。`finish` が問いと差分の要約を `state/risk-judge-<子>.md` に書くので、親がそれをサブエージェントに渡し、報告を `sh .ccnavi/scripts/ccnavi-ticket.sh record-risk <子> <項目> yes\|no --reason <根拠>` で記録する。判定は子の HEAD に結ぶので、HEAD が動けば取り直し |

点と加点した理由は、閉じたときの出力、フェーズの終わりの文面、`--explain`、レビューの依頼文の先頭
（「このレビューのリスク: 58 (HIGH) — 行数が多い（…）」）に出る。壊れた `risks.yml` は組み込みを使い、`--lint` と閉じたときの出力がそう言う。

### サブエージェントに渡すもの

`SubagentStart` で、cwd のワークツリーに関わる承認済みで開いている子の一覧（識別子・ワークツリー・範囲・満たしていない先行とその状態）を渡す。
親のワークツリーからならその親の子、子のワークツリーからならその子自身。それ以外には何も渡さない。判定は行き先で決まるので、これは案内でしかない。

### 参考にした運用

運用層は `参考/issue-mr-ticket-workflow`（`ticket.sh` / `worktree.sh` / `boundary.sh`）をもとにしている（[ADR-0025](docs/adr/0025-reference-workflow.md)）。

## ルールファイルが読めないとき

組み込みの既定を使って判定を続ける。止まらない（止めると壊れた設定を直す操作まで止まる）。設計は [ccnavi.md](ccnavi.md) の 5.6。
既定に入っているのは取り返しの付かない操作だけ。`rm -rf`、`git push`、`git reset --hard`、認証情報の置き場、シェルからガード自身の設定への書き込み。

| 操作 | 既定での扱い | なぜ |
|---|---|---|
| `Write` / `Edit` でルールファイルを直す | 通す | ここを止めると直す道が 1 本も残らない |
| シェルからルールファイルへ書き込む | 止める | 壊して緩い既定を使わせる順路を作らない |
| `git add` / `git restore --ours` でマージの衝突を解く | 通す | 戻すのは既にコミットされている内容で、新しい文面は書かない |

止めるのは書き込む綴りだけで、場所の名前が出ただけでは止めない。既定を使ったことは、止めた回だけでなく通した回にも伝える。

## 記録

判定した呼び出しは、通したものも含めて 1 行 1 件で追記される。全 23 欄は [ccnavi.md](ccnavi.md) の付録 B。

```
{"ts":"...","mode":"dry-run","event":"PreToolUse","tool":"Bash",
 "subject":"git push origin main","decision":"deny","enforced":false,
 "rules":["git-push"],"session":"...","ms":0.9}
```

| 欄 | 中身 |
|---|---|
| `decision` | `allow` `ask` `deny` `handover` `skip`。`handover` は権限モードに委ねた回 |
| `enforced` | 実際に適用したか。`dry-run` は `false` |
| `code` | 判定の根拠の種別。一覧は [ccnavi.md](ccnavi.md) の付録 A。よく出るのは `DENY_COMMAND_PATTERN`、`DENY_PATH`、`RULE_ASK`、`UNDECLARED`、`PARSE_UNCERTAIN`、`DENY_TICKET_SCOPE` |
| `reason` | `skip` の理由。`mode-disabled`、`event-not-checked`、`no-subject`、`nothing-to-run`、`payload-unusable`、`deadline-exceeded`、`tool-cannot-write`、`worktree-unreadable`（`detail` に理由）、`no-turn-baseline` の 9 つ |
| `tree` / `project` | 呼び出しの行き先が属するツリーとプロジェクト |
| `permission_mode` | Claude Code から来たモード。`handover` の行と合わせて読む |
| `paths` | 実行後の監視が検知した「種類とパス」 |
| `detail` | 実行後の監視では `preexisting`・`known`・`restored`（予行では `would-restore`）の件数 |
| `degraded` | コマンドを読み切れずに生の文字列で判定した回。`command-taken-as-code`、`unterminated-quote`、`unterminated-substitution`、`ambiguous-substitution`、`backquote`（後ろの 2 つは `DENY_AMBIGUOUS_FORM` / `DENY_BACKQUOTE` で止めた回） |
| `quoted` | 引用の中から切り出したコマンドにだけ当たったルールの id |
| `unwrapped` | 実行役のコマンド（`env`・`sudo`・`sh -c` など）の中のコマンド、または `cd` で移った先から見た綴り（設計 6.3.2）で当たったときの、そのコマンド。複数なら `\x00` でつなぐ |
| `fallback` | ルールファイルを読めず組み込みの既定で判定した回（`detail` にパス）。自身の層やプロジェクトの層が読めなかった回は、その層の名前（`self`、`lib` など）が入り、その層を空として判定している |
| `source` | 判定を下したルールの層。`common` / `self` / プロジェクトの名前。`rules` の id も層の名前付き（共通層は裸、それ以外は `self:worktrees`、`lib:schema`） |

```sh
jq -r 'select(.unwrapped) | .rules[]' logs/log.jsonl | sort | uniq -c
jq -r 'select(.decision == "deny") | .source' logs/log.jsonl | sort | uniq -c
```

チケットの子ごとの記録（`.ccnavi/approved/phases/<親>/<子>.risk.json` と `.judge.json`）にも項目ごとに `source` が入る。
種類を根拠に置くフェーズのマーカーには、その種類の層が入る。

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

いま効いている宣言を数え上げるには `--explain`。層ごとのルール、フェーズの種類とリスクの配点の表、承認されたチケットの作業範囲が出る。判定は行わない（REQ-DIA-01）。

```
■ rules 共通層（.ccnavi/common/rules.yml、deny 12 / ask 3 / allow 4）
  deny  guard-hooks                  Write|Edit|NotebookEdit            */.claude/hooks/*
■ rules 自身の層（.ccnavi/config/rules.yml、deny 0 / ask 0 / allow 1）
  allow self:worktrees               Write|Edit                         */.claude/worktrees/*
■ rules lib（projects/lib/.ccnavi/config/rules.yml、deny 1 / ask 0 / allow 0）
  deny  lib:schema                   Write|Edit                         */schema/*
■ phases（共通層 0 種、自身の層 7 種、lib 0 種）
  id              層        kind    title           review  scope
  design          自身の層  work    設計            mr      wip/design/*
■ risk（levels: medium 20 / high 40 / critical 70）
  id              層        加点条件            points  message
  big-diff        共通層    lines_over 300      25      行数が多い
```

（件数と中身は例。）並びは判定と同じ 共通層 → 自身の層 → プロジェクト（名前順）で、重複として捨てた定義は出ない。
読めない層は「読めない: <理由>。この層は空として扱う」、置いていない層は「この層は置いていない（無い = 空）」と出る。
`levels` の行は共通層の値。層で `levels` を書くとキーごとに小さいほうが勝つので、チケットに効く値はその `project:` の層で変わる。

### 見本で確かめる

```sh
ccnavi --test-samples .ccnavi/common/rule-samples.yml
uv run python tools/check_rules.py     # 同じことを、控えと記録を外して回す
```

見本をすべて判定に掛け、期待と食い違ったものを名指しする（1 件でもあれば終了コード 1）。見本は `deny` `ask` `allow` のタイプに置き、
タイプの名前が期待する判定になる。`subject` の `/repo` は走らせたワークスペースルートに読み替わる。
ルールを 1 件足したら見本も 1 行足し、**止めたくないものも必ず一緒に置く**。

見本はエージェントが直接書けない（「コアファイルを守る」）。下書きはワークツリーの `scratchpad/` に置き、ルールの下書きと一緒に利用者に渡す。
`/ccnavi-config` スキルがこの流れを回し、フェーズの種類とリスクの配点も見る。

### 試験の JSON

```sh
ccnavi --test Bash "cd /repo && git push" --json
ccnavi --test-samples .ccnavi/common/rule-samples.yml --json
```

`--test` と `--test-samples` の結果を JSON で出す。読み手は VS Code 拡張のルール設定画面。判定は文字で出すときと同じ関数を通る
（REQ-DIA-03）。`--json` のときは終了コードが常に 0 で、食い違いの数は `mismatches` で読む。
実例は `vscode-extension/ccnavi-board/test/fixtures/test.json` と `samples.json`。`tests/core/test_test_json.py` が同じ例で形を確かめる
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
| `unwrapped` | 実行役のコマンドが中で実行するコマンド、または `cd` で移った先から見た綴りでルールに当たったときの、そのコマンド。複数なら `\x00` でつなぐ。元の形で当たったとき・当たらなかったときは空 |
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
ccnavi --lint --flow .ccnavi/approved/flows/i0001-01.yml # 子のフロー 1 本も確かめる
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
解決したもので、`.claude/settings.json` の `env` はセッションにしか渡らないため、どこから来たかを名乗る。
`error` が 1 件でもあれば終了コードは 1、warn だけなら 0。CI が落とす対象は `error` だけでよい。
ルールの読み込みは判定と同じ経路を使う。

**ルール**

| 深刻度 | 拾うもの |
|---|---|
| error | ルールファイルが読めない、YAML として壊れている、版番号が違う |
| error | 文面（`deny` だけ）・`match`・`glob` を欠いたルール、`glob` と `regex` の両方があるルール |
| error | `ask` か `allow` に `message` を書いたルール（`ask` の文面は人の確認ダイアログにしか出ず、`allow` の文面はどこにも出ない。ルールは効いたまま） |
| error | 組み立てられない正規表現、読み込み時に弾いている先読み・後読み・後方参照 |
| error | `deny` が空（何も止めないガードは、入っているように見えて入っていない） |
| error | `additionalContextFile` / `additionalContextOnceFile` が絶対パスか `..` で上に出るパス（実行時も読まない） |
| warn | `allow` が空（言及の無い呼び出しが全部、権限モードへ渡る） |
| warn | `id` の無いルール、`id` が重複するルール |
| warn | 判定が対象を取り出せないツールを `match` に書いたルール |
| warn | 何にでも当たる、または選択肢が 3 つ以上ある `allow` に `additionalContext`（か `additionalContextFile`）を書いたルール（当たるたびに同じ文が積まれる。`additionalContextOnce` は文脈ごとに 1 度なので咎めない） |
| warn | `additionalContextFile` / `additionalContextOnceFile` が指すファイルが無い（作るまで何も足さない）、または先頭 4000 文字を超える（先頭だけが届き、切ったことを添える） |

**設定とモード**

| 深刻度 | 拾うもの |
|---|---|
| error | `.claude/settings.json` の `env` が `CCNAVI_MODE=disable` を宣言している |
| error | `CCNAVI_GUARD_TICKET_APPROVAL` / `CCNAVI_TICKET_CONTROL` が 2 値として読めない値（`enable` として扱って動く） |
| error | `.claude/settings.json` の `env` の `CCNAVI_BIN_PATH` が指す先が在るのに実行できない（hook が 126 で起動せず、何も判定していない。POSIX だけで見る。Windows は実行ビットを持たない。書いた綴りをそのまま見て、`.exe` は補わない） |
| warn | モードが `disable` / `dry-run`、あるいはモードとして読めない値 |
| warn | 読めない `CCNAVI_RESTORE_IF_DENY` / `CCNAVI_GUARD_CORE_FILES` の値 |
| warn | 守る働きを持つ門が止めない値になっている（`CCNAVI_GUARD_CORE_FILES` と `CCNAVI_RESTORE_IF_DENY` が `disable` か `dry-run`、`CCNAVI_GUARD_UNWATCHED` と `CCNAVI_GUARD_TICKET_APPROVAL` が `disable`） |
| warn | 上書き設定ファイル（`ccnavi.settings.local.json`。ccnavi 自身のソースツリーだけで読む。設計 4.3）が読めない |
| warn | `CCNAVI_TICKET_CONTROL=disable`（チケットの範囲も HITL ポイントも効かない） |

**層**

| 深刻度 | 拾うもの |
|---|---|
| error | 自身の層かプロジェクトの層のファイルが壊れている（その層を空として扱っている） |
| info | 裸の `id` と全欄が一致する宣言を、後ろの層で捨てた（ルール / フェーズの種類 / 配点） |
| warn | ルールの同じ `id` が層をまたいで中身違いで在る（両方効いている） |
| error | フェーズの種類の同じ `id` が層をまたいで中身違いで在る、表示名が層をまたいで重なる、`overlap` / `requires` が合成後の集合に居ない種類を指す（その層を空として扱っている） |
| warn | 配点の同じ `id` で中身が違う（両方数え、後ろの層は `<層>:<id>`） |
| error | 合成後の `levels` が `medium <= high <= critical` になっていない（その層を空として扱っている） |
| error | 配点の `script:` が層の外を指す（共通層から `.ccnavi/scripts/`、自身の層やプロジェクトの層から `.ccnavi/common/scripts/`）、または指す先が git プロジェクトルートに無い |
| error | `id` にコロンを書いた宣言（ルール / フェーズの種類 / 配点） |
| warn | ワークツリーの ccnavi ディレクトリに、元リポジトリに無いファイルがある（統合されるまで効かない）。承認済みの領域の下は数えない（そのツリーの版が読まれる） |

層のファイルが無いことは言わない（無い層は空で、正常な形）。

**hook の登録とスクリプト**

| 深刻度 | 拾うもの |
|---|---|
| warn | `PostToolUse` / `SubagentStart` / `SubagentStop` に ccnavi が登録されていない |
| warn | 登録はされているが git の作業ツリーではない（監視が何も検知しない） |
| warn | `.ccnavi/scripts/ccnavi-{ticket,review,git}.sh` が無い |

登録の検査は `.claude/settings.json` しか見ない。そのファイルが無いときは何も言わない（利用者ごとの設定は見えないため）。

**チケットとフェーズ**（チケット制御が有効なときだけ）

| 深刻度 | 拾うもの |
|---|---|
| error | 承認済みチケットの置き場への `Write` / `Edit` をルールが止めていない（承認の意味が消える） |
| error | 同じ識別子が複数の置き場にある、子の識別子が `<親>-<2 桁連番>` の形でない、連番が重なる |
| error | 孫を持つ子 |
| warn | 範囲の超過がある子（親の範囲・フェーズの種類の `scope` を超える項、regex の項）。判定がその上限で切り詰めて止めるので、CI の終了コードは落とさない（承認と同じ扱い） |
| error | ワークツリーの元リポジトリと承認済みチケットの `project:` が違う |
| error | 親が計画を持つのに `phases.yml` が読めない、`phases.yml` / `risks.yml` 自身の誤り |
| error | 承認済みチケットが読めない |
| warn | 未承認の提案がある |
| warn | `predecessors` を満たしていない（`done/` に無いか取り消し済み）のに着手している子 |
| warn | チケットの無いワークツリー、識別子と名前の一致しないワークツリー |
| warn | ワークツリー側に置かれた承認済みチケット（読まれない） |
| warn | 承認済みチケットはあるがワークツリーが無い（範囲が効かない） |
| warn | リモートの種類に合うトークン（`GITHUB_TOKEN` / `GITLAB_TOKEN`）が無い、`origin` が読めない |
| warn | ワークツリーでもワークスペースルートでもないのに `.claude/` を持つディレクトリがある |
| warn | 下書きの置き場（`scratchpad/`）に追跡されているファイルがある、または `scratchpad/` が git で無視されていない（ワークスペースと各プロジェクトのそれぞれ。追跡されていると、実行後の監視が下書きを範囲外として報告しはじめる） |

**プロジェクト**（`projects/` があるときだけ）

| 深刻度 | 拾うもの |
|---|---|
| warn | `projects/` がワークスペースの `.gitignore` に入っていない |
| error | 予約名（`common` / `self`。綴りの大文字小文字は問わない）のプロジェクトがある |
| warn | プロジェクトが `.claude/` を持っている |

## lint の JSON

```sh
ccnavi --lint --json
```

`--lint` と同じ苦情を、同じ深刻度で 1 つの JSON にまとめて出す。終了コードも同じ（error があれば 1）。読み手は VS Code 拡張の
プロジェクト管理画面と、`--flow` で渡したフローの苦情（`(flow)`）を読むフロー編集画面。この JSON の形は拡張との契約なので、変えるときは版を上げる。

| 鍵 | 何 |
|---|---|
| `version` | 形の版。整数（いま 1）。欄を足すだけなら上げない |
| `root` / `rules` / `mode` / `ticket_control` | 何を見て検証したか。人向けの文面が先頭に出すものと同じ |
| `projects[]` | 検証の対象になったプロジェクトの名前 |
| `problems[]` | 苦情 1 件ずつ。`{severity, where, detail}`。`severity` は `error` / `warn` / `info`。`where` は人向けの文面で `error:` の後ろに出る場所（`(projects/lib) rule-id`、`(self) (phases) design`、`--flow` で渡したフローなら `(flow)` など。ファイル全体への苦情なら空） |
| `errors` / `warns` / `infos` | 件数 |
| `flow` | `--flow` を渡したときだけ在る。`{path, data}`。`path` は確かめたファイルの絶対パス、`data` は実行ファイルが読んだ中身（下）。読めなければ `null` |

### 子のフローを保存せずに確かめる

```sh
ccnavi --lint --json --flow /tmp/flow.yml
```

`--flow <パス>` は、子チケットのフロー（設計 9.3.1）1 本を、`SubagentStart` が読むのと同じ読み手・同じ検査
（大きさ、リンク・ふつうのファイルでない・ハードリンク、UTF-8 として読めない、YAML として読めない、別名、形）で読む。
読めなければ場所 `(flow)` の error で言い、`detail` は渡したパスで始まる。無いファイルも error。VS Code の拡張の
フロー編集画面が、開くときと保存の前に本文を一時ファイルに書いて渡し、`(flow)` の苦情と `flow.data` を読む
（ほかの設定の苦情ではフローを止めない）。

`flow.data` は読めた中身を JSON にしたもの（`flow.as_json`）。PyYAML（YAML 1.1）の読みのままで、`0755` は 493、
`yes` は `true`、`0o17` は文字列になる。JSON にそのまま載らない値は `{"$ccnavi": <種類>, ...}` の印にする。

| 読めた値 | `data` での形 |
|---|---|
| 文字列・真偽値・null・並び | そのまま |
| 整数（`±(2**53 - 1)` まで） | 数 |
| それより大きい整数 | `{"$ccnavi": "int", "text": <十進>}` |
| 浮動小数 | `{"$ccnavi": "float", "value": <数>}`。JSON では 1 と 1.0 の区別が消えるので包む。有限でなければ `"text"` に `inf` / `-inf` / `nan` |
| キーが全部文字列の辞書 | オブジェクト。キーに `$ccnavi` があれば下の `map` |
| キーが文字列でない辞書 | `{"$ccnavi": "map", "items": [[キー, 値], ...]}` |
| 日付・日時・バイト列（`!!binary`）・集合（`!!set`）・組（`!!omap` / `!!pairs`） | `{"$ccnavi": "date" \| "datetime" \| "bytes" \| "set" \| "tuple", "text": <綴り>}`。ほかは `"other"` |

フロー編集画面は、開くときに自分の読み手（`yaml`、YAML 1.2）で読んだ中身とこれを見比べ、食い違えば開かない。
保存の前には、書き出す本文をこれに掛けて、画面が書こうとした中身と同じに読まれるときだけ書く。値の意味を
拡張が自分で決めないため（ADR-0035）。
読むのは `--lint` だけで、診断の外では落とし、`--test` / `--test-samples` / `--explain` でも「`--lint` でだけ読む」と
言って落とす。フローは判定の材料にならないので、層の置き場の門（下）とは別に数える。

### 1 つのプロジェクトのルールを保存せずに試す

```sh
ccnavi --test Write projects/lib/src/a.py --json --project-rules-file lib=/tmp/edited.yml
ccnavi --lint --json --project-rules-file lib=/tmp/edited.yml
ccnavi --lint --json --project-phases-file self=/tmp/phases.yml
ccnavi --lint --json --project-phases-file lib=/tmp/phases.yml
```

- `--project-rules-file <名前>=<パス>` は、その名前のプロジェクトのルールファイルの代わりに `<パス>` を読む
- `--project-phases-file <名前>=<パス>` は、その名前の層（`self` は自身の層）のフェーズの種類の代わりに `<パス>` を読み、共通層の種類と合成して確かめる。
  共通層の種類は `--phases` で差し替える。配点（risk）の層には差し替えがまだ無い
- 層の置き場を動かすフラグ 7 本（`--rules` / `--phases` / `--risk` / `--projects` / `--project-home` と上の 2 本）は、`--test` / `--test-samples` /
  `--lint` / `--explain` でだけ効く（ADR-0067）。診断の外（hook からの判定、`ticket` / `review` の副命令）に渡すと無視し、標準エラーにその旨を出す。
  守る対象（コアファイル）も差し替えを見ない
- `--root` と `--cwd` は 1 度しか渡せない（2 本目が在れば止める）。sh が自分のぶんを先に置くので、後勝ちの上書きを防ぐため

## ボードの JSON

```sh
ccnavi --explain --json
```

`--explain` のうちチケットに関わる部分を JSON で出す。読み手は VS Code の拡張「ccnavi ボード」。拡張はこれを並べるだけで、提案やマーカーを
自分では読まない。ネットワークには出ない。`version` が拡張の知っている版（いま 1）と違えば、拡張は読まずに版の違いを伝える。
実例は `vscode-extension/ccnavi-board/test/fixtures/board.json`。`tests/ticket/test_board.py` が同じ例で形を確かめる
（形を変えたら `CCNAVI_BOARD_FIXTURE=1` を付けてそのテストを走らせ、例を書き直す）。

| 鍵 | 何 |
|---|---|
| `version` | 形の版。整数 |
| `root` / `generated_at` | ワークスペースルートと、出した時刻 |
| `settings` | `ticket_control`（チケット制御を使うか）/ `tickets`（提案の置き場、相対）/ `approved`（承認済みチケットの置き場）/ `projects`（プロジェクトの置き場） |
| `trees[]` | ワークスペースルート・プロジェクト・ワークツリー。`{name, root, project, kind}`。`kind` は `main` / `project` / `worktree` |
| `layers[]` | 層ごとの宣言。並びは 共通層 → 自身の層 → プロジェクト（名前順）。`{name, rules, phases, risk, phases_file}`。`name` は `common` / `self` / プロジェクトの名前。`rules` は `{path, unreadable, deny, ask, allow}` で、各タイプはその層から実際に判定へ入ったルール `{id, section, source, match, kind, written, pattern, message}`（重複で捨てたものは入らない）。`phases` はその層のファイルに書いてある種類 `{id, source, kind, title, review, scope}`、`risk` は `{path, unreadable, factors}`、`phases_file` は `{path, unreadable}`。phases と risk は合成前の、その層のぶんだけ |
| `projects[]` | プロジェクトの名前 |
| `problems[]` | 読めなかった提案や承認済みチケットの説明。あっても他は出す |
| `pending_approval[]` | `--approve` で承認の対象に入る識別子（承認済みチケットの無い提案と、親の改版） |
| `tickets[]` | 識別子ごとに 1 件。提案と承認済みチケットのどちらか一方しか無くても出す |
| `parents[]` | 承認済みチケットのある親ごとの局面とフェーズ。承認前の親はフェーズを持たないのでここに無い |

`tickets[]` の 1 件。

| 鍵 | 何 |
|---|---|
| `ticket` / `parent` / `phase` / `title` / `project` / `issue` / `predecessors` / `human_review` | 提案（無ければ承認済みチケット）の frontmatter から |
| `predecessors_unmet[]` | 満たしていない先行（ADR-0088）。`{ticket, state, label}`。`state` は `todo` / `doing` / `review`（先行が閉じれば満たす）と `cancelled` / `missing` / `scattered` / `self` / `ancestor` / `cycle`（待っても満たさない）、`label` は人向けの言葉（「作業中（doing/）」など）。空でなければ承認と着手（`start`）が止まる（書き込みと `finish` は止まらない）。先行が無い子・親・閉じたチケットは空。ボードはこれで「先行待ち」のバッジを出し、自分では数えない |
| `proposal` | `{state, tree, tree_root, path}`。権威のあるツリー（親のツリー。無ければ元ツリー）の提案の置き場で見つけたもの。`state` は `todo`（承認待ち）/ `review`（レビュー待ち）。`doing/` `done/` に在るときは `null` |
| `copy` | `{status, approved_at, source_tree, path}`。`status` は `none`（未承認）/ `open`（`doing/`）/ `review`（`wip/proposals/review/`）/ `closed`（`done/`） |
| `blocked` | 空でなければ「読めるが信じられない」理由（ADR-0058）。判定はこのチケットのワークツリーへの書き込みを `DENY_TICKET_BLOCKED` で全部止める。`status` は `open` のままなので、止まっていることはこの欄でしか分からない |
| `worktree` | `{exists, path, project}`。`.claude/worktrees/<識別子>` が本物のワークツリーか（設計 9.5 の相互参照） |
| `started_at` / `completed_at` / `base_sha` / `cancelled_at` / `cancel_reason` | スクリプトが書く欄 |
| `seen_in[]` | 同じ識別子が写っている場所の全部。`{tree, state, path}`。子のワークツリーは親のブランチから切るので、親の提案が写っているのが普通 |
| `scattered[]` | どれが本物か決まらない写りの全部。`{tree, state, path}`。決まっていれば空。権威のツリー（親のツリー → 元ツリーの順。ADR-0073）で畳んで 2 つ以上残り、その残りが 2 つの置き場にまたがるか同じ置き場に重なるときに入る。状態の操作が「複数の場所にある」で止まる条件と、`--lint` が ERROR で言う条件と同じ。`seen_in` の数は食い違いを意味しない |
| `flow` | 子のフロー（設計 9.3.1）。親と、フローが無い閉じた子（終わった・取り消した）は `null`（ボードはこのとき「フローを作る」を出さない）。`{path, rel, tree, exists, linked, locked}`。`path` は読む先の絶対パス（権威のツリー＝承認済みチケットが在るツリーの版だけ。子のワークツリーの写しは読まない。承認の前は提案が在るツリーで、承認でフローもチケットと一緒に動く）、`rel` はツリーのルートからの相対（承認済みの領域の固定の置き場 `.ccnavi/approved/flows/<子>.yml`。中身は YAML）、`tree` はそのファイルを持つツリーのルート、`exists` はファイルが在るか、`linked` はファイルかツリーのルートからそこまでの途中がシンボリックリンクか（真なら読まないし書かない）、`locked` は判定がいまその書き込みを `DENY_TICKET_FLOW_LOCKED` で止めているか（着手中）。読むのは承認済みチケット（無ければ提案）の欄。ボードは `locked` をそのまま写し、自分で組み直さない |
| `risk` / `judge` | 子の記録 `phases/<親>/<子>.risk.json` と `.judge.json` の中身。無ければ `null` |
| `history[]` | 状態が動いた跡（ADR-0086）の新しい側 20 件を古い順に。`.ccnavi/approved/events/<識別子>.ndjson`（権威のツリー＝承認済みチケットが在るツリーの版）の 1 行ずつで、`{at, ticket, kind, from, to, via, ...}`。`at` は UTC の ISO 8601、`kind` は `approved` / `revised` / `raised` / `started` / `finished` / `cancelled` / `settled`（置き場が動いたもの）と `phase-mark` / `phase-reopened` / `parent-mark`（マーカー。親の跡に残り、`from` / `to` は `null` で `phase` / `mark` を持つ）、`from` / `to` は置き場の名前（`todo` / `doing` / `review` / `done`）、`via` は `cli`（sh の副命令）/ `terminal`（人が端末で）/ `board`（ボード）/ `hook`。種類ごとに `phase`・`mark`・`reason`・`base_sha`・`tree`・`followup_of`・`cleared` が付く。**補助で、状態の正は置き場の欄**。跡が無ければ空。読めない行があれば飛ばして `problems[]` で言う |

`parents[]` の 1 件。

| 鍵 | 何 |
|---|---|
| `ticket` / `closed` / `stage` | 識別子、閉じた承認済みチケットか、いまの局面（設計 9.7 の文。閉じた親は空文字） |
| `plan` / `feedback` | 全体計画とフィードバック計画（`null` は未計画） |
| `close_early` / `ready` / `closed_record` | 親のマーカー `close-early.json` / `ready.json` / `closed.json` の中身。無ければ `null`。`closed.json` は親を閉じたときに置かれ、どのフェーズをどこで見たか（`reviews`）を持つ |
| `accepted_threads[]` | 人が受け入れた未解決スレッド |
| `phases[]` | 番号順。`{number, type, title, label, state, tickets, states, marks, review_required, review_kind, gate_closed, review_waiting, deferred, review_at, covers, risk, risk_escalates, risk_line}`。`state` は `planned`（子がまだ無い）/ `active` / `ended`。`marks` はマーカーの種類 → 中身。`review_kind` は人がどこで見るか（`none` / `chat` / `mr`）。`gate_closed` は判定が使うのと同じ値で、レビューが済むまで止めているかを言う（人に見せる名前は「レビュー準備中」「レビュー待ち」）。`review_waiting` は依頼を出したのに止まったまま（人のレビュー待ち）で、ボードはこれを写すだけで組み直さない。`chat` のフェーズは依頼を出さないので常に `false`。このセッションで見る待ちは `review_kind` と `gate_closed` で読む |

## 承認の JSON

```sh
ccnavi --approve --preview --json [<絞り>...]           # 一覧を見る（承認済みチケットは置かない）
ccnavi --approve --preview --verify --json [<絞り>...]  # 承認できる状態かを確かめる（同上）
ccnavi --approve --yes <識別子,…> --digest <値> --json [<絞り>...]    # 見せた一覧をそのまま承認する
```

VS Code の拡張が、承認をボードのオーバーレイで行うための形。承認の対象を組むのは `--approve` と同じ関数で、`--explain --json` の
`pending_approval` と答えが割れない。実例は `vscode-extension/ccnavi-board/test/fixtures/approve-preview.json` ほか。
`tests/ticket/test_approve_json.py` が同じ例で形を確かめる（形を変えたら `CCNAVI_BOARD_FIXTURE=1` を付けてそのテストを走らせ、例を書き直す）。
`version` が拡張の知っている版（いま 1）と違えば、拡張は読まずに版の違いを伝える。

`--preview` の答え。

| 鍵 | 何 |
|---|---|
| `version` | 形の版。整数。`--yes` と同じ番号 |
| `root` / `generated_at` | ワークスペースルートと、出した時刻 |
| `batch[]` | 承認の対象。`{ticket, title, parent, phase, revision, tree, path, overflow}`。`parent` と `phase` は子だけ（親は `null`）。`revision` は親の改版。空なら承認待ちが無い |
| `batch[].overflow[]` | 範囲の超過（親の範囲・フェーズの種類の `scope` を超える項、regex の項）の説明。文字列の並びで、無ければ `[]`。承認は通るが、判定で止まる |
| `text` | 承認画面の本文そのまま。拡張はこれを等幅で並べ、項目には分けない |
| `digest` | 見せた中身の指紋。`--yes` の `--digest` にそのまま渡す |
| `rejected[]` | 承認の対象にしない提案。`{ticket, problems[]}`。載るのは形の壊れた提案（親が承認されていない、計画に無い番号、順序、先行（`predecessors`）が `done/` に無いか取り消し済み（ADR-0088）など）だけで、範囲の超過だけの子は `batch[]` に載る |
| `problems[]` | 読めない提案や承認済みチケットの説明 |

`--preview` 自身は、対象にしない提案があっても 0 で返る。答えが要るときだけ `--verify` を足す。
`--verify` の答えは `--preview` の答えに `verify` を足したもの。終了コードが答えそのもの（**0 = はい、3 = いいえ**。1 は使い方か設定の誤り）。

| 鍵 | 何 |
|---|---|
| `verify.ok` | 真偽。`--approve` を打てば、指定したもの（指定が無ければ承認待ち全部）がそのまま承認の対象に入るか |
| `verify.reason` | 答えの理由の名前。全部入るなら `ok`、入らないなら `refused`（絞りが通らない）/ `nothing-pending`（承認待ちが 1 件も無い）/ `rejected`（承認の対象にしない提案がある）。読めない提案（`problems[]`）はここに出ない |

`--verify` は、`--approve` が落とさない範囲の超過（`batch[].overflow`）と読めない提案（`problems[]`）では「いいえ」にしない。どちらも本文には出す。
承認で落ちるものは `ccnavi --lint` も同じ関数で名指しする（ただし「まだ承認できない」子は `--lint` では warn。先行が閉じれば通る子もここに入る。取り消し済み・どこにも無い・複数の場所にある・自分自身・自分の親・輪になった先行は error）。

`--yes` の答え。値は `--preview` の `batch[].ticket` をカンマで並べたもの。

| 鍵 | 何 |
|---|---|
| `version` | 同上 |
| `approved[]` / `copies[]` | 承認した識別子と、置いた承認済みチケットのパス |
| `lines[]` | 端末なら標準出力に出ていた行（マーカーを消したことなど） |
| `prompt` | Claude Code に渡す文。hook が次の `UserPromptSubmit` / `PreToolUse` で渡す文と同じ |
| `mismatch` | 一覧か中身が変わっていたとき。`{expected[], current[]}`、指紋が違えば `digest: {expected, current}`。このとき承認済みチケットは置かれず、終了コードは 1 |
| `partial` | 置いている途中で止まったとき（書けない、など）。`{placed[], ticket, reason}`。`placed[]` はそこまでに承認済みチケットに入ったぶん（新規は置いた、改版は書き換えた）、`ticket` は止まったところ、`reason` は理由、`lines[]` は止まるまでに出た行（端末なら標準出力に出ていたぶん）。**置いたものは戻さない**ので、どこまで進んだかをそのまま返す。終了コードは 1。承認済みチケットを運ぶ sh は送られていない |

- `--yes` は端末を求めない代わりに、見せた一覧と今の一覧が同じで、`--digest` の値（大文字小文字は問わない）が承認のときに読み直した中身の指紋と
  合うことを求める。`--digest` が無ければ承認せず、終了コードは 1
- 指紋が覆うのは承認画面の本文と、束のチケットごとに書き出す中身（新規は提案の frontmatter と本文、改版は計画を差し替えた承認済みチケット。
  `ccnavi_approved` は除く）
- エージェントが Bash や PowerShell で `--yes` を打つ道は、組み込みの deny（`builtin-guard-ticket-approval`）が塞ぐ。`--preview` は通す
- 後ろの `<絞り>` は `ccnavi --approve <識別子>...` と同じで、承認の対象を狭める。`--yes` の値（人が見た識別子）とは別に渡す。
  比べるのは「その絞りで今できる一覧」と「見た識別子」

## 残った指摘の JSON

ボードの「決める」が読み書きする形。拡張は sh（`ccnavi-review.sh decide`）を子プロセスで打ち、sh が取ってきた写しを実行ファイルに渡す。
版は `version`（今は 1）で、承認の JSON と別に数える。

`decide <N> --preview`（実行ファイルは `--reviewed N --accept-unresolved --preview --json`）は何も置かない。

```json
{"version": 1, "parent": "i0050", "phase": 2,
 "mr": {"number": 7, "url": "https://…/merge_requests/7"},
 "can_issue": false,
 "threads": [{"key": "https://…#note_1", "url": "https://…#note_1", "path": "src/a.py", "line": 3, "body": "…"}],
 "digest": "<64 桁の 16 進>"}
```

- `key` は選択を結ぶ鍵（URL か、無ければスレッドの id）。受け入れの控えにもこの綴りが入る
- `can_issue` は issue に回せるか（フィードバック計画が承認されたあとだけ真）
- `digest` は見せた指摘の指紋。親・フェーズ・マージリクエストの番号・`can_issue`・各指摘の鍵と場所と本文から組む

`decide <N> --choices <JSON> --digest <指紋>`（実行ファイルは `--yes <JSON> --digest <指紋> --json`）は
`{"<key>": "keep" | "fix" | "issue", …}` を受け、見せた指摘の全部に 1 つずつ付いていることを求める。

```json
{"version": 1, "ok": true, "parent": "i0050", "phase": 2,
 "reviewed": false, "followup": "i0050-03",
 "kept": ["…"], "fix": ["…"], "issue": [],
 "issue_draft": "", "prompt": "[ccnavi] 利用者が…",
 "issue_url": "", "warning": ""}
```

- `reviewed` はレビュー済みになったか（直す指摘が無ければ真）、`followup` は起こした続きの子
- `issue_url` と `warning` は sh が足す。置いたあとの投稿（issue とコメント）で起きたことで、置いたことは戻さない
- 見せた指摘と今の指摘が違えば、何も置かずに `{"version": 1, "ok": false, "mismatch": true, "digest": {"expected", "current"}}` を
  返して 1 で終わる

## 生の git は止めてラッパースクリプトへ寄せる

`.ccnavi/scripts/ccnavi-git.sh` は安全な git だけを通し、出力を抑えて結果だけを返す。生の `git` はルールで拒否し、拒否の文面からここへ誘導する。

```sh
sh .ccnavi/scripts/ccnavi-git.sh status
sh .ccnavi/scripts/ccnavi-git.sh log -p
sh .ccnavi/scripts/ccnavi-git.sh --help    # 通す形と通さない形の一覧
```

- **出力がコンテキストに丸ごと載るのを止める。** 全量は `logs/` に残し、標準出力へは要約 1 行と先頭 40 行だけを返す
- **オプションの穴を入口 1 本で塞ぐ。** `permissions.allow` の `Bash(git diff:*)` は前方一致でしかないので、サブコマンドごとに使ってよい形をここで決める

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
- `push` は**居るブランチを、そのままの名前で送る形だけ**通す。`--force`・`--force-with-lease`・`--delete`・`--all`・`--mirror`・`--tags`、
  別の綴りへ送る refspec（`HEAD:main` など）は通さない。`main` `master` `develop` `release` `release/*` へ直接は送れない。マージは人の側に残す
- 送るのは親だけ。子チケットのワークツリーからの push はラッパースクリプトが拒み、サブエージェントからの push は hook が拒む（`DENY_SUBAGENT_TICKET_OP`）
- サブコマンドより前のオプション（`git -c ...` など）は 1 つも受け取らない。`GIT_CONFIG_COUNT` と `GIT_EXTERNAL_DIFF` は実行前に消す

これは事故と浪費を減らすためのもので、敵対的な回避への防御ではない。実行役のコマンドの一覧に無いもの（`python -c`・`ssh` など）に埋めれば
hook の文字列一致は外れる。そこまで塞ぐなら `permissions.deny` か sandbox が要る。

記録は `logs/git-<日時>-<pid>.log` に成功でも失敗でも全量を書き、新しい順に 50 本だけ残す。`logs/` は `.gitignore` に入れ、コミットしない。

## 構成

| 場所 | 中身 |
|---|---|
| `main.py` | 配布物の入口。PyInstaller が渡すスクリプト |
| `ccnavi/hookio.py` | stdin の payload の解釈と、stdout に返す応答の組み立て |
| `ccnavi/rules.py` | ルールファイルの読み込みと検証 |
| `ccnavi/builtin.py` | ルールファイルを読めないときの組み込み既定 |
| `ccnavi/globmatch.py` | glob から正規表現への翻訳 |
| `ccnavi/shellread.py` | コマンド文字列のうち実際に実行される部分の切り出し |
| `ccnavi/settings.py` | 環境と設定ファイルからの設定解決 |
| `ccnavi/gitstate.py` | 作業ツリーで実際に何が変わったかを git から読む |
| `ccnavi/post.py` | 実行後の監視。保護領域の変更の検知、差し戻しの文、復元 |
| `ccnavi/ticket.py` | チケットの読み込みと、そこが宣言する作業範囲。親子の部分集合の検査 |
| `ccnavi/tree.py` | ワークツリー（git worktree）の特定。判定の鍵はファイルの行き先 |
| `ccnavi/approval.py` | 承認済みチケット、フェーズのマーカー、子ごとの記録、承認の画面 |
| `ccnavi/risk.py` | 実績で測るリスク。`risks.yml` の読み込み、差分の計測、スクリプトと定性項目 |
| `ccnavi/phase.py` | フェーズの終わりと HITL ポイント。提案から承認済みチケットへの同期 |
| `ccnavi/phasetypes.py` | フェーズの種類の定義（`phases.yml`）の読み込みと検証 |
| `ccnavi/review.py` | レビューの依頼と確認。作業ツリーの中の前提検査と、sh が渡す写し（JSON）の判定。ネットワークには出ない |
| `ccnavi/ops.py` | チケットの状態を動かす `ticket start / finish / cancel / record-risk`。閉じるときに実績のリスクを数える |
| `ccnavi/audit.py` | 1 行 1 件の追記記録 |
| `ccnavi/lint.py` | 設定とルールの検証。判定を行わない |
| `ccnavi/diagnose.py` | 判定を実行せずに試す `--test` と `--explain` |
| `ccnavi/cli.py` | 引数の解釈と振り分け。`ticket` / `review` の副命令を ops / review へ渡す |
| `ccnavi/events.py` | hook のイベントごとの手順。1 回の起動で何が起きるかはここを上から読む |
| `ccnavi/judge.py` | 実行前の判定。通す・聞く・止めるを決める |
| `ccnavi/reasons.py` | 判定に添える文面と理由コード |
| `ccnavi/ruleload.py` | この呼び出しに当てるルール集合を決める（ワークスペース・プロジェクト・その和） |
| `ccnavi/subagent.py` | SubagentStart / SubagentStop。開いている子の案内と、範囲外の変更の差し戻し |
| `ccnavi/ctxfile.py` | 当たったルールがモデルへ渡す文（additionalContext）。ファイルの本文と once の控え |
| `ccnavi/selfguard.py` | ccnavi 自身の設定ファイルと実行ファイルの控えと復元 |
| `ccnavi/modes.py` | enable / dry-run / disable の 3 値と終了コード。モードの解決 |
| `ccnavi/gitcmd.py` | git を 1 回起こす |
| `ccnavi/fsio.py` | ファイルの読み書きの型。控え・マーカー・承認済みチケット・下書きが全部これを通る |
| `build.py` | 配布物の組み立て。`dist/ccnavi/` を `.ccnavi/bin/<os>-<arch>/` へ写す |
| `ccnavi/platformtag.py` | 機械の語（`<os>-<arch>`）。組み立ての目印と、振り分けの sh が起動する実体の探し方 |
| `scripts/ccnavi-setup.sh` | 対象プロジェクトに設定を書き、実行ファイルとルールとスクリプトを配る |
| `.claude/hooks/lint-py.sh` / `test-py.sh` | このリポジトリ自身の開発用 hook。整形と検査、ターンの終わりのテスト |
| `.claude/hooks/mark-ext.sh` / `test-ext.sh` | 同じく拡張のぶん。触ったことの書き残しと、ターンの終わりに関わるグループだけ回すテスト |
| `vscode-extension/ccnavi-board/scripts/test-groups.js` | 拡張のテストの入口。触ったファイルから回すグループを決め、コンパイルは 1 回で済ませる |
| `.claude/skills/ccnavi-config/` / `commit/` | 設定 3 本を足す・確かめるスキルと、コミットの手順 |
| `.ccnavi/scripts/ccnavi-launcher.sh` | hook が起動する振り分けの sh（モード 100755）。原本と配布先で同じ綴り。1 つ上の `bin/<os>-<arch>/` から、この機械の実行ファイルを選ぶ。無ければ 127 |
| `.ccnavi/scripts/ccnavi-git.sh` | 安全な git だけを通し、出力を抑えて結果だけ返すラッパースクリプト |
| `.ccnavi/scripts/ccnavi-ticket.sh` | チケットの状態を動かす。親だけが呼ぶ。本体は `ccnavi ticket` |
| `.ccnavi/scripts/ccnavi-review.sh` | レビューの依頼と確認。親だけが呼ぶ。本体は `ccnavi review` |
| `.ccnavi/scripts/ccnavi-approve.sh` | 承認し、`ccnavi-push-approved.sh` で運ぶ。人が端末で打つ。本体は `ccnavi --approve` |
| `.ccnavi/scripts/ccnavi-push-approved.sh` | 承認済みチケットの置き場だけをコミットし、保護されたブランチでなければ親のブランチへ push する。人が打つ（エージェントからは止まる）。端末の `ccnavi-approve.sh` とボードの承認のあとに呼ばれる |
| `.ccnavi/scripts/ccnavi-fetch.sh` | セッションの頭で親ブランチと、ワークツリーの起点になるデフォルトブランチを取ってくる。進めるのは fast-forward だけ（ADR-0060） |
| `.ccnavi/scripts/ccnavi-clean.sh` / `ccnavi-clean.js` | ワークツリー 1 本の生成物（node_modules・.venv など）を消す。`worktree remove` の前に打つ。node が無ければ sh で同じものを消す。配らない |
| `tests/` | 受入テスト。内部の関数は呼ばず、標準入出力と終了コードだけを見る |
| `tools/gitlab/` | 実物または代役の GitLab に sh と実行ファイルを当てて 1 周する、人が手で回す道具。自動テストは呼ばない |
| `tests/fixtures/` | テスト用のルール（`rules.yml`、言及の無い呼び出しを見る `rules-undeclared.yml`） |
| `.ccnavi/common/rules.yml` / `risks.yml` | このリポジトリ自身の共通層の設定（共通層に `phases.yml` は置かない） |
| `.ccnavi/config/phases.yml` | このリポジトリ自身の層のフェーズの種類 |
| `.ccnavi/common/rule-samples.yml` | ルールが何を止めて何を通すかの見本 |
| `tools/check_rules.py` | 見本をぜんぶ判定に掛ける |
| `vscode-extension/ccnavi-board/` | VS Code 拡張。ボード・ルール設定・リスク管理・プロジェクト管理の画面 |
| `docs/adr/` | 設計判断の記録 |

## 配布物の条件

- 実行ファイル 1 つとその同梱物 1 フォルダ。使う側にランタイムの導入を求めない
- 実行前の判定の間に外部プロセスを起こさない（期限に達した hook は素通りするので、遅さがガードの穴になる）
- 実行後の監視は git を 1 回起こす
- ネットワークへは出ない。起こす外部プロセスはローカルの git と、子を閉じるときにリスクの配点の `script` 項目を走らせる `sh` だけ
- 作業ディレクトリに依らず同じ入力に同じ判定を返す
- 実行時の third-party 依存は PyYAML 1 本。読むのは `safe_load` に限り、`load` はエージェントが書けるファイルに向けては使わない
