# 診断ログを書く

sh・Python・TypeScript で「あとから何が起きたかを追う」ための行を書くときの決まり。
logger は 3 つの言語に 1 つずつあり、どれも同じ形の行を同じ置き場に出す。

| 言語 | logger | 呼び方 |
|---|---|---|
| sh（`.ccnavi/scripts/`） | `ccnavi-common.sh` の `log_debug` `log_info` `log_warn` `log_error` | `set -eu` の直後に共通部を読む（既にどの sh も読んでいる） |
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
  - sh: ルートは `ccnavi_workspace`（`CCNAVI_WORKSPACE`、無ければ cwd から上へ探す）。出どころは
    `$0` の名前から拡張子を落としたもの（`ccnavi-git`）。`CCNAVI_LOG_NAME` で上書きできる
  - Python: 呼び手が `get(name, root)` に渡す。`root` を省くと `CLAUDE_PROJECT_DIR`。どちらも無ければ書かない。
    実行ファイルは `cli` の `root`（`--root` か `default_root()`）を渡す
  - TS: 呼び手がワークスペースフォルダのパスを渡す（`folder.uri.fsPath`）。空なら書かない
- 出どころの名前は固定の短い語にする（`ccnavi`・`ccnavi-git`・`ccnavi-board`）。パスや利用者の入力を入れない
- `CCNAVI_LOG_LEVEL` = `DEBUG` / `INFO` / `WARN` / `ERROR`（大文字小文字は問わない）。空と読めない値は `INFO`
- 片付けは `ccnavi/prune.py` の `_prune_diag`。判定の記録と同じしきい値を使い、`CCNAVI_LOG_ROTATE_MB` を
  超えた 1 本を `<出どころ>.<日時>.log` へ名前を変え、`CCNAVI_LOG_KEEP_DAYS` のあいだ書かれていない
  `*.log` を消す。走るのはセッションの開始と `ccnavi --prune`

## sh の呼び方

```sh
log_info 拒否した -- "sub=$sub" "reason=$1"
log_debug 判定の材料 -- "sub=$sub" "top=$root" "cwd=$PWD"
```

- `--` より前は本文の語で、スペースでつないで 1 つの本文にする
- `--` より後ろは 1 引数に 1 つの `key=value`。値に空白があっても引数ごと `"…"` で括れば 1 つの値になる。
  `=` の無い引数は値の空なキーになる
- 値の中の `scheme://<資格情報>@host` は `<伏せた>@host` に伏せる（`ccnavi_mask_url` と同じ読み）。
  ほかの形は伏せない。一次の守りは「秘密の値を渡さない」
- 何があっても 0 を返す。`set -eu` の下でも止まらない。`|| :` を付ける必要は無い

## 書くこと

- **独自のログ方式を作らない。** `printf … >>logs/…` や `print` や `console.log` で診断を書かず、logger を使う
- 1 行 1 事象。「何を・どうした」を本文に、判断に要る値を `key=value` に書く
- レベルの使い分け
  - `INFO` 受け付けた操作、判定の結果、拒否の理由、最終の結果（終了コード）
  - `DEBUG` 判定の材料（どのブランチ・どのツリー・どの経路で読んだか）
  - `WARN` 続行できる異常（読めない入力を捨てて進んだ、など）
  - `ERROR` 処理を止める失敗
- 既定の `INFO` で、1 回の起動につき数行に収める。ループの中で `INFO` を書かない
- 出さないレベルでは logger が時刻も行も作らない。**`DEBUG` の引数を組み立てるために外部コマンドを
  起こさない**（`$(git …)` や `$(date)` を `log_debug` の引数に書かない。`$PWD` のように手元にある値を使う）。
  重い材料が要るときは Python なら `log.enabled(diaglog.DEBUG)`、TS なら `log.enabled("DEBUG")` で囲む

## 書かないこと

- 環境変数の値、トークン、クレデンシャル、個人情報、ファイルの中身、コマンドの全文（hook の `subject`）、
  コミットや依頼の本文。要るなら有無・長さ・件数を書く（`token=set`、`body_len=1234`）
- 伏せる前の値を先に書かない。Python は logger が本文と値に `ccnavi/redact.py` の `redact` を通すが、
  それは最後の網で、渡さないのが先。TS は伏せる仕掛けを持たない
- ログの成否で分岐しない。書けないとき（置き場が作れない・権限・容量）は logger が黙って捨て、
  本体の出力と終了コードは変わらない

## テスト

- 各スクリプト・モジュールのテストで診断ログの中身を検査しない。行の形と振る舞いは logger 自体のテストが
  固定している（`tests/core/test_diaglog.py`、`tests/sh/test_diaglog_sh.py`、拡張の `test/shared/log.test.ts`）
- `tests/sh/test_diaglog_sh.py` は同じ入力を 3 つの言語に渡し、時刻と pid を除いて同じ行になることを見る。
  logger の形を変えるときは 3 つを揃えて直し、このテストを通す
- ワークスペースの `logs/` を数えるテストは、`logs/diag/`（ディレクトリ）が増えることを前提にする
