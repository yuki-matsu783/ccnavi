---
type: guide
title: テンプレートが育つ流れ
description: 作業・レビュー計画のテンプレートが、完了した親から下書き・整え・ユーザの取り込み・再利用・配布までどう育つかの全体像
tags: [templates, ticket]
keywords: [テンプレート, 育つ, 全体像, 作業・レビュー計画, draft, handoff, templates, ticket new, 取り込み, 配布, 古くなった]
---

# テンプレートが育つ流れ

テンプレートは、完了した親の計画（`plan:` `feedback:` と `after`）と子のフローを 1 ファイルにしたもの。ほかと違い、最初のきっかけが「作業がうまくいって終わったこと」で、そこから次の作業の雛形が増えていく。

[共通の 6 段](grow.md)のうち、この対象の中身を表にする。

| 段階 | 何が起きるか | 誰が | 使うもの |
|---|---|---|---|
| きっかけ | 計画を持つ親が閉じるとき、ccnavi が案内を 1 行出す | ccnavi | 親の `finish` |
| 下書き | `ready` の前に下書きを作る。`ready` を打つと材料が退避されて拾えない | エージェント | `ccnavi --templates --draft <親>`、置き場は `wip/handoff/`（取り込み先と同じ相対パス） |
| 整え | `description` と `when` の `TODO` の印を消す。固有名を一般化し、効かなかった手順を削る | エージェント | 下書きを直接編集 |
| 検証 | `TODO` の残りは warn、制御文字を含む下書きは error。`user handoff` の下見が差分（既存があれば前後）を見せる | ccnavi | `ccnavi --lint`、`ccnavi user handoff`（`--yes` なし） |
| 承認 | 下見を読み、表示された `--expect <ハッシュ>` を付けて `--yes` で書く。ターミナルの前のユーザだけが打てる | ユーザ | `ccnavi user handoff <相対パス> --yes --expect <ハッシュ>` |
| 効く | 次の親の提案を、テンプレートから始められる | エージェント | `ccnavi ticket new <識別子> --title <題> --template <名前> --issue <値> --mr <番号>` |
| 配る | 共通は導入 sh が配布先へ配り、親の着手がプロジェクトの `.ccnavi/config/common/` へコピーする | ccnavi | `ccnavi-setup.sh`、親の `ticket start` |

## 規則

- 下書きを残したまま `ready` を打たない。未取り込みの下書きが `wip/handoff/` に残っていると `ready` が止まる。取り込むか、消す
- エージェントは `ccnavi user handoff` を打てない（見るだけの形も）。フローの文は子への案内文になるので、ユーザが読んでから効かせる
- 取り込み先は 2 つ。`common` は全プロジェクトに届く、`project`（既定）はそのプロジェクトだけ。同名で中身が違えば error で、プロジェクト側は使われない
- 提案に書くのは全体計画だけ。フィードバック計画は、全体計画の最後のレビューを見てから改版で 1 回足す（最初に入れると承認後に全体計画を改版できなくなる）
- 子のフローの下書きに効力は無い。ユーザがフロー編集画面で取り込むまでサブエージェントには渡らない
- フェーズ定義が変わるとテンプレートが古くなる。`--lint` は warn、使うときの承認で error になる。`ccnavi-stage.sh` で既存を `wip/handoff/` にコピーして直し、`user handoff` で取り込む
- 一覧と検索は `ccnavi --templates`（`--keyword` `--show`）。`--docs` の索引には載らない

## 次に読む

| 読むとき | 開くファイル |
|---|---|
| 手順の細部とオプション | `.ccnavi/docs/templates.md` |
| 計画の型 | `.ccnavi/docs/grow-phases.md`（フェーズが育つ流れ） |
