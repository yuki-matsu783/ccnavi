---
name: ccnavi-config
description: >-
  ccnavi の設定 3 本（rules.yml・risks.yml は .ccnavi/common/ と config/、phases.yml は config/）を書く・足す・直す・
  確かめる。ルールを足したい、フェーズ定義やリスクの配点を変えたい、書いた設定が
  意図どおりに効いているか疑わしい、`/ccnavi-config` と打たれた、のどれでも使う。
  足すときは下書きを検証してからユーザに渡し、確かめるときは lint と見本を回して
  食い違いを名指しする。settings.json の hook の登録や env はここでは扱わない。
---

# ccnavi-config

ccnavi の判定と進め方は 3 本のファイルで決まる。どれもユーザが持つ設定。

| ファイル | 決めるもの | どのレイヤーにも無いとき |
|---|---|---|
| `rules.yml` | 何を止め、何を聞き、何を通すか（`deny` / `ask` / `allow`） | 組み込みの既定だけで判定する（無いのは正常で、`--lint` も言わない） |
| `phases.yml` | フェーズ定義（config にだけ置く）。親の `plan:` に並べる名前と、その範囲・レビュー・成果物。順序は持たない（親の計画の項の `after` で書く） | 計画の無い親だけが使える（番号だけのフェーズ）。計画を持つ親は承認できない |
| `risks.yml` | 子を閉じるときに差分を数える配点。HIGH 以上はレビューが済むまで止まる | 組み込みの配点（定量 4 項目） |

置けるレイヤーは 3 つで、置き場はどれも固定。共通レイヤーは `.ccnavi/common/`、自身のレイヤーはワークスペースの
`.ccnavi/config/`、プロジェクトのレイヤーは `projects/<名前>/.ccnavi/config/`。`rules.yml` と `risks.yml` は共通レイヤーと
config のどちらにも置け、判定には、共通レイヤーにその判定に関わるレイヤーを足したものを使う。足すだけで、上書きも取り消しもしない。
`phases.yml` は config にだけ置き、親チケットの `project:` が指す 1 本で決まる（足し算はしない）。共通レイヤーに `phases.yml` があると error。
`phases.yml` を読むのは提案の承認の検査と `--plan-order <親> --fill-phases`（写しを差し込む）と `--lint` だけで、承認のあとの判定は
親チケットに写した定義（`phases:`）を読む。直しても進行中の親には届かない。
共通レイヤーは、親の着手でプロジェクトの `.ccnavi/common/` へミラーされる（プロジェクトの `config/` には触れない）。
どのレイヤーを足すかは操作で決まる。

- `rules.yml`: パスを持つツールは行き先のレイヤー、パスを持たないツールは自身のレイヤーと全プロジェクトのレイヤー
- `risks.yml`: 親チケットの `project:` が指すレイヤー。空なら自身のレイヤー
- `phases.yml`: 親チケットの `project:` が指すレイヤー 1 本。空なら自身のレイヤー

ファイルが無いのは「設定が無い」正常で、空として扱い、`fallback` にも診断にも出ない。壊れているときだけ `--lint` が error で言う。
組み込みの deny は、共通レイヤーと config の有無・状態によらず常に有効。

- 共通レイヤーの `rules.yml`: 無ければ空として、config のレイヤーを組み込みの deny の上に足す。壊れていれば、組み込みの既定だけで判定し、他のレイヤーは足さない
- `phases.yml`: 無いか壊れていれば、計画を持つ親の提案は承認できない（承認済みの親は写しで判定し続け、`--lint` が warn で言う）
- 共通レイヤーの `risks.yml`: 無ければ空として、他のレイヤーの項目だけを数える（組み込みの配点は共通も config も無いときだけ）。壊れていれば組み込みの配点だけで、他のレイヤーは足さない
- 他のレイヤーの `rules.yml` と `risks.yml`: 無ければ空。壊れていれば空として扱い、そのレイヤーの分だけが抜ける

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

診断のときは記録と state を外す（`--log "" --state ""`）。

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
