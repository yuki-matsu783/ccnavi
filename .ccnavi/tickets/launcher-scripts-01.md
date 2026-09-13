---
version: 1
ticket: launcher-scripts-01
parent: launcher-scripts
phase: 1
title: 設計を書く（振り分けの sh を .ccnavi/scripts/ へ、--bin の廃止）
rationale: '親チケット launcher-scripts の決定を、ADR-0041 の改版として `wip/design/launcher-scripts.md`
  に書く。置き場の表、sh の探し方、自己保護の対象、導入スクリプトの移し替え、このリポジトリの build.py の写し先、`--bin` 廃止の扱いを決め、実装の入口ごとに何を変えるかを
  1 行ずつ添える。 コードには触らない。

  '
human_review:
  required: true
  reason: hook の起動先と自己保護の対象を変える設計のため
allow:
- match: Write|Edit
  glob: wip/design/*
started_at: ''
completed_at: ''
base_sha: ''
ccnavi_approved:
  approved_at: 2026-09-13T19:15:00+0900
  source_tree: launcher-scripts
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/launcher-scripts/wip/tickets/todo/launcher-scripts-01.md
---

# 設計を書く

## 入力

- 親チケット `.ccnavi/tickets/launcher-scripts.md` の決定一覧
- ADR-0041、ADR-0005、ADR-0021（selfguard）、ADR-0042（ccnavi ディレクトリ）
- 今の `scripts/ccnavi-launcher.sh`、`scripts/ccnavi-setup.sh`、`ccnavi/platformtag.py`、
  `ccnavi/selfguard.py` の `binary_clause` と `launched_executable` の使いどころ

## 成果物

- `wip/design/launcher-scripts.md`。次を含む
  - 改版後の置き場の表（原本・配布先・このリポジトリ）
  - sh が実体を探す順と、見つからないときの文面と終了コード
  - 自己保護: `CCNAVI_BIN_PATH` の指す sh と `.ccnavi/bin/<os>-<arch>/` をどう当てるか。
    `binary_clause` と `launched_executable` の新しい形
  - 導入スクリプト: `--bin` を渡されたときの扱い、前の既定の書き換え、既定でない綴りの報告、
    古い `.ccnavi/bin/ccnavi` を消す条件
  - `build.py`: `.ccnavi/bin/<os>-<arch>/` へ写す手順と、写す前後で hook が起動できない窓の扱い
  - `ccnavi.settings.local.json` の上書きを残すか
  - VS Code 拡張の実行ファイルの探し方
  - 実装の入口ごとの変更点の一覧（1 行ずつ）と、受入テストで押さえる振る舞いの一覧
  - ADR-0041 を改める ADR の骨子
  - requirements.md に足す・直す REQ の候補（文面だけ）

## やらないこと

- コードと文書本体には触らない
- phases.yml の scope は変えない（人の持ち物）
