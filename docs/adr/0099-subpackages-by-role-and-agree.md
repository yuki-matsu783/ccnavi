---
type: adr
title: 役割ごとのサブパッケージと段、approval/agree の分割、--agree への改名
description: ccnavi/ を役割ごとの 6 つのサブパッケージに分けて読む向きを固定し、import の循環を無くすために承認済みチケットの置き場（approval）と合意の手続き（agree）を分け、承認の CLI を --agree に改名する
tags: [design-doc, ticket]
keywords: [サブパッケージ, 段, 循環, import, approval, agree, --agree, --approve, ccnavi-agree.sh, 互換の版, COMPAT, test_module_layers, flow-match]
---
# ADR-0099: 役割ごとのサブパッケージと段、approval/agree の分割、--agree への改名

状態: 採用

## 状況

2026-10-03。`ccnavi/` は 49 本のモジュールが 1 つのディレクトリに並んでいた。読む向きは
`tests/core/test_module_layers.py` の段（7 段）だけが持ち、ディレクトリにも名前にも現れなかった。
段の検査は循環を 2 組だけ既知として通していた。

| 循環 | 中身 |
|---|---|
| approval ↔ phase ↔ reasons | 承認済みチケットの走査（approval）と、承認の手続き（候補を組む・見せる・置く）が 1 本に同居し、手続きの側が phase と reasons を関数の中で引いていた |
| ops ↔ review | `ops` が `review.WIP_ROOT` を関数の中で引き、`review` が `ops` を呼んでいた |

もう 1 つ、`agree` という語が 2 つの意味で使われていた。CLI の承認は `--approve`、VS Code 拡張では
`flow-agree.ts` が「画面の中身と PyYAML の読みが一致するか」の意味で `agree` を使っていた。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| `WIP_ROOT` を `ticket` に移す（`ELI5` の隣） | 置き場のパスはチケットの置き場と同じ性質で、review にある理由が無かった。ops ↔ review の循環が消える | 関数の中の import のまま既知の循環として残す（隠すだけで、段の検査は同じに数える） |
| `approval` を置き場に限り、合意の手続きを新しい `agree` に分ける。`reasons.approved` は `agree.approved_text` の本体にする | 置き場は判定が読む土台で、手続きは人の操作。分けると approval は phase・reasons を読まなくなり、向きが agree → approval の 1 本になる | approval の中で手続きを関数の中の import にしたまま残す |
| 循環を 1 つも許さない。既知の循環の一覧（`KNOWN_KNOTS`）を消す | 既知として通す一覧は、次に同じ場所で循環ができたときに何も言わない | 一覧を残して空にする |
| `ccnavi/` を 6 つのサブパッケージに分ける（infra < records < policy < tickets < hook < entry）。直下は `__init__.py`（空）と `__main__.py` だけ | 役割がディレクトリで見え、サブパッケージをまたぐ向きをテストで固定できる。実測でこの順に上向きの import が 0 本だった | 段（7 段）をそのままディレクトリにする（段は深さで切ったもので役割と合わず、名前で探せない） |
| 段（TIERS）は残し、ドットの名前（`infra.fsio`）で持つ。サブパッケージの表（PACKAGES）を足す | サブパッケージの中の読む順はディレクトリでは言えないので、段が補う | 段を捨ててサブパッケージだけにする |
| 再輸出しない。サブパッケージの `__init__.py` は役割の docstring だけ。パッケージの中は相対の 2 形（`from . import x`、`from ..infra import fsio`）だけ | 行き先が import 文にそのまま残り、段とサブパッケージの検査が 1 通りの読みで済む | `__init__.py` で名前を並べ直す（行き先が `__init__.py` に見える）、絶対の `ccnavi.` を許す（表記が 2 つになる） |
| `ccnavi --approve` を `--agree` に改名し、別名は残さない。sh は `ccnavi-agree.sh` に改名する | 「合意」を表す語を 1 つにする。古い名前を残すと、保護（組み込みの deny）とヘルプの両方に 2 つの表記が残り続ける | `--approve` を別名として残す |
| 互換の版（COMPAT）を 3 に上げる | 呼ぶ側（sh と拡張）が頼るフラグの名前が変わり、呼ぶ側を直さないと動かない形になった（`ccnavi/entry/version.py` の上げ方どおり） | 上げない（古い sh は `--approve` を渡して argparse の苦情で落ち、理由が分からない） |
| 組み込みの deny（`builtin-guard-ticket-approval`）は `--agree` と `--approve`、`ccnavi-agree.sh` と `ccnavi-approve.sh` の両方を止める | 実行ファイルはもう `--approve` を受け付けないが、古い実行ファイルや sh のコピーが手元に残っていれば通ってしまう。広がるのは止める側だけ | 新しい表記だけを止める |
| VS Code 拡張の `flow-agree.ts` を `flow-match.ts` に、`flowDisagreement` などを `flowMismatch` に改める | `agree` を「合意」の意味に一本化する | そのまま残す |

変えなかったもの。置き場のモジュール名 `approval`、ディレクトリ `.ccnavi/approved/`、チケットの欄
`ccnavi_approved`、承認済みチケットの記録、判定の記録の理由コード `DENY_TICKET_APPROVAL_CLI`、
ルールの id `builtin-guard-ticket-approval`、環境変数 `CCNAVI_GUARD_TICKET_APPROVAL` とフラグ
`--guard-ticket-approval`、JSON の欄（`version` など）、Chrome 拡張の名前 `ccnavi-approval`、
日本語の文言「承認」。どれも保存済みのデータか利用者の設定に書かれる表記で、変えると既存の
ワークスペースとの互換が崩れる。

## 得たもの・失ったもの

- 得たもの: import の循環が 0 になり、テストが循環を一切許さない。役割がディレクトリで見え、
  サブパッケージをまたぐ逆向きの import を名指しで止められる。承認の手続きを読むときに置き場の
  コードを読まずに済む
- 失ったもの: `ccnavi.<モジュール>` を直に import していた外の呼び手（`main.py`・`build.py`・Chrome
  拡張の Pyodide の入口・テスト・`mock.patch` の文字列）の表記が全部変わった。`--approve` を打っていた
  人と、改名の前の sh・拡張は動かなくなる（互換の版の食い違いとして知らせる）。`.ccnavi/scripts/` は
  保護された場所なので、sh の改名と互換の版の書き換えは人の手で入れる必要がある
- 失ったもの（配布済みの Chrome 拡張）: COMPAT を 3 に上げたので、同梱の compat が 2 の配布済みの
  Chrome 拡張は、統合先が COMPAT=3 になると「始める」・承認・取り下げを拒み、表示だけになる
  （`chrome-extension/ccnavi-approval/src/core/protocol.ts:333-338`、`render.ts:71`。ADR-0093 の
  7.3 の設計どおり）。拡張の配り直しが要る
- 失ったもの（実行ファイル）: マージ後は `build.py` で実行ファイルを組み直すまで、端末の承認が通らない。
  旧い実行ファイルは `--agree` を知らないので、新しい sh（`ccnavi-agree.sh`）から呼ぶと落ちる
