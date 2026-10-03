---
type: guide
title: 診断ログを書く
description: sh、Python、TypeScript で診断ログを書くときの決まり。形式、置き場、レベル
tags: [records, state]
keywords: [診断ログ, ログ, logger, sh, Python, TypeScript, logfmt, ccnavi-common.sh, diaglog]
---

# 診断ログを書く

sh・Python・TypeScriptで「あとから何が起きたかを追う」ための行を書くときの決まりをまとめる。
loggerは言語ごとに1つずつあり、どれも同じ形の行を同じ置き場に出す。

| 言語 | logger | 呼び方 |
|---|---|---|
| sh（`.ccnavi/scripts/`） | `ccnavi-common.sh`の`log_debug` `log_info` `log_warn` `log_error` | `set -eu`の直後に共通部を読む（どのshも既に読んでいる） |
| Python（`ccnavi/`） | `ccnavi/diaglog.py` | `diaglog.get("<出どころ>", root).info("本文", key=value)` |
| TypeScript（拡張） | `vscode-extension/ccnavi-board/src/log.ts` | `diaglog.get("ccnavi-board", root).error("本文", { key: value })` |

## 契約の文面とは別物

診断ログは**標準出力にも標準エラーにも出さない**。次のものは利用者・モデル・テストとの契約なので、
loggerとは関係なく今のまま書く。loggerに置き換えず、文面も終了コードも変えない。

- hookの標準エラー（`ccnavi: …`）とexit 2の差し戻し、hookの標準出力のJSON
- `ccnavi-git.sh`の`reject`の文面と、結果の1行目（`ok  git …` / `fail  git …`）
- `ccnavi-review.sh`の`fail`の文面と`OK: …`の結果行
- `ccnavi-fetch.sh`の標準出力（モデルの文脈に入る）
- 判定の記録`logs/decisions.jsonl`（audit）と、`ccnavi-git.sh`が全量を残す`logs/git-*.log`

契約の文面を出した場所で、同じ事象を診断ログにも残してよい（`reject`と`fail`はそうしている）。
ただし診断ログには文面を写さず、拒否の種類を表す短い識別子だけを書く。識別子は`reason=unapproved-push`や
`reason=reset`のように`[a-z-]`で綴る。文面には利用者の引数（URL・パス）が入るので、資格情報が混ざることがある。
`reject`と`fail`は1つ目の引数に識別子を、2つ目に文面を取る（`fail`は3つ目に終了コードを取る）。

## 行の形

```
2026-09-27T10:15:03+09:00 INFO  ccnavi-git[4242] 拒否した sub=push reason=unapproved
```

- 時刻・レベル・出どころ・pidはloggerが付けるので、本文には書かない
- 時刻は現地時刻に時差を添えて、秒まで書く。BSDのdateがミリ秒を出せないので、3つとも秒にそろえている
- レベルは5字の左寄せで書く（`DEBUG` `INFO ` `WARN ` `ERROR`）
- 本文の後ろに`key=value`をlogfmtで並べる。値に空白・タブ・`"`・`=`・改行を含むときだけ`"…"`で囲み、
  中の`\`と`"`を`\`で逃がす。本文と値の改行は`\n`の2字に畳み、1事象を1行に収める
- 値は文字にして並べる。PythonとTSの真偽は`true`/`false`にし、`None`/`null`/`undefined`は空にする。
  小数は言語によって綴りが割れる（`1.0`と`1`）ので、整数か文字で渡す

## 置き場とレベル

- 置き場はワークスペースルートの`logs/diag/<出どころ>.log`で、無ければ作る（`logs/`は.gitignore済み）。
  ルートの決め方は言語ごとに次のとおり
  - sh: 呼ぶ側が`ccnavi_log_root`に入れたものをルートにする。空なら`ccnavi_workspace`で1度だけ探す
    （`CCNAVI_WORKSPACE`を見て、無ければcwdから上へ探す）。既にルートを解いたスクリプト（`ccnavi-git.sh`の`WS`、
    `ccnavi-review.sh`の`root`）は、解いた直後に`ccnavi_log_root`へ入れて探し直させない。出どころは
    `$0`の名前から拡張子を落としたもの（`ccnavi-git`）で、`CCNAVI_LOG_NAME`で上書きできる
  - Python: 呼び手が`get(name, root)`にルートを渡す。`root`を省くと`CLAUDE_PROJECT_DIR`を使い、
    どちらも無ければ書かない。実行ファイルは`cli`の`root`（`--root`か`default_root()`）を渡す
  - TS: 呼び手がワークスペースフォルダのパス（`folder.uri.fsPath`）を渡す。空なら書かない
- 出どころの名前は固定の短い語にする（`ccnavi`・`ccnavi-git`・`ccnavi-board`）。パスや利用者の入力は入れない。
  `[A-Za-z0-9_-]`以外の字（`/`・`\`・`.`・空白）を含む名前では、3つのloggerとも**書かずに捨てる**。
  別の字に置き換えると他の出どころの名前とぶつかり、どこから来た行かを読み違えるので、置き換えはしない
- **リンクは辿らない。** `logs`・`logs/diag`・書き先の`<出どころ>.log`のどれかがシンボリックリンクなら、
  3つのloggerとも書かずに捨てる。リンクの先へ追記すると、置き場の外のファイル（`logs/decisions.jsonl`など）を
  書き換えるからだ。確かめ方は、shが`[ -L ]`、Pythonがlstatと`O_NOFOLLOW`（無いOSではlstatだけ）、
  TSが`lstatSync`と、あるOSでは`O_NOFOLLOW`を使う。`logs`を別の場所へのリンクにしている環境では、
  診断ログは残らない。片付け（`_prune_diag`）も同じ場合はリンクを辿らず、報告に出す
- 新しいファイルは持ち主だけが読める0600で作る（shはumask 077のサブシェル、Pythonは`os.open`のmode、
  TSは`openSync`のmodeで指定する）。既にあるファイルの権限は変えない
- `CCNAVI_LOG_LEVEL`には`DEBUG`/`INFO`/`WARN`/`ERROR`を指定する（大文字小文字は問わない）。
  空の値と読めない値は`INFO`として扱う
- 片付けは`ccnavi/prune.py`の`_prune_diag`が受け持ち、セッションの開始時と`ccnavi --prune`で走る。
  しきい値は判定の記録と同じものを使う。`CCNAVI_LOG_ROTATE_MB`を超えた1本は`<出どころ>.<日時>.log`へ
  名前を変え、`CCNAVI_LOG_KEEP_DAYS`のあいだ書かれていない`*.log`は消す。その回にローテートした1本は、
  その回には消さない（`--prune --preview`も同じ結果を示す）

`logs/diag/`は自己防衛（selfguard）で守らない。診断ログは判定が読まない補助なので、エージェントが消したり
書き換えたりしても、止める・通すの判定は変わらない。守る値打ちより、hookのたびに控えを取る手間の方が大きい。
リンクを張って置き場の外を書き換えさせる道は、loggerがリンクを辿らないことで塞いでいる。

## shの呼び方

```sh
log_info 拒否した -- "sub=$sub" "reason=$1"   # $1 は reject の識別子（文面ではない）
log_debug 判定の材料 -- "sub=$sub" "top=$root" "cwd=$PWD"
```

- `--`より前は本文の語で、スペースでつないで1つの本文にする
- `--`より後ろは、1引数につき1つの`key=value`にする。値に空白があっても、引数ごと`"…"`で括れば1つの値になる。
  `=`の無い引数は、値が空のキーになる
- どんな場合も0を返すので、`set -eu`の下でも止まらない。`|| :`を付ける必要は無い
- 1行を書くときに起こす外部コマンドは、`date`と、置き場が無いときの`mkdir`だけ。ほかには、ファイルを
  新しく作るときだけumask用のサブシェルを1つ起こす。`ccnavi_log_root`が空なら、最初の1行で
  `ccnavi_workspace`（`dirname`などを起こす）が走る。読み込むときにも、CRを作る`$(printf '\r')`が1つある

## 伏せ字

3つのloggerは、本文（`--`より前）と値の両方に同じ伏せ字を当てる。空白・タブ・LF・CRで切った語ごとに判定する。

| 形 | 例 | 書かれる形 |
|---|---|---|
| `://`を含む語で、authority（`://`の後ろから次の`/`まで）に`@`がある | `https://user:tok@host/x` | `https://***@host/x` |
| `://`を含まない語で、最初の`/`より前にある最後の`@`の、さらに前に`:`がある（scp形） | `oauth2:tok@host:org/r.git` | `***@host:org/r.git` |
| scp形でも`:`が無いもの | `git@github.com:org/r.git` | そのまま |

- `@`を含まない文字列はそのまま返す（shは語に切らずに返す）
- 綴りは3つのloggerで1字まで同じ（`tests/sh/test_diaglog_sh.py`の突き合わせにURLとscp形を入れてある）
- 利用者やモデルに見せる文面の伏せ字（`ccnavi_mask_url`の`<伏せた>@host`）とは綴りが違う。そちらは
  契約の文面なので変えない
- 伏せ字は最後の安全網で、一次の守りは「秘密の値を渡さない」こと

3つのloggerで揃っていないところもある。Pythonはこのあとに`ccnavi/redact.py`の`redact`も通し、トークンの形
（`ghp_…`・`glpat-…`・`sk-…`など）、`名前=値`（`GITHUB_TOKEN=…`）、Authorizationヘッダ、`--password`などを
`ghp_ab***6789`や`***`に伏せる。shとTSはこれを持たず、上の2つの形だけを伏せる。shに`redact`を写すと
正規表現の読みが割れて重くなり、TSには秘密を扱う場面が無いためだ。

## 書くこと

- **独自のログ方式を作らない。** `printf … >>logs/…`や`print`や`console.log`で診断を書かず、loggerを使う。
  loggerに書いた事象を`console.error`などへ重ねて出さない（拡張の4画面もそうしている）
- 1行に1事象を書く。「何を・どうした」を本文に、判断に要る値を`key=value`に書く
- レベルは次のように使い分ける
  - `INFO` 受け付けた操作、判定の結果、拒否の理由、最終の結果（終了コード）
  - `DEBUG` 判定の材料（どのブランチ・どのツリー・どの経路で読んだか）
  - `WARN` 続行できる異常（読めない入力を捨てて進んだ、など）
  - `ERROR` 処理を止める失敗
- 既定の`INFO`では、1回の起動につき数行に収める。ループの中で`INFO`を書かない
- ツール呼び出しのたびに走るhook（ccnaviの実行ファイルの判定など）は、判定の結果も`DEBUG`に置く。
  既定の`INFO`で、hookの呼び出しの数だけ行を増やさない
- 出さないレベルでは、loggerは時刻も行も作らない。**`DEBUG`の引数を組み立てるために外部コマンドを
  起こさない**（`$(git …)`や`$(date)`を`log_debug`の引数に書かず、`$PWD`のように手元にある値を使う）。
  重い材料が要るときは、Pythonなら`log.enabled(diaglog.DEBUG)`、TSなら`log.enabled("DEBUG")`で囲む

## 書かないこと

- 環境変数の値、トークン、クレデンシャル、個人情報、ファイルの中身、コマンドの全文（hookの`subject`）、
  コミットや依頼の本文は書かない。要るなら有無・長さ・件数を書く（`token=set`、`body_len=1234`）
- 伏せる前の値を先に書かない。loggerは本文と値に伏せ字（上の「伏せ字」）を当てるが、それは最後の安全網で、
  値を渡さないことが先に来る
- ログの成否で分岐しない。置き場が作れない・権限が無い・容量が足りないなどで書けないときは、loggerは何も出さずに捨てる。
  本体の出力と終了コードは変わらない

## テスト

- 各スクリプトやモジュールのテストでは、診断ログの中身を検査しない。行の形と振る舞いは、logger自体のテスト
  （`tests/core/test_diaglog.py`、`tests/sh/test_diaglog_sh.py`、拡張の`test/shared/log.test.ts`）が固定している
- `tests/sh/test_diaglog_sh.py`は同じ入力を3つの言語に渡し、時刻とpidを除いて同じ行になることを確かめる。
  loggerの形を変えるときは3つをそろえて直し、このテストを通す
- ワークスペースの`logs/`を数えるテストは、`logs/diag/`（ディレクトリ）が増えることを前提にする
