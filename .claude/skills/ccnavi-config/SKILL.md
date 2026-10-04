---
name: ccnavi-config
description: >-
  ccnavi の設定 3 本（.ccnavi/common/ の rules.yml・phases.yml・risks.yml）を書く・足す・直す・
  確かめる。ルールを足したい、フェーズの種類やリスクの配点を変えたい、書いた設定が
  意図どおりに効いているか疑わしい、`/ccnavi-config` と打たれた、のどれでも使う。
  足すときは下書きを検証してからユーザに渡し、確かめるときは lint と見本を回して
  食い違いを名指しする。settings.json の hook の登録や env はここでは扱わない。
---

# ccnavi-config

ccnavi の判定と進め方は 3 本のファイルで決まる。どれもユーザが持つ設定。

| ファイル | 決めるもの | どの層にも無いとき |
|---|---|---|
| `rules.yml` | 何を止め、何を聞き、何を通すか（`deny` / `ask` / `allow`） | 組み込みの既定で判定し、`--lint` が error で言う |
| `phases.yml` | フェーズの種類。親の `plan:` に並べる名前と、その範囲・レビュー・成果物 | 番号だけのフェーズ。親の `plan:` は読めない |
| `risks.yml` | 子を閉じるときに差分を数える配点。HIGH 以上はレビューが済むまで止まる | 組み込みの配点（定量 4 項目） |

3 本とも置ける層は 3 つで、置き場はどれも固定。共通層は `.ccnavi/common/`、自身の層はワークスペースの
`.ccnavi/config/`、プロジェクトの層は `projects/<名前>/.ccnavi/config/`。判定には、共通層にその判定に
関わる層を足したものを使う。足すだけで、上書きも取り消しもしない。どの層を足すかは操作で決まる。

- `rules.yml`: パスを持つツールは行き先の層、パスを持たないツールは自身の層と全プロジェクトの層
- `phases.yml` と `risks.yml`: 親チケットの `project:` が指す層。空なら自身の層

一部の層だけに無い・壊れているときは次のとおり。壊れているときは `--lint` が error で言う。

- 共通層の `rules.yml`: 無くても壊れていても、組み込みの既定だけで判定する。他の層は足さない
- 共通層の `phases.yml`: 無ければ他の層の種類だけを使う。壊れていれば番号だけのフェーズになり、他の層は足さない
- 共通層の `risks.yml`: 無ければ組み込みの配点に他の層を足す。壊れていれば組み込みの配点だけで、他の層は足さない
- 他の層: 3 本とも、無ければ空。壊れていれば空として扱い、その層の分だけが抜ける

| 言われたこと | 読むもの |
|---|---|
| 足す・直す・新しく書く（「ルールを足して」「フェーズに調査を入れたい」「配点を変えたい」） | [references/add.md](references/add.md) |
| 確かめる・疑う（「効いているか見て」「lint して」「見本を回して」） | [references/check.md](references/check.md) |

足したあとは必ず確かめる側も通る。どちらか決まらないときだけユーザに聞く。

## 絶対ルール

- 3 本のファイルを書き換えない。下書きはワークツリーの `scratchpad/`（無ければセッションのスクラッチパッド。docs/claude/scratchpad.md）に置き、検証を通してから
  「何をなぜ変えるか」と一緒にユーザに渡す。置くのはユーザ。`dry-run` で警告だけで通っても同じ
- 判定を目で真似しない。当たるかどうかは必ず `ccnavi --test-samples` か `--lint` に聞く
- 見本を期待に合わせて書き換えない。食い違いが出たら、ルールと見本のどちらを直すかはユーザが決める
- TodoWrite と Agent ツールを使わない

## ccnavi の打ち方

以下で `ccnavi` と書いたら、このリポジトリでは `uv run python -m ccnavi`。配布先の
プロジェクトでは settings.json の `CCNAVI_BIN_PATH` が指す `.ccnavi/scripts/ccnavi-launcher.sh`。

診断のときは記録と控えを外す（`--log "" --state ""`）。

```sh
ccnavi --lint --log "" --state ""
ccnavi --lint --log "" --state "" --rules <下書き> --phases <下書き> --risk <下書き>
ccnavi --test-samples <見本.yml> --log "" --state "" --approved "" [--rules <下書き>]
ccnavi --explain --log "" --state ""
```

- `--lint` は判定をせず、防御を消す・全部止める記述を error、意図した防御が効いていない
  記述を warn で名指しする。3 本まとめて見る。error があれば終了コード 1
- `--test-samples` は見本をぜんぶ判定に掛け、置いたタイプ（`deny` / `ask` / `allow`）と
  違う判定になったものを並べる
- `--explain` はいま効いているルールと承認済みのチケットの範囲を並べる。判定はしない
- `--rules` / `--phases` / `--risk` で下書きを差し替えられる

`--test` に禁止語を書かない（`ccnavi --test Bash "git push"` はその Bash 自体が `raw-git` に当たる）。
単発で試したいものも見本ファイルを `scratchpad/` に書いて `--test-samples` で回す。見本の形は `.ccnavi/common/rule-samples.yml` の先頭のコメントにある。
