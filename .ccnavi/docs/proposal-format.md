---
title: 提案の書式
type: guide
description: 親の提案・子の提案の frontmatter と本文、plan / after / phases のコピー、子のフロー、雛形を作る ccnavi ticket new と、書いたあとに打つ検査。フェーズの型の選び方の引き先
tags: [ticket, phase]
keywords: [proposal-format, 提案書式, 提案, 子の提案, 親の提案, 書式, phases, フェーズ, plan, after, 計画, frontmatter, ticket new, 雛形, lint, flows, フロー]
---

# 提案の書式

提案は、チケット作業の親と子を `wip/proposals/todo/<識別子>.md` に書いたもの。承認すると、中身がそのまま承認済みチケットになる。
親の提案は手で書かず、雛形を `ccnavi ticket new` で作ってから空欄を埋める。子の提案は雛形が無いので、下の形で手で書く。
`ccnavi` は `.ccnavi/scripts/ccnavi-launcher.sh` 経由でも打てる（出力が示す形に従う）。提案を送る・承認を頼む流れは[ticket-flow.md](ticket-flow.md)、提案を書く前に合意が要る範囲は[decisions.md](decisions.md)。

## 書く前に決めること

- 識別子。親は `<先頭の語>-<番号>-<slug>`（例: `feature-50-settings-split`）。先頭の語は `feature` `hotfix` `fix` `bugfix` `chore` `refactor` `docs` のどれか（`CCNAVI_BRANCH_PREFIXES` で変えられる）。番号は issue の番号。
  子は `<親>-<フェーズ番号 2 桁>-<連番 2 桁>`（親のフェーズ 2 の 1 枚目は `feature-50-settings-split-02-01`）。ファイル名（拡張子を除く）と `ticket:` は一致させる
- 書く場所。親の提案は、識別子と同名のワークツリー（`.claude/worktrees/<識別子>`）の中の `wip/proposals/todo/` に書く（[worktree.md](worktree.md)）
- `issue:` と `mr:`。`ccnavi-start.sh` の出力で「必須:」の行に示された値を、そのまま親に書く
- フェーズの型（計画の各ステップ）。何を選ぶかは `phases.yml` の各定義の `when` に書いてある（共通は `.ccnavi/config/common/phases.yml`、ワークスペース固有は `.ccnavi/config/project/phases.yml`）。
  どの型がどんな `kind` `review` `scope` を持ち、何が書けるかは `ccnavi --explain` の「phases」の表で見る。型を足す・直すのはユーザの設定で、エージェントは書き換えない（[grow-phases.md](grow-phases.md)）

## 親の雛形を作る

親のワークツリーの中で打つ。

```
ccnavi ticket new <識別子> --title <タイトル> --plan <型,型,...> --issue <番号> --mr <番号>
```

- `--plan` は `phases.yml` の型の名前をカンマで並べる（例: `research,design,implement`）。計画は前のステップに続く一直線になる
- `--scope <glob>` は、`quick-work` のように `scope: inherit` の型だけで計画を組むときに必ず渡す（ワークツリーのルートからの相対パス。繰り返せる）
- `--plan` の代わりに `--template <名前>` を渡すと、溜めたテンプレートから計画を作る（[templates.md](templates.md)）
- 作るのは親だけ。`todo/<識別子>.md` に、frontmatter（`version` `ticket` `issue` `mr` `title` `phases` `plan` `rationale` `allow`）と、本文の見出し `## 目的` `## 範囲` `## 対象外` `## 完了の確かめ方` `## 計画の理由` が入る
- `rationale` と各見出しの下の `（ここに〜を書く）` は空欄の目印で、これが残ったままの新しい提案は、承認と `--lint` が止めるので、すべて書き換える
- 止まったときは、何も書かれない（子の識別子を渡した、識別子と同名のワークツリーの外で打った、同じ識別子の提案が既にある、など）。出力の案内に従う
- `allow` は、雛形が計画の型の `scope` を集めて入れたもの。この作業で触るファイルに合わせて、手で絞るか足す。承認しても書けない項があれば、警告で名指しされる

## 親の提案の frontmatter

雛形が書くのは次の形。`allow` / `ask` で、この作業が書ける範囲の上限を決める。

```yaml
---
version: 1
ticket: feature-50-settings-split
issue: 50                  # 必須。この MR が閉じる issue（別のリポジトリなら owner/repo#50）
mr: 12                     # 必須。仕事を載せる MR の番号（同じリポジトリ）
title: 設定画面の分割
phases:                    # 計画が使う型の定義のコピー。--fill-phases が作る（下の節）
  research: {kind: work, title: 調査, review: none, scope: ["wip/research/*"]}
  implement: {kind: work, title: 実装, review: mr, scope: ["src/*", "tests/*"]}
plan:
  - research               # 1。先行なし
  - {type: implement, after: [1]}  # 2。1 が済んでから
rationale: |
  Settings 配下のコンポーネントを分割する。
allow:
  - match: Write|Edit
    glob: "wip/research/*"
  - match: Write|Edit
    glob: "src/components/Settings/*"
ask:                       # 無くてよい。allow か ask のどちらかが 1 件は要る
  - match: Write|Edit
    glob: "src/components/*"
---
## 目的
…
```

- `allow` / `ask` の `glob` は、ワイルドカードが無ければ前置として扱う（`src` は `src/` 以下全部）。`..` `~` `$` と絶対パスは書けない。項は 20 件まで。書いていない場所は範囲外
- `human_review: {required: true, reason: …}` は子ごとの欄。省くと既定（`required: true`）。`required: false` にするときは `reason` に理由を書く
- `started_at` `completed_at` `base_sha` などの欄はスクリプトが書く。提案には書かない
- `branch:` は書けない（使えない欄としてエラーになる）。本文は自由で、MR の下書きの本文に使われる

## plan / after / phases

- `plan` の各ステップは、型の名前だけか `{type: <型>, after: [番号]}`。番号は 1 から数え、`after` は先に済んでいるべきステップの番号。自分より小さい番号だけを指せる
- `after` を書かないステップは、先行するステップが無いものとして扱う。最後のステップに、ほかの全部のステップが（間接でも）先行するように書く。`after` を書かないステップが複数あってもよい。承認で落ちるのは、最後のステップにほかのステップが先行していないとき（独立した作業は、最後のステップで受ける）
- 並行させたいステップは、`after` に互いを書かない。雛形は一直線なので、並行にするときは `after` を直す
- 計画を持たない親は `ticket start` が止まり、`--lint` も警告する。計画に使える型が `phases.yml` に無いときは、ユーザに型を足してもらう
- 親の `phases:` は、計画が使う型の定義**だけ**のコピー。手で写さず `ccnavi --plan-order <識別子> --fill-phases` で差し込む（`ticket new` は書いた直後に同じことをする）。
  承認のあとは、このコピーを読んで判定し、`phases.yml` は読まない。`phases.yml` を直したら、承認待ちの提案は `--fill-phases` を打ち直す
- `feedback:` は、レビューを受けたあとの改版で足すフィードバック計画（番号は全体計画の続き）。最初の提案には書かない

## 子の提案

子はフェーズごとに 1 枚以上作る。最初のフェーズの子は、親の提案と一緒に `todo/` に置き、`propose` は親の識別子で打つ。
後ろのフェーズの子は、前のフェーズが閉じて（レビューがあれば済んで）から出す。先に出すと承認で止まる。

```yaml
---
version: 1
ticket: feature-50-settings-split-02-01
parent: feature-50-settings-split
phase: 2                   # 親の plan の番号。計画があれば 1 から
predecessors: [feature-50-settings-split-01-01]  # 先に閉じているべき子。無ければ省く
title: 設定画面の分割（実装）
human_review:
  required: true
  reason: 設定の読み込み経路を変えるため
allow:
  - match: Write|Edit
    glob: "src/components/Settings/*"
---
この子で何をするか。
```

- `phase` は親の `plan` の番号と、識別子の 2 桁のフェーズ番号に揃える（食い違えばエラー）
- `allow` / `ask` が指す場所は、親の `allow` / `ask` の中に収める。超える項や `regex` は `--lint` が警告し、判定は親の範囲で切り詰める（超えた分は効かない）
- 子に `phases:` `plan:` `issue:` `mr:` は書かない。子は `phase:` で親の計画の番号を指す
- `predecessors` は子どうしの先行で、`plan` の `after`（フェーズどうしの先行）とは別。承認と `ticket start` は、先行の子が `done/` にあることを求める
- 計画のあるフェーズには、子が 1 枚は要る。子のないフェーズが残ったまま `finish` すると止まる

## 子のフロー

子には、担当のサブエージェントが作業中に読む手順書（フロー）を持たせられる。任意で、頼まれたときだけ書く。

- 下書きは `wip/proposals/flows/<子の識別子>.yml` に置く。効力は無く、ユーザがボードのフロー編集画面で取り込んで保存したものだけが効く。効力のある場所（承認済みの領域の `flows/`）にエージェントは書けない
- 形は YAML の 1 文書で、`id` `name` `version` `nodes` `connections` を持つ。ノードの種類と読む欄は、ボードのフロー編集画面で保存される形に合わせる。手で一から書かない
- 渡す前に `ccnavi --lint --flow <パス>` で読めるかを確かめる

## 書いたあとに打つ検査

```
ccnavi --lint
ccnavi --agree --preview --verify <識別子>
```

- `--lint` は提案と設定の形を検査する。`--agree --preview --verify` は、承認するときの検査を、承認せずに先に通す。どちらも承認と同じ規則で提案を調べる。後者はさらに、承認の対象に入るかも答える
- error が出たら直して、同じコマンドを打ち直す。「通る」（終了コード 0）は警告が無いという意味ではない。警告のうち、承認のあとに取り返せなくなるもの（提案を識別子と違う名前のワークツリーに書いた、など）は、承認を頼む前に直す
- 通ったら `ccnavi-ticket.sh propose <識別子>` で親のブランチへ送り、ユーザに承認を頼む（[ticket-flow.md](ticket-flow.md)）
- 空欄の目印の残り、`allow` と計画の型の `scope` が重ならない、`phases:` のコピーが `phases.yml` と食い違う、といった問題は、ここで名指しされる。出力の案内を読んで直す
