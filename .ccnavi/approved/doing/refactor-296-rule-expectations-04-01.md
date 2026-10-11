---
version: 1
ticket: refactor-296-rule-expectations-04-01
parent: refactor-296-rule-expectations
phase: 4
title: 文書・スキルの「見本」を「期待する判定」に改め、改名を記録する ADR を足す
rationale: |
  フェーズ 4。フェーズ 1〜3 でコード・拡張・ファイル名は新名になった。README・設計書・要件・スキルに残る
  旧名（rule-samples.yml・--test-samples・見本）を新名に揃え、改名を記録する ADR を 1 枚足す。
  過去の ADR の本文は書き換えない。
human_review:
  required: true
  reason: スキルと文書の用語を変え、改名を記録する ADR を足すため
allow:
  - match: Write|Edit
    glob: "README.md"
  - match: Write|Edit
    glob: "docs/*"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/docs/*"
  - match: Write|Edit
    glob: ".claude/skills/ccnavi-config/*"
  - match: Write|Edit
    glob: ".claude/skills/commit/references/*"
  - match: Write|Edit
    glob: "wip/scratchpad/*"
---
## 目的

文書とスキルの旧名を新名に揃える。用語はユーザの決定どおり。

- 日本語: 「見本」（ルールの期待の意味のもの）を「期待する判定」に。短くしてよい所は「期待」
- 英語側: `rule-expectations.yml` / `expectations:` / `--test-expectations` / `expectationsPath`

コード側（src・scripts・tests・extensions のコード）と `.ccnavi/common/rule-expectations.yml` は済んでいるので触らない。

## 先に洗った結果

`grep -rniE "rule-samples|--test-samples|samples:|samplesPath|見本|sample"` を、README.md・`docs/`（`docs/adr/` を除く）・
`.claude/skills/`・`extensions/vscode/ccnavi-board/docs/`・`extensions/chrome/ccnavi-approval/docs/`・`CLAUDE.md`・`docs/claude/` に当てた。
**20 ファイル・86 行**が当たる。うち **16 ファイル・75 行を直し、11 行を残す**。`CLAUDE.md`・`docs/claude/`・Chrome 拡張の docs は 0 件。

### 直す（16 ファイル・75 行）

| ファイル | 行数 | 内容 |
|---|---|---|
| `README.md` | 27 | 旗の表、「見本で確かめる」の節（見出しと 90 行目の参照を揃える）、`samples[]`・`samples_path`・`samples.json`、ファイル表 |
| `docs/design/diagnostics.md` | 6 | `--test-samples` の表、`rule-samples.yml` |
| `docs/design/startup-config.md` | 1 | 診断オプションの列挙 |
| `docs/design/pre-check.md` | 1 | `--test Stop` と見本の `tool: Stop` |
| `docs/design/self-guard.md` | 1 | 保護の説明の `rule-samples.yml` |
| `docs/design/multi-repo/places.md` | 3 | 置き場の表、診断フラグの列挙 |
| `docs/design/multi-repo/distribute-common.md` | 1 | 共通レイヤーの中身の列挙の「見本」 |
| `docs/requirements/extension-if.md` | 3 | REQ-EXT-03・16・22 の旗とファイル名 |
| `docs/requirements/diagnostics.md` | 2 | REQ-DIA-09 の「見本」、`--test-samples --json` |
| `.claude/skills/ccnavi-config/SKILL.md` | 7 | description、手順、旗 |
| `.claude/skills/ccnavi-config/references/check.md` | 9 | 「見本を回す」の節、旗、frontmatter の keywords |
| `.claude/skills/ccnavi-config/references/add.md` | 7 | 「見本を一緒に足す」、報告の雛形 |
| `.claude/skills/commit/references/test-groups.md` | 1 | `core` の説明の `rule-samples.yml` |
| `extensions/vscode/ccnavi-board/docs/design/structure.md` | 3 | `--test-samples`、fixtures `samples.json` |
| `extensions/vscode/ccnavi-board/docs/design/screens.md` | 1 | 35 行目の画面とホストの往復の名前（コードの現在の名前に合わせる） |
| `extensions/vscode/ccnavi-board/docs/requirements/rules.md` | 2 | 旗、「サンプルを一括で判定」の画面文言（画面の現在の文言に合わせる） |

### 残す（別の意味。11 行）

- `docs/design/tickets/phases.md` 3 行、`docs/design/tickets/approval.md` 162 行、拡張 `docs/design/structure.md` 186 行: 計画の待ち・振り直しの「見本の表」（`tests/fixtures/plan-*.json`）。試験データ
- `docs/requirements/approval.md` 1 行: 録ったホスト応答の見本（fixtures）
- 拡張 `docs/design/structure.md` 65・86 行、`docs/requirements/workflow.md` 1 行: 初回案内用のダミー（`tour-sample.ts`）
- 拡張 `docs/design/structure.md` 183 行、`docs/design/screens.md` 27 行: ボードの JSON の見本ファイル（`test/fixtures/board*`）
- 判断に迷う行（`approval.md` 162 行）は、読んで試験データの表なら残す

着手のとき、各行を読んでこの仕分けを確かめる。`sample` の機械置換はしない（残す行を壊す）。

## 新しい ADR

`docs/adr/0114-rule-expectations-rename.md`（番号は着手時に `origin/main` を取り直し、最後尾の次を採る。`docs/adr/README.md` の決まり）。
状態は `採用`（決定を覆すのではないので、過去の ADR は「置き換え」にしない）。

- 状況: ルールの期待を「見本」「sample」と呼んでいたが、意味は期待する判定で、ホスト応答の fixtures や案内のダミーの「見本」と語が衝突していた。
  旧名が過去の ADR 0016・0032・0035・0042・0051・0067 に残る
- 決定: 表（決めたこと / なぜ / 採らなかった側）。ファイル `rule-expectations.yml`、キー `expectations:`、旗 `--test-expectations`（旧 `--test-samples` は別名を残さず廃止）、
  拡張の設定 `ccnaviBoard.expectationsPath`、日本語は「期待する判定」。移行（読み替え・warn）は持たない（展開前のため）。過去の ADR の本文は書き換えない
- 得たもの・失ったもの: 語が揃い grep が効く。失うのは、履歴を引くとき旧名と新名の両方を引く手間
- 採らなかった案: 別名で旧名を残す、機械置換、過去の ADR を書き換える

書式は `docs/adr/README.md` に従う（題 `# ADR-0114: …`、状態、状況、決定、理由、得たもの・失ったもの、採らなかった案）。
`docs/adr/README.md` の「一覧」に 1 行足す。文書・スキル・コメントの本文では ADR 番号で言及せず、決めた中身を書く
（`docs/claude/environment.md`）。番号を書いてよいのは ADR の中だけ。

### README の表の旧名の扱い（案）

`docs/adr/README.md` 60 行目（動かした ADR の表の 0065 の行）が「`rule-samples.yml` から引かれている」と書く。
これは動かした時点の事実の記録（「数は動かした時点のもので、後から増えても直さない」の方針）なので、**本文は書き換えない**のを第一案とする。
ファイルは既に無いので「いまある物」としては壊れるが、時点の記録として読める。
代案は行末に「（現 `rule-expectations.yml`。ADR-0114）」を足す。ユーザに選んでもらう（相談）。

## 書き込み保護の対象になりうるファイル

保護されるかは着手のときに Edit が拒否されるかで分かる。可能性があるのは次の 4 ファイル。
拒否されたら、CLAUDE.md の手順どおり `wip/scratchpad/` に `cp` して編集し、ユーザに `mv` のフルパスを渡してターンを終える（4 本とも作ってから）。

- `.claude/skills/ccnavi-config/SKILL.md`
- `.claude/skills/ccnavi-config/references/check.md`
- `.claude/skills/ccnavi-config/references/add.md`
- `.claude/skills/commit/references/test-groups.md`

`CLAUDE.md`・`docs/claude/` は当たりが 0 件なので触らない。

## 完了条件

- 手順どおり同じ `grep -rniE "rule-samples|--test-samples|samples:|samplesPath|見本|sample"` を当て、`docs/adr/` の過去 ADR（0016・0032・0035・0042・0051・0067・README の表ほか）と、上の「残す」11 行だけが残る
- 新しい ADR が `tests/core/test_adr_numbers.py` の条件（番号の重複なし、題の形、一覧に 1 回載る）を満たす。このテストは文書だけのコミットでは回らないので手で回す
- 文書の文言を検査するテストは無い（`tests/` を洗った: 旧名を文書内容として読むものは見つからない）。着手のとき確かめ直す
- `ccnavi --lint` の error が 0
- ruff・他のテストは文書のみなので不要
