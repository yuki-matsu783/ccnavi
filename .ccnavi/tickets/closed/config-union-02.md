---
version: 1
ticket: config-union-02
parent: config-union
phase: 2
predecessors:
- config-union-01
title: 設定 3 本の和の受入テストを書く
rationale: '`wip/design/config-union.md`（フェーズ 1、レビュー済み）の振る舞いを、外から道具を動かす

  受入テストにする。入口（`python -m ccnavi` と同じ引数と標準入力、`tests/inproc.py`）より

  内側の関数は呼ばない。実装はまだ無いので、このテストは実装フェーズが終わるまで赤でよい。

  赤のテストは `unittest.expectedFailure` ではなく、そのまま赤で置く（実装が緑にする）。

  既存の `tests/test_projects.py` の作り（一時ディレクトリに git リポジトリと `projects/` を

  組み、`--projects` / `--rules` / `--mode` を渡して判定と `--lint` / `--explain --json` を

  読む）を手本にする。

  '
human_review:
  required: true
  reason: 実装が満たすべき振る舞いを固定するため
allow:
- match: Write|Edit
  glob: tests/*
started_at: 2026-09-12T19:14:59+0900
completed_at: 2026-09-12T20:08:34+0900
base_sha: 2c41d5ecac0f182d37120d655d71d56046fc707d
ccnavi_approved:
  approved_at: 2026-09-12T19:03:16+0900
  source_tree: config-union
  source_path: C:\Users\taniyama\Desktop\git\ccnavi\.claude\worktrees\config-union\wip\tickets\todo\config-union-02.md
---

# 設定 3 本の和の受入テストを書く

## 入力

- `wip/design/config-union.md`（設計。特に §25.4、§25.4.1、§25.4.2、§25.6、§25.9、§25.12 と REQ 候補）
- `tests/test_projects.py`、`tests/test_acceptance.py`、`tests/inproc.py`、`tests/test_selfguard.py`、
  `tests/test_lint.py`、`tests/test_phases.py`、`tests/test_risk.py`（手本と、既存のテストが
  今の置き場 `config/rules.yml` に依存している箇所）

## 成果物

- `tests/test_config_union.py` を新しく置く（1 本にまとめる。長ければ `test_config_union_rules.py` /
  `_phases_risk.py` / `_guard.py` に分けてよい）。次の振る舞いを 1 つずつテストにする
  - Write / Edit: 共通層 + 行き先の層の和（共通層の deny がプロジェクトのファイルに効く、
    プロジェクト層の deny がそのプロジェクトにだけ効く、ワークスペース自身の層 `.ccnavi/config/`
    がワークスペースのツリーに効く）
  - Bash: 共通層 + 自身の層 + 全プロジェクトの層。`cwd` によらず同じ
  - 同 `id` で全欄一致は後ろを捨てる（記録の `rules` に 1 本だけ、`--lint` に info）
  - 同 `id` で中身が違う rules は両方効く（記録に `credentials` と `lib:credentials` が並ぶ、`--lint` warn）
  - 壊れた層は空 + 記録の `fallback` に層の名前、共通層の deny は効いたまま、`--lint` error
  - 共通層自身が壊れたときは今どおり組み込みの既定（既存テストと同じ挙動）
  - `CCNAVI_PROJECT_RULES` が設定されていれば `--lint` warn、`CCNAVI_PROJECT_HOME` で傘の名前が動く
  - `projects/self/` は数えず `--lint` error
  - phases: 層の合成（`plan:` がプロジェクト層の種類を指せる）、同 `id` 違いと `title` の重なりは
    `--lint` error で層が空になる、全欄一致は info、`scope` は作業ツリー相対のまま
  - risk: `factors` 連結、同 `id` は error、`levels` はキーごとに min、逆転は error、
    `script:` が層の外を指すと error、記録の `source`
  - selfguard: 各層の `.ccnavi/config/` 3 本と共通層の phases / risk が控えと復元の対象、
    プロジェクトから切った作業ツリーの写しも対象、`*/.ccnavi/*` への Write / Edit とシェル書き込みが
    ルール無しで deny
  - `--explain --json` に層ごとの rules 全件と phases / risk の定義と出どころが出る
  - `projects/` が無いか空なら、既存の判定・記録が変わらない（既存の受入テストが通り続けることで担保。
    ここでは `.ccnavi/config/` を置かないワークスペースで判定が今と同じであることを 1 本足す）
  - 導入スクリプト（`tests/test_setup.py` の作りを手本に）: 共通層に rules / risk、自身の層に phases の
    ひな形が配られ、無ければ点検が言う
- 既存テストのうち、置き場が `config/rules.yml` から `.ccnavi/config/rules.yml` に変わることで
  変える必要があるものは、**変えずに一覧だけ** `tests/CONFIG_UNION_TODO.md` に書く（テスト名と
  何が変わるか）。実装フェーズが直す

## やらないこと

- `ccnavi/` のコードには触らない
- 既存テストを書き換えない（一覧に書くだけ）
