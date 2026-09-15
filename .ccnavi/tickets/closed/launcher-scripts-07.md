---
version: 1
ticket: launcher-scripts-07
parent: launcher-scripts
phase: 5
predecessors:
- launcher-scripts-06
- launcher-scripts-08
- launcher-scripts-09
title: 文書を直す（振り分けの sh の置き場、--bin の廃止、実行役のコマンドの中で実行されるコマンドへの当て方）
rationale: 'フェーズ 1〜4 で決めて入れたことを、利用者向けの README、実装の理屈の ccnavi.md、外から観測できる約束の

  requirements.md、判断の記録の ADR に反映する。ADR は置き場の変更（ADR-0043。ADR-0041 を置き換える）と、

  判定の当て方の変更（ADR-0044）に分ける。言い方は判断の記録どおり「実行役のコマンド」「中で実行されるコマンド」で、

  「包み」は使わない。

  '
human_review:
  required: true
  reason: 配布先の導入手順・env の表・要件の文面と、判定の当て方を決めた ADR を書くため
allow:
- match: Write|Edit
  glob: README.md
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: requirements.md
- match: Write|Edit
  glob: docs/*
ccnavi_approved:
  approved_at: 2026-09-14T21:46:10+0900
  source_tree: launcher-scripts
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/launcher-scripts/wip/tickets/todo/launcher-scripts-07.md
started_at: 2026-09-14T21:46:36+0900
base_sha: 7e2218c987fa376cbfd264bbd6989845e85e7a78
completed_at: 2026-09-14T22:15:31+0900
---

# 文書を直す

## 入力

- 設計 `wip/design/launcher-scripts.md`（11 節の docs の行、13 節の ADR-0043・ADR-0044 の骨子、14 節の requirements.md の候補、15 節の D-1〜D-10）
- MR #30 の判断の記録（言い方の決定）
- フェーズ 3 の依頼文の「実装で決めたこと」1〜10 と「設計との食い違い」（実装が設計から変えた点は、実装に合わせて書く）
- フェーズ 4 の `wip/design/scripts/COPY.md`（切り替えの順序）

## 直すもの

| ファイル | 直すこと |
|---|---|
| `README.md` | 「実行ファイルとルールを配る」の図と表（sh は `.ccnavi/scripts/ccnavi-launcher.sh`、実行ファイルは `.ccnavi/bin/<os>-<arch>/`）。env の表の `CCNAVI_BIN_PATH`。`--bin` の記述を消し、渡すと 2 で断ることを書く。移し替え（前の既定の綴りだけ書き換える、既定でない綴りは名指しだけ、前の `.ccnavi/bin/ccnavi` を消す条件）。「試験の JSON」の表に `unwrapped`（版は 1 のまま、足した欄）。開発の節に「dist を直に起動したいときは `.claude/settings.local.json` の env で `CCNAVI_BIN_PATH` を `dist/ccnavi/ccnavi` にする」の段落（設計 6 節）。`build.py` が `.ccnavi/bin/<target>/` へ写すこと。判定の節に、実行役のコマンド（`env`・`sudo`・`sh -c`・`xargs`・`find -exec` など）の中で実行されるコマンドにも deny・ask とサブエージェントの禁止を当て、allow には当てないこと、拒否と確認の文面の 1 行 |
| `ccnavi.md` | §4.6 の表と本文（置き場、sh が `../bin/` を探す、隣は探さない）。§8.1 の表の行と §8.2 の説明（自己保護の `binary_clause`・`launched_executable` が名前で 2 つの形を持つ）。判定の節に、`shellread` が中で実行されるコマンドの層を作る仕組み（一覧は組み込み、深さ 4、途中の層も残す、読み切れない形でも作る）、当てる先の線引き（deny・ask・禁止に当て、allow・exempt・チケットの範囲には当てない）とその理由、記録の欄 `unwrapped`、コードの扱い（全部が中で実行されるコマンドで当たったときは `PARSE_UNCERTAIN` にしない）。lint の N1・N2 |
| `requirements.md` | 設計 14 節の 6 件。番号は空きを取る。文面の「包み」「包みを外した形」は「実行役のコマンド」「中で実行されるコマンド」に置き換える |
| `docs/adr/0043-launcher-in-scripts.md`（新規） | 設計 13 節の ADR-0043 の骨子。決定に、実装で足した「前の `.ccnavi/bin/ccnavi` を消すのは env を前の既定から書き換えた回か、既に新しい綴りを指す回だけ」を入れる |
| `docs/adr/0044-*.md`（新規） | 設計 13 節の ADR-0044 の骨子（題は言い方の決定に合わせて「シェルのコマンドは、実行役のコマンドの中で実行されるコマンドにも止める側のルールだけを当てる」） |
| `docs/adr/0041-*.md` | 状態を「置き換え（ADR-0043）」に |
| `docs/adr/README.md` | 一覧に 0043・0044 を足し、0041 の状態を直す |

## 確かめること

- 「包み」という語が、直したファイルに残っていない（`grep -n 包み`）
- `--bin` の記述が README と ccnavi.md に残っていない（廃止の説明を除く）
- 表や例の綴り（`.ccnavi/scripts/ccnavi-launcher.sh`、`.ccnavi/bin/<os>-<arch>/`、`CCNAVI_BIN_PATH`）が実装（`scripts/ccnavi-setup.sh` の定数、`ccnavi/settings.py` の `OLD_BIN_PATHS`、`build.py`）と一致する
- `uv run python -m unittest discover -s tests -t .` が赤にならない（README の表を読むテストがあれば、それも含めて）

## やらないこと

- コード・テスト・sh・拡張（フェーズ 3・4 で済み）
- `vscode-extension/ccnavi-board/README.md`（フェーズ 3 で直した。docs の範囲にも入っていない）
- 設計書 `wip/design/launcher-scripts.md` のコード例の整形（`ruff format --check` が挙げる）。docs の範囲（`phases.yml`）に `wip/design/*` が無い。`wip/` を MR の前にブランチから外すなら、そのとき消える
- `.gitignore`・`SKILL.md`・`.claude/settings.json`・`.ccnavi/scripts/ccnavi-launcher.sh`（人が `COPY.md` で直す）
- `HANDOVER.md`（過去の記録）
