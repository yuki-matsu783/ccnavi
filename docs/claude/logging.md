---
type: guide
title: 診断ログを書く
description: sh、Python、TypeScriptで診断ログを書くときの決まり。形式、置き場、レベル
tags: [records, state]
keywords: [診断ログ, ログ, logger, sh, Python, TypeScript, logfmt, ccnavi-common.sh, diaglog]
---

# 診断ログを書く

診断ログは、あとで何が起きたかを調べるために残す。sh・Python・TypeScriptで診断ログを書くときは、次の決まりに従う。
loggerは言語ごとに1つずつあり、どれも同じ形式の行を同じ置き場に書く。

| 言語 | logger | 呼び方 |
|---|---|---|
| sh（`.ccnavi/scripts/`） | `ccnavi-common.sh`の`log_debug` `log_info` `log_warn` `log_error` | `log_info "本文" -- key=value` |
| Python（`ccnavi/`） | `ccnavi/diaglog.py` | `diaglog.get("<出どころ>", root).info("本文", key=value)` |
| TypeScript（拡張） | `vscode-extension/ccnavi-board/src/log.ts` | `diaglog.get("ccnavi-board", root).error("本文", { key: value })` |

## 契約として決まっている出力とは分ける

診断ログは**標準出力にも標準エラーにも出さない**。次の出力は、ユーザ・モデル・テストが読むことを前提に形が決まっている。
loggerとは関係なく今のまま出し、loggerに置き換えない。文面も終了コードも変えない。

- hookの標準エラー（`ccnavi: …`）とexit 2の差し戻し、hookの標準出力のJSON
- `ccnavi-git.sh`の`reject`の文面と、結果の1行目（`ok  git …` / `fail  git …`）
- `ccnavi-review.sh`の`fail`の文面と`OK: …`の結果行
- `ccnavi-fetch.sh`の標準出力（モデルの文脈に入る）
- 判定の記録`logs/decisions.jsonl`（audit）と、`ccnavi-git.sh`が全量を残す`logs/git-*.log`

これらを出した箇所で、同じ出来事を診断ログにも書いてよい（`reject`と`fail`はそうしている）。
ただし診断ログには文面を写さず、拒否の種類を表す短い識別子だけを書く。識別子は`reason=unapproved-push`や
`reason=reset`のように`[a-z-]`の文字で書く。文面にはユーザが渡した引数（URL・パス）が入るので、資格情報が混ざることがある。
`reject`と`fail`は1つ目の引数に識別子を、2つ目に文面を受け取る（`fail`は3つ目で終了コードを受け取る）。

## 行の形式

```
2026-09-27T10:15:03+09:00 INFO  ccnavi-git[4242] 拒否した sub=push reason=unapproved
```

- 時刻・レベル・出どころ・pidはloggerが付けるので、本文には書かない
- 時刻は現地時刻に時差を付け、秒まで書く。BSDのdateはミリ秒を出せないので、3つのloggerとも秒までにしている
- レベルは5文字の左寄せで書く（`DEBUG` `INFO ` `WARN ` `ERROR`）
- 本文の後ろに`key=value`をlogfmt形式で並べる。値に空白・タブ・`"`・`=`・改行が入るときだけ`"…"`で囲み、
  中の`\`と`"`は`\`でエスケープする。本文と値の改行は`\n`の2文字に置き換え、1つの出来事を1行に収める
- 値は文字列にして並べる。PythonとTSの真偽値は`true`/`false`、`None`/`null`/`undefined`は空文字にする。
  小数は言語によって表記が違う（`1.0`と`1`）ので、整数か文字列で渡す

## 置き場とレベル

- 置き場はワークスペースルートの`logs/diag/<出どころ>.log`で、無ければ作る（`logs/`は.gitignoreに入っている）。
  ワークスペースルートの決め方は言語ごとに次のとおり
  - sh: 呼び出し側が`ccnavi_log_root`に入れた値を使う。空なら`ccnavi_workspace`で1度だけ探す
    （`CCNAVI_WORKSPACE`を使い、無ければcwdから上へたどる）。ルートを求め終えているスクリプト（`ccnavi-git.sh`の`WS`、
    `ccnavi-review.sh`の`root`）は、求めた直後に`ccnavi_log_root`へ入れ、loggerに探し直させない。出どころは
    `$0`のファイル名から拡張子を除いたもの（`ccnavi-git`）で、`CCNAVI_LOG_NAME`で上書きできる
  - Python: 呼び出し側が`get(name, root)`にルートを渡す。`root`を省くと`CLAUDE_PROJECT_DIR`を使い、
    どちらも無ければ書かない。実行ファイルは`cli`の`root`（`--root`か`default_root()`）を渡す
  - TS: 呼び出し側がワークスペースフォルダのパス（`folder.uri.fsPath`）を渡す。空なら書かない
- 出どころの名前は固定の短い語にする（`ccnavi`・`ccnavi-git`・`ccnavi-board`）。パスやユーザの入力は入れない。
  名前に`[A-Za-z0-9_-]`以外の文字（`/`・`\`・`.`・空白）が入っていたら、3つのloggerとも**書き込まずに捨てる**。
  別の文字に置き換えると他の出どころの名前と重なり、どこから来た行かを取り違えるので、置き換えはしない
- **シンボリックリンクはたどらない。** `logs`・`logs/diag`・書き込み先の`<出どころ>.log`のどれかがシンボリックリンクなら、
  3つのloggerとも書き込まずに捨てる。リンク先へ追記すると、置き場の外のファイル（`logs/decisions.jsonl`など）を
  書き換えてしまうため。確かめ方は言語ごとに違い、shは`[ -L ]`、Pythonはlstatと`O_NOFOLLOW`（無いOSではlstatだけ）、
  TSは`lstatSync`と、使えるOSでは`O_NOFOLLOW`を使う。`logs`を別の場所へのリンクにしている環境では、
  診断ログは残らない。片付け（`_prune_diag`）もこの場合はリンクをたどらず、報告に出す
- 新しいファイルは持ち主だけが読める0600で作る（shはumask 077のサブシェル、Pythonは`os.open`のmode、
  TSは`openSync`のmodeで指定する）。既にあるファイルの権限は変えない
- 出力するレベルは`CCNAVI_LOG_LEVEL`に`DEBUG`/`INFO`/`WARN`/`ERROR`で指定する（大文字小文字は問わない）。
  空のときと解釈できない値のときは`INFO`になる
- 古いログの片付けは`ccnavi/prune.py`の`_prune_diag`が行い、セッションの開始時と`ccnavi --prune`の実行時に動く。
  しきい値は判定の記録と同じものを使う。`CCNAVI_LOG_ROTATE_MB`を超えたファイルは`<出どころ>.<日時>.log`に
  名前を変え、`CCNAVI_LOG_KEEP_DAYS`の日数のあいだ更新されていない`*.log`は消す。その回に名前を変えたファイルは、
  その回には消さない（`--prune --preview`も同じ結果を表示する）

`logs/diag/`は自己防衛（selfguard）の対象にしない。診断ログは判定に使わない補助の記録なので、エージェントが消したり
書き換えたりしても、止めるか通すかの判定は変わらない。守る利点より、hookが走るたびに控えを取る手間の方が大きい。
シンボリックリンクを使って置き場の外のファイルを書き換えさせる手口は、loggerがリンクをたどらないことで防いでいる。

## shからの呼び方

```sh
log_info 拒否した -- "sub=$sub" "reason=$1"   # $1 は reject の識別子（文面ではない）
log_debug 判定の材料 -- "sub=$sub" "top=$root" "cwd=$PWD"
```

- `--`より前の引数は本文で、スペースでつないで1つの本文にする
- `--`より後ろは、1つの引数に1つの`key=value`を書く。値に空白があっても、引数全体を`"…"`で囲めば1つの値になる。
  `=`の無い引数は、値が空のキーとして扱う
- loggerは常に0を返すので、`set -eu`の下でもスクリプトは止まらない。`|| :`を付ける必要は無い
- 1行を書くときに起動する外部コマンドは、`date`と、置き場が無いときの`mkdir`だけ。ほかには、ファイルを
  新しく作るときだけumask用のサブシェルを1つ起動する。`ccnavi_log_root`が空なら、最初の1行を書くときに
  `ccnavi_workspace`（`dirname`などを起動する）が走る。また、読み込み時にCRを作るための`$(printf '\r')`を1回実行する

## 資格情報を伏せる

3つのloggerは、本文（`--`より前）と値の両方で、資格情報を同じ規則で伏せる。空白・タブ・LF・CRで区切った語ごとに判定する。

| 対象 | 入力の例 | 出力 |
|---|---|---|
| `://`を含む語で、authority（`://`の後ろから次の`/`まで）に`@`がある | `https://user:tok@host/x` | `https://***@host/x` |
| `://`を含まない語で、最初の`/`より前にある最後の`@`よりさらに前に`:`がある（scp形式） | `oauth2:tok@host:org/r.git` | `***@host:org/r.git` |
| scp形式でも`:`が無いもの | `git@github.com:org/r.git` | 変えない |

- `@`を含まない文字列はそのまま返す（shは語に区切らずに返す）
- 出力は3つのloggerで1文字も違わない（`tests/sh/test_diaglog_sh.py`の比較にURLとscp形式を入れてある）
- ユーザやモデルに見せる文面での伏せ方（`ccnavi_mask_url`の`<伏せた>@host`）とは表記が違う。そちらは
  契約として決まっている出力なので変えない
- loggerが伏せるのは最後の安全網にすぎない。まず秘密の値をloggerに渡さないことで守る

伏せ方には、3つのloggerで揃っていない部分もある。Pythonはさらに`ccnavi/redact.py`の`redact`を通し、
トークンの形（`ghp_…`・`glpat-…`・`sk-…`など）、`名前=値`（`GITHUB_TOKEN=…`）、Authorizationヘッダ、`--password`などを
`ghp_ab***6789`や`***`に伏せる。shとTSにはこの処理が無く、上の2つの形式だけを伏せる。
shで`redact`を再現すると、正規表現の解釈が環境ごとに違ううえに処理も重くなる。TSには秘密を扱う場面が無い。
そのため、shとTSには持たせていない。

## 書くこと

- **独自のログの仕組みを作らない。** `printf … >>logs/…`や`print`や`console.log`で診断を書かず、loggerを使う。
  loggerに書いた出来事を、`console.error`などにも重ねて出さない（拡張の4つの画面もそうしている）
- 1行に1つの出来事を書く。何をどうしたかを本文に、判断に必要な値を`key=value`に書く
- レベルは次のように使い分ける
  - `INFO` 受け付けた操作、判定の結果、拒否の理由、最終結果（終了コード）
  - `DEBUG` 判定の材料（どのブランチ・どのツリー・どの経路で読んだか）
  - `WARN` 処理を続けられる異常（読めない入力を捨てて先へ進んだ、など）
  - `ERROR` 処理が止まる失敗
- 既定の`INFO`では、1回の起動につき数行に収める。ループの中では`INFO`を書かない
- ツールを呼ぶたびに走るhook（ccnaviの実行ファイルの判定など）は、判定の結果も`DEBUG`で書く。
  既定の`INFO`のままでhookの呼び出し回数だけ行が増えることを避ける
- 出力しないレベルの呼び出しでは、loggerは時刻も行も作らない。**`DEBUG`の引数を組み立てるために外部コマンドを
  起動しない**（`$(git …)`や`$(date)`を`log_debug`の引数に書かず、`$PWD`のように手元にある値を使う）。
  重い材料が必要なときは、Pythonなら`log.enabled(diaglog.DEBUG)`、TSなら`log.enabled("DEBUG")`の条件の中で組み立てる

## 書かないこと

- 環境変数の値、トークン、クレデンシャル、個人情報、ファイルの中身、コマンドの全文（hookの`subject`）、
  コミットや依頼の本文は書かない。必要なら有無・長さ・件数を書く（`token=set`、`body_len=1234`）
- 伏せる前の値を書かない。loggerは本文と値の資格情報を伏せる（上の「資格情報を伏せる」）が、それは最後の安全網で、
  まず値を渡さないことが先になる
- ログを書けたかどうかで処理を分けない。置き場を作れない、権限が無い、容量が足りないなどの理由で書けないときは、
  loggerは何も出さずに捨てる。本体の出力と終了コードは変わらない

## テスト

- 各スクリプトやモジュールのテストでは、診断ログの中身を検査しない。行の形式と動作は、logger自体のテスト
  （`tests/core/test_diaglog.py`、`tests/sh/test_diaglog_sh.py`、拡張の`test/shared/log.test.ts`）で固定している
- `tests/sh/test_diaglog_sh.py`は同じ入力を3つの言語のloggerに渡し、時刻とpidを除いて同じ行になることを確かめる。
  行の形式を変えるときは3つのloggerを揃えて直し、このテストを通す
- ワークスペースの`logs/`の中身を数えるテストは、`logs/diag/`ディレクトリが増えることを前提にする
