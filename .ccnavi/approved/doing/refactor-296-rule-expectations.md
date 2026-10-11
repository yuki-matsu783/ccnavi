---
version: 1
ticket: refactor-296-rule-expectations
title: ルールの見本を「期待する判定」に改名する（rule-samples.yml を rule-expectations.yml へ）
rationale: |
  ルールが何を止めて何を通すかを書いたファイルを `rule-samples.yml`、中身を「見本」「sample」と呼んでいる。
  実際は「この呼び出しはこの判定になるはず」という期待する判定で、見本（手本）とは意味がずれる。
  また「見本」は Chrome 拡張のホスト応答の fixtures やボードの案内用ダミーにも使われ、語が衝突している。
  ファイル名・CLI・YAML のキー・拡張の設定・関数名・文書の言い回しを、1 つの語に揃えて改める。
human_review:
  required: true
  reason: CLI の旗・YAML のキー・拡張の設定名・ファイル名という外に見える名前を変えるため
phases:
  implement:
    kind: work
    title: 実装とテスト
    review: mr
    scope:
    - src/*
    - tests/*
    - build.py
    - pyproject.toml
    - scripts/*
    - .ccnavi/*
    - .ccnavi/common/phases.yml
    - extensions/*
    when: 振る舞いを変えるとき。受入テスト作成を先に置く（計画に受入テスト作成の項も置く）
  docs:
    kind: work
    title: 文書
    review: mr
    scope:
    - README.md
    - docs/*
    - extensions/vscode/ccnavi-board/docs/*
    - extensions/chrome/ccnavi-approval/docs/*
    - CLAUDE.md
    - .claude/skills/*
plan:
  - implement                      # 1。src・scripts・tests（フェーズ 1）
  - {type: implement, after: [1]}  # 2。VS Code 拡張（フェーズ 2）
  - {type: implement, after: [1, 2]}  # 3。ファイル名の切り替え（フェーズ 3）
  - {type: docs, after: [1, 2, 3]}    # 4。文書・スキル・ADR（フェーズ 4。終端）
allow:
  - match: Write|Edit
    glob: "src/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "scripts/*"
  - match: Write|Edit
    glob: "extensions/*"
  - match: Write|Edit
    glob: "docs/*"
  - match: Write|Edit
    glob: "README.md"
  - match: Write|Edit
    glob: ".claude/skills/ccnavi-config/*"
  - match: Write|Edit
    glob: ".claude/skills/commit/references/*"
  - match: Write|Edit
    glob: "wip/*"
started_at: "2026-10-11T17:12:25+0900"
completed_at: ""
base_sha: "a52c008e2f8e80782331afbba4a7c335886a67e7"
---
## 結論

`rule-samples.yml` を `rule-expectations.yml` に改め、「見本」「sample」のうちルールの期待を指す使い方を
すべて新しい語に揃える。互換や移行は持たない（展開前のアプリのため）。実装は承認後。
`.ccnavi/common/rule-samples.yml` は保護対象なので、実装時は `wip/scratchpad/` に写して編集し、差し替えのコマンドをユーザに案内する。
識別子の番号 296 は仮（通し番号。承認時に合わなければ `--lint` が warn する）。

## 決定済みの点（ユーザの決定）

- CLI: 新しい `--test-expectations` に置き換え、旧 `--test-samples` は即廃止する。別名で残さない。
- 移行は考慮しない。旧ファイル名・旧キー `samples:`・旧設定 `samplesPath` の読み替えも warn / error も入れない。拡張設定は `samplesPath` を `expectationsPath` に改名するだけ。
- configsync の写し先に残る旧名の扱いは考えない。
- 過去の ADR の本文は書き換えない。改名を記録する ADR を 1 枚足す（決定を覆すのではないので置き換え扱いにはしない。旧名が 0016・0032・0035・0042・0067 に残ることを「状況」に書く）。
- 用語: 日本語の文書・コメントでは「期待する判定」（文脈上短くしてよい所は「期待」）。「見本」は「期待する判定」に置き換える。英語側は expectation(s) のまま。
  - ファイル `rule-expectations.yml`
  - YAML のキー `expectations:`（`--suggest --json` が出す `samples` キーも同じ）
  - CLI `--test-expectations`
  - 拡張の設定 `ccnaviBoard.expectationsPath`

## 影響範囲（grep -rniE "sample|見本" の仕分け）

全体で 159 ファイルが当たる。うち約 53 ファイルがルールの期待の意味で、残りは別の意味の語。

### 改名の対象（約 53 ファイル）

| 区分 | 主なファイル | 内容 |
|---|---|---|
| 置き場・ファイル | `.ccnavi/common/rule-samples.yml`（保護対象） | ファイル名、先頭コメント、`samples:` キー |
| 実行ファイル（src/ 13） | `src/ccnavi/entry/cli_args.py`（`--test-samples`）、`cli.py`、`cli_usage.py`（help 文）、`diagnose.py`（`test_samples`）、`diagnose_try.py`（`load_samples`・`SAMPLE_PLACEHOLDER`）、`suggest.py`（`samples`・`SAMPLE_LIMIT`・`_samples_pass`・JSON/YAML の `samples` キー）、`tickets/configsync.py`（`SAMPLES_NAME`） | CLI の旗、関数名、定数、候補の JSON |
| スクリプト | `scripts/check_rules.py` | パス定数、説明、旗の呼び出し |
| VS Code 拡張（約 20） | `package.json`（設定 `ccnaviBoard.samplesPath`、既定値のパス）、`README.md`、`src/ccnavi.ts`（`runSamples`、旗）、`src/rules-panel.ts`（`DEFAULT_SAMPLES`、`samplesPath`、`samplesRel`）、`src/core/rules-view.ts`、`src/core/testmodel.ts`（`parseSamplesJson`）、`src/core/suggestmodel.ts`（`SuggestSampleJson`・`samples`）、`src/webview/rules/App.tsx`・`Judge.tsx`、`docs/design/structure.md`、`docs/requirements/rules.md`、`test/fixtures/samples.json`・`suggest.json`、`test/rules/*`、`test/helpers/rules.ts` | 設定名、型名、画面の文言、フィクスチャ名 |
| テスト（tests/ 約 8） | `tests/core/test_test_json.py`、`test_check_rules.py`、`test_rule_when.py`、`tests/config/test_config_sync.py`・`test_stop_rules.py`・`test_common_layer_place.py`・`test_config_union_guard.py`、`tests/guard/test_suggest.py`・`test_repo_rules.py` | 旗、ファイル名、YAML の `samples:` |
| 文書 | `README.md`（約 14 行）、`docs/design/diagnostics.md`・`startup-config.md`・`self-guard.md`・`multi-repo/places.md`、`docs/requirements/diagnostics.md`・`extension-if.md` | 言い回しと旗の表 |
| スキル | `.claude/skills/ccnavi-config/SKILL.md`・`references/add.md`・`references/check.md`、`.claude/skills/commit/references/test-groups.md`（1 行） | 手順の旗とファイル名 |
| ADR | 既存の 0016・0032・0035・0042・0067、`docs/adr/README.md`（目次） | 本文は書き換えない。新しい ADR を 1 枚足し、目次に載せる |

### 対象にしない（別の意味。約 100 ファイル）

- Chrome 拡張とその sh の試験に置く「ホスト応答の見本」（fixtures）: `extensions/chrome/ccnavi-approval/**`（25 ファイル）、`tests/sh/github_host.py`・`gitlab_host.py`・`test_review_host_fixture.py`・`test_gitwrap.py`・`test_c1_sh.py`、ADR-0093 の「見本」約 40 箇所、ADR-0104。ルールの期待ではなく試験データ。
- ボードの初回案内用のダミー: `extensions/vscode/ccnavi-board/src/core/tour-sample.ts`（`sampleBoard`・`SAMPLE_NOTE`）、`webview/**/App.tsx` の `sample` 変数、`Tour.css`、ADR-0082・0083。
- 試験の代役の変数（`ticket_model.Ticket` を受ける `_undecided(sample, …)`、`tests/ticket/*`、`test/workflow/*`、`test/helpers/*` の多く）。
- `.ccnavi/common/rules.yml` の「見本の .env」・`.env.sample`（ファイル名そのものを指す）。
- ADR-0051・0066・0090・0109 などの一般語としての「見本」。

注意: `sample` の機械置換は対象外の約 100 ファイルを壊す。置換は区分ごと・語ごとに当てる。

## メリット

- 名前が中身（期待する判定）と合い、初めて読む人が手本と誤読しない。
- 「見本」が指していた別物（ルールの期待／ホスト応答の fixtures／案内のダミー）が分かれ、grep が効く。
- 文書・スキル・拡張・CLI・コードで語が揃う。
- 互換や移行を持たないので、別名や読み替えの保守が残らない。

## デメリット

- 外に見える名前（旗・YAML キー・拡張の設定・ファイル名）が変わる。旧 `--test-samples` を使う呼び出しや、旧名のファイルは、改名の時点で動かなくなる（展開前のため受け入れる）。
- 変更は約 53 ファイル・複数言語（Python・TS・sh・YAML・Markdown）にまたがり、機械置換できない。取りこぼしの危険がある。
- 過去の ADR は旧名のまま残るので、履歴を grep するときは旧名と新名の両方を引く。
- 保護対象のファイルの差し替えが、ユーザの手で要る。

## 作業の分け方（案）

親の `plan:` は 4 項（フロントマター参照）。フェーズ定義は `implement`（フェーズ 1〜3。`review: mr`）と `docs`（フェーズ 4。`review: mr`）。
フェーズ 2 はフェーズ 1 を、フェーズ 3 はフェーズ 1・2 を、フェーズ 4 はフェーズ 1〜3 を待つ（終端はフェーズ 4 の 1 つ）。
`implement` と `docs` の両方の範囲を持つ定義が無いので、ファイル名の切り替えを `implement` のフェーズ 3 に分けた（ユーザの決定）。

今回の承認で出すのは、親とフェーズ 1 の子 `refactor-296-rule-expectations-01-01` だけ（1 回の `--agree`）。
フェーズ 2 以降の子は、前のフェーズが閉じてレビューが済むまで承認できないので、今は出さず、その時点で提案する。

1. フェーズ 1（実行ファイルと設定の契約。子 01-01 を今回出す）: `src/`・`scripts/`・`tests/` を改め、新しい旗・キー・関数名に置き換える（旧名は残さない）。
2. フェーズ 2（VS Code 拡張）: 設定 `expectationsPath`、型・関数名・画面の文言、フィクスチャ名、拡張の README と設計書。
3. フェーズ 3（ファイル名の切り替え）: `rule-samples.yml` を `rule-expectations.yml` に改め、旧名の文字列 3 か所（`scripts/check_rules.py` の `SAMPLES` パス、`configsync.py` の値、`tests/config/test_config_sync.py` の鏡）を同時に書き換える。保護対象の `.ccnavi/common/rule-samples.yml` は、`wip/scratchpad/` に新名のファイルを作り、`mv` のコマンドをフルパスでユーザに渡す。ユーザが `mv` を打つまでは 3 か所を旧名のままにせず、`mv` と書き換えを同じ区切りで揃えて試験を通す手順を子の本文に書く。
4. フェーズ 4（文書・スキル・ADR）: README・設計書・要件・スキル 4 枚・改名を記録する新しい ADR。
