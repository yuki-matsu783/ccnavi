---
type: skill-reference
title: 確かめる
description: 設定ファイルが意図どおりに動作するか検証する手順。lint、期待する判定のテスト、運用ルール確認
tags: [rules, process-test, config]
keywords: [lint, 期待する判定, テスト, deny, allow, ask, 検証]
---

# 確かめる

設定が書いたつもりのことをしているか、ずれを機械に名指しさせ、機械が言えないところをユーザに聞く。
手順は 5 つ。1 で止まったら先へ進まない。

## 1. lint

```sh
ccnavi --lint --log "" --state ""
```

3 本まとめて読む。error が 1 件でもあればそこで止めて報告する。warn は読んで
「意図した防御が効いていない」箇所として拾う。

| 出どころ | error（防御が消える・全部止まる） | warn（判定は動くが意図とずれる） |
|---|---|---|
| `(rules)` / ルール id | 読めない、版が違う、`deny` が空、文面・match・glob を欠く、glob と regex の両方、`except` の形（空・要素が glob / regex の 1 つでない・知らないキー）、`when` の形（glob / regex と並べた・知らないキー・キーが 1 つでないノード・空のリスト・型違い・8 段 / 64 個の上限超え）、`ask` `allow` に `message`、組めない正規表現、`additionalContextFile` が上に出る | `allow` が空、id 無し・重複、適用する対象の無いツール、広い allow に文、指すファイルが無い・長い、何にでもヒットする単体の `deny`（`glob: "*"` ちょうどは除く）、何にでもヒットする `except`、Bash / PowerShell の deny / ask の `except`、何にでもヒットする・何にもヒットしない `when` のノード、Bash / PowerShell の deny / ask の `when` の `not` |
| `(phases)` | 読めない、版が違う、`phases` が無い、kind / review の値、feedback が none、id / title の重複、scope が外に出る、scope / deliverables が 20 件を超える、config の定義が共通レイヤーと同じ id で中身が違う・title が重なる | ―（順序の欄は読まず、警告も出さない） |
| `(risk)` | 読めない、版が違う、levels の順、id の重複・形、points が負、加点条件が 0 か 2 つ、script の置き場が外、glob が組めない | 知らないリスクレベル名 |
| `(ticket)` `(project)` `(mode)` | 承認済みチケットの置き場が守られていない、計画を持つ親に定義のコピー（phases:）が無いか壊れている | dry-run、登録の欠け、ワークツリーとチケットの食い違い |

下書きを見るときは `--rules` `--phases` `--risk` で差し替える。1 本だけ差し替えると、本物の残り 2 本と
組み合わせたときの答えになる。

## 2. rules: 期待する判定を回す

```sh
ccnavi --test-expectations .ccnavi/config/common/rule-expectations.yml --log "" --state "" --approved ""
```

出力は 3 つに分かれる。

| 見出し | 意味 | やること |
|---|---|---|
| 食い違い | 置いたタイプと違う判定になった | 1 件ずつ原因を読む（下） |
| 判定に入らずに通った | 呼び出しは通るが allow にヒットしたわけではない（`known` が偽） | 報告する。ルールを書いてもヒットしない場所 |
| 件数 | タイプごとの一致数 | 報告する |

食い違いには「ヒットしたルール」が出る。よくある形:

- glob が全体にヒットする書き方になっていない。 `git push *` は `cd /repo && git push` に
  ヒットしない。`*git push*` ならヒットする
- 語の切れ目が無い。 `*sed*` が `sedate` にヒットする。右は `*sed *`、左は regex
- タイプの順で強い側が先にヒットしている。 allow に置いた期待が deny の `builtin:guard-raw-git` にヒットする、
  など。この形なら期待の置き場かルールの広さのどちらかが間違っている

`--json` を付けると翻訳後の正規表現（`pattern`）と返る文面（`response`）まで出る。

守られているかも期待する判定で見る。rules.yml・phases.yml・risks.yml、承認済みチケットの置き場、
settings.json への Write / Edit を deny の期待に置いて回す。ワークスペースルート直下と、実在するワークツリーの
名前での設定の両方（このリポジトリでは組み込みの `builtin:guard-ccnavi-config` と `builtin:guard-settings` が止める）。出るのは実行前の判定（組み込みの保護を含む）で、
実行後に戻す働きは出ない。allow に見えたものは「止めていない」として報告する。

## 3. phases: 計画と噛み合うか

lint が通っても、フェーズ定義が計画と噛み合っているかは lint の外。

```sh
ccnavi --explain --log "" --state ""
```

承認済みの親とその計画が出る。見るのは 4 つ。

- 開いている親の `plan:` / `feedback:` が指すフェーズ定義が、全部 phases.yml（共通レイヤーと親の `project:` のレイヤーの和）に在るか。無ければ
  その親は次のフェーズを承認できない
- `review: none` のフェーズ定義が、本当にユーザが見なくてよい成果か。`when` に「飛ばしてよい条件」まで
  書いてあるか
- `scope` の広さ。子 ⊆ フェーズ定義 ⊆ 親なので、フェーズ定義の scope が親より広くても効かない。逆に狭すぎると
  子が書けない（子の超過は warn で、承認は止まらない）。rules.yml の deny と重なる場所は deny が勝つ
- `deliverables` に挙げた名前が、そのフェーズ定義の子が必ず作れる名前か。閉じるときに無いと、
  最後の子を閉じられない

## 4. risk: 配点が実績に合うか

配点を差分に適用する道具は `ticket finish` だけなので、ここでは見積もる。見積もりであって判定ではないと報告に書く。

```sh
sh .ccnavi/scripts/ccnavi-git.sh log --shortstat -20
```

- 最近の子 1 本ぶんの差分（行数・ファイル数）を基準と並べる。普通の子が毎回 HIGH に届く配点も、
  どの子も MEDIUM に届かない配点も意図に合っていない
- `glob` の項目は `max` が無ければ青天井。ヒットしそうなファイル数を数えて、1 回で CRITICAL に跳ねないか見る
- `script` の項目は、その sh を子のワークツリーで実際に打つ。`CCNAVI_BASE_SHA` と
  `CCNAVI_HEAD` を環境変数で渡し、標準出力が整数か `{"points": N}` か、失敗したときに
  何も出さないか（重いほうとして扱われ、丸ごと加点される）を見る
- `judge` の問いは、差分だけを読んで yes / no で答えられるか（答えられないと子が閉じられない）

## 5. ユーザに確認する

食い違っていなくても、次にヒットするものは黙って通さずに聞く。まとめて 1 回で、
AskUserQuestion。聞くのは「この設定をどうするか」で、「この呼び出しを通してよいか」ではない。

- 権限モードへの委譲（`UNDECLARED`）が多い。`logs/decisions.jsonl` の `decision == "handover"` を数える。
  同じ場所が繰り返すなら allow に足す先、毎回違うなら allow の粒度が細かすぎる。どちらかを添えて聞く
- 広すぎる allow・deny（`*` 単独、語の切れ目の無い glob、コマンド名を挟まない regex）
- regex を使っているルール。glob で書けない理由を確かめる
- ユーザが見なくてよいフェーズ定義（`review: none`）と、境目の点に届かない配点の組み合わせ
- 承認済みの親の計画が指すフェーズ定義を変える提案（親の計画が読めなくなる）

## 報告

書くのは 5 つ。lint の error / warn の中身、期待の件数と食い違いの原因、phases の噛み合わせ、
risk の見積もり、ユーザに確認した結果。直す必要があるなら、どのファイルのどこをどう直すかまで
書いて、[add.md](add.md) の「ユーザに渡す形」で依頼する。直すのはユーザ。
