---
type: design
title: 5. ルール
description: ルールファイルの書式、タイプの強さ、記法、対象の取り出し、どのルールファイルを引くか、読めないとき
tags: [design-doc, rules]
keywords: [ルール, rules.yml, deny, ask, allow, glob, regex, when, except, 記法]
---

[設計書の入口に戻る](../design.md)

## 5. ルール

### 5.1 書式

ルールファイルは YAML で、`version: 1` と `deny` / `ask` / `allow` の 3 つのリストを持つ。
1 件は当てるツール（`match`）・探すもの（`glob` `regex` `when` のどれか 1 つ）・見つけたときに返す文を 1 組で持つ。

| 欄 | 意味 |
|---|---|
| `id` | 識別子。必須。文面と記録に出す。記録では `<ワークスペースのルートからの相対パス>:<id>`（組み込みは `builtin:<名前>`）。id の無いルールは読み込まず error で名指しする |
| `match` | ツール名を `\|` でつないだもの。`Bash` `PowerShell` `Read` `Edit` `Write` `NotebookEdit` `Grep` `Glob` `Skill` `Agent` `WebFetch`。ツールでない名前として `Stop`（メインエージェントのターンの終わり）。`Stop` を書けるのは `allow` だけ（6.6） |
| `glob` | `fnmatch` の記法。文字列全体に当たる |
| `regex` | Python の正規表現。`re.search`。先読み・後読み・後方参照は受け付けない |
| `when` | 組み合わせ条件。`glob` / `regex` の個別条件を `all` / `any` / `not` で組む（5.3） |
| `except` | 省略できる。`glob` か `regex` の片方だけを持つマッピングのリスト。本体に当たっても、どれかに当たれば当たらない扱い（5.3） |
| `message` | `deny` だけ。必須。止められたモデルに届く「なぜ止めたか、代わりに何をするか」 |
| `additionalContext` / `additionalContextOnce` | 当たるたび / 文脈で最初に当たったときだけ、モデルへ渡す文（6.6） |
| `additionalContextFile` / `additionalContextOnceFile` | 文に続けて本文を渡すファイル。探す起点からの相対（順は 6.6） |
| `every` | 「渡す回」の刻み。当たった回数がこの倍数になった回だけが渡す回になる。既定は 1（6.6） |

`glob` と `regex` の値は引用符で囲む。囲まないと YAML が先に解釈する（`<<` はマージキー、`*` はエイリアス、`&` はアンカー、`!` はタグ）。

`ask` と `allow` に `message` を書くと `--lint` が error にする（ask の文面はユーザの確認ダイアログにしか出ず、allow の文面は
どこにも出ない。付録 C）。組み立てられないルールは外して名指しし、他のルールはそのまま使う。

### 5.2 タイプの強さ

強い順に `deny` `ask` `allow`。1 件でも `deny` に当たれば拒否で、弱いタイプは見に行かない。
同じタイプに複数当たったら全部の文面を返す（6.4）。どのタイプにも当たらなければ、ccnavi は判定を持たない（6.5）。

`allow` は「このプロジェクトで普通にやること」を並べる場所で、権限を配る場所ではない。

### 5.3 記法

`glob` は標準ライブラリの `fnmatch` そのままで、文字列全体に当たる。部分一致が欲しければ前後に `*` を書く。
左の語の切れ目が要るルールは `regex` を使う。`fnmatch` の外で足しているのは区切り文字の正規化 1 つだけで、
`/` と書けば `\` にも当たる。文字クラスの中に `/` を書いた `[a/b]` は読み込み時に外れる。

`glob` も `regex` も大文字小文字を区別しない。どの機械でも同じで、`*/.ccnavi/*` は `.Ccnavi/config/project/rules.yml` にも当たる（11.4）。
区別させたい部分は `(?-i:...)` で囲む。

`glob` が文字列全体に当たることの帰結: `git push` は素の `git push` にしか当たらず、`cd /repo && git push` には当たらない（`*git push*` と書けば当たる）。
語の切れ目は入らない。`*sed*` は `sedate` にも当たる。右側だけなら `*sed *` と空白を書いて切れ目を表せる。左側は書けない（`*git push*` は `legit push` にも当たり、`* git push*` は行頭の `git push` が外れる）。

`{root}` は、読み込み時にワークスペースルートの実パスへ置き換わる。regex では区切りが `[\\/]` になる。
ルートは hook なら `CLAUDE_PROJECT_DIR`、端末なら `--root` の実パス。区切りは `/` と `\` のどちらにも当たり、大文字小文字は区別しない。ルートを渡さずに読むと error。文面（`message` / `additionalContext` / `additionalContextOnce`）の `{root}` は、
モデルへ渡すときに、実パス（区切りは `/`）へ置き換える（`rules.fill_text`）。`{script:<名前>}` は `settings.script_command` の値に置き換える。ルートが渡されない読み方では、置き換えない。`<名前>` は `[A-Za-z0-9._-]+` だけで、形が違うものは置き換えず、スクリプトが無いものは置き換える。どちらも `--lint` が warn する。`--explain` と `--test` は書いた表記のまま `{root}` を出す。

先読み・後読み・後方参照を弾くのは、ルールがエンジンをまたいでも同じ意味になり、組み合わせ爆発を起こしうる書き方を受け付けないため
（Python の `re` に時間制限は無い）。

`{!root}` は、読み込み時に「ワークスペースルートで始まらない」へ展開される。`(?!...)` を使わずに否定を書くための
記法である。ルートを 1 文字ずつ分解した入れ子の式になり、繰り返しを含まない。展開先はどのレイヤーのルールでもワークスペースルート。

| 決まり | |
|---|---|
| 書ける場所 | `regex` だけ。`glob` に書くと error |
| 置ける位置 | `^` の直後に 1 回だけ。前に何かを置くと error |
| 直後の量化子 | error。`^{!root}?` は展開結果ごと省略でき、どの対象にも当たる式になる |
| 後ろに続く式 | 書ける。ただし `.*` のように任意の位置まで進む形で受けていなければ warn。展開結果が読み終える位置はパスの区切りとは限らない |
| ルートの正規化 | `realpath` したうえで末尾の区切りを除く |
| 対象がルートそのもの | 「中」。`Glob` を `path` 省略で呼ぶと対象が cwd（＝ルート）になる |
| 大文字小文字 | 区別しないので、表記だけが違うパスは「中」。トルコ語の `İ`/`ı` が `I`/`i` と同一視されるのは既知の例外 |

ルートの長さが 256 字以上だと展開しない（入れ子が 493 字で `re.compile` が `RecursionError` になる）。
組み立てられなかったルールは強い側を採る。`deny` と `ask` は `match` の全部に当たる式を持たせて止め、`allow` はどれにも
当てない（REQ-RUL-10）。書いた文面は残し、止められた側へ返す説明は別の欄に持つ。

`except` は「本体に当たっても除くもの」のリスト（REQ-RUL-11）。「A だが B は除く」を
1 本の regex で書くと 1 文字ずつの場合分けになる。それを本体と除くものの 2 つに分けて書くための欄である。

```yaml
  - id: workspace-root
    match: Write|Edit|NotebookEdit
    regex: '^{root}[\\/]'
    except:
      - regex: '^{root}[\\/]wip[\\/]handoff(?:[\\/]|$)'
      - regex: '^{root}[\\/]\.claude[\\/]worktrees(?:[\\/]|$)'
```

| 決まり | |
|---|---|
| 要素の形 | `glob` か `regex` のどちらか 1 つだけを持つマッピング。`when` の個別条件と同じ形 |
| 読み方 | 本体と同じ（`{root}`、`{!root}`、大文字小文字の無視、先読み・後方参照の禁止、glob の翻訳） |
| 当てる文字列 | 本体と同じ。`Bash` ならコマンド列の全体（実行役のコマンドの中のコマンドは、その層ごと）。除く語をコマンドのどこかに足すだけで外れるので、`Bash` / `PowerShell` の `deny` / `ask` に付けると `--lint` が warn |
| `match` | 見方は変わらない。除くのは対象の文字列だけ |
| 書式の誤り | 空のリスト、`null`、マッピングでない要素、`glob` と `regex` の両方・どちらも無い要素、知らないキー。ルールごと読み込まずに error |
| 何にでも当たる要素 | `--lint` が warn。ルールがどの呼び出しにも当たらなくなる |
| `{!root}` を組み立てられない要素 | error。`deny` / `ask` はその要素を外して使う（当たる範囲が広がり、止める側に外れる）。`allow` はルールごと外す。本体を組み立てられなかった `deny` / `ask` は `except` を付けずに全部止める |
| レイヤーの重複 | `except` も比べる。除くものだけが違う 2 本は別の定義として両方使う |

`when` は本体を組み合わせ条件で書く欄（REQ-RUL-12）。ルールは `glob` / `regex` / `when` のちょうど 1 つを持つ。
`except` は「A だが B は除く」しか書けず、「A か B」「A かつ B」は 1 本の式に詰めることになる。組み合わせ条件はそれを個別条件に分けて書く。

```yaml
  - id: secrets
    match: Write|Edit
    when:
      any:
        - glob: '*.pem'
        - all:
            - glob: '*/secrets/*'
            - not: {glob: '*.md'}
    message: ...
```

| 決まり | |
|---|---|
| ノードの形 | 次のどれか 1 つのキーだけを持つマッピング。`glob: '…'` / `regex: '…'`（個別条件）、`all: [ノード, …]`、`any: [ノード, …]`（1 個以上）、`not: ノード` |
| 個別条件の読み方 | 本体と同じ（`{root}`、`{!root}`、大文字小文字の無視、先読み・後方参照の禁止、glob の翻訳） |
| 当てる文字列 | 個別条件はどれも本体と同じ文字列に `search` する。`Bash` / `PowerShell` ならコマンド列の全体で、実行役のコマンドの中のコマンドはその層ごと |
| `except` との併用 | 書ける。意味は `all: [when, not: {any: except}]` |
| 上限 | 入れ子 8 段（最上位のノードが 1 段目）、ノード 64 個。判定は呼び出しのたびに組み合わせ条件をたどるので、重くならないように頭打ちにする |
| 書式の誤り | 知らないキー、キーが 0 個・2 個以上のノード、空のリスト、型の違う値（個別条件が空・文字列でない、`all` / `any` がリストでない、`not` がマッピングでない）、上限を超えた組み合わせ条件、`glob` / `regex` と並べた `when`。ルールごと読み込まずに error で、位置を `when.any[1].not` の形で名指しする |
| `{!root}` を組み立てられない個別条件 | error。`deny` / `ask` はルール全体を `match` の全部に当てて止め（`except` も付けない）、`allow` はルールごと外す。個別条件だけを外すと、`not` の下か上かで当たる範囲が広がるか狭まるかが変わり、向きが定まらないので、ルール全体で扱う |
| `--lint` | 同じ個別条件を肯定と否定で並べた `all` / `any`（`all: [A, not: A]` は何にも当たらず、`any: [A, not: A]` は何にでも当たる）を warn。同じ演算子の入れ子は開いて見る。何にでも当たる・何にも当たらないノード（`not` の下が何にでも当たる個別条件だけ、`any` の中に何にでも当たる個別条件など）を warn。`Bash` / `PowerShell` の `deny` / `ask` で `not` を含む組み合わせ条件を warn（除く語をコマンドのどこかに足すだけで外れる） |
| レイヤーの重複 | 個別条件の式と組み方の両方を比べる。`glob` で書いた 1 本と、同じ個別条件 1 つだけの `when` の 1 本は別の定義として扱う |
| 一覧と記録 | `--explain` は 1 行（`when: any(glob '*.pem', …)`）で出し、個別条件が 3 つ以上なら続けて字下げで出す。`--test --json` などのルールの欄は `kind: when` と、書いた形の `when` を持つ |

`except` と `when` の使い分け。「本体に当たっても、ここは除く」だけなら `except` で書く。並んだ除外を読むだけで
意味が取れるからである。「A か B」「A かつ B」、除外の中にさらに例外がある形のように、組み合わせが要るときに `when` を使う。

### 5.4 対象の取り出し

| `match` に書く名前 | 当てる対象 |
|---|---|
| `Bash` `PowerShell` | コマンド。`Bash` はシェルとして読んでから当てる（6.3）。`PowerShell` は生の文字列 |
| `Read` `Edit` `Write` `NotebookEdit` | ファイルのパス（行き着く先まで解いてから、6.2） |
| `Grep` `Glob` | 探す場所のパス（`path`。省略されていれば呼び出し側の cwd） |
| `Skill` | スキル名 |
| `Agent` | 起動の見出し（`description`、無ければ `prompt`） |
| `WebFetch` | URL |

表に無いツール名は対象が空で決して当たらず、`--lint` が warn にする。`match` は名前をそのまま突き合わせるので、
`Bash` のルールが `PowerShell` に及ぶことはない。

### 5.5 どのルールファイルを引くか

| 呼び出し | ルール集合 |
|---|---|
| パスを持つツール（`Read` `Grep` `Glob` `Write` `Edit` `NotebookEdit`） | 共通レイヤーに、行き先のツリーが属する 1 レイヤーを足したもの。プロジェクトに属すればそのレイヤー、属さなければワークスペース自身のレイヤー。`Grep` と `Glob` の行き先は探し始める場所で、省略されていれば `cwd`（11.4） |
| パスを持たないツール（`Bash` `PowerShell` `Skill` `Agent` `WebFetch`） | 共通レイヤー・自身のレイヤー・全プロジェクトのレイヤーのルールをタイプごとに連結したもの |

共通レイヤーはワークスペースルートの `.ccnavi/config/common/` の 1 本。プロジェクトの `.ccnavi/config/common/`（ミラー。11.12）は、そのプロジェクトだけを clone したときにだけ共通レイヤーとして読まれる。
共通と config のどちらのファイルも無くてよく、無いのは「設定が無い」正常で空として扱う。両方に無ければ組み込みの既定を使う（11.2、11.4）。

自身のレイヤーのルールに当たったときは識別子に `self:`、プロジェクトのレイヤーなら `<プロジェクト名>:` を前置して出す。

### 5.6 ルールが読めないとき

ルールファイルが無い・不正なときは、組み込みの既定で判定を続ける。通した回にも「既定を使った」と
伝え、記録に `fallback` を残す。既定に入っているのは取り返しの付かない操作だけ。

| 操作 | 既定での扱い |
|---|---|
| `rm -rf`、`git push`、`git reset --hard` | 止める。表記違いも止める（下） |
| 生の git（いつも） | 止める（`builtin:guard-raw-git`。9.1） |
| 認証情報の置き場（`.env*`、`.dev.vars`、`.netrc`、`.npmrc`、`.pypirc`、`.aws/`、`.ssh/`、`*.pem` / `*.key`、`secrets/`、`credentials/`、`credentials.json`、`id_rsa`、`id_ed25519`）への `Bash` `Read` `Write` `Edit` `NotebookEdit` | 止める（`builtin:credentials`。下） |
| シェルから `.claude/hooks/` `.claude/settings*.json`、ccnavi ディレクトリ `.ccnavi`、`logs/decisions.jsonl` `logs/state` `logs/archive`、`ccnavi-git.sh` へ書き込む形 | 止める（書き込みの形と場所の組で見る。8.2） |
| `.git` の中（`.git/` の下と、ワークツリーの `.git` ファイル）への `Write` `Edit` `NotebookEdit` と、シェルから書き込む形 | 止める（`builtin:guard-git-dir`・`builtin:guard-git-dir-via-bash`。下） |
| `Read`（認証情報の置き場を除く。強いタイプが先に当たる） | 通す |
| `hook`・`settings`・`scripts`・共通の設定 3 本への `Write` `Edit` `NotebookEdit` | 止める（`builtin:guard-hooks` など。`CCNAVI_GUARD_CORE_FILES` が `disable` でなければ。8.2）。ルールファイルが読めない間の修復も、直接は通さない。下書きを `wip/handoff/` に置き、ユーザが `ccnavi user handoff` で取り込む |
| それ以外 | 権限モードに委ねる |

取り返しの付かない操作の 3 本（`builtin:recursive-delete`・`builtin:git-push`・`builtin:git-reset-hard`）は、既定のときに限らず
常に当たる土台の deny で、glob の文字列一致ではなく regex で書く。glob（`*rm -rf *`・`*git push*`）は表記違いに外れるため。

- git は、サブコマンドの前に挟むグローバルオプション（`-C <path>`・`-c <k=v>`・`--git-dir[=]<dir>`・`--work-tree[=]<dir>`・`--no-pager` など）を
  読み飛ばしてから `push` / `reset` を見る。値を取るオプションは値を最後まで読むので（`git -C gitx push` の `gitx` も 1 つの値。読みで空白が 2 字続く空の値 `git -C '' push` は空の値として読む）、`git -c push.default=current status` の値の `push` も値として読む。`push` の後ろは空白・区切り・末尾だけを見る。`reset` は後ろのどこかに `--hard`（略した `--har` も）があれば止める。`--` の後ろはパスなので見ない（`git reset -- --hard`）。
  サブコマンドを直接起こす `git-push` / `git-reset`（パス付きの `/usr/lib/git-core/git-push`、`.exe` 付き）も同じ 1 本で止める。
- rm は、再帰（`-r` `-R` `--recursive`）と強制（`-f` `--force`）の組を、まとめた形（`-rf` `-fr` `-Rfv`）・分けた形（`-r -f`）・順序・
  長い形（略した形も）を問わずに止める。共通のルールファイルには書かず、この土台だけが持つ
- どの式も、照合の時間が入力の長さにほぼ比例する形で書く（所有的な繰り返しとアトミックグループ。先読みは使わない）。判定の期限は
  ルールの合間でしか確かめないので、1 本の照合が後戻りで長引くと hook の timeout に達し、呼び出しは止められずに通る。
  rm と reset は、後の始まりで当たるなら最初の始まりでも当たるので、コマンドの中で最初の `rm` / `reset` に決めて 1 度だけ読む。
  git のオプションは、中に git の始まりを持たない語だけを読む

認証情報の 1 本（`builtin:credentials`）も、既定のときに限らず常に当たる土台の deny。設定で切れず、誤検知が出ても外せない。
見本の `.env.example` も、対象の 5 つのツール（`Bash` `Read` `Write` `Edit` `NotebookEdit`）で止まる。`Grep` `Glob` `PowerShell` は対象に入れない。
鍵のファイルは拡張子として書かれた形（`x.pem`、`x.pem.bak`、`x.key.json`）で当てる。判定に達せなかったときの許可リスト（`hook/failsafe.py`）は、
この式に揃え、独自に `.gnupg`、`id_ecdsa`、`.p12` / `.pfx`、`.git`、操作記録の置き場を足している。

`.git` の中への書き込みの 2 本（`builtin:guard-git-dir`・`builtin:guard-git-dir-via-bash`）も、既定のときに限らず常に当たる土台の deny。
参照・索引・設定・hook を git を通さずに書き換えると、履歴や作業中の変更を失い、hook なら以後の git の操作のたびに任意のコマンドが走る。
文面は git の打ち方（ラッパーがあれば `ccnavi-git.sh`）を案内する。
サブコマンドの無い書き換え（hook、`.git/info/exclude`、`MERGE_MSG`、`index.lock`、ワークツリーの `.git` ファイル）は、ファイルと理由を添えてユーザに頼むよう案内する。
残った `index.lock` は、git のプロセスが無いことを確かめてから頼む。無視の設定は `.gitignore` で足す。

- 名指しのツールは、解決済みの絶対パスに `.git` の段（前後が区切りか末尾）があれば止める。`.gitignore`・`.github/`・`repo.git/` は当たらない。`Read` は止めない。
  名前の後ろの `.` と空白（`.git.`・`.git `）は Windows が開くときに落とすので、`.git` と同じに扱う
- シェルは、書き込みの形と `.git` の組で止める。`cat .git/config`・`cp .git/config /tmp/x` のような読みは通る。
  リダイレクトと `cp` `ln` `install` の行き先、`.git` をいま居る場所 `.` へ写す形は守る場所（8.2）と同じ部品で読む。
  `.git` の前は語の頭か区切り（`/` `\` `=`）に限り、URL の中の `.git` には当てない（`-t` の値だけは前を閉じない）。
  名前の後ろの `.`（`.git.`）は名指しのツールと同じに扱う。書き換える動詞は `.git` 用に読みの語を外す。
  - `mv` `rm` `tee` `truncate` `patch` `shred` は、`<` の直後の語を読みとして外す（`tee /tmp/o < .git/config`・`patch -p1 < .git/x` は通る）
  - `dd` は `of=` の値だけを書き先とみる（`dd if=.git/index of=/tmp/x` は通る）
  - `sed -i` は式（最初の選択肢でない語）を外し、その後ろの語を書き先とみる（`sed -i 's/foo/.git/' f` は通る）。
    `-e` で式を渡すと、2 本目以降の式の中の `.git` は書き先と区別できず止まる（`sed -i -e 's/a/b/' -e 's/c/.git/' f`）
  - `find` は、開始点が `.git`（とその下）で、述語に `-delete` `-exec` `-execdir` `-ok` `-okdir` を持つ形を止める。
    `find . -path ./.git -prune -o -type f -delete` は通る
  - `xargs` は、前のコマンドが `.git` を名指しし、`xargs` が書き込む動詞（上の動詞と `cp` `ln` `install`）を走らせる形を止める。
    `grep -rl x .git | xargs rm` のように、`.git` の中を探して外のファイルを消す形も止まる
- git のラッパーの中で行う `.git` への書き込み（統合の片付けなど）は、外側のコマンド文字列に `.git` が出ないので当たらない
- リダイレクトの行き先は `>` をまたがずに読むので、読み切れなかった生の文字列でも照合が長さに比例して終わる
- `cd .git` のあとの相対パスへの書き込みは、同じコマンドの中なら止める（`cd .git && echo x > config`・`cd .git && find . -delete`）。
  `cd` で移った先から見たパス（`shellread` の moved）にも当てるため
- 止めないもの。守る場所の検出と同じく、形の分かる書き込みだけを見る
  - `git` 自身のサブコマンド（`git config` など）
  - `cd` が別の Bash の呼び出しにまたがる形（`cd .git/hooks` のあと、別の呼び出しで `echo x > pre-commit`）。判定はセッションの cwd を見ない
  - インタプリタ（`python -c` など）の中の書き込み
  - 変数・コマンド置換・グロブで書いたパス（`$h/pre-commit`、`$(… --git-path hooks)/x`、`.gi?/config`）。`shellread` は変数の値も展開の結果も知らない
  - 上に無い書き込みの動詞（`touch` `chmod` `mkdir` `perl -pi` `rsync` `tar` `curl -o` `awk -i inplace`）は未実装。足すと読むだけの形の誤検知が増えるため
  - 親から始める `find`（`find . -name index.lock -delete`）。開始点が `.git` の形だけを見る
  - Windows の別名（8.3 形式の `GIT~1`、`.git::$INDEX_ALLOCATION`）。解決済みのパスでも別名のまま残る

生の git の 1 本（`builtin:guard-raw-git`）は、`git` がコマンドの位置にある形だけに当てる（行頭とコマンドの切れ目の後ろ、
`env`・`sudo`・`xargs` などの前置きは外した中のコマンドの層で、パス付き（`C:\Git\git.exe` も）と `git.exe` も。`then`・`do` などの予約語は
区切りの直後のものだけ読み飛ばす）。`grep git README.md`・`echo to do git` の引数の `git` には当てない。ルールファイルが読めずに既定を使っている間も足す。ラッパーはルールファイルを読まずに動き、
衝突の解決に要る `add`・`restore --ours` / `--theirs`・`merge-file -p` を通すので、直す道は残る（REQ-PRE-06）。

既定にはプロジェクトの `allow` が無いので、読み取り以外はほぼ確認になる。
自身のレイヤーかプロジェクトのレイヤーが読めないときは、そのレイヤーを空として扱い、Bash の和からも
外し、外したことを記録と診断で示す（11.4）。
