---
version: 1
ticket: refactor-296-rule-expectations-01-01
parent: refactor-296-rule-expectations
phase: 1
title: 実行ファイルと試験の「見本」を「期待する判定」に改める
rationale: |
  フェーズ 1。CLI の旗・YAML のキー・関数名・定数・試験を新しい名前に置き換える。
  外から見える契約（旗とキー）を先に決め、VS Code 拡張（フェーズ 2）と文書（フェーズ 3）が従う。
human_review:
  required: true
  reason: CLI の旗と JSON / YAML のキーという外に見える契約を変えるため
allow:
  - match: Write|Edit
    glob: "src/ccnavi/*"
  - match: Write|Edit
    glob: "scripts/check_rules.py"
  - match: Write|Edit
    glob: "tests/*"
started_at: "2026-10-11T17:12:31+0900"
completed_at: "2026-10-11T17:27:41+0900"
base_sha: "a52c008e2f8e80782331afbba4a7c335886a67e7"
---
## 目的

ルールの「見本 / sample」を、日本語の文書・コメントでは「期待する判定」（短くしてよい所は「期待」）、英語側は expectation(s) に改める。
旧名の別名・読み替え・warn / error は入れない（展開前のため。親の「決定済みの点」）。

## 直すもの

- `src/ccnavi/entry/cli_args.py`: 旗 `--test-samples` を `--test-expectations` に置き換える。旧旗は残さない。
- `src/ccnavi/entry/cli.py`・`cli_usage.py`（help 文）・`diagnose.py`（`test_samples`）・`diagnose_try.py`（`load_samples`・`run_samples`・`SAMPLE_PLACEHOLDER`）: 関数名・定数名・docstring・エラー文言（「見本の形が違う」など）を改める。
- `src/ccnavi/entry/suggest.py`: `samples`・`SAMPLE_LIMIT`・`_samples_pass` と、`--suggest --json` が出す `samples` キーおよび YAML 下書きの `samples:` を `expectations` に改める。
- `src/ccnavi/infra/settings.py` と `src/ccnavi/tickets/approval_checks.py` などの当たり: ルールの期待を指すものだけ直す。`approval_checks.py` の `_undecided(sample, …)` は試験の代役の変数で、別の意味なので直さない。
- `src/ccnavi/tickets/configsync.py`: 定数名 `SAMPLES_NAME` と docstring の言い回しを改める。**値の文字列 `"rule-samples.yml"` は変えない**（下の注意）。
- `scripts/check_rules.py`: 説明・変数名・旗の呼び出し（`--test-expectations`）を改める。**`SAMPLES` のパスが指す `rule-samples.yml` は変えない**。
- `tests/`: `test_test_json.py`・`test_check_rules.py`・`test_rule_when.py`・`test_config_sync.py`・`test_stop_rules.py`・`test_common_layer_place.py`・`test_config_union_guard.py`・`test_suggest.py`・`test_repo_rules.py` の旗・キー・言い回し。

`sample` の機械置換は使わない。別の意味の `sample`（`tests/sh/github_host.py` などのホスト応答の見本、`tests/ticket/*`、`.env.sample`）は直さない。

## 保護対象のファイルを触らない間、壊れないことの確認

`.ccnavi/common/rule-samples.yml` の差し替えはフェーズ 3（ユーザが `mv`）。それまでの間に壊れる所がないかを調べた結果:

- `diagnose_try.load_samples` が読むのは最上位の `deny` / `ask` / `allow` だけ。ファイル内の `samples:` というキーは読まない（`rule-samples.yml` にもその行は無い）。よって旧ファイルの中身は新しい読み手でそのまま読める。
- `scripts/check_rules.py` の `SAMPLES` が旧名のパスを指したまま `--test-expectations` に渡せば、`tests/core/test_check_rules.py`（`os.path.isfile(SAMPLES)` とリポジトリの期待する判定の一括確認）は壊れない。
- `configsync.py` の鏡の対象名を旧名のまま残せば、`tests/config/test_config_sync.py` の鏡の試験も、実リポジトリの写しも壊れない。試験が書く `SAMPLES = "version: 1\nsamples: []\n"` は旧名のファイルに置く中身なので、キーだけ `expectations: []` に改め、ファイル名はこの子では変えない。
- 結論: この子の範囲で、ファイル名の文字列 3 か所（`check_rules.py`・`configsync.py`・上の試験）を旧名に固定しておけば、フェーズ 1 だけが入った状態でも試験は通る。

この子では `rule-samples.yml` の `mv` も、旧名の文字列 3 か所（`scripts/check_rules.py` の `SAMPLES` パス、`configsync.py` の値、`tests/config/test_config_sync.py` の鏡）の書き換えもしない。フェーズ 1 は旧名固定のままにする。
この 3 つはフェーズ 3 で、ファイルの `mv` と同時に行う（ユーザの決定）。フェーズ 3 は `implement` で、実行ファイル・試験の範囲を持つ（文書はフェーズ 4）。

## 確かめること

- `ccnavi --test-expectations .ccnavi/common/rule-samples.yml` が通り、`--test-samples` は旗として拒まれる。
- `core`・`config`・`guard` の試験を通す（`commit` スキルの試験の分け方に従う）。
- `grep -rn "test-samples\|load_samples\|run_samples\|test_samples" src scripts tests` に残りがない。
