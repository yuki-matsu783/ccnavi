# ccnavi

Claude Code のツール呼び出しを hook で止め、止めた理由と代わりに取る手段を返す。

要求は [requirements.md](requirements.md)、設計は [ccnavi.md](ccnavi.md) にある。

## 開発

Python 3.12 以降。実行時の third-party 依存は持たない。標準ライブラリだけで動く。
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
  | uv run python -m ccnavi --rules testdata/rules.json --mode block
```

編集のたびに `.claude/hooks/lint-py.sh` が走り、整形・検査・テストをかけて
`dist/ccnavi/` を作り直す。登録されている実行ファイルが常に今のソースになる。

### 配布物

PyInstaller の onedir で組み立てる。Windows なら `dist/ccnavi/ccnavi.exe`、
Linux なら `dist/ccnavi/ccnavi`。単一ファイルの onefile は使わない。
起動のたびにランタイムを一時ディレクトリへ展開するので、実測で 1 呼び出しあたり
1.0〜1.5 秒かかった。hook はすべてのツール呼び出しで走るから、それが全部に乗る。

| 形式 | 1 呼び出しあたり | 配布物 |
|---|---|---|
| onedir | 約 220 ms | 17 MB のフォルダ |
| onefile | 1000〜1500 ms | 7 MB の 1 ファイル |

## 設定

設定は環境変数で渡す。プロジェクトは `.claude/settings.json` の `env` ブロックに書く。
Claude Code の設定スキーマは独自のキーを受け付けないので、ここが唯一開いている場所になる。
hook には実行ファイルだけを登録すればよい。

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "\"${CLAUDE_PROJECT_DIR}/dist/ccnavi/ccnavi\"", "timeout": 10 }
      ]}
    ]
  },
  "env": {
    "CCNAVI_MODE": "warn",
    "CCNAVI_RULES": ".claude/ccnavi/rules.json",
    "CCNAVI_LOG": ".claude/ccnavi/log.jsonl"
  }
}
```

| 変数 | 意味 |
|---|---|
| `CCNAVI_MODE` | `block`（既定）、`warn`、`off` |
| `CCNAVI_RULES` | ルールファイル。相対パスはプロジェクト根から |
| `CCNAVI_LOG` | 記録先。空文字にすると記録しない |

`${CLAUDE_PROJECT_DIR}` は hook の `command` では展開されるが `env` では展開されない。
`env` には相対パスを書く。値の変更はセッションを開き直すまで効かない。

プロジェクト根は `CLAUDE_PROJECT_DIR`、無ければ作業ディレクトリから親へ辿って
`.claude` を探して決める。どの階層から起動しても同じ設定に行き着く。

## ルール

1 件のルールは、当てるツール・探すもの・見つけたときに返す文面を 1 組で持つ。
文面を欠いたルールは受け付けない。

```json
{
  "id": "git-push",
  "match": "Bash",
  "pattern": "git push *",
  "message": "git push はエージェントからは実行しません。利用者に依頼してください。"
}
```

`pattern` の書き方は 3 つだけ。

| 書き方 | 意味 |
|---|---|
| `*` | 任意の文字列。空でもよい |
| `?` | 任意の 1 文字 |
| 空白 | 任意の空白の並び。`git push` は `git   push` にも当たる |

残りはそのままの文字として扱う。部分一致なので `git push` は
`/usr/bin/git push origin main` にも当たる。単語の切れ目は自動で見るので、
`sed *` は `sed` に当たり `sedate` には当たらない。`/` はどちらの区切り文字にも
当たるため、`secrets/` は Windows のパスでも効く。

`|` による択一のように上の 3 つで書けないものは、`pattern` の代わりに
`regex` に正規表現を書く。両方書いたルールは受け付けない。
先読み・後読み・後方参照は受け付けない。書ける範囲を狭く保つと、ルールが
エンジンをまたいでも同じ意味になる。加えてこの 2 つは、組み合わせ爆発を起こす
書き方の入口でもある。判定の途中で固まった hook は期限に達して素通りになる。

## Bash のコマンドは実行される部分だけを見る

`Bash` のルールは、コマンド文字列そのものではなく、シェルが実際に実行する部分に
当てる。生の文字列を探すと、禁止された語を書いただけの操作が止まる。

```sh
grep -n "git push" README.md      # 通る。git push は grep の引数
echo "git push origin main"       # 通る
# git push origin main            # 通る。コメント
cat <<'EOF' > notes.md            # 通る。ヒアドキュメントの本文は実行位置ではない
git push origin main
EOF

git push origin main              # 止まる
cd /repo && git push              # 止まる
/usr/bin/git push                 # 止まる
GIT_DIR=/repo/.git git push       # 止まる
echo $(git push origin main)      # 止まる。$( ) の中は実行される
```

引用が 1 語につないだ空白は、コマンドと引数の間の空白としては読まれない。
これが `"git push"` と `git push` を分ける。引用の中身そのものは残るので、
`cat "/home/u/.env"` はこれまでどおり `.env` のルールに当たる。

### 読み切れないとき

静的に読めないコマンドは、生の文字列との一致に縮退する。これは以前の挙動なので、
これまで捕まえていたものが抜けることはない。縮退した拒否は文面が変わり、
「禁止された操作を行った」ではなく「読めなかったので文字列に当てた」と伝える。

| 縮退する条件 | 例 |
|---|---|
| 文字列をコードとして実行する呼び出し | `bash -c`、`sh -c`、`eval`、`xargs`、`find -exec` |
| 引用やヒアドキュメントが閉じていない | `echo "git push` |

引用でコマンド名を割った `"git" push` や `g"it" push` は縮退しない。
シェルと同じ字句規則で語を組み直すので、本来の push としてそのまま止まる。

perl や python は縮退の対象に入れていない。
`perl -pi -e 's/git push/.../' README.md` のような 1 行編集は、
禁止語に触れる文書を直すときの普通の書き方で、
ここを諦めると直したい誤検知がそのまま残る。

### ヒアドキュメント自体は既定のルールで止める

本文を実行位置として数えないことと、ヒアドキュメントを使わせることは別の話。
既定のルールは `<<` を含む Bash 呼び出しを止める。ヒアドキュメントで書いた
ファイルは、`permissions` の宣言も、`PostToolUse` に登録した検査も、
どちらも通らない。ファイルの中身を落とす経路がそこだけ素通しになる。

代わりは `Write` / `Edit` ツール。プログラムに読ませる入力も、
`Write` でスクリプトをファイルに置いてから、そのファイルを渡す。

```sh
cat <<'EOF' > notes.md      # 止まる
uv run python - <<'PY'      # 止まる。ヒアストリング <<< も同じ
uv run python scratch.py    # 通る。scratch.py は Write で置く
```

算術式の `$((1 << 2))` は左シフトであってヒアドキュメントではないので、
読む前に落としてある。

`grep -n "<<" README.md` は止まる。引用された `<<` と素の `<<` を `shlex` が
区別しないため。これは**許容する誤検知**として設計に記載してある
（[ccnavi.md](ccnavi.md) §12.3 ①、§23.1 L-7）。止まる側に倒れること、
文面が「読めなかったので文字列に当てた」と名乗って本来の禁止と混ざらないこと、
対象をファイルへ逃がせば回避できることの 3 つが揃っているため。

## ファイルのパスは行き着く先で見る

`Read` `Write` `Edit` `MultiEdit` `NotebookEdit` のルールは、payload に来た
`file_path` そのものではなく、絶対パスに直し `..` を畳みシンボリックリンクを解いた
結果に当てる。守る対象は名前ではなく場所なので、同じ場所を指す別の綴りで
ルールを外せてはいけない。

```sh
.env                  # どれも同じ判定に行き着く
./.env
docs/../.env
/abs/path/to/.env
```

相対パスは payload の `cwd` から決まる。まだ存在しないファイルへの書き込みは
解けないので、絶対パスにして `..` を畳むところまでで止める。

## 動作モード

| 値 | 挙動 |
|---|---|
| `block` | 判定し、該当すれば止める（ブロック）。指定が無いときはこれ |
| `warn` | 同じ判定を行い、止めずに「block なら止めていた」と伝える（警告） |
| `off` | 判定しない |

`off` だけは `.claude/settings.json` に書いても効かない。名指しで無視して理由を出す。
その設定ファイルは作業ツリーの中にあってエージェントが書き換えられ、変更は次の
ツール呼び出しから効く。監視される側が監視を止められる経路を残さないための扱い。
`off` にするときはセッションを起動する側の環境から渡す。

## 記録

判定した呼び出しは、通したものも含めて 1 行 1 件で追記される。

```
{"ts":"...","mode":"warn","event":"PreToolUse","tool":"Bash",
 "subject":"git push origin main","decision":"deny","enforced":false,
 "rules":["git-push"],"session":"...","ms":0.9}
```

`decision` は下した判定、`enforced` は実際に適用したか。`warn` は
`deny` かつ `enforced:false` になるので、1 つのファイルで「何を止めるはずだったか」と
「何を実際に止めたか」の両方が数えられる。

判定しなかった回は `decision` が `skip` になり、`reason` が付く。
`mode-off`、`event-not-checked`、`no-subject`、`payload-unusable`、
`rules-unreadable`、`deadline-exceeded` の 6 つ。
通した回も残すのは、記録が無いことを「ccnavi が動かなかった」と読めるようにするため。

コマンドを読み切れずに生の文字列で判定した回は、判定を下したうえで `degraded` が付く。
`command-taken-as-code` と `unterminated-quote` の 2 つ。
止めたもののうち、どれだけが読み切れないまま出た判定かを後から数えられる。

## 設定の検証

`--lint` は判定を行わず、防御を無効化しうる記述だけを報告する。payload を読まないので
手で叩ける。ルールを書き換えたあとと、CI で走らせる。

```sh
ccnavi --lint                                  # 実運用と同じ設定を見る
ccnavi --lint --rules .claude/ccnavi/next.json # 入れ替える前のファイルを見る
```

```
ccnavi: 設定を検証する
  ルール: /repo/.claude/ccnavi/rules.json
  モード: warn（この起動の環境から解決したもの）
warn: (mode): warn なので判定しても呼び出しを止めない
error: no-message: 文面が無い。ルールは代わりに何をすべきかを言わなければならない
error: both: pattern と regex の両方がある。どちらで判定するのか決められない
warn: (id 無し: Task|Bash curl *): match の Task には当てる対象が無い。何も止まらない
error 2 件、warn 2 件
```

`error` はガードが働かない、あるいは働きすぎて全部を止める記述で、1 件でもあれば
終了コードは 1 になる。`warn` は判定そのものは動くが、書いた人が意図した防御が
効いていない記述で、終了コードは 0 のまま。CI が落とす対象は `error` だけでよい。

| 深刻度 | 拾うもの |
|---|---|
| error | ルールファイルが読めない、JSON として壊れている、版番号が違う |
| error | 文面・`match`・`pattern` を欠いたルール、`pattern` と `regex` の両方があるルール |
| error | 組み立てられない正規表現、読み込み時に弾いている先読み・後読み・後方参照 |
| error | 組み立てられたルールが 1 件も無い |
| error | `.claude/settings.json` の `env` が `CCNAVI_MODE=off` を宣言している |
| warn | モードが `off` / `warn`、あるいはモードとして読めない値 |
| warn | 上書き設定ファイルが読めない |
| warn | `id` の無いルール、`id` が重複するルール |
| warn | 判定が対象を取り出せないツールを `match` に書いたルール |

ルールの読み込みは判定と同じ経路を使う。検証だけが別の読み方をすると、
検証は通ったのに実運用で落ちるという、検証があるぶんかえって危ない形になる。

見るのは検証を起動した環境であって、セッションが受け取る環境ではない。
`.claude/settings.json` の `env` は Claude Code がセッションのプロセスに渡すもので、
端末から叩いた検証には入っていない。だから報告はモードがどこから来たかを名乗る。

## 構成

| 場所 | 中身 |
|---|---|
| `main.py` | 配布物の入口。PyInstaller が渡すスクリプト |
| `ccnavi/hookio.py` | stdin の payload の解釈と、stdout に返す応答の組み立て |
| `ccnavi/rules.py` | ルールファイルの読み込みと検証 |
| `ccnavi/pattern.py` | やさしい記法から正規表現への翻訳 |
| `ccnavi/shellread.py` | コマンド文字列のうち実際に実行される部分の切り出し |
| `ccnavi/settings.py` | 環境と設定ファイルからの設定解決 |
| `ccnavi/audit.py` | 1 行 1 件の追記記録 |
| `ccnavi/lint.py` | 設定とルールの検証。判定を行わない |
| `ccnavi/cli.py` | 引数と入力を 1 つの判定に繋ぐ |
| `build.py` | 配布物の組み立て |
| `tests/` | 受入テスト。内部の関数は呼ばず、標準入出力と終了コードだけを見る |
| `testdata/rules.json` | テスト用のルール |

## 配布物の条件

- 実行ファイル 1 つとその同梱物 1 フォルダ。使う側にランタイムの導入を求めない
- 判定の間に外部プロセスを起こさず、ネットワークへ出ない
- 作業ディレクトリに依らず同じ入力に同じ判定を返す
