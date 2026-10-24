---
title: Bash の読み方とパスの見方
type: guide
description: Bash のルールがコマンドのどこにヒットするか、止まる書き方と通る書き方、読み切れないとき、実行役のコマンド、ヒアドキュメント、ファイルのパスの扱い、探すツールが届かない範囲
tags: [user-guide, rules, bash]
keywords: [Bash, コマンド, 実行される部分, 引用, ヒアドキュメント, ブレース展開, バッククォート, DENY_BACKQUOTE, DENY_BRACE_EXPANSION, DENY_COMMAND_NAME_EXPANSION, DENY_AMBIGUOUS_FORM, PARSE_UNCERTAIN, 縮退, 実行役, env, sudo, xargs, パス, シンボリックリンク, Grep, .gitignore, permissions.deny]
---

# Bash の読み方とパスの見方

[ルールの書き方](rules.md)の続きである。`Bash`のルールがコマンドのどこにヒットするか、止まる書き方と通る書き方、
ファイルのパスや探すツールの扱いをまとめる。読み方の仕組みは [設計 6.2](../21_design/pre-check.md#62-パスは行き着く先で見る) と
[6.3](../21_design/pre-check.md#63-bash-は実行される部分だけを見る) にある。

## Bash のコマンドは実行される部分だけを見る

`Bash`のルールは、コマンド文字列そのものではなく、シェルが実際に実行する部分に
適用する。禁止された語を書いただけの操作は止まらない。

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

シェルが実行する中身は、書いた場所によらず独立したコマンドとして読む。二重引用の中の`$( )`、
プロセス置換`<( )` `>( )`、区切りを引用しないヒアドキュメント（`<<EOF`）の本文の`$( )`がこれにあたる
（バッククォートは読まずに止める。下の「書き直しを求める形は止めて案内する」）。中身は外側のコマンドの後ろにつながれ、外側の置換のあった場所には
`$`が 1 文字残る。`grep -n "$(git push)" f`は`grep -n $ f`と`git push`の 2 本として読まれるので、
grepのallowは後ろの`git push`まで通さない。外側のコマンドは分割されないので、`find $(pwd) -name x -delete`の
`-delete`はfindと同じコマンドとして見える。

引用の外の改行はコマンドの区切りになる。`#`がコメントになるのは語の始まりだけで、`a#b`や`$#`の`#`は文字。
`if` `then` `do` `{`などの予約語はコマンドの位置にあればそれだけで 1 本になり、後ろの語がコマンドの先頭になる。

引用が 1 語につないだ空白は、コマンドと引数の間の空白としては読まれない（`"git push"`と`git push`を分ける）。
引用の中身そのものは残るので、`cat "/home/u/.env"`は`.env`のルールにヒットする。

ルールの`regex`から見ると、コマンドとコマンドの間には`\x00`が置かれる。
`[^\x00]*`と書けば「同じコマンドの中」を指す。`.ccnavi/config/common/rules.yml`では、
`grep -n "git push" README.md`や`find . -name "a b"`のような引用付きのgrep / cat / findが、
後ろにコマンドが続かなければ`prefer-read-grep` / `prefer-glob`（allow）にヒットする。
`cat README.md | head -20`は`\x00`をまたぐのでヒットしない。

語の中に入った`>` `<` `|` `&` `(` `)`は、演算子としては読まれない。
`grep -n "x>y" notes.md`の`>`は文字で、素の`echo x>f`は演算子として残る。
引用符の中身が`>`や`<`だけの 1 語（`grep -n ">" f`、`grep -n "<" f`）も、文字として読む。`<<`は演算子の`<<`と区別が付かず、
ヒアドキュメントに見えるので、縮退して止まる（例: `grep -n "<<" f`）。

### 文字として書きたいとき

二重引用の中の`$( )`とバッククォートは、シェルが実行するので止まる（バッククォートは中身に依らず止まる）。
引用の中から切り出したコマンドにだけルールがヒットしたときは、返る文面にそのことと回避策が添えられ、
記録と`--test`に`quoted`（そのルールのid）が出る。

| 書きたいもの | 止まる書き方 | 通る書き方 |
|---|---|---|
| 本文にバッククォートを含む issue やマージリクエスト | ``glab issue create --description "use `git push` here"``（`DENY_BACKQUOTE`。gh なら `--body`） | 単一引用で包む、`` \` `` で書く、`--description-file <ファイル>`（gh なら `--body-file`） |
| 複数行のコミットメッセージ | `commit -m "$(cat <<'EOF' … EOF)"`（heredocのルール） | 本文をWriteで置いて`commit -F <ファイル>`、改行を含む`-m "…"` |
| gitの値を使うコマンド | `cd "$(git rev-parse --show-toplevel)"` | ラッパースクリプトで値を出して読み、次のコマンドにその値を書く |
| 今の場所を渡す検索 | `grep -rn foo "$(pwd)"`（allowから外れて確認になる） | 値をそのまま書く |

単一引用の中に`'`を書くときは`'"'"'`とつなぐ。

### 読み切れないとき

静的に読めないコマンドは、生の文字列との一致に縮退する。縮退した拒否の文面には、
読めなかったので生の文字列に適用したという断りが、`note:`で始まる英文の 1 行で付く。

縮退した呼び出しに`allow`は適用せず、`deny`と`ask`は適用する（生の文字列にヒットしすぎるぶん、判定は厳しい側にずれる）。
どこにもヒットしなければコード`PARSE_UNCERTAIN`でユーザに確認が出る。ここは権限モードに委ねない。

コメントだけの行のように、実行される部分が何も残らないコマンドは判定に入らない。

| 縮退する条件 | 例 |
|---|---|
| 文字列をコードとして実行する呼び出し | `bash -c`、`sh -c`、`eval`、`xargs`、`find -exec` |
| 引用やヒアドキュメントが閉じていない | `echo "git push` |
| 置換が閉じていない | `echo $(git push` |

置換の中身が 1 つでも読み切れなければ、コマンド全体が縮退する。シェルによって読み方が分かれる形と
バッククォートは、縮退ではなく止めて書き直しを案内する（下の「書き直しを求める形は止めて案内する」）。

引用でコマンド名を区切った`"git" push`や`g"it" push`は縮退しない。
シェルと同じ字句規則で語を組み直すので、本来のpushとしてそのまま止まる。

perlやpythonは縮退の対象に入れていない（`perl -pi -e 's/git push/.../' README.md`のような 1 行編集を通すため）。

記録に残る理由の名前と、縮退した呼び出しに`allow`を適用しない理由は [設計 6.3・6.5](../21_design/pre-check.md#63-bash-は実行される部分だけを見る) と
[縮退したとき allow を返さない判断の ADR](../30_adr/rules/0011-no-allow-when-degraded.md)にある。

### 書き直しを求める形は止めて案内する

書き直す方法が必ずあり、読み分けると規則が増えるか、読み違えると止めずに通してしまう形は、
読み解かずにルールより先に形ごとの理由コードで止め、書き直し方を返す。
`sh -c`・`eval`・`xargs`・`find -exec`は止めずに、縮退と「実行役のコマンド」の読みで扱う。

#### ブレース展開は語を並べて書く

引用の外の`{a,b}`や`{1..3}`は、シェルが実行する前に複数の語に広げる（`{git,push,origin,main}`は
`git push origin main`として実行される）。ccnaviは、ルールより先にコード`DENY_BRACE_EXPANSION`で止める
（広げ方がbashとzshで分かれるため）。止めた文面は
見つけた表記と書き直し方を示す。

| 止まる書き方 | 通る書き方 |
|---|---|
| `grep -rn foo --exclude-dir={node_modules,.git} .` | `grep -rn foo --exclude-dir=node_modules --exclude-dir=.git .` |
| `cp f{,.bak}` | `cp f f.bak` |
| `mkdir -p src/{a,b}` | `mkdir -p src/a src/b` |
| `touch file{1..3}` | `touch file1 file2 file3` |
| ブレースを文字として渡す`echo {a,b}` | `echo '{a,b}'` |

引用の中、`\{`、ヒアドキュメントの本文、コメントのブレースは止まらない。`find . -exec rm {} \;`や
`HEAD@{1}`のように、カンマも範囲も無いブレースも止まらない。代入の右辺（`x={a,b}`）、caseのパターン
（`case $x in {a,b})`）、`[[ $f == *.{jpg,png} ]]`はシェルが広げないが止まる。これは許容した誤検知である
（[設計 12.2](../21_design/limits.md#122-許容する誤検知)）。引用するか、パターンを`a|b)`や`*.jpg || … *.png`のように書けば通る。

#### コマンド名はそのまま書く

変数・置換・グロブをコマンド名に置くと、どのプログラムが走るかが書かれた文字列に無く、どのルールもヒットしない
（`c=git; $c push origin main`）。コード`DENY_COMMAND_NAME_EXPANSION`で止める。
前に置いた代入とリダイレクト（`FOO=1 >/dev/null $c`、`{fd}>/dev/null $c`）の後ろも、実行役のコマンドの中
（`env $c`・`sudo $c`・`sh $SCRIPT`）も見る。`sh -c "$c"`と`eval "$(…)"`の文字列の中は見ず、
読み切れないものとして確認にする。

| 止まる書き方 | 通る書き方 |
|---|---|
| `c=git; $c status` | `git status` |
| `G="sh .ccnavi/scripts/ccnavi-git.sh"; $G add f` | `sh .ccnavi/scripts/ccnavi-git.sh add f` |
| `sh $S/run.sh` | `sh /tmp/work/run.sh`（パスをそのまま書く） |
| `/usr/bin/gi? status` | `/usr/bin/git status` |

引数の位置の変数とグロブ（`echo $HOME`、`ls *.py`、`cd $S`、`timeout $T make`）、`[ -f x ]`、`case $x in`は止まらない。

#### バッククォートは `$( )` か単一引用で書く

バッククォートは二重引用の中でも、区切りを引用しないヒアドキュメントの中でも実行される。ccnaviは中身を読まず、
コード`DENY_BACKQUOTE`で止める。コマンド置換なら`$( )`で書く。文字として渡したいなら（issueやマージリクエストの本文の
Markdown など）、単一引用で包むか、`` \` `` と書くか、`--description-file <ファイル>`（gh なら `--body-file`）で渡す。単一引用の中、`` \` ``、
引用付きヒアドキュメントの本文、コメントのバッククォートは止まらない。

#### シェルで読みが割れる形は素直な形で書く

コード`DENY_AMBIGUOUS_FORM`で止める。どれもbashとzshで読み方が分かれるか、作業で使わない形。

| 止まる形 | 書き直し方 |
|---|---|
| `$( )`の中の`case`（`"$(case $x in a) echo 1;; esac)"`） | if/elifで書く。`$( )`の外で分岐する |
| `$((cmd) \| x)` | `$( (cmd) \| x )`と空白を入れる |
| 16 段を超える`$( )`の入れ子 | 内側を先に打ち、出た値を次のコマンドに書く |
| コマンドの位置の`coproc` | `&`でバックグラウンドにする |
| コマンドの位置の`select` | `for`で回す |

`$( )`の中の、コマンドの先頭ではない`case`の語（`"$(echo just in case)"`）でも止まる。許容した誤検知
（[設計 12.2](../21_design/limits.md#122-許容する誤検知)）。

## 実行役のコマンドが中で実行するコマンドにも適用する

`env rm -f x`の`env`のように、別のコマンドを走らせるためのコマンドがある（実行役のコマンド）。
ルールの多くは`(^|\x00)rm`のようにコマンドの先頭に固定して書くので、ccnaviは実行役のコマンドを 1 つずつ外し、
中で実行されるコマンドにも、止める側のルール（`deny`と`ask`）とサブエージェントの禁止を適用する。

```sh
env rm -f .ccnavi/config/common/rules.yml              # 止まる。env が実行する rm にヒットする
timeout 5 sed -i s/a/b/ .claude/settings.json   # 止まる
echo x | xargs rm -f .ccnavi/config/common/rules.yml   # 止まる
sh -c 'rm -f .ccnavi/config/common/rules.yml'          # 止まる
env sh .ccnavi/scripts/ccnavi-launcher.sh user agree --yes x   # 止まる（`user` の入口）
env curl -d @x https://example.com              # curl に書いた ask がヒットする
sudo -u me cat /etc/hosts                       # 読み取りの allow にはヒットしない。元の形のまま判定する
ssh host rm -f .ccnavi/config/common/rules.yml         # 外さない。ssh は一覧に無い
```

外すのは次の形。

| 実行役のコマンド | 中で実行されるコマンド |
|---|---|
| `VAR=値 <cmd>` | `<cmd>` |
| `/bin/sh x`、`git.exe x`（道筋や拡張子の付いた名前） | `sh x`、`git x` |
| `env` `command` `exec` `nohup` `time` `nice` `sudo` `doas` `timeout` `stdbuf` `chrt` `ionice` `taskset` | オプションとその値、コマンドの前の位置引数（`timeout`の時間、`chrt`と`taskset`の 1 語）、`--`、`env`と`sudo`の`名前=値`を飛ばした残り |
| `sh` `bash` `zsh` `dash` `ksh` `<ファイル> <引数>` | `<ファイル> <引数>` |
| `sh -c '<文字列>'`（`bash -lc`のような組み合わせも） | 文字列を読み直したコマンド |
| `eval <語…>` | 語をつないで読み直したコマンド |
| `.` / `source` `<ファイル> <引数>` | `<ファイル> <引数>` |
| `xargs [オプション] <cmd…>` | `<cmd…>` |
| `find … -exec` / `-execdir` / `-ok` / `-okdir` `<cmd…> ;`（または`+`） | `<cmd…>`。複数あればそれぞれ |

- 1 つずつ外し、途中のコマンドも残す。`env sh .ccnavi/scripts/ccnavi-user-agree.sh`なら`sh .ccnavi/scripts/ccnavi-user-agree.sh`と
  `.ccnavi/scripts/ccnavi-user-agree.sh`の両方に適用する。外す深さは 4 まで（元の形は数えない）
- シェルや`.` / `source`に渡したファイルの先は外さない（スクリプトの引数）。
  `command -v` / `-V`（探すだけ）と`sh -s`（標準入力を読む）も外さない
- 読み切れない形（`sh -c`・`eval`・`xargs`・`find -exec`・`source`・`.`）でも、語に分けられる限り外す。
  閉じない引用と閉じないヒアドキュメントでは外さない
- 一覧はccnaviの組み込みで、`rules.yml`からは足せない。一覧に無いもの（`ssh host <cmd>`・`python -c`・`perl -e`・`script -c`・`watch`・
  `busybox sh`など）は外さないので、先頭に固定したルールは外れる。変数の値・alias・関数
  の中までは追わない（コマンド名に変数を置いた`$SUDO rm …`は、上の「コマンド名はそのまま書く」で止まる）

適用する先は止める側だけにする。

| 適用する先 | 元の形 | 中で実行されるコマンド |
|---|---|---|
| `deny` | 適用する | 適用する |
| `ask` | 適用する | 適用する |
| サブエージェントの禁止 | 適用する | 適用する |
| `allow` | 読み切れたときだけ適用する | 適用しない |
| 止めている間でも通す形 | 適用する | 適用しない |
| チケットの範囲 | 適用する | 適用しない |

中で実行されるコマンドは止める側に足す適用先で、元の形の判定を消さないので、外し方を読み違えても緩くはならない。
`allow`や止めている間でも通す形に適用すると、実行役のコマンドを前に置くだけで通るようになる
（`sudo -u me cat /etc/hosts`、`env sh .ccnavi/scripts/ccnavi-review.sh confirm --phase 1`）。

中で実行されるコマンドでヒットしたときは、拒否と確認の文面に 1 行足す。

```
[ccnavi] DENY_COMMAND_PATTERN (rule: builtin:guard-setting-files)
subject: env rm -f .ccnavi/config/common/rules.yml
`env` が実行する `rm -f .ccnavi/config/common/rules.yml` にヒットしました。
ガード自身の設定と hook を、シェルから書き換えようとしています。…
```

記録の欄`unwrapped`にヒットしたコマンドが残る（[記録](records.md)）。`--test`は`unwrapped:`の行で出す（[ルールが何にヒットするかを確かめる](check.md)）。
読み切れない形で、ヒットしたルールが全部中で実行されるコマンドでヒットしたときは、拒否のコードが`PARSE_UNCERTAIN`ではなく
`DENY_COMMAND_PATTERN`になる。`PARSE_UNCERTAIN`の件数で読み切れない拒否を数えている集計とはずれる。
層の作り方、外す深さと語数の上限、値を取るオプションの一覧は [設計 6.3.1](../21_design/pre-check.md#631-実行役のコマンドの中で実行されるコマンド) にある。

## ヒアドキュメント自体は既定のルールで止める

既定のルールは`<<`を含むBash呼び出しを止める。ヒアドキュメントで書いたファイルは、
`permissions`の宣言も`PostToolUse`に登録した検査も通らないため。
代わりは`Write` / `Edit`ツール。プログラムに読ませる入力も、`Write`でファイルに置いてから渡す。

```sh
cat <<'EOF' > notes.md      # 止まる
uv run python - <<'PY'      # 止まる。ヒアストリング <<< も同じ
uv run python scratch.py    # 通る。scratch.py は Write で置く
```

算術式の`$((1 << 2))`は左シフトなので、読む前に取り除いてある。

`grep -n "<<" README.md`は止まる（引用された`<<`と素の`<<`を区別しない）。
許容する誤検知として設計に記載してある（設計 [6.3](../21_design/pre-check.md#63-bash-は実行される部分だけを見る)、[12.2](../21_design/limits.md#122-許容する誤検知)）。対象をファイルへ移せば回避できる。

## ファイルのパスは行き着く先で見る

`Read` `Write` `Edit` `NotebookEdit`のルールは、呼び出しに来た
`file_path`そのものではなく、絶対パスに直し`..`を解決しシンボリックリンクを解いた
結果に適用する。同じ場所を指す別の表記でルールを外せない。

```sh
.env                  # どれも同じ判定に行き着く
./.env
docs/../.env
/abs/path/to/.env
```

相対パスは呼び出しの`cwd`から決まる。まだ存在しないファイルへの書き込みは
シンボリックリンクを解けないので、絶対パスにして`..`を解決するところまでにとどめる。

## 探すツールが読むファイルは、ルールに届かない

`Grep`と`Glob`の対象は探し始める場所のパスで、そこから降りて読まれたファイルは
判定に届かない。`Grep(path=<git プロジェクトルート>)`は`.env`や`secrets/`の中身を返しうるが、
ルールは起点にしかヒットしない。

だから組み込みの`builtin:credentials`の`match`は`Bash|Read|Write|Edit|NotebookEdit`で、
`Grep`と`Glob`を含めていない。

`Grep`と`Glob`はccnaviが受け持たない。探すツールが読むファイルを見るのは、下の表の上 2 つ。

| 段 | 何を止めるか |
|---|---|
| `.gitignore`（ripgrepが読む） | `Grep`が起点から降りていく途中で出会うファイル |
| `.claude/settings.json`の`permissions.deny`の`Read(...)` | ファイル 1 つ 1 つ。`Grep`のファイル読み取りにも適用される |
| ccnaviのルール | `Read` `Write` `Edit` `NotebookEdit` `Bash`。`Grep`と`Glob`は受け持たない |

`projects/foo/.env`がfooの`.gitignore`に入っている場合に、それぞれの段がどう働くか。

| 呼び方 | ccnaviのルール | `.gitignore` | `Read()`の`deny` |
|---|---|---|---|
| `Grep(path=<git プロジェクトルート>)` | ヒットしない | 弾く | 弾く |
| `Grep(path=.../foo/.env)` | ヒットしない | ヒットしない | 弾く |
| `Read(.../foo/.env)` | 止める | ヒットしない | 弾く |
| `Bash: cat .../foo/.env` | 止める | ヒットしない | ヒットしない |

2 行目は`Read()`の`deny`が唯一の保護になる。`permissions.deny`を持たない配布先では、
ignoreされたファイルを名指しした`Grep`は通る。

### `.gitignore` を当てにしてよい範囲

`.gitignore`はripgrepの既定の挙動で動き、ccnaviの側では変えられない。当てにするときの結論は 2 つ。

- 起点そのものに指定されたパスにはヒットしない。ignoreされたディレクトリやファイルを`path`に渡すと、その中は読まれる
- `.git`が無いディレクトリでは`.gitignore`を読まない。cloneしていない置き場、git化していない`projects/<名前>/`が該当する

`.git`がある場所の扱い、隠しファイル、`.ignore`などの除外ファイルの扱いは [付録C](../21_design/appendix.md#付録-c-実測で確かめた前提) と
[探すツールと .gitignore の扱いの ADR](../30_adr/rules/0050-search-tools-and-ignore.md)にある。

ワークスペースルートがgit管理下で、`.gitignore`に`/projects/`があるときは、ルートからの
`Grep`は`projects/`へ降りない。プロジェクトのコードを探すときは`cd projects/<名前>`してから
呼ぶ（起点に指定した場所にはignoreがヒットしない）。
