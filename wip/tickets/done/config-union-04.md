---
version: 1
ticket: config-union-04
parent: config-union
phase: 3
predecessors: [config-union-03]
title: 実装 2/3 — phases と risk の合成
rationale: |
  設計 §25.4.1 と §25.4.2 を実装する。phases は親の写しの `project:` で層を決め、共通層と
  id ごとに合成（同 id 違い・title の重なりは error で層を空に、全欄一致は info）。risk は
  factors 連結、同 id error、levels はキーごとの min（書かれた鍵だけ）、逆転 error、`script:` の
  解決先を層ごとに分け（共通層は `.claude/ccnavi/` と `.claude/scripts/`、層は `.ccnavi/scripts/`）、
  たがいの側は error、git プロジェクトルートに無ければ lint error。記録（`risk.json` / `judge.json` /
  種類を根拠に置く印）と `--explain` の phases / risk の表に `source`。
  受入テスト `tests/test_config_union_phases_risk.py` と `test_config_union.py` の `ExplainTest` の
  残りを緑にする。
human_review:
  required: true
  reason: フェーズのゲートとリスクの配点の読み方を変えるため
allow:
  - match: Write|Edit
    glob: "ccnavi/*"
  - match: Write|Edit
    glob: "tests/*"
started_at: "2026-09-13T00:10:08+0900"
completed_at: "2026-09-13T01:14:55+0900"
base_sha: "7cbee21ad98288ab77f5819709e9de4b588001cb"
---

# 実装 2/3 — phases と risk の合成

## 触る入口

- `ccnavi/phasetypes.py`: `merge(common, extra, layer)`、`PhaseType.source`、overlap / requires の参照確認を合成後にも
- `ccnavi/phase.py`: `load_types(conf, project)`、呼び元（`phases_of`、承認、`lint`、`diagnose`）に `project` を渡す
- `ccnavi/risk.py`: `parse` に `home`、`levels` は書かれた鍵だけ、`merge`、`Factor.source` / `Factor.home`、
  `run_script(factor.home, ...)`、`load_definition(conf, project)`
- `ccnavi/ops.py`: `_score_child` / `judge` が層付きの定義を使い、記録に `source`、閉じるときの出力に層の `fallback`
- `ccnavi/approval.py`: 記録の書式の注記、印の `source`
- `ccnavi/diagnose.py`: phases / risk の表、`--explain --json` の `layers[].phases` / `layers[].risk`
- `ccnavi/lint.py`: phases / risk の合成後の Problem（重複 info、同 id error、title、levels、script）

## 成果物

- `tests/test_config_union_phases_risk.py` と `tests/test_config_union.py` が全件緑
- 既存テスト全体が緑（`CONFIG_UNION_TODO.md` の phases / risk 関連を直す）
- ruff 通過

## やらないこと

- selfguard の控えと復元の対象の拡張、導入スクリプト、移行（3/3）
