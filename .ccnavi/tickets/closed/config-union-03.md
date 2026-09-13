---
version: 1
ticket: config-union-03
parent: config-union
phase: 3
predecessors:
- config-union-02
title: 実装 1/3 — 層の置き場と rules の和
rationale: '設計 `wip/design/config-union.md` の §25.2 と §25.4 を実装する。`CCNAVI_PROJECT_HOME`

  （既定 `.ccnavi`）と層の解決（共通層 / 自身の層 / プロジェクトの層）、rules の和

  （Write / Edit は共通層 + 行き先の層、Bash は全部の和）、`self:` / `<名前>:` の接頭辞、

  重複の排除、同 id の warn、無い層は空・壊れた層は空 + fallback、`projects/self/` の除外、

  旧 `CCNAVI_PROJECT_RULES` の warn、`--explain` の rules の層ごとの全件と `--explain --json`
  の

  `layers`、記録の `source`（rules の分）。組み込み deny `*/.ccnavi/*`（`builtin-guard-project-home`）

  とシェル書き込みの `_PLACES` もここで入れる（置き場の綴りと一緒に決まるため）。

  受入テスト `tests/test_config_union.py` の rules の面を緑にする。既存テストのうち

  置き場の変更で直すもの（`tests/CONFIG_UNION_TODO.md` の rules 関連）はここで直す。

  '
human_review:
  required: true
  reason: 判定の読み込み経路を変えるため
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
started_at: 2026-09-12T22:58:31+0900
completed_at: 2026-09-13T00:09:33+0900
base_sha: 12e79be43e92c837df45146b75f46e86ec306227
ccnavi_approved:
  approved_at: 2026-09-12T20:24:19+0900
  source_tree: config-union
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\config-union\wip\tickets\todo\config-union-03.md
---

# 実装 1/3 — 層の置き場と rules の和

## 触る入口（設計「実装の入口ごとの変更点」より）

- `ccnavi/settings.py`: `PROJECT_HOME_ENV` / `DEFAULT_PROJECT_HOME` / `Settings.project_home`、
  `layer_path(conf, home_root, kind)`、`CCNAVI_PROJECT_RULES` を `RETIRED_ENVS` へ
- `ccnavi/ruleload.py`: `rules_for` を和に。`prefix_ids`（`self:` / `<名前>:`）、`merge_rules`
  （重複の排除、同 id 違いの Problem）、壊れた層は空 + `record.fallback`、`layer_files(conf)`
- `ccnavi/rules.py`: `Rule.source` と比較用の `key()`
- `ccnavi/selfguard.py`: `project_home_clause` / `PROJECT_HOME_RULE_ID`（`*/.ccnavi/*`）、
  `_PLACES` / `_COPY_PLACES` に `\.ccnavi[\\/]`（控えと復元の対象の拡張は 3/3 で）
- `ccnavi/judge.py`、`ccnavi/events.py`、`ccnavi/post.py`、`ccnavi/audit.py`（`Record.source`）
- `ccnavi/diagnose.py`: `explain` の rules を層ごとの全件に、`--explain --json` に `layers`
- `ccnavi/lint.py`: 層の点検（3 本の読み込み、旧 `config/rules.yml` の warn、重複 info、同 id warn / error、
  `projects/self/`）、`retired` に旧 env
- `ccnavi/tree.py`: `self` を数えない判断が要ればここ

## 成果物

- `tests/test_config_union.py` が全件緑（`ExplainTest` の phases / risk の表は 2/3 で緑にしてよい。
  その場合はどのテストを残したかを報告に書く）
- 既存テスト全体が緑（`CONFIG_UNION_TODO.md` の rules 関連を直す）
- ruff format / check 通過

## やらないこと

- phases / risk の合成（2/3）、selfguard の控えと復元の対象の拡張・導入スクリプト・移行（3/3）
- `scripts/`、`.ccnavi/`、文書には触らない
