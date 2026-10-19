---
type: adr
title: ルールの「見本」を「期待する判定」と呼び、名前を rule-expectations に改める
description: rule-samples.yml・samples:・--test-samples を rule-expectations.yml・expectations:・--test-expectations に改め、日本語は期待する判定に揃える
tags: [rules, naming, diagnostics]
keywords: [見本, sample, rule-samples, rule-expectations, expectations, --test-samples, --test-expectations, expectationsPath, 改名]
---
# ADR-0119: ルールの「見本」を「期待する判定」と呼び、名前を `rule-expectations` に改める

状態: 採用

## 状況

ルールが何を止めて何を通すかの期待を、日本語では「見本」、英語側では `sample` と呼んでいた
（`rule-samples.yml`・`samples:`・`--test-samples`・拡張の設定 `ccnaviBoard.samplesPath`）。
中身は「この呼び出しはこう判定されるはず」という期待する判定で、見本ではない。
しかも「見本」は、録ったホスト応答の fixtures、初回案内のダミー、計画の見本の表にも使っていて、
語が衝突し、grep で意味を分けられなかった。古い名前は過去の ADR 0016・0032・0035・0042・0051・0067 にも残っている（2026-10-11、ユーザの決定）。

## 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| ファイルは `.ccnavi/config/common/rule-expectations.yml`、キーは `expectations:`、オプションは `--test-expectations`、拡張の設定は `ccnaviBoard.expectationsPath` | 中身（期待する判定）と名前が合い、`sample` を含む別の物と grep で分けられる | 古い名前のまま、日本語だけ直す。英語と日本語の語がずれる |
| 古い名前の別名は残さず、`--test-samples` は廃止する。古いファイルの読み替えや警告も持たない | 展開前で、古い名前を使う利用者がいない。別名は二つの名前を長く抱える | 別名で古い名前を受ける。移行の警告を出す |
| 日本語の文書・スキルでは「期待する判定」と書く（短くしてよい所は「期待」）。別の意味の「見本」（ホスト応答、初回案内のダミー、計画の見本の表、ボードの JSON の見本ファイル）は残す | 語を改めるのは意味が期待のときだけ | 「見本」を全部直す。別の物まで壊れる |
| 直すときは語ごとに読んで手で直す。機械置換はしない | 残す側の「見本」を壊さないため | `sample` の機械置換 |
| 過去の ADR の本文は書き換えない。ADR の一覧の「動かした N 枚」の表の古い名前も、動かした時点の記録として残す | ADR は当時の決定の記録で、書き換えると履歴が読めなくなる | 過去の ADR を新名に書き換える。表の行に新名を足す |

## 得たもの・失ったもの

語が揃い、古い名前の grep が効く。失うのは、履歴（過去の ADR）を引くとき古い名前と新名の両方を引く手間。
また ADR の一覧の表が指す `rule-samples.yml` は、ファイルが既に無く、動かした時点の記録としてだけ読める。

## 採らなかった案

- 別名で古い名前を残す: 二つの名前を抱え続ける。展開前なので移行の要がない
- `sample` を機械置換する: 別の意味の「見本」「sample」まで変わる
- 過去の ADR を書き換える: 当時の決定の記録でなくなる
