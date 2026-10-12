---
type: guide
title: 作業・レビュー計画のテンプレート
description: 完了した親の計画と子のフローをテンプレートに溜める手順と、テンプレートから親の提案を始める手順
tags: [ticket, templates]
keywords: [テンプレート, 雛形, 作業・レビュー計画, plan, feedback, フロー, 下書き, 取り込み, --templates, --draft, --import, ticket new, 再利用, wip/proposals/templates]
---

# 作業・レビュー計画のテンプレート

テンプレートは、1 つの親の作業・レビュー計画（`plan:` と `feedback:` の項、項どうしの先行 `after`）と、子のフローの本文を 1 ファイルにまとめたもの。完了した作業の形を、次の作業で使い回すために溜める。

## 置き場

| 置き場 | 中身 | 書けるのは |
|---|---|---|
| `.ccnavi/config/common/templates/<名前>.yml` | 共通。配布先にも届く | ユーザ |
| `.ccnavi/config/project/templates/<名前>.yml` | そのプロジェクトだけ | ユーザ |
| `wip/proposals/templates/<名前>.yml` | 下書き。効力は無い | エージェント |

エージェントは `.ccnavi/` の下に書けない。溜めるときは下書きを書き、ユーザが取り込む。フローの文は子への案内文になるので、ユーザが差分を読んでから効かせる。

共通とプロジェクトに同名があり、中身が違うときは error になり、プロジェクト側は使われない。

## 見つける

- `ccnavi --templates` で一覧を見る。`--keyword <語>` で名前・説明・使う場面を検索し、`--show <名前>` で中身を見る
- `ccnavi --templates --json` が機械向けの答え。`ccnavi --lint` もテンプレートを検査する
- `ccnavi --docs` の索引には載らない。yml なので `--templates` で探す

## 溜める

1. 計画を持つ親が閉じるときに、ccnavi が案内を 1 行出す。`ready` の前に `ccnavi --templates --draft <親>` で下書きを作る。`ready` を打つと下書きの材料が退避されて拾えなくなる
2. 下書きの `description` と `when` に `TODO` の印が付く。固有名を一般化し、効かなかった手順を削り、印を消す。直すのはエージェントでよい
3. ユーザが `ccnavi --templates --import <名前> --to common|project` で取り込む。`--yes` なしだと何も書かず、検査と差分（既存があれば文の前後まで）を見せる。読んだうえで、表示された `--expect <ハッシュ>` を付けて `--yes` で書く。同名を改訂するときは `--replace`
4. 取り込んだ下書きは消える。消したことを `ccnavi-push-approved.sh` がコミットする

エージェントは `--import` を打てない。

## 使う

`ccnavi ticket new <識別子> --title <題> --template <名前> [--layer <名前>]` で、親の提案（`wip/proposals/todo/`）と、子のフローの下書き（`wip/proposals/flows/`）を作る。

- 提案の `plan:` / `feedback:` の `after` は、提案の番号に付け直される
- 提案は通常どおり承認を通る。循環・終端が 1 つ・種類の `kind` の検査は変わらない
- フローの下書きに効力は無い。ユーザがフロー編集画面で読んで取り込むまで、サブエージェントには渡らない
- 子の識別子は、各フェーズの最初の子 `<親>-<フェーズ番号>-01` と仮定している。別の連番で子を起こすときは、下書きの名前を合わせる
- `phases.yml` に無い種類を使うテンプレートは、提案を書かずに断る

## 古くなったテンプレート

種類の定義が変わると、テンプレートが定義と食い違う。`--lint` は warn にとどめ、使うときの承認で error になる。直すときは、下書きを作り直して `--import --replace` で差し替える。
