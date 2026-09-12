---
name: ccnavi-config
description: >-
  ccnavi の設定 3 本（.claude/ccnavi/ の rules.yml・phases.yml・risk.yml）を書く・足す・直す・
  確かめる。ルールを足したい、フェーズの種類やリスクの配点を変えたい、書いた設定が
  意図どおりに効いているか疑わしい、`/ccnavi-config` と打たれた、のどれでも使う。
  足すときは下書きを検証してから利用者に渡し、確かめるときは lint と見本を回して
  食い違いを名指しする。settings.json の hook の登録や env はここでは扱わない。
---

# ccnavi-config

ccnavi の判定と進め方は、3 本のファイルで決まる。どれも**人が持つ設定**で、
エージェントが書き換えると、自分のリスクや守りを自分で決められることになる。

| ファイル | 決めるもの | 無いとき |
|---|---|---|
| `.claude/ccnavi/rules.yml` | 何を止め、何を聞き、何を通すか（`deny` / `ask` / `allow`） | 組み込みの既定に落ち、`--lint` が言う |
| `.claude/ccnavi/phases.yml` | フェーズの種類。親の `plan:` に並べる名前と、その範囲・レビュー・成果物 | 番号だけのフェーズ。`plan:` は読めない |
| `.claude/ccnavi/risk.yml` | 子を閉じるときに差分を数える配点。HIGH 以上でゲートが閉じる | 組み込みの配点（定量 4 項目） |

このスキルの仕事は 2 つで、入口で分かれる。

| 言われたこと | 読むもの |
|---|---|
| 足す・直す・新しく書く（「ルールを足して」「フェーズに調査を入れたい」「配点を変えたい」） | [references/add.md](references/add.md) |
| 確かめる・疑う（「効いているか見て」「lint して」「見本を回して」） | [references/check.md](references/check.md) |

足したあとは必ず確かめる側も通る。どちらか決まらないときだけ利用者に聞く。

## 絶対ルール

- **3 本のファイルを書き換えない。** 下書きは scratchpad に置き、検証を通してから
  「何をなぜ変えるか」と一緒に利用者に渡す。置くのは利用者。`dry-run` で警告だけで
  通っても同じ。判定が緩んだときに手順が変わる形にしない
- **判定を目で真似しない。** 当たるかどうかは必ず `ccnavi --test-samples` か `--lint` に
  聞く。glob は正規表現に翻訳され、Bash はシェルとして読まれてから当たるので、
  読んだだけの「当たるはず」は判定と別のことを言う
- **見本を期待に合わせて書き換えない。** 食い違いが出たら、直すのはルールか見本の
  どちらかで、決めるのは利用者
- **TodoWrite と Agent ツールを使わない**

## ccnavi の打ち方

以下で `ccnavi` と書いたら、このリポジトリでは `uv run python -m ccnavi`。配り先の
プロジェクトでは settings.json の `CCNAVI_BIN_PATH` が指す実行ファイル（既定 `.claude/ccnavi/ccnavi`）。

診断のときは記録と控えを外す。外さないと、走っているセッションの記録に診断の行が混ざる。

```sh
ccnavi --lint --log "" --state ""
ccnavi --lint --log "" --state "" --rules <下書き> --phases <下書き> --risk <下書き>
ccnavi --test-samples <見本.yml> --log "" --state "" --approved "" [--rules <下書き>]
ccnavi --explain --log "" --state ""
```

- `--lint` は判定をせず、防御を消す・全部止める記述を error、意図した防御が効いていない
  記述を warn で名指しする。3 本まとめて見る。error があれば終了コード 1
- `--test-samples` は見本をぜんぶ判定に掛け、置いたタイプ（`deny` / `ask` / `allow`）と
  違う判定になったものを並べる。判定は hook と同じ関数を通る
- `--explain` はいま効いているルールと承認済みのチケットの範囲を並べる。判定はしない
- `--rules` / `--phases` / `--risk` で下書きを差し替えられる。本物を置く前に確かめる道

**`--test` に禁止語を書かない。** `ccnavi --test Bash "git push"` は、その Bash 自体が
`raw-git` に当たる。単発で試したいものも見本ファイルを scratchpad に書いて
`--test-samples` で回す。見本の形は `.claude/ccnavi/rule-samples.yml` の先頭のコメントにある。
