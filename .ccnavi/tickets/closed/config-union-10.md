---
version: 1
ticket: config-union-10
parent: config-union
phase: 4
predecessors:
- config-union-08
title: 文書と実装の食い違い 2 件を直す
rationale: '文書への敵対的レビューが、文書を信じた人が実際の挙動に裏切られる箇所を 2 件見つけた。

  1 件は README に移行の順番の警告が無いこと。README だけを読む利用者が旧 `phases.yml` を先に

  消すと、古い実行ファイルがフェーズの種類を読めなくなり、承認とゲートが止まる（このチケットの

  作業中に実際に起きた）。もう 1 件は `--lint --json` の `where` 欄の例が、自身の層について

  実装と違う綴りになっていること。例を信じて拡張などを書くと、自身の層の苦情を拾えない。

  '
human_review:
  required: true
  reason: 利用者が手順どおりに進めて壊れる箇所を直すため
allow:
- match: Write|Edit
  glob: README.md
- match: Write|Edit
  glob: ccnavi.md
started_at: 2026-09-13T09:23:50+0900
completed_at: 2026-09-13T09:27:32+0900
base_sha: 54208d29294e849c657ede0b0b962b2cc2b97585
ccnavi_approved:
  approved_at: 2026-09-13T09:23:15+0900
  source_tree: config-union
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\config-union\wip\tickets\todo\config-union-10.md
---

# 文書と実装の食い違い 2 件を直す

## 1（高）README に移行の順番の警告が無い

`ccnavi.md` の §25.12 と `HANDOVER.md` には「新しい実行ファイルを配ったあとに旧
`.claude/ccnavi/phases.yml` を消す」と書いてあるが、`README.md` の「実行ファイルとルールを配る」節
（296〜364 行付近）には無い。README だけを読む利用者が既存のワークスペースを移行するとき、先に
旧ファイルを消してから実行ファイルを配ると、古い実行ファイルは自身の層を読まないのでフェーズの
種類が全部消え、`plan:` を持つチケットの承認とゲートが止まる。

直し方: README の該当節に「移行」の小節を足し、次の順番とその理由を書く。

1. 自身の層 `.ccnavi/config/phases.yml`（`CCNAVI_PROJECT_HOME` の既定値の下）に種類を置く。
   旧 `.claude/ccnavi/phases.yml` は**まだ消さない**
2. 新しい実行ファイルを組んで配る
3. 旧 `.claude/ccnavi/phases.yml` を消す

逆にしたときに何が止まるか（`plan` があるのにフェーズの種類の定義が読めない、という承認の
エラー）も、見た人が原因に辿り着けるよう文面ごと書く。

## 2（中）`--lint --json` の `where` の例が自身の層だけ違う

`ccnavi/lint.py` の `layer_where()` は、自身の層に対して `(self)` を返す。文書の例は
`(自身の層)` になっている。

- `ccnavi.md` の §25.9 付近（`(自身の層) rule-id`）
- `README.md` の lint JSON の節（`(自身の層) (phases) design`）

共通層 `(rules)` とプロジェクトの層 `(projects/<名前>)` の例は実装と一致している。

直し方: 両文書の例を `(self) rule-id` と `(self) (phases) design` に直す。実装の綴りを変える
形は採らない（`--lint --json` は拡張が読む外向きの形なので、文書を実装に合わせる）。

## やらないこと

- コードは触らない
- 範囲外として残した食い違い（§25.3 と §25.5 の `repo`、HANDOVER の件数）はこのチケットでは扱わない
