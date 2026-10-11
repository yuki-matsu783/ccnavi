---
version: 1
ticket: refactor-296-rule-expectations-03-01
parent: refactor-296-rule-expectations
phase: 3
title: ルールの期待ファイルを rule-expectations.yml に切り替える
rationale: |
  フェーズ 3。ファイル名 rule-samples.yml を rule-expectations.yml に改め、旧名を指す文字列を同時に新名へ揃える。
  ファイルは書き込み保護の対象なので、新名のファイルを wip/scratchpad/ に作り、差し替えはユーザが行う。
human_review:
  required: true
  reason: ファイル名という外に見える名前を変え、保護対象のファイルの差し替えをユーザに頼むため
allow:
  - match: Write|Edit
    glob: "src/ccnavi/tickets/configsync.py"
  - match: Write|Edit
    glob: "src/ccnavi/entry/suggest.py"
  - match: Write|Edit
    glob: "src/ccnavi/entry/cli_usage.py"
  - match: Write|Edit
    glob: "scripts/check_rules.py"
  - match: Write|Edit
    glob: "tests/core/test_check_rules.py"
  - match: Write|Edit
    glob: "tests/config/test_config_sync.py"
  - match: Write|Edit
    glob: "tests/config/test_config_union_guard.py"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/src/rules-panel.ts"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/package.json"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/README.md"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/test/*"
  - match: Write|Edit
    glob: "wip/scratchpad/*"
started_at: ""
completed_at: ""
base_sha: ""
---
## 目的

`.ccnavi/common/rule-samples.yml` を `.ccnavi/common/rule-expectations.yml` に改める。フェーズ 1・2 は旧名のファイルを
そのまま読む形で閉じてあるので、ここでファイル名と、旧名を指す文字列を同じ区切りで切り替える。旧名の別名・読み替えは入れない（親の「決定済みの点」）。

文書（README・`docs/`・スキル・ADR）に残る旧名と「見本」はフェーズ 4 の仕事で、この子では触らない。

## 先に洗った結果

`grep -rn "rule-samples\|samples\.yml" src/ scripts/ tests/ extensions/`（node_modules・out・dist を除く）で **12 ファイル・18 行**。
`.ccnavi/` 配下（ファイル本体とその自己参照）は別に直す。

| ファイル | 行 | 内容 |
|---|---|---|
| `scripts/check_rules.py` | 1 | `EXPECTATIONS` のパス（24 行目）。**mv と同時でないと読めない** |
| `src/ccnavi/tickets/configsync.py` | 2 | `EXPECTATIONS_NAME` の値（38）、docstring（9） |
| `src/ccnavi/entry/suggest.py` | 3 | docstring（20）、コメント（111）、出力の文言（477） |
| `src/ccnavi/entry/cli_usage.py` | 1 | help 文の例（107） |
| `tests/config/test_config_sync.py` | 4 | `EXPECTATIONS_NAME` の鏡（87・94・169・176） |
| `tests/core/test_check_rules.py` | 1 | docstring（7）。リポジトリのファイルを読むのは `check_rules.py` の側 |
| `tests/config/test_config_union_guard.py` | 1 | コメント（391） |
| `extensions/vscode/ccnavi-board/src/rules-panel.ts` | 1 | `DEFAULT_EXPECTATIONS` の値（52） |
| `extensions/vscode/ccnavi-board/package.json` | 1 | `ccnaviBoard.expectationsPath` の `default`（117） |
| `extensions/vscode/ccnavi-board/README.md` | 1 | 設定表の「既定は …」（24） |
| `extensions/vscode/ccnavi-board/test/helpers/rules.ts` | 1 | `expectationsPath` の値（54） |
| `extensions/vscode/ccnavi-board/test/fixtures/expectations.json` | 1 | `expectations_path` の値 `/ws\\samples.yml`（5）→ `/ws\\expectations.yml` |

加えて、同じフィクスチャの 97 行目の `why`「食い違いの見本。…」を「食い違いの期待する判定。…」に改める。Python 側
`tests/core/test_test_json.py` は、既に同じ文言（67 行目）と `expectations.yml` のパスを書いていて、フィクスチャとの突き合わせは鍵だけ
（`_same_keys`）なので、**この子では触らない**。フィクスチャが書き出し側の古い値に残っていただけ。

`.ccnavi/common/rule-samples.yml` の中身: 「見本」が 1・12・106・110・112・116・124・153・228 行目、自己参照の `subject`
（`/repo/.claude/worktrees/x/.ccnavi/common/rule-samples.yml`）が 150 行目。

## 手順

1. `wip/scratchpad/rule-expectations.yml` に、旧ファイルを `cp` する。
2. その中の「見本」を「期待する判定」に改める。先頭コメントの題（1 行目）と 12 行目、why の言い回し（106・110〜124・153・228 行目の「見本」。
   文脈で「期待」に縮めてよい）、`samples` を指す語があれば `expectations`。150 行目の自己参照の `subject` は
   `/repo/.claude/worktrees/x/.ccnavi/common/rule-expectations.yml` にする。YAML のキーはフェーズ 1 で `expectations:` に済んでいる。
   `.env.sample` や「見本の .env」のようにファイル名そのものを指す語は直さない。
3. 上の表の 12 ファイルを、この子の範囲で書き換える。
4. 新ファイルが `wip/scratchpad/` に揃ったら、ターンを終え、ユーザに次の 2 つを**フルパス**で渡す（作業のワークツリーの絶対パスで書く）。
   - `mv <ワークツリー>/wip/scratchpad/rule-expectations.yml <ワークツリー>/.ccnavi/common/rule-expectations.yml`
   - `rm <ワークツリー>/.ccnavi/common/rule-samples.yml`（旧ファイルを残さない。git mv で一度に済ませてもよい）
5. ユーザが差し替えたら、確かめて子を閉じる。

## 順序の注意

mv と文字列の書き換えは同時でないと、次のテストが落ちる。

- `tests/core/test_check_rules.py`（`check_rules.py` が期待ファイルを読む）
- `tests/config/test_config_sync.py`（`configsync.py` の名前とその鏡）
- 拡張のテスト（`DEFAULT_EXPECTATIONS` と `test/helpers/rules.ts` の値が実在のファイルと合うかを見るもの）

手順 3 を終えて、ユーザが手順 4 を打つまでの間は、これらの一部が落ちる。これは想定どおりで、子は mv の後に試験を回して閉じる。
手順 3 だけを先にコミットしない（コミットは mv の後）。

## 完了条件（mv の後）

- `.ccnavi/common/rule-samples.yml` が無く、`rule-expectations.yml` が在る。
- `python -m unittest discover tests/core` と `tests/config` が通る。
- `extensions/vscode/ccnavi-board` で `pnpm test` が通る。
- `grep -rn "rule-samples\|samples\.yml" src/ scripts/ tests/ extensions/ .ccnavi/` に残りがない（`node_modules`・`out`・`dist` を除く）。残るのは文書・スキル・ADR（フェーズ 4）だけ。
- `scripts/check_rules.py` が新ファイルを読んで全件の期待どおりを返す。
