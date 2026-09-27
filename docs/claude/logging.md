# 診断ログを書く

sh・Python・TypeScript で「あとから何が起きたかを追う」ための行を書くときの決まり。
logger は 3 つの言語に 1 つずつあり、どれも同じ形の行を同じ置き場に出す。

| 言語 | logger | 呼び方 |
|---|---|---|
| sh（`.ccnavi/scripts/`） | `ccnavi-common.sh` の `log_debug` `log_info` `log_warn` `log_error` | `set -eu` の直後に共通部を読む（既にどの sh も読んでいる） |
| sh（開発用の hook `.claude/hooks/`） | 同じ | 下の「開発用の hook」の形で、読めたときだけ読む |
| Python（`ccnavi/`） | `ccnavi/diaglog.py` | `diaglog.get("<出どころ>", root).info("本文", key=value)` |
| TypeScript（拡張） | `vscode-extension/ccnavi-board/src/log.ts` | `diaglog.get("ccnavi-board", root).error("本文", { key: value })` |

## 契約の文面とは別物

診断ログは**標準出力にも標準エラーにも出さない**。次のものは利用者・モデル・テストとの契約で、
logger とは関係なく今のまま書く。logger に置き換えない。文面も終了コードも変えない。

- hook の標準エラー（`ccnavi: …`）と exit 2 の差し戻し、hook の標準出力の JSON
- `ccnavi-git.sh` の `reject` の文面と、結果の 1 行目（`ok  git …` / `fail  git …`）
- `ccnavi-review.sh` の `fail` の文面と `OK: …` の結果行
- `ccnavi-fetch.sh` の標準出力（モデルの文脈に入る）
- 判定の記録 `logs/decisions.jsonl`（audit）と、`ccnavi-git.sh` が全量を残す `logs/git-*.log`

契約の文面を出した場所で同じ事象を診断ログにも残すのはよい（`reject` と `fail` はそうしている）。
ただし診断ログには文面を写さず、拒否の種類を表す短い識別子（`reason=unapproved-push`・`reason=reset` など、
`[a-z-]`）だけを書く。文面には利用者の引数（URL・パス）が入り、資格情報が混ざることがある。
`reject` と `fail` は 1 つ目の引数に識別子を取り、文面は 2 つ目（`fail` の終了コードは 3 つ目）。
`ccnavi-approve.sh`・`ccnavi-ticket.sh`・`ccnavi-clean.sh` の `fail` も同じ形で、終了コードは省かずに書く。
標準エラーに複数行を出して止まるところ（使い方を続けて出すなど）は `fail` を通さず、文面を出した後に
`log_info 止めた -- "exit=2" "reason=<識別子>"` を書いてから `exit` する。`reason=` の値は変数にせず、固定の語で書く
（`tests/sh/test_diaglog_scripts.py` が綴りを見る）。

## 行の形

```
2026-09-27T10:15:03+09:00 INFO  ccnavi-git[4242] 拒否した sub=push reason=unapproved
```

- 時刻・レベル・出どころ・pid は logger が付ける。本文に書かない
- 時刻は現地時刻と時差、秒まで（BSD の date がミリ秒を出せないので 3 つとも秒）
- レベルは 5 字の左寄せ（`DEBUG` `INFO ` `WARN ` `ERROR`）
- 本文の後ろに `key=value` を logfmt で並べる。値に空白・タブ・`"`・`=`・改行を含むときだけ `"…"` で
  囲み、中の `\` と `"` を `\` で逃がす。本文と値の改行は `\n` の 2 字に畳む（1 事象 = 1 行）
- 値は文字にして並べる。Python と TS の真偽は `true` / `false`、`None` / `null` / `undefined` は空。
  小数は言語で綴りが割れる（`1.0` と `1`）ので、整数か文字で渡す

## 置き場とレベル

- ワークスペースルートの `logs/diag/<出どころ>.log`（`logs/` は .gitignore 済み）。無ければ作る
  - sh: ルートは呼ぶ側が `ccnavi_log_root` に入れたもの。空なら `ccnavi_workspace`（`CCNAVI_WORKSPACE`、
    無ければ cwd から上へ探す）で 1 度だけ探す。既にルートを解いたスクリプト（`ccnavi-git.sh` の `WS`、
    `ccnavi-review.sh` ほかの `root`）は解いた直後に `ccnavi_log_root` へ入れ、探し直させない。出どころは
    `$0` の名前から拡張子を落としたもの（`ccnavi-git`）。`CCNAVI_LOG_NAME` で上書きできる
  - Python: 呼び手が `get(name, root)` に渡す。`root` を省くと `CLAUDE_PROJECT_DIR`。どちらも無ければ書かない。
    実行ファイルは `cli` の `root`（`--root` か `default_root()`）を渡す
  - TS: 呼び手がワークスペースフォルダのパスを渡す（`folder.uri.fsPath`）。空なら書かない
- 出どころの名前は固定の短い語にする（`ccnavi`・`ccnavi-git`・`ccnavi-board`）。パスや利用者の入力を入れない。
  `[A-Za-z0-9_-]` 以外の字（`/`・`\`・`.`・空白）を含む名前では、3 つの logger とも**書かずに捨てる**
  （置き換えはしない。置き換えると別の出どころの名前とぶつかり、どこから来た行か読み違える）
- **リンクは辿らない。** `logs`・`logs/diag`・書き先の `<出どころ>.log` のどれかがシンボリックリンクなら、
  3 つの logger とも書かずに捨てる（sh は `[ -L ]`、Python は lstat と `O_NOFOLLOW`（無い OS では lstat だけ）、
  TS は `lstatSync` と、ある OS では `O_NOFOLLOW`）。リンクの先へ追記すると、置き場の外のファイル
  （`logs/decisions.jsonl` など）を書き換える。`logs` を別の場所へのリンクにしている環境では診断ログは
  残らない。片付け（`_prune_diag`）も同じ場合に辿らず、報告に出す
- 新しいファイルは持ち主だけが読める 0600 で作る（sh は umask 077 のサブシェル、Python は `os.open` の
  mode、TS は `openSync` の mode）。既にあるファイルの権限は変えない
- `CCNAVI_LOG_LEVEL` = `DEBUG` / `INFO` / `WARN` / `ERROR`（大文字小文字は問わない）。空と読めない値は `INFO`
- 片付けは `ccnavi/prune.py` の `_prune_diag`。判定の記録と同じしきい値を使い、`CCNAVI_LOG_ROTATE_MB` を
  超えた 1 本を `<出どころ>.<日時>.log` へ名前を変え、`CCNAVI_LOG_KEEP_DAYS` のあいだ書かれていない
  `*.log` を消す。その回にローテートした 1 本はその回には消さない（`--prune --preview` も同じ結果を示す）。
  走るのはセッションの開始と `ccnavi --prune`
- `logs/diag/` は自己防衛（selfguard）で守らない。診断ログは判定が読まない補助で、エージェントが消したり
  書き換えたりしても止める・通すの判定は変わらない。守る値打ちより、hook のたびに控えを取る手間の方が
  大きい。リンクを張って置き場の外を書き換えさせる道は、logger がリンクを辿らないことで塞ぐ

## sh の呼び方

```sh
log_info 拒否した -- "sub=$sub" "reason=$1"   # $1 は reject の識別子（文面ではない）
log_debug 判定の材料 -- "sub=$sub" "top=$root" "cwd=$PWD"
```

- `--` より前は本文の語で、スペースでつないで 1 つの本文にする
- `--` より後ろは 1 引数に 1 つの `key=value`。値に空白があっても引数ごと `"…"` で括れば 1 つの値になる。
  `=` の無い引数は値の空なキーになる
- 何があっても 0 を返す。`set -eu` の下でも止まらない。`|| :` を付ける必要は無い
- 1 行を書くときに起こす外部コマンドは `date` と、置き場が無いときの `mkdir` だけ。ほかに、ファイルを
  新しく作るときだけ umask 用のサブシェルを 1 つ起こし、`ccnavi_log_root` が空なら最初の 1 行で
  `ccnavi_workspace`（`dirname` などを起こす）が走る。読み込むときに CR を作る `$(printf '\r')` が 1 つある

## 開発用の hook

`.claude/hooks/` の hook は、ワークスペースでない場所や `CLAUDE_PROJECT_DIR` の無い呼ばれ方でも同じに動く必要がある。
共通部は読めたときだけ読み、読めないときは `log_*` を何もしない関数のまま残す。hook の標準エラー・終了コード・
`logs/session/` の状態ファイルは変えない。

```sh
log_debug() { :; }
log_info() { :; }
case "${CLAUDE_PROJECT_DIR:-}" in
/* | [A-Za-z]:*)
	if [ -f "$CLAUDE_PROJECT_DIR/.ccnavi/scripts/ccnavi-common.sh" ] && [ -r "$CLAUDE_PROJECT_DIR/.ccnavi/scripts/ccnavi-common.sh" ]; then
		. "$CLAUDE_PROJECT_DIR/.ccnavi/scripts/ccnavi-common.sh"
		ccnavi_log_root="$CLAUDE_PROJECT_DIR"
	fi
	;;
esac
```

- 読むのは、`.py` でない・拡張の外、などで早く抜ける分かれ道の後。抜ける呼び出しに共通部を読む重さを足さない
- Stop の hook（`test-py.sh`・`test-ext.sh`）は、通した・差し戻した・上限に達した・回さなかったを `INFO` で書く。
  拡張を触っていないターン（毎回の Stop）は `DEBUG`
- 編集のたびに走る hook は `DEBUG` だけ（`mark-ext.sh`）。`lint-py.sh` は通した側を `DEBUG`、差し戻しだけを `INFO`

## TypeScript（拡張）の書き方

- 実行ファイルの呼び出しは `ccnavi.ts` の `run` / `runScript` がまとめて 1 行書く。失敗・打ち切り・起動できないは
  `ERROR`、成功は `DEBUG` と所要時間（ms）。`sub` にはサブコマンド名を入れ、標準出力と標準エラーは中身を書かず
  長さ（`stdout_len`・`stderr_len`）だけを書く。呼び手の側で同じ事象を重ねて書かない
- core（`src/core/`）は logger を持たない純粋な層。パースの `ok: false` は、呼び手が `kind=json|shape` と長さで書く

## 伏せ字

3 つの logger は本文（`--` より前）と値の両方に、同じ伏せ字を当てる。空白・タブ・LF・CR で切った語ごとに見る。

| 形 | 例 | 書かれる形 |
|---|---|---|
| `://` を含む語。authority（`://` の後ろから次の `/` まで）に `@` がある | `https://user:tok@host/x` | `https://***@host/x` |
| `://` を含まない語。最初の `/` より前の最後の `@` より前に `:` がある（scp 形） | `oauth2:tok@host:org/r.git` | `***@host:org/r.git` |
| scp 形でも `:` が無いもの | `git@github.com:org/r.git` | そのまま |

- `@` を含まない文字列はそのまま（sh は語に切らずに返す）
- 綴りは 3 つで 1 字まで同じ（`tests/sh/test_diaglog_sh.py` の突き合わせに URL と scp 形を入れてある）
- **揃っていないところ:** Python はこのあとに `ccnavi/redact.py` の `redact` も通すので、トークンの形
  （`ghp_…`・`glpat-…`・`sk-…` など）・`名前=値`（`GITHUB_TOKEN=…`）・Authorization ヘッダ・`--password` などを
  `ghp_ab***6789` や `***` に伏せる。sh と TS はこれを持たず、上の 2 つの形だけを伏せる。sh で `redact` を
  写すと正規表現の読みが割れて重く、TS は秘密を扱う場面が無いため
- 利用者やモデルに見せる文面の伏せ字（`ccnavi_mask_url` の `<伏せた>@host`）とは綴りが違う。そちらは
  契約の文面なので変えない
- 伏せ字は最後の網で、一次の守りは「秘密の値を渡さない」

## 書くこと

- **独自のログ方式を作らない。** `printf … >>logs/…` や `print` や `console.log` で診断を書かず、logger を使う。
  logger に書いた事象を `console.error` などへ重ねて出さない（拡張の 4 画面もそうしている）
- 1 行 1 事象。「何を・どうした」を本文に、判断に要る値を `key=value` に書く
- レベルの使い分け
  - `INFO` 受け付けた操作、判定の結果、拒否の理由、最終の結果（終了コード）
  - `DEBUG` 判定の材料（どのブランチ・どのツリー・どの経路で読んだか）
  - `WARN` 続行できる異常（読めない入力を捨てて進んだ、など）
  - `ERROR` 処理を止める失敗
- 既定の `INFO` で、1 回の起動につき数行に収める。ループの中で `INFO` を書かない。ただし 1 周が利用者の
  頼んだ 1 つの操作で、周の数が「実際に動かしたもの」に限られるときは、周ごとの結果を `INFO` にしてよい
  （`ccnavi-push-approved.sh` の、変更のあったツリーごとのコミットと push）
- 最終の結果（終了コード）を残すために、最後の `exec` をやめて子として走らせ、終了コードをそのまま返してよい
  （`ccnavi-ticket.sh`・`ccnavi-clean.sh`）。標準出力・標準エラー・終了コードは変えない。関数を `|| x=$?` で呼ぶと
  関数の中で `set -e` が外れるので、そのときは `trap '…' EXIT` で抜けるときに書く
- ツール呼び出しのたびに走る hook（ccnavi の実行ファイルの判定など）は、判定の結果も `DEBUG` に置く。
  既定の `INFO` で hook の呼び出しの数だけ行を増やさない
- 出さないレベルでは logger が時刻も行も作らない。**`DEBUG` の引数を組み立てるために外部コマンドを
  起こさない**（`$(git …)` や `$(date)` を `log_debug` の引数に書かない。`$PWD` のように手元にある値を使う）。
  重い材料が要るときは Python なら `log.enabled(diaglog.DEBUG)`、TS なら `log.enabled("DEBUG")` で囲む

## 書かないこと

- 環境変数の値、トークン、クレデンシャル、個人情報、ファイルの中身、コマンドの全文（hook の `subject`）、
  コミットや依頼の本文。要るなら有無・長さ・件数を書く（`token=set`、`body_len=1234`）
- 伏せる前の値を先に書かない。logger が本文と値に伏せ字（上の「伏せ字」）を当てるが、それは最後の網で、
  渡さないのが先
- ログの成否で分岐しない。書けないとき（置き場が作れない・権限・容量）は logger が黙って捨て、
  本体の出力と終了コードは変わらない

## テスト

- 各スクリプト・モジュールのテストで診断ログの中身を検査しない。行の形と振る舞いは logger 自体のテストが
  固定している（`tests/core/test_diaglog.py`、`tests/sh/test_diaglog_sh.py`、拡張の `test/shared/log.test.ts`）
  - 例外: 秘密を書かないこと（資格情報や標準エラーの本文が行に入らないこと）の確認は、失敗経路のテストで
    診断ログの中身を見てよい
  - 例外: 拒否の識別子の綴り（kebab-case の固定の語であること、文面を写さないこと）は、識別子を書く sh ごとに
    `tests/sh/test_diaglog_sh.py`（`ccnavi-git.sh`・`ccnavi-review.sh`）と `tests/sh/test_diaglog_scripts.py`（ほかの sh と開発用の hook）が見る
- `tests/sh/test_diaglog_sh.py` は同じ入力を 3 つの言語に渡し、時刻と pid を除いて同じ行になることを見る。
  logger の形を変えるときは 3 つを揃えて直し、このテストを通す
- ワークスペースの `logs/` を数えるテストは、`logs/diag/`（ディレクトリ）が増えることを前提にする

## logger を使わないもの

使うかどうかを見たうえで、使わないと決めたもの（ADR-0093）。ここに無いものは使う。

| もの | 使わない理由 |
|---|---|
| `.ccnavi/scripts/ccnavi-launcher.sh` | hook ごとに実行ファイルへ `exec` するだけのホットパス。共通部を読む重さ（CR を作るサブシェルなど）を hook の呼び出しのたびに足さない。起動された実行ファイルが自分で書く |
| `scripts/ccnavi-setup.sh`・`scripts/ccnavi-build-install.sh`・`vscode-extension/ccnavi-board/scripts/package.sh` と拡張の `scripts/*.js` | 配布前か手元の道具。出力そのものが利用者向けの報告で、ワークスペースの `logs/` がまだ無い場面でも動く |
| `.ccnavi/scripts/ccnavi-clean.js` | 同じく出力が利用者向けの報告。呼ぶ側の `ccnavi-clean.sh` が受け付けと最終の結果を書く |
| `build.py`・`tools/run_tests.py` | 手元の道具で、出力が利用者向けの報告 |
| `logs/session/` の状態ファイル（`<セッション>.trees`・`.ext-files`・`.retries` など） | ログではなく、開発用の hook のあいだで受け渡す状態。形も置き場も変えない |
| 拡張の webview | Node の `fs` が無い。要るなら親のパネル（拡張ホスト）へ送り、そちらで書く |
| `tools/gitlab/probe_gitlab.py` の `steps.log` | 調査道具の成果物。利用者が読むために残す報告で、診断ログではない |
