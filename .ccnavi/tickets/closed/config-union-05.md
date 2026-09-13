---
version: 1
ticket: config-union-05
parent: config-union
phase: 3
predecessors:
- config-union-04
title: 実装 3/3 — 自己保護の対象、導入スクリプト、移行
rationale: '設計 §25.6 と §25.9 の導入スクリプト、§25.12 の移行を実装する。selfguard の控えと復元の対象を

  各層の `.ccnavi/config/` の 3 本と共通層の phases.yml / risk.yml に広げ（ファイル単位、

  作業ツリー内の写しも。`_worktree_copies` を切り元付きに）、`.ccnavi/scripts/` は組み込み deny +

  `CCNAVI_RESTORE_IF_DENY` に任せる。`--lint` に「作業ツリーの `.ccnavi/` に切り元に無いファイルが

  ある」の warn。導入スクリプトは共通層に rules.yml と risk.yml、自身の層に phases.yml のひな形を

  配り、「まだ無いもの」の点検に数え、`--all` の env に `CCNAVI_PROJECT_HOME` を足す。

  移行として、このワークスペース自身の層 `.ccnavi/config/phases.yml` を今の

  `.claude/ccnavi/phases.yml` の中身で作る（旧の削除は人。理由は下）。

  受入テスト `tests/test_config_union_guard.py` を緑にし、`tests/CONFIG_UNION_TODO.md` を消す。

  '
human_review:
  required: true
  reason: 自己保護の対象と導入スクリプトの配りものを変えるため
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: scripts/*
- match: Write|Edit
  glob: .ccnavi/*
started_at: 2026-09-13T01:15:09+0900
completed_at: 2026-09-13T01:55:43+0900
base_sha: 0b1efa4879bfeb887a50d5b35544818f054d66b5
ccnavi_approved:
  approved_at: 2026-09-12T20:24:19+0900
  source_tree: config-union
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\config-union\wip\tickets\todo\config-union-05.md
---

# 実装 3/3 — 自己保護の対象、導入スクリプト、移行

## 触る入口

- `ccnavi/selfguard.py`: `targets` の引数を層の並び（層、kind、パス）に、共通層の phases / risk を足す、
  `_worktree_copies(root, projects_dir)` で切り元付きに列挙して写しの相対を切り元から組む、
  冒頭 docstring の「3 つだけ」を「各層の設定 3 本」に
- `ccnavi/judge.py` / `ccnavi/events.py`: `selfguard.targets` に渡す並びを `ruleload.layer_files(conf)` に
- `ccnavi/lint.py`: 作業ツリーの `.ccnavi/` の新規ファイルの warn
- `scripts/ccnavi-setup.sh`: `DEPLOY_RISK` / `DEPLOY_PHASES`、`note_missing`、`--all` の `CCNAVI_PROJECT_HOME`
- `.ccnavi/config/phases.yml`: 今の `.claude/ccnavi/phases.yml` の 7 種をそのまま置く
- `tests/`: `test_config_union_guard.py` を緑に、`test_selfguard.py` / `test_setup.py` の既存を直す、
  `CONFIG_UNION_TODO.md` を消す

## 人の手が要るもの（閉じる前に報告する）

- `.claude/ccnavi/phases.yml` の削除。rules の `guard-ccnavi-config` と selfguard の写しの保護が
  先に当たるので、エージェントは消せない。`.ccnavi/config/phases.yml` を置いたあとに人が消す。
  消すまでは共通層と自身の層に同じ 7 種があり、全欄一致なので重複として後ろが捨てられ、
  判定は変わらない（`--lint` が info で言う）

## 成果物

- 新規 3 本と既存テスト全体が緑、ruff 通過
- `uv run --with pyinstaller python build.py` で実行ファイルが組める（配るのは人）
