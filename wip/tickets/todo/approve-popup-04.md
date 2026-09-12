---
version: 1
ticket: approve-popup-04
parent: approve-popup
phase: 4
predecessors: [approve-popup-03]
title: 承認ポップアップの文書（README・設計書・要求・引き継ぎ）
rationale: |
  設計 wip/design/approve-popup.md §4 の文書。実行ファイルの README に「承認の JSON」
  （`--approve --preview --json` / `--approve --yes … --json` の形）と、hook が承認を 1 度伝える
  ことを書く。ccnavi.md §17 で端末の壁の位置づけを直し（`--yes` は壁を持たず、組み込み deny が
  エージェントの経路を塞ぐ。dry-run では止まらないことを受け入れた）、§24.10 の「拡張の子プロセスを
  壁の外に置く」を「承認は拡張の子プロセスが打つ。accept / wrapup は端末のまま」に書き換える。
  requirements.md の REQ-APV に経路が増えたことを足し、HANDOVER.md に経緯を残す。
  拡張の README はフェーズ 3 で直した。
human_review:
  required: true
  reason: 人の合意の経路の説明が変わる
allow:
- match: Write|Edit
  glob: README.md
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: requirements.md
- match: Write|Edit
  glob: HANDOVER.md
started_at: ''
completed_at: ''
base_sha: ''
---

# 承認ポップアップの文書

| 文書 | 何 |
|---|---|
| `README.md` | 「効くのは承認したものだけ」の段に preview / yes。「ボードの JSON」の隣に「承認の JSON」。hook が承認を伝えること |
| `ccnavi.md` §17 | 端末の壁の位置づけと、`--yes` の経路 |
| `ccnavi.md` §24.10 | 承認は拡張の子プロセス、accept / wrapup は端末 |
| `requirements.md` | REQ-APV の該当 |
| `HANDOVER.md` | 経緯 |
