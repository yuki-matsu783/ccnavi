---
version: 1
ticket: config-union
title: rules / phases / risk をワークスペースとプロジェクトの和にする
rationale: |
  設定 3 本（rules / phases / risk）を「共通層 + そのツリーの層」の和で判定する。
  今は Write / Edit がプロジェクトの rules.yml 1 本で判定され、ワークスペースの
  guard-hooks / credentials / main-tree がプロジェクトのファイルに効かない。
  phases / risk はワークスペースに 1 本しか無く、scope がレイアウトに縛られるので
  2 つ目のプロジェクトで破綻する。設計 §25 の改版。
human_review:
  required: true
  reason: 判定の読み込み経路と自己保護の対象を変えるため
plan:
  - design
  - acceptance
  - implement
  - docs
# フィードバック計画。4 フェーズのレビューで出た指摘は、同じフェーズに子を足して応えた
# （02 → 06、03〜05 → 07 と 09、08 → 10）。この MR の中で追加の対応はしない。残りは
# 承認のあと handoff で別の issue に切り出す: 層が無いことを --lint が言うか、VS Code 拡張が
# 旧置き場を読む件、共通層の 3 本を組み込みで止めるか、傘を動かしたときの組み込みの既定の綴り。
feedback: []
allow:
  - match: Write|Edit
    glob: "wip/design/*"
  - match: Write|Edit
    glob: "ccnavi/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "scripts/*"
  - match: Write|Edit
    glob: ".ccnavi/*"
  - match: Write|Edit
    glob: ".claude/ccnavi/phases.yml"
  - match: Write|Edit
    glob: "build.py"
  - match: Write|Edit
    glob: "README.md"
  - match: Write|Edit
    glob: "ccnavi.md"
  - match: Write|Edit
    glob: "requirements.md"
  - match: Write|Edit
    glob: "CONTEXT.md"
  - match: Write|Edit
    glob: "HANDOVER.md"
  - match: Write|Edit
    glob: "CLAUDE.md"
started_at: ""
completed_at: ""
base_sha: ""
---

# rules / phases / risk をワークスペースとプロジェクトの和にする

2026-09-12 の設計セッションで決めたことの控え。設計フェーズはこれを §25 の改版として
`wip/design/` に書き、`ccnavi.md` へ写す。

## 骨

- 3 本とも「共通層 + そのツリーの層」の和。足すだけ、上書き無し、厳しいほうが勝つ
- Write / Edit / NotebookEdit と実行後の監視（§25.7）: 共通層 + 行き先の 1 プロジェクトの層
- Bash: 共通層 + 全プロジェクトの層（今どおり。どのプロジェクトに効くかは payload から
  決められないので決めない）
- `projects/` が無いか空のワークスペースでは挙動が変わらない（自身の層への移行を除く）

## 置き場

| 層 | 場所 | git |
|---|---|---|
| 共通層 | `.claude/ccnavi/{rules,phases,risk}.yml`（今のまま。`CCNAVI_RULES` / `CCNAVI_PHASES` / `CCNAVI_RISK`） | ワークスペース |
| ワークスペース自身の層 | `<ワークスペースルート>/.ccnavi/config/{rules,phases,risk}.yml` | ワークスペース |
| プロジェクトの層 | `projects/<名前>/.ccnavi/config/{rules,phases,risk}.yml` | プロジェクト |
| 固有スクリプト | 各層の `.ccnavi/scripts/` | 同上 |

- 環境変数は `CCNAVI_PROJECT_HOME`（既定 `.ccnavi`、git プロジェクトルート相対）1 本。
  `CCNAVI_PROJECT_RULES` は廃止。設定されていれば `--lint` が warn
- 読むのは常に git プロジェクトルートに checkout されている版。ブランチは問わない。
  作業ツリー内の `.ccnavi/` は読まない（rules の今の扱いと同じ）
- 層のファイルが無い = その層は空。壊れている = 空 + 記録の `fallback` + `--lint`。
  組み込み既定へは落ちない
- 今の `.claude/ccnavi/phases.yml` の 7 種は全部 `.ccnavi/config/phases.yml`（自身の層）へ
  移し、共通層の phases は空から始める。rules と risk は汎用なので共通層に残す

## 合成の規則

- 順は 共通層 → 自身の層 / プロジェクト層。`id` は共通層が裸、自身の層が `self:id`、
  プロジェクトが `<名前>:id`
- 裸の `id` と `{root}` 置換後の全欄が一致する定義は重複として後ろを捨てる。`--lint` が
  info で言い、`--explain` は残った 1 本だけ出す。Bash の和でも同じ
- 同 `id` で中身が違うとき: rules は両方効く + `--lint` warn。phases と risk は
  `--lint` error（phases は `title` の重なりも error）
- risk の `levels` はキーごとに小さいほう。合成後に `medium > high` などの逆転があれば error
- risk の `script:`: 共通層はワークスペースの `.claude/ccnavi/` と `.claude/scripts/`、
  各層は自身の `.ccnavi/scripts/` の下だけ。たがいの側は指せない。指す先が git プロジェクト
  ルートに無ければ `--lint` error。cwd は今どおり子の作業ツリー

## 守るもの

- 各層の `.ccnavi/config/` の 3 本を selfguard の中核に加える（ファイル単位、作業ツリー内の
  写しも）。共通層の phases.yml / risk.yml も同じ（既存の穴の修正。作業ツリーの中では
  allow `worktrees` が当たり Write / Edit で書けて復元されなかった）
- `.ccnavi/scripts/` は組み込み deny（`*/.ccnavi/*`、rules ファイルの外）+
  `CCNAVI_RESTORE_IF_DENY` で守る。作業ツリー内の新規ファイルは拾わない。設計書に明記し、
  `--lint` が「作業ツリーの `.ccnavi/` に git プロジェクトルートに無いファイルがある」を warn
- 設計書に前提を明記: ワークスペースの利用者は、プロジェクトの git プロジェクトルートに
  checkout されている版を信頼している

## 見せ方

- `--explain`: 層ごとに全件。ルールは `id / match / glob` を共通層・自身の層・各プロジェクトで
  並べ、phases と risk は `id / 出どころの層 / 主な欄` の表を足す
- 記録（`log.jsonl`、印、judge の記録）に `source: common | self | <名前>` を 1 欄足す
- `--lint`: `.ccnavi/config/` が無いことは言わない。旧 env、重複、同名の衝突、script の欠け、
  作業ツリー内の新規ファイルを言う
- 導入スクリプト: 共通層に `rules.yml` と `risk.yml`、自身の層に `phases.yml` のひな形を配り、
  「まだ無いもの」の点検にも数える

## 今回入れないもの

- VS Code 拡張の設定画面への phases / risk の追加
- `CCNAVI_TICKET_CONTROL` のプロジェクト単位化（README の語をワークスペース単位の意味に直すだけ）
- 反映先ブランチの git オブジェクトから設定を読む形
- プロジェクトの `.claude/` を認める形
- `.ccnavi/scripts/` を selfguard の中核にする形

## 受け入れる代償

- プロジェクトは共通層の `ask` を `allow` に緩められない
- 共通層の `allow` が全プロジェクトに効く
- Write / Edit に他プロジェクトのルールは足さないので「A のルールを B にも」は書けない
- 作業ツリーで足した `.ccnavi/` の変更は、git プロジェクトルートに入るまで効かない

## 承認前に人が決めること

- `.claude/ccnavi/phases.yml` の `implement` の scope は `ccnavi/*`, `tests/*`, `build.py`,
  `pyproject.toml` で、このチケットが触る `scripts/ccnavi-setup.sh`、`.ccnavi/config/*`、
  `.claude/ccnavi/phases.yml`（移動元）が入らない。`docs` の scope にも `CONTEXT.md` と
  `CLAUDE.md` が無い。phases.yml は人の持ち物なので、承認前に scope を広げるか、
  それらを別フェーズにするかを決める
- 親の識別子 `config-union` は仮。issue 番号で管理するなら `issue:` を足して改名する
